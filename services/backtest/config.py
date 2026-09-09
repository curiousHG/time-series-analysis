"""Run configuration: everything a backtest needs, serialisable to JSON for persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

from services.backtest.costs import ZERODHA_DELIVERY, CostPreset

UniverseKind = Literal["index", "list", "watchlist"]


@dataclass(frozen=True)
class UniverseSpec:
    kind: UniverseKind
    name: str = ""
    symbols: tuple[str, ...] = ()

    def label(self) -> str:
        if self.kind == "index":
            return self.name
        if self.kind == "watchlist":
            return "Watchlist"
        count = len(self.symbols)
        return f"{count} symbol{'' if count == 1 else 's'}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "symbols": list(self.symbols)}

    @classmethod
    def from_dict(cls, payload: dict) -> UniverseSpec:
        return cls(kind=payload["kind"], name=payload.get("name", ""), symbols=tuple(payload.get("symbols", ())))


@dataclass
class BacktestConfig:
    strategy: str
    params: dict[str, Any]
    universe: UniverseSpec
    start: date
    end: date
    init_cash: float = 1_000_000.0
    costs: CostPreset = ZERODHA_DELIVERY
    slippage_bps: float = 5.0
    strategy_overrides: dict[str, Any] = field(default_factory=dict)
    stake_amount: float | None = None
    adjust_splits: bool = True
    benchmark: str = "^NSEI"
    max_volume_share: float | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["universe"] = self.universe.to_dict()
        payload["costs"] = self.costs.to_dict()
        payload["start"] = self.start.isoformat()
        payload["end"] = self.end.isoformat()
        payload["strategy_overrides"] = {k: _roi_to_json(k, v) for k, v in self.strategy_overrides.items()}
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> BacktestConfig:
        data = dict(payload)
        data["universe"] = UniverseSpec.from_dict(data["universe"])
        data["costs"] = CostPreset.from_dict(data["costs"])
        data["start"] = date.fromisoformat(data["start"])
        data["end"] = date.fromisoformat(data["end"])
        data["strategy_overrides"] = {k: _roi_from_json(k, v) for k, v in data.get("strategy_overrides", {}).items()}
        return cls(**data)


def _roi_to_json(key: str, value: Any) -> Any:
    if key == "minimal_roi" and isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return value


def _roi_from_json(key: str, value: Any) -> Any:
    if key == "minimal_roi" and isinstance(value, dict):
        return {int(k): float(v) for k, v in value.items()}
    return value
