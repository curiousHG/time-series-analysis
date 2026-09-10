"""Basket result summaries: legacy trade shape, drawdowns, streaks, per-symbol / per-reason splits."""

from __future__ import annotations

import inspect
from datetime import date

import numpy as np
import pandas as pd
import pytest

from core.constants import TRADING_DAYS
from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERO
from services.backtest.engine import run_engine
from services.backtest.metrics import (
    consecutive_streaks,
    drawdown_stats,
    per_exit_reason_stats,
    per_symbol_stats,
    periodic_breakdown,
    summarize_result,
    trades_to_legacy,
)
from services.backtest.universe import BasketInputs
from strategies.basket import ENTER, EXIT, BasketStrategy


def _trades() -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=20)
    rows = [
        ("A", idx[0], idx[3], 100.0, 110.0, 10, 1000.0, 1.0, 1.5, 97.5, 0.0974, "exit_signal", "sig", 3),
        ("A", idx[4], idx[6], 110.0, 100.0, 10, 1100.0, 1.0, 1.5, -102.5, -0.0931, "stop_loss", "sig", 2),
        ("B", idx[2], idx[9], 50.0, 60.0, 20, 1000.0, 1.0, 1.5, 197.5, 0.1973, "roi", "sig", 7),
        ("B", idx[10], idx[19], 60.0, 55.0, 20, 1200.0, 1.0, 1.5, -102.5, -0.0854, "force_exit", "sig", 9),
        ("C", idx[5], idx[8], 10.0, 12.0, 100, 1000.0, 1.0, 1.5, 197.5, 0.1973, "exit_signal", "sig", 3),
    ]
    columns = [
        "symbol",
        "open_date",
        "close_date",
        "open_rate",
        "close_rate",
        "shares",
        "stake_amount",
        "entry_costs",
        "exit_costs",
        "profit_abs",
        "profit_ratio",
        "exit_reason",
        "enter_tag",
        "bars_held",
    ]
    df = pd.DataFrame(rows, columns=columns)
    df["max_rate"] = df[["open_rate", "close_rate"]].max(axis=1)
    df["min_rate"] = df[["open_rate", "close_rate"]].min(axis=1)
    df["exit_tag"] = None
    return df


def test_compute_metrics_annualises_with_trading_days():
    """No calendar literal in the metrics function, and its risk-free rate is the shared constant
    that is itself derived from TRADING_DAYS."""
    from services import backtest_service
    from services.constants import BACKTEST_RF_DAILY, BACKTEST_RISK_FREE

    source = inspect.getsource(backtest_service.compute_metrics)
    assert "252" not in source
    assert "BACKTEST_RF_DAILY" in source
    assert BACKTEST_RF_DAILY == BACKTEST_RISK_FREE / TRADING_DAYS
    assert TRADING_DAYS == 252


def test_trades_to_legacy_has_the_compute_metrics_shape():
    legacy = trades_to_legacy(_trades())
    assert list(legacy.columns) == ["Return", "PnL", "Entry Timestamp", "Exit Timestamp"]
    assert legacy["Return"].tolist() == _trades()["profit_ratio"].tolist()
    assert legacy["PnL"].tolist() == _trades()["profit_abs"].tolist()
    assert trades_to_legacy(_trades().iloc[0:0]).empty


def test_consecutive_streaks_count_the_longest_runs():
    assert consecutive_streaks(pd.Series([1.0, 2.0, -1.0, 3.0, 4.0, 5.0, -1.0, -2.0])) == (3, 2)
    assert consecutive_streaks(pd.Series([-1.0, -1.0])) == (0, 2)
    assert consecutive_streaks(pd.Series([], dtype=float)) == (0, 0)
    assert consecutive_streaks(pd.Series([0.0, 1.0])) == (1, 0)


def test_drawdown_stats_locate_the_peak_and_trough():
    idx = pd.bdate_range("2024-01-01", periods=8)
    equity = pd.Series([100.0, 110.0, 105.0, 90.0, 95.0, 120.0, 118.0, 125.0], index=idx)
    stats = drawdown_stats(equity)
    assert stats["max_drawdown_abs"] == pytest.approx(20.0)
    assert stats["max_drawdown_pct"] == pytest.approx(-20.0 / 110.0 * 100)
    assert stats["drawdown_start"] == idx[1]
    assert stats["drawdown_end"] == idx[3]
    assert stats["drawdown_days"] == 2
    flat = drawdown_stats(pd.Series([100.0, 101.0], index=idx[:2]))
    assert flat["max_drawdown_abs"] == 0.0
    assert flat["drawdown_start"] is None


def test_per_symbol_and_per_exit_reason_stats():
    by_symbol = per_symbol_stats(_trades()).set_index("symbol")
    assert list(by_symbol.index) == ["C", "B", "A"]
    assert by_symbol.loc["A", "trades"] == 2
    assert by_symbol.loc["A", "wins"] == 1
    assert by_symbol.loc["A", "win_rate"] == pytest.approx(50.0)
    assert by_symbol.loc["A", "profit_abs"] == pytest.approx(-5.0)
    assert by_symbol.loc["A", "costs"] == pytest.approx(5.0)
    assert by_symbol.loc["B", "avg_bars_held"] == pytest.approx(8.0)
    assert by_symbol.loc["B", "profit_abs"] == pytest.approx(95.0)

    by_reason = per_exit_reason_stats(_trades()).set_index("exit_reason")
    assert by_reason.loc["exit_signal", "trades"] == 2
    assert by_reason.loc["exit_signal", "profit_abs"] == pytest.approx(295.0)
    assert by_reason.loc["stop_loss", "win_rate"] == 0.0
    assert by_reason.loc["roi", "avg_profit_ratio"] == pytest.approx(0.1973)
    assert per_symbol_stats(_trades().iloc[0:0]).empty
    assert per_exit_reason_stats(_trades().iloc[0:0]).empty


def test_periodic_breakdown_by_month():
    idx = pd.bdate_range("2024-01-01", periods=45)
    equity = pd.Series(np.linspace(100.0, 144.0, 45), index=idx)
    monthly = periodic_breakdown(equity, "M")
    assert list(monthly.columns) == ["period", "start_equity", "end_equity", "return_pct"]
    assert len(monthly) == 3
    assert monthly.iloc[0]["start_equity"] == pytest.approx(100.0)
    assert monthly.iloc[-1]["end_equity"] == pytest.approx(144.0)
    assert monthly.iloc[1]["start_equity"] == pytest.approx(monthly.iloc[0]["end_equity"])
    chained = np.prod(1 + monthly["return_pct"] / 100)
    assert chained == pytest.approx(1.44)
    assert len(periodic_breakdown(equity, "W")) >= 9
    assert len(periodic_breakdown(equity, "Y")) == 1
    assert len(periodic_breakdown(equity, "D")) == 45
    assert periodic_breakdown(pd.Series(dtype=float), "M").empty


class _Buyer(BasketStrategy):
    name = "Buyer"
    max_open_trades = 1

    def populate_entry(self, df, symbol, ctx):
        df[ENTER] = np.arange(len(df)) == 0
        return df

    def populate_exit(self, df, symbol, ctx):
        df[EXIT] = np.arange(len(df)) == 5
        return df


def _result():
    idx = pd.bdate_range("2024-01-01", periods=12)
    close = pd.Series([100, 100, 110, 121, 115, 125, 120, 120, 120, 118, 122, 124], index=idx, dtype=float)
    frame = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=idx)
    bench_close = close * 0.5
    bench = pd.DataFrame(
        {"Open": bench_close, "High": bench_close, "Low": bench_close, "Close": bench_close, "Volume": 0.0}, index=idx
    )
    inputs = BasketInputs(
        calendar=idx,
        frames={"A": frame},
        start_pos=0,
        benchmark=bench,
        informative={},
        instrument_kinds={"A": "equity"},
        skipped=["Z"],
        warnings=[],
    )
    config = BacktestConfig(
        strategy="Buyer",
        params={},
        universe=UniverseSpec("list", symbols=("A",)),
        start=date(2024, 1, 1),
        end=date(2024, 1, 16),
        init_cash=100_000.0,
        costs=ZERO,
        slippage_bps=0.0,
    )
    return run_engine(inputs, _Buyer, {}, config)


def test_summarize_result_adds_the_basket_keys_on_top_of_compute_metrics():
    result = _result()
    metrics = summarize_result(result)
    expected = {
        "total_trades",
        "final_equity",
        "total_profit_abs",
        "total_return_pct",
        "total_costs",
        "entry_costs",
        "exit_costs",
        "avg_exposure",
        "max_open_positions",
        "consecutive_wins",
        "consecutive_losses",
        "best_day_pct",
        "worst_day_pct",
        "max_drawdown_abs",
        "drawdown_start",
        "drawdown_end",
        "drawdown_days",
        "benchmark_return_pct",
        "excess_return_pct",
        "beta",
        "symbols_traded",
        "sharpe",
        "cagr",
        "max_drawdown",
        "win_rate",
    }
    assert expected <= set(metrics)
    assert metrics["total_trades"] == 1
    assert metrics["symbols_traded"] == 1
    assert metrics["final_equity"] == pytest.approx(result.equity.iloc[-1])
    assert metrics["total_profit_abs"] == pytest.approx(result.equity.iloc[-1] - 100_000.0)
    assert metrics["total_return_pct"] == pytest.approx((result.equity.iloc[-1] / 100_000.0 - 1) * 100)
    assert metrics["total_costs"] == 0.0
    assert metrics["max_open_positions"] == 1
    assert 0 < metrics["avg_exposure"] < 100
    assert metrics["consecutive_wins"] == 1
    assert metrics["consecutive_losses"] == 0
    assert metrics["best_day_pct"] == pytest.approx(result.returns.max() * 100)
    assert metrics["worst_day_pct"] == pytest.approx(result.returns.min() * 100)
    assert metrics["max_drawdown_abs"] > 0
    assert metrics["drawdown_start"] is not None
    assert metrics["benchmark_return_pct"] == pytest.approx((124 / 100 - 1) * 100)
    assert metrics["excess_return_pct"] == pytest.approx(metrics["total_return_pct"] - metrics["benchmark_return_pct"])
    assert np.isfinite(metrics["beta"])
    assert result.metrics == metrics


def test_summarize_result_without_trades_or_benchmark_is_safe():
    result = _result()
    result.trades = result.trades.iloc[0:0]
    result.benchmark_returns = None
    metrics = summarize_result(result)
    assert metrics["total_trades"] == 0
    assert metrics["benchmark_return_pct"] is None
    assert metrics["beta"] is None
    assert metrics["consecutive_wins"] == 0
