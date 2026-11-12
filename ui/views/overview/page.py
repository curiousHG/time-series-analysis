"""Overview — the landing desk: market pulse, sector board, portfolio snapshot, alerts, movers."""

from datetime import date

import streamlit as st

from services.portfolio.value import build_portfolio_value_series, get_mapped_data
from services.stock.sync import StockRefreshSummary
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import page_header
from ui.state import navigation
from ui.state.loaders import clear_market_caches, load_txn_data
from ui.views.overview import alerts, indexes, market_pulse, movers, snapshot

_REFRESH_KEY = "overview_refresh"


def _overview_refresh_task() -> StockRefreshSummary:
    """Refresh what the Overview shows: stock prices + all NSE indices (bhavcopy) + the board's
    ^-indices (market pulse + international) to today. Runs on a background task."""
    from services.overview.market import refresh_overview_data  # noqa: PLC0415

    return refresh_overview_data(progress_cb=progress_cb(_REFRESH_KEY))


_REFRESH = BackgroundRefresh(
    _REFRESH_KEY,
    "Data refresh",
    cache_clearers=(clear_market_caches,),
    summarize=lambda r: f"{r.index_rows:,} index + {r.stock_rows:,} stock rows"
    if isinstance(r, StockRefreshSummary)
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
        _mapped, _portfolio_nav = _result  # type: ignore[misc]
        snapshot.render(_mapped, _portfolio_nav, _pv_df)
        st.divider()
        col_alerts, col_movers = st.columns([3, 2], gap="large")
        with col_alerts:
            alerts.render()
        with col_movers:
            movers.render()

with tab_indexes:
    indexes.render()
