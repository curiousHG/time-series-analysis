"""Base exchange interface — all exchanges implement this contract."""

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from core.enums import OrderSide, OrderType


class ExchangeBase(ABC):
    """Abstract base class for exchange integrations."""

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        since: datetime | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch OHLCV candle data.

        Returns DataFrame with columns: Date, Open, High, Low, Close, Volume.
        """

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> dict:
        """Place an order. Returns order info dict with at least 'order_id' and 'status'."""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order. Returns True if cancelled successfully."""

    @abstractmethod
    def get_balance(self) -> dict[str, float]:
        """Get account balances. Returns {currency: amount}."""

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """Get current ticker data. Returns dict with at least 'last_price'."""
