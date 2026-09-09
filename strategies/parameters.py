"""Declared strategy parameters.

A parameter is a descriptor: on the class it is the declaration (bounds, default, whether the
optimiser may tune it); on an instance it is the current value. `BasketStrategy.parameter_space`
collects the declarations so the run form and the optimiser can read them without introspection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class Parameter:
    default: Any
    optimize: bool = True
    help: str = ""
    name: str = field(default="", compare=False)
    kind: ClassVar[str] = "base"

    def __set_name__(self, owner: type, name: str) -> None:
        object.__setattr__(self, "name", name)

    def __get__(self, obj: object | None, objtype: type | None = None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name, self.default)

    def grid(self) -> list:
        return [self.default]

    def validate(self, value: Any) -> Any:
        return value


@dataclass(frozen=True)
class IntParameter(Parameter):
    low: int = 0
    high: int = 0
    step: int = 1
    kind: ClassVar[str] = "int"

    def __init__(self, default: int, low: int, high: int, step: int = 1, *, optimize: bool = True, help: str = ""):
        if not low <= default <= high:
            raise ValueError(f"default {default} outside [{low}, {high}]")
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "optimize", optimize)
        object.__setattr__(self, "help", help)
        object.__setattr__(self, "name", "")

    def grid(self) -> list[int]:
        return list(range(self.low, self.high + 1, self.step))


@dataclass(frozen=True)
class DecimalParameter(Parameter):
    low: float = 0.0
    high: float = 0.0
    step: float = 0.01
    kind: ClassVar[str] = "decimal"

    def __init__(
        self, default: float, low: float, high: float, step: float = 0.01, *, optimize: bool = True, help: str = ""
    ):
        if not low <= default <= high:
            raise ValueError(f"default {default} outside [{low}, {high}]")
        object.__setattr__(self, "default", float(default))
        object.__setattr__(self, "low", float(low))
        object.__setattr__(self, "high", float(high))
        object.__setattr__(self, "step", float(step))
        object.__setattr__(self, "optimize", optimize)
        object.__setattr__(self, "help", help)
        object.__setattr__(self, "name", "")

    @property
    def decimals(self) -> int:
        text = f"{self.step:.10f}".rstrip("0")
        return len(text.split(".")[1]) if "." in text else 0

    def grid(self) -> list[float]:
        count = int(round((self.high - self.low) / self.step))
        return [round(self.low + i * self.step, self.decimals) for i in range(count + 1)]


@dataclass(frozen=True)
class CategoricalParameter(Parameter):
    choices: tuple = ()
    kind: ClassVar[str] = "categorical"

    def __init__(self, default: Any, choices, *, optimize: bool = True, help: str = ""):
        choices = tuple(choices)
        if default not in choices:
            raise ValueError(f"default {default!r} not in choices {choices!r}")
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "choices", choices)
        object.__setattr__(self, "optimize", optimize)
        object.__setattr__(self, "help", help)
        object.__setattr__(self, "name", "")

    def grid(self) -> list:
        return list(self.choices)


@dataclass(frozen=True)
class BoolParameter(CategoricalParameter):
    kind: ClassVar[str] = "bool"

    def __init__(self, default: bool, *, optimize: bool = True, help: str = ""):
        super().__init__(bool(default), (False, True), optimize=optimize, help=help)
