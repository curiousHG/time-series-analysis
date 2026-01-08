from dash import Dash
import dash_bootstrap_components as dbc
from layout.sidebar import sidebar
from layout.main import main_layout
import callbacks.mutual_fund

app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
server = app.server

app.layout = main_layout(sidebar)

if __name__ == "__main__":
    app.run(debug=True, dev_tools_hot_reload=True)
    
