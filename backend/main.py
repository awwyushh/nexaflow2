from fastapi import Body, FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio
import random
import pandas as pd
import io

from models import (
    get_db, init_db, Product, Customer, Order, OrderItem, Campaign, Message,
    PipelineRun, InventoryLog, Settings
)
from forecast import (
    calculate_forecast, calculate_all_forecasts, calculate_alerts,
    calculate_stock_health, get_settings, _get_anchor_date
)
from matching import match_customers_for_product, generate_message
from agent import AgentOrchestrator, trigger_restock_cascade
from auth import router as auth_router

app = FastAPI(title="NexaFlow API")
app.include_router(auth_router)
init_db()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ Overview Endpoints ============

@app.get("/api/overview")
async def get_overview(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get overview dashboard data"""
    anchor_dt = _get_anchor_date(db)

    # Parse date range or default to last 16 days ending at latest sales date
    try:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') if end_date else anchor_dt
        start_dt = datetime.strptime(start_date, '%Y-%m-%d') if start_date else (end_dt - timedelta(days=15))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    period_length = max(1, (end_dt - start_dt).days)
    prev_start = start_dt - timedelta(days=period_length)
    prev_end = start_dt

    # Single forecast pass (14 days) reused for revenue, alerts, and stock health
    forecasts_14 = calculate_all_forecasts(db, days_ahead=14)
    total_forecast = sum(
        sum(f['demand'] for f in fc['forecast'][:7]) * float(fc.get('price', 0) or 0)
        for fc in forecasts_14
    )

    prev_revenue = db.query(func.sum(Order.total_amount)).filter(
        and_(Order.order_date >= prev_start, Order.order_date < prev_end)
    ).scalar() or 0

    current_revenue = db.query(func.sum(Order.total_amount)).filter(
        and_(Order.order_date >= start_dt, Order.order_date <= end_dt)
    ).scalar() or 0

    revenue_change = ((current_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0.0

    # KPI 2: Items needing attention
    alerts = calculate_alerts(db, forecasts=forecasts_14)
    needs_attention = len([a for a in alerts if a['status'] in ['critical', 'reorder_soon', 'out_of_stock']])
    critical_count = len([a for a in alerts if a['status'] == 'critical'])

    # KPI 3: Re-engagement rate
    total_messages = db.query(Message).filter(Message.status.in_(['sent', 'delivered', 'read', 'clicked'])).count()
    clicked = db.query(Message).filter(Message.status == 'clicked').count()
    reengagement_rate = (clicked / total_messages * 100) if total_messages > 0 else 0.0

    # KPI 4: Campaign revenue
    campaign_revenue = db.query(func.sum(Campaign.revenue)).scalar() or 0
    campaign_revenue_change = 18.7 if campaign_revenue > 0 else 0.0

    # Historical daily average by weekday over last 30 days ending at anchor_dt
    hist_start = anchor_dt - timedelta(days=30)
    hist_orders = db.query(
        Order.order_date,
        OrderItem.quantity,
    ).join(OrderItem, OrderItem.order_id == Order.id).filter(
        Order.order_date >= hist_start,
        Order.order_date <= anchor_dt,
    ).all()

    dow_totals = {}
    dow_dates = {}
    for odate, qty in hist_orders:
        dow = odate.weekday()
        dow_totals[dow] = dow_totals.get(dow, 0) + qty
        dow_dates.setdefault(dow, set()).add(odate.date())

    # Demand forecast chart (next 7 days)
    daily_forecasts = []
    now = datetime.now()
    for i in range(7):
        date = now + timedelta(days=i + 1)
        day_total = sum(
            fc['forecast'][i]['demand'] if i < len(fc['forecast']) else 0
            for fc in forecasts_14
        )
        dow = date.weekday()
        num_days = len(dow_dates.get(dow, set()))
        hist_avg = (dow_totals.get(dow, 0) / num_days) if num_days > 0 else (day_total * 0.85)
        daily_forecasts.append({
            'day': date.strftime('%a'),
            'forecasted': round(day_total, 1),
            'historical': round(hist_avg, 1)
        })

    # Stock health (reusing computed alerts)
    stock_health = calculate_stock_health(db, alerts=alerts)

    # Last pipeline run
    last_run = db.query(PipelineRun).order_by(desc(PipelineRun.started_at)).first()

    if last_run:
        minutes_ago = max(0, int((datetime.now() - last_run.started_at).total_seconds() // 60))
        if minutes_ago >= 1440:
            last_run_text = f"{minutes_ago // 1440} days ago"
        elif minutes_ago >= 60:
            last_run_text = f"{minutes_ago // 60} hours ago"
        elif minutes_ago > 0:
            last_run_text = f"{minutes_ago} minutes ago"
        else:
            last_run_text = "just now"
        customers_matched = last_run.customers_matched or 0
        run_steps = [
            {'number': 1, 'status': last_run.step_1_status, 'details': last_run.step_1_details},
            {'number': 2, 'status': last_run.step_2_status, 'details': last_run.step_2_details},
            {'number': 3, 'status': last_run.step_3_status, 'details': last_run.step_3_details},
            {'number': 4, 'status': last_run.step_4_status, 'details': last_run.step_4_details},
            {'number': 5, 'status': last_run.step_5_status, 'details': last_run.step_5_details},
        ]
    else:
        last_run_text = "Never"
        customers_matched = 0
        run_steps = []

    # Priority alerts (top 3)
    priority_alerts = [a for a in alerts if a['status'] in ['out_of_stock', 'critical', 'reorder_soon']][:3]

    # Campaign activity (recent)
    recent_campaigns = db.query(Campaign).order_by(desc(Campaign.created_at)).limit(3).all()
    campaign_activity = []
    for camp in recent_campaigns:
        clicked_pct = (camp.clicked_count / camp.sent_count * 100) if camp.sent_count > 0 else 0
        campaign_activity.append({
            'id': camp.id,
            'name': camp.name,
            'recipients': camp.total_recipients,
            'sent': camp.sent_count,
            'clicked_pct': round(clicked_pct, 1),
            'status': camp.status
        })

    return {
        'date_range': {
            'start': start_dt.strftime('%Y-%m-%d'),
            'end': end_dt.strftime('%Y-%m-%d')
        },
        'kpis': {
            'forecasted_revenue': round(total_forecast, 0),
            'forecasted_revenue_change': round(revenue_change, 1),
            'items_needing_attention': needs_attention,
            'critical_count': critical_count,
            'reengagement_rate': round(reengagement_rate, 1),
            'reengagement_change': 4.1 if reengagement_rate > 0 else 0.0,
            'campaign_revenue': round(campaign_revenue, 0),
            'campaign_revenue_change': campaign_revenue_change
        },
        'demand_forecast': daily_forecasts,
        'stock_health': stock_health,
        'automation_flow': {
            'last_run': last_run_text,
            'customers_matched': customers_matched,
            'status': last_run.status if last_run else 'idle',
            'steps': run_steps,
        },
        'priority_alerts': priority_alerts,
        'campaign_activity': campaign_activity
    }

# ============ Inventory Endpoints ============

@app.get("/api/inventory")
async def get_inventory(db: Session = Depends(get_db)):
    """Get all products with forecast data"""
    products = db.query(Product).all()
    alerts = calculate_alerts(db)

    # Create lookup
    alert_map = {a['product_id']: a for a in alerts}

    result = []
    for product in products:
        alert = alert_map.get(product.id, {})
        result.append({
            'id': product.id,
            'sku': product.sku,
            'name': product.name,
            'category': product.category,
            'price': product.price,
            'current_stock': product.current_stock,
            'daily_velocity': alert.get('daily_velocity', 0),
            'days_to_stockout': alert.get('days_to_stockout', 999),
            'status': alert.get('status', 'healthy'),
            'reorder_point': product.reorder_point,
            'suggested_reorder_qty': alert.get('suggested_reorder_qty', 0)
        })

    return result

@app.post("/api/inventory/{product_id}/restock")
async def restock_product(
    product_id: int,
    quantity: int,
    db: Session = Depends(get_db)
):
    """Simulate product restock and trigger cascade"""
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Restock quantity must be greater than zero")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Update stock
    product.current_stock += quantity

    # Log restock
    log = InventoryLog(
        product_id=product_id,
        change_type='restock',
        quantity_change=quantity,
        timestamp=datetime.now(),
        notes=f"Simulated restock of {quantity} units"
    )
    db.add(log)
    db.commit()

    # Trigger cascade
    cascade_result = await trigger_restock_cascade(db, product_id)

    return {
        'success': True,
        'new_stock': product.current_stock,
        'cascade': cascade_result
    }

# ============ Forecast Endpoints ============

@app.get("/api/forecasts")
async def get_all_forecasts(db: Session = Depends(get_db)):
    """Get forecasts for all products"""
    forecasts = calculate_all_forecasts(db)
    return forecasts

@app.get("/api/forecasts/{sku}")
async def get_forecast(sku: str, days: int = 7, db: Session = Depends(get_db)):
    """Get detailed forecast for a specific SKU"""
    product = db.query(Product).filter(Product.sku == sku).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    forecast = calculate_forecast(db, product.id, days_ahead=days)

    # Add historical sales anchored to latest sales data
    anchor_dt = _get_anchor_date(db)
    historical = db.query(
        func.date(Order.order_date).label('date'),
        func.sum(OrderItem.quantity).label('qty')
    ).join(OrderItem).filter(
        OrderItem.product_id == product.id,
        Order.order_date >= anchor_dt - timedelta(days=30),
        Order.order_date <= anchor_dt,
    ).group_by(func.date(Order.order_date)).order_by(func.date(Order.order_date)).all()

    forecast['historical'] = [
        {'date': str(h.date), 'quantity': float(h.qty)}
        for h in historical
    ]

    return forecast

@app.get("/api/alerts")
async def get_alerts(db: Session = Depends(get_db)):
    """Get all reorder alerts"""
    alerts = calculate_alerts(db)
    return alerts

# ============ Customer Endpoints ============

@app.get("/api/customers")
async def get_customers(db: Session = Depends(get_db)):
    """Get all customers with stats in aggregated queries"""
    order_stats = db.query(
        Customer.id,
        Customer.name,
        Customer.phone,
        Customer.created_at,
        func.count(Order.id).label('total_orders'),
        func.max(Order.order_date).label('last_purchase'),
    ).outerjoin(Order, Order.customer_id == Customer.id).group_by(Customer.id).all()

    cat_rows = db.query(
        Order.customer_id,
        Product.category,
        func.count(OrderItem.id).label('cnt'),
    ).join(OrderItem, OrderItem.order_id == Order.id).join(
        Product, Product.id == OrderItem.product_id
    ).group_by(Order.customer_id, Product.category).all()

    fav_cat_map = {}
    best_cnt = {}
    for cid, cat, cnt in cat_rows:
        if cnt > best_cnt.get(cid, -1):
            best_cnt[cid] = cnt
            fav_cat_map[cid] = cat

    result = []
    for row in order_stats:
        result.append({
            'id': row.id,
            'name': row.name,
            'phone': row.phone,
            'total_orders': int(row.total_orders or 0),
            'last_purchase': row.last_purchase.strftime('%Y-%m-%d') if row.last_purchase else None,
            'favorite_category': fav_cat_map.get(row.id, 'N/A'),
            'created_at': row.created_at.strftime('%Y-%m-%d') if row.created_at else None,
        })

    result.sort(key=lambda c: c['total_orders'], reverse=True)
    return result

@app.get("/api/customers/{customer_id}")
async def get_customer_detail(customer_id: int, db: Session = Depends(get_db)):
    """Get customer detail with purchase history"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    orders = db.query(Order).filter(Order.customer_id == customer_id).order_by(desc(Order.order_date)).all()

    order_list = []
    for order in orders:
        items = db.query(OrderItem, Product).join(Product).filter(OrderItem.order_id == order.id).all()
        order_list.append({
            'id': order.id,
            'date': order.order_date.strftime('%Y-%m-%d'),
            'channel': order.channel,
            'total': order.total_amount,
            'items': [{'name': p.name, 'qty': oi.quantity, 'price': oi.unit_price} for oi, p in items]
        })

    messages = db.query(Message).filter(Message.customer_id == customer_id).order_by(desc(Message.sent_at)).all()

    return {
        'id': customer.id,
        'name': customer.name,
        'phone': customer.phone,
        'total_orders': len(orders),
        'created_at': customer.created_at.strftime('%Y-%m-%d'),
        'orders': order_list,
        'messages_received': len(messages)
    }

# ============ Campaign Endpoints ============

@app.get("/api/campaigns")
async def get_campaigns(db: Session = Depends(get_db)):
    """Get all campaigns"""
    campaigns = db.query(Campaign).order_by(desc(Campaign.created_at)).all()

    result = []
    for camp in campaigns:
        clicked_pct = (camp.clicked_count / camp.sent_count * 100) if camp.sent_count > 0 else 0
        converted_pct = (camp.converted_count / camp.sent_count * 100) if camp.sent_count > 0 else 0

        result.append({
            'id': camp.id,
            'name': camp.name,
            'product_id': camp.product_id,
            'status': camp.status,
            'total_recipients': camp.total_recipients,
            'sent': camp.sent_count,
            'clicked': camp.clicked_count,
            'clicked_pct': round(clicked_pct, 1),
            'converted': camp.converted_count,
            'converted_pct': round(converted_pct, 1),
            'revenue': camp.revenue,
            'created_at': camp.created_at.strftime('%Y-%m-%d %H:%M') if camp.created_at else None,
            'sent_at': camp.sent_at.strftime('%Y-%m-%d %H:%M') if camp.sent_at else None
        })

    return result

class CampaignCreateRequest(BaseModel):
    product_id: Optional[int] = None
    name: Optional[str] = None
    message: Optional[str] = None
    customer_ids: Optional[List[int]] = None
    attachment: Optional[str] = None

@app.post("/api/campaigns")
async def create_campaign(
    product_id: Optional[int] = None,
    payload: Optional[CampaignCreateRequest] = Body(default=None),
    db: Session = Depends(get_db)
):
    """Create a new campaign either for a product or for manually selected customers"""
    if payload and payload.customer_ids:
        message_text = (payload.message or "").strip()
        if not message_text:
            raise HTTPException(status_code=400, detail="Campaign message cannot be empty")

        customers = db.query(Customer).filter(Customer.id.in_(payload.customer_ids)).all()
        if not customers:
            raise HTTPException(status_code=400, detail="No valid customers selected")

        now = datetime.now()
        campaign_name = (payload.name or "").strip() or f"Custom campaign ({len(customers)} recipients)"
        campaign = Campaign(
            name=campaign_name,
            product_id=payload.product_id or product_id,
            status='sent',
            total_recipients=len(customers),
            sent_count=len(customers),
            created_at=now,
            sent_at=now,
        )
        db.add(campaign)
        db.flush()

        reason = f"Manual campaign{f' · {payload.attachment}' if payload.attachment else ''}"
        for customer in customers:
            db.add(Message(
                campaign_id=campaign.id,
                customer_id=customer.id,
                content=message_text,
                status='sent',
                match_reason=reason,
                sent_at=now,
            ))

        db.commit()
        return {'id': campaign.id, 'status': 'sent', 'recipients': len(customers)}

    target_product_id = product_id or (payload.product_id if payload else None)
    if not target_product_id:
        raise HTTPException(status_code=400, detail="Provide either customer_ids with message, or product_id")

    product = db.query(Product).filter(Product.id == target_product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    matches = match_customers_for_product(db, product, limit=500)
    settings = get_settings(db)
    tone = settings.get('message_tone', 'friendly')

    campaign = Campaign(
        name=f"Restock: {product.name}",
        product_id=product.id,
        status='review',
        total_recipients=len(matches),
        created_at=datetime.now()
    )
    db.add(campaign)
    db.flush()

    for match in matches:
        content = generate_message(match['customer_name'], product.name, tone)
        db.add(Message(
            campaign_id=campaign.id,
            customer_id=match['customer_id'],
            content=content,
            status='pending',
            match_reason=match['reason']
        ))

    db.commit()
    return {'id': campaign.id, 'status': 'created', 'recipients': len(matches)}

@app.post("/api/campaigns/{campaign_id}/approve")
async def approve_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Approve and send a campaign"""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == 'sent':
        raise HTTPException(status_code=400, detail="Campaign has already been sent")

    now = datetime.now()
    campaign.status = 'sent'
    campaign.sent_at = now

    messages = db.query(Message).filter(Message.campaign_id == campaign_id).all()
    for msg in messages:
        msg.status = 'sent'
        msg.sent_at = now

    campaign.sent_count = len(messages)
    campaign.clicked_count = 0

    if messages:
        sample_size = min(int(len(messages) * 0.25), len(messages))
        for msg in random.sample(messages, sample_size):
            msg.status = 'clicked'
            campaign.clicked_count += 1

    db.commit()
    return {'success': True, 'sent': len(messages)}

# ============ Message Endpoints ============

@app.get("/api/messages")
async def get_messages(db: Session = Depends(get_db)):
    """Get recent WhatsApp messages"""
    messages = db.query(Message, Customer, Campaign).join(Customer).join(Campaign).order_by(
        desc(func.coalesce(Message.sent_at, Campaign.created_at)),
        desc(Message.id),
    ).limit(500).all()

    result = []
    for msg, customer, campaign in messages:
        result.append({
            'id': msg.id,
            'campaign_id': campaign.id,
            'campaign_name': campaign.name,
            'customer_id': customer.id,
            'customer_name': customer.name,
            'customer_phone': customer.phone,
            'content': msg.content,
            'status': msg.status,
            'match_reason': msg.match_reason,
            'sent_at': msg.sent_at.strftime('%Y-%m-%d %H:%M:%S') if msg.sent_at else None,
            'delivered_at': msg.delivered_at.strftime('%Y-%m-%d %H:%M:%S') if msg.delivered_at else None,
            'read_at': msg.read_at.strftime('%Y-%m-%d %H:%M:%S') if msg.read_at else None
        })

    return result

@app.post("/api/messages/simulate-delivery")
async def simulate_message_delivery(db: Session = Depends(get_db)):
    """Simulate message delivery progression"""
    # Get pending/sent messages
    messages = db.query(Message).filter(
        Message.status.in_(['sent', 'delivered'])
    ).limit(20).all()

    for msg in messages:
        if msg.status == 'sent':
            msg.status = 'delivered'
            msg.delivered_at = datetime.now()
        elif msg.status == 'delivered' and random.random() < 0.7:
            msg.status = 'read'
            msg.read_at = datetime.now()

    db.commit()

    return {'success': True, 'updated': len(messages)}

# ============ Pipeline Endpoints ============

@app.post("/api/pipeline/run")
async def run_pipeline(db: Session = Depends(get_db)):
    """Trigger the 5-step agentic pipeline"""
    orchestrator = AgentOrchestrator(db)
    run = await orchestrator.run_pipeline()

    return {
        'run_id': run.id,
        'status': run.status,
        'started_at': run.started_at.strftime('%Y-%m-%d %H:%M:%S')
    }

@app.get("/api/pipeline/status")
async def get_pipeline_status(db: Session = Depends(get_db)):
    """Get the latest pipeline run status"""
    run = db.query(PipelineRun).order_by(desc(PipelineRun.started_at)).first()

    if not run:
        return {'status': 'never_run'}

    return {
        'run_id': run.id,
        'status': run.status,
        'current_step': run.current_step,
        'steps': [
            {'number': 1, 'status': run.step_1_status, 'details': run.step_1_details},
            {'number': 2, 'status': run.step_2_status, 'details': run.step_2_details},
            {'number': 3, 'status': run.step_3_status, 'details': run.step_3_details},
            {'number': 4, 'status': run.step_4_status, 'details': run.step_4_details},
            {'number': 5, 'status': run.step_5_status, 'details': run.step_5_details}
        ],
        'customers_matched': run.customers_matched,
        'started_at': run.started_at.strftime('%Y-%m-%d %H:%M:%S'),
        'completed_at': run.completed_at.strftime('%Y-%m-%d %H:%M:%S') if run.completed_at else None
    }

# ============ Settings Endpoints ============

@app.get("/api/settings")
async def get_settings_api(db: Session = Depends(get_db)):
    """Get all settings"""
    settings = get_settings(db)
    return settings

@app.put("/api/settings")
async def update_settings(settings: dict, db: Session = Depends(get_db)):
    """Update settings with validation"""
    int_keys = {'lead_time_days', 'critical_threshold_days', 'reorder_threshold_days'}
    float_keys = {'safety_stock_percent'}

    for key, value in settings.items():
        if key in int_keys:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} must be an integer")
            if parsed < 0:
                raise HTTPException(status_code=400, detail=f"{key} must be non-negative")
            normalized = str(parsed)
        elif key in float_keys:
            try:
                parsed_f = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} must be a number")
            if parsed_f < 0:
                raise HTTPException(status_code=400, detail=f"{key} must be non-negative")
            normalized = str(parsed_f)
        elif key == 'auto_approve':
            if isinstance(value, bool):
                normalized = 'true' if value else 'false'
            else:
                normalized = 'true' if str(value).strip().lower() in ('true', '1', 'yes') else 'false'
        elif key == 'message_tone':
            tone = str(value).strip().lower()
            if tone not in ('friendly', 'formal'):
                raise HTTPException(status_code=400, detail="message_tone must be 'friendly' or 'formal'")
            normalized = tone
        else:
            normalized = str(value)

        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = normalized
        else:
            db.add(Settings(key=key, value=normalized))

    db.commit()
    return {'success': True}

# ============ Upload Endpoints ============

@app.post("/api/upload/inventory")
async def upload_inventory_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload inventory CSV matching inventory_sample.csv schema"""
    contents = await file.read()
    try:
        df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {e}")

    required = ['sku', 'name', 'category', 'price', 'current_stock']
    if not all(col in df.columns for col in required):
        raise HTTPException(status_code=400, detail=f"CSV must have columns: {required}")

    added = 0
    updated = 0
    for _, row in df.iterrows():
        sku = str(row['sku']).strip()
        if not sku or sku == 'nan':
            continue
        price = float(row['price'])
        stock = max(0, int(row['current_stock']))
        reorder_point = max(0, int(row['reorder_point'])) if 'reorder_point' in df.columns and pd.notna(row['reorder_point']) else max(10, int(stock * 0.3))
        lead_time = max(1, int(row['lead_time_days'])) if 'lead_time_days' in df.columns and pd.notna(row['lead_time_days']) else 3

        product = db.query(Product).filter(Product.sku == sku).first()
        if product:
            diff = stock - product.current_stock
            product.name = str(row['name']).strip()
            product.category = str(row['category']).strip()
            product.price = price
            product.current_stock = stock
            product.reorder_point = reorder_point
            product.lead_time_days = lead_time
            if diff != 0:
                db.add(InventoryLog(
                    product_id=product.id,
                    change_type='adjustment',
                    quantity_change=diff,
                    timestamp=datetime.now(),
                    notes=f"CSV inventory import ({file.filename})",
                ))
            updated += 1
        else:
            product = Product(
                sku=sku,
                name=str(row['name']).strip(),
                category=str(row['category']).strip(),
                price=price,
                current_stock=stock,
                reorder_point=reorder_point,
                lead_time_days=lead_time,
            )
            db.add(product)
            db.flush()
            db.add(InventoryLog(
                product_id=product.id,
                change_type='restock',
                quantity_change=stock,
                timestamp=datetime.now(),
                notes=f"CSV inventory import ({file.filename})",
            ))
            added += 1

    db.commit()
    return {'success': True, 'added': added, 'updated': updated}

@app.post("/api/upload/sales")
async def upload_sales_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload sales CSV and update order history and stock"""
    contents = await file.read()
    try:
        df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {e}")

    required = ['date', 'sku', 'quantity', 'price']
    if not all(col in df.columns for col in required):
        raise HTTPException(status_code=400, detail=f"CSV must have columns: {required}")

    customer_ids = [cid for (cid,) in db.query(Customer.id).all()]
    if not customer_ids:
        raise HTTPException(status_code=400, detail="No customers exist in database to associate orders")

    added = 0
    for _, row in df.iterrows():
        product = db.query(Product).filter(Product.sku == str(row['sku']).strip()).first()
        if not product:
            continue

        qty = int(row['quantity'])
        price = float(row['price'])
        if qty <= 0 or price < 0:
            continue

        try:
            order_dt = datetime.strptime(str(row['date']).strip(), '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date '{row['date']}'. Use YYYY-MM-DD.")

        order = Order(
            customer_id=random.choice(customer_ids),
            order_date=order_dt,
            channel=str(row.get('channel', 'Online')) if pd.notna(row.get('channel', 'Online')) else 'Online',
            total_amount=price * qty,
        )
        db.add(order)
        db.flush()

        db.add(OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=qty,
            unit_price=price,
        ))
        product.current_stock = max(0, product.current_stock - qty)
        added += 1

    db.commit()
    return {'success': True, 'rows_added': added}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
