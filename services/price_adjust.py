"""Split / unit-subdivision adjustment for price and NAV series — pure, no I/O.

Stored NAV and OHLCV are kept as the source publishes them. AMFI NAVs are never adjusted for
ETF unit splits (a 1:10 sub-division prints as a −90% day), and yfinance's adjusted history
still carries unadjusted splits and bonus issues from before ~2010 and for events Yahoo never
recorded (MOTILALOFS / GPIL 2024-01-01). Left alone those days become −90% "returns" and
−99% drawdowns in every metric, so metric computation runs its series through
`adjust_splits` first.

A split is a single-day ratio close to a conventional factor (or its inverse) that *persists*:
the level after the day stays at the new scale. A one-day blip that reverts is not a split
and is left to the glitch filters.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

SPLIT_FACTORS = (2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0, 50.0, 100.0)
# Tolerance on |ratio/factor - 1|. Prices move on the split day too, so exact matches are rare,
# but 8% keeps a genuine ~−55% crash (ratio 0.44, Yes Bank 2020) outside both the 2× and 2.5× bands. A
# demerger that lands on a clean factor (Vedanta 2026, ratio 0.35 ≈ 1/3) IS adjusted: for a
# price-only series that is closer to what a holder experienced than a −65% return.
SPLIT_TOL = 0.08
# Days after the break the new scale must hold for. Guards against blips and thin data. The
# persistence check compares medians on either side, so it is about scale, not precision: the
# stock can rally or slide 20% in the following week and still count as having re-based.
PERSIST_DAYS = 5
PERSIST_TOL = 0.25


def _nearest_factor(ratio: float, tol: float = SPLIT_TOL) -> float | None:
    """The conventional split factor `ratio` is closest to (as up- or down-move), or None."""
    if not np.isfinite(ratio) or ratio <= 0:
        return None
    up = ratio >= 1
    r = ratio if up else 1.0 / ratio
    best = min(SPLIT_FACTORS, key=lambda f: abs(math.log(r / f)))
    if abs(r / best - 1) > tol:
        return None
    return best if up else 1.0 / best


def detect_splits(series: pd.Series) -> list[tuple[pd.Timestamp, float]]:
    """(date, ratio) for each persistent conventional-factor jump in `series` (positive values,
    sorted DatetimeIndex). `ratio` > 1 means the level went up by that factor on that day."""
    s = series.dropna()
    s = s[s > 0]
    if len(s) < PERSIST_DAYS + 2:
        return []
    vals = s.to_numpy(dtype=float)
    idx = s.index
    out: list[tuple[pd.Timestamp, float]] = []
    for i in range(1, len(vals)):
        factor = _nearest_factor(vals[i] / vals[i - 1])
        if factor is None:
            continue
        before = np.median(vals[max(0, i - PERSIST_DAYS) : i])
        after_window = vals[i : i + PERSIST_DAYS]
        if len(after_window) < min(PERSIST_DAYS, 2):
            continue
        after = np.median(after_window)
        persisted = _nearest_factor(after / before, tol=PERSIST_TOL)
        if persisted is None or (persisted > 1) != (factor > 1):
            continue  # the level did not stay at the new scale — a blip, not a split
        out.append((idx[i], factor))
    return out


def split_factors(close: pd.Series) -> pd.Series:
    """Cumulative multiplier per date that puts every value on the post-split scale of the last
    detected split: the product of the ratios of all splits that happen *after* that date, so it
    is 1.0 from the last split onwards. Indexed like `close`."""
    factors = pd.Series(1.0, index=close.index, dtype=float)
    for day, ratio in detect_splits(close):
        factors.loc[factors.index < day] *= ratio
    return factors


def adjust_splits(series: pd.Series) -> pd.Series:
    """`series` rescaled so every value before a split is on the post-split scale.

    Returns the input unchanged when no split is detected. Only the values *before* each split
    day are touched, so the latest levels always match the source.
    """
    factors = split_factors(series)
    if (factors == 1.0).all():
        return series
    return series.astype(float) * factors


def adjust_ohlc_splits(df: pd.DataFrame) -> pd.DataFrame:
    """A Date-indexed OHLCV frame with the Close-derived split factor applied to Open/High/Low/
    Close and Volume divided by it, so pre-split bars are on the post-split scale. Returns a new
    frame; the input is untouched. Passes through when no split is detected."""
    factors = split_factors(df["Close"])
    if (factors == 1.0).all():
        return df
    out = df.copy()
    for column in ("Open", "High", "Low", "Close"):
        if column in out.columns:
            out[column] = out[column].astype(float) * factors
    if "Volume" in out.columns:
        out["Volume"] = out["Volume"].astype(float) / factors
    return out
