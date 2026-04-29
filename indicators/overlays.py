"""Overlay indicators — plotted on the price chart."""

import pandas as pd
import talib

from indicators.registry import register


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


@register("HT Trendline", "Hilbert Transform Instantaneous Trendline", overlay=True)
def ht_trendline(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {"HT Trendline": pd.Series(talib.HT_TRENDLINE(df["Close"]), index=df.index)}
