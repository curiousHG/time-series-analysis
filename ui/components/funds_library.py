"""Funds Library — metadata table with search, add, and on-demand fetch."""

import polars as pl
import streamlit as st

from data.repositories.metadata import ensure_metadata, refresh_metadata
from data.repositories.registry import save_to_registry
from ui.components.fund_picker import add_schemes
from ui.state.loaders import cached_search, load_metadata_cached


def _bust_caches():
    load_metadata_cached.clear()


def _search_and_add():
    query = st.text_input(
        "Search funds (AdvisorKhoj)",
        placeholder="e.g. HDFC Top 100",
        key="funds_library_search",
    )
    if not query or len(query) < 3:
        return

    with st.spinner("Searching…"):
        results = cached_search(query)
    if results.is_empty():
        st.caption("No matches.")
        return

    options = results["schemeName"].to_list()
    picked = st.multiselect(
        "Search results",
        options=options,
        key="funds_library_picked",
    )
    if picked and st.button("Add to library", key="funds_library_add"):
        save_to_registry(picked)
        added = add_schemes(picked)
        st.success(f"Added {len(added)} fund(s) to selection.")
        _bust_caches()
        st.rerun()


def render(selected_registry: pl.DataFrame):
    """Render the metadata table and search/add/fetch controls."""
    st.subheader("Funds Library")

    if selected_registry.height == 0:
        st.info("No funds selected. Use the sidebar picker or search below to add some.")
        _search_and_add()
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

    summary = f"{len(have)} / {len(scheme_names)} funds have metadata"
    if missing:
        summary += f" — **{len(missing)} missing**"
    st.caption(summary)

    # ---- Build display dataframe (one row per selected fund, including missing)
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
                    "Launch": str(r.get("launchDate")) if r.get("launchDate") else "—",
                    "AUM as of": str(r.get("aumAsOf")) if r.get("aumAsOf") else "—",
                    "Status": "✓",
                    "_scheme_name": name,
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
                    "Launch": "—",
                    "AUM as of": "—",
                    "Status": "missing",
                    "_scheme_name": name,
                }
            )

    import pandas as pd

    df = pd.DataFrame(rows).drop(columns=["_scheme_name"])

    def _color_status(val: str) -> str:
        if val == "✓":
            return "background-color: #86efac; color: #14532d"
        if val == "missing":
            return "background-color: #fde68a; color: #78350f"
        return ""

    st.dataframe(
        df.style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "AUM (₹ Cr)": st.column_config.NumberColumn(format="%.2f"),
            "TER %": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    # ---- Action row: bulk fetch + per-fund refresh
    c1, c2, c3 = st.columns([2, 2, 3])

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
        )
        if st.button("Refresh selected", use_container_width=True):
            with st.spinner(f"Refreshing {short_by_name.get(sel, sel)}…"):
                refresh_metadata(sel)
            _bust_caches()
            st.rerun()

    with c3, st.expander("Add a fund (search AdvisorKhoj)"):
        _search_and_add()
