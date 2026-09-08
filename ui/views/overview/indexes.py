"""Overview · Indexes tab — international movement, a TradingView-style index chart with
persisted indicators, index constituents, and the sector board."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from data.repositories.stock import list_bhavcopy_index_names
from indicators import INDICATOR_REGISTRY, compute_indicators
from services.benchmarks import index_display_name
from services.insights_service import INTERNATIONAL_INDEX_SYMBOLS
from ui.components import index_detail
from ui.components.metric_tiles import EM_DASH, fmt_pct
from ui.state.filter_persistence import hydrate_filters, make_persist_callback
from ui.state.loaders import load_index_chart_ohlcv, load_international_pulse_cached
from ui.views.overview import sector_performance
from ui.views.stock_analysis import chart as chart_tab

_PERSIST_KEY = "overview_index_filters"
_DEFAULTS = {
    "ov_idx_sel": "Nifty 50",
    "ov_idx_overlays": ["SMA 50", "SMA 200"],
    "ov_idx_panels": ["RSI"],
}
_persist = make_persist_callback(_PERSIST_KEY, _DEFAULTS)


def render() -> None:
    hydrate_filters(_PERSIST_KEY, _DEFAULTS)  # restore the persisted index + indicator choices

    _render_international()
    st.divider()
    sel = _render_chart()
    if sel and not sel.startswith("^"):
        st.divider()
        index_detail.render_constituents(sel)
    st.divider()
    sector_performance.render()


def _render_international() -> None:
    st.subheader("International indices")
    pulse = load_international_pulse_cached()
    if pulse.is_empty():
        st.caption("International index data loading — hit Refresh data if it stays empty.")
        return
    rows = pulse.to_dicts()
    for col, r in zip(st.columns(len(rows)), rows, strict=False):
        delta = fmt_pct(r["chg_1d"], decimals=2)
        col.metric(r["label"], f"{r['last_close']:,.0f}", delta=delta if delta != EM_DASH else None)
        col.caption(f"1W {fmt_pct(r['chg_1w'])} · 1M {fmt_pct(r['chg_1m'])}")


def _render_chart() -> str | None:
    st.subheader("Index chart")
    nse = list_bhavcopy_index_names()
    intl = [s for s, _, _ in INTERNATIONAL_INDEX_SYMBOLS]
    options = nse + intl
    if not options:
        st.info("No index data yet — click **🔄 Refresh data** above, or Settings > Stock Data.")
        return None
    if st.session_state.get("ov_idx_sel") not in options:
        st.session_state["ov_idx_sel"] = "Nifty 50" if "Nifty 50" in nse else options[0]

    sel = st.selectbox(
        "Index (160+ NSE indices incl. factor/strategy + international)",
        options,
        format_func=lambda s: index_display_name(s) if s.startswith("^") else s,
        key="ov_idx_sel",
        on_change=_persist,
    )
    index_detail.render_valuation(sel)
    idf = load_index_chart_ohlcv(sel, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    if idf.is_empty():
        st.warning("No price history for this index yet — it fills in as the bhavcopy backfills.")
        return sel
    index_detail.render_trailing(idf)

    sdf = idf.sort("Date").with_columns(pl.col("Date").cast(pl.Utf8).alias("time")).to_pandas()
    interval = (
        st.segmented_control(
            "Candle interval", options=["Daily", "Weekly", "Monthly"], default="Daily", key="ov_idx_interval"
        )
        or "Daily"
    )
    chart_df = chart_tab.resample_ohlc(sdf, interval)

    overlay_names = [n for n, e in INDICATOR_REGISTRY.items() if e["overlay"]]
    panel_names = [n for n, e in INDICATOR_REGISTRY.items() if not e["overlay"]]
    col_o, col_p = st.columns(2)
    with col_o:
        overlays_sel = st.multiselect("Overlays (on price)", overlay_names, key="ov_idx_overlays", on_change=_persist)
    with col_p:
        panels_sel = st.multiselect("Panels (below)", panel_names, key="ov_idx_panels", on_change=_persist)

    overlays_sel = [o for o in overlays_sel if o in overlay_names]  # guard stale persisted names
    panels_sel = [p for p in panels_sel if p in panel_names]
    overlays, panels = compute_indicators(chart_df, overlays_sel + panels_sel)
    chart_tab.render(chart_df, overlays, panels, panels_sel, index_display_name(sel) if sel.startswith("^") else sel)
    return sel
