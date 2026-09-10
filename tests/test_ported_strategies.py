"""The strategies ported from freq_strategies, on synthetic bars built to trigger each rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERO
from services.backtest.engine import run_engine
from services.backtest.universe import BasketInputs
from strategies.basket import ENTER, EXIT, SCORE, MarketContext
from strategies.ported import (
    CapitulationBounceStrategy,
    DipInUptrendStrategy,
    DonchianBreakoutStrategy,
    MomentumRegimeStrategy,
    OversoldReversionStrategy,
    PullbackTrendStrategy,
)


def _ohlcv(closes, *, opens=None, highs=None, lows=None, volume=500_000) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(closes))
    close = pd.Series(closes, index=index, dtype=float)
    open_ = pd.Series(opens, index=index, dtype=float) if opens is not None else close.shift(1).fillna(close.iloc[0])
    high = pd.Series(highs, index=index, dtype=float) if highs is not None else np.maximum(open_, close) * 1.005
    low = pd.Series(lows, index=index, dtype=float) if lows is not None else np.minimum(open_, close) * 0.995
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": float(volume)}, index=index)


def _context(frames, benchmark=None) -> MarketContext:
    index = next(iter(frames.values())).index
    return MarketContext(calendar=index, universe=tuple(frames), benchmark=benchmark)


def _signals(strategy, frames, *, benchmark=None) -> dict[str, pd.DataFrame]:
    ctx = _context(frames, benchmark)
    prepared = strategy.prepare_basket({s: strategy.populate_indicators(f.copy(), s, ctx) for s, f in frames.items()})
    out = {}
    for symbol, frame in prepared.items():
        frame = strategy.populate_entry(frame, symbol, ctx)
        frame = strategy.populate_exit(frame, symbol, ctx)
        out[symbol] = strategy.populate_score(frame, symbol, ctx)
    return out


def _run(strategy_cls, frames, *, params=None, benchmark=None, **overrides):
    index = next(iter(frames.values())).index
    inputs = BasketInputs(
        calendar=index,
        frames={s: f.copy() for s, f in frames.items()},
        start_pos=strategy_cls(**(params or {})).startup_candle_count,
        benchmark=benchmark,
        instrument_kinds=dict.fromkeys(frames, "equity"),
    )
    config = BacktestConfig(
        strategy=strategy_cls.name,
        params=params or {},
        universe=UniverseSpec("list", symbols=tuple(frames)),
        start=index[0].date(),
        end=index[-1].date(),
        init_cash=500_000.0,
        costs=ZERO,
        slippage_bps=0.0,
        strategy_overrides=overrides,
    )
    return run_engine(inputs, strategy_cls, params or {}, config)


def _slide(start: float, end: float, n: int) -> list[float]:
    return list(np.linspace(start, end, n))


def _walk(n: int, drift: float, vol: float, seed: int, *, start: float = 100.0) -> pd.DataFrame:
    """A seeded random walk with an independently noisy bar range, so ADX behaves like real data."""
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    index = pd.bdate_range("2020-01-01", periods=n)
    open_ = pd.Series(close, index=index).shift(1).fillna(close[0])
    span = rng.uniform(0.004, 0.02, n) * close
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + span,
            "Low": np.minimum(open_, close) - span,
            "Close": close,
            "Volume": 500_000.0,
        },
        index=index,
    )


# ------------------------------------------------------------------ reversion


def _dislocated() -> pd.DataFrame:
    """A noisy slide that drags price well below its own average and swings RSI through 30."""
    return _walk(220, drift=-0.004, vol=0.018, seed=1)


def test_oversold_reversion_needs_both_the_rsi_cross_and_the_dislocation():
    frames = {"FALL": _dislocated()}
    strategy = OversoldReversionStrategy(dislocation=-0.10)
    entries = _signals(strategy, frames)["FALL"][ENTER]
    assert entries.any()

    unreachable = OversoldReversionStrategy(dislocation=-0.90)
    assert not _signals(unreachable, frames)["FALL"][ENTER].any()


def test_oversold_reversion_exits_on_the_bar_count_not_on_a_signal():
    result = _run(OversoldReversionStrategy, {"FALL": _dislocated()}, params={"hold_bars": 4})
    assert not result.trades.empty
    closed = result.trades[result.trades["exit_reason"] == "custom_exit"]
    assert not closed.empty
    assert set(closed["exit_tag"]) == {"hold_expired"}
    assert (closed["bars_held"] == 4).all()


def test_the_time_exit_still_fires_when_the_exit_signal_is_switched_off():
    """freqtrade gates custom_exit behind use_exit_signal, which silently turned a 3-day hold into
    a 6-month one. The engine here checks it separately, so the hold is honoured either way."""
    frames = {"FALL": _dislocated()}
    on = _run(OversoldReversionStrategy, frames, params={"hold_bars": 3}, use_exit_signal=True)
    off = _run(OversoldReversionStrategy, frames, params={"hold_bars": 3}, use_exit_signal=False)
    for result in (on, off):
        assert (result.trades["bars_held"] <= 3).all()
    assert len(off.trades) == len(on.trades)


def test_oversold_reversion_ships_without_a_stop_in_the_way():
    assert OversoldReversionStrategy.stoploss < -0.9
    assert OversoldReversionStrategy.use_exit_signal is False


def test_capitulation_bounce_waits_for_the_green_bar_after_the_red_run():
    closes = [100.0, 97.0, 94.0, 91.0, 95.0, 96.0]
    opens = [101.0, 100.0, 97.0, 94.0, 91.0, 95.0]
    frames = {"DIP": _ohlcv([100.0] * 20 + closes, opens=[101.0] * 20 + opens)}
    signals = _signals(CapitulationBounceStrategy(red_bars=3, drop=0.05), frames)["DIP"]
    fired = np.flatnonzero(signals[ENTER].to_numpy())
    assert fired.tolist() == [24]

    impatient = _signals(CapitulationBounceStrategy(red_bars=3, drop=0.05, require_green=False), frames)["DIP"]
    assert impatient[ENTER].to_numpy()[23]


def test_capitulation_bounce_ignores_a_shallow_run():
    closes = [100.0, 99.8, 99.6, 99.4, 100.2]
    opens = [100.2, 100.0, 99.8, 99.6, 99.4]
    frames = {"DIP": _ohlcv([100.0] * 20 + closes, opens=[100.2] * 20 + opens)}
    assert not _signals(CapitulationBounceStrategy(red_bars=3, drop=0.05), frames)["DIP"][ENTER].any()


# ------------------------------------------------------------------- momentum


def _leader_and_laggard(n: int = 260) -> dict[str, pd.DataFrame]:
    return {
        "LEAD": _ohlcv(_slide(100.0, 220.0, n)),
        "LAG": _ohlcv(_slide(100.0, 108.0, n)),
        "SINK": _ohlcv(_slide(100.0, 70.0, n)),
    }


def test_momentum_regime_ranks_the_leader_above_the_laggard():
    frames = _leader_and_laggard()
    benchmark = _ohlcv(_slide(100.0, 160.0, 260))
    scores = _signals(MomentumRegimeStrategy(lookback=90), frames, benchmark=benchmark)
    assert scores["LEAD"][SCORE].iloc[-1] > scores["LAG"][SCORE].iloc[-1]
    assert np.isnan(scores["SINK"][SCORE].iloc[-1]), "a name below its own average must not be rankable"


def test_the_regime_gate_empties_the_book_when_the_benchmark_rolls_over():
    frames = _leader_and_laggard()
    falling = _ohlcv(_slide(160.0, 90.0, 260))
    scores = _signals(MomentumRegimeStrategy(lookback=90), frames, benchmark=falling)
    assert scores["LEAD"][SCORE].iloc[-1] != scores["LEAD"][SCORE].iloc[-1]
    assert scores["LEAD"][EXIT].iloc[-1]


def test_the_regime_gate_fails_open_without_a_benchmark():
    frames = _leader_and_laggard()
    scores = _signals(MomentumRegimeStrategy(lookback=90), frames, benchmark=None)
    assert not np.isnan(scores["LEAD"][SCORE].iloc[-1])


def test_the_gates_can_each_be_switched_off():
    frames = _leader_and_laggard()
    falling = _ohlcv(_slide(160.0, 90.0, 260))
    ungated = MomentumRegimeStrategy(lookback=90, use_regime_gate=False, use_trend_filter=False)
    scores = _signals(ungated, frames, benchmark=falling)
    assert not np.isnan(scores["SINK"][SCORE].iloc[-1])


def test_the_exit_band_is_well_clear_of_the_position_count():
    assert MomentumRegimeStrategy.exit_rank >= 2 * MomentumRegimeStrategy.top_k


# -------------------------------------------------------------------- trend


def test_donchian_breakout_enters_on_a_new_high_and_exits_at_the_channel_low():
    closes = [100.0] * 40 + _slide(100.0, 150.0, 40) + _slide(150.0, 95.0, 40)
    frames = {"RUN": _ohlcv(closes)}
    signals = _signals(DonchianBreakoutStrategy(entry_period=20, exit_period=10, adx_floor=0.0), frames)["RUN"]
    first_entry = np.flatnonzero(signals[ENTER].to_numpy())
    assert first_entry.size and 40 <= first_entry[0] <= 45
    assert signals[EXIT].to_numpy()[85:].any()


def test_a_high_adx_floor_blocks_the_breakout():
    closes = [100.0] * 40 + _slide(100.0, 150.0, 40)
    frames = {"RUN": _ohlcv(closes)}
    blocked = _signals(DonchianBreakoutStrategy(entry_period=20, adx_floor=100.0), frames)["RUN"]
    assert not blocked[ENTER].any()


def test_pullback_trend_buys_the_dip_inside_the_uptrend_not_the_crossover():
    trend = np.linspace(100.0, 180.0, 240) + np.sin(np.arange(240) / 6.0) * 8.0
    frames = {"UP": _ohlcv(list(trend))}
    strategy = PullbackTrendStrategy(fast_window=10, slow_window=40)
    signals = _signals(strategy, frames)["UP"]
    entered = signals[ENTER]
    assert entered.any()
    assert (signals.loc[entered, "Close"] < signals.loc[entered, "ma_fast"]).all()
    assert (signals.loc[entered, "ma_fast"] > signals.loc[entered, "ma_slow"]).all()


def test_pullback_trend_exits_when_the_averages_cross_back():
    closes = list(np.linspace(100.0, 160.0, 120)) + list(np.linspace(160.0, 90.0, 120))
    frames = {"UP": _ohlcv(closes)}
    signals = _signals(PullbackTrendStrategy(fast_window=10, slow_window=40), frames)["UP"]
    assert signals[EXIT].to_numpy()[-20:].all()


def test_dip_in_uptrend_requires_the_trend_the_calm_and_the_turn():
    frames = {"UP": _walk(900, drift=0.0007, vol=0.016, seed=4)}
    strategy = DipInUptrendStrategy(trend_window=100, band_period=20)
    signals = _signals(strategy, frames)["UP"]
    entered = signals[ENTER]
    assert entered.any()
    assert (signals.loc[entered, "Close"] > signals.loc[entered, "long_average"]).all()
    assert (signals.loc[entered, "roc"] < 0).all()


def test_dip_in_uptrend_never_buys_below_its_long_average():
    frames = {"DOWN": _walk(500, drift=-0.002, vol=0.016, seed=6, start=200.0)}
    assert not _signals(DipInUptrendStrategy(trend_window=100), frames)["DOWN"][ENTER].any()


# --------------------------------------------------------------- end to end


@pytest.mark.parametrize(
    ("strategy_cls", "frames"),
    [
        (OversoldReversionStrategy, {"FALL": _dislocated()}),
        (
            DonchianBreakoutStrategy,
            {"RUN": _ohlcv(_slide(100.0, 92.0, 80) + _slide(92.0, 165.0, 80) + _slide(165.0, 115.0, 60))},
        ),
        (PullbackTrendStrategy, {"UP": _ohlcv(list(np.linspace(100, 180, 240) + np.sin(np.arange(240) / 6.0) * 8))}),
    ],
)
def test_each_signal_strategy_runs_through_the_engine_and_books_trades(strategy_cls, frames):
    result = _run(strategy_cls, frames)
    assert not result.trades.empty
    assert result.equity.iloc[-1] > 0
    assert (result.trades["open_date"] < result.trades["close_date"]).all()


def test_the_momentum_basket_runs_through_the_engine_in_rank_mode():
    frames = _leader_and_laggard(300)
    benchmark = _ohlcv(_slide(100.0, 170.0, 300))
    result = _run(MomentumRegimeStrategy, frames, params={"lookback": 60}, benchmark=benchmark, top_k=2, exit_rank=3)
    assert not result.trades.empty
    assert "LEAD" in set(result.trades["symbol"])
    assert "SINK" not in set(result.trades["symbol"])


def test_every_ported_strategy_declares_tunable_parameters():
    for cls in (
        OversoldReversionStrategy,
        CapitulationBounceStrategy,
        MomentumRegimeStrategy,
        DonchianBreakoutStrategy,
        DipInUptrendStrategy,
        PullbackTrendStrategy,
    ):
        space = cls.parameter_space()
        assert space, f"{cls.name} declares no parameters"
        assert any(param.optimize for param in space.values()), f"{cls.name} has nothing to optimise"
        assert all(param.help for param in space.values()), f"{cls.name} has an undocumented parameter"
        assert cls().startup_candle_count > 0
