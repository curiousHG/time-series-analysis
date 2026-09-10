"""ML diagnostic chart builders (ui/charts/ml_diagnostics.py) on synthetic frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from ui.charts import ml_diagnostics as mlc
from ui.charts import theme


def _importance(n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    windows = pd.DataFrame(rng.random((n, 3)), index=[f"f{i}" for i in range(n)], columns=["w0", "w1", "w2"])
    windows["importance"] = windows[["w0", "w1", "w2"]].mean(axis=1)
    windows["std"] = windows[["w0", "w1", "w2"]].std(axis=1)
    return windows


def test_feature_importance_bar_top_n_with_error_bars():
    fig = mlc.feature_importance_bar(_importance(), top_n=10)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert len(trace.y) == 10
    assert trace.error_x.array is not None and len(trace.error_x.array) == 10
    assert list(trace.x) == sorted(trace.x)


def test_window_metrics_bar_has_zero_line():
    windows = pd.DataFrame(
        {"test_start": pd.to_datetime(["2023-01-01", "2023-04-01", "2023-07-01"]), "ic": [0.1, -0.05, 0.2]}
    )
    fig = mlc.window_metrics_bar(windows, "ic")
    assert len(fig.data) == 1
    assert len(fig.data[0].marker.color) == 3
    assert any(s.type == "line" and s.y0 == 0 for s in fig.layout.shapes)


def test_prediction_histogram_threshold_lines():
    preds = pd.DataFrame({"pred": np.random.default_rng(0).normal(size=200)})
    fig = mlc.prediction_histogram(preds, thresholds=(0.5, 1.0))
    assert len(fig.data) == 1
    assert len(fig.layout.shapes) == 2
    assert len(mlc.prediction_histogram(preds).layout.shapes) == 0


def test_coverage_area():
    cov = pd.Series([0.0, 0.5, 1.0, 1.0], index=pd.bdate_range("2024-01-01", periods=4))
    fig = mlc.coverage_area(cov)
    assert len(fig.data) == 1
    assert fig.data[0].fill == "tozeroy"
    assert fig.layout.yaxis.range == (0, 1)


def test_rank_metrics_are_drawn_against_the_no_skill_line():
    """AUC of 0.5 is a coin toss, so the bars must sit on a 0.5 base rather than growing from
    zero, which would make a skill-free model look uniformly good."""
    windows = pd.DataFrame(
        {"test_start": pd.to_datetime(["2024-01-01", "2024-04-01"]), "auc": [0.45, 0.60], "ic": [-0.1, 0.2]}
    )
    auc_fig = mlc.window_metrics_bar(windows, "auc")
    bar = auc_fig.data[0]
    assert bar.base == 0.5
    assert list(bar.y) == pytest.approx([-0.05, 0.10])
    assert list(bar.marker.color) == [theme.NEGATIVE, theme.POSITIVE]

    ic_fig = mlc.window_metrics_bar(windows, "ic")
    assert ic_fig.data[0].base == 0.0
