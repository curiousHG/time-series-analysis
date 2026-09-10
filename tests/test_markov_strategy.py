"""The transition-matrix strategies end to end: gating, confidence modes, and no lookahead."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERO
from services.backtest.engine import run_engine
from services.backtest.universe import BasketInputs
from strategies.basket import ENTER, EXIT, SCORE, MarketContext
from strategies.markov_strategy import (
    CONFIDENCE,
    EDGE,
    SAMPLES,
    TransitionEdgeRankStrategy,
    TransitionMatrixStrategy,
)


def _frames(n_symbols: int = 6, n_bars: int = 900, seed: int = 0) -> dict[str, pd.DataFrame]:
    """A basket where a fall past -1% is reliably followed by a strong up bar."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2018-01-01", periods=n_bars)
    out = {}
    for s in range(n_symbols):
        moves, previous = [], 0.0
        for _ in range(n_bars):
            previous = (0.02 if previous < -0.01 else 0.0) + rng.normal(0, 0.012)
            moves.append(previous)
        close = pd.Series(100 * np.exp(np.cumsum(moves)), index=index)
        open_ = close.shift(1).fillna(close.iloc[0])
        out[f"SYM{s}"] = pd.DataFrame(
            {
                "Open": open_,
                "High": np.maximum(open_, close) * 1.004,
                "Low": np.minimum(open_, close) * 0.996,
                "Close": close,
                "Volume": 500_000.0,
            },
            index=index,
        )
    return out


def _noise_frames(n_symbols: int = 5, n_bars: int = 700, seed: int = 9) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2018-01-01", periods=n_bars)
    out = {}
    for s in range(n_symbols):
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.014, n_bars))), index=index)
        open_ = close.shift(1).fillna(close.iloc[0])
        out[f"N{s}"] = pd.DataFrame(
            {
                "Open": open_,
                "High": np.maximum(open_, close) * 1.004,
                "Low": np.minimum(open_, close) * 0.996,
                "Close": close,
                "Volume": 500_000.0,
            },
            index=index,
        )
    return out


def _populate(strategy, frames):
    index = next(iter(frames.values())).index
    ctx = MarketContext(calendar=index, universe=tuple(frames), benchmark=None)
    prepared = strategy.prepare_basket({s: strategy.populate_indicators(f.copy(), s, ctx) for s, f in frames.items()})
    out = {}
    for symbol, frame in prepared.items():
        frame = strategy.populate_entry(frame, symbol, ctx)
        frame = strategy.populate_exit(frame, symbol, ctx)
        out[symbol] = strategy.populate_score(frame, symbol, ctx)
    return out


def _run(strategy_cls, frames, params=None, **overrides):
    index = next(iter(frames.values())).index
    params = params or {}
    inputs = BasketInputs(
        calendar=index,
        frames={s: f.copy() for s, f in frames.items()},
        start_pos=min(strategy_cls(**params).startup_candle_count, len(index) - 50),
        instrument_kinds=dict.fromkeys(frames, "equity"),
    )
    config = BacktestConfig(
        strategy=strategy_cls.name,
        params=params,
        universe=UniverseSpec("list", symbols=tuple(frames)),
        start=index[0].date(),
        end=index[-1].date(),
        init_cash=1_000_000.0,
        costs=ZERO,
        slippage_bps=0.0,
        strategy_overrides=overrides,
    )
    return run_engine(inputs, strategy_cls, params, config)


def test_the_strategy_writes_a_forecast_column_for_every_symbol():
    frames = _frames()
    populated = _populate(TransitionMatrixStrategy(min_samples=50), frames)
    for frame in populated.values():
        assert {CONFIDENCE, EDGE, SAMPLES}.issubset(frame.columns)
        assert frame[CONFIDENCE].notna().any()
        assert frame[SAMPLES].max() > 0


def test_entries_land_on_the_buckets_the_matrix_says_are_favourable():
    frames = _frames()
    strategy = TransitionMatrixStrategy(min_samples=50, entry_confidence=0.60)
    populated = _populate(strategy, frames)
    frame = populated["SYM0"]
    entered = frame[ENTER]
    assert entered.any()
    assert (frame.loc[entered, CONFIDENCE] >= 0.60).all()
    assert (frame.loc[entered, EDGE] > 0).all()
    assert (frame.loc[entered, SAMPLES] >= 50).all()


def test_a_confidence_bar_nobody_can_clear_produces_no_entries():
    frames = _frames()
    strategy = TransitionMatrixStrategy(min_samples=50, entry_confidence=0.99)
    assert not any(frame[ENTER].any() for frame in _populate(strategy, frames).values())


def test_the_sample_floor_blocks_a_thinly_observed_bucket():
    frames = _frames(n_symbols=2, n_bars=300)
    generous = _populate(TransitionMatrixStrategy(min_samples=10, entry_confidence=0.55), frames)
    strict = _populate(TransitionMatrixStrategy(min_samples=5000, entry_confidence=0.55), frames)
    assert any(frame[ENTER].any() for frame in generous.values())
    assert not any(frame[ENTER].any() for frame in strict.values())


def test_lift_mode_measures_confidence_against_the_days_base_rate():
    frames = _frames()
    absolute = _populate(TransitionMatrixStrategy(min_samples=50), frames)["SYM0"]
    lift = _populate(TransitionMatrixStrategy(min_samples=50, confidence_mode="lift"), frames)["SYM0"]
    difference = (absolute[CONFIDENCE] - lift[CONFIDENCE]).dropna()
    assert (difference > 0).all()
    assert difference.std() < 0.2, "the base rate moves slowly, so the gap should be nearly constant"


def test_the_significance_guard_removes_entries_backed_by_few_observations():
    frames = _frames(n_symbols=3, n_bars=400)
    loose = _populate(TransitionMatrixStrategy(min_samples=10, entry_confidence=0.55), frames)
    tight = _populate(TransitionMatrixStrategy(min_samples=10, entry_confidence=0.55, min_lift_z=4.0), frames)
    assert sum(int(f[ENTER].sum()) for f in tight.values()) < sum(int(f[ENTER].sum()) for f in loose.values())


def test_a_forecast_on_one_bar_does_not_change_when_later_bars_are_deleted():
    """The matrix read on a bar must be identical whether or not the future exists in the frame."""
    frames = _frames(n_symbols=4, n_bars=600, seed=3)
    cut = 400
    truncated = {s: f.iloc[:cut] for s, f in frames.items()}

    full = _populate(TransitionMatrixStrategy(min_samples=20), frames)
    short = _populate(TransitionMatrixStrategy(min_samples=20), truncated)

    for symbol in frames:
        a = full[symbol][CONFIDENCE].iloc[:cut]
        b = short[symbol][CONFIDENCE]
        assert np.allclose(a.fillna(-1).to_numpy(), b.fillna(-1).to_numpy())
        assert full[symbol][ENTER].iloc[:cut].tolist() == short[symbol][ENTER].tolist()


def test_pooling_gathers_more_observations_than_one_symbol_alone():
    frames = _frames(n_symbols=6, n_bars=500)
    pooled = _populate(TransitionMatrixStrategy(min_samples=10), frames)["SYM0"]
    alone = _populate(TransitionMatrixStrategy(min_samples=10, pooled=False), frames)["SYM0"]
    assert pooled[SAMPLES].max() > 4 * alone[SAMPLES].max()
    assert alone[CONFIDENCE].notna().any()


def test_the_run_books_trades_and_never_holds_past_the_cap():
    result = _run(TransitionMatrixStrategy, _frames(), {"min_samples": 50, "max_hold": 3})
    assert not result.trades.empty
    assert (result.trades["bars_held"] <= 3).all()
    assert result.equity.iloc[-1] > 0


def test_the_hold_cap_is_what_closes_the_book_once_the_exit_signal_is_off():
    """With the signal exit disabled the only thing left is the bars-held hook, and it must fire."""
    result = _run(TransitionMatrixStrategy, _frames(), {"min_samples": 50, "max_hold": 3}, use_exit_signal=False)
    assert not result.trades.empty
    reasons = set(result.trades["exit_reason"])
    assert "custom_exit" in reasons
    assert "exit_signal" not in reasons
    assert (result.trades["bars_held"] <= 3).all()


def test_the_planted_edge_shows_up_as_a_profitable_run_before_costs():
    result = _run(TransitionMatrixStrategy, _frames(seed=1), {"min_samples": 50, "entry_confidence": 0.60})
    assert result.metrics["total_trades"] > 20
    assert result.equity.iloc[-1] > result.equity.iloc[0]
    assert result.metrics["win_rate"] > 50


def test_a_basket_with_no_structure_barely_trades_at_a_demanding_bar():
    populated = _populate(TransitionMatrixStrategy(min_samples=100, entry_confidence=0.70), _noise_frames())
    assert sum(int(frame[ENTER].sum()) for frame in populated.values()) == 0


def test_the_rank_variant_scores_only_the_confident_names():
    frames = _frames()
    populated = _populate(TransitionEdgeRankStrategy(min_samples=50, entry_confidence=0.60), frames)
    frame = populated["SYM0"]
    ranked = frame[SCORE].notna()
    assert ranked.any()
    assert (frame.loc[ranked, CONFIDENCE] >= 0.60).all()
    assert (frame.loc[ranked, SCORE] > 0).all()


def test_the_rank_variant_runs_through_the_engine():
    result = _run(TransitionEdgeRankStrategy, _frames(), {"min_samples": 50}, top_k=3, exit_rank=5)
    assert not result.trades.empty
    assert result.metrics["total_trades"] > 0


def test_diagnostics_carry_the_matrix_the_state_table_and_a_hit_rate():
    strategy = TransitionMatrixStrategy(min_samples=50)
    _populate(strategy, _frames())
    diagnostics = strategy.diagnostics()

    assert diagnostics is not None
    assert diagnostics.matrix.shape == (11, 11)
    assert np.allclose(diagnostics.matrix.sum(axis=1), 1.0)
    assert list(diagnostics.states.columns) == [
        "bucket",
        "observations",
        "p_up",
        "p_down",
        "p_up_given_move",
        "lift_vs_base",
        "mean_next_return",
    ]
    assert diagnostics.summary["kind"] == "transition_matrix"
    assert 0 < diagnostics.summary["next_bar_hit_rate"] <= 1
    assert diagnostics.summary["transitions"] > 1000
    assert diagnostics.coverage.between(0, 1).all()


def test_the_diagnostics_hit_rate_beats_a_coin_flip_on_a_planted_edge():
    strategy = TransitionMatrixStrategy(min_samples=50, entry_confidence=0.60)
    _populate(strategy, _frames(seed=2))
    assert strategy.diagnostics().summary["next_bar_hit_rate"] > 0.6
    assert strategy.diagnostics().summary["next_bar_mean_return"] > 0


def test_the_exit_fires_when_confidence_falls_away():
    frames = _frames()
    frame = _populate(TransitionMatrixStrategy(min_samples=50, exit_confidence=0.52), frames)["SYM0"]
    assert frame[EXIT].any()
    assert (frame.loc[frame[EXIT] & frame[CONFIDENCE].notna(), CONFIDENCE] <= 0.52).all()


def test_bucket_settings_reach_the_scheme_and_change_the_matrix_shape():
    strategy = TransitionMatrixStrategy(bucket_width=1.5, n_side=3, min_samples=20)
    _populate(strategy, _frames(n_symbols=3, n_bars=400))
    assert strategy.scheme.n_states == 7
    assert strategy.diagnostics().matrix.shape == (7, 7)
    assert strategy.diagnostics().summary["bucket_width"] == pytest.approx(1.5)


def test_an_empty_basket_is_handled_without_raising():
    assert TransitionMatrixStrategy().prepare_basket({}) == {}


def test_diagnostics_grade_the_forecast_against_what_a_trade_can_actually_keep():
    """The matrix predicts a close-to-close move while the engine fills at the open, so the
    diagnostics report both, and the gap between them is what the strategy really has to spend."""
    strategy = TransitionMatrixStrategy(min_samples=50, entry_confidence=0.55)
    _populate(strategy, _frames(seed=2))
    summary = strategy.diagnostics().summary

    for key in ("baseline_mean_return", "overnight_gap_mean", "realisable_mean_return", "realisable_hit_rate"):
        assert key in summary
        assert summary[key] == summary[key], f"{key} should be a number"
    assert 0 <= summary["realisable_hit_rate"] <= 1
    assert summary["next_bar_mean_return"] > summary["baseline_mean_return"], "a called bar should beat the average"


def test_the_realisable_edge_is_positive_on_a_planted_basket_and_flat_on_noise():
    planted = TransitionMatrixStrategy(min_samples=50, entry_confidence=0.55)
    _populate(planted, _frames(seed=5))
    assert planted.diagnostics().summary["realisable_edge"] > 0

    noise = TransitionMatrixStrategy(min_samples=20, entry_confidence=0.51)
    _populate(noise, _noise_frames(n_symbols=8, n_bars=900, seed=12))
    assert abs(noise.diagnostics().summary["realisable_edge"]) < 0.004
