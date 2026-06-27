"""Compute and persist mutual-fund risk/return metrics into mf_scheme_metrics.

Use as a manual recompute (after a model change), as a daily cron, or for ad-hoc backfills:

    # Default: recompute only stale schemes (latest NAV > computed_at_nav_date)
    uv run python scripts/compute_metrics.py

    # Force a full recompute across every scheme with NAV history
    uv run python scripts/compute_metrics.py --all

    # Specific schemes (comma-separated)
    uv run python scripts/compute_metrics.py --schemes "Fund A Direct Growth,Fund B Direct Growth"

    # Strip spurious NAV spikes/zeros from mf_nav first, then full recompute
    uv run python scripts/compute_metrics.py --repair --all

Per-step timings land in logs/perf.log. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to sys.path so `python scripts/compute_metrics.py` resolves package imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.logging_config import setup_logging  # noqa: E402
from data.repositories.nav import repair_nav_glitches, repair_nav_scale_breaks  # noqa: E402
from services.mf_metrics import recompute_metrics, recompute_stale_metrics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--all", action="store_true", help="Full recompute across every scheme with NAV.")
    grp.add_argument("--stale", action="store_true", help="Only schemes whose NAV is newer than the cache (default).")
    grp.add_argument("--schemes", type=str, help="Comma-separated scheme_name list to recompute.")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="First strip spurious NAV spikes/zeros from mf_nav (data-sanctity cleanup) before recomputing.",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default 4).")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("scripts.compute_metrics")

    if args.repair:
        g = repair_nav_glitches()
        log.info("NAV repair: removed %d glitch row(s) across %d scheme(s).", g["rows_removed"], g["schemes_affected"])
        sb = repair_nav_scale_breaks()
        log.info(
            "NAV repair: rescaled %d scale-break row(s) across %d fund(s) (%d skipped).",
            sb["rows_rescaled"],
            sb["funds_rescaled"],
            len(sb["skipped"]),
        )

    if args.all:
        log.info("Recomputing metrics for ALL schemes (this may take a while)…")
        n = recompute_metrics(scheme_names=None, max_workers=args.workers)
    elif args.schemes:
        names = [s.strip() for s in args.schemes.split(",") if s.strip()]
        log.info("Recomputing metrics for %d explicitly listed scheme(s)…", len(names))
        n = recompute_metrics(scheme_names=names, max_workers=args.workers)
    else:
        log.info("Recomputing metrics for stale schemes…")
        n = recompute_stale_metrics(max_workers=args.workers)

    log.info("Done — upserted %d row(s). See logs/perf.log for timings.", n)
    return 0 if n is not None else 1


if __name__ == "__main__":
    sys.exit(main())
