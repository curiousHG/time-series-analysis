"""Future-aware training labels, prefixed `&-`.

Labels look `horizon` bars ahead and exist only to train models: the walk-forward purges them from
the training window and entry/exit code reads `&-pred` alone. The last `horizon` rows of every
label are NaN because their future is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from indicators import compute_indicators
from strategies.ml.features import TARGET_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable

LabelKind = Literal["regressor", "classifier"]

ATR_GAIN_CLIP = 5.0


@dataclass(frozen=True)
class LabelSpec:
    name: str
    kind: LabelKind
    fn: Callable[..., pd.Series]
    description: str


LABEL_REGISTRY: dict[str, LabelSpec] = {}


def register_label(name: str, kind: LabelKind, description: str):
    def decorate(fn):
        LABEL_REGISTRY[name] = LabelSpec(name, kind, fn, description)
        return fn

    return decorate


def label_column(name: str) -> str:
    return f"{TARGET_PREFIX}{name}"


def build_label(df: pd.DataFrame, name: str, horizon: int, **kw) -> pd.Series:
    if name not in LABEL_REGISTRY:
        raise KeyError(f"unknown label {name!r}; choose from {sorted(LABEL_REGISTRY)}")
    if horizon < 1:
        raise ValueError("horizon must be at least 1 bar")
    series = LABEL_REGISTRY[name].fn(df, horizon, **kw)
    return series.astype(float).rename(label_column(name))


def _natr(df: pd.DataFrame, natr: pd.Series | None) -> pd.Series:
    if natr is None:
        _, panels = compute_indicators(df, ["NATR"])
        natr = panels["NATR"]
    return natr.where(natr > 0)


@register_label("fwd_return", "regressor", "log return from Close to Close h bars ahead (or next Open to h+1 Close)")
def fwd_return(df: pd.DataFrame, horizon: int, *, realizable: bool = False) -> pd.Series:
    if realizable:
        return np.log(df["Close"].shift(-(horizon + 1)) / df["Open"].shift(-1))
    return np.log(df["Close"].shift(-horizon) / df["Close"])


@register_label("direction", "classifier", "1 when the forward log return exceeds `threshold`, else 0")
def direction(df: pd.DataFrame, horizon: int, *, threshold: float = 0.0, realizable: bool = False) -> pd.Series:
    ret = fwd_return(df, horizon, realizable=realizable)
    return (ret > threshold).astype(float).where(ret.notna())


@register_label("atr_fwd_gain", "regressor", "forward log return divided by NATR, clipped to +/-5")
def atr_fwd_gain(
    df: pd.DataFrame, horizon: int, *, natr: pd.Series | None = None, realizable: bool = False
) -> pd.Series:
    ret = fwd_return(df, horizon, realizable=realizable)
    return (ret / (_natr(df, natr) / 100.0)).clip(-ATR_GAIN_CLIP, ATR_GAIN_CLIP)


@register_label("triple_barrier", "classifier", "+1/-1 for the first k*NATR barrier touched within h bars, else 0")
def triple_barrier(df: pd.DataFrame, horizon: int, *, natr: pd.Series | None = None, k: float = 2.0) -> pd.Series:
    width = df["Close"] * k * _natr(df, natr) / 100.0
    upper = df["Close"] + width
    lower = df["Close"] - width
    out = pd.Series(np.nan, index=df.index)
    decided = pd.Series(False, index=df.index)
    for step in range(1, horizon + 1):
        hi = df["High"].shift(-step)
        lo = df["Low"].shift(-step)
        hit_lower = lo <= lower
        hit_upper = hi >= upper
        new_lower = hit_lower & ~decided
        new_upper = hit_upper & ~hit_lower & ~decided
        out[new_lower] = -1.0
        out[new_upper] = 1.0
        decided |= hit_lower | hit_upper
    known = width.notna() & df["Close"].shift(-horizon).notna()
    out = out.where(decided, 0.0)
    return out.where(known)
