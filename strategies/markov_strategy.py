"""Strategies that trade the bucketed-return transition matrix.

Both read the same forecast. Each symbol's latest daily move is placed in a bucket; the row of the
rolling transition matrix for that bucket gives P(next move lands in a positive bucket) and the
conditional expected next return. `TransitionMatrixStrategy` buys the names whose row clears a
confidence bar and holds them for a few bars; `TransitionEdgeRankStrategy` ranks the whole basket by
that expected return and holds the top slice.

Confidence is P(up given the move leaves the flat band). Renormalising against the down side rather
than reading the raw share of positive buckets matters: with an eleven-bucket scheme the flat band
holds about a fifth of every row, so a raw P(up) tops out near 0.48 and a 0.55 threshold could never
be met. On the directional reading 0.5 is genuinely neutral.

That confidence can be read two ways. In `absolute` mode the bar is the probability itself. In
`lift` mode it is the probability minus the same day's base rate across the basket, which is the
honest question: does knowing today's bucket tell you anything beyond the market's own drift?
`min_lift_z` adds a sample-size guard, so a row with three observations cannot pass on a lucky
proportion.

The forecast is for the close-to-close move of the next bar, while the engine fills at that bar's
open. The overnight gap between the two is not captured, which is the usual cost of trading a daily
signal and is why these run with a short hold rather than a same-bar exit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from strategies.basket import ENTER, ENTER_TAG, EXIT, SCORE, BasketStrategy, register_basket_strategy
from strategies.markov import (
    BucketScheme,
    TransitionDiagnostics,
    bucket_states,
    estimate,
    final_matrix,
    read_rows,
    scaled_returns,
    state_table,
)
from strategies.parameters import BoolParameter, CategoricalParameter, DecimalParameter, IntParameter

if TYPE_CHECKING:
    from collections.abc import Callable

    from strategies.basket import MarketContext, TradeSnapshot

logger = logging.getLogger(__name__)

CONFIDENCE = "tm_confidence"
EDGE = "tm_edge"
SAMPLES = "tm_samples"
STATE = "tm_state"
LIFT_Z = "tm_lift_z"
POOLED_STARTUP = 250
PER_SYMBOL_STARTUP = 750


class TransitionMatrixBase(BasketStrategy):
    """Shared estimation for the two transition-matrix strategies."""

    bucket_width = DecimalParameter(0.75, 0.10, 3.00, step=0.05, help="Bucket size, in percent or in sigma")
    n_side = IntParameter(5, 2, 10, help="Buckets either side of flat; the outermost absorbs the tails")
    scale = CategoricalParameter(
        "percent", choices=("percent", "sigma"), optimize=False, help="Bucket raw percent moves or volatility units"
    )
    window = IntParameter(750, 0, 2500, step=50, help="Trailing bars the matrix is counted over; 0 is expanding")
    alpha = DecimalParameter(1.0, 0.05, 5.0, step=0.05, optimize=False, help="Laplace prior on each transition")
    min_samples = IntParameter(200, 20, 2000, step=20, help="Observations a bucket needs before it may be traded")
    pooled = BoolParameter(True, optimize=False, help="One matrix for the whole basket rather than one per symbol")
    confidence_mode = CategoricalParameter(
        "absolute", choices=("absolute", "lift"), optimize=False, help="Read P(up) raw, or net of the day's base rate"
    )
    min_lift_z = DecimalParameter(0.0, 0.0, 4.0, step=0.25, help="Standard errors the lift over base rate must clear")

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self._diagnostics: TransitionDiagnostics | None = None

    @property
    def startup_candle_count(self) -> int:
        floor = POOLED_STARTUP if self.pooled else PER_SYMBOL_STARTUP
        return max(floor, int(self.window) // 3)

    @property
    def scheme(self) -> BucketScheme:
        return BucketScheme(width=float(self.bucket_width), n_side=int(self.n_side), scale=str(self.scale))

    def diagnostics(self):
        return self._diagnostics

    def prepare_basket(
        self, frames: dict[str, pd.DataFrame], progress_cb: Callable[..., None] | None = None
    ) -> dict[str, pd.DataFrame]:
        if not frames:
            return frames
        symbols = list(frames)
        closes = pd.DataFrame({sym: frames[sym]["Close"] for sym in symbols})
        returns = closes.pct_change(fill_method=None)
        scheme = self.scheme
        if progress_cb is not None:
            progress_cb(phase="Transition matrix", done=1, total=3, message=f"bucketing {len(symbols)} symbols")

        moves = returns.to_numpy(dtype=float).T
        states = bucket_states(scaled_returns(returns, scheme).to_numpy(dtype=float).T, scheme)
        stats = estimate(states, moves, scheme, window=int(self.window), alpha=float(self.alpha))

        if progress_cb is not None:
            progress_cb(phase="Transition matrix", done=2, total=3, message="rolling the matrix")
        confidence, edge, samples, lift_z = self._read(states, moves, stats)

        for i, symbol in enumerate(symbols):
            df = frames[symbol]
            df[CONFIDENCE] = confidence[i]
            df[EDGE] = edge[i]
            df[SAMPLES] = samples[i]
            df[LIFT_Z] = lift_z[i]
            df[STATE] = np.where(states[i] >= 0, states[i], np.nan)

        opens = pd.DataFrame({sym: frames[sym]["Open"] for sym in symbols})
        self._diagnostics = self._build_diagnostics(states, moves, scheme, confidence, samples, closes, opens)
        if progress_cb is not None:
            progress_cb(phase="Transition matrix", done=3, total=3)
        return frames

    def _read(self, states, moves, stats):
        """Per symbol and bar: the confidence to gate on, the expected return, and the guards."""
        if self.pooled:
            p_up = read_rows(states, stats.directional)
            expected = read_rows(states, stats.expected)
            samples = read_rows(states, stats.samples)
            lift_z = read_rows(states, stats.z_up())
            base = np.broadcast_to(stats.base_directional[None, :], p_up.shape)
        else:
            p_up, expected, samples, lift_z, base = self._per_symbol(states, moves)
        confidence = p_up if self.confidence_mode == "absolute" else p_up - base
        return confidence, expected, np.nan_to_num(samples), np.nan_to_num(lift_z)

    def _per_symbol(self, states, moves):
        """One matrix per symbol, estimated and read one symbol at a time to bound memory."""
        scheme = self.scheme
        out = [np.full(states.shape, np.nan) for _ in range(5)]
        for i in range(states.shape[0]):
            row_states = states[i : i + 1]
            own = estimate(row_states, moves[i : i + 1], scheme, window=int(self.window), alpha=float(self.alpha))
            for target, values in zip(out[:4], (own.directional, own.expected, own.samples, own.z_up()), strict=True):
                target[i] = read_rows(row_states, values)[0]
            out[4][i] = own.base_directional
        return tuple(out)

    def _build_diagnostics(self, states, moves, scheme, confidence, samples, closes, opens) -> TransitionDiagnostics:
        """Grade the forecast twice: against what it predicts, and against what a trade can keep.

        The matrix forecasts the next bar's close-to-close move, but the engine buys at that bar's
        open. Whatever the market has already repriced overnight is gone before the fill, so the
        two numbers can differ a lot, and only the second one has to clear the cost of trading.
        """
        usable = np.isfinite(confidence) & (samples >= int(self.min_samples))
        called = usable & (confidence >= self._entry_bar())
        predicted = _shift_back(moves)
        open_values = opens.to_numpy(dtype=float).T
        realisable = _shift_back(open_values, lag=2) / _shift_back(open_values, lag=1) - 1
        gap = _shift_back(open_values, lag=1) / closes.to_numpy(dtype=float).T - 1

        gradeable = np.isfinite(predicted) & np.isfinite(realisable)
        graded = called & gradeable
        return TransitionDiagnostics(
            matrix=final_matrix(states, moves, scheme, alpha=float(self.alpha)),
            states=state_table(states, moves, scheme, alpha=float(self.alpha)),
            summary={
                "kind": "transition_matrix",
                "buckets": scheme.n_states,
                "bucket_width": scheme.width,
                "scale": scheme.scale,
                "window": int(self.window),
                "pooled": bool(self.pooled),
                "transitions": int(np.isfinite(moves[:, 1:]).sum()),
                "bars_called": int(called.sum()),
                "call_rate": float(called.sum() / max(usable.sum(), 1)),
                "next_bar_hit_rate": _mean(graded, predicted > 0),
                "next_bar_mean_return": _mean(graded, predicted),
                "baseline_mean_return": _mean(gradeable, predicted),
                "overnight_gap_mean": _mean(graded, gap),
                "realisable_hit_rate": _mean(graded, realisable > 0),
                "realisable_mean_return": _mean(graded, realisable),
                "realisable_edge": _mean(graded, realisable) - _mean(gradeable, realisable),
            },
            coverage=pd.Series(usable.mean(axis=0), index=closes.index, name="coverage"),
        )

    def _entry_bar(self) -> float:
        raise NotImplementedError


def _shift_back(values: np.ndarray, lag: int = 1) -> np.ndarray:
    """`values` moved `lag` bars earlier, so row t carries what happens at t + lag."""
    out = np.full_like(values, np.nan, dtype=float)
    if lag < values.shape[1]:
        out[:, :-lag] = values[:, lag:]
    return out


def _mean(mask: np.ndarray, values: np.ndarray) -> float:
    return float(np.asarray(values, dtype=float)[mask].mean()) if mask.any() else float("nan")


@register_basket_strategy
class TransitionMatrixStrategy(TransitionMatrixBase):
    """Buy the buckets whose forecast row clears the confidence bar; hold for a few bars."""

    name = "Transition Matrix"
    mode = "signal"

    entry_confidence = DecimalParameter(0.55, 0.30, 0.90, step=0.01, help="Confidence a symbol needs to be bought")
    exit_confidence = DecimalParameter(0.48, 0.20, 0.80, step=0.01, help="Confidence below which a holding is sold")
    max_hold = IntParameter(5, 1, 40, help="Bars a position may live before it is closed regardless of signal")

    max_open_trades = 10
    stoploss = -0.08
    use_exit_signal = True

    def _entry_bar(self) -> float:
        return float(self.entry_confidence)

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        enter = (
            (df[CONFIDENCE] >= float(self.entry_confidence))
            & (df[EDGE] > 0)
            & (df[SAMPLES] >= int(self.min_samples))
            & (df[LIFT_Z] >= float(self.min_lift_z))
        )
        df[ENTER] = enter.fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "bucket_" + df[STATE].fillna(-1).astype(int).astype(str), None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        weak = df[CONFIDENCE] <= float(self.exit_confidence)
        df[EXIT] = (weak | df[CONFIDENCE].isna()).fillna(False).astype(bool)
        return df

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        df[SCORE] = df[EDGE]
        return df

    def custom_exit(self, trade: TradeSnapshot, ctx: MarketContext) -> str | None:
        """Close a position that has run out its forecast horizon.

        The engine checks this outside `use_exit_signal`, so turning the exit signal off shortens
        the book to this hold rather than letting positions run indefinitely. The check is a bar
        early because the exit fills at the next open.
        """
        return "hold_expired" if trade.bars_held + 1 >= int(self.max_hold) else None


@register_basket_strategy
class TransitionEdgeRankStrategy(TransitionMatrixBase):
    """Rank the basket by the matrix's conditional expected return and hold the top slice."""

    name = "Transition Edge Rank"
    mode = "rank"

    entry_confidence = DecimalParameter(0.50, 0.30, 0.90, step=0.01, help="Confidence a symbol needs to be rankable")

    top_k = 10
    exit_rank = 20
    rebalance_every = 1
    stoploss = -0.08
    use_exit_signal = False

    def _entry_bar(self) -> float:
        return float(self.entry_confidence)

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        eligible = (
            (df[CONFIDENCE] >= float(self.entry_confidence))
            & (df[SAMPLES] >= int(self.min_samples))
            & (df[LIFT_Z] >= float(self.min_lift_z))
        )
        df[SCORE] = df[EDGE].where(eligible & (df[EDGE] > 0))
        return df
