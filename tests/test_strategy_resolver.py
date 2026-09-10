"""File-based strategy discovery: what a dropped-in file does, and what a broken one does not do."""

from __future__ import annotations

import pytest

from strategies import resolver
from strategies.basket import BasketStrategy

SIGNAL_FILE = """
from strategies.basket import ENTER, BasketStrategy
from strategies.parameters import IntParameter


class DroppedIn(BasketStrategy):
    name = "Dropped In"
    mode = "signal"
    window = IntParameter(9, 2, 40)

    def populate_entry(self, df, symbol, ctx):
        df[ENTER] = df["Close"] > df["Close"].shift(self.window)
        return df
"""

UNNAMED_FILE = """
from strategies.basket import BasketStrategy


class NoDisplayName(BasketStrategy):
    mode = "rank"
"""

BROKEN_FILE = "import a_module_that_is_not_installed\n"

FREQTRADE_SHAPED = """
from strategies.basket import ENTER, EXIT, BasketStrategy


class FreqShaped(BasketStrategy):
    name = "Freq Shaped"

    def populate_entry_trend(self, dataframe, metadata):
        dataframe[ENTER] = dataframe["Close"] > 0
        dataframe["seen_pair"] = metadata["pair"]
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe[EXIT] = False
        return dataframe
"""


@pytest.fixture
def strategy_dir(tmp_path, monkeypatch):
    directory = tmp_path / "strategies"
    directory.mkdir()
    monkeypatch.setenv(resolver.STRATEGY_DIR_ENV, str(directory))
    resolver.reset_cache()
    yield directory
    resolver.reset_cache()


def test_a_file_dropped_in_the_directory_becomes_a_strategy(strategy_dir):
    (strategy_dir / "dropped_in.py").write_text(SIGNAL_FILE)
    found = resolver.discover(refresh=True)
    assert "Dropped In" in found.strategies
    record = found.strategies["Dropped In"]
    assert record.origin == "user"
    assert record.source.endswith("dropped_in.py")
    assert issubclass(record.cls, BasketStrategy)
    assert record.cls.parameter_space()["window"].default == 9


def test_no_registration_decorator_is_needed(strategy_dir):
    (strategy_dir / "dropped_in.py").write_text(SIGNAL_FILE)
    assert "@register" not in SIGNAL_FILE
    assert resolver.resolve("Dropped In", refresh=True).mode == "signal"


def test_a_class_without_a_display_name_falls_back_to_the_class_name(strategy_dir):
    (strategy_dir / "unnamed.py").write_text(UNNAMED_FILE)
    assert "NoDisplayName" in resolver.strategy_registry(refresh=True)


def test_a_broken_file_is_reported_and_the_others_still_load(strategy_dir):
    (strategy_dir / "broken.py").write_text(BROKEN_FILE)
    (strategy_dir / "dropped_in.py").write_text(SIGNAL_FILE)
    found = resolver.discover(refresh=True)
    assert "Dropped In" in found.strategies
    assert [record.source.split("/")[-1] for record in found.errors] == ["broken.py"]
    assert "a_module_that_is_not_installed" in found.errors[0].error


def test_editing_a_file_is_picked_up_without_a_restart(strategy_dir):
    path = strategy_dir / "dropped_in.py"
    path.write_text(SIGNAL_FILE)
    assert resolver.strategy_registry()["Dropped In"].parameter_space()["window"].default == 9

    path.write_text(SIGNAL_FILE.replace("IntParameter(9, 2, 40)", "IntParameter(21, 2, 40)"))
    assert resolver.strategy_registry()["Dropped In"].parameter_space()["window"].default == 21


def test_built_ins_are_listed_alongside_user_files(strategy_dir):
    (strategy_dir / "dropped_in.py").write_text(SIGNAL_FILE)
    found = resolver.discover(refresh=True)
    assert found.strategies["RSI Basket"].origin == "builtin"
    assert found.strategies["Dropped In"].origin == "user"
    assert list(found.strategies) == sorted(found.strategies)


def test_a_user_file_shadows_a_built_in_of_the_same_name(strategy_dir):
    (strategy_dir / "shadow.py").write_text(SIGNAL_FILE.replace('"Dropped In"', '"RSI Basket"'))
    found = resolver.discover(refresh=True)
    assert found.strategies["RSI Basket"].origin == "user"
    assert any("shadows" in message for message in found.warnings)


def test_files_starting_with_an_underscore_are_left_alone(strategy_dir):
    (strategy_dir / "_helpers.py").write_text(BROKEN_FILE)
    found = resolver.discover(refresh=True)
    assert found.errors == []


def test_resolving_an_unknown_name_raises(strategy_dir):
    with pytest.raises(KeyError, match="Nope"):
        resolver.resolve("Nope")


def test_the_freqtrade_hook_names_are_accepted(strategy_dir):
    import pandas as pd

    (strategy_dir / "freq_shaped.py").write_text(FREQTRADE_SHAPED)
    cls = resolver.resolve("Freq Shaped", refresh=True)
    frame = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
    populated = cls().populate_entry(frame, "TCS", None)
    assert populated["enter_long"].all()
    assert populated["seen_pair"].iloc[0] == "TCS"


def test_the_template_scaffolds_a_working_strategy(strategy_dir):
    path = resolver.new_strategy_file("MyIdea", display_name="My Idea", directory=strategy_dir)
    assert path.name == "MyIdea.py"
    assert resolver.resolve("My Idea", refresh=True).mode == "signal"

    rank_path = resolver.new_strategy_file("MyRanker", mode="rank", directory=strategy_dir)
    assert "populate_score" in rank_path.read_text()
    assert resolver.resolve("MyRanker", refresh=True).mode == "rank"


def test_the_template_refuses_to_overwrite_and_rejects_bad_input(strategy_dir):
    resolver.new_strategy_file("MyIdea", directory=strategy_dir)
    with pytest.raises(FileExistsError):
        resolver.new_strategy_file("MyIdea", directory=strategy_dir)
    with pytest.raises(ValueError, match="class name"):
        resolver.render_template("not a class")
    with pytest.raises(ValueError, match="mode"):
        resolver.render_template("Fine", mode="sideways")


def test_discovery_survives_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(resolver.STRATEGY_DIR_ENV, str(tmp_path / "absent"))
    resolver.reset_cache()
    found = resolver.discover(refresh=True)
    assert "RSI Basket" in found.strategies
    assert found.errors == []
    resolver.reset_cache()
