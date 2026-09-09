import numpy as np
import pandas as pd
import pytest

from strategies.basket import (
    ENTER,
    ENTER_TAG,
    EXIT,
    SCORE,
    BasketStrategy,
    MarketContext,
    finalize_signals,
    register_basket_strategy,
)
from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter


class _Momentum(BasketStrategy):
    name = "Test Momentum"
    mode = "rank"
    lookback = IntParameter(20, 5, 60, step=5)
    threshold = DecimalParameter(0.0, -0.1, 0.1, step=0.01, optimize=False)

    def populate_score(self, df, symbol, ctx):
        df[SCORE] = df["Close"].pct_change(self.lookback)
        return df


class _Child(_Momentum):
    name = "Test Child"
    lookback = IntParameter(10, 5, 60, step=5)
    model = CategoricalParameter("ridge", choices=("ridge",))


def _frame(n: int = 30) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series(np.linspace(100, 130, n), index=idx)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000}, index=idx)


def test_parameter_space_collects_across_the_mro_with_child_overrides():
    space = _Child.parameter_space()
    assert list(space) == ["lookback", "threshold", "model"]
    assert space["lookback"].default == 10


def test_instance_takes_declared_parameters_only():
    strat = _Momentum(lookback=40)
    assert strat.lookback == 40
    assert strat.threshold == 0.0
    assert strat.params_dict() == {"lookback": 40, "threshold": 0.0}
    with pytest.raises(TypeError):
        _Momentum(window=3)


def test_legacy_params_match_the_stock_page_widget_shape():
    legacy = _Momentum.legacy_params()
    assert legacy["lookback"] == {"default": 20, "min": 5, "max": 60, "step": 5, "help": ""}
    assert legacy["threshold"]["default"] == pytest.approx(0.0)


def test_overrides_only_touch_known_risk_and_basket_attributes():
    strat = _Momentum()
    strat.apply_overrides({"stoploss": -0.05, "top_k": 3, "minimal_roi": {0: 0.1}})
    assert strat.stoploss == -0.05
    assert strat.top_k == 3
    assert strat.minimal_roi == {0: 0.1}
    with pytest.raises(ValueError):
        strat.apply_overrides({"mode": "signal"})


def test_default_hooks_produce_empty_signals_and_scores():
    strat = _Momentum()
    ctx = MarketContext(calendar=pd.DatetimeIndex([]), universe=("A",), benchmark=None, informative={})
    df = strat.populate_entry(_frame(), "A", ctx)
    df = strat.populate_exit(df, "A", ctx)
    df = strat.populate_score(df, "A", ctx)
    assert not df[ENTER].any()
    assert not df[EXIT].any()
    assert df[SCORE].notna().sum() == 30 - 20


def test_finalize_signals_coerces_types_and_fills_missing_columns():
    df = _frame(5)
    df[ENTER] = [np.nan, 1, 0, np.nan, 1]
    out = finalize_signals(df, mode="signal")
    assert out[ENTER].dtype == bool
    assert out[ENTER].tolist() == [False, True, False, False, True]
    assert out[EXIT].dtype == bool and not out[EXIT].any()
    assert out[ENTER_TAG].isna().all()
    assert SCORE in out.columns and out[SCORE].isna().all()


def test_registry_keys_by_name_and_rejects_duplicates():
    registry: dict[str, type] = {}

    @register_basket_strategy(registry=registry)
    class One(BasketStrategy):
        name = "One"

    assert registry == {"One": One}
    with pytest.raises(ValueError):

        @register_basket_strategy(registry=registry)
        class Two(BasketStrategy):
            name = "One"
