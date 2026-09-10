from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import pytest

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.optimizer import (
    OptimizationSpec,
    ParamSpace,
    apply_best,
    build_search_space,
    config_with_params,
    optimize,
    split_is_oos,
)
from strategies.basket import BasketStrategy
from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter

pytest.importorskip("optuna")


class _Tunable(BasketStrategy):
    name = "Tunable"
    mode = "rank"
    fast = IntParameter(10, 1, 20)
    slow = DecimalParameter(0.5, 0.0, 1.0, step=0.1)
    pinned = IntParameter(3, 1, 5, optimize=False)
    flavour = CategoricalParameter("a", choices=("a", "b"))


@dataclass
class _FakeResult:
    equity: pd.Series
    trades: pd.DataFrame = field(default_factory=lambda: pd.DataFrame({"profit_abs": [1.0] * 30}))
    metrics: dict = field(default_factory=lambda: {"sharpe": 1.0, "total_trades": 30})


def _config(strategy: str = "Tunable") -> BacktestConfig:
    return BacktestConfig(
        strategy=strategy,
        params={},
        universe=UniverseSpec(kind="list", symbols=("TCS",)),
        start=date(2020, 1, 1),
        end=date(2024, 1, 1),
    )


@pytest.fixture(autouse=True)
def registered(monkeypatch):
    from services.backtest import optimizer

    monkeypatch.setattr(optimizer, "get_strategy", lambda name: _Tunable)


_NOISE = [((i * 37) % 11 - 5) / 1000 for i in range(60)]


def _peak_at_three(config: BacktestConfig, **kwargs) -> _FakeResult:
    """Equity whose Sharpe peaks when `fast` is 3: a fixed zero-mean wobble around a drift that
    is largest at 3, so the ratio rises and falls with the parameter."""
    fast = config.params.get("fast", 0)
    drift = (1.0 - abs(fast - 3) / 10) * 0.004
    values, level = [], 100.0
    for wobble in _NOISE:
        level *= 1 + drift + wobble
        values.append(level)
    return _FakeResult(equity=pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values))))


def test_search_space_reads_the_declaration_and_skips_pinned_parameters():
    space = build_search_space(_Tunable, OptimizationSpec())
    assert set(space) == {"fast", "slow", "flavour"}
    assert space["fast"] == ParamSpace("int", low=1, high=20, step=1)
    assert space["slow"].kind == "decimal"
    assert space["flavour"].choices == ("a", "b")


def test_search_space_honours_fixed_params_bounds_and_attribute_dimensions():
    spec = OptimizationSpec(
        fixed_params={"flavour": "b"},
        optimize_stoploss=True,
        optimize_top_k=True,
        space_overrides={"fast": ParamSpace("int", low=5, high=8, step=1)},
    )
    space = build_search_space(_Tunable, spec)
    assert "flavour" not in space
    assert space["fast"].low == 5
    assert space["stoploss"].kind == "decimal"
    assert space["top_k"].kind == "int"


def test_top_k_is_only_offered_for_rank_strategies():
    class _Signal(_Tunable):
        name = "Signal"
        mode = "signal"

    space = build_search_space(_Signal, OptimizationSpec(optimize_top_k=True))
    assert "top_k" not in space


def test_config_with_params_routes_attributes_to_overrides():
    config = config_with_params(_config(), {"fast": 7, "stoploss": -0.05, "top_k": 4})
    assert config.params == {"fast": 7}
    assert config.strategy_overrides == {"stoploss": -0.05, "top_k": 4}


def test_split_is_oos_cuts_the_range_and_passes_through_when_disabled():
    is_config, oos_config = split_is_oos(_config(), 0.25)
    assert is_config.start == date(2020, 1, 1)
    assert oos_config.end == date(2024, 1, 1)
    assert is_config.end == oos_config.start
    assert is_config.end < date(2024, 1, 1)
    assert split_is_oos(_config(), 0.0)[1] is None


def test_optimize_finds_the_planted_optimum():
    result = optimize(_config(), OptimizationSpec(n_trials=25, min_trades=1, seed=7), runner=_peak_at_three)
    assert abs(result.best_params["fast"] - 3) <= 1
    assert result.best_value is not None
    assert len(result.trials) == 25
    assert all(t.state == "COMPLETE" for t in result.trials)


def test_trials_below_min_trades_are_recorded_as_pruned():
    def few_trades(config, **kwargs):
        result = _peak_at_three(config)
        result.trades = pd.DataFrame({"profit_abs": [1.0]})
        return result

    result = optimize(_config(), OptimizationSpec(n_trials=5, min_trades=10, seed=1), runner=few_trades)
    assert [t.state for t in result.trials] == ["PRUNED"] * 5
    assert result.best_params == {}
    assert result.best_value is None


def test_out_of_sample_rescoring_uses_the_held_out_window():
    windows: list[tuple] = []

    def record(config, **kwargs):
        windows.append((config.start, config.end))
        return _peak_at_three(config)

    result = optimize(
        _config(), OptimizationSpec(n_trials=6, min_trades=1, oos_fraction=0.3, oos_top_n=2, seed=3), runner=record
    )
    scored = [t for t in result.trials if t.oos_value is not None]
    assert len(scored) == 2
    assert result.oos_start is not None and result.is_end == result.oos_start
    assert windows[-1][0] == result.oos_start


def test_should_stop_halts_the_search_early():
    calls = {"n": 0}

    def counting(config, **kwargs):
        calls["n"] += 1
        return _peak_at_three(config)

    optimize(
        _config(),
        OptimizationSpec(n_trials=50, min_trades=1, seed=5),
        runner=counting,
        should_stop=lambda: calls["n"] >= 3,
    )
    assert calls["n"] < 10


def test_apply_best_merges_the_winning_parameters():
    result = optimize(_config(), OptimizationSpec(n_trials=8, min_trades=1, seed=11), runner=_peak_at_three)
    applied = apply_best(_config(), result)
    assert applied.params["fast"] == result.best_params["fast"]
