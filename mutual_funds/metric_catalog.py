"""Single source of truth for mutual-fund metric column metadata.

Keeps renames, groupings, and formatting rules from drifting across views that surface
these metrics (primarily the MF Screener).
"""

from __future__ import annotations

# Always-shown identifying columns (display names). AMC/Plan/Option are excluded — they're
# driven by the screener's sidebar/inline filters, so columns would waste horizontal space.
IDENTITY_COLS: tuple[str, ...] = ("Scheme", "Category", "Sub-category")

# DB column name → display column name; downstream code only deals with display names.
METRIC_RENAME: dict[str, str] = {
    "scheme_name": "Scheme",
    "fund_house": "AMC",
    "category": "Category",
    "sub_category": "Sub-category",
    "plan": "Plan",
    "option": "Option",
    "aum_crores": "AUM (₹ Cr)",
    "expense_ratio": "TER %",
    "benchmark": "Benchmark",
    "cagr_1y": "1Y CAGR %",
    "cagr_3y": "3Y CAGR %",
    "cagr_5y": "5Y CAGR %",
    "cagr_10y": "10Y CAGR %",
    "cumulative_return_1y": "1Y Cum Return %",
    "avg_daily_return_1y": "Avg Daily Return %",
    "vol_1y": "1Y Vol %",
    "downside_vol_1y": "1Y Downside Vol %",
    "sharpe_1y": "Sharpe",
    "sortino_1y": "Sortino",
    "calmar_1y": "Calmar",
    "gain_to_pain_1y": "Gain/Pain",
    "max_dd_1y": "Max DD 1Y %",
    "max_dd_all": "Max DD All %",
    "pct_from_ath": "% from ATH",
    "win_rate_1y": "Win Rate %",
    "best_day_1y": "Best Day %",
    "worst_day_1y": "Worst Day %",
    "var_95_1y": "VaR 95% (Daily)",
    "cvar_95_1y": "CVaR 95% (Daily)",
    "skew_1y": "Skew",
    "kurt_1y": "Kurtosis",
    "kelly_1y": "Kelly %",
    "avg_win_1y": "Avg Win %",
    "avg_loss_1y": "Avg Loss %",
    "payoff_ratio_1y": "Payoff",
    "pct_equity": "% Equity",
    "pct_debt": "% Debt",
    "pct_cash": "% Cash",
    "pct_top3": "% Top 3",
    "pct_top5": "% Top 5",
    "pct_top10": "% Top 10",
    "abs_return_3m": "3M Return %",
    "abs_return_6m": "6M Return %",
    "abs_return_1y": "1Y Abs Return %",
    "alpha_1y": "Alpha (1Y) %",
    "beta_1y": "Beta (1Y)",
    "r2_1y": "R² (1Y)",
    "tracking_error_1y": "Tracking Error %",
    "inception_date": "Inception",
    # Rolling annualised-CAGR distribution (1/3/5Y windows), stored as decimal fractions.
    "rolling_1y_min": "Roll 1Y Min %",
    "rolling_1y_median": "Roll 1Y Median %",
    "rolling_1y_mean": "Roll 1Y Mean %",
    "rolling_1y_max": "Roll 1Y Max %",
    "rolling_3y_min": "Roll 3Y Min %",
    "rolling_3y_median": "Roll 3Y Median %",
    "rolling_3y_mean": "Roll 3Y Mean %",
    "rolling_3y_max": "Roll 3Y Max %",
    "rolling_5y_min": "Roll 5Y Min %",
    "rolling_5y_median": "Roll 5Y Median %",
    "rolling_5y_mean": "Roll 5Y Mean %",
    "rolling_5y_max": "Roll 5Y Max %",
}

# Logical groupings (display names). List order = column order in the table.
METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "Fund metadata": ("AUM (₹ Cr)", "TER %", "Benchmark", "Inception"),
    "Returns": (
        "3M Return %",
        "6M Return %",
        "1Y Abs Return %",
        "1Y CAGR %",
        "3Y CAGR %",
        "5Y CAGR %",
        "10Y CAGR %",
        "1Y Cum Return %",
        "Avg Daily Return %",
    ),
    "Rolling returns": (
        "Roll 1Y Min %",
        "Roll 1Y Median %",
        "Roll 1Y Mean %",
        "Roll 1Y Max %",
        "Roll 3Y Min %",
        "Roll 3Y Median %",
        "Roll 3Y Mean %",
        "Roll 3Y Max %",
        "Roll 5Y Min %",
        "Roll 5Y Median %",
        "Roll 5Y Mean %",
        "Roll 5Y Max %",
    ),
    "Risk-adjusted ratios": ("1Y Vol %", "1Y Downside Vol %", "Sharpe", "Sortino", "Calmar", "Gain/Pain"),
    "CAPM vs category benchmark": ("Alpha (1Y) %", "Beta (1Y)", "R² (1Y)", "Tracking Error %"),
    "Drawdown": ("Max DD 1Y %", "Max DD All %", "% from ATH"),
    "Distribution stats": (
        "Win Rate %",
        "Best Day %",
        "Worst Day %",
        "VaR 95% (Daily)",
        "CVaR 95% (Daily)",
        "Skew",
        "Kurtosis",
    ),
    "Position sizing": ("Kelly %", "Avg Win %", "Avg Loss %", "Payoff"),
    "Holdings composition": ("% Equity", "% Debt", "% Cash"),
    "Concentration": ("% Top 3", "% Top 5", "% Top 10"),
    "Tracking status": ("NAV", "Holdings", "Metadata"),
}

# Columns whose stored values are decimal fractions and need x100 for display.
METRIC_PCT_COLS: tuple[str, ...] = (
    "1Y CAGR %",
    "3Y CAGR %",
    "5Y CAGR %",
    "10Y CAGR %",
    "1Y Cum Return %",
    "Avg Daily Return %",
    "1Y Vol %",
    "1Y Downside Vol %",
    "Max DD 1Y %",
    "Max DD All %",
    "% from ATH",
    "Win Rate %",
    "Best Day %",
    "Worst Day %",
    "VaR 95% (Daily)",
    "CVaR 95% (Daily)",
    "Avg Win %",
    "Avg Loss %",
    "3M Return %",
    "6M Return %",
    "1Y Abs Return %",
    "Alpha (1Y) %",
    "Tracking Error %",
    "Roll 1Y Min %",
    "Roll 1Y Median %",
    "Roll 1Y Mean %",
    "Roll 1Y Max %",
    "Roll 3Y Min %",
    "Roll 3Y Median %",
    "Roll 3Y Mean %",
    "Roll 3Y Max %",
    "Roll 5Y Min %",
    "Roll 5Y Median %",
    "Roll 5Y Mean %",
    "Roll 5Y Max %",
    "% Equity",
    "% Debt",
    "% Cash",
    "% Top 3",
    "% Top 5",
    "% Top 10",
)

# Columns that should use AgGrid's numeric filter.
METRIC_NUMERIC_COLS: frozenset[str] = frozenset(
    {
        *METRIC_PCT_COLS,
        "AUM (₹ Cr)",
        "TER %",
        "Sharpe",
        "Sortino",
        "Calmar",
        "Gain/Pain",
        "Skew",
        "Kurtosis",
        "Kelly %",
        "Payoff",
        "Beta (1Y)",
        "R² (1Y)",
    }
)

# Columns that should use AgGrid's text filter.
METRIC_TEXT_COLS: frozenset[str] = frozenset(
    {"Scheme", "AMC", "Category", "Sub-category", "Benchmark", "Plan", "Option", "NAV", "Holdings", "Metadata"}
)

# Left-to-right column order for the screener table: identity first, then by group.
DISPLAY_COL_ORDER: tuple[str, ...] = IDENTITY_COLS + tuple(c for cols in METRIC_GROUPS.values() for c in cols)

# Flat ordered list of every non-identity metric column — feeds the sidebar multiselect.
ALL_METRIC_COLS: tuple[str, ...] = tuple(c for cols in METRIC_GROUPS.values() for c in cols)

# Lean default metric set shown on first load; user adds more from the sidebar multiselect.
DEFAULT_VISIBLE_METRICS: tuple[str, ...] = (
    "AUM (₹ Cr)",
    "TER %",
    "1Y CAGR %",
    "3Y CAGR %",
    "Sharpe",
    "Max DD 1Y %",
)

# --- Risk-vs-Return chart axis catalog --------------------------------------------------
# X-axis risk measures. Each entry is (db_column, display label, take_abs?); take_abs flips
# sign so a negative-stored stat (Max DD, CVaR) reads as a positive magnitude.
RISK_AXIS_OPTIONS: dict[str, tuple[str, str, bool]] = {
    "vol_1y": ("vol_1y", "Annualised Realised Volatility", False),
    "downside_vol_1y": ("downside_vol_1y", "Downside Deviation", False),
    "cvar_95_1y": ("cvar_95_1y", "Expected Shortfall (CVaR 95%, daily)", True),
    "max_dd_1y": ("max_dd_1y", "Max Drawdown", True),
}

# Y-axis return measures; some are derived (see services.screener_service.resolve_return_axis).
# alpha_1y is the CAPM intercept vs each fund's category benchmark (services.benchmarks.
# SUBCATEGORY_BENCHMARK), not a single index; only ir_numerator_1y is still vs Nifty 50.
RETURN_AXIS_OPTIONS: dict[str, str] = {
    "excess_return_1y": "Annualised Geometric Excess Return (vs RF)",
    "alpha_1y": "Jensen's Alpha (vs category benchmark)",
    "ir_numerator_1y": "Information Ratio numerator (active return vs Nifty 50)",
}

# Benchmark-relative measures; the screener shows a caveat caption when any is picked. All but
# ir_numerator_1y use each fund's category benchmark; ir_numerator_1y still uses Nifty 50.
BENCHMARK_DEPENDENT: frozenset[str] = frozenset(
    {"alpha_1y", "beta_1y", "tracking_error_1y", "r2_1y", "ir_numerator_1y"}
)

# Risk-free rate for the "Annualised Geometric Excess Return" axis. Mirrors
# services.mf_metrics.RISK_FREE_ANNUAL so it lines up with Sharpe-rf assumptions.
RISK_FREE_RATE_FOR_EXCESS_RETURN: float = 0.06


# DB column names for metric placeholders when the cache is empty; mirror data.constants.METRIC_FIELDS.
EMPTY_METRIC_DB_COLS: tuple[str, ...] = (
    "cagr_1y",
    "cagr_3y",
    "cagr_5y",
    "cagr_10y",
    "vol_1y",
    "downside_vol_1y",
    "sharpe_1y",
    "sortino_1y",
    "calmar_1y",
    "gain_to_pain_1y",
    "max_dd_1y",
    "cumulative_return_1y",
    "avg_daily_return_1y",
    "win_rate_1y",
    "best_day_1y",
    "worst_day_1y",
    "var_95_1y",
    "cvar_95_1y",
    "skew_1y",
    "kurt_1y",
    "kelly_1y",
    "avg_win_1y",
    "avg_loss_1y",
    "payoff_ratio_1y",
    "max_dd_all",
    "pct_from_ath",
    "abs_return_3m",
    "abs_return_6m",
    "abs_return_1y",
    "pct_equity",
    "pct_debt",
    "pct_cash",
    "pct_top3",
    "pct_top5",
    "pct_top10",
    "alpha_1y",
    "beta_1y",
    "r2_1y",
    "tracking_error_1y",
    "rolling_1y_min",
    "rolling_1y_median",
    "rolling_1y_mean",
    "rolling_1y_max",
    "rolling_3y_min",
    "rolling_3y_median",
    "rolling_3y_mean",
    "rolling_3y_max",
    "rolling_5y_min",
    "rolling_5y_median",
    "rolling_5y_mean",
    "rolling_5y_max",
)
