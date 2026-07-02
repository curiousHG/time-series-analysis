"""MF Analysis fund selector — sidebar filters over the tracked universe + selectbox."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import streamlit as st

from data.repositories.amfi import load_amfi_df
from data.repositories.scheme_metrics import load_metrics
from mutual_funds.display import detect_option, detect_plan, short_scheme_name
from services.screener_service import apply_name_filter
from ui.state.filter_persistence import cascade_subcategory_options, hydrate_filters, make_persist_callback

# Filter + selected-fund state persisted to selections.json so it survives page nav/refresh.
_MF_PERSIST_KEY = "mf_analysis_filters"
_MF_FILTER_DEFAULTS = {
    "mf_analysis_search": "",
    "mf_analysis_amc": [],
    "mf_analysis_cat": [],
    "mf_analysis_sub_cat": [],
    "mf_analysis_plan": [],
    "mf_analysis_option": [],
    "mf_analysis_only_meta": False,
    "mf_analysis_only_holdings": False,
    "mf_analysis_min_age": 0.0,
    "mf_analysis_fund": None,
}
_persist_mf_filters = make_persist_callback(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)


@st.cache_data(ttl=600, show_spinner=False)
def _load_tracked_enriched(names: tuple[str, ...]) -> pl.DataFrame:
    """Tracked funds + AMFI category/sub-category + computed plan/option, for the filters."""
    empty_schema = {
        "scheme_name": pl.Utf8,
        "fund_house": pl.Utf8,
        "category": pl.Utf8,
        "sub_category": pl.Utf8,
        "plan": pl.Utf8,
        "option": pl.Utf8,
        "inception_date": pl.Date,
    }
    if not names:
        return pl.DataFrame(schema=empty_schema)

    # inception_date (first NAV date) from cached metrics — the age filter's anchor.
    _m = load_metrics(list(names))
    inc = (
        _m.select(["scheme_name", "inception_date"])
        if "inception_date" in _m.columns
        else pl.DataFrame(schema={"scheme_name": pl.Utf8, "inception_date": pl.Date})
    )

    amfi = load_amfi_df().filter(pl.col("scheme_name").is_in(list(names)))
    if amfi.is_empty():
        # Fallback: scheme_name + computed plan/option so search still works.
        return (
            pl.DataFrame({"scheme_name": list(names)})
            .with_columns(
                pl.lit(None, dtype=pl.Utf8).alias("fund_house"),
                pl.lit(None, dtype=pl.Utf8).alias("category"),
                pl.lit(None, dtype=pl.Utf8).alias("sub_category"),
                pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
                pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
            )
            .join(inc, on="scheme_name", how="left")
        )

    enriched = amfi.with_columns(
        pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
        pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
    ).join(inc, on="scheme_name", how="left")
    return enriched.select(
        ["scheme_name", "fund_house", "category", "sub_category", "plan", "option", "inception_date"]
    )


def select_fund(tracked: pl.DataFrame) -> tuple[str | None, pl.DataFrame]:
    """Render the sidebar filters + fund selectbox; return (selected fund, enriched frame).

    Selected is None when no tracked fund matches the filters (caller stops the page).
    """
    all_tracked_names = tracked["schemeName"].to_list()
    enriched = _load_tracked_enriched(tuple(all_tracked_names))

    hydrate_filters(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)  # seed from disk before any widget is built

    with st.sidebar:
        st.header("Filter funds")
        name_query = st.text_input(
            "Search by name",
            placeholder="e.g. parag parikh flexi",
            key="mf_analysis_search",
            on_change=_persist_mf_filters,
            help="Case-insensitive substring match; multiple words are ANDed.",
        )
        amc_options = sorted(enriched["fund_house"].drop_nulls().unique().to_list())
        cat_options = sorted(enriched["category"].drop_nulls().unique().to_list())
        # Sub-category cascades off the selected category; stale persisted values pruned.
        sub_cat_options = cascade_subcategory_options(
            enriched, cat_key="mf_analysis_cat", sub_cat_key="mf_analysis_sub_cat"
        )

        amc_filter = st.multiselect("AMC", amc_options, key="mf_analysis_amc", on_change=_persist_mf_filters)
        cat_filter = st.multiselect("Category", cat_options, key="mf_analysis_cat", on_change=_persist_mf_filters)
        sub_cat_filter = st.multiselect(
            "Sub-category", sub_cat_options, key="mf_analysis_sub_cat", on_change=_persist_mf_filters
        )
        plan_filter = st.multiselect(
            "Plan", ["Direct", "Regular"], key="mf_analysis_plan", on_change=_persist_mf_filters
        )
        option_filter = st.multiselect(
            "Option",
            ["Growth", "IDCW", "Bonus", "ETF", "Other"],
            key="mf_analysis_option",
            on_change=_persist_mf_filters,
        )
        min_age_years = st.slider(
            "Min fund age (years)",
            0.0,
            25.0,
            step=0.5,
            key="mf_analysis_min_age",
            on_change=_persist_mf_filters,
            help="Keep funds with at least this much NAV history.",
        )

        st.divider()
        st.caption("Data availability")
        only_with_metadata = st.checkbox(
            "Only with metadata", key="mf_analysis_only_meta", on_change=_persist_mf_filters
        )
        only_with_holdings = st.checkbox(
            "Only with holdings", key="mf_analysis_only_holdings", on_change=_persist_mf_filters
        )

    filtered = apply_name_filter(enriched, name_query)
    if amc_filter:
        filtered = filtered.filter(pl.col("fund_house").is_in(amc_filter))
    if cat_filter:
        filtered = filtered.filter(pl.col("category").is_in(cat_filter))
    if sub_cat_filter:
        filtered = filtered.filter(pl.col("sub_category").is_in(sub_cat_filter))
    if plan_filter:
        filtered = filtered.filter(pl.col("plan").is_in(plan_filter))
    if option_filter:
        filtered = filtered.filter(pl.col("option").is_in(option_filter))
    if min_age_years > 0 and "inception_date" in filtered.columns:
        _cutoff = date.today() - timedelta(days=round(min_age_years * 365.25))
        filtered = filtered.filter(pl.col("inception_date") <= _cutoff)

    # Availability filters join against the tracked source statuses.
    if only_with_metadata or only_with_holdings:
        status_df = tracked.select(
            pl.col("schemeName").alias("scheme_name"),
            pl.col("metadataStatus"),
            pl.col("holdingsStatus"),
        )
        filtered = filtered.join(status_df, on="scheme_name", how="left")
        if only_with_metadata:
            filtered = filtered.filter(pl.col("metadataStatus") == "available")
        if only_with_holdings:
            filtered = filtered.filter(pl.col("holdingsStatus") == "available")
        filtered = filtered.drop("metadataStatus", "holdingsStatus")

    scheme_names = sorted(filtered["scheme_name"].to_list(), key=lambda n: short_scheme_name(n).lower())

    if not scheme_names:
        st.info(
            f"No tracked funds match the current filters ({len(all_tracked_names):,} tracked total). "
            "Clear filters in the sidebar or add more funds via **MF Screener**."
        )
        return None, enriched

    st.caption(f"Showing **{len(scheme_names):,}** of {len(all_tracked_names):,} tracked funds")

    # Drop a persisted fund the filters now exclude, so the selectbox value stays in its options.
    if st.session_state.get("mf_analysis_fund") not in scheme_names:
        st.session_state.pop("mf_analysis_fund", None)

    selected = st.selectbox(
        "Fund",
        options=scheme_names,
        format_func=short_scheme_name,
        key="mf_analysis_fund",
        on_change=_persist_mf_filters,
    )
    return selected, enriched
