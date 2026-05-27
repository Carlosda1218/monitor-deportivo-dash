from dash import html, dcc
from flask import session

def layout():
    if session.get("user_id"):
        session.clear()
    return html.Div([
        html.Div("Sesión cerrada.", style={"opacity": 0.7, "marginBottom": "12px"}),
        dcc.Link("Volver a iniciar sesión", href="/login", className="btn btn-primary"),
    ])
