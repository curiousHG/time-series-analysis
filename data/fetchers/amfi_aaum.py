"""AMFI scheme-wise average AUM: the disclosure calendar and one period's per-scheme figures (₹ lakhs)."""

from __future__ import annotations

import calendar
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import httpx
import polars as pl

from data.constants import AMFI_AAUM_SCHEMEWISE_URL, AMFI_REQUEST_INTERVAL_S, BROWSER_USER_AGENT
from data.exceptions import SourceThrottled, UpstreamFormatError
from data.fetchers.schemas import AmfiAaumData, AmfiAaumPeriods, AmfiAaumYears, validate_upstream

logger = logging.getLogger(__name__)

_SOURCE = "AMFI AAUM"
_SCOPE = {"strType": "Categorywise", "MF_ID": 0}
_BACKOFF_SECONDS = (2.0, 4.0, 8.0, 16.0, 32.0)
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_PERIOD_RE = re.compile(r"([A-Za-z]+)\s+(\d{4})\s*$")

AAUM_SCHEMA = {
    "scheme_code": pl.Int64,
    "period_end": pl.Date,
    "scheme_name": pl.Utf8,
    "aaum_lakhs": pl.Float64,
    "fof_domestic_lakhs": pl.Float64,
    "category": pl.Utf8,
    "fund_house": pl.Utf8,
}


@dataclass(frozen=True)
class AaumPeriod:
    """One AMFI disclosure period: the API ids that address it and the last day it covers."""

    fy_id: int
    period_id: int
    label: str
    period_end: date


def parse_period_end(label: str) -> date:
    """Last calendar day of the final month in an AMFI period label ("July - September 2026", "March 2007")."""
    m = _PERIOD_RE.search(label.strip())
    month = _MONTHS.get(m.group(1).lower()) if m else None
    if not m or month is None:
        raise UpstreamFormatError(_SOURCE, f"unrecognised period label {label!r}")
    year = int(m.group(2))
    return date(year, month, calendar.monthrange(year, month)[1])


def _get_json(client: httpx.Client, params: dict) -> object:
    """GET the AAUM endpoint; AMFI signals throttling with a 200 and a truncated body, so bad JSON is retried."""
    for backoff in (*_BACKOFF_SECONDS, None):
        time.sleep(AMFI_REQUEST_INTERVAL_S)
        resp = client.get(AMFI_AAUM_SCHEMEWISE_URL, params=params)
        if resp.status_code == httpx.codes.OK:
            try:
                return resp.json()
            except json.JSONDecodeError:
                pass
        if backoff is None:
            break
        logger.debug("AMFI AAUM %s answered %s; retrying in %.0fs", params, resp.status_code, backoff)
        time.sleep(backoff)
    raise SourceThrottled(_SOURCE, len(_BACKOFF_SECONDS) + 1)


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": BROWSER_USER_AGENT}, timeout=120)


def fetch_aaum_periods(since_year: int | None = None) -> list[AaumPeriod]:
    """Every disclosure period AMFI publishes, oldest first; `since_year` skips financial years starting earlier."""
    periods: list[AaumPeriod] = []
    with _client() as client:
        years = validate_upstream(_SOURCE, AmfiAaumYears, _get_json(client, _SCOPE)).data
        for fy in years:
            start_year = int(fy.financial_year.split()[1]) if len(fy.financial_year.split()) > 1 else 0
            if since_year is not None and start_year < since_year:
                continue
            listed = validate_upstream(_SOURCE, AmfiAaumPeriods, _get_json(client, {**_SCOPE, "fyId": fy.id}))
            periods.extend(AaumPeriod(fy.id, p.id, p.period, parse_period_end(p.period)) for p in listed.data.periods)
    return sorted(periods, key=lambda p: p.period_end)


def aaum_frame(payload: AmfiAaumData, period_end: date) -> pl.DataFrame:
    """Flatten one period's validated payload into AAUM_SCHEMA rows; a scheme listed twice keeps its largest figure."""
    rows = [
        {
            "scheme_code": s.scheme_code,
            "period_end": period_end,
            "scheme_name": s.scheme_name.strip(),
            "aaum_lakhs": s.amount.excl_fof_domestic or 0.0,
            "fof_domestic_lakhs": s.amount.fof_domestic or 0.0,
            "category": group.category,
            "fund_house": group.fund_house,
        }
        for group in payload.data
        for s in group.schemes
    ]
    frame = pl.DataFrame(rows, schema=AAUM_SCHEMA)
    return (
        frame.sort("aaum_lakhs", descending=True)
        .unique(subset=["scheme_code", "period_end"], keep="first")
        .sort("scheme_code")
    )


def fetch_aaum_period(period: AaumPeriod) -> pl.DataFrame:
    """Per-scheme average AUM for one disclosure period across every AMC, as AAUM_SCHEMA rows."""
    with _client() as client:
        raw = _get_json(client, {**_SCOPE, "fyId": period.fy_id, "periodId": period.period_id})
    payload = validate_upstream(_SOURCE, AmfiAaumData, raw)
    frame = aaum_frame(payload, period.period_end)
    logger.debug("AMFI AAUM %s: %d schemes", period.label, frame.height)
    return frame
