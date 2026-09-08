"""Settings → AMFI universe: size, freshness, and the sync action."""

from __future__ import annotations

import streamlit as st

from data.repositories.amfi import get_scheme_count, latest_amfi_nav_date, sync_amfi_master
from ui.components.chrome import section_header
from ui.views.settings.format import fmt_date, plural


def render() -> None:
    actions = section_header("AMFI universe")
    with actions:
        if st.button("Sync AMFI master", use_container_width=True):
            with st.spinner("Downloading NAVAll.txt…"):
                count = sync_amfi_master()
            st.toast(f"Synced {count:,} schemes from AMFI.", icon="✅")
            st.rerun()

    count = get_scheme_count()
    if count == 0:
        st.markdown("Not synced yet. The master list is what search and ISIN matching run on.")
        return
    st.markdown(f"{plural(count, 'scheme')}, NAVs as of {fmt_date(latest_amfi_nav_date())}.")
