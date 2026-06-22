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

### 1. Phase 2 — unify schema migrations on Alembic (deferred; biggest item)

Three schema mechanisms still coexist: `core/database.py:init_schema` (`create_all` on
boot), `migrations/runner.py` (hand-written, run via `scripts/migrate.py`), and `alembic/`
(one baseline revision `20260613_0001`). Port the 3 `migrations/runner.py` steps into
Alembic revisions chained after `20260613_0001`:

1. 24 `ALTER TABLE mf_scheme_metrics ADD COLUMN IF NOT EXISTS …` (metrics columns).
2. `stock_ohlcv.volume` INTEGER → BIGINT (idempotent type check first).
3. `pg_trgm` extension + GIN index for fuzzy scheme-name search.

Optional extra revision: drop legacy `bots` / `trades` / `orders` tables (the code is gone
as of `f29d587`; the tables were intentionally left in place).

Then: document `alembic stamp 20260613_0001` for pre-Alembic DBs; reword `init_schema`
docstring to "first-run table creation only"; delete `migrations/` + `scripts/migrate.py`;
update `CLAUDE.md` + `README.md`.

**Why deferred:** needs a live Postgres to verify idempotency. Verify with
`createdb trading_test` then `uv run alembic upgrade head` run **twice** (must be a no-op
the second time); confirm all schema objects exist; `uv run pytest`.

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
