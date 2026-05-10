"""Correlation analytics for mutual fund NAV returns."""

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

_LABEL_COL = "shortName"


def daily_returns(nav_pd: pd.DataFrame) -> pd.DataFrame:
    """Wide returns DataFrame indexed by date, columns = short fund names."""
    label_col = _LABEL_COL if _LABEL_COL in nav_pd.columns else "schemeName"
    return nav_pd.pivot(index="date", columns=label_col, values="nav").pct_change(fill_method=None)


def monthly_returns(nav_pd: pd.DataFrame) -> pd.DataFrame:
    label_col = _LABEL_COL if _LABEL_COL in nav_pd.columns else "schemeName"
    wide = nav_pd.pivot(index="date", columns=label_col, values="nav")
    if not isinstance(wide.index, pd.DatetimeIndex):
        wide.index = pd.to_datetime(wide.index)
    return wide.resample("ME").last().pct_change(fill_method=None)


def excess_returns(returns: pd.DataFrame, benchmark: pd.Series) -> pd.DataFrame:
    aligned = returns.join(benchmark.rename("__bench__"), how="inner")
    bench = aligned.pop("__bench__")
    return aligned.sub(bench, axis=0).dropna(how="all")


def downside_returns(returns: pd.DataFrame, benchmark: pd.Series) -> pd.DataFrame:
    aligned = returns.join(benchmark.rename("__bench__"), how="inner")
    bench = aligned.pop("__bench__")
    return aligned[bench < 0].dropna(how="all")


def correlation_matrix(returns: pd.DataFrame, min_periods: int = 30) -> pd.DataFrame:
    if returns.empty or returns.shape[1] < 2:
        return pd.DataFrame()
    return returns.corr(min_periods=min_periods).fillna(0)


def hierarchical_order(corr: pd.DataFrame) -> list[str]:
    """Order scheme names so similar funds cluster together (correlation distance + average linkage)."""
    if corr.empty or corr.shape[0] < 2:
        return list(corr.columns)

    distance = np.sqrt(np.clip(2 * (1 - corr.abs().to_numpy()), 0, None))
    np.fill_diagonal(distance, 0.0)
    condensed = squareform(distance, checks=False)
    z = linkage(condensed, method="average")
    leaves = leaves_list(z)
    cols = list(corr.columns)
    return [cols[i] for i in leaves]


def rolling_pair_corr(returns: pd.DataFrame, scheme_a: str, scheme_b: str, window: int) -> pd.Series:
    if scheme_a not in returns.columns or scheme_b not in returns.columns:
        return pd.Series(dtype="float64")
    return returns[scheme_a].rolling(window).corr(returns[scheme_b]).dropna()


def top_pair(corr: pd.DataFrame) -> tuple[str, str] | None:
    """Return the (a, b) scheme pair with the highest off-diagonal correlation."""
    if corr.empty or corr.shape[0] < 2:
        return None
    arr = corr.to_numpy().copy()
    np.fill_diagonal(arr, -np.inf)
    i, j = np.unravel_index(np.argmax(arr), arr.shape)
    cols = list(corr.columns)
    return cols[i], cols[j]
