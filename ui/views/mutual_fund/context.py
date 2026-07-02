"""Shared context for the MF Analysis tabs — one load, many renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from ui.state.loaders import load_benchmark_returns

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class FundContext:
    """Everything a tab needs to render one fund, loaded once by the page."""

    scheme_name: str
    short_name: str
    nav_pd: pd.Series  # daily NAV indexed by date
    returns: pd.Series  # daily pct-change of NAV
    meta: dict  # mf_metadata row ({} when absent)
    amfi_row: dict | None  # amfi_schemes row
    metrics_row: dict | None  # cached mf_scheme_metrics row
    sub_category: str | None
    holdings_status: str


def load_index_returns(symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily pct-change series via the shared cached loader; empty Series on failure."""
    try:
        return load_benchmark_returns(symbol, start, end)
    except Exception:
        return pd.Series(dtype="float64")


def rebased_index(returns: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Cumprod of daily returns reindexed to `dates`, rebased to 100 at the first non-null."""
    if returns.empty:
        return pd.Series(dtype="float64")
    idx = (1 + returns).cumprod()
    idx = idx.reindex(dates).ffill().bfill()
    if idx.dropna().empty:
        return pd.Series(dtype="float64")
    first = idx.dropna().iloc[0]
    return idx / first * 100
