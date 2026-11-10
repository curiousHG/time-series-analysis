"""Data freshness service — detects stale NAV and holdings data."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

import numpy as np

from data.repositories.holdings import load_latest_holdings_dates, slug_to_code_map
from data.repositories.nav import last_nav_date_by_name
from services.constants import HOLDINGS_STALE_DAYS, NAV_STALE_BUSINESS_DAYS, FreshnessStatus


class FreshnessKey(StrEnum):
    NAME = "name"
    SLUG = "slug"


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


def business_days_between(start: date, end: date) -> int:
    """Trading-ish business days between two dates (weekdays; holidays not modelled)."""
    return int(np.busday_count(start, end))


def _build_report(
    current_date: date,
    pairs: list[tuple[str, str]],
    last_date_by_key: dict[str, date],
    key_kind: FreshnessKey,
    threshold_days: int,
    use_business_days: bool,
) -> FreshnessReport:
    rows: list[FreshnessRow] = []
    stale_count = 0
    max_days = None
    max_bdays = None

    for name, slug in pairs:
        key = name if key_kind == FreshnessKey.NAME else slug
        last = last_date_by_key.get(key)
        if last is None:
            rows.append(
                FreshnessRow(
                    scheme_name=name,
                    slug=slug,
                    last_date=None,
                    days_old=None,
                    business_days_old=None,
                    status=FreshnessStatus.MISSING,
                )
            )
            stale_count += 1
            continue

        days = (current_date - last).days
        bdays = business_days_between(last, current_date) if use_business_days else None
        compare = bdays if bdays is not None else days
        is_stale = compare > threshold_days
        status: FreshnessStatus = FreshnessStatus.STALE if is_stale else FreshnessStatus.FRESH

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
    last_by_name: dict[str, date] = last_nav_date_by_name(scheme_names)

    pairs = list(zip(scheme_names, scheme_slugs, strict=False))
    return _build_report(
        current_date=today,
        pairs=pairs,
        last_date_by_key=last_by_name,
        key_kind=FreshnessKey.NAME,
        threshold_days=NAV_STALE_BUSINESS_DAYS,
        use_business_days=True,
    )


def compute_holdings_freshness(scheme_names: list[str], scheme_slugs: list[str]) -> FreshnessReport:
    """Report holdings freshness per scheme. Stale = > HOLDINGS_STALE_DAYS calendar days behind today."""
    today = datetime.now().date()
    last_by_slug: dict[str, date] = {}

    if scheme_slugs:
        slug_to_code = slug_to_code_map()
        code_to_slug = {slug_to_code[s]: s for s in scheme_slugs if s in slug_to_code}
        codes = list(code_to_slug.keys())
        if codes:
            for code, latest in load_latest_holdings_dates(codes).items():
                if code in code_to_slug:
                    last_by_slug[code_to_slug[code]] = latest

    pairs = list(zip(scheme_names, scheme_slugs, strict=False))
    return _build_report(
        current_date=today,
        pairs=pairs,
        last_date_by_key=last_by_slug,
        key_kind=FreshnessKey.SLUG,
        threshold_days=HOLDINGS_STALE_DAYS,
        use_business_days=False,
    )


def build_nav_status_rows(
    report: FreshnessReport,
    nav_stats: dict[str, tuple[int, object]],
    short_by_name: dict[str, str],
) -> list[dict]:
    """One dict per fund: Fund · Records · First Date · Last Date · Days Old · Status.
    `nav_stats` maps scheme name → (record_count, first_date) — see nav.nav_record_stats."""
    rows: list[dict] = []
    for r in report.rows:
        count, first_date = nav_stats.get(r.scheme_name, (0, None))
        rows.append(
            {
                "Fund": short_by_name.get(r.scheme_name, r.scheme_name),
                "Records": count,
                "First Date": str(first_date) if first_date else "-",
                "Last Date": str(r.last_date) if r.last_date else "-",
                "Days Old": r.days_old,
                "Status": r.status.capitalize(),
            }
        )
    return rows


def build_holdings_status_rows(
    report: FreshnessReport,
    holdings_counts: dict[str, int],
    short_by_name: dict[str, str],
) -> list[dict]:
    """One dict per fund: Fund · Holdings Count · Last Portfolio Date · Days Old · Status.
    `holdings_counts` maps scheme name → holding-row count — see holdings.holdings_count_by_scheme."""
    return [
        {
            "Fund": short_by_name.get(r.scheme_name, r.scheme_name),
            "Holdings Count": holdings_counts.get(r.scheme_name, 0),
            "Last Portfolio Date": str(r.last_date) if r.last_date else "-",
            "Days Old": r.days_old,
            "Status": r.status.capitalize(),
        }
        for r in report.rows
    ]
