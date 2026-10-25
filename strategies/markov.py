"""Bucketed daily returns and the causal rolling transition matrix estimated from them.

The count matrix in force on bar t sums only transitions completed on or before t.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_SIDE = 10
MIN_WIDTH = 0.01
FLAT = "flat"


@dataclass(frozen=True)
class BucketScheme:
    """How a percentage move becomes a bucket index."""

    width: float
    n_side: int
    scale: str = "percent"
    vol_window: int = 63

    def __post_init__(self) -> None:
        if self.width < MIN_WIDTH:
            raise ValueError(f"bucket width must be at least {MIN_WIDTH}, got {self.width}")
        if not 1 <= self.n_side <= MAX_SIDE:
            raise ValueError(f"n_side must be between 1 and {MAX_SIDE}, got {self.n_side}")
        if self.scale not in ("percent", "sigma"):
            raise ValueError(f"scale must be 'percent' or 'sigma', not {self.scale!r}")

    @property
    def n_states(self) -> int:
        return 2 * self.n_side + 1

    @property
    def centres(self) -> np.ndarray:
        """The nominal move at the middle of each bucket, as a fraction."""
        return (np.arange(self.n_states) - self.n_side) * self.width / 100.0

    @property
    def positive(self) -> np.ndarray:
        return np.arange(self.n_states) > self.n_side

    @property
    def negative(self) -> np.ndarray:
        return np.arange(self.n_states) < self.n_side

    def labels(self) -> list[str]:
        unit = "%" if self.scale == "percent" else "s"
        half = self.width / 2
        out = []
        for k in range(-self.n_side, self.n_side + 1):
            if k == 0:
                out.append(f"{FLAT} ±{half:g}{unit}")
            elif k == -self.n_side:
                out.append(f"≤ {(k * self.width + half):+g}{unit}")
            elif k == self.n_side:
                out.append(f"≥ {(k * self.width - half):+g}{unit}")
            else:
                out.append(f"{(k * self.width - half):+g}..{(k * self.width + half):+g}{unit}")
        return out


def scaled_returns(returns: pd.DataFrame, scheme: BucketScheme) -> pd.DataFrame:
    """Returns in the units the scheme buckets: percent, or multiples of trailing volatility."""
    if scheme.scale == "percent":
        return returns * 100.0
    sigma = returns.rolling(scheme.vol_window, min_periods=max(10, scheme.vol_window // 3)).std()
    return returns.divide(sigma.replace(0.0, np.nan))


def bucket_states(scaled: np.ndarray, scheme: BucketScheme) -> np.ndarray:
    """Bucket index per observation, or -1 where the move is unknown. Bucket k is centred on k * width."""
    out = np.full(scaled.shape, -1, dtype=np.int16)
    finite = np.isfinite(scaled)
    if not finite.any():
        return out
    index = np.rint(np.where(finite, scaled, 0.0) / scheme.width)
    index = np.clip(index, -scheme.n_side, scheme.n_side) + scheme.n_side
    out[finite] = index[finite].astype(np.int16)
    return out


@dataclass
class StateStats:
    """Per bar and per current bucket, what the matrix in force on that bar predicts.

    `directional` is P(up) renormalised against P(down) and is what the strategies gate on.
    """

    p_up: np.ndarray
    p_down: np.ndarray
    expected: np.ndarray
    samples: np.ndarray
    directional_samples: np.ndarray
    base_up: np.ndarray
    base_directional: np.ndarray
    scheme: BucketScheme

    @property
    def directional(self) -> np.ndarray:
        """P(up | the move leaves the flat band)."""
        with np.errstate(divide="ignore", invalid="ignore"):
            total = self.p_up + self.p_down
            return np.where(total > 0, self.p_up / np.maximum(total, 1e-12), np.nan)

    @property
    def edge_up(self) -> np.ndarray:
        """How much the current bucket lifts the directional odds above the same bar's base rate."""
        return self.directional - self.base_directional[:, None]

    def z_up(self) -> np.ndarray:
        """The lift over the base rate in standard errors of the row's own proportion."""
        with np.errstate(divide="ignore", invalid="ignore"):
            share = self.directional
            se = np.sqrt(np.clip(share * (1 - share), 1e-9, None) / np.maximum(self.directional_samples, 1))
            return np.where(self.directional_samples > 0, self.edge_up / se, 0.0)


def _accumulate(states: np.ndarray, moves: np.ndarray, n_states: int, n_bars: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar count and next-return sums for every (from, to) pair, indexed by the arrival bar."""
    counts = np.zeros((n_bars, n_states, n_states))
    sums = np.zeros((n_bars, n_states, n_states))
    if n_bars < 2:
        return counts, sums
    origin = states[:, :-1]
    arrival = states[:, 1:]
    landed = moves[:, 1:]
    valid = (origin >= 0) & (arrival >= 0) & np.isfinite(landed)
    if not valid.any():
        return counts, sums
    bars = np.broadcast_to(np.arange(1, n_bars), origin.shape)
    at, frm, to = bars[valid], origin[valid], arrival[valid]
    np.add.at(counts, (at, frm, to), 1.0)
    np.add.at(sums, (at, frm, to), landed[valid])
    return counts, sums


def _window_totals(cumulative: np.ndarray, window: int) -> np.ndarray:
    running = cumulative.cumsum(axis=0)
    if window <= 0:
        return running
    trailing = np.zeros_like(running)
    trailing[window:] = running[:-window]
    return running - trailing


def estimate(
    states: np.ndarray,
    moves: np.ndarray,
    scheme: BucketScheme,
    *,
    window: int = 0,
    alpha: float = 1.0,
) -> StateStats:
    """Roll a transition matrix across the calendar and read every row of every bar's matrix.

    `states` and `moves` are (symbols x bars); `window` is a trailing bar count (0 = expanding);
    `alpha` is the Laplace prior.
    """
    n_states = scheme.n_states
    n_bars = states.shape[1]
    counts, sums = _accumulate(states, moves, n_states, n_bars)
    total_counts = _window_totals(counts, window)
    total_sums = _window_totals(sums, window)

    row_counts = total_counts.sum(axis=2)
    prob = (total_counts + alpha) / (row_counts[..., None] + alpha * n_states)
    with np.errstate(divide="ignore", invalid="ignore"):
        expected = np.where(row_counts > 0, total_sums.sum(axis=2) / np.maximum(row_counts, 1e-12), np.nan)
    column_counts = total_counts.sum(axis=1)
    grand = column_counts.sum(axis=1)
    up_counts = column_counts[:, scheme.positive].sum(axis=1)
    down_counts = column_counts[:, scheme.negative].sum(axis=1)
    moved = up_counts + down_counts
    with np.errstate(divide="ignore", invalid="ignore"):
        base_up = np.where(grand > 0, up_counts / np.maximum(grand, 1e-12), np.nan)
        base_directional = np.where(moved > 0, up_counts / np.maximum(moved, 1e-12), np.nan)
    return StateStats(
        p_up=prob[:, :, scheme.positive].sum(axis=2),
        p_down=prob[:, :, scheme.negative].sum(axis=2),
        expected=expected,
        samples=row_counts,
        directional_samples=total_counts[:, :, scheme.positive | scheme.negative].sum(axis=2),
        base_up=base_up,
        base_directional=base_directional,
        scheme=scheme,
    )


def read_rows(states: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Pick each symbol's own row out of a (bars x states) array; NaN where the state is unknown."""
    bars = np.arange(values.shape[0])[None, :]
    picked = values[bars, np.clip(states, 0, None)]
    return np.where(states >= 0, picked, np.nan)


@dataclass
class TransitionDiagnostics:
    """What the Backtest Lab shows for a transition-matrix run."""

    matrix: pd.DataFrame
    states: pd.DataFrame
    summary: dict
    coverage: pd.Series


def final_matrix(states: np.ndarray, moves: np.ndarray, scheme: BucketScheme, *, alpha: float = 1.0) -> pd.DataFrame:
    """The whole-sample probability matrix, labelled, for the diagnostics heatmap."""
    counts, _ = _accumulate(states, moves, scheme.n_states, states.shape[1])
    totals = counts.sum(axis=0)
    prob = (totals + alpha) / (totals.sum(axis=1, keepdims=True) + alpha * scheme.n_states)
    labels = scheme.labels()
    return pd.DataFrame(prob, index=pd.Index(labels, name="from"), columns=pd.Index(labels, name="to"))


def state_table(states: np.ndarray, moves: np.ndarray, scheme: BucketScheme, *, alpha: float = 1.0) -> pd.DataFrame:
    """One row per bucket: how often it occurs and what tends to follow it, over the whole sample."""
    counts, sums = _accumulate(states, moves, scheme.n_states, states.shape[1])
    totals = counts.sum(axis=0)
    return_totals = sums.sum(axis=0)
    row_counts = totals.sum(axis=1)
    prob = (totals + alpha) / (row_counts[:, None] + alpha * scheme.n_states)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_next = np.where(row_counts > 0, return_totals.sum(axis=1) / np.maximum(row_counts, 1e-12), np.nan)
    column_counts = totals.sum(axis=0)
    up_total = column_counts[scheme.positive].sum()
    moved_total = up_total + column_counts[scheme.negative].sum()
    base = up_total / max(moved_total, 1e-12)
    p_up = prob[:, scheme.positive].sum(axis=1)
    p_down = prob[:, scheme.negative].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        directional = np.where(p_up + p_down > 0, p_up / np.maximum(p_up + p_down, 1e-12), np.nan)
    return pd.DataFrame(
        {
            "bucket": scheme.labels(),
            "observations": row_counts.astype(int),
            "p_up": p_up,
            "p_down": p_down,
            "p_up_given_move": directional,
            "lift_vs_base": directional - base,
            "mean_next_return": mean_next,
        }
    )
