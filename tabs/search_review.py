"""Search Review tab — lead-only. Look up a review by unique ID and see assignment/history."""

import pandas as pd
import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON
from data_loaders import load_assignments, load_ops_log


def _v(val, default="—"):
    """Safe value display — handles pandas NA/NaN/None."""
    if pd.isna(val):
        return default
    return val


def render(df_all):
    st.subheader("Search Reviews")
    st.caption("Look up a specific review by its unique identifier.")

    # ── Search mode ──
    search_mode = st.radio("Search mode", ["By ID", "By Customer Name + Chain"],
                           horizontal=True, key="sr_mode")

    if search_mode == "By ID":
        c1, c2 = st.columns([1, 3])
        with c1:
            field = st.selectbox("Search by", [
                "review_uid", "order_id", "review_id", "customer_id",
            ], key="sr_field", format_func=lambda x: {
                "review_uid": "Review UID",
                "order_id": "Order ID",
                "review_id": "Review ID",
                "customer_id": "Customer ID",
            }.get(x, x))
        with c2:
            query = st.text_input("Paste ID", placeholder="Paste the exact ID here...",
                                  key="sr_query")

        if not query or len(query.strip()) < 3:
            st.info("Paste a review UID, order ID, review ID, or customer ID to look it up.")
            return

        q = query.strip()
        if field not in df_all.columns:
            st.error(f"Field `{field}` not found in data.")
            return

        results = df_all[df_all[field].astype(str) == q].copy()
        if results.empty:
            results = df_all[df_all[field].astype(str).str.contains(q, na=False, case=False)].copy()

        if results.empty:
            st.warning(f"No reviews found with {field} = `{q}`")
            return

    else:
        # Customer name + chain search
        chains = sorted(df_all["chain_name"].unique().tolist()) if not df_all.empty else []
        c1, c2 = st.columns([2, 2])
        with c1:
            cust_name = st.text_input("Customer name", placeholder="Enter customer name...",
                                      key="sr_cust_name")
        with c2:
            chain_filter = st.selectbox("Chain", ["All"] + chains, key="sr_chain")

        if not cust_name or len(cust_name.strip()) < 2:
            st.info("Enter a customer name (at least 2 characters) and optionally select a chain.")
            return

        mask = df_all["customer_name"].astype(str).str.lower().str.contains(
            cust_name.strip().lower(), na=False)
        if chain_filter != "All":
            mask &= df_all["chain_name"] == chain_filter

        results = df_all[mask].copy()

        if results.empty:
            st.warning(f"No reviews found for customer '{cust_name}'"
                       + (f" in chain '{chain_filter}'" if chain_filter != "All" else ""))
            return

    st.success(f"Found {len(results)} review(s)")

    # ── Load assignment + ops log data ──
    assignments = load_assignments()
    ops = load_ops_log()

    asgn_map = {}
    if not assignments.empty:
        for _, a in assignments.iterrows():
            asgn_map[a["review_uid"]] = {
                "assigned_to": a["operator_email"],
                "assignment_status": a["status"],
                "assigned_at": a["assigned_at"],
                "completed_at": a.get("completed_at", ""),
            }

    ops_map = {}
    if not ops.empty:
        for _, o in ops.iterrows():
            uid = o["review_uid"]
            if uid not in ops_map:
                ops_map[uid] = []
            ops_map[uid].append({
                "action": o["action"],
                "by": o["performed_by"],
                "at": o["processing_timestamp"],
                "remarks": o.get("remarks", ""),
            })

    # ── Show each result ──
    for idx, r in results.iterrows():
        uid = r["review_uid"]
        icon = PRIORITY_ICON.get(r["priority"], "")
        status_icon = STATUS_ICON.get(r["status"], "")
        asgn = asgn_map.get(uid)

        st.markdown("---")
        st.markdown(f"### {icon} {status_icon} {r['chain_name']} · {r.get('brand_name') or ''} · "
                    f"{r['platform']} · {r['rating_display']}")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Review details**")
            st.markdown(f"- **Review UID:** `{uid}`")
            st.markdown(f"- **Order ID:** `{r.get('order_id') or '—'}`")
            st.markdown(f"- **Review ID:** `{r.get('review_id') or '—'}`")
            st.markdown(f"- **Store:** `{r['slug']}` · ID: `{r['store_id'] or '—'}`")
            st.markdown(f"- **Brand ID:** `{r.get('b_name_id') or '—'}`")
            st.markdown(f"- **Date:** {r['review_date']} · **Days left:** {r['days_left']}")
            st.markdown(f"- **Status:** {r['status']} · **Priority:** {r['priority']}")
            if r.get("portal_link"):
                st.link_button("Open portal", r["portal_link"])

        with col2:
            st.markdown("**Customer**")
            st.markdown(f"- **Name:** {_v(r['customer_name'])}")
            st.markdown(f"- **ID:** `{_v(r.get('customer_id'))}`")
            st.markdown(f"- **Type:** {_v(r['customer_type'])}")
            st.markdown(f"- **Orders:** {_v(r.get('orders_count'))}")
            if pd.notna(r.get("order_value")):
                st.markdown(f"- **Order value:** ${r['order_value']:.2f}")
            if r.get("items"):
                st.markdown(f"- **Items:** {r['items']}")
            st.markdown("")
            st.markdown("**Rating**")
            st.markdown(f"- **Display:** {r['rating_display']}")
            st.markdown(f"- **Raw value:** `{_v(r.get('rating_value'), _v(r.get('star_rating')))}`")
            st.markdown(f"- **Type:** {_v(r.get('rating_type'))}")

        with col3:
            st.markdown("**Assignment**")
            if asgn:
                st.markdown(f"- **Assigned to:** `{asgn['assigned_to']}`")
                st.markdown(f"- **Status:** {asgn['assignment_status']}")
                st.markdown(f"- **Assigned at:** {asgn['assigned_at']}")
                if asgn.get("completed_at"):
                    st.markdown(f"- **Completed at:** {asgn['completed_at']}")
            else:
                st.caption("Not assigned to any operator.")

            history = ops_map.get(uid, [])
            if history:
                st.markdown("**Action history**")
                for entry in history:
                    st.markdown(f"- **{entry['action']}** by `{entry['by']}` at {entry['at']}")
                    if entry.get("remarks"):
                        st.caption(f"  _{entry['remarks']}_")
            else:
                st.caption("No action history.")

        # Review + response text
        if r.get("review_text"):
            st.markdown("**Customer review:**")
            st.markdown(f"> {r['review_text']}")

        if r.get("response_text"):
            st.markdown("**AI Response:**")
            st.code(r["response_text"], language=None)
            if pd.notna(r.get("coupon_value")) and r["coupon_value"] > 0:
                st.markdown(f"💰 Coupon: **${r['coupon_value']:.2f}**")
