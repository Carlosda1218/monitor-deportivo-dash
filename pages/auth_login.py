from dash import html, dcc, Input, Output, State, callback
from flask import session
import hashlib
import random
import db

def _check_pw(plain: str, stored):
    try:
        import bcrypt
        if isinstance(stored, str):
            stored = stored.encode("utf-8")
        return bcrypt.checkpw((plain or "").encode("utf-8"), stored)
    except Exception:
        # ✅ Fallback: SHA256 (compatible con db.py)
        if stored is None:
            return False
        if isinstance(stored, (bytes, bytearray)):
            stored_bytes = bytes(stored)
        else:
            stored_bytes = str(stored).encode("utf-8", "ignore")

        candidate = hashlib.sha256((plain or "").encode("utf-8")).hexdigest().encode("utf-8")
        return candidate == stored_bytes

layout = html.Div(className="auth-wrap", children=[

    # Botón toggle de tema
    html.Button(id="btn-auth-theme", className="auth-theme-btn", children="☀"),

    # Panel izquierdo — Branding
    html.Div(className="auth-left", children=[
        html.Div(className="auth-left__brand", children=[
            html.Div(className="auth-left__mark", children=[
                html.Img(src="/assets/logo_powersync.svg", className="auth-left__logo"),
            ]),
            html.Div([
                html.Div("CombatIQ", className="auth-left__name"),
                html.Div("Combat Sports Performance", className="auth-left__tag"),
            ]),
        ]),
        html.Div(className="auth-left__body", children=[
            html.H2([
                "Monitoreo de\nrendimiento para ",
                html.Span("deportes de combate"),
            ], className="auth-left__headline"),
            html.P(
                "Carga, bienestar y análisis cardiovascular en un solo lugar. "
                "Para atletas y coaches que toman decisiones con datos.",
                className="auth-left__sub",
            ),
            html.Div(className="auth-left__features", children=[
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Semáforo semanal de carga y bienestar",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Análisis ECG / HRV por sesión",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Vista de equipo para coaches — por deporte",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Taekwondo y Boxeo — análisis especializado por deporte",
                ]),
            ]),
        ]),
        html.Div("© 2025 CombatIQ", className="auth-left__footer"),
    ]),

    # Panel derecho — Formulario
    html.Div(className="auth-right", children=[
        html.Div(className="auth-card", children=[

            html.H2("Bienvenido de vuelta", className="auth-title"),
            html.P("Inicia sesión para acceder a tu panel.", className="auth-subtitle"),

            html.Div(className="auth-field", children=[
                html.Label("Correo electrónico"),
                dcc.Input(id="login-email", type="email", placeholder="tu@correo.com"),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Contraseña"),
                dcc.Input(id="login-pass", type="password", placeholder="••••••••"),
            ]),

            html.Div(className="auth-remember", children=[
                html.Div(className="auth-remember-left", children=[
                    dcc.Checklist(
                        id="login-remember",
                        options=[{"label": " Recordarme", "value": "remember"}],
                        value=[],
                    ),
                ]),
                html.A("¿Olvidaste tu contraseña?", href="#", className="auth-remember-link"),
            ]),

            html.Button("Entrar", id="btn-login", className="auth-btn-primary"),
            html.Div(id="login-msg", className="auth-msg"),
            html.Div(id="login-redirect"),

            html.Div(className="auth-switch", children=[
                "¿No tienes cuenta? ", html.A("Crear cuenta", href="/registro"),
            ]),

            html.Div(className="auth-demo", children=[
                html.Div("Explorar sin cuenta", className="auth-demo__title"),
                html.Div(className="auth-demo__pills", children=[
                    html.Button("Atleta\nTaekwondo", id="btn-demo-login", className="auth-demo__pill"),
                    html.Button("Coach\nTaekwondo", id="btn-demo-coach-login", className="auth-demo__pill"),
                    html.Button("Coach\nBoxeo", id="btn-demo-coach-boxeo-login", className="auth-demo__pill"),
                ]),
                html.Div("Datos de ejemplo reales · sin registro", className="auth-demo__hint"),
                html.Div(id="demo-redirect"),
                html.Div(id="demo-coach-redirect"),
                html.Div(id="demo-coach-boxeo-redirect"),
            ]),
        ]),
    ]),
])

@callback(
    Output("login-msg","children"),
    Output("login-redirect","children"),
    Input("btn-login","n_clicks"),
    State("login-email","value"),
    State("login-pass","value"),
    prevent_initial_call=True
)
def do_login(n, email, pw):
    if not email or not pw:
        return "Completa usuario y contraseña.", None

    user = db.get_user_by_email(email)
    if not user:
        return "Usuario o contraseña incorrectos.", None

    stored = user.get("password_hash")
    if not _check_pw(pw, stored):
        return "Usuario o contraseña incorrectos.", None

    session["user_id"] = user["id"]
    session["role"] = user.get("role") or "coach"
    session["name"] = user.get("name") or ""
    session["sport"] = user.get("sport") or ""
    session["quote_idx"] = random.randint(0, 99)

    return "", dcc.Location(pathname="/dashboard", id="redirect-login")
