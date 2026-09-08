# Engineering standards

Opinionated conventions for this codebase, distilled from a four-lens audit (layering / duplication /
naming / UI structure, July 2026). **Enforcement:** `tests/test_standards.py` checks everything marked
⚙ mechanically — zero-tolerance where the tree is already clean, ratchet allowlists where violations
are frozen (fixing one requires deleting its allowlist entry, so the lists only shrink). Everything
else is review discipline.

## 1. Layering

```
ui/views  →  ui/state/loaders.py  →  services/  →  data/repositories  →  data/fetchers
                                          ↘  core/ · stocks/ · mutual_funds/ (domain, leaf)
```

- ⚙ `import streamlit` **only under `ui/`** — services/data/domain stay callable from CLI/bot/tests.
- ⚙ Nothing below the UI layer imports `ui.*`.
- ⚙ Domain packages (`stocks`, `mutual_funds`, `indicators`, `strategies`, `core`) import neither
  `services` nor `data.*` — they are leaf logic.
- ⚙ Views reach data **through `ui/state/loaders.py` (reads) or a service (writes/refreshes)** — never
  `from data.…` directly. 15 legacy files are ratcheted; route each through the seam, then delete its entry.
- ⚙ `_`-prefixed names are module-internal. Never import them across top-level packages — promote a
  public accessor instead.
- ⚙ `pg_insert` / `ON CONFLICT` writes are built **only in `data/repositories/`**.
- Deferred imports (`# noqa: PLC0415`) are for **heavy deps** (plotly, quantstats, vectorbt,
  jugaad_data, yfinance) and cycle-breaks only — never for stdlib modules.

## 2. Naming — verb taxonomy

| Verb | Meaning | Lives in |
|---|---|---|
| `fetch_*` | HTTP only, no DB | `data/fetchers/` only |
| `load_*` | DB read → DataFrame/rows | repositories, loaders |
| `get_*` | DB read → scalar/dict | repositories |
| `ensure_*` | DB-first gap-fetch (check DB → fetch missing → save → return from DB) | repositories |
| `save_*` / `upsert_*` | DB write | repositories |
| `sync_*` | pull a full external universe into a table | repositories/services |
| `refresh_*` | forward-fill to now | services |
| `recompute_*` | derive + persist metrics | services |
| `search_*` | fuzzy lookup | anywhere |

Never `query_*`, `read_*`, or bare adjectives (`latest_*`). Known misfits (`query_stocks`,
`read_index_ohlcv`, `latest_index_valuation`, `fetch_*` inside repositories) are queued for the
naming-alignment pass; new code follows the table from day one.

## 3. Data correctness

- **One source per datatype**, declared in `data/sources.py` — the routing registry is the single
  documented truth (the Settings → Reference table renders from it). Change routing ⇒ change the registry.
- **One price basis per table**: equities store yfinance auto-adjusted OHLCV only. Never append a
  raw-price source onto adjusted history (that's the split/dividend corruption class). Index *levels*
  are unadjusted by nature — the index bhavcopy is correct there.
- **Ingest guards live at the write path**: no non-positive closes; full-history re-fetches despike
  one-day glitches (`_despike`).
- ⚙ **One risk-free rate** — `services/constants.py` is the only definition (`RISK_FREE_ANNUAL`,
  `RF_DAILY`); `BACKTEST_RISK_FREE` is the single deliberate exception. Two definitions once made the
  same fund show different Sharpe per view.
- **No calendar/threshold magic numbers**: trading periods use `TRADING_DAYS` (252) and derivations
  (`TRADING_DAYS // 2`), staleness windows and backfill floors get named constants in
  `services/constants.py` / `data/constants.py`.

## 4. Caching (Streamlit)

- ⚙ `@st.cache_data` belongs in **`ui/state/loaders.py`** — one cache surface. (Four page-local
  helpers are ratcheted.)
- ⚙ **Every `cache_data` declares `ttl=`** — an un-TTL'd cache serves stale data until a manual
  `.clear()` and at least three loaders had no clear path at all. Use the named tiers (add to
  loaders.py): `TTL_FAST = 300`, `TTL_INTRADAY = 900`, `TTL_HOURLY = 3600`, `TTL_DAILY = 24 * 3600`.
  Write `24 * 3600`, never `86400`.
- Every mutating flow clears the loaders it invalidates — prefer domain-grouped clearing over
  hand-curated per-page lists.

## 5. UI structure

- **`page.py` is a slim orchestrator**: imports, function defs, one dispatch — no module-scope data
  loads or widget logic. Selection UIs get their own module (`selector.py`); tabs get tab modules.
- **Long-running work never runs inline under `st.spinner`** — anything that scrapes, fetches
  multi-symbol, or recomputes goes through `ui/components/background_refresh.py`
  (stale-while-revalidate + progress). `portfolio/page.py` is the reference implementation.
- **Widget/session keys are page-prefixed**: `pf_`, `sa_`, `mf_`, `ov_`, `stock_scr_`, `set_`. No
  bare generic keys (`drawdown`, `alpha`) — they are duplicate-key hazards.
- **Selections that should survive a restart** (picked ticker/fund, indicator sets, filters) persist
  via `ui/persistence/selections.py`, not raw `session_state`.
- **Charts**: figures are built by `ui/charts/` builders; colors come from `ui/charts/theme.py` — no
  hex/rgba literals in views. One builder per chart family (the two quarterly-results charts share one).
- **Widget-key state can't be written after the widget rendered** — stage into a `*_pending` key and
  apply it before instantiation on the next run (see `sa_ticker_pending`).
- Icons: `:material/...:` for buttons/expanders; emoji only in toasts and type badges.

## 6. Errors & logging

- ⚙ **Never `contextlib.suppress(Exception)` in a view path.** User-facing failures surface via
  `core.notifications.push_notice` (drained as toasts); truly-optional decoration may log-and-continue.
- ⚙ `logging.getLogger(__name__)` — never a hardcoded dotted path (goes stale on rename). Named
  namespace loggers (`perf`, `boot`, `data`) are fine.
- **No per-item `info` logging inside batch loops** — per-item goes to `debug`, one aggregate `info`
  per batch (a full refresh once emitted ~500 INFO lines and wrapped the rotating log).

## 7. Testing

Three legs, all in use — pick per situation:
1. **Pure-function tests** for math/transform logic (`test_backtest_metrics`, `test_symbol_routing`).
2. **Seam tests** with monkeypatched fetchers / `FakeSession` for repo+service flows
   (`test_refresh_safety` is the template) — required for money/data-integrity paths
   (tradebook import, metrics upserts, NAV/holdings refresh).
3. **AppTest smoke** for page renders with session-state preset (picker flows, view dispatch).

Rules: every new service/repo module ships at least one test; bug fixes ship the test that would have
caught them (flow-shaped, not just unit); `tests/test_standards.py` allowlists only shrink.

## 8. Amending these standards

Standards change by editing this file + `tests/test_standards.py` in the same commit. If a rule is
wrong in practice, delete it — an ignored standard is worse than none.
