"""ML layer for basket strategies: features, labels, predictor zoo, walk-forward, diagnostics.

Importing the package registers the two concrete strategies (`MLDirectionClassifier`,
`MLReturnRanker`) in `BASKET_STRATEGY_REGISTRY`.
"""

from strategies.ml.direction_classifier import MLDirectionClassifier
from strategies.ml.features import DO_PREDICT_COL, FEATURE_PREFIX, PRED_COL, TARGET_PREFIX
from strategies.ml.ml_strategy import MLStrategy
from strategies.ml.return_ranker import MLReturnRanker
