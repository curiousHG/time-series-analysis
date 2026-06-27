# Decision log

Why behind non-obvious choices. Inline comments stay minimal; the rationale lives here.

## Data quality (`mf_nav`, metrics)

- **Glitch repair** (`nav.repair_nav_glitches`) — isolated single-day spikes that revert
  (>2× vs *both* neighbours) and zero/negative NAVs are upstream errors. We delete only the
  bad point, never the fund. Previously one ancient bad day discarded a fund's whole history
  (~170 funds recovered after softening the corrupt-NAV guard to drop the day, not the fund).

- **Scale-break repair** (`nav.repair_nav_scale_breaks`) — some liquid/debt funds carry early
  history at a 1/10 or 1/100 scale spliced onto the real NAV (an **UP power-of-10 break**;
  MFAPI itself serves it). We rescale the wrong-scale segment up to the recent, web-verified
  scale. **DOWN** power-of-10 breaks are real **ETF unit splits** — left untouched.

- **Plan-stitch (ABSL Equity Hybrid95)** — a synthetic-code stub had IDCW-scale history stitched
  onto the Growth tail (~6.7×, not a power of 10). Fixed by re-fetching the real scheme's clean
  Growth series from MFAPI and replacing the stub's NAV.

- **Non-investable exclusion** — `compute_metrics_for_scheme` returns None for segregated
  portfolios (real but extreme distressed-debt side-pockets) and defunct/wound-up funds (NAV
  stale > 270 days), so their meaningless returns never reach the screener.

- **Verified real, not corrupt** — Silver ETFs (~+167% = the 2025 silver rally) and Nippon
  Taiwan (~+185–240% = the TSMC boom). Taiwan's `cagr_1y` slightly overstates because the metric
  uses 252 NAV-points, not a strict 365-day window.

## Benchmarks

Alpha/Beta/TE/R² use each fund's **category benchmark** (Large Cap → Nifty 100, Mid → Midcap 150,
Small → Smallcap 250, Flexi/ELSS/… → Nifty 500), not Nifty 50 — see
`services.benchmarks.SUBCATEGORY_BENCHMARK`. The IR-numerator axis still uses Nifty 50 (it needs a
per-fund benchmark CAGR we don't cache).

## Categorization

Synthetic-negative scheme codes are minted when a NAV/tradebook name variant (apostrophe, spacing,
word order) misses the exact AMFI name; the stubs carry no category/sub-category/AMC. 35 such funds
were categorized via a verified per-fund mapping, including a new **SIF** category for the
quant/ITI/Edelweiss long-short funds. Root cause (exact-name resolution) is still open — see
`refactor-followups.md`.

## Age filter

"Fund age" = `inception_date` = first NAV date (validated 1:1 against the oldest `mf_nav` row).
Funds without computed metrics are excluded when an age floor is set.

## Morningstar (investigated, deliberately not integrated)

morningstar.in serves licensed premium data via an internal service-account JWT (1h TTL) embedded
per page; the SAL API is `www.us-api.morningstar.com/sal/sal-service` (`clientId=RSIN_SAL`, `apikey`,
`X-SAL-ContentType`). Replaying it is a ToS violation, and `mstarpy` automates it only via a headless
browser. Decision: **don't integrate** — stay on license-clean sources (MFAPI, AMFI, AdvisorKhoj,
niftyindices).
