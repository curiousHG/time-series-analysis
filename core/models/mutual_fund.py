"""SQLModel ORM models for mutual fund data."""

import datetime

from sqlmodel import Field, SQLModel

TABLE_ARGS = {"extend_existing": True}


class MfNav(SQLModel, table=True):
    """Daily NAV per scheme. Phase 2: composite PK is now (scheme_code, date) — was
    (scheme_name, date). scheme_name dropped; resolve via the AmfiScheme join when needed.
    """

    __tablename__ = "mf_nav"
    __table_args__ = TABLE_ARGS

    scheme_code: int = Field(primary_key=True, foreign_key="amfi_schemes.scheme_code")
    date: datetime.date = Field(primary_key=True)
    nav: float


class MfHolding(SQLModel, table=True):
    """Per-fund portfolio holdings (each instrument + weight + ISIN). Phase 3: keyed on
    scheme_code FK to amfi_schemes (was scheme_slug TEXT). scheme_name + scheme_slug +
    scheme_common dropped — derive slug at fetch time via mutual_funds.display.make_slug.
    """

    __tablename__ = "mf_holdings"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: int = Field(foreign_key="amfi_schemes.scheme_code", index=True)
    portfolio_date: datetime.date | None = None
    instrument_name: str | None = None
    isin: str | None = None
    issuer_name: str | None = None
    asset_class: str | None = None
    asset_sub_class: str | None = None
    asset_type: str | None = None
    weight: float | None = None
    value: float | None = None
    quantity: float | None = None
    industry: str | None = None
    market_cap: str | None = None
    credit_rating: str | None = None
    credit_rating_eq: str | None = None


class MfSectorAllocation(SQLModel, table=True):
    __tablename__ = "mf_sector_allocation"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: int = Field(foreign_key="amfi_schemes.scheme_code", index=True)
    portfolio_date: datetime.date | None = None
    sector: str | None = None
    weight: float | None = None


class MfAssetAllocation(SQLModel, table=True):
    __tablename__ = "mf_asset_allocation"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: int = Field(foreign_key="amfi_schemes.scheme_code", index=True)
    portfolio_date: datetime.date | None = None
    asset_class: str | None = None
    weight: float | None = None


class MfRegistry(SQLModel, table=True):
    """Tracked-fund registry. Phase 2: PK is now scheme_code. Resolve scheme_name via the
    AmfiScheme join when needed. Codeless funds get synthetic negative codes during sync.
    """

    __tablename__ = "mf_registry"
    __table_args__ = TABLE_ARGS

    scheme_code: int = Field(primary_key=True, foreign_key="amfi_schemes.scheme_code")
    nav_status: str = Field(default="pending")  # pending | available | unavailable
    holdings_status: str = Field(default="pending")
    metadata_status: str = Field(default="pending")
    added_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    last_attempted_at: datetime.datetime | None = None


class MfAmc(SQLModel, table=True):
    """Asset Management Companies — dim table feeding mf_scheme.fund_house_id and
    mf_metadata.fund_house_id. ~50 distinct rows in production today; was being repeated
    14K+ times across amfi_schemes before normalisation.
    """

    __tablename__ = "mf_amc"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class MfCategory(SQLModel, table=True):
    """Scheme categories — dim table feeding mf_scheme.category_id and
    mf_metadata.category_id. ~50 distinct rows; same dedup story as MfAmc.
    """

    __tablename__ = "mf_category"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class MfScheme(SQLModel, table=True):
    """Canonical scheme dim table (renamed from amfi_schemes).

    Single source of truth for scheme identity — every MF table FKs here on `scheme_code`.
    For ~50 codeless registry funds (segregated portfolios, side-pockets) we assign synthetic
    negative scheme_codes during Phase 2's backfill; AMFI never issues negative codes so the
    namespaces don't collide.
    """

    __tablename__ = "amfi_schemes"  # rename to mf_scheme happens via migration ALTER
    __table_args__ = TABLE_ARGS

    scheme_code: int = Field(primary_key=True)
    isin_growth: str | None = Field(default=None, index=True)
    isin_reinvestment: str | None = None
    scheme_name: str
    nav: float | None = None
    nav_date: datetime.date | None = None
    fund_house_id: int | None = Field(default=None, foreign_key="mf_amc.id", index=True)
    category_id: int | None = Field(default=None, foreign_key="mf_category.id", index=True)


# Backwards-compat alias for callers still using the old name. New code should import
# `MfScheme` directly.
AmfiScheme = MfScheme


class MfMetadata(SQLModel, table=True):
    """AdvisorKhoj-sourced metadata. Phase 2: PK is now scheme_code (was scheme_name)."""

    __tablename__ = "mf_metadata"
    __table_args__ = TABLE_ARGS

    scheme_code: int = Field(primary_key=True, foreign_key="amfi_schemes.scheme_code")
    aum_crores: float | None = None  # Total Assets in ₹ Cr
    aum_as_of: datetime.date | None = None  # AUM disclosure date
    expense_ratio: float | None = None  # TER %
    expense_ratio_as_of: datetime.date | None = None
    fund_manager: str | None = None
    benchmark: str | None = None
    launch_date: datetime.date | None = None
    exit_load: str | None = None  # full exit load schedule (long text)
    asset_class: str | None = None
    status: str | None = None  # Open Ended / Close Ended
    min_investment: float | None = None
    min_topup: float | None = None
    turnover_ratio: float | None = None  # %
    source_url: str | None = None
    fetched_at: datetime.datetime | None = None
    # FKs into mf_amc / mf_category — replaced legacy fund_house / category text columns.
    fund_house_id: int | None = Field(default=None, foreign_key="mf_amc.id", index=True)
    category_id: int | None = Field(default=None, foreign_key="mf_category.id", index=True)


class MfSchemeMetrics(SQLModel, table=True):
    """Cached output of services.mf_metrics.compute_metrics_for_scheme.

    Persisting metrics removes the per-page-render quantstats burst — the MF Screener
    and Risk-vs-Return view become single-SELECT reads. `computed_at_nav_date` is the
    last NAV date observed when the row was computed; if it lags MfNav.max(date) for
    the same scheme, the row is stale and gets recomputed by scripts/compute_metrics.py.
    """

    __tablename__ = "mf_scheme_metrics"
    __table_args__ = TABLE_ARGS

    scheme_code: int = Field(primary_key=True, foreign_key="amfi_schemes.scheme_code")

    # 1Y / 3Y / 5Y / 10Y CAGR (decimals, not %)
    cagr_1y: float | None = None
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    cagr_10y: float | None = None

    # 1Y risk-adjusted ratios
    vol_1y: float | None = None
    downside_vol_1y: float | None = None  # annualised stddev of negative returns only
    sharpe_1y: float | None = None
    sortino_1y: float | None = None
    calmar_1y: float | None = None  # CAGR / |Max DD|
    gain_to_pain_1y: float | None = None  # sum(gains) / sum(|losses|)

    # 1Y drawdown / cumulative
    max_dd_1y: float | None = None
    cumulative_return_1y: float | None = None  # (1+r).prod() - 1
    avg_daily_return_1y: float | None = None  # r.mean()

    # 1Y distribution stats
    win_rate_1y: float | None = None  # fraction of positive days
    best_day_1y: float | None = None
    worst_day_1y: float | None = None
    var_95_1y: float | None = None  # 5% Value at Risk (negative number)
    cvar_95_1y: float | None = None  # Conditional VaR (expected loss in worst 5%)
    skew_1y: float | None = None
    kurt_1y: float | None = None

    # 1Y position-sizing diagnostics
    kelly_1y: float | None = None
    avg_win_1y: float | None = None
    avg_loss_1y: float | None = None
    payoff_ratio_1y: float | None = None  # |avg_win| / |avg_loss|

    # All-time
    max_dd_all: float | None = None
    pct_from_ath: float | None = None

    # Absolute (cumulative) returns over short windows — useful when annualisation distorts.
    abs_return_3m: float | None = None
    abs_return_6m: float | None = None
    abs_return_1y: float | None = None

    # Holdings composition (sum of weights by asset class). Persisted here so the screener
    # doesn't have to read mf_holdings on every page render.
    pct_equity: float | None = None
    pct_debt: float | None = None
    pct_cash: float | None = None

    # Concentration: sum of top-N weights.
    pct_top3: float | None = None
    pct_top5: float | None = None
    pct_top10: float | None = None

    # CAPM stats vs Nifty 50 (1Y window, ≥60 overlapping days; NaN otherwise).
    alpha_1y: float | None = None
    beta_1y: float | None = None
    r2_1y: float | None = None
    tracking_error_1y: float | None = None

    # First NAV observation — proxy for inception date when launch_date isn't in metadata.
    inception_date: datetime.date | None = None

    # Rolling annualised-CAGR distribution: at every NAV date, compute the N-year return ending
    # there and annualise. Then summarise that series with min/median/mean/max.
    # 1Y windows
    rolling_1y_min: float | None = None
    rolling_1y_median: float | None = None
    rolling_1y_mean: float | None = None
    rolling_1y_max: float | None = None
    # 3Y windows
    rolling_3y_min: float | None = None
    rolling_3y_median: float | None = None
    rolling_3y_mean: float | None = None
    rolling_3y_max: float | None = None
    # 5Y windows
    rolling_5y_min: float | None = None
    rolling_5y_median: float | None = None
    rolling_5y_mean: float | None = None
    rolling_5y_max: float | None = None

    # Latest NAV state at compute time
    last_nav: float | None = None
    last_nav_date: datetime.date | None = None
    history_days: int | None = None

    # Provenance — used to detect staleness vs MfNav.max(date)
    computed_at: datetime.datetime | None = None
    computed_at_nav_date: datetime.date | None = None


class MfTradebook(SQLModel, table=True):
    """Kite/Zerodha trade rows. Phase 3: `scheme_code` is denormalised on import via
    ISIN→amfi_schemes resolution. ISIN stays as the source-of-truth from the broker.
    """

    __tablename__ = "mf_tradebook"
    __table_args__ = TABLE_ARGS

    trade_id: str = Field(primary_key=True)
    symbol: str
    isin: str
    trade_date: datetime.date
    scheme_code: int | None = Field(default=None, foreign_key="amfi_schemes.scheme_code", index=True)
    exchange: str | None = None
    segment: str | None = None
    series: str | None = None
    trade_type: str
    auction: str | None = None
    quantity: float
    price: float
    order_id: str | None = None
    order_execution_time: str | None = None
