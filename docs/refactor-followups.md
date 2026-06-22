# YAGNI Refactor — Status & Follow-ups

Continuation notes for the YAGNI cleanup refactor. Everything below is on `main`.

## Current state (as of this doc)

- Branch: `main` @ `1e28e16`. Working tree clean (only `.claude/launch.json` untracked).
- `uv run pytest` → **14 passed**.
- `uv run ruff check . --exclude notebooks/` → **91 findings remaining** (all non-breaking
  style/quality; see "Ruff leftovers" below). No `F`-code (unused-import / undefined-name)
  breakage.
- App runs via `uv run streamlit run main.py` (port 8501). Config in
  [.claude/launch.json](../.claude/launch.json).

## What landed (3 commits)

| Commit | Summary |
|--------|---------|
| `f29d587` | **Remove dead code** — deleted `bot/`, `exchange/`, `core/models/trading.py`, `core/enums.py`, `core/exceptions.py`, `core/config.py`; dropped `pydantic-settings`; removed `_fetch_single_nav` alias; renamed `MfScheme`→`AmfiScheme` (deleted alias). `strategies/` + `services/backtest_service.py` kept (live). |
| `5251375` | **Consolidate + correctness** — new `data/repositories/scheme_codes.py` (single source for name→code resolution + synthetic minting); `datetime.utcnow()`→`datetime.now(UTC)`; atomic refresh via `holdings.replace_holdings_atomic`; shared `screener_service.apply_name_filter`; single-pass Settings overview; new `tests/test_scheme_codes.py`. |
| `1e28e16` | **Ruff autofix + format sweep** — 10 safe fixes + `ruff format`. No behaviour change. |

### Key new/changed anchors

- `data/repositories/scheme_codes.py` — `resolve_codes`, `resolve_codes_with_synthetic`,
  `mint_synthetic_codes(session, names)`, `resolve_or_mint_code`. **Use these** instead of
  re-implementing resolution/minting.
- `data/repositories/holdings.py:replace_holdings_atomic(slug, h, s, a)` — wipe+refill one
  fund's holdings/sectors/assets in a single transaction. Both refresh paths
  (`holdings.refresh_holdings_data`, `services/sync_service.refresh_holdings_for_schemes`)
  go through it.
- `data/repositories/nav.py` — `refresh_nav_data` deletes + upserts fetched schemes in one
  transaction; `_upsert_nav_rows(session, df, name_to_code)` is the session-aware writer.

## Remaining work

### 1. Phase 2 — unify schema migrations on Alembic — ✅ DONE

Alembic is now the single owner of schema deltas. The three `migrations/runner.py` steps
were ported into revision `alembic/versions/20260622_0002_port_handwritten_migrations.py`
(chained after `20260613_0001`): 42 `mf_scheme_metrics ADD COLUMN IF NOT EXISTS`,
`stock_ohlcv.volume` INTEGER→BIGINT (guarded), and `pg_trgm` + GIN index (in a SAVEPOINT
so a missing-privilege role degrades to ILIKE without poisoning the migration). All steps
idempotent; downgrade is non-destructive (drops only the rebuildable GIN index).

`core/database.py:init_schema` now documents create_all as first-run table creation only;
`migrations/` and `scripts/migrate.py` deleted; `CLAUDE.md` updated.

**Verified** on a scratch `trading_test` DB (real `trading` untouched): `init_schema`
then `alembic upgrade head` twice — 2nd run a no-op; confirmed 58 cols on
`mf_scheme_metrics`, `volume` bigint, `pg_trgm` + `idx_amfi_scheme_name_trgm` present,
`alembic_version` = `20260622_0002`; `uv run pytest` → 30 passed. Existing pre-Alembic DBs
can just run `uv run alembic upgrade head` (every step is idempotent — no stamp needed).

Not done (intentionally): dropping legacy `bots`/`trades`/`orders` tables — skipped to
honour the "no data deleted" constraint. Revisit as a separate opt-in revision if wanted.

### 2. Ruff leftovers (91 findings — not safely auto-fixable)

| Rule | Count | Note |
|------|-------|------|
| `C408` | 39 | `dict()`/`list()` → literals. Mostly Plotly `dict(...)` kwargs in `ui/views/`. Safe but `--unsafe-fixes`; review before applying. |
| `PLC0415` | 38 | Function-local imports — most encode **service↔repository circular deps**. Fix structurally (move shared low-level helpers down into `data/repositories`), not by force-inlining. |
| `RET504` | 5 | unnecessary-assign-before-return. |
| `TC002` / `TC003` | 6 | move type-only imports into `TYPE_CHECKING`. |
| `PERF401` | 2 | manual list-comprehension. |
| `C401` | 1 | unnecessary generator→set. |

`ruff check . --exclude notebooks/ --statistics` for the live breakdown. The remaining
`PLC0415` is the meaningful one — it's a signal of import cycles worth untangling.

### 3. Test gaps — ✅ DONE

Unit tests now cover the previously-untested math (all green; `uv run pytest` → 30 passed):
- `services/portfolio_service.py:build_portfolio_value_series` → `tests/test_portfolio_service.py`
- `services/backtest_service.py:compute_metrics` → `tests/test_backtest_metrics.py`
- `mutual_funds/tradebook.py` → `tests/test_tradebook.py`

### 4. End-to-end Streamlit verification (not yet done)

Boot `uv run streamlit run main.py` against the dev DB and confirm all 5 pages render and
work: Portfolio, Mutual Fund Analysis, Stock Analysis, MF Screener, Settings — plus AMFI
sync, screener search, a NAV+holdings refresh, and the Settings overview. Import-level
loading of `ui.app` was verified; live UI was not.

### 5. Deliberate deviations (context for future work)

- **Settings overview not cached.** The plan called for wrapping its 4 stat reads in a
  `@st.cache_data` loader. Skipped on purpose: it's an ops page where counts must reflect
  post-sync truth, and the refresh actions don't clear an overview cache. The real perf win
  (6 DataFrame scans → 1 single-pass `select`) was applied. Revisit only if reruns prove
  expensive, and wire cache invalidation into the refresh/sync actions if you do.

## References

- Original full plan: `~/.claude/plans/trading-project-tranquil-thompson.md` (outside the repo).
- Architecture / conventions: `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`.
- Knowledge graph: `graphify-out/GRAPH_REPORT.md` (god nodes + communities);
  `graphify query "<question>"` for cross-module questions. Run `graphify update .` after
  code changes.
- Dev tooling already configured: ruff rules + `vulture` + `pyrefly` in `pyproject.toml`;
  `.pre-commit-config.yaml`. Adopt with `uv sync && uv run pre-commit install`.

### Verification commands

```bash
uv run pytest                                   # unit tests (DB mocked)
uv run ruff check . --exclude notebooks/        # lint (91 known follow-ups)
uv run ruff format --check . --exclude notebooks/
uv run streamlit run main.py                    # manual 5-page smoke test
```
