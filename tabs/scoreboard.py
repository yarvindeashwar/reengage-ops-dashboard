"""Scoreboard tab — chain x platform aggregation."""

import streamlit as st


def render(df_all):
    if df_all.empty:
        st.info("No data.")
        return

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

    st.subheader("Chain x Platform Scoreboard")
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
