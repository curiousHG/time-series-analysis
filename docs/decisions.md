# Decision log

Why behind non-obvious choices. Inline comments stay minimal; the rationale lives here.

## NAV data quality (`mf_nav`)

- **Glitch repair** (`nav.repair_nav_glitches`, `_GLITCH_RATIO`) — isolated single-day spikes that revert
  (>2× vs *both* neighbours) and zero/negative NAVs are upstream errors. We delete only the
  bad point, never the fund; permanent steps (side-pocketing, bonus) disagree across neighbours and
  are not flagged. Previously one ancient bad day discarded a fund's whole history (~170 funds
  recovered after softening the guard to drop the day, not the fund).
- **Scale-break repair** (`nav.repair_nav_scale_breaks`) — some liquid/debt funds carry early
  history at a 1/10 or 1/100 scale spliced onto the real NAV (an **UP power-of-10 break**;
  MFAPI itself serves it). We rescale every earlier segment up to the latest scale, then skip the
  fund if the corrected whole-history CAGR is implausible (`_PLAUSIBLE_CAGR_MIN/MAX`, -10%/+60%).
  Tolerance for "clean 10^k" is `_SCALE_BREAK_TOL`; funds under `_SCALE_BREAK_MIN_POINTS` are skipped.
  **DOWN** power-of-10 breaks are real **ETF unit splits** — left untouched.
- **Plan-stitch (ABSL Equity Hybrid95)** — a synthetic-code stub had IDCW-scale history stitched
  onto the Growth tail (~6.7×, not a power of 10). Fixed by re-fetching the real scheme's clean
  Growth series from MFAPI and replacing the stub's NAV.
- **Atomic NAV replace** (`nav.refresh_nav_data`) — delete + re-insert runs in one transaction and
  only for schemes that fetched successfully, so a failed save never leaves NAV wiped.
- **Throttle must not fall back** (`nav.fetch_single_nav`) — `SourceThrottled` from MFAPI propagates;
  falling back to AdvisorKhoj on a 429 turned a transient throttle into a misleading "no record" and
  could mark a new fund unavailable. Pass `scheme_code` when known (see Scheme identity).
- **Metrics upsert traps** (`scheme_metrics.upsert_metrics`) — two input rows can resolve to one
  scheme_code, and `ON CONFLICT` refuses to touch a row twice in one statement, so
  `_last_row_per_scheme_code` keeps the last. Writes are chunked (`_UPSERT_CHUNK = 500`): ~70 columns ×
  a full recompute (3–4K rows) exceeds PostgreSQL's 65,535 bind-parameter limit, which silently
  killed the whole save.
- **Latest snapshot only** (`holdings_stats`, `holdings` loaders) — `mf_holdings` keeps several
  `portfolio_date` snapshots and legacy refreshes left duplicate rows. Loaders and stats take the
  latest snapshot per scheme, deduped on the natural key (duplicates collapse to the max weight).
  Weights are fractions when a fund's total is ≤ 1.5, else percent.
- **Verified real, not corrupt** — Silver ETFs (~+167% = the 2025 silver rally) and Nippon
  Taiwan (~+185–240% = the TSMC boom). Taiwan's `cagr_1y` slightly overstates because the metric
  uses 252 NAV-points, not a strict 365-day window.

## MF data sources (AMFI, MFAPI, AdvisorKhoj, Kuvera)

- **AMFI layout change** (`fetchers.mutual_fund`, NAVAll.txt) — in 2025 AMFI went from 6 to 8
  columns, moving the plan/option suffix out of the scheme name into `Plan`/`Option` columns. The
  layout is read from the file's own header; `_POSITIONAL_LAYOUTS` is the fallback. The old parser
  kept "succeeding" while nulling every NAV (shifted one place) and collapsing 14K names onto 3.7K,
  and the screener silently lost 98% of its rows.
- **Fail loud on format change** (`_validate_amfi`, `exceptions.UpstreamFormatError`) — a format
  change must stop a sync, not degrade one. Checks run on the parsed rows (`_AMFI_MIN_SCHEMES`,
  `_AMFI_MIN_VALUE_COVERAGE`), not just the header; AMFI has had >10K schemes with a NAV on every
  live row for years, so the floors sit far below normal.
- **Scheme name is now the shared base** (`compose_scheme_name`) — one name covers up to 38 codes, so
  it is neither unique nor parseable by `detect_plan`/`detect_option`. Re-attaching AMFI's plan/option
  restores the " - Direct - Growth" shape AdvisorKhoj and the display helpers expect.
- **Category normalisation** (`_ASSET_CLASS_ALIASES`, `_SUB_CLASS_ALIASES`, `_classify_amfi_header`) —
  AMFI spells one class several ways across headers (singular/plural, footnote asterisks), and files
  index funds/ETFs/FoFs under "Other Scheme" with the real class only in the sub-category; both
  routes must land on one category. Fund-house lines can contain parentheses ("IL&FS Mutual Fund
  (IDF)"), so headers are matched by the strict `_AMFI_CATEGORY_RE`, not a paren heuristic.
- **MFAPI rate limits** (`_MFAPI_BACKOFF_SECONDS`, `constants.NAV_FETCH_WORKERS`) — since Oct 2026
  MFAPI answers bulk refreshes with 429 and no Retry-After (114 of 200 requests throttled at 16
  workers, some even at 4). 4 workers plus a `_Cooldown` shared across threads, so one 429 holds the
  whole pool, keeps a full refresh inside the limit.
- **AdvisorKhoj portfolio key** (`fetch_portfolio`) — keyed by the scheme *name* as AdvisorKhoj
  spells it, form-encoded so `&` survives; slugs only work for plain-word names. An unknown key gets
  HTTP 200 with an empty body or the homepage (`SourceNotFound`).
- **AdvisorKhoj never 404s** (`fetch_fund_overview_html`, `extract_launch_date`) — unknown names
  return the homepage (detected by the missing `sch_over_table`; parsing it once stored a row of Nones
  marked "available"), and some listed names return a shell page with every field blank. Both are
  misses, not format changes. Whitespace is collapsed first: double spaces in AMFI names corrupt its
  redirect to the canonical slug (guaranteed 404).
- **AdvisorKhoj name candidates** (`mutual_funds.advisorkhoj.name_candidates`) — it keys funds by the
  pre-2025 AMFI name (`<base> - Direct Plan - Growth`), and AMFI's new option text varies, so
  candidate spellings are tried in measured hit-rate order: `<base> - <plan> - <short option>`
  (most funds), then the plan-without-"Plan" form, then AMFI's verbatim option text; previously
  resolved spellings go first. `holdings.resolve_and_fetch_portfolio` verifies every hit
  (`_returned_same_fund`) because AdvisorKhoj fuzzy-matches unknown keys onto other funds.
- **AdvisorKhoj metadata parsing** (`fetch_fund_metadata`, `_EXIT_LOAD_FIELD_RE`) — capital-E
  "Exit Load:" is the structured field; lowercase "exit load:" appears inline in schedule text and
  must not end the match, and the longest candidate wins over a short "Yes/No/Nil" indicator.
  `fund_house` stays None here; the repository fills it from `amfi_schemes`.
- **Kuvera** (`fetchers.kuvera`) — no published rate limit (~130 ms/call, no 429s at 4 workers).
  A throttle status gets one `_BACKOFF_SECONDS` retry, then surfaces so a bulk run stops instead of
  failing every fund. A name match only proposes; the ISIN must match the AMFI record, else None.
  AUM is in Rs millions (`/10` → crore, verified vs AdvisorKhoj); no SEBI benchmark is exposed.
- **Metadata source order** (`metadata.fetch_and_save`) — Kuvera (Direct plans, ISIN-verified), then
  AdvisorKhoj by verified slug (Regular plans), then by name, each filling only what the previous left
  empty; last resort is the Direct sibling's record. Only `_SCHEME_LEVEL_FIELDS` (AUM, manager,
  objective, riskometer, category) are copied from a sibling; TER, launch date and minimums differ by
  plan. A row without AUM counts as missing (`ensure_metadata`), so the next source gets a chance.
- **screener.in** (`fetchers.screener_in`, `stock_fundamentals.save_company_fundamentals`) —
  standalone-only reporters (Hitachi Energy / POWERINDIA) serve a consolidated page that loads 200
  with every value blank, so `fetch_company` keeps the first page whose top ratios carry values. A
  page with no top-ratio values (`_carries_no_top_ratios`) is a soft failure (throttle or blocked): it
  never overwrites good data or marks the symbol available. `_SLUG_OVERRIDES` covers mergers/renames
  that kept the old URL. The screen endpoint and Excel export are login-gated; company pages and
  search are public.
- **Morningstar: investigated, deliberately not integrated** — morningstar.in serves licensed premium
  data via an internal service-account JWT (1h TTL) embedded per page; replaying it is a ToS
  violation, and `mstarpy` automates it only via a headless browser. Stay on license-clean sources
  (MFAPI, AMFI, AdvisorKhoj, niftyindices).
- **Worker counts are measured** — `registry_service.backfill_missing` uses `max_workers=8`,
  `submit_delay=0.05`: MFAPI peaks at 16 but the pool is shared with metadata fetches, and AdvisorKhoj
  overview queues at 8+. `HOLDINGS_FETCH_WORKERS` is 16: AdvisorKhoj sustained ~78 rps, p95 ~190 ms.

## Scheme identity: codes are the key, names are display

AMFI's 8-column feed leaves Plan/Option blank on ~5,700 open-ended rows, so every variant of
such a fund arrives with one identical name (Motilal Oswal Midcap Fund: four codes). Any
name-keyed path then mixes variants — the NAV refresh once stored the IDCW series under the
Growth code, and the portfolio priced a Direct-plan holding at the IDCW NAV (Sep 2026).

- **Names are made unique at ingest** (`data.fetchers.mutual_fund.disambiguate_scheme_names`):
  option inferred from the ISIN columns (a reinvestment ISIN ⇒ IDCW) and stored in `option`;
  a remaining tie gets the scheme code appended, e.g. "Motilal Oswal Midcap Fund - Growth (127042)".
  `base_name` strips the suffix again so AdvisorKhoj candidates are unaffected. The parser
  raises `UpstreamFormatError` if names are still not unique.
- **NAV fetch, save and the portfolio load are keyed by `scheme_code`** (`fetch_single_nav(name,
  scheme_code)`, rows tagged with the code, `load_nav_by_codes`, `mf_metrics.nav_series`). Name-keyed
  loaders remain for the screener and single-fund views, which is safe only because names are unique.
- Plan (Direct/Regular) cannot be inferred offline when AMFI leaves it blank; Kuvera's bulk list
  carries no ISIN, so the code suffix is the honest fallback. In the screener AMFI's own
  `Plan`/`Option` win where present; the name-derived fallback covers the ~40% AMFI leaves blank.
- **Synthetic negative codes** (`AmfiScheme`) — codeless funds (segregated portfolios, side-pockets) and
  NAV/tradebook name variants that miss the exact AMFI name (apostrophe, spacing, word order) get a
  negative code; AMFI never issues negatives, so namespaces cannot collide. Stubs carry no
  category/sub-category/AMC; 35 such funds were categorized via a verified per-fund mapping,
  including a new **SIF** category for the quant/ITI/Edelweiss long-short funds. Root cause (exact-name
  resolution) is still open — see `refactor-followups.md`.
- **Option detection order** (`display.detect_option`) — IDCW is checked before Growth because a name
  holding both means the payout flavour. Suffix patterns in `mutual_funds.constants` are end-anchored
  and applied until the name stops shrinking, so "Axis Growth Opportunities Fund" is never eaten.
  `unique_short_names` resolves sibling clashes by appending option, then plan, then full name.
## Price data quality

- **One thread-safe yfinance path** (`fetchers.stock._YF_LOCK`, `_verified_single`,
  `fetch_symbols_batch`) — yfinance shares one session/cache across threads and `yf.download` can hand a
  caller another caller's frame. In production every `^` index received a stock's bar on a refresh
  day, because MF-metric threads refreshed benchmarks while stock threads fetched gaps. All calls go
  through the lock and every returned frame is checked against the requested ticker. A flat frame for
  a multi-ticker request cannot be attributed, so it raises `UpstreamFormatError` instead of being
  filed under `symbols[0]`.
- **Retired sources — do not reintroduce** (`data.sources`): niftyindices.com (died ~2026-07-02 and
  froze every series it fed; bhavcopy names replace it); jugaad per-symbol quotes (IST-midnight stamps
  = 18:30 UTC, so `.date()` shifted rows a day forward onto wrong dates, never overwrote corrupt rows
  and occasionally spiked); NSE stock bhavcopy daily append (raw basis onto adjusted history diverged
  after every split/dividend). Routing was set by live-probing every source in July 2026. The NSE index
  bhavcopy stays for indices only: it is the sole source covering all 160+ indices (incl. factor
  funds) plus valuation (P/E, P/B, yield), and index levels need no adjustment. Fundamentals and CAPM
  benchmarks follow the listing market (screener.in/^NSEI for NSE, yfinance/^GSPC for international).
- **Despike** (`stock_ohlcv._despike`, `_DESPIKE_RATIO = 5`) — Yahoo's early-2000s `.NS` history has days
  that spike (or swap between tickers) and recover next session (CIPLA 2003-04-14: 49.3 → 3.9 → 49.5).
  Real splits are persistent level shifts and never match the both-neighbours pattern.
- **Non-positive closes dropped** (`_ohlcv_io._upsert_ohlcv`) — Yahoo's early `.NS` zero/null-close days
  poison returns.
- **Phantom weekend bars** (`_ohlcv_io._drop_phantom_weekend_bars`) — yfinance answers a delisted/renamed
  ticker (TATAMOTORS after the demerger) with dates shifted a day earlier, i.e. a bar every Sunday.
  Weekend dates survive only if the Nifty 50 index bhavcopy shows NSE traded (`_special_session_dates`:
  Budget-day, Muhurat). The reverse also holds: yfinance never publishes those special sessions, so
  `stock_gap_ranges` filters weekend sessions out and bounds the check by a symbol's own first date.
- **Full re-fetch is fetch-then-replace** (`stock_ohlcv.refetch_stock_full`) — delete + re-insert in
  one transaction: a plain upsert would leave stale rows on dates the new pull skips, and the
  transaction makes it interrupt-safe.
- **Delisted symbols** — a registry row with NULL `exchange` means the symbol left the NSE master
  (TATAMOTORS → TMCV/TMPV); its history is static, so `refresh_stocks_batch` skips it instead of 404ing
  daily. `_empty_fetch_cache` (`_EMPTY_FETCH_TTL`, 1h) is an in-process negative cache so a symbol
  yfinance can never serve does not re-run a slow doomed fetch on every rerun; the TTL is short so
  transient outages retry.
- **Watermark rules** (`ohlcv_watermark._ensure_ohlcv`) — `_UNAVAILABLE_EMPTY_STREAK = 2` consecutive
  empty fetches flips a stock to `unavailable`, which short-circuits until a Settings retry clears it
  (indices never flip: `flag_unavailable=False`). The backward gap is fetched only while the floor is
  unconfirmed (`wm.earliest != db_min`) and locked only when nothing earlier arrived. Cold start pulls
  `_COLD_BACKFILL_DAYS` (10y) so all rolling-CAGR windows have data; ranges under `MIN_FETCH_DAYS` (5)
  are skipped as holiday/weekend noise, except `_refresh_to_today`.
- **Last NSE date uses the median** (`last_nse_stock_ohlcv_date`) — the plain max is dragged forward
  by a few international tickers while NSE symbols sit weeks behind, which once opened a 40-day hole
  via a forward-only refresh window.
- **Index sanity** (`index_ohlcv._MAX_INDEX_DAY_MOVE`) — a broad index does not halve or double in a
  session; a bar that far from the last stored close is another instrument's bar or a source glitch
  and is not stored. Name-keyed NSE indices (with a space) have no on-demand source; the bulk
  bhavcopy refresh maintains them. `list_bhavcopy_index_names` treats an index as live if published
  within `_LIVE_INDEX_WINDOW_DAYS` (45) of the newest bhavcopy day, so matured and renamed indices
  drop out of pickers while all-caps names like "NIFTY SME EMERGE" stay.
- **Stored as published, adjusted in metrics** (`services.price_adjust`) — AMFI NAVs are not adjusted for
  ETF unit splits (1:10 prints as a -90% day), and yfinance's adjusted history still carries
  pre-~2010 splits/bonuses and events Yahoo never recorded (MOTILALOFS / GPIL 2024-01-01). Left alone
  they become -90% "returns" and -99% drawdowns, so `mf_metrics.compute_metrics_for_scheme` and
  `stock_metrics._daily_returns` run series through `adjust_splits` first. `SPLIT_TOL` (0.08) on
  |ratio/factor − 1| keeps a real ~-55% crash (Yes Bank 2020, ratio 0.44) outside the 2× and 2.5×
  bands; a demerger landing on a clean factor (Vedanta 2026, ~1/3) is adjusted, closer to what a
  holder experienced. `PERSIST_DAYS`/`PERSIST_TOL` require the new scale to hold, comparing medians
  either side, so a reverting level is a blip, not a split.
- **Corrupt-series repair** (`stock_sync_service.find_corrupt_ohlcv_symbols`) — flags an implausible
  *adjacent-day* close jump (> `jump`× or < 1/`jump`), the signature of a stale adjusted series spliced
  onto raw rows (a ~600× splice gave PAGEIND `return_1y` ~26,775%). Adjacent-day detection leaves
  legitimate long-run growth alone. `repair_corrupt_ohlcv` resets the watermark
  (`clear_stock_ohlcv_status`) before the re-fetch.
- **International stocks** — fundamentals come live from yfinance (not screener.in), so
  `sync_missing_fundamentals` covers NSE/BSE only; CAPM benchmarks are S&P 500 rather than an INR
  index, which would mix currencies and market regimes. `seed_nifty500` loads price metrics only,
  hence the separate fundamentals fill. Exchange codes other than NSE register as `exchange="GLOBAL"`
  and fetch as-is.

## Benchmarks

- **Category benchmark, not Nifty 50** — Alpha/Beta/TE/R² use each fund's category benchmark (Large Cap
  → Nifty 100, Mid → Midcap 150, Small → Smallcap 250, Flexi/ELSS/… → Nifty 500), see
  `services.benchmarks.SUBCATEGORY_BENCHMARK`. The IR-numerator axis still uses Nifty 50 (it needs a
  per-fund benchmark CAGR we don't cache); the screener shows a caveat when a benchmark-relative
  measure is picked.
- **No benchmark where CAPM is meaningless** — Debt, Arbitrage, Index/ETF/FoF and Conservative Hybrid
  (`_DEBT_LIKE_KEYWORDS`) are absent from the map, so alpha/beta stays NaN instead of being computed
  against an equity index with near-zero correlation. Large & Mid Cap uses Nifty 200 as a proxy for
  LargeMidcap 250; Sectoral/Thematic and equity-heavy hybrids fall back to Nifty 500, a rough lens.
- **Symbol routing** (`benchmarks.BENCHMARK_SYMBOL_MAP`) — NSE indices use their bhavcopy names (bulk
  maintained daily, carry P/E, a day fresher than Yahoo); Yahoo `^` tickers remain only for Nifty
  50/Sensex and non-Indian indices (overseas FoFs). The old `^CNX…` aliases were retired after holding
  other instruments' bars. The midcap/smallcap names are NSE's own inconsistent spellings ("NIFTY
  Midcap 100" vs "Nifty Midcap 150"); do not "fix" the casing.
- **Factor indices fall back to the parent universe** (`_PARENT_UNIVERSE`) — Quality/Value/Momentum/Low-Vol
  indices are not fetchable, so they map to the closest parent. Order matters: longest tokens first, so
  "500" does not match inside "50".
- **Resolution chain** (`mf_metrics.recompute_metrics`) — metadata benchmark (if mappable) →
  sub-category → Nifty 50 for non-debt funds; each distinct index loads once. The benchmark is
  force-refreshed (`_load_benchmark_returns`) because `ensure_stock_data` skips sub-5-day gaps, which
  would leave CAPM on stale data.

## Metrics and portfolio analytics

- **One risk-free rate per domain** — MF metrics use `RISK_FREE_ANNUAL` (6%); the single-stock backtest
  uses `BACKTEST_RISK_FREE` (6.5%), the deliberate exception noted in `standards.md`. The UI-side
  constant in `ui.constants` intentionally differs from `RISK_FREE_ANNUAL` too.
- **Non-investable exclusion** — `compute_metrics_for_scheme` returns None for segregated portfolios
  (distressed-debt side-pockets) and defunct funds (NAV stale > `_DEFUNCT_AFTER_DAYS`, 270), so their
  meaningless returns never reach the screener. Schemes that stop passing validation have their
  cached row evicted. `DORMANT_AFTER_DAYS` is the tracked-scheme version: no source has holdings or AUM
  for a matured FMP, so every fetch path skips it.
- **Plausibility net** (`mf_metrics._MAX_PLAUSIBLE_DAILY_RETURN`) — on top of `repair_nav_glitches`, a
  >100% daily return is upstream corruption. Only those days are dropped, the history is kept (None
  if fewer than `TRADING_DAYS` returns remain).
- **Fund age** — "age" is `inception_date` = the first NAV date (validated 1:1 against the oldest
  `mf_nav` row). Funds without computed metrics are excluded when an age floor is set.
- **XIRR** (`portfolio_analytics.compute_xirr`) — flows dated after the valuation date happen when NAV
  is stale; their units are already in `terminal_value` at the last NAV, so they are treated as invested
  at the valuation date (years clipped to 0) rather than excluded or compounded negatively. The brentq
  bracket is -99.99% to +1000%; no sign change returns None.
- **Time-weighted returns** (`ui.views.portfolio.risk_metrics`) — `r_t = (V_t − V_{t-1} − CF_t) / V_{t-1}`.
  Raw `pct_change` on SIP days counts new money as return and inflates CAGR; extreme edge-case values
  are clipped. The risk-vs-return portfolio point uses the same series.

## Backtest Lab (Sep 2026)

- **Custom daily loop, not vectorbt** (`services/backtest/engine.py`). vectorbt's `from_signals`
  cannot express per-order rupee charges (DP charge, brokerage cap), integer shares with a cash
  floor, next-open fills with an intrabar stop ladder, or rank rebalancing with hysteresis. The
  numpy `(symbols × days)` loop is a few hundred lines and touches only open trades and the day's
  candidates per bar. vectorbt stays for the single-stock preview on the Stock Analysis page.
- **No lookahead by construction.** A signal or score on row T is acted on at row T+1's open; the
  engine does the shift, strategies never see fills, ML predictions are written on the bar they
  were made. Truncation-invariance tests (deleting future bars must not change earlier output)
  guard the engine, the features and the walk-forward.
- **Costs are Zerodha's published schedule** (`services/backtest/costs.py`, checked 2026-09-09):
  STT 0.1% both sides on equity delivery, 0.001% sell-only on ETFs, stamp duty on buys, DP
  charge per sell, GST on brokerage + exchange + SEBI. `NSE_TXN_PCT` defaults to the page value
  0.00307%; older notes said 0.00297%, confirm against a contract note. The intraday preset is a
  cost comparison only: the engine holds overnight.
- **Strategy parameters are descriptors** (`strategies/parameters.py`): the run form and the Optuna
  optimiser read the declared space (`BasketStrategy.parameter_space`) instead of introspecting
  widgets, and `optimize=False` keeps model-shape parameters out of the search by default. Stoploss is
  not optimised by default (freq_strategies lesson: better backtests, worse live results).
- **Equity-curve objectives.** Trade-based Sharpe/Calmar are degenerate for hold-and-rebalance
  books, so optimiser objectives and headline risk metrics come from the daily equity series.
- **Run lifecycle** — a finished run is immutable (editing saves a new draft) and the detail view reads
  persisted equity/trades/summary, never recomputing. Launches are capped at two concurrent runs, one
  when an ML strategy or optimisation is involved (each retrains per walk-forward window and would
  starve the others). The optimiser runs off-thread and persists every trial as it completes, so
  closing the browser loses nothing.
## Strategies (Sep 2026)

- **Discovery from files** (`strategies/resolver.py`), the way freqtrade reads `user_data/strategies`.
  A `.py` file there is imported, scanned for `BasketStrategy` subclasses and offered with no
  decorator and no restart; the directory is rescanned when any file's size or mtime moves. Built-ins
  keep the decorator registry and a user file wins a name clash (as in freqtrade), with a warning. A
  file that fails to import is reported on the Strategies view rather than silently vanishing.
- **`custom_exit` is not gated behind `use_exit_signal`** (`strategies/basket.py`). freq_strategies lost
  six months of holding period to exactly that freqtrade bug: turning the exit signal off silently
  disabled a 72-hour time exit and trades ran 184 days. Here the hook is checked separately, runs
  after that bar's stop/ROI ladder and fills at the next open, so it beats the *next* bar's stop like an
  exit signal does.
- **A bars-held hook must ask one bar early.** The decision is made at a close and the order rests at
  the next open, so `bars_held + 1 >= hold` makes the realised holding period equal the parameter.
- **Ported constants were recalibrated, not copied** (`strategies/ported.py`), from 1h crypto to daily
  equities. The reversion rule's "30% below the 50-day average" is common in alt-coins and near absent
  in NSE large caps, so it defaults to 10%; dislocation depth, not RSI depth, does the work. Reversion
  books park the stop out of the way: a 15% stop took a window from +34.98% to −0.13%, because the stop
  fires at the low the reversion starts from. The momentum basket's exit band is three times its
  position count (every band ≥ 2× beat a narrow one; a band only slightly wider than `top_k` was worse
  than none), and the index regime gate and exit band are complements (both on +38.8%, either off lost
  money). `_regime_ok` fails open when benchmark data is missing, so a run without an index still trades.
- **The Donchian channel is shifted a bar.** The source compares the close against a rolling maximum
  that includes the current bar, which only a bar closing exactly on its own high can clear. On daily
  bars it never fires.
- **ML features are causal and scale-free** (`strategies/ml`) — prices ×10 or volume ×7 give identical
  features; OBV/A-D enter as volume-scaled differences; VWAP is excluded (anchored at frame start).
  `build_features` runs on the symbol's own traded bars because a frame reindexed onto the basket
  calendar has NaN rows and TA-Lib returns NaN for every bar after the first gap. Walk-forward trains
  to `horizon + 1` bars before each test block so no label overlaps, fits scaler/PCA on the training
  window only, and seeds models with a fixed LightGBM thread count for reproducible reruns.

## Transition matrix over bucketed returns (Sep 2026)

- **Confidence is P(up | the move leaves the flat band)**, not the raw share of positive buckets.
  With an eleven-bucket scheme the flat band holds about a fifth of every row, so a raw P(up) tops
  out near 0.48 on NSE bars and no sensible threshold could ever be met. Renormalising against the
  down side puts 0.5 at genuine neutrality.
- **The exit threshold is a band under `entry_confidence`**, not an absolute level (`populate_exit`),
  so it follows the active confidence scale. An absolute one would be met by every bar in `lift` mode
  (confidence near zero), and the engine drops an entry whose bar also carries an exit, so no position
  would ever open. `lift` mode (probability minus the day's basket base rate) has `min_lift_z` as a
  sample-size guard.
- **Counts roll with a cumulative sum** (`strategies/markov.py`), so every bar carries its own
  matrix from a single pass and the matrix in force on a bar sums only transitions that had already
  landed. The same structure accumulates the realised next return, which makes a row a conditional
  expected return rather than a bucket-centre approximation. `alpha` is a Laplace prior so an unseen
  transition does not read as certainty.
- **The edge is real and mostly unreachable.** On Nifty 50 over 2019–2025 the deepest down bucket
  lifts the directional odds to 55.9% against a 51.5% base rate, with a +0.29% average next bar —
  textbook short-term reversal, decaying monotonically across the buckets. But the matrix forecasts
  a close-to-close move while the engine fills at the next open: on called bars the predicted move
  averages +0.168%, the overnight gap takes +0.264% of it, and an open-to-open trade keeps +0.131%
  against a +0.092% baseline. That is a 4bp edge against a round trip of roughly 30bp on the
  delivery preset with slippage, which is why the signal book loses about 18% over the period.
  The diagnostics (`_build_diagnostics`) report both gradings side by side so the gap is visible rather
  than inferred; the strategy is kept as an honest negative result, not tuned until a number looks good.

## UI and Streamlit

- **Widget-key staging** — a widget-keyed session-state entry cannot be written after the widget
  rendered (Streamlit raises). Cross-view jumps stage into `*_pending` keys (`sa_ticker_pending`,
  Backtest Lab `state.apply_pending`) applied before the widget instantiates on the next run.
- **Filter hydration** (`filter_persistence.hydrate_filters`) — re-seeds every run because Streamlit GCs
  widget keys on page navigation; idempotent so live edits survive. Widgets reading a seeded key must
  omit `default=`/`value=`, and None is never seeded. `cascade_subcategory_options` prunes stale
  persisted sub-categories so a multiselect never holds a value outside its options. Backtest Lab
  parameter keys are `bt_<form>_p_<strategy_slug>_<parameter>` and seeded the same way.
- **AgGrid** — `render_screener_grid` is deliberately keyless: with a fixed `key`, st_aggrid keeps the
  first mount's data and ignores changes to the filtered frame (one grid per page). Its return value
  is rows in displayed order, so "Fetch top N" is WYSIWYG (`DISPLAY_ORDER_KEY`, intersected with the
  current frame). streamlit-aggrid 1.x uses AgGrid v32 parametric theming: `alpine-dark`/`balham-dark`
  strings silently fall back to light, so dark must be a real `StAggridTheme`
  (`aggrid_theme.streamlit_dark_aggrid_theme`); the MF screener fixes to it. JsCode callbacks need
  `allow_unsafe_jscode=True`.
## Logging and process lifecycle

- **Idempotent setup on real state** (`core.logging_config.setup_logging`, `LOG_HANDLER_MARKER`) —
  Streamlit re-imports modules and resets globals, so idempotency checks a marker attribute on the
  handlers, not a module flag. Keep the marker value stable: bumping it adds a second handler set in a
  live hot-reload session; only a restart picks up new layouts.
- **Routing** — `data.log` hangs off the `data` parent logger so every child reaches it by propagation
  and still continues to root. `yfinance`/`peewee` go to `data.log` with `propagate=False`, off the
  console and `app.log`. The `httpx` logger is raised to WARNING: at INFO it was ~25% of `app.log`,
  duplicating the fetchers' own messages.
