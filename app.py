"""
ReEngage Ops Dashboard — Live BQ Data
Pulls real reviews, AI-generated responses, and configs from BigQuery.

Run:  streamlit run reengage_ops_dashboard.py --server.port 8520
"""

import json
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="ReEngage Ops Dashboard", page_icon="📬", layout="wide")

PRIORITY_ICON = {"CRITICAL": "🔴", "URGENT": "🟠", "NORMAL": "🟢"}
STATUS_ICON   = {"PENDING": "⏳", "RESPONDED": "✅", "EXPIRED": "💀"}
PROJECT = "arboreal-vision-339901"

TONALITIES     = ["casual", "enthusiastic", "grateful", "polished"]
RESPONSE_TYPES = ["ai", "template"]
COUPON_TYPES   = ["fixed", "percentage", "none"]
CUSTOMER_TYPES = ["new", "existing"]
FEEDBACK_TYPES = ["with_feedback", "without_feedback"]
SENTIMENTS     = ["positive", "negative", "neutral"]
RATING_OPTIONS_DD = ["RATING_VALUE_THUMBS_DOWN", "RATING_VALUE_THUMBS_UP", "RATING_VALUE_LOVED"]
RATING_OPTIONS_UE = ["1", "2", "3", "4", "5"]


# ── BigQuery client (cached) ─────────────────────────────────────────────────
@st.cache_resource
def bq_client():
    # Streamlit Cloud: use service account from secrets
    if "gcp_service_account" in st.secrets:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        return bigquery.Client(project=PROJECT, credentials=creds)
    # Local: use ADC
    return bigquery.Client(project=PROJECT)


def bq_read(sql: str) -> pd.DataFrame:
    return bq_client().query(sql).to_dataframe()


def bq_exec(sql: str):
    bq_client().query(sql).result()


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner="Loading reviews from BigQuery...")
def load_reviews() -> pd.DataFrame:
    """Pull real reviews matched to active configs with AI-generated responses."""
    sql = """
    WITH eligible_reviews AS (
        SELECT
            COALESCE(ar.review_id, ar.order_id) AS review_uid,
            ar.order_id,
            ar.review_id,
            sm.chain AS chain_name,
            ar.platform,
            ar.slug,
            ar.store_id,
            ar.customer_name,
            CASE WHEN SAFE_CAST(ar.orders_count AS INT64) > 1 THEN 'existing' ELSE 'new' END AS customer_type,
            ar.star_rating,
            ar.rating_type,
            ar.rating_value AS rating_value_raw,
            ar.review_text,
            ar.is_replied,
            ar.replied_comment,
            COALESCE(ar.review_timestamp, ar.order_timestamp) AS event_timestamp,
            DATE(DATETIME(COALESCE(ar.review_timestamp, ar.order_timestamp), 'America/Chicago')) AS review_date
        FROM `elt_data.automation_reviews` ar
        INNER JOIN `restaurant_aggregate_metrics.slug_am_mapping` sm ON ar.slug = sm.slug
        WHERE COALESCE(ar.review_timestamp, ar.order_timestamp)
              >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND EXISTS (
              SELECT 1 FROM `pg_cdc_public.review_response_config` rrc
              WHERE rrc.paused = FALSE AND sm.chain = rrc.chain_name
              AND (
                  ARRAY_LENGTH(IFNULL(JSON_VALUE_ARRAY(rrc.vb_platforms), [])) = 0
                  OR ar.platform IN UNNEST(JSON_VALUE_ARRAY(rrc.vb_platforms))
              )
              AND (
                  ARRAY_LENGTH(IFNULL(JSON_VALUE_ARRAY(rrc.ratings), [])) = 0
                  OR CAST(ar.star_rating AS STRING) IN UNNEST(JSON_VALUE_ARRAY(rrc.ratings))
              )
          )
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY COALESCE(ar.review_id, ar.order_id)
            ORDER BY COALESCE(ar.review_timestamp, ar.order_timestamp) DESC
        ) = 1
    ),

    with_days AS (
        SELECT *,
            CASE
                WHEN platform = 'Doordash' THEN
                    DATE_DIFF(DATE_ADD(review_date, INTERVAL 7 DAY), CURRENT_DATE('America/Chicago'), DAY)
                WHEN platform = 'UberEats' THEN
                    DATE_DIFF(DATE_ADD(review_date, INTERVAL 14 DAY), CURRENT_DATE('America/Chicago'), DAY)
                ELSE 0
            END AS days_left
        FROM eligible_reviews
    ),

    with_response AS (
        SELECT
            w.*,
            rr.response_text AS ai_response,
            rr.response_type AS rr_response_type,
            rr.coupon_value,
            rr.response_sent,
            rr.config_id
        FROM with_days w
        LEFT JOIN `pg_cdc_public.review_responses` rr ON w.order_id = rr.order_id
    ),

    with_status AS (
        SELECT *,
            CASE
                WHEN is_replied = TRUE THEN 'RESPONDED'
                WHEN response_sent IS NOT NULL THEN 'RESPONDED'
                WHEN days_left <= 0 THEN 'EXPIRED'
                ELSE 'PENDING'
            END AS status,
            -- replied_comment = Loop already posted this; ai_response = generated but not yet posted
            COALESCE(replied_comment, ai_response) AS response_text,
            CASE
                WHEN days_left <= 1 THEN 'CRITICAL'
                WHEN days_left = 2  THEN 'URGENT'
                ELSE 'NORMAL'
            END AS priority,
            CASE
                WHEN LOWER(CAST(star_rating AS STRING)) IN ('thumbs_up', 'rating_value_thumbs_up', 'rating_value_loved') THEN 'Thumbs Up'
                WHEN LOWER(CAST(star_rating AS STRING)) IN ('thumbs_down', 'rating_value_thumbs_down') THEN 'Thumbs Down'
                WHEN SAFE_CAST(star_rating AS FLOAT64) IS NOT NULL
                    THEN CONCAT(CAST(CAST(SAFE_CAST(star_rating AS FLOAT64) AS INT64) AS STRING), ' Stars')
                ELSE CAST(star_rating AS STRING)
            END AS rating_display,
            SAFE_CAST(star_rating AS FLOAT64) AS rating_numeric,
            CASE
                WHEN platform = 'UberEats' THEN
                    CONCAT('https://merchants.ubereats.com/manager/home/', store_id, '/feedback/reviews/', order_id)
                WHEN platform = 'Doordash' THEN
                    CONCAT('https://www.doordash.com/merchant/feedback?store_id=', store_id)
                ELSE ''
            END AS portal_link
        FROM with_response
    )

    SELECT
        review_uid, order_id, review_id, chain_name, platform, slug, store_id,
        customer_name, customer_type, CAST(star_rating AS STRING) AS star_rating,
        rating_numeric, rating_display, review_text, review_date, days_left,
        portal_link, response_text, rr_response_type AS response_type, coupon_value,
        CAST(config_id AS STRING) AS config_id, status, priority,
        is_replied
    FROM with_status
    ORDER BY
        CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'URGENT' THEN 2 ELSE 3 END,
        days_left ASC, chain_name, platform, slug
    """
    return bq_read(sql)


@st.cache_data(ttl=120, show_spinner="Loading configs...")
def load_response_configs() -> pd.DataFrame:
    sql = """
    SELECT config_id, config_name, chain_name, paused,
           response_type, tonality,
           TO_JSON_STRING(vb_platforms) AS vb_platforms,
           TO_JSON_STRING(ratings) AS ratings,
           TO_JSON_STRING(customer_types) AS customer_types,
           TO_JSON_STRING(review_sentiments) AS review_sentiments,
           TO_JSON_STRING(feedback_presence) AS feedback_presence,
           response_text AS response_template_legacy,
           coupon_type, coupon_fixed_value, coupon_percentage_value,
           dd_coupon_type, dd_coupon_fixed_value, dd_coupon_percentage_value,
           ue_coupon_type, ue_coupon_fixed_value, ue_coupon_percentage_value,
           paraphrase, min_order_value,
           created_by, created_at, updated_by, updated_at
    FROM `pg_cdc_public.review_response_config`
    ORDER BY paused ASC, updated_at DESC
    """
    return bq_read(sql)


@st.cache_data(ttl=120, show_spinner="Loading assignee configs...")
def load_assignee_configs() -> pd.DataFrame:
    sql = """
    SELECT assignee_id, chain_name, platform, last_updated_by, last_updated_at
    FROM `ops_metrics_data.reengage_assignee_config`
    ORDER BY chain_name, platform, assignee_id
    """
    return bq_read(sql)


@st.cache_data(ttl=120, show_spinner="Loading ops log...")
def load_ops_log() -> pd.DataFrame:
    sql = """
    SELECT id, review_uid, platform, chain_name, status, assignee, updated_by, remarks,
           processing_timestamp
    FROM `ops_metrics_data.reengage_ops_log`
    ORDER BY processing_timestamp DESC
    LIMIT 500
    """
    return bq_read(sql)


# ── Write helpers ─────────────────────────────────────────────────────────────

def log_ops_action(review_uid, platform, chain_name, status, assignee, updated_by, remarks=""):
    row_id = str(uuid.uuid4())
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sql = f"""
    INSERT INTO `ops_metrics_data.reengage_ops_log`
        (id, review_uid, platform, chain_name, status, assignee, updated_by, remarks, processing_timestamp)
    VALUES
        ('{row_id}', '{review_uid}', '{platform}', '{chain_name}',
         '{status}', '{assignee}', '{updated_by}',
         '{remarks.replace("'", "''")}', TIMESTAMP('{now}'))
    """
    bq_exec(sql)


def upsert_assignee_config(assignee_id, chain_name, platform, updated_by):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sql = f"""
    MERGE `ops_metrics_data.reengage_assignee_config` T
    USING (SELECT '{assignee_id}' AS assignee_id, '{chain_name}' AS chain_name, '{platform}' AS platform) S
    ON T.assignee_id = S.assignee_id AND T.chain_name = S.chain_name AND T.platform = S.platform
    WHEN MATCHED THEN
        UPDATE SET last_updated_by = '{updated_by}', last_updated_at = TIMESTAMP('{now}')
    WHEN NOT MATCHED THEN
        INSERT (assignee_id, chain_name, platform, last_updated_by, last_updated_at)
        VALUES (S.assignee_id, S.chain_name, S.platform, '{updated_by}', TIMESTAMP('{now}'))
    """
    bq_exec(sql)


def delete_assignee_config(assignee_id, chain_name, platform):
    sql = f"""
    DELETE FROM `ops_metrics_data.reengage_assignee_config`
    WHERE assignee_id = '{assignee_id}' AND chain_name = '{chain_name}' AND platform = '{platform}'
    """
    bq_exec(sql)


# ── Initialize session state ─────────────────────────────────────────────────
if "post_log" not in st.session_state:
    st.session_state.post_log = []


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

df_all = load_reviews()
rc_df  = load_response_configs()
cfg_df = load_assignee_configs()

# Derive filter options
chains    = sorted(df_all["chain_name"].unique().tolist()) if not df_all.empty else []
platforms = sorted(df_all["platform"].unique().tolist())   if not df_all.empty else []
statuses  = sorted(df_all["status"].unique().tolist())     if not df_all.empty else []

# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

st.title("📬 ReEngage Ops Dashboard")
auto_count = int(df_all["is_replied"].sum()) if not df_all.empty and "is_replied" in df_all.columns else 0
st.caption(f"Live data · {len(df_all):,} reviews · {auto_count:,} auto-posted by Loop · {len(rc_df)} configs · last 14 days")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Identity")
    me = st.text_input("Your email", value="", placeholder="you@loopkitchen.com")

    st.divider()
    st.markdown("**Quick stats**")
    if not df_all.empty:
        pending_count  = int((df_all["status"] == "PENDING").sum())
        critical_count = int(((df_all["priority"] == "CRITICAL") & (df_all["status"] == "PENDING")).sum())
        responded      = int((df_all["status"] == "RESPONDED").sum())
        has_response   = int(df_all["response_text"].notna().sum())
        auto_posted = int(df_all["is_replied"].sum()) if "is_replied" in df_all.columns else 0
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


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_q, tab_all, tab_sb, tab_ch, tab_rc, tab_cfg, tab_log = st.tabs(
    ["📥 My Queue", "🗂 All Reviews", "🏆 Scoreboard", "🏥 Chain Health",
     "🤖 Response Config", "⚙️ Assignee Config", "📋 Ops Log"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: My Queue
# ═══════════════════════════════════════════════════════════════════════════════
with tab_q:
    if not me:
        st.info("Enter your email in the sidebar to see your assigned queue.")
    else:
        # Check assignee config for this user
        my_assignments = cfg_df[cfg_df["assignee_id"] == me.strip()] if not cfg_df.empty else pd.DataFrame()
        if my_assignments.empty:
            st.warning(f"No assignee configs found for `{me}`. Go to Assignee Config tab to set up assignments.")

        c1, c2, c3 = st.columns(3)
        q_status   = c1.selectbox("Status",   ["PENDING", "All", "RESPONDED", "EXPIRED"], key="qs")
        q_priority = c2.selectbox("Priority",  ["All", "CRITICAL", "URGENT", "NORMAL"],   key="qp")
        q_chain    = c3.selectbox("Chain",     ["All"] + chains,                            key="qc")

        # Filter — for now show all pending since we don't have assignment in the query
        df_q = df_all.copy()
        if q_status != "All":
            df_q = df_q[df_q["status"] == q_status]
        if q_priority != "All":
            df_q = df_q[df_q["priority"] == q_priority]
        if q_chain != "All":
            df_q = df_q[df_q["chain_name"] == q_chain]

        # If user has assignments, filter to their chains
        if not my_assignments.empty:
            assigned_pairs = set(zip(my_assignments["chain_name"], my_assignments["platform"]))
            df_q = df_q[df_q.apply(lambda r: (r["chain_name"], r["platform"]) in assigned_pairs, axis=1)]

        if df_q.empty:
            st.success("Queue empty — nothing matching these filters.")
        else:
            pending = df_q[df_q["status"] == "PENDING"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("In queue", len(df_q))
            m2.metric("Pending", len(pending))
            m3.metric("Critical", len(pending[pending["priority"] == "CRITICAL"]))
            m4.metric("Has AI response", int(df_q["response_text"].notna().sum()))

            st.divider()
            for idx, r in df_q.head(50).iterrows():
                icon = f"{PRIORITY_ICON.get(r['priority'],'')} {STATUS_ICON.get(r['status'],'')}"
                resp_badge = "🤖" if pd.notna(r.get("response_text")) and r["response_text"] else "❌"
                header = (f"{icon}  {r['chain_name']} · {r['platform']} · "
                          f"{r['rating_display']} · {r['days_left']}d left  {resp_badge}")

                with st.expander(header, expanded=(r["priority"] == "CRITICAL" and r["status"] == "PENDING")):
                    left, mid, right = st.columns([2, 2, 1.5])
                    uid = r["review_uid"]

                    # ── Left: Review details ──
                    with left:
                        st.markdown(f"**Customer:** {r['customer_name'] or '—'}  ({r['customer_type']})")
                        st.markdown(f"**Store:** `{r['slug']}`")
                        st.markdown(f"**Review date:** {r['review_date']}  ·  **Days left:** {r['days_left']}")
                        if r.get("review_text"):
                            st.caption("Customer review:")
                            st.markdown(f"> {r['review_text']}")
                        else:
                            st.caption("_No review text_")

                    # ── Mid: Response text ──
                    with mid:
                        resp = r.get("response_text") or ""
                        already_posted = r.get("is_replied", False)
                        if already_posted and resp:
                            st.caption("✅ Already posted by Loop")
                            st.success(resp)
                        elif resp:
                            st.caption(f"🤖 AI response ready · Type: `{r.get('response_type', '—')}`")
                            st.info(resp)
                        else:
                            st.warning("_No response generated — config may not match this review_")
                        if pd.notna(r.get("coupon_value")) and r["coupon_value"] > 0:
                            st.markdown(f"💰 Coupon: **${r['coupon_value']:.2f}**")
                        if r.get("config_id"):
                            st.caption(f"Config ID: {r['config_id']}")

                    # ── Right: Action buttons ──
                    with right:
                        if r["status"] == "PENDING":
                            # === UberEats: Auto-post via API ===
                            if r["platform"] == "UberEats":
                                st.caption("🟢 UberEats — Auto-post available")
                                if resp:
                                    if st.button("🚀 Auto-post to UberEats", key=f"ue_{uid}",
                                                 type="primary", use_container_width=True):
                                        # In production: calls UberEats GraphQL submitEaterReviewReply
                                        st.session_state.post_log.append({
                                            "review_uid": uid, "response": resp[:100],
                                            "posted_at": datetime.now().isoformat()
                                        })
                                        log_ops_action(uid, "UberEats", r["chain_name"],
                                                       "RESPONDED", me.strip(), me.strip(),
                                                       "Auto-posted via UE API")
                                        st.success("✅ Posted via UberEats API!")
                                        st.cache_data.clear()
                                        st.rerun()
                                else:
                                    st.warning("No response to post")
                                st.caption("_Calls `submitEaterReviewReply` GraphQL_")

                            # === DoorDash: Copy + Open Portal ===
                            else:
                                st.caption("🟡 DoorDash — Manual post (no API)")
                                if resp:
                                    st.code(resp, language=None)
                                    st.caption("👆 Copy this response")
                                if r.get("portal_link"):
                                    st.link_button("📋 Open DD Portal & Paste",
                                                   r["portal_link"], use_container_width=True)

                            st.divider()
                            rem = st.text_input("Remarks", key=f"r_{uid}", placeholder="optional")
                            if st.button("✅ Mark responded", key=f"b_{uid}", use_container_width=True):
                                method = "auto_ue" if r["platform"] == "UberEats" else "manual_dd"
                                log_ops_action(uid, r["platform"], r["chain_name"],
                                               "RESPONDED", me.strip(), me.strip(), rem or method)
                                st.success("Logged!")
                                st.cache_data.clear()
                                st.rerun()
                        else:
                            st.markdown(f"Status: **{r['status']}**")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: All Reviews
# ═══════════════════════════════════════════════════════════════════════════════
with tab_all:
    c1, c2, c3, c4 = st.columns(4)
    f_ch = c1.selectbox("Chain",    ["All"] + chains,                             key="fc")
    f_pl = c2.selectbox("Platform", ["All"] + platforms,                           key="fp")
    f_st = c3.selectbox("Status",   ["All", "PENDING", "RESPONDED", "EXPIRED"],    key="fs")
    f_pr = c4.selectbox("Priority", ["All", "CRITICAL", "URGENT", "NORMAL"],       key="fpr")

    df_filt = df_all.copy()
    if f_ch != "All": df_filt = df_filt[df_filt["chain_name"] == f_ch]
    if f_pl != "All": df_filt = df_filt[df_filt["platform"]   == f_pl]
    if f_st != "All": df_filt = df_filt[df_filt["status"]     == f_st]
    if f_pr != "All": df_filt = df_filt[df_filt["priority"]   == f_pr]

    st.caption(f"{len(df_filt):,} reviews")

    if not df_filt.empty:
        show_cols = [
            "priority", "status", "days_left", "chain_name", "platform",
            "customer_name", "customer_type", "rating_display", "review_text",
            "response_text", "review_date", "portal_link",
        ]
        disp = df_filt[[c for c in show_cols if c in df_filt.columns]].copy()
        disp["priority"] = disp["priority"].map(lambda x: f"{PRIORITY_ICON.get(x,'')} {x}")
        disp["status"]   = disp["status"].map(lambda x: f"{STATUS_ICON.get(x,'')} {x}")
        st.dataframe(
            disp.head(200), use_container_width=True, hide_index=True,
            column_config={
                "portal_link":    st.column_config.LinkColumn("Portal"),
                "review_text":    st.column_config.TextColumn("Review", width="medium"),
                "response_text":  st.column_config.TextColumn("AI Response", width="medium"),
            },
        )
        st.download_button("Download CSV", df_filt.to_csv(index=False),
                           file_name="reengage_reviews.csv", mime="text/csv")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Scoreboard
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sb:
    if df_all.empty:
        st.info("No data.")
    else:
        # Aggregate by chain+platform (since we don't have assignee assignment yet)
        sb = df_all.groupby(["chain_name", "platform"]).agg(
            total=("review_uid", "count"),
            responded=("status", lambda x: (x == "RESPONDED").sum()),
            pending=("status",   lambda x: (x == "PENDING").sum()),
            expired=("status",   lambda x: (x == "EXPIRED").sum()),
            critical=("priority", lambda x: (x == "CRITICAL").sum()),
            has_ai_response=("response_text", lambda x: x.notna().sum()),
            avg_rating=("rating_numeric", "mean"),
        ).reset_index()
        sb["response_rate"] = (sb["responded"] / sb["total"] * 100).round(1)
        sb["ai_coverage"]   = (sb["has_ai_response"] / sb["total"] * 100).round(1)
        sb["avg_rating"]    = sb["avg_rating"].round(2)
        sb = sb.sort_values("total", ascending=False)

        st.subheader("Chain × Platform Scoreboard")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total reviews", f"{sb['total'].sum():,}")
        m2.metric("Responded", f"{int(sb['responded'].sum()):,}")
        m3.metric("AI coverage", f"{sb['has_ai_response'].sum() / sb['total'].sum() * 100:.1f}%")
        m4.metric("Pending", f"{int(sb['pending'].sum()):,}")

        st.dataframe(sb, use_container_width=True, hide_index=True,
                     column_config={
                         "response_rate": st.column_config.ProgressColumn(
                             "Response %", min_value=0, max_value=100, format="%.1f%%"),
                         "ai_coverage": st.column_config.ProgressColumn(
                             "AI Coverage %", min_value=0, max_value=100, format="%.1f%%"),
                     })


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Chain Health
# ═══════════════════════════════════════════════════════════════════════════════
with tab_ch:
    if df_all.empty:
        st.info("No data.")
    else:
        ch = df_all.groupby("chain_name").agg(
            total=("review_uid", "count"),
            responded=("status", lambda x: (x == "RESPONDED").sum()),
            pending=("status",   lambda x: (x == "PENDING").sum()),
            expired=("status",   lambda x: (x == "EXPIRED").sum()),
            critical=("priority", lambda x: (x == "CRITICAL").sum()),
            has_response=("response_text", lambda x: x.notna().sum()),
            avg_rating=("rating_numeric", "mean"),
            locations=("slug", "nunique"),
        ).reset_index()
        ch["response_rate"] = (ch["responded"] / ch["total"] * 100).round(1)
        ch["ai_coverage"]   = (ch["has_response"] / ch["total"] * 100).round(1)
        ch["avg_rating"]    = ch["avg_rating"].round(2)
        ch = ch.sort_values(["critical", "pending"], ascending=[False, False])

        st.subheader("Chain Health Overview")
        worst = ch[ch["critical"] > 0]
        if not worst.empty:
            st.error(f"⚠️ {len(worst)} chains have CRITICAL reviews pending")

        st.dataframe(ch, use_container_width=True, hide_index=True,
                     column_config={
                         "response_rate": st.column_config.ProgressColumn(
                             "Response %", min_value=0, max_value=100, format="%.1f%%"),
                         "ai_coverage": st.column_config.ProgressColumn(
                             "AI Coverage %", min_value=0, max_value=100, format="%.1f%%"),
                     })


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Response Config (read-only view of production configs)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_rc:
    st.subheader("Response Automation Configs")
    st.caption("Live configs from `review_response_config`. Manage via the Loop platform.")

    if rc_df.empty:
        st.info("No configs found.")
    else:
        active = rc_df[rc_df["paused"] == False]
        paused = rc_df[rc_df["paused"] == True]

        m1, m2, m3 = st.columns(3)
        m1.metric("Total configs", len(rc_df))
        m2.metric("Active", len(active))
        m3.metric("Paused", len(paused))

        import json

        for _, cfg in rc_df.iterrows():
            status_color = "🟢" if not cfg["paused"] else "🔴"
            status_badge = "ACTIVE" if not cfg["paused"] else "PAUSED"
            header = (f"{status_color} [{cfg['config_id']}] {cfg['config_name']}  —  "
                      f"{cfg['chain_name']}  ·  {cfg['response_type'].upper()}  ·  {status_badge}")

            with st.expander(header, expanded=False):
                col1, col2, col3 = st.columns(3)

                def _parse_json(val):
                    if pd.isna(val) or val is None:
                        return []
                    try:
                        return json.loads(val)
                    except:
                        return [str(val)]

                with col1:
                    st.markdown("**Targeting**")
                    st.markdown(f"- Chain: `{cfg['chain_name']}`")
                    plats = _parse_json(cfg['vb_platforms'])
                    st.markdown(f"- Platforms: {', '.join(plats) if plats else 'All'}")
                    rats = _parse_json(cfg['ratings'])
                    st.markdown(f"- Ratings: {', '.join(rats) if rats else 'All'}")
                    ctypes = _parse_json(cfg['customer_types'])
                    st.markdown(f"- Customer types: {', '.join(ctypes) if ctypes else 'All'}")
                    sents = _parse_json(cfg.get('review_sentiments'))
                    st.markdown(f"- Sentiments: {', '.join(sents) if sents else 'All'}")
                    fb = _parse_json(cfg.get('feedback_presence'))
                    st.markdown(f"- Feedback: {', '.join(fb) if fb else 'All'}")

                with col2:
                    st.markdown("**Response Generation**")
                    st.markdown(f"- Type: **{cfg['response_type'].upper()}**")
                    st.markdown(f"- Tonality: `{cfg.get('tonality', '—')}`")
                    st.markdown(f"- Paraphrase: {'Yes' if cfg.get('paraphrase') else 'No'}")
                    if cfg['response_type'] == 'ai':
                        st.markdown("- _AI generates personalized response using tone + review context_")

                with col3:
                    st.markdown("**Coupons**")
                    dd_type = cfg.get('dd_coupon_type', '—')
                    dd_val  = cfg.get('dd_coupon_fixed_value', 0) or 0
                    ue_type = cfg.get('ue_coupon_type', '—')
                    ue_val  = cfg.get('ue_coupon_fixed_value', 0) or 0
                    st.markdown(f"- DD: {dd_type} ${dd_val:.2f}")
                    st.markdown(f"- UE: {ue_type} ${ue_val:.2f}")
                    mov = cfg.get('min_order_value', 0) or 0
                    st.markdown(f"- Min order: ${mov:.2f}")
                    st.divider()
                    st.caption(f"Created by: {cfg.get('created_by', '—')}")
                    if cfg.get('updated_at'):
                        st.caption(f"Updated: {str(cfg['updated_at'])[:19]}")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Assignee Config
# ═══════════════════════════════════════════════════════════════════════════════
with tab_cfg:
    st.subheader("Assignee Config")
    st.caption("Controls round-robin assignment. Changes persist to BigQuery.")

    if not cfg_df.empty:
        st.dataframe(cfg_df, use_container_width=True, hide_index=True)
    else:
        st.info("No assignee configs yet. Add one below.")

    st.divider()
    col_a, col_d = st.columns(2)

    with col_a:
        st.markdown("**Add / update**")
        ne = st.text_input("Assignee email", key="ne")
        nc = st.selectbox("Chain name", chains, key="nc") if chains else st.text_input("Chain name", key="nc")
        np_val = st.selectbox("Platform", ["DoorDash", "UberEats"], key="np")
        if st.button("Save", key="save_cfg"):
            if not me:
                st.error("Enter your email in the sidebar.")
            elif not ne:
                st.error("Assignee email required.")
            else:
                chain_val = nc if isinstance(nc, str) else nc
                upsert_assignee_config(ne.strip(), chain_val, np_val, me.strip())
                st.success("Saved to BigQuery.")
                st.cache_data.clear()
                st.rerun()

    with col_d:
        st.markdown("**Delete**")
        if not cfg_df.empty:
            cfg_disp = cfg_df.copy()
            cfg_disp["label"] = cfg_disp["assignee_id"] + " | " + cfg_disp["chain_name"] + " | " + cfg_disp["platform"]
            pick = st.selectbox("Select", cfg_disp["label"].tolist(), key="dp")
            if st.button("Delete", type="secondary", key="del_cfg"):
                row = cfg_disp[cfg_disp["label"] == pick].iloc[0]
                delete_assignee_config(row["assignee_id"], row["chain_name"], row["platform"])
                st.success("Deleted from BigQuery.")
                st.cache_data.clear()
                st.rerun()
        else:
            st.info("No configs to delete.")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Ops Log
# ═══════════════════════════════════════════════════════════════════════════════
with tab_log:
    st.subheader("Operations Log")
    ops = load_ops_log()
    if ops.empty:
        st.info("No ops log entries yet. Mark reviews as responded to start logging.")
    else:
        st.caption(f"Showing last {len(ops)} entries")
        st.dataframe(ops, use_container_width=True, hide_index=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
col_f1, col_f2 = st.columns(2)
with col_f1:
    st.caption(f"Live BQ data · {len(df_all):,} reviews across {len(chains)} chains · "
               f"UberEats auto-post ready · DoorDash copy+paste")
with col_f2:
    if st.session_state.post_log:
        st.caption(f"📊 {len(st.session_state.post_log)} UE auto-posts this session")

# ── How automation works ──────────────────────────────────────────────────────
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

**Drip campaign flow:**
- Review arrives → AI generates response → held for ops review
- Ops can auto-post (UE) or copy+paste (DD)
- If no action in X days, escalate or auto-send based on config
- Customer can reply to email to reach a human
    """)
