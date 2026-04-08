"""Chain Health tab — per-chain aggregation."""

import streamlit as st


def render(df_all):
    if df_all.empty:
        st.info("No data.")
        return

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
