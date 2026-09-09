from datetime import date

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERODHA_INTRADAY


def test_config_round_trips_through_json_safe_dicts():
    config = BacktestConfig(
        strategy="RSI Basket",
        params={"window": 14},
        universe=UniverseSpec(kind="index", name="NIFTY 50"),
        start=date(2020, 1, 1),
        end=date(2024, 12, 31),
        costs=ZERODHA_INTRADAY,
        slippage_bps=8,
        strategy_overrides={"stoploss": -0.08, "top_k": 5},
    )
    payload = config.to_dict()
    assert payload["start"] == "2020-01-01"
    assert payload["costs"]["name"] == "zerodha_intraday"
    assert payload["universe"] == {"kind": "index", "name": "NIFTY 50", "symbols": []}
    assert BacktestConfig.from_dict(payload) == config


def test_universe_labels_read_naturally():
    assert UniverseSpec(kind="index", name="NIFTY 50").label() == "NIFTY 50"
    assert UniverseSpec(kind="list", symbols=("TCS", "INFY")).label() == "2 symbols"
    assert UniverseSpec(kind="watchlist").label() == "Watchlist"


def test_defaults_are_delivery_costs_and_ten_lakh():
    config = BacktestConfig(
        strategy="x",
        params={},
        universe=UniverseSpec(kind="list", symbols=("TCS",)),
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
    )
    assert config.init_cash == 1_000_000
    assert config.costs.name == "zerodha_delivery"
    assert config.slippage_bps == 5.0
    assert config.adjust_splits is True
    assert config.benchmark == "^NSEI"
