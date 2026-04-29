"""SQLModel engine and session factory."""

import logging
import os

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("data.store.database")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema():
    """Create all tables from SQLModel models."""
    import core.models  # noqa: F401 — registers models with SQLModel metadata

    SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized")
