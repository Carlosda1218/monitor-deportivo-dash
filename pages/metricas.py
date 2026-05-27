from dash import html, dcc
from flask import session
import plotly.graph_objects as go
import db
import ui_charts as _uc


def _kpi(label, value, sub="", color=None):
    style = {"color": color} if color else {}
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(str(value), className="kpi-value", style=style),
        html.Div(sub, className="kpi-sub"),
        html.Div(className="kpi-ecg-line"),
    ])


def _checkins_chart(checkins_daily):
    if not checkins_daily:
        return _uc.empty_figure(
            "Check-ins diarios",
            "Sin registros en los últimos 30 días.",
            height=260,
        )
    days  = [r["day"] for r in checkins_daily]
    counts = [r["n"]  for r in checkins_daily]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=days, y=counts,
        marker=dict(color=_uc.PS_PALETTE[0], opacity=0.85, line=dict(width=0)),
        hovertemplate="%{x}<br><b>%{y}</b> check-ins<extra></extra>",
    ))
    _uc.apply_chart_style(fig, title="Check-ins diarios — últimos 30 días", height=260)
    try:
        fig.update_layout(margin=dict(l=44, r=16, t=44, b=36))
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.07)",
                         zeroline=False, tickfont=dict(size=11, color="#8fa3bf"))
        fig.update_xaxes(showgrid=False, tickfont=dict(size=10, color="#8fa3bf"))
    except Exception:
        pass
    return fig


def _wau_trend_chart(wau_trend):
    if not wau_trend:
        return _uc.empty_figure("Usuarios activos semanales", "Sin datos de tendencia.", height=200)
    labels = [w["label"] for w in wau_trend]
    values = [w["n"]     for w in wau_trend]
    peak   = max(values) if values else 1

    fig = go.Figure()
    colors = [_uc.PS_PALETTE[0] if v == peak else "rgba(47,183,196,.35)" for v in values]
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker=dict(color=colors, line=dict(width=0)),
        hovertemplate="%{x}<br><b>%{y}</b> usuarios activos<extra></extra>",
    ))
    _uc.apply_chart_style(fig, title="Usuarios activos semanales (WAU) — 4 semanas", height=200)
    try:
        fig.update_layout(margin=dict(l=44, r=16, t=44, b=28))
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.07)",
                         zeroline=False, tickfont=dict(size=11, color="#8fa3bf"))
        fig.update_xaxes(showgrid=False, tickfont=dict(size=12, color="#8fa3bf"))
    except Exception:
        pass
    return fig


def _sport_bars(sport_dist):
    if not sport_dist:
        return html.P("Sin datos de deporte.", className="text-muted")
    total = sum(s["n"] for s in sport_dist) or 1
    items = []
    colors = [_uc.PS_PALETTE[0], _uc.PS_PALETTE[1], _uc.PS_PALETTE[2]]
    for i, s in enumerate(sport_dist):
        pct = round(s["n"] / total * 100)
        color = colors[i % len(colors)]
        items.append(html.Div(style={"marginBottom": "10px"}, children=[
            html.Div(style={"display": "flex", "justifyContent": "space-between",
                            "marginBottom": "4px"}, children=[
                html.Span(s["sport"], style={"fontSize": "13px", "color": "var(--ink)"}),
                html.Span(f"{s['n']} atletas · {pct}%",
                          style={"fontSize": "12px", "color": "var(--muted)"}),
            ]),
            html.Div(style={"background": "var(--surface)", "borderRadius": "6px",
                            "height": "8px", "overflow": "hidden"}, children=[
                html.Div(style={"width": f"{pct}%", "height": "8px",
                                "background": color, "borderRadius": "6px"}),
            ]),
        ]))
    return html.Div(items)


def layout():
    uid  = session.get("user_id")
    role = str(session.get("role") or "")

    if not uid or role != "admin":
        return html.Div([
            html.H2("Acceso restringido"),
            html.P("Esta sección es exclusiva para administradores y equipo interno.",
                   className="text-muted"),
        ], className="page-content")

    try:
        m = db.get_platform_metrics()
    except Exception as exc:
        return html.Div([
            html.H2("Error al cargar métricas"),
            html.P(str(exc), className="text-muted"),
        ], className="page-content")

    total_athletes    = m["total_athletes"]
    total_coaches     = m["total_coaches"]
    dau               = m["dau"]
    wau               = m["wau"]
    checkins_7d       = m["checkins_7d"]
    checkins_30d      = m["checkins_30d"]
    active_7d         = m["active_athletes_7d"]
    avg_w             = m["avg_wellness_7d"]
    new_users_30d     = m["new_users_30d"]
    sessions_7d       = m["sessions_7d"]
    sport_dist        = m["sport_dist"]
    wau_trend         = m.get("wau_trend", [])
    checkins_daily    = m["checkins_daily"]

    # Engagement: active athletes / total athletes (avoid div by 0)
    engagement_pct = (
        f"{round(active_7d / total_athletes * 100)}%"
        if total_athletes > 0 else "—"
    )
    avg_w_str = f"{avg_w:.0f}/100" if avg_w is not None else "Sin datos"
    avg_w_color = (
        "var(--neon)"   if avg_w and avg_w >= 75 else
        "var(--amber)"  if avg_w and avg_w >= 60 else
        "var(--punch)"  if avg_w else None
    )

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Métricas de uso"),
            html.P(
                "Uso de la plataforma en tiempo real · actualización al cargar la página.",
                className="text-muted",
            ),
        ]),

        html.Div(className="ecg-divider ecg-divider--spaced"),

        # KPI strip — fila 1
        html.Div(className="kpis", children=[
            _kpi("Atletas registrados", total_athletes,
                 f"{total_coaches} coach{'es' if total_coaches != 1 else ''}"),
            _kpi("Activos 7 días", active_7d,
                 f"{engagement_pct} del total · engagement semanal"),
            _kpi("DAU (últimas 24h)", dau,
                 "usuarios únicos con check-in reciente"),
            _kpi("WAU (últimos 7d)", wau,
                 "usuarios únicos con al menos 1 check-in"),
        ]),

        # KPI strip — fila 2
        html.Div(className="kpis", style={"marginTop": "8px"}, children=[
            _kpi("Check-ins 7d", checkins_7d,
                 f"{checkins_30d} en los últimos 30 días"),
            _kpi("Sesiones 7d", sessions_7d,
                 "entrenamientos registrados esta semana"),
            _kpi("Bienestar prom. 7d", avg_w_str,
                 "promedio equipo últimos 7 días", color=avg_w_color),
            _kpi("Nuevos usuarios 30d", new_users_30d,
                 "registros en el último mes"),
        ]),

        html.Div(className="ecg-divider ecg-divider--spaced"),

        # Tendencia WAU — fila completa antes del grid
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            dcc.Graph(
                figure=_wau_trend_chart(wau_trend),
                config=_uc.graph_config(),
                style={"width": "100%"},
            ),
        ]),

        html.Div(className="profile-main-grid", children=[
            # Gráfica de check-ins diarios
            html.Div(className="card", children=[
                dcc.Graph(
                    figure=_checkins_chart(checkins_daily),
                    config=_uc.graph_config(),
                    style={"width": "100%"},
                ),
            ]),

            # Desglose por deporte + resumen
            html.Div(className="profile-stack", children=[
                html.Div(className="card", children=[
                    html.H4("Distribución por deporte", className="card-title"),
                    html.P("Atletas activos por disciplina.", className="text-muted",
                           style={"marginBottom": "14px"}),
                    _sport_bars(sport_dist),
                ]),
                html.Div(className="card", children=[
                    html.H4("Resumen de plataforma", className="card-title"),
                    html.Ul([
                        html.Li([html.Strong("Total usuarios: "),
                                 str(m["total_users"])]),
                        html.Li([html.Strong("Coaches: "),
                                 str(total_coaches)]),
                        html.Li([html.Strong("Deportistas: "),
                                 str(total_athletes)]),
                        html.Li([html.Strong("Engagement semanal: "),
                                 engagement_pct]),
                        html.Li([html.Strong("Bienestar promedio: "),
                                 avg_w_str]),
                        html.Li([html.Strong("Check-ins/deportista activo: "),
                                 f"{round(checkins_7d / active_7d, 1)}"
                                 if active_7d > 0 else "—"]),
                    ], className="list-compact"),
                ]),
            ]),
        ]),
    ], className="page-content")
