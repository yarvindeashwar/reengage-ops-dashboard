"""My Queue tab — compact table view with detail panel for selected review."""

from datetime import datetime

import pandas as pd
import requests
import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON
from data_loaders import load_my_assignments
from write_helpers import log_action, mark_assignment_completed

BACKEND_URL = "https://api.loopapplication.xyz"
UE_TIERS = ["NONE", "TIER_1", "TIER_2", "TIER_3"]


def _post_reply_ubereats(review_id: str, comment: str, promotion: str) -> dict:
    """Call the backend /actions/review/reply endpoint for UberEats."""
    resp = requests.post(
        f"{BACKEND_URL}/actions/review/reply",
        params={"reviewId": review_id},
        json={
            "platform": "uber_eats",
            "comment": comment,
            "promotion": promotion,
        },
        timeout=30,
    )
    return {"ok": resp.ok, "status": resp.status_code, "body": resp.text}


def _save_reply_doordash(review_id: str, comment: str, promotion: int) -> dict:
    """Call the backend /actions/review/reply endpoint for DoorDash.
    Saves to review_reply_internal with sent=False. Does NOT post to DD."""
    resp = requests.post(
        f"{BACKEND_URL}/actions/review/reply",
        params={"reviewId": review_id},
        json={
            "platform": "doordash",
            "comment": comment[:300],  # DD limit 300 chars
            "promotion": promotion,
        },
        timeout=30,
    )
    return {"ok": resp.ok, "status": resp.status_code, "body": resp.text}


def render(df_all, user_email):
    if not user_email:
        st.info("Not logged in.")
        return

    my_asgn = load_my_assignments(user_email)

    if my_asgn.empty:
        st.success("No pending assignments. Check back after the next assignment run (8 AM daily).")
        return

    assigned_uids = set(my_asgn["review_uid"].tolist())
    df_q = df_all[df_all["review_uid"].isin(assigned_uids)].copy()

    if df_q.empty:
        st.info("Your assigned reviews have all been responded to or expired.")
        return

    asgn_map = my_asgn.set_index("review_uid")["assignment_id"].to_dict()
    df_q["assignment_id"] = df_q["review_uid"].map(asgn_map)
    df_q = df_q.sort_values("days_left", ascending=True).reset_index(drop=True)

    # ── Metrics row ──
    pending = df_q[df_q["status"] == "PENDING"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Assigned", len(df_q))
    m2.metric("Pending", len(pending))
    m3.metric("Critical", len(pending[pending["priority"] == "CRITICAL"]) if not pending.empty else 0)
    m4.metric("Has AI response", int(df_q["response_text"].notna().sum()))

    # ── Filters ──
    c1, c2, c3 = st.columns(3)
    f_priority = c1.selectbox("Priority", ["All", "CRITICAL", "URGENT", "NORMAL"], key="mq_pri")
    f_status = c2.selectbox("Status", ["PENDING", "All", "RESPONDED", "EXPIRED"], key="mq_st")
    f_has_resp = c3.selectbox("AI Response", ["All", "Has response", "No response"], key="mq_resp")

    df_show = df_q.copy()
    if f_priority != "All":
        df_show = df_show[df_show["priority"] == f_priority]
    if f_status != "All":
        df_show = df_show[df_show["status"] == f_status]
    if f_has_resp == "Has response":
        df_show = df_show[df_show["response_text"].notna()]
    elif f_has_resp == "No response":
        df_show = df_show[df_show["response_text"].isna()]

    if df_show.empty:
        st.info("No reviews match these filters.")
        return

    # ── Compact table ──
    table = df_show[[
        "priority", "status", "days_left", "chain_name", "platform",
        "customer_name", "rating_display", "review_text", "response_text",
    ]].copy()
    table.insert(0, "#", range(1, len(table) + 1))
    table["priority"] = table["priority"].map(lambda x: f"{PRIORITY_ICON.get(x, '')} {x}")
    table["status"] = table["status"].map(lambda x: f"{STATUS_ICON.get(x, '')} {x}")
    table["review_text"] = table["review_text"].fillna("").str[:80]
    table["response_text"] = table["response_text"].fillna("—").str[:60]
    table.columns = [
        "#", "Priority", "Status", "Days", "Chain", "Platform",
        "Customer", "Rating", "Review (preview)", "AI Response (preview)",
    ]

    st.dataframe(
        table, use_container_width=True, hide_index=True, height=320,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Days": st.column_config.NumberColumn(width="small"),
            "Review (preview)": st.column_config.TextColumn(width="medium"),
            "AI Response (preview)": st.column_config.TextColumn(width="medium"),
        },
    )

    st.caption(f"Showing {len(df_show)} reviews. Select one below to take action.")

    # ── Select & act ──
    st.divider()

    options = []
    for _, r in df_show.iterrows():
        icon = PRIORITY_ICON.get(r["priority"], "")
        resp_tag = "🤖" if pd.notna(r.get("response_text")) else "❌"
        label = (f"{icon} {r['chain_name']} · {r['platform']} · "
                 f"{r['rating_display']} · {r['days_left']}d · "
                 f"{r['customer_name'] or '—'} {resp_tag}")
        options.append(label)

    selected_idx = st.selectbox(
        "Select a review to respond",
        range(len(options)),
        format_func=lambda i: options[i],
        key="mq_select",
    )

    r = df_show.iloc[selected_idx]
    uid = r["review_uid"]
    asgn_id = r.get("assignment_id", "")
    resp = r.get("response_text") or ""

    # ── Detail panel ──
    col_detail, col_action = st.columns([3, 2])

    with col_detail:
        st.markdown(f"**{r['chain_name']}** · {r['platform']} · {r['rating_display']} · "
                    f"**{r['days_left']}d left** · {r['customer_name'] or '—'} ({r['customer_type']})")
        st.markdown(f"Store: `{r['slug']}`  ·  Review date: {r['review_date']}")

        if r.get("review_text"):
            st.markdown(f"> {r['review_text']}")
        else:
            st.caption("_No review text_")

        if pd.notna(r.get("coupon_value")) and r["coupon_value"] > 0:
            st.markdown(f"💰 Coupon: **${r['coupon_value']:.2f}**")

    with col_action:
        if r["status"] != "PENDING":
            st.markdown(f"Status: **{r['status']}**")
            return

        if r["platform"] == "UberEats":
            st.caption("🟢 UberEats — Auto-post available")
        else:
            st.caption("🟡 DoorDash — Copy & paste")
            if r.get("portal_link"):
                st.link_button("📋 Open DD Portal",
                               r["portal_link"], use_container_width=True)

    # ── Response editor (full width below detail) ──
    st.markdown("---")
    st.markdown("**Response to post:**")
    edited_resp = st.text_area(
        "Edit or write your response",
        value=resp,
        height=120,
        key=f"resp_{uid}",
        placeholder="No AI response available — write your own response here...",
        label_visibility="collapsed",
    )

    if not resp and not edited_resp:
        st.caption("💡 No AI response was generated. Write a custom response above.")
    elif edited_resp != resp:
        st.caption("✏️ Response modified from AI original")

    # ── Platform-specific options ──
    coupon_default = int(r["coupon_value"]) if pd.notna(r.get("coupon_value")) and r["coupon_value"] > 0 else 0

    if r["platform"] == "UberEats":
        t1, t2 = st.columns([1, 1])
        with t1:
            tier = st.selectbox("Promotion tier", UE_TIERS, key=f"tier_{uid}",
                                help="UberEats decides the dollar amount per tier. "
                                     "NONE = no promo, TIER_1/2/3 = increasing value.")
        with t2:
            if coupon_default > 0:
                st.caption(f"💡 Config suggests **${coupon_default}** coupon")
            else:
                st.caption("Config: no coupon configured")
    else:
        t1, t2 = st.columns([1, 1])
        with t1:
            dd_promo = st.number_input("Coupon $ amount", min_value=0, value=coupon_default,
                                       step=1, key=f"ddpromo_{uid}",
                                       help="Dollar amount for DoorDash promotion (0 = none)")
        with t2:
            if coupon_default > 0:
                st.caption(f"💡 Config suggests **${coupon_default}** coupon")
            else:
                st.caption("Config: no coupon configured")
        if edited_resp and len(edited_resp) > 300:
            st.warning(f"⚠️ DoorDash limit is 300 chars. Current: {len(edited_resp)} chars.")

    # ── Action buttons ──
    b1, b2, b3 = st.columns([2, 2, 3])

    with b1:
        if r["platform"] == "UberEats" and edited_resp:
            if st.button("🚀 Auto-post to UberEats", key=f"ue_{uid}",
                         type="primary", use_container_width=True):
                with st.spinner("Posting to UberEats..."):
                    review_id = r.get("review_id") or r.get("order_id") or uid
                    result = _post_reply_ubereats(review_id, edited_resp, tier)

                if result["ok"]:
                    st.session_state.post_log.append({
                        "review_uid": uid, "response": edited_resp[:100],
                        "posted_at": datetime.now().isoformat(),
                    })
                    log_action(uid, "UberEats", r["chain_name"],
                               "auto_post", user_email, user_email,
                               f"Posted via UE API, tier={tier}")
                    if asgn_id:
                        mark_assignment_completed(asgn_id, user_email)
                    st.success("✅ Posted to UberEats!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(f"Failed to post (HTTP {result['status']}): {result['body'][:200]}")

        elif r["platform"] == "Doordash" and edited_resp:
            if st.button("💾 Save & Open DD Portal", key=f"dd_{uid}",
                         type="primary", use_container_width=True):
                with st.spinner("Saving reply..."):
                    review_id = r.get("review_id") or r.get("order_id") or uid
                    result = _save_reply_doordash(review_id, edited_resp, dd_promo)

                if result["ok"]:
                    log_action(uid, "Doordash", r["chain_name"],
                               "save_dd_reply", user_email, user_email,
                               f"Saved DD reply, promo=${dd_promo}")
                    st.success("✅ Reply saved! Now paste it in the DD portal.")
                    if r.get("portal_link"):
                        st.link_button("📋 Open DD Portal & Paste",
                                       r["portal_link"], use_container_width=True)
                else:
                    st.error(f"Failed to save (HTTP {result['status']}): {result['body'][:200]}")

    with b2:
        if st.button("✅ Mark responded", key=f"b_{uid}", use_container_width=True):
            method = "auto_ue" if r["platform"] == "UberEats" else "manual_dd"
            log_action(uid, r["platform"], r["chain_name"],
                       "mark_responded", user_email, user_email,
                       f"custom_response" if edited_resp != resp else method)
            if asgn_id:
                mark_assignment_completed(asgn_id, user_email)
            st.success("Done!")
            st.cache_data.clear()
            st.rerun()

    with b3:
        rem = st.text_input("Remarks", key=f"r_{uid}", placeholder="optional")
