"""Custom exceptions for the trading platform."""


class TradingError(Exception):
    """Base exception for all trading errors."""


class DataFetchError(TradingError):
    """Error fetching data from an external source."""


class StrategyError(TradingError):
    """Error in strategy execution."""


class ExchangeError(TradingError):
    """Error communicating with an exchange."""


class InsufficientFundsError(ExchangeError):
    """Not enough balance to place order."""


class OrderError(ExchangeError):
    """Error placing or managing an order."""
