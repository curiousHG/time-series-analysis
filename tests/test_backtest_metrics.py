"""Unit tests for services.backtest_service.compute_metrics.

Trade-derived math runs on real data; the quantstats-backed fields are pinned by
monkeypatching `qs.stats.*` so the wiring (key mapping + ×100 scaling) is tested
independently of quantstats' internal numerics.
"""

import pytest


def _returns():
    import pandas as pd

    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.Series([0.01, -0.02, 0.015, 0.005, -0.01], index=idx)


def _trades(returns, pnl, *, durations_days=None, timestamps=True):
    import pandas as pd

    data = {"Return": returns, "PnL": pnl}
    if timestamps:
        n = len(returns)
        days = durations_days if durations_days is not None else [2] * n
        entry = pd.to_datetime(["2024-01-01"] * n)
        exit_ = entry + pd.to_timedelta(days, unit="D")
        data["Entry Timestamp"] = entry
        data["Exit Timestamp"] = exit_
    return pd.DataFrame(data)


def test_trade_math_winners_and_losers():
    from services.backtest_service import compute_metrics

    trades = _trades([0.10, -0.05, 0.20, -0.10], [100.0, -50.0, 200.0, -80.0])
    m = compute_metrics(_returns(), trades)

    assert m["win_rate"] == 50.0
    assert m["avg_win"] == pytest.approx(15.0)
    assert m["avg_loss"] == pytest.approx(-7.5)
    assert m["best_trade"] == pytest.approx(20.0)
    assert m["worst_trade"] == pytest.approx(-10.0)
    assert m["payoff"] == pytest.approx(2.0)
    assert m["profit_factor"] == pytest.approx(300.0 / 130.0)
    assert m["expectancy"] == pytest.approx(42.5)
    assert m["sqn"] == pytest.approx(0.54470, abs=1e-3)
    assert m["avg_duration"] == "2 days 00:00:00"


def test_no_losers_gives_zero_payoff_and_inf_profit_factor():
    from services.backtest_service import compute_metrics

    trades = _trades([0.10, 0.20], [100.0, 200.0])
    m = compute_metrics(_returns(), trades)

    assert m["avg_loss"] == 0
    assert m["payoff"] == 0  # avg_loss == 0 → guarded, no ZeroDivision
    assert m["profit_factor"] == float("inf")  # gross_losses == 0


def test_sqn_zero_for_single_trade():
    from services.backtest_service import compute_metrics

    trades = _trades([0.10], [100.0])
    m = compute_metrics(_returns(), trades)

    assert m["sqn"] == 0


def test_sqn_zero_for_zero_variance_returns():
    from services.backtest_service import compute_metrics

    # Clean integer-valued returns → variance is exactly 0 (no float epsilon),
    # so the `std() > 0` guard short-circuits sqn to 0.
    trades = _trades([3.0, 3.0, 3.0], [100.0, 100.0, 100.0])
    m = compute_metrics(_returns(), trades)

    assert m["sqn"] == 0


def test_avg_duration_na_without_timestamp_columns():
    from services.backtest_service import compute_metrics

    trades = _trades([0.10, -0.05], [100.0, -50.0], timestamps=False)
    m = compute_metrics(_returns(), trades)

    assert m["avg_duration"] == "N/A"


def test_quantstats_fields_scaling_and_mapping(monkeypatch):
    from services import backtest_service
    from services.backtest_service import compute_metrics

    stubs = {
        "cagr": 0.1,
        "comp": 0.2,
        "max_drawdown": -0.3,
        "volatility": 0.4,
        "sharpe": 1.5,
        "sortino": 2.5,
        "calmar": 3.5,
        "var": -0.05,
        "cvar": -0.07,
        "kelly_criterion": 0.25,
    }
    for name, value in stubs.items():
        monkeypatch.setattr(backtest_service.qs.stats, name, lambda *a, _v=value, **k: _v)

    trades = _trades([0.10, -0.05], [100.0, -50.0])
    m = compute_metrics(_returns(), trades)

    # ×100-scaled fields
    assert m["cagr"] == pytest.approx(10.0)
    assert m["cumulative_return"] == pytest.approx(20.0)
    assert m["max_drawdown"] == pytest.approx(-30.0)
    assert m["annual_volatility"] == pytest.approx(40.0)
    assert m["var_95"] == pytest.approx(-5.0)
    assert m["cvar_95"] == pytest.approx(-7.0)
    assert m["kelly"] == pytest.approx(25.0)
    # ratios passed through unscaled
    assert m["sharpe"] == pytest.approx(1.5)
    assert m["sortino"] == pytest.approx(2.5)
    assert m["calmar"] == pytest.approx(3.5)
