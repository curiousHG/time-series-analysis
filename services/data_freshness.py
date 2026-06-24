"""Data freshness service — detects stale NAV and holdings data."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import numpy as np
import polars as pl
from sqlmodel import func, select

from core.database import get_session
from core.models import AmfiScheme, MfHolding, MfNav
from data.repositories.holdings import _slug_to_code_map_cached
from mutual_funds.display import make_slug
from services.constants import HOLDINGS_STALE_DAYS, NAV_STALE_BUSINESS_DAYS, FreshnessStatus


@dataclass
class FreshnessRow:
    scheme_name: str
    slug: str
    last_date: date | None
    days_old: int | None
    business_days_old: int | None
    status: FreshnessStatus


@dataclass
class FreshnessReport:
    current_date: date
    rows: list[FreshnessRow]
    stale_count: int
    total: int
    max_days_old: int | None
    max_business_days_old: int | None

    @property
    def has_stale(self) -> bool:
        return self.stale_count > 0


def _busdays_between(start: date, end: date) -> int:
    return int(np.busday_count(start, end))


def _build_report(
    current_date: date,
    pairs: list[tuple[str, str]],
    last_date_by_key: dict[str, date],
    key_kind: Literal["name", "slug"],
    threshold_days: int,
    use_business_days: bool,
) -> FreshnessReport:
    rows: list[FreshnessRow] = []
    stale_count = 0
    max_days = None
    max_bdays = None

    for name, slug in pairs:
        key = name if key_kind == "name" else slug
        last = last_date_by_key.get(key)
        if last is None:
            rows.append(
                FreshnessRow(
                    scheme_name=name,
                    slug=slug,
                    last_date=None,
                    days_old=None,
                    business_days_old=None,
                    status="missing",
                )
            )
            stale_count += 1
            continue

        days = (current_date - last).days
        bdays = _busdays_between(last, current_date) if use_business_days else None
        compare = bdays if bdays is not None else days
        is_stale = compare > threshold_days
        status: FreshnessStatus = "stale" if is_stale else "fresh"

        rows.append(
            FreshnessRow(
                scheme_name=name,
                slug=slug,
                last_date=last,
                days_old=days,
                business_days_old=bdays,
                status=status,
            )
        )
        if is_stale:
            stale_count += 1
            max_days = days if max_days is None else max(max_days, days)
            if bdays is not None:
                max_bdays = bdays if max_bdays is None else max(max_bdays, bdays)

    return FreshnessReport(
        current_date=current_date,
        rows=rows,
        stale_count=stale_count,
        total=len(pairs),
        max_days_old=max_days,
        max_business_days_old=max_bdays,
    )


def compute_nav_freshness(scheme_names: list[str], scheme_slugs: list[str]) -> FreshnessReport:
    """Report NAV freshness per scheme. Stale = > NAV_STALE_BUSINESS_DAYS business days behind today."""
    today = datetime.now().date()
    last_by_name: dict[str, date] = {}

    if scheme_names:
        # Phase 2: MfNav is keyed on scheme_code; JOIN to amfi_schemes for the name.
        with get_session() as session:
            stmt = (
                select(AmfiScheme.scheme_name, func.max(MfNav.date))
                .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
                .where(AmfiScheme.scheme_name.in_(scheme_names))
                .group_by(AmfiScheme.scheme_name)
            )
            for row in session.exec(stmt).all():
                last_by_name[row[0]] = row[1]

    pairs = list(zip(scheme_names, scheme_slugs, strict=False))
    return _build_report(
        current_date=today,
        pairs=pairs,
        last_date_by_key=last_by_name,
        key_kind="name",
        threshold_days=NAV_STALE_BUSINESS_DAYS,
        use_business_days=True,
    )


def compute_holdings_freshness(scheme_names: list[str], scheme_slugs: list[str]) -> FreshnessReport:
    """Report holdings freshness per scheme. Stale = > HOLDINGS_STALE_DAYS calendar days behind today."""
    today = datetime.now().date()
    last_by_slug: dict[str, date] = {}

    if scheme_slugs:
        # Phase 3: holdings is keyed on scheme_code; resolve slug → code, query, then
        # remap back to slug for the report.
        slug_to_code = _slug_to_code_map_cached()
        code_to_slug = {slug_to_code[s]: s for s in scheme_slugs if s in slug_to_code}
        codes = list(code_to_slug.keys())
        if codes:
            with get_session() as session:
                stmt = (
                    select(MfHolding.scheme_code, func.max(MfHolding.portfolio_date))
                    .where(MfHolding.scheme_code.in_(codes))
                    .group_by(MfHolding.scheme_code)
                )
                for row in session.exec(stmt).all():
                    if row[1] is not None and row[0] in code_to_slug:
                        last_by_slug[code_to_slug[row[0]]] = row[1]

    pairs = list(zip(scheme_names, scheme_slugs, strict=False))
    return _build_report(
        current_date=today,
        pairs=pairs,
        last_date_by_key=last_by_slug,
        key_kind="slug",
        threshold_days=HOLDINGS_STALE_DAYS,
        use_business_days=False,
    )


# ---- Settings-page status table builders --------------------------------------------------
#
# These shape the (Fund, Records, First/Last Date, Days Old, Status) table the Refresh
# section renders. Pure data — no Streamlit imports — so the UI stays rendering-only.


def build_nav_status_rows(
    report: FreshnessReport,
    nav_df: pl.DataFrame,
    short_by_name: dict[str, str],
) -> list[dict]:
    """One dict per fund: Fund · Records · First Date · Last Date · Days Old · Status."""
    rows: list[dict] = []
    for r in report.rows:
        scheme_nav = nav_df.filter(pl.col("schemeName") == r.scheme_name)
        first_date = str(scheme_nav.select("date").to_series().min()) if scheme_nav.height > 0 else "-"
        rows.append(
            {
                "Fund": short_by_name.get(r.scheme_name, r.scheme_name),
                "Records": scheme_nav.height,
                "First Date": first_date,
                "Last Date": str(r.last_date) if r.last_date else "-",
                "Days Old": r.days_old,
                "Status": r.status.capitalize(),
            }
        )
    return rows


def build_holdings_status_rows(
    report: FreshnessReport,
    holdings_df: pl.DataFrame,
    short_by_name: dict[str, str],
) -> list[dict]:
    """One dict per fund: Fund · Holdings Count · Last Portfolio Date · Days Old · Status."""
    rows: list[dict] = []
    for r in report.rows:
        slug = make_slug(r.scheme_name)
        scheme_holdings = holdings_df.filter(pl.col("schemeSlug") == slug)
        rows.append(
            {
                "Fund": short_by_name.get(r.scheme_name, r.scheme_name),
                "Holdings Count": scheme_holdings.height,
                "Last Portfolio Date": str(r.last_date) if r.last_date else "-",
                "Days Old": r.days_old,
                "Status": r.status.capitalize(),
            }
        )
    return rows
