"""Rules ported from the freq_strategies research repo onto NSE daily equity bars, with ranges re-opened for
daily equities."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np
import pandas as pd
import talib

from strategies.basket import (
    ENTER,
    ENTER_TAG,
    EXIT,
    EXIT_TAG,
    SCORE,
    BasketStrategy,
    register_basket_strategy,
)
from strategies.parameters import BoolParameter, DecimalParameter, IntParameter

if TYPE_CHECKING:
    from strategies.basket import MarketContext, TradeSnapshot

NO_STOP = -0.99
TREND_COLUMN = "trend_ok"
REGIME_COLUMN = "regime_ok"


def _regime_ok(ctx: MarketContext, index: pd.Index, window: int) -> pd.Series:
    """True on bars where the benchmark closes above its own moving average; missing benchmark data fails open."""
    if ctx is None or ctx.benchmark is None or "Close" not in getattr(ctx.benchmark, "columns", []):
        return pd.Series(True, index=index)
    close = ctx.benchmark["Close"].reindex(index).ffill()
    average = close.rolling(window, min_periods=window // 2).mean()
    return (close > average).fillna(True)


@register_basket_strategy
class OversoldReversionStrategy(BasketStrategy):
    """Buy a deeply dislocated name as its RSI cuts below the oversold line while it sits far under its 50-day average.

    Exit is a bar count only.
    """

    name = "Oversold Reversion"
    mode = "signal"

    rsi_window = IntParameter(14, 5, 30, help="RSI lookback in bars")
    rsi_threshold = DecimalParameter(30.0, 10.0, 45.0, step=1.0, help="RSI level the cross must go below")
    average_window = IntParameter(50, 20, 200, step=5, help="Moving average the dislocation is measured against")
    dislocation = DecimalParameter(
        -0.10, -0.40, -0.02, step=0.01, help="How far below its average the price must sit, as a fraction"
    )
    hold_bars = IntParameter(3, 1, 30, help="Bars to hold before the time exit closes the position")

    stoploss = NO_STOP
    trailing_stop = False
    use_exit_signal = False
    max_open_trades = 12

    @property
    def startup_candle_count(self) -> int:
        return max(self.average_window + 10, 3 * self.rsi_window)

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        close = df["Close"].ffill()
        df["rsi"] = talib.RSI(close.to_numpy(dtype=float), timeperiod=int(self.rsi_window))
        average = close.rolling(int(self.average_window), min_periods=int(self.average_window) // 2).mean()
        df["dislocation"] = close / average - 1
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        rsi = df["rsi"]
        crossed_down = (rsi < float(self.rsi_threshold)) & (rsi.shift(1) >= float(self.rsi_threshold))
        enter = crossed_down & (df["dislocation"] < float(self.dislocation)) & (df["Volume"].fillna(0) > 0)
        df[ENTER] = enter.fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "oversold", None)
        return df

    def custom_exit(self, trade: TradeSnapshot, ctx: MarketContext) -> str | None:
        """Close the trade once it has run its hold.

        The check is a bar early because the exit fills at the next open, which makes the realised
        holding period exactly `hold_bars`.
        """
        return "hold_expired" if trade.bars_held + 1 >= int(self.hold_bars) else None


@register_basket_strategy
class CapitulationBounceStrategy(BasketStrategy):
    """Buy the first green bar after a run of red ones that gave up enough ground.

    Exit is the ROI ladder and the time cap.
    """

    name = "Capitulation Bounce"
    mode = "signal"

    red_bars = IntParameter(3, 2, 9, help="Consecutive down bars required before the bounce")
    drop = DecimalParameter(0.06, 0.01, 0.25, step=0.005, help="Cumulative fall over those bars, as a fraction")
    require_green = BoolParameter(True, optimize=False, help="Wait for a green confirmation bar")
    hold_bars = IntParameter(5, 1, 30, help="Bars to hold before the time exit closes the position")

    stoploss = NO_STOP
    use_exit_signal = False
    max_open_trades = 10

    @property
    def startup_candle_count(self) -> int:
        return int(self.red_bars) + 10

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        n = int(self.red_bars)
        red = (df["Close"] <= df["Open"]).astype(float)
        df["red_streak"] = red.rolling(n, min_periods=n).sum() >= n
        opened = df["Open"].shift(n - 1)
        df["run_drop"] = (opened - df["Close"]) / opened
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        n = int(self.red_bars)
        streak = df["red_streak"].shift(1).fillna(False) if self.require_green else df["red_streak"]
        drop = df["run_drop"].shift(1) if self.require_green else df["run_drop"]
        enter = streak.astype(bool) & (drop >= float(self.drop))
        if self.require_green:
            enter &= df["Close"] >= df["Open"]
        df[ENTER] = enter.fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], f"{n}_red", None)
        return df

    def custom_exit(self, trade: TradeSnapshot, ctx: MarketContext) -> str | None:
        """Close the trade once it has run its hold.

        The check is a bar early because the exit fills at the next open, which makes the realised
        holding period exactly `hold_bars`.
        """
        return "hold_expired" if trade.bars_held + 1 >= int(self.hold_bars) else None


@register_basket_strategy
class MomentumRegimeStrategy(BasketStrategy):
    """Hold the strongest trailing performers while the index and each name are above their moving averages.

    The book moves to cash when the benchmark is below its own average; `exit_rank` sets the exit band.
    """

    name = "Momentum Regime Basket"
    mode = "rank"

    lookback = IntParameter(90, 20, 252, step=5, help="Trailing return window that sets the rank")
    trend_window = IntParameter(50, 10, 200, step=5, help="A held name must close above this own-average")
    regime_window = IntParameter(100, 20, 250, step=10, help="Benchmark average that switches the book on and off")
    use_trend_filter = BoolParameter(True, optimize=False, help="Drop names below their own average")
    use_regime_gate = BoolParameter(True, optimize=False, help="Sit in cash while the benchmark is below its average")

    top_k = 5
    exit_rank = 15
    rebalance_every = "M"
    stoploss = NO_STOP
    use_exit_signal = True

    @property
    def startup_candle_count(self) -> int:
        return max(int(self.lookback), int(self.trend_window), int(self.regime_window)) + 10

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        close = df["Close"]
        df["momentum"] = close / close.shift(int(self.lookback)) - 1
        window = int(self.trend_window)
        above = close > close.rolling(window, min_periods=window // 2).mean()
        df[TREND_COLUMN] = above.fillna(False) if self.use_trend_filter else True
        gate = _regime_ok(ctx, df.index, int(self.regime_window))
        df[REGIME_COLUMN] = gate if self.use_regime_gate else pd.Series(True, index=df.index)
        return df

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        allowed = df[TREND_COLUMN].astype(bool) & df[REGIME_COLUMN].astype(bool)
        df[SCORE] = df["momentum"].where(allowed)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        blocked = ~(df[TREND_COLUMN].astype(bool) & df[REGIME_COLUMN].astype(bool))
        df[EXIT] = blocked.fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], np.where(df[REGIME_COLUMN].astype(bool), "trend_lost", "risk_off"), None)
        return df


@register_basket_strategy
class DonchianBreakoutStrategy(BasketStrategy):
    """Buy a new N-bar high (channel shifted one bar) while ADX shows the trend is strong enough."""

    name = "Donchian Breakout"
    mode = "signal"

    entry_period = IntParameter(24, 10, 120, step=2, help="Bars of prior highs the entry must clear")
    exit_period = IntParameter(12, 5, 60, step=1, help="Bars the exit low is measured over")
    adx_window = IntParameter(14, 7, 30, help="ADX lookback")
    adx_floor = DecimalParameter(20.0, 0.0, 40.0, step=1.0, help="Minimum ADX for a breakout to count")

    stoploss = -0.12
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.10
    max_open_trades = 8

    @property
    def startup_candle_count(self) -> int:
        return max(int(self.entry_period), int(self.exit_period), 3 * int(self.adx_window)) + 10

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        high, low, close = (df[c].ffill().to_numpy(dtype=float) for c in ("High", "Low", "Close"))
        df["channel_high"] = pd.Series(talib.MAX(high, timeperiod=int(self.entry_period)), index=df.index).shift(1)
        df["channel_low"] = pd.Series(talib.MIN(low, timeperiod=int(self.exit_period)), index=df.index).shift(1)
        df["adx"] = talib.ADX(high, low, close, timeperiod=int(self.adx_window))
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        breakout = (df["Close"] > df["channel_high"]) & (df["Close"].shift(1) <= df["channel_high"].shift(1))
        df[ENTER] = (breakout & (df["adx"] >= float(self.adx_floor))).fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "breakout", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        df[EXIT] = (df["Close"] <= df["channel_low"]).fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], "channel_low", None)
        return df


@register_basket_strategy
class DipInUptrendStrategy(BasketStrategy):
    """Buy the close crossing back up through the lower Bollinger band inside an uptrend.

    Trend gate: price above its long average, trend not strongly down, rate of change negative but turning.
    """

    name = "Dip In Uptrend"
    mode = "signal"

    band_period = IntParameter(20, 10, 60, step=2, help="Bollinger lookback")
    band_devs = DecimalParameter(2.0, 1.0, 3.5, step=0.1, help="Band width in standard deviations")
    trend_window = IntParameter(200, 50, 300, step=10, help="Long average the price must stay above")
    adx_window = IntParameter(14, 7, 30, help="ADX lookback")
    adx_ceiling = DecimalParameter(25.0, 10.0, 45.0, step=1.0, help="Above this the move is a trend, not a dip")
    roc_window = IntParameter(14, 5, 40, help="Rate-of-change lookback for the turn check")

    stoploss = -0.10
    max_open_trades = 8
    minimal_roi: ClassVar[dict[int, float]] = {0: 0.12, 10: 0.06, 20: 0.03, 40: 0.0}

    @property
    def startup_candle_count(self) -> int:
        return int(self.trend_window) + 20

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        close_s = df["Close"].ffill()
        close = close_s.to_numpy(dtype=float)
        upper, _, lower = talib.BBANDS(
            close, timeperiod=int(self.band_period), nbdevup=float(self.band_devs), nbdevdn=float(self.band_devs)
        )
        df["band_upper"], df["band_lower"] = upper, lower
        df["long_average"] = talib.EMA(close, timeperiod=int(self.trend_window))
        df["adx"] = talib.ADX(
            df["High"].ffill().to_numpy(dtype=float),
            df["Low"].ffill().to_numpy(dtype=float),
            close,
            timeperiod=int(self.adx_window),
        )
        df["roc"] = talib.ROC(close, timeperiod=int(self.roc_window))
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        gate = (
            (df["Close"] > df["long_average"])
            & (df["adx"] < float(self.adx_ceiling))
            & (df["roc"] < 0)
            & (df["roc"] > df["roc"].shift(1))
        )
        bounce = (df["Close"] > df["band_lower"]) & (df["Close"].shift(1) <= df["band_lower"].shift(1))
        df[ENTER] = (gate & bounce).fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "band_bounce", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        touched = (df["Close"] >= df["band_upper"]) | (df["Close"] < df["long_average"])
        df[EXIT] = touched.fillna(False).astype(bool)
        return df


@register_basket_strategy
class PullbackTrendStrategy(BasketStrategy):
    """Buy a pause in a confirmed uptrend (fast average above slow, close below fast); sell when the trend breaks."""

    name = "Pullback Trend"
    mode = "signal"

    fast_window = IntParameter(20, 5, 60, help="Fast moving average")
    slow_window = IntParameter(60, 20, 250, step=5, help="Slow moving average")
    max_stretch = DecimalParameter(
        0.0, 0.0, 0.15, step=0.005, help="Cap on how far below the fast average the close may sit; 0 disables it"
    )

    stoploss = -0.12
    max_open_trades = 8

    @property
    def startup_candle_count(self) -> int:
        return int(self.slow_window) + 10

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        close = df["Close"].ffill().to_numpy(dtype=float)
        df["ma_fast"] = talib.EMA(close, timeperiod=int(self.fast_window))
        df["ma_slow"] = talib.EMA(close, timeperiod=int(self.slow_window))
        return df

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        stretch = 1 - df["Close"] / df["ma_fast"]
        enter = (df["ma_fast"] > df["ma_slow"]) & (df["Close"] < df["ma_fast"])
        if float(self.max_stretch) > 0:
            enter &= stretch <= float(self.max_stretch)
        df[ENTER] = enter.fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "pullback", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        df[EXIT] = (df["ma_fast"] < df["ma_slow"]).fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], "trend_break", None)
        return df
