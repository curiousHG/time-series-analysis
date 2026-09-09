"""Optimiser chart builders (ui/charts/optimization.py) from plain trial DataFrames — no optuna."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ui.charts import optimization as opt


def _trials(n: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "number": range(n),
            "value": rng.normal(1.0, 0.3, n),
            "oos_value": np.r_[rng.normal(0.8, 0.3, 5), [np.nan] * (n - 5)],
            "state": ["COMPLETE"] * (n - 3) + ["PRUNED", "FAIL", "COMPLETE"],
            "fast": rng.integers(5, 30, n),
            "slow": rng.integers(30, 100, n),
            "kind": rng.choice(["sma", "ema"], n),
            "trailing": rng.choice([True, False], n),
        }
    )


def test_trials_history_traces_and_running_best():
    fig = opt.trials_history(_trials())
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 3
    complete, best, pruned = fig.data
    assert len(complete.x) == 10
    assert list(best.y) == sorted(best.y)
    assert len(pruned.x) == 2
    assert pruned.marker.symbol.endswith("open")


def test_trials_history_without_state_column():
    fig = opt.trials_history(_trials().drop(columns="state"))
    assert len(fig.data) == 2


def test_param_importance_bar_sorted():
    fig = opt.param_importance_bar({"fast": 0.2, "slow": 0.7, "kind": 0.1})
    assert len(fig.data) == 1
    assert list(fig.data[0].x) == [0.1, 0.2, 0.7]
    assert fig.data[0].orientation == "h"


def test_parallel_coordinates_maps_categoricals():
    fig = opt.parallel_coordinates(_trials(), ["fast", "kind", "trailing"])
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "parcoords"
    dims = {d.label: d for d in trace.dimensions}
    assert set(dims) == {"fast", "kind", "trailing", "value"}
    assert set(dims["kind"].ticktext) <= {"sma", "ema"}
    assert set(dims["trailing"].ticktext) <= {"True", "False"}
    assert set(dims["kind"].values) <= {0, 1}
    assert dims["fast"].ticktext is None


def test_param_slice():
    fig = opt.param_slice(_trials(), "slow")
    assert len(fig.data) == 1
    assert len(fig.data[0].x) == 10
    fig_cat = opt.param_slice(_trials(), "trailing")
    assert set(fig_cat.data[0].x) <= {"True", "False"}


def test_is_vs_oos_scatter_with_reference_line():
    fig = opt.is_vs_oos(_trials())
    assert len(fig.data) == 2
    points, ref = fig.data
    assert len(points.x) == 5
    assert ref.x[0] == ref.y[0] and ref.x[1] == ref.y[1]
