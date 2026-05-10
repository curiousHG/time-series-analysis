"""Repository for mf_scheme_metrics — cached per-scheme risk/return metrics.

The actual computation lives in services.mf_metrics; this module is purely DB CRUD +
staleness detection. Following the project's data-fetching policy: callers ask the
repository for metrics, the repository decides what's cached vs needs recompute, and
recomputation is delegated to the service layer.

Phase 2: keyed on scheme_code internally; public APIs still pass scheme_name for caller
convenience (resolved via the AmfiScheme join).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.models import AmfiScheme, MfNav, MfSchemeMetrics
from core.timing import timeit

logger = logging.getLogger("data.repositories.scheme_metrics")


_METRIC_FIELDS: tuple[str, ...] = (
    # CAGR
    "cagr_1y", "cagr_3y", "cagr_5y", "cagr_10y",
    # Risk-adjusted ratios
    "vol_1y", "downside_vol_1y", "sharpe_1y", "sortino_1y", "calmar_1y", "gain_to_pain_1y",
    # Drawdown / cumulative
    "max_dd_1y", "cumulative_return_1y", "avg_daily_return_1y",
    # Distribution stats
    "win_rate_1y", "best_day_1y", "worst_day_1y", "var_95_1y", "cvar_95_1y", "skew_1y", "kurt_1y",
    # Position-sizing diagnostics
    "kelly_1y", "avg_win_1y", "avg_loss_1y", "payoff_ratio_1y",
    # All-time
    "max_dd_all", "pct_from_ath",
    # Absolute returns
    "abs_return_3m", "abs_return_6m", "abs_return_1y",
    # Holdings composition
    "pct_equity", "pct_debt", "pct_cash",
    # Concentration
    "pct_top3", "pct_top5", "pct_top10",
    # CAPM vs Nifty 50
    "alpha_1y", "beta_1y", "r2_1y", "tracking_error_1y",
    # Rolling annualised-CAGR distribution (1/3/5 year windows)
    "rolling_1y_min", "rolling_1y_median", "rolling_1y_mean", "rolling_1y_max",
    "rolling_3y_min", "rolling_3y_median", "rolling_3y_mean", "rolling_3y_max",
    "rolling_5y_min", "rolling_5y_median", "rolling_5y_mean", "rolling_5y_max",
    # Provenance fields
    "inception_date", "last_nav", "last_nav_date", "history_days",
)  # fmt: skip


def _resolve_codes_with_synthetic(scheme_names: list[str]) -> dict[str, int]:
    """name -> scheme_code via amfi_schemes; mints synthetic negatives for missing names."""
    if not scheme_names:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, AmfiScheme.scheme_code).where(
                col(AmfiScheme.scheme_name).in_(scheme_names)
            )
        ).all()
        out = {r[0]: r[1] for r in rows}
        missing = [n for n in scheme_names if n not in out]
        if missing:
            min_code = session.exec(select(func.min(AmfiScheme.scheme_code))).one() or 0
            next_neg = min(min_code, 0) - 1
            for name in missing:
                session.exec(
                    pg_insert(AmfiScheme)
                    .values(scheme_code=next_neg, scheme_name=name)
                    .on_conflict_do_nothing(index_elements=["scheme_code"])
                )
                out[name] = next_neg
                next_neg -= 1
            session.commit()
    return out


@timeit("scheme_metrics.load")
def load_metrics(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Return cached metrics for the given schemes (or all). Output schema unchanged —
    `scheme_name` projected via the JOIN to `amfi_schemes`.
    """
    with get_session() as session:
        stmt = select(AmfiScheme.scheme_name.label("scheme_name"), MfSchemeMetrics).join(
            AmfiScheme, MfSchemeMetrics.scheme_code == AmfiScheme.scheme_code
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"scheme_name": pl.Utf8})

    return pl.DataFrame(
        {
            "scheme_name": [r[0] for r in rows],
            **{f: [getattr(r[1], f) for r in rows] for f in _METRIC_FIELDS},
            "computed_at": [r[1].computed_at for r in rows],
            "computed_at_nav_date": [r[1].computed_at_nav_date for r in rows],
        }
    )


@timeit("scheme_metrics.upsert_many")
def upsert_metrics(rows: list[dict[str, Any]]) -> int:
    """Bulk-upsert metric rows. Each row must carry `scheme_name`; we resolve to
    scheme_code via amfi_schemes (synthetic negatives for missing names).
    """
    if not rows:
        return 0

    name_to_code = _resolve_codes_with_synthetic([r["scheme_name"] for r in rows if r.get("scheme_name")])
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    payload = []
    for r in rows:
        name = r.get("scheme_name")
        if not name or name not in name_to_code:
            continue
        d = {"scheme_code": name_to_code[name]}
        for f in _METRIC_FIELDS:
            d[f] = r.get(f)
        d["computed_at"] = r.get("computed_at") or now
        d["computed_at_nav_date"] = r.get("computed_at_nav_date") or r.get("last_nav_date")
        payload.append(d)

    if not payload:
        return 0

    with get_session() as session:
        stmt = pg_insert(MfSchemeMetrics).values(payload)
        update_cols = {c: getattr(stmt.excluded, c) for c in payload[0] if c != "scheme_code"}
        stmt = stmt.on_conflict_do_update(index_elements=["scheme_code"], set_=update_cols)
        session.exec(stmt)
        session.commit()

    logger.info("upserted %d scheme_metrics rows", len(payload))
    return len(payload)


@timeit("scheme_metrics.find_stale")
def find_stale_schemes() -> list[str]:
    """Return scheme_names whose cached metric is older than the latest NAV.

    A scheme is stale when:
      • it has NAV history but no metrics row, or
      • its `computed_at_nav_date` < max(MfNav.date) for the same scheme.
    """
    with get_session() as session:
        # Latest NAV date per scheme. JOIN to amfi_schemes for the scheme_name.
        nav_stmt = (
            select(AmfiScheme.scheme_name, func.max(MfNav.date).label("max_date"))
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .group_by(AmfiScheme.scheme_name)
        )
        latest_nav = {r[0]: r[1] for r in session.exec(nav_stmt).all()}

        # All cached metric rows.
        m_stmt = (
            select(AmfiScheme.scheme_name, MfSchemeMetrics.computed_at_nav_date)
            .join(AmfiScheme, MfSchemeMetrics.scheme_code == AmfiScheme.scheme_code)
        )
        cached = {r[0]: r[1] for r in session.exec(m_stmt).all()}

    stale: list[str] = []
    for name, nav_max in latest_nav.items():
        cached_at = cached.get(name)
        if cached_at is None or cached_at < nav_max:
            stale.append(name)
    return stale


def clear_metrics(scheme_names: list[str] | None = None) -> int:
    """Drop cached metrics for the given names (or all). Useful when the metrics schema changes."""
    with get_session() as session:
        if scheme_names:
            codes = [
                r[0]
                for r in session.exec(
                    select(AmfiScheme.scheme_code).where(col(AmfiScheme.scheme_name).in_(scheme_names))
                ).all()
            ]
            if not codes:
                return 0
            result = session.exec(delete(MfSchemeMetrics).where(col(MfSchemeMetrics.scheme_code).in_(codes)))
        else:
            result = session.exec(delete(MfSchemeMetrics))
        session.commit()
    return getattr(result, "rowcount", 0) or 0
