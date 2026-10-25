"""Helpers shared by the upstream fetchers."""

from __future__ import annotations


def shape_of(payload: object) -> str:
    """Short description of an unexpected payload, for `UpstreamFormatError` messages."""
    if isinstance(payload, dict):
        return f"object with keys {sorted(payload)[:8]}"
    if isinstance(payload, list):
        return f"list of {len(payload)}"
    return type(payload).__name__
