# Define functions to calculate technical indicators
from scipy.signal import hilbert, chirp, find_peaks, peak_widths, welch, windows
from scipy.signal.windows import hamming
from scipy.signal import hilbert
import numpy as np
import pandas as pd


def MACD(df, fast=12, slow=26, signal=9):
    exp1 = df["Close"].ewm(span=fast, adjust=False).mean()
    exp2 = df["Close"].ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal


def ATR(df, n=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(n).mean()
    return atr


def normalized_ATR(df, n=14):
    atr = ATR(df, n)
    natr = atr / df["Close"] * 100
    return natr


def momentum(df, n=10):
    return df["Close"].diff(n)


def CO(df):
    adl = (
        (2 * df["Close"] - df["High"] - df["Low"])
        / (df["High"] - df["Low"])
        * df["Volume"]
    )
    ema3 = adl.ewm(span=3, adjust=False).mean()
    ema10 = adl.ewm(span=10, adjust=False).mean()
    co = ema3 - ema10
    return co


def OBV(df):
    vol = df["Volume"]
    change = np.where(df["Close"].diff() > 0, 1, -1)
    obv = (vol * change).cumsum()
    return obv


# def MFI(df, n=14):
#     typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
#     money_flow = typical_price * df["Volume"]
#     pos_flow = np.where(typical_price.diff() > 0, money_flow, 0)
#     neg_flow = np.where(typical_price.diff() < 0, money_flow, 0)
#     pos_mf = pos_flow.rolling(n).sum()
#     neg_mf = neg_flow.rolling(n).sum()
#     mf_ratio = pos_mf / neg_mf
#     mfi = 100 - (100 / (1 + mf_ratio))
#     return mfi
# def MFI(df, n=14):
#     # Calculate the typical price
#     typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
#     # Calculate the raw money flow
#     raw_money_flow = typical_price * df["Volume"]
#     # Calculate the money flow ratio
#     positive_flow = np.where(typical_price > typical_price.shift(), raw_money_flow, 0)
#     negative_flow = np.where(typical_price < typical_price.shift(), raw_money_flow, 0)
#     positive_mf = pd.Series(positive_flow).rolling(window=n, min_periods=0).sum()
#     negative_mf = pd.Series(negative_flow).rolling(window=n, min_periods=0).sum()
#     money_flow_ratio = positive_mf / negative_mf
#     # Calculate the money flow index
#     money_flow_index = 100 - (100 / (1 + money_flow_ratio))
#     return money_flow_index


def MFI(df):
    # Calculate the Money Flow Index
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    raw_money_flow = typical_price * df["Volume"]
    up_flow = np.where(typical_price > typical_price.shift(1), raw_money_flow, 0)
    down_flow = np.where(typical_price < typical_price.shift(1), raw_money_flow, 0)
    positive_money_flow = np.sum(up_flow)
    negative_money_flow = np.sum(down_flow)
    money_ratio = np.divide(
        positive_money_flow, negative_money_flow, where=negative_money_flow != 0
    )
    mfi = np.where(negative_money_flow == 0, 100, 100 - (100 / (1 + money_ratio)))
    return mfi


# def HTDCP(df):
#     cycle, trend = pd.core.window.HammingWindow(window_len=11).frequency_response()*100
#     hilbert = pd.Series(pd.Series.rolling(df["Close"], window=31, center=True).apply(lambda x: np.abs(pd.Series(x).hilbert().real))).ewm(span=21, adjust=False).mean()
#     phase = np.degrees(np.arctan((hilbert - hilbert.shift(1)) / 0.0001))
#     dcp = ((phase.rolling(14).sum() / 14) - 90) * (-1)
#     return dcp


def HTDCP(df):
    # Calculate the Hilbert Transform Dominant Cycle Phase
    analytic_signal = hilbert(df["Close"])
    instantaneous_phase = np.degrees(np.unwrap(np.angle(analytic_signal)))
    hilbert_transform = np.abs(analytic_signal)
    hilbert_envelope = (
        pd.Series(hilbert_transform)
        .rolling(window=31, center=True, min_periods=1)
        .mean()
    )
    hilbert_smoothed = (
        pd.Series(hilbert_envelope)
        .rolling(window=11, center=True, min_periods=1)
        .apply(lambda x: np.convolve(x, hamming(11), mode="same"), raw=True)
    )
    hilbert_smoothed /= np.sum(hamming(11))
    hilbert_difference = np.abs(hilbert_smoothed.diff())
    hilbert_smoothed_numpy = np.asarray(hilbert_smoothed)  # convert to numpy array
    dcphase = np.degrees(
        np.unwrap(
            np.angle(
                pd.Series(hilbert_smoothed_numpy * np.exp(-1j * instantaneous_phase))
                .rolling(window=31, center=True)
                .mean()
            )
        )
    )
    return dcphase


# def HTS(df, n=14):
#     # Extract the analytic signal using the Hilbert Transform
#     hilbert = pd.Series(pd.Series.rolling(df["Close"], window=31, center=True).apply(lambda x: pd.Series(x).hilbert()))
#     # Calculate the instantaneous amplitude and phase
#     inst_amplitude = np.abs(hilbert)
#     inst_phase = np.degrees(np.unwrap(np.angle(hilbert)))
#     # Calculate the sine wave
#     sinewave = np.sin(np.radians(inst_phase))
#     # Smooth the sine wave using an EMA
#     hts = pd.Series(sinewave).ewm(span=n, min_periods=n).mean()
#     return hts


def HTS(df):
    # Calculate the Hilbert Transform Sinewave
    analytic_signal = hilbert(df["Close"])
    amplitude_envelope = np.abs(analytic_signal)
    instant_phase = np.unwrap(np.angle(analytic_signal))
    sinewave = np.sin(instant_phase)
    return sinewave


# def HTTMM(df):
#     # Calculate the Hilbert Transform Trend Market Mode
#     imf = pd.core.window.HammingWindow(window_len=11).frequency_response()*100
#     hilbert = pd.Series(pd.Series.rolling(df["Close"], window=31, center=True).apply(lambda x: np.abs(pd.Series(x).hilbert().real))).ewm(span=21, adjust=False).mean()
#     smoothed_imf = pd.Series(hilbert).ewm(span=14, adjust=False).mean()
#     trend = np.where(smoothed_imf > smoothed_imf.shift(), 1, -1)
#     return trend


# from scipy.signal import hilbert, chirp, find_peaks, peak_widths, welch, windows

# def HTTMM(df):
#     # Calculate the Hilbert Transform Trend Market Mode
#     instantaneous_phase = np.unwrap(np.angle(hilbert(df['Close'])))
#     inst_period = np.diff(instantaneous_phase)
#     inst_period = np.insert(inst_period, 0, inst_period[0])
#     inst_frequency = np.divide(1, inst_period)
#     frequency = welch(df['Close'], window='hamming', nperseg=len(df['Close']))[0]
#     peak_ind, _ = find_peaks(frequency, prominence=0.1)
#     widths = peak_widths(frequency, peak_ind, rel_height=0.5)
#     dominant_period = np.mean(widths[0] / len(frequency))
#     trend_market_mode = np.mod(np.divide(360, dominant_period) * instantaneous_phase, 360)
#     return trend_market_mode


def HTTMM(df):
    if df.empty:
        return pd.Series()

    # Calculate the Hilbert Transform Trend Market Mode
    instantaneous_phase = np.unwrap(np.angle(hilbert(df["Close"])))
    inst_period = np.diff(instantaneous_phase)
    inst_period = np.insert(inst_period, 0, inst_period[0])
    inst_frequency = np.divide(1, inst_period)
    frequency = welch(df["Close"], window="hamming", nperseg=len(df["Close"]))[0]
    peak_ind, _ = find_peaks(frequency, prominence=0.1)
    widths = peak_widths(frequency, peak_ind, rel_height=0.5)
    dominant_period = np.mean(widths[0] / len(frequency))
    trend_market_mode = np.mod(
        np.divide(360, dominant_period) * instantaneous_phase, 360
    )
    return trend_market_mode


# # Create a new dataframe to store the technical indicators
# features = pd.DataFrame(index=data.index)

# # Calculate the technical indicators
# features["macd"], features["signal"] = MACD(data)
# features["atr"] = ATR(data)
# features["natr"] = normalized_ATR(data)
# features["momentum"] = momentum(data)
# features["co"] = CO(data)
# features["obv"] = OBV(data)
# features["mfi"] = MFI(data)
# features["dcp"] = HTDCP(data)
# features["hts"] = HTS(data)
# features["httmm"] = HTTMM(data)

# # Print the resulting dataframe
# print(features.head())
