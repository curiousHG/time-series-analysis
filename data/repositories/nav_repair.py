"""NAV data-quality repair — spike removal and scale-break rescaling of mf_nav."""

from __future__ import annotations

import logging
import math
from collections import defaultdict

import polars as pl
from sqlalchemy import text
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import AmfiScheme, MfNav
from core.timing import timeit

logger = logging.getLogger(__name__)


_GLITCH_RATIO = 2.0


def detect_nav_glitches(nav: pl.DataFrame) -> pl.DataFrame:
    """Flag spurious single-day NAV spikes (NAV<=0 or an isolated reverting spike). In/out cols: scheme_code,
    date, nav."""
    r = _GLITCH_RATIO
    return (
        nav.sort(["scheme_code", "date"])
        .with_columns(
            pl.col("nav").shift(1).over("scheme_code").alias("_prev"),
            pl.col("nav").shift(-1).over("scheme_code").alias("_nxt"),
        )
        .filter(
            (pl.col("nav") <= 0)
            | (
                (pl.col("_prev") > 0)
                & (pl.col("_nxt") > 0)
                & (pl.max_horizontal("_prev", "_nxt") / pl.min_horizontal("_prev", "_nxt") < r)
                & (
                    ((pl.col("nav") / pl.col("_prev") > r) & (pl.col("nav") / pl.col("_nxt") > r))
                    | ((pl.col("nav") / pl.col("_prev") < 1 / r) & (pl.col("nav") / pl.col("_nxt") < 1 / r))
                )
            )
        )
        .select("scheme_code", "date", "nav")
    )


@timeit("nav.repair_nav_glitches")
def repair_nav_glitches(scheme_codes: list[int] | None = None, dry_run: bool = False) -> dict:
    """Delete spurious single-day NAV spikes from mf_nav (the bad point only). `dry_run=True` reports without
    deleting."""
    with get_session() as session:
        stmt = select(MfNav.scheme_code, MfNav.date, MfNav.nav)
        if scheme_codes:
            stmt = stmt.where(col(MfNav.scheme_code).in_(scheme_codes))
        rows = session.exec(stmt).all()
    if not rows:
        return {"rows_removed": 0, "schemes_affected": 0, "samples": []}

    nav = pl.DataFrame({"scheme_code": [r[0] for r in rows], "date": [r[1] for r in rows], "nav": [r[2] for r in rows]})
    glitches = detect_nav_glitches(nav)
    if glitches.height == 0:
        return {"rows_removed": 0, "schemes_affected": 0, "samples": []}

    summary = {
        "rows_removed": glitches.height,
        "schemes_affected": int(glitches["scheme_code"].n_unique()),
        "samples": glitches.head(10).to_dicts(),
    }
    if dry_run:
        logger.info("repair_nav_glitches dry-run: %(rows_removed)d row(s), %(schemes_affected)d scheme(s)", summary)
        return summary

    with get_session() as session:
        for (code,), grp in glitches.group_by("scheme_code"):
            session.exec(delete(MfNav).where(MfNav.scheme_code == code, col(MfNav.date).in_(grp["date"].to_list())))
        session.commit()
    logger.warning(
        "Repaired %d glitch NAV row(s) across %d scheme(s)", summary["rows_removed"], summary["schemes_affected"]
    )
    return summary


_SCALE_BREAK_TOL = 0.04
_SCALE_BREAK_MIN_POINTS = 30
_PLAUSIBLE_CAGR_MIN = -0.10
_PLAUSIBLE_CAGR_MAX = 0.60


def _scale_break_factor(ratio: float) -> float | None:
    """10^k if `ratio` is a clean UP power-of-10 jump (>=~10x), else None (down-breaks -> None)."""
    if ratio <= 1:
        return None
    lg = math.log10(ratio)
    k = round(lg)
    return 10.0**k if k >= 1 and abs(lg - k) < _SCALE_BREAK_TOL else None


@timeit("nav.repair_nav_scale_breaks")
def repair_nav_scale_breaks(scheme_codes: list[int] | None = None, dry_run: bool = False) -> dict:
    """Rescale wrong-scale NAV segments so each fund's history is one continuous scale.

    Per non-segregated fund, multiply pre-break NAVs by the (compounded) break factor. A post-fix
    sanity gate skips funds whose resulting whole-history CAGR lands outside a plausible band.
    `dry_run` reports without writing.
    """
    with get_session() as session:
        stmt = (
            select(MfNav.scheme_code, MfNav.date, MfNav.nav, AmfiScheme.scheme_name)
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .order_by(col(MfNav.scheme_code), col(MfNav.date))
        )
        if scheme_codes:
            stmt = stmt.where(col(MfNav.scheme_code).in_(scheme_codes))
        rows = session.exec(stmt).all()

    series: dict[int, list] = defaultdict(list)
    names: dict[int, str] = {}
    for code, d, nav, name in rows:
        series[code].append((d, nav))
        names[code] = name

    fixes: list[tuple[int, list[tuple]]] = []
    skipped: list[dict] = []
    for code, pts in series.items():
        if "segregated" in (names.get(code) or "").lower():
            continue
        navs = [n for _, n in pts]
        dates = [d for d, _ in pts]
        n = len(navs)
        if n < _SCALE_BREAK_MIN_POINTS:
            continue
        brks = [
            (dates[i], f, i)
            for i in range(1, n)
            if navs[i - 1] and navs[i - 1] > 0 and (f := _scale_break_factor(navs[i] / navs[i - 1]))
        ]
        if not brks:
            continue
        bset = {i: f for _, f, i in brks}
        cum, fac = 1.0, [1.0] * n
        for j in range(n - 1, -1, -1):
            if (j + 1) in bset:
                cum *= bset[j + 1]
            fac[j] = cum
        corr0 = navs[0] * fac[0]
        yrs = (dates[-1] - dates[0]).days / 365.25
        cagr = (navs[-1] / corr0) ** (1 / yrs) - 1 if yrs > 0 and corr0 > 0 else 0.0
        if not (_PLAUSIBLE_CAGR_MIN <= cagr <= _PLAUSIBLE_CAGR_MAX):
            skipped.append({"scheme_code": code, "name": names.get(code), "cagr_after": round(cagr, 3)})
            continue
        fixes.append((code, [(d, f) for d, f, _ in brks]))

    summary = {"funds_rescaled": len(fixes), "rows_rescaled": 0, "skipped": skipped}
    if dry_run or not fixes:
        if dry_run:
            logger.info("repair_nav_scale_breaks dry-run: %d fund(s), %d skipped", len(fixes), len(skipped))
        return summary

    with get_session() as session:
        total = 0
        for code, brks in fixes:
            for bdate, factor in brks:
                result = session.exec(
                    text("UPDATE mf_nav SET nav = nav * :f WHERE scheme_code = :c AND date < :d").bindparams(
                        f=factor, c=code, d=bdate
                    )
                )
                total += result.rowcount
        session.commit()
    summary["rows_rescaled"] = total
    logger.warning("Rescaled %d scale-break NAV row(s) across %d fund(s)", total, len(fixes))
    return summary
