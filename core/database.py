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
    """Create any missing tables on startup (first-run table creation only).

    `create_all` owns first-time table creation: it is idempotent, fast (~60ms), and only
    ever CREATEs *missing* tables — it never ALTERs existing ones. Schema **deltas**
    (added columns, type widenings, extensions/indexes) are Alembic's job: run
    `uv run alembic upgrade head` after pulling model changes (see CLAUDE.md). Deltas are
    kept out of boot because they include heavy operations (ALTER COLUMN TYPE, GIN index
    builds) that can take minutes.
    """
    with timed("init_schema.import_models"):
        import core.models  # noqa: F401,PLC0415 — core.models imports core.database; deferred to avoid a cycle

    with timed("init_schema.create_all"):
        SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized (run 'alembic upgrade head' for schema migrations)")
