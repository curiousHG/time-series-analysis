STRATEGY_REGISTRY: dict[str, type] = {}


def register_strategy(cls):
    """Class decorator — adds strategy to the global registry."""
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


import strategies.markov_strategy  # noqa: E402
import strategies.ml  # noqa: E402
import strategies.ported  # noqa: E402
from strategies.basket_examples import MomentumRankStrategy, RsiBasketStrategy  # noqa: E402
from strategies.bollinger import BollingerStrategy  # noqa: E402
from strategies.macd import MACDStrategy  # noqa: E402
from strategies.rsi import RSIStrategy  # noqa: E402
from strategies.sma_crossover import SMACrossoverStrategy  # noqa: E402
