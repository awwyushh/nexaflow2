"""Email-OTP signup handled by this backend.

Supabase is used only as the auth/database store. All verification email is
sent by this service through Brevo's transactional API, so Supabase never has
to send mail (leave its "Confirm email" setting off).
"""

import hashlib
import html as html_lib
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import SignupOtp, get_db

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "NexaFlow")
OTP_PEPPER = os.getenv("OTP_PEPPER", "nexaflow-otp-pepper")

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------- request helpers ----------

def _request(url: str, *, method: str = "GET", headers: Optional[dict] = None, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        detail = error.read().decode() or error.reason
        try:
            parsed = json.loads(detail)
            detail = parsed.get("message") or parsed.get("msg") or parsed.get("error_description") or detail
        except json.JSONDecodeError:
            pass
        raise HTTPException(status_code=error.code if error.code < 500 else 502, detail=str(detail))
    except urllib.error.URLError as error:
        raise HTTPException(status_code=502, detail=f"Cannot reach {url}: {error.reason}")


def _require_config() -> None:
    missing = [
        name for name, value in (
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY),
            ("BREVO_API_KEY", BREVO_API_KEY),
            ("BREVO_SENDER_EMAIL", BREVO_SENDER_EMAIL),
        ) if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Backend email/OTP setup is incomplete. Add {', '.join(missing)} to backend/.env.",
        )


def _supabase_admin(path: str, *, method: str = "GET", body: Optional[dict] = None) -> dict:
    return _request(
        f"{SUPABASE_URL}{path}",
        method=method,
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
        body=body,
    )


def _hash_code(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}:{OTP_PEPPER}".encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_otp_email(email: str, business_name: str, code: str) -> None:
    safe_name = html_lib.escape(business_name or "there")
    safe_email = html_lib.escape(email)
    html = (
        f"<div style=\"font-family:Inter,Arial,sans-serif;color:#18302e;line-height:1.6\">"
        f"<h2 style=\"margin:0 0 12px\">Verify your NexaFlow account</h2>"
        f"<p>Hi {safe_name}, use the code below to confirm {safe_email}.</p>"
        f"<p style=\"font-size:30px;letter-spacing:8px;font-weight:700;margin:22px 0\">{code}</p>"
        f"<p style=\"color:#71817d;font-size:13px\">This code expires in {OTP_TTL_MINUTES} minutes. "
        f"If you did not request it, you can ignore this email.</p></div>"
    )
    _request(
        "https://api.brevo.com/v3/smtp/email",
        method="POST",
        headers={"api-key": BREVO_API_KEY, "Accept": "application/json"},
        body={
            "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
            "to": [{"email": email}],
            "subject": f"Your NexaFlow verification code: {code}",
            "htmlContent": html,
        },
    )


def _latest_otp(db: Session, email: str) -> Optional[SignupOtp]:
    return (
        db.query(SignupOtp)
        .filter(SignupOtp.email == email, SignupOtp.consumed.is_(False))
        .order_by(SignupOtp.created_at.desc())
        .first()
    )


def _issue_code(db: Session, email: str, business_name: str) -> None:
    _require_config()
    previous = _latest_otp(db, email)
    if previous and (datetime.utcnow() - previous.created_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")

    code = _generate_code()
    _send_otp_email(email, business_name, code)

    if previous:
        previous.consumed = True
    db.add(SignupOtp(
        email=email,
        code_hash=_hash_code(email, code),
        business_name=business_name,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
    ))
    db.commit()


def _normalize(payload: "OtpRequest") -> tuple:
    email = payload.email.strip().lower()
    business_name = payload.business_name.strip()
    password = payload.password
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(business_name) < 2:
        raise HTTPException(status_code=400, detail="Business name must contain at least 2 characters.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must contain at least 8 characters.")
    return email, business_name, password


# ---------- schemas ----------

class OtpRequest(BaseModel):
    email: str
    business_name: str = ""
    password: str


class OtpVerify(BaseModel):
    email: str
    code: str
    business_name: str = ""
    password: str


class EmailChange(BaseModel):
    email: str


# ---------- endpoints ----------

@router.post("/request-otp")
def request_otp(payload: OtpRequest, db: Session = Depends(get_db)):
    email, business_name, _ = _normalize(payload)
    _issue_code(db, email, business_name)
    return {"sent": True, "expires_in_minutes": OTP_TTL_MINUTES}


@router.post("/verify-otp")
def verify_otp(payload: OtpVerify, db: Session = Depends(get_db)):
    email, business_name, password = _normalize(payload)
    record = _latest_otp(db, email)
    if not record:
        raise HTTPException(status_code=400, detail="Request a verification code first.")
    if record.expires_at < datetime.utcnow():
        record.consumed = True
        db.commit()
        raise HTTPException(status_code=400, detail="That code has expired. Request a new one.")
    if record.attempts >= OTP_MAX_ATTEMPTS:
        record.consumed = True
        db.commit()
        raise HTTPException(status_code=429, detail="Too many incorrect attempts. Request a new code.")

    if not secrets.compare_digest(record.code_hash, _hash_code(email, payload.code.strip())):
        record.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="That code is not correct.")

    _require_config()
    try:
        _supabase_admin(
            "/auth/v1/admin/users",
            method="POST",
            body={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"business_name": business_name},
            },
        )
    except HTTPException as error:
        if error.status_code in (409, 422):
            raise HTTPException(status_code=409, detail="An account already exists for this email. Sign in instead.")
        raise

    record.consumed = True
    db.commit()
    return {"verified": True, "email": email}


@router.post("/change-email")
def change_email(payload: EmailChange, authorization: Optional[str] = Header(None)):
    """Swap the signed-in user's email without Supabase sending a confirmation."""
    _require_config()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in again to change your email.")
    token = authorization.split(" ", 1)[1].strip()
    if not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=503, detail="Add SUPABASE_ANON_KEY to backend/.env to verify sessions.")

    user = _request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
    )
    user_id = user.get("id")
    email = payload.email.strip().lower()
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in again to change your email.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    _supabase_admin(
        f"/auth/v1/admin/users/{user_id}",
        method="PUT",
        body={"email": email, "email_confirm": True},
    )
    return {"updated": True, "email": email}
