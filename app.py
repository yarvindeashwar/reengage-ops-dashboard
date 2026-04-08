"""
ReEngage Ops Dashboard V2 — Live BQ Data
Google OAuth login → role-based access (lead / tenured_operator / new_operator).
Daily assignment engine distributes reviews to operators.

Run:  streamlit run app.py --server.port 8520
"""

import streamlit as st

st.set_page_config(page_title="ReEngage Ops Dashboard", page_icon="📬", layout="wide")

from auth import login_page, is_lead, logout
from data_loaders import load_reviews, load_response_configs
from tabs import (
    my_queue, all_reviews, chain_health,
    response_config, ops_log, manage_users, lead_overview, search_review,
)

# ── Auth ─────────────────────────────────────────────────────────────────────
user_email, user_name, user_role = login_page()

# ── Session state ────────────────────────────────────────────────────────────
if "post_log" not in st.session_state:
    st.session_state.post_log = []

# ── Load data ────────────────────────────────────────────────────────────────
df_all = load_reviews()
rc_df  = load_response_configs()

chains    = sorted(df_all["chain_name"].unique().tolist()) if not df_all.empty else []
platforms = sorted(df_all["platform"].unique().tolist())   if not df_all.empty else []

# ── Header ───────────────────────────────────────────────────────────────────
st.title("📬 ReEngage Ops Dashboard")
auto_count = int(df_all["is_replied"].sum()) if not df_all.empty and "is_replied" in df_all.columns else 0
st.caption(f"Live data · {len(df_all):,} reviews · {auto_count:,} auto-posted by Loop · "
           f"{len(rc_df)} configs · last 14 days")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header(f"Hi, {user_name}")
    st.caption(f"{user_email} · **{user_role.replace('_', ' ').title()}**")

    st.divider()
    st.markdown("**Quick stats**")

    # Chain filter for sidebar stats
    sidebar_chain = st.selectbox("Filter by chain", ["All"] + chains, key="sidebar_chain")
    df_stats = df_all if sidebar_chain == "All" else df_all[df_all["chain_name"] == sidebar_chain]

    if not df_stats.empty:
        pending_count  = int((df_stats["status"] == "PENDING").sum())
        critical_count = int(((df_stats["priority"] == "CRITICAL") & (df_stats["status"] == "PENDING")).sum())
        responded      = int((df_stats["status"] == "RESPONDED").sum())
        has_response   = int(df_stats["response_text"].notna().sum())
        auto_posted    = int(df_stats["is_replied"].sum()) if "is_replied" in df_stats.columns else 0
        st.metric("Total reviews", f"{len(df_stats):,}")
        st.metric("Pending", f"{pending_count:,}")
        st.metric("🔴 Critical & pending", critical_count)
        st.metric("✅ Responded", f"{responded:,}")
        st.metric("🤖 Auto-posted by Loop", f"{auto_posted:,}")
        st.metric("📝 Has response text", f"{has_response:,}")

    st.divider()
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

    if st.button("Sign out"):
        logout()
        st.rerun()

# ── Tabs ─────────────────────────────────────────────────────────────────────
operator_tabs = [
    "📥 My Queue", "🗂 All Reviews", "🏥 Chain Health",
    "🤖 Response Config", "📋 Ops Log",
]
lead_tabs = ["🔍 Search Review", "📊 Operator Assignments", "👥 Manage Users"]

tab_names = operator_tabs + (lead_tabs if is_lead(user_role) else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    my_queue.render(df_all, user_email)

with tabs[1]:
    all_reviews.render(df_all, chains, platforms)

with tabs[2]:
    chain_health.render(df_all)

with tabs[3]:
    response_config.render(rc_df)

with tabs[4]:
    ops_log.render()

if is_lead(user_role):
    with tabs[5]:
        search_review.render(df_all)

    with tabs[6]:
        lead_overview.render(df_all)

    with tabs[7]:
        manage_users.render(user_email)

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"Live BQ data · {len(df_all):,} reviews across {len(chains)} chains")
