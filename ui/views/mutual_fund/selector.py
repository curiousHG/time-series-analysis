"""MF Analysis fund selector — selectbox over the tracked universe.

Fund discovery/filtering lives on the **MF Screener** page (click a fund to open it here),
so this page only needs to pick among the funds already tracked.
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import load_amfi_df
from data.repositories.scheme_metrics import load_metrics
from mutual_funds.display import detect_option, detect_plan, short_scheme_name
from ui.state.filter_persistence import hydrate_filters, make_persist_callback

# Selected-fund state persisted to selections.json so it survives page nav/refresh.
_MF_PERSIST_KEY = "mf_analysis_filters"
_MF_FILTER_DEFAULTS = {"mf_analysis_fund": None}
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
    """Render the fund selectbox; return (selected fund, enriched frame).

    Selected is None when there are no tracked funds (caller stops the page).
    """
    all_tracked_names = tracked["schemeName"].to_list()
    enriched = _load_tracked_enriched(tuple(all_tracked_names))

    hydrate_filters(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)  # seed selection from disk before the widget

    scheme_names = sorted(all_tracked_names, key=lambda n: short_scheme_name(n).lower())
    if not scheme_names:
        st.info("No tracked funds yet. Add funds via the **MF Screener** page.")
        return None, enriched

    # Drop a persisted fund that is no longer tracked, so the selectbox value stays in its options.
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
