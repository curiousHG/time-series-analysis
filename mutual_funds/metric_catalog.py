"""Single source of truth for mutual-fund metric column metadata.

Used by the MF Screener (and any other view that surfaces these metrics) so renames,
groupings, and formatting rules don't drift across pages.

What lives here:
  • IDENTITY_COLS         — display names for the columns that identify a row.
  • METRIC_GROUPS         — logical buckets of metric columns (display names).
  • METRIC_RENAME         — DB column → friendly display name.
  • METRIC_PCT_COLS       — columns whose stored value is a decimal fraction
                             and needs x100 to render as a percent.
  • METRIC_NUMERIC_COLS   — columns that should use AgGrid's numeric filter.
  • METRIC_TEXT_COLS      — columns that should use AgGrid's text filter.
  • DISPLAY_COL_ORDER     — canonical left-to-right order for the screener table.
  • DEFAULT_VISIBLE_METRICS — the lean default set of metric columns to show.
"""

from __future__ import annotations

# Always-shown identifying columns (display names, post-rename). AMC / Plan / Option are
# intentionally excluded — they're driven by sidebar / inline filters in the screener,
# so showing them as table columns wastes horizontal space.
IDENTITY_COLS: tuple[str, ...] = ("Scheme", "Category")

# DB column name → display column name. Stored separately from grouping so renames stay
# in one place; downstream code only deals with display names.
METRIC_RENAME: dict[str, str] = {
    "scheme_name": "Scheme",
    "fund_house": "AMC",
    "category": "Category",
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
    # Rolling annualised-CAGR distribution (1/3/5Y windows). Stored as decimal fractions.
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

# Logical groupings — display names. Ordering within each list = column order in the table.
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
    "CAPM vs Nifty 50": ("Alpha (1Y) %", "Beta (1Y)", "R² (1Y)", "Tracking Error %"),
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

# Columns that should use AgGrid's numeric column type / numeric filter.
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

# Columns that should use AgGrid's text filter (substring/contains).
METRIC_TEXT_COLS: frozenset[str] = frozenset(
    {"Scheme", "AMC", "Category", "Benchmark", "Plan", "Option", "NAV", "Holdings", "Metadata"}
)

# Canonical left-to-right column order for the screener table. Identity first, then by group.
DISPLAY_COL_ORDER: tuple[str, ...] = IDENTITY_COLS + tuple(c for cols in METRIC_GROUPS.values() for c in cols)

# A flat ordered list of every metric (non-identity) column — feeds the sidebar multiselect.
ALL_METRIC_COLS: tuple[str, ...] = tuple(c for cols in METRIC_GROUPS.values() for c in cols)

# Default metrics shown on first load. Lean enough to fit without horizontal scroll on a
# typical laptop; user adds more from the sidebar multiselect as needed.
DEFAULT_VISIBLE_METRICS: tuple[str, ...] = (
    "AUM (₹ Cr)",
    "TER %",
    "1Y CAGR %",
    "3Y CAGR %",
    "Sharpe",
    "Max DD 1Y %",
)

# DB column names for metric placeholders (when the cache is empty). Kept here so callers
# don't repeat the list — must mirror data/repositories/scheme_metrics._METRIC_FIELDS.
# --- Risk-vs-Return chart axis catalog --------------------------------------------------
# X options are downside-style risk measures; Y options are excess-of-benchmark / risk-free
# return measures. Each X entry is (db_column, display label, take_abs?) — `take_abs` flips
# sign so a negative-stored stat (Max DD, CVaR) reads as a positive magnitude on the axis.
RISK_AXIS_OPTIONS: dict[str, tuple[str, str, bool]] = {
    "vol_1y": ("vol_1y", "Annualised Realised Volatility", False),
    "downside_vol_1y": ("downside_vol_1y", "Downside Deviation", False),
    "cvar_95_1y": ("cvar_95_1y", "Expected Shortfall (CVaR 95%, daily)", True),
    "max_dd_1y": ("max_dd_1y", "Max Drawdown", True),
}

# Y axes — return measures. Some are derived rather than direct cache reads (excess return
# subtracts the risk-free rate, IR numerator subtracts Nifty 50's CAGR). The concrete
# derivation lives in services.screener_service.resolve_return_axis.
RETURN_AXIS_OPTIONS: dict[str, str] = {
    "excess_return_1y": "Annualised Geometric Excess Return (vs RF)",
    "alpha_1y": "Jensen's Alpha (vs Nifty 50)",
    "ir_numerator_1y": "Information Ratio numerator (active return vs Nifty 50)",
}

# Anything keyed off Nifty 50 — the screener surfaces a benchmark caveat caption when any
# of these axes are picked.
BENCHMARK_DEPENDENT: frozenset[str] = frozenset(
    {"alpha_1y", "beta_1y", "tracking_error_1y", "r2_1y", "ir_numerator_1y"}
)

# Risk-free rate used for the "Annualised Geometric Excess Return" axis. Mirrors
# services.mf_metrics.RISK_FREE_ANNUAL so excess returns line up with Sharpe-rf assumptions.
RISK_FREE_RATE_FOR_EXCESS_RETURN: float = 0.06


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
