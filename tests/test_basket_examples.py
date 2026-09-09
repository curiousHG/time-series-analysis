"""The two example basket strategies: registration, signal semantics, no look-ahead."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

import strategies
from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERO
from services.backtest.engine import run_engine
from services.backtest.universe import BasketInputs
from strategies.basket import BASKET_STRATEGY_REGISTRY, ENTER, ENTER_TAG, EXIT, SCORE, MarketContext
from strategies.basket_examples import MomentumRankStrategy, RsiBasketStrategy


def _frame(closes, start="2023-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1e6}, index=idx
    )


def _ctx(frame: pd.DataFrame, *symbols: str) -> MarketContext:
    return MarketContext(calendar=frame.index, universe=tuple(symbols))


def _v_shape(n: int = 120) -> list[float]:
    """Down, up, then down again so the RSI crosses both the oversold and the overbought level."""
    first, second = n // 2, n // 3
    down = 100 * np.cumprod(np.full(first, 0.985))
    up = down[-1] * np.cumprod(np.full(second, 1.015))
    tail = up[-1] * np.cumprod(np.full(n - first - second, 0.985))
    return [round(x, 2) for x in np.concatenate([down, up, tail])]


def test_examples_are_registered_when_the_strategies_package_is_imported():
    assert strategies.STRATEGY_REGISTRY
    assert BASKET_STRATEGY_REGISTRY[RsiBasketStrategy.name] is RsiBasketStrategy
    assert BASKET_STRATEGY_REGISTRY[MomentumRankStrategy.name] is MomentumRankStrategy
    assert RsiBasketStrategy.mode == "signal"
    assert MomentumRankStrategy.mode == "rank"


def test_rsi_parameter_space_and_startup():
    space = RsiBasketStrategy.parameter_space()
    assert list(space) == ["window", "oversold", "overbought"]
    assert (space["window"].default, space["window"].low, space["window"].high) == (14, 2, 50)
    assert (space["oversold"].default, space["oversold"].low, space["oversold"].high) == (30, 10, 45)
    assert (space["overbought"].default, space["overbought"].low, space["overbought"].high) == (70, 55, 90)
    assert RsiBasketStrategy(window=14).startup_candle_count >= 14
    assert RsiBasketStrategy(window=50).startup_candle_count >= RsiBasketStrategy(window=14).startup_candle_count


def test_rsi_enters_on_the_cross_above_oversold_and_exits_on_the_cross_below_overbought():
    frame = _frame(_v_shape())
    strategy = RsiBasketStrategy()
    ctx = _ctx(frame, "A")
    df = strategy.populate_indicators(frame.copy(), "A", ctx)
    df = strategy.populate_entry(df, "A", ctx)
    df = strategy.populate_exit(df, "A", ctx)
    rsi = df["rsi"]
    expected_entries = (rsi > 30) & (rsi.shift(1) <= 30)
    expected_exits = (rsi < 70) & (rsi.shift(1) >= 70)
    assert df[ENTER].sum() >= 1
    assert df[EXIT].sum() >= 1
    assert df[ENTER].tolist() == expected_entries.fillna(False).tolist()
    assert df[EXIT].tolist() == expected_exits.fillna(False).tolist()
    assert set(df.loc[df[ENTER], ENTER_TAG]) == {"rsi"}
    assert not df[ENTER].iloc[:14].any()


def test_rsi_signals_are_truncation_invariant():
    frame = _frame(_v_shape())
    strategy = RsiBasketStrategy()
    ctx = _ctx(frame, "A")

    def signals(f: pd.DataFrame) -> pd.DataFrame:
        df = strategy.populate_indicators(f.copy(), "A", ctx)
        df = strategy.populate_entry(df, "A", ctx)
        return strategy.populate_exit(df, "A", ctx)

    full = signals(frame)
    cut = signals(frame.iloc[:80])
    assert full[ENTER].iloc[:80].tolist() == cut[ENTER].tolist()
    assert full[EXIT].iloc[:80].tolist() == cut[EXIT].tolist()


def test_momentum_rank_score_and_basket_attributes():
    frame = _frame(list(np.linspace(100, 200, 200)))
    strategy = MomentumRankStrategy(lookback=20)
    ctx = _ctx(frame, "A")
    df = strategy.populate_indicators(frame.copy(), "A", ctx)
    df = strategy.populate_score(df, "A", ctx)
    expected = frame["Close"] / frame["Close"].shift(20) - 1
    pd.testing.assert_series_equal(df[SCORE], expected, check_names=False)
    assert df[SCORE].iloc[:20].isna().all()
    assert MomentumRankStrategy.top_k == 10
    assert MomentumRankStrategy.exit_rank == 15
    assert MomentumRankStrategy.rebalance_every == "M"
    space = MomentumRankStrategy.parameter_space()
    assert (space["lookback"].default, space["lookback"].low, space["lookback"].high, space["lookback"].step) == (
        126,
        20,
        252,
        5,
    )
    assert strategy.startup_candle_count > 20
    with pytest.raises(TypeError):
        MomentumRankStrategy(window=3)


def test_momentum_rank_strategy_runs_end_to_end_on_a_synthetic_basket():
    rng = np.random.default_rng(11)
    n = 140
    idx = pd.bdate_range("2023-01-02", periods=n)
    frames = {}
    for i, symbol in enumerate("ABCDEFGHIJKL"):
        drift = 0.0005 * (i - 5)
        close = 100 * np.cumprod(1 + rng.normal(drift, 0.01, n))
        close = pd.Series(np.round(close, 2), index=idx)
        frames[symbol] = pd.DataFrame(
            {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1e6}, index=idx
        )
    inputs = BasketInputs(
        calendar=idx,
        frames=frames,
        start_pos=25,
        benchmark=None,
        informative={},
        instrument_kinds={s: "equity" for s in frames},
        skipped=[],
        warnings=[],
    )
    config = BacktestConfig(
        strategy=MomentumRankStrategy.name,
        params={"lookback": 20},
        universe=UniverseSpec("list", symbols=tuple(frames)),
        start=idx[25].date(),
        end=idx[-1].date(),
        init_cash=1_000_000.0,
        costs=ZERO,
        slippage_bps=0.0,
        strategy_overrides={"top_k": 3, "exit_rank": 5},
    )
    result = run_engine(inputs, MomentumRankStrategy, {"lookback": 20}, config)
    assert len(result.trades) >= 3
    assert set(result.trades["exit_reason"]) <= {"rank_drop", "force_exit"}
    assert result.open_positions.max() <= 5
    assert (result.cash >= 0).all()
    assert np.allclose(result.equity, result.cash + result.invested)
    assert result.config.start == date(2023, 2, 6)
