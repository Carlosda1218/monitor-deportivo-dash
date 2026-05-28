# ── GEVENT MONKEY-PATCH — DEBE ser la primera importación del archivo ─────────
# Convierte stdlib (socket, ssl, threading, time.sleep, etc) en cooperativos.
# Sin esto, los HTTP calls a Anthropic/Supabase bloquean el thread del worker
# y la app se congela para todos los usuarios mientras se ejecuta cualquier IA.
# En local Windows con Python desktop puede fallar el import — silenciamos para
# no romper el dev; en Railway (Linux + gunicorn gevent worker) siempre carga OK.
try:
    from gevent import monkey as _gevent_monkey
    _gevent_monkey.patch_all()
except Exception:
    pass

import os, io, base64, json, csv, webbrowser, importlib, traceback, urllib.parse, random, logging
from threading import Timer
from datetime import datetime

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

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

from flask import Flask, session, request, redirect, jsonify
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
    suppress_callback_exceptions=True,
    meta_tags=[
        {"name": "viewport",      "content": "width=device-width, initial-scale=1, maximum-scale=1"},
        {"name": "theme-color",   "content": "#161f29"},
        {"name": "mobile-web-app-capable",        "content": "yes"},
        {"name": "apple-mobile-web-app-capable",  "content": "yes"},
        {"name": "apple-mobile-web-app-status-bar-style", "content": "black-translucent"},
        {"name": "apple-mobile-web-app-title",    "content": "CombatIQ"},
    ],
)

# ====== PWA service worker registration ======
app.index_string = """<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <link rel="manifest" href="/assets/manifest.json">
    <link rel="apple-touch-icon" href="/assets/icon-192.png">
  </head>
  <body>
    {%app_entry%}
    <footer>
      {%config%}
      {%scripts%}
      {%renderer%}
    </footer>
    <script>
      (function() {
        try {
          var cleanupKey = 'combatiq-sw-cache-cleanup-v2';
          if (window.localStorage && localStorage.getItem(cleanupKey) === '1') {
            return;
          }
          if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistrations().then(function(regs) {
              regs.forEach(function(r) { r.unregister(); });
            });
          }
          if (window.caches) {
            caches.keys().then(function(keys) {
              keys.forEach(function(k) { caches.delete(k); });
            });
          }
          if (window.localStorage) {
            localStorage.setItem(cleanupKey, '1');
          }
        } catch(e) {}
      })();
    </script>
  </body>
</html>"""

# ====== No-cache for HTML pages ======
@server.after_request
def _no_cache_html(response):
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@server.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok", "app": "CombatIQ"}, 200


@server.route("/debug/analyzer-version", methods=["GET"])
def debug_analyzer_version():
    """Tiny local diagnostic to confirm the running server loaded current code."""
    try:
        import pose_analyzer
        from views import signals_view
        return jsonify({
            "pid": os.getpid(),
            "pose_analyzer_file": getattr(pose_analyzer, "__file__", ""),
            "pose_analyzer_version": getattr(pose_analyzer, "_ANALYZER_VERSION", "NO_VERSION"),
            "signals_view_version": getattr(signals_view, "_POSE_ANALYSIS_VERSION", "NO_VERSION"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "pid": os.getpid()}), 500


@server.route("/logout", methods=["GET"])
def flask_logout():
    """Logout robusto: evita quedar atrapado en navegación interna de Dash."""
    session.clear()
    return redirect("/login?logged_out=1")

def _start_demo_session(kind: str):
    """Entrada demo robusta por HTTP: no depende de callbacks de Dash."""
    if kind == "coach-tkd":
        uid = db.ensure_demo_coach()
        session["role"] = "coach"
        session["name"] = "Demo Coach"
        session["sport"] = "Taekwondo"
    elif kind == "coach-boxeo":
        uid = db.ensure_demo_coach_boxeo()
        session["role"] = "coach"
        session["name"] = "Demo Coach Boxeo"
        session["sport"] = "Box"
    else:
        uid = db.ensure_demo_user()
        session["role"] = "deportista"
        session["name"] = "Demo Atleta"
        session["sport"] = "Taekwondo"
    session["user_id"] = uid
    session["is_demo"] = True
    session["quote_idx"] = random.randint(0, 99)
    return redirect("/dashboard")


@server.route("/demo/atleta", methods=["GET"])
def flask_demo_athlete():
    return _start_demo_session("athlete")


@server.route("/demo/coach-tkd", methods=["GET"])
def flask_demo_coach_tkd():
    return _start_demo_session("coach-tkd")


@server.route("/demo/coach-boxeo", methods=["GET"])
def flask_demo_coach_boxeo():
    return _start_demo_session("coach-boxeo")


# ====== DB init ======
db.init_db()

# ====== Logging ======
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("combatiq").setLevel(logging.INFO)

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


def _safe_unread(uid) -> int:
    try:
        return db.get_unread_count(int(uid)) if uid else 0
    except Exception:
        return 0


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
    if href == "/logout":
        return html.A(
            [
                html.Img(src=f"/assets/icons/{icon}", className="nav-ico"),
                html.Span(label, className="nav-label"),
            ],
            href=href,
            className=cls,
        )
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
                    _nav_link("Sobre CombatIQ", "/sobre", "profile.svg", pathname),
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
                        _nav_link("Análisis de rendimiento", "/analisis", "signals.svg", pathname),
                        _nav_link("Señales ECG / IMU", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar sesiones", "/comparar", "compare.svg", pathname),
                        _nav_link("Historial de combates", "/sesiones", "history.svg", pathname),
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
                        _nav_link("Competencias", "/competencia", "session.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Equipo",
                    [
                        _nav_link("Mi coach y equipo", "/usuarios", "team.svg", pathname),
                        _nav_link(
                            "Chat con el coach" + (
                                f" ({_unread})" if (_unread := _safe_unread(session.get("user_id"))) > 0 else ""
                            ),
                            "/chat", "team.svg", pathname,
                        ),
                        _nav_link("Comunicados del coach", "/mis-comunicados", "signals.svg", pathname),
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
                        _nav_link("Análisis de rendimiento", "/analisis", "signals.svg", pathname),
                        _nav_link("Señales ECG / IMU", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar rendimiento", "/comparar", "compare.svg", pathname),
                        _nav_link("Historial de combates", "/sesiones", "history.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Bienestar",
                    [
                        _nav_link("Mi check-in", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Historial", "/historico", "history.svg", pathname),
                        _nav_link("Competencias", "/competencia", "session.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Comunicación",
                    [
                        _nav_link(
                            "Chat con atletas" + (
                                f" ({_unread})" if (_unread := _safe_unread(session.get("user_id"))) > 0 else ""
                            ),
                            "/chat", "team.svg", pathname,
                        ),
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
        elif role == "admin":
            sections = [
                _nav_section(
                    "Admin",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Perfil / Ajustes", "/dashboard", "profile.svg", pathname),
                        _nav_link("Usuarios", "/usuarios", "team.svg", pathname),
                        _nav_link("Métricas de uso", "/metricas", "session.svg", pathname),
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
        else:
            sections = [
                _nav_section(
                    "Información",
                    [
                        _nav_link("Sobre CombatIQ", "/sobre", "profile.svg", pathname),
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
                        html.Img(src="/assets/logo_combatiq.svg", className="sidebar-brand__logo"),
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
                    "✦ Asistente IA",
                    id="btn-chat-toggle",
                    n_clicks=0,
                    style={
                        "width": "100%", "marginBottom": "8px",
                        "background": "rgba(47,183,196,0.12)",
                        "border": "1px solid rgba(47,183,196,0.35)",
                        "borderRadius": "8px", "color": "var(--neon, #2fb7c4)",
                        "fontSize": "12px", "fontWeight": "700",
                        "padding": "8px 12px", "cursor": "pointer",
                        "textAlign": "left",
                    },
                ),
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

_PUBLIC_AUTH_PATHS = {"/login", "/registro", "/recuperar-password", "/forgot-password"}


def _auth_public_layout(path: str):
    modname = {
        "/registro": "pages.auth_register",
        "/recuperar-password": "pages.auth_forgot",
        "/forgot-password": "pages.auth_forgot",
    }.get(path or "", "pages.auth_login")
    try:
        mod = importlib.import_module(modname)
        layout_fn = getattr(mod, "layout", None)
        return layout_fn() if callable(layout_fn) else mod.layout
    except Exception:
        return html.Div()


def _initial_path_and_content():
    logged = bool(session.get("user_id"))

    if not logged:
        req_path = request.path if request else "/login"
        public_path = req_path if req_path in _PUBLIC_AUTH_PATHS else "/login"
        # No forzamos pathname. dcc.Location debe leer la URL real para que
        # /registro y /recuperar-password no reboten de vuelta a /login.
        return None, _auth_public_layout(public_path)

    # Authenticated: do not force a pathname. The layout is usually requested
    # through /_dash-layout, so request.path is not the browser route. Let
    # dcc.Location read window.location to avoid double navigation/loading.
    return None, None


def serve_layout():
    initial_path, initial_content = _initial_path_and_content()
    page_content = html.Div(id="page-content", className="page-shell", children=initial_content)

    # Pre-populate sidebar links so the sidebar is interactive before any callback fires.
    # _sidebar_links() reads from Flask session (available here since we're in a request context).
    try:
        initial_sidebar = _sidebar_links(initial_path or "/")
    except Exception:
        initial_sidebar = None

    # Build a fresh sidebar with pre-populated links (replaces the static module-level sidebar).
    _sidebar = html.Div(
        id="sidebar",
        children=[
            html.Div(
                className="sidebar-brand",
                children=[
                    html.Div(
                        className="sidebar-brand__mark",
                        children=[html.Img(src="/assets/logo_combatiq.svg", className="sidebar-brand__logo")],
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
            html.Div(id="sidebar-links", children=initial_sidebar),
            html.Div(
                className="sidebar-theme-footer",
                children=[
                    html.Button(
                        "✦ Asistente IA",
                        id="btn-chat-toggle",
                        n_clicks=0,
                        style={
                            "width": "100%", "marginBottom": "8px",
                            "background": "rgba(47,183,196,0.12)",
                            "border": "1px solid rgba(47,183,196,0.35)",
                            "borderRadius": "8px", "color": "var(--neon, #2fb7c4)",
                            "fontSize": "12px", "fontWeight": "700",
                            "padding": "8px 12px", "cursor": "pointer",
                            "textAlign": "left",
                        },
                    ),
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

    # ── Floating AI chat assistant ────────────────────────────────────────────
    _chat_panel = html.Div(
        id="ai-chat-wrapper",
        style={"display": "none"},
        children=[
            html.Div(
                style={
                    "position": "fixed", "bottom": "80px", "left": "4px",
                    "width": "264px", "zIndex": "9999",
                    "background": "var(--surface, #1a2535)",
                    "border": "1px solid var(--border, #243040)",
                    "borderRadius": "12px",
                    "boxShadow": "0 8px 32px rgba(0,0,0,0.5)",
                    "display": "flex", "flexDirection": "column",
                    "maxHeight": "440px",
                },
                children=[
                    html.Div(
                        style={
                            "display": "flex", "justifyContent": "space-between",
                            "alignItems": "center",
                            "padding": "10px 14px 8px",
                            "borderBottom": "1px solid var(--border, #243040)",
                        },
                        children=[
                            html.Div([
                                html.Span("✦ Asistente IA", style={
                                    "fontSize": "13px", "fontWeight": "700",
                                    "color": "var(--neon, #2fb7c4)",
                                }),
                                html.Span(" CombatIQ", style={
                                    "fontSize": "11px", "color": "var(--muted, #6b7f94)",
                                }),
                            ]),
                            html.Button("✕", id="btn-chat-close", n_clicks=0, style={
                                "background": "none", "border": "none",
                                "color": "var(--muted, #6b7f94)", "cursor": "pointer",
                                "fontSize": "14px", "padding": "0 2px",
                            }),
                        ],
                    ),
                    html.Div(
                        id="ai-chat-messages",
                        style={
                            "flex": "1", "overflowY": "auto",
                            "padding": "10px 14px",
                            "display": "flex", "flexDirection": "column",
                            "gap": "8px", "minHeight": "120px", "maxHeight": "280px",
                        },
                        children=[
                            html.Div(
                                "Hola — pregúntame sobre tu rendimiento, bienestar o próxima competición.",
                                style={
                                    "fontSize": "12px", "color": "var(--ink, #cdd8e8)",
                                    "background": "rgba(47,183,196,0.08)",
                                    "borderRadius": "8px", "padding": "7px 10px",
                                    "alignSelf": "flex-start", "maxWidth": "85%",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "display": "flex", "gap": "6px",
                            "padding": "8px 10px 10px",
                            "borderTop": "1px solid var(--border, #243040)",
                        },
                        children=[
                            dcc.Input(
                                id="ai-chat-input",
                                type="text",
                                placeholder="Escribe aquí…",
                                debounce=False,
                                n_submit=0,
                                style={
                                    "flex": "1", "background": "var(--bg, #0f1923)",
                                    "border": "1px solid var(--border, #243040)",
                                    "borderRadius": "6px", "color": "var(--ink, #cdd8e8)",
                                    "fontSize": "12px", "padding": "6px 10px",
                                    "outline": "none",
                                },
                            ),
                            html.Button("↑", id="btn-chat-send", n_clicks=0, style={
                                "background": "var(--neon, #2fb7c4)", "border": "none",
                                "color": "#0f1923", "fontWeight": "700",
                                "borderRadius": "6px", "padding": "6px 12px",
                                "cursor": "pointer", "fontSize": "14px",
                            }),
                        ],
                    ),
                ],
            ),
        ],
    )
    # Snapshot auth state from Flask session (available here in request context)
    # and persist it in a session-scoped dcc.Store so callbacks can read it
    # even when Flask session is not forwarded in the Dash POST context.
    _auth_data = {
        "user_id": session.get("user_id"),
        "role": session.get("role"),
        "name": session.get("name"),
        "sport": session.get("sport"),
    }
    _loc = (dcc.Location(id="url", pathname=initial_path)
            if initial_path is not None
            else dcc.Location(id="url"))
    return html.Div([
        _loc,
        _sidebar,
        page_content,
        dcc.Download(id="dl-png"),
        dcc.Download(id="dl-peaks"),
        dcc.Store(id="dl-png-clicks", data=0),
        dcc.Store(id="ui-sidebar-collapsed", data=False),
        dcc.Store(id="theme-store", storage_type="local", data="dark"),
        dcc.Store(id="ai-chat-history", data=[]),
        dcc.Store(id="auth-store", data=_auth_data),
        html.Div(id="theme-applied", style={"display": "none"}),
        html.Div(id="theme-charts-applied", style={"display": "none"}),
        html.Button("®", id="btn-toggle-sidebar", n_clicks=0, className="sidebar-toggle"),
        _chat_panel,
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


page_login, err_login         = _safe_import("pages.auth_login")
page_register, err_register   = _safe_import("pages.auth_register")
page_forgot, err_forgot       = _safe_import("pages.auth_forgot")
page_dashboard, err_dashboard = _safe_import("pages.dashboard")
page_logout, err_logout       = _safe_import("pages.logout")
page_onboarding, err_onboard  = _safe_import("pages.onboarding")
page_metricas, err_metricas   = _safe_import("pages.metricas")
page_chat, err_chat           = _safe_import("pages.chat")
page_sesiones, err_sesiones   = _safe_import("pages.sesiones")

# Registrar callbacks del chat si el módulo cargó bien
if page_chat and hasattr(page_chat, "register_callbacks"):
    try:
        page_chat.register_callbacks(app)
    except Exception as _e:
        import logging as _log
        _log.getLogger("combatiq").warning("chat callbacks no registrados: %s", _e)

if page_sesiones and hasattr(page_sesiones, "register_callbacks"):
    try:
        page_sesiones.register_callbacks(app)
    except Exception as _e:
        import logging as _log
        _log.getLogger("combatiq").warning("sesiones callbacks no registrados: %s", _e)


# =========================
#        VISTAS
# =========================

# ---- ESTADO DEL EQUIPO (coach) ----
def _coach_team_status_layout_v2(coach_sport, roster_count, team_count, roster_tab, teams_tab,
                                  checkins_today=0, alerts_today=0):
    sport_label = coach_sport or "Deporte de combate"
    checkin_color = "var(--neon)" if checkins_today == roster_count and roster_count > 0 else (
        "var(--punch)" if checkins_today == 0 else "var(--amber)"
    )
    alert_color = "var(--punch)" if alerts_today > 0 else "var(--neon)"

    return html.Div([
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport_label, className="session-pill"),
                    html.Span("Equipo", className="session-pill session-pill--muted"),
                ]),
                html.H2("Mi equipo"),
                html.P(
                    "Estado de hoy, gestión de plantilla y organización por grupos.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Lectura rápida", className="card-title"),
                html.Ul([
                    html.Li([
                        html.Strong("Check-ins hoy: "),
                        html.Span(
                            f"{checkins_today}/{roster_count}",
                            style={"color": checkin_color, "fontWeight": "700"},
                        ),
                    ]),
                    html.Li([
                        html.Strong("Atletas en alerta: "),
                        html.Span(
                            str(alerts_today) if alerts_today else "Ninguno",
                            style={"color": alert_color, "fontWeight": "700"},
                        ),
                    ]),
                    html.Li([html.Strong("Equipos activos: "), str(team_count)]),
                    html.Li("Baja al tab 'Mis deportistas' para ver el estado individual de cada atleta."),
                ], className="list-compact"),
                html.Div(style={"marginTop": "12px"}, children=[
                    html.A(
                        html.Button("📄 Informe del equipo (PDF)", className="btn btn-ghost btn-xs"),
                        href=f"/informe-equipo/{session.get('user_id', 0)}",
                        target="_blank",
                        style={"textDecoration": "none"},
                    ),
                ]),
            ]),
        ]),
        html.Div(className="kpis profile-kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Atletas", className="kpi-label"),
                html.Div(str(roster_count), className="kpi-value"),
                html.Div("En tu plantilla activa", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Check-ins hoy", className="kpi-label"),
                html.Div(
                    f"{checkins_today}/{roster_count}",
                    className="kpi-value",
                    style={"color": checkin_color},
                ),
                html.Div("Atletas que ya registraron su estado", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("En alerta hoy", className="kpi-label"),
                html.Div(
                    str(alerts_today) if alerts_today else "0",
                    className="kpi-value",
                    style={"color": alert_color},
                ),
                html.Div("Wellness < 50 hoy — requieren atención", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),
        dcc.Tabs(
            id="tabs-coach-users",
            value="tab-roster",
            className="combatiq-tabs",
            style={"marginTop": "16px"},
            children=[
                dcc.Tab(label="Mis deportistas", value="tab-roster",
                        className="combatiq-tab", selected_className="combatiq-tab--active",
                        children=[roster_tab]),
                dcc.Tab(label="Equipos", value="tab-teams",
                        className="combatiq-tab", selected_className="combatiq-tab--active",
                        children=[teams_tab]),
            ]
        ),
    ], className="page-content profile-shell coach-shell")


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
                            html.Span("⌄", className="collapsible-card__chevron"),
                        ],
                    ),
                    html.Div(className="collapsible-card__body", children=body_children),
                ],
            )

        roster_tab = html.Div(className="coach-stack", children=[
            dcc.Download(id="dl-team-csv"),
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

                    # ── Exportar equipo ───────────────────────────────────────
                    html.Div(
                        style={"display": "flex", "alignItems": "center",
                               "gap": "8px", "margin": "12px 0 4px"},
                        children=[
                            dcc.Dropdown(
                                id="dl-team-period",
                                options=[
                                    {"label": "Última semana",     "value": "7"},
                                    {"label": "Último mes",        "value": "30"},
                                    {"label": "Últimos 3 meses",   "value": "90"},
                                    {"label": "Todo el historial", "value": "0"},
                                ],
                                value="30",
                                clearable=False,
                                style={"width": "160px", "fontSize": "12px"},
                            ),
                            html.Button(
                                "↓ Excel",
                                id="btn-dl-team",
                                className="btn btn-ghost btn-xs",
                            ),
                            html.Span(
                                "Una fila por atleta con bienestar, carga y alertas.",
                                className="text-muted",
                                style={"fontSize": "11px"},
                            ),
                        ],
                    ),

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

        # ── KPIs en tiempo real ──────────────────────────────────────────────
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _checkins_today = 0
        _alerts_today   = 0
        _roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]
        try:
            _qs_bulk = db.list_questionnaires_bulk(_roster_ids) if _roster_ids else {}
        except Exception:
            _qs_bulk = {}
        for _a in roster:
            _aid = _a.get("id")
            if not _aid:
                continue
            try:
                _qs = _qs_bulk.get(int(_aid))
                if _qs is None:
                    _qs = db.list_questionnaires(int(_aid)) or []
                for _q in _qs:
                    _ts = (_q.get("ts") or "")[:10]
                    _sc = _q.get("wellness_score")
                    if _ts == _today_str and _sc is not None:
                        _checkins_today += 1
                        if float(_sc) < 50:
                            _alerts_today += 1
                        break
            except Exception:
                pass

        return _coach_team_status_layout_v2(
            coach_sport=coach_sport,
            roster_count=_roster_count,
            team_count=_team_count,
            roster_tab=roster_tab,
            teams_tab=teams_tab,
            checkins_today=_checkins_today,
            alerts_today=_alerts_today,
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
                            html.Button("Chat con el coach", className="btn btn-primary"),
                            href="/chat", className="link-btn",
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
                    dcc.Link(html.Button("Abrir mi sesión", className="btn btn-ghost"),
                             href="/sesion", className="link-btn"),
                    dcc.Link(html.Button("Señales ECG / IMU", className="btn btn-ghost"),
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
    """Plantilla del equipo: tarjetas con estado de hoy de cada atleta."""
    if not roster_display:
        return html.Div(className="inner-card data-empty-state", children=[
            html.P("Tu plantilla está vacía.", className="empty-state-title"),
            html.P("Busca deportistas arriba y añádelos a tu plantilla.", className="text-muted"),
        ])

    def _ini(name):
        parts = (name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    _ABBREV = {"Taekwondo": "TKD", "Box": "BOX", "Boxeo": "BOX"}
    today_str = datetime.now().strftime("%Y-%m-%d")
    roster_ids = [int(a.get("id")) for a in roster_display if a.get("id") is not None]
    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}

    # ── Enriquecer con wellness de hoy ──────────────────────────────────────
    enriched = []
    for a in roster_display:
        aid = a.get("id")
        today_score = None
        last_score = None
        last_ts = None
        if aid:
            try:
                qs = qs_bulk.get(int(aid), [])
                for q in qs:
                    ts = (q.get("ts") or "")[:10]
                    score = q.get("wellness_score")
                    if last_ts is None and score is not None:
                        last_score = float(score)
                        last_ts = (q.get("ts") or "")[:16].replace("T", " ")
                    if ts == today_str and score is not None:
                        today_score = float(score)
                        break
            except Exception:
                pass
        enriched.append({**a, "_today": today_score, "_last": last_score, "_last_ts": last_ts})

    done_today = sum(1 for a in enriched if a["_today"] is not None)
    total = len(enriched)

    # ── Status helpers ───────────────────────────────────────────────────────
    def _status(score):
        if score is None:
            return "Pendiente", "var(--muted)", "roster-status--pending"
        if score >= 80:
            return "Listo", "var(--neon)", "roster-status--ok"
        if score >= 65:
            return "Bien", "#f0a832", "roster-status--good"
        if score >= 50:
            return "Atención", "#e45a5a", "roster-status--warn"
        return "Bajo", "var(--punch)", "roster-status--low"

    # ── Alerta de bienestar bajo ─────────────────────────────────────────────
    alert_names = [a.get("name", "—") for a in enriched if a["_today"] is not None and a["_today"] < 50]
    alert_bar = None
    if alert_names:
        plural = "s" if len(alert_names) > 1 else ""
        alert_bar = html.Div(
            style={
                "background": "rgba(228,90,90,0.12)",
                "border": "1px solid rgba(228,90,90,0.40)",
                "borderRadius": "8px",
                "padding": "10px 14px",
                "marginBottom": "10px",
                "display": "flex",
                "alignItems": "center",
                "gap": "10px",
            },
            children=[
                html.Span("⚠", style={"color": "var(--punch)", "fontSize": "16px", "flexShrink": "0"}),
                html.Div([
                    html.Span(
                        f"{len(alert_names)} deportista{plural} con bienestar bajo hoy — ",
                        style={"color": "var(--punch)", "fontWeight": "700", "fontSize": "13px"},
                    ),
                    html.Span(
                        ", ".join(alert_names),
                        style={"color": "var(--ink)", "fontSize": "13px"},
                    ),
                ]),
            ],
        )

    # ── Summary bar ─────────────────────────────────────────────────────────
    pending = total - done_today
    bar_color = "var(--neon)" if pending == 0 else ("var(--punch)" if pending > total // 2 else "#f0a832")
    summary_bar = html.Div(
        className="roster-summary-bar",
        children=[
            html.Span(
                f"{done_today}/{total} check-ins hoy",
                style={"fontWeight": "700", "color": bar_color, "fontSize": "13px"},
            ),
            html.Span(
                f"· {pending} pendiente{'s' if pending != 1 else ''}",
                style={"color": "var(--muted)", "fontSize": "12px", "marginLeft": "6px"},
            ) if pending else None,
        ],
    )

    # ── Cards ────────────────────────────────────────────────────────────────
    cards = []
    for a in enriched:
        name = a.get("name", "—")
        sport = a.get("sport") or ""
        joined = a.get("created_at") or "—"
        aid = a.get("id")
        today_score = a["_today"]
        last_score = a["_last"]
        last_ts = a["_last_ts"]
        sport_key = (sport or "").lower()
        sport_abbr = _ABBREV.get(sport, sport[:3].upper() if sport else "—")
        lbl, color, cls = _status(today_score)

        score_display = f"{today_score:.0f}" if today_score is not None else (
            f"{last_score:.0f}" if last_score is not None else "—"
        )
        score_sub = "Hoy" if today_score is not None else (
            f"Último: {last_ts[:10] if last_ts else '—'}"
        )

        cards.append(html.Div(
            className="roster-card",
            children=[
                # Avatar + nombre
                html.Div(className="roster-card__left", children=[
                    html.Div(_ini(name), className="athlete-row__avatar"),
                    html.Div(className="athlete-row__info", children=[
                        html.Div(name, className="athlete-row__name"),
                        html.Div(
                            f"{sport} · desde {joined}",
                            className="athlete-row__meta",
                        ),
                    ]),
                ]),
                # Score + status
                html.Div(className="roster-card__status", children=[
                    html.Div(
                        className=f"roster-status {cls}",
                        children=[
                            html.Span(score_display, className="roster-status__score"),
                            html.Span(lbl, className="roster-status__lbl"),
                        ],
                        style={"color": color},
                    ),
                    html.Div(score_sub, className="roster-status__sub"),
                ]),
                # Acciones rápidas
                html.Div(className="roster-card__actions", children=[
                    html.Span(sport_abbr, className=f"sport-badge sport-badge--{sport_key}"),
                    dcc.Link(
                        html.Button("Análisis", className="btn btn-ghost btn-xs"),
                        href="/analisis",
                    ) if aid else None,
                    html.A(
                        "PDF",
                        href=f"/informe/{aid}",
                        className="btn btn-ghost btn-xs",
                        target="_blank",
                        style={"textDecoration": "none"},
                    ) if aid else None,
                ]),
            ],
        ))

    return html.Div(children=[
        x for x in [alert_bar, summary_bar, html.Div(className="roster-grid", children=cards)]
        if x is not None
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

    _adopt_err = None
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
    except Exception as _e:
        _adopt_err = str(_e)

    roster, roster_opts = _refresh_roster_and_opts(coach_id)
    roster_ids = {a["id"] for a in _coach_roster(coach_id)}
    updated_search = _render_search_results(stored_results or [], roster_ids)
    msg = f"Error al añadir deportista: {_adopt_err}" if _adopt_err else "Deportista añadido a la plantilla."
    return _render_plantilla(roster), roster_opts, roster_opts, msg, updated_search


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
    if _to_str(session.get("role")) != "coach":
        return _render_team_members([]), []
    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return _render_team_members([]), []
    if hasattr(db, "team_belongs_to_coach") and not db.team_belongs_to_coach(int(team_id), coach_id):
        return _render_team_members([]), []
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
        return dash.no_update, dash.no_update, "db.py no soporta equipos aun (add_team_member)."
    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return dash.no_update, dash.no_update, "Sesión inválida. Vuelve a iniciar sesión."
    if hasattr(db, "team_belongs_to_coach") and not db.team_belongs_to_coach(int(team_id), coach_id):
        return dash.no_update, dash.no_update, "No tienes permisos sobre este equipo."
    coach_sport = _to_str(session.get("sport") or "") or None
    if hasattr(db, "coach_has_athlete") and not db.coach_has_athlete(coach_id, int(athlete_id), sport=coach_sport):
        return dash.no_update, dash.no_update, "Ese deportista no pertenece a tu plantilla."
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
        return dash.no_update, dash.no_update, "db.py no soporta equipos aun (remove_team_member)."
    if _to_str(session.get("role")) != "coach":
        return dash.no_update, dash.no_update, "No tienes permisos."
    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return dash.no_update, dash.no_update, "Sesión inválida. Vuelve a iniciar sesión."
    if hasattr(db, "team_belongs_to_coach") and not db.team_belongs_to_coach(int(team_id), coach_id):
        return dash.no_update, dash.no_update, "No tienes permisos sobre este equipo."
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
                    html.Span("⌄", className="collapsible-card__chevron"),
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
                    "Selecciona un atleta de tu plantilla para ver su ficha completa: estado físico, alertas activas, análisis AI y últimas métricas.",
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
        qrows = db.list_questionnaires(selected_id, limit=14) or []
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

    # ── Racha del atleta ──────────────────────────────────────────────────
    try:
        from pages.home import _calc_streak as _cs_fn
        _st = _cs_fn(selected_id)
        streak_cur  = _st["current"]
        streak_best = _st["best"]
    except Exception:
        streak_cur = streak_best = 0

    # ── Próxima competencia del atleta ────────────────────────────────────
    next_comp = None
    next_comp_days = None
    try:
        next_comp = db.get_next_competition(selected_id)
        if next_comp:
            from datetime import date as _ddate
            next_comp_days = (_ddate.fromisoformat(next_comp["event_date"]) - _ddate.today()).days
    except Exception:
        pass

    # ── Mini gráfico wellness (últimos 14 registros) ───────────────────────
    wellness_fig = None
    try:
        import plotly.graph_objects as _pgo
        from ui_charts import apply_chart_style
        ws_vals = [float(q["wellness_score"]) for q in qrows[:14]
                   if q.get("wellness_score") is not None][::-1]
        ws_dates = [(q.get("ts") or "")[:10] for q in qrows[:14]
                    if q.get("wellness_score") is not None][::-1]
        if len(ws_vals) >= 2:
            colors_ws = ["#2fb7c4" if v >= 80 else "#f0a832" if v >= 65 else
                         "#e09050" if v >= 50 else "#e45a5a" for v in ws_vals]
            wellness_fig = _pgo.Figure()
            wellness_fig.add_trace(_pgo.Scatter(
                x=ws_dates, y=ws_vals, mode="lines+markers",
                line=dict(color="rgba(47,183,196,.4)", width=2),
                marker=dict(size=8, color=colors_ws),
                hovertemplate="%{y:.0f}/100<extra></extra>",
            ))
            wellness_fig.add_hline(y=65, line_dash="dot",
                                   line_color="rgba(240,168,50,.4)", line_width=1)
            apply_chart_style(wellness_fig, height=160)
            wellness_fig.update_layout(
                margin=dict(l=4, r=4, t=8, b=4),
                yaxis=dict(range=[0, 105], showgrid=False),
                xaxis=dict(showticklabels=False),
                showlegend=False,
            )
    except Exception:
        wellness_fig = None

    # ── Alertas activas del atleta (análisis rápido) ──────────────────────
    _report = {}
    try:
        import analysis_engine as _AE
        _report = _AE.full_report(uid=selected_id, db_module=db)
        active_alerts = _report.get("alerts") or []
    except Exception:
        active_alerts = []

    # ── Nota de coaching AI — cargada lazily por load_athlete_card_ai_note ──

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

    w_color = ("#2fb7c4" if wellness and wellness >= 80 else
               "#f0a832" if wellness and wellness >= 65 else
               "#e09050" if wellness and wellness >= 50 else
               "#e45a5a" if wellness else "var(--muted)")

    streak_color = ("var(--neon)" if streak_cur >= 7 else
                    "#f0a832"     if streak_cur >= 3 else "var(--ink)")

    comp_kpi_val  = f"{next_comp_days}d" if next_comp_days is not None and next_comp_days >= 0 else "—"
    comp_kpi_sub  = next_comp.get("name", "")[:24] if next_comp else "Sin competencia próxima"

    alert_color = "var(--punch)" if active_alerts else "var(--muted)"

    metrics = html.Div(className="kpis profile-kpis", children=[
        html.Div(className="kpi", children=[
            html.Div("Estado del día", className="kpi-label"),
            html.Div(wellness_value, className="kpi-value", style={"color": w_color}),
            html.Div(readiness_hint, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Racha check-ins 🔥", className="kpi-label"),
            html.Div(f"{streak_cur} días", className="kpi-value",
                     style={"color": streak_color}),
            html.Div(f"Mejor: {streak_best} días", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Próxima competencia", className="kpi-label"),
            html.Div(comp_kpi_val, className="kpi-value"),
            html.Div(comp_kpi_sub, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
        html.Div(className="kpi", children=[
            html.Div("Alertas activas", className="kpi-label"),
            html.Div(str(len(active_alerts)), className="kpi-value",
                     style={"color": alert_color}),
            html.Div("Cardio, carga y bienestar" if active_alerts else "Sin alertas hoy", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ])

    context_fold = _athlete_sheet_fold(
        "Resumen del atleta",
        "Aquí ves el contexto base con el que conviene leer su día.",
        [
            html.Div(className="profile-context-grid", children=[
                html.Div(className="profile-grid-item", children=[
                    html.Div("Plan actual", className="kpi-label"),
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

    # Mini wellness chart card
    wellness_trend_card = html.Div(className="card", style={"marginBottom": "14px"}, children=[
        html.H4("Tendencia bienestar (últimas lecturas)", className="card-title"),
        dcc.Graph(figure=wellness_fig, config={"displayModeBar": False})
        if wellness_fig else
        html.P("Sin check-ins suficientes para mostrar tendencia.", className="text-muted"),
    ])

    # Alertas card — nota IA inyectada lazily por load_athlete_card_ai_note
    alerts_card = html.Div(className="card", style={"marginBottom": "14px"}, children=[
        html.H4("Alertas activas", className="card-title"),
        *([html.Div(className="alert-item alert-item--warn", children=[
            html.Span("⚠", className="alert-icon"),
            html.Span(
                f"{a.get('title', 'Alerta')}: {a.get('message') or a.get('msg') or str(a)}",
                className="alert-msg",
            ),
        ]) for a in active_alerts[:4]]
        if active_alerts else
        [html.P("Sin alertas activas hoy.", className="text-muted")]),
        html.Div(id="athlete-alert-note"),  # rellenado por load_athlete_card_ai_note
    ])

    resources_card = html.Div(className="card profile-links-card", children=[
        html.H4("Contacto y acciones", className="card-title"),
        html.Div(className="profile-note", children=[
            html.Strong("Correo: "),
            html.Span(email or "No disponible"),
        ]),
        html.Div(className="spacer-10"),
        html.H4("Sensores asignados", className="card-title"),
        html.Div(className="teammate-chips", children=[
            html.Span(lbl, className="teammate-chip") for lbl in sens_labels
        ]) if sens_labels else html.P("Sin sensores asignados.", className="text-muted"),
        html.Div(className="spacer-10"),
        html.Div(className="row-wrap-10 session-action-row", children=[
            html.A(
                "Descargar PDF",
                href=f"/informe/{selected_id}",
                className="btn btn-primary",
                target="_blank",
                style={"textDecoration": "none"},
            ),
            html.A("Correo", href=f"mailto:{email}", className="btn btn-ghost") if email else None,
            dcc.Link(html.Button("Análisis", className="btn btn-ghost"), href="/analisis"),
            dcc.Link(html.Button("Equipo", className="btn btn-ghost"), href="/usuarios"),
        ]),
    ])

    # ── Trofeos del atleta (vista solo lectura para el coach) ─────────────────
    try:
        _results = db.list_competition_results(int(selected_id)) or []
    except Exception:
        _results = []

    _MEDAL_EMOJI = {"gold":"🥇","silver":"🥈","bronze":"🥉","finalist":"🏅","participant":"🎖️"}
    _MEDAL_LABEL = {"gold":"Oro","silver":"Plata","bronze":"Bronce","finalist":"Finalista","participant":"Participante"}

    trophy_items = []
    for r in _results:
        medal = (r.get("medal") or "participant").lower()
        trophy_items.append(html.Div(className="trophy-card", children=[
            html.Div(_MEDAL_EMOJI.get(medal,"🎖️"), className=f"trophy-badge trophy-badge--{medal}"),
            html.Span(_MEDAL_LABEL.get(medal,medal.capitalize()), className=f"trophy-medal-chip trophy-medal-chip--{medal}"),
            html.Div(r.get("name",""), className="trophy-name"),
            html.Div(r.get("event_date","")[:10], className="trophy-date"),
        ]))

    trophies_card = html.Div(className="card", children=[
        html.Div(className="card-title-row", children=[
            html.H4("Trofeos del atleta", className="card-title"),
            html.Span(f"{len(_results)} logros", style={"fontSize":"12px","color":"var(--amber)" if _results else "var(--muted)","fontWeight":"600"}),
        ]),
        html.Div(className="trophy-shelf", children=trophy_items) if trophy_items
        else html.P("Sin logros registrados todavía.", className="text-muted"),
        html.Div(className="ecg-divider", style={"margin":"14px 0"}),
        html.H4("Registrar resultado", className="card-title"),
        html.P("Guarda el resultado de una competencia completada.", className="text-muted", style={"marginBottom":"12px"}),
        html.Div(className="filters-bar filters-bar--2", children=[
            html.Div(className="filter-item", children=[
                html.Label("Nombre de la competencia"),
                dcc.Input(id="tr-name", type="text", placeholder="Ej. Torneo Primavera 2026",
                          style={"width":"100%"}),
            ]),
            html.Div(className="filter-item", children=[
                html.Label("Fecha"),
                dcc.DatePickerSingle(id="tr-date", display_format="YYYY-MM-DD",
                                     placeholder="YYYY-MM-DD",
                                     style={"width":"100%"}),
            ]),
        ]),
        html.Div(className="filters-bar filters-bar--2", style={"marginTop":"10px"}, children=[
            html.Div(className="filter-item", children=[
                html.Label("Resultado"),
                dcc.Dropdown(id="tr-medal", clearable=False, value="gold", options=[
                    {"label":"🥇 Medalla de Oro",      "value":"gold"},
                    {"label":"🥈 Medalla de Plata",    "value":"silver"},
                    {"label":"🥉 Medalla de Bronce",   "value":"bronze"},
                    {"label":"🏅 Finalista",            "value":"finalist"},
                    {"label":"🎖️ Participante",         "value":"participant"},
                ]),
            ]),
            html.Div(className="filter-item", children=[
                html.Label("Categoría (opcional)"),
                dcc.Input(id="tr-category", type="text", placeholder="Ej. -68 kg Senior",
                          style={"width":"100%"}),
            ]),
        ]),
        html.Div(className="btn-save-row", style={"marginTop":"14px"}, children=[
            html.Button("Guardar logro", id="btn-save-trophy", n_clicks=0, className="btn btn-primary"),
            html.Div(id="trophy-save-msg", className="text-muted"),
        ]),
    ])

    # ── Notas de sesión (coach → atleta) ──────────────────────────────────
    try:
        _past_notes = db.list_session_notes(selected_id, coach_id=int(coach_id), limit=5) or []
    except Exception:
        _past_notes = []

    def _note_item(n):
        ts = (n.get("created_at") or "")[:16].replace("T", " ")
        return html.Div(className="note-item", children=[
            html.Span(str(n.get("note") or ""), className="note-item__text"),
            html.Div(ts, className="note-item__date"),
        ])

    notes_card = html.Div(className="card", children=[
        html.H4("Notas de sesión", className="card-title"),
        html.P("Apunta observaciones privadas de este atleta.", className="text-muted",
               style={"marginBottom": "10px"}),
        html.Div(
            className="note-list",
            children=[_note_item(n) for n in _past_notes]
            if _past_notes else [html.P("Sin notas todavía.", className="text-muted")],
        ),
        html.Div(className="ecg-divider", style={"margin": "12px 0"}),
        dcc.Textarea(
            id="note-text",
            placeholder="Escribe una nota sobre la sesión de hoy…",
            style={"width": "100%", "minHeight": "72px", "resize": "vertical"},
        ),
        html.Div(className="btn-save-row", style={"marginTop": "10px"}, children=[
            html.Button("Guardar nota", id="btn-save-note", n_clicks=0,
                        className="btn btn-ghost",
                        style={"fontSize": "13px", "padding": "6px 16px"}),
            html.Div(id="note-save-msg", className="text-muted", style={"fontSize": "12px"}),
        ]),
    ])

    ai_note_card = html.Div(
        className="card",
        style={"marginBottom": "16px"},
        children=[
            html.Div(
                className="card-title-row",
                children=[
                    html.H4("Análisis AI del día", className="card-title"),
                    html.Div(
                        style={"display": "flex", "gap": "8px", "alignItems": "center", "flexWrap": "wrap"},
                        children=[
                            html.Span("Claude · CombatIQ",
                                      style={"fontSize": "11px", "color": "var(--neon)",
                                             "fontWeight": "600", "fontFamily": "monospace"}),
                            html.Button(
                                "Generar análisis IA",
                                id="btn-athlete-card-ai-note",
                                n_clicks=0,
                                className="btn btn-ghost",
                                style={"fontSize": "11px", "padding": "5px 10px"},
                            ),
                        ],
                    ),
                ],
            ),
            dcc.Loading(type="dot", color="var(--neon)",
                        children=html.Div(
                            id="athlete-card-ai-note",
                            children=html.P(
                                'Pulsa "Generar análisis IA" para crear una lectura contextual del atleta seleccionado.',
                                className="text-muted",
                            ),
                        )),
        ],
    )

    return html.Div(className="coach-stack", children=[
        hero,
        metrics,
        ai_note_card,
        html.Div(className="profile-main-grid", children=[
            html.Div(className="profile-stack", children=[
                context_fold,
                wellness_trend_card,
                detail_fold,
            ]),
            html.Div(className="profile-stack", children=[
                alerts_card,
                signal_fold,
                trophies_card,
                notes_card,
                resources_card,
            ]),
        ]),
    ])


@app.callback(
    Output("athlete-card-ai-note",  "children"),
    Output("athlete-alert-note",    "children"),
    Input("btn-athlete-card-ai-note", "n_clicks"),
    Input("athlete-select-v2", "value"),
    prevent_initial_call=True,
)
def load_athlete_card_ai_note(n_ai, user_id):
    _empty_alert = html.Span()   # placeholder vacío para el output de alertas

    if not user_id:
        raise PreventUpdate
    if callback_context.triggered_id != "btn-athlete-card-ai-note":
        return (
            html.P(
                'Atleta seleccionado. Pulsa "Generar análisis IA" para crear una lectura contextual sin bloquear la navegación.',
                className="text-muted",
            ),
            _empty_alert,
        )
    if not n_ai:
        raise PreventUpdate
    role = _to_str(session.get("role")) or ""
    coach_id = session.get("user_id")
    if role != "coach" or not coach_id:
        raise PreventUpdate

    try:
        selected_id = int(user_id)
        athletes = _coach_roster(int(coach_id))
        if not any(int(a["id"]) == selected_id for a in athletes if a.get("id") is not None):
            raise PreventUpdate
    except (TypeError, ValueError):
        raise PreventUpdate

    u = db.get_user_by_id(selected_id)
    if not u:
        raise PreventUpdate
    name  = _profile_label(u.get("name"), "Atleta")
    sport = _profile_label(u.get("sport"), "")

    _report = {}
    try:
        import analysis_engine as _AE
        _report = _AE.full_report(uid=selected_id, db_module=db)
    except Exception:
        pass

    _extra_ai = {}
    try:
        _nc = db.get_next_competition(selected_id)
        if _nc:
            from datetime import date as _ddate
            _extra_ai["competition"] = {
                "name":       _nc.get("name", ""),
                "event_date": _nc.get("event_date", ""),
                "days_until": (_ddate.fromisoformat(_nc["event_date"]) - _ddate.today()).days,
            }
    except Exception:
        pass

    note = ""
    try:
        import ai_insights as _AI
        note = _AI.generate_coaching_note(_report, athlete_name=name, sport=sport,
                                          extra=_extra_ai or None)
    except Exception:
        pass

    # Nota IA sobre la alerta más crítica (Haiku, no bloquea si falla)
    alert_note_el = _empty_alert
    _active_alerts = _report.get("alerts") or []
    if _active_alerts:
        try:
            import ai_insights as _AI
            _alert_txt = _AI.generate_alert_note(
                _active_alerts[0],
                athlete_name=name,
                sport=sport,
            )
            if _alert_txt:
                alert_note_el = html.P(
                    _alert_txt,
                    style={
                        "fontSize": "11px", "color": "var(--neon)",
                        "marginTop": "8px",  "paddingLeft": "24px",
                        "lineHeight": "1.4",
                    },
                )
        except Exception:
            pass

    if not note:
        return (
            html.P(
                "Configura ANTHROPIC_API_KEY en .env para activar el análisis narrativo con IA.",
                className="text-muted",
            ),
            alert_note_el,
        )
    return dcc.Markdown(note, className="ai-note"), alert_note_el


@app.callback(
    Output("trophy-save-msg", "children"),
    Input("btn-save-trophy", "n_clicks"),
    State("athlete-select-v2", "value"),
    State("tr-name",     "value"),
    State("tr-date",     "date"),
    State("tr-medal",    "value"),
    State("tr-category", "value"),
    prevent_initial_call=True,
)
def save_trophy(n, athlete_id, name, event_date, medal, category):
    from dash.exceptions import PreventUpdate
    if not n:
        raise PreventUpdate
    role = _to_str(session.get("role")) or ""
    if role != "coach":
        return "Solo el coach puede registrar resultados."
    if not athlete_id:
        return "Selecciona un atleta primero."
    coach_sport = _to_str(session.get("sport") or "") or None
    coach_id = session.get("user_id")
    try:
        coach_id_int = int(coach_id)
        athlete_id_int = int(athlete_id)
    except Exception:
        return "Selección de atleta inválida."
    if not coach_id or not db.coach_has_athlete(coach_id_int, athlete_id_int, sport=coach_sport):
        return "Ese atleta no pertenece a tu plantilla."
    if not name or not name.strip():
        return "Escribe el nombre de la competencia."
    if not event_date:
        return "Selecciona la fecha del evento."
    try:
        db.add_competition_result(
            athlete_id_int,
            name.strip(),
            event_date[:10],
            medal=medal or "participant",
            category=(category or "").strip() or None,
        )
        return "Logro guardado. Recarga la ficha para verlo."
    except Exception:
        return "Error al guardar el logro. Inténtalo de nuevo."


@app.callback(
    Output("note-save-msg", "children"),
    Input("btn-save-note", "n_clicks"),
    State("athlete-select-v2", "value"),
    State("note-text", "value"),
    prevent_initial_call=True,
)
def save_session_note(n, athlete_id, note_text):
    from dash.exceptions import PreventUpdate
    if not n:
        raise PreventUpdate
    role = _to_str(session.get("role")) or ""
    if role != "coach":
        return "Solo el coach puede guardar notas."
    coach_id = session.get("user_id")
    if not athlete_id or not coach_id:
        return "Selecciona un atleta primero."
    coach_sport = _to_str(session.get("sport") or "") or None
    try:
        coach_id_int = int(coach_id)
        athlete_id_int = int(athlete_id)
    except Exception:
        return "Selección de atleta inválida."
    if not db.coach_has_athlete(coach_id_int, athlete_id_int, sport=coach_sport):
        return "Ese atleta no pertenece a tu plantilla."
    if not note_text or not note_text.strip():
        return "Escribe algo antes de guardar."
    try:
        db.add_session_note(coach_id_int, athlete_id_int, note_text.strip())
        return "Nota guardada. Recarga la ficha para verla."
    except Exception as exc:
        return f"Error: {exc}"


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

    # Comunicados ya publicados
    existing_anns = db.list_announcements_for_coach(int(coach_id)) if coach_id else []
    ann_count = len(existing_anns)

    def _ann_item(a):
        ts_label = (a.get("created_at") or "")[:16].replace("T", " ")
        pinned = a.get("pinned")
        return html.Div(className="ann-item" + (" ann-item--pinned" if pinned else ""), children=[
            html.Div(className="ann-item__header", children=[
                html.Span(
                    ["📌 ", a.get("title", "")] if pinned else a.get("title", ""),
                    className="ann-item__title",
                ),
                html.Span(ts_label, className="ann-item__date"),
            ]),
            html.P(a.get("body") or "", className="ann-item__body") if a.get("body") else None,
        ])

    ann_list = [_ann_item(a) for a in existing_anns] if existing_anns else [
        html.P("Aún no has publicado ningún comunicado.", className="text-muted")
    ]

    return html.Div(className="coach-shell", children=[
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport_label.title(), className="session-pill"),
                    html.Span("Coach · Comunicados", className="session-pill session-pill--muted"),
                ]),
                html.H2("Comunicados"),
                html.P(
                    f"Publica avisos para los {len(athletes)} deportistas de {sport_label}. "
                    "Lo verán la próxima vez que abran la app.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Estado del canal", className="card-title"),
                html.Ul(className="list-compact", children=[
                    html.Li([html.Strong("Plantilla: "), f"{len(athletes)} deportista{'s' if len(athletes) != 1 else ''} en {sport_label}"]),
                    html.Li([html.Strong("Publicados: "), f"{ann_count} comunicado{'s' if ann_count != 1 else ''}"]),
                    html.Li([html.Strong("Con correo BCC: "), f"{len(emails)} destinatario{'s' if len(emails) != 1 else ''}"]),
                ]),
            ]),
        ]),

        html.Div(className="kpis profile-kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Deportistas", className="kpi-label"),
                html.Div(str(len(athletes)), className="kpi-value"),
                html.Div("En tu plantilla activa", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Publicados", className="kpi-label"),
                html.Div(str(ann_count), className="kpi-value"),
                html.Div("Comunicados en la app", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("BCC disponible", className="kpi-label"),
                html.Div(str(len(emails)), className="kpi-value"),
                html.Div("Con correo registrado", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),

        html.Div(className="ecg-divider ecg-divider--spaced"),

        # Card: nuevo comunicado in-app
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Publicar comunicado", className="card-title"),
            html.P(
                "El aviso aparecerá en la sección de comunicados de todos tus deportistas.",
                className="text-muted",
                style={"marginBottom": "16px"},
            ),
            html.Div(className="auth-field", style={"marginBottom": "12px"}, children=[
                html.Label("Título", htmlFor="ann-title"),
                dcc.Input(
                    id="ann-title",
                    type="text",
                    placeholder="Ej: Cambio de horario el viernes",
                    className="auth-input",
                    style={"width": "100%"},
                    maxLength=120,
                ),
            ]),
            html.Div(className="auth-field", style={"marginBottom": "12px"}, children=[
                html.Label("Mensaje (opcional)", htmlFor="ann-body"),
                dcc.Textarea(
                    id="ann-body",
                    placeholder="Escribe el detalle del aviso...",
                    style={"width": "100%", "minHeight": "90px", "resize": "vertical",
                           "padding": "10px", "borderRadius": "8px",
                           "border": "1px solid var(--line)", "background": "var(--surface)",
                           "color": "var(--ink)", "fontSize": "14px", "fontFamily": "inherit"},
                ),
            ]),
            html.Div(className="auth-field", style={"marginBottom": "16px"}, children=[
                dcc.Checklist(
                    id="ann-pinned",
                    options=[{"label": " Fijar en la parte superior", "value": "pinned"}],
                    value=[],
                    style={"fontSize": "14px"},
                ),
            ]),
            html.Div(className="btn-save-row", children=[
                html.Button("Publicar comunicado", id="ann-submit", className="btn btn-primary", n_clicks=0),
                html.Div(id="ann-feedback", className="text-muted", style={"fontSize": "13px"}),
            ]),
        ]),

        # Card: correo BCC (mantener como segunda opción)
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Envío masivo por correo (BCC)", className="card-title"),
            html.P(
                "Abre tu cliente de correo con el equipo completo en copia oculta.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            html.Div(className="btn-save-row", children=[
                html.A(
                    "Abrir cliente de correo (BCC)",
                    href=mailto_link,
                    className="btn btn-ghost" if emails else "btn btn-ghost btn-disabled",
                    style={"pointerEvents": "auto" if emails else "none", "opacity": 1.0 if emails else 0.4},
                ),
                html.Span(
                    f"{len(emails)} destinatario{'s' if len(emails) != 1 else ''}" if emails else "Sin destinatarios",
                    className="text-muted",
                    style={"fontSize": "12px"},
                ),
            ]),
            html.Div(athlete_rows, style={"marginTop": "14px"}),
        ]),

        # Card: historial de comunicados
        html.Div(className="card", id="ann-history-card", children=[
            html.H4("Historial de comunicados", className="card-title"),
            html.Div(id="ann-list", children=ann_list),
        ]),
    ])


@app.callback(
    Output("ann-feedback", "children"),
    Output("ann-list", "children"),
    Output("ann-title", "value"),
    Output("ann-body", "value"),
    Input("ann-submit", "n_clicks"),
    State("ann-title", "value"),
    State("ann-body", "value"),
    State("ann-pinned", "value"),
    prevent_initial_call=True,
)
def post_announcement(n, title, body, pinned_val):
    if not n:
        raise dash.exceptions.PreventUpdate
    role = _to_str(session.get("role")) or ""
    coach_id = session.get("user_id")
    if role != "coach" or not coach_id:
        return "Sin permisos.", dash.no_update, dash.no_update, dash.no_update
    title = (title or "").strip()
    if not title:
        return "El título es obligatorio.", dash.no_update, dash.no_update, dash.no_update

    coach_sport = (_to_str(session.get("sport")) or "").strip()
    pinned = bool(pinned_val and "pinned" in pinned_val)
    db.add_announcement(int(coach_id), coach_sport, title, body or "", pinned)

    # Reconstruye la lista actualizada
    anns = db.list_announcements_for_coach(int(coach_id))

    def _ann_item(a):
        ts_label = (a.get("created_at") or "")[:16].replace("T", " ")
        p = a.get("pinned")
        return html.Div(className="ann-item" + (" ann-item--pinned" if p else ""), children=[
            html.Div(className="ann-item__header", children=[
                html.Span(["📌 ", a.get("title", "")] if p else a.get("title", ""),
                          className="ann-item__title"),
                html.Span(ts_label, className="ann-item__date"),
            ]),
            html.P(a.get("body") or "", className="ann-item__body") if a.get("body") else None,
        ])

    new_list = [_ann_item(a) for a in anns] if anns else [
        html.P("Aún no has publicado ningún comunicado.", className="text-muted")
    ]

    # ── Notificar a atletas del equipo si tienen email ──────────────────
    try:
        import notifications as _N
        import threading as _thr
        if _N.is_configured():
            _athletes = db.list_roster_for_coach(int(coach_id), sport=coach_sport) or []
            _emails = []
            for _a in _athletes:
                _prefs = db.get_notification_prefs(_a["id"])
                if _prefs.get("announcement_notify", 1):
                    _addr = _a.get("email") or _a.get("correo")
                    if _addr and "@" in _addr:
                        _emails.append(_addr)
            if _emails:
                _coach_nm = _to_str(session.get("name")) or "Tu coach"
                _thr.Thread(
                    target=_N.notify_athlete_new_announcement,
                    args=(_emails, coach_sport, title, body or "", _coach_nm),
                    daemon=True,
                ).start()
    except Exception:
        pass

    return "✓ Comunicado publicado.", new_list, "", ""


# ---- COMUNICADOS DEL COACH (vista lectura para deportistas) ----
def view_mis_comunicados():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or ""
    if role not in ("deportista", "admin"):
        return html.Div("Esta sección es para deportistas.", className="text-muted")

    sport = (_to_str(session.get("sport")) or "").strip()
    anns = []
    if sport:
        try:
            anns = db.list_announcements_for_sport(sport) or []
        except Exception:
            anns = []

    def _ann_card(a):
        ts_label = (a.get("created_at") or "")[:16].replace("T", " ")
        coach_name = a.get("coach_name") or "Tu coach"
        pinned = a.get("pinned")
        return html.Div(
            className="card" + (" ann-item--pinned" if pinned else ""),
            style={"marginBottom": "12px",
                   "borderLeft": "4px solid var(--neon)" if pinned else "4px solid var(--line)"},
            children=[
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between",
                           "alignItems": "flex-start", "flexWrap": "wrap", "gap": "6px",
                           "marginBottom": "8px"},
                    children=[
                        html.Div(children=[
                            html.Span("📌 " if pinned else "", style={"color": "var(--neon)"}),
                            html.Strong(a.get("title", "Sin título"), style={"fontSize": "15px"}),
                        ]),
                        html.Div(children=[
                            html.Span(coach_name, className="text-muted",
                                      style={"fontSize": "12px", "marginRight": "8px"}),
                            html.Span(ts_label, className="text-muted", style={"fontSize": "12px"}),
                        ]),
                    ],
                ),
                html.P(a.get("body") or "", className="text-muted",
                       style={"fontSize": "14px", "lineHeight": "1.6"}) if a.get("body") else None,
            ],
        )

    pinned_anns = [a for a in anns if a.get("pinned")]
    regular_anns = [a for a in anns if not a.get("pinned")]

    content_blocks = []
    if pinned_anns:
        content_blocks.append(html.H4("Fijados", className="card-title",
                                       style={"marginBottom": "10px"}))
        content_blocks.extend(_ann_card(a) for a in pinned_anns)
        if regular_anns:
            content_blocks.append(html.Div(className="ecg-divider ecg-divider--spaced"))

    if regular_anns:
        content_blocks.append(html.H4("Recientes", className="card-title",
                                       style={"marginBottom": "10px"}))
        content_blocks.extend(_ann_card(a) for a in regular_anns)

    if not anns:
        content_blocks.append(html.Div(className="inner-card data-empty-state", children=[
            html.P("Tu coach no ha publicado ningún comunicado todavía.", className="empty-state-title"),
            html.P("Cuando tu coach publique un aviso para el equipo de "
                   f"{sport.title() if sport else 'tu deporte'}, aparecerá aquí.",
                   className="text-muted"),
        ]))

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Comunicados del coach"),
            html.P(
                f"Avisos y comunicados de tu coach para el equipo de {sport.title() if sport else 'tu deporte'}.",
                className="text-muted",
            ),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(content_blocks),
    ], className="page-content")


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

    # ── Comunicados del coach (inbox) ─────────────────────────────────────
    athlete_sport = _to_str(session.get("sport")) or ""
    inbox_anns = []
    try:
        inbox_anns = db.list_announcements_for_sport(athlete_sport, limit=15) or []
    except Exception:
        inbox_anns = []

    def _inbox_item(a):
        ts_label = (a.get("created_at") or "")[:16].replace("T", " ")
        p = a.get("pinned")
        coach_nm = a.get("coach_name") or "Coach"
        return html.Div(className="ann-item" + (" ann-item--pinned" if p else ""), children=[
            html.Div(className="ann-item__header", children=[
                html.Span(["📌 ", a.get("title", "")] if p else a.get("title", ""),
                          className="ann-item__title"),
                html.Span(f"{coach_nm} · {ts_label}", className="ann-item__date"),
            ]),
            html.P(a.get("body") or "", className="ann-item__body") if a.get("body") else None,
        ])

    inbox_items = [_inbox_item(a) for a in inbox_anns] if inbox_anns else [
        html.P("Sin comunicados recientes de tu coach.", className="text-muted")
    ]
    inbox_card = html.Div(className="card", style={"marginBottom": "16px"}, children=[
        html.H4("Comunicados de tu coach", className="card-title"),
        html.Div(inbox_items),
    ])

    return html.Div([
        html.Div(className="page-head", children=[
            html.H2("Contacto con mi coach"),
            html.P(helper, className="text-muted"),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # ── Comunicados recientes del coach ──────────────────────────────
        inbox_card,

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


def _athlete_day_decision(score, sport_key, has_ecg=False, days_to_comp=None):
    """
    Convierte la lectura diaria en una decisión accionable para el atleta.
    Solo usa datos ya cargados en la vista, así evitamos consultas extra y cambios de lógica.
    """
    sport_key = (sport_key or "").strip().lower()
    focus = (
        "patadas, distancia y explosividad"
        if sport_key == "taekwondo" else
        "guardia, manos y ritmo de golpeo"
        if sport_key == "boxeo" else
        "técnica, ritmo y control corporal"
    )
    try:
        s = float(score)
    except Exception:
        s = None
    try:
        dtc = int(days_to_comp) if days_to_comp is not None else None
    except Exception:
        dtc = None

    if s is None:
        label = "Primero completa tu check-in"
        detail = "Sin estado del día, la lectura deportiva queda incompleta. Empieza con sensaciones antes de subir intensidad."
        color = "#f0a832"
        actions = [
            "Responder check-in antes de entrenar fuerte.",
            f"Trabajar {focus} a intensidad baja-media mientras completas contexto.",
            "Registrar molestias o fatiga si aparecen.",
        ]
    elif dtc is not None and 0 <= dtc <= 7 and s < 60:
        label = "Modo competencia: protege la carga"
        detail = "La competencia está cerca y el estado no invita a sumar fatiga. Hoy conviene afinar sin inventar estímulos."
        color = "var(--punch)"
        actions = [
            f"Priorizar {focus} con volumen corto.",
            "Evitar cargas nuevas, sparring duro o bloque extra de acondicionamiento.",
            "Confirmar recuperación, peso y molestias con el coach.",
        ]
    elif s >= 80:
        label = "Entrena fuerte, midiendo la respuesta"
        detail = "Buen punto de partida para una sesión exigente si la ejecución se mantiene limpia."
        color = "var(--neon)"
        actions = [
            f"Usar el bloque principal para exigir {focus}.",
            "Comparar sensación inicial contra respuesta final.",
            "Guardar señal o sesión para validar la carga real.",
        ]
    elif s >= 60:
        label = "Entrena con control técnico"
        detail = "Hay base para trabajar bien, pero la calidad de ejecución debe mandar sobre el volumen."
        color = "var(--neon)"
        actions = [
            f"Subir intensidad solo si el foco técnico ({focus}) se mantiene estable.",
            "Usar descansos completos entre rounds o bloques.",
            "Revisar recuperación al terminar antes de añadir trabajo extra.",
        ]
    elif s >= 40:
        label = "Ajusta carga y cuida calidad"
        detail = "Hoy parece mejor ganar precisión que acumular fatiga. Baja el volumen y conserva intención técnica."
        color = "#f0a832"
        actions = [
            f"Trabajar {focus} en bloques cortos.",
            "Evitar castigar errores técnicos con más volumen.",
            "Cerrar con movilidad, respiración o recuperación activa.",
        ]
    else:
        label = "Recuperación activa y aviso al coach"
        detail = "La señal del día es baja. Forzar puede esconder fatiga, molestias o mala recuperación."
        color = "var(--punch)"
        actions = [
            "Avisar al coach antes de entrenar fuerte.",
            "Cambiar sparring o alta intensidad por técnica suave.",
            "Priorizar sueño, hidratación y revisión de molestias.",
        ]

    actions.append(
        "Contrasta la sesión con ECG/IMU al terminar."
        if has_ecg else
        "Si puedes, sube ECG/IMU para completar la lectura objetiva."
    )
    return {"label": label, "detail": detail, "color": color, "actions": actions[:4]}


def _coach_day_decision(athlete_count, checkins, ecg_ready, red_athletes, pending_names, upcoming_comps, sport_key):
    """
    Resume la jornada del coach en una decisión priorizada sin esconder los datos base.
    """
    sport_key = (sport_key or "").strip().lower()
    sport_focus = (
        "distancia, piernas y explosividad"
        if sport_key == "taekwondo" else
        "guardia, manos y ritmo de golpeo"
        if sport_key == "boxeo" else
        "técnica, carga y recuperación"
    )
    red_count = len(red_athletes or [])
    pending_count = max(int(athlete_count or 0) - int(checkins or 0), 0)
    nearest_comp = None
    if upcoming_comps:
        try:
            nearest_comp = min(upcoming_comps, key=lambda c: int(c.get("days", 999)))
        except Exception:
            nearest_comp = upcoming_comps[0]

    if not athlete_count:
        label = "Primero construye la plantilla"
        detail = "Sin atletas vinculados no hay lectura de equipo. El valor aparece cuando puedes comparar readiness, señales y seguimiento."
        color = "#f0a832"
        actions = [
            "Agregar atletas o revisar vinculaciones del coach.",
            "Definir deporte base del grupo.",
            "Pedir el primer check-in antes de la siguiente sesión.",
        ]
    elif red_count:
        names = ", ".join((a.get("name") or "Atleta") for a in (red_athletes or [])[:3])
        label = "Prioridad: revisar atletas en rojo"
        detail = f"{red_count} atleta{'s' if red_count != 1 else ''} acumula bienestar bajo. Antes de planear carga, confirma estado y molestias."
        color = "var(--punch)"
        actions = [
            f"Hablar primero con: {names}.",
            "Separar trabajo técnico suave de alta intensidad.",
            "Registrar decisión de carga para seguimiento.",
        ]
    elif nearest_comp and int(nearest_comp.get("days", 999)) <= 7:
        label = "Semana de competencia: afinar, no cargar"
        detail = "La prioridad es llegar fresco. Mantén intensidad corta, baja volumen y confirma detalles logísticos."
        color = "#f0a832"
        actions = [
            f"Afinar {sport_focus} con bloques breves.",
            "Evitar estímulos nuevos o sparring innecesariamente duro.",
            "Confirmar peso, equipo y recuperación de quienes compiten.",
        ]
    elif int(checkins or 0) == 0 or pending_count > max(1, athlete_count // 2):
        label = "Primero cierra check-ins"
        detail = "Hay demasiada poca información para decidir carga fina. Pide contexto antes de dividir grupos."
        color = "#f0a832"
        actions = [
            f"Pedir check-in a {pending_count} atleta{'s' if pending_count != 1 else ''} pendiente{'s' if pending_count != 1 else ''}.",
            "Abrir la sesión con movilidad y técnica controlada.",
            f"Observar {sport_focus} antes de subir intensidad.",
        ]
    else:
        label = "Sesión viable: divide por readiness"
        detail = "Ya tienes suficiente contexto para separar intensidad, técnica y recuperación sin improvisar."
        color = "var(--neon)"
        actions = [
            "Grupo verde: bloque principal con control de calidad.",
            "Grupo medio: técnica y descansos completos.",
            "Grupo bajo: ajuste de carga o recuperación activa.",
        ]

    if ecg_ready:
        actions.append(f"Usar {ecg_ready} señal{'es' if ecg_ready != 1 else ''} ECG/IMU para confirmar respuesta real.")
    elif athlete_count:
        actions.append("Pedir al menos una señal ECG/IMU para validar la carga del día.")

    return {"label": label, "detail": detail, "color": color, "actions": actions[:4]}


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

    _SPORT_ALIAS = {"boxeo": "box", "tkd": "taekwondo", "boxing": "box"}
    sport_norm = _SPORT_ALIAS.get(sport_norm, sport_norm)
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
    roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]
    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}
    try:
        ecg_bulk = db.get_last_ecg_metrics_bulk(roster_ids) if roster_ids else {}
    except Exception:
        ecg_bulk = {}

    for athlete in roster[:]:
        aid = athlete.get("id")
        athlete_name = athlete.get("name") or "Deportista"
        if aid is None:
            continue

        has_checkin = False
        try:
            qrows = qs_bulk.get(int(aid), [])
            has_checkin = bool(qrows)
            if has_checkin:
                latest_checkins += 1
        except Exception:
            has_checkin = False

        has_ecg = False
        try:
            has_ecg = bool(ecg_bulk.get(int(aid)))
            if has_ecg:
                athletes_with_ecg += 1
        except Exception:
            has_ecg = False

        if (has_checkin or has_ecg) and len(focus_names) < 4:
            focus_names.append(athlete_name)
        if not has_checkin and len(pending_names) < 4:
            pending_names.append(athlete_name)

    # ── Alerta de racha roja (3 días consecutivos < 50) ──────────────────────
    _red_athletes = []
    try:
        coach_sport_filter = (_to_str(session.get("sport")) or "").strip() or None
        _red_athletes = db.get_red_streak_athletes(
            int(coach_id), days=3, threshold=50.0, sport=coach_sport_filter
        ) if hasattr(db, "get_red_streak_athletes") else []
    except Exception:
        _red_athletes = []

    red_streak_alert = None
    if _red_athletes:
        names_str = ", ".join(a.get("name", "Atleta") for a in _red_athletes[:4])
        if len(_red_athletes) > 4:
            names_str += f" +{len(_red_athletes)-4} más"
        red_streak_alert = html.Div(className="red-streak-alert", children=[
            html.Div("🚨", className="red-streak-alert__icon"),
            html.Div(className="red-streak-alert__body", children=[
                html.Div(
                    f"{len(_red_athletes)} atleta{'s' if len(_red_athletes)>1 else ''} con bienestar bajo 3 días seguidos",
                    className="red-streak-alert__title",
                ),
                html.Div(names_str, className="red-streak-alert__names"),
            ]),
        ])

    ref_sport = sport or ((roster[0].get("sport") if roster else "") or "")
    blueprint = _session_blueprint_for_sport(ref_sport, role="coach")
    recommendations = _session_recommendations(blueprint, None, role="coach")
    session_chip = _session_structure_chip(blueprint["modo"])
    focus_preview = ", ".join(focus_names[:3]) if focus_names else "Todavía no hay deportistas priorizados con lectura reciente."
    from questionnaires import norm_sport as _ns_j
    _coach_sport_key = _ns_j(ref_sport)

    # ── Próximas competencias del equipo ─────────────────────────────────────
    _today_date = datetime.utcnow().date()
    _upcoming_comps = []
    for _a in roster:
        _aid = _a.get("id")
        if not _aid:
            continue
        try:
            _nxt = db.get_next_competition(int(_aid))
            if _nxt:
                _ev_date = datetime.strptime(_nxt["event_date"][:10], "%Y-%m-%d").date()
                _days = (_ev_date - _today_date).days
                if 0 <= _days <= 60:
                    _upcoming_comps.append({
                        "name": _a.get("name", "Atleta"),
                        "event": _nxt.get("name", "Competencia"),
                        "date": _nxt["event_date"][:10],
                        "days": _days,
                    })
        except Exception:
            pass
    _upcoming_comps.sort(key=lambda x: x["days"])
    _coach_decision = _coach_day_decision(
        athlete_count,
        latest_checkins,
        athletes_with_ecg,
        _red_athletes,
        pending_names,
        _upcoming_comps,
        _coach_sport_key,
    )


    def _comp_countdown_block():
        if not _upcoming_comps:
            return None
        items = []
        for _c in _upcoming_comps[:5]:
            _d = _c["days"]
            _color = "#e45a5a" if _d <= 7 else ("#f0a832" if _d <= 21 else "var(--neon)")
            items.append(html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "padding": "8px 0",
                       "borderBottom": "1px solid var(--line)"},
                children=[
                    html.Div(children=[
                        html.Div(_c["name"], style={"fontWeight": "600", "fontSize": "13px", "color": "var(--ink)"}),
                        html.Div(_c["event"], style={"fontSize": "12px", "color": "var(--muted)"}),
                    ]),
                    html.Div(
                        f"{_d}d" if _d > 0 else "¡HOY!",
                        style={"fontWeight": "900", "fontSize": "22px", "color": _color, "minWidth": "48px", "textAlign": "right"},
                    ),
                ],
            ))
        return html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("Competencias próximas del equipo", className="card-title"),
            html.P(
                f"{len(_upcoming_comps)} atleta{'s' if len(_upcoming_comps)!=1 else ''} con competencia en los próximos 60 días.",
                className="text-muted",
                style={"marginBottom": "10px"},
            ),
            html.Div(items),
        ])

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
                        html.Span("⌄", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(className="collapsible-card__body", children=body_children),
            ],
        )

    return html.Div(
        [
            red_streak_alert,
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
                                    html.Span(datetime.utcnow().strftime("%d %b %Y").lstrip("0"), className="session-pill session-pill--muted"),
                                ],
                            ),
                            html.H2("Mi jornada"),
                            html.P(
                                (
                                    "Revisa quién llega explosivo y sin molestias de pierna antes de definir la carga de combate de hoy."
                                    if _coach_sport_key == "taekwondo" else
                                    "Revisa quién llega con manos libres y sin carga antes de decidir si hoy toca saco o sombra."
                                    if _coach_sport_key == "boxeo" else
                                    "Aquí ordenas el día del equipo: a quién revisar primero, cómo abrir la sesión y qué confirmar antes de ajustar la carga."
                                ),
                                className="text-muted",
                            ),
                        ],
                    ),
                    html.Div(
                        className="card session-focus-card",
                        children=[
                            html.H4("Decisión de la jornada", className="card-title"),
                            html.Div(
                                _coach_decision["label"],
                                className="kpi-value",
                                style={"fontSize": "22px", "color": _coach_decision["color"]},
                            ),
                            html.P(
                                _coach_decision["detail"],
                                className="text-muted",
                            ),
                            html.Ul(
                                [html.Li(action) for action in _coach_decision["actions"]],
                                className="list-compact",
                            ),
                            html.Div(
                                [
                                    html.Strong("Base: "),
                                    f"{latest_checkins}/{athlete_count} check-ins · {athletes_with_ecg}/{athlete_count} señales · {focus_preview}",
                                ],
                                className="text-muted",
                                style={"fontSize": "12px", "marginTop": "10px"},
                            ),
                        ],
                    ),
                ],
            ),
            _comp_countdown_block(),
            html.Div(
                className="card",
                style={"marginBottom": "16px"},
                children=[
                    html.Div(
                        className="card-title-row",
                        children=[
                            html.H4("Resumen del equipo hoy", className="card-title"),
                            html.Div(
                                style={"display": "flex", "gap": "8px", "alignItems": "center", "flexWrap": "wrap"},
                                children=[
                                    html.Span(
                                        "Claude · CombatIQ",
                                        style={"fontSize": "11px", "color": "var(--neon)",
                                               "fontWeight": "600", "fontFamily": "monospace"},
                                    ),
                                    html.Button(
                                        "Generar resumen IA",
                                        id="btn-sesion-team-ai-note",
                                        n_clicks=0,
                                        className="btn btn-ghost",
                                        style={"fontSize": "11px", "padding": "5px 10px"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                    dcc.Loading(type="dot", color="var(--neon)",
                                children=html.Div(
                                    id="sesion-team-ai-note",
                                    children=html.P(
                                        'Pulsa "Generar resumen IA" cuando quieras abrir la lectura del equipo.',
                                        className="text-muted",
                                        style={"fontSize": "13px"},
                                    ),
                                )),
                ],
            ),
            html.Div(className="ecg-divider ecg-divider--spaced"),
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
                                            dcc.Link(html.Button("Señales ECG / IMU", className="btn btn-ghost"), href="/ecg"),
                                            dcc.Link(html.Button("Abrir comparativa", className="btn btn-ghost"), href="/comparar"),
                                        ],
                                    ),
                                    html.Div(className="ecg-divider", style={"margin": "14px 0"}),
                                    html.H4("Recordatorios de check-in", className="card-title"),
                                    html.P(
                                        f"{len(pending_names)} deportista{'s' if len(pending_names) != 1 else ''} "
                                        f"sin check-in reciente. Solo se notifica a quienes tienen el recordatorio activado.",
                                        className="text-muted",
                                        style={"marginBottom": "10px"},
                                    ),
                                    html.Div(className="btn-save-row", children=[
                                        html.Button(
                                            "Enviar recordatorio a pendientes",
                                            id="btn-send-reminders",
                                            n_clicks=0,
                                            className="btn btn-ghost",
                                            style={"fontSize": "13px", "padding": "6px 16px"},
                                            disabled=len(pending_names) == 0,
                                        ),
                                        html.Div(id="reminder-msg", className="text-muted",
                                                 style={"fontSize": "12px"}),
                                    ]),
                                    html.Div(className="ecg-divider", style={"margin": "14px 0"}),
                                    html.H4("Resumen semanal", className="card-title"),
                                    html.P(
                                        "Envía un email con el estado del equipo esta semana.",
                                        className="text-muted",
                                        style={"marginBottom": "10px"},
                                    ),
                                    html.Div(className="btn-save-row", children=[
                                        html.Button(
                                            "Enviar resumen semanal",
                                            id="btn-weekly-digest",
                                            n_clicks=0,
                                            className="btn btn-ghost",
                                            style={"fontSize": "13px", "padding": "6px 16px"},
                                        ),
                                        html.Div(id="digest-msg", className="text-muted",
                                                 style={"fontSize": "12px"}),
                                    ]),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
        className="page-content session-shell coach-shell",
    )


@app.callback(
    Output("reminder-msg", "children"),
    Input("btn-send-reminders", "n_clicks"),
    prevent_initial_call=True,
)
def send_checkin_reminders(n):
    from dash.exceptions import PreventUpdate
    if not n:
        raise PreventUpdate
    role = _to_str(session.get("role")) or ""
    if role != "coach":
        return "Solo el coach puede enviar recordatorios."
    coach_id = session.get("user_id")
    if not coach_id:
        return "Sesión no válida."

    import notifications as _N
    from datetime import datetime as _dt, timedelta as _td

    if not _N.is_configured():
        return "Configura MAIL_SERVER, MAIL_USER y MAIL_PASS para enviar emails."

    coach_sport = (_to_str(session.get("sport")) or "").strip() or None
    roster = _coach_roster(int(coach_id)) if coach_id else []
    cutoff = (_dt.utcnow() - _td(hours=36)).isoformat()
    roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]
    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}

    sent, skipped = 0, 0
    for athlete in roster:
        aid = athlete.get("id")
        if not aid:
            continue
        prefs = db.get_notification_prefs(int(aid))
        if not prefs.get("checkin_reminder", 0):
            skipped += 1
            continue
        try:
            qrows = qs_bulk.get(int(aid), [])
            if qrows and (qrows[0].get("ts") or "") > cutoff:
                continue  # ya tiene check-in reciente
        except Exception:
            pass
        email = athlete.get("email") or athlete.get("correo")
        if not email or "@" not in email:
            skipped += 1
            continue
        import threading as _thr
        _thr.Thread(
            target=_N.notify_athlete_checkin_reminder,
            args=(email, str(athlete.get("name") or "Deportista"),
                  str(athlete.get("sport") or coach_sport or "")),
            daemon=True,
        ).start()
        sent += 1

    if sent == 0:
        if skipped:
            return f"Ningún atleta tiene el recordatorio activado ({skipped} sin preferencia)."
        return "Todos ya tienen check-in reciente."
    return f"✓ {sent} recordatorio{'s' if sent != 1 else ''} enviado{'s' if sent != 1 else ''}."


@app.callback(
    Output("digest-msg", "children"),
    Input("btn-weekly-digest", "n_clicks"),
    prevent_initial_call=True,
)
def send_weekly_digest(n):
    from dash.exceptions import PreventUpdate
    if not n:
        raise PreventUpdate
    role = _to_str(session.get("role")) or ""
    if role != "coach":
        return "Solo el coach puede enviar el resumen."
    coach_id = session.get("user_id")
    if not coach_id:
        return "Sesión no válida."

    import notifications as _N
    from datetime import datetime as _dt, timedelta as _td

    if not _N.is_configured():
        return "Configura MAIL_SERVER, MAIL_USER y MAIL_PASS para enviar emails."

    try:
        coach = db.get_user_by_id(int(coach_id))
        coach_email = (coach or {}).get("email") or ""
        coach_name  = (coach or {}).get("name") or "Coach"
        coach_sport = (_to_str(session.get("sport")) or "").strip()

        if not coach_email or "@" not in coach_email:
            return "El coach no tiene email registrado."

        roster = db.get_team_weekly_summary(int(coach_id))
        roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]
        try:
            qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
        except Exception:
            qs_bulk = {}

        cutoff_7d = (_dt.utcnow() - _td(days=7)).isoformat()
        active_ids = set()
        checkins_7d = 0
        wellness_scores = []
        for a in roster:
            aid = a.get("id")
            if not aid:
                continue
            try:
                qrows = qs_bulk.get(int(aid), [])
                recent = [q for q in qrows if (q.get("ts") or "") >= cutoff_7d]
                if recent:
                    active_ids.add(aid)
                    checkins_7d += len(recent)
                    wellness_scores.extend(
                        float(q["wellness_score"])
                        for q in recent
                        if q.get("wellness_score") is not None
                    )
            except Exception:
                pass

        avg_wellness = (sum(wellness_scores) / len(wellness_scores)) if wellness_scores else None

        red_athletes = db.get_red_streak_athletes(
            int(coach_id), days=3, threshold=50.0,
            sport=coach_sport or None,
        )

        top_athletes = sorted(
            [
                {"name": a.get("name"), "sport": a.get("sport"),
                 "wellness": a.get("weekly", {}).get("wellness_avg") or 0}
                for a in roster
                if (a.get("weekly") or {}).get("wellness_avg") is not None
            ],
            key=lambda x: x["wellness"],
            reverse=True,
        )

        stats = {
            "total_athletes": len(roster),
            "active_7d":      len(active_ids),
            "checkins_7d":    checkins_7d,
            "avg_wellness":   avg_wellness,
            "red_athletes":   red_athletes,
            "top_athletes":   top_athletes,
            "sport":          coach_sport,
        }

        import threading as _thr
        _thr.Thread(
            target=_N.notify_coach_weekly_digest,
            args=(coach_email, coach_name, stats),
            daemon=True,
        ).start()
        return f"✓ Resumen enviado a {coach_email}."

    except Exception as exc:
        return f"Error al generar resumen: {exc}"


def _checkin_heatmap_fig(uid_int: int):
    """Calendar heatmap de los últimos 13 semanas (estilo GitHub). Retorna figura Plotly."""
    from datetime import date, timedelta
    import plotly.graph_objects as go

    qs = db.list_questionnaires(uid_int) or []
    checkin_map = {}
    for q in qs:
        ts = (q.get("ts") or "")[:10]
        if ts:
            ws = q.get("wellness_score")
            if ts not in checkin_map:
                checkin_map[ts] = ws

    today = date.today()
    # Empieza en el lunes más reciente antes de hace 13 semanas
    start = today - timedelta(weeks=13)
    while start.weekday() != 0:
        start -= timedelta(days=1)

    n_weeks = 14
    day_labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    z      = [[None] * n_weeks for _ in range(7)]
    hover  = [[""] * n_weeks for _ in range(7)]
    x_tick_labels = []

    d = start
    for w in range(n_weeks):
        for dow in range(7):
            if d > today:
                z[dow][w] = None
                hover[dow][w] = ""
            else:
                ds = d.strftime("%Y-%m-%d")
                ws = checkin_map.get(ds)
                z[dow][w] = float(ws) if ws is not None else -5
                label = d.strftime("%d %b")
                hover[dow][w] = f"{label}: {ws:.0f}/100" if ws is not None else f"{label}: sin registro"
            d += timedelta(days=1)
        x_tick_labels.append((start + timedelta(weeks=w)).strftime("%d %b"))

    colorscale = [
        [0.00, "#1a2535"],
        [0.04, "#1a2535"],
        [0.05, "#e45a5a"],
        [0.50, "#f0a832"],
        [1.00, "#2fb7c4"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=list(range(n_weeks)),
        y=day_labels,
        customdata=hover,
        hovertemplate="%{customdata}<extra></extra>",
        colorscale=colorscale,
        zmin=-5,
        zmax=100,
        showscale=False,
        xgap=3,
        ygap=3,
    ))

    fig.update_layout(
        height=160,
        margin=dict(l=32, r=8, t=8, b=28),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#8fa3bf", size=10),
        xaxis=dict(
            tickvals=list(range(0, n_weeks, 2)),
            ticktext=[x_tick_labels[i] for i in range(0, n_weeks, 2)],
            showgrid=False, zeroline=False, side="bottom",
        ),
        yaxis=dict(showgrid=False, zeroline=False, autorange="reversed"),
    )
    return fig


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
    from questionnaires import norm_sport as _nsport
    _sport_key = _nsport(sport)
    _today_str = datetime.utcnow().strftime("%d %b %Y").lstrip("0")

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

        try:
            _rds = db.get_readiness_score(uid_int) if uid_int else None
        except Exception:
            _rds = None
        _athlete_decision = _athlete_day_decision(
            last_wellness_val,
            _sport_key,
            has_ecg=(last_bpm != "Sin registros"),
            days_to_comp=(_rds.get("days_to_comp") if _rds else None),
        )

        # AI note loaded lazily via sesion-ai-note-output callback

        # Banner para atletas nuevos sin ningún registro todavía
        _is_new_athlete = (last_wellness_val is None and last_bpm == "Sin registros")
        _new_athlete_banner = None
        if _is_new_athlete:
            _new_athlete_banner = html.Div(
                className="card",
                style={"marginBottom": "0", "borderLeft": "4px solid var(--neon)"},
                children=[
                    html.H4("Hola, empecemos", className="card-title"),
                    html.P(
                        "Todavía no tienes registros. Estos 3 pasos te ponen en marcha en menos de 5 minutos:",
                        className="text-muted",
                        style={"marginBottom": "14px"},
                    ),
                    html.Div(className="filters-bar filters-bar--3", children=[
                        html.Div(className="filter-item", children=[
                            html.Div("1", className="kpi-value", style={"color": "var(--neon)", "fontSize": "28px"}),
                            html.Div("Check-in de hoy", className="kpi-label"),
                            html.P("Tu punto de partida diario.", className="text-muted"),
                            dcc.Link(html.Button("Responder ahora", className="btn btn-primary",
                                                 style={"marginTop": "8px", "fontSize": "12px"}),
                                     href="/cuestionario"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Div("2", className="kpi-value", style={"color": "var(--neon)", "fontSize": "28px"}),
                            html.Div("Completa tu perfil", className="kpi-label"),
                            html.P("Nivel, categoría y cercanía a competencia.", className="text-muted"),
                            dcc.Link(html.Button("Ir a mi perfil", className="btn btn-ghost",
                                                 style={"marginTop": "8px", "fontSize": "12px"}),
                                     href="/dashboard"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Div("3", className="kpi-value", style={"color": "var(--neon)", "fontSize": "28px"}),
                            html.Div("Sube un ECG o IMU", className="kpi-label"),
                            html.P("Para ver tu recuperación y carga real.", className="text-muted"),
                            dcc.Link(html.Button("Señales ECG / IMU", className="btn btn-ghost",
                                                 style={"marginTop": "8px", "fontSize": "12px"}),
                                     href="/ecg"),
                        ]),
                    ]),
                ],
            )

        # Card "Modo competencia" cuando faltan ≤7 días para la próxima competencia
        _comp_mode_banner = None
        if _rds and _rds.get("days_to_comp") is not None and _rds["days_to_comp"] <= 7:
            _dtc = _rds["days_to_comp"]
            _ev_name = _rds.get("next_event", "tu competencia")
            if _dtc == 0:
                _dtc_txt = "¡Es hoy!"
                _dtc_color = "#e45a5a"
            elif _dtc == 1:
                _dtc_txt = "Mañana"
                _dtc_color = "#e45a5a"
            else:
                _dtc_txt = f"{_dtc} días"
                _dtc_color = "#f0a832"

            _comp_tips = (
                [
                    "Evita cargas nuevas o técnicas no entrenadas — solo refuerza lo que ya dominas.",
                    "Prioriza el sueño y la hidratación sobre cualquier sesión extra de volumen.",
                    "Reduce el RPE al 60-70 % — activa sin fatigar.",
                    "Confirma el peso de combate hoy si hay pesaje.",
                ]
                if _sport_key in ("taekwondo", "boxeo")
                else [
                    "Semana de descarga: baja el volumen pero mantén la intensidad corta.",
                    "Confirma que tienes el equipamiento, los documentos y el transporte listos.",
                    "Duerme al menos 8 horas — la recuperación del sueño acumula efectos en 48 h.",
                ]
            )
            _comp_mode_banner = html.Div(
                className="card",
                style={"borderLeft": f"4px solid {_dtc_color}", "marginBottom": "0"},
                children=[
                    html.Div(
                        style={"display": "flex", "justifyContent": "space-between",
                               "alignItems": "flex-start", "flexWrap": "wrap", "gap": "12px"},
                        children=[
                            html.Div(children=[
                                html.H4("Modo competencia activo", className="card-title",
                                        style={"color": _dtc_color}),
                                html.P(
                                    f"{_ev_name} — {_dtc_txt}",
                                    className="text-muted",
                                    style={"marginBottom": "10px"},
                                ),
                                html.Ul(
                                    [html.Li(t) for t in _comp_tips],
                                    className="list-compact",
                                ),
                            ]),
                            html.Div(
                                _dtc_txt,
                                style={
                                    "fontSize": "52px", "fontWeight": "900",
                                    "color": _dtc_color, "lineHeight": "1",
                                    "minWidth": "80px", "textAlign": "right",
                                },
                            ),
                        ],
                    ),
                ],
            )

        return html.Div(
            [
                _new_athlete_banner,
                _comp_mode_banner,
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
                                        html.Span(_today_str, className="session-pill session-pill--muted"),
                                    ],
                                ),
                                html.H2("Mi sesión"),
                                html.P(
                                    f"{name}, " + (
                                        "ve cómo llegas de piernas y explosividad antes de empezar el round de hoy."
                                        if _sport_key == "taekwondo" else
                                        "ve cómo llegas de manos y guardia antes de subir al saco hoy."
                                        if _sport_key == "boxeo" else
                                        "aquí puedes ver cómo llegas hoy, qué sesión toca y qué te conviene revisar primero."
                                    ),
                                    className="text-muted",
                                ),
                            ],
                        ),
                        html.Div(
                            className="card session-focus-card",
                            children=[
                                html.H4("Decisión del día", className="card-title"),
                                html.Div(
                                    _athlete_decision["label"],
                                    className="kpi-value",
                                    style={"fontSize": "22px", "color": _athlete_decision["color"]},
                                ),
                                html.P(_athlete_decision["detail"], className="text-muted"),
                                html.Ul(
                                    [html.Li(action) for action in _athlete_decision["actions"]],
                                    className="list-compact",
                                ),
                                html.Div(
                                    [
                                        html.Strong("Base: "),
                                        f"{last_wellness_text}{(' | ' + wellness_ts) if wellness_ts else ''} · {last_bpm} · {next_step}",
                                    ],
                                    className="text-muted",
                                    style={"fontSize": "12px", "marginTop": "10px"},
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(className="ecg-divider ecg-divider--spaced"),
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
                html.Div(className="ecg-divider ecg-divider--spaced"),
                *([html.Div(
                    className="card readiness-card",
                    style={"marginBottom": "16px", "borderLeft": f"4px solid {_rds['color']}"},
                    children=[
                        html.Div(
                            style={"display": "flex", "alignItems": "center",
                                   "justifyContent": "space-between", "flexWrap": "wrap",
                                   "gap": "12px"},
                            children=[
                                html.Div(children=[
                                    html.Div("Forma de competición", className="kpi-label"),
                                    html.Div(
                                        _rds["label"],
                                        className="kpi-value",
                                        style={"color": _rds["color"], "fontSize": "22px"},
                                    ),
                                    html.Div(
                                        (f"{_rds['days_to_comp']} días para {_rds['next_event']}"
                                         if _rds.get("days_to_comp") is not None
                                         else "Sin competencia próxima registrada"),
                                        className="kpi-sub",
                                    ),
                                ]),
                                html.Div(
                                    style={"display": "flex", "alignItems": "center", "gap": "8px"},
                                    children=[
                                        html.Div(
                                            str(_rds["score"]),
                                            style={
                                                "fontSize": "48px", "fontWeight": "900",
                                                "color": _rds["color"], "lineHeight": "1",
                                            },
                                        ),
                                        html.Div(
                                            "/ 100",
                                            style={"color": "var(--muted)", "fontSize": "14px",
                                                   "alignSelf": "flex-end", "marginBottom": "6px"},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(className="ecg-divider", style={"margin": "12px 0"}),
                        html.Div(
                            style={"display": "flex", "gap": "20px", "flexWrap": "wrap"},
                            children=[
                                html.Div([
                                    html.Span("Bienestar ", style={"color": "var(--muted)", "fontSize": "12px"}),
                                    html.Span(f"{_rds['breakdown']['wellness_avg_pts']}/40",
                                              style={"fontWeight": "700", "color": _rds["color"], "fontSize": "12px"}),
                                ]),
                                html.Div([
                                    html.Span("Tendencia ", style={"color": "var(--muted)", "fontSize": "12px"}),
                                    html.Span(f"{_rds['breakdown']['wellness_trend_pts']}/20",
                                              style={"fontWeight": "700", "color": _rds["color"], "fontSize": "12px"}),
                                ]),
                                html.Div([
                                    html.Span("Carga ", style={"color": "var(--muted)", "fontSize": "12px"}),
                                    html.Span(f"{_rds['breakdown']['load_pts']}/20",
                                              style={"fontWeight": "700", "color": _rds["color"], "fontSize": "12px"}),
                                ]),
                                html.Div([
                                    html.Span("Timing competencia ", style={"color": "var(--muted)", "fontSize": "12px"}),
                                    html.Span(f"{_rds['breakdown']['comp_timing_pts']}/20",
                                              style={"fontWeight": "700", "color": _rds["color"], "fontSize": "12px"}),
                                ]),
                            ],
                        ),
                    ],
                )] if _rds else []),
                *([html.Div(
                    className="card",
                    style={"marginBottom": "16px"},
                    children=[
                        html.Div(
                            className="card-title-row",
                            children=[
                                html.H4("Tu análisis del día", className="card-title"),
                                html.Div(
                                    style={"display": "flex", "gap": "8px", "alignItems": "center", "flexWrap": "wrap"},
                                    children=[
                                        html.Span(
                                            "Claude · CombatIQ",
                                            style={"fontSize": "11px", "color": "var(--neon)",
                                                   "fontWeight": "600", "fontFamily": "monospace"},
                                        ),
                                        html.Button(
                                            "Generar lectura IA",
                                            id="btn-sesion-ai-note",
                                            n_clicks=0,
                                            className="btn btn-ghost",
                                            style={"fontSize": "11px", "padding": "5px 10px"},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        dcc.Loading(
                            type="dot", color="var(--neon)",
                            children=html.Div(
                                id="sesion-ai-note-output",
                                children=html.P(
                                    'Pulsa "Generar lectura IA" cuando quieras abrir el análisis del día.',
                                    className="text-muted",
                                    style={"fontSize": "13px"},
                                ),
                            ),
                        ),
                    ],
                )]),
                html.Div(
                    className="session-main-grid",
                    children=[
                        html.Div(
                            className="session-stack",
                            children=[
                                html.Details(
                                    className="card collapsible-card",
                                    open=True,
                                    children=[
                                        html.Summary(className="collapsible-card__summary", children=[
                                            html.Div(className="collapsible-card__head", children=[
                                                html.Span("Qué sesión toca", className="card-title"),
                                                html.Span(f"{sport or 'Deporte'} · {session_chip}", className="text-muted"),
                                            ]),
                                            html.Span("⌄", className="collapsible-card__chevron"),
                                        ]),
                                        html.Div(className="collapsible-card__body", children=[
                                            html.Ul(
                                                [
                                                    html.Li([html.Strong("Objetivo principal: "), blueprint["objetivo_principal"]]),
                                                    html.Li([html.Strong("Qué se busca hoy: "), blueprint["objetivo_desc"]]),
                                                    html.Li([html.Strong("Estructura: "), blueprint["estructura"]]),
                                                    html.Li([html.Strong("Qué mirar: "), blueprint["lectura"]]),
                                                ],
                                                className="list-compact",
                                            ),
                                        ]),
                                    ],
                                ),
                                html.Details(
                                    className="card collapsible-card",
                                    open=False,
                                    children=[
                                        html.Summary(className="collapsible-card__summary", children=[
                                            html.Div(className="collapsible-card__head", children=[
                                                html.Span(blueprint["detalle_titulo"], className="card-title"),
                                                html.Span("Detalle de la sesión", className="text-muted"),
                                            ]),
                                            html.Span("⌄", className="collapsible-card__chevron"),
                                        ]),
                                        html.Div(className="collapsible-card__body", children=[
                                            html.Div(blueprint["nota"], className="text-muted"),
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
                                        ]),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            className="session-stack",
                            children=[
                                html.Details(
                                    className="card collapsible-card",
                                    open=True,
                                    children=[
                                        html.Summary(className="collapsible-card__summary", children=[
                                            html.Div(className="collapsible-card__head", children=[
                                                html.Span("Qué te conviene hacer", className="card-title"),
                                                html.Span("Lectura rápida + recomendaciones", className="text-muted"),
                                            ]),
                                            html.Span("⌄", className="collapsible-card__chevron"),
                                        ]),
                                        html.Div(className="collapsible-card__body", children=[
                                            html.Ul(
                                                [
                                                    html.Li(
                                                        "1. Confirma cómo llegas de piernas y explosividad antes de apretar intensidad."
                                                        if _sport_key == "taekwondo" else
                                                        "1. Confirma cómo llegas de manos y guardia antes de apretar intensidad."
                                                        if _sport_key == "boxeo" else
                                                        "1. Confirma cómo llegas hoy antes de apretar intensidad."
                                                    ),
                                                    html.Li("2. Lee la sesión según su estructura: rounds, bloques o trabajo libre."),
                                                    html.Li("3. Después entra a señales e histórico para validar carga y recuperación."),
                                                ],
                                                className="list-compact",
                                            ),
                                            html.Div(className="spacer-10"),
                                            html.Span("Recomendaciones de hoy", className="card-title"),
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
                                                    dcc.Link(html.Button("Señales ECG / IMU", className="btn btn-ghost"), href="/ecg"),
                                                    dcc.Link(html.Button("Historial wellbeing", className="btn btn-ghost"), href="/historico"),
                                                ],
                                            ),
                                        ]),
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
                html.Div(className="ecg-divider ecg-divider--spaced"),
                html.Details(
                    className="card collapsible-card",
                    open=False,
                    children=[
                        html.Summary(className="collapsible-card__summary", children=[
                            html.Div(className="collapsible-card__head", children=[
                                html.Span("Historial de check-ins", className="card-title"),
                                html.Span("Últimas 13 semanas", className="text-muted"),
                            ]),
                            html.Span("⌄", className="collapsible-card__chevron"),
                        ]),
                        html.Div(className="collapsible-card__body", children=[
                            html.P(
                                "Cada celda es un día. Verde = buen bienestar, amarillo = intermedio, rojo = bajo, gris = sin registro.",
                                className="text-muted",
                                style={"marginBottom": "10px"},
                            ),
                            dcc.Graph(
                                figure=_checkin_heatmap_fig(uid_int),
                                config={"displayModeBar": False},
                                style={"borderRadius": "8px"},
                            ),
                        ]),
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
        from questionnaires import norm_sport as _ns
        by_sport = {
            "taekwondo": [
                "Entrada-patada-salida: 3×8 combinaciones por lado.",
                "Patadas de competencia: dollyo, neryo, bandal — series cortas y explosivas.",
                "Condición: 6×30s alta / 60s recuperación activa.",
            ],
            "boxeo": [
                "Sombra 3×3 min — enfoque en pies + guardia.",
                "Saco: 4×2 min (jab–cross–hook, trabaja distancia).",
                "Core: 3×45s planchas laterales + rotaciones.",
            ],
        }
        base = ["Calentamiento 8–10 min (movilidad + activación).","Trabajo técnico 20–30 min.","Vuelta a la calma 5 min + estiramientos."]
        sport_key = _ns((sport or "").strip())
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
                dcc.Link(html.Button("Señales ECG / IMU", className="btn btn-ghost"), href="/ecg"),
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
_NOTE_CORRUPT = {
    "simulaci?n": "simulación",
    "t?cnica":    "técnica",
    "evaluaci?n": "evaluación",
    "recuperaci?n": "recuperación",
    "d?a":        "día",
    "competici?n": "competición",
    "ajuste t?cnico": "ajuste técnico",
}

def _fix_note(text: str) -> str:
    if not text or "?" not in text:
        return text
    low = text.lower()
    for bad, good in _NOTE_CORRUPT.items():
        if bad in low:
            import re
            text = re.sub(re.escape(bad), good, text, flags=re.IGNORECASE)
    return text


def _build_peso_recent_table(rows: list):
    """Last 8 weight entries as a compact inline table."""
    recent = sorted(rows, key=lambda x: x.get("date") or "", reverse=True)[:8]
    if not recent:
        return html.P("Sin registros todavía.", className="text-muted",
                      style={"padding": "8px 0"})
    trs = []
    for r in recent:
        w = r.get("weight")
        t = r.get("target")
        diff = round(w - t, 1) if w is not None and t is not None else None
        diff_cls = ("tc-up" if diff <= 0 else "tc-down") if diff is not None else ""
        diff_str = (f"{diff:+.1f}") if diff is not None else "—"
        trs.append(html.Tr([
            html.Td(r.get("date", "—"), style={"color": "var(--muted)", "fontSize": "12px"}),
            html.Td(f"{w:.1f} kg" if w is not None else "—", style={"fontWeight": "600"}),
            html.Td(f"{t:.1f} kg" if t is not None else "—", style={"color": "var(--muted)"}),
            html.Td(html.Span(diff_str, className=diff_cls)),
            html.Td(_fix_note(r.get("note") or "") or "—", style={"color": "var(--muted)", "fontSize": "12px"}),
        ]))
    return html.Table(className="tbl-compact", children=[
        html.Thead(html.Tr([
            html.Th("Fecha"), html.Th("Peso"), html.Th("Objetivo"),
            html.Th("Diff"), html.Th("Nota"),
        ])),
        html.Tbody(trs),
    ])


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


_TKD_CATEGORIES = ["-54", "-58", "-63", "-68", "-74", "-80", "-87", "+87",
                   "-46", "-49", "-53", "-57", "-62", "-67", "-73", "+73"]
_BOX_CATEGORIES  = ["-46", "-48", "-51", "-54", "-57", "-60", "-63.5", "-67",
                    "-71", "-75", "-80", "-86", "-92", "+92",
                    "-48", "-51", "-54", "-57", "-60", "-63", "-66", "-70", "-75", "-81", "+81"]

_WEIGHT_CATS = {
    "taekwondo": [("-54 kg", -54), ("-58 kg", -58), ("-63 kg", -63), ("-68 kg", -68),
                  ("-74 kg", -74), ("-80 kg", -80), ("-87 kg", -87), ("+87 kg", 88),
                  ("-46 kg", -46), ("-49 kg", -49), ("-53 kg", -53), ("-57 kg", -57),
                  ("-62 kg", -62), ("-67 kg", -67), ("-73 kg", -73), ("+73 kg", 74)],
    "boxeo":     [("-46 kg", -46), ("-48 kg", -48), ("-51 kg", -51), ("-54 kg", -54),
                  ("-57 kg", -57), ("-60 kg", -60), ("-63.5 kg", -63.5), ("-67 kg", -67),
                  ("-71 kg", -71), ("-75 kg", -75), ("-80 kg", -80), ("-86 kg", -86),
                  ("-92 kg", -92), ("+92 kg", 93)],
}


def _peso_alert_card(rows, comp_date_str=None):
    """
    Tarjeta de alertas de peso: ritmo de corte peligroso, fuera de objetivo, sin datos.
    Devuelve None si no hay alertas.
    """
    if not rows or len(rows) < 2:
        return None

    alerts = []

    # ── Ritmo de bajada ────────────────────────────────────────────────────
    rows_s = sorted(rows, key=lambda x: x.get("date") or "")
    recent = [r for r in rows_s if r.get("weight") is not None]
    if len(recent) >= 2:
        try:
            from datetime import date as _ddate
            d1 = _ddate.fromisoformat(recent[-1]["date"])
            d0 = _ddate.fromisoformat(recent[-2]["date"])
            days = max((d1 - d0).days, 1)
            rate = (recent[-2]["weight"] - recent[-1]["weight"]) / days
            if rate > 0.5:
                alerts.append(("danger",
                    f"Bajada de {rate:.2f} kg/día — ritmo demasiado rápido. "
                    "Por encima de 0.5 kg/día aumenta el riesgo de pérdida muscular y bajada de rendimiento."))
            elif rate > 0.3:
                alerts.append(("warn",
                    f"Bajada de {rate:.2f} kg/día — en el límite superior recomendado (0.3 kg/día). "
                    "Vigila sueño, energía y wellness esta semana."))
        except Exception:
            pass

    # ── Proyección a competencia ───────────────────────────────────────────
    if comp_date_str:
        try:
            from datetime import date as _ddate
            comp = _ddate.fromisoformat(comp_date_str)
            today = _ddate.today()
            days_left = (comp - today).days
            last = recent[-1]
            last_w = last.get("weight")
            target = next((r.get("target") for r in reversed(rows_s) if r.get("target") is not None), None)
            if last_w is not None and target is not None and days_left > 0:
                needed = last_w - target
                rate_needed = needed / days_left
                if needed > 0:
                    if rate_needed > 0.5:
                        alerts.append(("danger",
                            f"Competencia en {days_left} días: necesitas bajar {needed:.1f} kg "
                            f"({rate_needed:.2f} kg/día) — ritmo peligroso. "
                            "Habla con tu médico o nutricionista."))
                    elif rate_needed > 0.3:
                        alerts.append(("warn",
                            f"Competencia en {days_left} días: necesitas {needed:.1f} kg más "
                            f"({rate_needed:.2f} kg/día). Es factible pero exige disciplina nutricional."))
                    else:
                        alerts.append(("ok",
                            f"Competencia en {days_left} días: necesitas {needed:.1f} kg "
                            f"({rate_needed:.2f} kg/día). Ritmo manejable — sigue el plan."))
                elif needed <= 0:
                    alerts.append(("ok",
                        f"Ya estás en o por debajo del objetivo para la competencia en {days_left} días."))
        except Exception:
            pass

    if not alerts:
        return None

    _color = {"danger": "var(--punch)", "warn": "#f0a832", "ok": "var(--neon)"}
    _icon  = {"danger": "⚠", "warn": "!", "ok": "✓"}

    return html.Div(
        className="card",
        style={"marginBottom": "16px"},
        children=[
            html.H4("Alertas del plan de peso", className="card-title"),
            html.Div(
                className="peso-alerts-list",
                children=[
                    html.Div(
                        className="peso-alert-item",
                        style={"borderLeftColor": _color.get(lvl, "var(--muted)")},
                        children=[
                            html.Span(_icon.get(lvl, "·"), className="peso-alert-icon",
                                      style={"color": _color.get(lvl)}),
                            html.Span(msg, className="peso-alert-msg"),
                        ],
                    )
                    for lvl, msg in alerts
                ],
            ),
        ],
    )


def _coach_peso_view(uid_int: int, sport: str):
    from datetime import date as _ddate
    roster = _coach_roster(uid_int) if uid_int else []
    sport_key = Q.norm_sport(sport)
    today = _ddate.today()

    rows_by_athlete = []
    alert_count = 0
    total_with_data = 0
    upcoming_weigh_ins = 0

    for athlete in roster:
        aid = athlete.get("id")
        name = athlete.get("name") or "Deportista"
        a_sport = athlete.get("sport") or sport
        if not aid:
            continue
        try:
            w_entry = db.get_latest_weight_entry(int(aid))
        except Exception:
            w_entry = None
        try:
            next_comp = db.get_next_competition(int(aid))
        except Exception:
            next_comp = None

        days_to_comp = None
        comp_name = None
        if next_comp:
            try:
                comp_date = _ddate.fromisoformat(next_comp["event_date"][:10])
                days_to_comp = (comp_date - today).days
                if days_to_comp < 0:
                    days_to_comp = None
                else:
                    comp_name = next_comp.get("name", "Competencia")
            except Exception:
                pass

        current_w = w_entry.get("weight") if w_entry else None
        target_w = w_entry.get("target") if w_entry else None
        last_date = w_entry.get("date", "—") if w_entry else None

        if current_w is not None:
            total_with_data += 1
        if days_to_comp is not None and days_to_comp <= 14:
            upcoming_weigh_ins += 1

        gap = round(current_w - target_w, 1) if (current_w is not None and target_w is not None) else None

        risk = "ok"
        if gap is not None and days_to_comp is not None:
            if gap > 3 and days_to_comp <= 21:
                risk = "danger"
                alert_count += 1
            elif gap > 1.5 and days_to_comp <= 30:
                risk = "warn"
        elif gap is not None and gap > 3:
            risk = "warn"

        rows_by_athlete.append({
            "aid": aid, "name": name, "sport": a_sport,
            "current_w": current_w, "target_w": target_w, "last_date": last_date,
            "gap": gap, "days_to_comp": days_to_comp, "comp_name": comp_name, "risk": risk,
        })

    rows_by_athlete.sort(key=lambda r: ({"danger": 0, "warn": 1, "ok": 2}.get(r["risk"], 2),
                                         r["days_to_comp"] if r["days_to_comp"] is not None else 9999))

    def _athlete_row(r):
        border_color = "#e45a5a" if r["risk"] == "danger" else ("#f0a832" if r["risk"] == "warn" else "var(--line)")
        badge_color  = "#e45a5a" if r["risk"] == "danger" else ("#f0a832" if r["risk"] == "warn" else "#27c98f")
        badge_text   = "Alerta corte" if r["risk"] == "danger" else ("Atención" if r["risk"] == "warn" else "OK")
        w_str   = f"{r['current_w']:.1f} kg" if r["current_w"] is not None else "—"
        t_str   = f"{r['target_w']:.1f} kg"  if r["target_w"]  is not None else "—"
        gap_str = f"{r['gap']:+.1f} kg"       if r["gap"]       is not None else "—"
        gap_color = ("#27c98f" if r["gap"] <= 0 else ("#f0a832" if r["gap"] <= 2 else "#e45a5a")) if r["gap"] is not None else None
        dtc = r["days_to_comp"]
        comp_str = ("¡Hoy!" if dtc == 0 else f"{dtc}d") if dtc is not None else "—"
        comp_color = "#e45a5a" if (dtc is not None and dtc <= 7) else "var(--ink)"
        return html.Div(
            className="card",
            style={"marginBottom": "10px", "borderLeft": f"4px solid {border_color}"},
            children=[html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "flexWrap": "wrap", "gap": "12px"},
                children=[
                    html.Div(children=[
                        html.Div(r["name"], style={"fontWeight": "700", "fontSize": "14px"}),
                        html.Div((r["sport"] or "").title(), className="text-muted", style={"fontSize": "12px"}),
                    ]),
                    html.Div(style={"display": "flex", "gap": "24px", "alignItems": "center", "flexWrap": "wrap"}, children=[
                        html.Div(children=[html.Div("Peso actual", className="kpi-label"),
                                           html.Div(w_str, style={"fontWeight": "700", "fontSize": "15px"}),
                                           html.Div(r.get("last_date") or "—", className="text-muted", style={"fontSize": "11px"})]),
                        html.Div(children=[html.Div("Objetivo", className="kpi-label"),
                                           html.Div(t_str, style={"fontWeight": "700", "fontSize": "15px"})]),
                        html.Div(children=[html.Div("Diferencia", className="kpi-label"),
                                           html.Div(gap_str, style={"fontWeight": "700", "fontSize": "15px",
                                                                     **( {"color": gap_color} if gap_color else {})})]),
                        html.Div(children=[html.Div("Competencia", className="kpi-label"),
                                           html.Div(comp_str, style={"fontWeight": "700", "fontSize": "15px", "color": comp_color}),
                                           html.Div(r.get("comp_name") or "—", className="text-muted",
                                                    style={"fontSize": "11px", "maxWidth": "120px", "overflow": "hidden"})]),
                        html.Div(badge_text, style={"background": badge_color, "color": "#fff",
                                                    "borderRadius": "6px", "padding": "4px 10px",
                                                    "fontSize": "12px", "fontWeight": "700"}),
                    ]),
                    dcc.Link(html.Button("Ver perfil", className="btn btn-ghost",
                                         style={"fontSize": "12px", "padding": "4px 10px"}), href="/usuarios"),
                ],
            )],
        )

    no_data = not any(r["current_w"] is not None for r in rows_by_athlete)
    no_target_count = sum(1 for r in rows_by_athlete if r["target_w"] is None and r["current_w"] is not None)

    if sport_key == "taekwondo":
        coach_tip = "En TKD el corte de peso es una variable táctica. Detecta quién está lejos del objetivo con competencia próxima para ajustar carga y nutrición esta semana."
    elif sport_key == "boxeo":
        coach_tip = "En boxeo el corte mal gestionado destruye velocidad y guardia. Un atleta con >3 kg a cortar en menos de 3 semanas necesita un plan de nutrición revisado hoy."
    else:
        coach_tip = "Revisa quién tiene un objetivo de peso definido y cómo se compara con la competencia más próxima."

    return html.Div([
        html.Div(className="page-head", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport.title() or "Equipo", className="session-pill"),
                html.Span("Control de peso", className="session-pill session-pill--muted"),
            ]),
            html.H2("Plan de peso del equipo"),
            html.P(coach_tip, className="text-muted"),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Con registro", className="kpi-label"),
                html.Div(f"{total_with_data} / {len(roster)}", className="kpi-value"),
                html.Div("Atletas con peso registrado", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("En alerta", className="kpi-label"),
                html.Div(str(alert_count), className="kpi-value",
                         style={"color": "#e45a5a"} if alert_count > 0 else {}),
                html.Div("Corte difícil + comp. próxima", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Pesajes próximos", className="kpi-label"),
                html.Div(str(upcoming_weigh_ins), className="kpi-value",
                         style={"color": "#f0a832"} if upcoming_weigh_ins > 0 else {}),
                html.Div("Competencias en ≤14 días", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Sin objetivo", className="kpi-label"),
                html.Div(str(no_target_count), className="kpi-value"),
                html.Div("Tienen peso pero no meta definida", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(className="card", style={"marginBottom": "16px", "borderLeft": "4px solid var(--neon)"}, children=[
            html.H4("¿Cómo puede ayudar el coach?", className="card-title"),
            html.Ul(className="list-compact", children=[
                html.Li([html.Strong("Detectar atletas en riesgo: "), ">3 kg a bajar con <21 días → plan de nutrición ajustado esta semana."]),
                html.Li([html.Strong("Reducir carga en el corte final: "), "bajar RPE objetivo la última semana para no añadir estrés al déficit calórico."]),
                html.Li([html.Strong("Pedir registros frecuentes: "), "cada 2-3 días es suficiente para tener datos confiables y detectar ritmos peligrosos."]),
            ]),
        ]),
        html.H4("Estado por atleta", className="card-title", style={"marginBottom": "12px"}),
        *(
            [_athlete_row(r) for r in rows_by_athlete]
            if rows_by_athlete else
            [html.Div(className="inner-card data-empty-state", children=[
                html.P("No hay atletas en tu equipo todavía.", className="empty-state-title"),
            ])]
        ),
        *(
            [html.Div(className="inner-card data-empty-state", style={"marginTop": "12px"}, children=[
                html.P("Ningún atleta ha registrado peso todavía.", className="empty-state-title"),
                html.P("Los atletas registran su peso desde 'Plan de peso' en su propia cuenta. Sus datos aparecerán aquí.", className="text-muted"),
            ])] if (no_data and rows_by_athlete) else []
        ),
    ], className="page-content")


def view_peso():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role == "coach":
        uid_int = int(session["user_id"])
        sport = _to_str(session.get("sport") or "")
        return _coach_peso_view(uid_int, sport)
    if role not in ("deportista", "admin"):
        return html.Div("Esta sección está pensada para deportistas.", className="muted")

    sport = _to_str(session.get("sport") or "")
    sport_key = Q.norm_sport(sport)
    today = datetime.utcnow().date().isoformat()

    # ── Copy por deporte ─────────────────────────────────────────────────────
    if sport_key == "taekwondo":
        head_sub = "En taekwondo el pesaje define tu categoría y tu rival. Lleva el control con tiempo para no forzar el corte en los últimos días."
        cat_note  = "Categorías World Taekwondo (hombre y mujer). Usa el peso de competencia como objetivo en cada registro."
    elif sport_key == "boxeo":
        head_sub = "En boxeo el peso es uno de los factores tácticos más importantes. Un corte bien gestionado llega al pesaje sin perder fuerza ni velocidad."
        cat_note  = "Categorías AIBA (amateur) y WBC/WBA (pro) de referencia. Usa el límite de tu categoría como objetivo."
    else:
        head_sub = "Registra tu peso con regularidad para detectar tendencias y mantener el control antes de competencias."
        cat_note  = "Introduce el peso límite de tu categoría como objetivo para ver cuánto te queda."

    cats = _WEIGHT_CATS.get(sport_key, [])
    cat_opts = [{"label": c, "value": abs(v)} for c, v in cats]

    return html.Div([
        dcc.Store(id="peso-store", data={"rev": 0}),
        dcc.Store(id="peso-comp-date-store", data={"comp_date": None}),
        dcc.Store(id="peso-data-store"),
        dcc.Download(id="dl-peso-csv"),

        # ── Hero ─────────────────────────────────────────────────────────────
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport.title() or "Deporte", className="session-pill"),
                    html.Span("Control de peso", className="session-pill session-pill--muted"),
                ]),
                html.H2("Plan de peso"),
                html.P(head_sub, className="text-muted"),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4("Categorías de referencia", className="card-title"),
                html.P(cat_note, className="text-muted", style={"marginBottom": "10px"}),
                html.Div(
                    className="cat-chips",
                    children=[
                        html.Span(c, className="cat-chip") for c, _ in cats[:8]
                    ] if cats else [html.P("—", className="text-muted")],
                ),
            ]),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),

        # ── KPI Strip + Alertas (poblados por callbacks) ─────────────────────
        html.Div(id="peso-kpi-strip", style={"marginBottom": "16px"}),
        html.Div(id="peso-alerts"),

        html.Div(className="panel-grid", style={"marginBottom": "16px"}, children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Evolución del peso", className="card-title"),
                    html.P(
                        "Te ayuda a ver si te acercas a tu objetivo y cómo va el ritmo de bajada.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(id="peso-graph-wrap", children=[
                        html.Div(className="inner-card inner-card--chart", children=[
                            dcc.Graph(id="peso-graph", figure=_uc.placeholder_figure(420), style={"height": "420px"}),
                        ]),
                    ]),
                    html.Div(
                        id="peso-no-data",
                        className="inner-card data-empty-state",
                        style={"display": "none"},
                        children=[
                            html.P("Empieza a controlar tu peso",
                                   style={"fontWeight": "700", "fontSize": "15px", "marginBottom": "12px"}),
                            html.Div(children=[
                                html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "10px", "alignItems": "flex-start"}, children=[
                                    html.Span("1", className="step-circle"),
                                    html.Div([html.Strong("Introduce tu peso actual"), html.Span(" — usa el formulario de la derecha.", className="text-muted")]),
                                ]),
                                html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "10px", "alignItems": "flex-start"}, children=[
                                    html.Span("2", className="step-circle"),
                                    html.Div([html.Strong("Añade tu peso objetivo"), html.Span(" — el límite de tu categoría o tu meta.", className="text-muted")]),
                                ]),
                                html.Div(style={"display": "flex", "gap": "12px", "alignItems": "flex-start"}, children=[
                                    html.Span("3", className="step-circle"),
                                    html.Div([html.Strong("Con 3+ registros"), html.Span(" verás la curva de evolución y la proyección a competencia.", className="text-muted")]),
                                ]),
                            ]),
                        ],
                    ),
                ]),
            ]),
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Registrar peso", className="card-title"),
                    html.P(
                        "Guarda tu peso actual. Si tienes competencia próxima, indica la fecha para ver si vas en tiempo.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(
                        className="filters-bar filters-bar--3",
                        style={"marginBottom": "14px"},
                        children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Fecha del registro"),
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
                                html.Label("Peso objetivo / límite de categoría (kg)"),
                                dcc.Input(
                                    id="peso-objetivo",
                                    type="number", min=0, step=0.1,
                                    placeholder="Ej: 66.0",
                                    style={"width": "100%"},
                                ),
                            ]),
                        ],
                    ),
                    html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "14px"}, children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Fecha de competencia (opcional)"),
                            dcc.DatePickerSingle(
                                id="peso-comp-date",
                                date=None,
                                display_format="YYYY-MM-DD",
                                placeholder="Selecciona fecha...",
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Nota (opcional)"),
                            dcc.Input(
                                id="peso-nota",
                                type="text",
                                placeholder="Ej: Semana de descarga, pesaje oficial...",
                                style={"width": "100%"},
                            ),
                        ]),
                    ]),
                    html.Div(className="btn-save-row", children=[
                        html.Button("Guardar registro", id="btn-save-peso", className="btn btn-primary"),
                        html.Div(id="peso-msg", className="text-danger"),
                    ]),
                ]),
            ]),
        ]),

        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.Div(className="card-title-row", children=[
                html.H4("Últimos registros", className="card-title"),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                    dcc.Dropdown(
                        id="dl-peso-period",
                        options=[
                            {"label": "Última semana",     "value": "7"},
                            {"label": "Último mes",        "value": "30"},
                            {"label": "Últimos 3 meses",   "value": "90"},
                            {"label": "Todo el historial", "value": "0"},
                        ],
                        value="30",
                        clearable=False,
                        style={"width": "155px", "fontSize": "12px"},
                    ),
                    html.Button("↓ Excel", id="btn-dl-peso", className="btn btn-ghost btn-xs"),
                ]),
            ]),
            html.Div(id="peso-recent-table"),
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
    Output("peso-comp-date-store", "data"),
    Input("peso-comp-date", "date"),
    prevent_initial_call=True,
)
def store_comp_date(comp_date):
    return {"comp_date": comp_date}


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
    Output("peso-data-store", "data"),
    Input("peso-store", "data"),
    Input("peso-comp-date-store", "data"),
    prevent_initial_call=False,
)
def load_peso_data(_store, _comp_store):
    # Carga desde DB (persistente)
    comp_date = (_comp_store or {}).get("comp_date") if _comp_store else None

    if not session.get("user_id"):
        return {"status": "unauthenticated", "rows": [], "table_data": [], "comp_date": comp_date}

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

    table_data = [
        {"date": r.get("date"), "weight": r.get("weight"), "target": r.get("target"), "note": r.get("note")}
        for r in (rows or [])
    ]

    return {"status": "ok", "rows": rows or [], "table_data": table_data, "comp_date": comp_date}


@app.callback(
    Output("peso-table", "data"),
    Output("peso-kpi-strip", "children"),
    Output("peso-graph-wrap", "style"),
    Output("peso-no-data", "style"),
    Output("peso-alerts", "children"),
    Output("peso-recent-table", "children"),
    Input("peso-data-store", "data"),
    prevent_initial_call=True,
)
def render_peso_summary(data):
    data = data or {}
    rows = data.get("rows") or []
    table_data = data.get("table_data") or []
    comp_date = data.get("comp_date")
    alert_card = _peso_alert_card(rows, comp_date)
    if not rows:
        return (
            table_data,
            _build_peso_kpis([]),
            _PESO_GRAPH_HIDDEN,
            _PESO_NODATA_VISIBLE,
            alert_card,
            _build_peso_recent_table(rows),
        )
    return (
        table_data,
        _build_peso_kpis(rows),
        _PESO_GRAPH_VISIBLE,
        _PESO_NODATA_HIDDEN,
        alert_card,
        _build_peso_recent_table(rows),
    )


@app.callback(
    Output("peso-graph", "figure"),
    Input("peso-data-store", "data"),
    prevent_initial_call=True,
)
def render_peso_graph(data):
    data = data or {}
    rows = data.get("rows") or []
    comp_date = data.get("comp_date")
    if not rows:
        return go.Figure()

    rows_sorted = sorted(rows, key=lambda x: (x.get("date") or "", x.get("id") or 0))
    rows_chart = [r for r in rows_sorted if r.get("weight") is not None]
    dates   = [r.get("date")   for r in rows_chart]
    weights = [r.get("weight") for r in rows_chart]

    # Valor objetivo único: el target más reciente no-nulo de cualquier fila.
    # Dibujarlo como línea horizontal plana evita gaps cuando entradas
    # individuales no tienen target_kg registrado.
    target_value = next(
        (r.get("target") for r in reversed(rows_sorted) if r.get("target") is not None),
        None,
    )

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=weights,
        mode="lines+markers", name="Peso (kg)",
        line=dict(width=2.8, color=_uc.PS_PALETTE[0]),
        marker=dict(size=6, color=_uc.PS_PALETTE[0], line=dict(color="rgba(255,255,255,0.9)", width=1.4)),
        hovertemplate="%{x}<br>%{y:.1f} kg<extra>Peso</extra>",
        connectgaps=False,
    ))

    if target_value is not None and dates:
        # Extender la línea objetivo hasta comp_date cuando existe proyección,
        # para que coincida con el extremo derecho del gráfico.
        objetivo_x1 = comp_date if comp_date else dates[-1]
        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=dates[0], x1=objetivo_x1,
            y0=target_value, y1=target_value,
            line=dict(dash="dash", color="#94a3b8", width=1.8),
        )
        # Traza fantasma solo para que aparezca en la leyenda
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="lines",
            name=f"Objetivo {target_value:.1f} kg",
            line=dict(width=1.8, dash="dash", color="#94a3b8"),
            showlegend=True,
        ))

    # ── Línea de proyección a competencia ────────────────────────────────
    if comp_date and weights and target_value is not None:
        try:
            from datetime import date as _ddate
            last_date = dates[-1]
            last_w    = weights[-1]
            target    = target_value
            if last_date and last_w is not None:
                fig.add_trace(go.Scatter(
                    x=[last_date, comp_date],
                    y=[last_w, target],
                    mode="lines",
                    name="Proyección",
                    line=dict(width=1.4, dash="dot", color="#27c98f"),
                    hovertemplate="%{x}<br>%{y:.1f} kg<extra>Proyección</extra>",
                ))
                fig.add_vline(
                    x=comp_date,
                    line_dash="dash",
                    line_color="rgba(240,168,50,.6)",
                    annotation_text="Competencia",
                    annotation_position="top right",
                )
        except Exception:
            pass

    _uc.add_last_point_highlight(fig, dates, weights, name="Último peso", color=_uc.PS_PALETTE[0], size=10)
    _uc.apply_chart_style(fig, title="Evolución del peso", x_title="Fecha", y_title="Peso (kg)", height=420)
    fig.update_layout(margin=dict(l=40, r=18, t=52, b=40), transition=dict(duration=0))
    fig.update_xaxes(nticks=min(8, len(dates)) if dates else None)
    fig.update_yaxes(tickformat=".1f")

    return fig



# =======================
# Nutrición (deportista)
# =======================
def _build_nutri_recent_table(rows: list):
    """Last 8 nutrition entries as a compact inline table."""
    recent = sorted(rows, key=lambda x: x.get("date") or "", reverse=True)[:8]
    if not recent:
        return html.P("Sin registros todavía.", className="text-muted",
                      style={"padding": "8px 0"})
    trs = []
    for r in recent:
        adh = r.get("adherence")
        adh_cls = ("tc-up" if adh >= 80 else ("tc-same" if adh >= 60 else "tc-down")) if adh is not None else ""
        kcal  = r.get("kcal")
        water = r.get("water_ml")
        trs.append(html.Tr([
            html.Td(r.get("date", "—"), style={"color": "var(--muted)", "fontSize": "12px"}),
            html.Td(html.Span(f"{adh:.0f}%" if adh is not None else "—", className=adh_cls),
                    style={"fontWeight": "600"}),
            html.Td(f"{kcal:.0f}" if kcal is not None else "—"),
            html.Td(f"{water:.0f} ml" if water is not None else "—"),
            html.Td(_fix_note(r.get("note") or "") or "—", style={"color": "var(--muted)", "fontSize": "12px"}),
        ]))
    return html.Table(className="tbl-compact", children=[
        html.Thead(html.Tr([
            html.Th("Fecha"), html.Th("Adh."), html.Th("Kcal"),
            html.Th("Agua"), html.Th("Nota"),
        ])),
        html.Tbody(trs),
    ])


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
            html.Div(best_str, className="kpi-value", style={"color": "var(--green)"}),
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


# ── Nutrición personalizada — helpers ────────────────────────────────────────

def _nutri_weight_kg(latest_w, weight_category: str | None) -> float | None:
    """Peso real (báscula) o parsea el límite de la categoría."""
    if latest_w and latest_w.get("weight_kg"):
        return float(latest_w["weight_kg"])
    if weight_category:
        import re as _re
        m = _re.search(r"\d+(?:\.\d+)?", str(weight_category))
        if m:
            return float(m.group())
    return None


def _nutri_days_to_comp(next_comp) -> int | None:
    if not next_comp:
        return None
    try:
        ev = datetime.strptime(str(next_comp.get("event_date", ""))[:10], "%Y-%m-%d").date()
        d = (ev - datetime.utcnow().date()).days
        return max(0, d)
    except Exception:
        return None


_RECIPES = {
    "taekwondo": [
        {
            "title": "Arroz pre-entrenamiento",
            "context": "2 h antes de entrenar",
            "badge_color": "var(--neon)",
            "ingredients": [
                "150 g arroz blanco cocido",
                "100 g pechuga de pollo grillada",
                "1 plátano maduro",
                "Aceite de oliva extra virgen + sal",
            ],
            "note": "Carbos de rápida absorción + proteína ligera. Sin fibra excesiva para no pesarte.",
        },
        {
            "title": "Bowl de recuperación TKD",
            "context": "30 min post-entrenamiento",
            "badge_color": "var(--green)",
            "ingredients": [
                "80 g avena en copos",
                "150 g yogur griego natural",
                "1 cucharada de miel",
                "Frutos rojos o arándanos",
                "25 g proteína en polvo (opcional)",
            ],
            "note": "Ventana anabólica: carbos + proteína para reparar fibras y reponer glucógeno.",
        },
        {
            "title": "Desayuno de competencia",
            "context": "3 h antes del pesaje",
            "badge_color": "var(--amber)",
            "ingredients": [
                "2 tostadas de pan integral",
                "2 huevos revueltos con aceite de oliva",
                "1 naranja entera",
                "Agua con pizca de sal y limón",
            ],
            "note": "Ligero, digerible y sin residuo intestinal. Nada nuevo en día de competencia.",
        },
    ],
    "boxeo": [
        {
            "title": "Plato de guardia (pre-sparring)",
            "context": "2-3 h antes de sparring",
            "badge_color": "var(--neon)",
            "ingredients": [
                "120 g pasta integral cocida",
                "100 g atún al natural escurrido",
                "Tomate cherry + pepino",
                "Aceite de oliva + limón",
            ],
            "note": "Recarga de glucógeno para mantener velocidad de manos y agilidad en los últimos rounds.",
        },
        {
            "title": "Batido de recuperación Box",
            "context": "Inmediatamente post-entrenamiento",
            "badge_color": "var(--green)",
            "ingredients": [
                "250 ml leche semidesnatada o vegetal",
                "50 g avena",
                "1 plátano",
                "1 cucharada cacao puro en polvo",
                "25 g proteína en polvo (opcional)",
            ],
            "note": "Proteína + carbos en proporción 1:3 para acelerar recuperación y reducir DOMS.",
        },
        {
            "title": "Breakfast del púgil",
            "context": "Día de pesaje o pelea",
            "badge_color": "var(--amber)",
            "ingredients": [
                "3 huevos a la plancha",
                "1 tortilla de maíz integral",
                "100 g batata o camote cocida",
                "Zumo de naranja natural (200 ml)",
            ],
            "note": "Sin grasas pesadas. Proteína completa + carbos de calidad. Hidratar con electrolitos.",
        },
    ],
}
_RECIPES["box"] = _RECIPES["boxeo"]


def _build_nutri_plan_card(weight_kg: float | None, sport_key: str,
                           days_to_comp: int | None, weight_source: str) -> html.Div:
    recipes = _RECIPES.get(sport_key, _RECIPES.get("taekwondo"))

    # ── Si no hay peso, muestra cards genérico + recetas ────────────────────
    if not weight_kg:
        weight_header = html.P(
            "Añade tu peso actual en el panel de inicio para ver tus macros personalizados.",
            className="text-muted", style={"fontSize": "12px", "marginBottom": "12px"},
        )
        macro_chips = []
    else:
        # ── Cálculo de macros ──────────────────────────────────────────────
        prot_lo = round(weight_kg * 1.6)
        prot_hi = round(weight_kg * 2.0)
        carb_lo = round(weight_kg * 4)
        carb_hi = round(weight_kg * 6)
        fat_lo  = round(weight_kg * 0.9)
        fat_hi  = round(weight_kg * 1.2)
        water   = round(weight_kg * 35)

        # Ajuste si hay competencia próxima
        comp_note = None
        if days_to_comp is not None and days_to_comp <= 7:
            comp_note = (f"⚠️ Competencia en {days_to_comp} día{'s' if days_to_comp != 1 else ''}: "
                         "reduce carbos a 3–4 g/kg, sin fibra el día del pesaje y recarga 24 h antes.")
            water_note = f"{water}–{round(weight_kg*40)} ml/día (más si hay sauna)"
        elif days_to_comp is not None and days_to_comp <= 21:
            comp_note = (f"📅 Competencia en {days_to_comp} días: mantén proteína alta, "
                         "empieza a practicar tu protocolo de pesaje esta semana.")
            water_note = f"{water} ml/día mínimo"
        else:
            water_note = f"{water} ml/día mínimo"

        src_label = "peso registrado" if weight_source == "scale" else "categoría de peso"
        weight_header = html.Div([
            html.Span(f"Calculado para {weight_kg:.0f} kg",
                      style={"fontSize": "11px", "background": "rgba(13,148,136,.12)",
                             "color": "var(--neon)", "borderRadius": "20px",
                             "padding": "2px 10px", "fontWeight": "700"}),
            html.Span(f" · {src_label}",
                      style={"fontSize": "11px", "color": "var(--muted)"}),
        ], style={"marginBottom": "12px"})

        macro_chips = [
            html.Div(className="nutri-macro-chip", children=[
                html.Span("Proteína", className="nutri-chip-label"),
                html.Span(f"{prot_lo}–{prot_hi} g/día", className="nutri-chip-value"),
                html.Span("1.6–2.0 g/kg", className="nutri-chip-sub"),
            ]),
            html.Div(className="nutri-macro-chip nutri-macro-chip--carb", children=[
                html.Span("Carbohidratos", className="nutri-chip-label"),
                html.Span(f"{carb_lo}–{carb_hi} g/día", className="nutri-chip-value"),
                html.Span("días de entreno", className="nutri-chip-sub"),
            ]),
            html.Div(className="nutri-macro-chip nutri-macro-chip--fat", children=[
                html.Span("Grasas", className="nutri-chip-label"),
                html.Span(f"{fat_lo}–{fat_hi} g/día", className="nutri-chip-value"),
                html.Span("0.9–1.2 g/kg", className="nutri-chip-sub"),
            ]),
            html.Div(className="nutri-macro-chip nutri-macro-chip--water", children=[
                html.Span("Agua", className="nutri-chip-label"),
                html.Span(water_note, className="nutri-chip-value"),
                html.Span("35 ml/kg base", className="nutri-chip-sub"),
            ]),
        ]

    # ── Recetas ─────────────────────────────────────────────────────────────
    recipe_cards = []
    for r in recipes:
        recipe_cards.append(
            html.Div(className="nutri-recipe-card", children=[
                html.Div(className="nutri-recipe-header", children=[
                    html.Span(r["title"], className="nutri-recipe-title"),
                    html.Span(r["context"],
                              style={"fontSize": "10px", "background": "rgba(13,148,136,.10)",
                                     "color": r["badge_color"], "borderRadius": "20px",
                                     "padding": "2px 8px", "fontWeight": "600",
                                     "whiteSpace": "nowrap"}),
                ]),
                html.Ul([html.Li(ing) for ing in r["ingredients"]],
                        className="nutri-recipe-list"),
                html.P(r["note"], className="nutri-recipe-note"),
            ])
        )

    comp_banner = []
    if weight_kg and comp_note:
        comp_banner = [html.Div(comp_note, className="nutri-comp-banner")]

    return html.Div(className="card profile-focus-card", children=[
        html.H4("Tu plan nutricional", className="card-title"),
        weight_header,
        *comp_banner,
        html.Div(macro_chips, className="nutri-macro-grid") if macro_chips else html.Div(),
        html.Details(style={"marginTop": "14px"}, children=[
            html.Summary("Ver recetas para tu deporte",
                         style={"fontSize": "12px", "fontWeight": "600",
                                "cursor": "pointer", "color": "var(--neon)",
                                "marginBottom": "8px"}),
            html.Div(recipe_cards, className="nutri-recipes-list"),
        ]),
    ])


# ── Nutrición view ────────────────────────────────────────────────────────────

def _coach_nutri_view(uid_int: int, sport: str):
    from datetime import date as _ddate, timedelta as _td
    roster = _coach_roster(uid_int) if uid_int else []
    sport_key = Q.norm_sport(sport)
    today = _ddate.today()
    week_ago = today - _td(days=7)

    rows_by_athlete = []
    alert_count = 0
    total_with_data = 0
    team_adh_vals: list = []

    for athlete in roster:
        aid = athlete.get("id")
        name = athlete.get("name") or "Deportista"
        a_sport = athlete.get("sport") or sport
        if not aid:
            continue
        try:
            nutri_rows = db.list_nutrition_entries(int(aid), limit=14) or []
        except Exception:
            nutri_rows = []
        try:
            next_comp = db.get_next_competition(int(aid))
        except Exception:
            next_comp = None

        days_to_comp = None
        comp_name = None
        if next_comp:
            try:
                comp_date = _ddate.fromisoformat(next_comp["event_date"][:10])
                d = (comp_date - today).days
                if d >= 0:
                    days_to_comp = d
                    comp_name = next_comp.get("name", "Competencia")
            except Exception:
                pass

        last7 = [r for r in nutri_rows if r.get("date") and r["date"] >= week_ago.isoformat()]
        adh_vals = [r.get("adherence") for r in last7 if r.get("adherence") is not None]
        adh_avg  = sum(adh_vals) / len(adh_vals) if adh_vals else None
        kcal_vals = [r.get("kcal") for r in nutri_rows if r.get("kcal") is not None]
        kcal_avg  = sum(kcal_vals) / len(kcal_vals) if kcal_vals else None
        water_vals = [r.get("water_ml") for r in nutri_rows if r.get("water_ml") is not None]
        water_avg  = sum(water_vals) / len(water_vals) if water_vals else None
        logs_this_week = len(last7)

        if nutri_rows:
            total_with_data += 1
            if adh_avg is not None:
                team_adh_vals.append(adh_avg)

        risk = "ok"
        if adh_avg is not None and adh_avg < 70 and days_to_comp is not None and days_to_comp <= 21:
            risk = "danger"
            alert_count += 1
        elif adh_avg is not None and adh_avg < 70:
            risk = "warn"
        elif logs_this_week == 0 and days_to_comp is not None and days_to_comp <= 14:
            risk = "warn"

        rows_by_athlete.append({
            "aid": aid, "name": name, "sport": a_sport,
            "adh_avg": adh_avg, "kcal_avg": kcal_avg, "water_avg": water_avg,
            "logs_this_week": logs_this_week,
            "days_to_comp": days_to_comp, "comp_name": comp_name, "risk": risk,
        })

    rows_by_athlete.sort(key=lambda r: ({"danger": 0, "warn": 1, "ok": 2}.get(r["risk"], 2),
                                         r["days_to_comp"] if r["days_to_comp"] is not None else 9999))

    def _athlete_row(r):
        border_color = "#e45a5a" if r["risk"] == "danger" else ("#f0a832" if r["risk"] == "warn" else "var(--line)")
        badge_color  = "#e45a5a" if r["risk"] == "danger" else ("#f0a832" if r["risk"] == "warn" else "#27c98f")
        badge_text   = "Riesgo nutricional" if r["risk"] == "danger" else ("Atención" if r["risk"] == "warn" else "OK")
        adh_str   = f"{r['adh_avg']:.0f}%"   if r["adh_avg"]   is not None else "—"
        adh_color = ("#27c98f" if r["adh_avg"] >= 80 else ("#f0a832" if r["adh_avg"] >= 60 else "#e45a5a")) if r["adh_avg"] is not None else None
        kcal_str  = f"{r['kcal_avg']:.0f}"   if r["kcal_avg"]  is not None else "—"
        water_str = f"{r['water_avg']/1000:.1f} L" if r["water_avg"] is not None else "—"
        dtc = r["days_to_comp"]
        comp_str  = ("¡Hoy!" if dtc == 0 else f"{dtc}d") if dtc is not None else "—"
        comp_color = "#e45a5a" if (dtc is not None and dtc <= 7) else "var(--ink)"
        return html.Div(
            className="card",
            style={"marginBottom": "10px", "borderLeft": f"4px solid {border_color}"},
            children=[html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "flexWrap": "wrap", "gap": "12px"},
                children=[
                    html.Div(children=[
                        html.Div(r["name"], style={"fontWeight": "700", "fontSize": "14px"}),
                        html.Div((r["sport"] or "").title(), className="text-muted", style={"fontSize": "12px"}),
                    ]),
                    html.Div(style={"display": "flex", "gap": "20px", "alignItems": "center", "flexWrap": "wrap"}, children=[
                        html.Div(children=[html.Div("Adherencia 7d", className="kpi-label"),
                                           html.Div(adh_str, style={"fontWeight": "700", "fontSize": "15px",
                                                                     **( {"color": adh_color} if adh_color else {})})]),
                        html.Div(children=[html.Div("Kcal media", className="kpi-label"),
                                           html.Div(kcal_str, style={"fontWeight": "700", "fontSize": "15px"})]),
                        html.Div(children=[html.Div("Hidratación", className="kpi-label"),
                                           html.Div(water_str, style={"fontWeight": "700", "fontSize": "15px"})]),
                        html.Div(children=[html.Div("Registros semana", className="kpi-label"),
                                           html.Div(str(r["logs_this_week"]), style={"fontWeight": "700", "fontSize": "15px"})]),
                        html.Div(children=[html.Div("Comp. próxima", className="kpi-label"),
                                           html.Div(comp_str, style={"fontWeight": "700", "fontSize": "15px", "color": comp_color}),
                                           html.Div(r.get("comp_name") or "—", className="text-muted", style={"fontSize": "11px"})]),
                        html.Div(badge_text, style={"background": badge_color, "color": "#fff",
                                                    "borderRadius": "6px", "padding": "4px 10px",
                                                    "fontSize": "12px", "fontWeight": "700"}),
                    ]),
                    dcc.Link(html.Button("Ver perfil", className="btn btn-ghost",
                                         style={"fontSize": "12px", "padding": "4px 10px"}), href="/usuarios"),
                ],
            )],
        )

    team_adh_avg   = sum(team_adh_vals) / len(team_adh_vals) if team_adh_vals else None
    team_adh_str   = f"{team_adh_avg:.0f} %" if team_adh_avg is not None else "—"
    team_adh_color = ("#27c98f" if team_adh_avg >= 80 else ("#f0a832" if team_adh_avg >= 60 else "#e45a5a")) if team_adh_avg is not None else None
    no_data = total_with_data == 0
    no_logs_count = sum(1 for r in rows_by_athlete if r["logs_this_week"] == 0)

    if sport_key == "taekwondo":
        coach_tip = "La nutrición en TKD sostiene la explosividad y el corte de peso. Un déficit mal gestionado se nota en el primer round de competencia."
    elif sport_key == "boxeo":
        coach_tip = "En boxeo la energía en los últimos rounds depende de lo que el atleta comió las 48 h previas. Baja adherencia con competencia próxima es señal de alerta."
    else:
        coach_tip = "Revisa la adherencia nutricional del equipo, especialmente de los atletas con competencia próxima."

    return html.Div([
        html.Div(className="page-head", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport.title() or "Equipo", className="session-pill"),
                html.Span("Nutrición", className="session-pill session-pill--muted"),
            ]),
            html.H2("Nutrición del equipo"),
            html.P(coach_tip, className="text-muted"),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Con registros", className="kpi-label"),
                html.Div(f"{total_with_data} / {len(roster)}", className="kpi-value"),
                html.Div("Atletas con historial de nutrición", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Adherencia media", className="kpi-label"),
                html.Div(team_adh_str, className="kpi-value",
                         style={"color": team_adh_color} if team_adh_color else {}),
                html.Div("Últimos 7 días del equipo", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("En alerta", className="kpi-label"),
                html.Div(str(alert_count), className="kpi-value",
                         style={"color": "#e45a5a"} if alert_count > 0 else {}),
                html.Div("Baja adherencia + comp. próxima", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Sin datos esta semana", className="kpi-label"),
                html.Div(str(no_logs_count), className="kpi-value"),
                html.Div("No han registrado en 7 días", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(className="card", style={"marginBottom": "16px", "borderLeft": "4px solid var(--neon)"}, children=[
            html.H4("¿Cómo puede ayudar el coach?", className="card-title"),
            html.Ul(className="list-compact", children=[
                html.Li([html.Strong("Antes de sesiones exigentes: "), "si la adherencia del equipo es <70%, considera bajar la intensidad planificada."]),
                html.Li([html.Strong("Semana de competencia: "), "verifica que atletas en semana de descarga tengan adherencia >80% y agua registrada."]),
                html.Li([html.Strong("Sin datos ≠ sin plan: "), "un atleta que no registra puede no estar siguiendo ningún plan — conversación directa."]),
            ]),
        ]),
        # ── Sección de validación ─────────────────────────────────────────────
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Validar dieta de un atleta", className="card-title"),
            html.P(
                "Selecciona un atleta, revisa sus últimos registros y deja tu validación semanal.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            dcc.Dropdown(
                id="nutri-coach-athlete-select",
                options=[{"label": a.get("name") or "Atleta", "value": a.get("id")}
                         for a in roster if a.get("id") is not None],
                placeholder="Selecciona un atleta…",
                style={"marginBottom": "12px"},
            ),
            html.Div(id="nutri-coach-detail-panel"),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.H4("Estado nutricional por atleta", className="card-title", style={"marginBottom": "12px"}),
        *(
            [_athlete_row(r) for r in rows_by_athlete]
            if rows_by_athlete else
            [html.Div(className="inner-card data-empty-state", children=[
                html.P("No hay atletas en tu equipo todavía.", className="empty-state-title"),
            ])]
        ),
        *(
            [html.Div(className="inner-card data-empty-state", style={"marginTop": "12px"}, children=[
                html.P("Ningún atleta ha registrado datos de nutrición todavía.", className="empty-state-title"),
                html.P("Los atletas registran desde 'Nutrición' en su propia cuenta. Sus datos aparecerán aquí.", className="text-muted"),
            ])] if (no_data and rows_by_athlete) else []
        ),
    ], className="page-content")


def view_nutricion():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role == "coach":
        uid_int = int(session["user_id"])
        sport = _to_str(session.get("sport") or "")
        return _coach_nutri_view(uid_int, sport)
    if role not in ("deportista", "admin"):
        return html.Div("Esta sección está pensada para deportistas.", className="muted")

    uid       = int(session.get("user_id"))
    sport     = _to_str(session.get("sport") or "")
    sport_key = Q.norm_sport(sport)
    today     = datetime.utcnow().date().isoformat()

    # ── Perfil + peso + competencia ──────────────────────────────────────────
    ap          = db.get_athlete_profile(uid) if hasattr(db, "get_athlete_profile") else {}
    latest_w    = db.get_latest_weight_entry(uid) if hasattr(db, "get_latest_weight_entry") else None
    next_comp   = db.get_next_competition(uid) if hasattr(db, "get_next_competition") else None
    days_to_comp = _nutri_days_to_comp(next_comp)
    weight_kg   = _nutri_weight_kg(latest_w, ap.get("weight_category"))
    w_source    = "scale" if (latest_w and latest_w.get("weight_kg")) else "category"

    # ── Feedback del coach ───────────────────────────────────────────────────
    _coach_feedback = None
    try:
        _coach_feedback = db.get_latest_nutrition_feedback(uid)
    except Exception:
        pass

    def _coach_feedback_card(fb):
        if not fb:
            return None
        from datetime import date as _ddate, timedelta as _td
        try:
            week_start = _ddate.fromisoformat(fb["week_start"])
            week_end   = week_start + _td(days=6)
            week_label = f"Semana del {week_start.strftime('%d %b')} al {week_end.strftime('%d %b %Y')}"
        except Exception:
            week_label = fb.get("week_start", "")
        validated_ts = (fb.get("validated_at") or "")[:10]
        coach_name   = fb.get("coach_name") or "Tu coach"
        note         = fb.get("note") or ""
        try:
            days_ago = (_ddate.today() - _ddate.fromisoformat(fb["week_start"])).days
            is_recent = days_ago <= 14
        except Exception:
            is_recent = True
        border = "4px solid #27c98f" if is_recent else "4px solid var(--line)"
        return html.Div(
            className="card",
            style={"marginBottom": "16px", "borderLeft": border},
            children=[
                html.Div(
                    style={"display": "flex", "alignItems": "center",
                           "gap": "10px", "marginBottom": "8px"},
                    children=[
                        html.Span("✓", style={"color": "#27c98f", "fontWeight": "900",
                                               "fontSize": "18px"}),
                        html.Div(children=[
                            html.Strong(f"Dieta validada por {coach_name}",
                                        style={"fontSize": "14px"}),
                            html.Div(f"{week_label} · {validated_ts}",
                                     className="text-muted", style={"fontSize": "12px"}),
                        ]),
                    ],
                ),
                *(
                    [html.P(f'"{note}"',
                             style={"fontSize": "14px", "fontStyle": "italic",
                                    "color": "var(--ink)", "margin": "0"})]
                    if note else []
                ),
            ],
        )

    _feedback_card = _coach_feedback_card(_coach_feedback)

    # ── Copy por deporte ─────────────────────────────────────────────────────
    if sport_key == "taekwondo":
        head_sub = "En taekwondo la nutrición sostiene la explosividad y el control de peso. Un déficit mal gestionado se nota en el primer round."
        water_placeholder = "Ej: 2500 (ml)"
    elif sport_key == "boxeo":
        head_sub = "En boxeo la energía sostenida en los rounds depende de lo que comes las 48 h anteriores. No hay atajos."
        water_placeholder = "Ej: 3000 (ml)"
    else:
        head_sub = "Registra tu adherencia, macros y agua para detectar si la nutrición está afectando tu rendimiento."
        water_placeholder = "Ej: 2000 (ml)"

    return html.Div([
        dcc.Store(id="nutri-store", data={"rev": 0}),
        dcc.Store(id="nutri-data-store"),
        dcc.Download(id="dl-nutri-csv"),

        # ── Hero ─────────────────────────────────────────────────────────────
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport.title() or "Deporte", className="session-pill"),
                    html.Span("Nutrición", className="session-pill session-pill--muted"),
                ]),
                html.H2("Control nutricional"),
                html.P(head_sub, className="text-muted"),
            ]),
            _build_nutri_plan_card(weight_kg, sport_key, days_to_comp, w_source),
        ]),
        html.Div(className="ecg-divider ecg-divider--spaced"),
        *([_feedback_card] if _feedback_card else []),

        # ── KPI Strip ────────────────────────────────────────────────────────
        html.Div(id="nutri-kpi-strip", style={"marginBottom": "16px"}),

        # ── Insight wellness-nutrición (callback) ─────────────────────────────
        html.Div(id="nutri-insight"),

        html.Div(className="panel-grid", style={"marginBottom": "16px"}, children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Adherencia y energía", className="card-title"),
                    html.P(
                        "Adherencia al plan y kcal diarias. Compáralos con tus días de bienestar alto y bajo.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    html.Div(id="nutri-graph-wrap", children=[
                        html.Div(className="inner-card inner-card--chart", children=[
                            dcc.Graph(id="nutri-graph", figure=_uc.placeholder_figure(420), style={"height": "420px"}),
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
                    html.H4("Registrar hoy", className="card-title"),
                    html.P(
                        "Rellena los campos que tengas. Con adherencia y agua ya tienes lo mínimo útil.",
                        className="text-muted",
                        style={"marginBottom": "12px"},
                    ),
                    # Fecha + adherencia
                    html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "14px"}, children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Fecha"),
                            dcc.DatePickerSingle(id="nutri-date", date=today, display_format="YYYY-MM-DD"),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Kcal totales (opcional)"),
                            dcc.Input(id="nutri-kcal", type="number", min=0, step=10,
                                      placeholder="Ej: 2400", style={"width": "100%"}),
                        ]),
                    ]),
                    # Macros
                    html.Div(className="filters-bar filters-bar--3", style={"marginBottom": "14px"}, children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Proteína (g)"),
                            dcc.Input(id="nutri-protein", type="number", min=0, step=1,
                                      placeholder="Ej: 160", style={"width": "100%"}),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Carbohidratos (g)"),
                            dcc.Input(id="nutri-carbs", type="number", min=0, step=1,
                                      placeholder="Ej: 300", style={"width": "100%"}),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Grasas (g)"),
                            dcc.Input(id="nutri-fats", type="number", min=0, step=1,
                                      placeholder="Ej: 70", style={"width": "100%"}),
                        ]),
                    ]),
                    # Agua + adherencia slider
                    html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "14px"}, children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Agua (ml)"),
                            dcc.Input(id="nutri-water", type="number", min=0, step=100,
                                      placeholder=water_placeholder, style={"width": "100%"}),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Comentario (opcional)"),
                            dcc.Input(id="nutri-nota", type="text",
                                      placeholder="Ej: Día de sauna, mucha hambre...",
                                      style={"width": "100%"}),
                        ]),
                    ]),
                    html.Div(className="filter-item", style={"marginBottom": "18px"}, children=[
                        html.Label("Adherencia al plan (%)"),
                        html.P("¿Qué tan bien seguiste tu plan de alimentación hoy?",
                               className="text-muted", style={"fontSize": "12px", "marginBottom": "8px"}),
                        dcc.Slider(id="nutri-adherencia", min=0, max=100, step=5, value=80,
                                   marks={0: "0%", 25: "25%", 50: "50%", 75: "75%", 100: "100%"},
                                   tooltip={"placement": "bottom", "always_visible": False}),
                    ]),
                    html.Div(className="btn-save-row", children=[
                        html.Button("Guardar registro", id="btn-save-nutri", className="btn btn-primary"),
                        html.Div(id="nutri-msg", className="text-danger"),
                    ]),
                ]),
            ]),
        ]),

        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.Div(className="card-title-row", children=[
                html.H4("Últimos registros", className="card-title"),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                    dcc.Dropdown(
                        id="dl-nutri-period",
                        options=[
                            {"label": "Última semana",     "value": "7"},
                            {"label": "Último mes",        "value": "30"},
                            {"label": "Últimos 3 meses",   "value": "90"},
                            {"label": "Todo el historial", "value": "0"},
                        ],
                        value="30",
                        clearable=False,
                        style={"width": "155px", "fontSize": "12px"},
                    ),
                    html.Button("↓ Excel", id="btn-dl-nutri", className="btn btn-ghost btn-xs"),
                ]),
            ]),
            html.Div(id="nutri-recent-table"),
        ]),

        html.Details(
            className="card collapsible-card",
            children=[
                html.Summary(className="collapsible-card__summary", children=[
                    html.Div([
                        html.H4("Historial de nutrición", className="card-title"),
                        html.P("Ábrelo cuando quieras revisar registros anteriores.", className="text-muted"),
                    ], className="collapsible-card__head"),
                    html.Span("›", className="collapsible-card__chevron"),
                ]),
                html.Div(className="collapsible-card__body", children=[
                    html.Div(className="dt-pro", children=[
                        DataTable(
                            id="nutri-table",
                            columns=[
                                {"name": "Fecha",         "id": "date"},
                                {"name": "Adherencia (%)", "id": "adherence"},
                                {"name": "Kcal",           "id": "kcal"},
                                {"name": "Proteína (g)",   "id": "protein_g"},
                                {"name": "Carbs (g)",      "id": "carbs_g"},
                                {"name": "Grasas (g)",     "id": "fats_g"},
                                {"name": "Agua (ml)",      "id": "water_ml"},
                                {"name": "Comentario",     "id": "note"},
                            ],
                            data=[], page_size=8,
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
    State("nutri-protein", "value"),
    State("nutri-carbs", "value"),
    State("nutri-fats", "value"),
    State("nutri-water", "value"),
    State("nutri-nota", "value"),
    prevent_initial_call=True,
)
def save_nutricion(n, data, date, adherence, kcal, protein, carbs, fats, water, note):
    if not n:
        raise PreventUpdate

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

    def _f(v):
        try: return float(v) if v is not None else None
        except Exception: return None

    try:
        if hasattr(db, "add_nutrition_entry"):
            db.add_nutrition_entry(uid, date_str, adh, _f(kcal), (note or "").strip(),
                                   protein_g=_f(protein), carbs_g=_f(carbs),
                                   fats_g=_f(fats), water_ml=_f(water))
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
    Output("nutri-data-store", "data"),
    Input("nutri-store", "data"),
    prevent_initial_call=False,
)
def load_nutri_data(_store):
    if not session.get("user_id"):
        return {"status": "unauthenticated", "uid": None, "rows": [], "table_data": []}

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
        {
            "date": r.get("date"), "adherence": r.get("adherence"), "kcal": r.get("kcal"),
            "protein_g": r.get("protein_g"), "carbs_g": r.get("carbs_g"),
            "fats_g": r.get("fats_g"), "water_ml": r.get("water_ml"), "note": r.get("note"),
        }
        for r in (rows or [])
    ]

    return {"status": "ok", "uid": uid, "rows": rows or [], "table_data": table_data}


def _build_nutri_insight(uid, rows):
    # ── Insight nutrición-wellness ───────────────────────────────────────────
    nutri_insight = None
    try:
        if uid and rows:
            qs_rows = db.list_questionnaires(uid) or []
            wellness_by_date = {}
            for q in qs_rows:
                d = (q.get("ts") or "")[:10]
                w = q.get("wellness_score")
                if d and w is not None:
                    wellness_by_date[d] = float(w)
            paired = [(r, wellness_by_date[r["date"]]) for r in rows if r.get("date") in wellness_by_date and r.get("adherence") is not None]
            if len(paired) >= 4:
                high_adh = [w for r, w in paired if r["adherence"] >= 75]
                low_adh  = [w for r, w in paired if r["adherence"] < 75]
                if high_adh and low_adh:
                    avg_high = sum(high_adh) / len(high_adh)
                    avg_low  = sum(low_adh)  / len(low_adh)
                    diff = avg_high - avg_low
                    if abs(diff) >= 3:
                        direction = "sube" if diff > 0 else "baja"
                        color = "var(--neon)" if diff > 0 else "#f0a832"
                        nutri_insight = html.Div(
                            className="card",
                            style={"marginBottom": "16px", "borderLeft": f"3px solid {color}"},
                            children=[
                                html.H4("Nutrición y bienestar — correlación", className="card-title"),
                                html.P(
                                    f"En los días con adherencia alta (≥75 %), tu bienestar promedio es "
                                    f"{avg_high:.0f}/100. En días con baja adherencia, {direction} a "
                                    f"{avg_low:.0f}/100 — una diferencia de {abs(diff):.0f} puntos.",
                                    className="text-muted",
                                ),
                                html.P(
                                    "Esto sugiere que seguir el plan nutricional tiene impacto real en cómo llegas al entrenamiento." if diff > 0
                                    else "La diferencia es pequeña — puede haber otros factores más determinantes que la nutrición esta semana.",
                                    style={"fontSize": "13px"},
                                ),
                            ],
                        )
    except Exception:
        pass
    return nutri_insight


@app.callback(
    Output("nutri-table", "data"),
    Output("nutri-kpi-strip", "children"),
    Output("nutri-graph-wrap", "style"),
    Output("nutri-no-data", "style"),
    Output("nutri-insight", "children"),
    Output("nutri-recent-table", "children"),
    Input("nutri-data-store", "data"),
    prevent_initial_call=True,
)
def render_nutri_summary(data):
    data = data or {}
    uid = data.get("uid")
    rows = data.get("rows") or []
    table_data = data.get("table_data") or []
    nutri_insight = _build_nutri_insight(uid, rows)
    if not rows:
        return (
            table_data,
            _build_nutri_kpis([]),
            _NUTRI_GRAPH_HIDDEN,
            _NUTRI_NODATA_VISIBLE,
            nutri_insight,
            _build_nutri_recent_table(rows),
        )
    return (
        table_data,
        _build_nutri_kpis(rows),
        _NUTRI_GRAPH_VISIBLE,
        _NUTRI_NODATA_HIDDEN,
        nutri_insight,
        _build_nutri_recent_table(rows),
    )


@app.callback(
    Output("nutri-graph", "figure"),
    Input("nutri-data-store", "data"),
    prevent_initial_call=True,
)
def render_nutri_graph(data):
    data = data or {}
    rows = data.get("rows") or []
    if not rows:
        return go.Figure()

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

    return fig



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
        html.Div(className="ecg-divider ecg-divider--spaced"),

        # --- Identidad de producto ---
        html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("¿Para quién es CombatIQ?", className="card-title"),
            html.P(
                "Para atletas de taekwondo y boxeo, y para los coaches que los preparan. "
                "No es una app genérica de fitness: "
                "cada lectura, recomendación y cuestionario está pensado para el contexto real "
                "del deporte de contacto.",
                className="text-muted",
                style={"marginBottom": "14px"},
            ),
            html.Div(className="filters-bar filters-bar--3", children=[
                html.Div(className="filter-item", children=[
                    html.Div("Especialidad", className="kpi-label"),
                    html.Div("Taekwondo · Boxeo", className="kpi-value"),
                    html.P("Enfoque actual del producto.", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("Señales reales", className="kpi-label"),
                    html.Div("IMU + ECG/HR", className="kpi-value"),
                    html.P("Base del análisis de sesión.", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("Decisiones mejores", className="kpi-label"),
                    html.Div("Contexto + Tendencia", className="kpi-value"),
                    html.P("Lectura accionable en cada vista.", className="text-muted"),
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
                html.Li("Señales ECG / IMU con datos reales, no solo RPE subjetivo."),
                html.Li("Histórico y comparativas para detectar sobrecargas antes de que afecten."),
                html.Li("Panel diferenciado: el coach ve el equipo, el deportista ve su evolución."),
            ], className="list-compact", style={"marginTop": "10px"}),
        ]),

        # --- Info técnica y versión ---
        html.Div(className="card", children=[
            html.H4("Versión y base técnica", className="card-title"),
            html.Div(className="filters-bar filters-bar--2", style={"marginTop": "10px"}, children=[
                html.Div(className="filter-item", children=[
                    html.Div("Versión", className="kpi-label"),
                    html.Div("v1.0", className="kpi-value"),
                    html.P("CombatIQ — Combat Sports Performance", className="text-muted"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Div("Base", className="kpi-label"),
                    html.Div("Web app + análisis", className="kpi-value"),
                    html.P("Python · Plotly · reportes profesionales", className="text-muted"),
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
        html.Div(className="ecg-divider ecg-divider--spaced"),

        # --- Card principal ---
        html.Div(className="card", style={"marginBottom": "14px"}, children=[
            html.H4("Tu enlace de registro", className="card-title"),
            html.P(body, className="text-muted", style={"marginBottom": "18px"}),

            html.Div(className="filters-bar filters-bar--2", style={"marginBottom": "16px"}, children=[
                html.Div(className="filter-item", children=[
                    html.Label("Enlace de registro"),
                    html.Div(invite_path, className="code-cell"),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("Código de referencia"),
                    html.Div(ref_code, className="code-cell"),
                ]),
            ]),

            html.Div(className="btn-save-row", children=[
                dcc.Clipboard(
                    target_id=None,
                    content=invite_path,
                    title="Copiar enlace",
                    className="btn btn-primary",
                ),
                dcc.Clipboard(
                    target_id=None,
                    content=ref_code,
                    title="Copiar código",
                    className="btn btn-ghost",
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
    Output("notif-save-msg", "children"),
    Input("notif-save-btn", "n_clicks"),
    State("notif-low-wellness", "value"),
    State("notif-announcements", "value"),
    State("notif-checkin-reminder", "value"),
    prevent_initial_call=True,
)
def save_notif_prefs(n, low_wellness_val, announcements_val, reminder_val):
    if not n:
        raise PreventUpdate
    uid = session.get("user_id")
    if not uid:
        return "Inicia sesión."
    try:
        db.save_notification_prefs(
            int(uid),
            low_wellness_alert=bool(low_wellness_val and "on" in low_wellness_val),
            announcement_notify=bool(announcements_val and "on" in announcements_val),
            checkin_reminder=bool(reminder_val and "on" in reminder_val),
        )
        return "✓ Preferencias guardadas."
    except Exception:
        return "Error al guardar."


@app.callback(
    Output("notif-coach-save-msg", "children"),
    Input("notif-coach-save-btn", "n_clicks"),
    State("notif-coach-wellness", "value"),
    State("notif-coach-weekly", "value"),
    prevent_initial_call=True,
)
def save_coach_notif_prefs(n, wellness_val, weekly_val):
    if not n:
        raise PreventUpdate
    uid = session.get("user_id")
    if not uid:
        return "Inicia sesión."
    try:
        db.save_notification_prefs(
            int(uid),
            low_wellness_alert=bool(wellness_val and "on" in wellness_val),
            announcement_notify=True,
            checkin_reminder=bool(weekly_val and "on" in weekly_val),
        )
        return "✓ Preferencias guardadas."
    except Exception:
        return "Error al guardar."


@app.callback(
    Output("athlete-digest-msg", "children"),
    Input("btn-athlete-weekly-digest", "n_clicks"),
    prevent_initial_call=True,
)
def send_athlete_weekly_digest(n):
    if not n:
        raise PreventUpdate
    uid = session.get("user_id")
    if not uid:
        return "Inicia sesión."
    try:
        uid_int = int(uid)
        user = db.get_user_by_id(uid_int)
        if not user:
            return "Usuario no encontrado."
        to_email = _to_str(user.get("email") or "")
        if not to_email or "@" not in to_email:
            return "Sin email registrado."
        name  = _to_str(user.get("name") or "Atleta")
        sport = _to_str(user.get("sport") or "deporte")

        weekly = {}
        if hasattr(db, "get_weekly_load_summary"):
            weekly = db.get_weekly_load_summary(uid_int) or {}

        qs = db.list_questionnaires(uid_int) or []
        streak = 0
        import datetime as _dt
        today = _dt.date.today()
        for i in range(30):
            day = today - _dt.timedelta(days=i)
            day_str = day.isoformat()
            if any(q.get("ts", "").startswith(day_str) for q in qs):
                streak += 1
            else:
                break

        next_comp = None
        if hasattr(db, "get_next_competition"):
            _nc = db.get_next_competition(uid_int)
            if _nc:
                try:
                    _ev_date = _dt.datetime.strptime(_nc["event_date"][:10], "%Y-%m-%d").date()
                    _days = (_ev_date - today).days
                    if _days >= 0:
                        next_comp = {
                            "event_name": _nc.get("event_name") or _nc.get("name") or "Competencia",
                            "event_date": _nc["event_date"][:10],
                            "days": _days,
                        }
                except Exception:
                    pass

        import notifications as _notif
        sent = _notif.notify_weekly_summary_athlete(
            to_email=to_email,
            name=name,
            sport=sport,
            streak=streak,
            avg_wellness=weekly.get("wellness_avg"),
            load_7d=int(weekly.get("load_units") or 0),
            next_comp=next_comp,
        )
        if sent:
            return f"✓ Resumen enviado a {to_email}"
        return "Email no configurado — revisa las variables MAIL_* en .env."
    except Exception as exc:
        import logging as _log
        _log.warning("send_athlete_weekly_digest error: %s", exc)
        return "Error al enviar. Revisa la consola para más detalles."


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
    except Exception:
        return "Error al guardar el RPE. Inténtalo de nuevo."


# ====== ROUTER ======
@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    State("auth-store", "data"),
)
def router(path, auth_store):
    def errbox(title, err):
        return html.Div([
            h2(title),
            html.Pre(err, style={
                "whiteSpace": "pre-wrap", "background": "#2b1f23",
                "border": "1px solid #4a2b31", "padding": "12px",
                "borderRadius": "10px", "color": "#FFB4B4", "overflow": "auto"
            })
        ])

    # Use Flask session first; fall back to auth-store (client-side sessionStorage)
    # when the session cookie is not forwarded in the Dash POST request context.
    flask_uid = session.get("user_id")
    store_uid = (auth_store or {}).get("user_id") if auth_store else None
    user_id = flask_uid or store_uid
    logged = bool(user_id)
    logging.getLogger("combatiq").debug(
        "[ROUTER] path=%s flask_uid=%s store_uid=%s logged=%s",
        path, flask_uid, store_uid, logged,
    )

    public_paths = _PUBLIC_AUTH_PATHS | {"/sobre"}
    if not logged and path not in public_paths and path not in (None, "", "/", "/inicio", "/home"):
        if page_login:
            return page_login.layout() if callable(getattr(page_login, "layout", None)) else page_login.layout
        return errbox("/login", err_login)

    if path in (None, "", "/", "/inicio", "/home"):
        if not logged:
            # Keep pre-rendered content intact — a nested dcc.Location won't
            # navigate; let the sidebar links guide the user to /login instead.
            raise PreventUpdate
        return home_tiles()
    if path in ("/usuarios", "/legacy"):
        return view_usuarios()
    if path == "/deportista":
        return view_deportista_v2()
    if path == "/anuncios":
        return view_anuncios()
    if path == "/mis-comunicados":
        return view_mis_comunicados()
    if path == "/contacto":
        return view_contacto_coach()
    if path == "/chat":
        if page_chat:
            return page_chat.layout()
        return errbox("/chat", err_chat)
    if path == "/sensores":
        return sensors_view.layout()
    if path == "/ecg":
        return signals_view.layout()
    if path == "/cuestionario":
        return wellbeing_page.layout_questionnaire()
    if path == "/historico":
        return wellbeing_page.layout_history()
    if path == "/sesiones":
        if page_sesiones:
            return page_sesiones.layout()
        return errbox("/sesiones", err_sesiones)
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
    if path == "/metricas":
        if page_metricas:
            return page_metricas.layout()
        return errbox("/metricas", err_metricas)
    if path == "/competencia":
        return view_competencia()

    mod, err = None, None
    if path == "/login":
        mod, err = page_login, err_login
    if path == "/registro":
        mod, err = page_register, err_register
    if path in ("/recuperar-password", "/forgot-password"):
        mod, err = page_forgot, err_forgot
    if path == "/onboarding":
        mod, err = page_onboarding, err_onboard
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
def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


AUTO_OPEN = _env_flag("POWERSYNC_AUTO_OPEN", "1")
COMBATIQ_DEBUG = _env_flag("COMBATIQ_DEBUG", "0")
_OPEN_SENTINEL = os.path.join(os.path.expanduser("~"), ".combatiq_opened")


def _open_browser_once(url):
    try:
        _chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        _opened = False
        for _cp in _chrome_paths:
            if os.path.exists(_cp):
                webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(_cp))
                webbrowser.get("chrome").open(url)
                _opened = True
                break
        if not _opened:
            webbrowser.open_new(url)
    except Exception:
        pass


# =============================================================================
# Planificación de competencia
# =============================================================================

def view_competencia():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta sección.")
    try:
        uid = int(session["user_id"])
    except (TypeError, ValueError):
        return html.Div("Sesión inválida. Inicia sesión de nuevo.")
    sport = (_to_str(session.get("sport")) or "").strip()
    role  = _to_str(session.get("role")) or ""
    is_coach = role == "coach"
    roster = _coach_roster(uid) if is_coach else []

    sport_key = "taekwondo" if ("taekwondo" in sport.lower() or "tkd" in sport.lower()) else \
                "boxeo"     if "box" in sport.lower() else "otro"
    sport_label = sport.title() or "Deporte"

    # Focus card tips por deporte
    if sport_key == "taekwondo":
        tips = [
            html.Li([html.Strong("Descarga TKD: "), "reducir volumen 40-50% en la última semana, mantener intensidad técnica."]),
            html.Li([html.Strong("Pesaje: "), "planifica el corte de peso con ≥4 semanas de antelación."]),
            html.Li([html.Strong("Pre-competencia: "), "última sesión intensa 3-4 días antes del torneo."]),
            html.Li([html.Strong("Sparring: "), "detener sparring de contacto completo 7 días antes."]),
        ]
    elif sport_key == "boxeo":
        tips = [
            html.Li([html.Strong("Descarga Boxeo: "), "semana -1: solo técnica y sombra, sin sparring fuerte."]),
            html.Li([html.Strong("Hidratación: "), "rehidratación activa las 24 h posteriores al pesaje."]),
            html.Li([html.Strong("Corte de peso: "), "máximo 4-5% de peso corporal en la semana previa."]),
            html.Li([html.Strong("Visualización: "), "integrar 10 min/día de mentalización en la semana de pelea."]),
        ]
    else:
        tips = [
            html.Li([html.Strong("Descarga precompetitiva: "), "reducir volumen 30-50% las 2 semanas previas."]),
            html.Li([html.Strong("Intensidad: "), "mantener calidad técnica aunque bajes carga."]),
            html.Li([html.Strong("Recuperación: "), "priorizar sueño y nutrición en la semana final."]),
        ]

    # ── Cálculo de la fase actual ─────────────────────────────────────────
    from datetime import date as _date
    today = _date.today()

    def _days_to(ev):
        try:
            return (_date.fromisoformat(ev["event_date"]) - today).days
        except Exception:
            return None

    def _phase(days):
        if days is None:
            return "Sin competencia", "var(--muted)"
        if days < 0:
            return "Competencia pasada", "var(--muted)"
        if days <= 7:
            return "Semana de competencia", "var(--punch)"
        if days <= 21:
            return "Pre-competencia", "#f0a832"
        if days <= 42:
            return "Específica", "#2fb7c4"
        return "Base / General", "var(--muted)"

    def _event_sort_key(ev):
        d = _days_to(ev)
        if d is not None and d >= 0:
            return (0, d, ev.get("event_date") or "")
        return (1, ev.get("event_date") or "", ev.get("name") or "")

    if is_coach:
        events = []
        for athlete in roster:
            athlete_id = athlete.get("id")
            if not athlete_id:
                continue
            for ev in db.list_competition_events(int(athlete_id), limit=20) or []:
                ev = dict(ev)
                ev["_athlete_id"] = int(athlete_id)
                ev["_athlete_name"] = athlete.get("name") or "Deportista"
                events.append(ev)
        events.sort(key=_event_sort_key)
        upcoming_events = [ev for ev in events if (_days_to(ev) is not None and _days_to(ev) >= 0)]
        next_ev = upcoming_events[0] if upcoming_events else None
    else:
        events = db.list_competition_events(uid, limit=20)
        next_ev = db.get_next_competition(uid)

    next_days   = _days_to(next_ev) if next_ev else None
    phase_label, phase_color = _phase(next_days)

    # KPIs
    days_kpi = str(next_days) if next_days is not None and next_days >= 0 else "—"
    event_kpi = next_ev["name"][:22] if next_ev else "Sin programar"
    event_sub = next_ev.get("event_date", "")[:10] if next_ev else "—"
    if next_ev and next_ev.get("_athlete_name"):
        event_sub = f"{event_sub} · {next_ev['_athlete_name']}"

    # ── Taper chart ───────────────────────────────────────────────────────
    taper_fig = None
    if next_ev and next_days is not None and 0 <= next_days <= 84:
        import plotly.graph_objects as _go
        weeks_out = [8, 7, 6, 5, 4, 3, 2, 1, 0]
        # Carga recomendada % (base→taper)
        if sport_key == "taekwondo":
            load_pct  = [85, 90, 95, 95, 90, 80, 60, 40, 20]
            intensity = [75, 80, 85, 90, 90, 85, 80, 75, 60]
        else:
            load_pct  = [80, 88, 92, 95, 90, 78, 58, 38, 20]
            intensity = [72, 78, 82, 88, 88, 82, 78, 72, 58]

        # weeks_remaining → index in x_labels (weeks_out is descending: 8..0)
        weeks_remaining = min(max(next_days // 7, 0), 8)
        current_idx = 8 - weeks_remaining          # x_labels[0]="S-8", [8]="Competencia"
        x_labels = [f"S-{w}" if w > 0 else "Competencia" for w in weeks_out]

        taper_fig = _go.Figure()
        taper_fig.add_trace(_go.Bar(
            x=x_labels, y=load_pct,
            name="Volumen (%)", marker_color="rgba(79,159,217,.55)",
            hovertemplate="%{y}%<extra>Volumen</extra>",
        ))
        taper_fig.add_trace(_go.Scatter(
            x=x_labels, y=intensity,
            name="Intensidad (%)", mode="lines+markers",
            line=dict(color="#2fb7c4", width=2.5),
            marker=dict(size=7, color="#2fb7c4"),
            hovertemplate="%{y}%<extra>Intensidad</extra>",
        ))
        # Marca la semana actual — usar índice entero en ejes categóricos
        if 0 <= current_idx < len(x_labels):
            taper_fig.add_vline(
                x=current_idx,
                line_dash="dot", line_color="#f0a832", line_width=2,
                annotation_text="Hoy", annotation_position="top",
                annotation_font_color="#f0a832",
            )
        from ui_charts import apply_chart_style
        apply_chart_style(taper_fig, height=280)
        taper_fig.update_layout(
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(title="% de carga base", range=[0, 110]),
            barmode="overlay",
        )

    # ── Tabla de eventos ──────────────────────────────────────────────────
    def _event_row(ev):
        d = _days_to(ev)
        ph, ph_col = _phase(d)
        days_txt = f"{d} días" if d is not None and d >= 0 else ("Pasado" if d is not None else "—")
        athlete_name = ev.get("_athlete_name")
        return html.Div(className="comp-event-row", children=[
            html.Div(className="comp-event-main", children=[
                html.Span(ev.get("name", "—"), className="comp-event-name"),
                *([html.Span(athlete_name, className="comp-event-loc text-muted")] if athlete_name else []),
                html.Span(ev.get("event_date", "")[:10], className="comp-event-date"),
                html.Span(ev.get("location") or "", className="comp-event-loc text-muted"),
            ]),
            html.Div(className="comp-event-meta", children=[
                html.Span(ph, style={"color": ph_col, "fontWeight": "700", "fontSize": "12px"}),
                html.Span(days_txt, className="text-muted", style={"fontSize": "12px"}),
            ]),
        ])

    event_rows = [_event_row(ev) for ev in events] if events else [
        html.P("Sin competencias programadas todavía.", className="text-muted")
    ]

    athlete_options = [
        {"label": f"{a.get('name', 'Deportista')} · {a.get('sport', sport_label)}", "value": int(a["id"])}
        for a in roster if a.get("id")
    ]
    default_athlete_id = athlete_options[0]["value"] if athlete_options else None
    athlete_selector = (
        html.Div(className="auth-field", style={"marginBottom": "10px"}, children=[
            html.Label("Deportista", htmlFor="comp-athlete"),
            dcc.Dropdown(
                id="comp-athlete",
                options=athlete_options,
                value=default_athlete_id,
                placeholder="Selecciona deportista...",
                clearable=False,
            ),
            html.P(
                "La competencia se guardará en la ficha del deportista seleccionado.",
                className="text-muted",
                style={"fontSize": "12px", "marginTop": "6px"},
            ),
        ])
        if is_coach else
        dcc.Dropdown(
            id="comp-athlete",
            options=[{"label": "Mi competencia", "value": uid}],
            value=uid,
            style={"display": "none"},
            clearable=False,
        )
    )

    return html.Div(className="page-content", children=[
        html.Div(className="profile-hero-grid", children=[
            html.Div(className="page-head profile-hero", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport_label, className="session-pill"),
                    html.Span(phase_label, className="session-pill session-pill--muted",
                              style={"color": phase_color}),
                ]),
                html.H2("Planificación de competencia"),
                html.P(
                    (
                        "Revisa las próximas competencias de tu equipo y registra eventos por deportista "
                        "para ajustar la descarga, carga y seguimiento."
                        if is_coach else
                        "Registra tus próximas competencias y consulta la curva de carga recomendada "
                        "para llegar en la mejor forma posible."
                    ),
                    className="text-muted",
                ),
            ]),
            html.Div(className="card profile-focus-card", children=[
                html.H4(f"Guía de descarga precompetitiva — {sport_label}", className="card-title"),
                html.Ul(tips, className="list-compact"),
            ]),
        ]),

        html.Div(className="kpis profile-kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Próxima competencia", className="kpi-label"),
                html.Div(days_kpi, className="kpi-value",
                         style={"color": phase_color}),
                html.Div("días restantes", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Evento", className="kpi-label"),
                html.Div(event_kpi, className="kpi-value",
                         style={"fontSize": "16px" if len(event_kpi) > 14 else "22px"}),
                html.Div(event_sub, className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi", children=[
                html.Div("Fase actual", className="kpi-label"),
                html.Div(phase_label, className="kpi-value",
                         style={"fontSize": "14px", "color": phase_color}),
                html.Div("Según días a competencia", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]),

        html.Div(className="ecg-divider ecg-divider--spaced"),

        # ── Taper chart ──────────────────────────────────────────────────
        *([
            html.Div(className="card", style={"marginBottom": "16px"}, children=[
                html.H4("Curva de carga recomendada", className="card-title"),
                html.P(
                    f"Volumen e intensidad sugeridos semana a semana hacia {next_ev['name']}. "
                    "La línea naranja marca dónde estás ahora.",
                    className="text-muted",
                    style={"marginBottom": "12px"},
                ),
                dcc.Graph(figure=taper_fig, config={"displayModeBar": False}),
            ]),
        ] if taper_fig else [
            html.Div(className="card", style={"marginBottom": "16px"}, children=[
                html.P(
                    "Registra una competencia del equipo para ver la curva de descarga."
                    if is_coach else
                    "Registra una competencia próxima para ver la curva de descarga.",
                    className="text-muted",
                ),
            ]),
        ]),

        # ── Checklist pre-competencia ─────────────────────────────────────
        *([
            html.Div(className="card", style={"marginBottom": "16px"}, children=[
                html.H4("Checklist pre-competencia", className="card-title"),
                html.P(
                    f"Tareas clave para llegar bien a {next_ev['name']}. "
                    "Marca cada item cuando esté listo.",
                    className="text-muted",
                    style={"marginBottom": "14px"},
                ),
                dcc.Checklist(
                    id="comp-checklist",
                    options=[{"label": " " + t, "value": t} for t in (
                        [
                            "Última sesión técnica ligera (sin sparring fuerte)",
                            "Pesaje oficial planificado (≤24 h antes si aplica)",
                            "Kit empacado: protecciones, dobok, chaleco PSS",
                            "Hidratación post-pesaje planificada",
                            "Visualización del combate (10 min/día)",
                            "Calentamiento de activación preparado",
                            "Sueño: 8+ h la noche anterior",
                        ] if sport_key == "taekwondo" else [
                            "Última sesión de sombra (sin sparring fuerte)",
                            "Pesaje oficial planificado (≤24 h antes si aplica)",
                            "Vendas, guantes y protecciones empacados",
                            "Rehidratación post-pesaje planificada",
                            "Plan de calentamiento (10-15 min) preparado",
                            "Mentalización completada",
                            "Sueño: 8+ h la noche anterior",
                        ] if sport_key == "boxeo" else [
                            "Equipo completo empacado y revisado",
                            "Pesaje oficial planificado (si aplica)",
                            "Nutrición e hidratación del día de competencia",
                            "Calentamiento preparado",
                            "Visualización y mentalización",
                            "Logística confirmada (transporte, hora de llegada)",
                            "Sueño: 8+ h la noche anterior",
                        ]
                    )],
                    value=[],
                    style={"fontSize": "14px", "lineHeight": "2"},
                    labelStyle={"display": "block"},
                    inputStyle={"marginRight": "8px"},
                ),
                html.Div(
                    "0 de 7 ítems completados — empieza cuando quieras.",
                    id="comp-checklist-progress",
                    className="text-muted",
                    style={"fontSize": "13px", "marginTop": "12px"},
                ),
            ]),
        ] if next_days is not None and next_days <= 21 else []),

        # ── Formulario nueva competencia ─────────────────────────────────
        html.Div(className="card", style={"marginBottom": "16px"}, children=[
            html.H4("Añadir competencia del equipo" if is_coach else "Añadir competencia", className="card-title"),
            html.P(
                "Elige el deportista y registra su próximo torneo o pelea."
                if is_coach else
                "Registra el próximo torneo o pelea para activar la planificación.",
                   className="text-muted", style={"marginBottom": "14px"}),
            athlete_selector,
            html.Div(className="ob-form-grid", children=[
                html.Div(className="auth-field", children=[
                    html.Label("Nombre del evento", htmlFor="comp-name"),
                    dcc.Input(id="comp-name", type="text", placeholder="Ej. Open Nacional TKD 2026",
                              className="auth-input", style={"width": "100%"}, maxLength=80),
                ]),
                html.Div(className="auth-field", children=[
                    html.Label("Fecha"),
                    dcc.DatePickerSingle(id="comp-event-date",
                                         display_format="DD/MM/YYYY",
                                         placeholder="Selecciona fecha",
                                         style={"width": "100%"}),
                ]),
                html.Div(className="auth-field", children=[
                    html.Label("Peso objetivo (kg, opcional)", htmlFor="comp-target-w"),
                    dcc.Input(id="comp-target-w", type="number", min=30, max=200, step=0.1,
                              placeholder="Ej. 62.5",
                              style={"width": "100%"}),
                ]),
                html.Div(className="auth-field", children=[
                    html.Label("Lugar (opcional)", htmlFor="comp-location"),
                    dcc.Input(id="comp-location", type="text", placeholder="Ciudad / recinto",
                              className="auth-input", style={"width": "100%"}, maxLength=80),
                ]),
            ]),
            html.Div(className="auth-field", style={"marginTop": "10px"}, children=[
                html.Label("Notas (opcional)"),
                dcc.Textarea(id="comp-notes", placeholder="Ej. Torneo clasificatorio para Nacionales.",
                             style={"width": "100%", "minHeight": "72px", "resize": "vertical",
                                    "padding": "10px", "borderRadius": "8px",
                                    "border": "1px solid var(--line)", "background": "var(--surface)",
                                    "color": "var(--ink)", "fontSize": "14px", "fontFamily": "inherit"}),
            ]),
            html.Div(className="btn-save-row", style={"marginTop": "14px"}, children=[
                html.Button("Guardar competencia", id="comp-save-btn", className="btn btn-primary", n_clicks=0),
                html.Div(id="comp-save-msg", className="text-muted", style={"fontSize": "13px"}),
            ]),
        ]),

        # ── Historial de eventos ──────────────────────────────────────────
        html.Div(className="card", children=[
            html.H4("Competencias programadas", className="card-title"),
            html.Div(id="comp-event-list", children=event_rows),
        ]),
    ])


@app.callback(
    Output("comp-save-msg", "children"),
    Output("comp-event-list", "children"),
    Output("comp-name", "value"),
    Output("comp-event-date", "date"),
    Output("comp-target-w", "value"),
    Output("comp-location", "value"),
    Output("comp-notes", "value"),
    Input("comp-save-btn", "n_clicks"),
    State("comp-name", "value"),
    State("comp-event-date", "date"),
    State("comp-target-w", "value"),
    State("comp-location", "value"),
    State("comp-notes", "value"),
    State("comp-athlete", "value"),
    prevent_initial_call=True,
)
def save_competition_event(n, name, event_date, target_w, location, notes, athlete_id):
    if not n:
        raise PreventUpdate
    uid = session.get("user_id")
    if not uid:
        return "Inicia sesión.", *[dash.no_update]*6
    name = (name or "").strip()
    if not name:
        return "El nombre es obligatorio.", *[dash.no_update]*6
    if not event_date:
        return "Selecciona una fecha.", *[dash.no_update]*6

    sport = (_to_str(session.get("sport")) or "").strip()
    role = _to_str(session.get("role")) or ""
    target_user_id = int(uid)
    if role == "coach":
        if not athlete_id:
            return "Selecciona un deportista.", *[dash.no_update]*6
        try:
            athlete_id_int = int(athlete_id)
        except (TypeError, ValueError):
            return "Deportista inválido.", *[dash.no_update]*6
        if not db.coach_has_athlete(int(uid), athlete_id_int, sport=sport or None):
            return "Ese deportista no pertenece a tu plantilla.", *[dash.no_update]*6
        target_user_id = athlete_id_int

    try:
        db.add_competition_event(
            target_user_id, name, event_date, sport,
            float(target_w) if target_w else None,
            (location or "").strip(),
            (notes or "").strip(),
        )
    except Exception as exc:
        return f"Error: {exc}", *[dash.no_update]*6

    # Reconstruye lista
    from datetime import date as _date
    today = _date.today()

    def _days_to(ev):
        try:
            return (_date.fromisoformat(ev["event_date"]) - today).days
        except Exception:
            return None

    def _phase(days):
        if days is None:
            return "Sin competencia", "var(--muted)"
        if days < 0:
            return "Pasada", "var(--muted)"
        if days <= 7:
            return "Semana de competencia", "var(--punch)"
        if days <= 21:
            return "Pre-competencia", "#f0a832"
        if days <= 42:
            return "Específica", "#2fb7c4"
        return "Base / General", "var(--muted)"

    def _ev_row(ev):
        d = _days_to(ev)
        ph, ph_col = _phase(d)
        dt = f"{d} días" if d is not None and d >= 0 else ("Pasado" if d is not None else "—")
        athlete_name = ev.get("_athlete_name")
        return html.Div(className="comp-event-row", children=[
            html.Div(className="comp-event-main", children=[
                html.Span(ev.get("name", "—"), className="comp-event-name"),
                *([html.Span(athlete_name, className="comp-event-loc text-muted")] if athlete_name else []),
                html.Span(ev.get("event_date", "")[:10], className="comp-event-date"),
                html.Span(ev.get("location") or "", className="comp-event-loc text-muted"),
            ]),
            html.Div(className="comp-event-meta", children=[
                html.Span(ph, style={"color": ph_col, "fontWeight": "700", "fontSize": "12px"}),
                html.Span(dt, className="text-muted", style={"fontSize": "12px"}),
            ]),
        ])

    if role == "coach":
        roster = _coach_roster(int(uid))
        events = []
        for athlete in roster:
            aid = athlete.get("id")
            if not aid:
                continue
            for ev in db.list_competition_events(int(aid), limit=20) or []:
                ev = dict(ev)
                ev["_athlete_id"] = int(aid)
                ev["_athlete_name"] = athlete.get("name") or "Deportista"
                events.append(ev)
        events.sort(key=lambda ev: (
            0 if (_days_to(ev) is not None and _days_to(ev) >= 0) else 1,
            _days_to(ev) if (_days_to(ev) is not None and _days_to(ev) >= 0) else ev.get("event_date", ""),
            ev.get("name", ""),
        ))
    else:
        events = db.list_competition_events(int(uid), limit=20)
    new_list = [_ev_row(ev) for ev in events] if events else [
        html.P("Sin competencias programadas todavía.", className="text-muted")
    ]
    return "✓ Competencia guardada.", new_list, "", None, None, "", ""


@app.callback(
    Output("comp-checklist-progress", "children"),
    Input("comp-checklist", "value"),
    prevent_initial_call=True,
)
def update_checklist_progress(checked):
    if checked is None:
        checked = []
    n_checked = len(checked)
    n_total = 7
    if n_checked == 0:
        return "0 de 7 ítems completados — empieza cuando quieras."
    if n_checked == n_total:
        return "✓ ¡Checklist completo! Estás listo/a para competir."
    return f"{n_checked} de {n_total} ítems completados."


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


def _sensor_api_authorized() -> bool:
    expected = os.environ.get("COMBATIQ_SENSOR_API_TOKEN")
    if not expected:
        return True
    provided = flask_request.headers.get("X-CombatIQ-Token", "")
    auth = flask_request.headers.get("Authorization", "")
    if not provided and auth.lower().startswith("bearer "):
        provided = auth.split(" ", 1)[1].strip()
    try:
        import hmac as _hmac
        return _hmac.compare_digest(str(provided), str(expected))
    except Exception:
        return provided == expected


def _normalize_sensor_api_code(raw_code: str | None) -> str:
    try:
        return S.normalize_code(raw_code)
    except Exception:
        return str(raw_code or "").strip().upper()


def _assign_sensor_if_known(user_id: int, sensor_code: str) -> None:
    try:
        if sensor_code and S.is_known_code(sensor_code) and hasattr(db, "add_user_sensor"):
            db.add_user_sensor(int(user_id), _normalize_sensor_api_code(sensor_code))
    except Exception:
        pass


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
        if not _sensor_api_authorized():
            return jsonify({"ok": False, "error": "No autorizado"}), 401
        data = flask_request.get_json(force=True, silent=True) or {}
        device_id   = str(data.get("device_id", "hub")).strip() or "hub"
        user_id     = int(data.get("user_id", 0))
        # acepta "sensor_code" o "sensor_type" (alias del hub)
        sensor_code = _normalize_sensor_api_code(
            data.get("sensor_code") or data.get("sensor_type") or "UNKNOWN"
        )
        if not user_id:
            return jsonify({"ok": False, "error": "user_id es obligatorio"}), 400
        found = db.update_device_last_seen(device_id, user_id)
        if not found:
            db.register_device(user_id, sensor_code, device_id)
        _assign_sensor_if_known(user_id, sensor_code)
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
        if not _sensor_api_authorized():
            return jsonify({"ok": False, "error": "No autorizado"}), 401
        data = flask_request.get_json(force=True, silent=True) or {}
        device_id   = str(data.get("device_id", "hub")).strip() or "hub"
        user_id     = int(data.get("user_id", 0))
        # acepta "sensor_code" o "sensor" (alias del hub)
        sensor_code = _normalize_sensor_api_code(
            data.get("sensor_code") or data.get("sensor") or ""
        )
        session_id  = data.get("session_id")

        if not user_id or not sensor_code:
            return jsonify({"ok": False, "error": "user_id y sensor_code son obligatorios"}), 400

        if device_id:
            db.update_device_last_seen(device_id, user_id)
        _assign_sensor_if_known(user_id, sensor_code)

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

        elif sensor_code in ("IMU_GLOVE", "IMU_WRIST", "IMU_FOOT", "IMU_HEAD", "IMU_ANKLE"):
            n_hits        = data.get("n_hits")
            hits_per_min  = data.get("hits_per_min")
            mean_int_g    = data.get("mean_int_g")
            max_int_g     = data.get("max_int_g")
            mean_ang_vel  = data.get("mean_ang_vel")
            max_ang_vel   = data.get("max_ang_vel")
            if any(v is not None for v in [n_hits, hits_per_min, mean_int_g, max_int_g]):
                db.save_imu_metrics(
                    user_id, filename,
                    int(n_hits or 0),
                    float(hits_per_min or 0),
                    float(mean_int_g or 0),
                    float(max_int_g or 0),
                    session_id=session_id,
                    sensor_type=sensor_code,
                    mean_ang_vel=float(mean_ang_vel) if mean_ang_vel is not None else None,
                    max_ang_vel=float(max_ang_vel)   if max_ang_vel  is not None else None,
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

        # Registrar paquete en sensor_session si hay sesión activa
        if session_id:
            try:
                db.record_sensor_sample(int(session_id), sensor_code)
            except Exception:
                pass  # no bloquear el flujo de datos si falla

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
        caller_id = session.get("user_id")
        caller_role = _to_str(session.get("role")) or ""
        if not caller_id:
            return jsonify({"devices": [], "error": "No autenticado"}), 401
        caller_id_int = int(caller_id)
        if caller_role == "deportista" and caller_id_int != int(user_id):
            return jsonify({"devices": [], "error": "No autorizado"}), 403
        if caller_role == "coach":
            coach_sport = _to_str(session.get("sport") or "") or None
            if not db.coach_has_athlete(caller_id_int, int(user_id), sport=coach_sport):
                return jsonify({"devices": [], "error": "No autorizado"}), 403
        if caller_role not in ("deportista", "coach", "admin"):
            return jsonify({"devices": [], "error": "No autorizado"}), 403

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


# =============================================================================
# PDF Report — /informe/<uid>
# =============================================================================

@server.route("/sw.js")
def service_worker():
    """Service Worker para PWA — cache-first para assets, network-first para navegación."""
    from flask import Response
    sw_code = """
const CACHE_NAME = 'combatiq-v4';

// Assets estáticos que se precargan en install
const PRECACHE_ASSETS = [
  '/assets/10_theme.css',
  '/assets/logo_combatiq.svg',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
];

// Respuesta offline mínima para rutas de navegación sin caché
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CombatIQ — Sin conexión</title>
<style>
  body{margin:0;background:#161f29;color:#c8d8e8;font-family:system-ui,sans-serif;
       display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:16px;}
  h1{color:#2fb7c4;font-size:1.4rem;margin:0;}
  p{color:#7a9ab5;font-size:.95rem;margin:0;}
  a{color:#2fb7c4;text-decoration:none;}
</style>
</head>
<body>
  <h1>Sin conexión</h1>
  <p>Revisa tu conexión a internet y <a href="/">recarga</a>.</p>
</body>
</html>`;

// ── Install: precaché de assets clave ────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: eliminar cachés de versiones anteriores ────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;

  // Pasar sin interceptar: Dash runtime y endpoints API
  if (url.pathname.startsWith('/_dash') || url.pathname.startsWith('/api/')) return;

  if (url.pathname.startsWith('/assets/')) {
    // Cache-first para assets: sirve del caché y actualiza en background
    e.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(e.request).then(cached => {
          const net = fetch(e.request).then(res => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          });
          return cached || net;
        })
      )
    );
    return;
  }

  // Network-only para rutas de navegación — las páginas son dinámicas (auth-dependent)
  // y no deben cachearse; solo se usa el fallback offline si la red falla totalmente.
  e.respondWith(
    fetch(e.request)
      .catch(() =>
        caches.match(e.request).then(cached =>
          cached || new Response(OFFLINE_HTML, {
            headers: {'Content-Type': 'text/html; charset=utf-8'},
          })
        )
      )
  );
});
"""
    return Response(sw_code, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/"})


_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
_DEFAULT_MAX_VIDEO_MB = 300
_LEGACY_POSE_ROUTE_MAX_FRAMES = 1500


def _video_upload_dirs():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    primary = os.path.join(base_dir, "data", "uploads")
    legacy_data = os.path.join(base_dir, "data", "uploads_legacy")
    legacy = os.path.join(base_dir, "assets", "uploads")
    return primary, legacy_data, legacy


_UPLOAD_ALIAS_CACHE = {"mtime": None, "data": {}}


def _upload_aliases():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "upload_aliases.json")
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        _UPLOAD_ALIAS_CACHE.update({"mtime": None, "data": {}})
        return {}
    if _UPLOAD_ALIAS_CACHE.get("mtime") == mtime:
        return _UPLOAD_ALIAS_CACHE.get("data") or {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        aliases = {
            os.path.basename(str(k)): os.path.basename(str(v))
            for k, v in (data or {}).items()
            if k and v
        }
    except Exception:
        aliases = {}
    _UPLOAD_ALIAS_CACHE.update({"mtime": mtime, "data": aliases})
    return aliases


def _resolve_uploaded_video(filename: str):
    fname = os.path.basename(str(filename or ""))
    if not fname:
        return None
    fname = _upload_aliases().get(fname, fname)
    if os.path.splitext(fname)[1].lower() not in _VIDEO_EXTS:
        return None
    for folder in _video_upload_dirs():
        try:
            base = os.path.abspath(folder)
            candidate = os.path.abspath(os.path.join(base, fname))
            if os.path.commonpath([base, candidate]) == base and os.path.exists(candidate):
                return candidate
        except Exception:
            continue
    return None


@server.route("/uploads/<path:filename>", methods=["GET"])
def uploaded_video_route(filename):
    """Serve user-uploaded videos outside Dash's assets scan."""
    from flask import abort, send_from_directory
    if not session.get("user_id"):
        abort(403)
    path = _resolve_uploaded_video(filename)
    if not path:
        abort(404)
    folder, fname = os.path.dirname(path), os.path.basename(path)
    return send_from_directory(folder, fname, conditional=True)


@server.route("/upload-video", methods=["POST"])
def upload_video_route():
    """Recibe un video por multipart/form-data y lo guarda fuera de assets."""
    import re as _re, uuid as _uuid
    if not session.get("user_id"):
        return jsonify({"error": "No autenticado"}), 401
    f = flask_request.files.get("file")
    if not f:
        return jsonify({"error": "No se recibió archivo"}), 400
    try:
        max_mb = int(os.getenv("COMBATIQ_MAX_VIDEO_MB", str(_DEFAULT_MAX_VIDEO_MB)) or _DEFAULT_MAX_VIDEO_MB)
    except (TypeError, ValueError):
        max_mb = _DEFAULT_MAX_VIDEO_MB
    max_bytes = max(1, max_mb) * 1024 * 1024
    if flask_request.content_length and flask_request.content_length > max_bytes:
        return jsonify({"error": f"Video demasiado grande. Límite: {max_mb} MB."}), 413
    _UPLOADS, _LEGACY_DATA_UPLOADS, _LEGACY_UPLOADS = _video_upload_dirs()
    os.makedirs(_UPLOADS, exist_ok=True)
    name = os.path.basename(f.filename or "video.mp4").replace(" ", "_")
    name = _re.sub(r"[^a-zA-Z0-9._-]", "", name)
    base, ext = os.path.splitext(name)
    if ext.lower() not in _VIDEO_EXTS:
        allowed = ", ".join(sorted(_VIDEO_EXTS))
        return jsonify({"error": f"Formato de video no soportado. Usa: {allowed}."}), 400
    base = (base or "video")[:80]
    candidate = f"{base}{ext}"
    full = os.path.join(_UPLOADS, candidate)
    legacy_data_full = os.path.join(_LEGACY_DATA_UPLOADS, candidate)
    legacy_full = os.path.join(_LEGACY_UPLOADS, candidate)
    if os.path.exists(full) or os.path.exists(legacy_data_full) or os.path.exists(legacy_full):
        candidate = f"{base}_{_uuid.uuid4().hex[:8]}{ext}"
        full = os.path.join(_UPLOADS, candidate)
    f.save(full)
    if os.path.getsize(full) > max_bytes:
        try:
            os.remove(full)
        except Exception:
            pass
        return jsonify({"error": f"Video demasiado grande. Límite: {max_mb} MB."}), 413
    return jsonify({"url": f"/uploads/{candidate}", "filename": candidate})


@server.route("/analyze-pose", methods=["POST"])
def analyze_pose_route():
    """Analiza postura en un video ya subido. Body JSON: {"filename": "video.mp4"}"""
    from flask import session as flask_session
    if not flask_session.get("user_id"):
        return jsonify({"error": "No autenticado"}), 401
    data     = flask_request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"error": "Falta el campo 'filename'"}), 400
    path = _resolve_uploaded_video(filename)
    if not path:
        return jsonify({"error": "No encuentro el video subido"}), 404
    try:
        sample_every = int(data.get("sample_every") or os.getenv("COMBATIQ_POSE_SAMPLE_EVERY", "10") or 10)
    except (TypeError, ValueError):
        sample_every = 10
    sample_every = max(1, min(sample_every, 60))
    try:
        max_frames = int(data.get("max_frames") or os.getenv("COMBATIQ_POSE_MAX_FRAMES", "220") or 220)
    except (TypeError, ValueError):
        max_frames = 220
    try:
        route_max_frames = int(
            os.getenv(
                "COMBATIQ_LEGACY_POSE_ROUTE_MAX_FRAMES",
                str(_LEGACY_POSE_ROUTE_MAX_FRAMES),
            ) or _LEGACY_POSE_ROUTE_MAX_FRAMES
        )
    except (TypeError, ValueError):
        route_max_frames = _LEGACY_POSE_ROUTE_MAX_FRAMES
    route_max_frames = max(1, route_max_frames)
    max_frames = max(1, min(max_frames, route_max_frames))
    try:
        from pose_analyzer import analyze_video
        sport_hint = data.get("sport") or session.get("sport")
        target = data.get("target") or data.get("pose_target") or "auto"
        result = analyze_video(
            path,
            sample_every=sample_every,
            max_frames=max_frames,
            sport=sport_hint,
            target=target,
        )
    except Exception as exc:
        return jsonify({"error": f"No se pudo analizar el video: {exc}"}), 500
    return jsonify(result)


def _export_filename_stem(value: str, default: str = "combatiq") -> str:
    """Return an ASCII-safe filename stem for HTTP download headers."""
    from report_utils import safe_filename_stem

    return safe_filename_stem(value, default)


@server.route("/informe/<int:uid>", methods=["GET"])
def generar_informe(uid):
    """
    Genera un informe PDF del atleta.
    Acceso: el propio atleta, o cualquier coach autenticado.
    """
    from flask import make_response, abort
    caller_id   = session.get("user_id")
    caller_role = _to_str(session.get("role")) or ""
    if not caller_id:
        abort(403)

    caller_id_int = int(caller_id)
    # Atleta solo puede ver su propio informe; coach solo su plantilla; admin todo.
    allowed = False
    if caller_role == "deportista":
        allowed = caller_id_int == uid
    elif caller_role == "coach":
        coach_sport = _to_str(session.get("sport") or "") or None
        allowed = db.coach_has_athlete(caller_id_int, int(uid), sport=coach_sport)
    elif caller_role == "admin":
        allowed = True
    if not allowed:
        abort(403)

    athlete = db.get_user_by_id(uid)
    if not athlete:
        abort(404)

    # ── Recopilar datos ─────────────────────────────────────────────────────
    from datetime import datetime as _dt
    import json as _json

    qs_all   = db.list_questionnaires(uid) or []
    weights  = db.list_weight_entries(uid, limit=60) or []
    nutri    = db.list_nutrition_entries(uid, limit=60) or []
    hrv_data = db.get_last_ecg_metrics(uid) if hasattr(db, "get_last_ecg_metrics") else None

    # Últimos 30 registros de bienestar con fecha válida
    wellness_rows = [
        q for q in qs_all
        if q.get("wellness_score") is not None
    ][:30]

    sport      = athlete.get("sport") or "—"
    name       = athlete.get("name") or "Deportista"
    report_date = _dt.now().strftime("%d/%m/%Y %H:%M")

    avg_wellness = (
        sum(float(r["wellness_score"]) for r in wellness_rows) / len(wellness_rows)
        if wellness_rows else None
    )
    last_wellness = float(wellness_rows[0]["wellness_score"]) if wellness_rows else None
    last_weight   = weights[0]["weight"] if weights else None
    target_weight = weights[0].get("target") if weights else None

    avg_protein = (
        sum(r.get("protein_g") or 0 for r in nutri) / len(nutri)
        if nutri else None
    )
    avg_kcal = (
        sum(r.get("kcal") or 0 for r in nutri) / len(nutri)
        if nutri else None
    )

    # Racha check-ins (días consecutivos con registro)
    checkin_dates = sorted(set(
        (r.get("ts") or "")[:10] for r in qs_all if (r.get("ts") or "")[:10]
    ), reverse=True)
    streak = 0
    today_d = _dt.now().date()
    for i, d in enumerate(checkin_dates):
        try:
            expected = today_d - __import__("datetime").timedelta(days=i)
            if _dt.strptime(d, "%Y-%m-%d").date() == expected:
                streak += 1
            else:
                break
        except Exception:
            break

    # ── Construir PDF con CombatIQPDF ────────────────────────────────────────
    from report_utils import CombatIQPDF
    from reportlab.lib.units import cm as _cm

    pdf = CombatIQPDF()
    pdf.header(
        "Informe de Rendimiento",
        f"{sport.title()} · Registro: {(athlete.get('created_at') or '')[:10]}",
        name,
        sport,
        session=f"Generado {report_date}",
        source=f"ID #{uid}",
    )

    # ── Status badge según bienestar ─────────────────────────────────────────
    if last_wellness is not None:
        if last_wellness >= 80:
            _ws_status, _ws_label = "ok",    f"Bienestar óptimo — {last_wellness:.0f}/100"
        elif last_wellness >= 60:
            _ws_status, _ws_label = "warn",  f"Bienestar moderado — {last_wellness:.0f}/100"
        else:
            _ws_status, _ws_label = "alert", f"Bienestar bajo — {last_wellness:.0f}/100 · Vigilar carga"
        pdf.status_badge(_ws_label, _ws_status)

    # KPIs principales
    ws_val  = f"{last_wellness:.0f}"  if last_wellness  is not None else "—"
    avg_val = f"{avg_wellness:.0f}"   if avg_wellness   is not None else "—"
    w_val   = f"{last_weight:.1f}"    if last_weight    is not None else "—"
    stk_val = str(streak)
    cnt_val = str(len(wellness_rows))
    # Status semántico por umbrales deportivos
    _ws_status  = ("ok" if (last_wellness or 0) >= 80 else ("warn" if (last_wellness or 0) >= 60 else "alert")) if last_wellness is not None else None
    _avg_status = ("ok" if (avg_wellness  or 0) >= 75 else "warn") if avg_wellness is not None else None
    _stk_status = ("ok" if streak >= 7 else ("warn" if streak >= 3 else None))
    pdf.metric_table([
        {"label": "Ultimo bienestar",   "value": ws_val,  "unit": "/ 100", "status": _ws_status},
        {"label": "Promedio bienestar", "value": avg_val, "unit": "/ 100", "status": _avg_status},
        {"label": "Ultimo peso",        "value": w_val,   "unit": "kg"},
        {"label": "Racha check-ins",    "value": stk_val, "unit": "dias",  "status": _stk_status},
    ])
    pdf.spacer(0.18)
    kpis2 = [{"label": "Check-ins totales", "value": cnt_val, "unit": "registros"}]
    if avg_kcal:
        kpis2.append({"label": "Kcal promedio", "value": f"{avg_kcal:.0f}", "unit": "kcal/día"})
    if avg_protein:
        kpis2.append({"label": "Proteína media", "value": f"{avg_protein:.0f}", "unit": "g/día"})
    if last_weight and target_weight:
        diff = float(last_weight) - float(target_weight)
        kpis2.append({"label": "Vs objetivo peso", "value": f"{diff:+.1f}", "unit": "kg"})
    pdf.metric_table(kpis2[:4])
    pdf.spacer(0.14)

    # Lectura rápida del perfil
    _overview_lines = []
    if last_wellness is not None:
        _overview_lines.append(
            f"Bienestar actual de {last_wellness:.0f}/100 "
            + ("— rango óptimo para competir." if last_wellness >= 80 else
               "— nivel aceptable, monitorear." if last_wellness >= 60 else
               "— por debajo del umbral, revisar carga y descanso.")
        )
    if streak > 0:
        _overview_lines.append(
            f"Racha de {streak} día{'s' if streak != 1 else ''} consecutivo{'s' if streak != 1 else ''} con check-in"
            + (" — excelente consistencia." if streak >= 7 else
               " — buena constancia." if streak >= 3 else ".")
        )
    if last_weight and target_weight:
        diff = float(last_weight) - float(target_weight)
        _overview_lines.append(
            f"Peso actual {last_weight:.1f} kg — "
            + (f"{abs(diff):.1f} kg sobre el objetivo." if diff > 0.3 else
               f"{abs(diff):.1f} kg bajo el objetivo." if diff < -0.3 else
               "dentro del objetivo de peso.")
        )
    if _overview_lines:
        pdf.card("Resumen del atleta", _overview_lines,
                 subtitle=f"Registro desde {(athlete.get('created_at') or '')[:10] or 'fecha no disponible'}.")

    # ── Narrativa IA ─────────────────────────────────────────────────────────
    try:
        import ai_insights as _AI_PDF
        _scores_all_pdf = [float(r["wellness_score"]) for r in wellness_rows if r.get("wellness_score") is not None]
        _trend_pdf = ""
        if len(_scores_all_pdf) >= 6:
            _l3, _p3 = sum(_scores_all_pdf[:3])/3, sum(_scores_all_pdf[3:6])/3
            _trend_pdf = ("ascendente" if _l3 > _p3 + 4 else
                          "descendente" if _l3 < _p3 - 4 else "estable")
        _low_days_pdf = sum(1 for s in _scores_all_pdf if s < 50)
        _next_comp_pdf = db.get_next_competition(uid) if hasattr(db, "get_next_competition") else None
        _comp_ctx = {}
        if _next_comp_pdf:
            try:
                from datetime import date as _ddate
                _nd = (_ddate.fromisoformat((_next_comp_pdf.get("event_date") or "")[:10]) - _ddate.today()).days
                _comp_ctx = {"name": _next_comp_pdf.get("name", ""), "days_until": _nd}
            except Exception:
                _comp_ctx = {"name": _next_comp_pdf.get("name", "")}
        _hrv_ctx = {}
        if hrv_data:
            _hrv_ctx = {"bpm": hrv_data.get("bpm", 0), "rmssd": hrv_data.get("rmssd_ms") or hrv_data.get("rmssd", 0)}
        _pdf_narrative = _AI_PDF.generate_pdf_narrative({
            "name":              name,
            "sport":             sport,
            "avg_wellness":      avg_wellness,
            "last_wellness":     last_wellness,
            "streak":            streak,
            "trend_txt":         _trend_pdf,
            "last_weight":       last_weight,
            "target_weight":     target_weight,
            "ecg":               _hrv_ctx,
            "next_comp":         _comp_ctx,
            "n_sessions_month":  len(wellness_rows),
            "low_wellness_days": _low_days_pdf,
        })
        if _pdf_narrative:
            pdf.spacer(0.14)
            pdf.highlight_card(
                "Analisis narrativo - CombatIQ IA",
                [_pdf_narrative],
                subtitle="Generado por IA",
            )
    except Exception:
        traceback.print_exc()  # narrativa AI opcional; el PDF continúa igualmente

    pdf.spacer(0.22)

    # ── Próxima competencia ───────────────────────────────────────────────────
    try:
        _next_comp = db.get_next_competition(uid)
    except Exception:
        _next_comp = None
    if _next_comp:
        _ev_date  = (_next_comp.get("event_date") or "")[:10]
        _ev_name  = _next_comp.get("name") or "Competencia"
        _ev_loc   = _next_comp.get("location") or ""
        _ev_tw    = _next_comp.get("target_weight")
        try:
            from datetime import date as _ddate
            _days_to = (_ddate.fromisoformat(_ev_date) - _ddate.today()).days
            _days_lbl = f"en {_days_to} días" if _days_to > 0 else ("¡HOY!" if _days_to == 0 else f"hace {abs(_days_to)} días")
        except Exception:
            _days_lbl = ""
        _comp_lines = [f"Evento: {_ev_name}  ·  Fecha: {_ev_date} ({_days_lbl})"]
        if _ev_loc:
            _comp_lines.append(f"Lugar: {_ev_loc}")
        if _ev_tw:
            _comp_w_diff = round(float(last_weight) - float(_ev_tw), 1) if last_weight else None
            _comp_lines.append(
                f"Peso objetivo: {float(_ev_tw):.1f} kg"
                + (f"  (faltan {_comp_w_diff:+.1f} kg)" if _comp_w_diff is not None else "")
            )
        pdf.section_title("Próxima competencia")
        pdf.card(_ev_name, _comp_lines,
                 subtitle=_days_lbl,
                 accent=None)
        pdf.spacer(0.18)

    # ── ECG ──────────────────────────────────────────────────────────────────
    pdf.section_title("Actividad cardiovascular", "Última lectura ECG registrada")
    # hrv_data ya recopilado en el bloque de datos (línea ~9134) para que la
    # narrativa AI también pueda usarlo. No redefinir aquí.
    if hrv_data:
        bpm_v   = f"{hrv_data['bpm']:.0f}"   if hrv_data.get("bpm")   else "—"
        sdnn_v  = f"{hrv_data['sdnn']:.1f}"  if hrv_data.get("sdnn")  else "—"
        rmssd_v = f"{hrv_data['rmssd']:.1f}" if hrv_data.get("rmssd") else "—"
        rmssd_f = float(hrv_data.get("rmssd") or 0)
        sdnn_f  = float(hrv_data.get("sdnn")  or 0)
        if rmssd_f >= 50 and sdnn_f >= 50:
            _ecg_status, _ecg_label = "ok",    "Recuperación cardiovascular favorable"
            _ecg_detail = "RMSSD y SDNN altos: el sistema nervioso autónomo muestra buena recuperación."
        elif rmssd_f >= 30 and sdnn_f >= 30:
            _ecg_status, _ecg_label = "warn",  "Lectura estable — monitorear en contexto"
            _ecg_detail = "Valores dentro del rango normal. Considerar carga del día y sensación del deportista."
        else:
            _ecg_status, _ecg_label = "alert", "Posible fatiga — vigilar carga de entrenamiento"
            _ecg_detail = "RMSSD bajo: puede indicar fatiga acumulada o estrés autonómico. Revisar descanso."
        pdf.status_badge(_ecg_label, _ecg_status)
        _sdnn_status  = "ok" if sdnn_f  >= 50 else ("warn" if sdnn_f  >= 30 else "alert")
        _rmssd_status = "ok" if rmssd_f >= 50 else ("warn" if rmssd_f >= 30 else "alert")
        pdf.metric_table([
            {"label": "FC media", "value": bpm_v,   "unit": "lpm"},
            {"label": "SDNN",     "value": sdnn_v,  "unit": "ms",  "status": _sdnn_status},
            {"label": "RMSSD",    "value": rmssd_v, "unit": "ms",  "status": _rmssd_status},
        ])
        pdf.spacer(0.1)
        pdf.card("Interpretación ECG", [_ecg_detail,
            "RMSSD ≥ 50 ms y SDNN ≥ 50 ms: recuperación favorable.",
            "RMSSD < 30 ms: señal de fatiga o estrés — reducir carga si el atleta lo confirma.",
        ])
    else:
        pdf.card("Sin datos ECG", [
            "Sube un archivo ECG en la sección Análisis para ver métricas cardiovasculares.",
            "La variabilidad de la frecuencia cardíaca (HRV) es uno de los indicadores de recuperación más útiles.",
        ])
    pdf.spacer(0.22)

    # ── Historial de bienestar ────────────────────────────────────────────────
    pdf.section_title("Historial de bienestar", "Últimos 20 registros · Bienestar (0-100), RPE (0-10), carga estimada")
    if wellness_rows:
        # Trend: compare last 3 vs previous 3
        _scores_all = [float(r["wellness_score"]) for r in wellness_rows if r.get("wellness_score") is not None]
        _trend_txt = ""
        if len(_scores_all) >= 6:
            _last3 = sum(_scores_all[:3]) / 3
            _prev3 = sum(_scores_all[3:6]) / 3
            if _last3 > _prev3 + 4:
                _trend_txt = f"Tendencia ascendente: promedio reciente {_last3:.0f} vs {_prev3:.0f} los 3 anteriores."
            elif _last3 < _prev3 - 4:
                _trend_txt = f"Tendencia descendente: promedio reciente {_last3:.0f} vs {_prev3:.0f} los 3 anteriores. Revisar carga."
            else:
                _trend_txt = f"Tendencia estable: promedio reciente {_last3:.0f} vs {_prev3:.0f} los 3 anteriores."
        n_alerts = sum(1 for s in _scores_all if s < 50)
        _alert_txt = f"{n_alerts} registro{'s' if n_alerts != 1 else ''} con bienestar < 50." if n_alerts > 0 else "Sin registros en zona de alerta (<50)."
        pdf.card("Tendencia de bienestar",
                 [t for t in [_trend_txt, _alert_txt] if t],
                 subtitle=f"Promedio global: {avg_wellness:.0f}/100 sobre {len(wellness_rows)} registros." if avg_wellness else None)
        wt_rows = []
        for r in wellness_rows[:20]:
            rpe = r.get("rpe")
            dur = r.get("duration_min")
            carga = str(round(float(rpe) * float(dur))) if rpe is not None and dur is not None else "—"
            wt_rows.append([
                (r.get("ts") or "")[:10],
                f"{float(r['wellness_score']):.0f}/100",
                str(rpe) if rpe is not None else "—",
                str(dur) if dur is not None else "—",
                carga,
            ])
        pdf.table(
            ["Fecha", "Bienestar", "RPE", "Duracion (min)", "Carga (UA)"],
            wt_rows,
            col_widths=[3.5*_cm, 3.2*_cm, 2.0*_cm, 3.5*_cm, 3.2*_cm],
            score_col=1,
        )
    else:
        pdf.card("Sin registros", ["No hay registros de bienestar disponibles todavía.",
                                   "El atleta debe completar el check-in diario para generar este historial."])
    pdf.spacer(0.22)

    # ── Carga de entrenamiento ────────────────────────────────────────────────
    pdf.section_title("Carga de entrenamiento", "Últimas 4 semanas")
    load_history = db.get_load_history(uid, weeks=4) if hasattr(db, "get_load_history") else []
    if load_history:
        lh_rows = []
        for b in load_history:
            lh_rows.append([
                b.get("label", "—"),
                str(int(b["load_units"])) if b.get("load_units") else "Sin datos",
                f"{b['wellness_avg']:.0f} / 100" if b.get("wellness_avg") else "—",
                str(b.get("n_sessions", 0)),
            ])
        pdf.table(
            ["Semana", "Carga (UA)", "Bienestar prom.", "Sesiones"],
            lh_rows,
            col_widths=[5.5*_cm, 3.5*_cm, 5.5*_cm, 2.9*_cm],
        )
    else:
        pdf.card("Sin historial de carga", [
            "Registra sesiones con RPE y duración para ver esta sección.",
        ])
    pdf.spacer(0.22)

    # ── Últimas sesiones ──────────────────────────────────────────────────────
    pdf.section_title("Últimas sesiones registradas")
    sessions_log = db.list_sessions(uid, limit=10) if hasattr(db, "list_sessions") else []
    if sessions_log:
        ss_rows = []
        for s in sessions_log:
            ts_s = (s.get("ts_start") or "")[:16].replace("T", " ")
            ts_e = (s.get("ts_end")   or "")[:16].replace("T", " ")
            ss_rows.append([
                ts_s[:10]       if ts_s         else "—",
                (s.get("sport") or "—").title(),
                ts_s[11:16]     if len(ts_s) > 10 else "—",
                ts_e[11:16]     if len(ts_e) > 10 else "—",
                "Cerrada" if s.get("status") == "closed" else "Abierta",
            ])
        pdf.table(
            ["Fecha", "Deporte", "Inicio", "Fin", "Estado"],
            ss_rows,
            col_widths=[3.5*_cm, 3.5*_cm, 2.5*_cm, 2.5*_cm, 5.4*_cm],
        )
    else:
        pdf.card("Sin sesiones", ["No hay sesiones registradas todavía."])
    pdf.spacer(0.22)

    # ── Peso ──────────────────────────────────────────────────────────────────
    pdf.section_title("Historial de peso", "Últimos 15 registros · evolución y objetivo de categoría")
    if weights:
        _w_vals = [float(r["weight"]) for r in weights if r.get("weight") is not None]
        _w_min  = min(_w_vals) if _w_vals else None
        _w_max  = max(_w_vals) if _w_vals else None
        _w_summary_lines = []
        if _w_min and _w_max:
            _w_summary_lines.append(f"Rango registrado: {_w_min:.1f} – {_w_max:.1f} kg.")
        if last_weight and target_weight:
            _wdiff = float(last_weight) - float(target_weight)
            _w_summary_lines.append(
                f"Actual {last_weight:.1f} kg vs objetivo {float(target_weight):.1f} kg "
                + (f"— {_wdiff:+.1f} kg. Reducir mediante dieta y/o corte de peso controlado." if _wdiff > 0.5 else
                   f"— {abs(_wdiff):.1f} kg bajo objetivo, vigilar que no sea déficit excesivo." if _wdiff < -0.5 else
                   "— dentro del objetivo de categoría.")
            )
        if _w_summary_lines:
            pdf.card("Análisis de peso", _w_summary_lines)
        pw_rows = []
        for r in weights[:15]:
            w = r.get("weight")
            t = r.get("target")
            diff_s = f"{float(w)-float(t):+.1f}" if (w is not None and t is not None) else "—"
            pw_rows.append([
                r.get("date") or "—",
                f"{float(w):.1f}" if w is not None else "—",
                f"{float(t):.1f}" if t is not None else "—",
                diff_s,
                (r.get("note") or "")[:40],
            ])
        pdf.table(
            ["Fecha", "Peso (kg)", "Objetivo (kg)", "Dif. (kg)", "Nota"],
            pw_rows,
            col_widths=[3.2*_cm, 2.8*_cm, 3.0*_cm, 2.5*_cm, 5.9*_cm],
        )
    else:
        pdf.card("Sin registros de peso", [
            "No hay registros de peso disponibles.",
            "Registra el peso diariamente para hacer seguimiento hacia el objetivo de categoría.",
        ])
    pdf.spacer(0.22)

    # ── Nutrición ─────────────────────────────────────────────────────────────
    pdf.section_title("Registro nutricional", "Últimos 15 registros · kcal, macros y adherencia al plan")
    if nutri:
        summ = []
        if avg_kcal:
            summ.append(f"Promedio kcal/día: {avg_kcal:.0f} kcal")
        if avg_protein:
            summ.append(f"Proteína media: {avg_protein:.0f} g/día")
        _avg_adh = None
        _adh_vals = [float(r["adherence"]) for r in nutri if r.get("adherence") is not None]
        if _adh_vals:
            _avg_adh = sum(_adh_vals) / len(_adh_vals)
            summ.append(f"Adherencia media al plan: {_avg_adh:.0f}%")
        if summ:
            pdf.card("Resumen nutricional", summ,
                     subtitle="Basado en los registros disponibles del período.")
        nt_rows = []
        for r in nutri[:15]:
            nt_rows.append([
                (r.get("date") or r.get("created_at") or "—")[:10],
                f"{r.get('adherence', 0):.0f}%" if r.get("adherence") is not None else "—",
                str(int(r["kcal"]))     if r.get("kcal")      else "—",
                f"{r['protein_g']:.0f}" if r.get("protein_g") else "—",
                f"{r['carbs_g']:.0f}"   if r.get("carbs_g")   else "—",
                f"{r['fats_g']:.0f}"    if r.get("fats_g")    else "—",
            ])
        pdf.table(
            ["Fecha", "Adherencia", "kcal", "Proteína (g)", "Carbos (g)", "Grasas (g)"],
            nt_rows,
            col_widths=[3.2*_cm, 2.5*_cm, 2.2*_cm, 2.8*_cm, 2.8*_cm, 2.9*_cm],
        )
    else:
        pdf.card("Sin registros nutricionales", [
            "No hay registros nutricionales disponibles.",
            "Registra la nutrición diaria para hacer seguimiento de macros y adherencia al plan.",
        ])

    pdf_bytes = pdf.finish()
    safe_name = _export_filename_stem(name, "deportista")
    filename  = f"combatiq_informe_{safe_name}_{_dt.now().strftime('%Y%m%d')}.pdf"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"]        = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@server.route("/informe-equipo/<int:coach_id>", methods=["GET"])
def generar_informe_equipo(coach_id):
    """
    PDF de equipo para el coach: una sección compacta por atleta.
    Solo accesible por el propio coach o un admin.
    """
    from flask import make_response, abort
    caller_id   = session.get("user_id")
    caller_role = _to_str(session.get("role")) or ""
    if not caller_id:
        abort(403)
    if caller_role == "coach" and int(caller_id) != coach_id:
        abort(403)
    if caller_role not in ("coach", "admin"):
        abort(403)

    coach = db.get_user_by_id(coach_id)
    if not coach:
        abort(404)

    from datetime import datetime as _dt, timedelta
    import json as _json, io

    coach_name  = coach.get("name") or "Coach"
    coach_sport = (coach.get("sport") or "").title()
    report_date = _dt.now().strftime("%d/%m/%Y %H:%M")
    roster      = _coach_roster(coach_id) or []
    roster_ids  = [int(a.get("id")) for a in roster if a.get("id") is not None]
    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}

    from report_utils import CombatIQPDF
    from reportlab.lib.units import cm as _cm

    pdf = CombatIQPDF()
    pdf.header(
        "Informe de Equipo",
        f"{coach_sport} · {len(roster)} atleta{'s' if len(roster) != 1 else ''} en plantilla",
        coach_name,
        coach_sport or "—",
        session=f"Generado {report_date}",
        source="CombatIQ Coach",
    )

    # ── Tabla resumen del equipo ──────────────────────────────────────────────
    cutoff_7 = (_dt.utcnow().date() - timedelta(days=7)).isoformat()
    athletes_data = []

    for a in roster:
        aid   = a.get("id")
        aname = a.get("name") or "—"
        qs = qs_bulk.get(int(aid), []) if aid else []
        qs7 = [q for q in qs if (q.get("ts") or "")[:10] >= cutoff_7]

        last_q    = qs[0] if qs else None
        last_ws   = float(last_q["wellness_score"]) if last_q and last_q.get("wellness_score") is not None else None
        last_date = (last_q.get("ts") or "")[:10] if last_q else "—"
        ws7       = [float(q["wellness_score"]) for q in qs7 if q.get("wellness_score") is not None]
        avg7      = round(sum(ws7) / len(ws7), 1) if ws7 else None
        load7     = round(sum(
            float(q["rpe"]) * float(q["duration_min"]) for q in qs7
            if q.get("rpe") is not None and q.get("duration_min") is not None
        ))

        dates_set = sorted(set((q.get("ts") or "")[:10] for q in qs if (q.get("ts") or "")[:10]), reverse=True)
        streak = 0
        today_d = _dt.utcnow().date()
        for i, d in enumerate(dates_set):
            try:
                if _dt.strptime(d, "%Y-%m-%d").date() == today_d - timedelta(days=i):
                    streak += 1
                else:
                    break
            except Exception:
                break

        alerta = last_ws is not None and last_ws < 50
        athletes_data.append({
            "a": a, "qs": qs,
            "last_ws": last_ws, "last_date": last_date,
            "avg7": avg7, "load7": load7, "streak": streak, "alerta": alerta,
        })

    # ── Stats globales del equipo ─────────────────────────────────────────────
    _n_total   = len(athletes_data)
    _n_alerts  = sum(1 for ad in athletes_data if ad["alerta"])
    _n_checkin = sum(1 for ad in athletes_data if ad["last_date"] >= cutoff_7)
    _ws_team   = [ad["avg7"] for ad in athletes_data if ad["avg7"] is not None]
    _team_avg  = round(sum(_ws_team) / len(_ws_team), 1) if _ws_team else None

    if _n_alerts > 0:
        _team_status, _team_label = "alert", f"{_n_alerts} atleta{'s' if _n_alerts != 1 else ''} con bienestar bajo — revisar carga"
    elif _n_checkin < _n_total // 2:
        _team_status, _team_label = "warn", f"Baja tasa de check-ins: {_n_checkin} de {_n_total} completaron"
    else:
        _team_status, _team_label = "ok", f"Equipo en buen estado — {_n_checkin}/{_n_total} con check-in reciente"
    pdf.status_badge(_team_label, _team_status)

    _checkin_status = "ok" if (_n_total and _n_checkin / _n_total >= 0.8) else "warn"
    _alert_status   = ("ok" if _n_alerts == 0 else ("warn" if _n_alerts / max(_n_total, 1) < 0.25 else "alert"))
    _avg_status_t   = ("ok" if (_team_avg or 0) >= 75 else ("warn" if (_team_avg or 0) >= 60 else "alert")) if _team_avg is not None else None
    _team_kpis = [
        {"label": "Atletas",        "value": str(_n_total),   "unit": "en plantilla"},
        {"label": "Con check-in",   "value": str(_n_checkin), "unit": "ultimos 7d",    "status": _checkin_status},
        {"label": "En alerta",      "value": str(_n_alerts),  "unit": "bienest. < 50", "status": _alert_status},
    ]
    if _team_avg is not None:
        _team_kpis.append({"label": "Bienestar equipo", "value": f"{_team_avg:.0f}", "unit": "prom. 7d", "status": _avg_status_t})
    pdf.metric_table(_team_kpis[:4])
    pdf.spacer(0.12)

    pdf.section_title("Resumen del equipo", "Últimos 7 días · bienestar, carga y racha de check-ins")
    pdf.card(
        "Cómo leer esta tabla",
        [
            "U. bienestar: último valor reportado por el deportista (0-100, donde ≥80 es óptimo).",
            "Prom. 7d: promedio de bienestar en los últimos 7 días — indica tendencia reciente.",
            "Carga 7d: suma de (RPE × duración en minutos) de los últimos 7 días — carga de entrenamiento acumulada.",
            "Racha: días consecutivos con check-in completado — refleja consistencia del seguimiento.",
            "Estado: 'Alerta' si el último bienestar fue < 50; 'OK' en caso contrario.",
        ],
        subtitle="Referencia rápida para interpretar cada columna.",
        accent=None,
    )
    sum_rows = []
    for ad in athletes_data:
        sum_rows.append([
            ad["a"].get("name") or "—",
            f"{ad['last_ws']:.0f}/100" if ad["last_ws"] is not None else "—",
            f"{ad['avg7']:.0f}/100"    if ad["avg7"]    is not None else "—",
            str(ad["load7"])           if ad["load7"]               else "0",
            str(ad["streak"]) + "d",
            "⚠ Alerta" if ad["alerta"] else "OK",
        ])
    pdf.table(
        ["Atleta", "Ult. bienestar", "Prom. 7d", "Carga 7d (UA)", "Racha", "Estado"],
        sum_rows,
        col_widths=[4.5*_cm, 2.8*_cm, 2.8*_cm, 3.2*_cm, 2.0*_cm, 2.1*_cm],
        score_col=1,
    )
    pdf.spacer(0.3)

    # ── Ficha por atleta ──────────────────────────────────────────────────────
    for ad in athletes_data:
        a_sport = (ad["a"].get("sport") or "—").title()
        aname   = ad["a"].get("name") or "—"
        _ws_str = f"{ad['last_ws']:.0f}/100" if ad["last_ws"] is not None else "—"
        pdf.section_title(
            f"{aname}  ·  {a_sport}",
            f"Ultimo check-in: {ad['last_date']} · Bienestar: {_ws_str} · Racha: {ad['streak']} dias",
        )
        if ad["qs"]:
            at_rows = []
            for q in ad["qs"][:8]:
                rpe = q.get("rpe")
                dur = q.get("duration_min")
                lu  = str(round(float(rpe) * float(dur))) if rpe is not None and dur is not None else "—"
                at_rows.append([
                    (q.get("ts") or "")[:10],
                    f"{float(q['wellness_score']):.0f}/100" if q.get("wellness_score") is not None else "—",
                    str(rpe) if rpe is not None else "—",
                    str(dur) if dur is not None else "—",
                    lu,
                ])
            pdf.table(
                ["Fecha", "Bienestar", "RPE", "Duracion (min)", "Carga (UA)"],
                at_rows,
                col_widths=[3.5*_cm, 3.5*_cm, 2.0*_cm, 4.0*_cm, 4.4*_cm],
                score_col=1,
            )
        else:
            pdf.card("Sin check-ins", ["No hay registros de bienestar para este deportista."])
        pdf.spacer(0.25)

    # ── Recomendaciones para el coach ────────────────────────────────────────
    if athletes_data:
        _rec_lines = []
        _alert_names = [ad["a"].get("name") for ad in athletes_data if ad["alerta"]]
        if _alert_names:
            _rec_lines.append(f"Atletas en alerta: {', '.join(_alert_names[:5])}. Considerar reducir carga o revisar check-in individual.")
        _no_checkin = [ad["a"].get("name") for ad in athletes_data if not ad["last_date"] or ad["last_date"] < cutoff_7]
        if _no_checkin:
            _rec_lines.append(f"Sin check-in esta semana: {', '.join(_no_checkin[:5])}. Recordar importancia del registro diario.")
        _high_load = [ad["a"].get("name") for ad in athletes_data if ad["load7"] and ad["load7"] > 2000]
        if _high_load:
            _rec_lines.append(f"Carga 7d elevada (>2000 UA): {', '.join(_high_load[:5])}. Monitorear signos de sobreentrenamiento.")
        if not _rec_lines:
            _rec_lines.append("El equipo se encuentra en buen estado general. Mantener el seguimiento regular.")
        _rec_lines.append("Este informe es un apoyo para la toma de decisiones del entrenador, no un diagnóstico clínico.")
        pdf.section_title("Recomendaciones")
        pdf.card("Acciones sugeridas para esta semana", _rec_lines,
                 subtitle="Basadas en los datos de los últimos 7 días.", accent=None)

    pdf_bytes = pdf.finish()
    safe_name = _export_filename_stem(coach_name, "coach")
    filename  = f"combatiq_equipo_{safe_name}_{_dt.now().strftime('%Y%m%d')}.pdf"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"]        = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ====== CSV Downloads ======

def _csv_str(headers: list, rows: list) -> str:
    """Genera un string CSV con BOM UTF-8 para compatibilidad con Excel."""
    lines = ["﻿" + ",".join(f'"{h}"' for h in headers)]
    for r in rows:
        lines.append(",".join(
            f'"{str(v).replace(chr(34), chr(39))}"' if v is not None else '""'
            for v in r
        ))
    return "\n".join(lines)


def _csv_date_cutoff(period_val) -> str | None:
    """Devuelve la fecha ISO mínima según el período seleccionado, o None si es 'todo'."""
    from datetime import datetime as _dt, timedelta
    try:
        days = int(period_val or 0)
    except (ValueError, TypeError):
        days = 0
    if days <= 0:
        return None
    return (_dt.utcnow().date() - timedelta(days=days)).isoformat()


@app.callback(
    Output("dl-peso-csv", "data"),
    Input("btn-dl-peso", "n_clicks"),
    State("dl-peso-period", "value"),
    prevent_initial_call=True,
)
def download_peso_csv(_, period):
    uid = session.get("user_id")
    if not uid:
        raise PreventUpdate
    from datetime import datetime as _dt
    from report_utils import xlsx_table
    cutoff = _csv_date_cutoff(period)
    rows = db.list_weight_entries(int(uid), limit=500)
    if cutoff:
        rows = [r for r in rows if (r.get("date") or "") >= cutoff]
    usr  = db.get_user_by_id(int(uid)) or {}
    meta = [
        ("Atleta",   usr.get("name", "")),
        ("Deporte",  usr.get("sport", "")),
        ("Periodo",  f"Ultimos {period} dias" if period and int(period) > 0 else "Historial completo"),
        ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
    ]
    headers = ["Fecha", "Peso (kg)", "Objetivo (kg)", "Diferencia con objetivo (kg)", "Nota"]
    data = []
    for r in sorted(rows, key=lambda x: x.get("date") or "", reverse=True):
        w = r.get("weight")
        t = r.get("target")
        diff = round(w - t, 2) if w is not None and t is not None else None
        data.append([
            r.get("date", ""),
            round(w, 2) if w is not None else "",
            round(t, 2) if t is not None else "",
            diff if diff is not None else "",
            r.get("note") or "",
        ])
    period_tag = f"_{period}d" if period and int(period) > 0 else "_completo"
    safe_name  = _export_filename_stem(usr.get("name", ""), "atleta")
    fname      = f"combatiq_peso_{safe_name}{period_tag}_{_dt.utcnow().strftime('%Y%m%d')}.xlsx"
    xl = xlsx_table(
        "Historial de peso",
        meta, headers, data,
        sheet_name="Peso",
        col_types={1: "number2", 2: "number2", 3: "number2"},
    )
    return dcc.send_bytes(lambda b: b.write(xl), fname)


@app.callback(
    Output("dl-nutri-csv", "data"),
    Input("btn-dl-nutri", "n_clicks"),
    State("dl-nutri-period", "value"),
    prevent_initial_call=True,
)
def download_nutri_csv(_, period):
    uid = session.get("user_id")
    if not uid:
        raise PreventUpdate
    from datetime import datetime as _dt
    from report_utils import xlsx_table
    cutoff = _csv_date_cutoff(period)
    rows = db.list_nutrition_entries(int(uid), limit=500)
    if cutoff:
        rows = [r for r in rows if (r.get("date") or "") >= cutoff]
    usr  = db.get_user_by_id(int(uid)) or {}
    meta = [
        ("Atleta",   usr.get("name", "")),
        ("Deporte",  usr.get("sport", "")),
        ("Periodo",  f"Ultimos {period} dias" if period and int(period) > 0 else "Historial completo"),
        ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
    ]
    headers = [
        "Fecha", "Adherencia al plan (%)", "Calorías (kcal)",
        "Proteína (g)", "Carbohidratos (g)", "Grasas (g)", "Agua (ml)", "Nota",
    ]
    data = []
    for r in sorted(rows, key=lambda x: x.get("date") or "", reverse=True):
        def _r(v, dec=1):
            return round(v, dec) if v is not None else ""
        data.append([
            r.get("date", ""),
            _r(r.get("adherence")),
            _r(r.get("kcal"), 0),
            _r(r.get("protein_g")),
            _r(r.get("carbs_g")),
            _r(r.get("fats_g")),
            _r(r.get("water_ml"), 0),
            r.get("note") or "",
        ])
    period_tag = f"_{period}d" if period and int(period) > 0 else "_completo"
    safe_name  = _export_filename_stem(usr.get("name", ""), "atleta")
    fname      = f"combatiq_nutricion_{safe_name}{period_tag}_{_dt.utcnow().strftime('%Y%m%d')}.xlsx"
    xl = xlsx_table(
        "Historial de nutricion",
        meta, headers, data,
        sheet_name="Nutricion",
        col_types={1: "pct", 2: "int", 3: "number2", 4: "number2", 5: "number2", 6: "int"},
    )
    return dcc.send_bytes(lambda b: b.write(xl), fname)


# ── Nutrición coach: panel de detalle al seleccionar atleta ───────────────────

@app.callback(
    Output("nutri-coach-detail-panel", "children"),
    Input("nutri-coach-athlete-select", "value"),
    prevent_initial_call=True,
)
def nutri_coach_load_athlete_detail(athlete_id):
    if not athlete_id:
        raise PreventUpdate
    coach_id = session.get("user_id")
    role     = _to_str(session.get("role")) or ""
    if role != "coach" or not coach_id:
        raise PreventUpdate
    try:
        coach_int   = int(coach_id)
        athlete_int = int(athlete_id)
    except (TypeError, ValueError):
        raise PreventUpdate

    athletes = _coach_roster(coach_int)
    if not any(int(a["id"]) == athlete_int for a in athletes if a.get("id") is not None):
        raise PreventUpdate

    athlete = db.get_user_by_id(athlete_int)
    if not athlete:
        raise PreventUpdate

    rows = db.list_nutrition_entries(athlete_int, limit=7) or []

    from datetime import date as _ddate, timedelta as _td
    today_iso = _ddate.today().isoformat()
    week_start = (_ddate.today() - _td(days=_ddate.today().weekday())).isoformat()

    existing = None
    try:
        existing = db.get_latest_nutrition_feedback(athlete_int, coach_int)
    except Exception:
        pass

    already_validated = (
        existing and existing.get("week_start") == week_start
    )

    def _entry_row(r):
        adh = r.get("adherence")
        adh_color = ("#27c98f" if (adh or 0) >= 80 else ("#f0a832" if (adh or 0) >= 60 else "#e45a5a")) if adh is not None else "var(--muted)"
        kcal  = f"{r['kcal']:.0f}" if r.get("kcal") is not None else "—"
        water = f"{r['water_ml']/1000:.1f} L" if r.get("water_ml") is not None else "—"
        return html.Div(
            style={"display": "flex", "gap": "20px", "padding": "6px 0",
                   "borderBottom": "1px solid var(--line)", "flexWrap": "wrap"},
            children=[
                html.Span(r.get("date", "—"), style={"minWidth": "90px", "fontSize": "13px", "color": "var(--muted)"}),
                html.Span(f"{adh:.0f}%" if adh is not None else "—",
                          style={"fontWeight": "700", "color": adh_color, "minWidth": "45px", "fontSize": "13px"}),
                html.Span(f"{kcal} kcal", style={"minWidth": "70px", "fontSize": "13px"}),
                html.Span(f"Agua {water}", style={"fontSize": "13px", "color": "var(--muted)"}),
            ],
        )

    return html.Div([
        html.Div(
            style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                   "borderBottom": "1px solid var(--line)", "paddingBottom": "8px"},
            children=[
                html.Span("Fecha", style={"minWidth": "90px", "fontSize": "12px", "color": "var(--muted)", "fontWeight": "600"}),
                html.Span("Adh.", style={"minWidth": "45px", "fontSize": "12px", "color": "var(--muted)", "fontWeight": "600"}),
                html.Span("Kcal", style={"minWidth": "70px", "fontSize": "12px", "color": "var(--muted)", "fontWeight": "600"}),
                html.Span("Hidratación", style={"fontSize": "12px", "color": "var(--muted)", "fontWeight": "600"}),
            ],
        ),
        *([_entry_row(r) for r in rows] if rows else
          [html.P("Este atleta no tiene registros de nutrición todavía.", className="text-muted",
                  style={"fontSize": "13px", "padding": "8px 0"})]),
        html.Div(style={"marginTop": "14px"}, children=[
            html.Label("Nota del coach (opcional)", style={"fontSize": "13px", "marginBottom": "6px", "display": "block"}),
            dcc.Textarea(
                id="nutri-coach-note",
                placeholder="Ej: Sube las kcal 200 el día previo a la competencia. Buen trabajo con el agua esta semana.",
                style={"width": "100%", "minHeight": "70px", "resize": "vertical",
                       "borderRadius": "8px", "padding": "8px", "fontSize": "13px"},
                value=existing.get("note") or "" if existing else "",
            ),
        ]),
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "12px", "marginTop": "10px"}, children=[
            html.Button(
                "✓ Validar plan esta semana" if not already_validated else "✓ Actualizar validación",
                id="btn-nutri-coach-validate",
                className="btn btn-primary",
                style={"fontSize": "13px"},
                **{"data-athlete": str(athlete_int), "data-week": week_start},
            ),
            html.Div(id="nutri-coach-validate-msg", children=(
                html.Span(f"Ya validado el {existing['validated_at'][:10]}",
                          style={"color": "#27c98f", "fontSize": "13px"})
                if already_validated else ""
            )),
        ]),
    ])


@app.callback(
    Output("nutri-coach-validate-msg", "children"),
    Input("btn-nutri-coach-validate", "n_clicks"),
    State("nutri-coach-athlete-select", "value"),
    State("nutri-coach-note", "value"),
    prevent_initial_call=True,
)
def nutri_coach_save_validation(n_clicks, athlete_id, note):
    if not athlete_id:
        raise PreventUpdate
    coach_id = session.get("user_id")
    role     = _to_str(session.get("role")) or ""
    if role != "coach" or not coach_id:
        raise PreventUpdate
    try:
        coach_int   = int(coach_id)
        athlete_int = int(athlete_id)
    except (TypeError, ValueError):
        raise PreventUpdate

    athletes = _coach_roster(coach_int)
    if not any(int(a["id"]) == athlete_int for a in athletes if a.get("id") is not None):
        raise PreventUpdate

    from datetime import date as _ddate, timedelta as _td
    week_start = (_ddate.today() - _td(days=_ddate.today().weekday())).isoformat()

    try:
        db.upsert_nutrition_feedback(athlete_int, coach_int, week_start, note or "")
    except Exception:
        return html.Span("Error al guardar. Inténtalo de nuevo.", style={"color": "#e45a5a", "fontSize": "13px"})

    athlete = db.get_user_by_id(athlete_int)
    athlete_name = (athlete or {}).get("name") or "el atleta"
    today_str = _ddate.today().strftime("%d %b %Y")
    return html.Span(f"✓ Plan de {athlete_name} validado · {today_str}",
                     style={"color": "#27c98f", "fontSize": "13px", "fontWeight": "600"})


@app.callback(
    Output("dl-team-csv", "data"),
    Input("btn-dl-team", "n_clicks"),
    State("dl-team-period", "value"),
    prevent_initial_call=True,
)
def download_team_csv(_, period):
    uid = session.get("user_id")
    if not uid or _to_str(session.get("role")) != "coach":
        raise PreventUpdate

    coach_id    = int(uid)
    coach_sport = _to_str(session.get("sport") or "") or None
    roster      = _coach_roster(coach_id)
    if not roster:
        raise PreventUpdate
    roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]
    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}

    from datetime import datetime as _dt, timedelta, date as _date
    try:
        days = int(period or 0)
    except (ValueError, TypeError):
        days = 0
    cutoff = (_dt.utcnow().date() - timedelta(days=days)).isoformat() if days > 0 else None

    headers = [
        "Atleta", "Deporte",
        "Último check-in", "Último bienestar (0-100)",
        f"Prom. bienestar ({days}d)" if days > 0 else "Prom. bienestar",
        "RPE último", "Duración última (min)", "Carga semana (UA)",
        "Racha check-ins (días)", "Alerta (bienestar < 50)",
    ]

    rows_out = []
    for a in roster:
        aid = a.get("id")
        name = a.get("name") or "—"
        sport = (a.get("sport") or "—").title()

        qs = qs_bulk.get(int(aid), []) if aid else []

        if cutoff:
            qs_period = [q for q in qs if (q.get("ts") or "")[:10] >= cutoff]
        else:
            qs_period = qs

        last_q     = qs[0] if qs else None
        last_date  = (last_q.get("ts") or "")[:10] if last_q else ""
        last_ws    = round(float(last_q["wellness_score"]), 1) if last_q and last_q.get("wellness_score") is not None else ""
        last_rpe   = last_q.get("rpe") if last_q else ""
        last_dur   = last_q.get("duration_min") if last_q else ""

        ws_vals = [float(q["wellness_score"]) for q in qs_period if q.get("wellness_score") is not None]
        avg_ws  = round(sum(ws_vals) / len(ws_vals), 1) if ws_vals else ""

        # Carga semana actual (UA) — últimos 7 días
        week_cutoff = (_dt.utcnow().date() - timedelta(days=7)).isoformat()
        load_rows = [q for q in qs if (q.get("ts") or "")[:10] >= week_cutoff
                     and q.get("rpe") is not None and q.get("duration_min") is not None]
        week_load = round(sum(float(q["rpe"]) * float(q["duration_min"]) for q in load_rows)) if load_rows else ""

        # Racha de check-ins
        checkin_dates = sorted(set((q.get("ts") or "")[:10] for q in qs if (q.get("ts") or "")[:10]), reverse=True)
        streak = 0
        today_d = _dt.utcnow().date()
        for i, d in enumerate(checkin_dates):
            try:
                expected = today_d - timedelta(days=i)
                if _dt.strptime(d, "%Y-%m-%d").date() == expected:
                    streak += 1
                else:
                    break
            except Exception:
                break

        alerta = "Sí" if (last_ws != "" and float(last_ws) < 50) else ("No" if last_ws != "" else "Sin dato")

        rows_out.append([
            name, sport, last_date, last_ws, avg_ws,
            last_rpe, last_dur, week_load, streak, alerta,
        ])

    from report_utils import xlsx_table
    coach_row   = db.get_user_by_id(coach_id)
    coach_name  = (coach_row.get("name") or "—") if coach_row else "—"
    period_label = f"Últimos {days} días" if days > 0 else "Completo"
    meta = [
        ("Coach",    coach_name),
        ("Deporte",  (coach_sport or "Todos").title()),
        ("Período",  period_label),
        ("Atletas",  str(len(roster))),
        ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
    ]
    xl = xlsx_table(
        "Resumen del equipo",
        meta, headers, rows_out,
        sheet_name="Equipo",
        col_types={3: "number2", 4: "number2", 5: "number2", 6: "int", 7: "int", 8: "int"},
    )
    period_tag = f"_{days}d" if days > 0 else "_completo"
    sport_tag  = f"_{coach_sport.lower().replace(' ', '_')}" if coach_sport else ""
    fname = f"combatiq_equipo{sport_tag}{period_tag}_{_dt.utcnow().strftime('%Y%m%d')}.xlsx"
    return dcc.send_bytes(lambda b: b.write(xl), fname)


# ── AI Chat callbacks ─────────────────────────────────────────────────────────

@app.callback(
    Output("ai-chat-wrapper", "style"),
    Input("btn-chat-toggle", "n_clicks"),
    Input("btn-chat-close",  "n_clicks"),
    prevent_initial_call=True,
)
def toggle_chat(open_clicks, close_clicks):
    trigger = callback_context.triggered_id if callback_context.triggered_id else ""
    if trigger == "btn-chat-close":
        return {"display": "none"}
    return {"display": "block"}


@app.callback(
    Output("ai-chat-messages", "children"),
    Output("ai-chat-history",  "data"),
    Output("ai-chat-input",    "value"),
    Input("btn-chat-send",  "n_clicks"),
    Input("ai-chat-input",  "n_submit"),
    State("ai-chat-input",  "value"),
    State("ai-chat-history", "data"),
    prevent_initial_call=True,
)
def send_chat_message(n_send, n_submit, message, history):
    if not message or not message.strip():
        raise PreventUpdate

    message = message.strip()
    history = history or []

    # Build coach context from session
    def _sint(v):
        try: return int(v)
        except Exception: return None

    coach_name = session.get("name") or "Coach"
    sport      = _to_str(session.get("sport")) or "combate"
    coach_id   = _sint(session.get("user_id"))

    role = session.get("role") or "deportista"
    athletes_ctx = []
    athlete_self_ctx = None

    def _next_comp_str(uid):
        """Returns 'Nombre (YYYY-MM-DD)' or None."""
        try:
            comp = db.get_next_competition(int(uid))
            if comp:
                return f"{comp.get('name','Competencia')} ({comp.get('event_date','')})"
        except Exception:
            pass
        return None

    def _ecg_str(uid):
        """Returns short ECG string or None."""
        try:
            ecg = db.get_last_ecg_metrics(int(uid))
            if ecg:
                return f"{int(ecg['bpm'])} bpm · SDNN {int(ecg['sdnn'])} ms · RMSSD {int(ecg['rmssd'])} ms"
        except Exception:
            pass
        return None

    if role == "coach" and coach_id:
        try:
            roster = _coach_roster(coach_id)
            aids = [_sint(a.get("id")) for a in roster[:15] if _sint(a.get("id"))]

            # Bulk queries — single round-trip each
            ecg_bulk = {}
            qs_bulk  = {}
            try:
                ecg_bulk = db.get_last_ecg_metrics_bulk(aids) or {}
            except Exception:
                pass
            try:
                qs_bulk = db.list_questionnaires_bulk(aids) or {}
            except Exception:
                pass

            # Single competition query for all athletes
            comp_bulk = {}
            if aids:
                try:
                    import sqlite3 as _sq
                    today = datetime.utcnow().strftime("%Y-%m-%d")
                    with db._get_conn() as _con:
                        _con.row_factory = _sq.Row
                        _cur = _con.cursor()
                        _placeholders = ",".join("?" * len(aids))
                        _cur.execute(
                            f"SELECT user_id, name, event_date FROM competition_events "
                            f"WHERE user_id IN ({_placeholders}) AND event_date >= ? "
                            f"ORDER BY event_date ASC",
                            aids + [today],
                        )
                        for row in _cur.fetchall():
                            uid_row = row["user_id"]
                            if uid_row not in comp_bulk:
                                comp_bulk[uid_row] = f"{row['name']} ({row['event_date']})"
                except Exception:
                    pass

            for a in roster[:15]:
                aid  = _sint(a.get("id"))
                name = a.get("name", "Atleta")
                last_date = None
                wellness  = None
                trend     = []
                qs_list   = qs_bulk.get(aid, []) if aid else []
                if qs_list:
                    q         = qs_list[0]  # DESC order: index 0 = most recent
                    wellness  = q.get("wellness_score")
                    last_date = (q.get("ts") or q.get("created_at") or "")[:10]
                    trend     = [x.get("wellness_score") for x in reversed(qs_list[:5]) if x.get("wellness_score") is not None]
                ecg_raw = ecg_bulk.get(aid) if aid else None
                ecg = None
                if ecg_raw:
                    try:
                        ecg = f"{int(ecg_raw['bpm'])} bpm · SDNN {int(ecg_raw['sdnn'])} ms"
                    except Exception:
                        pass
                athletes_ctx.append({
                    "name": name,
                    "wellness": wellness,
                    "wellness_trend": trend,
                    "last_session_date": last_date,
                    "sport": a.get("sport"),
                    "ecg_last": ecg,
                    "next_comp": comp_bulk.get(aid),
                })
        except Exception:
            pass
    elif role == "deportista" and coach_id:
        try:
            qs = db.list_questionnaires(coach_id) or []
            last_q    = qs[0] if qs else {}  # DESC order: index 0 = most recent
            wellness  = last_q.get("wellness_score")
            last_date = (last_q.get("ts") or last_q.get("created_at") or "")[:10]
            trend     = [x.get("wellness_score") for x in reversed(qs[:5]) if x.get("wellness_score") is not None]
            athlete_self_ctx = {
                "name":              coach_name,
                "sport":             sport,
                "wellness":          wellness,
                "wellness_trend":    trend,
                "last_session_date": last_date,
                "n_sessions":        len(qs),
                "ecg_last":          _ecg_str(coach_id),
                "next_comp":         _next_comp_str(coach_id),
            }
        except Exception:
            pass

    # Call AI. If the external provider is unavailable, keep the assistant useful
    # instead of surfacing a raw "Connection error" bubble.
    if role == "deportista":
        ctx = {
            "athlete_name": coach_name,
            "sport": sport,
            "role": "deportista",
            "athlete_ctx": athlete_self_ctx,
        }
    else:
        ctx = {"coach_name": coach_name, "sport": sport, "athletes": athletes_ctx}

    try:
        from ai_insights import generate_chat_response as _chat
        reply = _chat(message, history, context=ctx)
    except Exception as exc:
        logging.getLogger("combatiq").warning("floating AI chat fallback: %s", exc)
        if role == "deportista":
            reply = (
                "Modo local: no pude conectar con la IA externa, pero puedo ayudarte con tu contexto. "
                "Revisa tu check-in, tu ultima sesion y tu RPE; si hoy estas bajo de energia, baja volumen "
                "y trabaja tecnica limpia."
            )
        else:
            reply = (
                "Modo local: no pude conectar con la IA externa, pero puedo ayudarte con el equipo. "
                "Prioriza atletas con bienestar bajo, confirma check-ins pendientes y usa ECG/RPE antes "
                "de asignar intensidad."
            )

    # Update history (keep last 20 exchanges)
    new_history = (history + [
        {"role": "user",      "content": message},
        {"role": "assistant", "content": reply},
    ])[-20:]

    # Rebuild message bubbles
    _BUBBLE_USER = {
        "fontSize": "12px", "color": "var(--ink)", "lineHeight": "1.4",
        "background": "rgba(255,255,255,0.05)",
        "borderRadius": "8px", "padding": "7px 10px",
        "alignSelf": "flex-end", "maxWidth": "85%",
    }
    _BUBBLE_AI = {
        "fontSize": "12px", "color": "var(--ink)", "lineHeight": "1.4",
        "background": "rgba(47,183,196,0.08)",
        "borderRadius": "8px", "padding": "7px 10px",
        "alignSelf": "flex-start", "maxWidth": "85%",
    }

    bubbles = [html.Div(
        "Hola — pregúntame sobre tu rendimiento, bienestar o próxima competición.",
        style=_BUBBLE_AI,
    )]
    for turn in new_history:
        style = _BUBBLE_USER if turn["role"] == "user" else _BUBBLE_AI
        bubbles.append(html.Div(turn["content"], style=style))

    return bubbles, new_history, ""


# ── Sesión: carga lazy de la nota IA ──────────────────────────────────────────

@app.callback(
    Output("sesion-ai-note-output", "children"),
    Input("btn-sesion-ai-note", "n_clicks"),
    Input("url", "pathname"),
    prevent_initial_call=True,
)
def load_sesion_ai_note(n_ai, pathname):
    if pathname != "/sesion":
        raise PreventUpdate
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or ""
    if not uid or role != "deportista":
        raise PreventUpdate
    if callback_context.triggered_id != "btn-sesion-ai-note":
        return html.P(
            'Lectura lista para generarse. Pulsa "Generar lectura IA" cuando quieras el análisis del día.',
            className="text-muted",
            style={"fontSize": "13px"},
        )
    if not n_ai:
        raise PreventUpdate
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        raise PreventUpdate

    user  = db.get_user_by_id(uid_int)
    name  = (user or {}).get("name", "Atleta")
    sport = (user or {}).get("sport") or ""

    _athlete_report = {}
    try:
        import analysis_engine as _AE
        _athlete_report = _AE.full_report(uid_int, db)
    except Exception:
        traceback.print_exc()

    _rds = None
    try:
        _rds = db.get_readiness_score(uid_int)
    except Exception:
        pass

    _ai_extra = {}
    if _rds and _rds.get("days_to_comp") is not None:
        _ai_extra["competition"] = {"days_until": _rds["days_to_comp"]}

    try:
        import ai_insights as _AI
        note = _AI.generate_athlete_note(
            _athlete_report,
            athlete_name=name,
            sport=sport,
            extra=_ai_extra or None,
        )
    except Exception:
        traceback.print_exc()
        return html.P(
            "No se pudo cargar el módulo de análisis IA. Revisa los logs del servidor.",
            className="text-muted",
            style={"fontSize": "13px"},
        )

    if not note:
        return html.P(
            "No se generó análisis IA. Verifica que ANTHROPIC_API_KEY esté configurada en .env",
            className="text-muted",
            style={"fontSize": "13px"},
        )

    return dcc.Markdown(note, className="ai-note")


# ── Sesión coach: carga lazy del resumen de equipo ────────────────────────────

@app.callback(
    Output("sesion-team-ai-note", "children"),
    Input("btn-sesion-team-ai-note", "n_clicks"),
    Input("url", "pathname"),
    prevent_initial_call=True,
)
def load_sesion_team_ai_note(n_ai, pathname):
    if pathname != "/sesion":
        raise PreventUpdate
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or ""
    if not uid or role != "coach":
        raise PreventUpdate
    if callback_context.triggered_id != "btn-sesion-team-ai-note":
        return html.P(
            'Resumen listo para generarse. Pulsa "Generar resumen IA" cuando quieras abrir la lectura del equipo.',
            className="text-muted",
            style={"fontSize": "13px"},
        )
    if not n_ai:
        raise PreventUpdate
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        raise PreventUpdate

    sport = (_to_str(session.get("sport")) or "").strip()
    roster = _coach_roster(uid_int)
    roster_ids = [int(a.get("id")) for a in roster if a.get("id") is not None]

    athlete_count = len(roster)
    latest_checkins = 0
    athletes_with_ecg = 0
    focus_names: list = []
    pending_names: list = []

    try:
        qs_bulk = db.list_questionnaires_bulk(roster_ids) if roster_ids else {}
    except Exception:
        qs_bulk = {}
    try:
        ecg_bulk = db.get_last_ecg_metrics_bulk(roster_ids) if roster_ids else {}
    except Exception:
        ecg_bulk = {}

    for athlete in roster:
        aid = athlete.get("id")
        athlete_name = athlete.get("name") or "Deportista"
        if aid is None:
            continue
        has_checkin = bool(qs_bulk.get(int(aid), []))
        if has_checkin:
            latest_checkins += 1
        has_ecg = bool(ecg_bulk.get(int(aid)))
        if has_ecg:
            athletes_with_ecg += 1
        if (has_checkin or has_ecg) and len(focus_names) < 4:
            focus_names.append(athlete_name)
        if not has_checkin and len(pending_names) < 4:
            pending_names.append(athlete_name)

    _red_athletes: list = []
    try:
        _red_athletes = db.get_red_streak_athletes(
            uid_int, days=3, threshold=50.0, sport=sport or None
        ) if hasattr(db, "get_red_streak_athletes") else []
    except Exception:
        _red_athletes = []

    _today_date = datetime.utcnow().date()
    _upcoming_comps: list = []
    for _a in roster:
        _aid = _a.get("id")
        if not _aid:
            continue
        try:
            _nxt = db.get_next_competition(int(_aid))
            if _nxt:
                _ev_date = datetime.strptime(_nxt["event_date"][:10], "%Y-%m-%d").date()
                _days = (_ev_date - _today_date).days
                if 0 <= _days <= 60:
                    _upcoming_comps.append({
                        "name": _a.get("name", "Atleta"),
                        "event": _nxt.get("name", "Competencia"),
                        "date": _nxt["event_date"][:10],
                        "days": _days,
                    })
        except Exception:
            pass

    _team_data = {
        "athlete_count": athlete_count,
        "checkins": latest_checkins,
        "ecg_ready": athletes_with_ecg,
        "red_athletes": [a.get("name", "Atleta") for a in _red_athletes],
        "focus_names": focus_names,
        "pending_names": pending_names,
        "upcoming_comps": _upcoming_comps,
    }

    note = ""
    try:
        import ai_insights as _AI
        note = _AI.generate_team_summary(_team_data, sport=sport or "combate", coach_id=uid_int)
    except Exception:
        raise PreventUpdate

    if not note:
        return html.P(
            "Activa la integración AI añadiendo tu ANTHROPIC_API_KEY en el archivo .env para obtener el resumen diario del equipo.",
            className="text-muted",
            style={"fontSize": "13px"},
        )
    return dcc.Markdown(note, className="ai-note")


if __name__ == "__main__":
    import socket as _socket
    PORT = int(os.environ.get("PORT", 8051))
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

    # use_reloader=False evita el WinError 10038 en Python 3.13 + Windows.
    # Debug/hot reload quedan apagados por defecto para demo: reducen overlays,
    # sockets y trabajo extra del navegador. Activalos con COMBATIQ_DEBUG=1.
    if AUTO_OPEN:
        Timer(1.0, lambda: _open_browser_once(URL)).start()
    app.run(
        debug=COMBATIQ_DEBUG,
        host=HOST,
        port=PORT,
        use_reloader=False,
        threaded=True,
        dev_tools_hot_reload=False,
    )
