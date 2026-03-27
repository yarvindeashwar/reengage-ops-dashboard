"""Response Config tab — read-only view of production configs."""

import json

import pandas as pd
import streamlit as st


def render(rc_df):
    st.subheader("Response Automation Configs")
    st.caption("Live configs from `review_response_config`. Manage via the Loop platform.")

    if rc_df.empty:
        st.info("No configs found.")
        return

    active = rc_df[rc_df["paused"] == False]

    # Unique chains with active automation (exclude chains that only have paused configs)
    paused_only_chains = set(rc_df[rc_df["paused"] == True]["chain_name"]) - set(active["chain_name"])
    unique_chains_with_automation = active["chain_name"].nunique()

    m1, m2, m3 = st.columns(3)
    m1.metric("Total configs", len(rc_df))
    m2.metric("Active", len(active))
    m3.metric("Customers with automation", unique_chains_with_automation)

    def _parse_json(val):
        if pd.isna(val) or val is None:
            return []
        try:
            return json.loads(val)
        except Exception:
            return [str(val)]

    for _, cfg in rc_df.iterrows():
        status_color = "🟢" if not cfg["paused"] else "🔴"
        status_badge = "ACTIVE" if not cfg["paused"] else "PAUSED"
        header = (f"{status_color} [{cfg['config_id']}] {cfg['config_name']}  —  "
                  f"{cfg['chain_name']}  ·  {cfg['response_type'].upper()}  ·  {status_badge}")

        with st.expander(header, expanded=False):
            col1, col2, col3 = st.columns(3)

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
