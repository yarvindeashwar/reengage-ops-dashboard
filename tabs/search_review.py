"""Search Review tab — lead-only. Search any review and see who it's assigned to / who responded."""

import pandas as pd
import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON
from data_loaders import load_assignments, load_ops_log


def render(df_all):
    st.subheader("Search Reviews")
    st.caption("Search any review by customer name, review text, order ID, chain, etc. "
               "See assignment and response history.")

    # ── Search box ──
    query = st.text_input("Search", placeholder="Customer name, order ID, chain, review text...",
                          key="sr_query")

    if not query or len(query) < 2:
        st.info("Enter at least 2 characters to search.")
        return

    q = query.strip().lower()

    # Search across multiple columns
    mask = pd.Series(False, index=df_all.index)
    search_cols = [
        "review_uid", "order_id", "review_id", "chain_name", "brand_name",
        "slug", "store_id", "customer_name", "customer_id",
        "review_text", "response_text", "platform",
    ]
    for col in search_cols:
        if col in df_all.columns:
            mask |= df_all[col].astype(str).str.lower().str.contains(q, na=False)

    results = df_all[mask].copy()

    if results.empty:
        st.warning(f"No reviews found matching '{query}'.")
        return

    st.caption(f"Found {len(results)} reviews matching '{query}'")

    # ── Load assignment + ops log data for enrichment ──
    assignments = load_assignments()
    ops = load_ops_log()

    # Build lookup: review_uid -> assignment info
    asgn_map = {}
    if not assignments.empty:
        for _, a in assignments.iterrows():
            asgn_map[a["review_uid"]] = {
                "assigned_to": a["operator_email"],
                "assignment_status": a["status"],
                "assigned_at": a["assigned_at"],
                "completed_at": a.get("completed_at", ""),
            }

    # Build lookup: review_uid -> ops log entries
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

    # ── Results table ──
    # Add assignment columns
    results["assigned_to"] = results["review_uid"].map(
        lambda uid: asgn_map.get(uid, {}).get("assigned_to", "—"))
    results["asgn_status"] = results["review_uid"].map(
        lambda uid: asgn_map.get(uid, {}).get("assignment_status", "—"))

    show_cols = [
        "priority", "status", "days_left",
        "chain_name", "brand_name", "platform",
        "customer_name", "customer_id", "rating_display",
        "review_text", "response_text",
        "assigned_to", "asgn_status",
        "review_date", "portal_link",
    ]
    disp = results[[c for c in show_cols if c in results.columns]].copy()
    disp["priority"] = disp["priority"].map(lambda x: f"{PRIORITY_ICON.get(x, '')} {x}")
    disp["status"] = disp["status"].map(lambda x: f"{STATUS_ICON.get(x, '')} {x}")

    st.dataframe(
        disp.head(100), use_container_width=True, hide_index=True,
        column_config={
            "days_left": st.column_config.NumberColumn("Days"),
            "review_text": st.column_config.TextColumn("Review", width="medium"),
            "response_text": st.column_config.TextColumn("AI Response", width="medium"),
            "portal_link": st.column_config.LinkColumn("Portal"),
            "assigned_to": st.column_config.TextColumn("Assigned To"),
            "asgn_status": st.column_config.TextColumn("Assignment"),
        },
    )

    # ── Detail view for selected review ──
    st.divider()
    st.markdown("**Review detail**")

    options = []
    for _, r in results.head(100).iterrows():
        icon = PRIORITY_ICON.get(r["priority"], "")
        label = (f"{icon} {r['chain_name']} · {r['platform']} · "
                 f"{r['customer_name'] or '—'} · {r['review_uid'][:20]}")
        options.append(label)

    if not options:
        return

    selected_idx = st.selectbox("Select review for detail", range(len(options)),
                                format_func=lambda i: options[i], key="sr_select")

    r = results.iloc[selected_idx]
    uid = r["review_uid"]

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**{r['chain_name']}** · {r.get('brand_name') or ''} · {r['platform']}")
        st.markdown(f"**Customer:** {r['customer_name'] or '—'} ({r['customer_type']}) · "
                    f"ID: `{r.get('customer_id') or '—'}`")
        st.markdown(f"**Review UID:** `{uid}`")
        st.markdown(f"**Order ID:** `{r.get('order_id') or '—'}`")
        st.markdown(f"**Rating:** {r['rating_display']} · **Status:** {r['status']}")

        if r.get("review_text"):
            st.markdown(f"> {r['review_text']}")

        if r.get("response_text"):
            st.markdown("**AI Response:**")
            st.code(r["response_text"], language=None)

    with col2:
        # Assignment info
        asgn = asgn_map.get(uid)
        if asgn:
            st.markdown("**Assignment**")
            st.markdown(f"- Assigned to: **{asgn['assigned_to']}**")
            st.markdown(f"- Status: **{asgn['assignment_status']}**")
            st.markdown(f"- Assigned at: {asgn['assigned_at']}")
            if asgn.get("completed_at"):
                st.markdown(f"- Completed at: {asgn['completed_at']}")
        else:
            st.caption("Not assigned to any operator.")

        # Ops log history
        history = ops_map.get(uid, [])
        if history:
            st.markdown("**Action history**")
            for entry in history:
                st.markdown(f"- **{entry['action']}** by `{entry['by']}` at {entry['at']}")
                if entry.get("remarks"):
                    st.caption(f"  _{entry['remarks']}_")
        else:
            st.caption("No action history for this review.")
