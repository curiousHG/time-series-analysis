"""Scheme name → scheme_code resolution + synthetic-code minting.

Single source of truth for turning `scheme_name` into `amfi_schemes.scheme_code`,
including the synthetic-negative minting used when a tracked fund has no AMFI master
row yet (segregated portfolios, brand-new funds whose AMFI row hasn't synced). AMFI
never issues negative codes, so the synthetic and real namespaces never collide.

This replaces four near-identical resolvers that had drifted apart:
`nav._resolve_codes`, `scheme_metrics._resolve_codes_with_synthetic`,
`registry_service._resolve_or_mint_code`, plus the inline mint block in
`metadata.save_metadata`.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme
from data.repositories.holdings import clear_slug_cache

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger("data.repositories.scheme_codes")


def _utcnow_naive() -> dt.datetime:
    """Naive UTC timestamp matching the TIMESTAMP-without-tz `db_added_at` column."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def resolve_codes(scheme_names: list[str]) -> dict[str, int]:
    """Return {name: scheme_code} via amfi_schemes for names that already exist."""
    if not scheme_names:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, AmfiScheme.scheme_code).where(col(AmfiScheme.scheme_name).in_(scheme_names))
        ).all()
    return {r[0]: r[1] for r in rows}


def mint_synthetic_codes(session: Session, names: list[str]) -> dict[str, int]:
    """Insert synthetic-negative amfi_schemes rows for `names` within the caller's session.

    Does NOT commit and does NOT clear the slug cache — the caller owns the transaction
    so the mint can share it with the caller's other writes. Returns {name: minted_code}.
    """
    if not names:
        return {}
    min_code = session.exec(select(func.min(AmfiScheme.scheme_code))).one() or 0
    next_neg = min(min_code, 0) - 1
    out: dict[str, int] = {}
    for name in names:
        session.exec(
            pg_insert(AmfiScheme)
            .values(scheme_code=next_neg, scheme_name=name, db_added_at=_utcnow_naive())
            .on_conflict_do_nothing(index_elements=["scheme_code"])
        )
        out[name] = next_neg
        next_neg -= 1
    return out


def resolve_codes_with_synthetic(scheme_names: list[str]) -> dict[str, int]:
    """{name: scheme_code}; mints synthetic negatives for any name not in amfi_schemes.

    Commits the mint and clears the slug → scheme_code cache so newly minted schemes are
    visible to holdings save/load immediately.
    """
    if not scheme_names:
        return {}
    out = resolve_codes(scheme_names)
    missing = [n for n in scheme_names if n not in out]
    if missing:
        with get_session() as session:
            out.update(mint_synthetic_codes(session, missing))
            session.commit()
        clear_slug_cache()
        logger.warning("Minted %d synthetic scheme code(s); e.g. %s", len(missing), missing[:3])
    return out


def resolve_or_mint_code(scheme_name: str) -> int:
    """Resolve a single scheme_name to its code, minting a synthetic negative if absent."""
    codes = resolve_codes([scheme_name])
    if scheme_name in codes:
        return codes[scheme_name]
    with get_session() as session:
        code = mint_synthetic_codes(session, [scheme_name])[scheme_name]
        session.commit()
    clear_slug_cache()
    logger.warning("Minted synthetic code %d for previously-unknown scheme %s", code, scheme_name)
    return code
