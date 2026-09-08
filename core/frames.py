"""Row-tuples from a SELECT → a typed polars frame, the shape every repository loader returns."""

from __future__ import annotations

import polars as pl


def frame_from_rows(rows, schema: dict[str, type[pl.DataType] | pl.DataType]) -> pl.DataFrame:
    """Build a frame whose columns are `schema` keys in SELECT order. Empty rows give an empty
    frame with the same schema, so callers never special-case the no-data path."""
    columns = list(schema)
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame({c: [r[i] for r in rows] for i, c in enumerate(columns)}, schema=schema)
