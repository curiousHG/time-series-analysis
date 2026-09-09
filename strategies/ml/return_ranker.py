"""Rank-mode ML strategy: a regressor on the ATR-normalised forward gain, score = prediction where
`do_predict`. For signal mode the prediction's rolling z-score (over past predictions only) drives
entries above `entry_z` and exits below `exit_z`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from strategies.basket import ENTER, ENTER_TAG, EXIT, EXIT_TAG, SCORE, register_basket_strategy
from strategies.ml.features import rolling_zscore
from strategies.ml.ml_strategy import MLStrategy, prediction_mask
from strategies.ml.predictors import adapt_params, available_predictors
from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter

if TYPE_CHECKING:
    import pandas as pd

    from strategies.basket import MarketContext

REGRESSORS = tuple(available_predictors("regressor"))
DEFAULT_REGRESSOR = "lightgbm_reg" if "lightgbm_reg" in REGRESSORS else "hist_gb_reg"


@register_basket_strategy
class MLReturnRanker(MLStrategy):
    name: ClassVar[str] = "ML Return Ranker"
    mode: ClassVar[str] = "rank"
    label_name: ClassVar[str] = "atr_fwd_gain"

    top_k: int = 10
    rebalance_every = "M"

    model = CategoricalParameter(DEFAULT_REGRESSOR, REGRESSORS, help="Regressor from the predictor zoo")
    n_estimators = IntParameter(300, 50, 800, step=50, help="Trees / boosting rounds")
    learning_rate = DecimalParameter(0.05, 0.01, 0.30, step=0.01, help="Boosting learning rate")
    num_leaves = IntParameter(15, 7, 63, step=4, help="Leaves per tree")
    max_depth = IntParameter(4, 2, 12, help="Tree depth")
    min_child_samples = IntParameter(50, 10, 300, step=10, help="Minimum rows per leaf")
    zscore_window = IntParameter(63, 21, 252, optimize=False, help="Bars of past predictions behind the z-score")
    entry_z = DecimalParameter(1.0, 0.0, 3.0, step=0.1, help="Signal mode: enter when prediction z-score >= this")
    exit_z = DecimalParameter(0.0, -2.0, 2.0, step=0.1, help="Signal mode: exit when prediction z-score <= this")

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

    def _zscore(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        pred, ok = prediction_mask(df)
        return rolling_zscore(pred.where(ok), self.zscore_window), ok

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        z, ok = self._zscore(df)
        df[ENTER] = (ok & (z >= self.entry_z)).fillna(False).astype(bool)
        df[ENTER_TAG] = np.where(df[ENTER], "ml_z_high", None)
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        z, ok = self._zscore(df)
        df[EXIT] = (ok & (z <= self.exit_z)).fillna(False).astype(bool)
        df[EXIT_TAG] = np.where(df[EXIT], "ml_z_low", None)
        return df

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        pred, ok = prediction_mask(df)
        df[SCORE] = pred.where(ok)
        return df
