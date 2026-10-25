"""UI-package constants: risk-free rate, screener filter defaults, chart modes.

Grouped by the view/concern that owns each block.
"""

from __future__ import annotations

from pathlib import Path

from mutual_funds.metric_catalog import DEFAULT_VISIBLE_METRICS

RISK_FREE = 0.065
RF_DAILY = RISK_FREE / 252

SELECTIONS_PATH = Path("data/user/selections.json")

SCREENER_PERSIST_KEY = "screener_filters"
FILTER_DEFAULTS = {
    "screener_name_query": "",
    "screener_amcs": [],
    "screener_cats": [],
    "screener_sub_cats": [],
    "screener_plans": ["Direct"],
    "screener_options": ["Growth"],
    "screener_aum_min": 0,
    "screener_ter_max": 2.5,
    "screener_min_age": 0.0,
    "screener_only_untracked": False,
    "screener_has_nav": False,
    "screener_visible_metrics": list(DEFAULT_VISIBLE_METRICS),
}
SLIDER_DEFAULTS = {
    "screener_cagr_min": -50,
    "screener_sharpe_min": -2.0,
    "screener_dd_min": -100,
}

STOCK_SCREENER_PERSIST_KEY = "stock_screener_filters"
STOCK_FILTER_DEFAULTS: dict = {
    "stock_scr_name": "",
    "stock_scr_cats": [],
    "stock_scr_mcap": 0.0,
    "stock_scr_pe": 0.0,
    "stock_scr_roe": 0.0,
    "stock_scr_alpha": 0.0,
    "stock_scr_visible": None,
}

BACKTEST_PERSIST_KEY = "backtest_filters"
BACKTEST_FILTER_DEFAULTS: dict = {
    "bt_filter_name": "",
    "bt_filter_status": [],
    "bt_filter_strategy": [],
}
BACKTEST_SELECTION_KEY = "backtest_selected_run"

BACKFILL_HELP_TEXT = (
    "Fetch NAV + metadata for the top-N filtered rows and compute their risk/return metrics. "
    "Already-loaded funds are skipped. Holdings aren't fetched here — use Settings → *Update All Holdings*."
)

STATUS_STYLES = {
    "Fresh": "background-color: #86efac; color: #14532d",
    "Stale": "background-color: #fde68a; color: #78350f",
    "Missing": "background-color: #fca5a5; color: #7f1d1d",
    "Available": "background-color: #86efac; color: #14532d",
    "Pending": "background-color: #fde68a; color: #78350f",
    "Unavailable": "background-color: #fca5a5; color: #7f1d1d",
}

MODE_CAGR_VOL = "CAGR vs Volatility"
MODE_ALPHA_BETA = "Alpha vs Beta"
BUBBLE_SIZE = 14
PORTFOLIO_COLOUR = "#fbbf24"
