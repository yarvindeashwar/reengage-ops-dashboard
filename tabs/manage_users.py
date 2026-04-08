"""Manage Users tab — lead-only page for adding/editing/deleting operators."""

import streamlit as st

from config import ALL_ROLES, ROLE_LEAD, ROLE_TENURED, ROLE_NEW
from auth import load_all_users, add_user, update_user_role, update_user_approval, remove_user
from assignment import run_assignment

ROLE_LABELS = {
    ROLE_LEAD: "Lead",
    ROLE_TENURED: "Tenured Operator (full weight)",
    ROLE_NEW: "New Operator (reduced weight)",
}


def render(current_user_email):
    st.subheader("Manage Users")
    st.caption("Add, edit roles, set reduction %, or remove operators. Only leads can access this page.")

    users_df = load_all_users()

    if not users_df.empty:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total users", len(users_df))
        m2.metric("Leads", int((users_df["role"] == ROLE_LEAD).sum()))
        m3.metric("Tenured", int((users_df["role"] == ROLE_TENURED).sum()))
        m4.metric("New operators", int((users_df["role"] == ROLE_NEW).sum()))

        st.divider()

        disp = users_df.copy()
        disp["role_display"] = disp["role"].map(ROLE_LABELS)
        disp["reduction"] = disp.apply(
            lambda r: f"{r['reduction_pct']:.0f}%" if r["role"] == ROLE_NEW else "—", axis=1
        )
        disp["access"] = disp["approved"].map({True: "✅ Active", False: "🚫 Revoked"})
        st.dataframe(
            disp[["email", "name", "role_display", "reduction", "access", "added_by", "created_at"]],
            use_container_width=True, hide_index=True,
            column_config={"role_display": "Role", "reduction": "Reduction %", "access": "Access"},
        )
    else:
        st.info("No users found. Add the first user below.")

    st.divider()

    col_add, col_edit = st.columns(2)

    # ── Add new user ──
    with col_add:
        st.markdown("**Add new user**")
        new_email = st.text_input("Email", key="mu_email", placeholder="user@loopkitchen.com")
        new_name = st.text_input("Name", key="mu_name", placeholder="Full name")
        new_role = st.selectbox("Role", ALL_ROLES, key="mu_role",
                                format_func=lambda r: ROLE_LABELS.get(r, r))
        new_reduction = 0.0
        if new_role == ROLE_NEW:
            new_reduction = st.number_input(
                "Reduction %", min_value=0.0, max_value=90.0, value=20.0, step=5.0,
                key="mu_reduction",
                help="Percentage of reviews to subtract from this operator's share (redistributed to others)",
            )

        if st.button("Add user", key="mu_add", type="primary"):
            if not new_email or not new_name:
                st.error("Email and name are required.")
            else:
                add_user(new_email.strip(), new_name.strip(), new_role, new_reduction, current_user_email)
                st.success(f"Added {new_email} as {ROLE_LABELS[new_role]}")
                st.cache_data.clear()
                st.rerun()

    # ── Edit / delete existing user ──
    with col_edit:
        st.markdown("**Edit existing user**")
        if not users_df.empty:
            edit_email = st.selectbox("Select user", users_df["email"].tolist(), key="mu_edit_email")
            selected = users_df[users_df["email"] == edit_email].iloc[0]

            current_role = selected["role"]
            current_reduction = float(selected.get("reduction_pct", 0))
            st.caption(f"Current: **{ROLE_LABELS.get(current_role, current_role)}** · "
                       f"Reduction: **{current_reduction:.0f}%** · "
                       f"Approved: **{selected['approved']}**")

            new_role_edit = st.selectbox(
                "Change role", ALL_ROLES, key="mu_edit_role",
                index=ALL_ROLES.index(current_role) if current_role in ALL_ROLES else 0,
                format_func=lambda r: ROLE_LABELS.get(r, r),
            )
            new_reduction_edit = current_reduction
            if new_role_edit == ROLE_NEW:
                new_reduction_edit = st.number_input(
                    "Reduction %", min_value=0.0, max_value=90.0,
                    value=current_reduction, step=5.0, key="mu_edit_reduction",
                )

            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("Update role", key="mu_update_role"):
                    update_user_role(edit_email, new_role_edit,
                                     new_reduction_edit if new_role_edit == ROLE_NEW else 0.0)
                    st.success(f"Updated {edit_email}")
                    st.cache_data.clear()
                    st.rerun()
            with c2:
                if selected["approved"]:
                    if st.button("Revoke access", key="mu_revoke", type="secondary"):
                        update_user_approval(edit_email, False)
                        st.success(f"Revoked access for {edit_email}")
                        st.cache_data.clear()
                        st.rerun()
                else:
                    if st.button("Approve access", key="mu_approve", type="primary"):
                        update_user_approval(edit_email, True)
                        st.success(f"Approved {edit_email}")
                        st.cache_data.clear()
                        st.rerun()
            with c3:
                if edit_email != current_user_email:
                    if st.button("🗑 Delete user", key="mu_remove", type="secondary"):
                        remove_user(edit_email)
                        st.success(f"Removed {edit_email}")
                        st.cache_data.clear()
                        st.rerun()
                else:
                    st.caption("_Can't delete yourself_")
        else:
            st.info("No users to edit yet.")

    # ── Manual assignment trigger ──
    st.divider()
    st.markdown("**Run assignment engine**")
    st.caption("Normally runs at 8 AM daily. Use this button to trigger manually.")
    if st.button("🔄 Run assignment now", key="mu_run_assign"):
        with st.spinner("Assigning reviews..."):
            result = run_assignment()
        if "error" in result:
            st.error(result["error"])
        else:
            st.success(f"Assigned **{result.get('assigned', 0)}** reviews to "
                       f"**{result.get('operators', 0)}** operators")
            if "per_operator" in result:
                for email, info in result["per_operator"].items():
                    st.caption(f"  {email}: {info['count']} reviews, "
                               f"{info['chain_platforms']} chain×platform combos")
            st.cache_data.clear()
            st.rerun()
