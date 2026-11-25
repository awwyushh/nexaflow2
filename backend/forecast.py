import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from models import Product, Order, OrderItem, Forecast, Settings

def get_settings(db: Session):
    """Get settings as dict"""
    settings_list = db.query(Settings).all()
    return {s.key: s.value for s in settings_list}

def _get_anchor_date(db: Session) -> datetime:
    """Anchor forecasting windows to latest order date when sales history is older than now."""
    latest_order = db.query(func.max(Order.order_date)).scalar()
    now = datetime.now()
    if latest_order and latest_order < now:
        return latest_order
    return now

def _compute_forecast_for_product(
    product: Product,
    order_items: list,
    anchor_dt: datetime,
    days_ahead: int = 7,
):
    """Compute EWMA + day-of-week seasonality forecast from pre-fetched order items."""
    if not order_items:
        return {
            'sku': product.sku,
            'name': product.name,
            'category': product.category,
            'forecast': [{
                'date': (datetime.now() + timedelta(days=i + 1)).strftime('%Y-%m-%d'),
                'demand': 0.0,
                'lower': 0.0,
                'upper': 0.0,
            } for i in range(days_ahead)],
            'current_stock': product.current_stock,
            'days_to_stockout': 0 if product.current_stock == 0 else 999,
            'daily_velocity': 0.0,
            'method': 'No historical data',
            'confidence': '95%',
        }

    ninety_days_ago = anchor_dt - timedelta(days=90)
    df = pd.DataFrame(order_items, columns=['date', 'quantity'])
    df['date'] = pd.to_datetime(df['date']).dt.date

    daily_sales = df.groupby('date')['quantity'].sum().reset_index()
    daily_sales.columns = ['date', 'qty']

    date_range = pd.date_range(
        start=ninety_days_ago.date(),
        end=anchor_dt.date(),
        freq='D',
    )
    full_series = pd.DataFrame({'date': date_range.date})
    daily_sales = full_series.merge(daily_sales, on='date', how='left').fillna(0)

    daily_sales['date'] = pd.to_datetime(daily_sales['date'])
    daily_sales['dow'] = daily_sales['date'].dt.dayofweek
    dow_avg = daily_sales.groupby('dow')['qty'].mean()
    overall_avg = daily_sales['qty'].mean()
    dow_factor = (dow_avg / overall_avg).fillna(1.0) if overall_avg > 0 else pd.Series([1.0] * 7)

    alpha = 0.3
    ewma = daily_sales['qty'].ewm(alpha=alpha, adjust=False).mean()
    base_forecast = float(ewma.iloc[-1]) if len(ewma) > 0 else float(overall_avg)

    residuals = daily_sales['qty'] - ewma
    std_residual = float(residuals.std()) if len(residuals) > 1 else base_forecast * 0.3

    forecasts = []
    now = datetime.now()
    for i in range(days_ahead):
        future_date = now + timedelta(days=i + 1)
        dow = future_date.weekday()
        seasonal_factor = float(dow_factor.get(dow, 1.0))

        predicted = max(0.0, base_forecast * seasonal_factor)
        lower = max(0.0, predicted - 1.96 * std_residual)
        upper = predicted + 1.96 * std_residual

        forecasts.append({
            'date': future_date.strftime('%Y-%m-%d'),
            'demand': round(predicted, 2),
            'lower': round(lower, 2),
            'upper': round(upper, 2),
        })

    recent_7_days = float(daily_sales.tail(7)['qty'].mean())
    avg_forecast_daily = float(np.mean([f['demand'] for f in forecasts])) if forecasts else 0.0
    effective_daily = recent_7_days if recent_7_days > 0 else avg_forecast_daily

    if product.current_stock <= 0:
        days_to_stockout = 0
    else:
        cumulative = 0.0
        days_to_stockout = None
        for i, f in enumerate(forecasts):
            cumulative += f['demand']
            if cumulative >= product.current_stock:
                days_to_stockout = i + 1
                break

        if days_to_stockout is None:
            if effective_daily > 0:
                remaining = max(0.0, product.current_stock - cumulative)
                days_to_stockout = days_ahead + max(1, int(round(remaining / effective_daily)))
            else:
                days_to_stockout = 999

    return {
        'product_id': product.id,
        'sku': product.sku,
        'name': product.name,
        'category': product.category,
        'price': product.price,
        'current_stock': product.current_stock,
        'forecast': forecasts,
        'days_to_stockout': days_to_stockout,
        'daily_velocity': round(recent_7_days, 2),
        'method': 'EWMA + Day-of-week seasonality',
        'confidence': '95%',
    }

def calculate_forecast(db: Session, product_id: int, days_ahead: int = 7):
    """
    Calculate demand forecast for a product using EWMA + seasonality.
    Returns forecast data with confidence bands.
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None

    anchor_dt = _get_anchor_date(db)
    ninety_days_ago = anchor_dt - timedelta(days=90)

    order_items = db.query(
        Order.order_date,
        OrderItem.quantity,
    ).join(OrderItem).filter(
        OrderItem.product_id == product_id,
        Order.order_date >= ninety_days_ago,
        Order.order_date <= anchor_dt,
    ).all()

    return _compute_forecast_for_product(product, order_items, anchor_dt, days_ahead=days_ahead)

def calculate_all_forecasts(db: Session, days_ahead: int = 7):
    """Calculate forecasts for all products in two bulk queries."""
    products = db.query(Product).all()
    if not products:
        return []

    anchor_dt = _get_anchor_date(db)
    ninety_days_ago = anchor_dt - timedelta(days=90)

    rows = db.query(
        OrderItem.product_id,
        Order.order_date,
        OrderItem.quantity,
    ).join(Order, OrderItem.order_id == Order.id).filter(
        Order.order_date >= ninety_days_ago,
        Order.order_date <= anchor_dt,
    ).all()

    by_product = {}
    for pid, order_date, qty in rows:
        by_product.setdefault(pid, []).append((order_date, qty))

    return [
        _compute_forecast_for_product(product, by_product.get(product.id, []), anchor_dt, days_ahead=days_ahead)
        for product in products
    ]

def _safe_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def calculate_alerts(db: Session, forecasts: list = None):
    """Calculate reorder alerts based on forecasts and thresholds."""
    settings = get_settings(db)
    lead_time = max(0, _safe_int(settings.get('lead_time_days', 3), 3))
    safety_stock_pct = max(0.0, _safe_float(settings.get('safety_stock_percent', 20), 20.0))
    critical_days = max(0, _safe_int(settings.get('critical_threshold_days', 3), 3))
    reorder_days = max(critical_days, _safe_int(settings.get('reorder_threshold_days', 7), 7))

    if forecasts is None:
        forecasts = calculate_all_forecasts(db, days_ahead=14)

    alerts = []
    for fc in forecasts:
        days_to_stockout = fc['days_to_stockout']
        daily_velocity = fc['daily_velocity']
        current_stock = fc['current_stock']

        status = 'healthy'
        priority = 3

        if current_stock == 0:
            status = 'out_of_stock'
            priority = 0
        elif days_to_stockout <= critical_days:
            status = 'critical'
            priority = 1
        elif days_to_stockout <= reorder_days:
            status = 'reorder_soon'
            priority = 2
        elif days_to_stockout > 60:
            status = 'overstocked'
            priority = 4

        safety_stock = daily_velocity * lead_time * (safety_stock_pct / 100.0)
        suggested_qty = int(daily_velocity * (lead_time + 14) + safety_stock - current_stock)
        suggested_qty = max(0, suggested_qty)

        if status == 'out_of_stock':
            reason = "Out of stock - immediate reorder required"
        elif status == 'critical':
            reason = f"Predicted stock-out in {days_to_stockout} days; lead time {lead_time} days -> reorder now"
        else:
            reason = f"Predicted stock-out in {days_to_stockout} days; lead time {lead_time} days"

        alerts.append({
            'product_id': fc.get('product_id'),
            'sku': fc['sku'],
            'name': fc['name'],
            'category': fc['category'],
            'current_stock': current_stock,
            'daily_velocity': daily_velocity,
            'days_to_stockout': days_to_stockout,
            'status': status,
            'priority': priority,
            'suggested_reorder_qty': suggested_qty,
            'reason': reason,
        })

    alerts.sort(key=lambda x: (x['priority'], x['days_to_stockout']))
    return alerts

def calculate_stock_health(db: Session, alerts: list = None):
    """Calculate stock health distribution."""
    if alerts is None:
        alerts = calculate_alerts(db)
    total = len(alerts)

    if total == 0:
        return {
            'healthy': 0, 'low_stock': 0,
            'out_of_stock': 0, 'overstocked': 0,
            'critical_count': 0, 'reorder_soon_count': 0,
        }

    counts = {
        'out_of_stock': len([a for a in alerts if a['status'] == 'out_of_stock']),
        'critical': len([a for a in alerts if a['status'] == 'critical']),
        'reorder_soon': len([a for a in alerts if a['status'] == 'reorder_soon']),
        'overstocked': len([a for a in alerts if a['status'] == 'overstocked']),
    }
    counts['healthy'] = total - sum(counts.values())

    return {
        'healthy': round(counts['healthy'] / total * 100, 1),
        'low_stock': round((counts['critical'] + counts['reorder_soon']) / total * 100, 1),
        'out_of_stock': round(counts['out_of_stock'] / total * 100, 1),
        'overstocked': round(counts['overstocked'] / total * 100, 1),
        'critical_count': counts['critical'],
        'reorder_soon_count': counts['reorder_soon'],
    }
