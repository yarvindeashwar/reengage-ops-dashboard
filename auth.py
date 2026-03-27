"""
Google OAuth login and role-based authorization.
Uses BigQuery for user management (v2 tables).

Add to .streamlit/secrets.toml:
    [google_oauth]
    client_id = "YOUR_CLIENT_ID.apps.googleusercontent.com"
    client_secret = "YOUR_CLIENT_SECRET"
    redirect_uri = "https://your-cloud-run-url"
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

from config import ROLE_LEAD, TABLE_USERS
from db import bq_read, bq_exec

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe(val: str) -> str:
    return val.replace("'", "''")


# ── OAuth helpers ────────────────────────────────────────────────────────────

def _get_oauth_config():
    oauth = st.secrets.get("google_oauth", {})
    return {
        "client_id":     oauth.get("client_id", ""),
        "client_secret": oauth.get("client_secret", ""),
        "redirect_uri":  oauth.get("redirect_uri", "http://localhost:8501"),
    }


def _build_auth_url(config):
    params = {
        "client_id":     config["client_id"],
        "redirect_uri":  config["redirect_uri"],
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "offline",
        "prompt":        "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _exchange_code(code, config):
    token_resp = requests.post(GOOGLE_TOKEN_URL, data={
        "code":          code,
        "client_id":     config["client_id"],
        "client_secret": config["client_secret"],
        "redirect_uri":  config["redirect_uri"],
        "grant_type":    "authorization_code",
    })
    if token_resp.status_code != 200:
        return None
    access_token = token_resp.json().get("access_token")
    if not access_token:
        return None
    user_resp = requests.get(GOOGLE_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    })
    if user_resp.status_code != 200:
        return None
    return user_resp.json()


# ── Login / logout ───────────────────────────────────────────────────────────

def _is_demo_mode() -> bool:
    return st.secrets.get("demo_mode", False)


def login_page():
    if st.session_state.get("auth_email"):
        return (
            st.session_state["auth_email"],
            st.session_state.get("auth_name", st.session_state["auth_email"]),
            st.session_state.get("auth_role", ""),
        )

    if _is_demo_mode():
        return _demo_login()

    config = _get_oauth_config()
    code = st.query_params.get("code")

    if code:
        user_info = _exchange_code(code, config)
        st.query_params.clear()

        if user_info and user_info.get("email"):
            email = user_info["email"]
            name  = user_info.get("name", email)

            user = get_user(email)

            if user is None and _user_count() == 0:
                add_user(email, name, ROLE_LEAD, 0.0, "bootstrap")
                user = get_user(email)

            if user is None or not user.get("approved"):
                st.title("📬 ReEngage Ops Dashboard")
                st.error("🚫 Not authorised. Contact your lead to get access.")
                st.caption(f"Signed in as: {email}")
                st.stop()

            st.session_state["auth_email"] = email
            st.session_state["auth_name"]  = name
            st.session_state["auth_role"]  = user["role"]
            st.rerun()
        else:
            st.error("Login failed. Please try again.")

    st.title("📬 ReEngage Ops Dashboard")
    st.markdown("---")
    st.subheader("Sign in to continue")
    st.link_button("🔐 Sign in with Google", _build_auth_url(config), type="primary")
    st.stop()


def _demo_login():
    users = load_all_users()

    if users.empty:
        st.title("📬 ReEngage Ops Dashboard — Demo")
        st.error("No users found. Add the first user via BigQuery or ask your admin.")
        st.stop()

    st.title("📬 ReEngage Ops Dashboard — Demo Mode")
    st.markdown("---")
    st.caption("⚠️ Demo mode — no Google auth required.")

    users["label"] = users["name"] + "  (" + users["role"].str.replace("_", " ") + ")  —  " + users["email"]
    pick = st.selectbox("Sign in as:", users["label"].tolist())
    row = users[users["label"] == pick].iloc[0]

    if st.button("Sign in", type="primary"):
        st.session_state["auth_email"] = row["email"]
        st.session_state["auth_name"]  = row["name"]
        st.session_state["auth_role"]  = row["role"]
        st.rerun()

    st.stop()


def logout():
    for key in ["auth_email", "auth_name", "auth_role"]:
        st.session_state.pop(key, None)


# ── User CRUD (BigQuery) ────────────────────────────────────────────────────

def _user_count() -> int:
    df = bq_read(f"SELECT COUNT(*) as cnt FROM `{TABLE_USERS}`")
    return int(df.iloc[0]["cnt"])


def get_user(email: str) -> dict | None:
    se = _safe(email)
    df = bq_read(f"""
        SELECT email, name, role, approved, reduction_pct
        FROM `{TABLE_USERS}` WHERE email = '{se}' LIMIT 1
    """)
    if df.empty:
        return None
    r = df.iloc[0]
    return {
        "email": r["email"], "name": r["name"], "role": r["role"],
        "approved": bool(r["approved"]), "reduction_pct": float(r.get("reduction_pct", 0)),
    }


def load_all_users() -> pd.DataFrame:
    return bq_read(f"""
        SELECT email, name, role, approved, reduction_pct,
               added_by, created_at, updated_at
        FROM `{TABLE_USERS}` ORDER BY role, email
    """)


def add_user(email: str, name: str, role: str, reduction_pct: float, added_by: str):
    now = _now()
    se, sn, sa = _safe(email), _safe(name), _safe(added_by)
    bq_exec(f"""
    MERGE `{TABLE_USERS}` T USING (SELECT '{se}' AS email) S ON T.email = S.email
    WHEN MATCHED THEN UPDATE SET
        name = '{sn}', role = '{role}', approved = TRUE,
        reduction_pct = {reduction_pct}, updated_at = TIMESTAMP('{now}')
    WHEN NOT MATCHED THEN INSERT
        (email, name, role, approved, reduction_pct, added_by, created_at, updated_at)
    VALUES ('{se}', '{sn}', '{role}', TRUE, {reduction_pct}, '{sa}',
            TIMESTAMP('{now}'), TIMESTAMP('{now}'))
    """)


def update_user_role(email: str, new_role: str, reduction_pct: float):
    now = _now()
    se = _safe(email)
    bq_exec(f"""
    UPDATE `{TABLE_USERS}`
    SET role = '{new_role}', reduction_pct = {reduction_pct},
        updated_at = TIMESTAMP('{now}')
    WHERE email = '{se}'
    """)


def update_user_approval(email: str, approved: bool):
    now = _now()
    se = _safe(email)
    bq_exec(f"""
    UPDATE `{TABLE_USERS}`
    SET approved = {approved}, updated_at = TIMESTAMP('{now}')
    WHERE email = '{se}'
    """)


def remove_user(email: str):
    from write_helpers import redistribute_assignments
    redistribute_assignments(email)
    se = _safe(email)
    bq_exec(f"DELETE FROM `{TABLE_USERS}` WHERE email = '{se}'")


def is_lead(role: str) -> bool:
    return role == ROLE_LEAD
