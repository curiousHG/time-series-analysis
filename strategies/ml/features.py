"""Symbol-agnostic feature registry built on the TA-Lib indicator registry.

Every feature is a ratio to Close, a bounded oscillator, a log return or a rolling z-score, so a
frame with prices x10 and volume x7 yields identical features. Cumulative indicators (OBV, A/D)
enter only as volume-scaled differences; VWAP is excluded because its anchor is the frame start.
Features are causal: no back-fill, and truncating future bars never changes earlier rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from core.constants import TRADING_DAYS
from indicators import compute_indicators

if TYPE_CHECKING:
    from collections.abc import Callable

FEATURE_PREFIX = "%-"
TARGET_PREFIX = "&-"
PRED_COL = "&-pred"
DO_PREDICT_COL = "do_predict"

QUARTER = TRADING_DAYS // 4
MONTH = TRADING_DAYS // 12
HILBERT_LOOKBACK = 64


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    fn: Callable[[pd.DataFrame, dict[str, pd.Series]], pd.Series]
    lookback: int
    group: str
    sets: tuple[str, ...]
    indicators: tuple[str, ...] = ()


FEATURE_REGISTRY: dict[str, FeatureSpec] = {}

FEATURE_SETS = ("core", "full")


def register_feature(
    name: str, *, lookback: int, group: str, sets: tuple[str, ...] = ("full",), indicators: tuple[str, ...] = ()
):
    """Register `fn(df, ind) -> Series` under `%-name`; `indicators` names registry entries it reads."""

    full_name = f"{FEATURE_PREFIX}{name}"
    all_sets = tuple(dict.fromkeys((*sets, "full")))

    def decorate(fn):
        if full_name in FEATURE_REGISTRY:
            raise ValueError(f"feature {full_name!r} already registered")
        FEATURE_REGISTRY[full_name] = FeatureSpec(full_name, fn, lookback, group, all_sets, tuple(indicators))
        return fn

    return decorate


def feature_names(feature_set: str = "core") -> list[str]:
    if feature_set not in FEATURE_SETS:
        raise KeyError(f"unknown feature set {feature_set!r}; choose from {FEATURE_SETS}")
    return [name for name, spec in FEATURE_REGISTRY.items() if feature_set in spec.sets]


def startup_bars(names: list[str] | tuple[str, ...]) -> int:
    return max((FEATURE_REGISTRY[name].lookback for name in names), default=0)


def rolling_zscore(s: pd.Series, window: int) -> pd.Series:
    roll = s.rolling(window, min_periods=window)
    return (s - roll.mean()) / roll.std(ddof=1)


def build_features(df: pd.DataFrame, names: list[str] | tuple[str, ...]) -> pd.DataFrame:
    specs = [FEATURE_REGISTRY[name] for name in names]
    needed = list(dict.fromkeys(ind for spec in specs for ind in spec.indicators))
    overlays, panels = compute_indicators(df, needed)
    ind = {**overlays, **panels}
    out = pd.DataFrame(index=df.index)
    for spec in specs:
        out[spec.name] = spec.fn(df, ind).astype(float)
    return out


def _log_returns(df: pd.DataFrame, periods: int) -> pd.Series:
    return np.log(df["Close"] / df["Close"].shift(periods))


def _ratio_to_close(df: pd.DataFrame, series: pd.Series) -> pd.Series:
    return df["Close"] / series - 1.0


def _channel_position(df: pd.DataFrame, upper: pd.Series, lower: pd.Series) -> pd.Series:
    width = upper - lower
    return ((df["Close"] - lower) / width.where(width > 0)).clip(-1.0, 2.0)


def _volume_scaled(series: pd.Series, df: pd.DataFrame, window: int = MONTH) -> pd.Series:
    baseline = df["Volume"].rolling(window, min_periods=window).mean()
    return series / baseline.where(baseline > 0)


@register_feature("sma20_ratio", lookback=20, group="trend", sets=("core",), indicators=("SMA 20",))
def sma20_ratio(df, ind):
    return _ratio_to_close(df, ind["SMA 20"])


@register_feature("sma50_ratio", lookback=50, group="trend", sets=("core",), indicators=("SMA 50",))
def sma50_ratio(df, ind):
    return _ratio_to_close(df, ind["SMA 50"])


@register_feature("ema_spread", lookback=26, group="trend", sets=("core",), indicators=("EMA 12", "EMA 26"))
def ema_spread(df, ind):
    return (ind["EMA 12"] - ind["EMA 26"]) / df["Close"]


@register_feature("bb_position", lookback=20, group="volatility", sets=("core",), indicators=("Bollinger Bands",))
def bb_position(df, ind):
    return _channel_position(df, ind["BB Upper"], ind["BB Lower"])


@register_feature("bb_width", lookback=20, group="volatility", sets=("core",), indicators=("Bollinger Bands",))
def bb_width(df, ind):
    return (ind["BB Upper"] - ind["BB Lower"]) / ind["BB Middle"]


@register_feature("dc_position", lookback=20, group="trend", sets=("core",), indicators=("Donchian Channel",))
def dc_position(df, ind):
    return _channel_position(df, ind["DC Upper"], ind["DC Lower"])


@register_feature("macd_ratio", lookback=34, group="momentum", sets=("core",), indicators=("MACD",))
def macd_ratio(df, ind):
    return ind["MACD"] / df["Close"]


@register_feature("macd_hist_ratio", lookback=34, group="momentum", sets=("core",), indicators=("MACD",))
def macd_hist_ratio(df, ind):
    return ind["Histogram"] / df["Close"]


@register_feature("adx", lookback=28, group="trend", sets=("core",), indicators=("ADX",))
def adx(df, ind):
    return ind["ADX"] / 100.0


@register_feature("di_diff", lookback=28, group="trend", sets=("core",), indicators=("ADX",))
def di_diff(df, ind):
    return (ind["+DI"] - ind["-DI"]) / 100.0


@register_feature("rsi", lookback=15, group="momentum", sets=("core",), indicators=("RSI",))
def rsi(df, ind):
    return ind["RSI"] / 100.0


@register_feature("mfi", lookback=15, group="volume", sets=("core",), indicators=("MFI",))
def mfi(df, ind):
    return ind["MFI"] / 100.0


@register_feature("stoch_k", lookback=10, group="momentum", sets=("core",), indicators=("Stochastic",))
def stoch_k(df, ind):
    return ind["%K"] / 100.0


@register_feature("roc10", lookback=11, group="momentum", sets=("core",), indicators=("ROC",))
def roc10(df, ind):
    return ind["ROC"] / 100.0


@register_feature("log_ret_1", lookback=1, group="returns", sets=("core",))
def log_ret_1(df, ind):
    return _log_returns(df, 1)


@register_feature("log_ret_5", lookback=5, group="returns", sets=("core",))
def log_ret_5(df, ind):
    return _log_returns(df, 5)


@register_feature("log_ret_21", lookback=MONTH, group="returns", sets=("core",))
def log_ret_21(df, ind):
    return _log_returns(df, MONTH)


@register_feature("dist_high_63", lookback=QUARTER, group="trend", sets=("core",))
def dist_high_63(df, ind):
    return df["Close"] / df["High"].rolling(QUARTER, min_periods=QUARTER).max() - 1.0


@register_feature("dist_low_63", lookback=QUARTER, group="trend", sets=("core",))
def dist_low_63(df, ind):
    return df["Close"] / df["Low"].rolling(QUARTER, min_periods=QUARTER).min() - 1.0


@register_feature("natr", lookback=15, group="volatility", sets=("core",), indicators=("NATR",))
def natr(df, ind):
    return ind["NATR"] / 100.0


@register_feature("vol_21", lookback=MONTH + 1, group="volatility", sets=("core",))
def vol_21(df, ind):
    return _log_returns(df, 1).rolling(MONTH, min_periods=MONTH).std(ddof=1)


@register_feature("vol_63", lookback=QUARTER + 1, group="volatility", sets=("core",))
def vol_63(df, ind):
    return _log_returns(df, 1).rolling(QUARTER, min_periods=QUARTER).std(ddof=1)


@register_feature("vol_ratio", lookback=QUARTER + 1, group="volatility", sets=("core",))
def vol_ratio(df, ind):
    long = vol_63(df, ind)
    return vol_21(df, ind) / long.where(long > 0)


@register_feature("range_pct", lookback=1, group="volatility", sets=("core",))
def range_pct(df, ind):
    return (df["High"] - df["Low"]) / df["Close"]


@register_feature("gap", lookback=2, group="returns", sets=("core",))
def gap(df, ind):
    return np.log(df["Open"] / df["Close"].shift(1))


@register_feature("volume_z", lookback=QUARTER + 1, group="volume", sets=("core",))
def volume_z(df, ind):
    return rolling_zscore(np.log(df["Volume"].where(df["Volume"] > 0)), QUARTER)


@register_feature("sma200_ratio", lookback=200, group="trend", indicators=("SMA 200",))
def sma200_ratio(df, ind):
    return _ratio_to_close(df, ind["SMA 200"])


@register_feature("dema_ratio", lookback=40, group="trend", indicators=("DEMA",))
def dema_ratio(df, ind):
    return _ratio_to_close(df, ind["DEMA 20"])


@register_feature("tema_ratio", lookback=60, group="trend", indicators=("TEMA",))
def tema_ratio(df, ind):
    return _ratio_to_close(df, ind["TEMA 20"])


@register_feature("kama_ratio", lookback=32, group="trend", indicators=("KAMA",))
def kama_ratio(df, ind):
    return _ratio_to_close(df, ind["KAMA 30"])


@register_feature("sar_distance", lookback=2, group="trend", indicators=("Parabolic SAR",))
def sar_distance(df, ind):
    return (df["Close"] - ind["SAR"]) / df["Close"]


@register_feature("ht_trendline_ratio", lookback=HILBERT_LOOKBACK, group="trend", indicators=("HT Trendline",))
def ht_trendline_ratio(df, ind):
    return _ratio_to_close(df, ind["HT Trendline"])


@register_feature("kc_position", lookback=22, group="volatility", indicators=("Keltner Channel",))
def kc_position(df, ind):
    return _channel_position(df, ind["KC Upper"], ind["KC Lower"])


@register_feature("aroon_osc", lookback=15, group="trend", indicators=("Aroon",))
def aroon_osc(df, ind):
    return (ind["Aroon Up"] - ind["Aroon Down"]) / 100.0


@register_feature("stochrsi_k", lookback=22, group="momentum", indicators=("Stochastic RSI",))
def stochrsi_k(df, ind):
    return ind["StochRSI %K"] / 100.0


@register_feature("ppo", lookback=26, group="momentum", indicators=("PPO",))
def ppo(df, ind):
    return ind["PPO"] / 100.0


@register_feature("trix", lookback=44, group="momentum", indicators=("TRIX",))
def trix(df, ind):
    return ind["TRIX"] / 100.0


@register_feature("cci", lookback=20, group="momentum", indicators=("CCI",))
def cci(df, ind):
    return ind["CCI"] / 100.0


@register_feature("williams_r", lookback=14, group="momentum", indicators=("Williams %R",))
def williams_r(df, ind):
    return ind["%R"] / 100.0


@register_feature("ultimate_osc", lookback=29, group="momentum", indicators=("Ultimate Oscillator",))
def ultimate_osc(df, ind):
    return ind["UO"] / 100.0


@register_feature("obv_slope", lookback=MONTH + 5, group="volume", indicators=("OBV",))
def obv_slope(df, ind):
    return _volume_scaled(ind["OBV"].diff(5), df)


@register_feature("ad_slope", lookback=MONTH + 5, group="volume", indicators=("Chaikin A/D",))
def ad_slope(df, ind):
    return _volume_scaled(ind["AD"].diff(5), df)


@register_feature("chaikin_osc", lookback=MONTH, group="volume", indicators=("Chaikin Oscillator",))
def chaikin_osc(df, ind):
    return _volume_scaled(ind["Chaikin"], df)


@register_feature("ht_dc_phase", lookback=HILBERT_LOOKBACK, group="cycle", indicators=("HT DC Phase",))
def ht_dc_phase(df, ind):
    return ind["DC Phase"] / 360.0


@register_feature("ht_dc_period", lookback=HILBERT_LOOKBACK, group="cycle", indicators=("HT DC Period",))
def ht_dc_period(df, ind):
    return ind["DC Period"] / 50.0


@register_feature("ht_sine", lookback=HILBERT_LOOKBACK, group="cycle", indicators=("HT Sine Wave",))
def ht_sine(df, ind):
    return ind["Sine"]


@register_feature("ht_lead_sine", lookback=HILBERT_LOOKBACK, group="cycle", indicators=("HT Sine Wave",))
def ht_lead_sine(df, ind):
    return ind["Lead Sine"]


@register_feature("ht_trend_mode", lookback=HILBERT_LOOKBACK, group="cycle", indicators=("HT Trend Mode",))
def ht_trend_mode(df, ind):
    return ind["Trend Mode"].astype(float)
