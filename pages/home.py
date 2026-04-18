import datetime
from collections import defaultdict

import plotly.graph_objects as go
from dash import dcc, html
from flask import session

import db
import ui_charts as _uc


MONTHS_SHORT = [
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
]


def _greeting():
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "Buenos días"
    if hour < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _date_str():
    days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    months = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    now = datetime.datetime.now()
    return f"{days[now.weekday()].capitalize()}, {now.day} de {months[now.month - 1]} de {now.year}"


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("T", " ")[:19])
    except Exception:
        return None


def _format_dt(dt):
    if not dt:
        return "Sin registros"
    return f"{dt.day:02d} {MONTHS_SHORT[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')}"


def _questionnaire_rows(user_id):
    try:
        return db.list_questionnaires(int(user_id)) or []
    except Exception:
        return []


def _last_wellness(user_id):
    rows = _questionnaire_rows(user_id)
    latest = None
    for row in rows:
        dt = _parse_ts(row.get("ts") or "")
        if not dt:
            continue
        if latest is None or dt > latest["dt"]:
            latest = {
                "dt": dt,
                "score": row.get("wellness_score"),
            }
    if not latest:
        return {
            "value": "Sin datos",
            "sub": "Aún no hay cuestionarios registrados.",
            "detail": "Completa tu check-in para empezar a ver tu tendencia.",
        }
    score = latest["score"]
    value = f"{float(score):.0f}/100" if score is not None else "Sin dato"
    return {
        "value": value,
        "sub": "Último check-in",
        "detail": f"Registrado el {_format_dt(latest['dt'])}.",
    }


def _count_checkins_7d(user_id):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    total = 0
    for row in _questionnaire_rows(user_id):
        dt = _parse_ts(row.get("ts") or "")
        if dt and dt >= cutoff:
            total += 1
    return total


def _last_ecg(user_id):
    try:
        hrv = db.get_last_ecg_metrics(int(user_id))
    except Exception:
        hrv = None

    if not hrv:
        return {
            "value": "Sin datos",
            "sub": "Último ECG",
            "detail": "Sube una señal para ver tu referencia cardiovascular.",
        }

    bpm = hrv.get("bpm")
    sdnn = hrv.get("sdnn")
    rmssd = hrv.get("rmssd")
    value = f"{float(bpm):.0f} bpm" if bpm is not None else "Sin dato"
    parts = []
    if sdnn is not None:
        parts.append(f"SDNN {float(sdnn):.0f} ms")
    if rmssd is not None:
        parts.append(f"RMSSD {float(rmssd):.0f} ms")
    return {
        "value": value,
        "sub": "Último ECG",
        "detail": " - ".join(parts) if parts else "Sin detalle disponible.",
    }


def _athlete_trend_fig(user_id):
    pts = []
    for row in _questionnaire_rows(user_id):
        dt = _parse_ts(row.get("ts") or "")
        score = row.get("wellness_score")
        if dt is None or score is None:
            continue
        pts.append((dt, float(score)))

    pts = sorted(pts, key=lambda item: item[0])[-14:]
    if not pts:
        return None

    x = [f"{dt.day:02d} {MONTHS_SHORT[dt.month - 1]}" for dt, _ in pts]
    y = [score for _, score in pts]

    fig = go.Figure()
    fig.add_trace(_uc.make_area_trace(x, y, "Bienestar", _uc.PS_PALETTE[0], fill_opacity=0.14))
    _uc.apply_chart_style(fig, title="Tendencia de bienestar", height=260)
    fig.update_layout(showlegend=False, margin=dict(l=16, r=10, t=44, b=24))
    fig.update_yaxes(range=[0, 100], title=None)
    fig.update_xaxes(title=None)
    return fig


def _athlete_context_note(user_id):
    try:
        coach = db.get_user_coach(int(user_id))
    except Exception:
        coach = None
    if not coach:
        return "Aún no tienes coach asignado. Puedes seguir tu estado del día desde aquí y completar tu check-in."
    coach_name = coach.get("name") or "Tu coach"
    sport = coach.get("sport") or "tu equipo"
    return f"Coach asignado: {coach_name}. Referencia principal: {sport}."


def _coach_team_summary(coach_id):
    try:
        athletes = db.list_athletes_for_coach(int(coach_id)) or []
    except Exception:
        athletes = []

    try:
        teams = db.list_teams(int(coach_id)) or []
    except Exception:
        teams = []

    today = datetime.datetime.utcnow().date()
    start = today - datetime.timedelta(days=6)
    counts_by_day = defaultdict(int)
    athletes_today = set()
    last_entry = None

    for athlete in athletes:
        athlete_id = athlete.get("id")
        if athlete_id is None:
            continue
        for row in _questionnaire_rows(athlete_id):
            dt = _parse_ts(row.get("ts") or "")
            if not dt:
                continue
            day = dt.date()
            if start <= day <= today:
                counts_by_day[day] += 1
                if day == today:
                    athletes_today.add(athlete_id)
            if last_entry is None or dt > last_entry["dt"]:
                last_entry = {
                    "dt": dt,
                    "name": athlete.get("name") or "Atleta",
                    "score": row.get("wellness_score"),
                }

    days = [start + datetime.timedelta(days=offset) for offset in range(7)]
    labels = [f"{day.day:02d} {MONTHS_SHORT[day.month - 1]}" for day in days]
    values = [counts_by_day.get(day, 0) for day in days]

    fig = None
    if any(values):
        fig = go.Figure()
        fig.add_trace(_uc.make_bar_trace(labels, values, "Check-ins", _uc.PS_PALETTE[0], opacity=0.84))
        _uc.apply_chart_style(fig, title="Actividad de cuestionarios", height=260)
        fig.update_layout(showlegend=False, margin=dict(l=16, r=10, t=44, b=24))
        fig.update_xaxes(title=None)
        fig.update_yaxes(title=None)

    if last_entry:
        score = last_entry["score"]
        score_txt = f"{float(score):.0f}/100" if score is not None else "sin puntuación"
        detail = f"Último check-in: {last_entry['name']} con {score_txt}, {_format_dt(last_entry['dt'])}."
    else:
        detail = "Todavía no hay cuestionarios recientes del equipo."

    return {
        "athletes": len(athletes),
        "teams": len(teams),
        "week_total": sum(values),
        "today_total": len(athletes_today),
        "detail": detail,
        "fig": fig,
    }


def _tool_tile(title, href, icon):
    return dcc.Link(
        html.Div(
            className="tile-card tile-card--compact",
            children=[
                html.Img(src=f"/assets/icons/{icon}", className="tile-icon"),
                html.Div(title, className="tile-title"),
            ],
        ),
        href=href,
        className="tile-link",
    )


def _flow_item(step, title, desc, href):
    return dcc.Link(
        html.Div(
            className="home-flow__item",
            children=[
                html.Div(step, className="home-flow__num"),
                html.Div(
                    className="home-flow__body",
                    children=[
                        html.Div(title, className="home-flow__item-title"),
                        html.Div(desc, className="home-flow__item-copy"),
                    ],
                ),
            ],
        ),
        href=href,
        className="home-flow__link",
    )


def _tool_group(title, subtitle, items):
    return html.Div(
        className="tiles-group",
        children=[
            html.Div(
                className="tiles-group__head",
                children=[
                    html.Div(title, className="tiles-group__title"),
                    html.P(subtitle, className="tiles-group__copy"),
                ],
            ),
            html.Div([_tool_tile(*item) for item in items], className="grid-secondary"),
        ],
    )


def _summary_kpi(label, value, sub):
    return html.Div(
        className="home-kpi",
        children=[
            html.Div(label, className="home-kpi__label"),
            html.Div(value, className="home-kpi__value"),
            html.Div(sub, className="home-kpi__sub"),
        ],
    )


def _summary_card(title, subtitle, metrics, note):
    return html.Div(
        className="card home-panel-card",
        children=[
            html.Div("Lectura útil", className="home-panel-card__eyebrow"),
            html.H3(title, className="home-panel-card__title"),
            html.P(subtitle, className="home-panel-card__copy"),
            html.Div(metrics, className="home-kpi-grid"),
            html.Div(note, className="home-panel-note"),
        ],
    )


def _chart_card(title, subtitle, figure, empty_text):
    return html.Div(
        className="card home-chart-card",
        children=[
            html.Div(
                className="home-chart-card__head",
                children=[
                    html.Div(title, className="home-chart-card__title"),
                    html.Div(subtitle, className="home-chart-card__copy"),
                ],
            ),
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                className="home-chart-card__graph",
                style={"height": "260px", "width": "100%"},
            ) if figure else html.Div(empty_text, className="home-chart-card__empty"),
        ],
    )


def layout():
    name = session.get("name") or ""
    sport = session.get("sport") or ""
    role = session.get("role") or ""
    user_id = session.get("user_id")

    if not user_id:
        return html.Div(
            className="home-shell",
            children=[
                html.Div(
                    className="card home-panel-card",
                    children=[
                        html.Div("Inicio", className="home-panel-card__eyebrow"),
                        html.H3("Bienvenido a CombatIQ", className="home-panel-card__title"),
                        html.P(
                            "Inicia sesión para ver tu panel con una lectura clara de cuestionarios, señales y contexto deportivo.",
                            className="home-panel-card__copy",
                        ),
                        html.Div(
                            className="home-inline-actions",
                            children=[
                                dcc.Link(html.Button("Iniciar sesión", className="btn btn-primary"), href="/login"),
                                dcc.Link(html.Button("Crear cuenta", className="btn btn-ghost"), href="/registro"),
                            ],
                        ),
                    ],
                )
            ],
        )

    first_name = str(name).split()[0] if name else "Atleta"
    greeting = _greeting()
    meta_parts = [_date_str()]
    if sport:
        meta_parts.append(sport)
    if role:
        meta_parts.append("Coach" if role == "coach" else "Deportista")

    if role == "coach":
        team = _coach_team_summary(user_id)
        hero_copy = (
            "Aquí puedes ver cómo llega tu equipo hoy y qué te conviene revisar primero antes de pasar a otras vistas."
        )
        hero_badges = ["Coach", sport or "Deporte de combate"]
        flow_items = [
            _flow_item("1", "Revisa el foco del día", "Detecta quién llega con menos actividad o necesita seguimiento.", "/dashboard"),
            _flow_item("2", "Abre la sesión", "Define contexto, objetivo y estructura del trabajo del equipo.", "/sesion"),
            _flow_item("3", "Baja a detalle si hace falta", "Usa análisis e histórico solo cuando aporten a la decisión.", "/comparar"),
        ]
        summary_card = _summary_card(
            "Así llega tu equipo hoy",
            "Una lectura rápida para revisar actividad reciente y no empezar el día a ciegas.",
            [
                _summary_kpi("Deportistas", str(team["athletes"]), "En tu roster"),
                _summary_kpi("Check-ins hoy", str(team["today_total"]), "Atletas con registro"),
                _summary_kpi("Últimos 7 días", str(team["week_total"]), "Cuestionarios del equipo"),
                _summary_kpi("Equipos", str(team["teams"]), "Grupos activos"),
            ],
            team["detail"],
        )
        chart_card = _chart_card(
            "Actividad de cuestionarios",
            "Te ayuda a ver si el equipo viene registrando su estado con continuidad.",
            team["fig"],
            "Aún no hay actividad suficiente para mostrar una tendencia del equipo.",
        )
        tool_groups = [
            _tool_group(
                "Gestión del equipo",
                "Desde aquí puedes entrar rápido a las vistas que más usarás con tus atletas.",
                [
                    ("Equipo", "/usuarios", "team.svg"),
                    ("Perfil de atleta", "/deportista", "profile.svg"),
                    ("Comunicados", "/anuncios", "history.svg"),
                ],
            ),
            _tool_group(
                "Seguimiento",
                "Si necesitas confirmar algo con más detalle, puedes entrar desde aquí.",
                [
                    ("Análisis de sesión", "/ecg", "signals.svg"),
                    ("Histórico y comparación", "/comparar", "compare.svg"),
                    ("Sensores", "/sensores", "sensors.svg"),
                ],
            ),
        ]
    else:
        wellness = _last_wellness(user_id)
        ecg = _last_ecg(user_id)
        checkins_7d = _count_checkins_7d(user_id)
        hero_copy = (
            "Aquí puedes ver cómo llegas hoy, qué registros tienes recientes y qué te conviene revisar primero."
        )
        hero_badges = ["Deportista", sport or "Preparación diaria"]
        flow_items = [
            _flow_item("1", "Mira tu estado", "Revisa cómo llegas hoy y cuándo fue tu último check-in.", "/dashboard"),
            _flow_item("2", "Abre tu sesión", "Mantén claro el objetivo del día antes de pasar al análisis.", "/sesion"),
            _flow_item("3", "Compara cuando lo necesites", "Usa histórico para confirmar tendencias, no como primer paso.", "/comparar"),
        ]
        summary_card = _summary_card(
            "Estado de hoy",
            "Una vista rápida para entender tu estado del día sin tener que ir pantalla por pantalla.",
            [
                _summary_kpi("Último check-in", wellness["value"], wellness["sub"]),
                _summary_kpi("Últimos 7 días", str(checkins_7d), "Cuestionarios respondidos"),
                _summary_kpi("Último ECG", ecg["value"], ecg["sub"]),
            ],
            f"{wellness['detail']} {ecg['detail']} {_athlete_context_note(user_id)}",
        )
        chart_card = _chart_card(
            "Tendencia de bienestar",
            "Sirve para ver si vienes estable o si tu estado ha cambiado en los últimos días.",
            _athlete_trend_fig(user_id),
            "Todavía no hay suficientes cuestionarios para mostrar una tendencia útil.",
        )
        tool_groups = [
            _tool_group(
                "Seguimiento",
                "Aquí tienes a mano las vistas que más te ayudan a seguir tu evolución.",
                [
                    ("Análisis de sesión", "/ecg", "signals.svg"),
                    ("Histórico y comparación", "/comparar", "compare.svg"),
                    ("Histórico de wellbeing", "/historico", "history.svg"),
                ],
            ),
            _tool_group(
                "Apoyo del día",
                "Puedes usar estas herramientas cuando quieras llevar mejor tu control diario.",
                [
                    ("Peso", "/peso", "weight.svg"),
                    ("Nutrición", "/nutricion", "nutrition.svg"),
                    ("Contacto con coach", "/contacto", "team.svg"),
                ],
            ),
        ]

    return html.Div(
        className="home-shell",
        children=[
            html.Div(
                className="card home-hero",
                children=[
                    html.Div(
                        className="home-hero__main",
                        children=[
                            html.Div(
                                [html.Span(label, className="home-badge") for label in hero_badges],
                                className="home-badges",
                            ),
                            html.Div(
                                className="home-header",
                                children=[
                                    html.H1(f"{greeting}, {first_name}", className="home-greeting"),
                                    html.P(" - ".join(meta_parts), className="home-meta"),
                                ],
                            ),
                            html.P(hero_copy, className="home-lead"),
                        ],
                    ),
                    html.Div(
                        className="home-hero__side",
                        children=[
                            html.Div("Por dónde empezar", className="home-flow__title"),
                            html.Div(
                                "Si no sabes qué mirar primero, este orden suele funcionar bien.",
                                className="home-flow__intro",
                            ),
                            html.Div(flow_items, className="home-flow"),
                        ],
                    ),
                ],
            ),
            html.Div(className="home-overview-grid", children=[summary_card, chart_card]),
            html.Div(
                className="tiles-section",
                children=[
                    html.Div(
                        className="tiles-section__head",
                        children=[
                            html.Div(
                                [
                                    html.P("Herramientas frecuentes", className="tiles-section-label"),
                                    html.P(
                                        "El menú lateral sigue siendo la navegación principal. Aquí solo dejamos algunos accesos útiles para entrar más rápido.",
                                        className="tiles-section-copy",
                                    ),
                                ]
                            )
                        ],
                    ),
                    html.Div(tool_groups, className="tiles-group-grid"),
                ],
            ),
        ],
    )
