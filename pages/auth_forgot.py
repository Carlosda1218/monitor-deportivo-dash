import os
import time
from collections import defaultdict

from dash import html, dcc, Input, Output, State, callback
from flask import session, request as _flask_request

import db


_RESET_MAX = 5
_RESET_WIN = 15 * 60
_reset_attempts: dict = defaultdict(list)

SYMS = "!@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~"


def _rate_check(ip: str) -> tuple:
    now = time.monotonic()
    recent = [t for t in _reset_attempts[ip] if t > now - _RESET_WIN]
    _reset_attempts[ip] = recent
    if len(recent) >= _RESET_MAX:
        wait = int(_RESET_WIN - (now - min(recent))) + 1
        return True, wait
    return False, 0


def _rate_record(ip: str) -> None:
    _reset_attempts[ip].append(time.monotonic())


def _strength_score(pw: str) -> int:
    if not pw:
        return 0
    length = len(pw)
    has_lower = any(c.islower() for c in pw)
    has_upper = any(c.isupper() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_sym = any(c in SYMS for c in pw)
    classes = sum([has_lower, has_upper, has_digit, has_sym])
    score = min(6, length // 2) + classes * 2
    if length >= 12:
        score += 2
    return min(score, 14)


def _show_local_reset_code() -> bool:
    if os.environ.get("COMBATIQ_SHOW_RESET_TOKEN") == "1":
        return True
    return os.environ.get("POWERSYNC_SECRET", "dev-secret-change-me") == "dev-secret-change-me"


def _local_code_box(token: str, expires_at: str):
    if not token or not _show_local_reset_code():
        return None
    return html.Div(
        className="auth-reset-code",
        children=[
            html.Div("Código temporal local", style={"fontWeight": "800", "marginBottom": "6px"}),
            html.Code(token, style={"fontSize": "13px", "wordBreak": "break-all"}),
            html.P(
                f"Válido hasta {expires_at[:16].replace('T', ' ')}. "
                "En producción este código debe enviarse por correo, no mostrarse en pantalla.",
                className="text-muted",
                style={"fontSize": "12px", "margin": "8px 0 0"},
            ),
        ],
        style={
            "border": "1px solid rgba(47,183,196,.35)",
            "borderRadius": "12px",
            "padding": "12px",
            "marginTop": "10px",
            "background": "rgba(47,183,196,.08)",
        },
    )


layout = html.Div(className="auth-wrap", children=[
    html.Button(id="btn-auth-theme-reset", className="auth-theme-btn", children="☀"),

    html.Div(className="auth-left", children=[
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
                "Recupera el acceso\n",
                html.Span("sin perder tu historial"),
            ], className="auth-left__headline"),
            html.P(
                "Protegemos tus sesiones, métricas y datos de rendimiento. "
                "Usa un código temporal para definir una contraseña nueva.",
                className="auth-left__sub",
            ),
            html.Div(className="auth-left__features", children=[
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Token temporal con caducidad",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "No revelamos si un correo existe",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Cambio de contraseña con hash seguro",
                ]),
            ]),
        ]),
        html.Div("© 2026 CombatIQ", className="auth-left__footer"),
    ]),

    html.Div(className="auth-right", children=[
        html.Div(className="auth-card", children=[
            html.H2("Restablecer contraseña", className="auth-title"),
            html.P(
                "Escribe tu correo y solicita un código temporal. Después define tu nueva contraseña.",
                className="auth-subtitle",
            ),

            html.Div(className="auth-field", children=[
                html.Label("Correo electrónico"),
                dcc.Input(id="forgot-email", type="email", placeholder="tu@correo.com"),
            ]),
            html.Button("Enviar código", id="btn-forgot-request", className="auth-btn-primary"),
            html.Div(id="forgot-request-msg", className="auth-msg"),
            html.Div(id="forgot-local-code"),

            html.Div(style={"height": "16px"}),
            html.Div(className="auth-field", children=[
                html.Label("Código temporal"),
                dcc.Input(id="forgot-token", type="text", placeholder="Pega aquí el código recibido"),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Nueva contraseña"),
                dcc.Input(id="forgot-pass", type="password", placeholder="Mínimo 8 caracteres"),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Confirmar nueva contraseña"),
                dcc.Input(id="forgot-pass2", type="password", placeholder="Repite la contraseña"),
            ]),
            html.Button("Cambiar contraseña", id="btn-forgot-reset", className="auth-btn-primary"),
            html.Div(id="forgot-reset-msg", className="auth-msg"),
            html.Div(id="forgot-redirect"),

            html.Div(className="auth-switch", children=[
                "¿Recordaste tu contraseña? ", dcc.Link("Inicia sesión", href="/login"),
            ]),
        ]),
    ]),
])


@callback(
    Output("forgot-request-msg", "children"),
    Output("forgot-local-code", "children"),
    Input("btn-forgot-request", "n_clicks"),
    State("forgot-email", "value"),
    prevent_initial_call=True,
)
def request_reset(n_clicks, email):
    if not email:
        return "Escribe tu correo para solicitar el código.", None

    ip = (_flask_request.remote_addr or "unknown")
    blocked, wait = _rate_check(ip)
    if blocked:
        mins = wait // 60
        secs = wait % 60
        wait_str = f"{mins} min {secs} s" if mins else f"{secs} s"
        return f"Demasiadas solicitudes. Espera {wait_str}.", None

    _rate_record(ip)
    info = db.create_password_reset_token(email, request_ip=ip)
    msg = (
        "Si el correo existe en CombatIQ, generamos un código temporal para "
        "restablecer la contraseña."
    )
    return msg, _local_code_box(info.get("token"), info.get("expires_at", ""))


@callback(
    Output("forgot-reset-msg", "children"),
    Output("forgot-redirect", "children"),
    Input("btn-forgot-reset", "n_clicks"),
    State("forgot-email", "value"),
    State("forgot-token", "value"),
    State("forgot-pass", "value"),
    State("forgot-pass2", "value"),
    prevent_initial_call=True,
)
def reset_password(n_clicks, email, token, pw, pw2):
    if not all([email, token, pw, pw2]):
        return "Completa correo, código y nueva contraseña.", None
    if pw != pw2:
        return "Las contraseñas no coinciden.", None
    if _strength_score(pw) < 7:
        return "Contraseña demasiado débil. Usa 8+ caracteres con mayúsculas, minúsculas, números y símbolo.", None

    ok = db.reset_password_with_token(email, token, pw)
    if not ok:
        return "Código inválido o caducado. Solicita uno nuevo e inténtalo otra vez.", None

    session.clear()
    return "Contraseña actualizada. Redirigiendo a inicio de sesión...", dcc.Location(
        pathname="/login",
        id="redirect-forgot-login",
    )
