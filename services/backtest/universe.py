"""Universe resolution and DB-first input loading for a basket backtest.

`load_backtest_inputs` builds everything the engine needs: an NSE calendar with warm-up bars
before `config.start`, one Date-indexed OHLCV frame per symbol reindexed to that calendar (NaN
for missing bars), the benchmark and informative frames, and each symbol's instrument kind for
the cost model. Data comes from the repositories only; the slow per-symbol gap fill is reported
through `progress_cb` as the "Loading data" phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from data.repositories.stock_ohlcv import (
    ensure_stock_data,
    load_instrument_kinds,
    load_stock_ohlcv_many,
    nse_trading_days,
)
from services.market_data_service import index_constituents
from services.price_adjust import adjust_ohlc_splits
from stocks.constants import NIFTY_50, to_bare_symbol

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    import polars as pl

    from services.backtest.config import BacktestConfig, UniverseSpec
    from strategies.basket import BasketStrategy

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
MIN_BARS_BEYOND_STARTUP = 5
CALENDAR_DAYS_PER_BAR = 2
CALENDAR_PAD_DAYS = 30


@dataclass
class BasketInputs:
    calendar: pd.DatetimeIndex
    frames: dict[str, pd.DataFrame]
    start_pos: int
    benchmark: pd.DataFrame | None = None
    informative: dict[str, pd.DataFrame] = field(default_factory=dict)
    instrument_kinds: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_universe(spec: UniverseSpec, watchlist: list[str] | None = None) -> list[str]:
    """Bare NSE symbols for a universe spec, deduplicated in order: index constituents (with the
    seeded Nifty 50 list as fallback), the caller's watchlist, or a pasted list."""
    if spec.kind == "index":
        symbols = list(index_constituents(spec.name))
        if not symbols and spec.name.strip().upper() == "NIFTY 50":
            symbols = list(NIFTY_50)
    elif spec.kind == "list":
        symbols = list(spec.symbols)
    else:
        symbols = list(watchlist or [])
    cleaned = (to_bare_symbol(s.strip().upper()) for s in symbols if s and s.strip())
    return list(dict.fromkeys(s for s in cleaned if s))


def startup_bars(strategy_cls: type[BasketStrategy], config: BacktestConfig) -> int:
    """Warm-up bars the configured strategy needs — read from a parametrised instance because
    strategies may derive it from their parameters."""
    strategy = strategy_cls(**config.params)
    strategy.apply_overrides(config.strategy_overrides)
    return int(strategy.startup_candle_count)


def load_backtest_inputs(
    config: BacktestConfig,
    strategy_cls: type[BasketStrategy],
    *,
    watchlist: list[str] | None = None,
    progress_cb: Callable[..., None] | None = None,
) -> BasketInputs:
    warnings: list[str] = []
    symbols = resolve_universe(config.universe, watchlist)
    startup = startup_bars(strategy_cls, config)
    calendar, start_pos, warm_up_warning = _calendar_with_warm_up(config.start, config.end, startup)
    if warm_up_warning:
        warnings.append(warm_up_warning)
    first_day, last_day = calendar[0].date(), calendar[-1].date()

    failed = _ensure_symbols(symbols, first_day, last_day, warnings, progress_cb)
    loaded = load_stock_ohlcv_many([s for s in symbols if s not in failed], first_day, last_day)
    frames, skipped = _build_frames(symbols, loaded, calendar, startup, adjust=config.adjust_splits)

    benchmark = _load_reference_frame(config.benchmark, first_day, last_day, calendar, warnings)
    informative = {}
    for symbol in strategy_cls.informative_symbols:
        frame = _load_reference_frame(symbol, first_day, last_day, calendar, warnings)
        if frame is not None:
            informative[symbol] = frame

    if config.universe.kind == "index":
        warnings.append(
            f"Universe is today's {config.universe.name} constituent list — results carry survivorship bias."
        )
    if skipped:
        logger.info("backtest inputs: %d symbol(s) skipped for insufficient history", len(skipped))
    return BasketInputs(
        calendar=calendar,
        frames=frames,
        start_pos=start_pos,
        benchmark=benchmark,
        informative=informative,
        instrument_kinds=load_instrument_kinds(list(frames)),
        skipped=skipped,
        warnings=warnings,
    )


def _calendar_with_warm_up(start: date, end: date, startup: int) -> tuple[pd.DatetimeIndex, int, str | None]:
    """NSE trading days from `startup` bars before `start` through `end`, with the position of the
    first trading bar. When the calendar cannot supply the warm-up, trading starts at the first
    bar that has it and a warning names the effective start date."""
    window = timedelta(days=startup * CALENDAR_DAYS_PER_BAR + CALENDAR_PAD_DAYS)
    days = nse_trading_days(start - window, end)
    first_live = next((i for i, d in enumerate(days) if d >= start), None)
    if first_live is None:
        raise ValueError(f"no NSE trading days between {start} and {end}")
    warm_start = first_live - startup
    if warm_start >= 0:
        days = days[warm_start:]
        return pd.DatetimeIndex(pd.to_datetime(days)), startup, None
    if startup >= len(days):
        raise ValueError(f"only {len(days)} trading day(s) available, strategy needs {startup} warm-up bars")
    effective = days[startup]
    warning = (
        f"Only {first_live} warm-up bar(s) available before {start} (strategy needs {startup}); "
        f"trading starts {effective.isoformat()}."
    )
    return pd.DatetimeIndex(pd.to_datetime(days)), startup, warning


def _ensure_symbols(symbols, first_day, last_day, warnings: list[str], progress_cb) -> set[str]:
    """Run the DB-first gap fill per symbol; returns the symbols whose load raised."""
    failed: set[str] = set()
    total = len(symbols)
    for i, symbol in enumerate(symbols):
        _report(progress_cb, phase="Loading data", done=i, total=total, message=f"Loading {symbol}")
        try:
            ensure_stock_data(symbol, first_day, last_day)
        except Exception as exc:
            logger.warning("backtest inputs: %s failed to load: %s", symbol, exc)
            warnings.append(f"{symbol}: data load failed ({exc})")
            failed.add(symbol)
    _report(progress_cb, phase="Loading data", done=total, total=total)
    return failed


def _build_frames(symbols, loaded: pl.DataFrame, calendar, startup: int, *, adjust: bool):
    parts = {}
    if loaded.height:
        for key, part in loaded.partition_by("Symbol", as_dict=True).items():
            parts[key[0] if isinstance(key, tuple) else key] = part
    frames: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    needed = startup + MIN_BARS_BEYOND_STARTUP
    for symbol in symbols:
        part = parts.get(symbol)
        frame = _to_calendar_frame(part, calendar, adjust=adjust) if part is not None else None
        if frame is None or frame["Close"].notna().sum() < needed:
            skipped.append(symbol)
            continue
        frames[symbol] = frame
    return frames, skipped


def _to_calendar_frame(part: pl.DataFrame, calendar: pd.DatetimeIndex, *, adjust: bool) -> pd.DataFrame | None:
    if part.height == 0:
        return None
    frame = part.select(["Date", *OHLCV_COLUMNS]).to_pandas()
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame = frame.set_index("Date").astype(float).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if adjust:
        frame = adjust_ohlc_splits(frame)
    return frame.reindex(calendar)


def _load_reference_frame(symbol, first_day, last_day, calendar, warnings: list[str]) -> pd.DataFrame | None:
    try:
        raw = ensure_stock_data(symbol, first_day, last_day)
    except Exception as exc:
        logger.warning("backtest inputs: reference %s failed to load: %s", symbol, exc)
        warnings.append(f"{symbol}: reference data load failed ({exc})")
        return None
    if raw is None or raw.height == 0:
        warnings.append(f"{symbol}: no reference data in range")
        return None
    return _to_calendar_frame(raw, calendar, adjust=False)


def _report(progress_cb: Callable[..., None] | None, **fields: Any) -> None:
    if progress_cb is not None:
        progress_cb(**fields)
