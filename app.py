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
    my_queue, all_reviews, scoreboard, chain_health,
    response_config, ops_log, manage_users, lead_overview,
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
    if not df_all.empty:
        pending_count  = int((df_all["status"] == "PENDING").sum())
        critical_count = int(((df_all["priority"] == "CRITICAL") & (df_all["status"] == "PENDING")).sum())
        responded      = int((df_all["status"] == "RESPONDED").sum())
        has_response   = int(df_all["response_text"].notna().sum())
        auto_posted    = int(df_all["is_replied"].sum()) if "is_replied" in df_all.columns else 0
        st.metric("Total reviews", f"{len(df_all):,}")
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
# Operators: My Queue, All Reviews, Scoreboard, Chain Health, Response Config, Ops Log
# Leads: + Operator Assignments, Manage Users
operator_tabs = [
    "📥 My Queue", "🗂 All Reviews", "🏆 Scoreboard", "🏥 Chain Health",
    "🤖 Response Config", "📋 Ops Log",
]
lead_tabs = ["📊 Operator Assignments", "👥 Manage Users"]

tab_names = operator_tabs + (lead_tabs if is_lead(user_role) else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    my_queue.render(df_all, user_email)

with tabs[1]:
    all_reviews.render(df_all, chains, platforms)

with tabs[2]:
    scoreboard.render(df_all)

with tabs[3]:
    chain_health.render(df_all)

with tabs[4]:
    response_config.render(rc_df)

with tabs[5]:
    ops_log.render()

if is_lead(user_role):
    with tabs[6]:
        lead_overview.render(df_all)

    with tabs[7]:
        manage_users.render(user_email)

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
col_f1, col_f2 = st.columns(2)
with col_f1:
    st.caption(f"Live BQ data · {len(df_all):,} reviews across {len(chains)} chains · "
               f"UberEats auto-post ready · DoorDash copy+paste")
with col_f2:
    if st.session_state.post_log:
        st.caption(f"📊 {len(st.session_state.post_log)} UE auto-posts this session")

with st.expander("ℹ️ How response automation works", expanded=False):
    st.markdown("""
**Platform capabilities:**

| Platform | Auto-post | Coupon | Method |
|----------|-----------|--------|--------|
| **UberEats** | ✅ Yes | ✅ Yes | GraphQL `submitEaterReviewReply` — one-click from dashboard |
| **DoorDash** | ❌ No API | ❌ Manual | Copy AI response → Open portal → Paste |

**Response generation:**
1. Config matching — review matched to best config by chain, platform, rating, customer type
2. AI mode — sends review + tonality to LLM, generates personalized response
3. Template mode — picks template, replaces variables. If paraphrase=True, varies wording

**Assignment engine** (runs daily at 8 AM):
- Distributes pending reviews equally across operators
- Minimises chain×platform combos per operator (fewer 3P logins)
- New operators get reduced share (configurable %) — surplus goes to others
- Leads can trigger manually from Manage Users tab
    """)
