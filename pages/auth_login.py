import time
import hashlib
import hmac
import random
from collections import defaultdict

from dash import html, dcc, Input, Output, State, callback
from flask import session, request as _flask_request
import db

# ── Rate limiting (in-memory, por IP) ────────────────────────────────────────
_LOGIN_MAX   = 5          # intentos fallidos permitidos
_LOGIN_WIN   = 15 * 60   # ventana de 15 minutos

_login_attempts: dict = defaultdict(list)


def _rate_check(ip: str) -> tuple:
    """Devuelve (bloqueado, segundos_restantes)."""
    now    = time.monotonic()
    cutoff = now - _LOGIN_WIN
    recent = [t for t in _login_attempts[ip] if t > cutoff]
    _login_attempts[ip] = recent
    if len(recent) >= _LOGIN_MAX:
        wait = int(_LOGIN_WIN - (now - min(recent))) + 1
        return True, wait
    return False, 0


def _rate_record(ip: str) -> None:
    _login_attempts[ip].append(time.monotonic())


def _rate_clear(ip: str) -> None:
    _login_attempts.pop(ip, None)

def _check_pw(plain: str, stored):
    # PostgreSQL BYTEA llega como memoryview; SQLite BLOB llega como bytes
    if stored is None:
        return False
    if isinstance(stored, memoryview):
        stored = bytes(stored)
    elif isinstance(stored, bytearray):
        stored = bytes(stored)
    elif isinstance(stored, str):
        stored = stored.encode("utf-8")
    try:
        import bcrypt
        return bcrypt.checkpw((plain or "").encode("utf-8"), stored)
    except Exception:
        # Fallback: PBKDF2 actual de db.py y SHA256 legacy.
        stored_bytes = stored if isinstance(stored, bytes) else str(stored).encode("utf-8", "ignore")

        if stored_bytes.startswith(b"pbkdf2:"):
            try:
                _, salt_hex, dk_hex = stored_bytes.split(b":")
                salt = bytes.fromhex(salt_hex.decode())
                expected = bytes.fromhex(dk_hex.decode())
                candidate = hashlib.pbkdf2_hmac(
                    "sha256",
                    (plain or "").encode("utf-8"),
                    salt,
                    260_000,
                )
                return hmac.compare_digest(candidate, expected)
            except Exception:
                return False

        candidate = hashlib.sha256((plain or "").encode("utf-8")).hexdigest().encode("utf-8")
        return hmac.compare_digest(candidate, stored_bytes)

layout = html.Div(className="auth-wrap", children=[

    # Botón toggle de tema
    html.Button(
        id="btn-auth-theme",
        className="auth-theme-btn",
        children="☀",
        **{"aria-label": "Cambiar tema claro / oscuro"},
    ),

    # Panel izquierdo — Branding
    html.Div(className="auth-left", **{"role": "complementary", "aria-label": "Información de CombatIQ"}, children=[
        html.Div(className="auth-left__brand", children=[
            html.Div(className="auth-left__mark", children=[
                html.Img(src="/assets/logo_combatiq.svg", className="auth-left__logo"),
            ]),
            html.Div([
                html.Div("CombatIQ", className="auth-left__name"),
                html.Div("Combat Sports Performance", className="auth-left__tag"),
            ]),
        ]),
        html.Div(className="auth-left__body", children=[
            html.H2([
                "Monitoreo de\nrendimiento para ",
                html.Span("taekwondo y boxeo"),
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
        html.Div("© 2026 CombatIQ", className="auth-left__footer"),
    ]),

    # Panel derecho — Formulario
    html.Div(className="auth-right", **{"role": "main"}, children=[
        html.Div(className="auth-card", children=[

            html.H2("Bienvenido de vuelta", className="auth-title"),
            html.P("Inicia sesión para acceder a tu panel.", className="auth-subtitle"),

            html.Div(className="auth-field", children=[
                html.Label("Correo electrónico", htmlFor="login-email"),
                dcc.Input(
                    id="login-email",
                    type="email",
                    placeholder="tu@correo.com",
                    autoComplete="email",
                    required=True,
                ),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Contraseña", htmlFor="login-pass"),
                dcc.Input(
                    id="login-pass",
                    type="password",
                    placeholder="••••••••",
                    autoComplete="current-password",
                    required=True,
                ),
            ]),

            html.Div(className="auth-remember", children=[
                html.Div(className="auth-remember-left", children=[
                    dcc.Checklist(
                        id="login-remember",
                        options=[{"label": " Recordarme", "value": "remember"}],
                        value=[],
                    ),
                ]),
                dcc.Link("¿Olvidaste tu contraseña?", href="/recuperar-password", className="auth-remember-link"),
            ]),

            html.Button(
                "Entrar",
                id="btn-login",
                className="auth-btn-primary",
                type="submit",
                **{"aria-label": "Iniciar sesión"},
            ),
            html.Div(
                id="login-msg",
                className="auth-msg",
                **{"role": "status", "aria-live": "polite"},
            ),
            html.Div(id="login-redirect"),

            html.Div(className="auth-switch", children=[
                "¿No tienes cuenta? ", dcc.Link("Crear cuenta", href="/registro"),
            ]),

            html.Div(className="auth-demo", **{"role": "region", "aria-label": "Acceso a cuentas demo"}, children=[
                html.Div("Explorar sin cuenta", className="auth-demo__title"),
                html.Div(className="auth-demo__pills", children=[
                    html.A(
                        "Atleta\nTaekwondo",
                        id="btn-demo-login",
                        href="/demo/atleta",
                        className="auth-demo__pill",
                        **{"aria-label": "Entrar como atleta demo de Taekwondo"},
                    ),
                    html.A(
                        "Coach\nTaekwondo",
                        id="btn-demo-coach-login",
                        href="/demo/coach-tkd",
                        className="auth-demo__pill",
                        **{"aria-label": "Entrar como coach demo de Taekwondo"},
                    ),
                    html.A(
                        "Coach\nBoxeo",
                        id="btn-demo-coach-boxeo-login",
                        href="/demo/coach-boxeo",
                        className="auth-demo__pill",
                        **{"aria-label": "Entrar como coach demo de Boxeo"},
                    ),
                ]),
                html.Div("Datos realistas de ejemplo · sin registro", className="auth-demo__hint"),
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
    State("login-remember","value"),
    prevent_initial_call=True
)
def do_login(n, email, pw, remember):
    ip = (_flask_request.remote_addr or "unknown")
    blocked, wait = _rate_check(ip)
    if blocked:
        mins = wait // 60
        secs = wait % 60
        wait_str = f"{mins} min {secs} s" if mins else f"{secs} s"
        return f"Demasiados intentos fallidos. Espera {wait_str}.", None

    if not email or not pw:
        return "Completa usuario y contraseña.", None

    user = db.get_user_by_email(email)
    if not user:
        _rate_record(ip)
        return "Usuario o contraseña incorrectos.", None

    stored = user.get("password_hash")
    if not _check_pw(pw, stored):
        _rate_record(ip)
        return "Usuario o contraseña incorrectos.", None

    _rate_clear(ip)
    role = user.get("role") or "coach"
    if role not in {"deportista", "coach", "admin", "inversor"}:
        return "Rol no disponible en esta versión de la app.", None

    session.permanent = ("remember" in (remember or []))
    session["user_id"] = user["id"]
    session["role"] = role
    session["name"] = user.get("name") or ""
    session["sport"] = user.get("sport") or ""
    session["quote_idx"] = random.randint(0, 99)

    if not user.get("onboarding_done"):
        dest = "/onboarding"
    else:
        dest = "/dashboard"
    return "", dcc.Location(pathname=dest, id="redirect-login")
