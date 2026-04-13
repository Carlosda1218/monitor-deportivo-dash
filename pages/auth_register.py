from dash import html, dcc, Input, Output, State, callback
from flask import session
import db

DEPORTES = ["Taekwondo","Judo","Kickboxing","Box","Muay Thai","MMA","Karate","Sambo"]
SYMS = "!@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~"


def strength_score(pw: str) -> int:
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


def strength_label_color(score: int):
    pct = int(score / 14 * 100) if score > 0 else 0
    if score <= 3:
        return "Muy débil", "#ff4d4d", pct
    if score <= 6:
        return "Débil", "#ff9f43", pct
    if score <= 9:
        return "Media", "#f1c40f", pct
    if score <= 12:
        return "Fuerte", "#2ecc71", pct
    return "Excelente", "#00f28a", pct


layout = html.Div(className="auth-wrap", children=[

    # Botón toggle de tema
    html.Button(id="btn-auth-theme-reg", className="auth-theme-btn", children="☀"),

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
                "Tu rendimiento,\n",
                html.Span("con contexto real"),
            ], className="auth-left__headline"),
            html.P(
                "Registra sesiones, monitorea bienestar y analiza tu carga semanal. "
                "Diseñado para atletas de combate y sus coaches.",
                className="auth-left__sub",
            ),
            html.Div(className="auth-left__features", children=[
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Perfil deportivo personalizado por deporte",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Cuestionario de bienestar adaptativo",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Historial de carga y tendencias en 4 semanas",
                ]),
                html.Div(className="auth-left__feature", children=[
                    html.Div(className="auth-left__feature-dot"),
                    "Roles separados para atleta y coach",
                ]),
            ]),
        ]),
        html.Div("© 2025 CombatIQ", className="auth-left__footer"),
    ]),

    # Panel derecho — Formulario
    html.Div(className="auth-right", children=[
        html.Div(className="auth-card", children=[

            html.H2("Crea tu cuenta", className="auth-title"),
            html.P("Empieza a monitorear rendimiento desde hoy.", className="auth-subtitle"),

        # Nombre + Correo en fila
        html.Div(className="auth-row", children=[
            html.Div(className="auth-field", children=[
                html.Label("Nombre completo"),
                dcc.Input(id="reg-name", type="text", placeholder="Tu nombre"),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Correo electrónico"),
                dcc.Input(id="reg-email", type="email", placeholder="tu@correo.com"),
            ]),
        ]),

        # Contraseña + fuerza
        html.Div(className="auth-field", children=[
            html.Label("Contraseña"),
            dcc.Input(id="reg-pass", type="password", placeholder="Mínimo 8 caracteres"),
            html.Div(className="pw-strength-track", children=[
                html.Div(id="pw-bar", className="pw-strength-bar",
                         style={"width": "0%", "background": "#2b3a52"}),
            ]),
            html.Div(id="pw-label", className="pw-strength-label"),
        ]),

        # Confirmar contraseña
        html.Div(className="auth-field", children=[
            html.Label("Confirmar contraseña"),
            dcc.Input(id="reg-pass2", type="password", placeholder="Repite la contraseña"),
        ]),

        # Rol + Deporte en fila
        html.Div(className="auth-row", children=[
            html.Div(className="auth-field", children=[
                html.Label("Rol"),
                dcc.Dropdown(
                    id="reg-role",
                    options=[
                        {"label": "Coach", "value": "coach"},
                        {"label": "Deportista", "value": "deportista"},
                    ],
                    placeholder="Selecciona…",
                    clearable=False,
                ),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Deporte / Arte marcial"),
                dcc.Dropdown(
                    id="reg-sport",
                    options=[{"label": d, "value": d} for d in DEPORTES]
                             + [{"label": "Otro", "value": "OTRA"}],
                    placeholder="Selecciona…",
                    clearable=False,
                ),
            ]),
        ]),

        # Deporte custom (condicional)
        html.Div(
            id="reg-sport-custom-box",
            style={"display": "none"},
            children=[
                html.Div(className="auth-field", children=[
                    html.Label("Especifica tu deporte"),
                    dcc.Input(id="reg-sport-custom", type="text",
                              placeholder="Ej: BJJ, Lucha olímpica, K-1…"),
                ]),
            ],
        ),

        html.Button("Crear cuenta", id="btn-register", className="auth-btn-primary"),
        html.Div(id="reg-msg", className="auth-msg"),
        html.Div(id="reg-redirect"),

        html.Div(className="auth-switch", children=[
            "¿Ya tienes cuenta? ", html.A("Inicia sesión", href="/login"),
        ]),
        ]),  # auth-card
    ]),      # auth-right
])


@callback(Output("pw-bar", "style"), Output("pw-label", "children"), Input("reg-pass", "value"))
def update_pw_strength(pw):
    score = strength_score(pw or "")
    label, color, pct = strength_label_color(score)
    style = {
        "height": "8px",
        "width": f"{pct}%",
        "borderRadius": "8px",
        "background": color,
        "transition": "width .25s, background .25s",
    }
    hint = " Sugerencia: usa 8+ caracteres con mayúsculas, minúsculas, números y símbolo." if score < 7 else ""
    return style, f"{label} ({pct}%) {hint}"


@callback(Output("reg-sport-custom-box", "style"), Input("reg-sport", "value"))
def toggle_custom_sport(selected):
    return {"display": "block", "marginTop": "8px"} if selected == "OTRA" else {"display": "none"}


@callback(
    Output("reg-msg", "children"),
    Output("reg-redirect", "children"),
    Input("btn-register", "n_clicks"),
    State("reg-name", "value"),
    State("reg-email", "value"),
    State("reg-pass", "value"),
    State("reg-pass2", "value"),
    State("reg-role", "value"),
    State("reg-sport", "value"),
    State("reg-sport-custom", "value"),
    prevent_initial_call=True
)
def do_register(n, name, email, pw, pw2, role, sport, sport_custom):
    if not all([name, email, pw, pw2, role]):
        return "Falta completar campos obligatorios (nombre, correo, contraseña, confirmar, rol).", None

    if not sport:
        return "Selecciona deporte (o 'Otro').", None

    if sport == "OTRA":
        if not (sport_custom and str(sport_custom).strip()):
            return "Seleccionaste 'Otro'. Especifica tu deporte / especialidad.", None
        sport = str(sport_custom).strip()

    if pw != pw2:
        return "Las contraseñas no coinciden.", None

    if strength_score(pw) < 7:
        return "Contraseña demasiado débil. Usa 8+ caracteres con mayúsculas, minúsculas, números y símbolo.", None

    email_clean = (email or "").strip()

    try:
        db.create_user(name, email_clean, pw, role, sport)
    except Exception as e:
        return f"Error: {e}", None

    user = db.get_user_by_email(email_clean)
    session["user_id"] = user["id"]
    session["role"] = user["role"] or "deportista"
    session["name"] = user["name"] or ""
    session["sport"] = user["sport"] or ""

    return "", dcc.Location(pathname="/dashboard", id="redirect-register")
