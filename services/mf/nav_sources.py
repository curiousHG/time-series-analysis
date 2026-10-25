"""NAV sourcing — chooses MFAPI vs AdvisorKhoj, persists the result and triggers metric recomputes."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
from sqlmodel import col, delete

from core.database import get_session
from core.models import MfNav
from core.timing import timeit
from data.exceptions import SourceThrottled
from data.fetchers.advisorkhoj import fetch_nav_from_advisorkhoj
from data.fetchers.mfapi import fetch_nav_from_mfapi, resolve_mfapi_code
from data.repositories.amfi import lookup_scheme_code_by_exact_name
from data.repositories.nav import load_nav_df, nav_json_to_df, save_nav_df, upsert_nav_rows
from data.repositories.scheme_codes import resolve_codes, resolve_codes_with_synthetic
from services.mf.metrics import recompute_metrics

logger = logging.getLogger(__name__)


def fetch_single_nav(scheme_name: str, scheme_code: int | None = None) -> pl.DataFrame:
    """Fetch NAV for one scheme from MFAPI, falling back to AdvisorKhoj; rows are tagged with the code used.

    `SourceThrottled` from MFAPI propagates. Pass `scheme_code` when known to avoid name-based sibling mix-ups.
    """
    known = scheme_code if scheme_code is not None else lookup_scheme_code_by_exact_name(scheme_name)
    tag: int | None = int(known) if known is not None else None
    if known is not None and int(known) <= 0:
        mfapi_code = None
    else:
        mfapi_code = str(known) if known is not None else resolve_mfapi_code(scheme_name)
    if mfapi_code:
        try:
            df = fetch_nav_from_mfapi(mfapi_code, scheme_name)
            return df.with_columns(pl.lit(tag, dtype=pl.Int64).alias("scheme_code"))
        except SourceThrottled:
            raise
        except Exception as e:
            logger.info("MFAPI failed for %s (code=%s): %s", scheme_name, mfapi_code, e)

    logger.debug("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    df = nav_json_to_df(data["nav_data"], scheme_name)
    return df.with_columns(pl.lit(scheme_code, dtype=pl.Int64).alias("scheme_code"))


@timeit("nav.fetch_nav_parallel")
def fetch_nav_parallel(scheme_names: list[str], name_to_code: dict[str, int] | None = None) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    codes = name_to_code or resolve_codes(scheme_names)
    new_frames = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {pool.submit(fetch_single_nav, scheme, codes.get(scheme)): scheme for scheme in scheme_names}
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)
    return new_frames


def _recompute_metrics_for(scheme_names: list[str]) -> None:
    """Best-effort metrics recompute; failures are logged, never break the NAV save."""
    if not scheme_names:
        return
    try:
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
    """Re-fetch and replace NAV; only schemes that fetch successfully are replaced (delete + insert in one
    transaction)."""
    new_frames = fetch_nav_parallel(scheme_names)
    fetched_schemes = [name for df in new_frames if df.height for name in df["schemeName"].unique().to_list()]
    if not fetched_schemes:
        return load_nav_df(scheme_names)

    name_to_code = resolve_codes_with_synthetic(fetched_schemes)
    codes = [name_to_code[n] for n in fetched_schemes if n in name_to_code]
    with get_session() as session:
        if codes:
            session.exec(delete(MfNav).where(col(MfNav.scheme_code).in_(codes)))
        for df in new_frames:
            if df.height:
                upsert_nav_rows(session, df, name_to_code)
        session.commit()

    _recompute_metrics_for(fetched_schemes)
    return load_nav_df(scheme_names)
