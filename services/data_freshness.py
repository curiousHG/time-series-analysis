"""Data freshness service — detects stale NAV and holdings data."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import numpy as np
from sqlmodel import func, select

from core.database import get_session
from core.models import MfHolding, MfNav

NAV_STALE_BUSINESS_DAYS = 1
HOLDINGS_STALE_DAYS = 35

Status = Literal["fresh", "stale", "missing"]


@dataclass
class FreshnessRow:
    scheme_name: str
    slug: str
    last_date: date | None
    days_old: int | None
    business_days_old: int | None
    status: Status


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
        compare = bdays if use_business_days else days
        is_stale = compare > threshold_days
        status: Status = "stale" if is_stale else "fresh"

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
        with get_session() as session:
            stmt = (
                select(MfNav.scheme_name, func.max(MfNav.date))
                .where(MfNav.scheme_name.in_(scheme_names))
                .group_by(MfNav.scheme_name)
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
        with get_session() as session:
            stmt = (
                select(MfHolding.scheme_slug, func.max(MfHolding.portfolio_date))
                .where(MfHolding.scheme_slug.in_(scheme_slugs))
                .group_by(MfHolding.scheme_slug)
            )
            for row in session.exec(stmt).all():
                if row[1] is not None:
                    last_by_slug[row[0]] = row[1]

    pairs = list(zip(scheme_names, scheme_slugs, strict=False))
    return _build_report(
        current_date=today,
        pairs=pairs,
        last_date_by_key=last_by_slug,
        key_kind="slug",
        threshold_days=HOLDINGS_STALE_DAYS,
        use_business_days=False,
    )
