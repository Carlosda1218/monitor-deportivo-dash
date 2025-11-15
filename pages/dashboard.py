from dash import html

def layout():
    return html.Div([
        html.H2("Dashboard"),
        html.P("Bienvenido a PowerSync. Usa el menú para comenzar: Usuarios, Sensores, ECG o Cuestionario."),
        html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Estado", className="kpi-label"),
                html.Div("Activo", className="kpi-value"),
                html.Div(className="kpi-ecg-line")
            ]),
            html.Div(className="kpi", children=[
                html.Div("Módulos", className="kpi-label"),
                html.Div("Usuarios • Sensores • ECG • Cuestionario", className="kpi-value", style={"fontSize":"16px"}),
                html.Div(className="kpi-ecg-line")
            ]),
        ])
    ])
