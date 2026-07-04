"""Overview — the landing desk: market pulse, sector board, portfolio snapshot, alerts, movers."""

import streamlit as st

from services.portfolio_service import build_portfolio_value_series, get_mapped_data
from ui.state import navigation
from ui.state.loaders import (
    load_fund_movers_cached,
    load_index_performance_cached,
    load_market_pulse_cached,
    load_overview_alerts_cached,
    load_txn_data,
)
from ui.views.overview import alerts, market_pulse, movers, sector_performance, snapshot


def _refresh_data() -> None:
    """Refresh what the Overview shows: stock prices (NSE bhavcopy) + the board indices to today,
    then drop the cached insight frames. Stashes an outcome toast for after the rerun."""
    from data.repositories.stock import refresh_index_to_today  # noqa: PLC0415 — defer off boot
    from services.insights_service import MARKET_PULSE_SYMBOLS, SECTOR_INDEX_SYMBOLS  # noqa: PLC0415
    from services.stock_sync_service import refresh_stocks_via_bhavcopy  # noqa: PLC0415

    with st.spinner("Refreshing market & stock data…"):
        try:
            n = refresh_stocks_via_bhavcopy()
        except Exception:
            n = 0
        syms = {s for s, _, _ in SECTOR_INDEX_SYMBOLS} | set(MARKET_PULSE_SYMBOLS)
        for sym in syms:
            try:
                refresh_index_to_today(sym)
            except Exception:
                continue
    for loader in (load_market_pulse_cached, load_index_performance_cached, load_overview_alerts_cached, load_fund_movers_cached):
        loader.clear()
    st.session_state["_ov_refresh_msg"] = ("✅", f"Refreshed {len(syms)} indices + {n:,} stock price rows.")


st.title("Overview")

_ov_msg = st.session_state.pop("_ov_refresh_msg", None)
if _ov_msg:
    st.toast(_ov_msg[1], icon=_ov_msg[0])

_hdr, _btn = st.columns([5, 1], vertical_alignment="center")
with _btn:
    if st.button("🔄 Refresh data", key="ov_refresh", use_container_width=True, help="Update stock prices + indices to today"):
        _refresh_data()
        st.rerun()

# ---- Market pulse + sector board (render even without a tradebook)
market_pulse.render()
st.divider()
sector_performance.render()
st.divider()

# ---- Portfolio snapshot + alerts + movers (need the tradebook)
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
