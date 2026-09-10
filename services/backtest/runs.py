"""Run lifecycle: create a configuration, execute it, persist the result, read it back.

`start_run` is synchronous; the UI wraps it in `core.background.start_task` under `task_key(run_id)`
so a long run stays off the Streamlit thread. Runs move draft -> queued -> running -> done | failed,
and a failed run keeps its error text until it is relaunched.

A saved run records the symbols it actually ran on: the UI resolves a watchlist universe into an
explicit list before creating the run, so later edits to the watchlist cannot change a stored result.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from data.repositories import backtest_runs as repo
from services.backtest.config import BacktestConfig
from services.backtest.engine import run_engine
from services.backtest.metrics import periodic_breakdown
from services.backtest.universe import load_backtest_inputs
from strategies.basket import BASKET_STRATEGY_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Callable

    import polars as pl

    from services.backtest.engine import BacktestResult
    from strategies.parameters import Parameter

logger = logging.getLogger(__name__)

TASK_PREFIX = "bt_run_"
COST_KEYS = ("brokerage", "stt", "exchange_txn", "sebi", "stamp", "gst", "dp")


@dataclass(frozen=True)
class StrategySpec:
    """What the run form needs to render a strategy: its declared parameters and the class
    attributes a run may override."""

    name: str
    mode: str
    parameters: dict[str, Parameter]
    stoploss: float
    trailing_stop: bool
    minimal_roi: dict[int, float]
    max_open_trades: int
    top_k: int
    exit_rank: int | None
    rebalance_every: int | str
    startup_candle_count: int
    is_ml: bool


@dataclass
class RunDetail:
    run_id: int
    name: str
    status: str
    config: BacktestConfig
    metrics: dict
    trades: pd.DataFrame
    equity: pd.Series
    benchmark: pd.Series | None
    per_symbol: pd.DataFrame
    per_exit_reason: pd.DataFrame
    periodic: pd.DataFrame
    costs: dict[str, float]
    duration_s: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    diagnostics: Any | None = None


def task_key(run_id: int) -> str:
    return f"{TASK_PREFIX}{run_id}"


def strategy_catalog() -> dict[str, StrategySpec]:
    """Every registered basket strategy with the parameters and attributes the run form needs."""
    catalog: dict[str, StrategySpec] = {}
    for name, cls in BASKET_STRATEGY_REGISTRY.items():
        instance = cls()
        catalog[name] = StrategySpec(
            name=name,
            mode=cls.mode,
            parameters=cls.parameter_space(),
            stoploss=instance.stoploss,
            trailing_stop=instance.trailing_stop,
            minimal_roi=dict(instance.minimal_roi),
            max_open_trades=instance.max_open_trades,
            top_k=instance.top_k,
            exit_rank=instance.exit_rank,
            rebalance_every=instance.rebalance_every,
            startup_candle_count=int(instance.startup_candle_count),
            is_ml=hasattr(instance, "wf_config"),
        )
    return catalog


def get_strategy(name: str) -> type:
    if name not in BASKET_STRATEGY_REGISTRY:
        raise KeyError(f"no basket strategy named {name!r}")
    return BASKET_STRATEGY_REGISTRY[name]


def create_run(config: BacktestConfig, name: str) -> int:
    payload = config.to_dict()
    return repo.save_backtest_run(
        name=name,
        strategy=config.strategy,
        mode=get_strategy(config.strategy).mode,
        config=payload,
        params=payload["params"],
        universe=payload["universe"],
        costs=payload["costs"],
        start=config.start,
        end=config.end,
        init_cash=config.init_cash,
    )


def update_run(run_id: int, config: BacktestConfig, name: str) -> None:
    payload = config.to_dict()
    repo.update_backtest_run(
        run_id,
        name=name,
        config=payload,
        params=payload["params"],
        universe=payload["universe"],
        costs=payload["costs"],
    )


def duplicate_run(run_id: int, overrides: dict | None = None) -> int:
    row = repo.get_backtest_run(run_id)
    if row is None:
        raise KeyError(run_id)
    overrides = dict(overrides or {})
    name = overrides.pop("name", f"{row['name']} (copy)")
    payload = {**row["config"], **overrides}
    return create_run(BacktestConfig.from_dict(payload), name)


def delete_run(run_id: int) -> None:
    repo.delete_backtest_run(run_id)


def run_backtest_basket(
    config: BacktestConfig, *, watchlist: list[str] | None = None, progress_cb: Callable[..., None] | None = None
) -> BacktestResult:
    """Load inputs and simulate, without touching the database. The optimiser and tests use this."""
    strategy_cls = get_strategy(config.strategy)
    inputs = load_backtest_inputs(config, strategy_cls, watchlist=watchlist, progress_cb=progress_cb)
    return run_engine(inputs, strategy_cls, config.params, config, progress_cb=progress_cb)


def start_run(run_id: int, *, progress_cb: Callable[..., None] | None = None) -> dict:
    """Execute a stored run and persist its result. Returns the summary metrics for the toast."""
    row = repo.get_backtest_run(run_id)
    if row is None:
        raise KeyError(run_id)
    config = BacktestConfig.from_dict(row["config"])
    repo.save_backtest_run_status(run_id, "queued")
    repo.save_backtest_run_status(run_id, "running")
    started = time.perf_counter()
    try:
        result = run_backtest_basket(config, progress_cb=progress_cb)
    except Exception as exc:
        logger.exception("backtest run %s failed", run_id)
        repo.save_backtest_run_status(run_id, "failed", error=str(exc))
        raise
    if progress_cb is not None:
        progress_cb(phase="Saving", done=1, total=1)
    summary = _json_safe(result.metrics)
    diagnostics = diagnostics_payload(result.diagnostics)
    if diagnostics is not None:
        summary["ml_diagnostics"] = diagnostics
    repo.save_backtest_result(
        run_id,
        summary=summary,
        equity=equity_payload(result),
        trades=trade_records(result.trades),
        duration_s=time.perf_counter() - started,
    )
    return summary


def load_run(run_id: int) -> RunDetail | None:
    row = repo.get_backtest_run(run_id)
    if row is None:
        return None
    config = BacktestConfig.from_dict(row["config"])
    trades = repo.load_backtest_trades(run_id).to_pandas()
    equity, benchmark = _equity_from_payload(row.get("equity"))
    metrics = dict(row.get("summary") or {})
    diagnostics = diagnostics_from_payload(metrics.pop("ml_diagnostics", None))
    return RunDetail(
        run_id=run_id,
        name=row["name"],
        status=row["status"],
        config=config,
        metrics=metrics,
        trades=trades,
        equity=equity,
        benchmark=benchmark,
        per_symbol=_per_symbol(trades),
        per_exit_reason=_per_exit_reason(trades),
        periodic=periodic_breakdown(equity, "M") if len(equity) else pd.DataFrame(),
        costs=cost_breakdown(trades),
        duration_s=row.get("duration_s"),
        error=row.get("error"),
        diagnostics=diagnostics,
    )


def list_runs() -> pl.DataFrame:
    return repo.load_backtest_runs()


def equity_payload(result: BacktestResult) -> dict:
    """The equity curve in a JSON-safe shape: parallel date and value lists."""
    dates = [d.date().isoformat() for d in result.equity.index]
    payload = {"dates": dates, "equity": [float(v) for v in result.equity], "cash": [float(v) for v in result.cash]}
    if result.benchmark_returns is not None and len(result.benchmark_returns):
        growth = (1 + result.benchmark_returns.reindex(result.equity.index).fillna(0.0)).cumprod()
        payload["benchmark"] = [float(v) for v in growth * float(result.equity.iloc[0])]
    return payload


def trade_records(trades: pd.DataFrame) -> list[dict]:
    """Trade rows as plain dicts with date objects, ready for the repository."""
    records: list[dict] = []
    for row in trades.to_dict("records"):
        record = dict(row)
        for key in ("open_date", "close_date"):
            record[key] = pd.Timestamp(record[key]).date()
        record["shares"] = int(record["shares"])
        record["bars_held"] = int(record["bars_held"])
        records.append(record)
    return records


def cost_breakdown(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {"entry": 0.0, "exit": 0.0}
    return {"entry": float(trades["entry_costs"].sum()), "exit": float(trades["exit_costs"].sum())}


def _per_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    from services.backtest.metrics import per_symbol_stats  # noqa: PLC0415 — avoids a cycle at import time

    return per_symbol_stats(trades)


def _per_exit_reason(trades: pd.DataFrame) -> pd.DataFrame:
    from services.backtest.metrics import per_exit_reason_stats  # noqa: PLC0415

    return per_exit_reason_stats(trades)


def _equity_from_payload(payload: dict | None) -> tuple[pd.Series, pd.Series | None]:
    if not payload or not payload.get("dates"):
        return pd.Series(dtype=float), None
    index = pd.DatetimeIndex(pd.to_datetime(payload["dates"]))
    equity = pd.Series(payload["equity"], index=index, name="equity")
    benchmark = pd.Series(payload["benchmark"], index=index, name="benchmark") if payload.get("benchmark") else None
    return equity, benchmark


def _json_safe(metrics: dict) -> dict:
    out: dict = {}
    for key, value in metrics.items():
        if isinstance(value, pd.Timestamp):
            out[key] = value.date().isoformat()
        elif isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            out[key] = None
        elif hasattr(value, "item"):
            out[key] = value.item()
        else:
            out[key] = value
    return out


OPT_TASK_PREFIX = "bt_opt_"


@dataclass
class OptimizationDetail:
    optimization_id: int
    run_id: int
    status: str
    objective: str
    n_trials: int
    best_params: dict
    best_value: float | None
    param_importance: dict[str, float]
    trials: pd.DataFrame
    is_end: Any | None = None
    oos_start: Any | None = None
    error: str | None = None


def optimization_task_key(run_id: int) -> str:
    return f"{OPT_TASK_PREFIX}{run_id}"


def start_optimization(
    run_id: int,
    spec,
    *,
    progress_cb: Callable[..., None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Search the base run's parameter space and persist every trial. Returns the optimisation id."""
    from services.backtest.optimizer import optimize  # noqa: PLC0415 — pulls optuna in only when used

    row = repo.get_backtest_run(run_id)
    if row is None:
        raise KeyError(run_id)
    config = BacktestConfig.from_dict(row["config"])
    optimization_id = repo.save_optimization(
        run_id=run_id, objective=spec.objective, n_trials=spec.n_trials, spec=spec.to_dict()
    )
    try:
        result = optimize(config, spec, progress_cb=progress_cb, should_stop=should_stop)
    except Exception as exc:
        logger.exception("optimisation for run %s failed", run_id)
        repo.save_optimization_result(
            optimization_id,
            status="failed",
            best_params=None,
            best_value=None,
            param_importance=None,
            is_end=None,
            oos_start=None,
            trials=[],
            error=str(exc),
        )
        raise
    repo.save_optimization_result(
        optimization_id,
        status="done",
        best_params=result.best_params,
        best_value=result.best_value,
        param_importance=result.param_importance,
        is_end=result.is_end,
        oos_start=result.oos_start,
        trials=[t.as_row() for t in result.trials],
    )
    return optimization_id


def load_optimization(optimization_id: int) -> OptimizationDetail | None:
    row = repo.get_optimization(optimization_id)
    if row is None:
        return None
    return OptimizationDetail(
        optimization_id=optimization_id,
        run_id=row["run_id"],
        status=row["status"],
        objective=row["objective"],
        n_trials=row["n_trials"],
        best_params=row.get("best_params") or {},
        best_value=row.get("best_value"),
        param_importance=row.get("param_importance") or {},
        trials=trials_frame(repo.load_trials(optimization_id)),
        is_end=row.get("is_end"),
        oos_start=row.get("oos_start"),
        error=row.get("error"),
    )


def latest_optimization(run_id: int) -> int | None:
    rows = repo.load_optimizations(run_id)
    return rows[0]["id"] if rows else None


def trials_frame(trials: list[dict]) -> pd.DataFrame:
    """One row per trial with the suggested parameters flattened into columns, which is what the
    trials table and the parallel-coordinates chart read."""
    if not trials:
        return pd.DataFrame(columns=["number", "state", "value", "oos_value", "n_trades", "duration_s"])
    rows = []
    for trial in trials:
        row = {
            "number": trial["number"],
            "state": trial["state"],
            "value": trial["value"],
            "oos_value": trial.get("oos_value"),
            "n_trades": trial["n_trades"],
            "duration_s": trial["duration_s"],
            **(trial.get("params") or {}),
            **{f"metric_{k}": v for k, v in (trial.get("is_metrics") or {}).items()},
        }
        rows.append(row)
    return pd.DataFrame(rows)


def param_columns(trials: pd.DataFrame) -> list[str]:
    """The parameter columns in a trials frame, in declaration order."""
    fixed = {"number", "state", "value", "oos_value", "n_trades", "duration_s"}
    return [c for c in trials.columns if c not in fixed and not c.startswith("metric_")]


MAX_STORED_PREDICTIONS = 20_000


def diagnostics_payload(diagnostics) -> dict | None:
    """ML diagnostics in a JSON-safe shape. The prediction column is kept for the distribution
    chart and capped, because a wide basket over ten years is far more detail than the chart
    can show."""
    if diagnostics is None:
        return None
    predictions = diagnostics.predictions
    values: list[float] = []
    if len(predictions) and "pred" in predictions.columns:
        column = predictions["pred"].dropna()
        if len(column) > MAX_STORED_PREDICTIONS:
            column = column.sample(MAX_STORED_PREDICTIONS, random_state=0).sort_index()
        values = [float(v) for v in column]
    return {
        "summary": _json_safe(diagnostics.summary),
        "windows": _records(diagnostics.windows),
        "importance": _records(diagnostics.importance.reset_index()),
        "coverage": {str(k): float(v) for k, v in diagnostics.coverage.items()},
        "predictions": values,
    }


def diagnostics_from_payload(payload: dict | None):
    """Rebuild the frames the diagnostic charts read. Returns None when the run carried none."""
    if not payload:
        return None
    from strategies.ml.diagnostics import MLDiagnostics  # noqa: PLC0415 — domain import, only for ML runs

    windows = pd.DataFrame(payload.get("windows") or [])
    for column in ("train_start", "train_end", "test_start", "test_end"):
        if column in windows.columns:
            windows[column] = pd.to_datetime(windows[column])
    importance = pd.DataFrame(payload.get("importance") or [])
    if "feature" in importance.columns:
        importance = importance.set_index("feature")
    return MLDiagnostics(
        summary=payload.get("summary") or {},
        windows=windows,
        importance=importance,
        predictions=pd.DataFrame({"pred": payload.get("predictions") or []}, dtype=float),
        coverage=pd.Series(payload.get("coverage") or {}, dtype=float),
    )


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty:
        return []
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.date.astype(str)
    return [{k: _cell(v) for k, v in record.items()} for record in out.to_dict("records")]


def _cell(value):
    if value is None or (isinstance(value, float) and value != value):
        return None
    return value.item() if hasattr(value, "item") else value
