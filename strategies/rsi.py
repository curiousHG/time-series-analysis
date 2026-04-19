from strategies.base import Strategy


class RSIStrategy(Strategy):
    name = "RSI"

    def __init__(self, window=14):
        self.window = window

    def indicators(self, price):
        import vectorbt as vbt

        rsi = vbt.RSI.run(price, window=self.window)
        return {"RSI": rsi.rsi}

    def signals(self, price, indicators):
        rsi = indicators["RSI"]
        return (rsi < 30, rsi > 70)  # entries  # exits
