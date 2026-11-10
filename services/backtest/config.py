"""Run configuration: everything a backtest needs, serialisable to JSON for persistence."""

from __future__ import annotations

from datetime import date  # noqa: TC003
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from services.backtest.costs import ZERODHA_DELIVERY, CostPreset


class UniverseKind(StrEnum):
    INDEX = "index"
    LIST = "list"
    WATCHLIST = "watchlist"


class UniverseSpec(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: UniverseKind
    name: str = ""
    symbols: tuple[str, ...] = ()

    def label(self) -> str:
        if self.kind == UniverseKind.INDEX:
            return self.name
        if self.kind == UniverseKind.WATCHLIST:
            return "Watchlist"
        count = len(self.symbols)
        return f"{count} symbol{'' if count == 1 else 's'}"


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    strategy: str
    params: dict[str, Any]
    universe: UniverseSpec
    start: date
    end: date
    init_cash: float = 1_000_000.0
    costs: CostPreset = ZERODHA_DELIVERY
    slippage_bps: float = 5.0
    strategy_overrides: dict[str, Any] = Field(default_factory=dict)
    stake_amount: float | None = None
    adjust_splits: bool = True
    benchmark: str = "^NSEI"
    max_volume_share: float | None = None

    @field_validator("strategy_overrides", mode="before")
    @classmethod
    def _roi_keys_to_int(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _roi_from_json(k, v) for k, v in value.items()}
        return value

    @field_serializer("strategy_overrides")
    def _roi_keys_to_str(self, value: dict[str, Any]) -> dict[str, Any]:
        return {k: _roi_to_json(k, v) for k, v in value.items()}


def _roi_to_json(key: str, value: Any) -> Any:
    if key == "minimal_roi" and isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return value


def _roi_from_json(key: str, value: Any) -> Any:
    if key == "minimal_roi" and isinstance(value, dict):
        return {int(k): float(v) for k, v in value.items()}
    return value
