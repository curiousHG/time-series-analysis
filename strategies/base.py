from typing import ClassVar

import pandas as pd


class Strategy:
    name = "Base"
    params: ClassVar[dict] = {}
    stoploss: float = -0.10
    trailing_stop: bool = False
    trailing_stop_positive: float = 0.01

    def indicators(self, price: pd.Series) -> dict:
        return {}

    def signals(self, price: pd.Series, indicators: dict) -> tuple[pd.Series, pd.Series]:
        raise NotImplementedError
