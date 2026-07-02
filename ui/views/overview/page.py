"""Overview — the landing desk: market pulse, portfolio snapshot, alerts, movers."""

import streamlit as st

from services.portfolio_service import build_portfolio_value_series, get_mapped_data
from ui.state import navigation
from ui.state.loaders import load_txn_data
from ui.views.overview import alerts, market_pulse, movers, snapshot

st.title("Overview")

# ---- Market pulse (renders even without a tradebook)
market_pulse.render()
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
