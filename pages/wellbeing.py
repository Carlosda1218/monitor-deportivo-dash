import json
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import (
    add_last_point_highlight,
    add_reference_band,
    apply_chart_style,
    graph_config,
    make_bar_trace,
    make_line_marker_trace,
)

from dash import html, dcc, Input, Output, State, callback
from dash.exceptions import PreventUpdate

from flask import session

import db
import questionnaires as Q


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v


def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return None


def h2(txt):
    return html.H2(txt, style={"margin": "6px 0 12px"})


def _coach_roster(coach_id: int):
    if not coach_id:
        return []

    out = []
    seen = set()
    for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
        if hasattr(db, fn):
            try:
                rows = getattr(db, fn)(int(coach_id)) or []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id")
                    if rid is None or rid in seen:
                        continue
                    seen.add(rid)
                    out.append(r)
            except Exception:
                pass
    return out


def _team_member_ids(team_id: int):
    if not team_id:
        return set()
    if not hasattr(db, "list_team_members"):
        return set()
    try:
        members = db.list_team_members(int(team_id)) or []
        return {int(m.get("athlete_id")) for m in members if m.get("athlete_id") is not None}
    except Exception:
        return set()


def _session_label(s: dict) -> str:
    if not s:
        return "—"
    sid = s.get("id", "—")
    ts = (s.get("ts_start") or "")[:19].replace("T", " ")
    st = (s.get("status") or "—")
    return f"#{sid} · {ts} · {st}"


def _athlete_sport(user_id):
    if not user_id:
        return ""
    try:
        u = db.get_user_by_id(int(user_id)) if hasattr(db, "get_user_by_id") else None
        return (u.get("sport") or "").strip().lower() if u else ""
    except Exception:
        return ""


def _make_slider(qdef: dict):
    return html.Div(
        className="q-question",
        children=[
            html.Label(qdef["label"]),
            dcc.Slider(
                id=f"q-{qdef['key']}",
                min=qdef.get("min", 1),
                max=qdef.get("max", 5),
                step=qdef.get("step", 1),
                value=qdef.get("default", 3),
                tooltip={"placement": "bottom"},
                marks={
                    int(v): str(int(v))
                    for v in np.arange(qdef.get("min", 1), qdef.get("max", 5) + qdef.get("step", 1), qdef.get("step", 1))
                    if float(v).is_integer()
                },
            ),
        ],
    )


GROUP_TITLES = {
    "base": ("Base del día", "Estas preguntas nos dicen cómo llega hoy el deportista en energía, sueño, recuperación y sensación general."),
    "taekwondo": ("Para taekwondo", "Preguntas enfocadas en explosividad, agilidad, ritmo de combate y sensaciones del tren inferior."),
    "boxeo": ("Para boxeo", "Preguntas enfocadas en ritmo de golpeo, rapidez, precisión y sensaciones del tren superior."),
    "competencia": ("Para competencia", "Preguntas para entender frescura, claridad mental y presión antes de una sesión clave o competencia."),
    "peso": ("Para control del peso", "Preguntas para detectar si el manejo del peso está afectando el rendimiento del día."),
    "molestia": ("Para molestia o lesión", "Preguntas para entender si una molestia actual puede cambiar la sesión o la lectura del día."),
}


_GROUP_COLORS = {
    "base":        "#0ea5e9",
    "taekwondo":   "#2fb7c4",
    "boxeo":       "#f0a832",
    "competencia": "#e45a5a",
    "peso":        "#27c98f",
    "molestia":    "#7b6fff",
}


def _group_header(group_key: str):
    title, subtitle = GROUP_TITLES.get(group_key, (group_key.title(), ""))
    color = _GROUP_COLORS.get(group_key, "var(--neon)")
    return html.Div(
        className="q-group-header",
        style={"borderLeftColor": color},
        children=[
            html.Div(title, className="q-group-header__title"),
            html.Div(subtitle, className="q-group-header__sub"),
        ],
    )


def _group_block(group_key: str, children):
    return html.Details(
        id=f"group-{group_key}",
        className="collapsible-card q-group-card",
        style={"display": "none"},
        open=(group_key == "base"),
        children=[
            html.Summary(
                className="collapsible-card__summary",
                children=[
                    _group_header(group_key),
                    html.Span("⌄", className="collapsible-card__chevron"),
                ],
            ),
            html.Div(className="collapsible-card__body", children=children),
        ],
    )


# =======================
# Cuestionario (layout)
# =======================


def _layout_questionnaire_legacy():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    team_selector = html.Div([
        dcc.Dropdown(id="q-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []
    options_users = []
    default_user = None

    if role == "coach" and uid:
        coach_id = int(uid)
        teams = []
        if hasattr(db, "list_teams"):
            try:
                teams = db.list_teams(coach_id) or []
            except Exception:
                teams = []

        team_options = [{"label": "Todos", "value": "ALL"}] + [
            {
                "label": f"{t.get('name', 'Equipo')}" + (f" · {t.get('sport')}" if t.get("sport") else ""),
                "value": t.get("id"),
            }
            for t in teams if t.get("id") is not None
        ]
        default_team = team_options[1]["value"] if len(team_options) > 1 else "ALL"
        team_selector = html.Div([
            html.Label("Equipo"),
            dcc.Dropdown(id="q-team", options=team_options, value=default_team, placeholder="Selecciona equipo..."),
            html.Br(),
        ])

        athletes = _coach_roster(coach_id)
        if default_team not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(default_team))
            athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []

        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u and u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    if role == "deportista":
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(id="q-user", options=options_users, value=default_user, disabled=True),
        ])
    else:
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(id="q-user", options=options_users, value=default_user, placeholder="Selecciona deportista..."),
        ])

    question_items = []
    ordered_groups = ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]
    defs = Q.question_defs()
    for group_key in ordered_groups:
        group_questions = [_make_slider(q) for q in defs if q.get("group") == group_key]
        question_items.append(_group_block(group_key, group_questions))

    return html.Div([

        # ── Encabezado ──────────────────────────────────────────────────────
        html.Div(className="page-head", children=[
            html.H2("Estado competitivo del día"),
            html.P(
                "Responde este check-in para saber cómo llegas hoy y qué conviene tener en cuenta antes de entrenar o competir.",
                className="text-muted",
            ),
        ]),

        # ── Card: quién ──────────────────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Deportista", className="card-title"),
            team_selector,
            user_selector,
            html.Div(id="q-sport-chip", className="q-sport-chip"),
        ]),

        # ── Card: contexto del día ───────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Contexto del día", className="card-title"),
            html.Div(className="filters-bar filters-bar--3", children=[
                html.Div(className="filter-item", children=[
                    html.Label("¿Hay competencia importante cerca?"),
                    dcc.Dropdown(
                        id="q-competition",
                        options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}],
                        value="no", clearable=False,
                    ),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("¿El control del peso hoy importa?"),
                    dcc.Dropdown(
                        id="q-weight",
                        options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}],
                        value="no", clearable=False,
                    ),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("¿Hay molestia a considerar?"),
                    dcc.Dropdown(
                        id="q-injury",
                        options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}],
                        value="no", clearable=False,
                    ),
                ]),
            ]),
            html.Div(style={"marginTop": "14px"}, children=[
                html.Label("Asociar a sesión"),
                dcc.Dropdown(
                    id="q-session",
                    options=[
                        {"label": "Auto (sesión abierta / crear si no existe)", "value": "AUTO"},
                        {"label": "Sin sesión (general)", "value": "NONE"},
                    ],
                    value="AUTO", clearable=False,
                ),
                html.Small(
                    "Tip: si creas una sesión en Señales, puedes ligar el cuestionario para comparaciones por sesión.",
                    className="text-muted",
                ),
            ]),
        ]),

        # ── Card: preguntas ──────────────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "20px"}, children=[
            html.H4("Preguntas del día", className="card-title"),
            html.Div(children=question_items),
        ]),

        # ── Guardar ──────────────────────────────────────────────────────────
        html.Button("Guardar cuestionario", id="btn-save-q", className="btn btn-primary",
                    style={"marginBottom": "20px", "width": "100%"}),

        # ── Resultado ───────────────────────────────────────────────────────
        html.Div(className="inner-card", style={"borderRadius": "14px", "padding": "8px 12px 16px"}, children=[
            html.H4("Resultado del día", className="card-title", style={"padding": "8px 4px 0"}),
            dcc.Graph(id="q-gauge", figure=go.Figure(), config=graph_config(),
                      style={"height": "360px", "width": "100%"}),
            html.Div(id="q-explain", style={"padding": "0 8px 8px"}),
        ]),

    ])


def layout_questionnaire():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    team_selector = html.Div([
        dcc.Dropdown(id="q-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []
    options_users = []
    default_user = None

    if role == "coach" and uid:
        coach_id = int(uid)
        teams = []
        if hasattr(db, "list_teams"):
            try:
                teams = db.list_teams(coach_id) or []
            except Exception:
                teams = []

        team_options = [{"label": "Todos", "value": "ALL"}] + [
            {
                "label": f"{t.get('name', 'Equipo')}" + (f" · {t.get('sport')}" if t.get("sport") else ""),
                "value": t.get("id"),
            }
            for t in teams if t.get("id") is not None
        ]
        default_team = team_options[1]["value"] if len(team_options) > 1 else "ALL"
        team_selector = html.Div([
            html.Label("Equipo"),
            dcc.Dropdown(id="q-team", options=team_options, value=default_team, placeholder="Selecciona equipo..."),
            html.Br(),
        ])

        athletes = _coach_roster(coach_id)
        if default_team not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(default_team))
            athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []

        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u and u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    if role == "deportista":
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(id="q-user", options=options_users, value=default_user, disabled=True),
        ])
    else:
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(id="q-user", options=options_users, value=default_user, placeholder="Selecciona deportista..."),
        ])

    question_items = []
    ordered_groups = ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]
    defs = Q.question_defs()
    for group_key in ordered_groups:
        group_questions = [_make_slider(q) for q in defs if q.get("group") == group_key]
        question_items.append(_group_block(group_key, group_questions))

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Estado competitivo del día"),
            html.P(
                "Responde este check-in para saber cómo llegas hoy y qué conviene tener en cuenta antes de entrenar o competir.",
                className="text-muted",
            ),
        ]),
        html.Div(className="questionnaire-grid", children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Deportista", className="card-title"),
                    team_selector,
                    user_selector,
                    html.Div(id="q-sport-chip", className="q-sport-chip"),
                ]),
                html.Div(className="card", children=[
                    html.H4("Contexto del día", className="card-title"),
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("¿Hay competencia importante cerca?"),
                            dcc.Dropdown(id="q-competition", options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}], value="no", clearable=False),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("¿El control del peso hoy importa?"),
                            dcc.Dropdown(id="q-weight", options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}], value="no", clearable=False),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("¿Hay molestia a considerar?"),
                            dcc.Dropdown(id="q-injury", options=[{"label": "No", "value": "no"}, {"label": "Sí", "value": "si"}], value="no", clearable=False),
                        ]),
                    ]),
                    html.Div(style={"marginTop": "14px"}, children=[
                        html.Label("Asociar a sesión"),
                        dcc.Dropdown(
                            id="q-session",
                            options=[
                                {"label": "Auto (sesión abierta / crear si no existe)", "value": "AUTO"},
                                {"label": "Sin sesión (general)", "value": "NONE"},
                            ],
                            value="AUTO",
                            clearable=False,
                        ),
                        html.Small(
                            "Si hoy vas a trabajar con una sesión concreta, puedes dejarla asociada para revisarla mejor después.",
                            className="text-muted",
                        ),
                    ]),
                ]),
                html.Div(className="inner-card q-result-card", style={"borderRadius": "14px", "padding": "8px 12px 16px"}, children=[
                    html.H4("Resultado del día", className="card-title", style={"padding": "8px 4px 0"}),
                    html.Div("Estado competitivo del día (0-100)", className="text-muted", style={"padding": "0 8px 4px"}),
                    dcc.Graph(id="q-gauge", figure=go.Figure(), config=graph_config(), style={"height": "300px", "width": "100%"}),
                    html.Div(id="q-explain", style={"padding": "10px 8px 8px"}),
                ]),
            ]),
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Preguntas del día", className="card-title"),
                    html.P(
                        "Responde por bloques. Empezamos por la base del día y solo verás lo que aplica hoy.",
                        className="text-muted q-block-copy",
                    ),
                    html.Div(className="q-groups", children=question_items),
                    html.Button("Guardar cuestionario", id="btn-save-q", className="btn btn-primary", style={"marginTop": "18px", "width": "100%"}),
                ]),
            ]),
        ]),
    ])


@callback(
    Output("q-user", "options"),
    Output("q-user", "value"),
    Input("q-team", "value"),
    prevent_initial_call=False,
)
def update_q_user_options(team_id):
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        raise PreventUpdate

    coach_id = session.get("user_id")
    if not coach_id:
        return [], None

    athletes = _coach_roster(int(coach_id))
    if team_id not in (None, "", "ALL"):
        member_ids = _team_member_ids(int(team_id))
        athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []

    options = [
        {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
        for u in athletes if u.get("id") is not None
    ]
    return options, (options[0]["value"] if options else None)


@callback(
    Output("q-sport-chip", "children"),
    *[Output(f"group-{g}", "style") for g in ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]],
    *[Output(f"group-{g}", "open") for g in ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]],
    Input("q-user", "value"),
    Input("q-competition", "value"),
    Input("q-weight", "value"),
    Input("q-injury", "value"),
    prevent_initial_call=False,
)
def update_question_visibility(user_id, competition, weight, injury):
    sport = _athlete_sport(user_id)
    active = set(Q.score_breakdown({}, sport=sport, competition=(competition == "si"), weight=(weight == "si"), injury=(injury == "si"))["active_keys"])
    chip = ""
    if sport == "taekwondo":
        chip = "Perfil activo: Taekwondo"
    elif sport == "boxeo":
        chip = "Perfil activo: Boxeo"
    elif sport:
        chip = f"Perfil activo: {sport.title()}"
    else:
        chip = "Perfil activo: General"

    active_groups = {q.get("group") for q in Q.question_defs() if q["key"] in active}

    group_styles = []
    group_open = []
    for g in ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]:
        is_active = g in active_groups
        group_styles.append({"display": "block"} if is_active else {"display": "none"})
        group_open.append(g in {"base", "taekwondo", "boxeo"} and is_active)
    return (chip, *group_styles, *group_open)


@callback(
    Output("q-session", "options"),
    Output("q-session", "value"),
    Input("q-user", "value"),
    prevent_initial_call=False,
)
def load_q_sessions(user_id):
    base_opts = [
        {"label": "Auto (sesión abierta / crear si no existe)", "value": "AUTO"},
        {"label": "Sin sesión (general)", "value": "NONE"},
    ]
    if not user_id:
        return base_opts, "AUTO"

    sessions = []
    if hasattr(db, "list_sessions"):
        try:
            sessions = db.list_sessions(int(user_id), limit=25) or []
        except Exception:
            sessions = []

    opts = base_opts.copy()
    for s in sessions:
        sid = s.get("id")
        if sid is None:
            continue
        ts = (s.get("ts_start") or "")[:16]
        status = s.get("status") or ""
        label = f"#{sid} · {ts} · {status}" if ts else f"#{sid} · {status}"
        opts.append({"label": label, "value": int(sid)})
    return opts, "AUTO"


@callback(
    Output("q-gauge", "figure"),
    Output("q-explain", "children"),
    Input("btn-save-q", "n_clicks"),
    State("q-user", "value"),
    State("q-session", "value"),
    State("q-competition", "value"),
    State("q-weight", "value"),
    State("q-injury", "value"),
    *[State(f"q-{k}", "value") for k, _ in Q.questions()],
    prevent_initial_call=True,
)
def save_q(n, user_id, session_id, competition, weight, injury, *values):
    if not user_id:
        raise PreventUpdate

    sport = _athlete_sport(user_id)
    comp_flag = competition == "si"
    weight_flag = weight == "si"
    injury_flag = injury == "si"

    ans = {}
    for (k, _), v in zip(Q.questions(), values):
        meta = Q.question_meta(k)
        ans[k] = meta.get("default", 3) if v is None else v

    breakdown = Q.score_breakdown(ans, sport=sport, competition=comp_flag, weight=weight_flag, injury=injury_flag)
    wellness = breakdown["score"]

    payload = {k: ans.get(k) for k in breakdown["active_keys"]}
    payload["_ctx"] = {
        "sport": sport,
        "competition": comp_flag,
        "weight": weight_flag,
        "injury": injury_flag,
    }

    sid = None
    if session_id in (None, "", "NONE"):
        sid = None
    elif session_id == "AUTO":
        if hasattr(db, "ensure_open_session"):
            try:
                actor_id = _safe_int(session.get("user_id"))
                athlete = db.get_user_by_id(int(user_id)) if hasattr(db, "get_user_by_id") else None
                sport_name = athlete.get("sport") if athlete else None
                sid = db.ensure_open_session(int(user_id), created_by=actor_id, sport=sport_name)
            except Exception:
                sid = None
    else:
        sid = _safe_int(session_id)

    db.save_questionnaire(
        int(user_id),
        payload,
        wellness,
        None,
        None,
        session_id=sid,
    )

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=wellness,
        domain={"x": [0.0, 1.0], "y": [0.2, 0.98]},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "rgba(150,175,200,0.6)"},
            "bar": {"color": "#0891b2", "thickness": 0.28},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 50], "color": "rgba(200,50,50,0.18)"},
                {"range": [50, 65], "color": "rgba(220,140,30,0.18)"},
                {"range": [65, 80], "color": "rgba(200,185,30,0.18)"},
                {"range": [80, 100], "color": "rgba(30,170,100,0.18)"},
            ],
        },
    ))
    apply_chart_style(fig, height=320)
    fig.update_layout(margin=dict(l=18, r=18, t=6, b=6))

    if wellness >= 80:
        estado = "Listo/a para una sesión exigente o competencia"
    elif wellness >= 65:
        estado = "Buen estado, con algunos puntos a vigilar"
    elif wellness >= 50:
        estado = "Día de atención: conviene interpretar la carga con cuidado"
    else:
        estado = "Estado comprometido para exigencia alta"

    detail_map = {d["key"]: d for d in breakdown["details"]}
    top_positive = sorted([d for d in breakdown["details"] if d["dimension"] == "positive"], key=lambda x: x["score"], reverse=True)[:2]
    top_risks = sorted([d for d in breakdown["details"] if d["dimension"] == "risk"], key=lambda x: x["score"], reverse=True)[:2]

    positives_txt = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_positive) if top_positive else "sin puntos fuertes claros"
    risks_txt = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_risks) if top_risks else "sin señales de riesgo destacadas"

    explain = html.Div([
        html.P([html.Strong("Lectura general: "), estado]),
        html.P(
            f"Este valor combina un 70% de capacidad del día (energía, recuperación, sueño, disposición y variables del deporte) "
            f"con un 30% de señales de riesgo (fatiga, cuerpo pesado, molestias, tensión y peso si aplica)."
        ),
        html.Ul([
            html.Li(f"Capacidad del día: {breakdown['positive_avg']:.0f}/100"),
            html.Li(f"Señales de riesgo: {breakdown['risk_avg']:.0f}/100"),
            html.Li(f"Lo mejor de hoy: {positives_txt}."),
            html.Li(f"Lo que más pesa en contra hoy: {risks_txt}."),
        ]),
    ])
    return fig, explain


def rolling_mean(y, window: int):
    y = list(map(float, y))
    n = len(y)
    if window <= 1 or n == 0:
        return y
    if n < window:
        m = sum(y) / n
        return [m] * n
    cumsum = [0.0]
    for val in y:
        cumsum.append(cumsum[-1] + val)
    res = []
    for i in range(1, n + 1):
        a = max(0, i - window)
        b = i
        w = b - a
        res.append((cumsum[b] - cumsum[a]) / w)
    return res


def _history_kpi(label: str, value: str, sub: str):
    return html.Div(
        className="kpi kpi--mini",
        children=[
            html.Div(label, className="kpi-label"),
            html.Div(value, className="kpi-value"),
            html.Div(sub, className="kpi-sub"),
        ],
    )


def _history_summary(pts, cap_pts, risk_pts, carga_pts):
    if not pts:
        return [
            _history_kpi("Último estado", "Sin dato", "Todavía no hay check-ins visibles."),
            _history_kpi("Registros", "0", "En cuanto se guarde el primero, verás la evolución."),
            _history_kpi("Lectura rápida", "Pendiente", "Aquí resumiremos la tendencia más reciente."),
        ]

    latest_score = float(pts[-1][2])
    prev_score = float(pts[-2][2]) if len(pts) >= 2 else None
    delta = latest_score - prev_score if prev_score is not None else None

    if latest_score >= 80:
        status_sub = "Último estado competitivo del día."
    elif latest_score >= 65:
        status_sub = "Buen punto de partida con detalles a vigilar."
    elif latest_score >= 50:
        status_sub = "Conviene interpretar la carga con algo más de cuidado."
    else:
        status_sub = "Hoy conviene bajar exigencia y revisar contexto."

    if delta is None:
        trend_value = "Primer dato"
        trend_sub = "Todavía no hay un registro anterior para comparar."
    else:
        trend_value = f"{delta:+.0f}"
        if delta >= 6:
            trend_sub = "Subió claramente frente al registro anterior."
        elif delta <= -6:
            trend_sub = "Bajó de forma visible frente al registro anterior."
        else:
            trend_sub = "Se mantiene bastante parecido al registro anterior."

    if carga_pts:
        load_value = f"{float(carga_pts[-1][2]):.0f}"
        load_sub = "Última carga calculada con RPE y duración."
    elif cap_pts or risk_pts:
        cap = float(cap_pts[-1][2]) if cap_pts else None
        risk = float(risk_pts[-1][2]) if risk_pts else None
        if cap is not None and risk is not None:
            load_value = f"{cap:.0f} / {risk:.0f}"
            load_sub = "Capacidad del día frente a señales de riesgo."
        elif cap is not None:
            load_value = f"{cap:.0f}"
            load_sub = "Capacidad del día en el último registro."
        else:
            load_value = f"{risk:.0f}" if risk is not None else "Sin dato"
            load_sub = "Señales de riesgo del último registro."
    else:
        load_value = "Sin dato"
        load_sub = "Faltan datos complementarios para esta lectura."

    return [
        _history_kpi("Último estado", f"{latest_score:.0f}/100", status_sub),
        _history_kpi("Cambio reciente", trend_value, trend_sub),
        _history_kpi("Lectura rápida", load_value, load_sub),
    ]


def _empty_chart(title: str, message: str):
    fig = go.Figure()
    apply_chart_style(fig, title=title, height=420)
    fig.add_annotation(
        text=message,
        showarrow=False,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        font=dict(size=13, color="#8fa3bf"),
    )
    fig.update_xaxes(showgrid=False, showticklabels=False)
    fig.update_yaxes(showgrid=False, showticklabels=False)
    return fig




def _parse_answers_json(raw):
    if not raw:
        return {}
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8', 'ignore')
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _history_athletes_for_role(role, uid, team_id="ALL"):
    athletes = []
    if role == "coach" and uid:
        athletes = _coach_roster(int(uid))
        if team_id not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(team_id))
            athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []
    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]
    return [a for a in athletes if a and a.get("id") is not None]


def _history_options(role, uid, team_id="ALL"):
    athletes = _history_athletes_for_role(role, uid, team_id)
    options = []
    for u in athletes:
        try:
            qs = db.list_questionnaires(int(u.get("id"))) or []
        except Exception:
            qs = []
        if not qs:
            continue
        options.append({"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")})
    return options


def layout_history():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    team_selector = html.Div([
        dcc.Dropdown(id="h-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []
    if role == "coach" and uid:
        coach_id = int(uid)
        teams = []
        if hasattr(db, "list_teams"):
            try:
                teams = db.list_teams(coach_id) or []
            except Exception:
                teams = []

        team_options = [{"label": "Todos", "value": "ALL"}] + [
            {
                "label": f"{t.get('name', 'Equipo')}" + (f" · {t.get('sport')}" if t.get("sport") else ""),
                "value": t.get("id"),
            }
            for t in teams if t.get("id") is not None
        ]
        default_team = team_options[1]["value"] if len(team_options) > 1 else "ALL"
        team_selector = html.Div([
            html.Label("Equipo"),
            dcc.Dropdown(id="h-team", options=team_options, value=default_team, placeholder="Selecciona equipo..."),
            html.Br(),
        ])

        athletes = _coach_roster(coach_id)
        if default_team not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(default_team))
            athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []
    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]

    options_users = _history_options(role, uid, default_team if role == "coach" and uid else "ALL")

    return html.Div([

        # ── Encabezado ──────────────────────────────────────────────────────
        html.Div(className="page-head", children=[
            html.H2("Histórico de wellbeing"),
            html.P(
                "Aquí puedes ver cómo ha ido cambiando el estado del día y qué señales merece la pena revisar con más calma.",
                className="text-muted",
            ),
        ]),

        # ── Card: selector ───────────────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "20px"}, children=[
            html.H4("Qué deportista quieres revisar", className="card-title"),
            team_selector,
            html.Div(className="filter-item", style={"marginTop": "8px"}, children=[
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="h-user",
                    options=options_users,
                    value=(options_users[0]["value"] if options_users else None),
                    placeholder="Selecciona deportista...",
                ),
            ]),
            html.Small(
                "Solo aparecen deportistas con check-ins guardados.",
                className="text-muted", style={"marginTop": "8px", "display": "block"},
            ),
        ]),

        html.Div(id="h-summary", className="kpis kpis--auto kpis--tight", style={"marginBottom": "16px"}),

        # ── Gráficas ─────────────────────────────────────────────────────────
        html.Div(className="wellbeing-history-grid", children=[
            html.Div(className="inner-card", style={"borderRadius": "14px", "padding": "8px 12px 12px"}, children=[
                html.H4("Estado del día", className="card-title", style={"padding": "8px 4px 0"}),
                dcc.Graph(id="h-wellness", figure=go.Figure(), config=graph_config(),
                          style={"height": "380px", "width": "100%"}),
            ]),

            html.Div(className="inner-card", style={"borderRadius": "14px", "padding": "8px 12px 12px"}, children=[
                html.H4("Carga y señales del contexto", className="card-title", style={"padding": "8px 4px 0"}),
                dcc.Graph(id="h-load", figure=go.Figure(), config=graph_config(),
                          style={"height": "380px", "width": "100%"}),
                html.Details(className="collapsible-card wellbeing-help", children=[
                    html.Summary(className="collapsible-card__summary", children=[
                        html.Div(className="collapsible-card__head", children=[
                            html.Div("Cómo leer esta gráfica", className="card-title"),
                            html.Div(
                                "Te contamos cuándo estás viendo carga real y cuándo una lectura más contextual.",
                                className="text-muted",
                            ),
                        ]),
                        html.Span("⌄", className="collapsible-card__chevron"),
                    ]),
                    html.Div(className="collapsible-card__body", children=[
                        html.Ul(className="list-compact", children=[
                            html.Li("Si hay RPE y duración, verás la carga histórica de esos registros."),
                            html.Li("Si no existe esa carga, la gráfica cambia para comparar capacidad del día y señales de riesgo."),
                            html.Li("Úsala como apoyo para detectar cambios, no como una lectura aislada."),
                        ]),
                    ]),
                ]),
            ]),
        ]),

    ])


@callback(
    Output("h-user", "options"),
    Output("h-user", "value"),
    Input("h-team", "value"),
    prevent_initial_call=False,
)
def update_h_user_options(team_id):
    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")
    options = _history_options(role, uid, team_id)
    return options, (options[0]["value"] if options else None)


@callback(
    Output("h-summary", "children"),
    Output("h-wellness", "figure"),
    Output("h-load", "figure"),
    Input("h-user", "value"),
    prevent_initial_call=False,
)
def render_history(user_id):
    if not user_id:
        summary = _history_summary([], [], [], [])
        return (
            summary,
            _empty_chart("Estado del día", "Selecciona un deportista con check-ins guardados."),
            _empty_chart("Carga y señales del contexto", "Aquí verás la evolución cuando existan registros."),
        )

    try:
        rows = db.list_questionnaires(int(user_id)) or []
    except Exception:
        rows = []

    if not rows:
        summary = _history_summary([], [], [], [])
        return (
            summary,
            _empty_chart("Estado del día", "Este deportista todavía no tiene check-ins guardados."),
            _empty_chart("Carga y señales del contexto", "Aún no hay datos suficientes para mostrar evolución."),
        )

    pts = []
    carga_pts = []
    cap_pts = []
    risk_pts = []
    sport_default = _athlete_sport(user_id)

    for r in rows:
        ts = r.get("ts") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("T", " ")[:19]) if ts else None
        except Exception:
            dt = None
        if dt is None:
            continue
        label = dt.strftime("%d/%m %H:%M")
        ws = r.get("wellness_score")
        if ws is not None:
            pts.append((dt, label, float(ws)))

        answers = _parse_answers_json(r.get("answers_json"))
        ctx = answers.get("_ctx", {}) if isinstance(answers, dict) else {}
        sport = (ctx.get("sport") or sport_default or "").strip().lower()
        comp = bool(ctx.get("competition"))
        weight = bool(ctx.get("weight"))
        injury = bool(ctx.get("injury"))
        try:
            bd = Q.score_breakdown(answers, sport=sport, competition=comp, weight=weight, injury=injury)
            cap_pts.append((dt, label, float(bd.get("positive_avg", 0.0))))
            risk_pts.append((dt, label, float(bd.get("risk_avg", 0.0))))
        except Exception:
            pass

        rpe = r.get("rpe")
        dur = r.get("duration_min")
        if rpe is not None and dur is not None:
            try:
                carga_pts.append((dt, label, float(rpe) * float(dur)))
            except Exception:
                pass

    pts.sort(key=lambda x: x[0])
    cap_pts.sort(key=lambda x: x[0])
    risk_pts.sort(key=lambda x: x[0])
    carga_pts.sort(key=lambda x: x[0])

    summary = _history_summary(pts, cap_pts, risk_pts, carga_pts)
    fig_w = go.Figure()
    fig_l = go.Figure()

    if pts:
        x_vals = [p[1] for p in pts]
        y_vals = [p[2] for p in pts]
        add_reference_band(fig_w, y0=80, y1=100, fillcolor="rgba(39,201,143,0.10)")
        add_reference_band(fig_w, y0=50, y1=65, fillcolor="rgba(240,168,50,0.09)")
        fig_w.add_trace(make_line_marker_trace(x_vals, y_vals, "Estado del día", color="#0ea5e9", width=2.6, marker_size=7))
        add_last_point_highlight(fig_w, x_vals, y_vals, name="Último estado", color="#0ea5e9", size=11)
        apply_chart_style(fig_w, title="Estado del día", height=420)
        fig_w.update_yaxes(range=[0, 100], title="Puntaje")
    else:
        fig_w = _empty_chart("Estado del día", "No hay registros válidos para graficar el estado del día.")

    if carga_pts:
        x_vals = [p[1] for p in carga_pts]
        y_vals = [p[2] for p in carga_pts]
        fig_l.add_trace(make_bar_trace(x_vals, y_vals, "Carga histórica", color="#f0a832", opacity=0.86))
        apply_chart_style(fig_l, title="Carga histórica (registros con RPE y duración)", height=420)
        fig_l.update_yaxes(title="Carga")
    elif cap_pts or risk_pts:
        if cap_pts:
            x_cap = [p[1] for p in cap_pts]
            y_cap = [p[2] for p in cap_pts]
            add_reference_band(fig_l, y0=80, y1=100, fillcolor="rgba(39,201,143,0.08)")
            fig_l.add_trace(make_line_marker_trace(x_cap, y_cap, "Capacidad del día", color="#27c98f", width=2.4, marker_size=7))
            add_last_point_highlight(fig_l, x_cap, y_cap, name="Última capacidad", color="#27c98f", size=10)
        if risk_pts:
            x_risk = [p[1] for p in risk_pts]
            y_risk = [p[2] for p in risk_pts]
            fig_l.add_trace(make_line_marker_trace(x_risk, y_risk, "Señales de riesgo", color="#e45a5a", width=2.4, marker_size=7))
            add_last_point_highlight(fig_l, x_risk, y_risk, name="Último riesgo", color="#e45a5a", size=10)
        apply_chart_style(fig_l, title="Capacidad del día vs señales de riesgo", height=420)
        fig_l.update_yaxes(range=[0, 100], title="Puntaje")
    else:
        fig_l = _empty_chart("Carga y señales del contexto", "Aún no hay suficientes datos para mostrar la segunda gráfica.")

    return summary, fig_w, fig_l
