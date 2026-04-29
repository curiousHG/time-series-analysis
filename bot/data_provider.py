"""Data provider — unified interface for backtest and live data access."""

import logging
from datetime import date, datetime

import pandas as pd

from core.enums import RunMode
from exchange.base import ExchangeBase

logger = logging.getLogger("bot.data_provider")


class DataProvider:
    """Provides candle data to strategies, abstracting the data source.

    In backtest mode, reads from the database (historical data).
    In live/dry_run mode, fetches from the exchange.
    """

    def __init__(self, mode: RunMode, exchange: ExchangeBase | None = None):
        self._mode = mode
        self._exchange = exchange
        self._cache: dict[str, pd.DataFrame] = {}

    def ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: datetime | date | None = None,
        end: datetime | date | None = None,
    ) -> pd.DataFrame:
        """Get OHLCV data for a symbol."""
        if self._mode == RunMode.BACKTEST:
            return self._ohlcv_from_db(symbol, start, end)
        elif self._exchange:
            return self._exchange.get_ohlcv(symbol, timeframe, since=start)
        else:
            return self._ohlcv_from_db(symbol, start, end)

    def current_price(self, symbol: str) -> float | None:
        """Get the latest known price for a symbol."""
        if self._exchange:
            ticker = self._exchange.get_ticker(symbol)
            return ticker.get("last_price")

        # Fallback: last row from cached OHLCV
        if symbol in self._cache and not self._cache[symbol].empty:
            return float(self._cache[symbol]["Close"].iloc[-1])
        return None

    def _ohlcv_from_db(
        self,
        symbol: str,
        start: datetime | date | None,
        end: datetime | date | None,
    ) -> pd.DataFrame:
        from data.repositories.stock import ensure_stock_data

        start = start or datetime(2020, 1, 1)
        end = end or datetime.now()
        df = ensure_stock_data(symbol, start, end)
        pdf = df.to_pandas() if hasattr(df, "to_pandas") else df
        self._cache[symbol] = pdf
        return pdf
