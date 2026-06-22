"""Derivable statistics from the holdings / sector / asset-allocation tables.

Pure functions — they take dataframes (or nothing) and return scalars/dicts. UI layer
caches the inputs; this module just does the math.
"""

from __future__ import annotations

import polars as pl
from sqlmodel import func, select

from core.database import get_session
from core.models import MfAssetAllocation, MfHolding


def _norm_to_pct(values: list[float]) -> list[float]:
    """Normalise a weight list to sum to 100 (handles raw 0-1 vs 0-100 inputs)."""
    if not values:
        return values
    total = sum(values)
    if total <= 0:
        return values
    if total <= 1.5:  # weights stored as fractions
        return [v / total * 100.0 for v in values]
    return [v / total * 100.0 for v in values]


def asset_breakdown(slug: str) -> dict[str, float]:
    """Return {asset_class -> %} for a fund. Always normalised to 100%."""
    from data.repositories.holdings import _resolve_slug

    code = _resolve_slug(slug)
    if code is None:
        return {}
    with get_session() as session:
        latest_date_subq = (
            select(func.max(MfAssetAllocation.portfolio_date))
            .where(MfAssetAllocation.scheme_code == code)
            .scalar_subquery()
        )
        rows = session.exec(
            select(MfAssetAllocation.asset_class, MfAssetAllocation.weight)
            .where(MfAssetAllocation.scheme_code == code)
            .where(MfAssetAllocation.portfolio_date == latest_date_subq)
        ).all()
    if not rows:
        return {}
    # Latest snapshot can still have row-level duplicates from legacy refreshes; collapse
    # by asset_class taking max weight (duplicates hold identical values).
    by_class: dict[str, float] = {}
    for cls, wt in rows:
        key = cls or "Other"
        by_class[key] = max(by_class.get(key, 0.0), float(wt or 0.0))
    classes = list(by_class.keys())
    weights = list(by_class.values())
    pct = _norm_to_pct(weights)
    return dict(zip(classes, pct, strict=False))


def holdings_for_slug(slug: str) -> pl.DataFrame:
    """Load all `mf_holdings` rows for one slug as polars (used by helpers below)."""
    from data.repositories.holdings import _resolve_slug

    code = _resolve_slug(slug)
    if code is None:
        return pl.DataFrame(
            schema={
                "instrument_name": pl.Utf8,
                "weight": pl.Float64,
                "asset_class": pl.Utf8,
                "market_cap": pl.Utf8,
                "credit_rating": pl.Utf8,
                "industry": pl.Utf8,
            }
        )
    with get_session() as session:
        # Restrict to the latest portfolio_date — `mf_holdings` carries multiple snapshots
        # per scheme, and would otherwise inflate counts and weights.
        latest_date_subq = (
            select(func.max(MfHolding.portfolio_date)).where(MfHolding.scheme_code == code).scalar_subquery()
        )
        rows = session.exec(
            select(
                MfHolding.instrument_name,
                MfHolding.weight,
                MfHolding.asset_class,
                MfHolding.market_cap,
                MfHolding.credit_rating,
                MfHolding.industry,
            )
            .where(MfHolding.scheme_code == code)
            .where(MfHolding.portfolio_date == latest_date_subq)
        ).all()
    if not rows:
        return pl.DataFrame(
            schema={
                "instrument_name": pl.Utf8,
                "weight": pl.Float64,
                "asset_class": pl.Utf8,
                "market_cap": pl.Utf8,
                "credit_rating": pl.Utf8,
                "industry": pl.Utf8,
            }
        )
    return pl.DataFrame(
        {
            "instrument_name": [r[0] for r in rows],
            "weight": [float(r[1] or 0.0) for r in rows],
            "asset_class": [r[2] for r in rows],
            "market_cap": [r[3] for r in rows],
            "credit_rating": [r[4] for r in rows],
            "industry": [r[5] for r in rows],
        }
    ).unique(subset=["instrument_name"], keep="first")


def market_cap_breakdown(holdings: pl.DataFrame) -> dict[str, float]:
    """% of equity holdings by market cap bucket (Largecap/Midcap/Smallcap/Other).

    Operates on the equity slice if `asset_class` is meaningful; otherwise on whatever is provided.
    """
    if holdings.is_empty():
        return {}
    eq = holdings
    if "asset_class" in holdings.columns:
        eq_filter = holdings.filter(
            pl.col("asset_class").is_null() | pl.col("asset_class").str.to_lowercase().str.contains("equity")
        )
        if not eq_filter.is_empty():
            eq = eq_filter
    grouped = (
        eq.with_columns(pl.col("market_cap").fill_null("Other").alias("bucket"))
        .group_by("bucket")
        .agg(pl.col("weight").sum().alias("w"))
    )
    if grouped.is_empty():
        return {}
    total = float(grouped["w"].sum() or 0.0)
    if total <= 0:
        return {}
    return {row["bucket"]: float(row["w"]) / total * 100.0 for row in grouped.iter_rows(named=True)}


def concentration(holdings: pl.DataFrame, top_n: int) -> float | None:
    """Sum of weights of the top-N holdings (as a % of total weight)."""
    if holdings.is_empty():
        return None
    sorted_w = holdings.sort("weight", descending=True)
    top = sorted_w.head(top_n)["weight"].sum()
    total = sorted_w["weight"].sum()
    if total <= 0:
        return None
    return float(top) / float(total) * 100.0


def credit_breakdown(holdings: pl.DataFrame) -> dict[str, float]:
    """% of debt holdings by credit-rating bucket. Returns {} if no debt rows."""
    if holdings.is_empty() or "credit_rating" not in holdings.columns:
        return {}
    debt = holdings.filter(pl.col("credit_rating").is_not_null() & (pl.col("credit_rating") != ""))
    if debt.is_empty():
        return {}
    grouped = debt.group_by("credit_rating").agg(pl.col("weight").sum().alias("w"))
    total = float(grouped["w"].sum() or 0.0)
    if total <= 0:
        return {}
    return {row["credit_rating"]: float(row["w"]) / total * 100.0 for row in grouped.iter_rows(named=True)}


def quick_stats(slug: str) -> dict:
    """One-shot summary of derivable stats for a fund. Used in MF Analysis Holdings tab and screener."""
    holdings = holdings_for_slug(slug)
    asset = asset_breakdown(slug)
    mcap = market_cap_breakdown(holdings)
    credit = credit_breakdown(holdings)

    # Convenience aggregates
    pct_equity = sum(v for k, v in asset.items() if k and "equity" in k.lower())
    pct_debt = sum(v for k, v in asset.items() if k and ("debt" in k.lower() or "bond" in k.lower()))
    pct_cash = sum(
        v for k, v in asset.items() if k and ("cash" in k.lower() or "treps" in k.lower() or "money" in k.lower())
    )

    pct_largecap = mcap.get("Largecap", 0.0) or mcap.get("Large Cap", 0.0)
    pct_midcap = mcap.get("Midcap", 0.0) or mcap.get("Mid Cap", 0.0)
    pct_smallcap = mcap.get("Smallcap", 0.0) or mcap.get("Small Cap", 0.0)
    pct_other_mcap = sum(
        v for k, v in mcap.items() if k not in {"Largecap", "Large Cap", "Midcap", "Mid Cap", "Smallcap", "Small Cap"}
    )

    # Sovereign / corporate / "B-rated" / "Poor" rough buckets from credit ratings
    def _sum_matching(d: dict, *keywords: str) -> float:
        return sum(v for k, v in d.items() if k and any(kw.lower() in k.lower() for kw in keywords))

    pct_sovereign = _sum_matching(credit, "sovereign", "govt", "g-sec", "tbill", "soverign")
    pct_corporate = _sum_matching(credit, "aaa", "aa+", "aa", "aa-", "a+", "a", "a-")  # corporate-ish (high-grade)
    pct_b_rated = _sum_matching(credit, "bbb", "bb", "b-", "b+", "b ")
    pct_poor = _sum_matching(credit, "below", "unrated", "default", "d ", "ccc", "c-rated")

    return {
        "n_holdings": holdings.height,
        "asset_breakdown": asset,
        "market_cap_breakdown": mcap,
        "credit_breakdown": credit,
        "pct_equity": pct_equity,
        "pct_debt": pct_debt,
        "pct_cash": pct_cash,
        "pct_largecap": pct_largecap,
        "pct_midcap": pct_midcap,
        "pct_smallcap": pct_smallcap,
        "pct_other_mcap": pct_other_mcap,
        "pct_top3": concentration(holdings, 3),
        "pct_top5": concentration(holdings, 5),
        "pct_top10": concentration(holdings, 10),
        "pct_sovereign": pct_sovereign,
        "pct_corporate_high_grade": pct_corporate,
        "pct_b_rated": pct_b_rated,
        "pct_poor_rated": pct_poor,
    }
