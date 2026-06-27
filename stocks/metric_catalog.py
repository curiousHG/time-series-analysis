"""Display catalog for the stock screener — column renames, groups, alpha categorisation.

Parallels mutual_funds/metric_catalog.py.
"""

from __future__ import annotations

from stocks.constants import ALPHA_POSITIVE, BETA_MARKET

# db column → display name.
STOCK_METRIC_RENAME: dict[str, str] = {
    "symbol": "Symbol",
    "stock_name": "Name",
    "market_cap": "Market Cap (Cr)",
    "current_price": "Price",
    "stock_pe": "P/E",
    "book_value": "Book Value",
    "dividend_yield": "Div Yield %",
    "roce": "ROCE %",
    "roe": "ROE %",
    "sales_latest_q": "Sales (Q)",
    "net_profit_latest_q": "Net Profit (Q)",
    "opm_latest_q": "OPM % (Q)",
    "eps_latest_q": "EPS (Q)",
    "yoy_sales_growth": "YoY Sales %",
    "yoy_profit_growth": "YoY Profit %",
    "qoq_sales_growth": "QoQ Sales %",
    "qoq_profit_growth": "QoQ Profit %",
    "promoter_holding": "Promoter %",
    "promoter_holding_change_1y": "Promoter Δ1y",
    "return_1y": "1Y Return %",
    "vol_1y": "1Y Vol %",
    "beta_1y": "Beta",
    "alpha_1y": "Alpha %",
    "r2_1y": "R²",
    "alpha_category": "Alpha Category",
}

STOCK_METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "Identity": ("Symbol", "Name"),
    "Valuation": ("Market Cap (Cr)", "Price", "P/E", "Book Value", "Div Yield %"),
    "Quality": ("ROCE %", "ROE %", "Promoter %", "Promoter Δ1y"),
    "Latest quarter": ("Sales (Q)", "Net Profit (Q)", "OPM % (Q)", "EPS (Q)", "YoY Sales %", "YoY Profit %"),
    "Price (CAPM vs Nifty)": ("1Y Return %", "1Y Vol %", "Beta", "Alpha %", "Alpha Category"),
}

DEFAULT_VISIBLE_COLS: tuple[str, ...] = (
    "Name",
    "Market Cap (Cr)",
    "P/E",
    "ROE %",
    "YoY Profit %",
    "1Y Return %",
    "Alpha %",
    "Beta",
    "Alpha Category",
)

# Scatter axis choices (display label → db column).
SCATTER_AXES: dict[str, str] = {
    "Alpha %": "alpha_1y",
    "Beta": "beta_1y",
    "1Y Return %": "return_1y",
    "1Y Vol %": "vol_1y",
    "ROE %": "roe",
    "P/E": "stock_pe",
}


def alpha_category(alpha: float | None, beta: float | None) -> str:
    """Categorise a stock on the alpha/beta plane (the screener's headline grouping)."""
    if alpha is None or beta is None:
        return "Unrated"
    perf = "Outperformer" if alpha > ALPHA_POSITIVE else "Laggard"
    risk = "aggressive" if beta >= BETA_MARKET else "defensive"
    return f"{perf} ({risk})"


# Stable colour per category for the scatter.
CATEGORY_COLORS: dict[str, str] = {
    "Outperformer (defensive)": "#10b981",
    "Outperformer (aggressive)": "#6366f1",
    "Laggard (defensive)": "#f59e0b",
    "Laggard (aggressive)": "#ef4444",
    "Unrated": "#64748b",
}
