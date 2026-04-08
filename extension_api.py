"""
Extension API — FastAPI endpoints for the ReEngage Chrome extension.
Routes: GET /api/extension/lookup, POST /api/extension/mark-responded

Auth: operator email read from Chrome profile (chrome.identity.getProfileUserInfo),
sent as X-Operator-Email header. Validated against approved users table in BigQuery.
"""

import os
import re
import uuid
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import bigquery

from config import PROJECT, TABLE_OPS_LOG, TABLE_ASSIGNMENTS, TABLE_USERS, TABLE_AUTOMATION_REVIEWS

logger = logging.getLogger(__name__)

app = FastAPI()

# Chrome extensions have no origin — allow all for extension requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ── BigQuery client ───────────────────────────────────────────────────────────

_bq = None


def get_bq():
    global _bq
    if _bq is None:
        refresh_token = os.environ.get("GCP_REFRESH_TOKEN")
        if refresh_token:
            # Cloud Run: use OAuth credentials from env vars
            creds = OAuthCredentials(
                token=None,
                refresh_token=refresh_token,
                client_id=os.environ.get("GCP_CLIENT_ID"),
                client_secret=os.environ.get("GCP_CLIENT_SECRET"),
                token_uri="https://oauth2.googleapis.com/token",
            )
            creds.refresh(AuthRequest())
            _bq = bigquery.Client(project=PROJECT, credentials=creds)
        else:
            # Local: use Application Default Credentials (gcloud auth application-default login)
            _bq = bigquery.Client(project=PROJECT)
    return _bq


def bq_read(sql: str):
    return list(get_bq().query(sql).result())


def bq_exec(sql: str):
    get_bq().query(sql).result()


# ── Auth ──────────────────────────────────────────────────────────────────────

def validate_operator(email: str) -> str:
    """Check email is non-empty and approved in users table."""
    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="Missing operator email")

    safe = email.replace("'", "''")
    rows = bq_read(f"""
        SELECT email FROM `{PROJECT}.{TABLE_USERS}`
        WHERE email = '{safe}' AND approved = TRUE
        LIMIT 1
    """)

    if not rows:
        raise HTTPException(status_code=403, detail="Operator not approved")

    return email


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/extension/health")
def health():
    return {"status": "ok"}


@app.get("/api/extension/lookup")
def lookup(
    order_id: str,
    x_operator_email: str = Header(None),
):
    operator_email = validate_operator(x_operator_email or "")

    if not UUID_RE.match(order_id):
        raise HTTPException(status_code=400, detail="Invalid order_id")

    safe_id = order_id.replace("'", "")
    rows = bq_read(f"""
        SELECT
            rr.response_text,
            rr.coupon_value,
            rr.response_type,
            rr.response_sent,
            ar.customer_name,
            ar.review_text,
            ar.star_rating,
            ar.slug,
            ar.is_replied
        FROM `{PROJECT}.pg_cdc_public.review_responses` rr
        LEFT JOIN `{PROJECT}.elt_data.automation_reviews` ar
            ON rr.order_id = ar.order_id
        WHERE rr.order_id = '{safe_id}'
        LIMIT 1
    """)

    if not rows:
        return {"found": False, "order_id": order_id}

    r = rows[0]
    return {
        "found": True,
        "order_id": order_id,
        "response_text": r.response_text,
        "coupon_value": float(r.coupon_value) if r.coupon_value else 0,
        "response_type": r.response_type,
        "response_sent": str(r.response_sent) if r.response_sent else None,
        "customer_name": r.customer_name,
        "review_text": r.review_text,
        "star_rating": str(r.star_rating) if r.star_rating else None,
        "slug": r.slug,
        "is_replied": bool(r.is_replied),
        "operator_email": operator_email,
    }


class MarkRespondedBody(BaseModel):
    order_id: str
    platform: str
    chain_name: str = ""


@app.post("/api/extension/mark-responded")
def mark_responded(
    body: MarkRespondedBody,
    x_operator_email: str = Header(None),
):
    operator_email = validate_operator(x_operator_email or "")

    if not UUID_RE.match(body.order_id):
        raise HTTPException(status_code=400, detail="Invalid order_id")

    safe_order_id = body.order_id.replace("'", "")

    # Look up real review_uid from automation_reviews (COALESCE(review_id, order_id))
    uid_rows = bq_read(f"""
        SELECT COALESCE(review_id, order_id) AS review_uid
        FROM `{PROJECT}.{TABLE_AUTOMATION_REVIEWS}`
        WHERE order_id = '{safe_order_id}'
        LIMIT 1
    """)
    review_uid = uid_rows[0].review_uid if uid_rows else body.order_id

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    safe_uid = review_uid.replace("'", "''")
    safe_platform = body.platform.replace("'", "''")
    safe_chain = body.chain_name.replace("'", "''")
    safe_operator = operator_email.replace("'", "''")
    platform_short = "extension_ue" if "uber" in body.platform.lower() else "extension_dd"
    log_id = str(uuid.uuid4())

    # Write to ops_log
    bq_exec(f"""
        INSERT INTO `{PROJECT}.{TABLE_OPS_LOG}`
            (id, review_uid, platform, chain_name, action,
             operator_email, performed_by, remarks, processing_timestamp)
        VALUES
            ('{log_id}', '{safe_uid}', '{safe_platform}', '{safe_chain}',
             'mark_responded', '{safe_operator}', '{safe_operator}',
             '{platform_short}', TIMESTAMP('{now}'))
    """)

    # Upsert assignment: update if exists, create as completed if not
    existing = bq_read(f"""
        SELECT assignment_id FROM `{PROJECT}.{TABLE_ASSIGNMENTS}`
        WHERE order_id = '{safe_order_id}'
        LIMIT 1
    """)
    if existing:
        bq_exec(f"""
            UPDATE `{PROJECT}.{TABLE_ASSIGNMENTS}`
            SET status = 'completed', completed_at = TIMESTAMP('{now}')
            WHERE order_id = '{safe_order_id}' AND status = 'pending'
        """)
    else:
        assignment_id = str(uuid.uuid4())
        bq_exec(f"""
            INSERT INTO `{PROJECT}.{TABLE_ASSIGNMENTS}`
                (assignment_id, review_uid, order_id, operator_email,
                 chain_name, platform, days_left, status, assigned_at, completed_at)
            VALUES
                ('{assignment_id}', '{safe_uid}', '{safe_order_id}', '{safe_operator}',
                 '{safe_chain}', '{safe_platform}', 0, 'completed',
                 TIMESTAMP('{now}'), TIMESTAMP('{now}'))
        """)

    return {"success": True, "operator_email": operator_email, "review_uid": review_uid}
