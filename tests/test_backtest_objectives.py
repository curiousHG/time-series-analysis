from dataclasses import dataclass, field

import pandas as pd
import pytest

from services.backtest import objectives


@dataclass
class _FakeResult:
    equity: pd.Series
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)


def _equity(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)), dtype=float)


def _trades(profits: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"profit_abs": profits})


def test_total_return_and_drawdown_from_the_equity_curve():
    equity = _equity([100, 120, 90, 110])
    assert objectives.total_return_pct(equity) == pytest.approx(10.0)
    assert objectives.max_drawdown_pct(equity) == pytest.approx(25.0)
    assert objectives.max_drawdown_pct(_equity([100, 110, 120])) == pytest.approx(0.0)


def test_sharpe_and_sortino_are_zero_without_variation():
    flat = _FakeResult(equity=_equity([100, 100, 100, 100]))
    assert objectives.sharpe(flat) == 0.0
    assert objectives.sortino(flat) == 0.0
    rising = _FakeResult(equity=_equity([100, 101, 102, 104, 103, 106]))
    assert objectives.sharpe(rising) > 0


def test_sortino_only_penalises_downside():
    result = _FakeResult(equity=_equity([100, 102, 101, 104, 103, 108]))
    assert objectives.sortino(result) > objectives.sharpe(result)


def test_profit_factor_and_expectancy_from_trades():
    result = _FakeResult(equity=_equity([100, 110]), trades=_trades([100.0, -50.0, 25.0]))
    assert objectives.profit_factor(result) == pytest.approx(2.5)
    assert objectives.expectancy(result) == pytest.approx(25.0)
    all_wins = _FakeResult(equity=_equity([100, 110]), trades=_trades([10.0, 20.0]))
    assert objectives.profit_factor(all_wins) == pytest.approx(30.0)


def test_total_return_dd_applies_the_penalty():
    result = _FakeResult(equity=_equity([100, 120, 90, 110]))
    assert objectives.total_return_dd(result) == pytest.approx(10.0 - 25.0)
    assert objectives.total_return_dd(result, penalty=0.5) == pytest.approx(10.0 - 12.5)


def test_calmar_needs_a_drawdown_to_divide_by():
    assert objectives.calmar(_FakeResult(equity=_equity([100, 110, 120]))) == 0.0
    assert objectives.calmar(_FakeResult(equity=_equity([100, 120, 90, 130]))) > 0


def test_score_prunes_runs_with_too_few_trades():
    result = _FakeResult(equity=_equity([100, 101, 103, 106]), trades=_trades([1.0, 2.0]))
    assert objectives.score(result, "sharpe", min_trades=5) is None
    assert objectives.score(result, "sharpe", min_trades=2) is not None


def test_score_rejects_unknown_objectives():
    with pytest.raises(KeyError):
        objectives.score(_FakeResult(equity=_equity([100, 110])), "profit")
