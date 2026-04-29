STRATEGY_REGISTRY: dict[str, type] = {}


def register_strategy(cls):
    """Class decorator — adds strategy to the global registry."""
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


# Auto-register all strategies on import
from strategies.bollinger import BollingerStrategy  # noqa: E402, F401
from strategies.macd import MACDStrategy  # noqa: E402, F401
from strategies.rsi import RSIStrategy  # noqa: E402, F401
from strategies.sma_crossover import SMACrossoverStrategy  # noqa: E402, F401
