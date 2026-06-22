"""Settings → AMFI Master Data section."""

from __future__ import annotations

import streamlit as st

from data.repositories.amfi import get_scheme_count, load_recent_additions, sync_amfi_master


def render() -> None:
    st.markdown("#### AMFI Master")

    amfi_count = get_scheme_count()
    st.caption(f"{amfi_count:,} schemes in the local universe used for search and ISIN matching.")

    if st.button("Sync AMFI Master", type="secondary", use_container_width=False):
        with st.spinner("Downloading AMFI NAVAll.txt..."):
            count = sync_amfi_master()
        st.success(f"Synced **{count:,}** schemes from AMFI")
        st.rerun()

    recent = load_recent_additions(limit=25)
    if recent.is_empty():
        st.caption("No AMFI additions have been timestamped yet. Future syncs will record newly inserted schemes here.")
        return

    st.markdown("**Recently added to local DB**")
    st.dataframe(
        recent.to_pandas(),
        use_container_width=True,
        hide_index=True,
        column_config={
            "schemeCode": st.column_config.NumberColumn("Code", format="%d"),
            "schemeName": st.column_config.TextColumn("Scheme"),
            "fundHouse": st.column_config.TextColumn("AMC"),
            "category": st.column_config.TextColumn("Category"),
            "isinGrowth": st.column_config.TextColumn("ISIN"),
            "dbAddedAt": st.column_config.DatetimeColumn("Added to DB"),
        },
    )
