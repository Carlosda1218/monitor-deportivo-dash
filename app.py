import os, io, base64, json, csv, webbrowser, importlib, traceback, urllib.parse, random
from threading import Timer
from datetime import datetime

# Workaround: Python 3.13 + Windows tiene un bug en mimetypes.read_windows_registry()
# que congela el arranque al importar Dash. Se parchea antes de la importación.
import mimetypes as _mt
_orig_rwr = getattr(_mt, "read_windows_registry", None)
if _orig_rwr:
    def _safe_rwr(add_type=None):
        try:
            _orig_rwr(add_type) if add_type is not None else _orig_rwr()
        except Exception:
            pass
    _mt.read_windows_registry = _safe_rwr

import numpy as np
import plotly.graph_objects as go

from flask import Flask, session, request
import dash
from dash import Dash, html, dcc, Input, Output, State, callback_context, ALL
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate

import db
import sensors as S
import questionnaires as Q

import pages.wellbeing as wellbeing_page
import ui_charts as _uc
# ====== NUEVAS VISTAS (clases) ======
from views.signals_view import SignalsView
from views.sensors_view import SensorsView
from views.compare_view import CompareView
from views.analysis_view import AnalysisView

# ====== Flask + Dash ======
server = Flask(__name__)
server.secret_key = os.environ.get("POWERSYNC_SECRET", "dev-secret-change-me")

app = dash.Dash(
    __name__,
    server=server,
    title="CombatIQ",
    suppress_callback_exceptions=True
)

# ====== DB init ======
db.init_db()

# ====== Instancias de vistas nuevas ======
signals_view  = SignalsView(app, db, S)
sensors_view  = SensorsView(app, db, S)
compare_view  = CompareView(app, db, S)
analysis_view = AnalysisView(app, db, S)

# ====== Layout (CS-003 — Sidebar Pro • Desktop) ======
SIDEBAR_W = 272  # px (match assets/10_theme.css)
PAGE_COLLAPSED_MARGIN = 40  # px (cuando el sidebar está colapsado)


def h2(txt):
    return html.H2(txt, style={"margin": "6px 0 12px"})


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v


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



def _coach_roster(coach_id: int):
    """
    Roster unificado del coach, filtrado por el deporte del coach.
    Un coach de Taekwondo solo ve atletas de Taekwondo; ídem para Boxeo.
    """
    if not coach_id:
        return []

    # Deporte del coach desde sesión (disponible en contexto de request)
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None

    out = []
    seen = set()

    for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
        if hasattr(db, fn):
            try:
                # Pasa sport si la función lo acepta (list_athletes_for_coach ya lo hace)
                import inspect
                sig = inspect.signature(getattr(db, fn))
                if "sport" in sig.parameters:
                    rows = getattr(db, fn)(int(coach_id), sport=coach_sport) or []
                else:
                    rows = getattr(db, fn)(int(coach_id)) or []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id")
                    if rid is None or rid in seen:
                        continue
                    # Filtro de deporte por si la función no lo aplica en DB
                    if coach_sport and r.get("sport") and r["sport"] != coach_sport:
                        continue
                    seen.add(rid)
                    out.append(r)
            except Exception:
                pass

    return out

# ====== Sidebar (CS-003 — Pro) ======
def _is_active(pathname: str, href: str) -> bool:
    p = pathname or ""
    if href == "/":
        return p in ("/", "/inicio", "/home", "")
    return p == href


def _nav_link(label: str, href: str, icon: str, pathname: str):
    cls = "nav-link" + (" active" if _is_active(pathname, href) else "")
    return dcc.Link(
        [
            html.Img(src=f"/assets/icons/{icon}", className="nav-ico"),
            html.Span(label, className="nav-label"),
        ],
        href=href,
        className=cls,
    )


def _nav_section(title: str, items):
    return html.Div(
        [html.Div(title, className="nav-section-title")] + items,
        className="nav-section",
    )


def _sidebar_links(pathname: str):
    logged = bool(session.get("user_id"))
    role = _to_str(session.get("role")) or "no autenticado"
    name = _to_str(session.get("name")) if session.get("name") else None

    # Normaliza strings en sesión (evita bytes/encoding raros)
    session["role"] = role
    if name is not None:
        session["name"] = name

    is_demo = bool(session.get("is_demo"))

    # Meta del usuario (arriba del menú)
    if logged:
        meta = html.Div(
            [
                html.Div(
                    [
                        html.Div(name or "Usuario", className="sidebar-user__name"),
                        html.Div("Sesión activa", className="sidebar-user__sub"),
                    ],
                    className="sidebar-user__text",
                ),
                html.Span(role, className="badge-role"),
            ],
            className="sidebar-user",
        )
    else:
        meta = html.Div(
            [
                html.Div(
                    [
                        html.Div("CombatIQ", className="sidebar-user__name"),
                        html.Div("Inicia sesión para continuar", className="sidebar-user__sub"),
                    ],
                    className="sidebar-user__text",
                ),
                html.Span("offline", className="badge-role"),
            ],
            className="sidebar-user",
        )

    sections = []
    bottom = []

    if not logged:
        sections = [
            _nav_section(
                "Cuenta",
                [
                    _nav_link("Iniciar sesión", "/login", "profile.svg", pathname),
                    _nav_link("Registrarse", "/registro", "profile.svg", pathname),
                ],
            )
        ]
    else:
        if role == "deportista":
            sections = [
                _nav_section(
                    "Inicio",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Mi sesión", "/sesion", "session.svg", pathname),
                        _nav_link("Mi perfil", "/dashboard", "profile.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Análisis",
                    [
                        _nav_link("Análisis profesional", "/analisis", "signals.svg", pathname),
                        _nav_link("Análisis de sesión", "/ecg", "signals.svg", pathname),
                        _nav_link("Histórico y comparación", "/comparar", "compare.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Wellbeing",
                    [
                        _nav_link("Check-in diario", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Histórico de wellbeing", "/historico", "history.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Seguimiento",
                    [
                        _nav_link("Sensores", "/sensores", "sensors.svg", pathname),
                        _nav_link("Peso", "/peso", "weight.svg", pathname),
                        _nav_link("Nutrición", "/nutricion", "nutrition.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Equipo",
                    [
                        _nav_link("Mi coach y equipo", "/usuarios", "team.svg", pathname),
                        _nav_link("Contacto con coach", "/contacto", "team.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Información",
                    [
                        _nav_link("Sobre CombatIQ", "/sobre", "profile.svg", pathname),
                        _nav_link("Invitar", "/invita", "team.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]

        elif role == "coach":
            sections = [
                _nav_section(
                    "Inicio",
                    [
                        _nav_link("Panel de equipo", "/", "session.svg", pathname),
                        _nav_link("Mi jornada", "/sesion", "session.svg", pathname),
                        _nav_link("Mi perfil", "/dashboard", "profile.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Mi equipo",
                    [
                        _nav_link("Estado del equipo", "/usuarios", "team.svg", pathname),
                        _nav_link("Ficha de atleta", "/deportista", "profile.svg", pathname),
                        _nav_link("Sensores del equipo", "/sensores", "sensors.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Rendimiento",
                    [
                        _nav_link("Análisis profesional", "/analisis", "signals.svg", pathname),
                        _nav_link("Análisis de señales", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparativa", "/comparar", "compare.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Bienestar",
                    [
                        _nav_link("Check-in del equipo", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Historial", "/historico", "history.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Comunicación",
                    [
                        _nav_link("Comunicados", "/anuncios", "signals.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Información",
                    [
                        _nav_link("Sobre CombatIQ", "/sobre", "profile.svg", pathname),
                        _nav_link("Invitar atletas", "/invita", "team.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]
        else:
            # admin (u otro rol)
            sections = [
                _nav_section(
                    "Admin",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Perfil / Ajustes", "/dashboard", "profile.svg", pathname),
                        _nav_link("Usuarios", "/usuarios", "team.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Monitoreo",
                    [
                        _nav_link("Sensores", "/sensores", "sensors.svg", pathname),
                        _nav_link("Señales (ECG)", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar", "/comparar", "compare.svg", pathname),
                        _nav_link("Bienestar", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Tendencias", "/historico", "history.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]

    demo_badge = html.Div(
        "MODO DEMO",
        style={
            "background": "#f0a83220",
            "border": "1px solid #f0a83260",
            "color": "#f0a832",
            "borderRadius": "6px",
            "padding": "4px 10px",
            "fontSize": "11px",
            "fontWeight": "700",
            "textAlign": "center",
            "margin": "8px 12px 0",
        },
    ) if is_demo else None

    return html.Div(
        [x for x in [
            meta,
            demo_badge,
            html.Div(sections, className="nav-scroll"),
            html.Div(bottom, className="nav-bottom"),
        ] if x is not None],
        className="nav-body",
    )


sidebar = html.Div(
    id="sidebar",
    children=[
        html.Div(
            className="sidebar-brand",
            children=[
                html.Div(
                    className="sidebar-brand__mark",
                    children=[
                        html.Img(src="/assets/logo_powersync.svg", className="sidebar-brand__logo"),
                    ],
                ),
                html.Div(
                    className="sidebar-brand__text",
                    children=[
                        html.Span("CombatIQ", className="sidebar-brand__name"),
                        html.Span("Combat Sports Performance", className="sidebar-brand__tag"),
                    ],
                ),
            ],
        ),
        html.Div(id="sidebar-links"),
        html.Div(
            className="sidebar-theme-footer",
            children=[
                html.Button(
                    id="btn-theme-toggle",
                    n_clicks=0,
                    className="theme-toggle",
                    children=[html.Span(id="theme-toggle-icon", className="theme-toggle__icon"), " Tema claro / oscuro"],
                ),
            ],
        ),
    ],
)

content = html.Div(id="page-content", className="page-shell")


def _initial_path_and_content():
    try:
        req_path = request.path
    except Exception:
        req_path = None

    logged = bool(session.get("user_id"))
    if not logged and req_path in (None, "", "/", "/inicio", "/home"):
        try:
            login_mod = importlib.import_module("pages.auth_login")
            layout_fn = getattr(login_mod, "layout", None)
            login_layout = layout_fn() if callable(layout_fn) else login_mod.layout
        except Exception:
            login_layout = html.Div()
        return "/login", login_layout

    return None, None


def serve_layout():
    initial_path, initial_content = _initial_path_and_content()
    page_content = html.Div(id="page-content", className="page-shell", children=initial_content)
    return html.Div([
        dcc.Location(id="url", pathname=initial_path),
        sidebar,
        page_content,
        dcc.Download(id="dl-png"),
        dcc.Download(id="dl-peaks"),
        dcc.Store(id="dl-png-clicks", data=0),
        dcc.Store(id="ui-sidebar-collapsed", data=False),
        dcc.Store(id="theme-store", storage_type="local", data="dark"),
        html.Div(id="theme-applied", style={"display": "none"}),
        html.Div(id="theme-charts-applied", style={"display": "none"}),
        html.Button("®", id="btn-toggle-sidebar", n_clicks=0, className="sidebar-toggle")
    ])

# === LAYOUT ===
_legacy_boot_layout = html.Div([
    dcc.Location(id="url"),
    sidebar,
    content,
    dcc.Download(id="dl-png"),
    dcc.Download(id="dl-peaks"),
    dcc.Store(id="dl-png-clicks", data=0),
    dcc.Store(id="ui-sidebar-collapsed", data=False),
    dcc.Store(id="theme-store", storage_type="local", data="dark"),
    html.Div(id="theme-applied", style={"display": "none"}),
    html.Div(id="theme-charts-applied", style={"display": "none"}),
    html.Button("«", id="btn-toggle-sidebar", n_clicks=0, className="sidebar-toggle")
])
app.layout = serve_layout


@app.callback(Output("sidebar-links", "children"), Input("url", "pathname"))
def _render_sidebar(pathname):
    return _sidebar_links(pathname)


# ====== IMPORT páginas externas ======
def _safe_import(modname: str):
    try:
        mod = importlib.import_module(modname)
        return mod, None
    except Exception:
        return None, traceback.format_exc()


page_login, err_login = _safe_import("pages.auth_login")
page_register, err_register = _safe_import("pages.auth_register")
page_dashboard, err_dashboard = _safe_import("pages.dashboard")
page_logout, err_logout = _safe_import("pages.logout")


# =========================
#        VISTAS
# =========================

# ---- ESTADO DEL EQUIPO (coach) ----
def _coach_team_status_layout_v2(coach_sport, roster_count, team_count, roster_tab, teams_tab):
    return html.Div([
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(coach_sport or "Deporte de combate", className="session-pill"),
                    html.Span("Coach", className="session-pill session-pill--muted"),
                ]),
                html.H2("Estado del equipo"),
                html.P(
                    "Aquí preparas tu plantilla y tus equipos antes de pasar al seguimiento individual.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Lo más útil aquí", className="card-title"),
                html.P(
                    "Primero reúne a tus deportistas en plantilla y después arma los grupos con los que vas a trabajar.",
                    className="text-muted",
                ),
                html.Ul([
                    html.Li([html.Strong("Plantilla actual: "), f"{roster_count} atleta{'s' if roster_count != 1 else ''}"]),
                    html.Li([html.Strong("Equipos activos: "), str(team_count)]),
                    html.Li([html.Strong("Primero: "), "revisa o amplía tu plantilla."]),
                    html.Li([html.Strong("Después: "), "crea equipos y asigna miembros según cómo quieras trabajar."]),
                ], className="list-compact"),
            ]),
        ]),
        html.Div(className="kpis profile-kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Atletas en plantilla", className="kpi-label"),
                html.Div(str(roster_count), className="kpi-value"),
                html.Div("Base actual de deportistas bajo tu seguimiento", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Equipos activos", className="kpi-label"),
                html.Div(str(team_count), className="kpi-value"),
                html.Div("Grupos que ya puedes usar para organizar trabajo", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Vista actual", className="kpi-label"),
                html.Div("Organización", className="kpi-value", style={"fontSize": "22px"}),
                html.Div("Plantilla y equipos listos para trabajar con más orden", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),
        html.Div(className="card", children=[
            html.H4("Paso recomendado", className="card-title"),
            html.P(
                "Si todavía no tienes un grupo creado, empieza por ahí. Si ya lo tienes, pasa a gestionar sus miembros.",
                className="text-muted",
            ),
            html.Ul([
                html.Li("Mis deportistas: buscar, añadir y limpiar la plantilla."),
                html.Li("Equipos: crear grupos y asignar miembros según el contexto de trabajo."),
            ], className="list-compact"),
        ]),
        dcc.Tabs(
            id="tabs-coach-users",
            value="tab-roster",
            className="combatiq-tabs",
            children=[
                dcc.Tab(label="Mis deportistas", value="tab-roster",
                        className="combatiq-tab", selected_className="combatiq-tab--active",
                        children=[roster_tab]),
                dcc.Tab(label="Equipos", value="tab-teams",
                        className="combatiq-tab", selected_className="combatiq-tab--active",
                        children=[teams_tab]),
            ]
        ),
    ], className="page-content profile-shell")


# ---- USUARIOS ----
def view_usuarios():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    user_id = session.get("user_id")

    sports_base = ["Taekwondo", "Box"]
    sports_opts = [{"label": s, "value": s} for s in sports_base]

    # Coach solo busca en su propio deporte — no tiene sentido buscar fuera
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None
    if role == "coach" and coach_sport:
        sports_opts_search = [{"label": coach_sport, "value": coach_sport}]
    else:
        sports_opts_search = [{"label": "Cualquiera", "value": ""}] + sports_opts

    # =========================
    # COACH: roster + equipos
    # =========================
    if role == "coach" and user_id:
        coach_id = int(user_id)

        roster = _coach_roster(coach_id)
        roster_opts = [{"label": f"{a['name']} ({a.get('sport') or '-'})", "value": a["id"]} for a in roster]

        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]

        _roster_display = [{**a, "created_at": (a.get("created_at") or "")[:10]} for a in roster]

        def team_fold(title, hint, body_children, open_by_default=False):
            return html.Details(
                className="card collapsible-card team-collapsible-card",
                open=open_by_default,
                children=[
                    html.Summary(
                        className="collapsible-card__summary",
                        children=[
                            html.Div(
                                className="collapsible-card__head",
                                children=[
                                    html.H4(title, className="card-title"),
                                    html.P(hint, className="text-muted"),
                                ],
                            ),
                            html.Span(">", className="collapsible-card__chevron"),
                        ],
                    ),
                    html.Div(className="collapsible-card__body", children=body_children),
                ],
            )

        roster_tab = html.Div(className="coach-stack", children=[
            team_fold(
                "Buscar deportista",
                "Busca por nombre y añade deportistas a tu plantilla.",
                [
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Nombre"),
                            dcc.Input(id="coach-search-text", type="text", placeholder="Buscar por nombre...", className="filter-input"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Deporte"),
                            dcc.Dropdown(
                                id="coach-search-sport",
                                options=sports_opts_search,
                                value=coach_sport or "",
                                disabled=bool(coach_sport),
                                placeholder=coach_sport or "Cualquiera",
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Button("Buscar", id="btn-coach-search", n_clicks=0,
                                        className="btn btn-primary btn-full-mt"),
                        ]),
                    ]),
                    html.Div(id="coach-search-msg", className="text-muted form-msg--below"),
                    dcc.Store(id="search-results-store", data=[]),
                    html.Div(id="search-results-container"),
                ],
                open_by_default=False,
            ),
            team_fold(
                "Mi plantilla",
                f"Aquí ves y mantienes a tus {len(_roster_display)} deportistas actuales.",
                [
                    html.Div(id="plantilla-list", children=_render_plantilla(_roster_display)),
                    html.Div(className="btn-save-row", children=[
                        dcc.Dropdown(
                            id="plantilla-remove-dropdown",
                            options=roster_opts,
                            placeholder="Selecciona deportista a retirar...",
                            clearable=True,
                        ),
                        html.Button("Retirar de la plantilla", id="btn-roster-remove", n_clicks=0, className="btn btn-ghost"),
                        html.Div(id="coach-roster-msg", className="text-danger"),
                    ]),
                ],
                open_by_default=True,
            ),
        ])

        roster_tab = html.Div(className="coach-stack", children=[
            team_fold(
                "Buscar deportista",
                "Busca por nombre y añade deportistas a tu plantilla.",
                [
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Nombre"),
                            dcc.Input(id="coach-search-text", type="text", placeholder="Buscar por nombre...", className="filter-input"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Deporte"),
                            dcc.Dropdown(
                                id="coach-search-sport",
                                options=sports_opts_search,
                                value=coach_sport or "",
                                disabled=bool(coach_sport),
                                placeholder=coach_sport or "Cualquiera",
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Button("Buscar", id="btn-coach-search", n_clicks=0,
                                        className="btn btn-primary btn-full-mt"),
                        ]),
                    ]),
                    html.Div(id="coach-search-msg", className="text-muted form-msg--below"),
                    dcc.Store(id="search-results-store", data=[]),
                    html.Div(id="search-results-container"),
                ],
                open_by_default=False,
            ),
            team_fold(
                "Mi plantilla",
                f"Aquí ves a quién tienes en seguimiento y puedes retirarlo si hace falta.",
                [
                    html.Div(id="plantilla-list", children=_render_plantilla(_roster_display)),
                    html.Div(className="btn-save-row", children=[
                        dcc.Dropdown(
                            id="plantilla-remove-dropdown",
                            options=roster_opts,
                            placeholder="Selecciona deportista a retirar...",
                            clearable=True,
                        ),
                        html.Button("Retirar de la plantilla", id="btn-roster-remove", n_clicks=0, className="btn btn-ghost"),
                        html.Div(id="coach-roster-msg", className="text-danger"),
                    ]),
                ],
                open_by_default=True,
            ),
        ])

        teams_tab = html.Div(className="coach-stack", children=[
            team_fold(
                "Crear equipo",
                "Ponle nombre al grupo y créalo antes de asignar deportistas.",
                [
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Nombre del equipo"),
                            dcc.Input(id="team-name", type="text", placeholder="Ej. Élite senior", className="filter-input"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Deporte (opcional)"),
                            dcc.Dropdown(id="team-sport", options=sports_opts_search, value="", placeholder="Cualquiera"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Button("Crear equipo", id="btn-team-create", n_clicks=0,
                                        className="btn btn-primary btn-full-mt"),
                        ]),
                    ]),
                    html.Div(id="team-create-msg", className="text-danger form-msg"),
                ],
                open_by_default=False,
            ),
            team_fold(
                "Gestionar miembros",
                "Selecciona un equipo y añade o retira deportistas según cómo quieras trabajar.",
                [
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Equipo"),
                            dcc.Dropdown(id="team-select", options=team_opts, placeholder="Selecciona un equipo"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Atleta de la plantilla"),
                            dcc.Dropdown(id="team-add-athlete", options=roster_opts, placeholder="Selecciona deportista"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Button("Agregar al equipo", id="btn-team-add-member", n_clicks=0,
                                        className="btn btn-primary btn-full-mt"),
                        ]),
                    ]),
                    html.Div(id="team-msg", className="text-muted form-msg--below"),
                    dcc.Store(id="team-members-store", data=[]),
                    html.H4("Miembros del equipo", className="card-title", style={"marginTop": "18px"}),
                    html.Div(id="team-members-container"),
                    html.P(
                        "Añade primero al deportista a la plantilla antes de asignarlo a un equipo.",
                        className="text-muted", style={"marginTop": "10px"},
                    ),
                ],
                open_by_default=True,
            ),
        ])

        _roster_count = len(roster)
        _team_count   = len(teams) if teams else 0

        return _coach_team_status_layout_v2(
            coach_sport=coach_sport,
            roster_count=_roster_count,
            team_count=_team_count,
            roster_tab=roster_tab,
            teams_tab=teams_tab,
        )

    # =========================
    # DEPORTISTA: ver su coach
    # =========================
    if role == "deportista" and user_id:
        coach = db.get_user_coach(int(user_id))

        # --- Compañeros de equipo ---
        _QUOTES = [
            ("El campeón no se hace en el ring. Se revela ahí.", "Joe Louis"),
            ("No entrenes hasta que puedas hacerlo bien. Entrena hasta que no puedas hacerlo mal.", "Anónimo"),
            ("El dolor es temporal. Rendirse dura para siempre.", "Anónimo"),
            ("En el combate, los que aguantan son los que ganaron la batalla antes de entrar.", "Hélio Gracie"),
            ("La disciplina es elegir entre lo que quieres ahora y lo que quieres más.", "Abraham Lincoln"),
            ("Un guerrero no se rinde si todavía puede luchar.", "Kyūzō Mifune"),
            ("La fuerza no viene del cuerpo. Viene de la voluntad indomable.", "Mahatma Gandhi"),
            ("Todos los campeones fueron alguna vez contendientes que no aceptaron rendirse.", "Rocky Balboa"),
        ]
        _q_idx = session.get("quote_idx", datetime.now().day) % len(_QUOTES)
        _quote_text, _quote_src = _QUOTES[_q_idx]

        teams_with_mates = []
        if coach:
            try:
                all_teams = db.list_teams(int(coach["id"])) if hasattr(db, "list_teams") else []
                for _t in all_teams:
                    _members = db.list_team_members(_t["id"]) if hasattr(db, "list_team_members") else []
                    if any(m["athlete_id"] == int(user_id) for m in _members):
                        _mates = [m for m in _members if m["athlete_id"] != int(user_id)]
                        teams_with_mates.append({"team": _t, "teammates": _mates})
            except Exception:
                teams_with_mates = []

        def _initials(n):
            parts = (n or "?").split()
            return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

        # --- Card del coach ---
        if not coach:
            coach_card = html.Div(
                className="card",
                children=[
                    html.H4("Tu coach", className="card-title"),
                    html.Div(
                        className="inner-card data-empty-state",
                        children=[
                            html.P("Aún no tienes un coach asignado.", className="empty-state-title"),
                            html.P("Pide a tu coach que te añada a su plantilla desde su panel.", className="text-muted"),
                        ],
                    ),
                ],
            )
        else:
            coach_name  = coach.get("name") or "Coach"
            coach_sport = coach.get("sport") or "—"
            coach_since = (coach.get("created_at") or "")[:10] or "—"
            coach_card = html.Div(
                className="card",
                children=[
                    html.H4("Tu coach", className="card-title"),
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="inner-cell", children=[
                            html.Div("Nombre", className="kpi-label"),
                            html.Div(coach_name, className="kpi-value"),
                        ]),
                        html.Div(className="inner-cell", children=[
                            html.Div("Deporte", className="kpi-label"),
                            html.Div(coach_sport, className="kpi-value"),
                        ]),
                        html.Div(className="inner-cell", children=[
                            html.Div("En la plataforma desde", className="kpi-label"),
                            html.Div(coach_since, className="kpi-value"),
                        ]),
                    ]),
                    html.Div(className="btn-save-row", children=[
                        dcc.Link(
                            html.Button("Contactar con el coach", className="btn btn-primary"),
                            href="/contacto", className="link-btn",
                        ),
                        dcc.Link(
                            html.Button("Actualizar mi check-in", className="btn btn-ghost"),
                            href="/cuestionario", className="link-btn",
                        ),
                    ]),
                ],
            )

        # --- Cards de equipo + compañeros ---
        if teams_with_mates:
            team_cards = []
            for entry in teams_with_mates:
                _team  = entry["team"]
                _mates = entry["teammates"]
                _mate_count = len(_mates)
                if _mates:
                    chips = html.Div(
                        className="teammate-chips",
                        children=[
                            html.Div(className="teammate-chip", children=[
                                html.Span(_initials(m["name"]), className="teammate-chip__avatar"),
                                html.Span(m["name"], className="teammate-chip__name"),
                            ]) for m in _mates
                        ],
                    )
                else:
                    chips = html.P("Aún no hay otros miembros en este equipo.", className="text-muted")

                team_cards.append(html.Div(
                    className="card",
                    children=[
                        html.Div(className="card-header-row", children=[
                            html.H4(_team.get("name") or "Mi equipo", className="card-title"),
                            html.Span(
                                f"{_mate_count} compañero{'s' if _mate_count != 1 else ''}",
                                className="count-badge",
                            ),
                        ]),
                        chips,
                    ],
                ))
            team_block = html.Div(className="coach-stack", children=team_cards)
        else:
            team_block = html.Div(
                className="card",
                children=[
                    html.H4("Mi equipo", className="card-title"),
                    html.Div(
                        className="inner-card data-empty-state",
                        children=[
                            html.P("Aún no formas parte de ningún equipo.", className="empty-state-title"),
                            html.P("Tu coach puede añadirte a un equipo desde su panel.", className="text-muted"),
                        ],
                    ),
                ],
            )

        return html.Div([
            html.Div(className="page-head", children=[
                html.H2("Mi coach y equipo"),
                html.P("Tu tribu · Comparte el camino.", className="text-muted"),
            ]),
            html.Div(className="ecg-divider ecg-divider--spaced"),
            html.Div(className="coach-stack", children=[
                coach_card,
                team_block,
                html.Div(className="quote-card", children=[
                    f'"{_quote_text}"',
                    html.Span(f"— {_quote_src}", className="quote-card__source"),
                ]),
                html.Div(className="btn-save-row", children=[
                    dcc.Link(html.Button("Ver mis sesiones", className="btn btn-ghost"),
                             href="/sesion", className="link-btn"),
                    dcc.Link(html.Button("Ir a análisis", className="btn btn-ghost"),
                             href="/ecg", className="link-btn"),
                ]),
            ]),
        ])

    # =========================
    # ADMIN: gestión de usuarios
    # =========================
    users = db.list_users()

    table = DataTable(
        id="tbl-users",
        data=users,
        columns=[
            {"name": "ID", "id": "id"},
            {"name": "Nombre", "id": "name"},
            {"name": "Rol", "id": "role"},
            {"name": "Deporte", "id": "sport"},
            {"name": "Alta", "id": "created_at"}
        ],
        page_size=8, style_table={"overflowX": "auto"},
        sort_action="native", filter_action="native",
    )

    add_controls = html.Div(className="filters-bar filters-bar--3", children=[
        html.Div(className="filter-item", children=[
            html.Label("Nombre completo"),
            dcc.Input(id="in-name", type="text", placeholder="Nombre completo", className="filter-input"),
        ]),
        html.Div(className="filter-item", children=[
            html.Label("Deporte"),
            dcc.Dropdown(id="in-sport", options=sports_opts, placeholder="Deporte"),
        ]),
        html.Div(className="filter-item", children=[
            html.Button("Añadir usuario", id="btn-add", n_clicks=0, className="btn btn-primary btn-full-mt"),
        ]),
    ])

    delete_controls = html.Div(className="filters-bar filters-bar--2", children=[
        html.Div(className="filter-item", children=[
            html.Label("Usuario a eliminar"),
            dcc.Dropdown(
                id="in-del-user",
                options=[{"label": f"{u['name']} ({u.get('role', '?')})", "value": u["id"]} for u in users],
                placeholder="Selecciona usuario"
            ),
        ]),
        html.Div(className="filter-item", children=[
            html.Button("Eliminar", id="btn-del", n_clicks=0, className="btn btn-danger btn-full-mt"),
        ]),
    ])

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Gestión de usuarios"),
            html.P(
                "Da de alta o elimina usuarios. Los coaches gestionan su plantilla y equipos desde su propia sección.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(className="coach-stack", children=[
            html.Div(className="card", children=[
                html.H4("Añadir usuario", className="card-title"),
                html.Div(add_controls),
            ]),
            html.Div(className="card", children=[
                html.H4("Eliminar usuario", className="card-title"),
                html.Div(delete_controls),
            ]),
            html.Div(className="card", children=[
                html.H4("Todos los usuarios", className="card-title"),
                html.Div(className="dt-pro", children=[table]),
                html.Div(id="users-msg", className="text-danger form-msg"),
            ]),
        ]),
    ])


@app.callback(
    Output("tbl-users", "data", allow_duplicate=True),
    Output("in-del-user", "options", allow_duplicate=True),
    Output("users-msg", "children"),
    Input("btn-add", "n_clicks"),
    Input("btn-del", "n_clicks"),
    State("in-name", "value"),
    State("in-sport", "value"),
    State("in-del-user", "value"),
    prevent_initial_call=True
)
def user_actions(n_add, n_del, name, sport, del_user_id):
    role = _to_str(session.get("role")) or "no autenticado"

    # A partir de ahora, SOLO admin puede crear/eliminar usuarios (evitamos "usuarios rápidos" del coach).
    if role != "admin":
        users = []
        options = []
        return users, options, "No tienes permisos para modificar usuarios."

    trig = [t["prop_id"] for t in callback_context.triggered][0]
    msg = ""

    if "btn-add" in trig:
        if not name:
            msg = "Nombre requerido."
        else:
            db.add_user(name, sport, role="deportista", coach_id=None)
            msg = "Usuario añadido."

    elif "btn-del" in trig:
        if not del_user_id:
            msg = "Selecciona usuario a eliminar."
        else:
            db.delete_user(int(del_user_id))
            msg = "Usuario eliminado."

    users = db.list_users()
    options = [{"label": f"{u['name']} ({u.get('role', '?')})", "value": u["id"]} for u in users]
    return users, options, msg

# =========================
# COACH: Roster + Equipos
# =========================


def _fallback_search_athletes(text: str = "", sport: str = None, limit: int = 50):
    """Fallback si tu db.py aún no trae search_athletes()."""
    rows = []
    try:
        rows = db.list_users() or []
    except Exception:
        rows = []
    rows = [u for u in rows if (u.get("role", "deportista") == "deportista")]

    t = (text or "").strip().lower()
    s = (sport or "").strip().lower() if sport else None

    out = []
    for u in rows:
        name = (u.get("name") or "").lower()
        usport = (u.get("sport") or "").lower()
        if t and t not in name:
            continue
        if s and s != usport:
            continue
        out.append(u)
        if len(out) >= int(limit):
            break
    return out


@app.callback(
    Output("search-results-container", "children"),
    Output("search-results-store", "data"),
    Output("coach-search-msg", "children"),
    Input("btn-coach-search", "n_clicks"),
    State("coach-search-text", "value"),
    State("coach-search-sport", "value"),
    prevent_initial_call=True
)
def coach_search_athletes(n, text, sport):
    if _to_str(session.get("role")) != "coach":
        return None, [], "Inicia sesión como coach."
    if not n:
        raise PreventUpdate

    text = (text or "").strip()
    sport_filter = (sport or "").strip() or None

    # Coach siempre busca en su propio deporte — ignora lo que venga del dropdown
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None
    if coach_sport:
        sport_filter = coach_sport

    try:
        if hasattr(db, "search_athletes"):
            results = db.search_athletes(text=text, sport=sport_filter, limit=50) or []
        else:
            results = _fallback_search_athletes(text=text, sport=sport_filter, limit=50)
    except Exception:
        results = _fallback_search_athletes(text=text, sport=sport_filter, limit=50)

    results_display = [{**r, "created_at": (r.get("created_at") or "")[:10]} for r in results]

    try:
        coach_id = int(session.get("user_id"))
        roster = _coach_roster(coach_id)
        roster_ids = {a["id"] for a in roster}
    except Exception:
        roster_ids = set()

    return (
        _render_search_results(results_display, roster_ids),
        results_display,
        f"{len(results)} deportista(s) encontrado(s).",
    )


def _render_team_members(members):
    """Renderiza los miembros de un equipo como cards con botón Quitar directo."""
    if not members:
        return html.Div(
            html.P("Este equipo aún no tiene miembros.", className="text-muted"),
            style={"padding": "12px 0"},
        )

    def _ini(name):
        parts = (name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    _ABBREV = {"Taekwondo": "TKD", "Box": "BOX", "Boxeo": "BOX"}

    cards = []
    for m in members:
        aid = m.get("athlete_id") or m.get("id")
        sport = m.get("sport") or ""
        since = (m.get("added_at") or m.get("created_at") or "")[:10]
        cards.append(
            html.Div(className="athlete-row", children=[
                html.Div(_ini(m.get("name", "?")), className="athlete-row__avatar"),
                html.Div(className="athlete-row__info", children=[
                    html.Div(m.get("name", "—"), className="athlete-row__name"),
                    html.Div(f"{sport} · desde {since}" if since else sport, className="athlete-row__meta"),
                ]),
                html.Span(
                    _ABBREV.get(sport, sport[:3].upper()),
                    className=f"sport-badge sport-badge--{sport.lower()}",
                ),
                html.Button(
                    "Quitar",
                    id={"type": "remove-member-btn", "index": aid},
                    className="btn btn-ghost btn-xs",
                    n_clicks=0,
                ),
            ])
        )
    return html.Div(className="plantilla-list", style={"marginTop": "8px"}, children=cards)


def _render_search_results(results, roster_ids=None):
    """Renderiza los resultados de búsqueda como cards con botón directo por atleta."""
    roster_ids = set(roster_ids or [])
    if not results:
        return html.Div(
            html.P("Sin resultados. Prueba con otro nombre o deporte.", className="text-muted"),
            style={"padding": "16px 0"},
        )

    def _ini(name):
        parts = (name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    _ABBREV = {"Taekwondo": "TKD", "Box": "BOX", "Boxeo": "BOX"}

    cards = []
    for r in results:
        aid = r.get("id")
        sport = r.get("sport") or ""
        in_roster = aid in roster_ids
        cards.append(
            html.Div(className="athlete-row", children=[
                html.Div(_ini(r.get("name", "?")), className="athlete-row__avatar"),
                html.Div(className="athlete-row__info", children=[
                    html.Div(r.get("name", "—"), className="athlete-row__name"),
                    html.Div(sport, className="athlete-row__meta"),
                ]),
                html.Span(
                    _ABBREV.get(sport, sport[:3].upper()),
                    className=f"sport-badge sport-badge--{sport.lower()}",
                ),
                html.Button(
                    "✓ En plantilla" if in_roster else "+ Añadir",
                    id={"type": "add-athlete-btn", "index": aid},
                    className="btn btn-ghost btn-xs" if in_roster else "btn btn-primary btn-xs",
                    disabled=in_roster,
                    n_clicks=0,
                ),
            ])
        )
    return html.Div(className="plantilla-list", style={"marginTop": "12px"}, children=cards)


def _render_plantilla(roster_display):
    """Genera la lista visual de atletas de la plantilla."""
    if not roster_display:
        return html.Div(className="inner-card data-empty-state", children=[
            html.P("Tu plantilla está vacía.", className="empty-state-title"),
            html.P("Busca deportistas arriba y añádelos a tu plantilla.", className="text-muted"),
        ])

    def _ini(name):
        parts = (name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    _ABBREV = {"Taekwondo": "TKD", "Box": "BOX", "Boxeo": "BOX"}

    return html.Div(className="plantilla-list", children=[
        html.Div(className="athlete-row", children=[
            html.Div(_ini(a.get("name", "?")), className="athlete-row__avatar"),
            html.Div(className="athlete-row__info", children=[
                html.Div(a.get("name", "—"), className="athlete-row__name"),
                html.Div(
                    f"{a.get('sport', '—')} · desde {a.get('created_at', '—')}",
                    className="athlete-row__meta",
                ),
            ]),
            html.Span(
                _ABBREV.get(a.get("sport", ""), (a.get("sport") or "")[:3].upper()),
                className=f"sport-badge sport-badge--{(a.get('sport') or '').lower()}",
            ),
        ]) for a in roster_display
    ])


def _refresh_roster_and_opts(coach_id: int):
    roster = _coach_roster(int(coach_id))
    roster_opts = [{"label": f"{a['name']} ({a.get('sport') or '-'})", "value": a["id"]} for a in roster]
    roster_display = [{**a, "created_at": (a.get("created_at") or "")[:10]} for a in roster]
    return roster_display, roster_opts


@app.callback(
    Output("plantilla-list", "children"),
    Output("plantilla-remove-dropdown", "options"),
    Output("team-add-athlete", "options"),
    Output("coach-roster-msg", "children"),
    Output("search-results-container", "children", allow_duplicate=True),
    Input({"type": "add-athlete-btn", "index": ALL}, "n_clicks"),
    State("search-results-store", "data"),
    prevent_initial_call=True
)
def add_athlete_to_roster(n_clicks_list, stored_results):
    if not any(n for n in (n_clicks_list or []) if n):
        raise PreventUpdate

    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate

    import json
    try:
        prop_id = ctx.triggered[0]["prop_id"]
        btn_id = json.loads(prop_id.rsplit(".", 1)[0])
        athlete_id = int(btn_id["index"])
    except Exception:
        raise PreventUpdate

    if _to_str(session.get("role")) != "coach":
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        raise PreventUpdate

    try:
        if hasattr(db, "adopt_athlete_set_primary_if_empty"):
            db.adopt_athlete_set_primary_if_empty(coach_id, athlete_id)
        elif hasattr(db, "adopt_athlete"):
            db.adopt_athlete(coach_id, athlete_id)
        elif hasattr(db, "_get_conn"):
            with db._get_conn() as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE users SET coach_id=COALESCE(coach_id, ?) WHERE id=?",
                    (int(coach_id), int(athlete_id))
                )
                con.commit()
    except Exception:
        pass

    roster, roster_opts = _refresh_roster_and_opts(coach_id)
    roster_ids = {a["id"] for a in _coach_roster(coach_id)}
    updated_search = _render_search_results(stored_results or [], roster_ids)
    return _render_plantilla(roster), roster_opts, roster_opts, "Deportista añadido a la plantilla.", updated_search


@app.callback(
    Output("plantilla-list", "children", allow_duplicate=True),
    Output("plantilla-remove-dropdown", "options", allow_duplicate=True),
    Output("team-add-athlete", "options", allow_duplicate=True),
    Output("coach-roster-msg", "children", allow_duplicate=True),
    Input("btn-roster-remove", "n_clicks"),
    State("plantilla-remove-dropdown", "value"),
    prevent_initial_call=True
)
def coach_remove_from_roster(n, athlete_id_selected):
    if _to_str(session.get("role")) != "coach":
        return _render_plantilla([]), [], [], "No tienes permisos."
    if not n:
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return _render_plantilla([]), [], [], "Sesión inválida. Vuelve a iniciar sesión."

    if not athlete_id_selected:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return _render_plantilla(roster), roster_opts, roster_opts, "Selecciona un deportista del desplegable."

    try:
        athlete_id = int(athlete_id_selected)
    except Exception:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return _render_plantilla(roster), roster_opts, roster_opts, "Selección inválida."

    try:
        if hasattr(db, "remove_adopted_athlete"):
            db.remove_adopted_athlete(coach_id, athlete_id)
        if hasattr(db, "_get_conn"):
            with db._get_conn() as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE users SET coach_id=NULL WHERE id=? AND coach_id=?",
                    (int(athlete_id), int(coach_id))
                )
                con.commit()
    except Exception:
        pass

    roster, roster_opts = _refresh_roster_and_opts(coach_id)
    return _render_plantilla(roster), roster_opts, roster_opts, "Deportista retirado de la plantilla."


@app.callback(
    Output("team-select", "options"),
    Output("team-select", "value"),
    Output("team-create-msg", "children"),
    Input("btn-team-create", "n_clicks"),
    State("team-name", "value"),
    State("team-sport", "value"),
    prevent_initial_call=True
)
def coach_create_team(n, name, sport):
    if _to_str(session.get("role")) != "coach":
        return [], None, "No tienes permisos."
    if not n:
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return [], None, "Sesión inválida. Vuelve a iniciar sesión."

    name = (name or "").strip()
    sport_val = (sport or "").strip() or None

    if not name:
        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
        return team_opts, None, "Nombre de equipo requerido."

    if not hasattr(db, "create_team"):
        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
        return team_opts, None, "Tu db.py todavía no soporta equipos (create_team)."

    new_id = None
    try:
        new_id = db.create_team(coach_id, name, sport_val)
    except Exception:
        new_id = None

    teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
    team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
    return team_opts, new_id, "Equipo creado." if new_id else "No se pudo crear el equipo."


def _fmt_members(rows):
    return [{**r, "added_at": (r.get("added_at") or "")[:10]} for r in (rows or [])]


@app.callback(
    Output("team-members-container", "children"),
    Output("team-members-store", "data"),
    Input("team-select", "value"),
)
def coach_load_team_members(team_id):
    if not team_id:
        return html.Div(html.P("Selecciona un equipo para ver sus miembros.", className="text-muted"),
                        style={"padding": "12px 0"}), []
    if not hasattr(db, "list_team_members"):
        return _render_team_members([]), []
    try:
        members = _fmt_members(db.list_team_members(int(team_id)))
        return _render_team_members(members), members
    except Exception:
        return _render_team_members([]), []


@app.callback(
    Output("team-members-container", "children", allow_duplicate=True),
    Output("team-members-store", "data", allow_duplicate=True),
    Output("team-msg", "children"),
    Input("btn-team-add-member", "n_clicks"),
    State("team-select", "value"),
    State("team-add-athlete", "value"),
    prevent_initial_call=True
)
def coach_add_team_member(n, team_id, athlete_id):
    if _to_str(session.get("role")) != "coach":
        return dash.no_update, dash.no_update, "No tienes permisos."
    if not n:
        raise PreventUpdate
    if not team_id:
        return dash.no_update, dash.no_update, "Selecciona un equipo."
    if not athlete_id:
        return dash.no_update, dash.no_update, "Selecciona un deportista."
    if not hasattr(db, "add_team_member"):
        return dash.no_update, dash.no_update, "db.py no soporta equipos aún (add_team_member)."
    try:
        db.add_team_member(int(team_id), int(athlete_id), role_label=None)
    except Exception:
        pass
    members = _fmt_members(db.list_team_members(int(team_id))) if hasattr(db, "list_team_members") else []
    return _render_team_members(members), members, "Miembro agregado."


@app.callback(
    Output("team-members-container", "children", allow_duplicate=True),
    Output("team-members-store", "data", allow_duplicate=True),
    Output("team-msg", "children", allow_duplicate=True),
    Input({"type": "remove-member-btn", "index": ALL}, "n_clicks"),
    State("team-select", "value"),
    prevent_initial_call=True
)
def coach_remove_team_member(n_clicks_list, team_id):
    if not any(n for n in (n_clicks_list or []) if n):
        raise PreventUpdate

    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate

    import json
    try:
        prop_id = ctx.triggered[0]["prop_id"]
        btn_id = json.loads(prop_id.rsplit(".", 1)[0])
        athlete_id = int(btn_id["index"])
    except Exception:
        raise PreventUpdate

    if not team_id:
        raise PreventUpdate
    if not hasattr(db, "remove_team_member"):
        return dash.no_update, dash.no_update, "db.py no soporta equipos aún (remove_team_member)."
    try:
        db.remove_team_member(int(team_id), int(athlete_id))
    except Exception:
        pass
    members = _fmt_members(db.list_team_members(int(team_id))) if hasattr(db, "list_team_members") else []
    return _render_team_members(members), members, "Miembro retirado del equipo."

def _athlete_sheet_fold(title, hint, body_children, open_by_default=False):
    return html.Details(
        className="card collapsible-card",
        open=open_by_default,
        children=[
            html.Summary(
                className="collapsible-card__summary",
                children=[
                    html.Div(
                        className="collapsible-card__head",
                        children=[
                            html.H4(title, className="card-title"),
                            html.P(hint, className="text-muted"),
                        ],
                    ),
                    html.Span(">", className="collapsible-card__chevron"),
                ],
            ),
            html.Div(className="collapsible-card__body", children=body_children),
        ],
    )


def view_deportista_v2():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        return html.Div("No tienes permisos para ver esta sección (solo coach).", className="muted")

    coach_id = session.get("user_id")
    coach_sport = (_to_str(session.get("sport")) or "").strip() or "Deporte de combate"
    athletes = _coach_roster(int(coach_id)) if coach_id else []
    options_users = [
        {"label": f"{u['name']} | {u.get('sport', '-')}", "value": u["id"]}
        for u in athletes
    ]
    default_val = options_users[0]["value"] if options_users else None

    return html.Div([
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(coach_sport, className="session-pill"),
                    html.Span("Coach", className="session-pill session-pill--muted"),
                ]),
                html.H2("Ficha de atleta"),
                html.P(
                    "Aquí revisas el contexto de cada deportista antes de ajustar carga, seguimiento o análisis.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Qué puedes resolver aquí", className="card-title"),
                html.P(
                    "Elige a un atleta y confirma rápido cómo llega, qué conviene vigilar y qué dato te falta para decidir mejor.",
                    className="text-muted",
                ),
                html.Ul([
                    html.Li([html.Strong("Plantilla disponible: "), f"{len(options_users)} atleta{'s' if len(options_users) != 1 else ''}"]),
                    html.Li([html.Strong("Primero: "), "elige a quién quieres revisar hoy."]),
                    html.Li([html.Strong("Después: "), "entra al detalle solo en lo que necesites confirmar."]),
                ], className="list-compact"),
            ]),
        ]),
        html.Div(className="card", children=[
            html.H4("Seleccionar deportista", className="card-title"),
            html.P(
                "Busca aquí al atleta que quieres revisar sin salir de esta vista.",
                className="text-muted",
            ),
            html.Div(className="filter-item", children=[
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="athlete-select-v2",
                    options=options_users,
                    value=default_val,
                    placeholder="Selecciona deportista de tu plantilla...",
                ),
            ]),
        ]),
        html.Div(id="athlete-card-v2", style={"marginTop": "16px"}),
    ], className="page-content profile-shell")


@app.callback(
    Output("athlete-card-v2", "children"),
    Input("athlete-select-v2", "value"),
    prevent_initial_call=False
)
def render_athlete_card_v2(user_id):
    role = _to_str(session.get("role")) or "no autenticado"
    coach_id = session.get("user_id")

    if role != "coach":
        return None

    try:
        selected_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        selected_id = None

    if not selected_id:
        return html.Div(className="inner-card data-empty-state", children=[
            html.P("Selecciona un deportista de tu plantilla.", className="empty-state-title"),
            html.P("Aquí verás su contexto deportivo, su lectura reciente y lo más importante para hoy.", className="text-muted"),
        ])

    athletes = _coach_roster(int(coach_id)) if coach_id else []
    if not any(int(a["id"]) == selected_id for a in athletes if a.get("id") is not None):
        return html.Div("Este deportista no pertenece a tu equipo.", className="text-muted")

    u = db.get_user_by_id(selected_id)
    if not u:
        return html.Div("No se encontró el deportista seleccionado.")

    name = _profile_label(u.get("name"), "Sin nombre")
    sport = _profile_label(u.get("sport"), "Deporte sin definir")
    created = (u.get("created_at") or "")[:10] or "-"
    email = u.get("email") or None

    try:
        qrows = db.list_questionnaires(selected_id) or []
    except Exception:
        qrows = []
    last_q = qrows[0] if qrows else None
    wellness = float(last_q["wellness_score"]) if last_q and last_q.get("wellness_score") is not None else None
    q_date = (last_q.get("ts") or "").replace("T", " ")[:16] if last_q else None
    latest_answers = {}
    if last_q and last_q.get("answers_json"):
        try:
            raw_answers = last_q.get("answers_json") or "{}"
            latest_answers = json.loads(raw_answers) if isinstance(raw_answers, str) else (raw_answers or {})
        except Exception:
            latest_answers = {}

    try:
        last_ecg = db.get_last_ecg_metrics(selected_id)
    except Exception:
        last_ecg = None

    try:
        sens_codes = db.get_user_sensors(selected_id) or []
    except Exception:
        sens_codes = []
    sens_labels = [S.catalog()[c]["name"] for c in sens_codes if c in S.catalog()]

    try:
        ap = db.get_athlete_profile(selected_id) if hasattr(db, "get_athlete_profile") else _profile_defaults()
    except Exception:
        ap = _profile_defaults()

    try:
        weekly_summary = db.get_weekly_load_summary(selected_id) if hasattr(db, "get_weekly_load_summary") else {}
    except Exception:
        weekly_summary = {}

    try:
        recent_sessions = db.list_sessions(selected_id, limit=3) if hasattr(db, "list_sessions") else []
    except Exception:
        recent_sessions = []

    abbrev = {"Taekwondo": "TKD", "Box": "BOX", "Boxeo": "BOX"}
    weekly_flag_labels = {
        "green": "Semana sólida",
        "yellow": "Semana con atención",
        "red": "Carga a revisar",
        "gray": "Sin datos",
    }
    trend_labels = {
        "up": "Carga subiendo",
        "down": "Carga bajando",
        "stable": "Carga estable",
    }

    def initials(full_name):
        parts = (full_name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    def fmt_datetime(value, fallback="Sin registro"):
        if not value:
            return fallback
        return str(value).replace("T", " ")[:16]

    def readiness_state(score):
        if score is None:
            return "Sin check-in", "Todavía no hay lectura del día."
        if score < 50:
            return "Día para ajustar", "Conviene bajar exigencia y revisar molestias antes de apretar."
        if score < 70:
            return "Listo con control", "Hay base para trabajar, pero conviene vigilar recuperación y sensación corporal."
        return "Listo para trabajar", "Tiene una base sólida para sostener la sesión de hoy."

    wellness_value = f"{wellness:.0f}/100" if wellness is not None else "Sin datos"
    readiness_title, readiness_hint = readiness_state(wellness)
    bpm_val = f"{last_ecg.get('bpm', 0):.0f} bpm" if last_ecg and last_ecg.get("bpm") is not None else "Sin ECG"
    sdnn_val = f"{last_ecg.get('sdnn', 0):.0f} ms" if last_ecg and last_ecg.get("sdnn") is not None else "Sin dato"
    rmssd_val = f"{last_ecg.get('rmssd', 0):.0f} ms" if last_ecg and last_ecg.get("rmssd") is not None else "Sin dato"
    session_count = weekly_summary.get("n_sessions", 0)
    load_units = weekly_summary.get("load_units")
    weekly_load = f"{load_units} UA" if load_units is not None else "Sin carga"
    weekly_flag = (weekly_summary.get("flag") or "gray").strip().lower()
    trend = (weekly_summary.get("trend") or "stable").strip().lower()
    level_chip = _profile_label(ap.get("competitive_level"), "Seguimiento activo")
    current_status = _profile_label(ap.get("current_status"), "Sin estado definido")
    watch_zone = _profile_label(ap.get("watch_zone"), "Sin zona marcada")
    competition_proximity = _profile_label(ap.get("competition_proximity"), "Sin competencia cercana")
    weekly_flag_label = weekly_flag_labels.get(weekly_flag, "Sin datos")
    trend_label = trend_labels.get(trend, "Carga estable")
    sensor_value = f"{len(sens_labels)} activo{'s' if len(sens_labels) != 1 else ''}" if sens_labels else "Sin sensores"

    hero = html.Div(className="profile-hero-grid", children=[
        html.Div(className="page-head profile-hero", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport, className="session-pill"),
                html.Span(level_chip, className="session-pill session-pill--muted"),
            ]),
            html.Div(className="athlete-profile-header", children=[
                html.Div(className="athlete-row__avatar athlete-row__avatar--lg", children=initials(name)),
                html.Div(className="athlete-row__info", children=[
                    html.Div(name, className="athlete-row__name athlete-name--lg"),
                    html.Div(f"{sport} | en seguimiento desde {created}", className="athlete-row__meta"),
                ]),
                html.Span(
                    abbrev.get(sport, sport[:3].upper()),
                    className=f"sport-badge sport-badge--{sport.lower()}",
                ),
            ]),
            html.P(
                "Aquí confirmas cómo llega hoy, qué arrastra de contexto y dónde conviene poner la atención antes de decidir.",
                className="text-muted",
            ),
        ]),
        html.Div(className="card profile-focus-card", children=[
            html.H4("Qué conviene mirar primero", className="card-title"),
            html.P(
                "Empieza por el estado del día y luego confirma recuperación, carga reciente y cualquier zona a vigilar.",
                className="text-muted",
            ),
            html.Ul([
                html.Li([html.Strong("Estado del día: "), f"{readiness_title} ({wellness_value})"]),
                html.Li([html.Strong("Último check-in: "), q_date or "Sin registro todavía"]),
                html.Li([html.Strong("Recuperación cardiovascular: "), bpm_val]),
                html.Li([html.Strong("Atención actual: "), watch_zone if watch_zone != "Sin zona marcada" else competition_proximity]),
            ], className="list-compact"),
        ]),
    ])

    metrics = html.Div(className="kpis profile-kpis", children=[
        html.Div(className="kpi", children=[
            html.Div("Estado del día", className="kpi-label"),
            html.Div(wellness_value, className="kpi-value"),
            html.Div(readiness_hint, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Recuperación cardiovascular", className="kpi-label"),
            html.Div(bpm_val, className="kpi-value"),
            html.Div(f"SDNN {sdnn_val} | RMSSD {rmssd_val}", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Semana actual", className="kpi-label"),
            html.Div(str(session_count), className="kpi-value"),
            html.Div(f"{weekly_load} | {trend_label}", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Sensores activos", className="kpi-label"),
            html.Div(sensor_value, className="kpi-value", style={"fontSize": "22px"}),
            html.Div("Disponibles para seguir recuperación y movimiento", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ])

    context_fold = _athlete_sheet_fold(
        "Resumen del atleta",
        "Aquí ves el contexto base con el que conviene leer su día.",
        [
            html.Div(className="profile-context-grid", children=[
                html.Div(className="profile-grid-item", children=[
                    html.Div("Estado actual", className="kpi-label"),
                    html.Div(current_status, className="kpi-value", style={"fontSize": "18px"}),
                ]),
                html.Div(className="profile-grid-item", children=[
                    html.Div("Nivel competitivo", className="kpi-label"),
                    html.Div(level_chip, className="kpi-value", style={"fontSize": "18px"}),
                ]),
                html.Div(className="profile-grid-item", children=[
                    html.Div("Categoría / peso", className="kpi-label"),
                    html.Div(_profile_label(ap.get("weight_category")), className="kpi-value", style={"fontSize": "18px"}),
                ]),
                html.Div(className="profile-grid-item", children=[
                    html.Div("Lado dominante", className="kpi-label"),
                    html.Div(_profile_label(ap.get("dominant_side")), className="kpi-value", style={"fontSize": "18px"}),
                ]),
                html.Div(className="profile-grid-item", children=[
                    html.Div("Zona a vigilar", className="kpi-label"),
                    html.Div(watch_zone, className="kpi-value", style={"fontSize": "18px"}),
                ]),
                html.Div(className="profile-grid-item", children=[
                    html.Div("Competencia", className="kpi-label"),
                    html.Div(competition_proximity, className="kpi-value", style={"fontSize": "18px"}),
                ]),
            ]),
            html.Div(className="profile-note", children=[
                html.Strong("Nota de contexto: "),
                html.Span(_profile_label(ap.get("profile_note"), "Todavía no hay una nota añadida para este deportista.")),
            ]),
        ],
        open_by_default=True,
    )

    recent_session_items = []
    for session_row in recent_sessions[:3]:
        session_date = fmt_datetime(session_row.get("ts_start"), "Fecha pendiente")
        session_status = "abierta" if (session_row.get("status") or "").strip().lower() == "open" else "cerrada"
        session_sport = _profile_label(session_row.get("sport"), sport)
        recent_session_items.append(
            html.Li(f"{session_date} | {session_sport} | sesión {session_status}")
        )
    if not recent_session_items:
        recent_session_items = [html.Li("Todavía no hay sesiones recientes registradas.")]

    signal_fold = _athlete_sheet_fold(
        "Seguimiento reciente",
        "Aquí confirmas lo último que dejó el deportista antes de decidir.",
        [
            html.Ul([
                html.Li([html.Strong("Check-in reciente: "), q_date or "Sin registro todavía"]),
                html.Li([html.Strong("Lectura del día: "), f"{readiness_title} | {wellness_value}"]),
                html.Li([html.Strong("Semana actual: "), f"{session_count} sesiones | {weekly_load} | {weekly_flag_label}"]),
                html.Li([html.Strong("Tendencia de carga: "), trend_label]),
            ], className="list-compact"),
            html.Div(className="spacer-10"),
            html.H4("Últimas sesiones", className="card-title"),
            html.Ul(recent_session_items, className="list-compact"),
        ],
        open_by_default=True,
    )

    if latest_answers:
        answer_items = [
            html.Li(f"{str(k).replace('_', ' ').capitalize()}: {v}")
            for k, v in list(latest_answers.items())[:4]
        ]
    else:
        answer_items = [html.Li("Todavía no hay respuestas recientes para ampliar el contexto del día.")]

    detail_fold = _athlete_sheet_fold(
        "Puntos finos del contexto",
        "Detalle adicional del último cuestionario y del contexto reciente.",
        [
            html.H4("Señales del último cuestionario", className="card-title"),
            html.Ul(answer_items, className="list-compact"),
        ],
        open_by_default=False,
    )

    resources_card = html.Div(className="card profile-links-card", children=[
        html.H4("Contacto y acciones útiles", className="card-title"),
        html.P(
            "Correo, sensores y accesos rápidos para seguir el caso con más contexto.",
            className="text-muted",
        ),
        html.Div(className="profile-note", children=[
            html.Strong("Correo: "),
            html.Span(email or "No disponible"),
        ]),
        html.Div(className="spacer-10"),
        html.H4("Sensores asignados", className="card-title"),
        html.Div(className="teammate-chips", children=[
            html.Span(lbl, className="teammate-chip") for lbl in sens_labels
        ]) if sens_labels else html.P("No tiene sensores asignados todavía.", className="text-muted"),
        html.Div(className="spacer-10"),
        html.Div(className="row-wrap-10 session-action-row", children=[
            html.A("Enviar correo", href=f"mailto:{email}", className="btn btn-primary") if email else None,
            dcc.Link(html.Button("Ir a análisis", className="btn btn-ghost"), href="/ecg"),
            dcc.Link(html.Button("Abrir comparativa", className="btn btn-ghost"), href="/comparar"),
            dcc.Link(html.Button("Volver al equipo", className="btn btn-ghost"), href="/usuarios"),
        ]),
    ])

    return html.Div(className="coach-stack", children=[
        hero,
        metrics,
        html.Div(className="profile-main-grid", children=[
            html.Div(className="profile-stack", children=[
                context_fold,
                detail_fold,
            ]),
            html.Div(className="profile-stack", children=[
                signal_fold,
                resources_card,
            ]),
        ]),
    ])


# ---- ANUNCIOS AL EQUIPO (para coach) ----
def view_anuncios():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        return html.Div("No tienes permisos para ver esta sección (solo coach).", className="muted")

    coach_id = session.get("user_id")
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None

    # Usa filtro de deporte del coach
    athletes = []
    if coach_id:
        try:
            import inspect as _inspect
            for _fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
                if hasattr(db, _fn):
                    _sig = _inspect.signature(getattr(db, _fn))
                    if "sport" in _sig.parameters:
                        athletes = getattr(db, _fn)(int(coach_id), sport=coach_sport) or []
                    else:
                        athletes = getattr(db, _fn)(int(coach_id)) or []
                    if athletes:
                        break
        except Exception:
            athletes = []
    if coach_sport:
        athletes = [a for a in athletes if not a.get("sport") or a["sport"] == coach_sport]

    emails = [u.get("email") or u.get("correo") for u in athletes if u.get("email") or u.get("correo")]
    mailto_link = f"mailto:?bcc={','.join(emails)}" if emails else "#"
    sport_label = coach_sport.title() if coach_sport else "tu equipo"

    # Filas de atleta con email
    def _athlete_row(u):
        addr = u.get("email") or u.get("correo") or ""
        name = u.get("name") or "Sin nombre"
        return html.Div(
            className="btn-save-row",
            style={"padding": "8px 0", "borderBottom": "1px solid var(--line)"},
            children=[
                html.Span(name, style={"fontWeight": "600", "minWidth": "160px", "display": "inline-block"}),
                html.Span(addr or "Sin correo", className="text-muted", style={"fontSize": "13px"}),
            ],
        )

    athlete_rows = [_athlete_row(u) for u in athletes] if athletes else [
        html.P("No hay deportistas en tu plantilla todavía.", className="text-muted")
    ]

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Comunicados"),
            html.P(
                f"Contacta con los {len(athletes)} deportistas de {sport_label} por correo electrónico.",
                className="text-muted",
            ),
        ]),
        html.Hr(className="ecg-divider"),

        # Card: correos del equipo
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Correos del equipo", className="card-title"),
            html.P(
                "Lista de deportistas con correo registrado. Puedes copiarlos o usar el botón de abajo.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            html.Div(athlete_rows),
        ]),

        # Card: acción
        html.Div(className="card", children=[
            html.H4("Redactar mensaje", className="card-title"),
            html.P(
                "Abre tu cliente de correo con todos los destinatarios en BCC para enviar el comunicado.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            html.Div(className="btn-save-row", children=[
                html.A(
                    "Abrir cliente de correo (BCC)",
                    href=mailto_link,
                    className="btn btn-primary" if emails else "btn btn-primary btn-disabled",
                    style={"pointerEvents": "auto" if emails else "none", "opacity": 1.0 if emails else 0.4},
                ),
                html.Span(
                    f"{len(emails)} destinatario{'s' if len(emails) != 1 else ''}" if emails else "Sin destinatarios",
                    className="text-muted",
                    style={"fontSize": "12px"},
                ),
            ]),
            html.P(
                "Tip: más adelante podrás enviar avisos directamente desde la app con historial de mensajes.",
                className="text-muted",
                style={"marginTop": "12px", "fontSize": "12px"},
            ),
        ]),
    ])


# ---- CONTACTO COACH (para deportista) ----
def view_contacto_coach():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div("Esta sección está pensada para deportistas.", className="muted")

    user_id = session.get("user_id")
    uid_int = int(user_id) if user_id else None

    coach = db.get_user_coach(uid_int) if uid_int else None

    if coach:
        options = [{
            "label": f"{coach['name']} ({coach.get('sport', '-')})",
            "value": coach["id"],
        }]
        default_val = coach["id"]
        helper = "Este es tu coach asignado en CombatIQ."
    else:
        coaches = db.list_coaches()
        options = [{
            "label": f"{u['name']} ({u.get('sport', '-')})",
            "value": u["id"],
        } for u in coaches]
        default_val = options[0]["value"] if options else None
        helper = "Todavía no tienes un coach asignado. Selecciona uno de la lista para contactar."

    # ── Último check-in del deportista ───────────────────────────────────
    last_score = None
    last_date  = "Sin registros"
    last_note  = ""
    try:
        qs = db.list_questionnaires(uid_int) or []
        if qs:
            q0 = qs[0]
            last_score = q0.get("wellness_score")
            ts = (q0.get("ts") or "")[:16].replace("T", " ")
            last_date = ts or "Sin fecha"
            last_note = q0.get("notes") or q0.get("nota") or ""
    except Exception:
        pass

    score_txt = f"{float(last_score):.0f} / 100" if last_score is not None else "Sin datos"

    # ── Perfil deportivo del atleta ───────────────────────────────────────
    athlete_profile = {}
    try:
        if uid_int and hasattr(db, "get_athlete_profile"):
            athlete_profile = db.get_athlete_profile(uid_int) or {}
    except Exception:
        pass

    sport        = _to_str(session.get("sport")) or "—"
    proximity    = athlete_profile.get("competition_proximity") or "—"
    watch_zone   = athlete_profile.get("watch_zone") or ""
    athlete_name = _to_str(session.get("name")) or "Deportista"

    # ── mailto prerellenado ──────────────────────────────────────────────
    subject = f"Estado del día — {athlete_name} — {last_date}"
    body_lines = [
        f"Hola coach,",
        f"",
        f"Te comparto mi estado de hoy:",
        f"  · Deporte: {sport}",
        f"  · Bienestar: {score_txt}",
        f"  · Cercanía a competencia: {proximity}",
    ]
    if watch_zone:
        body_lines.append(f"  · Zona a vigilar: {watch_zone}")
    if last_note:
        body_lines.append(f"  · Nota: {last_note}")
    body_lines += ["", "Quedo atento a tus indicaciones.", athlete_name]
    mailto_body = urllib.parse.quote("\n".join(body_lines))
    mailto_subject = urllib.parse.quote(subject)

    coach_email = coach.get("email") or coach.get("correo") if coach else None
    mailto_href = (
        f"mailto:{coach_email}?subject={mailto_subject}&body={mailto_body}"
        if coach_email else "#"
    )

    # ── Tarjeta de estado del día ─────────────────────────────────────────
    score_color = (
        "#27c98f" if last_score is not None and float(last_score) >= 70
        else "#f0a832" if last_score is not None and float(last_score) >= 45
        else "#e45a5a" if last_score is not None
        else None
    )
    estado_card = html.Div(className="card", style={"marginBottom": "16px"}, children=[
        html.H4("Tu estado de hoy", className="card-title"),
        html.P("Esto es lo que le vas a compartir a tu coach.", className="text-muted",
               style={"marginBottom": "14px"}),
        html.Div(className="filters-bar filters-bar--3", children=[
            html.Div(className="filter-item", children=[
                html.Div("Bienestar", className="kpi-label"),
                html.Div(
                    score_txt,
                    style={"fontWeight": "800", "fontSize": "20px", "marginTop": "4px",
                           "color": score_color or "var(--ink)"},
                ),
                html.Div(last_date, className="text-muted", style={"fontSize": "12px", "marginTop": "2px"}),
            ]),
            html.Div(className="filter-item", children=[
                html.Div("Deporte", className="kpi-label"),
                html.Div(sport, style={"fontWeight": "700", "marginTop": "4px"}),
                html.Div(proximity, className="text-muted", style={"fontSize": "12px", "marginTop": "2px"}),
            ]),
            html.Div(className="filter-item", children=[
                html.Div("Zona a vigilar", className="kpi-label"),
                html.Div(
                    watch_zone if watch_zone else "Sin zona registrada",
                    style={"fontWeight": "700", "marginTop": "4px"},
                    className="" if watch_zone else "text-muted",
                ),
            ]),
        ]),
        html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "marginTop": "16px"}, children=[
            html.A(
                "Compartir mi estado por correo",
                href=mailto_href,
                className="btn btn-primary",
            ),
            dcc.Link(
                html.Button("Actualizar check-in", className="btn btn-ghost"),
                href="/cuestionario",
            ),
        ]) if coach_email else html.P(
            "Completa el check-in para tener tu estado listo.",
            className="text-muted",
        ),
    ])

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Contacto con mi coach"),
            html.P(helper, className="text-muted"),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # ── Selector de coach ────────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Tu coach", className="card-title"),
            html.Div(className="filter-item", children=[
                html.Label("Selecciona coach"),
                dcc.Dropdown(
                    id="coach-select",
                    options=options,
                    value=default_val,
                    placeholder="Selecciona coach...",
                ),
            ]),
        ]),

        # ── Ficha del coach (dinámica via callback) ──────────────────────
        html.Div(id="coach-contact-card", style={"marginBottom": "16px"}),

        # ── Estado del día del deportista ────────────────────────────────
        estado_card,
    ])


@app.callback(
    Output("coach-contact-card", "children"),
    Input("coach-select", "value"),
    prevent_initial_call=False
)
def render_coach_contact_card(coach_id):
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div("No tienes permisos para usar esta sección.", className="muted")

    if not coach_id:
        return html.Div("Selecciona un coach en el desplegable de arriba.")

    user_id = session.get("user_id")
    assigned = db.get_user_coach(int(user_id)) if user_id else None
    if assigned and assigned["id"] == coach_id:
        u = assigned
    else:
        coaches = db.list_coaches()
        u = next((x for x in coaches if x["id"] == coach_id), None)

    if not u:
        return html.Div("No se encontró el coach seleccionado.")

    name = u.get("name", "Coach")
    sport = u.get("sport", "—")
    email = u.get("email") or u.get("correo")

    return html.Div(className="card", children=[
        html.H4(name, className="card-title"),
        html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "16px"}, children=[
            html.Div(className="filter-item", children=[
                html.Div("Deporte", className="kpi-label"),
                html.Div(sport, style={"fontWeight": "700", "marginTop": "4px"}),
            ]),
            html.Div(className="filter-item", children=[
                html.Div("Correo", className="kpi-label"),
                html.Div(
                    html.A(email, href=f"mailto:{email}", style={"color": "var(--neon)"}),
                    style={"marginTop": "4px"},
                ) if email else html.Div("No disponible", className="text-muted", style={"marginTop": "4px"}),
            ]),
        ]),
        html.A(
            "Escribir al coach",
            href=f"mailto:{email}",
            className="btn btn-primary",
        ) if email else html.P(
            "Este coach no tiene email registrado. Contacta con el administrador de la plataforma.",
            className="text-muted",
        ),
    ])


# === NUEVA VISTA: SESIÓN RÁPIDA (DEPORTISTA) ===

def _readiness_badge(score):
    """
    Traduce un score a una lectura rápida y deportiva.
    """
    try:
        s = float(score)
    except Exception:
        return "Sin check-in reciente", "Conviene completar el estado del día antes de interpretar la sesión."

    if s >= 80:
        return "Listo para apretar", "Buen punto de partida para una sesión exigente si la técnica acompaña."
    if s >= 60:
        return "Listo con control", "Hay base para entrenar bien, pero conviene vigilar ritmo y recuperación."
    if s >= 40:
        return "Día para ajustar carga", "Mejor priorizar calidad, control del ritmo y decisiones finas."
    return "Día de vigilancia", "Conviene bajar exigencia, revisar sensaciones y evitar forzar la sesión."


def _session_blueprint_for_sport(sport: str, role: str = "deportista"):
    """
    Plantillas visibles de contexto de sesión.
    No persiste nada; solo aterriza la narrativa del producto con lenguaje deportivo.

    CS-012: separa el tipo de sesión del objetivo principal del día para que
    la lectura sea más operativa y más fiel al producto.
    """
    sport_norm = (sport or "").strip().lower()

    defaults = {
        "tipo": "Sesión de entrenamiento",
        "objetivo_principal": "Técnica",
        "objetivo_desc": "Hoy conviene priorizar calidad de ejecución y control de la carga.",
        "estructura": "Por bloques · activación + bloque principal + vuelta a la calma.",
        "lectura": "Revisa cómo llegas hoy antes de empujar intensidad.",
        "coach": "Úsala para decidir si hoy conviene técnica, volumen o una sesión más controlada.",
        "modo": "bloques",
        "detalle_titulo": "Bloques sugeridos",
        "detalle": [
            "Activación · 10 min",
            "Bloque principal · 20–30 min",
            "Vuelta a la calma · 5–8 min",
        ],
        "nota": "Estructura flexible para días de técnica, carga moderada o reajuste.",
        "objetivos_secundarios": ["Volumen", "Recuperación"],
    }

    sport_map = {
        "taekwondo": {
            "tipo": "Sesión técnica con rounds",
            "objetivo_principal": "Técnica",
            "objetivo_desc": "Afinar distancia, timing y explosividad sin perder control.",
            "estructura": "Por rounds · 3 a 5 rounds de trabajo con descanso corto entre esfuerzos.",
            "lectura": "La lectura útil es ritmo de combate, explosividad y recuperación entre rounds.",
            "coach": "Antes de empujar intensidad, confirma readiness del día y caída de rendimiento entre rounds.",
            "modo": "rounds",
            "detalle_titulo": "Rounds sugeridos",
            "detalle": [
                "Round 1 · activación técnica · 2 min",
                "Round 2–4 · bloque principal · 2 min",
                "Descanso entre rounds · 45–60 s",
            ],
            "nota": "Pensado para técnica, sparring controlado o simulación ligera.",
            "objetivos_secundarios": ["Intensidad", "Simulación", "Evaluación"],
        },
        "box": {
            "tipo": "Sesión de boxeo por rounds",
            "objetivo_principal": "Intensidad",
            "objetivo_desc": "Sostener ritmo, precisión y control del esfuerzo en bloque competitivo.",
            "estructura": "Por rounds · trabajo técnico o saco con pausas claras entre rounds.",
            "lectura": "Aquí importa ver ritmo, intensidad máxima y recuperación entre rounds.",
            "coach": "Te sirve para decidir si hoy el foco es volumen, ritmo de combate o trabajo más técnico.",
            "modo": "rounds",
            "detalle_titulo": "Rounds sugeridos",
            "detalle": [
                "Round 1 · sombra / técnica · 3 min",
                "Round 2–5 · saco o manoplas · 3 min",
                "Descanso entre rounds · 60 s",
            ],
            "nota": "Útil para ritmo de combate, precisión y control del esfuerzo.",
            "objetivos_secundarios": ["Volumen", "Simulación", "Evaluación"],
        },
        "kickboxing": {
            "tipo": "Sesión mixta de striking",
            "objetivo_principal": "Intensidad",
            "objetivo_desc": "Sostener combinaciones con buena intensidad y control técnico.",
            "estructura": "Por rounds o bloques · técnica + bloque principal + descarga.",
            "lectura": "Prioriza lectura de intensidad, coordinación y recuperación.",
            "coach": "Confirma si hoy toca apretar o mantener calidad sin sobreactivar carga.",
            "modo": "bloques",
            "detalle_titulo": "Bloques sugeridos",
            "detalle": [
                "Bloque 1 · técnica combinada · 10 min",
                "Bloque 2 · rounds de striking · 12–16 min",
                "Bloque 3 · descarga y movilidad · 6 min",
            ],
            "nota": "Combina trabajo técnico y esfuerzo alto sin perder control de la carga.",
            "objetivos_secundarios": ["Técnica", "Volumen", "Recuperación"],
        },
        "judo": {
            "tipo": "Sesión técnica por bloques",
            "objetivo_principal": "Volumen",
            "objetivo_desc": "Construir calidad de agarre, entrada y esfuerzo repetido.",
            "estructura": "Por bloques · movilidad + entradas técnicas + bloque principal.",
            "lectura": "La sesión debe mostrar ritmo de trabajo y tolerancia al esfuerzo repetido.",
            "coach": "Úsala para decidir si el día está para volumen técnico o una carga más controlada.",
            "modo": "bloques",
            "detalle_titulo": "Bloques sugeridos",
            "detalle": [
                "Bloque 1 · movilidad y agarres · 8 min",
                "Bloque 2 · entradas técnicas · 12 min",
                "Bloque 3 · bloque principal · 10–15 min",
            ],
            "nota": "Pensado para acumular calidad técnica y esfuerzo repetido con control.",
            "objetivos_secundarios": ["Técnica", "Evaluación", "Recuperación"],
        },
        "mma": {
            "tipo": "Sesión integrada de combate",
            "objetivo_principal": "Simulación",
            "objetivo_desc": "Ordenar transiciones y sostener esfuerzo sin romper técnica.",
            "estructura": "Formato libre guiado · técnica + situaciones + bloque principal.",
            "lectura": "Ayuda a ver si el día está para sumar estímulos o mantener control.",
            "coach": "Úsala para decidir cuánto abrir el bloque principal y cuánto proteger la técnica.",
            "modo": "libre",
            "detalle_titulo": "Estructura sugerida",
            "detalle": [
                "Duración estimada total · 45–60 min",
                "Secuencia flexible según objetivo del día",
                "Nota abierta para ajustes por coach",
            ],
            "nota": "Formato útil cuando la sesión mezcla técnica, lucha y striking en el mismo día.",
            "objetivos_secundarios": ["Intensidad", "Evaluación", "Técnica"],
        },
    }

    base = sport_map.get(sport_norm, defaults)
    return {
        "tipo": base["tipo"],
        "objetivo_principal": base["objetivo_principal"],
        "objetivo_desc": base["objetivo_desc"],
        "estructura": base["estructura"],
        "lectura": base["lectura"] if role == "deportista" else base["coach"],
        "modo": base["modo"],
        "detalle_titulo": base["detalle_titulo"],
        "detalle": base["detalle"],
        "nota": base["nota"],
        "objetivos_secundarios": base.get("objetivos_secundarios", []),
    }


def _session_structure_chip(modo: str):
    labels = {
        "rounds": "Estructura · rounds",
        "bloques": "Estructura · bloques",
        "libre": "Estructura · libre",
    }
    return labels.get((modo or "").strip().lower(), "Estructura · sesión")


def _session_recommendations(blueprint: dict, readiness_score=None, role: str = "deportista"):
    """
    CS-013: recomendaciones visibles de sesión.
    Regresa recomendaciones breves, deportivas, contextuales y no clínicas,
    conectadas a tipo de sesión, objetivo principal y readiness del día.
    """
    objetivo = (blueprint or {}).get("objetivo_principal", "")
    modo = (blueprint or {}).get("modo", "")

    try:
        score = float(readiness_score) if readiness_score is not None else None
    except Exception:
        score = None

    recs = []

    if score is None:
        recs.append(("informativa", "Completa tu check-in antes de sacar conclusiones de carga o recuperación."))
    elif score >= 80:
        recs.append(("acción sugerida", "Buen día para empujar la sesión si la técnica se mantiene estable."))
    elif score >= 60:
        recs.append(("informativa", "Hay base para trabajar bien hoy, pero conviene vigilar cómo respondes durante la sesión."))
    elif score >= 40:
        recs.append(("vigilancia", "Conviene priorizar calidad y controlar la carga antes de subir intensidad."))
    else:
        recs.append(("vigilancia", "Día para proteger sensaciones, bajar exigencia y evitar forzar el bloque principal."))

    objetivo_map = {
        "Técnica": ("acción sugerida", "Enfócate en calidad de ejecución, distancia y timing antes que en volumen."),
        "Intensidad": ("acción sugerida", "Aprieta en esfuerzos clave, pero revisa si la recuperación entre bloques se sostiene."),
        "Volumen": ("informativa", "Busca consistencia y ritmo estable para que la sesión sume sin romper la técnica."),
        "Simulación": ("acción sugerida", "Trata la sesión como contexto competitivo y observa si el ritmo se sostiene hasta el final."),
        "Evaluación": ("informativa", "Mantén condiciones parecidas entre bloques para que la lectura del día sea comparable."),
        "Recuperación": ("vigilancia", "Usa la sesión para soltar, recuperar sensaciones y salir mejor de lo que entraste."),
    }
    if objetivo in objetivo_map:
        recs.append(objetivo_map[objetivo])

    modo_map = {
        "rounds": ("informativa", "Lee la sesión round a round para detectar caída de rendimiento y recuperación entre esfuerzos."),
        "bloques": ("informativa", "Compara cómo responde el cuerpo entre bloques antes de decidir si subir o sostener la carga."),
        "libre": ("informativa", "Como la estructura es más flexible, apóyate más en sensaciones y en la lectura final de la sesión."),
    }
    if modo in modo_map:
        recs.append(modo_map[modo])

    if role == "coach":
        recs.append(("acción sugerida", "Cruza estas señales con análisis e histórico antes de ajustar la carga del día."))
    else:
        recs.append(("acción sugerida", "Después de la sesión, entra a análisis e histórico para validar cómo respondió tu cuerpo."))

    out = []
    seen = set()
    for kind, text in recs:
        key = (kind.strip().lower(), text.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"tipo": kind, "texto": text})
    return out[:4]


# === SESIÓN COMO HUB CONTEXTUAL ===
def _coach_jornada_layout_v2(uid_int: int, sport: str):
    """
    CS-COACH-002B - Mi jornada compacta v1
    Reduce la sensación de saturación usando bloques desplegables sin tocar
    la lógica operativa de la jornada.
    """
    coach_id = uid_int
    roster = _coach_roster(int(coach_id)) if coach_id else []
    athlete_count = len(roster)
    latest_checkins = 0
    athletes_with_ecg = 0
    focus_names = []
    pending_names = []

    for athlete in roster[:]:
        aid = athlete.get("id")
        athlete_name = athlete.get("name") or "Deportista"
        if aid is None:
            continue

        has_checkin = False
        try:
            qrows = db.list_questionnaires(int(aid)) or []
            has_checkin = bool(qrows)
            if has_checkin:
                latest_checkins += 1
        except Exception:
            has_checkin = False

        has_ecg = False
        try:
            has_ecg = bool(db.get_last_ecg_metrics(int(aid)))
            if has_ecg:
                athletes_with_ecg += 1
        except Exception:
            has_ecg = False

        if (has_checkin or has_ecg) and len(focus_names) < 4:
            focus_names.append(athlete_name)
        if not has_checkin and len(pending_names) < 4:
            pending_names.append(athlete_name)

    ref_sport = sport or ((roster[0].get("sport") if roster else "") or "")
    blueprint = _session_blueprint_for_sport(ref_sport, role="coach")
    recommendations = _session_recommendations(blueprint, None, role="coach")
    session_chip = _session_structure_chip(blueprint["modo"])
    focus_preview = ", ".join(focus_names[:3]) if focus_names else "Todavía no hay deportistas priorizados con lectura reciente."

    def journey_fold(title, hint, body_children, open_by_default=False):
        return html.Details(
            className="card collapsible-card",
            open=open_by_default,
            children=[
                html.Summary(
                    className="collapsible-card__summary",
                    children=[
                        html.Div(
                            className="collapsible-card__head",
                            children=[
                                html.H4(title, className="card-title"),
                                html.P(hint, className="text-muted"),
                            ],
                        ),
                        html.Span(">", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(className="collapsible-card__body", children=body_children),
            ],
        )

    return html.Div(
        [
            html.Div(
                className="session-hero-grid",
                children=[
                    html.Div(
                        className="page-head session-hero",
                        children=[
                            html.Div(
                                className="session-pill-row",
                                children=[
                                    html.Span(ref_sport or "Deporte de combate", className="session-pill"),
                                    html.Span(session_chip, className="session-pill session-pill--muted"),
                                ],
                            ),
                            html.H2("Mi jornada"),
                            html.P(
                                "Aquí ordenas el día del equipo: a quién revisar primero, cómo abrir la sesión y qué confirmar antes de ajustar la carga.",
                                className="text-muted",
                            ),
                        ],
                    ),
                    html.Div(
                        className="card session-focus-card",
                        children=[
                            html.H4("Qué conviene revisar primero", className="card-title"),
                            html.P(
                                "Empieza por quienes ya dejaron una lectura útil para tomar decisiones con mejor contexto.",
                                className="text-muted",
                            ),
                            html.Ul(
                                [
                                    html.Li([html.Strong("Lecturas del día: "), f"{latest_checkins} de {athlete_count}"]),
                                    html.Li([html.Strong("Señales listas: "), f"{athletes_with_ecg} de {athlete_count}"]),
                                    html.Li([html.Strong("Prioridad rápida: "), focus_preview]),
                                ],
                                className="list-compact",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="kpis session-kpis",
                children=[
                    html.Div(
                        className="kpi",
                        children=[
                            html.Div("Plan guía del día", className="kpi-label"),
                            html.Div(blueprint["tipo"], className="kpi-value", style={"fontSize": "22px"}),
                            html.Div(blueprint["objetivo_desc"], className="kpi-sub"),
                            html.Div(className="kpi-ecg-line"),
                        ],
                    ),
                    html.Div(
                        className="kpi",
                        children=[
                            html.Div("Lecturas listas", className="kpi-label"),
                            html.Div(f"{latest_checkins} / {athlete_count}", className="kpi-value"),
                            html.Div("Deportistas con check-in reciente para abrir la jornada con contexto.", className="kpi-sub"),
                            html.Div(className="kpi-ecg-line"),
                        ],
                    ),
                    html.Div(
                        className="kpi",
                        children=[
                            html.Div("Señales listas", className="kpi-label"),
                            html.Div(f"{athletes_with_ecg} / {athlete_count}", className="kpi-value"),
                            html.Div("Base disponible para revisar recuperación y respuesta al esfuerzo.", className="kpi-sub"),
                            html.Div(className="kpi-ecg-line"),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="session-main-grid",
                children=[
                    html.Div(
                        className="session-stack",
                        children=[
                            journey_fold(
                                "Cómo abrir la jornada",
                                f"{ref_sport or 'Deporte de combate'} | {session_chip}",
                                [
                                    html.Ul(
                                        [
                                            html.Li([html.Strong("Objetivo principal: "), blueprint["objetivo_principal"]]),
                                            html.Li([html.Strong("Lo importante hoy: "), blueprint["objetivo_desc"]]),
                                            html.Li([html.Strong("Estructura sugerida: "), blueprint["estructura"]]),
                                            html.Li([html.Strong("En qué fijarte primero: "), blueprint["lectura"]]),
                                        ],
                                        className="list-compact",
                                    ),
                                ],
                                open_by_default=True,
                            ),
                            journey_fold(
                                "Orden de revisión",
                                "Primero lecturas recientes y después pendientes del día.",
                                [
                                    html.P(
                                        "Primero entra con quienes ya tienen check-in o señal reciente. Así validas rápido cómo llega el grupo y dónde conviene afinar.",
                                        className="text-muted",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.Ul(
                                        [html.Li(name) for name in focus_names] if focus_names else [html.Li("Todavía no hay deportistas con lectura reciente para priorizar.")],
                                        className="list-compact",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.P(
                                        "Después conviene revisar a quienes todavía no dejaron contexto del día.",
                                        className="text-muted",
                                    ),
                                    html.Ul(
                                        [html.Li(name) for name in pending_names] if pending_names else [html.Li("El equipo ya tiene check-in reciente.")],
                                        className="list-compact",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.Div(
                                        "También podrías orientar la jornada hacia: "
                                        + (", ".join(blueprint["objetivos_secundarios"]) if blueprint["objetivos_secundarios"] else "Sin objetivos secundarios definidos."),
                                        className="text-muted",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="session-stack",
                        children=[
                            journey_fold(
                                "Qué decidir hoy",
                                "Mantén visible el criterio del día y abre el detalle solo cuando haga falta.",
                                [
                                    html.P(
                                        "No hace falta abrir todo de golpe. Entra por el contexto, confirma la respuesta del equipo y luego baja al detalle solo si hace falta.",
                                        className="text-muted",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.Ul(
                                        [
                                            html.Li("1. Confirma quién llega con mejor base para el bloque principal."),
                                            html.Li("2. Revisa señales y recuperación antes de subir intensidad o volumen."),
                                            html.Li("3. Usa comparativa e histórico solo para respaldar el ajuste final del día."),
                                        ],
                                        className="list-compact",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.H4("Recomendaciones del día", className="card-title"),
                                    html.Ul(
                                        [
                                            html.Li([
                                                html.Strong(f"{item['tipo'].capitalize()}: "),
                                                item["texto"],
                                            ])
                                            for item in recommendations
                                        ],
                                        className="list-compact",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="card",
                                children=[
                                    html.H4("Acciones rápidas", className="card-title"),
                                    html.P(
                                        "Estas entradas te ayudan a seguir la jornada sin perderte entre módulos.",
                                        className="text-muted",
                                    ),
                                    html.Div(className="spacer-10"),
                                    html.Div(
                                        className="row-wrap-10 session-action-row",
                                        children=[
                                            dcc.Link(html.Button("Abrir estado del equipo", className="btn btn-primary"), href="/usuarios"),
                                            dcc.Link(html.Button("Ir a análisis", className="btn btn-ghost"), href="/ecg"),
                                            dcc.Link(html.Button("Abrir comparativa", className="btn btn-ghost"), href="/comparar"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
        className="page-content session-shell",
    )


def view_sesion():
    """
    CS-009 — Contexto visible de sesión v1
    Refuerza Sesión como unidad central con contexto visible:
    tipo, objetivo, estructura y lectura rápida por rol.
    """
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or "no autenticado"

    if not uid:
        return html.Div(
            [
                html.H2("Sesión"),
                html.P("Inicia sesión para ver el contexto de tu sesión y tu lectura del día."),
            ],
            className="page-content",
        )

    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None

    user = db.get_user_by_id(uid_int) if uid_int else None
    name = user.get("name") if user else "Usuario"
    sport = (user or {}).get("sport") or ""
    blueprint = _session_blueprint_for_sport(sport, role=role)

    # ---------- Deportista ----------
    if role == "deportista":
        last_wellness_text = "Sin registros"
        last_wellness_val = None
        wellness_ts = ""
        try:
            qs = db.list_questionnaires(uid_int) or []
            if qs:
                q0 = qs[0]
                last_wellness_val = q0.get("wellness_score", None)
                ts = q0.get("ts") or ""
                wellness_ts = ts.replace("T", " ")[:16] if ts else ""
                if last_wellness_val is not None:
                    last_wellness_text = f"{float(last_wellness_val):.0f} / 100"
                else:
                    last_wellness_text = wellness_ts or "Sin registros"
        except Exception:
            pass

        readiness_title, readiness_desc = _readiness_badge(last_wellness_val)

        last_bpm = "Sin registros"
        last_hrv_detail = ""
        try:
            hrv = db.get_last_ecg_metrics(uid_int)
            if hrv:
                bpm = hrv.get("bpm", None)
                sdnn = hrv.get("sdnn", None)
                rmssd = hrv.get("rmssd", None)
                if bpm is not None:
                    last_bpm = f"{float(bpm):.0f} bpm"
                parts = []
                if sdnn is not None:
                    parts.append(f"SDNN {float(sdnn):.1f} ms")
                if rmssd is not None:
                    parts.append(f"RMSSD {float(rmssd):.1f} ms")
                last_hrv_detail = " · ".join(parts)
        except Exception:
            pass

        recommendations = _session_recommendations(blueprint, last_wellness_val, role="deportista")
        session_chip = _session_structure_chip(blueprint["modo"])
        next_step = (
            "Responde tu check-in antes de interpretar la carga."
            if last_wellness_val is None
            else "Después, entra a análisis e histórico para confirmar cómo respondió tu cuerpo."
        )

        return html.Div(
            [
                html.Div(
                    className="session-hero-grid",
                    children=[
                        html.Div(
                            className="page-head session-hero",
                            children=[
                                html.Div(
                                    className="session-pill-row",
                                    children=[
                                        html.Span(sport or "Deporte por definir", className="session-pill"),
                                        html.Span(session_chip, className="session-pill session-pill--muted"),
                                    ],
                                ),
                                html.H2("Mi sesión"),
                                html.P(
                                    f"{name}, aquí puedes ver cómo llegas hoy, qué sesión toca y qué te conviene revisar primero.",
                                    className="text-muted",
                                ),
                            ],
                        ),
                        html.Div(
                            className="card session-focus-card",
                            children=[
                                html.H4("Cómo llegas hoy", className="card-title"),
                                html.P(readiness_desc, className="text-muted"),
                                html.Ul(
                                    [
                                        html.Li([html.Strong("Check-in: "), f"{last_wellness_text}{(' | ' + wellness_ts) if wellness_ts else ''}"]),
                                        html.Li([html.Strong("Recuperación cardiovascular: "), last_bpm]),
                                        html.Li([html.Strong("Siguiente paso: "), next_step]),
                                    ],
                                    className="list-compact",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    className="kpis session-kpis",
                    children=[
                        html.Div(
                            className="kpi",
                            children=[
                                html.Div("Tipo de sesión", className="kpi-label"),
                                html.Div(blueprint["tipo"], className="kpi-value", style={"fontSize": "22px"}),
                                html.Div(blueprint["objetivo_desc"], className="kpi-sub"),
                                html.Div(className="kpi-ecg-line"),
                            ],
                        ),
                        html.Div(
                            className="kpi",
                            children=[
                                html.Div("Estado del día", className="kpi-label"),
                                html.Div(readiness_title, className="kpi-value", style={"fontSize": "22px"}),
                                html.Div(
                                    f"{last_wellness_text}{(' | ' + wellness_ts) if wellness_ts else ''}",
                                    className="kpi-sub",
                                ),
                                html.Div(className="kpi-ecg-line"),
                            ],
                        ),
                        html.Div(
                            className="kpi",
                            children=[
                                html.Div("Recuperación / cardio", className="kpi-label"),
                                html.Div(last_bpm, className="kpi-value"),
                                html.Div(
                                    last_hrv_detail or "Carga un ECG en Análisis para completar esta lectura.",
                                    className="kpi-sub",
                                ),
                                html.Div(className="kpi-ecg-line"),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    className="session-main-grid",
                    children=[
                        html.Div(
                            className="session-stack",
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.H4("Qué sesión toca", className="card-title"),
                                        html.P(
                                            f"{sport or 'Deporte no definido'} | {session_chip}",
                                            className="text-muted",
                                            style={"marginBottom": "10px"},
                                        ),
                                        html.Ul(
                                            [
                                                html.Li([html.Strong("Objetivo principal: "), blueprint["objetivo_principal"]]),
                                                html.Li([html.Strong("Qué se busca hoy: "), blueprint["objetivo_desc"]]),
                                                html.Li([html.Strong("Estructura: "), blueprint["estructura"]]),
                                                html.Li([html.Strong("Qué mirar: "), blueprint["lectura"]]),
                                            ],
                                            className="list-compact",
                                        ),
                                    ],
                                ),
                                html.Div(
                                    className="card",
                                    children=[
                                        html.H4(blueprint["detalle_titulo"], className="card-title"),
                                        html.Div(
                                            blueprint["nota"],
                                            className="text-muted",
                                        ),
                                        html.Div(className="spacer-10"),
                                        html.Ul(
                                            [html.Li(item) for item in blueprint["detalle"]],
                                            className="list-compact",
                                        ),
                                        html.Div(className="spacer-10"),
                                        html.Div(
                                            "También podría entrar hoy: "
                                            + (", ".join(blueprint["objetivos_secundarios"]) if blueprint["objetivos_secundarios"] else "Sin objetivos secundarios."),
                                            className="text-muted",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            className="session-stack",
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.H4("Qué te conviene hacer", className="card-title"),
                                        html.Div("Empieza por esta lectura rápida y luego entra al detalle solo cuando haga falta.", className="text-muted"),
                                        html.Div(className="spacer-10"),
                                        html.Ul(
                                            [
                                                html.Li("1. Confirma cómo llegas hoy antes de apretar intensidad."),
                                                html.Li("2. Lee la sesión según su estructura: rounds, bloques o trabajo libre."),
                                                html.Li("3. Después entra a señales e histórico para validar carga y recuperación."),
                                            ],
                                            className="list-compact",
                                        ),
                                        html.Div(className="spacer-10"),
                                        html.H4("Recomendaciones de hoy", className="card-title"),
                                        html.Ul(
                                            [
                                                html.Li([
                                                    html.Strong(f"{item['tipo'].capitalize()}: "),
                                                    item["texto"],
                                                ])
                                                for item in recommendations
                                            ],
                                            className="list-compact",
                                        ),
                                        html.Div(className="spacer-10"),
                                        html.Div(
                                            className="row-wrap-10 session-action-row",
                                            children=[
                                                dcc.Link(html.Button("Responder check-in", className="btn btn-primary"), href="/cuestionario"),
                                                dcc.Link(html.Button("Ir a análisis", className="btn btn-ghost"), href="/ecg"),
                                                dcc.Link(html.Button("Ver histórico", className="btn btn-ghost"), href="/historico"),
                                            ],
                                        ),
                                    ],
                                ),
                                html.Div(
                                    className="card session-rpe-card",
                                    children=[
                                        html.H4("¿Cómo fue la sesión?", className="card-title"),
                                        html.P(
                                            "Registra el esfuerzo percibido y la duración. Esto ayuda a seguir tu carga semanal sin salir de esta vista.",
                                            className="text-muted",
                                        ),
                                        html.Div(
                                            className="filters-bar filters-bar--2",
                                            style={"marginTop": "14px", "marginBottom": "14px"},
                                            children=[
                                                html.Div(className="filter-item", children=[
                                                    html.Label("Esfuerzo percibido (RPE)"),
                                                    dcc.Slider(
                                                        id="rpe-slider",
                                                        min=1, max=10, step=1, value=6,
                                                        marks={
                                                            1: {"label": "1"},
                                                            3: {"label": "Ligero"},
                                                            6: {"label": "Moderado"},
                                                            8: {"label": "Exigente"},
                                                            10: {"label": "Máximo"},
                                                        },
                                                        tooltip={"placement": "top", "always_visible": True},
                                                    ),
                                                ]),
                                                html.Div(className="filter-item", children=[
                                                    html.Label("Duración (minutos)"),
                                                    dcc.Input(
                                                        id="rpe-duration",
                                                        type="number",
                                                        min=0, max=300, step=5,
                                                        placeholder="Ej. 90",
                                                        style={"width": "100%"},
                                                    ),
                                                ]),
                                            ],
                                        ),
                                        html.Div(
                                            className="btn-save-row",
                                            children=[
                                                html.Button("Registrar esfuerzo", id="btn-save-rpe", className="btn btn-primary"),
                                                html.Div(id="rpe-save-msg", className="text-muted"),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
            className="page-content session-shell",
        )

    # ---------- Coach ----------
    if role == "coach":
        return _coach_jornada_layout_v2(uid_int, sport)

    return html.Div(
        [
            html.H2("Sesión"),
            html.P("Esta vista todavía no está configurada para este rol."),
        ],
        className="page-content",
    )


# --- Home por tiles (según rol) ---

def home_tiles():
    """Home: panel-grid con KPIs, tendencia, equipo y recomendaciones. Sin accesos rápidos."""
    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")
    logged = bool(uid)

    try:
        page_home = importlib.import_module("pages.home")
        layout_fn = getattr(page_home, "layout", None)
        if callable(layout_fn):
            return layout_fn()
    except Exception:
        pass

    def _parse_ts(ts: str):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("T", " ")[:19])
        except Exception:
            return None

    def _get_last_wellness(user_id: int):
        val, pretty = None, "Sin registros"
        try:
            qs = db.list_questionnaires(int(user_id)) or []
            if qs:
                q0 = qs[0]
                ts = q0.get("ts") or ""
                val = q0.get("wellness_score", None)
                dt = _parse_ts(ts)
                pretty_ts = dt.strftime("%d %b %Y · %H:%M") if dt else ts.replace("T"," ")[:16]
                if val is not None:
                    pretty = f"{float(val):.0f} / 100 · {pretty_ts}" if pretty_ts else f"{float(val):.0f} / 100"
                else:
                    pretty = pretty_ts or "Sin registros"
        except Exception:
            pass
        return val, pretty

    def _count_checkins_7d(user_id: int):
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=7)
        try:
            rows = db.list_questionnaires(int(user_id)) or []
        except Exception:
            rows = []
        return sum(1 for r in rows if (dt := _parse_ts(r.get("ts") or "")) and dt >= cutoff)

    def _wellness_trend_fig(user_id: int):
        try:
            rows = db.list_questionnaires(int(user_id)) or []
        except Exception:
            rows = []
        pts = sorted(
            [(dt, float(v)) for r in rows[:14]
             if (v := r.get("wellness_score")) is not None
             and (dt := _parse_ts(r.get("ts") or "")) is not None],
            key=lambda x: x[0]
        )
        fig = go.Figure()
        if not pts:
            _uc.apply_chart_style(fig)
            fig.update_layout(height=220, margin=dict(l=12,r=12,t=12,b=12))
            fig.update_xaxes(visible=False)
            fig.update_yaxes(visible=False)
            return fig
        x = [p[0].strftime("%d %b") for p in pts]
        y = [p[1] for p in pts]
        fig.add_trace(_uc.make_area_trace(x, y, "Bienestar"))
        _uc.apply_chart_style(fig)
        fig.update_layout(height=220, margin=dict(l=12,r=12,t=12,b=24), showlegend=False)
        fig.update_yaxes(range=[0,100], title=None)
        fig.update_xaxes(title=None)
        return fig

    def _get_last_ecg(user_id: int):
        bpm_txt, hrv_txt = "Sin registros", "—"
        try:
            hrv = db.get_last_ecg_metrics(int(user_id))
            if hrv:
                bpm = hrv.get("bpm")
                sdnn = hrv.get("sdnn")
                rmssd = hrv.get("rmssd")
                if bpm is not None:
                    bpm_txt = f"{float(bpm):.0f} bpm"
                parts = []
                if sdnn is not None:
                    parts.append(f"SDNN {float(sdnn):.0f} ms")
                if rmssd is not None:
                    parts.append(f"RMSSD {float(rmssd):.0f} ms")
                hrv_txt = " · ".join(parts) if parts else "—"
        except Exception:
            pass
        return bpm_txt, hrv_txt

    def _team_card_html(role, uid):
        if role == "deportista":
            coach = None
            try:
                coach = db.get_user_coach(int(uid))
            except Exception:
                pass
            if coach:
                email = coach.get("email") or coach.get("correo")
                return html.Div([
                    html.H4("Mi equipo", className="card-title"),
                    html.Div(coach.get("name", "Coach"), style={"fontWeight":800,"fontSize":"16px"}),
                    html.Div(coach.get("sport","—"), style={"opacity":.85,"fontSize":"13px"}),
                    html.Div(f"Email: {email}" if email else "", style={"opacity":.85,"fontSize":"13px","marginTop":"4px"}),
                    html.Div(className="spacer-10"),
                    dcc.Link(html.Button("Contacto", className="btn btn-primary"), href="/contacto"),
                ])
            return html.Div([
                html.H4("Mi equipo", className="card-title"),
                html.Div("Aún no tienes coach asignado.", style={"opacity":.85}),
                html.Div(className="spacer-10"),
                dcc.Link(html.Button("Ir a Equipo", className="btn btn-ghost"), href="/usuarios"),
            ])
        if role == "coach":
            roster = []
            try:
                roster = _coach_roster(int(uid)) if uid else []
            except Exception:
                pass
            teams = []
            try:
                teams = db.list_teams(int(uid)) or []
            except Exception:
                pass
            return html.Div([
                html.H4("Mi equipo", className="card-title"),
                html.Div(f"Deportistas: {len(roster)}", style={"fontWeight":800,"fontSize":"16px"}),
                html.Div(f"Equipos: {len(teams)}", style={"opacity":.85,"fontSize":"13px","marginTop":"4px"}),
                html.Div(className="spacer-10"),
                dcc.Link(html.Button("Gestionar equipo", className="btn btn-primary"), href="/usuarios"),
            ])
        return html.Div()

    def _recs_today(sport):
        by_sport = {
            "Taekwondo": ["Combinaciones 3–4 golpes, foco en distancia.","Pierna: 3×10 patadas controladas por lado.","Condición: 6×30s alta / 60s suave."],
            "Boxeo":     ["Sombra 3×3 min (pies + guardia).","Saco: 4×2 min (jab–cross–hook).","Core: 3×45s planchas + rotaciones."],
            "Box":       ["Sombra 3×3 min (pies + guardia).","Saco: 4×2 min (jab–cross–hook).","Core: 3×45s planchas + rotaciones."],
        }
        base = ["Calentamiento 8–10 min (movilidad + activación).","Trabajo técnico 20–30 min.","Vuelta a la calma 5 min + estiramientos."]
        sport_key = (sport or "").strip()
        specific = by_sport.get(sport_key, [])
        return specific + (["—"] if specific else []) + base

    # ---- Sin sesión ----
    if not logged:
        return html.Div([
            html.Div(className="card", style={"maxWidth":"480px","margin":"60px auto"}, children=[
                html.H3("Bienvenido a CombatIQ", className="card-title"),
                html.P("Inicia sesión para ver tu panel.", className="text-muted"),
                html.Div(style={"display":"flex","gap":"10px","marginTop":"16px"}, children=[
                    dcc.Link(html.Button("Iniciar sesión", className="btn btn-primary"), href="/login"),
                    dcc.Link(html.Button("Crear cuenta", className="btn btn-ghost"), href="/registro"),
                ]),
            ]),
        ])

    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None

    user = db.get_user_by_id(uid_int) if uid_int else None
    name = (user or {}).get("name") or "Deportista"
    sport = (user or {}).get("sport") or ""
    role_label = "Deportista" if role == "deportista" else ("Coach" if role == "coach" else "Admin")

    last_wellness_val, last_wellness_pretty = _get_last_wellness(uid_int)
    checkins_7d = _count_checkins_7d(uid_int) if uid_int else 0
    bpm_txt, hrv_txt = _get_last_ecg(uid_int) if uid_int and role == "deportista" else ("—","—")

    qs_count = 0
    try:
        qs_count = len(db.list_questionnaires(uid_int) or [])
    except Exception:
        pass

    kpis = [
        html.Div(className="kpi", children=[
            html.Div("Bienestar (último)", className="kpi-label"),
            html.Div(f"{float(last_wellness_val):.0f}" if last_wellness_val is not None else "—", className="kpi-value"),
            html.Div(last_wellness_pretty, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ]
    if role == "deportista":
        kpis.append(html.Div(className="kpi", children=[
            html.Div("Cardio (último ECG)", className="kpi-label"),
            html.Div(bpm_txt, className="kpi-value"),
            html.Div(hrv_txt, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]))
    kpis.append(html.Div(className="kpi", children=[
        html.Div("Check-ins registrados", className="kpi-label"),
        html.Div(str(qs_count), className="kpi-value"),
        html.Div("historial de bienestar", className="kpi-sub"),
        html.Div(className="kpi-ecg-line"),
    ]))

    recs = _recs_today(sport)
    rec_items = []
    for r in recs:
        rec_items.append(html.Hr(style={"opacity":.3}) if r == "—" else html.Li(r))

    left_col = html.Div(className="panel-col", children=[
        html.Div(className="card", children=[
            html.H3(f"Hola, {name}", className="card-title"),
            html.Div(f"{role_label}{(' · ' + sport) if sport else ''}", className="text-muted"),
            html.Div(className="spacer-10"),
            html.Div(className="ecg-divider"),
            html.Div(className="spacer-10"),
            html.Div(className="kpis kpis--auto", children=kpis),
        ]),
        html.Div(className="card", children=[
            html.H4("Resumen semanal", className="card-title"),
            html.Small("Actividad de los últimos 7 días.", className="text-muted"),
            html.Div(className="kpis kpis--tight", style={"marginTop":"10px"}, children=[
                html.Div(className="kpi kpi--mini", children=[
                    html.Div("Check-ins (7 días)", className="kpi-label"),
                    html.Div(str(checkins_7d), className="kpi-value"),
                    html.Div("cuestionarios", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
                html.Div(className="kpi kpi--mini", children=[
                    html.Div("Último bienestar", className="kpi-label"),
                    html.Div(f"{float(last_wellness_val):.0f} / 100" if last_wellness_val is not None else "—", className="kpi-value"),
                    html.Div(last_wellness_pretty, className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
                html.Div(className="kpi kpi--mini", children=[
                    html.Div("Último ECG", className="kpi-label"),
                    html.Div(bpm_txt if role == "deportista" else "—", className="kpi-value"),
                    html.Div(hrv_txt if role == "deportista" else "—", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
            ]),
        ]),
        html.Div(className="card", children=[
            html.H4("Tendencia de bienestar", className="card-title"),
            dcc.Graph(
                figure=_wellness_trend_fig(uid_int),
                config={"displayModeBar": False, "responsive": True},
                className="panel-graph",
                style={"height":"220px","width":"100%"},
            ),
        ]),
    ])

    right_col = html.Div(className="panel-col", children=[
        html.Div(className="card", children=_team_card_html(role, uid_int)),
        html.Div(className="card", children=[
            html.H4("Recomendado hoy", className="card-title"),
            html.Ul(rec_items, className="list-compact"),
            html.Small("Sugerencias generales por deporte.", className="text-muted"),
        ]),
        html.Div(className="card", children=[
            html.H4("Siguiente paso", className="card-title"),
            html.Div(
                "Abre la sesión para confirmar contexto, revisar readiness y decidir la siguiente acción."
                if role == "coach" else
                "Abre tu sesión para revisar tu estado del día y decidir si hoy toca empujar o ajustar carga.",
                className="text-muted",
            ),
            html.Div(className="spacer-10"),
            html.Div(className="stack-8", children=[
                dcc.Link(html.Button("Abrir sesión", className="btn btn-primary"), href="/sesion"),
                dcc.Link(html.Button("Ir a análisis" if role == "coach" else "Ver señales", className="btn btn-ghost"), href="/ecg"),
            ]),
        ]),
    ])

    return html.Div([
        html.H1("Panel", className="page-title"),
        html.Div(className="panel-grid", children=[left_col, right_col]),
    ])


# =======================
# Plan de peso (deportista)
# =======================
def _build_peso_kpis(rows):
    """Construye el strip de KPIs de peso a partir de los registros (más reciente primero)."""
    if not rows:
        return html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[html.Div("Último peso", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Sin registros aún", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Objetivo", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Sin definir", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Diferencia", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Registra tu primer peso", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Tendencia", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Sin datos previos", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
        ])

    last = rows[0]
    last_w = last.get("weight")

    # Objetivo: tomar el más reciente que tenga target definido
    target_val = next((r.get("target") for r in rows if r.get("target") is not None), None)

    # Diferencia peso actual vs objetivo
    if last_w is not None and target_val is not None:
        diff = last_w - target_val
        diff_str = f"{diff:+.1f} kg"
        diff_color = "#27c98f" if diff <= 0 else ("#f0a832" if diff <= 2 else "#e45a5a")
        diff_sub = "En objetivo" if diff <= 0 else ("Cerca del objetivo" if diff <= 2 else "Por encima del objetivo")
    else:
        diff_str, diff_color, diff_sub = "—", None, "Define un objetivo para ver la diferencia"

    # Tendencia: comparar último vs anterior
    if len(rows) >= 2:
        prev_w = rows[1].get("weight")
        if last_w is not None and prev_w is not None:
            delta = last_w - prev_w
            if delta < -0.1:
                trend_str, trend_color, trend_sub = "↓ Bajando", "#27c98f", f"{delta:+.1f} kg vs registro anterior"
            elif delta > 0.1:
                trend_str, trend_color, trend_sub = "↑ Subiendo", "#e45a5a", f"{delta:+.1f} kg vs registro anterior"
            else:
                trend_str, trend_color, trend_sub = "→ Estable", None, "Sin cambio significativo"
        else:
            trend_str, trend_color, trend_sub = "—", None, "Sin datos suficientes"
    else:
        trend_str, trend_color, trend_sub = "Primer registro", None, "Agrega más para ver la tendencia"

    w_str = f"{last_w:.1f} kg" if last_w is not None else "—"
    t_str = f"{target_val:.1f} kg" if target_val is not None else "Sin definir"

    return html.Div(className="kpis", children=[
        html.Div(className="kpi", children=[
            html.Div("Último peso", className="kpi-label"),
            html.Div(w_str, className="kpi-value"),
            html.Div(f"Fecha: {last.get('date', '—')}", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Objetivo", className="kpi-label"),
            html.Div(t_str, className="kpi-value"),
            html.Div("Peso de competición", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Diferencia", className="kpi-label"),
            html.Div(diff_str, className="kpi-value",
                     style={"color": diff_color} if diff_color else {}),
            html.Div(diff_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Tendencia", className="kpi-label"),
            html.Div(trend_str, className="kpi-value",
                     style={"color": trend_color} if trend_color else {}),
            html.Div(trend_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ])


def view_peso():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div(
            "Esta sección está pensada para deportistas. Más adelante añadiremos vista para coach.",
            className="muted"
        )

    today = datetime.utcnow().date().isoformat()

    return html.Div([
        dcc.Store(id="peso-store", data={"rev": 0}),

        # ── Encabezado ──────────────────────────────────────────────────────
        html.Div(className="page-head", children=[
            html.H2("Plan de peso"),
            html.P(
                "Aquí puedes registrar tu peso de hoy, seguir la tendencia y abrir el historial solo cuando te haga falta.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # ── KPI Strip (poblado por callback) ─────────────────────────────────
        html.Div(id="peso-kpi-strip", style={"marginBottom": "16px"}),

        html.Div(className="panel-grid", style={"marginBottom": "16px"}, children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Evolución del peso", className="card-title"),
                    html.P(
                        "Te ayuda a ver si te acercas a tu objetivo y cómo se mueve tu peso en los últimos registros.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(id="peso-graph-wrap", children=[
                        html.Div(className="inner-card inner-card--chart", children=[
                            dcc.Graph(id="peso-graph", figure=go.Figure(), style={"height": "300px"}),
                        ]),
                    ]),
                    html.Div(
                        id="peso-no-data",
                        className="inner-card data-empty-state",
                        style={"display": "none"},
                        children=[
                            html.P("Aún no hay registros de peso.", style={"fontWeight": "600", "marginBottom": "6px"}),
                            html.P("Introduce tu primer registro para empezar a ver tu evolución.", className="text-muted"),
                        ],
                    ),
                ]),
            ]),
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Registrar peso", className="card-title"),
                    html.P(
                        "Guarda tu peso actual y, si te sirve, añade una nota breve para entender mejor el contexto del día.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(
                        className="filters-bar filters-bar--3",
                        style={"marginBottom": "14px"},
                        children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Fecha"),
                                dcc.DatePickerSingle(
                                    id="peso-date",
                                    date=today,
                                    display_format="YYYY-MM-DD",
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Peso actual (kg)"),
                                dcc.Input(
                                    id="peso-actual",
                                    type="number", min=0, step=0.1,
                                    placeholder="Ej: 68.5",
                                    style={"width": "100%"},
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Objetivo (kg, opcional)"),
                                dcc.Input(
                                    id="peso-objetivo",
                                    type="number", min=0, step=0.1,
                                    placeholder="Ej: 66.0",
                                    style={"width": "100%"},
                                ),
                            ]),
                        ],
                    ),
                    html.Div(className="filter-item", style={"marginBottom": "14px"}, children=[
                        html.Label("Nota (opcional)"),
                        dcc.Input(
                            id="peso-nota",
                            type="text",
                            placeholder="Ej: Semana de descarga o competición cercana",
                            style={"width": "100%"},
                        ),
                    ]),
                    html.Div(className="btn-save-row", children=[
                        html.Button("Guardar registro", id="btn-save-peso", className="btn btn-primary"),
                        html.Div(id="peso-msg", className="text-danger"),
                    ]),
                ]),
            ]),
        ]),

        html.Details(
            className="card collapsible-card",
            children=[
                html.Summary(
                    className="collapsible-card__summary",
                    children=[
                        html.Div(
                            [
                                html.H4("Historial de peso", className="card-title"),
                                html.P(
                                    "Ábrelo cuando quieras revisar registros anteriores con más detalle.",
                                    className="text-muted",
                                ),
                            ],
                            className="collapsible-card__head",
                        ),
                        html.Span("›", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(className="collapsible-card__body", children=[
                    html.Div(className="dt-pro", children=[
                        DataTable(
                            id="peso-table",
                            columns=[
                                {"name": "Fecha", "id": "date"},
                                {"name": "Peso (kg)", "id": "weight"},
                                {"name": "Objetivo (kg)", "id": "target"},
                                {"name": "Nota", "id": "note"},
                            ],
                            data=[],
                            page_size=8,
                            style_table={"overflowX": "auto"},
                        ),
                    ]),
                ]),
            ],
        ),
    ])


@app.callback(
    Output("peso-store", "data"),
    Output("peso-msg", "children"),
    Input("btn-save-peso", "n_clicks"),
    State("peso-store", "data"),
    State("peso-date", "date"),
    State("peso-actual", "value"),
    State("peso-objetivo", "value"),
    State("peso-nota", "value"),
    prevent_initial_call=True,
)
def save_peso(n, data, date, weight, target, note):
    if not n:
        raise PreventUpdate

    # Persistencia en DB (sin tocar otras funciones)
    if not session.get("user_id"):
        return (data or {"rev": 0}), "Inicia sesión para guardar tu peso."

    try:
        uid = int(session.get("user_id"))
    except Exception:
        return (data or {"rev": 0}), "Sesión inválida. Vuelve a iniciar sesión."

    if weight is None:
        return (data or {"rev": 0}), "Introduce tu peso actual en kg."

    date_str = date or datetime.utcnow().date().isoformat()
    try:
        w = float(weight)
    except Exception:
        return (data or {"rev": 0}), "Peso actual no válido."

    try:
        t = float(target) if target is not None else None
    except Exception:
        t = None

    try:
        if hasattr(db, "add_weight_entry"):
            db.add_weight_entry(uid, date_str, w, t, (note or "").strip())
        else:
            return (data or {"rev": 0}), "Tu db.py aún no soporta peso persistente (add_weight_entry)."
    except Exception:
        return (data or {"rev": 0}), "No se pudo guardar el registro de peso."

    cur = data or {"rev": 0}
    try:
        rev = int(cur.get("rev", 0)) + 1
    except Exception:
        rev = 1
    return {"rev": rev}, "Registro guardado."



_PESO_GRAPH_HIDDEN = {"display": "none"}
_PESO_GRAPH_VISIBLE = {}
_PESO_NODATA_VISIBLE = {"display": "block"}
_PESO_NODATA_HIDDEN = {"display": "none"}


@app.callback(
    Output("peso-graph", "figure"),
    Output("peso-table", "data"),
    Output("peso-kpi-strip", "children"),
    Output("peso-graph-wrap", "style"),
    Output("peso-no-data", "style"),
    Input("peso-store", "data"),
    prevent_initial_call=False,
)
def update_peso_view(_store):
    # Carga desde DB (persistente)
    if not session.get("user_id"):
        fig = go.Figure()
        fig.update_layout(
            height=300,
            margin=dict(l=40, r=18, t=52, b=40),
            title=dict(text="Inicia sesión para ver tus registros de peso", x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                      color="#f2f5fa", size=13),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            transition=dict(duration=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        return fig, [], _build_peso_kpis([]), _PESO_GRAPH_HIDDEN, _PESO_NODATA_VISIBLE

    try:
        uid = int(session.get("user_id"))
    except Exception:
        uid = None

    rows = []
    try:
        if uid and hasattr(db, "list_weight_entries"):
            rows = db.list_weight_entries(uid, limit=200) or []
    except Exception:
        rows = []

    # Tabla: devolvemos lo que venga (usualmente más reciente primero)
    table_data = [
        {"date": r.get("date"), "weight": r.get("weight"), "target": r.get("target"), "note": r.get("note")}
        for r in (rows or [])
    ]

    if not rows:
        return go.Figure(), table_data, _build_peso_kpis([]), _PESO_GRAPH_HIDDEN, _PESO_NODATA_VISIBLE

    rows_sorted = sorted(rows, key=lambda x: (x.get("date") or "", x.get("id") or 0))
    dates = [d.get("date") for d in rows_sorted]
    weights = [d.get("weight") for d in rows_sorted]
    targets = [d.get("target") for d in rows_sorted]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=weights,
            mode="lines+markers",
            name="Peso (kg)",
            line=dict(width=2.8, color=_uc.PS_PALETTE[0]),
            marker=dict(
                size=6,
                color=_uc.PS_PALETTE[0],
                line=dict(color="rgba(255,255,255,0.9)", width=1.4),
            ),
            hovertemplate="%{x}<br>%{y:.1f} kg<extra>Peso</extra>",
            connectgaps=False,
        )
    )

    if any(t is not None for t in targets):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=targets,
                mode="lines+markers",
                name="Objetivo (kg)",
                line=dict(width=1.8, dash="dash", color="#94a3b8"),
                marker=dict(size=4, color="#cbd5e1", line=dict(width=0)),
                hovertemplate="%{x}<br>%{y:.1f} kg<extra>Objetivo</extra>",
                connectgaps=False,
            )
        )

    _uc.add_last_point_highlight(fig, dates, weights, name="Último peso", color=_uc.PS_PALETTE[0], size=10)
    _uc.apply_chart_style(fig, title="Evolución del peso", x_title="Fecha", y_title="Peso (kg)", height=420)
    fig.update_layout(margin=dict(l=40, r=18, t=52, b=40), transition=dict(duration=0))
    fig.update_xaxes(nticks=min(8, len(dates)) if dates else None)
    fig.update_yaxes(tickformat=".1f")

    return fig, table_data, _build_peso_kpis(rows), _PESO_GRAPH_VISIBLE, _PESO_NODATA_HIDDEN



# =======================
# Nutrición (deportista)
# =======================
def _build_nutri_kpis(rows):
    """KPI strip de nutrición a partir de los registros (más reciente primero)."""
    from datetime import date as _date

    if not rows:
        return html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[html.Div("Adherencia media", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Sin registros aún", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Kcal media", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Sin datos", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Mejor día", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Registra para ver tu pico", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
            html.Div(className="kpi", children=[html.Div("Racha actual", className="kpi-label"), html.Div("—", className="kpi-value"), html.Div("Empieza hoy", className="kpi-sub"), html.Div(className="kpi-ecg-line")]),
        ])

    # Adherencia media últimos 7 registros
    last7 = [r.get("adherence") for r in rows[:7] if r.get("adherence") is not None]
    adh_avg = sum(last7) / len(last7) if last7 else None
    adh_str = f"{adh_avg:.0f} %" if adh_avg is not None else "—"
    adh_color = "#27c98f" if (adh_avg or 0) >= 80 else ("#f0a832" if (adh_avg or 0) >= 60 else "#e45a5a")
    adh_sub = "Últimos 7 registros" if len(last7) == 7 else f"Últimos {len(last7)} registros"

    # Kcal media (solo registros con kcal)
    kcal_vals = [r.get("kcal") for r in rows if r.get("kcal") is not None]
    kcal_avg = sum(kcal_vals) / len(kcal_vals) if kcal_vals else None
    kcal_str = f"{kcal_avg:.0f} kcal" if kcal_avg is not None else "Sin datos"
    kcal_sub = f"De {len(kcal_vals)} registro{'s' if len(kcal_vals) != 1 else ''}" if kcal_vals else "Opcional en el formulario"

    # Mejor día (mayor adherencia)
    best = max(rows, key=lambda r: r.get("adherence") or 0)
    best_adh = best.get("adherence")
    best_str = f"{best_adh:.0f} %" if best_adh is not None else "—"
    best_sub = f"Fecha: {best.get('date', '—')}"

    # Racha de días consecutivos (del más reciente hacia atrás)
    _dates = sorted(set(r.get("date") for r in rows if r.get("date")), reverse=True)
    streak = 0
    if _dates:
        streak = 1
        for i in range(1, len(_dates)):
            try:
                d1 = _date.fromisoformat(_dates[i - 1])
                d2 = _date.fromisoformat(_dates[i])
                if (d1 - d2).days == 1:
                    streak += 1
                else:
                    break
            except Exception:
                break
    streak_str = f"{streak} día{'s' if streak != 1 else ''}"
    streak_color = "#27c98f" if streak >= 7 else ("#f0a832" if streak >= 3 else None)
    streak_sub = "Sigue así" if streak >= 7 else ("Buen ritmo" if streak >= 3 else "Último registro: " + (_dates[0] if _dates else "—"))

    return html.Div(className="kpis", children=[
        html.Div(className="kpi", children=[
            html.Div("Adherencia media", className="kpi-label"),
            html.Div(adh_str, className="kpi-value", style={"color": adh_color}),
            html.Div(adh_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Kcal media", className="kpi-label"),
            html.Div(kcal_str, className="kpi-value"),
            html.Div(kcal_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Mejor día", className="kpi-label"),
            html.Div(best_str, className="kpi-value", style={"color": "#27c98f"}),
            html.Div(best_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Racha actual", className="kpi-label"),
            html.Div(streak_str, className="kpi-value",
                     style={"color": streak_color} if streak_color else {}),
            html.Div(streak_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ])


def view_nutricion():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div(
            "Esta sección está pensada para deportistas. Más adelante añadiremos vista para coach.",
            className="muted"
        )

    today = datetime.utcnow().date().isoformat()

    return html.Div([
        dcc.Store(id="nutri-store", data={"rev": 0}),

        # ── Encabezado ──────────────────────────────────────────────────────
        html.Div(className="page-head", children=[
            html.H2("Nutrición"),
            html.P(
                "Aquí puedes registrar cómo te fue con tu plan, ver la tendencia y abrir el historial solo cuando quieras revisarlo.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # ── KPI Strip (poblado por callback) ─────────────────────────────────
        html.Div(id="nutri-kpi-strip", style={"marginBottom": "16px"}),

        html.Div(className="panel-grid", style={"marginBottom": "16px"}, children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Evolución de la adherencia", className="card-title"),
                    html.P(
                        "Aquí puedes ver si estás manteniendo el plan y si las kcal acompañan esa tendencia.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(id="nutri-graph-wrap", children=[
                        html.Div(className="inner-card inner-card--chart", children=[
                            dcc.Graph(id="nutri-graph", figure=go.Figure(), style={"height": "300px"}),
                        ]),
                    ]),
                    html.Div(
                        id="nutri-no-data",
                        className="inner-card data-empty-state",
                        style={"display": "none"},
                        children=[
                            html.P("Aún no hay registros de nutrición.", style={"fontWeight": "600", "marginBottom": "6px"}),
                            html.P("Introduce tu primer registro para empezar a ver tu adherencia.", className="text-muted"),
                        ],
                    ),
                ]),
            ]),
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Registrar nutrición", className="card-title"),
                    html.P(
                        "Anota cómo te fue hoy con tu plan. Si quieres, añade kcal y un comentario para dejar más contexto.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(
                        className="filters-bar filters-bar--2",
                        style={"marginBottom": "14px"},
                        children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Fecha"),
                                dcc.DatePickerSingle(
                                    id="nutri-date",
                                    date=today,
                                    display_format="YYYY-MM-DD",
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Kcal totales (opcional)"),
                                dcc.Input(
                                    id="nutri-kcal",
                                    type="number", min=0, step=10,
                                    placeholder="Ej: 2200",
                                    style={"width": "100%"},
                                ),
                            ]),
                        ],
                    ),
                    html.Div(className="filter-item", style={"marginBottom": "18px"}, children=[
                        html.Label("Adherencia al plan (%)"),
                        html.P(
                            "¿Qué tan bien seguiste tu plan de alimentación hoy?",
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "8px"},
                        ),
                        dcc.Slider(
                            id="nutri-adherencia",
                            min=0, max=100, step=5, value=80,
                            marks={0: "0 %", 25: "25 %", 50: "50 %", 75: "75 %", 100: "100 %"},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ]),
                    html.Div(className="filter-item", style={"marginBottom": "14px"}, children=[
                        html.Label("Comentario (opcional)"),
                        dcc.Input(
                            id="nutri-nota",
                            type="text",
                            placeholder="Ej: Día de descanso o mucha hambre por la noche",
                            style={"width": "100%"},
                        ),
                    ]),
                    html.Div(className="btn-save-row", children=[
                        html.Button("Guardar registro", id="btn-save-nutri", className="btn btn-primary"),
                        html.Div(id="nutri-msg", className="text-danger"),
                    ]),
                ]),
            ]),
        ]),

        html.Details(
            className="card collapsible-card",
            children=[
                html.Summary(
                    className="collapsible-card__summary",
                    children=[
                        html.Div(
                            [
                                html.H4("Historial de nutrición", className="card-title"),
                                html.P(
                                    "Ábrelo cuando quieras revisar registros anteriores con más detalle.",
                                    className="text-muted",
                                ),
                            ],
                            className="collapsible-card__head",
                        ),
                        html.Span("›", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(className="collapsible-card__body", children=[
                    html.Div(className="dt-pro", children=[
                        DataTable(
                            id="nutri-table",
                            columns=[
                                {"name": "Fecha", "id": "date"},
                                {"name": "Adherencia (%)", "id": "adherence"},
                                {"name": "Kcal", "id": "kcal"},
                                {"name": "Comentario", "id": "note"},
                            ],
                            data=[],
                            page_size=8,
                            style_table={"overflowX": "auto"},
                        ),
                    ]),
                ]),
            ],
        ),
    ])


@app.callback(
    Output("nutri-store", "data"),
    Output("nutri-msg", "children"),
    Input("btn-save-nutri", "n_clicks"),
    State("nutri-store", "data"),
    State("nutri-date", "date"),
    State("nutri-adherencia", "value"),
    State("nutri-kcal", "value"),
    State("nutri-nota", "value"),
    prevent_initial_call=True,
)
def save_nutricion(n, data, date, adherence, kcal, note):
    if not n:
        raise PreventUpdate

    # Persistencia en DB (sin tocar otras funciones)
    if not session.get("user_id"):
        return (data or {"rev": 0}), "Inicia sesión para guardar tu nutrición."

    try:
        uid = int(session.get("user_id"))
    except Exception:
        return (data or {"rev": 0}), "Sesión inválida. Vuelve a iniciar sesión."

    if adherence is None:
        return (data or {"rev": 0}), "Indica al menos tu % de adherencia."

    date_str = date or datetime.utcnow().date().isoformat()
    try:
        adh = float(adherence)
    except Exception:
        return (data or {"rev": 0}), "Adherencia no válida."

    try:
        kc = float(kcal) if kcal is not None else None
    except Exception:
        kc = None

    try:
        if hasattr(db, "add_nutrition_entry"):
            db.add_nutrition_entry(uid, date_str, adh, kc, (note or "").strip())
        else:
            return (data or {"rev": 0}), "Tu db.py aún no soporta nutrición persistente (add_nutrition_entry)."
    except Exception:
        return (data or {"rev": 0}), "No se pudo guardar el registro de nutrición."

    cur = data or {"rev": 0}
    try:
        rev = int(cur.get("rev", 0)) + 1
    except Exception:
        rev = 1
    return {"rev": rev}, "Registro de nutrición guardado."



_NUTRI_GRAPH_HIDDEN  = {"display": "none"}
_NUTRI_GRAPH_VISIBLE = {}
_NUTRI_NODATA_VISIBLE = {"display": "block"}
_NUTRI_NODATA_HIDDEN  = {"display": "none"}


@app.callback(
    Output("nutri-graph", "figure"),
    Output("nutri-table", "data"),
    Output("nutri-kpi-strip", "children"),
    Output("nutri-graph-wrap", "style"),
    Output("nutri-no-data", "style"),
    Input("nutri-store", "data"),
    prevent_initial_call=False,
)
def update_nutri_view(_store):
    if not session.get("user_id"):
        return go.Figure(), [], _build_nutri_kpis([]), _NUTRI_GRAPH_HIDDEN, _NUTRI_NODATA_VISIBLE

    try:
        uid = int(session.get("user_id"))
    except Exception:
        uid = None

    rows = []
    try:
        if uid and hasattr(db, "list_nutrition_entries"):
            rows = db.list_nutrition_entries(uid, limit=200) or []
    except Exception:
        rows = []

    table_data = [
        {"date": r.get("date"), "adherence": r.get("adherence"), "kcal": r.get("kcal"), "note": r.get("note")}
        for r in (rows or [])
    ]

    if not rows:
        return go.Figure(), table_data, _build_nutri_kpis([]), _NUTRI_GRAPH_HIDDEN, _NUTRI_NODATA_VISIBLE

    rows_sorted = sorted(rows, key=lambda x: (x.get("date") or "", x.get("id") or 0))
    dates = [d.get("date") for d in rows_sorted]
    adherence = [d.get("adherence") for d in rows_sorted]
    kcal = [d.get("kcal") for d in rows_sorted]

    fig = go.Figure()
    _uc.add_reference_band(fig, y0=80, y1=100, fillcolor="rgba(39,201,143,0.08)")
    fig.add_hline(y=80, line=dict(color="rgba(39,201,143,0.34)", width=1.4, dash="dot"))
    fig.add_trace(
        go.Bar(
            x=dates,
            y=adherence,
            name="Adherencia (%)",
            marker=dict(color=_uc.PS_PALETTE[0], opacity=0.86, line=dict(width=0)),
            hovertemplate="%{x}<br>%{y:.0f} %<extra>Adherencia</extra>",
        )
    )

    if any(k is not None for k in kcal):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=kcal,
                name="Kcal",
                mode="lines+markers",
                yaxis="y2",
                line=dict(width=2.2, color="#4f9fd9"),
                marker=dict(size=5, color="#4f9fd9", line=dict(color="rgba(255,255,255,0.88)", width=1.1)),
                hovertemplate="%{x}<br>%{y:.0f} kcal<extra>Kcal</extra>",
                connectgaps=False,
            )
        )
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, zeroline=False))

    _uc.add_last_point_highlight(fig, dates, adherence, name="Última adherencia", color=_uc.PS_PALETTE[0], size=9)
    _uc.apply_chart_style(fig, title="Nutrición y adherencia", x_title="Fecha", y_title="Adherencia (%)", height=420)
    fig.update_layout(margin=dict(l=40, r=18, t=52, b=40), transition=dict(duration=0), bargap=0.22)
    fig.update_xaxes(nticks=min(8, len(dates)) if dates else None)
    fig.update_yaxes(range=[0, 100], ticksuffix=" %")
    if any(k is not None for k in kcal):
        fig.update_layout(
            yaxis2=dict(
                title=dict(text="Kcal", font=dict(color="#94a3b8")),
                overlaying="y",
                side="right",
                showgrid=False,
                zeroline=False,
                rangemode="tozero",
                tickfont=dict(color="#94a3b8"),
            ),
        )

    return fig, table_data, _build_nutri_kpis(rows), _NUTRI_GRAPH_VISIBLE, _NUTRI_NODATA_HIDDEN



# =======================
# Sobre CombatIQ
# =======================
def view_sobre():
    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Sobre CombatIQ"),
            html.P(
                "Hecha para coaches y deportistas que quieren entrenar con datos, no solo con sensaciones.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # --- Identidad de producto ---
        html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("¿Para quién es CombatIQ?", className="card-title"),
            html.P(
                "Para atletas de deportes de combate — taekwondo, boxeo y artes marciales — "
                "y para los coaches que los preparan. No es una app genérica de fitness: "
                "cada lectura, recomendación y cuestionario está pensado para el contexto real "
                "del deporte de contacto.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            html.Div(className="filters-bar filters-bar--3", children=[
                html.Div(className="filter-item", children=[
                    html.Div("🥊", style={"fontSize": "24px", "marginBottom": "6px"}),
                    html.Div("Deportes de combate", style={"fontWeight": "800", "fontSize": "14px"}),
                    html.P("Taekwondo y boxeo como núcleo del MVP.", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("📡", style={"fontSize": "24px", "marginBottom": "6px"}),
                    html.Div("Señales reales", style={"fontWeight": "800", "fontSize": "14px"}),
                    html.P("IMU + ECG/HR como base del análisis de sesión.", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("🎯", style={"fontSize": "24px", "marginBottom": "6px"}),
                    html.Div("Decisiones mejores", style={"fontWeight": "800", "fontSize": "14px"}),
                    html.P("Contexto, tendencia y lectura accionable en cada vista.", className="text-muted"),
                ]),
            ]),
        ]),

        # --- Qué resuelve ---
        html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("¿Qué resuelve?", className="card-title"),
            html.P(
                "La mayoría de los equipos de combate entrenan sin datos o con hojas de cálculo "
                "desconectadas. CombatIQ pone en un solo lugar el estado del día, la carga acumulada, "
                "las señales fisiológicas y el historial — para que coach y deportista hablen "
                "el mismo idioma.",
                className="text-muted",
            ),
            html.Ul([
                html.Li("Check-in diario contextualizado por deporte y cercanía a competencia."),
                html.Li("Análisis de sesión con señales reales, no solo RPE subjetivo."),
                html.Li("Histórico y comparativas para detectar sobrecargas antes de que afecten."),
                html.Li("Panel diferenciado: el coach ve el equipo, el deportista ve su evolución."),
            ], className="list-compact", style={"marginTop": "10px"}),
        ]),

        # --- Info técnica y versión ---
        html.Div(className="card", children=[
            html.H4("Versión y tecnología", className="card-title"),
            html.Div(className="filters-bar filters-bar--2", style={"marginTop": "10px"}, children=[
                html.Div(className="filter-item", children=[
                    html.Div("Versión", className="kpi-label"),
                    html.Div("MVP · v1.0", style={"fontWeight": "800", "fontSize": "16px", "marginTop": "4px"}),
                    html.P("PowerSync / CombatIQ", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("Stack", className="kpi-label"),
                    html.Div("Dash + Flask + SQLite", style={"fontWeight": "800", "fontSize": "16px", "marginTop": "4px"}),
                    html.P("Python · Plotly · Pandas", className="text-muted"),
                ]),
            ]),
        ]),
    ])


# =======================
# Invita a tus amigos / deportistas
# =======================
def view_invita():
    user_id = session.get("user_id")
    role = _to_str(session.get("role")) or ""

    if isinstance(user_id, int):
        ref_code = f"ATH-{user_id:04d}"
    else:
        try:
            ref_code = f"ATH-{int(user_id):04d}"
        except Exception:
            ref_code = "COMBATIQ"

    invite_path = f"/registro?ref={ref_code}"

    if role == "coach":
        headline = "Añade deportistas a tu plataforma"
        body = (
            "Comparte este enlace con los atletas que quieras incorporar. "
            "Al registrarse con tu código quedarán disponibles para añadirlos a tu plantilla."
        )
    else:
        headline = "Invita a un compañero"
        body = (
            "Si entrenas con alguien más, comparte este enlace para que también pueda "
            "registrar sus sesiones y seguir su evolución en CombatIQ."
        )

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Invitar"),
            html.P(headline, className="text-muted"),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # --- Card principal ---
        html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("Tu enlace de registro", className="card-title"),
            html.P(body, className="text-muted", style={"marginBottom": "18px"}),

            html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "16px"}, children=[
                html.Div(className="filter-item", children=[
                    html.Label("Enlace de registro"),
                    html.Div(
                        invite_path,
                        className="inner-cell",
                        style={"padding": "10px 14px", "fontFamily": "monospace", "fontSize": "13px"},
                    ),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("Código de referencia"),
                    html.Div(
                        ref_code,
                        className="inner-cell",
                        style={"padding": "10px 14px", "fontFamily": "monospace",
                               "fontSize": "15px", "fontWeight": "800", "letterSpacing": "0.05em"},
                    ),
                ]),
            ]),

            html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}, children=[
                dcc.Clipboard(
                    target_id=None,
                    content=invite_path,
                    title="Copiar enlace",
                    className="btn btn-primary",
                    style={"display": "inline-flex", "alignItems": "center", "gap": "6px"},
                ),
                dcc.Clipboard(
                    target_id=None,
                    content=ref_code,
                    title="Copiar código",
                    className="btn btn-ghost",
                    style={"display": "inline-flex", "alignItems": "center", "gap": "6px"},
                ),
            ]),
        ]),

        # --- Card: cómo funciona ---
        html.Div(className="card", children=[
            html.H4("¿Cómo funciona?", className="card-title"),
            html.Ul([
                html.Li("Comparte el enlace o el código con quien quieras invitar."),
                html.Li("La persona crea su cuenta desde la pantalla de registro."),
                html.Li([
                    "Si eres coach, después " ,
                    html.Strong("añádela a tu plantilla"),
                    " desde la sección Equipo.",
                ]),
                html.Li("A partir de ahí ya aparece en cuestionarios, análisis e histórico."),
            ], className="list-compact"),
        ]),
    ])




@app.callback(
    Output("dash-profile-msg", "children"),
    Input("dash-save-profile", "n_clicks"),
    State("dash-competitive-level", "value"),
    State("dash-weight-category", "value"),
    State("dash-dominant-side", "value"),
    State("dash-current-status", "value"),
    State("dash-watch-zone", "value"),
    State("dash-competition-proximity", "value"),
    State("dash-profile-note", "value"),
    prevent_initial_call=True,
)
def save_dashboard_profile(n_clicks, competitive_level, weight_category, dominant_side, current_status, watch_zone, competition_proximity, profile_note):
    if not n_clicks:
        raise PreventUpdate

    if not session.get("user_id"):
        return "Inicia sesión para guardar tu perfil."

    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return "Solo el deportista puede editar este bloque por ahora."

    try:
        uid = int(session.get("user_id"))
    except Exception:
        return "Sesión inválida."

    payload = {
        "competitive_level": competitive_level,
        "weight_category": weight_category,
        "dominant_side": dominant_side,
        "current_status": current_status,
        "watch_zone": watch_zone,
        "competition_proximity": competition_proximity,
        "profile_note": profile_note,
    }

    try:
        if hasattr(db, "save_athlete_profile"):
            db.save_athlete_profile(uid, payload)
        else:
            return "Tu base de datos todavía no soporta perfil deportivo extendido."
    except Exception:
        return "No se pudo guardar el perfil deportivo."

    return "Perfil deportivo guardado."

# === Sidebar toggle (callback normal en Python) ===
@app.callback(
    Output("ui-sidebar-collapsed", "data"),
    Output("sidebar", "style"),
    Output("page-content", "style"),
    Output("btn-toggle-sidebar", "style"),
    Output("btn-toggle-sidebar", "children"),
    Input("btn-toggle-sidebar", "n_clicks"),
    State("ui-sidebar-collapsed", "data"),
)
def toggle_sidebar(n, collapsed):
    collapsed = bool(collapsed)
    clicked = (n or 0) > 0
    NEW = (not collapsed) if clicked else collapsed

    # Solo movemos posiciones (el look lo controla CSS)
    sb = {"left": "0px"}
    pg = {"marginLeft": f"{SIDEBAR_W}px"}
    btn = {"left": f"{SIDEBAR_W + 12}px"}
    txt = "«"

    if NEW:
        sb["left"] = f"-{SIDEBAR_W}px"
        pg["marginLeft"] = f"{PAGE_COLLAPSED_MARGIN}px"
        btn["left"] = "16px"
        txt = "»"

    return NEW, sb, pg, btn, txt


# ====== CS-027 — Tema claro / oscuro ======
# Alterna el store entre "dark" y "light"
@app.callback(
    Output("theme-store", "data"),
    Input("btn-theme-toggle", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)
def toggle_theme(_, current):
    return "light" if (current or "dark") == "dark" else "dark"


# Clientside callback: aplica data-theme al <html> sin round-trip
app.clientside_callback(
    """
    function(theme) {
        var t = theme || 'dark';
        document.documentElement.setAttribute('data-theme', t);
        return [t, t === 'light' ? '☾' : '☀'];
    }
    """,
    Output("theme-applied", "children"),
    Output("theme-toggle-icon", "children"),
    Input("theme-store", "data"),
)


# ====== CS-029 — Toggle de tema en pantallas auth ======
app.clientside_callback(
    """
    function(n, current) {
        if (!n) return [window.dash_clientside.no_update, window.dash_clientside.no_update];
        var t = (current === 'dark') ? 'light' : 'dark';
        return [t, t === 'light' ? '☾' : '☀'];
    }
    """,
    Output("theme-store", "data", allow_duplicate=True),
    Output("btn-auth-theme", "children"),
    Input("btn-auth-theme", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """
    function(n, current) {
        if (!n) return [window.dash_clientside.no_update, window.dash_clientside.no_update];
        var t = (current === 'dark') ? 'light' : 'dark';
        return [t, t === 'light' ? '☾' : '☀'];
    }
    """,
    Output("theme-store", "data", allow_duplicate=True),
    Output("btn-auth-theme-reg", "children"),
    Input("btn-auth-theme-reg", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)


# ====== CS-027b — Actualizar gráficas Plotly según tema ======
app.clientside_callback(
    """
    function(theme) {
        var light = (theme === 'light');
        var fontColor  = light ? '#1a2433' : '#E7ECF3';
        var gridColor  = light ? 'rgba(80,110,150,0.12)' : 'rgba(255,255,255,0.08)';
        var lineColor  = light ? 'rgba(80,110,150,0.18)' : 'rgba(255,255,255,0.14)';
        var tickColor  = light ? 'rgba(80,110,150,0.30)' : 'rgba(255,255,255,0.20)';
        var tickFont   = light ? '#3a5068' : '#8fa3bf';
        var hoverBg    = light ? '#e0f4ff' : '#0d1520';
        var hoverBdr   = light ? '#0284c7' : '#0ea5e9';
        var spikeColor = light ? 'rgba(2,132,199,0.40)' : 'rgba(14,165,233,0.35)';
        var traceColor = light ? '#0284c7' : '#0ea5e9';

        var layoutUpd = {
            'font.color': fontColor,
            'xaxis.gridcolor': gridColor,  'xaxis.linecolor': lineColor,
            'xaxis.tickcolor': tickColor,  'xaxis.tickfont.color': tickFont,
            'xaxis.spikecolor': spikeColor,
            'yaxis.gridcolor': gridColor,  'yaxis.linecolor': lineColor,
            'yaxis.tickcolor': tickColor,  'yaxis.tickfont.color': tickFont,
            'yaxis2.gridcolor': gridColor, 'yaxis2.linecolor': lineColor,
            'yaxis2.tickcolor': tickColor, 'yaxis2.tickfont.color': tickFont,
            'legend.font.color': fontColor,
            'hoverlabel.bgcolor': hoverBg, 'hoverlabel.bordercolor': hoverBdr,
            'hoverlabel.font.color': fontColor
        };

        var _applyToGraph = function(el) {
            try {
                Plotly.relayout(el, layoutUpd);
                if (el.data && el.data.length > 0) {
                    var t0type = (el.data[0].type || 'scatter').toLowerCase();
                    if (t0type === 'scatter' || t0type === 'scattergl') {
                        var rs = {'line.color': traceColor, 'marker.color': traceColor};
                        if (el.data[0].fill && el.data[0].fill !== 'none') {
                            var rgb = light ? '2,132,199' : '14,165,233';
                            rs['fillcolor'] = 'rgba(' + rgb + ',0.13)';
                        }
                        Plotly.restyle(el, rs, [0]);
                    }
                }
            } catch(e) {}
        };

        var _applyAll = function() {
            document.querySelectorAll('.js-plotly-plot').forEach(_applyToGraph);
        };

        _applyAll();
        setTimeout(_applyAll, 400);
        setTimeout(_applyAll, 1200);
        return theme || 'dark';
    }
    """,
    Output("theme-charts-applied", "children"),
    Input("theme-store", "data"),
)


# ====== CS-021 — Tarjeta compartible ======
@app.callback(
    Output("download-share-card", "data"),
    Input("btn-share-week", "n_clicks"),
    prevent_initial_call=True,
)
def download_share_card(_):
    uid = session.get("user_id")
    if not uid:
        raise PreventUpdate
    try:
        from pages.dashboard import generate_share_card
        uid_int = int(uid)
        user = db.get_user_by_id(uid_int)
        name = str(user.get("name", "Atleta")) if user else "Atleta"
        sport = str(user.get("sport", "")) if user else ""
        weekly = db.get_weekly_load_summary(uid_int) if hasattr(db, "get_weekly_load_summary") else {}
        history = db.get_load_history(uid_int) if hasattr(db, "get_load_history") else []
        profile = db.get_athlete_profile(uid_int) if hasattr(db, "get_athlete_profile") else {}
        comp_prox = (profile.get("competition_proximity") or "") if profile else ""
        png = generate_share_card(name, sport, weekly, history, comp_prox)
        if png is None:
            raise PreventUpdate
        return dcc.send_bytes(lambda b: b.write(png), "combatiq_semana.png")
    except PreventUpdate:
        raise
    except Exception:
        raise PreventUpdate


# ====== CS-022 — Modo demo ======
@app.callback(
    Output("demo-redirect", "children"),
    Input("btn-demo-login", "n_clicks"),
    prevent_initial_call=True,
)
def enter_demo_mode(_):
    demo_uid = db.ensure_demo_user()
    session["user_id"] = demo_uid
    session["role"] = "deportista"
    session["name"] = "Demo Atleta"
    session["sport"] = "Taekwondo"
    session["is_demo"] = True
    session["quote_idx"] = random.randint(0, 99)
    return dcc.Location(pathname="/dashboard", id="redirect-demo")


# ====== CS-023 — Modo demo Coach ======
@app.callback(
    Output("demo-coach-redirect", "children"),
    Input("btn-demo-coach-login", "n_clicks"),
    prevent_initial_call=True,
)
def enter_demo_coach_mode(_):
    coach_uid = db.ensure_demo_coach()
    session["user_id"] = coach_uid
    session["role"] = "coach"
    session["name"] = "Demo Coach"
    session["sport"] = "Taekwondo"
    session["is_demo"] = True
    session["quote_idx"] = random.randint(0, 99)
    return dcc.Location(pathname="/dashboard", id="redirect-demo-coach")


# ====== CS-023b — Modo demo Coach Boxeo ======
@app.callback(
    Output("demo-coach-boxeo-redirect", "children"),
    Input("btn-demo-coach-boxeo-login", "n_clicks"),
    prevent_initial_call=True,
)
def enter_demo_coach_boxeo_mode(_):
    coach_uid = db.ensure_demo_coach_boxeo()
    session["user_id"] = coach_uid
    session["role"] = "coach"
    session["name"] = "Demo Coach Boxeo"
    session["sport"] = "Boxeo"
    session["is_demo"] = True
    session["quote_idx"] = random.randint(0, 99)
    return dcc.Location(pathname="/dashboard", id="redirect-demo-coach-boxeo")


# ====== CS-019 — RPE rápido post-sesión ======
@app.callback(
    Output("rpe-save-msg", "children"),
    Input("btn-save-rpe", "n_clicks"),
    State("rpe-slider", "value"),
    State("rpe-duration", "value"),
    prevent_initial_call=True,
)
def save_quick_rpe(n_clicks, rpe, duration):
    uid = session.get("user_id")
    if not uid or not rpe:
        raise PreventUpdate
    try:
        uid_int = int(uid)
        dur = float(duration) if duration else 0.0
        open_sess = db.ensure_open_session(uid_int)
        sid = open_sess if isinstance(open_sess, int) else (open_sess.get("id") if open_sess else None)
        db.save_rpe_entry(uid_int, float(rpe), dur, sid)
        from analysis_engine import invalidate_cache as _inv
        _inv(uid_int)
        label = "Ligero" if rpe <= 3 else ("Moderado" if rpe <= 6 else ("Exigente" if rpe <= 8 else "Máximo"))
        dur_str = f" · {int(dur)} min" if dur > 0 else ""
        return f"Registrado — RPE {rpe} ({label}){dur_str}. Ya suma a tu carga semanal."
    except Exception as e:
        return f"Error al guardar: {e}"


# ====== ROUTER ======
@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def router(path):
    def errbox(title, err):
        return html.Div([
            h2(title),
            html.Pre(err, style={
                "whiteSpace": "pre-wrap", "background": "#2b1f23",
                "border": "1px solid #4a2b31", "padding": "12px",
                "borderRadius": "10px", "color": "#FFB4B4", "overflow": "auto"
            })
        ])

    logged = bool(session.get("user_id"))
    if not logged and path in (None, "", "/", "/inicio", "/home"):
        return dcc.Location(pathname="/login", id="redirect-root-login")

    if path in ("/", "/inicio", "/home"):
        return home_tiles()
    if path in ("/usuarios", "/legacy"):
        return view_usuarios()
    if path == "/deportista":
        return view_deportista_v2()
    if path == "/anuncios":
        return view_anuncios()
    if path == "/contacto":
        return view_contacto_coach()
    if path == "/sensores":
        return sensors_view.layout()
    if path == "/ecg":
        return signals_view.layout()
    if path == "/cuestionario":
        return wellbeing_page.layout_questionnaire()
    if path == "/historico":
        return wellbeing_page.layout_history()
    if path == "/sesion":
        return view_sesion()
    if path == "/comparar":
        return compare_view.layout()
    if path == "/analisis":
        return analysis_view.layout()
    if path == "/peso":
        return view_peso()
    if path == "/nutricion":
        return view_nutricion()
    if path == "/sobre":
        return view_sobre()
    if path == "/invita":
        return view_invita()

    mod, err = None, None
    if path == "/login":
        mod, err = page_login, err_login
    if path == "/registro":
        mod, err = page_register, err_register
    if path == "/dashboard":
        mod, err = page_dashboard, err_dashboard
    if path == "/logout":
        mod, err = page_logout, err_logout

    if err:
        return errbox(f"Error importando {path}", err)
    if not mod:
        return html.Div("Vista no disponible.")
    return mod.layout() if callable(getattr(mod, "layout", None)) else mod.layout


# === Auto-open helper ===
AUTO_OPEN = os.environ.get("POWERSYNC_AUTO_OPEN", "1") == "1"
_OPEN_SENTINEL = os.path.join(os.path.expanduser("~"), ".powersync_opened")


def _open_browser_once(url):
    try:
        if not os.path.exists(_OPEN_SENTINEL):
            webbrowser.open_new(url)
            open(_OPEN_SENTINEL, "w").close()
    except Exception:
        pass


# =============================================================================
# API REST — Hardware de sensores
# =============================================================================
# Estos endpoints permiten que dispositivos físicos (BLE hub, scripts externos,
# o cualquier hardware con capacidad HTTP) se comuniquen con CombatIQ.
#
# Flujo recomendado:
#   1. Coach empareja el dispositivo desde la UI (/sensores → "Parear dispositivo")
#      → llama a db.register_device() que crea la fila en sensor_devices
#   2. Hardware envía POST /api/sensor-ping cada N segundos para indicar que está vivo
#      → actualiza last_seen y status='connected'
#   3. Hardware envía POST /api/sensor-data con las lecturas
#      → guarda en las tablas correspondientes (ecg_metrics, imu_metrics, etc.)
#   4. La UI de Sensores consulta GET /api/sensor-status/<user_id> para mostrar
#      el estado de cada dispositivo en tiempo real
# =============================================================================

from flask import jsonify, request as flask_request


@server.route("/api/sensor-ping", methods=["POST"])
def api_sensor_ping():
    """
    Hardware → CombatIQ: heartbeat del sensor.

    Body JSON esperado:
    {
        "device_id": "AA:BB:CC:DD:EE:FF",
        "user_id": 5,
        "sensor_code": "ECG"          (opcional, solo para info)
    }
    """
    try:
        data = flask_request.get_json(force=True, silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        user_id   = int(data.get("user_id", 0))
        if not device_id or not user_id:
            return jsonify({"ok": False, "error": "device_id y user_id son obligatorios"}), 400
        found = db.update_device_last_seen(device_id, user_id)
        if not found:
            # Registro automático como fallback (si el dispositivo no estaba emparejado)
            sensor_code = str(data.get("sensor_code", "UNKNOWN"))
            db.register_device(user_id, sensor_code, device_id)
        return jsonify({"ok": True, "found": found}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@server.route("/api/sensor-data", methods=["POST"])
def api_sensor_data():
    """
    Hardware → CombatIQ: lectura de datos del sensor.

    Body JSON esperado (los campos varían según sensor_code):
    {
        "device_id": "AA:BB:CC:DD:EE:FF",
        "user_id": 5,
        "sensor_code": "ECG",
        "session_id": 12,              (opcional)
        "filename": "ecg_20260414.csv", (opcional, nombre del archivo de origen)
        "fs": 250,                     (ECG: frecuencia de muestreo)
        "bpm": 72.5,                   (ECG: frecuencia cardiaca)
        "sdnn": 45.2,                  (ECG: HRV SDNN)
        "rmssd": 38.1,                 (ECG: HRV RMSSD)
        "peaks_count": 186,            (ECG: latidos detectados)
        "n_hits": 120,                 (IMU: golpes)
        "hits_per_min": 40.0,          (IMU: ritmo)
        "mean_int_g": 2.3,             (IMU: intensidad media)
        "max_int_g": 8.5,              (IMU: pico de impacto)
        "rms": 0.45,                   (EMG: RMS)
        "peak": 1.2,                   (EMG: pico)
        "fatigue": 0.3                 (EMG: índice de fatiga simple)
    }
    """
    try:
        data = flask_request.get_json(force=True, silent=True) or {}
        device_id   = str(data.get("device_id", "")).strip()
        user_id     = int(data.get("user_id", 0))
        sensor_code = str(data.get("sensor_code", "")).strip().upper()
        session_id  = data.get("session_id")

        if not user_id or not sensor_code:
            return jsonify({"ok": False, "error": "user_id y sensor_code son obligatorios"}), 400

        # Actualiza last_seen si tenemos device_id
        if device_id:
            db.update_device_last_seen(device_id, user_id)

        filename = str(data.get("filename", f"{sensor_code.lower()}_live_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"))

        if sensor_code == "ECG":
            fs  = int(data.get("fs", 250))
            bpm = data.get("bpm")
            sdnn  = data.get("sdnn")
            rmssd = data.get("rmssd")
            peaks = data.get("peaks_count")
            if any(v is not None for v in [bpm, sdnn, rmssd, peaks]):
                ecg_id = db.add_ecg_file(user_id, filename, fs, session_id=session_id)
                if bpm is not None:
                    db.save_ecg_metrics_latest(
                        ecg_id,
                        float(bpm),
                        float(sdnn or 0),
                        float(rmssd or 0),
                        int(peaks or 0),
                    )

        elif sensor_code in ("IMU_GLOVE", "IMU_HEAD"):
            n_hits      = data.get("n_hits")
            hits_per_min = data.get("hits_per_min")
            mean_int_g  = data.get("mean_int_g")
            max_int_g   = data.get("max_int_g")
            if any(v is not None for v in [n_hits, hits_per_min, mean_int_g, max_int_g]):
                db.save_imu_metrics(
                    user_id, filename,
                    int(n_hits or 0),
                    float(hits_per_min or 0),
                    float(mean_int_g or 0),
                    float(max_int_g or 0),
                    session_id=session_id,
                )

        elif sensor_code in ("EMG_ARM", "EMG_LEG"):
            rms     = data.get("rms")
            peak    = data.get("peak")
            fatigue = data.get("fatigue")
            if any(v is not None for v in [rms, peak, fatigue]):
                db.save_emg_metrics(
                    user_id, filename,
                    float(rms or 0),
                    float(peak or 0),
                    float(fatigue or 0),
                    session_id=session_id,
                )

        from analysis_engine import invalidate_cache as _inv
        _inv(user_id)
        return jsonify({"ok": True, "sensor_code": sensor_code}), 200

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@server.route("/api/sensor-status/<int:user_id>", methods=["GET"])
def api_sensor_status(user_id):
    """
    UI → CombatIQ: estado actual de los dispositivos de un usuario.
    Usado por dcc.Interval en la vista de Sensores para polling ligero.

    Respuesta:
    {
        "devices": [
            {
                "sensor_code": "ECG",
                "device_id": "AA:BB:CC:DD:EE:FF",
                "device_label": "Banda ECG #1",
                "computed_status": "connected",   // connected|idle|offline|paired
                "last_seen": "2026-04-14T10:30:00",
                "firmware_version": "1.2.3"
            },
            ...
        ]
    }
    """
    try:
        devices = db.get_user_devices(int(user_id))
        # Sólo devolvemos los campos necesarios para la UI
        out = [
            {
                "sensor_code":      d["sensor_code"],
                "device_id":        d["device_id"],
                "device_label":     d.get("device_label") or "",
                "computed_status":  d.get("computed_status", "paired"),
                "last_seen":        d.get("last_seen") or "",
                "firmware_version": d.get("firmware_version") or "",
            }
            for d in devices
        ]
        return jsonify({"devices": out}), 200
    except Exception as exc:
        return jsonify({"devices": [], "error": str(exc)}), 500


if __name__ == "__main__":
    import socket as _socket
    PORT = int(os.environ.get("PORT", 8050))
    # HOST: "0.0.0.0" permite que dispositivos de la misma red WiFi se conecten.
    # Cambia a "127.0.0.1" si solo quieres acceso local (sin hardware externo).
    HOST = os.environ.get("HOST", "0.0.0.0")
    URL  = f"http://127.0.0.1:{PORT}/"

    # Imprime la IP local para que el hardware sepa a dónde apuntar
    try:
        _local_ip = _socket.gethostbyname(_socket.gethostname())
        print(f"\n  Abre en este equipo:  http://127.0.0.1:{PORT}/")
        print(f"  Red local (otros):    http://{_local_ip}:{PORT}/")
        print(f"  API sensores:         http://{_local_ip}:{PORT}/api/sensor-ping")
        print(f"                        http://{_local_ip}:{PORT}/api/sensor-data\n")
    except Exception:
        print(f"\n  Abre en este equipo:  http://127.0.0.1:{PORT}/\n")

    # use_reloader=False evita el WinError 10038 en Python 3.13 + Windows
    if AUTO_OPEN:
        Timer(1.0, lambda: _open_browser_once(URL)).start()
    app.run(debug=True, host=HOST, port=PORT, use_reloader=False)
