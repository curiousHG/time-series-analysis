"""Unit tests for the MF screener's WYSIWYG top-N backfill picker."""

import polars as pl

from ui.views.mf_screener.backfill import pick_backfill_names


def _frame(names: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"scheme_name": names})


def test_picks_displayed_order_not_frame_order():
    filtered = _frame(["A", "B", "C", "D"])
    displayed = ["D", "C", "B", "A"]  # user re-sorted the grid
    assert pick_backfill_names(filtered, displayed, 2) == ["D", "C"]


def test_stale_displayed_names_are_dropped():
    filtered = _frame(["A", "B"])  # sidebar filters changed since the grid rendered
    displayed = ["Z", "B", "Y", "A"]
    assert pick_backfill_names(filtered, displayed, 3) == ["B", "A"]


def test_falls_back_to_frame_order_without_grid_state():
    filtered = _frame(["A", "B", "C"])
    assert pick_backfill_names(filtered, None, 2) == ["A", "B"]
    assert pick_backfill_names(filtered, [], 2) == ["A", "B"]


def test_fully_stale_display_falls_back():
    filtered = _frame(["A", "B"])
    assert pick_backfill_names(filtered, ["X", "Y"], 2) == ["A", "B"]
