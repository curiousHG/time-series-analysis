"""Small formatting helpers shared by the Settings sections."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


def fmt_date(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else "unknown"


def plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"
