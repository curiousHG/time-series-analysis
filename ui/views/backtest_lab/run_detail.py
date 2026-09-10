"""Detail view: everything one finished run produced.

Reads a `RunDetail` through the cached loader and never recomputes — the equity curve, trades and
summary were persisted when the run finished, so opening a run is a database read.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from services.backtest.config import UniverseSpec
from services.backtest.costs import PRESET_LABELS
from ui.charts import backtest as charts
from ui.charts import ml_diagnostics as ml_charts
from ui.charts import theme
from ui.components.chrome import section_header
from ui.components.metric_tiles import EM_DASH, Kpi, fmt_inr_compact, render_kpi_row
from ui.components.monthly_returns import render_monthly_returns
from ui.state.loaders import (
    clear_backtest_caches,
    load_backtest_run_cached,
    load_backtest_runs_cached,
    load_stock_open_close,
)
from ui.views.backtest_lab import state
from ui.views.backtest_lab.params import params_summary
from ui.views.stock_analysis import chart as price_chart

if TYPE_CHECKING:
    from services.backtest.runs import RunDetail

logger = logging.getLogger(__name__)

CHART_HISTORY_START = "2000-01-01"
TRADE_COLUMNS = (
    "symbol",
    "open_date",
    "close_date",
    "open_rate",
    "close_rate",
    "shares",
    "stake_amount",
    "profit_abs",
    "profit_ratio",
    "exit_reason",
    "bars_held",
)
_TRADE_COLUMN_CONFIG = {
    "open_rate": st.column_config.NumberColumn("Entry", format="₹%.2f"),
    "close_rate": st.column_config.NumberColumn("Exit", format="₹%.2f"),
    "stake_amount": st.column_config.NumberColumn("Stake", format="₹%.0f"),
    "profit_abs": st.column_config.NumberColumn("P&L", format="₹%.0f"),
    "profit_ratio": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
    "open_date": st.column_config.DateColumn("Opened", format="DD MMM YYYY"),
    "close_date": st.column_config.DateColumn("Closed", format="DD MMM YYYY"),
    "bars_held": st.column_config.NumberColumn("Bars", format="%d"),
}


def _pct(value: Any, decimals: int = 1) -> str:
    return EM_DASH if value is None or pd.isna(value) else f"{float(value):.{decimals}f}%"


def _ratio(value: Any) -> str:
    """Two decimals for a number, the value itself for anything else — the ML summary mixes
    counts and ratios with labels such as the model kind."""
    if value is None:
        return EM_DASH
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or pd.isna(value):
        return EM_DASH if not isinstance(value, bool) else str(value)
    return f"{float(value):.2f}"


def config_caption(detail: RunDetail) -> str:
    """One line describing exactly what this run was: universe, period, costs and parameters."""
    config = detail.config
    universe = config.universe if isinstance(config.universe, UniverseSpec) else UniverseSpec.from_dict(config.universe)
    costs = PRESET_LABELS.get(config.costs.name, config.costs.name)
    return (
        f"{config.strategy} · {universe.label()} · {config.start:%b %Y} to {config.end:%b %Y} · "
        f"{costs} + {config.slippage_bps:.0f} bps · start ₹{config.init_cash:,.0f} · {params_summary(config.params)}"
    )


def _kpi_rows(detail: RunDetail) -> None:
    m = detail.metrics
    render_kpi_row(
        [
            Kpi("Total return", _pct(m.get("total_return_pct"))),
            Kpi("CAGR", _pct(m.get("cagr"))),
            Kpi("Sharpe", _ratio(m.get("sharpe"))),
            Kpi("Max drawdown", _pct(m.get("max_drawdown"))),
            Kpi("Final equity", fmt_inr_compact(m.get("final_equity"))),
            Kpi("Net P&L", fmt_inr_compact(m.get("total_profit_abs"))),
        ]
    )
    render_kpi_row(
        [
            Kpi("Trades", m.get("total_trades", 0)),
            Kpi("Win rate", _pct(m.get("win_rate"), 0)),
            Kpi("Profit factor", _ratio(m.get("profit_factor"))),
            Kpi("Avg exposure", _pct(m.get("avg_exposure"), 0)),
            Kpi("Costs paid", fmt_inr_compact(m.get("total_costs")), help="Brokerage, taxes and DP charges."),
            Kpi("Vs benchmark", _pct(m.get("excess_return_pct")), help="Total return minus the benchmark's."),
        ]
    )


def _render_actions(detail: RunDetail) -> None:
    dup_col, opt_col, cmp_col, _ = st.columns([1, 1, 1, 3])
    if dup_col.button("Duplicate", key="bt_detail_dup", use_container_width=True):
        from services.backtest.runs import duplicate_run  # noqa: PLC0415 — pulls the engine + quantstats in

        new_id = duplicate_run(detail.run_id, {})
        clear_backtest_caches()
        state.go_to("Runs", run_id=new_id)
    if opt_col.button("Optimise", key="bt_detail_optimise", use_container_width=True):
        state.go_to("Optimise", run_id=detail.run_id)
    if cmp_col.button("Compare with…", key="bt_detail_compare", use_container_width=True):
        state.go_to("Compare", compare=[detail.run_id])


def _render_charts(detail: RunDetail) -> None:
    section_header("Equity", "Strategy against the benchmark, with the drawdown underneath.")
    st.plotly_chart(charts.equity_vs_benchmark(detail.equity, detail.benchmark), use_container_width=True)

    left, right = st.columns(2)
    with left:
        section_header("P&L by symbol")
        if detail.per_symbol.empty:
            st.caption("No closed trades.")
        else:
            st.plotly_chart(charts.per_symbol_bar(detail.per_symbol), use_container_width=True)
    with right:
        section_header("P&L by exit reason")
        if detail.per_exit_reason.empty:
            st.caption("No closed trades.")
        else:
            st.plotly_chart(charts.exit_reason_bar(detail.per_exit_reason), use_container_width=True)

    left, right = st.columns(2)
    with left:
        section_header("Where the money went", "Gross P&L stepped down by the charges on every order.")
        gross = float(detail.metrics.get("total_profit_abs") or 0.0) + float(detail.metrics.get("total_costs") or 0.0)
        st.plotly_chart(
            charts.cost_waterfall(gross, {"Entry costs": detail.costs["entry"], "Exit costs": detail.costs["exit"]}),
            use_container_width=True,
        )
    with right:
        section_header("Daily returns")
        st.plotly_chart(charts.returns_histogram(detail.equity.pct_change()), use_container_width=True)


def _render_trade_log(detail: RunDetail) -> None:
    trades = detail.trades
    section_header("Trade log", f"{len(trades)} closed trade(s).")
    if trades.empty:
        st.caption("This run closed no trades.")
        return
    left, right = st.columns(2)
    with left:
        symbols = st.multiselect("Symbols", sorted(trades["symbol"].unique()), key="bt_trade_symbols")
    with right:
        reasons = st.multiselect("Exit reason", sorted(trades["exit_reason"].dropna().unique()), key="bt_trade_reasons")
    shown = trades
    if symbols:
        shown = shown[shown["symbol"].isin(symbols)]
    if reasons:
        shown = shown[shown["exit_reason"].isin(reasons)]
    columns = [c for c in TRADE_COLUMNS if c in shown.columns]
    display = shown[columns].copy()
    if "profit_ratio" in display.columns:
        display["profit_ratio"] = display["profit_ratio"] * 100
    st.dataframe(display, hide_index=True, use_container_width=True, column_config=_TRADE_COLUMN_CONFIG)


def trade_markers(trades: pd.DataFrame) -> list[dict]:
    """Entry and exit markers for the candlestick chart, coloured by the trade's outcome."""
    markers: list[dict] = []
    for row in trades.itertuples():
        outcome = theme.POSITIVE if row.profit_abs >= 0 else theme.NEGATIVE
        markers.append(
            {
                "time": str(pd.Timestamp(row.open_date).date()),
                "position": "belowBar",
                "color": theme.ACCENT,
                "shape": "arrowUp",
                "text": f"Buy {int(row.shares)}",
            }
        )
        markers.append(
            {
                "time": str(pd.Timestamp(row.close_date).date()),
                "position": "aboveBar",
                "color": outcome,
                "shape": "arrowDown",
                "text": f"{row.exit_reason} ₹{row.profit_abs:,.0f}",
            }
        )
    return markers


def _render_chart_expander(detail: RunDetail) -> None:
    with st.expander("Trades on the chart", icon=":material/candlestick_chart:"):
        if detail.trades.empty:
            st.caption("No trades to plot.")
            return
        left, right = st.columns([2, 1])
        symbol = left.selectbox("Symbol", sorted(detail.trades["symbol"].unique()), key="bt_chart_symbol")
        show = right.checkbox("Load candles", key="bt_chart_show", help="Fetches this symbol's daily bars.")
        if not show:
            st.caption("Tick **Load candles** to draw the entries and exits on the price chart.")
            return
        frame = load_stock_open_close(
            [symbol], pd.to_datetime(CHART_HISTORY_START), pd.Timestamp.today().normalize()
        ).to_pandas()
        if frame.empty:
            st.caption(f"No price history stored for {symbol}.")
            return
        subset = detail.trades[detail.trades["symbol"] == symbol]
        price_chart.render(frame.sort_values("Date"), {}, {}, [], f"bt_{symbol}", markers=trade_markers(subset))


def _render_ml_expander(detail: RunDetail) -> None:
    diagnostics = detail.diagnostics
    if diagnostics is None:
        return
    with st.expander("ML diagnostics", icon=":material/network_intelligence:"):
        summary = getattr(diagnostics, "summary", {}) or {}
        render_kpi_row([Kpi(k.replace("_", " ").capitalize(), _ratio(v)) for k, v in list(summary.items())[:6]])
        windows = getattr(diagnostics, "windows", pd.DataFrame())
        importance = getattr(diagnostics, "importance", pd.DataFrame())
        if not importance.empty:
            section_header("Feature importance", "Mean across walk-forward windows, with the spread.")
            st.plotly_chart(ml_charts.feature_importance_bar(importance), use_container_width=True)
        metrics = [c for c in ("ic", "auc", "hit_rate", "rmse", "log_loss") if c in windows.columns]
        if metrics:
            metric = st.selectbox("Per-window metric", metrics, key="bt_ml_metric")
            st.plotly_chart(ml_charts.window_metrics_bar(windows, metric), use_container_width=True)
        predictions = getattr(diagnostics, "predictions", pd.DataFrame())
        if not predictions.empty:
            thresholds = tuple(
                float(v) for k, v in detail.config.params.items() if k.endswith("threshold") and v is not None
            )
            st.plotly_chart(
                ml_charts.prediction_histogram(predictions, thresholds=thresholds), use_container_width=True
            )
        coverage = getattr(diagnostics, "coverage", None)
        if coverage is not None and len(coverage):
            section_header("Prediction coverage", "Share of the universe carrying a live prediction.")
            st.plotly_chart(ml_charts.coverage_area(coverage), use_container_width=True)


def _render_reference_expanders(detail: RunDetail) -> None:
    with st.expander("All metrics", icon=":material/table:"):
        rows = pd.DataFrame({"Metric": list(detail.metrics), "Value": [detail.metrics[k] for k in detail.metrics]})
        st.dataframe(rows.astype({"Value": str}), hide_index=True, use_container_width=True)
    with st.expander("Run configuration", icon=":material/settings:"):
        st.json(detail.config.to_dict())


def _render_unfinished(detail: RunDetail) -> None:
    if detail.status == "failed":
        st.error(f"This run failed: {detail.error or 'no error text was recorded.'}")
        st.caption("Fix the configuration with **Edit** on the Runs view, or relaunch it as-is.")
    else:
        st.info("This run has no results yet — launch it from the Runs view.")
    with st.expander("Run configuration", icon=":material/settings:"):
        st.json(detail.config.to_dict())


def render() -> None:
    runs = load_backtest_runs_cached()
    options = [] if runs.is_empty() else [int(v) for v in runs["id"].to_list()]
    names = {} if runs.is_empty() else dict(zip(options, runs["name"].to_list(), strict=False))
    if not options:
        st.info("No runs yet — create one on the Runs view.")
        return
    state.sync_picker(state.DETAIL_PICKER_KEY, options)
    run_id = st.selectbox("Run", options, format_func=lambda i: names.get(i, str(i)), key=state.DETAIL_PICKER_KEY)
    state.set_selected_run(run_id)

    detail = load_backtest_run_cached(int(run_id))
    if detail is None:
        st.warning("That run no longer exists.")
        return
    st.caption(config_caption(detail))
    _render_actions(detail)
    if detail.status != "done" or detail.equity.empty:
        _render_unfinished(detail)
        return

    _kpi_rows(detail)
    _render_charts(detail)
    render_monthly_returns(detail.equity.pct_change().dropna())
    _render_trade_log(detail)
    _render_chart_expander(detail)
    _render_ml_expander(detail)
    _render_reference_expanders(detail)
