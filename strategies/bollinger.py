from strategies import register_strategy
from strategies.base import Strategy


@register_strategy
class BollingerStrategy(Strategy):
    name = "Bollinger Bands"
    params = {
        "bb_period": {"default": 20, "min": 5, "max": 50, "step": 1, "help": "Bollinger Band lookback period"},
        "bb_std": {"default": 2.0, "min": 0.5, "max": 4.0, "step": 0.5, "help": "Standard deviation multiplier"},
        "rsi_guard": {"default": 40, "min": 10, "max": 60, "step": 5, "help": "RSI guard: only buy when RSI below this"},
    }
    trailing_stop = True
    trailing_stop_positive = 0.02

    def __init__(self, bb_period=20, bb_std=2.0, rsi_guard=40):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_guard = rsi_guard

    def indicators(self, price):
        import vectorbt as vbt

        bb = vbt.BBANDS.run(price, window=self.bb_period, alpha=self.bb_std)
        rsi = vbt.RSI.run(price, window=14)
        return {
            "BB_upper": bb.upper,
            "BB_middle": bb.middle,
            "BB_lower": bb.lower,
            "RSI": rsi.rsi,
        }

    def signals(self, price, indicators):
        bb_lower = indicators["BB_lower"]
        bb_upper = indicators["BB_upper"]
        rsi = indicators["RSI"]

        # Guard: RSI must be in oversold zone
        guard = rsi < self.rsi_guard

        # Entry trigger: price crosses below lower band
        trigger_entry = (price < bb_lower) & (price.shift(1) >= bb_lower.shift(1))
        entries = guard & trigger_entry

        # Exit trigger: price crosses above upper band
        exits = (price > bb_upper) & (price.shift(1) <= bb_upper.shift(1))

        return (entries.fillna(False), exits.fillna(False))
