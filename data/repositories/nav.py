"""NAV data repository — load, save, fetch, and ensure NAV data.

NAV is keyed on `scheme_code` (int), but public functions still take
`scheme_names: list[str]`, resolved to codes via `amfi_schemes`. Output keeps `schemeName`
so name-joining callers (Portfolio analytics, screener) don't change.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.models import AmfiScheme, MfNav
from core.timing import timeit
from data.fetchers.mutual_fund import (
    fetch_nav_from_advisorkhoj,
    fetch_nav_from_mfapi,
    resolve_mfapi_code,
)
from data.repositories.amfi import lookup_scheme_code_by_exact_name
from data.repositories.scheme_codes import resolve_codes_with_synthetic

logger = logging.getLogger("data.repositories.nav")


# Name -> code resolution + synthetic minting lives in data.repositories.scheme_codes.


def _resolve_names(scheme_codes: list[int]) -> dict[int, str]:
    """Return scheme_code -> name via amfi_schemes."""
    if not scheme_codes:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_code, AmfiScheme.scheme_name).where(col(AmfiScheme.scheme_code).in_(scheme_codes))
        ).all()
    return {r[0]: r[1] for r in rows}


def _get_or_resolve_scheme_code(scheme_name: str) -> str | None:
    """Resolve a scheme_name to its MFAPI/AMFI code (str). Direct AMFI lookup, then fuzzy."""
    code = lookup_scheme_code_by_exact_name(scheme_name)
    if code:
        return code
    return resolve_mfapi_code(scheme_name)


# ---- NAV data ----


def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
    """Convert MFAPI raw nav payload to a (date, nav, schemeName) DataFrame."""
    cleaned = [{"ts_ms": int(row[0]), "nav": float(row[1])} for row in nav_json if row and row[1] is not None]
    return (
        pl.DataFrame(cleaned)
        .with_columns(
            pl.from_epoch(pl.col("ts_ms"), time_unit="ms").dt.date().alias("date"),
            pl.col("nav").alias("nav"),
            pl.lit(scheme_name).alias("schemeName"),
        )
        .select("date", "nav", "schemeName")
        .sort("date")
        .unique(subset=["date", "schemeName"], keep="last")
    )


def _upsert_nav_rows(session, df: pl.DataFrame, name_to_code: dict[str, int]) -> None:
    """Upsert df's (date, nav, schemeName) rows into mf_nav within the caller's session."""
    for row in df.iter_rows(named=True):
        code = name_to_code[row["schemeName"]]
        stmt = (
            pg_insert(MfNav)
            .values(scheme_code=code, date=row["date"], nav=row["nav"])
            .on_conflict_do_update(
                index_elements=["scheme_code", "date"],
                set_={"nav": row["nav"]},
            )
        )
        session.exec(stmt)


def save_nav_df(df: pl.DataFrame) -> None:
    """Upsert NAV rows (date, nav, schemeName) into the DB.

    schemeName -> scheme_code via amfi_schemes; names with no AMFI match get a
    synthetic-negative code (see scheme_codes) so the FK never violates.
    """
    if df.height == 0:
        return
    name_to_code = resolve_codes_with_synthetic(df["schemeName"].unique().to_list())
    with get_session() as session:
        _upsert_nav_rows(session, df, name_to_code)
        session.commit()
    logger.info("Saved %d NAV rows to database", df.height)


def load_nav_df(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Load NAV data as (date, nav, schemeName), JOINing mf_nav -> amfi_schemes to project
    scheme_name. Filtering by scheme_names goes through the JOIN (cheap; small, indexed).
    """
    with get_session() as session:
        stmt = (
            select(MfNav.date, MfNav.nav, AmfiScheme.scheme_name)
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .order_by(col(MfNav.date))
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8})
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "nav": [r[1] for r in rows],
            "schemeName": [r[2] for r in rows],
        }
    )


def fetch_single_nav(scheme_name: str) -> pl.DataFrame:
    """Fetch NAV for a single scheme. Tries MFAPI first, falls back to AdvisorKhoj."""
    scheme_code = _get_or_resolve_scheme_code(scheme_name)
    if scheme_code:
        try:
            return fetch_nav_from_mfapi(scheme_code, scheme_name)
        except Exception as e:
            logger.warning("MFAPI failed for %s (code=%s): %s", scheme_name, scheme_code, e)

    logger.info("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    return nav_json_to_df(data["nav_data"], scheme_name)


@timeit("nav.fetch_nav_parallel")
def fetch_nav_parallel(scheme_names: list[str]) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    new_frames = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {pool.submit(fetch_single_nav, scheme): scheme for scheme in scheme_names}
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)
    return new_frames


def _recompute_metrics_for(scheme_names: list[str]) -> None:
    """Best-effort metrics recompute. Logged-only on failure — never break the NAV save."""
    if not scheme_names:
        return
    try:
        from services.mf_metrics import recompute_metrics  # noqa: PLC0415 — repo→service; top-level import would cycle

        recompute_metrics(scheme_names)
    except Exception:
        logger.exception("Post-NAV-save metrics recompute failed for %d scheme(s)", len(scheme_names))


@timeit("nav.ensure_nav_data")
def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """DB-first: load existing NAV, fetch only missing schemes, recompute metrics, return all."""
    nav_df = load_nav_df(scheme_names)
    existing = nav_df.select("schemeName").unique().to_series().to_list() if nav_df.height else []
    missing = list(set(scheme_names) - set(existing))
    if not missing:
        return nav_df

    new_frames = fetch_nav_parallel(missing)
    saved_schemes: list[str] = []
    for df in new_frames:
        save_nav_df(df)
        if df.height:
            saved_schemes.extend(df["schemeName"].unique().to_list())

    _recompute_metrics_for(saved_schemes)
    return load_nav_df(scheme_names)


def refresh_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Re-fetch and replace NAV for given schemes. Only schemes that fetch successfully are
    deleted/replaced — a transient upstream failure must not erase good local NAV history.
    """
    new_frames = fetch_nav_parallel(scheme_names)
    fetched_schemes = [name for df in new_frames if df.height for name in df["schemeName"].unique().to_list()]
    if not fetched_schemes:
        return load_nav_df(scheme_names)

    # Delete + re-insert in one transaction so a save failure never wipes NAV with no replacement.
    name_to_code = resolve_codes_with_synthetic(fetched_schemes)
    codes = [name_to_code[n] for n in fetched_schemes if n in name_to_code]
    with get_session() as session:
        if codes:
            session.exec(delete(MfNav).where(col(MfNav.scheme_code).in_(codes)))
        for df in new_frames:
            if df.height:
                _upsert_nav_rows(session, df, name_to_code)
        session.commit()

    _recompute_metrics_for(fetched_schemes)
    return load_nav_df(scheme_names)


def count_distinct_nav_schemes() -> int:
    """Number of distinct schemes that have any NAV history."""
    with get_session() as session:
        return int(session.exec(select(func.count(func.distinct(MfNav.scheme_code)))).one() or 0)


# A single-day NAV that jumps >2x (or <0.5x) vs BOTH neighbours and then reverts is upstream
# data corruption (a NAV-scale change, a one-off bad print, a near-zero division). 2.0 is
# conservative: a real fund doesn't double in a day and undo it the next. Genuine corporate
# actions (side-pocketing, bonus) step *permanently*, so neighbours disagree → not flagged.
_GLITCH_RATIO = 2.0


def detect_nav_glitches(nav: pl.DataFrame) -> pl.DataFrame:
    """Flag spurious single-day NAV spikes. In/out columns: scheme_code, date, nav.

    Glitch = NAV<=0, or a point sitting >ratio away from *both* neighbours in the same
    direction while those neighbours agree within `ratio` (i.e. an isolated spike that
    reverts). Vectorised so it scales to the full mf_nav table in one pass.
    """
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
    """Delete spurious single-day NAV spikes from mf_nav so they stop poisoning metrics.

    These isolated glitches (often one bad day in 2007-2015, frequently the *same* date across
    many funds) made the metrics corrupt-NAV guard discard the whole fund. Removing just the bad
    point lets the fund's real history through. `dry_run=True` reports without deleting.
    """
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


# A persistent jump to a clean power-of-10 (>=10x) in a non-segregated fund is a unit/scale-stitch
# error in the upstream feed - e.g. a liquid fund's early history at a Rs10 base spliced onto the
# real Rs1000+ NAV (MFAPI itself carries these). It's not a market move. We rescale the earlier
# (wrong-scale) segment UP to the recent, correct scale. DOWN power-of-10 breaks (an ETF unit
# split, Rs4000->Rs40) are real corporate actions and deliberately left untouched.
_SCALE_BREAK_TOL = 0.04  # |log10(ratio) - k| tolerance to call a jump a clean 10^k


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

    For every non-segregated fund with a clean power-of-10 UP break, multiply all NAVs before the
    break by the break factor (compounding across multiple breaks) to lift the wrong-scale early
    history onto the correct recent scale. A post-fix sanity gate skips any fund whose resulting
    whole-history CAGR lands outside a plausible band (catches multi-stitch oddities). `dry_run`
    reports without writing.
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

    fixes: list[tuple[int, list[tuple]]] = []  # (code, [(break_date, factor), ...])
    skipped: list[dict] = []
    for code, pts in series.items():
        if "segregated" in (names.get(code) or "").lower():
            continue
        navs = [n for _, n in pts]
        dates = [d for d, _ in pts]
        n = len(navs)
        if n < 30:
            continue
        brks = [
            (dates[i], f, i)
            for i in range(1, n)
            if navs[i - 1] and navs[i - 1] > 0 and (f := _scale_break_factor(navs[i] / navs[i - 1]))
        ]
        if not brks:
            continue
        # Bring every pre-break segment to the latest scale, then sanity-check whole-history CAGR.
        bset = {i: f for _, f, i in brks}
        cum, fac = 1.0, [1.0] * n
        for j in range(n - 1, -1, -1):
            if (j + 1) in bset:
                cum *= bset[j + 1]
            fac[j] = cum
        corr0 = navs[0] * fac[0]
        yrs = (dates[-1] - dates[0]).days / 365.25
        cagr = (navs[-1] / corr0) ** (1 / yrs) - 1 if yrs > 0 and corr0 > 0 else 0.0
        if not (-0.10 <= cagr <= 0.60):
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


def last_nav_date_by_name(scheme_names: list[str]) -> dict:
    """Map name → most-recent NAV date in mf_nav. Drives the sync service's incremental updates."""
    if not scheme_names:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, func.max(MfNav.date))
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .where(col(AmfiScheme.scheme_name).in_(scheme_names))
            .group_by(AmfiScheme.scheme_name)
        ).all()
    return {r[0]: r[1] for r in rows if r[1] is not None}
