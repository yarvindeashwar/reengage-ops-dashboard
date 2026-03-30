"""All Reviews tab — global review browser with filters."""

import streamlit as st

from config import PRIORITY_ICON, STATUS_ICON


def render(df_all, chains, platforms):
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
        disp = df_filt[[c for c in show_cols if c in df_filt.columns]].copy()
        disp["priority"] = disp["priority"].map(lambda x: f"{PRIORITY_ICON.get(x,'')} {x}")
        disp["status"]   = disp["status"].map(lambda x: f"{STATUS_ICON.get(x,'')} {x}")
        st.dataframe(
            disp.head(200), use_container_width=True, hide_index=True,
            column_config={
                "days_left":      st.column_config.NumberColumn("Days", width="small"),
                "portal_link":    st.column_config.LinkColumn("Portal"),
                "review_text":    st.column_config.TextColumn("Review", width="medium"),
                "response_text":  st.column_config.TextColumn("AI Response", width="medium"),
                "items":          st.column_config.TextColumn("Items", width="medium"),
                "order_value":    st.column_config.NumberColumn("Order $", format="$%.0f"),
                "orders_count":   st.column_config.NumberColumn("Orders"),
            },
        )
        st.download_button("Download CSV", df_filt.to_csv(index=False),
                           file_name="reengage_reviews.csv", mime="text/csv")
