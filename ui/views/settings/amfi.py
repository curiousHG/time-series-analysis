"""Settings → AMFI universe: size, freshness, and the sync action."""

from __future__ import annotations

import streamlit as st

from ui.components.chrome import section_header
from ui.state.loaders import load_amfi_status_cached, sync_amfi
from ui.views.settings.format import fmt_date, plural


def render() -> None:
    actions = section_header("AMFI universe")
    with actions:
        if st.button("Sync AMFI master", use_container_width=True):
            with st.spinner("Downloading NAVAll.txt…"):
                count = sync_amfi()
            st.toast(f"Synced {count:,} schemes from AMFI.", icon="✅")
            st.rerun()

    status = load_amfi_status_cached()
    count = status.scheme_count
    if count == 0:
        st.markdown("Not synced yet. The master list is what search and ISIN matching run on.")
        return
    st.markdown(f"{plural(count, 'scheme')}, NAVs as of {fmt_date(status.latest_nav_date)}.")
