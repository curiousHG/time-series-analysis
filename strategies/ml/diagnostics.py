"""Tabular summary of a walk-forward run for the Backtest Lab detail view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from strategies.ml.walk_forward import WalkForwardResult

METRIC_KEYS = ("ic", "hit_rate", "rmse", "auc", "log_loss")


@dataclass
class MLDiagnostics:
    summary: dict
    windows: pd.DataFrame
    importance: pd.DataFrame
    predictions: pd.DataFrame
    coverage: pd.Series


def _windows_frame(result: WalkForwardResult) -> pd.DataFrame:
    rows = []
    for report in result.windows:
        w = report.window
        rows.append(
            {
                "idx": w.idx,
                "symbol": report.symbol,
                "train_start": w.train_start,
                "train_end": w.train_end,
                "test_start": w.test_start,
                "test_end": w.test_end,
                "n_train": report.n_train,
                "n_test": report.n_test,
                "fit_seconds": report.fit_seconds,
                **report.metrics,
            }
        )
    columns = [
        "idx",
        "symbol",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "n_train",
        "n_test",
        "fit_seconds",
    ]
    return pd.DataFrame(rows, columns=columns) if not rows else pd.DataFrame(rows)


def _importance_frame(result: WalkForwardResult) -> pd.DataFrame:
    stacks = [r.importance for r in result.windows if r.importance is not None]
    if not stacks:
        return pd.DataFrame(columns=["importance", "std", "n_windows"], index=pd.Index([], name="feature"))
    matrix = np.vstack(stacks)
    out = pd.DataFrame(
        {"importance": matrix.mean(axis=0), "std": matrix.std(axis=0), "n_windows": len(stacks)},
        index=pd.Index(result.feature_names, name="feature"),
    )
    return out.sort_values("importance", ascending=False)


def _predictions_frame(result: WalkForwardResult, frames: dict[str, pd.DataFrame], label_col: str) -> pd.DataFrame:
    parts = []
    for sym, pred in result.predictions.items():
        df = frames[sym]
        parts.append(
            pd.DataFrame(
                {
                    "date": pred.index,
                    "symbol": sym,
                    "pred": pred.to_numpy(),
                    "do_predict": result.do_predict[sym].to_numpy(),
                    "label": df[label_col].to_numpy() if label_col in df.columns else np.nan,
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["date", "symbol", "pred", "do_predict", "label"])
    return pd.concat(parts, ignore_index=True)


def build_diagnostics(result: WalkForwardResult, frames: dict[str, pd.DataFrame], label_col: str) -> MLDiagnostics:
    windows = _windows_frame(result)
    coverage = pd.Series(
        {sym: float(flags.mean()) if len(flags) else 0.0 for sym, flags in result.do_predict.items()},
        name="coverage",
        dtype=float,
    )
    kind = "classifier" if any("auc" in r.metrics for r in result.windows) else "regressor"
    summary: dict = {
        "kind": kind,
        "n_windows": len(result.windows),
        "n_symbols": len(result.predictions),
        "n_features": len(result.feature_names),
        "fit_seconds": float(windows["fit_seconds"].sum()) if len(windows) else 0.0,
        "coverage_mean": float(coverage.mean()) if len(coverage) else 0.0,
    }
    for key in METRIC_KEYS:
        if key in windows.columns:
            summary[f"{key}_mean"] = float(windows[key].mean())
            summary[f"{key}_min"] = float(windows[key].min())
    if "ic" in windows.columns and len(windows) > 1 and windows["ic"].std() > 0:
        summary["ic_ir"] = float(windows["ic"].mean() / windows["ic"].std())
    return MLDiagnostics(
        summary=summary,
        windows=windows,
        importance=_importance_frame(result),
        predictions=_predictions_frame(result, frames, label_col),
        coverage=coverage,
    )
