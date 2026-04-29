"""Mutual fund registry repository — scheme name/slug registry."""

import logging

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import MfRegistry

logger = logging.getLogger("data.repositories.registry")


def make_slug(name: str) -> str:
    return "-".join(c.strip("-") for c in name.replace("(", " ").replace(")", " ").split() if c and c != "-").lower()


def load_registry() -> pl.DataFrame:
    with get_session() as session:
        rows = session.exec(select(MfRegistry).order_by(col(MfRegistry.scheme_name))).all()
    if not rows:
        return pl.DataFrame(schema={"schemeName": pl.Utf8, "schemeSlug": pl.Utf8, "source": pl.Utf8})
    return pl.DataFrame(
        {
            "schemeName": [r.scheme_name for r in rows],
            "schemeSlug": [r.scheme_slug for r in rows],
            "source": [r.source for r in rows],
        }
    )


def save_to_registry(names: list[str]):
    if not names:
        return
    with get_session() as session:
        for name in names:
            stmt = (
                pg_insert(MfRegistry)
                .values(scheme_name=name, scheme_slug=make_slug(name), source="advisorkhoj")
                .on_conflict_do_nothing(index_elements=["scheme_name"])
            )
            session.exec(stmt)
        session.commit()
    logger.info("Saved %d schemes to registry", len(names))
