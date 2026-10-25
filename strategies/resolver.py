"""File-based strategy discovery, in the shape freqtrade uses.

Built-in strategies register on import; user strategies are `.py` files in `user_data/strategies/`, rescanned when
a file's size or mtime changes. A user strategy that shadows a built-in wins and records a warning; a file that
fails to import is recorded as a load error.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from strategies.basket import BASKET_STRATEGY_REGISTRY, BasketStrategy

logger = logging.getLogger(__name__)

STRATEGY_DIR_ENV = "BT_STRATEGY_DIR"
DEFAULT_STRATEGY_DIR = Path(__file__).resolve().parent.parent / "user_data" / "strategies"
MODULE_PREFIX = "bt_user_strategies"
Origin = Literal["builtin", "user"]


@dataclass(frozen=True)
class StrategyRecord:
    name: str
    cls: type[BasketStrategy] | None
    origin: Origin
    source: str
    error: str | None = None

    @property
    def loaded(self) -> bool:
        return self.cls is not None


@dataclass
class Discovery:
    strategies: dict[str, StrategyRecord] = field(default_factory=dict)
    errors: list[StrategyRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signature: tuple = ()


_cache: Discovery | None = None


def user_strategy_dir() -> Path:
    override = os.environ.get(STRATEGY_DIR_ENV)
    return Path(override).expanduser() if override else DEFAULT_STRATEGY_DIR


def _directory_signature(directory: Path) -> tuple:
    if not directory.is_dir():
        return ()
    entries = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        stat = path.stat()
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def _module_name(path: Path) -> str:
    return f"{MODULE_PREFIX}.{path.stem}"


def _is_concrete(obj) -> bool:
    """A class defined in the scanned module that can actually be instantiated as a strategy."""
    return (
        inspect.isclass(obj)
        and issubclass(obj, BasketStrategy)
        and obj is not BasketStrategy
        and not inspect.isabstract(obj)
        and not getattr(obj, "abstract", False)
    )


def strategy_name(cls: type[BasketStrategy]) -> str:
    """The declared display name, falling back to the class name when the class did not set one."""
    declared = cls.__dict__.get("name")
    if isinstance(declared, str) and declared:
        return declared
    inherited = getattr(cls, "name", "")
    return inherited if inherited and inherited != BasketStrategy.name else cls.__name__


def _import_file(path: Path) -> tuple[list[type[BasketStrategy]], str | None]:
    name = _module_name(path)
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return [], f"{path.name} is not an importable module"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        return [], traceback.format_exc(limit=6)
    found = [obj for obj in vars(module).values() if _is_concrete(obj) and obj.__module__ == name]
    return found, None


def _scan_user_directory(directory: Path) -> Discovery:
    found = Discovery(signature=_directory_signature(directory))
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        classes, error = _import_file(path)
        if error is not None:
            logger.warning("strategy file %s failed to import", path.name)
            found.errors.append(StrategyRecord(name=path.stem, cls=None, origin="user", source=str(path), error=error))
            continue
        if not classes:
            found.warnings.append(f"{path.name} defines no BasketStrategy subclass")
            continue
        for cls in classes:
            name = strategy_name(cls)
            if name in found.strategies:
                found.warnings.append(f"{name!r} is defined more than once; {path.name} was ignored")
                continue
            found.strategies[name] = StrategyRecord(name=name, cls=cls, origin="user", source=str(path))
    return found


def _builtin_records() -> dict[str, StrategyRecord]:
    records: dict[str, StrategyRecord] = {}
    for name, cls in BASKET_STRATEGY_REGISTRY.items():
        module = sys.modules.get(cls.__module__)
        source = getattr(module, "__file__", cls.__module__) or cls.__module__
        records[name] = StrategyRecord(name=name, cls=cls, origin="builtin", source=source)
    return records


def discover(*, refresh: bool = False) -> Discovery:
    """Every strategy the app can run, built-in plus whatever is in the user directory."""
    global _cache
    directory = user_strategy_dir()
    signature = _directory_signature(directory)
    if _cache is not None and not refresh and _cache.signature == signature:
        return _cache
    scanned = _scan_user_directory(directory)
    merged = _builtin_records()
    warnings = list(scanned.warnings)
    for name, record in scanned.strategies.items():
        if name in merged:
            warnings.append(f"user strategy {name!r} shadows the built-in of the same name")
        merged[name] = record
    _cache = Discovery(
        strategies=dict(sorted(merged.items())), errors=scanned.errors, warnings=warnings, signature=signature
    )
    return _cache


def strategy_registry(*, refresh: bool = False) -> dict[str, type[BasketStrategy]]:
    return {name: record.cls for name, record in discover(refresh=refresh).strategies.items() if record.cls is not None}


def resolve(name: str, *, refresh: bool = False) -> type[BasketStrategy]:
    registry = strategy_registry(refresh=refresh)
    if name not in registry:
        registry = strategy_registry(refresh=True)
    if name not in registry:
        raise KeyError(f"no basket strategy named {name!r}")
    return registry[name]


def load_errors(*, refresh: bool = False) -> list[StrategyRecord]:
    return list(discover(refresh=refresh).errors)


def reset_cache() -> None:
    global _cache
    _cache = None


TEMPLATE = '''"""{class_name}: describe the idea in a sentence.

Drop this file in {directory} and it appears in the Backtest Lab strategy picker.
"""

from __future__ import annotations

import talib

from strategies.basket import ENTER, EXIT, SCORE, BasketStrategy
from strategies.parameters import DecimalParameter, IntParameter


class {class_name}(BasketStrategy):
    name = "{display_name}"
    mode = "{mode}"

    stoploss = -0.10
    trailing_stop = False
    minimal_roi = {{}}
    max_open_trades = 5
{mode_attributes}
    fast = IntParameter(20, 5, 60, help="Fast moving-average window")
    slow = IntParameter(50, 20, 200, step=5, help="Slow moving-average window")
    threshold = DecimalParameter(0.0, -0.05, 0.05, step=0.005, optimize=False, help="Entry cushion")

    @property
    def startup_candle_count(self) -> int:
        return self.slow + 10

    def populate_indicators(self, df, symbol, ctx):
        close = df["Close"].ffill().to_numpy(dtype=float)
        df["ma_fast"] = talib.SMA(close, timeperiod=self.fast)
        df["ma_slow"] = talib.SMA(close, timeperiod=self.slow)
        return df

{body}'''

_SIGNAL_BODY = """    def populate_entry(self, df, symbol, ctx):
        df[ENTER] = (df["ma_fast"] > df["ma_slow"] * (1 + self.threshold)).fillna(False)
        return df

    def populate_exit(self, df, symbol, ctx):
        df[EXIT] = (df["ma_fast"] < df["ma_slow"]).fillna(False)
        return df
"""

_RANK_BODY = """    def populate_score(self, df, symbol, ctx):
        df[SCORE] = df["ma_fast"] / df["ma_slow"] - 1
        return df
"""

_RANK_ATTRIBUTES = '    top_k = 10\n    exit_rank = 15\n    rebalance_every = "M"\n'


def render_template(class_name: str, *, display_name: str | None = None, mode: str = "signal") -> str:
    if not class_name.isidentifier():
        raise ValueError(f"{class_name!r} is not a valid Python class name")
    if mode not in ("signal", "rank"):
        raise ValueError(f"mode must be 'signal' or 'rank', not {mode!r}")
    return TEMPLATE.format(
        class_name=class_name,
        display_name=display_name or class_name,
        mode=mode,
        directory=user_strategy_dir(),
        mode_attributes=_RANK_ATTRIBUTES if mode == "rank" else "",
        body=_RANK_BODY if mode == "rank" else _SIGNAL_BODY,
    )


def new_strategy_file(
    class_name: str, *, display_name: str | None = None, mode: str = "signal", directory: Path | None = None
) -> Path:
    """Write a starter strategy and return its path. Refuses to overwrite an existing file."""
    target_dir = Path(directory) if directory is not None else user_strategy_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{class_name}.py"
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    path.write_text(render_template(class_name, display_name=display_name, mode=mode), encoding="utf-8")
    reset_cache()
    return path
