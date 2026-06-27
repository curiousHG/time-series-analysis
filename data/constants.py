"""Data-package constants: external URLs/headers, fetch thresholds, schema field maps."""

from __future__ import annotations

import polars as pl

from core.models import MfSchemeMetrics

# External data-source endpoints (data.fetchers.mutual_fund).
MFAPI_BASE_URL = "https://api.mfapi.in/mf"
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
BASE_OVERVIEW_URL = "https://www.advisorkhoj.com/mutual-funds-research/{scheme_name}"
NAV_URL = "https://www.advisorkhoj.com/mutual-funds-research/getCompleteNavReportForFundOverview"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}

# NSE equity master — the listed-stock universe (data.fetchers.stock.fetch_nse_equity_list).
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}

# niftyindices.com — authoritative source for Nifty index history (incl. Smallcap 250 /
# Midcap 150 that yfinance lacks). Params must be wrapped in a `cinfo` JSON string.
NIFTYINDICES_PAGE_URL = "https://niftyindices.com/reports/historical-data"
NIFTYINDICES_HISTORY_URL = "https://niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString"
NIFTYINDICES_HEADERS = {
    "Referer": NIFTYINDICES_PAGE_URL,
    "Origin": "https://niftyindices.com",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0 Safari/537.36",
    "Accept": "*/*",
    "Content-Type": "application/json; charset=UTF-8",
}

# Stock OHLCV (data.repositories.stock).
MIN_FETCH_DAYS = 5  # don't fetch ranges shorter than 5 days (avoids holiday/weekend gaps)
EMPTY_OHLCV = pl.DataFrame(
    schema={
        "Date": pl.Date,
        "Open": pl.Float64,
        "High": pl.Float64,
        "Low": pl.Float64,
        "Close": pl.Float64,
        "Volume": pl.Int64,
    }
)

# Polars column → ORM field mapping for holdings (data.repositories.holdings).
HOLDINGS_FIELD_MAP = {
    "portfolioDate": "portfolio_date",
    "instrumentName": "instrument_name",
    "isin": "isin",
    "issuerName": "issuer_name",
    "assetClass": "asset_class",
    "assetSubClass": "asset_sub_class",
    "assetType": "asset_type",
    "weight": "weight",
    "value": "value",
    "quantity": "quantity",
    "industry": "industry",
    "marketCapBucket": "market_cap",
    "creditRating": "credit_rating",
    "creditRatingEq": "credit_rating_eq",
}

# All cached metric field names (data.repositories.scheme_metrics).
# metric_catalog.EMPTY_METRIC_DB_COLS mirrors this set.
METRIC_FIELDS: tuple[str, ...] = (
    # CAGR
    "cagr_1y", "cagr_3y", "cagr_5y", "cagr_10y",
    # Risk-adjusted ratios
    "vol_1y", "downside_vol_1y", "sharpe_1y", "sortino_1y", "calmar_1y", "gain_to_pain_1y",
    # Drawdown / cumulative
    "max_dd_1y", "cumulative_return_1y", "avg_daily_return_1y",
    # Distribution stats
    "win_rate_1y", "best_day_1y", "worst_day_1y", "var_95_1y", "cvar_95_1y", "skew_1y", "kurt_1y",
    # Position-sizing diagnostics
    "kelly_1y", "avg_win_1y", "avg_loss_1y", "payoff_ratio_1y",
    # All-time
    "max_dd_all", "pct_from_ath",
    # Absolute returns
    "abs_return_3m", "abs_return_6m", "abs_return_1y",
    # Holdings composition
    "pct_equity", "pct_debt", "pct_cash",
    # Concentration
    "pct_top3", "pct_top5", "pct_top10",
    # CAPM vs Nifty 50
    "alpha_1y", "beta_1y", "r2_1y", "tracking_error_1y",
    # Rolling annualised-CAGR distribution (1/3/5 year windows)
    "rolling_1y_min", "rolling_1y_median", "rolling_1y_mean", "rolling_1y_max",
    "rolling_3y_min", "rolling_3y_median", "rolling_3y_mean", "rolling_3y_max",
    "rolling_5y_min", "rolling_5y_median", "rolling_5y_mean", "rolling_5y_max",
    # Provenance fields
    "inception_date", "last_nav", "last_nav_date", "history_days",
)  # fmt: skip

# Every column on mf_scheme_metrics except the join key, read off the table at import time
# so a new metric column on the model auto-flows into the screener SELECT.
METRIC_COLS = [c for c in MfSchemeMetrics.__table__.columns if c.name != "scheme_code"]
