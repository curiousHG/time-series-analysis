"""Signal-mode ML strategy: enter when P(up) clears `entry_threshold`, exit when it drops below
`exit_threshold`; the gap between the two is hysteresis. Every signal is masked by `do_predict`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from strategies.basket import ENTER, ENTER_TAG, EXIT, EXIT_TAG, SCORE, register_basket_strategy
from strategies.ml.ml_strategy import MLStrategy, prediction_mask
from strategies.ml.predictors import adapt_params, available_predictors
from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter

if TYPE_CHECKING:
    import pandas as pd

    from strategies.basket import MarketContext

CLASSIFIERS = tuple(available_predictors("classifier"))
DEFAULT_CLASSIFIER = "lightgbm_clf" if "lightgbm_clf" in CLASSIFIERS else "hist_gb_clf"


@register_basket_strategy
class MLDirectionClassifier(MLStrategy):
    name: ClassVar[str] = "ML Direction Classifier"
    mode: ClassVar[str] = "signal"
    label_name: ClassVar[str] = "direction"

    model = CategoricalParameter(DEFAULT_CLASSIFIER, CLASSIFIERS, help="Classifier from the predictor zoo")
    n_estimators = IntParameter(300, 50, 800, step=50, help="Trees / boosting rounds")
    learning_rate = DecimalParameter(0.05, 0.01, 0.30, step=0.01, help="Boosting learning rate")
    num_leaves = IntParameter(15, 7, 63, step=4, help="Leaves per tree")
    max_depth = IntParameter(4, 2, 12, help="Tree depth (-1 style unlimited not offered)")
    min_child_samples = IntParameter(50, 10, 300, step=10, help="Minimum rows per leaf")
    entry_threshold = DecimalParameter(0.58, 0.50, 0.80, step=0.01, help="Enter when P(up) is at least this")
    exit_threshold = DecimalParameter(0.48, 0.20, 0.55, step=0.01, help="Exit when P(up) is at most this")
    direction_threshold = DecimalParameter(
        0.0, -0.05, 0.05, step=0.005, optimize=False, help="Forward log return that counts as up"
    )

    def predictor_name(self) -> str:
        return self.model

    def model_params(self) -> dict[str, Any]:
        canonical = {
            "n_estimators": self.n_estimators,
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "max_depth": self.max_depth,
            "min_child_samples": self.min_child_samples,
        }
        return adapt_params(self.model, canonical)

    def label_kwargs(self) -> dict[str, Any]:
        return {"threshold": self.direction_threshold}

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        prob, ok = prediction_mask(df)
        df[ENTER] = (ok & (prob >= self.entry_threshold)).fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "ml_up", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        prob, ok = prediction_mask(df)
        df[EXIT] = (ok & (prob <= self.exit_threshold)).fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], "ml_down", None)
        return df

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        prob, ok = prediction_mask(df)
        df[SCORE] = prob.where(ok)
        return df
