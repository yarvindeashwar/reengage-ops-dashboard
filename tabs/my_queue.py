"""My Queue tab — table view with generate responses and inline mark responded."""

import pandas as pd
import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON
from data_loaders import load_my_assignments, load_configs_for_matching
from write_helpers import log_action, mark_assignment_completed


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
    # Sort: chain_name first, then DD before UE within each chain, then by days_left
    platform_order = {"Doordash": 0, "UberEats": 1}
    df_q["_plat_order"] = df_q["platform"].map(platform_order).fillna(2)
    df_q = df_q.sort_values(
        ["chain_name", "_plat_order", "days_left"],
        ascending=[True, True, True]
    ).drop(columns=["_plat_order"]).reset_index(drop=True)

    # ── Metrics ──
    pending = df_q[df_q["status"] == "PENDING"]
    no_response = df_q[df_q["response_text"].isna()]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Assigned", len(df_q))
    m2.metric("Pending", len(pending))
    m3.metric("Critical", len(pending[pending["priority"] == "CRITICAL"]) if not pending.empty else 0)
    m4.metric("Missing AI response", len(no_response))

    # ── Generate Responses button ──
    if len(no_response) > 0:
        st.divider()
        st.markdown(f"**{len(no_response)} reviews** don't have AI responses yet.")
        if st.button("✨ Generate Responses", type="primary", key="gen_resp"):
            _generate_responses(no_response, user_email)

    # ── Filters ──
    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    chains = ["All"] + sorted(df_q["chain_name"].dropna().unique().tolist())
    platforms = ["All"] + sorted(df_q["platform"].dropna().unique().tolist())
    f_chain = c1.selectbox("Chain", chains, key="mq_chain")
    f_platform = c2.selectbox("Platform", platforms, key="mq_plat")
    f_priority = c3.selectbox("Priority", ["All", "CRITICAL", "URGENT", "NORMAL"], key="mq_pri")
    f_status = c4.selectbox("Status", ["PENDING", "All", "RESPONDED", "EXPIRED"], key="mq_st")
    f_has_resp = c5.selectbox("AI Response", ["All", "Has response", "No response"], key="mq_resp")

    df_show = df_q.copy()
    if f_chain != "All":
        df_show = df_show[df_show["chain_name"] == f_chain]
    if f_platform != "All":
        df_show = df_show[df_show["platform"] == f_platform]
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

    st.caption(f"Showing {len(df_show)} reviews")

    # ── Table with all columns ──
    table_cols = [
        "review_uid", "order_id", "review_id",
        "priority", "status", "days_left",
        "chain_name", "brand_name", "b_name_id", "platform",
        "slug", "store_id",
        "customer_name", "customer_id", "customer_type", "orders_count",
        "rating_display", "rating_value",
        "order_value", "items",
        "review_text", "response_text",
        "review_date", "portal_link",
    ]
    table = df_show[[c for c in table_cols if c in df_show.columns]].copy()
    table.insert(0, "#", range(1, len(table) + 1))
    table["priority"] = table["priority"].map(lambda x: f"{PRIORITY_ICON.get(x, '')} {x}")
    table["status"] = table["status"].map(lambda x: f"{STATUS_ICON.get(x, '')} {x}")

    st.dataframe(
        table, use_container_width=True, hide_index=True, height=400,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "days_left": st.column_config.NumberColumn("Days", width="small"),
            "order_value": st.column_config.NumberColumn("Order $", format="$%.0f"),
            "review_text": st.column_config.TextColumn("Review", width="medium"),
            "response_text": st.column_config.TextColumn("AI Response", width="medium"),
            "items": st.column_config.TextColumn("Items", width="medium"),
            "portal_link": st.column_config.LinkColumn("Portal"),
        },
    )

    # ── Copy AI response + Mark responded ──
    st.divider()
    c1, c2 = st.columns([3, 1])
    with c1:
        options = []
        for _, r in df_show.iterrows():
            icon = PRIORITY_ICON.get(r["priority"], "")
            resp_tag = "🤖" if pd.notna(r.get("response_text")) else "❌"
            label = (f"{icon} {r['chain_name']} · {r['platform']} · "
                     f"{r['rating_display']} · {r['days_left']}d · "
                     f"{r['customer_name'] or '—'} · {r['order_id'] or ''} {resp_tag}")
            options.append(label)

        selected_idx = st.selectbox("Select review", range(len(options)),
                                    format_func=lambda i: options[i], key="mq_select")
    with c2:
        rem = st.text_input("Remarks", key="mq_rem", placeholder="optional")

    r = df_show.iloc[selected_idx]
    uid = r["review_uid"]
    asgn_id = r.get("assignment_id", "")
    resp = r.get("response_text") or ""

    # Copy button + response preview
    if resp:
        b1, b2 = st.columns([1, 4])
        with b1:
            st.code(f"📋 Copy", language=None)
        with b2:
            st.code(resp, language=None)
    if r.get("portal_link"):
        st.link_button("📋 Open portal", r["portal_link"])

    if st.button("✅ Mark responded", key=f"mr_{uid}", use_container_width=True):
        method = "manual_dd" if r["platform"] == "Doordash" else "manual_ue"
        log_action(uid, r["platform"], r["chain_name"],
                   "mark_responded", user_email, user_email, rem or method)
        if asgn_id:
            mark_assignment_completed(asgn_id, user_email)
        st.success("Done!")
        st.cache_data.clear()
        st.rerun()


def _generate_responses(no_response_df, user_email):
    """Generate AI responses for reviews that don't have one."""
    from response_generator import generate_and_save, find_matching_config
    from write_helpers import log_action

    configs = load_configs_for_matching()
    if not configs:
        st.error("No response configs found. Cannot generate responses.")
        return

    total = len(no_response_df)
    progress = st.progress(0, text=f"Generating responses... 0/{total}")
    success = 0
    skipped = 0
    failed = 0

    for i, (_, review) in enumerate(no_response_df.iterrows()):
        review_dict = review.to_dict()

        config = find_matching_config(review_dict, configs)
        if not config:
            skipped += 1
            progress.progress((i + 1) / total, text=f"Generating... {i+1}/{total} (no config match)")
            continue

        try:
            result = generate_and_save(review_dict, config)
            if result.get("saved"):
                success += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            st.caption(f"Error for {review_dict.get('order_id', '?')}: {e}")

        progress.progress((i + 1) / total, text=f"Generating... {i+1}/{total}")

    progress.empty()
    st.success(f"Done! Generated: **{success}** · Skipped (no config): **{skipped}** · Failed: **{failed}**")

    log_action("batch", "all", "all", "generate_responses", user_email, user_email,
               f"Generated {success}, skipped {skipped}, failed {failed}")

    st.cache_data.clear()
    st.rerun()
