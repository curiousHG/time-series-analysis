"""End-to-end AppTest smoke for every page against the local Postgres: a default render must raise
no exception. Loaders are not patched; the network is blocked so a render never fetches."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from core.database import engine

PAGES = (
    "ui/views/overview/page.py",
    "ui/views/portfolio/page.py",
    "ui/views/mutual_fund/page.py",
    "ui/views/mf_screener/page.py",
    "ui/views/stock_analysis/page.py",
    "ui/views/stock_screener/page.py",
    "ui/views/backtest_lab/page.py",
    "ui/views/settings/page.py",
)

pytestmark = pytest.mark.db


def _postgres_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception:
        return False
    return True


if not _postgres_reachable():
    pytest.skip("local Postgres unreachable", allow_module_level=True)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network call attempted during page smoke test")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    import yfinance

    monkeypatch.setattr(yfinance, "download", refuse)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page: str) -> None:
    at = AppTest.from_file(page).run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
