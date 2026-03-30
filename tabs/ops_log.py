"""Ops Log tab — audit trail of review actions."""

import streamlit as st

from data_loaders import load_ops_log


def render():
    st.subheader("Operations Log")
    ops = load_ops_log()
    if ops.empty:
        st.info("No ops log entries yet. Mark reviews as responded to start logging.")
    else:
        st.caption(f"Showing last {len(ops)} entries")
        st.dataframe(ops, use_container_width=True, hide_index=True)
