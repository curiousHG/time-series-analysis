"""selections.json persistence: a bad field must not wipe the other saved selections."""

from __future__ import annotations

import json

import pytest

from ui.persistence import selections


@pytest.fixture
def path(tmp_path, monkeypatch):
    target = tmp_path / "selections.json"
    monkeypatch.setattr(selections, "SELECTIONS_PATH", target)
    return target


def test_missing_file_gives_defaults(path):
    assert selections.load_selection("selected_stocks") == []


def test_one_invalid_field_keeps_the_rest(path):
    path.write_text(json.dumps({"selected_stocks": ["INFY"], "backtest_selected_run": "not-an-id"}))

    assert selections.load_selection("selected_stocks") == ["INFY"]
    assert selections.load_selection("backtest_selected_run") is None


def test_save_after_an_invalid_field_preserves_valid_ones(path):
    path.write_text(json.dumps({"selected_stocks": ["INFY"], "mf_bookmarks": 5}))

    selections.save_selection("backtest_selected_run", 7)

    stored = json.loads(path.read_text())
    assert stored["selected_stocks"] == ["INFY"]
    assert stored["backtest_selected_run"] == 7


def test_corrupt_file_gives_defaults(path):
    path.write_text("{not json")
    assert selections.load_selection("mf_bookmarks") == []


def test_unknown_key_is_rejected(path):
    with pytest.raises(KeyError):
        selections.save_selection("no_such_key", 1)
