"""SQLModel ORM models — re-exports all models for backward compatibility."""

from core.models.mutual_fund import (  # noqa: F401
    AmfiScheme,  # backwards-compat alias for MfScheme
    MfAmc,
    MfAssetAllocation,
    MfCategory,
    MfHolding,
    MfMetadata,
    MfNav,
    MfRegistry,
    MfScheme,
    MfSchemeMetrics,
    MfSectorAllocation,
    MfTradebook,
)
from core.models.stock import StockOhlcv, StockRegistry  # noqa: F401
from core.models.trading import Bot, Order, Trade  # noqa: F401
