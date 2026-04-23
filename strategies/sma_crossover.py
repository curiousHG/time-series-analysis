from strategies import register_strategy
from strategies.base import Strategy


@register_strategy
class SMACrossoverStrategy(Strategy):
    name = "SMA Crossover"
    params = {
        "fast_period": {"default": 20, "min": 5, "max": 100, "step": 5, "help": "Fast SMA period"},
        "slow_period": {"default": 50, "min": 20, "max": 200, "step": 10, "help": "Slow SMA period"},
    }

    def __init__(self, fast_period=20, slow_period=50):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def indicators(self, price):
        import vectorbt as vbt

        fast_ma = vbt.MA.run(price, window=self.fast_period)
        slow_ma = vbt.MA.run(price, window=self.slow_period)
        return {"Fast_SMA": fast_ma.ma, "Slow_SMA": slow_ma.ma}

    def signals(self, price, indicators):
        fast = indicators["Fast_SMA"]
        slow = indicators["Slow_SMA"]

        # Entry: fast SMA crosses above slow SMA
        entries = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        # Exit: fast SMA crosses below slow SMA
        exits = (fast < slow) & (fast.shift(1) >= slow.shift(1))

        return (entries.fillna(False), exits.fillna(False))
