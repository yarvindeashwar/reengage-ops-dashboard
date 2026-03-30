"""Lead Overview tab — see all operators' assignments and jump into any queue."""

import pandas as pd
import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON
from data_loaders import load_assignments
from auth import load_all_users


def render(df_all):
    st.subheader("Operator Assignments Overview")
    st.caption("See what each operator is working on. Click into any operator to view their reviews.")

    assignments = load_assignments()
    users_df = load_all_users()

    if assignments.empty:
        st.info("No assignments yet. Run the assignment engine or wait for the 8 AM daily run.")
        return

    # Summary metrics
    pending_a = assignments[assignments["status"] == "pending"]
    completed_a = assignments[assignments["status"] == "completed"]
    expired_a = assignments[assignments["status"] == "expired"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total assignments", len(assignments))
    m2.metric("Pending", len(pending_a))
    m3.metric("Completed", len(completed_a))
    m4.metric("Expired", len(expired_a))

    st.divider()

    # Per-operator breakdown
    st.markdown("### Per-operator breakdown")

    op_summary = pending_a.groupby("operator_email").agg(
        pending_count=("assignment_id", "count"),
        chains=("chain_name", "nunique"),
        platforms=("platform", "nunique"),
        min_days_left=("days_left", "min"),
    ).reset_index()

    # Merge with user info
    if not users_df.empty:
        op_summary = op_summary.merge(
            users_df[["email", "name", "role", "reduction_pct"]],
            left_on="operator_email", right_on="email", how="left"
        ).drop(columns=["email"], errors="ignore")

    if not op_summary.empty:
        st.dataframe(
            op_summary.sort_values("pending_count", ascending=False),
            use_container_width=True, hide_index=True,
            column_config={
                "operator_email": "Operator",
                "pending_count": "Pending",
                "chains": "Chains",
                "platforms": "Platforms",
                "min_days_left": "Most Urgent (days)",
                "name": "Name",
                "role": "Role",
                "reduction_pct": "Reduction %",
            },
        )

    st.divider()

    # Drill into a specific operator
    st.markdown("### Drill into operator queue")
    operator_list = sorted(pending_a["operator_email"].unique().tolist()) if not pending_a.empty else []

    if not operator_list:
        st.info("No pending assignments to drill into.")
        return

    selected_op = st.selectbox("Select operator", operator_list, key="lead_op_select")

    op_assignments = pending_a[pending_a["operator_email"] == selected_op]
    op_uids = set(op_assignments["review_uid"].tolist())

    df_op = df_all[df_all["review_uid"].isin(op_uids)].copy()
    df_op = df_op.sort_values("days_left", ascending=True)

    if df_op.empty:
        st.info(f"No matching reviews for {selected_op}.")
        return

    # Chain×Platform combos for this operator
    combos = df_op.groupby(["chain_name", "platform"]).size().reset_index(name="count")
    st.caption(f"**{selected_op}** — " +
               " · ".join(f"{r['chain_name']}/{r['platform']} ({r['count']})" for _, r in combos.iterrows()))

    # Show reviews table
    show_cols = [
        "priority", "status", "days_left", "chain_name", "platform",
        "customer_name", "rating_display", "review_text",
        "response_text", "review_date", "portal_link",
    ]
    disp = df_op[[c for c in show_cols if c in df_op.columns]].copy()
    disp["priority"] = disp["priority"].map(lambda x: f"{PRIORITY_ICON.get(x,'')} {x}")
    disp["status"]   = disp["status"].map(lambda x: f"{STATUS_ICON.get(x,'')} {x}")

    st.dataframe(
        disp.head(100), use_container_width=True, hide_index=True,
        column_config={
            "portal_link":   st.column_config.LinkColumn("Portal"),
            "review_text":   st.column_config.TextColumn("Review", width="medium"),
            "response_text": st.column_config.TextColumn("AI Response", width="medium"),
        },
    )
