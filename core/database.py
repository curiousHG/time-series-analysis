"""SQLModel engine and session factory."""

import logging
import os

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("data.store.database")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema():
    """Create all tables from SQLModel models, then run lightweight migrations."""
    import core.models  # noqa: F401 — registers models with SQLModel metadata

    SQLModel.metadata.create_all(engine)
    _run_migrations()
    logger.info("Database schema initialized")


def _run_migrations():
    """Hand-rolled migrations: add new mf_registry columns, drop legacy ones, drop fund_mapping."""
    with engine.begin() as conn:
        # mf_registry: add new columns
        conn.execute(text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS scheme_code INTEGER"))
        conn.execute(
            text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS nav_status TEXT NOT NULL DEFAULT 'pending'")
        )
        conn.execute(
            text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS holdings_status TEXT NOT NULL DEFAULT 'pending'")
        )
        conn.execute(
            text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS metadata_status TEXT NOT NULL DEFAULT 'pending'")
        )
        conn.execute(text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS added_at TIMESTAMP NOT NULL DEFAULT NOW()"))
        conn.execute(text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS last_attempted_at TIMESTAMP"))

        # mf_registry: backfill from existing data tables
        conn.execute(
            text(
                """
                UPDATE mf_registry r SET nav_status = 'available'
                WHERE EXISTS (SELECT 1 FROM mf_nav n WHERE n.scheme_name = r.scheme_name)
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE mf_registry r SET metadata_status = 'available'
                WHERE EXISTS (SELECT 1 FROM mf_metadata m WHERE m.scheme_name = r.scheme_name)
                """
            )
        )
        # holdings keyed by slug; backfill by joining via scheme_slug if still present, else skip.
        if _column_exists(conn, "mf_registry", "scheme_slug"):
            conn.execute(
                text(
                    """
                    UPDATE mf_registry r SET holdings_status = 'available'
                    WHERE EXISTS (SELECT 1 FROM mf_holdings h WHERE h.scheme_slug = r.scheme_slug)
                    """
                )
            )

        # backfill scheme_code from amfi_schemes by name
        conn.execute(
            text(
                """
                UPDATE mf_registry r SET scheme_code = a.scheme_code
                FROM amfi_schemes a
                WHERE a.scheme_name = r.scheme_name AND r.scheme_code IS NULL
                """
            )
        )

        # mf_registry: drop legacy columns
        conn.execute(text("ALTER TABLE mf_registry DROP COLUMN IF EXISTS scheme_slug"))
        conn.execute(text("ALTER TABLE mf_registry DROP COLUMN IF EXISTS short_name"))
        conn.execute(text("ALTER TABLE mf_registry DROP COLUMN IF EXISTS source"))

        # drop fund_mapping table
        conn.execute(text("DROP TABLE IF EXISTS fund_mapping"))

        # stock_ohlcv.volume: widen to BIGINT — index volumes overflow 32-bit INTEGER (~2.1B max).
        conn.execute(text("ALTER TABLE stock_ohlcv ALTER COLUMN volume TYPE BIGINT"))

        # Enable pg_trgm + GIN index for fuzzy scheme-name search
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_amfi_scheme_name_trgm "
                    "ON amfi_schemes USING gin (scheme_name gin_trgm_ops)"
                )
            )
        except Exception as e:
            logger.warning("pg_trgm setup skipped: %s", e)

    logger.info("Migrations complete (mf_registry reshaped, fund_mapping dropped, pg_trgm enabled)")


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None
