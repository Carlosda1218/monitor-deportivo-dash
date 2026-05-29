import datetime
from collections import defaultdict

import plotly.graph_objects as go
from dash import dcc, html
from flask import session

import db
import ui_charts as _uc
import questionnaires as _Q
from questionnaires import norm_sport as _norm_sport


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


def _questionnaire_rows(user_id, _preloaded=None):
    if _preloaded is not None:
        return _preloaded
    try:
        return db.list_questionnaires(int(user_id)) or []
    except Exception:
        return []


def _calc_streak(user_id, _rows=None) -> dict:
    """Calcula racha actual y mejor racha de check-ins consecutivos."""
    rows = _questionnaire_rows(user_id, _rows)
    dates = sorted(set(
        (r.get("ts") or "")[:10]
        for r in rows if (r.get("ts") or "")[:10]
    ), reverse=True)
    if not dates:
        return {"current": 0, "best": 0, "today": False}

    today = datetime.datetime.utcnow().date()
    streak = 0
    for i, d in enumerate(dates):
        try:
            expected = today - datetime.timedelta(days=i)
            if datetime.datetime.strptime(d, "%Y-%m-%d").date() == expected:
                streak += 1
            else:
                break
        except Exception:
            break

    # Mejor racha histórica
    best = 0
    run = 0
    for i in range(len(dates)):
        if i == 0:
            run = 1
        else:
            try:
                d0 = datetime.datetime.strptime(dates[i - 1], "%Y-%m-%d").date()
                d1 = datetime.datetime.strptime(dates[i], "%Y-%m-%d").date()
                if (d0 - d1).days == 1:
                    run += 1
                else:
                    run = 1
            except Exception:
                run = 1
        best = max(best, run)

    today_done = bool(dates and dates[0] == today.isoformat())
    return {"current": streak, "best": best, "today": today_done}


def _streak_widget(streak: dict) -> html.Div:
    """Tarjeta de racha para el home del atleta."""
    cur = streak["current"]
    best = streak["best"]
    today_done = streak["today"]

    if cur >= 30:
        emoji, color, msg = "🔥", "var(--neon)", f"¡Racha legendaria! {cur} días seguidos."
    elif cur >= 14:
        emoji, color, msg = "🔥", "var(--neon)", f"¡Racha increíble! {cur} días sin fallar."
    elif cur >= 7:
        emoji, color, msg = "🔥", "#f0a832", f"Una semana seguida. ¡Sigue así!"
    elif cur >= 3:
        emoji, color, msg = "🔥", "#f0a832", f"{cur} días consecutivos. Buen ritmo."
    elif cur == 1:
        emoji, color, msg = "✓", "#2fb7c4", "Hoy ya está. Vuelve mañana para extender la racha."
    else:
        emoji, color, msg = "○", "var(--muted)", "Empieza el check-in de hoy para activar tu racha."

    # Próximo hito
    milestones = [3, 7, 14, 21, 30, 60, 90]
    next_milestone = next((m for m in milestones if m > cur), None)
    milestone_txt = f"{next_milestone - cur} días para el hito de {next_milestone} 🏆" if next_milestone else "¡Nivel máximo!"

    return html.Div(className="streak-widget", children=[
        html.Div(className="streak-widget__left", children=[
            html.Span(emoji, className="streak-fire"),
            html.Div(className="streak-nums", children=[
                html.Span(str(cur), className="streak-cur", style={"color": color}),
                html.Span(" días", className="streak-unit"),
            ]),
        ]),
        html.Div(className="streak-widget__right", children=[
            html.P(msg, className="streak-msg"),
            html.P(milestone_txt, className="streak-milestone"),
            html.P(f"Mejor racha: {best} días", className="streak-best"),
        ]),
    ])


def _last_wellness(user_id, _rows=None):
    rows = _questionnaire_rows(user_id, _rows)
    latest = None
    for row in rows:
        dt = _parse_ts(row.get("ts") or "")
        if not dt:
            continue
        if latest is None or dt > latest["dt"]:
            latest = {
                "dt": dt,
                "score": row.get("wellness_score"),
                "row": row,
            }
    if not latest:
        return {
            "value": "Sin datos",
            "sub": "Aún no hay cuestionarios registrados.",
            "detail": "Completa tu check-in para empezar a ver tu tendencia.",
            "today_row": None,
        }
    score = latest["score"]
    value = f"{float(score):.0f}/100" if score is not None else "Sin dato"

    today = datetime.datetime.utcnow().date()
    today_row = latest["row"] if latest["dt"].date() == today else None

    return {
        "value": value,
        "sub": "Último check-in",
        "detail": f"Registrado el {_format_dt(latest['dt'])}.",
        "today_row": today_row,
        "latest_score": float(score) if score is not None else None,
    }


def _home_rec_banner(today_row, score, sport):
    """
    Miniatura de la recomendación del día en el home.
    Solo aparece si el atleta ya hizo el check-in hoy.
    """
    if today_row is None or score is None:
        return dcc.Link(
            html.Div(
                className="home-rec-banner home-rec-banner--pending",
                children=[
                    html.Span("○", className="home-rec-banner__icon"),
                    html.Div([
                        html.Div("Check-in pendiente", className="home-rec-banner__label"),
                        html.Div(
                            "Completa tu check-in del día para ver qué tipo de sesión te conviene hoy.",
                            className="home-rec-banner__text",
                        ),
                    ]),
                    html.Span("→", className="home-rec-banner__arrow"),
                ],
            ),
            href="/cuestionario",
            className="home-rec-banner__link",
        )

    sport_key = _norm_sport(sport)

    if score >= 80:
        color, icon = "var(--neon)", "▲"
        label = "Listo para exigencia alta"
        if sport_key == "taekwondo":
            text = "Kyorugi con peto y contacto — tienes piernas y explosividad para sostener rondas a ritmo de competición."
        elif sport_key == "boxeo":
            text = "Saco de potencia y sparring técnico — buen día para medir timing bajo carga real."
        else:
            text = "Sesión de alta intensidad viable — buen momento para exigirte al máximo."
    elif score >= 65:
        color, icon = "#f0a832", "●"
        label = "Intensidad controlada"
        if sport_key == "taekwondo":
            text = "Patada técnica en distancia — dollyo y bandal con precisión, sin contacto pleno hoy."
        elif sport_key == "boxeo":
            text = "Sombra técnica y saco al 75% — buena sesión para trabajar guardia y esquivas."
        else:
            text = "Sesión técnica a ritmo medio — calidad de movimiento sobre carga."
    elif score >= 50:
        color, icon = "#e45a5a", "▼"
        label = "Ajusta la carga hoy"
        if sport_key == "taekwondo":
            text = "Sin contacto en peto — trabajo de Poomsae o pateo en peteca, cuida cadera y rodilla."
        elif sport_key == "boxeo":
            text = "Sombra técnica lenta, sin saco de potencia — cuida tus muñecas y hombros."
        else:
            text = "Reduce el volumen al 60-70% — prioriza la técnica y el descanso activo."
    else:
        color, icon = "var(--punch)", "⚠"
        label = "Recuperación prioritaria"
        if sport_key == "taekwondo":
            text = "Sin tatami hoy — movilidad de cadera, estiramiento de isquiosurales y habla con tu coach antes de patear."
        elif sport_key == "boxeo":
            text = "Sin guantes hoy — movilidad articular y recuperación activa."
        else:
            text = "Recuperación activa — no fuerces el entrenamiento hoy."

    return html.Div(
        className="home-rec-banner",
        style={"borderLeftColor": color},
        children=[
            html.Span(icon, className="home-rec-banner__icon", style={"color": color}),
            html.Div([
                html.Div(label, className="home-rec-banner__label", style={"color": color}),
                html.Div(text, className="home-rec-banner__text"),
            ]),
            html.Div(
                f"{score:.0f}/100",
                className="home-rec-banner__score",
                style={"color": color},
            ),
        ],
    )


def _count_checkins_7d(user_id, _rows=None):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    total = 0
    for row in _questionnaire_rows(user_id, _rows):
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


def _athlete_trend_fig(user_id, _rows=None):
    pts = []
    for row in _questionnaire_rows(user_id, _rows):
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


def _coach_team_summary(coach_id, sport=None):
    try:
        athletes = db.list_roster_for_coach(int(coach_id), sport=sport) or []
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

    # Single bulk query instead of one query per athlete
    athlete_ids = [a["id"] for a in athletes if a.get("id") is not None]
    name_map = {a["id"]: (a.get("name") or "Atleta") for a in athletes if a.get("id") is not None}
    try:
        bulk_qs = db.list_questionnaires_bulk(athlete_ids)
    except Exception:
        bulk_qs = {}

    for athlete_id in athlete_ids:
        for row in bulk_qs.get(athlete_id, []):
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
                    "name": name_map.get(athlete_id, "Atleta"),
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
        meta_parts.append("Coach" if role == "coach" else ("Admin" if role == "admin" else "Deportista"))

    _rec_banner = None  # populated only for athletes below

    if role == "coach":
        team = _coach_team_summary(user_id, sport=sport)
        _sport_key = _norm_sport(sport)
        if _sport_key == "taekwondo":
            hero_copy = "Revisa quién llega con piernas para Kyorugi y quién necesita técnica controlada hoy."
            flow_items = [
                _flow_item("1", "Revisa el foco del día", "Detecta quién llega sin explosividad, con molestias de cadera o rodilla, o con baja recuperación.", "/usuarios"),
                _flow_item("2", "Abre la sesión", "Define si toca distancia y bandal, entrada-salida con contraataque, o simulación de combate con peto.", "/sesion"),
                _flow_item("3", "Baja a detalle si hace falta", "Usa análisis biomecánico (cámara y velocidad de pateo) e histórico para decisiones tácticas.", "/comparar"),
            ]
        elif _sport_key == "boxeo":
            hero_copy = "Revisa quién llega con manos libres y si el equipo aguanta otro bloque de alta intensidad."
            flow_items = [
                _flow_item("1", "Revisa el foco del día", "Detecta quién llega con molestias de guardia o con carga acumulada alta.", "/usuarios"),
                _flow_item("2", "Abre la sesión", "Define si hoy toca saco, sombra técnica o trabajo más controlado.", "/sesion"),
                _flow_item("3", "Baja a detalle si hace falta", "Usa análisis e histórico solo cuando aporten a la decisión táctica.", "/comparar"),
            ]
        else:
            hero_copy = "Aquí puedes ver cómo llega tu equipo hoy y qué te conviene revisar primero antes de pasar a otras vistas."
            flow_items = [
                _flow_item("1", "Revisa el foco del día", "Detecta quién llega con menos actividad o necesita seguimiento.", "/usuarios"),
                _flow_item("2", "Abre la sesión", "Define contexto, objetivo y estructura del trabajo del equipo.", "/sesion"),
                _flow_item("3", "Baja a detalle si hace falta", "Usa análisis e histórico solo cuando aporten a la decisión.", "/comparar"),
            ]
        hero_badges = ["Coach", sport or "Deporte de combate"]
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
                    ("Señales ECG / IMU", "/ecg", "signals.svg"),
                    ("Comparar rendimiento", "/comparar", "compare.svg"),
                    ("Historial de combates", "/sesiones", "history.svg"),
                ],
            ),
        ]
    elif role == "admin":
        _rec_banner = None
        _streak_card = None
        hero_copy = "Vista ejecutiva de la plataforma: usuarios registrados, actividad reciente y estado general del sistema."
        flow_items = [
            _flow_item("1", "Métricas de plataforma", "DAU, WAU, engagement, distribución por deporte y bienestar general.", "/metricas"),
            _flow_item("2", "Gestión de usuarios", "Alta de deportistas, asignación de coaches y roles.", "/usuarios"),
            _flow_item("3", "Seguimiento de equipo", "Accede al dashboard de seguimiento como lo verían los coaches.", "/dashboard"),
        ]
        hero_badges = ["Admin", "CombatIQ"]
        try:
            _all_users = db.list_users() or []
            _n_athletes = sum(1 for u in _all_users if u.get("role") == "deportista")
            _n_coaches  = sum(1 for u in _all_users if u.get("role") == "coach")
        except Exception:
            _all_users, _n_athletes, _n_coaches = [], 0, 0
        summary_card = _summary_card(
            "Estado de la plataforma",
            "Resumen rápido del estado actual. Para análisis detallado accede a las métricas.",
            [
                _summary_kpi("Usuarios", str(len(_all_users)), "Cuentas registradas"),
                _summary_kpi("Deportistas", str(_n_athletes), "Atletas activos"),
                _summary_kpi("Coaches", str(_n_coaches), "Entrenadores"),
            ],
            "Accede al panel de métricas para ver DAU, WAU, engagement y tendencias.",
        )
        chart_card = _chart_card(
            "Panel de métricas",
            "El panel de métricas incluye DAU, WAU, engagement y distribución por deporte.",
            None,
            "Accede al panel de métricas para ver el análisis completo de la plataforma.",
        )
        tool_groups = [
            _tool_group(
                "Administración",
                "Accesos directos a las secciones clave de gestión de la plataforma.",
                [
                    ("Métricas", "/metricas", "signals.svg"),
                    ("Usuarios", "/usuarios", "team.svg"),
                    ("Anuncios", "/anuncios", "history.svg"),
                ],
            ),
            _tool_group(
                "Seguimiento",
                "Vistas de rendimiento y bienestar de los atletas.",
                [
                    ("Dashboard", "/dashboard", "profile.svg"),
                    ("Señales ECG / IMU", "/ecg", "signals.svg"),
                    ("Comparar", "/comparar", "compare.svg"),
                ],
            ),
        ]
    else:
        # Fetch questionnaire rows once — reused by wellness, streak, trend, count
        _qs_rows = _questionnaire_rows(user_id)
        wellness = _last_wellness(user_id, _qs_rows)
        ecg = _last_ecg(user_id)
        checkins_7d = _count_checkins_7d(user_id, _qs_rows)
        _sport_key = _norm_sport(sport)
        if _sport_key == "taekwondo":
            hero_copy = "Revisa si llegas con piernas frescas y sin molestias antes del entrenamiento de hoy."
            flow_items = [
                _flow_item("1", "Mira tu estado", "Comprueba fatiga de pierna, recuperación y carga acumulada antes de subir al tatami.", "/dashboard"),
                _flow_item("2", "Abre tu sesión", "Define el foco: distancia y bandal, contraataque o trabajo de Poomsae.", "/sesion"),
                _flow_item("3", "Compara cuando lo necesites", "Confirma tendencias de explosividad y precisión antes de un torneo o selectivo.", "/comparar"),
            ]
        elif _sport_key == "boxeo":
            hero_copy = "Revisa si llegas con manos libres y carga manejable antes de subir al saco."
            flow_items = [
                _flow_item("1", "Mira tu estado", "Ve si llegas ágil, sin molestias de guardia y con energía para los rounds.", "/dashboard"),
                _flow_item("2", "Abre tu sesión", "Define si el foco hoy es técnica de manos, ritmo en saco o recuperación.", "/sesion"),
                _flow_item("3", "Compara cuando lo necesites", "Confirma si el ritmo de golpeo ha mejorado antes de un bloque competitivo.", "/comparar"),
            ]
        else:
            hero_copy = "Aquí puedes ver cómo llegas hoy, qué registros tienes recientes y qué te conviene revisar primero."
            flow_items = [
                _flow_item("1", "Mira tu estado", "Revisa cómo llegas hoy y cuándo fue tu último check-in.", "/dashboard"),
                _flow_item("2", "Abre tu sesión", "Mantén claro el objetivo del día antes de pasar al análisis.", "/sesion"),
                _flow_item("3", "Compara cuando lo necesites", "Usa histórico para confirmar tendencias, no como primer paso.", "/comparar"),
            ]
        hero_badges = ["Deportista", sport or "Preparación diaria"]
        _rec_banner = _home_rec_banner(
            wellness.get("today_row"),
            wellness.get("latest_score"),
            sport,
        )
        _streak = _calc_streak(user_id, _qs_rows)
        _streak_card = _streak_widget(_streak)
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
            _athlete_trend_fig(user_id, _qs_rows),
            "Todavía no hay suficientes cuestionarios para mostrar una tendencia útil.",
        )
        tool_groups = [
            _tool_group(
                "Seguimiento",
                "Aquí tienes a mano las vistas que más te ayudan a seguir tu evolución.",
                [
                    ("Señales ECG / IMU", "/ecg", "signals.svg"),
                    ("Comparar sesiones", "/comparar", "compare.svg"),
                    ("Historial de wellbeing", "/historico", "history.svg"),
                ],
            ),
            _tool_group(
                "Apoyo del día",
                "Puedes usar estas herramientas cuando quieras llevar mejor tu control diario.",
                [
                    ("Peso", "/peso", "weight.svg"),
                    ("Nutrición", "/nutricion", "nutrition.svg"),
                    ("Chat con el coach", "/chat", "team.svg"),
                ],
            ),
        ]

    shell_cls = "home-shell coach-shell" if role == "coach" else "home-shell"

    return html.Div(
        className=shell_cls,
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
            _rec_banner,
            _streak_card if role != "coach" else None,
            html.Div(className="home-overview-grid", children=[summary_card, chart_card]),
            html.Details(
                className="tiles-section collapsible-card",
                open=False,
                children=[
                    html.Summary(
                        className="collapsible-card__summary tiles-section__head",
                        children=[
                            html.Div([
                                html.P("Herramientas frecuentes", className="tiles-section-label"),
                                html.P(
                                    "Accesos rápidos — el menú lateral sigue siendo la navegación principal.",
                                    className="tiles-section-copy",
                                ),
                            ]),
                            html.Span("⌄", className="collapsible-card__chevron"),
                        ],
                    ),
                    html.Div(className="collapsible-card__body", children=[
                        html.Div(tool_groups, className="tiles-group-grid"),
                    ]),
                ],
            ),
        ],
    )
