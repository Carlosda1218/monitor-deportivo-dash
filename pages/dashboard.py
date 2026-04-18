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
        className="profile-grid-item",
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
        className=f"athlete-week-card",
        style={"borderLeftColor": color},
        children=[
            html.Div(
                className="athlete-week-card__header",
                children=[
                    html.H4("Tu semana", className="card-title", style={"margin": 0}),
                    html.Span(
                        _FLAG_LABELS.get(flag, ""),
                        className="athlete-week-card__badge",
                        style={"background": color},
                    ),
                ]
            ),
            html.Div(
                className="athlete-week-stats",
                children=[
                    html.Div([
                        html.Div("Sesiones", className="kpi-label"),
                        html.Div(str(n_sessions), className="kpi-value"),
                        html.Div("últimos 7 días", className="kpi-sub"),
                    ], className="athlete-week-stat"),
                    html.Div([
                        html.Div("Carga total", className="kpi-label"),
                        html.Div(load_str, className="kpi-value"),
                        html.Div("RPE × min", className="kpi-sub"),
                    ], className="athlete-week-stat"),
                    html.Div([
                        html.Div("Bienestar prom.", className="kpi-label"),
                        html.Div(wellness_str, className="kpi-value"),
                        html.Div("últimos 7 días", className="kpi-sub"),
                    ], className="athlete-week-stat"),
                    html.Div([
                        html.Div("Tendencia", className="kpi-label"),
                        html.Div(
                            _TREND_ICONS.get(trend, "→"),
                            className="kpi-value",
                            style={"color": color},
                        ),
                        html.Div(_TREND_LABELS.get(trend, ""), className="kpi-sub"),
                    ], className="athlete-week-stat"),
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
        className=f"athlete-card-coach athlete-card-coach--{flag}",
        children=[
            html.Div(
                className="athlete-card-coach__header",
                children=[
                    html.Div([
                        html.Div(name,  className="athlete-card-coach__name"),
                        html.Div(sport, className="athlete-card-coach__sport"),
                    ]),
                    html.Span("●", className="athlete-card-coach__dot", style={"color": color}),
                ]
            ),
            html.Div(
                className="athlete-card-coach__stats",
                children=[
                    html.Div([
                        html.Div("Sesiones",   className="athlete-card-coach__stat-label"),
                        html.Div(str(n_sessions), className="athlete-card-coach__stat-value"),
                    ]),
                    html.Div([
                        html.Div("Carga (UA)", className="athlete-card-coach__stat-label"),
                        html.Div(load_str,     className="athlete-card-coach__stat-value"),
                    ]),
                    html.Div([
                        html.Div("Bienestar",  className="athlete-card-coach__stat-label"),
                        html.Div(
                            f"{wellness_str} {_TREND_ICONS.get(trend, '→')}",
                            className="athlete-card-coach__stat-value",
                            style={"color": color},
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

    _icons = {"info": "→", "warning": "!", "alert": "!!"}

    items = [
        html.Div(className=f"rec-item rec-item--{r.get('level','info')}", children=[
            html.Span(_icons.get(r.get("level", "info"), "→"), className="rec-item__icon"),
            html.Span(r["text"], className="rec-item__text"),
        ])
        for r in recs
    ]

    return html.Div(
        className="inner-card",
        style={"padding": "18px 20px", "marginTop": "14px"},
        children=[
            html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px", "marginBottom": "14px"}, children=[
                html.H4("Recomendaciones del día", className="card-title", style={"margin": 0}),
                html.Small("Basadas en tu bienestar, carga y contexto deportivo.", className="text-muted"),
            ]),
            html.Div(style={"display": "flex", "flexDirection": "column", "gap": "8px"}, children=items),
        ]
    )


def _parse_ts(ts):
    """Parsea timestamp ISO o YYYY-MM-DD, devuelve datetime o None."""
    if not ts:
        return None
    from datetime import datetime
    s = str(ts).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            trunc = s[:19] if fmt == "%Y-%m-%dT%H:%M:%S" else (s[:10] if fmt == "%Y-%m-%d" else s)
            return datetime.strptime(trunc, fmt)
        except ValueError:
            continue
    return None


def _coach_daily_snapshot(team_summary):
    from datetime import datetime

    snapshot = {
        "total": 0,
        "checkins_ready": 0,
        "checkins_pending": 0,
        "review_count": 0,
        "competition_week": 0,
        "priority_red": 0,
        "priority_yellow": 0,
        "ready_today": 0,
        "pending_names": [],
        "competition_names": [],
    }
    review_ids = set()
    now = datetime.utcnow()

    for athlete in (team_summary or []):
        athlete_id = athlete.get("id")
        athlete_name = _safe_str(athlete.get("name"), "Atleta")
        snapshot["total"] += 1

        weekly = athlete.get("weekly", {}) or {}
        flag = (weekly.get("flag") or "gray").strip().lower()
        if flag == "red":
            snapshot["priority_red"] += 1
            review_ids.add(athlete_id or athlete_name)
        elif flag == "yellow":
            snapshot["priority_yellow"] += 1

        try:
            athlete_profile = (
                db.get_athlete_profile(int(athlete_id))
                if athlete_id and hasattr(db, "get_athlete_profile")
                else _profile_defaults()
            )
        except Exception:
            athlete_profile = _profile_defaults()

        competition = (athlete_profile.get("competition_proximity") or "").strip()
        if competition == "Semana competitiva":
            snapshot["competition_week"] += 1
            snapshot["competition_names"].append(athlete_name)
            review_ids.add(athlete_id or athlete_name)

        try:
            qrows = db.list_questionnaires(int(athlete_id)) if athlete_id else []
        except Exception:
            qrows = []

        latest_q = qrows[0] if qrows else None
        latest_dt = _parse_ts((latest_q or {}).get("ts"))
        latest_score = (latest_q or {}).get("wellness_score")

        fresh_checkin = False
        if latest_q and latest_dt is not None:
            age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600.0)
            fresh_checkin = age_hours <= 36

        if fresh_checkin:
            snapshot["checkins_ready"] += 1
            try:
                score_num = float(latest_score)
            except Exception:
                score_num = None
            if score_num is not None and score_num >= 70 and flag != "red":
                snapshot["ready_today"] += 1
        else:
            snapshot["checkins_pending"] += 1
            snapshot["pending_names"].append(athlete_name)
            review_ids.add(athlete_id or athlete_name)

    snapshot["review_count"] = len(review_ids)
    return snapshot


def _coach_followup_card(snapshot):
    pending_names = snapshot.get("pending_names", [])
    competition_names = snapshot.get("competition_names", [])
    pending = snapshot.get("checkins_pending", 0)

    items = []
    if pending:
        pending_text = ", ".join(pending_names[:3])
        if pending > 3:
            pending_text += " y más"
        items.append(
            html.Li(
                [
                    html.Strong("Falta la entrada del día de: "),
                    f"{pending_text}.",
                ]
            )
        )
    else:
        items.append(
            html.Li("Todo el equipo tiene una lectura reciente para arrancar con más contexto.")
        )

    if competition_names:
        competition_text = ", ".join(competition_names[:3])
        if len(competition_names) > 3:
            competition_text += " y más"
        items.append(
            html.Li(
                [
                    html.Strong("Semana sensible: "),
                    f"{competition_text} está en semana competitiva.",
                ]
            )
        )

    if snapshot.get("priority_red", 0):
        red_count = snapshot.get("priority_red", 0)
        items.append(
            html.Li(
                [
                    html.Strong("Vigilancia alta: "),
                    f"{red_count} atleta{'s' if red_count != 1 else ''} llega{'n' if red_count != 1 else ''} con alerta roja semanal.",
                ]
            )
        )
    elif snapshot.get("priority_yellow", 0):
        yellow_count = snapshot.get("priority_yellow", 0)
        items.append(
            html.Li(
                [
                    html.Strong("Seguimiento fino: "),
                    f"{yellow_count} atleta{'s' if yellow_count != 1 else ''} merece{'n' if yellow_count != 1 else ''} control antes del bloque más exigente.",
                ]
            )
        )

    return html.Div(
        className="inner-card",
        style={"borderRadius": "14px", "padding": "16px 20px", "marginTop": "16px"},
        children=[
            html.H4("Qué no perder hoy", className="card-title"),
            html.P(
                "Esta lectura rápida te ayuda a no dejar fuera pendientes, contexto competitivo o señales de vigilancia.",
                className="text-muted",
            ),
            html.Ul(items, className="list-compact", style={"marginTop": "12px"}),
        ],
    )


def _coach_profile_layout_v3(name, sport, created_pretty, team_summary, coach_id):
    athlete_count = len(team_summary or [])
    coach_snapshot = _coach_daily_snapshot(team_summary)

    try:
        teams = db.list_teams(int(coach_id)) or []
    except Exception:
        teams = []

    team_count = len(teams)
    focus_sport = sport or "Deporte de combate"
    checkins_ready = coach_snapshot.get("checkins_ready", 0)
    total = coach_snapshot.get("total", 0)
    review_count = coach_snapshot.get("review_count", 0)
    competition_week = coach_snapshot.get("competition_week", 0)

    return html.Div([
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(focus_sport, className="session-pill"),
                    html.Span("Coach", className="session-pill session-pill--muted"),
                ]),
                html.H2("Mi perfil"),
                html.P(
                    "Aquí ves tu rol, tu deporte y el estado general del equipo antes de pasar a trabajar.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Lo esencial de tu perfil", className="card-title"),
                html.P(
                    "Tienes a mano tu rol, tu deporte foco y una lectura rápida del equipo.",
                    className="text-muted",
                ),
                html.Ul([
                    html.Li([html.Strong("Rol: "), "Coach principal"]),
                    html.Li([html.Strong("Deporte foco: "), focus_sport]),
                    html.Li([html.Strong("Plantilla actual: "), f"{athlete_count} atleta{'s' if athlete_count != 1 else ''}"]),
                    html.Li([html.Strong("Alta en la plataforma: "), created_pretty]),
                ], className="list-compact"),
            ]),
        ]),
        html.Div(className="kpis profile-kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Equipo a cargo", className="kpi-label"),
                html.Div(str(athlete_count), className="kpi-value"),
                html.Div(f"{team_count} equipo{'s' if team_count != 1 else ''} activo{'s' if team_count != 1 else ''}", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Lecturas listas", className="kpi-label"),
                html.Div(f"{checkins_ready} / {total}" if total else "Sin datos", className="kpi-value"),
                html.Div("Atletas con contexto reciente para decidir mejor hoy", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Atención prioritaria", className="kpi-label"),
                html.Div(str(review_count) if total else "Sin datos", className="kpi-value"),
                html.Div(
                    f"{competition_week} en semana competitiva" if competition_week else "Sin casos competitivos urgentes ahora mismo",
                    className="kpi-sub",
                ),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),
        html.Div(className="profile-main-grid", children=[
            html.Div(className="profile-stack", children=[
                _coach_profile_fold(
                    "Resumen del perfil",
                    "Consulta aquí tu rol, deporte y estructura actual.",
                    [
                        html.Div(className="profile-context-grid", children=[
                            _profile_grid_item("Nombre visible", name),
                            _profile_grid_item("Rol", "Coach"),
                            _profile_grid_item("Deporte principal", focus_sport),
                            _profile_grid_item("Plantilla actual", f"{athlete_count} atleta{'s' if athlete_count != 1 else ''}"),
                            _profile_grid_item("Equipos activos", str(team_count)),
                            _profile_grid_item("Casos a revisar hoy", str(review_count) if total else "0"),
                        ]),
                        html.Div(className="profile-note", children=[
                            html.Strong("Lectura rápida: "),
                            html.Span(
                                "Desde aquí puedes pasar al panel, la jornada o el estado del equipo según lo que necesites revisar."
                            ),
                        ]),
                    ],
                    open_by_default=True,
                ),
                _coach_profile_fold(
                    "Qué revisar hoy",
                    "Aquí puedes ver pendientes y puntos que conviene vigilar hoy.",
                    [_coach_followup_card(coach_snapshot)],
                    open_by_default=False,
                ),
            ]),
            html.Div(className="profile-stack", children=[
                _coach_profile_fold(
                    "Qué puedes revisar ahora",
                    "Desde aquí puedes saltar rápido a la vista que te haga falta hoy.",
                    [
                        html.Ul([
                            html.Li("Entra a Panel de equipo cuando quieras una lectura global del grupo."),
                            html.Li("Usa Mi jornada para ordenar el día y decidir por dónde empezar."),
                            html.Li("Abre Estado del equipo si necesitas bajar ya al detalle por atleta."),
                            html.Li("Pasa a Análisis solo cuando haga falta confirmar señales y respuesta al esfuerzo."),
                        ], className="list-compact"),
                    ],
                    open_by_default=False,
                ),
                html.Div(className="card profile-links-card", children=[
                    html.H4("Accesos útiles", className="card-title"),
                    html.P(
                        "Desde aquí entras directo a lo que más vas a consultar durante el día.",
                        className="text-muted",
                    ),
                    html.Div(className="row-wrap-10 session-action-row", children=[
                        _quick_link("Abrir panel de equipo", "/", primary=True),
                        _quick_link("Ir a mi jornada", "/sesion"),
                        _quick_link("Ver estado del equipo", "/usuarios"),
                        _quick_link("Ir a análisis", "/ecg"),
                    ]),
                ]),
            ]),
        ]),
    ], className="page-content profile-shell")


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
                                        html.P("Actualiza aquí tu perfil deportivo cuando cambie tu contexto.", className="text-muted"),
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

    if role == "coach":
        return _coach_profile_layout_v3(name, sport, created_pretty, team_summary, uid_int)

    return html.Div([
        html.H2("Panel"),
        html.P("Rol no reconocido. Vuelve a iniciar sesión.", className="text-muted"),
    ], className="page-content")

