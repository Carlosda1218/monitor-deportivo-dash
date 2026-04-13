from dash import html, dcc
from flask import session
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import db
import ui_charts as _uc


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v


def _safe_str(value, default="-"):
    value = _to_str(value)
    if not value:
        return default
    return str(value)




def _profile_defaults():
    return {
        "competitive_level": None,
        "weight_category": None,
        "dominant_side": None,
        "current_status": None,
        "watch_zone": None,
        "competition_proximity": None,
        "profile_note": None,
    }


def _profile_label(value, fallback="Sin definir"):
    value = _to_str(value)
    if not value:
        return fallback
    return str(value)


def _profile_grid_item(label, value):
    return html.Div(
        className="inner-cell",
        style={
            "background": "#151a21",
            "border": "1px solid #232a36",
            "borderRadius": "12px",
            "padding": "12px",
        },
        children=[
            html.Div(label, className="kpi-label"),
            html.Div(_profile_label(value), className="kpi-value", style={"fontSize": "18px"}),
        ],
    )

def _quick_link(label, href, primary=False):
    return dcc.Link(
        html.Button(label, className="btn btn-primary" if primary else "btn btn-ghost"),
        href=href,
        style={"display": "inline-block"}
    )


# --- CS-021: Tarjeta compartible ---

def generate_share_card(name, sport, weekly_summary, load_history, competition_proximity=""):
    """Genera una imagen PNG (600×380 @2x) lista para compartir con el resumen semanal."""
    import io, datetime

    FONT  = "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial"
    BG    = "#0d1117"
    TEAL  = "#2fb7c4"
    AMBER = "#f0a832"
    TEXT  = "#e8ecf0"
    MUTED = "#8fa3bf"

    labels   = [h["label"] for h in load_history]
    loads    = [h["load_units"] if h["load_units"] is not None else 0 for h in load_history]
    wellness = [h["wellness_avg"] for h in load_history]
    has_load     = any(l > 0 for l in loads)
    has_wellness = any(w is not None for w in wellness)

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if has_load:
        fig.add_trace(
            go.Bar(x=labels, y=loads, name="Carga semanal (UA)",
                   marker=dict(color=TEAL, opacity=0.75, line=dict(width=0))),
            secondary_y=False,
        )
    if has_wellness:
        fig.add_trace(
            go.Scatter(x=labels, y=wellness, name="Bienestar promedio (/100)", mode="lines+markers",
                       line=dict(color=AMBER, width=2.5),
                       marker=dict(size=7, color=AMBER), connectgaps=False),
            secondary_y=True,
        )

    n_sessions   = weekly_summary.get("n_sessions", 0)
    load_val     = weekly_summary.get("load_units")
    wellness_val = weekly_summary.get("wellness_avg")
    load_str     = f"{load_val} UA" if load_val is not None else "—"
    wellness_str = f"{wellness_val:.0f}/100" if wellness_val is not None else "—"

    _COMP = {
        "Semana competitiva":     ("SEMANA COMPETITIVA", "#e45a5a"),
        "Próximas 3-4 semanas":   ("~3-4 SEM PARA TORNEO", AMBER),
        "Próximas 6-8 semanas":   ("~6-8 SEM PARA TORNEO", TEAL),
    }
    comp_text, comp_color = _COMP.get((competition_proximity or "").strip(), ("", MUTED))
    week_str = datetime.date.today().strftime("Sem %W · %Y")

    fig.update_layout(
        width=600, height=380,
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(family=FONT, color=TEXT),
        margin=dict(l=60, r=60, t=110, b=72),
        showlegend=True,
        legend=dict(
            orientation="v",
            x=0.02, y=0.98,
            xanchor="left",
            yanchor="top",
            bgcolor="rgba(13,17,23,0.75)",
            bordercolor="rgba(255,255,255,0.08)",
            borderwidth=1,
            font=dict(size=11, color=MUTED, family=FONT),
        ),
    )

    _ax = dict(showgrid=True, gridcolor="rgba(255,255,255,0.07)", zeroline=False,
               showline=False, tickfont=dict(size=10, color=MUTED), tickcolor="rgba(0,0,0,0)")
    try:
        fig.update_xaxes(**_ax)
        fig.update_yaxes(secondary_y=False, **_ax)
        fig.update_yaxes(secondary_y=True, range=[0, 100], showgrid=False,
                         **{k: v for k, v in _ax.items() if k != "showgrid"})
    except Exception:
        pass

    def _ann(text, x, y, size, color, anchor="left", **kw):
        fig.add_annotation(text=text, x=x, y=y, xref="paper", yref="paper",
                           font=dict(size=size, color=color, family=FONT),
                           showarrow=False, xanchor=anchor, **kw)

    _ann("⚡ CombatIQ", 0.02, 1.30, 20, TEAL)
    if comp_text:
        _ann(comp_text, 0.98, 1.30, 11, comp_color, anchor="right")
    display = f"{name}{f' · {sport}' if sport else ''}  ·  {week_str}"
    _ann(display, 0.02, 1.13, 13, TEXT)
    metrics = f"<b>{n_sessions}</b> sesiones    <b>{load_str}</b> carga    <b>{wellness_str}</b> bienestar"
    _ann(metrics, 0.5, -0.24, 13, TEXT, anchor="center")
    _ann("combatiq.app", 0.98, -0.24, 10, MUTED, anchor="right")

    buf = io.BytesIO()
    try:
        fig.write_image(buf, format="png", width=600, height=380, scale=2)
        return buf.getvalue()
    except Exception:
        return None


# --- CS-018: Gráfica de carga 4 semanas ---

def _load_chart(history):
    """Combo chart: barras de carga (UA) + línea de bienestar — últimas 4 semanas."""
    if not history:
        return None

    labels  = [h["label"] for h in history]
    loads   = [h["load_units"] if h["load_units"] is not None else 0 for h in history]
    wellnes = [h["wellness_avg"] for h in history]

    has_load     = any(l > 0 for l in loads)
    has_wellness = any(w is not None for w in wellnes)

    if not has_load and not has_wellness:
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if has_load:
        fig.add_trace(
            _uc.make_bar_trace(labels, loads, "Carga (UA)", _uc.PS_PALETTE[0], opacity=0.80),
            secondary_y=False,
        )

    if has_wellness:
        fig.add_trace(
            _uc.make_line_marker_trace(labels, wellnes, "Bienestar", _uc.PS_PALETTE[1], width=2.5, marker_size=8),
            secondary_y=True,
        )

    # Eje izquierdo: carga
    _safe_ax = lambda **kw: fig.update_yaxes(secondary_y=False, **kw)
    try:
        fig.update_yaxes(
            title_text="Carga (UA)",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.08)",
            zeroline=False,
            tickfont=dict(size=11, color="#8fa3bf"),
            title=dict(font=dict(size=12, color=_uc.PS_PALETTE[0])),
            secondary_y=False,
        )
    except Exception:
        pass

    # Eje derecho: bienestar 0–100
    try:
        fig.update_yaxes(
            title_text="Bienestar",
            range=[0, 100],
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=11, color="#8fa3bf"),
            title=dict(font=dict(size=12, color=_uc.PS_PALETTE[1])),
            secondary_y=True,
        )
    except Exception:
        pass

    _uc.apply_chart_style(
        fig,
        title="Evolución de carga y bienestar — 4 semanas",
        height=270,
    )

    # Ajuste de margen superior para el título
    try:
        fig.update_layout(margin=dict(l=52, r=52, t=52, b=36))
    except Exception:
        pass

    return fig


def _load_chart_card(history):
    fig = _load_chart(history)
    if fig is None:
        return html.Div()
    return html.Div(
        className="inner-card",
        style={
            "borderRadius": "14px",
            "padding": "4px 8px 8px 8px",
            "marginTop": "14px",
        },
        children=[
            dcc.Graph(
                figure=fig,
                config=_uc.graph_config(),
                style={"width": "100%"},
            )
        ],
    )


# --- CS-016: Carga Acumulada Semanal ---

_FLAG_COLORS = {"green": "#27c98f", "yellow": "#f0a832", "red": "#e45a5a", "gray": "#5a6478"}
_FLAG_LABELS = {"green": "En forma", "yellow": "Con atención", "red": "Revisar carga", "gray": "Sin datos"}
_TREND_ICONS = {"up": "↑", "down": "↓", "stable": "→"}
_TREND_LABELS = {"up": "Carga subiendo", "down": "Carga bajando", "stable": "Carga estable"}


def _weekly_card_athlete(summary):
    flag = summary.get("flag", "gray")
    color = _FLAG_COLORS.get(flag, _FLAG_COLORS["gray"])
    n_sessions = summary.get("n_sessions", 0)
    load_units = summary.get("load_units")
    wellness_avg = summary.get("wellness_avg")
    trend = summary.get("trend", "stable")

    load_str = f"{load_units} UA" if load_units is not None else "Sin datos"
    wellness_str = f"{wellness_avg:.0f} / 100" if wellness_avg is not None else "Sin datos"

    return html.Div(
        className="inner-card",
        style={
            "background": "#0f131a",
            "border": f"1px solid {color}",
            "borderRadius": "14px",
            "padding": "18px 20px",
            "marginTop": "18px",
        },
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "justifyContent": "space-between", "marginBottom": "14px"},
                children=[
                    html.H4("Tu semana", className="card-title", style={"margin": 0}),
                    html.Span(
                        _FLAG_LABELS.get(flag, ""),
                        style={
                            "background": color,
                            "color": "#fff",
                            "borderRadius": "20px",
                            "padding": "3px 14px",
                            "fontSize": "13px",
                            "fontWeight": "600",
                        }
                    ),
                ]
            ),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr", "gap": "12px"},
                children=[
                    html.Div([
                        html.Div("Sesiones", className="kpi-label"),
                        html.Div(str(n_sessions), className="kpi-value"),
                        html.Div("últimos 7 días", className="kpi-sub"),
                    ], className="inner-cell", style={"background": "#151a21", "borderRadius": "10px", "padding": "12px"}),
                    html.Div([
                        html.Div("Carga total", className="kpi-label"),
                        html.Div(load_str, className="kpi-value"),
                        html.Div("RPE × min", className="kpi-sub"),
                    ], className="inner-cell", style={"background": "#151a21", "borderRadius": "10px", "padding": "12px"}),
                    html.Div([
                        html.Div("Bienestar prom.", className="kpi-label"),
                        html.Div(wellness_str, className="kpi-value"),
                        html.Div("últimos 7 días", className="kpi-sub"),
                    ], className="inner-cell", style={"background": "#151a21", "borderRadius": "10px", "padding": "12px"}),
                    html.Div([
                        html.Div("Tendencia", className="kpi-label"),
                        html.Div(
                            _TREND_ICONS.get(trend, "→"),
                            className="kpi-value",
                            style={"color": color}
                        ),
                        html.Div(_TREND_LABELS.get(trend, ""), className="kpi-sub"),
                    ], className="inner-cell", style={"background": "#151a21", "borderRadius": "10px", "padding": "12px"}),
                ]
            ),
        ]
    )


def _athlete_card_coach(athlete):
    w = athlete.get("weekly", {})
    flag = w.get("flag", "gray")
    color = _FLAG_COLORS.get(flag, _FLAG_COLORS["gray"])
    name = _safe_str(athlete.get("name"), "Atleta")
    sport = _safe_str(athlete.get("sport"), "")
    n_sessions = w.get("n_sessions", 0)
    load_units = w.get("load_units")
    wellness_avg = w.get("wellness_avg")
    trend = w.get("trend", "stable")

    load_str = f"{load_units} UA" if load_units is not None else "—"
    wellness_str = f"{wellness_avg:.0f}" if wellness_avg is not None else "—"

    return html.Div(
        className="inner-card",
        style={
            "background": "#0f131a",
            "border": f"1px solid {color}40",
            "borderLeft": f"3px solid {color}",
            "borderRadius": "10px",
            "padding": "14px 16px",
        },
        children=[
            html.Div(
                style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start"},
                children=[
                    html.Div([
                        html.Div(name, style={"fontWeight": "600", "fontSize": "15px", "color": "#e8ecf0"}),
                        html.Div(sport, style={"fontSize": "12px", "opacity": 0.6, "marginTop": "2px"}),
                    ]),
                    html.Span("●", style={"color": color, "fontSize": "20px", "lineHeight": "1"}),
                ]
            ),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "8px", "marginTop": "12px"},
                children=[
                    html.Div([
                        html.Div("Sesiones", style={"fontSize": "11px", "opacity": 0.6}),
                        html.Div(str(n_sessions), style={"fontWeight": "700", "fontSize": "20px", "color": "#e8ecf0"}),
                    ]),
                    html.Div([
                        html.Div("Carga (UA)", style={"fontSize": "11px", "opacity": 0.6}),
                        html.Div(load_str, style={"fontWeight": "700", "fontSize": "20px", "color": "#e8ecf0"}),
                    ]),
                    html.Div([
                        html.Div("Bienestar", style={"fontSize": "11px", "opacity": 0.6}),
                        html.Div(
                            f"{wellness_str} {_TREND_ICONS.get(trend, '→')}",
                            style={"fontWeight": "700", "fontSize": "20px", "color": color}
                        ),
                    ]),
                ]
            ),
        ]
    )


# --- CS-017: Recomendaciones deportivas por contexto ---

def _build_recommendations(sport, wellness_val, weekly_summary, athlete_profile, latest_answers):
    """Genera lista de {text, level} según bienestar, carga y contexto deportivo.
    level: 'info' | 'warning' | 'alert'. Máximo 4 recomendaciones."""
    recs = []
    a = latest_answers or {}
    load_units = weekly_summary.get("load_units") or 0
    trend = weekly_summary.get("trend", "stable")
    n_sessions = weekly_summary.get("n_sessions", 0)
    competition_proximity = (athlete_profile.get("competition_proximity") or "").strip()
    watch_zone = (athlete_profile.get("watch_zone") or "").strip()
    sport_clean = (sport or "").strip().lower()
    has_wellness = wellness_val is not None
    has_load = weekly_summary.get("has_data", False)

    if not has_wellness and not has_load:
        recs.append({"text": "Registra tu primera sesión y responde el cuestionario para ver recomendaciones personalizadas.", "level": "info"})
        return recs

    # Estado del día
    if has_wellness:
        if wellness_val < 50:
            recs.append({"text": "Readiness baja hoy. Considera sesión técnica liviana o recuperación activa en lugar de alta intensidad.", "level": "alert"})
        elif wellness_val < 70:
            recs.append({"text": "Estado moderado. Ajusta la intensidad y monitorea cómo responde el cuerpo durante la sesión.", "level": "warning"})
        else:
            recs.append({"text": "Buen estado general. Momento para trabajar con exigencia real.", "level": "info"})

    # Carga semanal
    if trend == "up" and load_units > 300:
        recs.append({"text": "Semana de carga alta. Asegura el descanso nocturno antes de la próxima sesión fuerte.", "level": "warning"})
    elif n_sessions == 0 and has_wellness:
        recs.append({"text": "Sin sesiones registradas esta semana. Registra tus entrenamientos para que la carga sea útil.", "level": "info"})

    # Cercanía a competencia
    if competition_proximity == "Semana competitiva":
        recs.append({"text": "Estás en semana de competencia. Prioriza frescura sobre volumen. Nada nuevo esta semana.", "level": "alert"})
    elif competition_proximity == "Próximas 3-4 semanas":
        recs.append({"text": "Competencia cercana. Mantén la intensidad pero reduce el volumen progresivamente.", "level": "info"})

    # Zona a vigilar
    if watch_zone:
        limitacion = a.get("limitacion_molestia")
        molestia = a.get("molestia_general")
        if limitacion is not None and int(limitacion) >= 3:
            recs.append({"text": f"La zona de vigilancia ({watch_zone}) reporta limitación. Evita carga directa sobre esa zona hoy.", "level": "warning"})
        elif molestia is not None and int(molestia) >= 4:
            recs.append({"text": f"Molestia alta en {watch_zone}. Comenta con tu coach antes de subir intensidad.", "level": "warning"})

    # Taekwondo
    if "taekwondo" in sport_clean:
        exp = a.get("tkd_explosividad")
        if exp is not None and int(exp) <= 2:
            recs.append({"text": "Explosividad baja hoy. No es el mejor día para velocidad reactiva o contraataque rápido.", "level": "warning"})
        mol_inf = a.get("tkd_molestia_inferior")
        if mol_inf is not None and int(mol_inf) >= 4:
            recs.append({"text": "Molestia en tren inferior. Prioriza técnica de manos y guardia. Evita pateos de alta intensidad.", "level": "warning"})

    # Boxeo
    if "boxeo" in sport_clean or "box" in sport_clean:
        mol_sup = a.get("box_molestia_superior")
        if mol_sup is not None and int(mol_sup) >= 4:
            recs.append({"text": "Molestia en tren superior. Considera sparring técnico sin contacto o trabajo de desplazamiento.", "level": "warning"})
        rapidez = a.get("box_rapidez")
        if rapidez is not None and int(rapidez) <= 2:
            recs.append({"text": "Velocidad de manos baja hoy. Coordinación o sombra ligera puede ser más productiva que contacto.", "level": "warning"})

    return recs[:4]


def _recommendations_card(recs):
    if not recs:
        return html.Div()

    _icon_color = {"info": "#27c98f", "warning": "#f0a832", "alert": "#e45a5a"}
    _icons = {"info": "→", "warning": "!", "alert": "!!"}

    items = []
    for r in recs:
        lv = r.get("level", "info")
        items.append(html.Div(className=f"rec-item rec-item--{lv}", children=[
            html.Span(_icons.get(lv, "→"), className="rec-item__icon"),
            html.Span(r["text"], className="rec-item__text"),
        ]))

    return html.Div(
        className="inner-card",
        style={"background": "#0f131a", "border": "1px solid #232a36", "borderRadius": "14px", "padding": "18px 20px", "marginTop": "14px"},
        children=[
            html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px", "marginBottom": "14px"}, children=[
                html.H4("Recomendaciones del día", className="card-title", style={"margin": 0}),
                html.Small("Basadas en tu bienestar, carga y contexto deportivo.", className="text-muted"),
            ]),
            html.Div(style={"display": "flex", "flexDirection": "column", "gap": "8px"}, children=items),
        ]
    )


def _coach_alerts(team_summary):
    alerts = [a for a in team_summary if a.get("weekly", {}).get("flag") == "red"]
    if not alerts:
        return html.Div()
    names = ", ".join(_safe_str(a.get("name"), "Atleta") for a in alerts)
    return html.Div(
        className="inner-card inner-card--alert",
        style={"background": "#1a0a0a", "border": "1px solid #e45a5a40", "borderLeft": "3px solid #e45a5a",
               "borderRadius": "8px", "padding": "10px 14px", "marginTop": "10px",
               "display": "flex", "gap": "10px", "alignItems": "flex-start"},
        children=[
            html.Span("!!", style={"color": "#e45a5a", "fontWeight": "700", "fontSize": "14px", "minWidth": "18px"}),
            html.Span(f"Requieren atención esta semana: {names}. Revisa su carga y bienestar antes de la próxima sesión.",
                      style={"fontSize": "14px", "color": "#d8dde6", "lineHeight": "1.5"}),
        ]
    )


def _parse_ts(ts):
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("T", " ")[:19])
    except Exception:
        return None


def _parse_answers_json(raw):
    if not raw:
        return {}
    try:
        import json as _json
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "ignore")
        data = _json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def _coach_focus_candidates(team_summary):
    from datetime import datetime

    candidates = []
    now = datetime.utcnow()

    for athlete in (team_summary or []):
        athlete_id = athlete.get("id")
        if athlete_id is None:
            continue

        try:
            athlete_profile = db.get_athlete_profile(int(athlete_id)) if hasattr(db, "get_athlete_profile") else _profile_defaults()
        except Exception:
            athlete_profile = _profile_defaults()

        try:
            qrows = db.list_questionnaires(int(athlete_id)) or []
        except Exception:
            qrows = []

        latest_q = qrows[0] if qrows else None
        latest_dt = _parse_ts((latest_q or {}).get("ts"))
        latest_answers = _parse_answers_json((latest_q or {}).get("answers_json"))
        latest_wellness = (latest_q or {}).get("wellness_score")

        weekly = athlete.get("weekly", {}) or {}
        load_units = weekly.get("load_units")
        load_units_num = float(load_units) if load_units is not None else 0.0
        trend = (weekly.get("trend") or "stable").strip().lower()
        flag = (weekly.get("flag") or "gray").strip().lower()
        sport = (athlete.get("sport") or "").strip().lower()
        competition = (athlete_profile.get("competition_proximity") or "").strip()
        watch_zone = (athlete_profile.get("watch_zone") or "").strip()

        reasons = []
        score = 0

        if latest_q is None:
            reasons.append("sin check-in reciente")
            score += 110
            checkin_stale = True
        else:
            checkin_stale = False
            if latest_dt is None:
                reasons.append("check-in con fecha no clara")
                score += 45
                checkin_stale = True
            else:
                age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600.0)
                if age_hours > 36:
                    reasons.append("check-in no actualizado")
                    score += 55
                    checkin_stale = True

            if latest_wellness is not None:
                try:
                    latest_wellness = float(latest_wellness)
                except Exception:
                    latest_wellness = None

            if latest_wellness is not None and latest_wellness < 55:
                reasons.append("readiness baja")
                score += 85
            elif latest_wellness is not None and latest_wellness < 70:
                reasons.append("readiness con vigilancia")
                score += 40

        if flag == "red":
            reasons.append("semana en rojo")
            score += 75
        elif flag == "yellow":
            reasons.append("semana con atencion")
            score += 35

        if trend == "up" and load_units_num >= 280:
            reasons.append("carga subiendo")
            score += 25

        if competition == "Semana competitiva":
            reasons.append("semana competitiva")
            score += 70
        elif competition == "Próximas 3-4 semanas":
            reasons.append("competencia cercana")
            score += 25

        if watch_zone:
            reasons.append(f"vigilar {watch_zone}")
            score += 15

        tkd_mol = _safe_int(latest_answers.get("tkd_molestia_inferior"))
        tkd_exp = _safe_int(latest_answers.get("tkd_explosividad"))
        box_mol = _safe_int(latest_answers.get("box_molestia_superior"))
        box_speed = _safe_int(latest_answers.get("box_rapidez"))

        if "taekwondo" in sport:
            if tkd_mol is not None and tkd_mol >= 4:
                reasons.append("molestia en tren inferior")
                score += 45
            elif tkd_exp is not None and tkd_exp <= 2:
                reasons.append("explosividad baja")
                score += 15

        if "boxeo" in sport or sport == "box" or "box" in sport:
            if box_mol is not None and box_mol >= 4:
                reasons.append("molestia en tren superior")
                score += 45
            elif box_speed is not None and box_speed <= 2:
                reasons.append("rapidez de manos baja")
                score += 15

        unique_reasons = []
        seen = set()
        for reason in reasons:
            key = reason.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique_reasons.append(reason)

        if not unique_reasons:
            continue

        if checkin_stale:
            headline = "Falta la lectura del dia"
            action = "Pidele el check-in antes del bloque principal para decidir mejor la carga."
        elif competition == "Semana competitiva":
            headline = "Semana sensible de competencia"
            action = "Confirma frescura y evita sumar volumen si hoy no te cambia la sesion."
        elif "taekwondo" in sport and tkd_mol is not None and tkd_mol >= 4:
            headline = "Atleta para proteger el pateo"
            action = "Si entrena hoy, protege pateo, desplazamiento y pierna de apoyo antes de apretar intensidad."
        elif ("boxeo" in sport or sport == "box" or "box" in sport) and box_mol is not None and box_mol >= 4:
            headline = "Atleta para proteger manos y guardia"
            action = "Si entrena hoy, baja contacto y vigila hombro, mano y guardia antes del bloque fuerte."
        elif flag == "red":
            headline = "Carga y bienestar delicados"
            action = "Abre su sesion o su analisis antes de decidir intensidad o volumen."
        elif flag == "yellow":
            headline = "Seguimiento fino antes de apretar"
            action = "Mantenlo bajo vigilancia y revisa como responde antes del bloque mas exigente."
        else:
            headline = "Atleta para revisar con contexto"
            action = "Revisa su estado del dia y mantente atento a como entra en la sesion."

        if score >= 130:
            badge_label = "Primero"
            badge_color = "#e45a5a"
            badge_bg = "#1a0a0a"
        elif score >= 75:
            badge_label = "Mirar hoy"
            badge_color = "#f0a832"
            badge_bg = "#1a150a"
        else:
            badge_label = "Seguimiento"
            badge_color = "#2fb7c4"
            badge_bg = "#0a1518"

        candidates.append({
            "score": score,
            "name": _safe_str(athlete.get("name"), "Atleta"),
            "sport": _safe_str(athlete.get("sport"), "-"),
            "headline": headline,
            "action": action,
            "reasons": unique_reasons[:3],
            "badge_label": badge_label,
            "badge_color": badge_color,
            "badge_bg": badge_bg,
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:4]


def _coach_focus_today(team_summary):
    if not team_summary:
        return html.Div()

    candidates = _coach_focus_candidates(team_summary)
    if not candidates:
        return html.Div(
            className="inner-card",
            style={"borderRadius": "14px", "padding": "16px 20px", "marginTop": "16px"},
            children=[
                html.H4("A quien mirar hoy", className="card-title"),
                html.Small("Hoy no hay banderas fuertes en el equipo. Puedes seguir el plan y usar sesion o analisis como apoyo fino.", className="text-muted"),
            ],
        )

    cards = []
    for candidate in candidates:
        reasons_text = " · ".join(candidate["reasons"])
        cards.append(
            html.Div(
                className="focus-candidate",
                style={
                    "border": f"1px solid {candidate['badge_color']}35",
                    "borderLeft": f"3px solid {candidate['badge_color']}",
                    "borderRadius": "10px",
                    "padding": "14px 16px",
                },
                children=[
                    html.Div(
                        style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start", "gap": "12px"},
                        children=[
                            html.Div([
                                html.Div(candidate["name"], style={"fontWeight": "700", "fontSize": "15px", "color": "#e8ecf0"}),
                                html.Div(candidate["sport"], style={"fontSize": "12px", "opacity": 0.6, "marginTop": "2px"}),
                            ]),
                            html.Span(
                                candidate["badge_label"],
                                style={
                                    "background": candidate["badge_bg"],
                                    "color": candidate["badge_color"],
                                    "border": f"1px solid {candidate['badge_color']}45",
                                    "borderRadius": "999px",
                                    "padding": "4px 10px",
                                    "fontSize": "11px",
                                    "fontWeight": "700",
                                    "whiteSpace": "nowrap",
                                },
                            ),
                        ],
                    ),
                    html.Div(candidate["headline"], style={"marginTop": "12px", "fontWeight": "700", "fontSize": "14px", "color": "#dfe7f2"}),
                    html.Div(f"Motivos: {reasons_text}", style={"marginTop": "8px", "fontSize": "13px", "lineHeight": "1.5", "color": "#9fb0c3"}),
                    html.Div(
                        [
                            html.Strong("Accion sugerida: ", style={"color": "#e8ecf0"}),
                            html.Span(candidate["action"], style={"color": "#d8dde6"}),
                        ],
                        style={"marginTop": "10px", "fontSize": "13px", "lineHeight": "1.55"},
                    ),
                ],
            )
        )

    return html.Div(
        className="inner-card",
        style={"borderRadius": "14px", "padding": "16px 20px", "marginTop": "16px"},
        children=[
            html.H4("A quien mirar hoy", className="card-title"),
            html.Small("Empieza por estos atletas si quieres abrir una lectura util antes de la sesion.", className="text-muted"),
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(auto-fit, minmax(260px, 1fr))",
                    "gap": "12px",
                    "marginTop": "14px",
                },
                children=cards,
            ),
        ],
    )


# --- CS-020: Contador de días a competencia ---

_COMP_CONFIG = {
    "Semana competitiva": {
        "color": "#e45a5a",
        "bg": "#1a0a0a",
        "icon": "🏆",
        "label": "Semana de competencia",
        "detail": "Prioriza frescura sobre volumen. Nada nuevo esta semana.",
    },
    "Próximas 3-4 semanas": {
        "color": "#f0a832",
        "bg": "#1a150a",
        "icon": "⏱",
        "label": "~3-4 semanas para el torneo",
        "detail": "Fase de ajuste — mantén intensidad y reduce volumen progresivamente.",
    },
    "Próximas 6-8 semanas": {
        "color": "#2fb7c4",
        "bg": "#0a1518",
        "icon": "📅",
        "label": "~6-8 semanas para el torneo",
        "detail": "Bloque de carga en curso — buena ventana para trabajo de volumen.",
    },
}


def _competition_badge(athlete_profile):
    proximity = (athlete_profile.get("competition_proximity") or "").strip()
    cfg = _COMP_CONFIG.get(proximity)
    if not cfg:
        return html.Div()

    color = cfg["color"]
    return html.Div(
        className="comp-badge",
        style={
            "border": f"1px solid {color}50",
            "borderLeft": f"3px solid {color}",
            "borderRadius": "10px",
            "padding": "12px 16px",
            "marginTop": "14px",
            "display": "flex",
            "alignItems": "center",
            "gap": "14px",
        },
        children=[
            html.Span(cfg["icon"], style={"fontSize": "22px", "lineHeight": "1"}),
            html.Div([
                html.Div(
                    cfg["label"],
                    style={"fontWeight": "700", "fontSize": "14px", "color": color},
                ),
                html.Div(
                    cfg["detail"],
                    style={"fontSize": "13px", "color": "#a0aec0", "marginTop": "2px"},
                ),
            ]),
        ],
    )


def _team_kpis(team_summary):
    """KPIs específicos de coach: semáforo del equipo, bienestar promedio, carga total."""
    counts = {"green": 0, "yellow": 0, "red": 0, "gray": 0}
    wellness_vals = []
    total_load = 0

    for a in team_summary:
        w = a.get("weekly", {})
        counts[w.get("flag", "gray")] += 1
        wv = w.get("wellness_avg")
        if wv is not None:
            wellness_vals.append(wv)
        lu = w.get("load_units")
        if lu is not None:
            total_load += lu

    semaphore_parts = []
    for flag, color, label in [("green","#27c98f","verde"), ("yellow","#f0a832","amarillo"), ("red","#e45a5a","rojo")]:
        if counts[flag] > 0:
            semaphore_parts.append(
                html.Span(f"{counts[flag]} {label}", style={"color": color, "fontWeight": "700"})
            )
    semaphore_children = []
    for i, part in enumerate(semaphore_parts):
        semaphore_children.append(part)
        if i < len(semaphore_parts) - 1:
            semaphore_children.append(html.Span(" / ", style={"opacity": 0.4}))

    wellness_avg_team = sum(wellness_vals) / len(wellness_vals) if wellness_vals else None
    wellness_str = f"{wellness_avg_team:.0f} / 100" if wellness_avg_team is not None else "Sin datos"
    load_str = f"{int(total_load)} UA" if total_load else "—"

    return html.Div(className="kpis", children=[
        html.Div(className="kpi", children=[
            html.Div("Estado del equipo", className="kpi-label"),
            html.Div(semaphore_children if semaphore_children else [html.Span("Sin datos")], className="kpi-value"),
            html.Div(f"{len(team_summary)} atletas en roster", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Bienestar promedio", className="kpi-label"),
            html.Div(wellness_str, className="kpi-value"),
            html.Div("Promedio del equipo esta semana", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Carga total del equipo", className="kpi-label"),
            html.Div(load_str, className="kpi-value"),
            html.Div("Suma RPE × min todos los atletas", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ])


def _team_load_chart(team_summary):
    """Gráfica horizontal de barras: carga semanal por atleta, coloreada por semáforo."""
    if not team_summary:
        return html.Div()

    names, loads, colors = [], [], []
    for a in reversed(team_summary):  # reversed → el primer atleta queda arriba
        w = a.get("weekly", {})
        lu = w.get("load_units") or 0
        flag = w.get("flag", "gray")
        names.append(_safe_str(a.get("name"), "Atleta"))
        loads.append(lu)
        colors.append(_FLAG_COLORS.get(flag, _FLAG_COLORS["gray"]))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=loads, y=names,
        orientation="h",
        marker=dict(color=colors, opacity=0.85),
        hovertemplate="%{y}: %{x} UA<extra></extra>",
        showlegend=False,
    ))
    _uc.apply_chart_style(fig)
    fig.update_layout(
        height=46 + len(names) * 52,
        margin=dict(l=0, r=16, t=8, b=24),
        xaxis=dict(title="Carga (UA)", gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(tickfont=dict(size=13)),
        bargap=0.35,
    )

    return html.Div(
        className="inner-card",
        style={"padding": "16px", "marginTop": "16px"},
        children=[
            html.H4("Carga semanal por atleta", className="card-title"),
            html.P("RPE × minutos acumulados esta semana. Color según semáforo de carga.", className="text-muted"),
            dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"marginTop": "10px"}),
        ]
    )


def _coach_priorities(team_summary):
    """Sección de prioridades: agrupa atletas por urgencia (rojo > amarillo > verde)."""
    if not team_summary:
        return html.Div()

    groups = {"red": [], "yellow": [], "green": [], "gray": []}
    for a in team_summary:
        flag = a.get("weekly", {}).get("flag", "gray")
        groups[flag].append(a)

    sections = []

    if groups["red"]:
        sections.append(_priority_group(
            "Requieren atención",
            "Carga o bienestar críticos. Revisión antes de la próxima sesión.",
            groups["red"], "#e45a5a",
        ))
    if groups["yellow"]:
        sections.append(_priority_group(
            "Vigilar",
            "Tendencia a sobrecarga o bienestar moderado. Seguimiento recomendado.",
            groups["yellow"], "#f0a832",
        ))
    if groups["green"]:
        sections.append(_priority_group(
            "En forma",
            "Carga y bienestar dentro del rango óptimo.",
            groups["green"], "#27c98f",
        ))
    if groups["gray"] and not groups["red"] and not groups["yellow"] and not groups["green"]:
        sections.append(html.Div(
            "Sin sesiones registradas esta semana.",
            style={"opacity": 0.5, "fontSize": "13px", "padding": "10px 0"},
        ))

    return html.Div(
        className="inner-card",
        style={"padding": "16px 20px", "marginTop": "16px"},
        children=[
            html.H4("Prioridades esta semana", className="card-title"),
            html.P("Clasificación por urgencia para orientar las decisiones de entrenamiento.", className="text-muted"),
            html.Div(sections, style={"marginTop": "14px"}),
        ]
    )


def _priority_group(title, subtitle, athletes, color):
    """Bloque de un grupo de prioridad dentro de la sección de prioridades."""
    rows = []
    for a in athletes:
        w = a.get("weekly", {})
        name = _safe_str(a.get("name"), "Atleta")
        load_str = f"{w.get('load_units')} UA" if w.get("load_units") is not None else "—"
        wv = w.get("wellness_avg")
        wellness_str = f"Bienestar {wv:.0f}" if wv is not None else "Sin bienestar"
        watch = ""
        try:
            import json as _json
            prof_raw = a.get("athlete_profile_json")
            if prof_raw:
                prof = _json.loads(prof_raw) if isinstance(prof_raw, str) else prof_raw
                watch = prof.get("watch_zone") or ""
        except Exception:
            pass

        detail_parts = [load_str, wellness_str]
        if watch:
            detail_parts.append(f"Vigilar: {watch}")

        rows.append(html.Div(
            style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                   "padding": "8px 0", "borderBottom": "1px solid #1c2330"},
            children=[
                html.Div([
                    html.Span("● ", style={"color": color, "fontSize": "11px"}),
                    html.Span(name, style={"fontWeight": "600", "fontSize": "14px", "color": "#e8ecf0"}),
                ]),
                html.Div(
                    " · ".join(detail_parts),
                    style={"fontSize": "12px", "color": "#8fa3bf"},
                ),
            ]
        ))

    return html.Div(
        style={"marginBottom": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "baseline", "gap": "8px", "marginBottom": "4px"},
                children=[
                    html.Span(title, style={"fontWeight": "700", "fontSize": "13px", "color": color}),
                    html.Span(subtitle, style={"fontSize": "12px", "opacity": 0.55}),
                ]
            ),
            html.Div(rows),
        ]
    )


def _team_cards_grid(athletes):
    if not athletes:
        return html.Div(
            "Todavía no tienes atletas en tu roster. Ve a Equipo para añadirlos.",
            className="inner-card",
            style={"opacity": 0.7, "marginTop": "16px", "padding": "20px", "fontSize": "14px"},
        )
    return html.Div(
        style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fill, minmax(260px, 1fr))",
            "gap": "14px",
            "marginTop": "14px",
        },
        children=[_athlete_card_coach(a) for a in athletes],
    )


def layout():
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or "no autenticado"

    if not uid:
        return html.Div([
            html.H2("Inicio"),
            html.P("Bienvenido a CombatIQ. Usa el menú para entrar a sesiones, análisis y wellbeing."),
            html.Div(className="kpis", children=[
                html.Div(className="kpi", children=[
                    html.Div("Estado", className="kpi-label"),
                    html.Div("Invitado", className="kpi-value"),
                    html.Div(className="kpi-ecg-line")
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Accesos", className="kpi-label"),
                    html.Div("Inicio • Sesiones • Análisis • Wellbeing", className="kpi-value", style={"fontSize": "16px"}),
                    html.Div(className="kpi-ecg-line")
                ]),
            ])
        ])

    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None

    user = db.get_user_by_id(uid_int) if uid_int else None
    if not user:
        return html.Div([
            html.H2("Inicio"),
            html.P("No se pudieron cargar los datos de tu perfil. Intenta volver a iniciar sesión."),
        ])

    name = _safe_str(user.get("name"), "Sin nombre")
    sport = _safe_str(user.get("sport"), "Sin deporte definido")
    created_at = user.get("created_at") or ""
    created_pretty = created_at[:10] if created_at else "-"

    try:
        athlete_profile = db.get_athlete_profile(uid_int) if hasattr(db, "get_athlete_profile") else _profile_defaults()
    except Exception:
        athlete_profile = _profile_defaults()

    last_wellness = "Sin registros"
    last_wellness_val = None
    latest_answers = {}
    try:
        qs = db.list_questionnaires(uid_int)
        if qs:
            q0 = qs[0]
            last_wellness_val = q0.get("wellness_score", None)
            ts = q0.get("ts") or ""
            ts_pretty = ts.replace("T", " ")[:16] if ts else ""
            if last_wellness_val is not None:
                last_wellness = f"{last_wellness_val:.0f} / 100 · {ts_pretty}"
            else:
                last_wellness = ts_pretty or "Sin registros"
            try:
                import json as _json
                raw = q0.get("answers_json") or "{}"
                latest_answers = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                latest_answers = {}
    except Exception:
        pass

    last_bpm = "Sin registros"
    last_hrv_detail = ""
    try:
        hrv = db.get_last_ecg_metrics(uid_int)
        if hrv:
            bpm = hrv.get("bpm", None)
            sdnn = hrv.get("sdnn", None)
            rmssd = hrv.get("rmssd", None)
            if bpm is not None:
                last_bpm = f"{bpm:.0f} bpm"
            parts = []
            if sdnn is not None:
                parts.append(f"SDNN {sdnn:.1f} ms")
            if rmssd is not None:
                parts.append(f"RMSSD {rmssd:.1f} ms")
            last_hrv_detail = " · ".join(parts)
    except Exception:
        pass

    weekly_summary = {}
    team_summary = []
    load_history = []
    try:
        if role == "deportista" and uid_int:
            weekly_summary = db.get_weekly_load_summary(uid_int) if hasattr(db, "get_weekly_load_summary") else {}
            load_history = db.get_load_history(uid_int) if hasattr(db, "get_load_history") else []
        elif role == "coach" and uid_int:
            team_summary = db.get_team_weekly_summary(uid_int) if hasattr(db, "get_team_weekly_summary") else []
    except Exception:
        pass

    if role == "deportista":
        readiness_value = _safe_str(f"{last_wellness_val:.0f} / 100" if last_wellness_val is not None else "Sin datos")
        profile_note = _profile_label(athlete_profile.get("profile_note"), "Sin nota de contexto todavía.")

        return html.Div([
            html.Div(className="profile-hero-grid", children=[
                html.Div(className="page-head profile-hero", children=[
                    html.Div(className="session-pill-row", children=[
                        html.Span(sport, className="session-pill"),
                        html.Span(_profile_label(athlete_profile.get("competitive_level"), "Perfil base"), className="session-pill session-pill--muted"),
                    ]),
                    html.H2("Mi perfil"),
                    html.P(
                        "Aquí puedes ver cómo llegas hoy, qué partes de tu perfil influyen en la lectura y editar solo cuando haga falta.",
                        className="text-muted",
                    ),
                ]),
                html.Div(className="card profile-focus-card", children=[
                    html.H4("Lo que más influye hoy", className="card-title"),
                    html.P("Este contexto ayuda a que tu sesión, análisis y wellbeing hablen el mismo idioma.", className="text-muted"),
                    html.Ul([
                        html.Li([html.Strong("Estado del día: "), readiness_value]),
                        html.Li([html.Strong("Estado actual: "), _profile_label(athlete_profile.get("current_status"), "Sin definir")]),
                        html.Li([html.Strong("Zona a vigilar: "), _profile_label(athlete_profile.get("watch_zone"), "Sin zona marcada")]),
                        html.Li([html.Strong("Competencia: "), _profile_label(athlete_profile.get("competition_proximity"), "Sin competencia cercana")]),
                    ], className="list-compact"),
                ]),
            ]),
            html.Div(className="kpis profile-kpis", children=[
                html.Div(className="kpi", children=[
                    html.Div("Perfil deportivo", className="kpi-label"),
                    html.Div(name, className="kpi-value"),
                    html.Div(f"Deporte: {sport}", className="kpi-sub"),
                    html.Div(f"Desde: {created_pretty}", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line")
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Estado del día", className="kpi-label"),
                    html.Div(readiness_value, className="kpi-value"),
                    html.Div(last_wellness, className="kpi-sub"),
                    html.Div(className="kpi-ecg-line")
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Análisis cardiovascular", className="kpi-label"),
                    html.Div(last_bpm, className="kpi-value"),
                    html.Div(last_hrv_detail or "Sube un ECG para ver tus métricas.", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line")
                ]),
            ]),
            html.Div(className="profile-main-grid", children=[
                html.Div(className="profile-stack", children=[
                    _competition_badge(athlete_profile),
                    _weekly_card_athlete(weekly_summary),
                    _recommendations_card(_build_recommendations(sport, last_wellness_val, weekly_summary, athlete_profile, latest_answers)),
                    _load_chart_card(load_history),
                    html.Div(className="profile-share-row", children=[
                        html.Button(
                            "Compartir esta semana",
                            id="btn-share-week",
                            className="btn btn-ghost",
                            style={"fontSize": "13px", "padding": "6px 16px"},
                        ),
                        dcc.Download(id="download-share-card"),
                    ]),
                ]),
                html.Div(className="profile-stack", children=[
                    html.Div(
                        className="inner-card profile-context-card",
                        style={"borderRadius": "14px", "padding": "16px"},
                        children=[
                            html.H4("Tu contexto deportivo", className="card-title"),
                            html.P("Estos datos ayudan a que la lectura del día y las recomendaciones se parezcan más a tu realidad.", className="text-muted"),
                            html.Div(className="profile-context-grid", children=[
                                _profile_grid_item("Nivel competitivo", athlete_profile.get("competitive_level")),
                                _profile_grid_item("Categoría / peso", athlete_profile.get("weight_category")),
                                _profile_grid_item("Lado dominante", athlete_profile.get("dominant_side")),
                                _profile_grid_item("Estado actual", athlete_profile.get("current_status")),
                                _profile_grid_item("Zona a vigilar", athlete_profile.get("watch_zone")),
                                _profile_grid_item("Cercanía a competencia", athlete_profile.get("competition_proximity")),
                            ]),
                            html.Div(className="profile-note", children=[
                                html.Strong("Nota de contexto: "),
                                html.Span(profile_note),
                            ]),
                        ],
                    ),
                    html.Details(
                        className="card collapsible-card profile-edit-card",
                        children=[
                            html.Summary(
                                className="collapsible-card__summary",
                                children=[
                                    html.Div(className="collapsible-card__head", children=[
                                        html.H4("Editar perfil deportivo", className="card-title"),
                                        html.P("Abre este bloque si quieres actualizar tu contexto sin llenar la pantalla de controles.", className="text-muted"),
                                    ]),
                                    html.Span("›", className="collapsible-card__chevron"),
                                ],
                            ),
                            html.Div(className="collapsible-card__body", children=[
                                html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "10px"}, children=[
                                    html.Div(className="filter-item", children=[
                                        html.Label("Nivel competitivo"),
                                        dcc.Dropdown(id="dash-competitive-level", value=athlete_profile.get("competitive_level"), clearable=False,
                                                     options=[
                                                         {"label": "Iniciación", "value": "Iniciación"},
                                                         {"label": "Intermedio", "value": "Intermedio"},
                                                         {"label": "Competitivo", "value": "Competitivo"},
                                                         {"label": "Alto rendimiento", "value": "Alto rendimiento"},
                                                     ]),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Categoría / peso"),
                                        dcc.Input(id="dash-weight-category", type="text", value=athlete_profile.get("weight_category"), placeholder="Ej. -63 kg", style={"width": "100%"}),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Lado dominante"),
                                        dcc.Dropdown(id="dash-dominant-side", value=athlete_profile.get("dominant_side"), clearable=False,
                                                     options=[
                                                         {"label": "Derecho", "value": "Derecho"},
                                                         {"label": "Izquierdo", "value": "Izquierdo"},
                                                         {"label": "Mixto", "value": "Mixto"},
                                                     ]),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Estado actual"),
                                        dcc.Dropdown(id="dash-current-status", value=athlete_profile.get("current_status"), clearable=False,
                                                     options=[
                                                         {"label": "Listo para apretar", "value": "Listo para apretar"},
                                                         {"label": "Listo con control", "value": "Listo con control"},
                                                         {"label": "Día para ajustar carga", "value": "Día para ajustar carga"},
                                                         {"label": "En vigilancia", "value": "En vigilancia"},
                                                     ]),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Zona a vigilar"),
                                        dcc.Input(id="dash-watch-zone", type="text", value=athlete_profile.get("watch_zone"), placeholder="Ej. tobillo derecho", style={"width": "100%"}),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Cercanía a competencia"),
                                        dcc.Dropdown(id="dash-competition-proximity", value=athlete_profile.get("competition_proximity"), clearable=False,
                                                     options=[
                                                         {"label": "Sin competencia cercana", "value": "Sin competencia cercana"},
                                                         {"label": "Próximas 6-8 semanas", "value": "Próximas 6-8 semanas"},
                                                         {"label": "Próximas 3-4 semanas", "value": "Próximas 3-4 semanas"},
                                                         {"label": "Semana competitiva", "value": "Semana competitiva"},
                                                     ]),
                                    ]),
                                ]),
                                html.Div(className="auth-field", style={"marginTop": "10px"}, children=[
                                    html.Label("Nota de contexto"),
                                    dcc.Textarea(id="dash-profile-note", value=athlete_profile.get("profile_note") or "", placeholder="Ej. priorizar pierna izquierda y controlar carga por cercanía a torneo.", style={"width": "100%", "minHeight": "88px"}),
                                ]),
                                html.Div(style={"marginTop": "14px", "display": "flex", "gap": "10px", "alignItems": "center", "flexWrap": "wrap"}, children=[
                                    html.Button("Guardar perfil", id="dash-save-profile", className="btn btn-primary"),
                                    html.Div(id="dash-profile-msg", className="text-muted"),
                                ]),
                            ]),
                        ],
                    ),
                    html.Div(className="card profile-links-card", children=[
                        html.H4("Accesos útiles", className="card-title"),
                        html.P("Cuando quieras seguir, aquí tienes las entradas que más sentido suelen tener después del perfil.", className="text-muted"),
                        html.Div(className="row-wrap-10 session-action-row", children=[
                            _quick_link("Ver sesiones", "/sesion", primary=True),
                            _quick_link("Ir a análisis", "/ecg"),
                            _quick_link("Responder wellbeing", "/cuestionario"),
                        ]),
                    ]),
                ]),
            ]),
        ], className="page-content profile-shell")

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2(f"Panel de {name}"),
            html.P(
                f"Vista de coach · {sport} · {len(team_summary)} atleta{'s' if len(team_summary) != 1 else ''} en roster.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider"),
        _team_kpis(team_summary),
        _coach_focus_today(team_summary),
        _coach_priorities(team_summary),
        _team_load_chart(team_summary),
        html.Div(
            className="card",
            style={"marginTop": "20px"},
            children=[
                html.H4("Detalle del equipo", className="card-title"),
                html.P("Carga acumulada (RPE × min) y bienestar promedio de los últimos 7 días.", className="text-muted"),
                _team_cards_grid(team_summary),
                _coach_alerts(team_summary),
            ]
        ),
        html.Div(className="filters-bar", style={"marginTop": "20px", "gap": "10px"}, children=[
            _quick_link("Ver equipo", "/usuarios", primary=True),
            _quick_link("Ir a análisis", "/ecg"),
            _quick_link("Wellbeing del equipo", "/cuestionario"),
        ]),
    ], className="page-content profile-shell")
