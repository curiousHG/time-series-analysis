"""Flow test for the unified Stock-Analysis picker catalog (services.market_data_service.analysis_catalog).

Verifies the classification that drives the picker: registry rows become 'stock' or 'etf' by quote_type,
and both NSE bhavcopy names and international `^` symbols become 'index' — so a selected ticker routes to
the right view (equity / ETF / index)."""

from __future__ import annotations

import polars as pl

import services.market_data_service as mds


def test_analysis_catalog_classifies_stock_etf_index(monkeypatch):
    monkeypatch.setattr(
        "data.repositories.stock.load_registry_catalog",
        lambda: pl.DataFrame(
            {
                "symbol": ["RELIANCE", "GOLDBEES", "POWERINDIA"],
                "name": ["Reliance Industries", "Gold ETF", "Hitachi Energy India"],
                "quote_type": [None, "ETF", None],
            }
        ),
    )
    monkeypatch.setattr("data.repositories.stock.list_bhavcopy_index_names", lambda: ["Nifty 50", "Nifty Bank"])
    monkeypatch.setattr("services.insights_service.INTERNATIONAL_INDEX_SYMBOLS", [("^GSPC", "S&P 500", "US")])

    kind = {c["id"]: c["kind"] for c in mds.analysis_catalog()}

    assert kind["RELIANCE"] == "stock"
    assert kind["POWERINDIA"] == "stock"
    assert kind["GOLDBEES"] == "etf"  # quote_type='ETF' → ETF, not a plain equity
    assert kind["Nifty 50"] == "index"
    assert kind["Nifty Bank"] == "index"
    assert kind["^GSPC"] == "index"  # international symbols are indices too


def test_analysis_catalog_carries_display_names(monkeypatch):
    monkeypatch.setattr(
        "data.repositories.stock.load_registry_catalog",
        lambda: pl.DataFrame({"symbol": ["GOLDBEES"], "name": ["Gold ETF"], "quote_type": ["ETF"]}),
    )
    monkeypatch.setattr("data.repositories.stock.list_bhavcopy_index_names", lambda: [])
    monkeypatch.setattr("services.insights_service.INTERNATIONAL_INDEX_SYMBOLS", [("^GSPC", "S&P 500", "US")])

    by_id = {c["id"]: c for c in mds.analysis_catalog()}

    assert by_id["GOLDBEES"]["name"] == "Gold ETF"  # picker label can search by name
    assert by_id["^GSPC"]["name"] == "S&P 500"  # international display name, not the raw ^ symbol
