"""Flow tests for the Stock Analysis chart renderer (ui.views.stock_analysis.chart).

These render REAL frame shapes end-to-end through `chart.render` (with the lightweight-charts
component + st.subheader stubbed) so the null-OHLC / null-volume paths are exercised — the class of
bug that crashed "CNX 100 Equal Weight" (an index that carries close values but no open/high/low)."""

from __future__ import annotations

import pandas as pd
import pytest

import ui.views.stock_analysis.chart as chart


@pytest.fixture
def captured(monkeypatch):
    """Capture the charts payload instead of rendering the JS component."""
    cap: dict = {}
    monkeypatch.setattr(chart, "renderLightweightCharts", lambda charts, key: cap.__setitem__("charts", charts))
    return cap


def _frame(closes, opens):
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "Date": dates,
            "time": dates.strftime("%Y-%m-%d"),
            "Open": opens,
            "High": [None if o is None else o + 1 for o in opens],
            "Low": [None if o is None else o - 1 for o in opens],
            "Close": closes,
            "Volume": [1000 + i for i in range(n)],
        }
    )


def test_full_ohlc_renders_candlestick(captured):
    chart.render(_frame([100, 101, 102], [99, 100, 101]), {}, {}, [], "RELIANCE")
    charts = captured["charts"]
    assert charts[0]["series"][0]["type"] == "Candlestick"
    assert len(charts[0]["series"][0]["data"]) == 3
    # price pane + volume pane
    assert any(c["series"][0]["type"] == "Histogram" for c in charts[1:])


def test_close_only_index_falls_back_to_line_without_crashing(captured):
    # "CNX 100 Equal Weight" shape: close + volume present, open/high/low all None.
    chart.render(_frame([100, 101, 102], [None, None, None]), {}, {}, [], "CNX 100 Equal Weight")
    charts = captured["charts"]
    # No candles possible → price pane is a Line of closes, not a Candlestick.
    assert charts[0]["series"][0]["type"] == "Line"
    assert len(charts[0]["series"][0]["data"]) == 3
    # Volume pane still builds (colour must not do close >= None).
    vol = next(c["series"][0] for c in charts[1:] if c["series"][0]["type"] == "Histogram")
    assert len(vol["data"]) == 3


def test_markers_land_on_price_series(captured):
    markers = [
        {"time": "2024-01-03", "position": "aboveBar", "color": "#ef4444", "shape": "arrowDown", "text": "exit"},
        {"time": "2024-01-01", "position": "belowBar", "color": "#10b981", "shape": "arrowUp", "text": "entry"},
        {"time": "2030-01-01", "position": "belowBar", "color": "#10b981", "shape": "arrowUp", "text": "orphan"},
    ]
    chart.render(_frame([100, 101, 102], [99, 100, 101]), {}, {}, [], "RELIANCE", markers=markers)
    price = captured["charts"][0]["series"][0]
    assert [m["time"] for m in price["markers"]] == ["2024-01-01", "2024-01-03"]
    assert "markers" not in captured["charts"][0]["series"][0]["options"]


def test_no_markers_key_without_markers(captured):
    chart.render(_frame([100, 101, 102], [99, 100, 101]), {}, {}, [], "RELIANCE")
    assert "markers" not in captured["charts"][0]["series"][0]


def test_partial_null_rows_are_skipped_in_candles(captured):
    # One row has a null open — it must be dropped from candles, not crash.
    chart.render(_frame([100, 101, 102], [99, None, 101]), {}, {}, [], "SOMESTOCK")
    candles = captured["charts"][0]["series"][0]
    assert candles["type"] == "Candlestick"
    assert len(candles["data"]) == 2  # the null-open row dropped
