import pytest

from services.backtest.costs import (
    NSE_TXN_PCT,
    PRESETS,
    TICK,
    ZERO,
    ZERODHA_DELIVERY,
    ZERODHA_INTRADAY,
    CostPreset,
    apply_costs,
    fill_price,
    round_tick,
)


def test_delivery_costs_match_the_hand_computed_example():
    buy = apply_costs("buy", 100, 1000.0, ZERODHA_DELIVERY)
    sell = apply_costs("sell", 100, 1000.0, ZERODHA_DELIVERY)
    assert buy.brokerage == 0
    assert buy.stt == pytest.approx(100.0)
    assert buy.exchange_txn == pytest.approx(100_000 * NSE_TXN_PCT)
    assert buy.sebi == pytest.approx(0.10)
    assert buy.stamp == pytest.approx(15.0)
    assert buy.gst == pytest.approx(0.18 * (buy.exchange_txn + buy.sebi))
    assert buy.dp == 0
    assert buy.total == pytest.approx(118.74, abs=0.01)
    assert sell.stamp == 0
    assert sell.dp == pytest.approx(15.34)
    assert sell.total == pytest.approx(119.08, abs=0.01)


def test_intraday_brokerage_is_capped_at_twenty_rupees():
    buy = apply_costs("buy", 100, 1000.0, ZERODHA_INTRADAY)
    assert buy.brokerage == pytest.approx(20.0)
    small = apply_costs("buy", 10, 100.0, ZERODHA_INTRADAY)
    assert small.brokerage == pytest.approx(0.30)
    sell = apply_costs("sell", 100, 1000.0, ZERODHA_INTRADAY)
    assert sell.stt == pytest.approx(25.0)
    assert buy.stt == 0
    assert buy.stamp == pytest.approx(3.0)


def test_etf_pays_stt_on_the_sell_side_only():
    buy = apply_costs("buy", 100, 1000.0, ZERODHA_DELIVERY, instrument="etf")
    sell = apply_costs("sell", 100, 1000.0, ZERODHA_DELIVERY, instrument="etf")
    assert buy.stt == 0
    assert sell.stt == pytest.approx(1.0)


def test_zero_preset_costs_nothing():
    assert apply_costs("buy", 100, 1000.0, ZERO).total == 0
    assert apply_costs("sell", 100, 1000.0, ZERO).total == 0


def test_tick_rounding_and_slippage_sides():
    assert round_tick(100.01, "buy") == pytest.approx(100.05)
    assert round_tick(100.04, "sell") == pytest.approx(100.0)
    assert TICK == 0.05
    assert fill_price(100.0, "buy", 10) == pytest.approx(100.10)
    assert fill_price(100.0, "sell", 10) == pytest.approx(99.90)
    assert fill_price(100.0, "buy", 0) == pytest.approx(100.0)


def test_presets_round_trip_through_dicts():
    for preset in PRESETS.values():
        assert CostPreset.from_dict(preset.to_dict()) == preset
    custom = CostPreset.from_dict({**ZERODHA_DELIVERY.to_dict(), "name": "custom", "brokerage_pct": 0.0005})
    assert custom.name == "custom"
    assert apply_costs("buy", 100, 1000.0, custom).brokerage == pytest.approx(50.0)
