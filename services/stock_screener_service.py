"""Stock screener assembly + filters. Joins stock_registry with the cached stock_metrics
and tags each row with its alpha category. Parallels services.screener_service (MF)."""

from __future__ import annotations

import polars as pl
from sqlmodel import select

from core.database import get_session
from core.models import StockRegistry
from data.repositories.stock_fundamentals import load_stock_metrics
from stocks.metric_catalog import alpha_category


def _registry_names() -> pl.DataFrame:
    with get_session() as session:
        rows = session.exec(select(StockRegistry.symbol, StockRegistry.stock_name)).all()
    return pl.DataFrame(
        {"symbol": [r[0] for r in rows], "stock_name": [r[1] for r in rows]},
        schema={"symbol": pl.Utf8, "stock_name": pl.Utf8},
    )


def build_stock_screener_df() -> pl.DataFrame:
    """One row per stock with cached metrics + name + alpha category (empty if nothing cached)."""
    metrics = load_stock_metrics()
    if metrics.is_empty():
        return metrics
    df = metrics.join(_registry_names(), on="symbol", how="left")
    return df.with_columns(
        pl.struct(["alpha_1y", "beta_1y"])
        .map_elements(lambda s: alpha_category(s["alpha_1y"], s["beta_1y"]), return_dtype=pl.Utf8)
        .alias("alpha_category")
    )


def apply_stock_filters(
    df: pl.DataFrame,
    *,
    name_query: str = "",
    market_cap_min: float | None = None,
    pe_max: float | None = None,
    roe_min: float | None = None,
    alpha_min: float | None = None,
    categories: list[str] | None = None,
) -> pl.DataFrame:
    """Polars filter chain over the screener frame. Missing values are excluded by a threshold."""
    if df.is_empty():
        return df
    if name_query:
        for tok in name_query.lower().split():
            df = df.filter(
                pl.col("stock_name").str.to_lowercase().str.contains(tok, literal=True)
                | pl.col("symbol").str.to_lowercase().str.contains(tok, literal=True)
            )
    if market_cap_min is not None:
        df = df.filter(pl.col("market_cap") >= market_cap_min)
    if pe_max is not None:
        df = df.filter(pl.col("stock_pe") <= pe_max)
    if roe_min is not None:
        df = df.filter(pl.col("roe") >= roe_min)
    if alpha_min is not None:
        df = df.filter(pl.col("alpha_1y") >= alpha_min)
    if categories:
        df = df.filter(pl.col("alpha_category").is_in(categories))
    return df
