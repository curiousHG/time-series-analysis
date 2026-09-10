"""Compare view: two to five runs on one equity axis, one metrics table and the settings that differ.

The parameter table deliberately shows only rows where the runs disagree — the point of a comparison
is the delta, and a full config dump buries it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from services.backtest.config import UniverseSpec
from ui.charts import backtest as charts
from ui.components.chrome import section_header
from ui.state.loaders import load_backtest_run_cached, load_backtest_runs_cached
from ui.views.backtest_lab import state

if TYPE_CHECKING:
    from services.backtest.runs import RunDetail

PICKER_KEY = "bt_compare_pick"
METRIC_LABELS = {
    "total_return_pct": "Total return %",
    "cagr": "CAGR %",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "calmar": "Calmar",
    "max_drawdown": "Max drawdown %",
    "win_rate": "Win rate %",
    "profit_factor": "Profit factor",
    "total_trades": "Trades",
    "total_costs": "Costs paid ₹",
    "avg_exposure": "Avg exposure %",
    "final_equity": "Final equity ₹",
}


def metrics_table(details: list[RunDetail]) -> pd.DataFrame:
    """Headline metrics with one column per run — the shape a side-by-side read wants."""
    return pd.DataFrame(
        {d.name: [d.metrics.get(key) for key in METRIC_LABELS] for d in details},
        index=list(METRIC_LABELS.values()),
    )


def _settings(detail: RunDetail) -> dict[str, Any]:
    config = detail.config
    universe = config.universe if isinstance(config.universe, UniverseSpec) else UniverseSpec.from_dict(config.universe)
    return {
        "strategy": config.strategy,
        "universe": universe.label(),
        "period": f"{config.start} to {config.end}",
        "costs": config.costs.name,
        "slippage_bps": config.slippage_bps,
        "init_cash": config.init_cash,
        "stake_amount": config.stake_amount,
        **{f"param · {k}": v for k, v in config.params.items()},
        **{f"override · {k}": v for k, v in config.strategy_overrides.items()},
    }


def params_diff(details: list[RunDetail]) -> pd.DataFrame:
    """Only the settings the runs disagree on, one column per run."""
    settings = [_settings(d) for d in details]
    keys = list(dict.fromkeys(k for s in settings for k in s))
    rows = {k: [s.get(k) for s in settings] for k in keys}
    differing = {
        k: [str(v) if v is not None else "—" for v in values]
        for k, values in rows.items()
        if len(set(map(str, values))) > 1
    }
    return pd.DataFrame(differing, index=[d.name for d in details]).T


def render() -> None:
    runs = load_backtest_runs_cached()
    options = [] if runs.is_empty() else [int(v) for v in runs["id"].to_list()]
    names = {} if runs.is_empty() else dict(zip(options, runs["name"].to_list(), strict=False))
    if len(options) < state.MIN_COMPARE:
        st.info("Compare needs at least two runs — create another one on the Runs view.")
        return
    if PICKER_KEY not in st.session_state:
        st.session_state[PICKER_KEY] = [i for i in state.compare_ids() if i in options]
    picked = st.multiselect(
        f"Runs to compare ({state.MIN_COMPARE} to {state.MAX_COMPARE})",
        options,
        format_func=lambda i: names.get(i, str(i)),
        max_selections=state.MAX_COMPARE,
        key=PICKER_KEY,
    )
    if len(picked) < state.MIN_COMPARE:
        st.caption("Pick at least two runs.")
        return

    details = [d for d in (load_backtest_run_cached(int(i)) for i in picked) if d is not None]
    finished = [d for d in details if d.status == "done" and not d.equity.empty]
    if len(finished) < state.MIN_COMPARE:
        st.warning("At least two of the picked runs need results — launch them first.")
        return
    state.set_compare_ids([d.run_id for d in finished])

    section_header("Equity", "Each run rebased to 100 at its own start, so shapes are comparable.")
    curves = {d.name: d.equity for d in finished}
    benchmark = next((d.benchmark for d in finished if d.benchmark is not None), None)
    st.plotly_chart(charts.equity_overlay(curves, benchmark), use_container_width=True)

    section_header("Metrics")
    st.dataframe(metrics_table(finished), use_container_width=True, column_config={})

    section_header("What differs", "Rows the runs disagree on — everything else is identical.")
    diff = params_diff(finished)
    if diff.empty:
        st.caption("These runs share every setting.")
    else:
        st.dataframe(diff, use_container_width=True)
