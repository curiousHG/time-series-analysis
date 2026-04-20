"""Technical indicators powered by TA-Lib.

Each indicator is registered via @register and returns a dict of named Series.
The stock page UI reads INDICATOR_REGISTRY to build the selector.
"""

import pandas as pd
import talib

INDICATOR_REGISTRY: dict[str, dict] = {}


def register(name: str, description: str, overlay: bool = False):
    """Decorator to register an indicator function."""

    def wrapper(fn):
        INDICATOR_REGISTRY[name] = {
            "fn": fn,
            "description": description,
            "overlay": overlay,
        }
        return fn

    return wrapper


# ===========================================================================
#  Overlay indicators (plotted on price chart)
# ===========================================================================


@register("SMA 20", "20-day Simple Moving Average", overlay=True)
def sma_20(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"SMA 20": pd.Series(talib.SMA(df["Close"], timeperiod=20), index=df.index)}


@register("SMA 50", "50-day Simple Moving Average", overlay=True)
def sma_50(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"SMA 50": pd.Series(talib.SMA(df["Close"], timeperiod=50), index=df.index)}


@register("SMA 200", "200-day Simple Moving Average", overlay=True)
def sma_200(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"SMA 200": pd.Series(talib.SMA(df["Close"], timeperiod=200), index=df.index)}


@register("EMA 12", "12-day Exponential Moving Average", overlay=True)
def ema_12(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"EMA 12": pd.Series(talib.EMA(df["Close"], timeperiod=12), index=df.index)}


@register("EMA 26", "26-day Exponential Moving Average", overlay=True)
def ema_26(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"EMA 26": pd.Series(talib.EMA(df["Close"], timeperiod=26), index=df.index)}


@register("Bollinger Bands", "20-day Bollinger Bands (2 std)", overlay=True)
def bollinger_bands(df: pd.DataFrame) -> dict[str, pd.Series]:
    upper, middle, lower = talib.BBANDS(df["Close"], timeperiod=20, nbdevup=2, nbdevdn=2)
    return {
        "BB Upper": pd.Series(upper, index=df.index),
        "BB Middle": pd.Series(middle, index=df.index),
        "BB Lower": pd.Series(lower, index=df.index),
    }


@register("Parabolic SAR", "Parabolic Stop and Reverse", overlay=True)
def parabolic_sar(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"SAR": pd.Series(talib.SAR(df["High"], df["Low"]), index=df.index)}


@register("DEMA", "Double EMA (20)", overlay=True)
def dema(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"DEMA 20": pd.Series(talib.DEMA(df["Close"], timeperiod=20), index=df.index)}


@register("TEMA", "Triple EMA (20)", overlay=True)
def tema(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"TEMA 20": pd.Series(talib.TEMA(df["Close"], timeperiod=20), index=df.index)}


@register("KAMA", "Kaufman Adaptive MA (30)", overlay=True)
def kama(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"KAMA 30": pd.Series(talib.KAMA(df["Close"], timeperiod=30), index=df.index)}


@register("VWAP", "Volume Weighted Average Price", overlay=True)
def vwap(df: pd.DataFrame) -> dict[str, pd.Series]:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tp_vol = (typical * df["Volume"]).cumsum()
    cum_vol = df["Volume"].cumsum()
    return {"VWAP": cum_tp_vol / cum_vol}


@register("Donchian Channel", "20-day Donchian Channel", overlay=True)
def donchian(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "DC Upper": df["High"].rolling(20).max(),
        "DC Lower": df["Low"].rolling(20).min(),
    }


@register("Keltner Channel", "20-day Keltner Channel (1.5 ATR)", overlay=True)
def keltner(df: pd.DataFrame) -> dict[str, pd.Series]:
    mid = pd.Series(talib.EMA(df["Close"], timeperiod=20), index=df.index)
    atr_val = pd.Series(talib.ATR(df["High"], df["Low"], df["Close"], timeperiod=20), index=df.index)
    return {
        "KC Upper": mid + 1.5 * atr_val,
        "KC Middle": mid,
        "KC Lower": mid - 1.5 * atr_val,
    }


# ===========================================================================
#  Panel indicators (separate subplot)
# ===========================================================================


@register("RSI", "14-day Relative Strength Index", overlay=False)
def rsi(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"RSI": pd.Series(talib.RSI(df["Close"], timeperiod=14), index=df.index)}


@register("MACD", "MACD (12, 26, 9)", overlay=False)
def macd(df: pd.DataFrame) -> dict[str, pd.Series]:
    m, s, h = talib.MACD(df["Close"], fastperiod=12, slowperiod=26, signalperiod=9)
    return {
        "MACD": pd.Series(m, index=df.index),
        "Signal": pd.Series(s, index=df.index),
        "Histogram": pd.Series(h, index=df.index),
    }


@register("ATR", "14-day Average True Range", overlay=False)
def atr(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"ATR": pd.Series(talib.ATR(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index)}


@register("OBV", "On-Balance Volume", overlay=False)
def obv(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"OBV": pd.Series(talib.OBV(df["Close"], df["Volume"]), index=df.index)}


@register("MFI", "14-day Money Flow Index", overlay=False)
def mfi(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"MFI": pd.Series(talib.MFI(df["High"], df["Low"], df["Close"], df["Volume"], timeperiod=14), index=df.index)}


@register("Stochastic", "Stochastic Oscillator (14, 3, 3)", overlay=False)
def stochastic(df: pd.DataFrame) -> dict[str, pd.Series]:
    slowk, slowd = talib.STOCH(df["High"], df["Low"], df["Close"])
    return {
        "%K": pd.Series(slowk, index=df.index),
        "%D": pd.Series(slowd, index=df.index),
    }


@register("Stochastic RSI", "Stochastic RSI (14)", overlay=False)
def stoch_rsi(df: pd.DataFrame) -> dict[str, pd.Series]:
    fastk, fastd = talib.STOCHRSI(df["Close"], timeperiod=14)
    return {
        "StochRSI %K": pd.Series(fastk, index=df.index),
        "StochRSI %D": pd.Series(fastd, index=df.index),
    }


@register("ADX", "Average Directional Index (14)", overlay=False)
def adx(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "ADX": pd.Series(talib.ADX(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index),
        "+DI": pd.Series(talib.PLUS_DI(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index),
        "-DI": pd.Series(talib.MINUS_DI(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index),
    }


@register("CCI", "Commodity Channel Index (20)", overlay=False)
def cci(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"CCI": pd.Series(talib.CCI(df["High"], df["Low"], df["Close"], timeperiod=20), index=df.index)}


@register("Williams %R", "Williams %R (14)", overlay=False)
def williams_r(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"%R": pd.Series(talib.WILLR(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index)}


@register("Momentum", "10-day Price Momentum", overlay=False)
def momentum(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"Momentum": pd.Series(talib.MOM(df["Close"], timeperiod=10), index=df.index)}


@register("ROC", "Rate of Change (10)", overlay=False)
def roc(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"ROC": pd.Series(talib.ROC(df["Close"], timeperiod=10), index=df.index)}


@register("Chaikin A/D", "Chaikin A/D Line", overlay=False)
def chaikin_ad(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"AD": pd.Series(talib.AD(df["High"], df["Low"], df["Close"], df["Volume"]), index=df.index)}


@register("Chaikin Oscillator", "Chaikin Oscillator (3, 10)", overlay=False)
def chaikin_osc(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"Chaikin": pd.Series(talib.ADOSC(df["High"], df["Low"], df["Close"], df["Volume"], fastperiod=3, slowperiod=10), index=df.index)}


@register("Aroon", "Aroon Up/Down (14)", overlay=False)
def aroon(df: pd.DataFrame) -> dict[str, pd.Series]:
    down, up = talib.AROON(df["High"], df["Low"], timeperiod=14)
    return {
        "Aroon Up": pd.Series(up, index=df.index),
        "Aroon Down": pd.Series(down, index=df.index),
    }


@register("NATR", "Normalized ATR (14)", overlay=False)
def natr(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"NATR": pd.Series(talib.NATR(df["High"], df["Low"], df["Close"], timeperiod=14), index=df.index)}


@register("PPO", "Percentage Price Oscillator", overlay=False)
def ppo(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"PPO": pd.Series(talib.PPO(df["Close"], fastperiod=12, slowperiod=26), index=df.index)}


@register("TRIX", "Triple Smoothed EMA Rate of Change (15)", overlay=False)
def trix(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"TRIX": pd.Series(talib.TRIX(df["Close"], timeperiod=15), index=df.index)}


@register("Ultimate Oscillator", "Ultimate Oscillator (7, 14, 28)", overlay=False)
def ultimate_osc(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"UO": pd.Series(talib.ULTOSC(df["High"], df["Low"], df["Close"]), index=df.index)}


# ===========================================================================
#  Hilbert Transform indicators
# ===========================================================================


@register("HT Trendline", "Hilbert Transform Instantaneous Trendline", overlay=True)
def ht_trendline(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"HT Trendline": pd.Series(talib.HT_TRENDLINE(df["Close"]), index=df.index)}


@register("HT DC Phase", "Hilbert Transform Dominant Cycle Phase", overlay=False)
def ht_dcphase(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"DC Phase": pd.Series(talib.HT_DCPHASE(df["Close"]), index=df.index)}


@register("HT DC Period", "Hilbert Transform Dominant Cycle Period", overlay=False)
def ht_dcperiod(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"DC Period": pd.Series(talib.HT_DCPERIOD(df["Close"]), index=df.index)}


@register("HT Sine Wave", "Hilbert Transform Sine Wave", overlay=False)
def ht_sine(df: pd.DataFrame) -> dict[str, pd.Series]:
    sine, leadsine = talib.HT_SINE(df["Close"])
    return {
        "Sine": pd.Series(sine, index=df.index),
        "Lead Sine": pd.Series(leadsine, index=df.index),
    }


@register("HT Trend Mode", "Hilbert Transform Trend vs Cycle Mode (0/1)", overlay=False)
def ht_trendmode(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"Trend Mode": pd.Series(talib.HT_TRENDMODE(df["Close"]), index=df.index)}


# ===========================================================================
#  Compute helper
# ===========================================================================


def compute_indicators(
    df: pd.DataFrame, selected: list[str]
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """
    Compute selected indicators. Returns (overlays, panels).
    overlays: series to plot on the price chart
    panels: series to plot in separate subplots
    """
    overlays: dict[str, pd.Series] = {}
    panels: dict[str, pd.Series] = {}

    for name in selected:
        if name not in INDICATOR_REGISTRY:
            continue
        entry = INDICATOR_REGISTRY[name]
        result = entry["fn"](df)
        if entry["overlay"]:
            overlays.update(result)
        else:
            panels.update(result)

    return overlays, panels
