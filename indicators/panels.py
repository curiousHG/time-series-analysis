"""Panel indicators — plotted in separate subplots below the price chart."""

import pandas as pd
import talib

from indicators.registry import register


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
    return {
        "MFI": pd.Series(talib.MFI(df["High"], df["Low"], df["Close"], df["Volume"], timeperiod=14), index=df.index)
    }


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
    return {
        "Chaikin": pd.Series(
            talib.ADOSC(df["High"], df["Low"], df["Close"], df["Volume"], fastperiod=3, slowperiod=10), index=df.index
        )
    }


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


# ---- Hilbert Transform (panel) ----


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
