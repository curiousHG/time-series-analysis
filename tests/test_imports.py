"""Import smoke: every app module imports cleanly, so a broken import after a file move or rename
fails immediately with the module name in the test id. No database, no network."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_PACKAGES = ("ui", "services", "data", "core", "stocks", "mutual_funds", "indicators", "strategies")

RUNS_STREAMLIT_AT_IMPORT = frozenset({"ui.app"})


def _module_names() -> list[str]:
    names: list[str] = []
    for pkg in APP_PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            parts = path.relative_to(ROOT).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            name = ".".join(parts)
            if name in RUNS_STREAMLIT_AT_IMPORT:
                continue
            if parts[:2] == ("ui", "views") and parts[-1] == "page":
                continue
            names.append(name)
    return names


@pytest.mark.parametrize("module_name", _module_names())
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
