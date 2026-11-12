"""Net-flow estimates from AMFI average AUM: the part of AUM growth that NAV growth does not explain.

flow_t = AUM_t - AUM_{t-1} * NAV_t / NAV_{t-1}, with AUM and NAV both averaged over the disclosure period.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import polars as pl

from data.repositories.aaum import load_aaum_with_nav

if TYPE_CHECKING:
    from collections.abc import Iterable

LAKHS_PER_CRORE = 100
MAX_PERIOD_GAP_DAYS = 95
_PLAN_SUFFIX_RE = re.compile(
    r"\s*[-\u2013(]\s*(direct|regular|retail|institutional|growth|idcw|dividend|bonus)\b.*$", re.IGNORECASE
)
_TRAILING_NOISE_RE = re.compile(r"[\s\-\u2013(]+$")
_CATEGORY_PREFIX_RE = re.compile(
    r"^(equity schemes?|other schemes?|hybrid schemes?|income/debt oriented schemes?|solution oriented schemes?)"
    r"\s*-\s*",
    re.IGNORECASE,
)
_EQUITY_CATEGORY_ALIASES = {
    "sectoral/ thematic": "Sectoral/Thematic",
    "sectoral fund": "Sectoral/Thematic",
    "thematic fund": "Sectoral/Thematic",
    "elss- tax saver fund": "ELSS",
}
_INDEX_FUND_LABELS = {
    "other scheme - index funds": "Index Funds",
    "index funds - equity funds": "Index Funds",
    "index funds - debt funds": "Index Funds (Debt)",
    "index funds - hybrid fund": "Index Funds (Hybrid)",
}


def canonical_category(label: str | None) -> str | None:
    """One name per SEBI category across AMFI's per-AMC label variants.

    "Equity Scheme - Small Cap Fund" and "Equity Schemes - Small Cap Fund" \u2192 "Small Cap Fund"; both index-fund labels \u2192
    "Index Funds"; sectoral and thematic labels \u2192 "Sectoral/Thematic". Unknown labels lose only the scheme-type prefix.
    """
    if not label:
        return None
    text = re.sub(r"\s+", " ", label).strip()
    lowered = text.lower()
    if lowered in _INDEX_FUND_LABELS:
        return _INDEX_FUND_LABELS[lowered]
    body = _CATEGORY_PREFIX_RE.sub("", text)
    if lowered.startswith("equity"):
        return _EQUITY_CATEGORY_ALIASES.get(body.lower(), body)
    return body


def fund_family(scheme_name: str | None) -> str | None:
    """Plan-agnostic fund name: "Parag Parikh Flexi Cap Fund - Direct Plan - Growth" → "Parag Parikh Flexi Cap Fund"."""
    if not scheme_name:
        return None
    return _TRAILING_NOISE_RE.sub("", _PLAN_SUFFIX_RE.sub("", scheme_name.strip())) or scheme_name.strip()


def compute_scheme_flows(aum_nav: pl.DataFrame) -> pl.DataFrame:
    """Per scheme-period net flow in ₹ crore and as a share of the prior period's AUM.

    `aum_nav` needs scheme_code, period_end, aum_lakhs, avg_nav (as `load_aaum_with_nav` returns). The first period
    of each scheme, periods more than MAX_PERIOD_GAP_DAYS after the scheme's previous one (a missing quarter), and
    periods where either NAV average is missing get null flows.
    """
    adjacent = (pl.col("period_end") - pl.col("period_end").shift(1).over("scheme_code")).dt.total_days() <= (
        MAX_PERIOD_GAP_DAYS
    )
    return (
        aum_nav.sort(["scheme_code", "period_end"])
        .with_columns(
            pl.when(adjacent).then(pl.col("aum_lakhs").shift(1).over("scheme_code")).alias("prev_aum_lakhs"),
            pl.when(adjacent).then(pl.col("avg_nav").shift(1).over("scheme_code")).alias("prev_avg_nav"),
        )
        .with_columns((pl.col("avg_nav") / pl.col("prev_avg_nav")).alias("nav_growth"))
        .with_columns(
            (pl.col("aum_lakhs") - pl.col("prev_aum_lakhs") * pl.col("nav_growth")).alias("flow_lakhs"),
        )
        .with_columns(
            (pl.col("aum_lakhs") / LAKHS_PER_CRORE).alias("aum_cr"),
            (pl.col("flow_lakhs") / LAKHS_PER_CRORE).alias("flow_cr"),
            pl.when(pl.col("prev_aum_lakhs") > 0)
            .then(pl.col("flow_lakhs") / pl.col("prev_aum_lakhs"))
            .otherwise(None)
            .alias("flow_pct"),
        )
        .drop("flow_lakhs")
    )


def aggregate_flows(scheme_flows: pl.DataFrame, by: str | list[str]) -> pl.DataFrame:
    """Sum flows and AUM per `by` group and period; flow_pct is relative to the group's prior AUM where NAV was known."""
    keys = [by] if isinstance(by, str) else list(by)
    priced = pl.col("flow_cr").is_not_null()
    return (
        scheme_flows.group_by([*keys, "period_end"])
        .agg(
            pl.col("aum_cr").sum(),
            pl.col("flow_cr").sum(),
            (pl.col("prev_aum_lakhs").filter(priced).sum() / LAKHS_PER_CRORE).alias("priced_prev_aum_cr"),
            pl.len().alias("plans"),
        )
        .with_columns(
            pl.when(pl.col("priced_prev_aum_cr") > 0)
            .then(pl.col("flow_cr") / pl.col("priced_prev_aum_cr"))
            .otherwise(None)
            .alias("flow_pct")
        )
        .sort([*keys, "period_end"])
    )


def trailing_flow_summary(flows: pl.DataFrame, by: str | list[str], periods: int = 4) -> pl.DataFrame:
    """Per group over its last `periods` disclosure periods: latest AUM, net flow (₹ cr), flow as % of the AUM at the
    window start, AUM growth, and how many of those periods had inflows. Expects `aggregate_flows` output."""
    keys = [by] if isinstance(by, str) else list(by)
    return (
        flows.sort([*keys, "period_end"])
        .group_by(keys)
        .agg(
            pl.col("period_end").last().alias("as_of"),
            pl.col("aum_cr").last().alias("aum_cr"),
            pl.col("aum_cr").tail(periods + 1).first().alias("aum_cr_start"),
            pl.col("flow_cr").tail(periods).sum().alias("flow_cr"),
            (pl.col("flow_cr").tail(periods) > 0).sum().alias("inflow_periods"),
        )
        .with_columns(
            pl.when(pl.col("aum_cr_start") > 0)
            .then(pl.col("flow_cr") / pl.col("aum_cr_start"))
            .otherwise(None)
            .alias("flow_pct_of_start"),
            pl.when(pl.col("aum_cr_start") > 0)
            .then(pl.col("aum_cr") / pl.col("aum_cr_start") - 1)
            .otherwise(None)
            .alias("aum_growth"),
        )
        .sort("flow_cr", descending=True)
    )


def load_scheme_flows(scheme_codes: Iterable[int] | None = None) -> pl.DataFrame:
    """Per scheme-period flows for stored AAUM, with plan-agnostic `family` and label-agnostic `category_group` columns
    for grouping."""
    flows = compute_scheme_flows(load_aaum_with_nav(scheme_codes))
    return flows.with_columns(
        pl.col("scheme_name").map_elements(fund_family, return_dtype=pl.Utf8).alias("family"),
        pl.col("category").map_elements(canonical_category, return_dtype=pl.Utf8).alias("category_group"),
    )
