"""Funds Library — metadata table with bulk fetch and per-fund refresh."""

import polars as pl
import streamlit as st

from data.repositories.metadata import ensure_metadata, refresh_metadata
from ui.state.loaders import load_metadata_cached


def _bust_caches():
    load_metadata_cached.clear()


def render(selected_registry: pl.DataFrame):
    """Render the metadata table and fetch/refresh controls."""
    if selected_registry.height == 0:
        st.info("Select funds from the sidebar to start analysis.")
        return

    scheme_names = selected_registry["schemeName"].to_list()
    short_by_name = (
        dict(zip(selected_registry["schemeName"].to_list(), selected_registry["shortName"].to_list(), strict=False))
        if "shortName" in selected_registry.columns
        else {n: n for n in scheme_names}
    )

    meta_df = load_metadata_cached(tuple(sorted(scheme_names)))
    have = set(meta_df["schemeName"].to_list()) if meta_df.height else set()
    missing = [n for n in scheme_names if n not in have]

    with st.expander(
        f"Funds Library — {len(have)} / {len(scheme_names)} have metadata"
        + (f" · {len(missing)} missing" if missing else ""),
        expanded=bool(missing),
    ):
        rows = []
        for name in sorted(scheme_names, key=lambda n: short_by_name.get(n, n)):
            m = meta_df.filter(pl.col("schemeName") == name) if meta_df.height else pl.DataFrame()
            if m.height:
                r = m.row(0, named=True)
                rows.append(
                    {
                        "Fund": short_by_name.get(name, name),
                        "AMC": r.get("fundHouse") or "—",
                        "AUM (₹ Cr)": r.get("aumCrores"),
                        "TER %": r.get("expenseRatio"),
                        "Category": r.get("category") or "—",
                        "Benchmark": r.get("benchmark") or "—",
                    }
                )
            else:
                rows.append(
                    {
                        "Fund": short_by_name.get(name, name),
                        "AMC": "—",
                        "AUM (₹ Cr)": None,
                        "TER %": None,
                        "Category": "—",
                        "Benchmark": "—",
                    }
                )

        import pandas as pd

        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "AUM (₹ Cr)": st.column_config.NumberColumn(format="%.2f"),
                "TER %": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button(
                f"Fetch missing ({len(missing)})",
                disabled=not missing,
                type="primary",
                use_container_width=True,
            ):
                with st.spinner(f"Fetching metadata for {len(missing)} fund(s)…"):
                    ensure_metadata(missing)
                _bust_caches()
                st.rerun()

        with c2:
            sel = st.selectbox(
                "Refresh metadata for…",
                options=scheme_names,
                format_func=lambda n: short_by_name.get(n, n),
                key="funds_library_refresh_pick",
                label_visibility="collapsed",
            )
            if st.button("Refresh selected", use_container_width=True):
                with st.spinner(f"Refreshing {short_by_name.get(sel, sel)}…"):
                    refresh_metadata(sel)
                _bust_caches()
                st.rerun()
