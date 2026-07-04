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
from ui.persistence.selections import load_selection, save_selection
from ui.state.filter_persistence import hydrate_filters, make_persist_callback

# Selected-fund state persisted to selections.json so it survives page nav/refresh.
_MF_PERSIST_KEY = "mf_analysis_filters"
_MF_FILTER_DEFAULTS = {"mf_analysis_fund": None}
_persist_mf_filters = make_persist_callback(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)
_BOOKMARKS_KEY = "mf_bookmarks"  # user-curated quick-access funds (selections.json)


def _quick_jump() -> None:
    """on_change for the quick-access dropdown: jump the fund picker to the chosen fund."""
    pick = st.session_state.get("mf_quick_access")
    if pick:
        st.session_state["mf_analysis_fund"] = pick


@st.cache_data(ttl=600, show_spinner=False)
def _portfolio_fund_names() -> list[str]:
    """Scheme names the user holds (from the tradebook) — the default quick-access group."""
    try:
        from services.portfolio_analytics import fund_values_from_nav  # noqa: PLC0415 — defer heavy import off boot
        from services.portfolio_service import get_mapped_data  # noqa: PLC0415
        from ui.state.loaders import load_txn_data  # noqa: PLC0415

        txn = load_txn_data()
        result = get_mapped_data(txn) if txn is not None else None
        if result is None:
            return []
        mapped, portfolio_nav = result
        return sorted(fund_values_from_nav(mapped, portfolio_nav).keys())
    except Exception:
        return []


def _render_quick_access(tracked_set: set[str]) -> None:
    """One dropdown combining portfolio holdings + bookmarks; selecting jumps the fund picker.
    Each entry is tagged 📁 (held) / ⭐ (bookmarked)."""
    portfolio = {n for n in _portfolio_fund_names() if n in tracked_set}
    bookmarks = {n for n in load_selection(_BOOKMARKS_KEY, []) if n in tracked_set}
    combined = sorted(portfolio | bookmarks, key=lambda n: short_scheme_name(n).lower())
    if not combined:
        return

    def _label(name: str | None) -> str:
        if name is None:
            return "— jump to a portfolio / bookmarked fund —"
        tag = ("📁" if name in portfolio else "") + ("⭐" if name in bookmarks else "")
        return f"{tag} {short_scheme_name(name)}"

    if st.session_state.get("mf_quick_access") not in (None, *combined):
        st.session_state.pop("mf_quick_access", None)
    st.selectbox(
        "Quick access (portfolio & bookmarks)",
        options=[None, *combined],
        format_func=_label,
        key="mf_quick_access",
        on_change=_quick_jump,
    )


def _render_bookmark_toggle(selected: str | None) -> None:
    """★/☆ button to add/remove the selected fund from bookmarks."""
    bookmarks = load_selection(_BOOKMARKS_KEY, [])
    is_bookmarked = selected in bookmarks
    if st.button(
        "★" if is_bookmarked else "☆",
        key="mf_bookmark_toggle",
        help="Remove bookmark" if is_bookmarked else "Bookmark this fund",
        disabled=not selected,
    ):
        updated = set(bookmarks)
        updated.discard(selected) if is_bookmarked else updated.add(selected)
        save_selection(_BOOKMARKS_KEY, sorted(updated))
        st.rerun()


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

    # Sort by full name so plan/option variants (Direct/Regular, Growth/IDCW) group together.
    scheme_names = sorted(all_tracked_names)
    if not scheme_names:
        st.info("No tracked funds yet. Add funds via the **MF Screener** page.")
        return None, enriched

    tracked_set = set(scheme_names)
    # Quick access: one dropdown combining portfolio holdings (default) + user bookmarks.
    _render_quick_access(tracked_set)

    # Drop a persisted fund that is no longer tracked, so the selectbox value stays in its options.
    if st.session_state.get("mf_analysis_fund") not in scheme_names:
        st.session_state.pop("mf_analysis_fund", None)

    col_pick, col_star = st.columns([20, 1], vertical_alignment="bottom")
    with col_pick:
        # Full scheme name shown so Direct/Regular · Growth/IDCW variants are distinguishable.
        selected = st.selectbox(
            "Fund", options=scheme_names, key="mf_analysis_fund", on_change=_persist_mf_filters
        )
    with col_star:
        _render_bookmark_toggle(selected)
    return selected, enriched
