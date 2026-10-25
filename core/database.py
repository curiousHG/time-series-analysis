"""SQLModel engine and session factory."""

import logging

from sqlmodel import Session, SQLModel, create_engine

from core.constants import DATABASE_URL
from core.timing import timed

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema() -> None:
    """Create missing tables on startup (CREATE only, never ALTER). Schema deltas are Alembic's job."""
    with timed("init_schema.import_models"):
        import core.models  # noqa: F401,PLC0415

    with timed("init_schema.create_all"):
        SQLModel.metadata.create_all(engine)
    logger.debug("Database schema initialized (run 'alembic upgrade head' for schema migrations)")
