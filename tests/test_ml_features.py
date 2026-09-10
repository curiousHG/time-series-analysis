import numpy as np
import pandas as pd
import pytest

from strategies.ml.features import (
    FEATURE_PREFIX,
    FEATURE_REGISTRY,
    build_features,
    feature_names,
    rolling_zscore,
    startup_bars,
)


def synthetic_ohlcv(n: int = 400, seed: int = 7, start: str = "2020-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    log_ret = rng.normal(0.0004, 0.015, n)
    close = 100 * np.exp(np.cumsum(log_ret))
    open_ = close * np.exp(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.006, n)))
    volume = rng.lognormal(12, 0.4, n).round()
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return synthetic_ohlcv()


def test_registry_sets_have_expected_shape():
    core = feature_names("core")
    full = feature_names("full")
    assert 20 <= len(core) <= 30
    assert 40 <= len(full) <= 50
    assert set(core) <= set(full)
    assert all(name.startswith(FEATURE_PREFIX) for name in full)
    assert len(full) == len(set(full))
    assert set(full) == set(FEATURE_REGISTRY)
    assert not any("vwap" in name.lower() for name in full)
    with pytest.raises(KeyError):
        feature_names("nope")


@pytest.mark.parametrize("name", sorted(FEATURE_REGISTRY))
def test_every_feature_is_scale_invariant(frame, name):
    scaled = frame.copy()
    for col in ("Open", "High", "Low", "Close"):
        scaled[col] = scaled[col] * 10
    scaled["Volume"] = scaled["Volume"] * 7
    base = build_features(frame, [name])[name]
    other = build_features(scaled, [name])[name]
    assert base.notna().sum() > 100, f"{name} is mostly NaN"
    np.testing.assert_allclose(base.to_numpy(), other.to_numpy(), rtol=1e-4, atol=1e-6, equal_nan=True)


@pytest.mark.parametrize("name", sorted(FEATURE_REGISTRY))
def test_every_feature_is_truncation_invariant(frame, name):
    full = build_features(frame, [name])[name]
    cut = build_features(frame.iloc[:-40], [name])[name]
    np.testing.assert_allclose(full.iloc[:-40].to_numpy(), cut.to_numpy(), rtol=1e-9, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("name", sorted(FEATURE_REGISTRY))
def test_first_valid_index_within_declared_lookback(frame, name):
    series = build_features(frame, [name])[name]
    first = series.first_valid_index()
    position = frame.index.get_loc(first)
    assert position <= startup_bars([name]), f"{name} first valid at {position} > lookback {startup_bars([name])}"
    assert series.iloc[position:].notna().all()


def test_build_features_keeps_index_and_no_backfill(frame):
    names = feature_names("core")
    out = build_features(frame, names)
    assert list(out.columns) == names
    assert out.index.equals(frame.index)
    assert out.iloc[0].isna().any()
    assert np.isfinite(out.iloc[startup_bars(names) :].to_numpy()).all()


def test_startup_bars_is_the_max_lookback():
    assert startup_bars(feature_names("full")) >= startup_bars(feature_names("core"))
    assert startup_bars([]) == 0


def test_rolling_zscore_hand_check():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0])
    z = rolling_zscore(s, 3)
    assert z.iloc[:2].isna().all()
    assert z.iloc[2] == pytest.approx(1.0)
    assert z.iloc[4] == pytest.approx((10 - 17 / 3) / np.std([3, 4, 10], ddof=1))


def test_features_survive_non_trading_gaps_on_a_shared_calendar():
    """A frame reindexed onto a basket calendar has NaN rows where the symbol did not trade.
    TA-Lib returns NaN for every bar after such a gap, so features are computed on traded bars
    only and reindexed back."""
    frame = synthetic_ohlcv(400)
    calendar = frame.index.union(pd.DatetimeIndex(["2020-02-01", "2020-06-06"]))
    gapped = frame.reindex(calendar)
    assert gapped["Close"].isna().sum() == 2

    names = feature_names("core")
    built = build_features(gapped, names)

    assert built.loc[gapped["Close"].isna()].isna().all().all()
    traded_tail = built.loc[gapped["Close"].notna()].tail(100)
    assert traded_tail.notna().all().all()
    direct = build_features(frame, names)
    pd.testing.assert_frame_equal(built.loc[frame.index], direct, check_freq=False)
