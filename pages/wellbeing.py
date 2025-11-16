import dash
from dash import html
dash.register_page(__name__, path="/wellbeing")
try:
    from questionnaires import layout as q_layout  # ajusta si tu layout se llama distinto
    layout = q_layout
except Exception:
    layout = html.Div([html.H1("Cuestionario de bienestar"), html.P("Conecta aquí questionnaires.py")])
