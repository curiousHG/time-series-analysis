"""Unit tests for services.stock_fundamentals_service + symbol-form helpers."""

import polars as pl
import pytest

from services.stock_fundamentals_service import fundamentals_percentiles
from stocks.constants import to_bare_symbol, to_yf_symbol


def test_symbol_round_trip():
    assert to_yf_symbol("RELIANCE") == "RELIANCE.NS"
    assert to_yf_symbol("RELIANCE.NS") == "RELIANCE.NS"
    assert to_yf_symbol("^NSEI") == "^NSEI"
    assert to_bare_symbol("RELIANCE.NS") == "RELIANCE"
    assert to_bare_symbol(to_yf_symbol("TCS")) == "TCS"


def test_percentiles_exact_ranks_and_direction():
    df = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D", "E"],
            "roe": [5.0, 10.0, 15.0, 20.0, 25.0],  # higher is better → E = P100
            "stock_pe": [10.0, 20.0, 30.0, 40.0, 50.0],  # lower is better → A = P100
        }
    )
    pct = fundamentals_percentiles(df, {"roe": True, "stock_pe": False})
    rows = {r["symbol"]: r for r in pct.iter_rows(named=True)}
    assert rows["E"]["roe_pctile"] == pytest.approx(100.0)
    assert rows["A"]["roe_pctile"] == pytest.approx(0.0)
    assert rows["C"]["roe_pctile"] == pytest.approx(50.0)
    assert rows["A"]["stock_pe_pctile"] == pytest.approx(100.0)  # cheapest P/E ranks best
    assert rows["E"]["stock_pe_pctile"] == pytest.approx(0.0)


def test_percentiles_null_handling():
    df = pl.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "roe": [5.0, None, 15.0],
        }
    )
    pct = fundamentals_percentiles(df, {"roe": True})
    rows = {r["symbol"]: r for r in pct.iter_rows(named=True)}
    assert rows["B"]["roe_pctile"] is None
    assert rows["A"]["roe_pctile"] == pytest.approx(0.0)
    assert rows["C"]["roe_pctile"] == pytest.approx(100.0)


def test_percentiles_missing_column_skipped():
    df = pl.DataFrame({"symbol": ["A"], "roe": [5.0]})
    pct = fundamentals_percentiles(df, {"roe": True, "not_a_column": True})
    assert pct.columns == ["symbol", "roe_pctile"]


def test_percentiles_empty_universe():
    assert fundamentals_percentiles(pl.DataFrame()).is_empty()
