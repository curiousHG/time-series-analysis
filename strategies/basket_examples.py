"""Two reference basket strategies, one per engine mode.

`RsiBasketStrategy` (signal mode) enters on the RSI crossing up through the oversold level and
exits on it crossing down through the overbought level. `MomentumRankStrategy` (rank mode)
scores each symbol by its trailing return and lets the engine hold the top-K with hysteresis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import talib

from strategies.basket import ENTER, ENTER_TAG, EXIT, EXIT_TAG, SCORE, BasketStrategy, register_basket_strategy
from strategies.parameters import IntParameter

RSI_COLUMN = "rsi"


@register_basket_strategy
class RsiBasketStrategy(BasketStrategy):
    name = "RSI Basket"
    mode = "signal"

    window = IntParameter(14, 2, 50, help="RSI lookback in bars")
    oversold = IntParameter(30, 10, 45, help="Enter when RSI crosses above this level")
    overbought = IntParameter(70, 55, 90, help="Exit when RSI crosses below this level")

    @property
    def startup_candle_count(self) -> int:
        return max(50, 3 * self.window)

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx) -> pd.DataFrame:
        close = df["Close"].ffill().to_numpy(dtype=float)
        df[RSI_COLUMN] = talib.RSI(close, timeperiod=self.window)
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx) -> pd.DataFrame:
        rsi = df[RSI_COLUMN]
        crossed_up = (rsi > self.oversold) & (rsi.shift(1) <= self.oversold)
        df[ENTER] = crossed_up.fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "rsi", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx) -> pd.DataFrame:
        rsi = df[RSI_COLUMN]
        crossed_down = (rsi < self.overbought) & (rsi.shift(1) >= self.overbought)
        df[EXIT] = crossed_down.fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], "rsi", None)
        return df


@register_basket_strategy
class MomentumRankStrategy(BasketStrategy):
    name = "Momentum Rank"
    mode = "rank"

    lookback = IntParameter(126, 20, 252, step=5, help="Trailing return window in bars")

    top_k = 10
    exit_rank = 15
    rebalance_every = "M"

    @property
    def startup_candle_count(self) -> int:
        return self.lookback + 1

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx) -> pd.DataFrame:
        df[SCORE] = df["Close"] / df["Close"].shift(self.lookback) - 1
        return df
