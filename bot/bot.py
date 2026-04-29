"""Trading bot — ties together strategy, exchange, and data provider."""

import logging

from bot.data_provider import DataProvider
from core.enums import BotState, OrderSide
from exchange.base import ExchangeBase
from strategies.base import Strategy

logger = logging.getLogger("bot.bot")


class TradingBot:
    """A single trading bot instance.

    Holds a strategy, exchange, and data provider.
    The Worker manages the bot lifecycle; this class handles per-tick logic.
    """

    def __init__(
        self,
        name: str,
        strategy: Strategy,
        exchange: ExchangeBase,
        data_provider: DataProvider,
        symbol: str,
        stake_amount: float = 10_000.0,
    ):
        self.name = name
        self.strategy = strategy
        self.exchange = exchange
        self.data_provider = data_provider
        self.symbol = symbol
        self.stake_amount = stake_amount
        self.state = BotState.STOPPED
        self._position_open = False

    def process(self):
        """Run one tick of the trading loop.

        1. Fetch latest candles
        2. Compute indicators
        3. Check entry/exit signals
        4. Place orders if needed
        """
        ohlcv = self.data_provider.ohlcv(self.symbol)
        if ohlcv.empty:
            logger.warning("No data for %s", self.symbol)
            return

        price = ohlcv.set_index("Date")["Close"].dropna()
        if len(price) < 30:
            logger.info("Not enough data for %s (%d rows)", self.symbol, len(price))
            return

        indicators = self.strategy.indicators(price)
        entries, exits = self.strategy.signals(price, indicators)

        latest_entry = bool(entries.iloc[-1]) if not entries.empty else False
        latest_exit = bool(exits.iloc[-1]) if not exits.empty else False

        if latest_entry and not self._position_open:
            self._open_position(price.iloc[-1])
        elif latest_exit and self._position_open:
            self._close_position(price.iloc[-1])

    def _open_position(self, price: float):
        quantity = self.stake_amount / price
        result = self.exchange.place_order(self.symbol, OrderSide.BUY, quantity, price)
        if result.get("status") == "filled":
            self._position_open = True
            logger.info("[%s] Opened position: %s x%.4f @ %.2f", self.name, self.symbol, quantity, price)

    def _close_position(self, price: float):
        balance = self.exchange.get_balance()
        quantity = balance.get(self.symbol, 0.0)
        if quantity <= 0:
            self._position_open = False
            return

        result = self.exchange.place_order(self.symbol, OrderSide.SELL, quantity, price)
        if result.get("status") == "filled":
            self._position_open = False
            logger.info("[%s] Closed position: %s x%.4f @ %.2f", self.name, self.symbol, quantity, price)
