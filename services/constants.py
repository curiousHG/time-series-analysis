"""Services-package constants: risk-free rates, freshness windows, fetch concurrency."""

from __future__ import annotations

from enum import StrEnum

from core.constants import TRADING_DAYS


class BackfillSource(StrEnum):
    NAV = "nav"
    METADATA = "metadata"
    HOLDINGS = "holdings"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


class FetchOutcome(StrEnum):
    UPDATED = "updated"
    SKIPPED = "skipped"
    FAILED = "failed"


class RefreshScope(StrEnum):
    NAV = "nav"
    HOLDINGS = "holdings"
    ALL = "all"


BACKTEST_RISK_FREE = 0.065
BACKTEST_RF_DAILY = BACKTEST_RISK_FREE / TRADING_DAYS

RISK_FREE_ANNUAL = 0.06
RF_DAILY = RISK_FREE_ANNUAL / TRADING_DAYS

NAV_STALE_BUSINESS_DAYS = 1
HOLDINGS_STALE_DAYS = 35
DORMANT_AFTER_DAYS = 90

NAV_FETCH_WORKERS = 4
HOLDINGS_FETCH_WORKERS = 16
