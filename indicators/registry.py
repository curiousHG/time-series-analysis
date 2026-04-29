"""Indicator registry and compute helper."""

import pandas as pd

INDICATOR_REGISTRY: dict[str, dict] = {}


def register(name: str, description: str, overlay: bool = False):
    """Decorator to register an indicator function."""

    def wrapper(fn):
        INDICATOR_REGISTRY[name] = {
            "fn": fn,
            "description": description,
            "overlay": overlay,
        }
        return fn

    return wrapper


def compute_indicators(df: pd.DataFrame, selected: list[str]) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """
    Compute selected indicators. Returns (overlays, panels).
    overlays: series to plot on the price chart
    panels: series to plot in separate subplots
    """
    overlays: dict[str, pd.Series] = {}
    panels: dict[str, pd.Series] = {}

    for name in selected:
        if name not in INDICATOR_REGISTRY:
            continue
        entry = INDICATOR_REGISTRY[name]
        result = entry["fn"](df)
        if entry["overlay"]:
            overlays.update(result)
        else:
            panels.update(result)

    return overlays, panels
