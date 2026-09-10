"""Universe picker for the run form: an NSE index, the Stock Analysis watchlist, or a pasted list.

The picker always resolves the choice to bare symbols and shows the count, so nobody launches a
ten-year run against an index whose constituent list came back empty.
"""

from __future__ import annotations

import streamlit as st

from services.backtest.config import UniverseSpec
from ui.persistence.selections import load_selection
from ui.state.loaders import load_universe_cached
from ui.views.backtest_lab.params import seed

INDEX_CHOICES = (
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY 100",
    "NIFTY 200",
    "NIFTY 500",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 250",
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY PHARMA",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY ENERGY",
)
KIND_LABELS = {"index": "Index", "watchlist": "Watchlist", "list": "Custom"}
WATCHLIST_SELECTION = "selected_stocks"


def watchlist_symbols() -> list[str]:
    """The Stock Analysis watchlist — the same persisted selection that page writes."""
    return [str(s) for s in (load_selection(WATCHLIST_SELECTION, []) or [])]


def resolve(spec: UniverseSpec) -> list[str]:
    """Bare NSE symbols for `spec`, through the cached loader."""
    return load_universe_cached(spec.kind, spec.name, tuple(spec.symbols), tuple(watchlist_symbols()))


def freeze(spec: UniverseSpec, symbols: list[str]) -> UniverseSpec:
    """A watchlist is a moving target — store what it resolved to, so the run records what it ran on."""
    if spec.kind != "watchlist":
        return spec
    return UniverseSpec(kind="list", name="Watchlist", symbols=tuple(symbols))


def render(*, prefix: str, universe: UniverseSpec | None = None, force: bool = False) -> tuple[UniverseSpec, list[str]]:
    """Draw the picker and return the chosen spec with the symbols it resolves to."""
    kind_key, index_key, custom_key = f"{prefix}universe_kind", f"{prefix}universe_index", f"{prefix}universe_custom"
    current = universe or UniverseSpec(kind="index", name=INDEX_CHOICES[0])
    seed(kind_key, KIND_LABELS.get(current.kind, "Index"), force=force)
    seed(index_key, current.name if current.name in INDEX_CHOICES else INDEX_CHOICES[0], force=force)
    seed(custom_key, ", ".join(current.symbols) if current.kind == "list" else "", force=force)

    label = st.radio("Universe", list(KIND_LABELS.values()), horizontal=True, key=kind_key)
    if label == "Index":
        spec = UniverseSpec(kind="index", name=st.selectbox("Index", INDEX_CHOICES, key=index_key))
    elif label == "Watchlist":
        spec = UniverseSpec(kind="watchlist")
        st.caption(
            f"Uses the {len(watchlist_symbols())} stock(s) tracked on Stock Analysis, frozen when you create the run."
        )
    else:
        raw = st.text_area(
            "Symbols",
            key=custom_key,
            placeholder="TCS, INFY, HDFCBANK",
            help="Comma- or newline-separated NSE symbols; the .NS suffix is optional.",
            height=80,
        )
        spec = UniverseSpec(
            kind="list", symbols=tuple(s.strip() for s in raw.replace("\n", ",").split(",") if s.strip())
        )

    symbols = resolve(spec)
    if symbols:
        st.caption(f"{len(symbols)} symbol(s) resolved — {', '.join(symbols[:6])}{' …' if len(symbols) > 6 else ''}")
    else:
        st.warning("This universe resolves to no symbols yet — pick another index or paste a list.")
    return spec, symbols
