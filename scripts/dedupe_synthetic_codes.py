"""Merge synthetic negative scheme_codes into their real AMFI counterparts.

Phase 2 normalisation introduced synthetic negative `scheme_code` values for any
tracked fund whose `scheme_name` didn't match `amfi_schemes.scheme_name` exactly. In
practice ~95% of those mismatches are pure case drift (`Bharat 22 ETF` vs
`BHARAT 22 ETF`) — the fund DOES exist in AMFI master, just under a different casing.

This script does a `LOWER(name) = LOWER(name)` lookup for every synthetic-coded row,
and where a real positive code matches, re-keys all dependent rows (mf_nav,
mf_metadata, mf_scheme_metrics, mf_registry) from the synthetic to the real code, then
drops the synthetic `amfi_schemes` row.

Default mode is `--dry-run` (show proposed merges + row counts, no DB writes). Use
`--apply` to actually perform the merge.

    uv run python scripts/dedupe_synthetic_codes.py            # preview
    uv run python scripts/dedupe_synthetic_codes.py --apply    # commit

Idempotent — running twice is safe (second run finds zero merges).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import re  # noqa: E402

from sqlalchemy import text  # noqa: E402

from core.database import engine  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from data.repositories.holdings import clear_slug_cache  # noqa: E402
from mutual_funds.display import detect_option, detect_plan  # noqa: E402

# Tables that carry scheme_code FKs into amfi_schemes. mf_nav's composite PK (scheme_code,
# date) needs special handling — see _merge_pair.
_THIN_TABLES_SINGLE_PK = ("mf_metadata", "mf_scheme_metrics", "mf_registry")
_THIN_TABLES_COMPOSITE_PK = ("mf_nav",)


# Words to strip when computing a "stem" for fuzzy matching. AMFI's master names include
# explicit plan + option words; tracked names sometimes drop them, run them together, or
# wrap in parens. We strip everything plan/option-related so the residual stem matches
# regardless of formatting: '(Direct Plan)' / '- Regular -' / 'Direct Growth' / 'Regular Growth'
# all collapse to the same core "fund identity" string.
_PARENS_PLAN = re.compile(r"\(\s*(direct|regular|retail|institutional|wholesale)[^)]*\)", re.IGNORECASE)
_PLAN_WORDS = re.compile(r"\b(direct|regular|retail|institutional|wholesale)\b", re.IGNORECASE)
_PLAN_LITERAL = re.compile(r"\bplan\b", re.IGNORECASE)
_OPTION_WORDS = re.compile(
    r"\b(growth|idcw|dividend|bonus|reinvestment|payout"
    r"|monthly|quarterly|annual|half\s*yearly|fortnightly|daily)\b",
    re.IGNORECASE,
)
_OPTION_SUFFIX = re.compile(r"\b(option|payout|reinvestment)\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _stem(name: str) -> str:
    """Normalise a scheme name to its core fund identity stem.

    Strips parens-wrapped plan markers, bare plan words (Direct/Regular/etc), bare 'plan'
    and 'option' words, all option/dividend/payout words, then drops everything that isn't
    a-z0-9. Examples that collapse to identical stems:
      'Invesco India Arbitrage Fund - Regular Plan - Growth Option'
      'Invesco India Arbitrage Fund - Growth Option'
      'Invesco India Arbitrage Fund - Regular - Growth'
      'Invesco India Arbitrage Fund - Direct Growth'
      'Invesco India Arbitrage Fund(Regular Plan)Growth Option'
    """
    s = name.lower()
    s = _PARENS_PLAN.sub(" ", s)
    s = _OPTION_WORDS.sub(" ", s)
    s = _OPTION_SUFFIX.sub(" ", s)
    s = _PLAN_WORDS.sub(" ", s)
    s = _PLAN_LITERAL.sub(" ", s)
    s = _NON_WORD.sub("", s)
    return s


_NAV_TOLERANCE = 0.05  # 5% — anything beyond this is treated as "wrong fund, abort"


def _nav_matches(conn, syn_code: int, real_code: int) -> tuple[bool, float | None, float | None]:
    """Compare the synthetic's most-recent NAV to the AMFI master NAV for the real code.

    Returns (matches, our_nav, amfi_nav). `matches` is True iff |our_nav - amfi_nav| / amfi_nav
    is within `_NAV_TOLERANCE`. Returns (True, None, x) when we have no NAV history (zero-NAV
    orphan: nothing to verify, name match is the only signal so we trust it).
    """
    our_nav_row = conn.execute(
        text("SELECT nav FROM mf_nav WHERE scheme_code = :c ORDER BY date DESC LIMIT 1"),
        {"c": syn_code},
    ).first()
    amfi_nav_row = conn.execute(text("SELECT nav FROM amfi_schemes WHERE scheme_code = :c"), {"c": real_code}).first()
    our_nav = float(our_nav_row[0]) if our_nav_row and our_nav_row[0] is not None else None
    amfi_nav = float(amfi_nav_row[0]) if amfi_nav_row and amfi_nav_row[0] is not None else None
    if our_nav is None:
        return True, None, amfi_nav  # nothing to verify
    if amfi_nav is None or amfi_nav <= 0:
        return True, our_nav, amfi_nav  # AMFI has no NAV either; can't verify, trust name
    return abs(our_nav - amfi_nav) / amfi_nav <= _NAV_TOLERANCE, our_nav, amfi_nav


def _propose_merges(conn, *, prefer_regular: bool = False) -> list[tuple[int, str, int, str]]:
    """Return [(synthetic_code, synthetic_name, real_code, real_name), ...] candidates.

    Three-pass match + NAV sanity guard:
      1. LOWER(name) = LOWER(name)  — exact case-insensitive match.
      2. Stem + plan + option triple match (catches Phase-2 name-format drift).
      3. *If* `prefer_regular=True`: synthetics whose name has no plan word fall back to
         `Regular`. Indian-MF convention for short-form names from MFAPI / tradebook is
         the Regular plan; opt-in only because there's a small risk of mis-assigning a
         Direct fund whose user-facing name was abbreviated.

    Pass 2 / 3 drop the candidate if multiple AMFI rows share the same (stem, plan, option)
    (ambiguous → safer to skip).

    NAV guard: every candidate's most-recent stored NAV is compared to AMFI's NAV. If they
    diverge by more than `_NAV_TOLERANCE` (5%), the candidate is rejected — the name match
    was misleading (MFAPI fuzzy resolution returned a different fund).
    """
    # Pass 1 — exact case-insensitive match.
    pass1 = conn.execute(
        text(
            """
            SELECT s.scheme_code, s.scheme_name, r.scheme_code, r.scheme_name
            FROM amfi_schemes s
            JOIN amfi_schemes r
              ON r.scheme_code > 0 AND LOWER(r.scheme_name) = LOWER(s.scheme_name)
            WHERE s.scheme_code < 0
            ORDER BY s.scheme_code DESC
            """
        )
    ).all()
    # NAV sanity-check Pass 1 candidates too (cheap & catches wrong-fund cases like
    # `Equity Hybrid95` resolving to the wrong AMFI row at MFAPI fetch time).
    pass1_candidates: list[tuple[int, str, int, str]] = []
    pass1_rejected: list[tuple[int, str, int, str, float, float]] = []
    for syn_code, syn_name, real_code, real_name in pass1:
        ok, our_nav, amfi_nav = _nav_matches(conn, syn_code, real_code)
        if ok:
            pass1_candidates.append((syn_code, syn_name, real_code, real_name))
        else:
            pass1_rejected.append((syn_code, syn_name, real_code, real_name, our_nav or 0.0, amfi_nav or 0.0))

    matched_syn_codes = {r[0] for r in pass1_candidates}  # only count NAV-validated as matched
    candidates: list[tuple[int, str, int, str]] = list(pass1_candidates)
    nav_rejections: list[tuple[int, str, int, str, float, float]] = list(pass1_rejected)

    # Pass 2 — stem + (plan, option) triple match for unresolved synthetics.
    unresolved = conn.execute(
        text("SELECT scheme_code, scheme_name FROM amfi_schemes WHERE scheme_code < 0 ORDER BY scheme_code")
    ).all()
    unresolved = [(c, n) for (c, n) in unresolved if c not in matched_syn_codes]
    if not unresolved:
        return candidates

    # Build an index of real-code rows keyed by (stem, plan, option). One synthetic-stem
    # may map to many real rows (different IDCW frequencies, payout flavours, etc.); we
    # only accept a merge when there's exactly one real row at the same (plan, option).
    real_rows = conn.execute(text("SELECT scheme_code, scheme_name FROM amfi_schemes WHERE scheme_code > 0")).all()
    by_key: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    for code, name in real_rows:
        key = (_stem(name), detect_plan(name) or "", detect_option(name) or "")
        by_key.setdefault(key, []).append((code, name))

    ambiguous = 0
    still_unresolved: list[tuple[int, str]] = []
    for syn_code, syn_name in unresolved:
        plan = detect_plan(syn_name) or ""
        opt = detect_option(syn_name) or ""
        bucket = by_key.get((_stem(syn_name), plan, opt), [])
        if len(bucket) == 1:
            real_code, real_name = bucket[0]
            ok, our_nav, amfi_nav = _nav_matches(conn, syn_code, real_code)
            if ok:
                candidates.append((syn_code, syn_name, real_code, real_name))
            else:
                nav_rejections.append((syn_code, syn_name, real_code, real_name, our_nav or 0.0, amfi_nav or 0.0))
        elif len(bucket) > 1:
            ambiguous += 1
        else:
            still_unresolved.append((syn_code, syn_name))

    pass2_added = len(candidates) - len(pass1_candidates)

    # Pass 3 — opt-in: when the synthetic's name has NO plan word, default to "Regular".
    pass3_added = pass3_ambiguous = 0
    if prefer_regular and still_unresolved:
        for syn_code, syn_name in still_unresolved:
            if detect_plan(syn_name):
                continue  # plan was detectable; pass 2 already had its shot
            opt = detect_option(syn_name) or ""
            bucket = by_key.get((_stem(syn_name), "Regular", opt), [])
            if len(bucket) == 1:
                real_code, real_name = bucket[0]
                ok, our_nav, amfi_nav = _nav_matches(conn, syn_code, real_code)
                if ok:
                    candidates.append((syn_code, syn_name, real_code, real_name))
                    pass3_added += 1
                else:
                    nav_rejections.append((syn_code, syn_name, real_code, real_name, our_nav or 0.0, amfi_nav or 0.0))
            elif len(bucket) > 1:
                pass3_ambiguous += 1

    log = logging.getLogger("scripts.dedupe_synthetic_codes")
    log.info(
        "Pass 1 (LOWER=LOWER): %d matches. Pass 2 (stem+plan+option): +%d matches, %d ambiguous. "
        "Pass 3 (--prefer-regular): +%d matches, %d ambiguous. "
        "NAV sanity rejected: %d candidates (>%d%% NAV deviation = wrong fund).",
        len(pass1_candidates),
        pass2_added,
        ambiguous,
        pass3_added,
        pass3_ambiguous,
        len(nav_rejections),
        int(_NAV_TOLERANCE * 100),
    )
    if nav_rejections:
        for syn_code, syn_name, real_code, _real_name, our_nav, amfi_nav in nav_rejections:
            delta_pct = (our_nav - amfi_nav) / amfi_nav * 100 if amfi_nav else 0
            log.warning(
                "NAV-mismatch rejection: syn=%d (%r) → real=%d  our_nav=%.2f  amfi_nav=%.2f  Δ=%.1f%%",
                syn_code,
                syn_name[:60],
                real_code,
                our_nav,
                amfi_nav,
                delta_pct,
            )
    return candidates


def _row_counts_for_synthetic(conn, syn_code: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for tbl in _THIN_TABLES_SINGLE_PK + _THIN_TABLES_COMPOSITE_PK:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE scheme_code = :c"), {"c": syn_code}).first()[0]
        if n:
            out[tbl] = n
    return out


def _merge_pair(conn, syn_code: int, real_code: int) -> dict[str, dict[str, int]]:
    """Move every row from syn_code to real_code. Returns per-table {moved, dropped} counts.

    Strategy:
      * Single-PK tables (mf_metadata, mf_scheme_metrics, mf_registry):
          - If a row already exists at real_code: keep it, DELETE the synthetic row.
          - Otherwise: UPDATE the synthetic row's scheme_code to real_code.
      * Composite-PK tables (mf_nav, PK = (scheme_code, date)):
          - For each (date) where the real_code row already has data: DELETE the
            corresponding synthetic-code row (real wins).
          - For dates only in the synthetic: UPDATE scheme_code to real_code.
    """
    summary: dict[str, dict[str, int]] = {}

    for tbl in _THIN_TABLES_SINGLE_PK:
        existing_at_real = conn.execute(
            text(f"SELECT 1 FROM {tbl} WHERE scheme_code = :c LIMIT 1"), {"c": real_code}
        ).first()
        if existing_at_real:
            res = conn.execute(text(f"DELETE FROM {tbl} WHERE scheme_code = :c"), {"c": syn_code})
            summary[tbl] = {"moved": 0, "dropped": getattr(res, "rowcount", 0) or 0}
        else:
            res = conn.execute(
                text(f"UPDATE {tbl} SET scheme_code = :real WHERE scheme_code = :syn"),
                {"real": real_code, "syn": syn_code},
            )
            summary[tbl] = {"moved": getattr(res, "rowcount", 0) or 0, "dropped": 0}

    # mf_nav: drop synthetic rows whose date already exists at real_code, then update the rest.
    drop_res = conn.execute(
        text(
            """
            DELETE FROM mf_nav
            WHERE scheme_code = :syn
              AND date IN (SELECT date FROM mf_nav WHERE scheme_code = :real)
            """
        ),
        {"syn": syn_code, "real": real_code},
    )
    move_res = conn.execute(
        text("UPDATE mf_nav SET scheme_code = :real WHERE scheme_code = :syn"),
        {"real": real_code, "syn": syn_code},
    )
    summary["mf_nav"] = {
        "moved": getattr(move_res, "rowcount", 0) or 0,
        "dropped": getattr(drop_res, "rowcount", 0) or 0,
    }

    # Finally remove the synthetic amfi_schemes row.
    conn.execute(text("DELETE FROM amfi_schemes WHERE scheme_code = :c"), {"c": syn_code})

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="Commit the merges. Without this flag, runs as a dry-run.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N candidate pairs.")
    parser.add_argument(
        "--prefer-regular",
        action="store_true",
        help="When a synthetic's name has no plan word (e.g. 'Invesco India Arbitrage Fund - "
        "Growth Option'), default to the Regular plan. Indian-MF convention for short-form "
        "names from MFAPI / tradebook. Off by default to avoid mis-assigning Direct funds.",
    )
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("scripts.dedupe_synthetic_codes")

    with engine.begin() if args.apply else engine.connect() as conn:
        candidates = _propose_merges(conn, prefer_regular=args.prefer_regular)
        if args.limit:
            candidates = candidates[: args.limit]

        if not candidates:
            log.info("No synthetic-code rows match a real (case-insensitive) AMFI scheme. Nothing to do.")
            return 0

        log.info(
            "Found %d synthetic→real merge candidate(s)%s.",
            len(candidates),
            " (DRY RUN — no changes will be made)" if not args.apply else "",
        )

        totals = {"merged": 0, "rows_moved": 0, "rows_dropped": 0}
        for syn_code, syn_name, real_code, _real_name in candidates:
            counts = _row_counts_for_synthetic(conn, syn_code)
            log.info(
                "[%s] syn=%-5d → real=%-7d  %r  (rows: %s)",
                "MERGE " if args.apply else "WOULD ",
                syn_code,
                real_code,
                syn_name[:70],
                ", ".join(f"{k}={v}" for k, v in counts.items()) or "no dependent rows",
            )
            if args.apply:
                summary = _merge_pair(conn, syn_code, real_code)
                totals["merged"] += 1
                for s in summary.values():
                    totals["rows_moved"] += s["moved"]
                    totals["rows_dropped"] += s["dropped"]

        # Show how many synthetics still remain after the merge (these are the truly
        # codeless funds — AMFI master has no row at all, even case-insensitive).
        remaining = conn.execute(text("SELECT COUNT(*) FROM amfi_schemes WHERE scheme_code < 0")).first()[0]
        if args.apply:
            # We rewrote/deleted scheme rows; invalidate the holdings slug cache so any
            # long-running process (Streamlit) reloading after dedupe sees the new map.
            clear_slug_cache()
            log.info(
                "Done. Merged %d synthetic codes — moved %d rows, dropped %d duplicates. "
                "%d synthetic codes remain (no AMFI counterpart).",
                totals["merged"],
                totals["rows_moved"],
                totals["rows_dropped"],
                remaining,
            )
        else:
            log.info(
                "Dry-run complete. %d candidates queued. Run with --apply to commit. "
                "(After merge: ~%d synthetic codes will remain — funds with no AMFI master row.)",
                len(candidates),
                remaining - len(candidates),
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
