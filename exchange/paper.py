"""Paper exchange — simulated trading with no real orders."""

import logging
from datetime import datetime

import pandas as pd

from core.enums import OrderSide, OrderType
from exchange.base import ExchangeBase

logger = logging.getLogger("exchange.paper")


class PaperExchange(ExchangeBase):
    """Simulated exchange for paper trading and backtesting.

    Maintains an in-memory balance and order book.
    All orders fill immediately at the last known price.
    """

    def __init__(self, initial_balance: float = 100_000.0, currency: str = "INR"):
        self._balance: dict[str, float] = {currency: initial_balance}
        self._currency = currency
        self._orders: list[dict] = []
        self._next_order_id = 1
        self._last_prices: dict[str, float] = {}

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        since: datetime | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch OHLCV from the data repository."""
        from datetime import timedelta

        from data.repositories.stock import ensure_stock_data

        end = datetime.now()
        start = since or (end - timedelta(days=limit))
        df = ensure_stock_data(symbol, start, end)
        return df.to_pandas() if hasattr(df, "to_pandas") else df

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> dict:
        """Simulate order fill at last known price."""
        fill_price = price or self._last_prices.get(symbol, 0.0)
        if fill_price <= 0:
            return {"order_id": None, "status": "rejected", "reason": "no price available"}

        order_id = f"paper-{self._next_order_id}"
        self._next_order_id += 1

        cost = fill_price * quantity
        if side == OrderSide.BUY:
            if self._balance.get(self._currency, 0) < cost:
                return {"order_id": order_id, "status": "rejected", "reason": "insufficient funds"}
            self._balance[self._currency] -= cost
            self._balance[symbol] = self._balance.get(symbol, 0) + quantity
        else:
            if self._balance.get(symbol, 0) < quantity:
                return {"order_id": order_id, "status": "rejected", "reason": "insufficient holdings"}
            self._balance[symbol] -= quantity
            self._balance[self._currency] += cost

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side.value,
            "quantity": quantity,
            "price": fill_price,
            "status": "filled",
            "filled_at": datetime.utcnow().isoformat(),
        }
        self._orders.append(order)
        logger.info("Paper %s %s x%.2f @ %.2f", side.value, symbol, quantity, fill_price)
        return order

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Paper orders fill immediately — nothing to cancel."""
        return False

    def get_balance(self) -> dict[str, float]:
        return dict(self._balance)

    def get_ticker(self, symbol: str) -> dict:
        return {"last_price": self._last_prices.get(symbol, 0.0), "symbol": symbol}

    def set_price(self, symbol: str, price: float):
        """Manually set the last known price for a symbol (used in backtesting)."""
        self._last_prices[symbol] = price
