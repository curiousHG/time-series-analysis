"""Basket strategy contract.

A strategy sees one Date-indexed OHLCV frame per symbol, may inspect the basket once in `prepare_basket`,
and writes boolean entry/exit columns or a score column. A signal on row T is acted on at row T+1's open.
Risk attributes follow freqtrade; `signal` and `rank` are the two engine modes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import numpy as np
import pandas as pd

from strategies.parameters import CategoricalParameter, DecimalParameter, IntParameter, Parameter

if TYPE_CHECKING:
    from collections.abc import Callable

ENTER = "enter_long"
EXIT = "exit_long"
SCORE = "score"
ENTER_TAG = "enter_tag"
EXIT_TAG = "exit_tag"

Mode = Literal["signal", "rank"]

OVERRIDABLE = frozenset(
    {
        "stoploss",
        "trailing_stop",
        "trailing_stop_positive",
        "trailing_stop_positive_offset",
        "minimal_roi",
        "max_open_trades",
        "rebalance_every",
        "top_k",
        "exit_rank",
        "rank_weights",
        "use_exit_signal",
    }
)


@dataclass
class MarketContext:
    calendar: pd.DatetimeIndex
    universe: tuple[str, ...]
    benchmark: pd.DataFrame | None = None
    informative: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeSnapshot:
    """One open trade as `custom_exit` and `custom_stake_amount` see it, at the bar being priced."""

    symbol: str
    date: pd.Timestamp
    bars_held: int
    open_rate: float
    current_rate: float
    current_profit: float
    max_rate: float
    min_rate: float
    enter_tag: str | None = None


class BasketStrategy:
    name: ClassVar[str] = "Basket"
    mode: ClassVar[Mode] = "signal"
    informative_symbols: ClassVar[tuple[str, ...]] = ()

    stoploss: float = -0.10
    trailing_stop: bool = False
    trailing_stop_positive: float = 0.02
    trailing_stop_positive_offset: float = 0.0
    minimal_roi: ClassVar[dict[int, float]] = {}
    startup_candle_count: int = 50
    use_exit_signal: bool = True

    max_open_trades: int = 5
    rebalance_every: int | Literal["W", "M"] = "M"
    top_k: int = 10
    exit_rank: int | None = None
    rank_weights: Literal["equal", "score"] = "equal"

    def __init__(self, **params: Any) -> None:
        space = self.parameter_space()
        unknown = set(params) - set(space)
        if unknown:
            raise TypeError(f"{type(self).__name__} has no parameter(s) {sorted(unknown)}")
        for key, value in params.items():
            self.__dict__[key] = space[key].validate(value)
        self.minimal_roi = dict(self.minimal_roi)

    @classmethod
    def parameter_space(cls) -> dict[str, Parameter]:
        return {
            attr: value
            for klass in reversed(cls.__mro__)
            for attr, value in vars(klass).items()
            if isinstance(value, Parameter)
        }

    @classmethod
    def legacy_params(cls) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for attr, param in cls.parameter_space().items():
            if isinstance(param, (IntParameter, DecimalParameter)):
                out[attr] = {
                    "default": param.default,
                    "min": param.low,
                    "max": param.high,
                    "step": param.step,
                    "help": param.help,
                }
            elif isinstance(param, CategoricalParameter):
                out[attr] = {"default": param.default, "choices": list(param.choices), "help": param.help}
        return out

    def params_dict(self) -> dict[str, Any]:
        return {attr: getattr(self, attr) for attr in self.parameter_space()}

    def apply_overrides(self, overrides: dict[str, Any] | None) -> None:
        for key, value in (overrides or {}).items():
            if key not in OVERRIDABLE:
                raise ValueError(f"{key!r} is not an overridable strategy attribute")
            if key == "minimal_roi":
                value = {int(k): float(v) for k, v in dict(value).items()}
            setattr(self, key, value)

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        return df

    def prepare_basket(
        self, frames: dict[str, pd.DataFrame], progress_cb: Callable[..., None] | None = None
    ) -> dict[str, pd.DataFrame]:
        return frames

    def populate_entry(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        if overrides(type(self), "populate_entry_trend"):
            return self.populate_entry_trend(df, {"pair": symbol, "ctx": ctx})
        df[ENTER] = False
        return df

    def populate_exit(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        if overrides(type(self), "populate_exit_trend"):
            return self.populate_exit_trend(df, {"pair": symbol, "ctx": ctx})
        df[EXIT] = False
        return df

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """freqtrade's name for `populate_entry`; override either one."""
        dataframe[ENTER] = False
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """freqtrade's name for `populate_exit`; override either one."""
        dataframe[EXIT] = False
        return dataframe

    def populate_score(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        df[SCORE] = np.nan
        return df

    def custom_exit(self, trade: TradeSnapshot, ctx: MarketContext) -> str | bool | None:
        """Return an exit reason (or True) to close `trade` at the next open.

        Called for each open trade at every bar's close, independent of `use_exit_signal`.
        """
        return None

    def custom_stake_amount(
        self, symbol: str, proposed_stake: float, free_cash: float, portfolio_value: float
    ) -> float:
        """Resize an entry before it is placed. Return 0 to skip the trade."""
        return proposed_stake

    def diagnostics(self) -> Any | None:
        return None


def overrides(cls: type, hook: str) -> bool:
    """True when `cls` supplies its own `hook` rather than inheriting the no-op from the base."""
    return getattr(cls, hook, None) is not getattr(BasketStrategy, hook, None)


def finalize_signals(df: pd.DataFrame, *, mode: Mode) -> pd.DataFrame:
    for column in (ENTER, EXIT):
        if column not in df.columns:
            df[column] = False
        df[column] = df[column].fillna(False).astype(bool)
    for column in (ENTER_TAG, EXIT_TAG):
        if column not in df.columns:
            df[column] = None
    if SCORE not in df.columns:
        df[SCORE] = np.nan
    df[SCORE] = pd.to_numeric(df[SCORE], errors="coerce").astype(float)
    if mode == "rank" and df[SCORE].isna().all() and df[ENTER].any():
        df[SCORE] = df[ENTER].astype(float)
    return df


BASKET_STRATEGY_REGISTRY: dict[str, type[BasketStrategy]] = {}


def register_basket_strategy(cls: type[BasketStrategy] | None = None, *, registry: dict | None = None):
    target = BASKET_STRATEGY_REGISTRY if registry is None else registry

    def decorate(klass: type[BasketStrategy]) -> type[BasketStrategy]:
        if klass.name in target:
            raise ValueError(f"a basket strategy named {klass.name!r} is already registered")
        target[klass.name] = klass
        return klass

    return decorate(cls) if cls is not None else decorate
