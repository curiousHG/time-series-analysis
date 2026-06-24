"""SQLModel engine and session factory."""

import logging

from sqlmodel import Session, SQLModel, create_engine

from core.constants import DATABASE_URL
from core.timing import timed

logger = logging.getLogger("data.store.database")

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema() -> None:
    """Create missing tables on startup. `create_all` only CREATEs missing tables, never ALTERs.

    Schema deltas (columns, type widenings, indexes) are Alembic's job — run
    `uv run alembic upgrade head` after model changes (see CLAUDE.md). Kept out of boot
    because deltas include slow ops (ALTER COLUMN TYPE, GIN index builds).
    """
    with timed("init_schema.import_models"):
        import core.models  # noqa: F401,PLC0415 — core.models imports core.database; deferred to avoid a cycle

    with timed("init_schema.create_all"):
        SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized (run 'alembic upgrade head' for schema migrations)")
