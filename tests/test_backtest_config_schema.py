from datetime import date

from services.backtest.config import BacktestConfig, UniverseKind, UniverseSpec
from services.backtest.costs import ZERODHA_INTRADAY
from services.backtest.optimizer import OptimizationSpec, ParamSpace

EXPECTED_JSON = {
    "strategy": "RSI Basket",
    "params": {"window": 14},
    "universe": {"kind": "list", "name": "", "symbols": ["TCS", "INFY"]},
    "start": "2020-01-01",
    "end": "2024-12-31",
    "init_cash": 500000.0,
    "costs": ZERODHA_INTRADAY.model_dump(),
    "slippage_bps": 8.0,
    "strategy_overrides": {"stoploss": -0.08, "minimal_roi": {"0": 0.1, "30": 0.02}},
    "stake_amount": None,
    "adjust_splits": True,
    "benchmark": "^NSEI",
    "max_volume_share": None,
}


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy="RSI Basket",
        params={"window": 14},
        universe=UniverseSpec(kind=UniverseKind.LIST, symbols=("TCS", "INFY")),
        start=date(2020, 1, 1),
        end=date(2024, 12, 31),
        init_cash=500_000,
        costs=ZERODHA_INTRADAY,
        slippage_bps=8,
        strategy_overrides={"stoploss": -0.08, "minimal_roi": {0: 0.1, 30: 0.02}},
    )


def test_config_dumps_to_the_stored_json_shape():
    assert _config().model_dump(mode="json") == EXPECTED_JSON


def test_config_validates_back_to_an_equal_config():
    restored = BacktestConfig.model_validate(EXPECTED_JSON)
    assert restored == _config()
    assert restored.strategy_overrides["minimal_roi"] == {0: 0.1, 30: 0.02}


def test_unknown_keys_in_old_payloads_are_ignored():
    payload = {**EXPECTED_JSON, "retired_field": 1, "universe": {**EXPECTED_JSON["universe"], "legacy": True}}
    assert BacktestConfig.model_validate(payload) == _config()


def test_optimization_spec_dump_keeps_the_stored_shape():
    spec = OptimizationSpec(space_overrides={"fast": ParamSpace(kind="int", low=1, high=5, step=1)}, timeout_s=9.0)
    dumped = spec.model_dump(mode="json")
    assert "timeout_s" not in dumped
    assert dumped["objective"] == "sharpe"
    assert dumped["space_overrides"]["fast"] == {"kind": "int", "low": 1.0, "high": 5.0, "step": 1.0, "choices": []}
