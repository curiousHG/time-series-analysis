from dash import callback, Input, Output, State
import polars as pl

from src.mutualFunds.data_store import ensure_nav_data, ensure_holdings_data
from src.mutualFunds.registry import load_registry
from views.mutualFund.page import render_tab

@callback(
    Output("nav-store", "data"),
    Output("holdings-store", "data"),
    Input("run-btn", "n_clicks"),
    State("scheme-picker", "value"),
    prevent_initial_call=True,
)
def load_data(_, slugs):
    registry = load_registry().filter(pl.col("schemeSlug").is_in(slugs))
    names = registry["schemeName"].to_list()

    nav = ensure_nav_data(names)
    h, s, a = ensure_holdings_data(slugs)

    return (
        nav.to_pandas().to_dict("records"),
        {
            "holdings": h.to_pandas().to_dict("records"),
            "sectors": s.to_pandas().to_dict("records"),
            "assets": a.to_pandas().to_dict("records"),
        },
    )

@callback(
    Output("selected-schemes-store", "data"),
    Input("scheme-picker", "value"),
    prevent_initial_call=True,
)
def sync_selected_schemes(slugs):
    print("Selected schemes:", slugs)
    return slugs or []  # Default scheme slug


@callback(
    Output("mf-tab-content", "children"),
    Input("mf-tabs", "value"),
    Input("nav-store", "data"),
    Input("holdings-store", "data"),
    Input("selected-schemes-store", "data"),
)
def switch_tab(tab, nav, holdings, selected_slugs):
    return render_tab(tab, nav, holdings, selected_slugs)
