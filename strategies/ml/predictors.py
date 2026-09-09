"""Model zoo behind one `Predictor` interface.

Classifiers return P(y == 1); regressors return the point estimate. Every model is seeded and
LightGBM runs deterministically on a fixed thread count so a rerun reproduces a backtest.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

PredictorKind = Literal["regressor", "classifier"]

CANONICAL_HYPERPARAMS = ("n_estimators", "learning_rate", "num_leaves", "max_depth", "min_child_samples")


class Predictor(Protocol):
    kind: str

    def fit(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None) -> Predictor: ...

    def predict(self, X: np.ndarray) -> np.ndarray: ...

    def feature_importance(self) -> np.ndarray | None: ...


class SklearnPredictor:
    """Adapts any sklearn-style estimator: predict_proba for classifiers, coef_/importances_ for importance."""

    def __init__(self, model: Any, kind: PredictorKind) -> None:
        self.model = model
        self.kind = kind

    def fit(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None) -> SklearnPredictor:
        if sample_weight is None:
            self.model.fit(X, y)
        else:
            self.model.fit(X, y, sample_weight=sample_weight)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.kind == "classifier":
            proba = self.model.predict_proba(X)
            classes = list(self.model.classes_)
            column = classes.index(1) if 1 in classes else len(classes) - 1
            return np.asarray(proba[:, column], dtype=float)
        return np.asarray(self.model.predict(X), dtype=float).ravel()

    def feature_importance(self) -> np.ndarray | None:
        importances = getattr(self.model, "feature_importances_", None)
        if importances is not None:
            return np.asarray(importances, dtype=float)
        coef = getattr(self.model, "coef_", None)
        if coef is None:
            return None
        coef = np.asarray(coef, dtype=float)
        return np.abs(coef.reshape(-1, coef.shape[-1])).sum(axis=0) if coef.ndim > 1 else np.abs(coef)


@dataclass(frozen=True)
class PredictorSpec:
    name: str
    kind: PredictorKind
    factory: Callable[[dict[str, Any], int], Predictor]
    defaults: dict[str, Any] = field(default_factory=dict)
    requires: tuple[str, ...] = ("sklearn",)
    aliases: dict[str, str | None] = field(default_factory=dict)


PREDICTOR_REGISTRY: dict[str, PredictorSpec] = {}


def _lightgbm_threads() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


def _ridge(params: dict, seed: int) -> Predictor:
    from sklearn.linear_model import Ridge  # noqa: PLC0415

    return SklearnPredictor(Ridge(**params), "regressor")


def _logistic(params: dict, seed: int) -> Predictor:
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415

    return SklearnPredictor(LogisticRegression(random_state=seed, **params), "classifier")


def _random_forest_clf(params: dict, seed: int) -> Predictor:
    from sklearn.ensemble import RandomForestClassifier  # noqa: PLC0415

    return SklearnPredictor(RandomForestClassifier(random_state=seed, **params), "classifier")


def _random_forest_reg(params: dict, seed: int) -> Predictor:
    from sklearn.ensemble import RandomForestRegressor  # noqa: PLC0415

    return SklearnPredictor(RandomForestRegressor(random_state=seed, **params), "regressor")


def _hist_gb_clf(params: dict, seed: int) -> Predictor:
    from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: PLC0415

    return SklearnPredictor(HistGradientBoostingClassifier(random_state=seed, **params), "classifier")


def _hist_gb_reg(params: dict, seed: int) -> Predictor:
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: PLC0415

    return SklearnPredictor(HistGradientBoostingRegressor(random_state=seed, **params), "regressor")


def _lightgbm_common(seed: int) -> dict[str, Any]:
    return {
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "random_state": seed,
        "num_threads": _lightgbm_threads(),
    }


def _lightgbm_clf(params: dict, seed: int) -> Predictor:
    from lightgbm import LGBMClassifier  # noqa: PLC0415

    return SklearnPredictor(LGBMClassifier(**_lightgbm_common(seed), **params), "classifier")


def _lightgbm_reg(params: dict, seed: int) -> Predictor:
    from lightgbm import LGBMRegressor  # noqa: PLC0415

    return SklearnPredictor(LGBMRegressor(**_lightgbm_common(seed), **params), "regressor")


_FOREST_ALIASES = {
    "n_estimators": "n_estimators",
    "learning_rate": None,
    "num_leaves": None,
    "max_depth": "max_depth",
    "min_child_samples": "min_samples_leaf",
}
_HIST_GB_ALIASES = {
    "n_estimators": "max_iter",
    "learning_rate": "learning_rate",
    "num_leaves": "max_leaf_nodes",
    "max_depth": "max_depth",
    "min_child_samples": "min_samples_leaf",
}
_LINEAR_ALIASES = dict.fromkeys(CANONICAL_HYPERPARAMS)
_LIGHTGBM_ALIASES = {name: name for name in CANONICAL_HYPERPARAMS}

_SPECS = (
    PredictorSpec("ridge", "regressor", _ridge, {"alpha": 1.0}, ("sklearn",), _LINEAR_ALIASES),
    PredictorSpec("logistic", "classifier", _logistic, {"C": 1.0, "max_iter": 1000}, ("sklearn",), _LINEAR_ALIASES),
    PredictorSpec(
        "random_forest_clf",
        "classifier",
        _random_forest_clf,
        {"n_estimators": 200, "min_samples_leaf": 20, "n_jobs": 1},
        ("sklearn",),
        _FOREST_ALIASES,
    ),
    PredictorSpec(
        "random_forest_reg",
        "regressor",
        _random_forest_reg,
        {"n_estimators": 200, "min_samples_leaf": 20, "n_jobs": 1},
        ("sklearn",),
        _FOREST_ALIASES,
    ),
    PredictorSpec(
        "hist_gb_clf",
        "classifier",
        _hist_gb_clf,
        {"max_iter": 200, "learning_rate": 0.05},
        ("sklearn",),
        _HIST_GB_ALIASES,
    ),
    PredictorSpec(
        "hist_gb_reg",
        "regressor",
        _hist_gb_reg,
        {"max_iter": 200, "learning_rate": 0.05},
        ("sklearn",),
        _HIST_GB_ALIASES,
    ),
    PredictorSpec(
        "lightgbm_clf",
        "classifier",
        _lightgbm_clf,
        {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50},
        ("lightgbm",),
        _LIGHTGBM_ALIASES,
    ),
    PredictorSpec(
        "lightgbm_reg",
        "regressor",
        _lightgbm_reg,
        {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50},
        ("lightgbm",),
        _LIGHTGBM_ALIASES,
    ),
)
for _spec in _SPECS:
    PREDICTOR_REGISTRY[_spec.name] = _spec


def _importable(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def available_predictors(kind: PredictorKind | None = None) -> list[str]:
    return [
        name
        for name, spec in PREDICTOR_REGISTRY.items()
        if (kind is None or spec.kind == kind) and all(_importable(m) for m in spec.requires)
    ]


def adapt_params(name: str, canonical: dict[str, Any]) -> dict[str, Any]:
    """Translate the canonical tree hyper-parameters into `name`'s own keyword names, dropping the inapplicable."""
    aliases = PREDICTOR_REGISTRY[name].aliases
    out: dict[str, Any] = {}
    for key, value in canonical.items():
        target = aliases.get(key, key)
        if target is not None:
            out[target] = value
    return out


def make_predictor(name: str, params: dict[str, Any] | None = None, *, seed: int = 42) -> Predictor:
    if name not in PREDICTOR_REGISTRY:
        raise KeyError(f"unknown predictor {name!r}; choose from {sorted(PREDICTOR_REGISTRY)}")
    spec = PREDICTOR_REGISTRY[name]
    merged = {**spec.defaults, **(params or {})}
    return spec.factory(merged, seed)
