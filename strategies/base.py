import pandas as pd


class Strategy:
    name = "Base"
    # Typed params for UI auto-generation: {name: {default, min, max, step, help}}
    params: dict = {}
    # Default risk management (overridable per strategy)
    stoploss: float = -0.10  # -10% hard stoploss
    trailing_stop: bool = False
    trailing_stop_positive: float = 0.01

    def indicators(self, price: pd.Series) -> dict:
        return {}

    def signals(self, price: pd.Series, indicators: dict) -> tuple[pd.Series, pd.Series]:
        raise NotImplementedError
