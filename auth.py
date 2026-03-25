"""
Google OAuth login and role-based authorization.
Uses local SQLite for user management (v2 tables).

Add to .streamlit/secrets.toml:
    [google_oauth]
    client_id = "YOUR_CLIENT_ID.apps.googleusercontent.com"
    client_secret = "YOUR_CLIENT_SECRET"
    redirect_uri = "http://localhost:8501"
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

from config import ROLE_LEAD
from local_db import get_conn

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
    """Check if demo mode is enabled in secrets."""
    return st.secrets.get("demo_mode", False)


def login_page():
    """
    Show Google login (or demo picker), check authorization.
    Returns (email, name, role) if authorized, or stops execution.
    """
    if st.session_state.get("auth_email"):
        return (
            st.session_state["auth_email"],
            st.session_state.get("auth_name", st.session_state["auth_email"]),
            st.session_state.get("auth_role", ""),
        )

    # ── Demo mode: local user picker instead of Google OAuth ──
    if _is_demo_mode():
        return _demo_login()

    # ── Production: Google OAuth ──
    config = _get_oauth_config()
    code = st.query_params.get("code")

    if code:
        user_info = _exchange_code(code, config)
        st.query_params.clear()

        if user_info and user_info.get("email"):
            email = user_info["email"]
            name  = user_info.get("name", email)

            user = get_user(email)

            # Bootstrap: first-ever user becomes lead automatically
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
    """Demo mode login — pick a user from the local DB."""
    import pandas as pd
    conn = get_conn()
    users = pd.read_sql_query(
        "SELECT email, name, role FROM users WHERE approved = 1 ORDER BY role, email", conn
    )
    conn.close()

    if users.empty:
        st.title("📬 ReEngage Ops Dashboard — Demo")
        st.error("No demo users found. Run `python seed_demo.py` first.")
        st.stop()

    st.title("📬 ReEngage Ops Dashboard — Demo Mode")
    st.markdown("---")
    st.caption("⚠️ Demo mode — no Google auth required. Disable by removing `demo_mode = true` from secrets.")

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


# ── User CRUD (local SQLite) ────────────────────────────────────────────────

def _user_count() -> int:
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count

def get_user(email: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT email, name, role, approved, reduction_pct FROM users WHERE email = ?",
        (email,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "email": row["email"], "name": row["name"], "role": row["role"],
        "approved": bool(row["approved"]), "reduction_pct": float(row["reduction_pct"]),
    }


def load_all_users() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT email, name, role, approved, reduction_pct, added_by, created_at, updated_at "
        "FROM users ORDER BY role, email",
        conn,
    )
    conn.close()
    return df


def add_user(email: str, name: str, role: str, reduction_pct: float, added_by: str):
    now = _now()
    conn = get_conn()
    conn.execute("""
        INSERT INTO users (email, name, role, approved, reduction_pct, added_by, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            name = excluded.name, role = excluded.role, approved = 1,
            reduction_pct = excluded.reduction_pct, updated_at = excluded.updated_at
    """, (email, name, role, reduction_pct, added_by, now, now))
    conn.commit()
    conn.close()


def update_user_role(email: str, new_role: str, reduction_pct: float):
    now = _now()
    conn = get_conn()
    conn.execute(
        "UPDATE users SET role = ?, reduction_pct = ?, updated_at = ? WHERE email = ?",
        (new_role, reduction_pct, now, email),
    )
    conn.commit()
    conn.close()


def update_user_approval(email: str, approved: bool):
    now = _now()
    conn = get_conn()
    conn.execute(
        "UPDATE users SET approved = ?, updated_at = ? WHERE email = ?",
        (int(approved), now, email),
    )
    conn.commit()
    conn.close()


def remove_user(email: str):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    conn.commit()
    conn.close()


def is_lead(role: str) -> bool:
    return role == ROLE_LEAD
