"""Optuna search over a strategy's declared parameters.

The search space comes from `BasketStrategy.parameter_space()`, so a strategy declares its knobs
once and both the run form and the optimiser read them. Parameter dimensions land in
`config.params`; stoploss and top_k are strategy attributes and land in `config.strategy_overrides`.

Trials run one at a time against the same loaded inputs. With `oos_fraction` set, trials are scored
on the earlier slice and the best few are re-run on the held-out tail, so the trials table can show
whether a winning parameter set survives out of sample.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from services.backtest.objectives import DEFAULT_MIN_TRADES, score
from services.backtest.runs import get_strategy, run_backtest_basket
from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from services.backtest.config import BacktestConfig

logger = logging.getLogger(__name__)

STOPLOSS_BOUNDS = (-0.30, -0.03)
TOP_K_BOUNDS = (3, 30)
SUMMARY_METRIC_KEYS = (
    "cagr",
    "sharpe",
    "sortino",
    "calmar",
    "max_drawdown",
    "total_return_pct",
    "win_rate",
    "profit_factor",
    "total_trades",
    "total_costs",
)


@dataclass(frozen=True)
class ParamSpace:
    kind: str
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: tuple = ()

    def to_dict(self) -> dict:
        return {"kind": self.kind, "low": self.low, "high": self.high, "step": self.step, "choices": list(self.choices)}


@dataclass(frozen=True)
class OptimizationSpec:
    objective: str = "sharpe"
    n_trials: int = 50
    min_trades: int = DEFAULT_MIN_TRADES
    optimize_stoploss: bool = False
    optimize_top_k: bool = False
    oos_fraction: float = 0.0
    oos_top_n: int = 5
    space_overrides: dict[str, ParamSpace] = field(default_factory=dict)
    fixed_params: dict[str, Any] = field(default_factory=dict)
    seed: int | None = 42
    timeout_s: float | None = None

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "n_trials": self.n_trials,
            "min_trades": self.min_trades,
            "optimize_stoploss": self.optimize_stoploss,
            "optimize_top_k": self.optimize_top_k,
            "oos_fraction": self.oos_fraction,
            "oos_top_n": self.oos_top_n,
            "space_overrides": {k: v.to_dict() for k, v in self.space_overrides.items()},
            "fixed_params": self.fixed_params,
            "seed": self.seed,
        }


@dataclass
class TrialRecord:
    number: int
    state: str
    params: dict
    value: float | None = None
    n_trades: int = 0
    is_metrics: dict = field(default_factory=dict)
    oos_metrics: dict | None = None
    oos_value: float | None = None
    duration_s: float = 0.0

    def as_row(self) -> dict:
        return {
            "number": self.number,
            "state": self.state,
            "params": self.params,
            "value": self.value,
            "n_trades": self.n_trades,
            "is_metrics": self.is_metrics,
            "oos_metrics": self.oos_metrics,
            "oos_value": self.oos_value,
            "duration_s": self.duration_s,
        }


@dataclass
class OptimizationResult:
    strategy_name: str
    spec: OptimizationSpec
    best_params: dict
    best_value: float | None
    trials: list[TrialRecord]
    param_importance: dict[str, float]
    is_end: date | None = None
    oos_start: date | None = None


ATTRIBUTE_DIMENSIONS = ("stoploss", "top_k")


def build_search_space(strategy_cls: type, spec: OptimizationSpec) -> dict[str, ParamSpace]:
    """Declared parameters with `optimize=True`, minus pinned ones, plus the optional attribute
    dimensions. Caller-supplied bounds win over the declaration."""
    space: dict[str, ParamSpace] = {}
    for name, param in strategy_cls.parameter_space().items():
        if not param.optimize or name in spec.fixed_params:
            continue
        if isinstance(param, IntParameter):
            space[name] = ParamSpace("int", low=param.low, high=param.high, step=param.step)
        elif isinstance(param, DecimalParameter):
            space[name] = ParamSpace("decimal", low=param.low, high=param.high, step=param.step)
        elif isinstance(param, CategoricalParameter):
            space[name] = ParamSpace("categorical", choices=tuple(param.choices))
    if spec.optimize_stoploss:
        space["stoploss"] = ParamSpace("decimal", low=STOPLOSS_BOUNDS[0], high=STOPLOSS_BOUNDS[1], step=0.01)
    if spec.optimize_top_k and getattr(strategy_cls, "mode", "signal") == "rank":
        space["top_k"] = ParamSpace("int", low=TOP_K_BOUNDS[0], high=TOP_K_BOUNDS[1], step=1)
    for name, override in spec.space_overrides.items():
        if name in space or name in ATTRIBUTE_DIMENSIONS:
            space[name] = override
    return space


def suggest_params(trial, space: dict[str, ParamSpace]) -> dict:
    values: dict = {}
    for name, dim in space.items():
        if dim.kind == "int":
            values[name] = trial.suggest_int(name, int(dim.low), int(dim.high), step=int(dim.step) if dim.step else 1)
        elif dim.kind == "decimal":
            values[name] = trial.suggest_float(name, float(dim.low), float(dim.high), step=dim.step)
        else:
            values[name] = trial.suggest_categorical(name, list(dim.choices))
    return values


def config_with_params(config: BacktestConfig, params: dict) -> BacktestConfig:
    """Split a trial's suggestion: strategy attributes into overrides, everything else into params."""
    attributes = {k: v for k, v in params.items() if k in ATTRIBUTE_DIMENSIONS}
    plain = {k: v for k, v in params.items() if k not in ATTRIBUTE_DIMENSIONS}
    return replace(
        config,
        params={**config.params, **plain},
        strategy_overrides={**config.strategy_overrides, **attributes},
    )


def split_is_oos(config: BacktestConfig, oos_fraction: float) -> tuple[BacktestConfig, BacktestConfig | None]:
    """Split the run's date range: the earlier `1 - oos_fraction` for the search, the tail held out."""
    if not 0 < oos_fraction < 1:
        return config, None
    span = (config.end - config.start).days
    if span < 2:
        return config, None
    cut = config.start + (config.end - config.start) * (1 - oos_fraction)
    return replace(config, end=cut), replace(config, start=cut)


def apply_best(config: BacktestConfig, result: OptimizationResult) -> BacktestConfig:
    return config_with_params(config, result.best_params)


def optimize(
    config: BacktestConfig,
    spec: OptimizationSpec,
    *,
    progress_cb: Callable[..., None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    runner: Callable[..., Any] | None = None,
) -> OptimizationResult:
    """Search `spec.n_trials` parameter sets and return the trials with the best one."""
    import optuna  # noqa: PLC0415 — heavy dep, only needed when an optimisation actually runs

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    strategy_cls = get_strategy(config.strategy)
    space = build_search_space(strategy_cls, spec)
    if not space:
        raise ValueError(f"{config.strategy} has no tunable parameters")
    run = runner or run_backtest_basket
    is_config, oos_config = split_is_oos(config, spec.oos_fraction)
    records: list[TrialRecord] = []

    def objective(trial) -> float:
        params = suggest_params(trial, space)
        started = time.perf_counter()
        result = run(config_with_params(is_config, params))
        value = score(result, spec.objective, min_trades=spec.min_trades)
        record = TrialRecord(
            number=trial.number,
            state="COMPLETE" if value is not None else "PRUNED",
            params=params,
            value=value,
            n_trades=len(result.trades),
            is_metrics=_compact(result.metrics),
            duration_s=time.perf_counter() - started,
        )
        records.append(record)
        _report(
            progress_cb,
            phase="Optimising",
            done=trial.number + 1,
            total=spec.n_trials,
            message=f"trial {trial.number}: {spec.objective}={_fmt(value)} trades={record.n_trades}",
        )
        if value is None:
            raise optuna.TrialPruned
        return value

    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=spec.seed, multivariate=True)
    )
    study.optimize(
        objective,
        n_trials=spec.n_trials,
        timeout=spec.timeout_s,
        n_jobs=1,
        gc_after_trial=True,
        callbacks=[lambda st, _tr: st.stop() if should_stop is not None and should_stop() else None],
    )

    completed = [r for r in records if r.state == "COMPLETE" and r.value is not None]
    if oos_config is not None and completed:
        _score_out_of_sample(completed, oos_config, spec, run, progress_cb)
    best = max(completed, key=lambda r: r.value, default=None)
    return OptimizationResult(
        strategy_name=config.strategy,
        spec=spec,
        best_params=best.params if best else {},
        best_value=best.value if best else None,
        trials=records,
        param_importance=_importance(study),
        is_end=is_config.end if oos_config is not None else None,
        oos_start=oos_config.start if oos_config is not None else None,
    )


def _score_out_of_sample(completed, oos_config, spec, run, progress_cb) -> None:
    top = sorted(completed, key=lambda r: r.value, reverse=True)[: spec.oos_top_n]
    for i, record in enumerate(top):
        result = run(config_with_params(oos_config, record.params))
        record.oos_value = score(result, spec.objective, min_trades=1)
        record.oos_metrics = _compact(result.metrics)
        _report(progress_cb, phase="Out of sample", done=i + 1, total=len(top))


def _importance(study) -> dict[str, float]:
    import optuna  # noqa: PLC0415

    complete = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if len(complete) < 2:
        return {}
    try:
        return {k: float(v) for k, v in optuna.importance.get_param_importances(study).items()}
    except (ValueError, RuntimeError) as exc:
        logger.debug("parameter importance unavailable: %s", exc)
        return {}


def _compact(metrics: dict) -> dict:
    return {k: metrics[k] for k in SUMMARY_METRIC_KEYS if k in metrics and _finite(metrics[k])}


def _finite(value) -> bool:
    return not (isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))))


def _fmt(value: float | None) -> str:
    return "pruned" if value is None else f"{value:.3f}"


def _report(progress_cb: Callable[..., None] | None, **fields) -> None:
    if progress_cb is not None:
        progress_cb(**fields)
