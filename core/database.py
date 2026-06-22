"""SQLModel engine and session factory."""

import logging
import os

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("data.store.database")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

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
    from core.timing import timed

    with timed("init_schema.import_models"):
        import core.models  # noqa: F401 — registers models with SQLModel metadata

    with timed("init_schema.create_all"):
        SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized (run 'alembic upgrade head' for schema migrations)")
