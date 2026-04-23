from strategies import register_strategy
from strategies.base import Strategy


@register_strategy
class MACDStrategy(Strategy):
    name = "MACD Crossover"
    params = {
        "fast_period": {"default": 12, "min": 2, "max": 50, "step": 1, "help": "Fast EMA period"},
        "slow_period": {"default": 26, "min": 10, "max": 100, "step": 1, "help": "Slow EMA period"},
        "signal_period": {"default": 9, "min": 2, "max": 30, "step": 1, "help": "Signal line period"},
    }

    def __init__(self, fast_period=12, slow_period=26, signal_period=9):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def indicators(self, price):
        import vectorbt as vbt

        macd = vbt.MACD.run(
            price,
            fast_window=self.fast_period,
            slow_window=self.slow_period,
            signal_window=self.signal_period,
        )
        return {"MACD": macd.macd, "Signal": macd.signal, "Histogram": macd.hist}

    def signals(self, price, indicators):
        hist = indicators["Histogram"]
        # Enter when histogram crosses above zero, exit when crosses below
        entries = (hist > 0) & (hist.shift(1) <= 0)
        exits = (hist < 0) & (hist.shift(1) >= 0)
        return (entries, exits)
