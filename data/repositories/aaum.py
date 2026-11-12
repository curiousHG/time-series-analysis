"""AMFI average-AUM repository (mf_aaum): DB-first period sync plus AUM-with-NAV reads for flow estimation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import MfAaum
from data.constants import AMFI_AAUM_FLOOR_YEAR
from data.fetchers.amfi_aaum import AAUM_SCHEMA, fetch_aaum_period, fetch_aaum_periods

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import date

logger = logging.getLogger(__name__)

_UPSERT_CHUNK = 5000


def get_aaum_period_ends() -> set[date]:
    """Disclosure periods already stored."""
    with get_session() as session:
        return set(session.exec(select(col(MfAaum.period_end)).distinct()).all())


def save_aaum(frame: pl.DataFrame) -> int:
    """Upsert AAUM_SCHEMA rows into mf_aaum; returns rows written."""
    if frame.is_empty():
        return 0
    rows = frame.select(list(AAUM_SCHEMA)).to_dicts()
    with get_session() as session:
        for i in range(0, len(rows), _UPSERT_CHUNK):
            stmt = pg_insert(MfAaum).values(rows[i : i + _UPSERT_CHUNK])
            stmt = stmt.on_conflict_do_update(
                index_elements=["scheme_code", "period_end"],
                set_={
                    c: stmt.excluded[c]
                    for c in ("scheme_name", "aaum_lakhs", "fof_domestic_lakhs", "category", "fund_house")
                },
            )
            session.exec(stmt)
        session.commit()
    return len(rows)


def sync_aaum(
    since_year: int = AMFI_AAUM_FLOOR_YEAR, *, progress_cb: Callable[[int, int, str], None] | None = None
) -> int:
    """Fetch every AMFI disclosure period from `since_year` that mf_aaum lacks; returns rows saved.

    Published periods are final, so stored periods are never re-fetched.
    """
    have = get_aaum_period_ends()
    missing = [p for p in fetch_aaum_periods(since_year) if p.period_end not in have]
    saved = 0
    for i, period in enumerate(missing, start=1):
        if progress_cb:
            progress_cb(i, len(missing), period.label)
        saved += save_aaum(fetch_aaum_period(period))
    logger.info("AMFI AAUM sync: %d new period(s), %d rows", len(missing), saved)
    return saved


def load_aaum(scheme_codes: Iterable[int] | None = None) -> pl.DataFrame:
    """Stored AAUM rows (AAUM_SCHEMA), optionally limited to `scheme_codes`, ordered by scheme then period."""
    stmt = select(MfAaum)
    if scheme_codes is not None:
        stmt = stmt.where(col(MfAaum.scheme_code).in_(list(scheme_codes)))
    with get_session() as session:
        rows = session.exec(stmt.order_by(col(MfAaum.scheme_code), col(MfAaum.period_end))).all()
    return pl.DataFrame([r.model_dump() for r in rows], schema=AAUM_SCHEMA)


_AAUM_WITH_NAV_SQL = """
with periods as (
    select period_end,
           lag(period_end) over (order by period_end) as prev_end
    from (select distinct period_end from mf_aaum) p
),
nav_avg as (
    select a.scheme_code, a.period_end, avg(n.nav) as avg_nav
    from mf_aaum a
    join periods p on p.period_end = a.period_end
    join mf_nav n on n.scheme_code = a.scheme_code
                 and n.date <= a.period_end
                 and n.date > coalesce(p.prev_end, a.period_end - interval '3 months')
    {where}
    group by a.scheme_code, a.period_end
)
select a.scheme_code, a.period_end, a.scheme_name, a.aaum_lakhs + a.fof_domestic_lakhs as aum_lakhs,
       a.category, a.fund_house, v.avg_nav
from mf_aaum a
left join nav_avg v on v.scheme_code = a.scheme_code and v.period_end = a.period_end
{where}
order by a.scheme_code, a.period_end
"""


def load_aaum_with_nav(scheme_codes: Iterable[int] | None = None) -> pl.DataFrame:
    """AAUM per scheme-period with that period's average NAV (null where mf_nav has no history)."""
    codes = sorted(set(scheme_codes)) if scheme_codes is not None else None
    where = "where a.scheme_code = any(:codes)" if codes is not None else ""
    sql = text(_AAUM_WITH_NAV_SQL.format(where=where))
    with get_session() as session:
        result = session.connection().execute(sql, {"codes": codes} if codes is not None else {})
        rows = [dict(r._mapping) for r in result]
    return pl.DataFrame(
        rows,
        schema={
            "scheme_code": pl.Int64,
            "period_end": pl.Date,
            "scheme_name": pl.Utf8,
            "aum_lakhs": pl.Float64,
            "category": pl.Utf8,
            "fund_house": pl.Utf8,
            "avg_nav": pl.Float64,
        },
    )
