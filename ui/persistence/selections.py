"""File-based persistence for user selections."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ui.constants import SELECTIONS_PATH


class Selections(BaseModel):
    """Every key stored in selections.json. Widget filter snapshots are open-ended string-keyed dicts."""

    model_config = ConfigDict(extra="ignore")

    selected_schemes: list[str] = Field(default_factory=list)
    selected_stocks: list[str] = Field(default_factory=list)
    default_schemes: list[str] = Field(default_factory=list)
    default_stocks: list[str] = Field(default_factory=list)
    mf_bookmarks: list[str] = Field(default_factory=list)
    backtest_selected_run: int | None = None
    screener_filters: dict[str, Any] = Field(default_factory=dict)
    stock_screener_filters: dict[str, Any] = Field(default_factory=dict)
    mf_analysis_filters: dict[str, Any] = Field(default_factory=dict)
    overview_index_filters: dict[str, Any] = Field(default_factory=dict)
    backtest_filters: dict[str, Any] = Field(default_factory=dict)


def _load_all() -> Selections:
    """The stored selections; a field that fails validation falls back to its default, the rest are kept."""
    try:
        raw = SELECTIONS_PATH.read_text()
    except OSError:
        return Selections()
    try:
        return Selections.model_validate_json(raw)
    except ValidationError as e:
        invalid = {err["loc"][0] for err in e.errors() if err["loc"]}
        try:
            stored = json.loads(raw)
        except ValueError:
            return Selections()
        if not isinstance(stored, dict):
            return Selections()
        return Selections.model_validate({k: v for k, v in stored.items() if k not in invalid})


def load_selection(key: str) -> Any:
    """Return the stored value for `key`, or its default when the file is missing or unreadable."""
    return getattr(_load_all(), key)


def save_selection(key: str, value: Any) -> None:
    if key not in Selections.model_fields:
        raise KeyError(key)
    updated = Selections.model_validate({**_load_all().model_dump(), key: value})
    SELECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTIONS_PATH.write_text(updated.model_dump_json(indent=2))
