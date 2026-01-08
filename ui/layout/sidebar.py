from dash import html, dcc
import dash_bootstrap_components as dbc
from src.mutualFunds.registry import load_registry

registry = load_registry()

sidebar = html.Div(
    [
        html.H3("💼 Mutual Funds"),
        html.Hr(),

        dcc.Dropdown(
            options=[
                {"label": r["schemeName"], "value": r["schemeSlug"]}
                for r in registry.to_dicts()
            ],
            value=["parag-parikh-dynamic-asset-allocation-fund-regular-plan-growth",
                            "360-one-silver-etf",
                            "parag-parikh-flexi-cap-fund-regular-plan-growth"],
            multi=True,
            id="scheme-picker",
            placeholder="Select funds",
            style={"width": "auto"},
        ),

        html.Br(),

        dbc.Button("Run Analysis", id="run-btn", color="primary"),
    ],
    style={
        "width": "320px",
        "padding": "15px",
        "background": "#111",
        "height": "100vh",
        "position": "fixed",
    },
)
