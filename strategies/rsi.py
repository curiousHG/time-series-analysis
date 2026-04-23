from strategies import register_strategy
from strategies.base import Strategy


@register_strategy
class RSIStrategy(Strategy):
    name = "RSI"
    params = {
        "window": {"default": 14, "min": 2, "max": 50, "step": 1, "help": "RSI lookback period"},
        "oversold": {"default": 30, "min": 5, "max": 50, "step": 5, "help": "Buy when RSI drops below this"},
        "overbought": {"default": 70, "min": 50, "max": 95, "step": 5, "help": "Sell when RSI rises above this"},
    }

    def __init__(self, window=14, oversold=30, overbought=70):
        self.window = window
        self.oversold = oversold
        self.overbought = overbought

    def indicators(self, price):
        import vectorbt as vbt

        rsi = vbt.RSI.run(price, window=self.window)
        return {"RSI": rsi.rsi}

    def signals(self, price, indicators):
        rsi = indicators["RSI"]
        return (rsi < self.oversold, rsi > self.overbought)
