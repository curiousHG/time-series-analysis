"""Run hand-rolled DB migrations.

Migrations are NOT run on every Streamlit boot — they include heavy operations
(ALTER COLUMN TYPE rewrites whole tables, UPDATE backfills full-scan mf_nav, GIN
index builds). Running them on each app start added ~140s to cold load.

Run this manually after pulling model changes:
    uv run python scripts/migrate.py

It is idempotent — safe to run multiple times. Per-step timings land in logs/perf.log.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.logging_config import setup_logging  # noqa: E402
from migrations import run_all  # noqa: E402


def main() -> int:
    setup_logging()
    log = logging.getLogger("scripts.migrate")
    log.info("Starting migrations…")
    try:
        run_all()
    except Exception:
        log.exception("Migrations failed")
        return 1
    log.info("Migrations finished. See logs/perf.log for per-step timings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
