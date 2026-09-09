"""Trading costs for NSE equities and ETFs, modelled per order.

Rates follow zerodha.com/charges (checked 2026-09-09). Percentages are fractions of turnover.
GST applies to brokerage, exchange transaction charges and the SEBI fee. The DP charge is levied
per scrip per sell day, which equals per sell order for a daily-bar engine.

NSE_TXN_PCT: the charges page shows 0.00307%; earlier notes in this repo said 0.00297%. The page
value is the default until confirmed against a contract note.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

Side = Literal["buy", "sell"]
Instrument = Literal["equity", "etf"]

TICK = 0.05
NSE_TXN_PCT = 0.0000307
SEBI_PCT = 0.000001
GST_PCT = 0.18


@dataclass(frozen=True)
class CostPreset:
    name: str
    brokerage_pct: float = 0.0
    brokerage_cap: float | None = None
    stt_buy_pct: float = 0.0
    stt_sell_pct: float = 0.0
    etf_stt_sell_pct: float = 0.0
    exchange_txn_pct: float = 0.0
    sebi_pct: float = 0.0
    stamp_buy_pct: float = 0.0
    gst_pct: float = GST_PCT
    dp_charge_per_sell: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> CostPreset:
        return cls(**payload)


ZERODHA_DELIVERY = CostPreset(
    name="zerodha_delivery",
    stt_buy_pct=0.001,
    stt_sell_pct=0.001,
    etf_stt_sell_pct=0.00001,
    exchange_txn_pct=NSE_TXN_PCT,
    sebi_pct=SEBI_PCT,
    stamp_buy_pct=0.00015,
    dp_charge_per_sell=15.34,
)
ZERODHA_INTRADAY = CostPreset(
    name="zerodha_intraday",
    brokerage_pct=0.0003,
    brokerage_cap=20.0,
    stt_sell_pct=0.00025,
    etf_stt_sell_pct=0.00001,
    exchange_txn_pct=NSE_TXN_PCT,
    sebi_pct=SEBI_PCT,
    stamp_buy_pct=0.00003,
)
ZERO = CostPreset(name="zero", gst_pct=0.0)

PRESETS: dict[str, CostPreset] = {p.name: p for p in (ZERODHA_DELIVERY, ZERODHA_INTRADAY, ZERO)}
PRESET_LABELS = {"zerodha_delivery": "Zerodha delivery", "zerodha_intraday": "Zerodha intraday", "zero": "No costs"}


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    dp: float = 0.0

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst + self.dp

    def as_dict(self) -> dict[str, float]:
        return {**asdict(self), "total": self.total}


def apply_costs(
    side: Side, shares: int, price: float, preset: CostPreset, *, instrument: Instrument = "equity"
) -> CostBreakdown:
    turnover = shares * price
    if turnover <= 0:
        return CostBreakdown()
    brokerage = turnover * preset.brokerage_pct
    if preset.brokerage_cap is not None:
        brokerage = min(brokerage, preset.brokerage_cap)
    if instrument == "etf":
        stt = turnover * preset.etf_stt_sell_pct if side == "sell" else 0.0
    else:
        stt = turnover * (preset.stt_buy_pct if side == "buy" else preset.stt_sell_pct)
    exchange_txn = turnover * preset.exchange_txn_pct
    sebi = turnover * preset.sebi_pct
    stamp = turnover * preset.stamp_buy_pct if side == "buy" else 0.0
    gst = preset.gst_pct * (brokerage + exchange_txn + sebi)
    dp = preset.dp_charge_per_sell if side == "sell" else 0.0
    return CostBreakdown(brokerage, stt, exchange_txn, sebi, stamp, gst, dp)


def round_tick(price: float, side: Side) -> float:
    ticks = price / TICK
    rounded = math.ceil(ticks - 1e-9) if side == "buy" else math.floor(ticks + 1e-9)
    return round(rounded * TICK, 2)


def fill_price(raw: float, side: Side, slippage_bps: float) -> float:
    slipped = raw * (1 + slippage_bps / 1e4) if side == "buy" else raw * (1 - slippage_bps / 1e4)
    return round_tick(slipped, side)
