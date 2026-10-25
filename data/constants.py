"""Data-package constants: external URLs/headers, fetch thresholds, schema field maps."""

from __future__ import annotations

import polars as pl

from core.models import MfSchemeMetrics

MFAPI_BASE_URL = "https://api.mfapi.in/mf"
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
BASE_OVERVIEW_URL = "https://www.advisorkhoj.com/mutual-funds-research/{scheme_name}"
NAV_URL = "https://www.advisorkhoj.com/mutual-funds-research/getCompleteNavReportForFundOverview"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}

NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NIFTY500_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}

MIN_FETCH_DAYS = 5
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

METRIC_FIELDS: tuple[str, ...] = (
    "cagr_1y", "cagr_3y", "cagr_5y", "cagr_10y",
    "vol_1y", "downside_vol_1y", "sharpe_1y", "sortino_1y", "calmar_1y", "gain_to_pain_1y",
    "max_dd_1y", "cumulative_return_1y", "avg_daily_return_1y",
    "win_rate_1y", "best_day_1y", "worst_day_1y", "var_95_1y", "cvar_95_1y", "skew_1y", "kurt_1y",
    "kelly_1y", "avg_win_1y", "avg_loss_1y", "payoff_ratio_1y",
    "max_dd_all", "pct_from_ath",
    "abs_return_3m", "abs_return_6m", "abs_return_1y",
    "pct_equity", "pct_debt", "pct_cash",
    "pct_top3", "pct_top5", "pct_top10",
    "alpha_1y", "beta_1y", "r2_1y", "tracking_error_1y",
    "rolling_1y_min", "rolling_1y_median", "rolling_1y_mean", "rolling_1y_max",
    "rolling_3y_min", "rolling_3y_median", "rolling_3y_mean", "rolling_3y_max",
    "rolling_5y_min", "rolling_5y_median", "rolling_5y_mean", "rolling_5y_max",
    "inception_date", "last_nav", "last_nav_date", "history_days",
)  # fmt: skip

METRIC_COLS = [c for c in MfSchemeMetrics.__table__.columns if c.name != "scheme_code"]
