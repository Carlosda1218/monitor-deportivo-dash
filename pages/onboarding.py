"""
CombatIQ — Onboarding wizard (pasos 1-4)
Se muestra una sola vez después del registro.
Flujo diferenciado para coach vs atleta.
"""

from dash import html, dcc, Input, Output, State, callback, no_update
from dash.exceptions import PreventUpdate
from flask import session
import db

_STEPS = 4

_ATHLETE_LABELS = ["Bienvenida", "Tu perfil",  "Check-in",       "Listo"]
_COACH_LABELS   = ["Bienvenida", "Tu equipo",  "Panel de coach", "Listo"]


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v


# ── Step indicator ────────────────────────────────────────────────────────────

def _step_indicator(current: int, is_coach: bool = False) -> html.Div:
    labels = _COACH_LABELS if is_coach else _ATHLETE_LABELS
    dots = []
    for i, lbl in enumerate(labels, start=1):
        active = i == current
        done   = i < current
        cls    = "ob-dot ob-dot--done" if done else ("ob-dot ob-dot--active" if active else "ob-dot")
        dots.append(html.Div(className="ob-step", children=[
            html.Div(className=cls, children="✓" if done else str(i)),
            html.Span(lbl, className="ob-step-lbl" + (" ob-step-lbl--active" if active else "")),
        ]))
        if i < _STEPS:
            dots.append(html.Div(className="ob-connector" + (" ob-connector--done" if done else "")))
    return html.Div(className="ob-stepper", children=dots)


# ── Contenido de cada paso ───────────────────────────────────────────────────

def _step1_content(name: str, sport: str, role: str) -> html.Div:
    is_coach = role == "coach"
    sport_key = sport.lower() if sport else ""
    is_tkd    = "taekwondo" in sport_key or "tkd" in sport_key
    is_box    = "box" in sport_key

    if is_coach:
        if is_tkd:
            eyebrow  = "⚡ Plataforma de seguimiento para entrenadores de Taekwondo"
            subtitle = "Monitoriza el bienestar, carga y readiness de cada uno de tus atletas en tiempo real."
        elif is_box:
            eyebrow  = "🥊 Plataforma de seguimiento para entrenadores de Boxeo"
            subtitle = "Controla el estado de tu equipo, detecta fatiga y toma decisiones antes de cada sparring."
        else:
            eyebrow  = "🏆 Plataforma de seguimiento para entrenadores de combate"
            subtitle = "Monitoriza el bienestar, carga y readiness de cada uno de tus atletas."
        features = [
            "Panel de equipo con readiness diario",
            "Alertas de sobreentrenamiento y fatiga",
            "Comunicación directa con cada atleta",
            "Monitor de combate ECG/IMU en tiempo real",
        ]
    else:
        if is_tkd:
            eyebrow  = "⚡ Plataforma de rendimiento para Taekwondo de competición"
            subtitle = "Registra tus check-ins, controla el peso de combate y analiza tu ECG antes de cada torneo."
        elif is_box:
            eyebrow  = "🥊 Plataforma de rendimiento para Boxeo de competición"
            subtitle = "Lleva el control de tu bienestar, tu peso y tu carga de sparring desde un solo lugar."
        else:
            eyebrow  = "🏆 Plataforma de rendimiento para deportes de combate"
            subtitle = "Monitorea tu bienestar, peso y carga de entrenamiento desde un solo lugar."
        features = [
            "Check-in de bienestar diario",
            "Control de peso y categoría",
            "Análisis ECG / HRV / IMU",
            "Comunicación directa con tu coach",
        ]

    return html.Div(className="ob-step-content", children=[
        html.Div(className="ob-welcome-icon", children="👋"),
        html.H2(f"Bienvenido/a, {name}.", className="ob-title"),
        html.P(eyebrow,  className="ob-eyebrow"),
        html.P(subtitle, className="ob-subtitle"),
        html.Div(className="ob-feature-list", children=[
            html.Div(className="ob-feature", children=[
                html.Span("✓", className="ob-feature-check"),
                html.Span(f),
            ]) for f in features
        ]),
    ])


def _step2_content(sport: str, role: str) -> html.Div:
    if role == "coach":
        sport_key = sport.lower() if sport else ""
        is_tkd    = "taekwondo" in sport_key or "tkd" in sport_key
        if is_tkd:
            tip = "Un equipo de TKD bien monitorizado llega al torneo en el momento de forma óptimo."
        else:
            tip = "El coach que lleva registro de bienestar detecta el sobreentrenamiento antes de que afecte al sparring."
        return html.Div(className="ob-step-content", children=[
            html.Div(className="ob-welcome-icon", children="👥"),
            html.H2("Gestiona a tu equipo", className="ob-title"),
            html.P("Desde CombatIQ puedes añadir y monitorizar a todos tus atletas en un solo lugar.",
                   className="ob-subtitle"),
            html.Div(className="ob-feature-list", children=[
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Ve a la sección 'Equipo' para añadir atletas por email"),
                ]),
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Cada atleta registrado aparece en tu panel con su estado diario"),
                ]),
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Recibirás alertas cuando un atleta reporta fatiga alta o molestia"),
                ]),
            ]),
            html.Div(className="ob-tip-card", children=[
                html.Span("💡", style={"fontSize": "18px"}),
                html.P(tip, className="text-muted text-xs", style={"margin": 0}),
            ]),
        ])
    else:
        return html.Div(className="ob-step-content", children=[
            html.H2("Completa tu perfil deportivo", className="ob-title"),
            html.P(
                "Esta información adapta todas las recomendaciones y alertas a tu realidad competitiva.",
                className="ob-subtitle",
            ),
            html.Div(id="ob-step2-msg", className="text-muted text-xs",
                     style={"marginTop": "8px"}),
        ])


def _step3_content(sport: str, role: str) -> html.Div:
    is_coach  = role == "coach"
    sport_key = sport.lower() if sport else ""
    is_tkd    = "taekwondo" in sport_key or "tkd" in sport_key

    if is_coach:
        if is_tkd:
            tip = "El Home del coach muestra el estado de readiness de todo el equipo antes de cada entrenamiento."
        else:
            tip = "Con el semáforo diario sabes de un vistazo quién puede entrenar fuerte y quién necesita recuperar."
        return html.Div(className="ob-step-content", children=[
            html.Div(className="ob-welcome-icon", children="📊"),
            html.H2("Tu panel de seguimiento", className="ob-title"),
            html.P("El Home del coach es tu centro de mando. Esto es lo que encontrarás:",
                   className="ob-subtitle"),
            html.Div(className="ob-feature-list", children=[
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Semáforo de readiness por atleta (verde / amarillo / rojo)"),
                ]),
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Historial de check-ins y tendencias de bienestar del equipo"),
                ]),
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Acceso al monitor de combate ECG/IMU en tiempo real"),
                ]),
                html.Div(className="ob-feature", children=[
                    html.Span("→", className="ob-feature-check"),
                    html.Span("Chat integrado con cada atleta"),
                ]),
            ]),
            html.Div(className="ob-tip-card", children=[
                html.Span("💡", style={"fontSize": "18px"}),
                html.P(tip, className="text-muted text-xs", style={"margin": 0}),
            ]),
        ])
    else:
        if is_tkd:
            tip = "Los atletas de TKD que registran su bienestar cada mañana llegan mejor a los Kumite de competición."
        else:
            tip = "Los boxeadores que llevan un registro de bienestar detectan el sobreentrenamiento antes de que afecte al sparring."
        return html.Div(className="ob-step-content", children=[
            html.Div(className="ob-welcome-icon", children="📋"),
            html.H2("Haz tu primer check-in ahora", className="ob-title"),
            html.P("El check-in diario es el corazón de CombatIQ. Tarda menos de 2 minutos.",
                   className="ob-subtitle"),
            html.Div(className="ob-tip-card", children=[
                html.Span("💡", style={"fontSize": "18px"}),
                html.P(tip, className="text-muted text-xs", style={"margin": 0}),
            ]),
            html.P("Puedes hacerlo ahora o más tarde desde el menú lateral.",
                   className="text-muted text-xs", style={"marginTop": "12px"}),
        ])


def _step4_content(sport: str, role: str) -> html.Div:
    is_coach  = role == "coach"
    sport_key = sport.lower() if sport else ""

    if is_coach:
        next_action = "Ir a mi panel"
        tip         = "Cuando tus atletas estén registrados verás su estado aquí cada mañana."
        items = [
            "Añadir atletas desde la sección 'Equipo'",
            "Revisar el Home cada mañana antes del entrenamiento",
            "Lanzar el Monitor de Combate en la sección 'Señales'",
        ]
    else:
        next_action = "Ver mi dashboard"
        tip         = "Regresa cada mañana antes del entrenamiento para registrar cómo llegas."
        items = [
            "Explorar el Home para ver el resumen del día",
            "Hacer el check-in de bienestar en 'Wellbeing'",
            "Subir un ECG en 'Señales ECG / IMU'",
        ]

    return html.Div(className="ob-step-content", children=[
        html.Div(className="ob-welcome-icon", children="🎯"),
        html.H2("Ya estás listo/a.", className="ob-title"),
        html.P("CombatIQ está configurado. Esto es lo que puedes hacer ahora:",
               className="ob-subtitle"),
        html.Div(className="ob-feature-list", children=[
            html.Div(className="ob-feature", children=[
                html.Span("→", className="ob-feature-check"),
                html.Span(item),
            ]) for item in items
        ]),
        html.P(tip, className="ob-tip-card"),
        dcc.Link(
            html.Button(next_action, className="btn btn-primary ob-final-btn"),
            href="/dashboard",
            id="ob-final-link",
        ),
    ])


# ── Layout principal ─────────────────────────────────────────────────────────

def layout():
    uid   = session.get("user_id")
    role  = _to_str(session.get("role"))  or ""
    name  = _to_str(session.get("name"))  or "Deportista"
    sport = _to_str(session.get("sport")) or ""

    if not uid:
        return dcc.Location(pathname="/login", id="ob-redirect-login")

    try:
        user = db.get_user_by_id(int(uid))
        if user and user.get("onboarding_done"):
            return dcc.Location(pathname="/dashboard", id="ob-redirect-done")
    except Exception:
        pass

    existing_profile = {}
    try:
        existing_profile = db.get_athlete_profile(int(uid)) or {}
    except Exception:
        pass

    is_coach  = (role == "coach")
    sport_key = sport.lower()
    is_tkd    = "taekwondo" in sport_key or "tkd" in sport_key

    cat_options_tkd = [
        {"label": "-54 kg", "value": "-54 kg"},
        {"label": "-58 kg", "value": "-58 kg"},
        {"label": "-63 kg", "value": "-63 kg"},
        {"label": "-68 kg", "value": "-68 kg"},
        {"label": "-74 kg", "value": "-74 kg"},
        {"label": "-80 kg", "value": "-80 kg"},
        {"label": "-87 kg", "value": "-87 kg"},
        {"label": "+87 kg", "value": "+87 kg"},
    ]
    cat_options_box = [
        {"label": "Minimosca -49 kg", "value": "-49 kg"},
        {"label": "Mosca -52 kg",     "value": "-52 kg"},
        {"label": "Gallo -56 kg",     "value": "-56 kg"},
        {"label": "Pluma -60 kg",     "value": "-60 kg"},
        {"label": "Ligero -64 kg",    "value": "-64 kg"},
        {"label": "Welter -69 kg",    "value": "-69 kg"},
        {"label": "Medio -75 kg",     "value": "-75 kg"},
        {"label": "Semipesado -81 kg","value": "-81 kg"},
        {"label": "Pesado +81 kg",    "value": "+81 kg"},
    ]
    cat_options = cat_options_tkd if is_tkd else cat_options_box

    # The form fields MUST always be present in the DOM (even for coaches) because
    # the navigate_steps callback uses them in State. Coaches see them hidden always.
    form_area = html.Div(
        id="ob-form-area",
        className="ob-form-grid",
        style={"display": "none"},
        children=[
            html.Div(className="auth-field", children=[
                html.Label("Nivel competitivo"),
                dcc.Dropdown(
                    id="ob-level",
                    options=[
                        {"label": "Iniciación",         "value": "Iniciación"},
                        {"label": "Intermedio",         "value": "Intermedio"},
                        {"label": "Competitivo",        "value": "Competitivo"},
                        {"label": "Alto rendimiento",   "value": "Alto rendimiento"},
                    ],
                    value=existing_profile.get("competitive_level"),
                    placeholder="Selecciona tu nivel...",
                    clearable=False,
                ),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Categoría de peso"),
                dcc.Dropdown(
                    id="ob-category",
                    options=cat_options,
                    value=existing_profile.get("weight_category"),
                    placeholder="Selecciona categoría...",
                    clearable=False,
                ),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Lado dominante"),
                dcc.Dropdown(
                    id="ob-side",
                    options=[
                        {"label": "Derecho / Ortodoxo", "value": "Derecho"},
                        {"label": "Izquierdo / Zurdo",  "value": "Izquierdo"},
                        {"label": "Mixto / Switch",     "value": "Mixto"},
                    ],
                    value=existing_profile.get("dominant_side"),
                    placeholder="Selecciona...",
                    clearable=False,
                ),
            ]),
            html.Div(className="auth-field", children=[
                html.Label("Cercanía a competencia"),
                dcc.Dropdown(
                    id="ob-proximity",
                    options=[
                        {"label": "Sin competencia cercana", "value": "Sin competencia cercana"},
                        {"label": "Próximas 6-8 semanas",    "value": "Próximas 6-8 semanas"},
                        {"label": "Próximas 3-4 semanas",    "value": "Próximas 3-4 semanas"},
                        {"label": "Semana competitiva",      "value": "Semana competitiva"},
                    ],
                    value=existing_profile.get("competition_proximity", "Sin competencia cercana"),
                    clearable=False,
                ),
            ]),
        ],
    )

    return html.Div(className="ob-shell", children=[
        dcc.Store(id="ob-step-store", data=1),

        html.Div(className="ob-header", children=[
            html.Span("CombatIQ", className="ob-logo"),
        ]),

        html.Div(className="ob-card", children=[
            html.Div(id="ob-stepper-container",
                     children=_step_indicator(1, is_coach)),

            html.Div(className="ecg-divider", style={"margin": "20px 0"}),

            html.Div(id="ob-content",
                     children=_step1_content(name, sport, role)),

            # Always-present form fields — shown only on athlete step 2
            form_area,

            html.Div(className="ecg-divider", style={"margin": "20px 0"}),

            html.Div(className="ob-nav", children=[
                html.Button("Atrás", id="ob-back-btn", className="btn btn-ghost",
                            style={"visibility": "hidden"}, n_clicks=0),
                html.Button("Siguiente →", id="ob-next-btn",
                            className="btn btn-primary", n_clicks=0),
                html.Div(id="ob-skip-row", children=[
                    html.Span("o ", style={"color": "var(--muted)", "fontSize": "13px"}),
                    html.Button("completar después", id="ob-skip-btn",
                                className="btn-link-muted", n_clicks=0),
                ]),
            ]),

            html.Div(id="ob-redirect-container"),
        ]),

        dcc.Store(id="ob-profile-store", data={}),
    ])


# ── Callbacks ────────────────────────────────────────────────────────────────

@callback(
    Output("ob-step-store",         "data"),
    Output("ob-stepper-container",  "children"),
    Output("ob-content",            "children"),
    Output("ob-form-area",          "style"),
    Output("ob-next-btn",           "children"),
    Output("ob-back-btn",           "style"),
    Output("ob-skip-row",           "style"),
    Output("ob-redirect-container", "children"),
    Output("ob-profile-store",      "data"),
    Input("ob-next-btn",  "n_clicks"),
    Input("ob-back-btn",  "n_clicks"),
    Input("ob-skip-btn",  "n_clicks"),
    State("ob-step-store",    "data"),
    State("ob-profile-store", "data"),
    State("ob-level",     "value"),
    State("ob-category",  "value"),
    State("ob-side",      "value"),
    State("ob-proximity", "value"),
    prevent_initial_call=True,
)
def navigate_steps(n_next, n_back, n_skip, step, profile_data,
                   level, category, side, proximity):
    from dash import ctx

    uid   = session.get("user_id")
    name  = _to_str(session.get("name"))  or "Deportista"
    sport = _to_str(session.get("sport")) or ""
    role  = _to_str(session.get("role"))  or ""
    is_coach = (role == "coach")

    triggered = ctx.triggered_id

    if triggered == "ob-skip-btn":
        _finish_onboarding(uid, profile_data)
        return (step, no_update, no_update, no_update, no_update, no_update, no_update,
                dcc.Location(pathname="/dashboard", id="ob-skip-redirect"), profile_data)

    # Save athlete profile when advancing from step 2 (only for athletes)
    if triggered == "ob-next-btn" and step == 2 and not is_coach:
        new_profile = {
            "competitive_level":      level,
            "weight_category":        category,
            "dominant_side":          side,
            "competition_proximity":  proximity or "Sin competencia cercana",
        }
        try:
            if uid and hasattr(db, "save_athlete_profile"):
                db.save_athlete_profile(int(uid), new_profile)
        except Exception:
            pass
        profile_data = new_profile

    new_step = step
    if triggered == "ob-next-btn":
        new_step = min(step + 1, _STEPS)
    elif triggered == "ob-back-btn":
        new_step = max(step - 1, 1)

    if triggered == "ob-next-btn" and step == _STEPS:
        _finish_onboarding(uid, profile_data)
        return (new_step, no_update, no_update, no_update, no_update, no_update, no_update,
                dcc.Location(pathname="/dashboard", id="ob-done-redirect"), profile_data)

    content = _get_step_content(new_step, name, sport, role, profile_data)
    stepper = _step_indicator(new_step, is_coach)

    # Form area visible only for athletes on step 2
    form_style = ({"marginTop": "16px"}
                  if not is_coach and new_step == 2
                  else {"display": "none"})

    next_lbl = "Siguiente →"
    if new_step == _STEPS - 1:
        next_lbl = "Ir al check-in →" if not is_coach else "Siguiente →"
    elif new_step == _STEPS:
        next_lbl = "Empezar ahora →"

    back_style = {"visibility": "hidden"} if new_step == 1 else {}
    skip_style = ({"display": "none"} if new_step == _STEPS
                  else {"display": "flex", "alignItems": "center", "gap": "6px"})

    return (new_step, stepper, content, form_style,
            next_lbl, back_style, skip_style, None, profile_data)


def _get_step_content(step, name, sport, role, profile_data):
    if step == 1:
        return _step1_content(name, sport, role)
    if step == 2:
        return _step2_content(sport, role)
    if step == 3:
        return _step3_content(sport, role)
    return _step4_content(sport, role)


def _finish_onboarding(uid, profile_data):
    if not uid:
        return
    try:
        db.mark_onboarding_done(int(uid))
    except Exception:
        pass
