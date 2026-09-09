"""Backtest chart builders (ui/charts/backtest.py) on synthetic inputs — shape checks only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from ui.charts import backtest as bt
from ui.charts import theme
from ui.charts.theme import with_alpha


def _equity(n: int = 60, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series(100_000 * np.cumprod(1 + rng.normal(0.0005, 0.01, n)), index=idx, name="equity")


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["A", "A", "B", "C"],
            "open_date": pd.to_datetime(["2024-01-02", "2024-02-01", "2024-01-10", "2024-01-15"]),
            "close_date": pd.to_datetime(["2024-01-20", "2024-02-20", "2024-02-05", "2024-01-25"]),
            "open_rate": [100.0, 110.0, 50.0, 200.0],
            "close_rate": [110.0, 105.0, 55.0, 190.0],
            "profit_abs": [1000.0, -500.0, 500.0, -800.0],
            "exit_reason": ["roi", "stop_loss", "exit_signal", "stop_loss"],
        }
    )


def test_with_alpha_formats_rgba():
    assert with_alpha("#ef4444", 0.18) == "rgba(239, 68, 68, 0.18)"
    assert with_alpha("10b981", 1) == "rgba(16, 185, 129, 1)"


def test_with_alpha_rejects_bad_input():
    with pytest.raises(ValueError):
        with_alpha("#abc", 0.5)


def test_equity_vs_benchmark_two_rows_and_traces():
    eq = _equity()
    bench = _equity(seed=1) / 40
    fig = bt.equity_vs_benchmark(eq, bench)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 3
    assert fig.layout.yaxis2.domain is not None and fig.layout.yaxis.domain[0] > fig.layout.yaxis2.domain[1]
    assert fig.layout.title.text is None
    dd = fig.data[-1]
    assert dd.fillcolor == with_alpha(theme.NEGATIVE, 0.18)
    assert float(np.nanmin(dd.y)) <= 0 and float(np.nanmax(dd.y)) <= 0


def test_equity_vs_benchmark_without_benchmark_and_rebased():
    fig = bt.equity_vs_benchmark(_equity(), None, rebase=True)
    assert len(fig.data) == 2
    assert fig.data[0].y[0] == pytest.approx(100.0)


def test_equity_vs_benchmark_benchmark_shares_equity_scale_when_not_rebased():
    eq = _equity()
    fig = bt.equity_vs_benchmark(eq, _equity(seed=2) / 1000, rebase=False)
    assert fig.data[1].y[0] == pytest.approx(float(eq.iloc[0]))


def test_equity_overlay_trace_count():
    curves = {"run a": _equity(), "run b": _equity(seed=3), "run c": _equity(seed=4)}
    assert len(bt.equity_overlay(curves, _equity(seed=5)).data) == 4
    assert len(bt.equity_overlay(curves, None).data) == 3
    fig = bt.equity_overlay(curves, None, rebase=True)
    assert all(t.y[0] == pytest.approx(100.0) for t in fig.data)


def test_per_symbol_bar_keeps_top_and_bottom_n():
    per_symbol = pd.DataFrame(
        {
            "symbol": [f"S{i}" for i in range(40)],
            "trades": [3] * 40,
            "profit_abs": [float(i - 20) * 100 for i in range(40)],
            "win_rate": [0.5] * 40,
        }
    )
    fig = bt.per_symbol_bar(per_symbol, top_n=5)
    assert len(fig.data) == 1
    assert len(fig.data[0].y) == 10
    assert fig.data[0].orientation == "h"
    colours = set(fig.data[0].marker.color)
    assert colours <= {theme.POSITIVE, theme.NEGATIVE}


def test_per_symbol_bar_small_input_keeps_all():
    per_symbol = pd.DataFrame({"symbol": ["A", "B"], "trades": [1, 2], "profit_abs": [5.0, -3.0], "win_rate": [1, 0]})
    assert len(bt.per_symbol_bar(per_symbol).data[0].y) == 2


def test_exit_reason_bar():
    per_exit = pd.DataFrame({"exit_reason": ["roi", "stop_loss"], "trades": [4, 2], "profit_abs": [900.0, -300.0]})
    fig = bt.exit_reason_bar(per_exit)
    assert len(fig.data) == 1
    assert list(fig.data[0].y) == ["stop_loss", "roi"]
    assert list(fig.data[0].customdata.ravel()) == [2, 4]


def test_cost_waterfall_ends_with_total_equal_to_net():
    costs = {"brokerage": 120.0, "stt": 80.0, "slippage": 50.0}
    fig = bt.cost_waterfall(1000.0, costs)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "waterfall"
    assert trace.measure[0] == "absolute"
    assert trace.measure[-1] == "total"
    assert trace.y[-1] == pytest.approx(1000.0 - 250.0)
    assert all(v < 0 for v in trace.y[1:-1])


def test_returns_histogram_two_sign_traces():
    rng = np.random.default_rng(0)
    fig = bt.returns_histogram(pd.Series(rng.normal(0, 0.01, 300)))
    assert len(fig.data) == 2
    assert fig.data[0].xbins.size == fig.data[1].xbins.size


def test_trade_markers_figure_has_line_and_markers():
    eq = _equity(n=60)
    price = pd.Series(np.linspace(100, 120, 60), index=eq.index)
    fig = bt.trade_markers_figure(price, _trades())
    assert len(fig.data) == 3
    entries, exits = fig.data[1], fig.data[2]
    assert len(entries.x) == 4 and len(exits.x) == 4
    assert set(entries.marker.color) <= {theme.POSITIVE, theme.NEGATIVE}
    assert "stop_loss" in exits.customdata[1]


def test_trade_markers_figure_empty_trades():
    price = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    fig = bt.trade_markers_figure(price, _trades().iloc[0:0])
    assert len(fig.data) == 3
