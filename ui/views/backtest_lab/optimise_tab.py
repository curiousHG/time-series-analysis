"""Optimise view: search a run's parameter space with Optuna and apply the winner to a new run.

The search runs off-thread under `bt_opt_<run id>`; every trial is persisted as it completes, so the
charts here are drawn from stored trial records rather than a live study, and closing the browser
does not lose the search.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import streamlit as st

from services.backtest.objectives import DEFAULT_MIN_TRADES, OBJECTIVE_LABELS
from ui.charts import optimization as opt_charts
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import section_header
from ui.components.metric_tiles import EM_DASH, Kpi, render_kpi_row
from ui.state.loaders import (
    clear_backtest_caches,
    load_backtest_run_cached,
    load_backtest_runs_cached,
    load_optimization_cached,
)
from ui.views.backtest_lab import state
from ui.views.backtest_lab.params import seed

if TYPE_CHECKING:
    from services.backtest.runs import OptimizationDetail, RunDetail, StrategySpec

logger = logging.getLogger(__name__)

DEFAULT_TRIALS = 50
ML_TRIALS = 30
MAX_TRIALS = 500
IS_FRACTION_KEY = "bt_opt_is_fraction"
DEFAULT_IS_FRACTION = 0.7
_TRIAL_COLUMN_CONFIG = {
    "value": st.column_config.NumberColumn("Objective", format="%.4f"),
    "oos_value": st.column_config.NumberColumn("Out of sample", format="%.4f"),
    "duration_s": st.column_config.NumberColumn("Seconds", format="%.1f"),
    "n_trades": st.column_config.NumberColumn("Trades", format="%d"),
}


def _task_handle(run_id: int) -> BackgroundRefresh:
    from services.backtest.runs import optimization_task_key  # noqa: PLC0415 — pulls the engine in

    return BackgroundRefresh(
        optimization_task_key(run_id),
        "Optimisation",
        cache_clearers=(clear_backtest_caches,),
        summarize=lambda opt_id: f"optimisation #{opt_id} stored",
    )


def _bounds(param: Any, name: str) -> tuple[float, float]:
    """Bound inputs that keep the parameter's own type, so an integer knob is not offered in
    hundredths."""
    low_key, high_key = f"bt_opt_low_{name}", f"bt_opt_high_{name}"
    cast = int if param.kind == "int" else float
    seed(low_key, cast(param.low))
    seed(high_key, cast(param.high))
    left, right = st.columns(2)
    low = left.number_input(f"{name} — low", key=low_key, step=cast(param.step))
    high = right.number_input(f"{name} — high", key=high_key, step=cast(param.step))
    return float(low), float(high)


def _search_space(spec: StrategySpec, chosen: list[str]) -> dict[str, tuple[str, float, float, float]]:
    """Per-parameter bound overrides for the numeric dimensions the user kept."""
    overrides: dict[str, tuple[str, float, float, float]] = {}
    for name in chosen:
        param = spec.parameters[name]
        if param.kind not in ("int", "decimal"):
            continue
        low, high = _bounds(param, name)
        if low >= high:
            st.warning(f"{name}: the low bound must sit below the high bound.")
            continue
        overrides[name] = (param.kind, low, high, float(param.step))
    return overrides


def _start(run_id: int, detail: RunDetail, settings: dict, handle: BackgroundRefresh) -> None:
    from services.backtest.optimizer import OptimizationSpec, ParamSpace  # noqa: PLC0415 — pulls optuna in
    from services.backtest.runs import start_optimization  # noqa: PLC0415 — pulls the engine in

    spec = OptimizationSpec(
        objective=settings["objective"],
        n_trials=settings["n_trials"],
        min_trades=settings["min_trades"],
        optimize_stoploss=settings["optimize_stoploss"],
        optimize_top_k=settings["optimize_top_k"],
        oos_fraction=settings["oos_fraction"],
        space_overrides={
            name: ParamSpace(kind=kind, low=low, high=high, step=step)
            for name, (kind, low, high, step) in settings["space_overrides"].items()
        },
        fixed_params={k: v for k, v in detail.config.params.items() if k in settings["pinned"]},
    )
    handle.start(lambda: start_optimization(run_id, spec, progress_cb=progress_cb(handle.key)))
    logger.info("backtest lab: optimisation started for run %s (%s)", run_id, spec.objective)


def _render_form(run_id: int, detail: RunDetail, spec: StrategySpec, handle: BackgroundRefresh) -> None:
    tunable = [name for name, param in spec.parameters.items() if param.optimize]
    left, middle, right, far = st.columns(4)
    with left:
        seed("bt_opt_objective", "sharpe")
        objective = st.selectbox(
            "Objective", list(OBJECTIVE_LABELS), format_func=lambda o: OBJECTIVE_LABELS[o], key="bt_opt_objective"
        )
    with middle:
        seed("bt_opt_trials", ML_TRIALS if spec.is_ml else DEFAULT_TRIALS)
        n_trials = int(st.number_input("Trials", min_value=2, max_value=MAX_TRIALS, step=5, key="bt_opt_trials"))
    with right:
        seed("bt_opt_min_trades", DEFAULT_MIN_TRADES)
        min_trades = int(st.number_input("Min trades", min_value=0, max_value=500, step=5, key="bt_opt_min_trades"))
    with far:
        seed(IS_FRACTION_KEY, DEFAULT_IS_FRACTION)
        is_fraction = float(
            st.slider(
                "In-sample share",
                0.5,
                1.0,
                step=0.05,
                key=IS_FRACTION_KEY,
                help="The rest of the period is held out: the best trials are re-scored on it, "
                "which is how you see whether a winning parameter set was just fitted to noise.",
            )
        )

    chosen = st.multiselect("Parameters to search", tunable, default=tunable, key="bt_opt_params")
    pinned = [name for name in spec.parameters if name not in chosen]
    space_overrides = _search_space(spec, chosen)
    attr_left, attr_right = st.columns(2)
    with attr_left:
        optimize_stoploss = st.checkbox(
            "Search the stoploss", key="bt_opt_stoploss", help="Off by default — an optimised stop overfits quickly."
        )
    with attr_right:
        optimize_top_k = st.checkbox("Search top K", key="bt_opt_top_k", disabled=spec.mode != "rank")

    if spec.is_ml:
        st.caption("This strategy retrains on every walk-forward window per trial — expect minutes, not seconds.")
    if not chosen and not (optimize_stoploss or optimize_top_k):
        st.warning("Pick at least one parameter (or a strategy attribute) to search.")
        return
    if handle.is_running():
        st.caption("An optimisation is already running for this run.")
        return
    if st.button("Start optimisation", key="bt_opt_start", type="primary"):
        _start(
            run_id,
            detail,
            {
                "objective": objective,
                "n_trials": n_trials,
                "min_trades": min_trades,
                "optimize_stoploss": optimize_stoploss,
                "optimize_top_k": optimize_top_k,
                "oos_fraction": round(1.0 - is_fraction, 2),
                "space_overrides": space_overrides,
                "pinned": pinned,
            },
            handle,
        )
        st.rerun()


def _apply_best(run_id: int, detail: RunDetail, opt: OptimizationDetail) -> None:
    from services.backtest.optimizer import config_with_params  # noqa: PLC0415 — pulls optuna in
    from services.backtest.runs import duplicate_run  # noqa: PLC0415 — pulls the engine in

    payload = config_with_params(detail.config, opt.best_params).to_dict()
    new_id = duplicate_run(
        run_id,
        {
            "params": payload["params"],
            "strategy_overrides": payload["strategy_overrides"],
            "name": f"{detail.name} · best trial",
        },
    )
    clear_backtest_caches()
    logger.info("backtest lab: applied best trial of optimisation %s to new run %s", opt.optimization_id, new_id)
    state.go_to("Runs", run_id=new_id)


def _render_results(run_id: int, detail: RunDetail, opt: OptimizationDetail) -> None:
    from services.backtest.runs import param_columns  # noqa: PLC0415 — pulls the engine in

    trials = opt.trials
    complete = trials[trials["state"] == "COMPLETE"] if "state" in trials.columns else trials
    best_oos = (
        complete["oos_value"].max() if "oos_value" in complete.columns and complete["oos_value"].notna().any() else None
    )
    render_kpi_row(
        [
            Kpi("Objective", OBJECTIVE_LABELS.get(opt.objective, opt.objective)),
            Kpi("Best in-sample", f"{opt.best_value:.4f}" if opt.best_value is not None else EM_DASH),
            Kpi("Best out of sample", f"{best_oos:.4f}" if best_oos is not None else EM_DASH),
            Kpi("Trials kept", f"{len(complete)} of {len(trials)}"),
        ]
    )
    if opt.status == "failed":
        st.error(f"The optimisation failed: {opt.error or 'no error text was recorded.'}")
    if trials.empty:
        st.caption("No trials stored yet.")
        return

    section_header("Trials", "Every trial's objective with the running best.")
    st.plotly_chart(opt_charts.trials_history(trials), use_container_width=True)
    params = param_columns(trials)
    left, right = st.columns(2)
    with left:
        section_header("Parameter importance")
        if opt.param_importance:
            st.plotly_chart(opt_charts.param_importance_bar(opt.param_importance), use_container_width=True)
        else:
            st.caption("Importance needs at least two completed trials.")
    with right:
        section_header("In sample vs out of sample")
        if best_oos is not None:
            st.plotly_chart(opt_charts.is_vs_oos(trials), use_container_width=True)
        else:
            st.caption("Set an in-sample share below 1.00 to hold out a tail.")
    if params:
        section_header("Parameter space", "One axis per searched parameter, coloured by objective.")
        st.plotly_chart(opt_charts.parallel_coordinates(trials, params), use_container_width=True)

    st.dataframe(
        trials.sort_values("value", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config=_TRIAL_COLUMN_CONFIG,
    )
    st.caption(f"Best parameters: {opt.best_params or '—'}")
    disabled = not opt.best_params
    if st.button("Apply best to a new run", key="bt_opt_apply", type="primary", disabled=disabled):
        _apply_best(run_id, detail, opt)


def render(catalog: dict[str, StrategySpec]) -> None:
    from services.backtest.runs import latest_optimization  # noqa: PLC0415 — pulls the engine in

    runs = load_backtest_runs_cached()
    options = [] if runs.is_empty() else [int(v) for v in runs["id"].to_list()]
    names = {} if runs.is_empty() else dict(zip(options, runs["name"].to_list(), strict=False))
    if not options:
        st.info("No runs yet — create one on the Runs view, then optimise it here.")
        return
    state.sync_picker(state.OPT_PICKER_KEY, options)
    run_id = int(
        st.selectbox("Base run", options, format_func=lambda i: names.get(i, str(i)), key=state.OPT_PICKER_KEY)
    )
    detail = load_backtest_run_cached(run_id)
    if detail is None:
        st.warning("That run no longer exists.")
        return
    spec = catalog.get(detail.config.strategy)
    if spec is None:
        st.warning(f"“{detail.config.strategy}” is no longer registered.")
        return

    handle = _task_handle(run_id)
    handle.consume()
    if handle.is_running():
        handle.poll()

    section_header("Search", "Trials run one at a time against the base run's data, loaded once.")
    _render_form(run_id, detail, spec, handle)

    opt_id = latest_optimization(run_id)
    if opt_id is None:
        st.caption("No optimisation has run for this configuration yet.")
        return
    opt = load_optimization_cached(int(opt_id))
    if opt is not None:
        section_header("Results", f"Optimisation #{opt.optimization_id} · {opt.status}.")
        _render_results(run_id, detail, opt)
