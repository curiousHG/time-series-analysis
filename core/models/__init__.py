"""SQLModel ORM models — re-exports all models for backward compatibility."""

from core.models.backtest import BacktestOptimization, BacktestRun, BacktestTrade, BacktestTrial
from core.models.mutual_fund import (
    AmfiScheme,
    MfAaum,
    MfAmc,
    MfAssetAllocation,
    MfCategory,
    MfHolding,
    MfMetadata,
    MfNav,
    MfRegistry,
    MfSchemeMetrics,
    MfSectorAllocation,
    MfTradebook,
)
from core.models.stock import (
    IndexOhlcv,
    IndexRegistry,
    IndexTri,
    StockMetrics,
    StockOhlcv,
    StockQuarterly,
    StockRegistry,
)
