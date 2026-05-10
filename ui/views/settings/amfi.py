"""Settings → AMFI Master Data section."""

from __future__ import annotations

import streamlit as st

from data.repositories.amfi import get_scheme_count, sync_amfi_master


def render() -> None:
    st.divider()
    st.subheader("AMFI Master Data")

    amfi_count = get_scheme_count()
    st.write(f"**{amfi_count:,}** schemes in database")

    if st.button("Sync AMFI Master", type="primary"):
        with st.spinner("Downloading AMFI NAVAll.txt..."):
            count = sync_amfi_master()
        st.success(f"Synced **{count:,}** schemes from AMFI")
        st.rerun()
