"""File-based persistence for user selections (replaces flaky cookie layer)."""

import json
from pathlib import Path

SELECTIONS_PATH = Path("data/user/selections.json")


def _load_all() -> dict:
    if not SELECTIONS_PATH.exists():
        return {}
    try:
        return json.loads(SELECTIONS_PATH.read_text())
    except Exception:
        return {}


def _save_all(data: dict):
    SELECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTIONS_PATH.write_text(json.dumps(data, indent=2))


def load_selection(key: str, default=None):
    return _load_all().get(key, default)


def save_selection(key: str, value):
    data = _load_all()
    data[key] = value
    _save_all(data)
