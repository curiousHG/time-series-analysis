"""Worker — manages bot lifecycle with a state machine."""

import logging
from threading import Event, Thread

from bot.bot import TradingBot
from core.enums import BotState

logger = logging.getLogger("bot.worker")


class Worker:
    """Runs a TradingBot in a background thread with state management.

    States: STOPPED → RUNNING ↔ PAUSED → STOPPED

    The worker throttles execution to the bot's timeframe,
    sleeping between ticks to avoid hammering the exchange.
    """

    def __init__(self, bot: TradingBot, interval_seconds: float = 60.0):
        self.bot = bot
        self.interval = interval_seconds
        self._thread: Thread | None = None
        self._stop_event = Event()

    @property
    def state(self) -> BotState:
        return self.bot.state

    def start(self):
        """Start the worker loop in a background thread."""
        if self.bot.state == BotState.RUNNING:
            logger.warning("Worker %s is already running", self.bot.name)
            return

        self.bot.state = BotState.RUNNING
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, daemon=True, name=f"worker-{self.bot.name}")
        self._thread.start()
        logger.info("Worker %s started", self.bot.name)

    def stop(self):
        """Stop the worker loop gracefully."""
        self.bot.state = BotState.STOPPED
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 5)
        logger.info("Worker %s stopped", self.bot.name)

    def pause(self):
        """Pause the worker (keeps thread alive but skips processing)."""
        self.bot.state = BotState.PAUSED
        logger.info("Worker %s paused", self.bot.name)

    def resume(self):
        """Resume a paused worker."""
        if self.bot.state == BotState.PAUSED:
            self.bot.state = BotState.RUNNING
            logger.info("Worker %s resumed", self.bot.name)

    def _loop(self):
        """Main worker loop — runs bot.process() on each tick."""
        while not self._stop_event.is_set():
            if self.bot.state == BotState.RUNNING:
                try:
                    self.bot.process()
                except Exception as e:
                    logger.error("Worker %s error: %s", self.bot.name, e)

            self._stop_event.wait(timeout=self.interval)
