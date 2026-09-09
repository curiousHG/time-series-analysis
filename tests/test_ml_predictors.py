import importlib.util

import numpy as np
import pytest

from strategies.ml.predictors import PREDICTOR_REGISTRY, adapt_params, available_predictors, make_predictor

CANONICAL = {"n_estimators": 60, "learning_rate": 0.1, "num_leaves": 7, "max_depth": 3, "min_child_samples": 10}


def _linear_problem(seed: int = 0, n: int = 600):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    signal = 1.5 * X[:, 0] - 1.0 * X[:, 1] + 0.1 * rng.normal(size=n)
    return X, signal


def test_registry_contents_and_kinds():
    assert set(PREDICTOR_REGISTRY) == {
        "ridge",
        "logistic",
        "random_forest_clf",
        "random_forest_reg",
        "hist_gb_clf",
        "hist_gb_reg",
        "lightgbm_clf",
        "lightgbm_reg",
    }
    assert {PREDICTOR_REGISTRY[n].kind for n in PREDICTOR_REGISTRY} == {"regressor", "classifier"}
    assert "ridge" in available_predictors("regressor")
    assert "logistic" in available_predictors("classifier")
    assert set(available_predictors()) == set(available_predictors("regressor")) | set(
        available_predictors("classifier")
    )
    with pytest.raises(KeyError):
        make_predictor("nope")


def test_lightgbm_availability_follows_find_spec():
    has_lgb = importlib.util.find_spec("lightgbm") is not None
    assert ("lightgbm_reg" in available_predictors("regressor")) is has_lgb


@pytest.mark.parametrize("name", sorted(PREDICTOR_REGISTRY))
def test_every_predictor_fits_a_synthetic_linear_signal(name):
    spec = PREDICTOR_REGISTRY[name]
    for module in spec.requires:
        pytest.importorskip(module)
    X, signal = _linear_problem()
    train, test = slice(0, 400), slice(400, None)
    model = make_predictor(name, adapt_params(name, CANONICAL), seed=1)
    assert model.kind == spec.kind
    if spec.kind == "regressor":
        model.fit(X[train], signal[train])
        pred = model.predict(X[test])
        assert np.corrcoef(pred, signal[test])[0, 1] > 0.8
    else:
        y = (signal > 0).astype(int)
        model.fit(X[train], y[train], sample_weight=np.ones(400))
        pred = model.predict(X[test])
        assert pred.shape == (200,)
        assert ((pred >= 0) & (pred <= 1)).all()
        assert ((pred > 0.5) == y[test]).mean() > 0.85
    importance = model.feature_importance()
    if name.startswith("hist_gb"):
        assert importance is None
    else:
        assert importance.shape == (4,)
        assert importance[0] >= importance[2]


def test_predictors_are_deterministic_under_a_seed():
    X, signal = _linear_problem()
    a = make_predictor("random_forest_reg", {"n_estimators": 20}, seed=3).fit(X, signal).predict(X[:10])
    b = make_predictor("random_forest_reg", {"n_estimators": 20}, seed=3).fit(X, signal).predict(X[:10])
    np.testing.assert_array_equal(a, b)


def test_adapt_params_translates_canonical_names_per_model():
    assert adapt_params("ridge", CANONICAL) == {}
    hist = adapt_params("hist_gb_clf", CANONICAL)
    assert hist == {"max_iter": 60, "learning_rate": 0.1, "max_leaf_nodes": 7, "max_depth": 3, "min_samples_leaf": 10}
    forest = adapt_params("random_forest_reg", CANONICAL)
    assert forest == {"n_estimators": 60, "max_depth": 3, "min_samples_leaf": 10}
    assert adapt_params("lightgbm_reg", CANONICAL) == CANONICAL
