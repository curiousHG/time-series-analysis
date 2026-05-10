"""Hand-rolled DB migrations.

These exist for two reasons that `SQLModel.metadata.create_all` can't handle on its own:

1. Schema drift — columns added to a model after the table was first created.
   `create_all` only creates *missing* tables; it never ALTERs existing ones.

2. Operations outside SQLModel's vocabulary — `CREATE EXTENSION pg_trgm`, GIN
   index builds, `ALTER COLUMN TYPE`.

Every migration must be **idempotent**: re-running it on an already-migrated DB is
a no-op (or strictly safe). Once a migration has been applied to every known
environment and is no longer relevant to fresh deployments, delete it.

Run with: `uv run python scripts/migrate.py`
"""

from migrations.runner import run_all

__all__ = ["run_all"]
