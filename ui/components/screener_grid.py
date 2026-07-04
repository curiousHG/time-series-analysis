"""Shared AgGrid screener table — header/floating filters, typed columns, pinned link
column, native Cmd/Ctrl+C copy. Generalized from the MF screener so the stock screener
(and future grids) get the same interaction model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from ui.charts.theme import LINK

if TYPE_CHECKING:
    import pandas as pd

# Re-fit columns to grid width on first render and on resize. Respects each column's
# min/max, so many metrics overflow to a horizontal scroll instead of being crushed.
_FIT_COLUMNS = JsCode("function(params) { params.api.sizeColumnsToFit(); }")

_NUMERIC_FORMATTER = "x == null ? '' : Number(x).toFixed(2)"


def _build_grid_options(
    pdf: pd.DataFrame,
    *,
    numeric_cols: set[str] | frozenset[str],
    text_cols: set[str] | frozenset[str],
    pinned_col: str | None,
    link_col: str | None,
    pinned_min_width: int = 300,
):
    """Configure AgGrid: header filters, numeric vs text filter type, pinned link column,
    native Cmd/Ctrl+C copy.
    """
    gob = GridOptionsBuilder.from_dataframe(pdf)
    gob.configure_default_column(
        sortable=True,
        resizable=True,
        filter=True,
        floatingFilter=True,  # filter input under the column header
        minWidth=80,
    )
    for c in pdf.columns:
        if c in numeric_cols:
            gob.configure_column(
                c,
                type=["numericColumn"],
                filter="agNumberColumnFilter",
                valueFormatter=_NUMERIC_FORMATTER,
            )
        elif c in text_cols:
            gob.configure_column(c, filter="agTextColumnFilter")

    if pinned_col and pinned_col in pdf.columns:
        style = {"color": LINK, "cursor": "pointer", "fontWeight": 600} if pinned_col == link_col else None
        gob.configure_column(pinned_col, pinned="left", minWidth=pinned_min_width, cellStyle=style)
    if link_col and link_col != pinned_col and link_col in pdf.columns:
        gob.configure_column(link_col, cellStyle={"color": LINK, "cursor": "pointer", "fontWeight": 600})

    gob.configure_grid_options(
        domLayout="normal",
        suppressHorizontalScroll=False,
        alwaysShowVerticalScroll=True,
        enableCellTextSelection=True,  # text-selectable cells for native copy
        ensureDomOrder=True,  # Cmd/Ctrl+C honours visual order
        suppressCopyRowsToClipboard=False,
        copyHeadersToClipboard=True,  # include column names in the clipboard
        onGridSizeChanged=_FIT_COLUMNS,  # re-fit on resize
        onFirstDataRendered=_FIT_COLUMNS,  # ...and on initial render
    )

    return gob.build()


def render_screener_grid(
    pdf: pd.DataFrame,
    *,
    numeric_cols: set[str] | frozenset[str],
    text_cols: set[str] | frozenset[str],
    theme: Any,
    pinned_col: str | None = None,
    link_col: str | None = None,
    height: int = 650,
    pinned_min_width: int = 300,
) -> dict:
    """Render the grid and return the AgGrid response (for click-through).

    Deliberately keyless: with a fixed `key`, st_aggrid keeps the first mount's data and
    won't re-render when the filtered frame changes (unless `reload_data` gymnastics are
    added). One grid per page means no key is needed.
    """
    grid_options = _build_grid_options(
        pdf,
        numeric_cols=numeric_cols,
        text_cols=text_cols,
        pinned_col=pinned_col,
        link_col=link_col,
        pinned_min_width=pinned_min_width,
    )
    return AgGrid(
        pdf,
        gridOptions=grid_options,
        height=height,
        theme=theme,
        allow_unsafe_jscode=True,  # required for the sizeColumnsToFit callbacks
        # `cellClicked` drives open-on-click; the rest keep filter / sort in sync.
        update_on=["cellClicked", "filterChanged", "sortChanged"],
        # Return rows in the order the user actually sees (client-side sort + header filters
        # applied) so callers like the screener's "Fetch top N" can be WYSIWYG.
        data_return_mode="FILTERED_AND_SORTED",
    )


def clicked_cell_value(grid_response: dict, col: str) -> str | None:
    """Return the cell value when the user just clicked in column `col`, else None."""
    event = grid_response.get("event_data") or {}
    if event.get("streamlitRerunEventTriggerName") != "cellClicked":
        return None
    if (event.get("colDef") or {}).get("field") != col:
        return None
    value = (event.get("data") or {}).get(col) or event.get("value")
    return str(value) if value else None
