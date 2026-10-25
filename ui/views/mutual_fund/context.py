"""Shared context for the MF Analysis tabs — one load, many renderers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from ui.state.loaders import load_benchmark_returns

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FundContext:
    """Everything a tab needs to render one fund, loaded once by the page."""

    scheme_name: str
    short_name: str
    nav_pd: pd.Series
    returns: pd.Series
    meta: dict
    amfi_row: dict | None
    metrics_row: dict | None
    sub_category: str | None
    holdings_status: str


def load_index_returns(symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily pct-change series via the shared cached loader; empty Series on failure (logged with trace)."""
    try:
        series = load_benchmark_returns(symbol, start, end)
    except Exception:
        logger.exception("Benchmark load failed for %s (%s → %s)", symbol, start.date(), end.date())
        return pd.Series(dtype="float64")
    if series.empty:
        logger.warning("Benchmark %s returned no rows for %s → %s", symbol, start.date(), end.date())
    return series


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
