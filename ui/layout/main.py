from dash import html, dcc


def main_layout(sidebar):
    return html.Div(
        [
            sidebar,
            html.Div(
                [
                    dcc.Store(id="nav-store"),
                    dcc.Store(id="holdings-store"),
                    dcc.Store(
                        id="selected-schemes-store",
                        storage_type="local",
                        data=[
                            "parag-parikh-dynamic-asset-allocation-fund-regular-plan-growth",
                            "360-one-silver-etf",
                            "parag-parikh-flexi-cap-fund-regular-plan-growth",
                        ],
                    ),
                    dcc.Tabs(
                        id="mf-tabs",
                        value="overview",
                        children=[
                            dcc.Tab(label="Overview", value="overview"),
                            dcc.Tab(label="Overlap", value="overlap"),
                            dcc.Tab(label="Returns", value="returns"),
                            dcc.Tab(label="Holdings", value="holdings"),
                        ],
                    ),
                    html.Div(id="mf-tab-content"),
                ],
                style={"marginLeft": "300px", "padding": "20px"},
            ),
        ]
    )
