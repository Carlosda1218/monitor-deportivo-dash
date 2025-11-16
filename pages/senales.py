import dash
from dash import html
dash.register_page(__name__, path="/senales")
try:
    from sensors import layout as sensors_layout  # ajusta si tu layout se llama distinto
    layout = sensors_layout
except Exception:
    layout = html.Div([html.H1("Cargar señales"), html.P("Conecta aquí tu módulo sensors.py")])
