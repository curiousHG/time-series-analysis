"""MF registry repository — tracked funds and their per-source statuses, keyed on scheme_code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, select

from core.database import get_session
from core.enums import SourceStatus
from core.models import (
    AmfiScheme,
    MfAssetAllocation,
    MfHolding,
    MfMetadata,
    MfNav,
    MfRegistry,
    MfSectorAllocation,
)


@dataclass(frozen=True, slots=True)
class RegistryStatuses:
    """Per-source fetch statuses of one registry row."""

    nav_status: SourceStatus
    holdings_status: SourceStatus
    metadata_status: SourceStatus


def load_tracked_rows() -> list[tuple]:
    """Registry rows joined to amfi_schemes, ordered by scheme name: name, code, nav/holdings/metadata status,
    added_at, last_attempted_at, AMFI nav_date, advisorkhoj_slug, AMFI nav."""
    with get_session() as session:
        return list(
            session.exec(
                select(
                    AmfiScheme.scheme_name,
                    MfRegistry.scheme_code,
                    MfRegistry.nav_status,
                    MfRegistry.holdings_status,
                    MfRegistry.metadata_status,
                    MfRegistry.added_at,
                    MfRegistry.last_attempted_at,
                    AmfiScheme.nav_date,
                    MfRegistry.advisorkhoj_slug,
                    AmfiScheme.nav,
                )
                .join(AmfiScheme, MfRegistry.scheme_code == AmfiScheme.scheme_code)
                .order_by(AmfiScheme.scheme_name)
            ).all()
        )


def get_registry_statuses(scheme_code: int) -> RegistryStatuses | None:
    """Source statuses of one registry row, or None if the fund is not registered."""
    with get_session() as session:
        row = session.get(MfRegistry, scheme_code)
        if row is None:
            return None
        return RegistryStatuses(row.nav_status, row.holdings_status, row.metadata_status)


def set_registry_statuses(scheme_code: int, **statuses: SourceStatus) -> None:
    """Set the given status columns and stamp last_attempted_at; no-op if the fund is not registered."""
    with get_session() as session:
        row = session.get(MfRegistry, scheme_code)
        if row is None:
            return
        for k, v in statuses.items():
            setattr(row, k, v)
        row.last_attempted_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(row)
        session.commit()


def save_registry_row(scheme_code: int) -> None:
    """Insert a `pending` registry row; an existing row is left untouched."""
    with get_session() as session:
        stmt = (
            pg_insert(MfRegistry)
            .values(
                scheme_code=scheme_code,
                nav_status=SourceStatus.PENDING,
                holdings_status=SourceStatus.PENDING,
                metadata_status=SourceStatus.PENDING,
                added_at=datetime.now(UTC).replace(tzinfo=None),
            )
            .on_conflict_do_nothing(index_elements=["scheme_code"])
        )
        session.exec(stmt)
        session.commit()


def delete_fund(scheme_code: int) -> None:
    """Delete a fund's NAV, metadata, holdings, sector/asset allocation and registry rows in one transaction."""
    with get_session() as session:
        session.exec(delete(MfNav).where(col(MfNav.scheme_code) == scheme_code))
        session.exec(delete(MfMetadata).where(col(MfMetadata.scheme_code) == scheme_code))
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_code) == scheme_code))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code) == scheme_code))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code) == scheme_code))
        session.exec(delete(MfRegistry).where(col(MfRegistry.scheme_code) == scheme_code))
        session.commit()


def reconcile_registry_statuses() -> int:
    """Mark a source `available` for every registry row that has data for it; returns rows changed."""
    with get_session() as session:
        regs = session.exec(select(MfRegistry)).all()
        nav_codes = set(session.exec(select(MfNav.scheme_code).distinct()).all())
        meta_codes = set(session.exec(select(MfMetadata.scheme_code)).all())
        codes_with_holdings = set(session.exec(select(MfHolding.scheme_code).distinct()).all())

        updated = 0
        for r in regs:
            nav_s = SourceStatus.AVAILABLE if r.scheme_code in nav_codes else r.nav_status
            hold_s = SourceStatus.AVAILABLE if r.scheme_code in codes_with_holdings else r.holdings_status
            meta_s = SourceStatus.AVAILABLE if r.scheme_code in meta_codes else r.metadata_status
            if (nav_s, hold_s, meta_s) != (r.nav_status, r.holdings_status, r.metadata_status):
                r.nav_status = nav_s
                r.holdings_status = hold_s
                r.metadata_status = meta_s
                session.add(r)
                updated += 1
        session.commit()
    return updated
