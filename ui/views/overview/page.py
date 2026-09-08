"""Overview — the landing desk: market pulse, sector board, portfolio snapshot, alerts, movers."""

from datetime import date

import streamlit as st

from services.portfolio_service import build_portfolio_value_series, get_mapped_data
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import page_header
from ui.state import navigation
from ui.state.loaders import (
    load_fund_movers_cached,
    load_index_performance_cached,
    load_international_pulse_cached,
    load_market_pulse_cached,
    load_overview_alerts_cached,
    load_txn_data,
)
from ui.views.overview import alerts, indexes, market_pulse, movers, snapshot

_REFRESH_KEY = "overview_refresh"


def _overview_refresh_task() -> dict:
    """Refresh what the Overview shows: stock prices + all NSE indices (bhavcopy) + the board's
    ^-indices (market pulse + international) to today. Runs on a background task."""
    from core.background import set_task_progress  # noqa: PLC0415 — defer off boot
    from data.repositories.stock import refresh_index_to_today  # noqa: PLC0415
    from services.insights_service import INTERNATIONAL_PULSE_SYMBOLS, MARKET_PULSE_SYMBOLS  # noqa: PLC0415
    from services.stock_sync_service import refresh_all_stock_data  # noqa: PLC0415

    result = refresh_all_stock_data(progress_cb=progress_cb(_REFRESH_KEY))
    board = sorted(set(MARKET_PULSE_SYMBOLS) | set(INTERNATIONAL_PULSE_SYMBOLS))
    for i, sym in enumerate(board, 1):
        try:
            refresh_index_to_today(sym)
        except Exception:
            continue
        set_task_progress(_REFRESH_KEY, phase="Board indices", done=i, total=len(board))
    return result


def _clear_overview_caches() -> None:
    for loader in (
        load_market_pulse_cached,
        load_index_performance_cached,
        load_international_pulse_cached,
        load_overview_alerts_cached,
        load_fund_movers_cached,
    ):
        loader.clear()


_REFRESH = BackgroundRefresh(
    _REFRESH_KEY,
    "Data refresh",
    cache_clearers=(_clear_overview_caches,),
    summarize=lambda r: f"{r.get('index_rows', 0):,} index + {r.get('stock_rows', 0):,} stock rows"
    if isinstance(r, dict)
    else str(r),
)


_REFRESH.consume()

page_header(
    "Overview",
    f"{date.today():%A, %d %b %Y}",
    actions=lambda: _REFRESH.start_button(
        "",
        _overview_refresh_task,
        key="ov_refresh",
        icon=":material/refresh:",
        help="Update stock and index prices to today",
        use_container_width=False,
    ),
)
if _REFRESH.is_running():
    _REFRESH.poll()

tab_dashboard, tab_indexes = st.tabs(["Dashboard", "Indexes"])

with tab_dashboard:
    market_pulse.render()
    st.divider()
    _txn = load_txn_data()
    _result = get_mapped_data(_txn) if _txn is not None else None
    _pv_df = build_portfolio_value_series(*_result) if _result is not None else None
    if _pv_df is None or _pv_df.empty:
        st.info("No tradebook loaded yet — upload your Kite/Zerodha CSV to unlock the portfolio desk.")
        st.page_link(navigation.SETTINGS_PAGE, label="Open Settings →")
    else:
        _mapped, _portfolio_nav = _result  # type: ignore[misc]  # guarded by _pv_df above
        snapshot.render(_mapped, _portfolio_nav, _pv_df)
        st.divider()
        col_alerts, col_movers = st.columns([3, 2], gap="large")
        with col_alerts:
            alerts.render()
        with col_movers:
            movers.render()

with tab_indexes:
    indexes.render()
