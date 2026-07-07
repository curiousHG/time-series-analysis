"""International-stock support: yfinance fundamentals mapping, global search normalisation, and
CAPM benchmark routing by exchange. All yfinance calls are stubbed — these pin OUR mapping."""

from __future__ import annotations

import pandas as pd
import pytest

import data.fetchers.stock as fetchers
from services.stock_metrics import benchmark_symbol_for_exchange


class _StubTicker:
    def __init__(self, info, qis=None):
        self.info = info
        self.quarterly_income_stmt = qis if qis is not None else pd.DataFrame()


@pytest.fixture
def aapl_stub(monkeypatch):
    qis = pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): {"Total Revenue": 100e9, "Net Income": 25e9, "Operating Income": 30e9, "Diluted EPS": 1.5},
            pd.Timestamp("2025-12-31"): {"Total Revenue": 120e9, "Net Income": 30e9, "Operating Income": 40e9, "Diluted EPS": 2.0},
        }
    )
    info = {
        "longName": "Apple Inc.",
        "currency": "USD",
        "fullExchangeName": "NasdaqGS",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 4.5e12,
        "currentPrice": 311.0,
        "trailingPE": 37.8,
        "priceToBook": 42.9,
        "dividendYield": 0.35,  # yfinance already returns dividend yield as a percentage
        "returnOnEquity": 1.41,  # …but ratio fields as fractions
        "profitMargins": 0.26,
        "revenueGrowth": 0.08,
        "beta": 1.09,
        "fiftyTwoWeekHigh": 320.0,
        "fiftyTwoWeekLow": 210.0,
        "longBusinessSummary": "Designs smartphones.",
    }
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda sym: _StubTicker(info, qis))


def test_global_fundamentals_maps_ratios_and_quarters(aapl_stub):
    g = fetchers.fetch_global_fundamentals("AAPL")

    assert g["name"] == "Apple Inc."
    assert g["currency"] == "USD"
    assert g["market_cap"] == 4.5e12
    assert g["pe"] == 37.8
    assert g["roe"] == pytest.approx(141.0)  # fraction → %
    assert g["profit_margin"] == pytest.approx(26.0)
    assert g["revenue_growth"] == pytest.approx(8.0)
    assert g["dividend_yield"] == 0.35  # already %; passed through untouched
    # quarters sorted oldest→newest with derived operating margin
    assert [q["label"] for q in g["quarters"]] == ["Dec 2025", "Mar 2026"]
    assert g["quarters"][1]["sales"] == 100e9
    assert g["quarters"][1]["opm_pct"] == pytest.approx(30.0)


def test_global_fundamentals_unknown_symbol_is_none(monkeypatch):
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda sym: _StubTicker({}))
    assert fetchers.fetch_global_fundamentals("NOPE123") is None


def test_search_global_tickers_filters_to_tradables(monkeypatch):
    class _StubSearch:
        def __init__(self, *a, **k):
            self.quotes = [
                {"symbol": "AAPL", "shortname": "Apple Inc.", "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
                {"symbol": "BTC-USD", "shortname": "Bitcoin", "exchDisp": "CCC", "quoteType": "CRYPTOCURRENCY"},
                {"symbol": "^GSPC", "shortname": "S&P 500", "exchDisp": "SNP", "quoteType": "INDEX"},
                {"symbol": "VOO", "shortname": "Vanguard S&P 500", "exchDisp": "NYSEArca", "quoteType": "ETF"},
            ]

    monkeypatch.setattr(fetchers.yf, "Search", _StubSearch)

    out = fetchers.search_global_tickers("apple")

    assert [(r["symbol"], r["quote_type"]) for r in out] == [("AAPL", "EQUITY"), ("VOO", "ETF")]
    assert out[0]["exchange"] == "NASDAQ"


def test_benchmark_routing_by_exchange():
    assert benchmark_symbol_for_exchange("NSE") == "^NSEI"
    assert benchmark_symbol_for_exchange(None) == "^NSEI"  # registry NSE-master rows carry no exchange
    assert benchmark_symbol_for_exchange("NASDAQ") == "^GSPC"
    assert benchmark_symbol_for_exchange("NYSE") == "^GSPC"
