import json
import threading
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import (
    add_last_point_highlight,
    add_reference_band,
    apply_chart_style,
    empty_figure,
    graph_config,
    make_bar_trace,
    make_line_marker_trace,
    placeholder_figure,
)

from dash import html, dcc, Input, Output, State, callback, callback_context, no_update
from dash.exceptions import PreventUpdate

from flask import session

import db
import questionnaires as Q
import analysis_engine as AE


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


def _is_wellbeing_result_output(value) -> bool:
    text = str(value)
    return any(
        token in text
        for token in ("q-gauge.figure", "q-explain.children", "q-trend.figure")
    )


def _purge_wellbeing_result_callbacks() -> None:
    """Remove stale wellbeing result callbacks before registering the current one."""
    try:
        import dash._callback as _dash_callback

        for key in list(_dash_callback.GLOBAL_CALLBACK_MAP):
            if _is_wellbeing_result_output(key):
                _dash_callback.GLOBAL_CALLBACK_MAP.pop(key, None)
        _dash_callback.GLOBAL_CALLBACK_LIST[:] = [
            item for item in _dash_callback.GLOBAL_CALLBACK_LIST
            if not _is_wellbeing_result_output(item.get("output", ""))
        ]
    except Exception:
        pass

    try:
        import dash as _dash

        active_app = _dash.get_app()
        app_map = getattr(active_app, "callback_map", {}) or {}
        app_list = getattr(active_app, "_callback_list", []) or []
        for key in list(app_map):
            if _is_wellbeing_result_output(key):
                app_map.pop(key, None)
        app_list[:] = [
            item for item in app_list
            if not _is_wellbeing_result_output(item.get("output", ""))
        ]
    except Exception:
        pass


def _wellbeing_result_callback(*args, **kwargs):
    """Register the single wellbeing result callback, even after Dash setup."""
    _purge_wellbeing_result_callbacks()
    try:
        import dash as _dash

        active_app = _dash.get_app()
        return active_app.callback(*args, **kwargs)
    except Exception:
        return callback(*args, **kwargs)



def _coach_roster(coach_id: int):
    if not coach_id:
        return []

    import inspect as _inspect
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None

    out = []
    seen = set()
    for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
        if hasattr(db, fn):
            try:
                sig = _inspect.signature(getattr(db, fn))
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
                    if coach_sport and r.get("sport") and r["sport"] != coach_sport:
                        continue
                    seen.add(rid)
                    out.append(r)
            except Exception:
                pass
    return out


def _can_access_athlete(athlete_id: int) -> bool:
    aid = _safe_int(athlete_id)
    actor_id = _safe_int(session.get("user_id"))
    role = _to_str(session.get("role")) or ""
    if not aid or not actor_id:
        return False
    if role == "deportista":
        return aid == actor_id
    if role == "coach":
        return any(_safe_int(a.get("id")) == aid for a in _coach_roster(actor_id))
    if role == "admin":
        return True
    return False


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


def _today_wellness(athlete_id: int):
    """Return today's wellness score (float) for an athlete, or None if not yet filled."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        qs = db.list_questionnaires(int(athlete_id)) or []
        for q in qs:
            ts = (q.get("ts") or q.get("created_at") or q.get("timestamp") or "")[:10]
            if ts == today:
                w = q.get("wellness_score")
                return float(w) if w is not None else None
    except Exception:
        pass
    return None


def _score_class(score):
    if score is None:
        return "checkin-score--pending"
    if score >= 80:
        return "checkin-score--ok"
    if score >= 65:
        return "checkin-score--good"
    if score >= 50:
        return "checkin-score--warn"
    return "checkin-score--low"


def _score_label(score):
    if score is None:
        return "Pendiente"
    if score >= 80:
        return "Listo"
    if score >= 65:
        return "Bien"
    if score >= 50:
        return "Atención"
    return "Bajo"


def _build_fast_wellbeing_message(score: float, sport: str, positives: list, risks: list) -> str:
    """Mensaje local instantaneo para no bloquear el guardado con IA externa."""
    sport_key = Q.norm_sport(sport)
    positive = (positives or ["tu disposición a registrar cómo estás"])[0]
    risk = (risks or ["la fatiga acumulada"])[0]

    if score < 50:
        base = "Hoy conviene bajar la exigencia y escuchar al cuerpo"
        action = "prioriza técnica limpia, movilidad y recuperación activa"
    else:
        base = "Hoy puedes entrenar, pero con control de carga"
        action = "mantén intensidad media y corta la sesión si el cuerpo se apaga"

    if sport_key == "taekwondo":
        sport_tip = "cuida la base, la distancia y la velocidad sin forzar patadas máximas"
    elif sport_key == "boxeo":
        sport_tip = "trabaja guardia, desplazamiento y precisión antes que volumen duro"
    else:
        sport_tip = "elige trabajo técnico antes que volumen intenso"

    return (
        f"{base}: apóyate en {positive.lower()} y vigila {risk.lower()}. "
        f"Para hoy, {action}; {sport_tip}."
    )


def _render_coach_checkin_overview(coach_id: int):
    """Builds the team check-in status grid for coaches."""
    import inspect
    coach_sport = (_to_str(session.get("sport")) or "").strip() or None
    athletes = []
    for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
        if hasattr(db, fn):
            try:
                sig = inspect.signature(getattr(db, fn))
                if "sport" in sig.parameters:
                    rows = getattr(db, fn)(int(coach_id), sport=coach_sport) or []
                else:
                    rows = getattr(db, fn)(int(coach_id)) or []
                seen = {a.get("id") for a in athletes}
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id")
                    if rid is None or rid in seen:
                        continue
                    if coach_sport and r.get("sport") and r["sport"] != coach_sport:
                        continue
                    athletes.append(r)
                if athletes:
                    break
            except Exception:
                pass

    if not athletes:
        return html.Div(
            className="card",
            children=[
                html.H4("Estado del equipo hoy", className="card-title"),
                html.P("Todavía no tienes deportistas en tu plantilla.", className="text-muted"),
            ],
        )

    today = datetime.now().strftime("%d/%m/%Y")
    items = []
    filled = 0
    for a in athletes:
        aid = a.get("id")
        name = a.get("name") or "Sin nombre"
        sport = a.get("sport") or ""
        initials = "".join(p[0].upper() for p in name.split()[:2]) if name else "?"
        score = _today_wellness(int(aid)) if aid is not None else None
        if score is not None:
            filled += 1
        score_cls = _score_class(score)
        score_lbl = _score_label(score)
        score_num = f"{score:.0f}" if score is not None else "—"

        items.append(html.Div(
            className="checkin-athlete-card",
            children=[
                html.Div(initials, className="checkin-avatar"),
                html.Div(className="checkin-info", children=[
                    html.Div(name, className="checkin-name"),
                    html.Div(sport.title() if sport else "—", className="checkin-sport text-muted"),
                ]),
                html.Div(className=f"checkin-score {score_cls}", children=[
                    html.Span(score_num, className="checkin-score__num"),
                    html.Span(score_lbl, className="checkin-score__lbl"),
                ]),
            ],
        ))

    pending = len(athletes) - filled
    summary_color = "var(--neon)" if pending == 0 else ("var(--punch)" if pending > len(athletes) // 2 else "#f0a832")

    return html.Div(
        className="card",
        children=[
            html.Div(className="card-title-row", children=[
                html.H4("Estado del equipo hoy", className="card-title"),
                html.Span(
                    f"{filled}/{len(athletes)} completados · {today}",
                    style={"fontSize": "12px", "color": summary_color, "fontWeight": "600"},
                ),
            ]),
            html.Div(className="checkin-athlete-grid", children=items),
        ],
    )


def _build_action_rec(score: float, sport: str, details: list) -> html.Div:
    """
    Tarjeta de recomendación de acción del día, específica por deporte y score.
    Cierra el loop check-in → datos → QUÉ HAGO HOY.
    """
    sport_key = Q.norm_sport(sport)

    # ── Nivel base según score ───────────────────────────────────────────────
    if score >= 80:
        level = "alta"
        level_color = "var(--neon)"
        level_icon = "▲"
        level_label = "Listo para exigencia alta"
        if sport_key == "taekwondo":
            actions = [
                "Combinaciones de entrada-patada explosiva (dollyo + bandal), series de 5×6.",
                "Simulación de combate o sparring técnico de alta intensidad.",
                "Trabaja distancia larga y penetración — tienes energía para sostener ritmo.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Saco de potencia + sombra con combinaciones largas (1-2-3-2 y hooks).",
                "Sparring técnico de contacto controlado — buena sesión para trabajar timing.",
                "Rounds de alta intensidad con recuperación de 1:1.",
            ]
        else:
            actions = [
                "Sesión de alta intensidad viable.",
                "Trabaja técnica específica a máxima calidad.",
                "Buen día para sparring o simulación de competencia.",
            ]
    elif score >= 65:
        level = "media"
        level_color = "#f0a832"
        level_icon = "●"
        level_label = "Intensidad controlada"
        if sport_key == "taekwondo":
            actions = [
                "Técnica de patada con énfasis en calidad sobre velocidad.",
                "Trabajo de distancia y desplazamiento sin contacto full.",
                "Evita series largas de penetración — prioriza precisión sobre explosividad hoy.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sombra técnica a ritmo medio — buena sesión para trabajar guardia.",
                "Saco a 70-75% de intensidad, énfasis en combinaciones limpias.",
                "Sin sparring de contacto hoy — guarda el desgaste para cuando estés al 100%.",
            ]
        else:
            actions = [
                "Sesión técnica a intensidad moderada.",
                "Prioriza calidad de movimiento sobre carga.",
                "Evita exigencia máxima — cuida la recuperación.",
            ]
    elif score >= 50:
        level = "baja"
        level_color = "#e45a5a"
        level_icon = "▼"
        level_label = "Día de atención — ajusta la carga"
        if sport_key == "taekwondo":
            actions = [
                "Calentamiento largo + movilidad articular de cadera y tobillo.",
                "Técnica suave sin contacto y sin exigencia de explosividad.",
                "Si hay sparring planificado, conviértelo en trabajo de sombra o distancia técnica.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sombra lenta con énfasis en guardia y movimiento de pies.",
                "Sin saco de potencia hoy — riesgo de lesión de muñeca o hombro cuando llegas cargado.",
                "Trabaja respiración y ritmo, no fuerza.",
            ]
        else:
            actions = [
                "Reducir intensidad al 60-70% del plan.",
                "Técnica suave y movilidad articular.",
                "Sin cargas máximas ni contacto.",
            ]
    else:
        level = "comprometido"
        level_color = "var(--punch)"
        level_icon = "⚠"
        level_label = "Estado comprometido — recuperación prioritaria"
        if sport_key == "taekwondo":
            actions = [
                "Sesión de recuperación activa: movilidad, estiramientos dinámicos, nada de impacto.",
                "Si el entrenamiento es obligatorio: técnica de sombra muy suave, sin contacto.",
                "Habla con tu coach — entrenar forzado hoy puede costarte 3 días después.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Recuperación activa: movilidad de hombros, estiramientos, sin guantes hoy.",
                "Si hay sesión grupal: participa en el calentamiento pero evita el saco.",
                "Hidratación y descanso son la sesión de hoy.",
            ]
        else:
            actions = [
                "Recuperación activa prioritaria.",
                "Sin cargas de ningún tipo.",
                "Consulta con tu coach antes de entrenar.",
            ]

    # ── Alertas específicas por dimensión ───────────────────────────────────
    specific_alerts = []
    risk_map = {d["key"]: d for d in details if d["dimension"] == "risk"}

    mol_inf = risk_map.get("tkd_molestia_inferior")
    if mol_inf and mol_inf["score"] > 50:
        specific_alerts.append("Molestia en tren inferior — evita patadas de alto impacto y cambios de dirección bruscos.")

    mol_sup = risk_map.get("box_molestia_superior")
    if mol_sup and mol_sup["score"] > 50:
        specific_alerts.append("Molestia en tren superior — sin saco de potencia ni impacto de guardia hoy.")

    mol_gen = risk_map.get("molestia_general")
    if mol_gen and mol_gen["score"] > 60:
        specific_alerts.append("Molestia activa detectada — prioriza no agravar antes de progresar.")

    pos_map = {d["key"]: d for d in details if d["dimension"] == "positive"}
    sueno_cal = pos_map.get("sueno_calidad")
    sueno_h = pos_map.get("sueno_horas")
    if (sueno_cal and sueno_cal["score"] < 40) or (sueno_h and sueno_h["score"] < 40):
        specific_alerts.append("Sueño insuficiente o de baja calidad — la velocidad de reacción y la toma de decisiones bajan. Considera ajustar el volumen.")

    fatiga = risk_map.get("fatiga_general")
    if fatiga and fatiga["score"] > 60:
        specific_alerts.append("Fatiga acumulada alta — si llevas varios días así, es señal de que necesitas un día de descarga.")

    # ── Render ───────────────────────────────────────────────────────────────
    alert_items = [
        html.Li(a, className="rec-alert-item")
        for a in specific_alerts
    ] if specific_alerts else []

    return html.Div(
        className="rec-card",
        children=[
            html.Div(
                className="rec-card__header",
                style={"borderLeftColor": level_color},
                children=[
                    html.Span(level_icon, className="rec-card__icon", style={"color": level_color}),
                    html.Div([
                        html.Div("Recomendación del día", className="rec-card__eyebrow"),
                        html.Div(level_label, className="rec-card__title", style={"color": level_color}),
                    ]),
                ],
            ),
            html.Ul(
                [html.Li(a, className="rec-action-item") for a in actions],
                className="rec-actions-list",
            ),
            html.Div(
                className="rec-alerts-block",
                children=[
                    html.Div("Puntos específicos a tener en cuenta hoy:", className="rec-alerts-label"),
                    html.Ul(alert_items, className="rec-alerts-list"),
                ],
            ) if alert_items else None,
        ],
    )


def _build_coach_rec(score: float, sport: str, details: list) -> html.Div:
    """
    Recomendación de sesión para el coach según el estado observado en el equipo.
    Piensa como coach: ¿qué tipo de sesión planteo hoy?
    """
    sport_key = Q.norm_sport(sport)

    if score >= 80:
        level_color = "var(--neon)"
        level_icon = "▲"
        level_label = "Equipo listo — sesión exigente viable"
        if sport_key == "taekwondo":
            actions = [
                "Buena sesión para sparring técnico o simulación de combate por rounds.",
                "Trabaja entradas explosivas y combinaciones de patada — el equipo tiene energía.",
                "Momento ideal para exigir velocidad de decisión bajo presión.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sesión de saco de potencia + sparring por rounds bien controlado.",
                "Trabaja combinaciones largas y transiciones guardia-ataque.",
                "Buen día para medir timing bajo fatiga real.",
            ]
        else:
            actions = [
                "Sesión de alta intensidad — el equipo está listo.",
                "Exige técnica a máxima calidad y ritmo.",
                "Buena jornada para simulación de competencia.",
            ]
    elif score >= 65:
        level_color = "#f0a832"
        level_icon = "●"
        level_label = "Equipo bien — ajusta puntos específicos"
        if sport_key == "taekwondo":
            actions = [
                "Técnica de patada con énfasis en precisión, velocidad controlada.",
                "Distancia y desplazamiento — evita sparring de contacto total.",
                "Identifica quién llega más cargado y dale trabajo técnico independiente.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sombra y saco técnico — buena sesión para trabajar guardia y esquivas.",
                "Sin sparring de alta intensidad — observa quién llega con la guardia caída.",
                "Trabaja combinaciones 1-2-3 con énfasis en la vuelta a posición.",
            ]
        else:
            actions = [
                "Sesión técnica a intensidad media.",
                "Observa quién necesita más recuperación y ajusta su carga.",
                "Prioriza calidad sobre volumen hoy.",
            ]
    elif score >= 50:
        level_color = "#e45a5a"
        level_icon = "▼"
        level_label = "Equipo con fatiga — reduce la carga planificada"
        if sport_key == "taekwondo":
            actions = [
                "Calentamiento largo + movilidad de cadera y tobillo como cuerpo principal.",
                "Técnica suave, sin contacto — trabaja postura y alineación.",
                "Si tenías sparring planificado: posponlo o conviértelo en sombra a baja intensidad.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sombra técnica lenta + trabajo de guardia sin guantes.",
                "Sin saco hoy — el riesgo de lesión sube cuando el equipo llega cargado.",
                "Sesión corta (45-50 min) con mucho tiempo de técnica individual.",
            ]
        else:
            actions = [
                "Reducir volumen e intensidad al 60-70% del plan.",
                "Prioriza técnica suave y movilidad.",
                "Evalúa individualmente si alguien necesita descanso completo.",
            ]
    else:
        level_color = "var(--punch)"
        level_icon = "⚠"
        level_label = "Equipo comprometido — recuperación prioritaria"
        if sport_key == "taekwondo":
            actions = [
                "Sesión de recuperación activa: estiramientos dinámicos, movilidad, sin impacto.",
                "Trabajo mental: revisión de video, análisis táctico, sin exigencia física.",
                "Considera si adelantar el día libre o convertir la sesión en trabajo físico suave.",
            ]
        elif sport_key == "boxeo":
            actions = [
                "Sin guantes hoy — movilidad de hombros, cadenas musculares y respiración.",
                "Si hay sesión grupal: calentamiento colectivo + trabajo individual muy suave.",
                "Usa el tiempo para análisis de video o charla táctica.",
            ]
        else:
            actions = [
                "Recuperación activa prioritaria.",
                "Sin cargas de intensidad.",
                "Evalúa con cada atleta si hay algo puntual que explique el estado del equipo.",
            ]

    # Alertas específicas del equipo
    alerts = []
    risk_map = {d["key"]: d for d in details if d["dimension"] == "risk"}
    carga = risk_map.get("coach_carga_acumulada") or risk_map.get("carga_equipo")
    if carga and carga.get("score", 0) > 60:
        alerts.append("Carga acumulada alta en el equipo — considera si el plan semanal necesita un día de descarga.")
    molestias = risk_map.get("coach_molestias_equipo") or risk_map.get("molestias_grupo")
    if molestias and molestias.get("score", 0) > 60:
        alerts.append("Molestias reportadas en el grupo — identifica quién antes de la sesión.")

    alert_items = [html.Li(a, className="rec-alert-item") for a in alerts]

    return html.Div(
        className="rec-card",
        children=[
            html.Div(
                className="rec-card__header",
                style={"borderLeftColor": level_color},
                children=[
                    html.Span(level_icon, className="rec-card__icon", style={"color": level_color}),
                    html.Div([
                        html.Div("Sesión recomendada hoy", className="rec-card__eyebrow"),
                        html.Div(level_label, className="rec-card__title", style={"color": level_color}),
                    ]),
                ],
            ),
            html.Ul(
                [html.Li(a, className="rec-action-item") for a in actions],
                className="rec-actions-list",
            ),
            html.Div(
                className="rec-alerts-block",
                children=[
                    html.Div("Puntos a revisar antes de empezar:", className="rec-alerts-label"),
                    html.Ul(alert_items, className="rec-alerts-list"),
                ],
            ) if alert_items else None,
        ],
    )


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
    # Coach groups
    "equipo":  ("Observación del equipo", "Cómo ves al grupo hoy — energía, motivación y dinámica."),
    "alertas": ("Alertas de hoy", "Factores que pueden afectar la calidad o seguridad de la sesión."),
    "sesion":  ("Sesión planificada", "Qué tienes preparado para el entrenamiento de hoy (contexto, no cuenta en el score)."),
}


_GROUP_COLORS = {
    "base":        "#0ea5e9",
    "taekwondo":   "#2fb7c4",
    "boxeo":       "#f0a832",
    "competencia": "#e45a5a",
    "peso":        "#27c98f",
    "molestia":    "#7b6fff",
    # Coach groups
    "equipo":  "#0ea5e9",
    "alertas": "#e45a5a",
    "sesion":  "#27c98f",
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


def _group_block(group_key: str, children, visible: bool = False, open_default: bool = None):
    """visible=True skips display:none (for coach groups that are always shown)."""
    is_open = open_default if open_default is not None else (group_key == "base")
    style = {} if visible else {"display": "none"}
    return html.Details(
        id=f"group-{group_key}",
        className="collapsible-card q-group-card",
        style=style,
        open=is_open,
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
# Shared helpers (gauge / result)
# =======================


def _build_gauge_fig(wellness: float) -> go.Figure:
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
                {"range": [0, 50],  "color": "rgba(200,50,50,0.18)"},
                {"range": [50, 65], "color": "rgba(220,140,30,0.18)"},
                {"range": [65, 80], "color": "rgba(200,185,30,0.18)"},
                {"range": [80, 100],"color": "rgba(30,170,100,0.18)"},
            ],
        },
    ))
    apply_chart_style(fig, height=320)
    fig.update_layout(margin=dict(l=18, r=18, t=6, b=6))
    return fig


# =======================
# Cuestionario (layout)
# =======================


def _layout_coach_checkin(coach_id: int):
    """Check-in form for coaches: they observe and rate the team, not themselves."""
    overview = _render_coach_checkin_overview(coach_id)

    defs = Q.coach_question_defs()
    coach_groups = ["equipo", "alertas", "sesion"]
    question_items = []
    for i, gk in enumerate(coach_groups):
        group_qs = [_make_slider(q) for q in defs if q.get("group") == gk]
        question_items.append(_group_block(gk, group_qs, visible=True, open_default=(i == 0)))

    # Componentes ocultos requeridos por save_q (que comparte btn-save-q como Input)
    # Sin estos, Dash lanza "nonexistent object in State" cuando el coach hace click.
    hidden_athlete_states = html.Div(style={"display": "none"}, children=[
        dcc.Dropdown(id="q-user",       options=[], value=None),
        dcc.Dropdown(id="q-session",    options=[], value="NONE"),
        dcc.Dropdown(id="q-competition",options=[{"label":"No","value":"no"}], value="no"),
        dcc.Dropdown(id="q-weight",     options=[{"label":"No","value":"no"}], value="no"),
        dcc.Dropdown(id="q-injury",     options=[{"label":"No","value":"no"}], value="no"),
        *[dcc.Slider(id=f"q-{k}", min=1, max=5, step=1, value=3)
          for k, _ in Q.questions()],
    ])

    return html.Div(className="coach-shell", children=[
        hidden_athlete_states,

        html.Div(className="page-head", children=[
            html.H2("Check-in del equipo"),
            html.P(
                "Registra lo que observas hoy sobre el grupo. "
                "Abre solo las secciones que quieras puntuar.",
                className="text-muted",
            ),
        ]),

        html.Div(className="questionnaire-grid", children=[
            # ── Columna izquierda: estado del equipo + gauge ─────────────────
            html.Div(className="panel-col", children=[
                overview,
                html.Div(className="inner-card q-result-card", children=[
                    html.H4("Lectura del equipo hoy", className="card-title"),
                    html.Div("Preparación del equipo (0–100)", className="text-muted q-result-subtitle"),
                    dcc.Graph(id="q-gauge", figure=empty_figure("", "Responde y guarda el check-in para ver el resultado.", height=300), config=graph_config(),
                              style={"height": "300px", "width": "100%"}),
                    html.Div(id="q-explain", className="q-result-explain"),
                    html.Div(style={"marginTop": "14px", "borderTop": "1px solid var(--line)", "paddingTop": "10px"}, children=[
                        html.Div("Tendencia reciente", className="text-muted",
                                 style={"fontSize": "11px", "fontWeight": "600",
                                        "textTransform": "uppercase", "letterSpacing": ".06em",
                                        "marginBottom": "2px"}),
                        dcc.Graph(id="q-trend", figure=placeholder_figure(150),
                                  config={"displayModeBar": False},
                                  style={"height": "150px", "width": "100%"}),
                    ]),
                ]),
            ]),

            # ── Columna derecha: preguntas + guardar ─────────────────────────
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4("Observación del día", className="card-title"),
                    html.P(
                        "Responde por bloques. Las secciones cerradas usan el valor por defecto.",
                        className="text-muted q-block-copy",
                    ),
                    html.Div(className="q-groups", children=question_items),
                    html.Button(
                        "Guardar check-in del equipo",
                        id="btn-save-q",
                        className="btn btn-primary",
                        style={"marginTop": "18px", "width": "100%"},
                    ),
                ]),
            ]),
        ]),
    ])


def layout_questionnaire():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    # Coaches get their own team-observation check-in, not the athlete self-assessment
    if role == "coach" and uid:
        return _layout_coach_checkin(int(uid))

    team_selector = html.Div([
        dcc.Dropdown(id="q-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []
    options_users = []
    default_user = None

    _athlete_sport_key = ""
    if role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes if u and u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None
        _athlete_sport_key = Q.norm_sport((u or {}).get("sport") or "")

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

    if _athlete_sport_key == "taekwondo":
        _checkin_sub = "Responde cómo llegas hoy: explosividad, piernas y estado general antes del round."
    elif _athlete_sport_key == "boxeo":
        _checkin_sub = "Responde cómo llegas hoy: manos, guardia y estado general antes de subir al saco."
    else:
        _checkin_sub = "Responde este check-in para saber cómo llegas hoy y qué conviene tener en cuenta antes de entrenar o competir."
    selector_title = "Tu perfil" if role == "deportista" else "Deportista"

    question_items = []
    ordered_groups = ["base", "taekwondo", "boxeo", "competencia", "peso", "molestia"]
    defs = Q.question_defs()
    for group_key in ordered_groups:
        group_questions = [_make_slider(q) for q in defs if q.get("group") == group_key]
        question_items.append(_group_block(group_key, group_questions))

    # Hidden placeholders so save_coach_q States always exist in the DOM
    hidden_coach_states = html.Div(style={"display": "none"}, children=[
        *[dcc.Slider(id=f"q-{k}", min=1, max=5, step=1, value=3)
          for k, _ in Q.coach_questions()],
    ])

    return html.Div([
        hidden_coach_states,
        html.Div(className="page-head", children=[
            html.H2("Estado competitivo del día"),
            html.P(_checkin_sub, className="text-muted"),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),
        html.Div(className="questionnaire-grid", children=[
            html.Div(className="panel-col", children=[
                html.Div(className="card", children=[
                    html.H4(selector_title, className="card-title"),
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
                html.Div(className="inner-card q-result-card", children=[
                    html.H4("Resultado del día", className="card-title"),
                    html.Div("Estado competitivo del día (0-100)", className="text-muted q-result-subtitle"),
                    dcc.Graph(id="q-gauge", figure=empty_figure("", "Responde y guarda el check-in para ver el resultado.", height=320), config=graph_config(), style={"height": "320px", "width": "100%"}),
                    html.Div(id="q-explain", className="q-result-explain"),
                    html.Div(style={"marginTop": "14px", "borderTop": "1px solid var(--line)", "paddingTop": "10px"}, children=[
                        html.Div("Tendencia reciente", className="text-muted",
                                 style={"fontSize": "11px", "fontWeight": "600",
                                        "textTransform": "uppercase", "letterSpacing": ".06em",
                                        "marginBottom": "2px"}),
                        dcc.Graph(id="q-trend", figure=placeholder_figure(150),
                                  config={"displayModeBar": False},
                                  style={"height": "150px", "width": "100%"}),
                    ]),
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
    sport = Q.norm_sport(_athlete_sport(user_id))
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


def _build_trend_fig(uid: int):
    """Mini línea de bienestar (últimos 14 check-ins) con zonas de referencia."""
    try:
        rows = db.list_questionnaires(int(uid), limit=20) or []
    except Exception:
        rows = []
    pts = [
        (r.get("ts", "")[:10], float(r["wellness_score"]))
        for r in rows if r.get("wellness_score") is not None
    ]
    pts = sorted(pts, key=lambda p: p[0])[-14:]
    if not pts:
        return empty_figure("", "Sin registros recientes", height=150)
    x_vals = [p[0] for p in pts]
    y_vals = [p[1] for p in pts]
    fig = go.Figure()
    add_reference_band(fig, y0=80, y1=100, fillcolor="rgba(39,201,143,0.10)")
    add_reference_band(fig, y0=50, y1=65,  fillcolor="rgba(240,168,50,0.09)")
    fig.add_trace(make_line_marker_trace(
        x_vals, y_vals, "Bienestar", color="#0ea5e9", width=2.2, marker_size=5,
    ))
    apply_chart_style(fig, height=150)
    fig.update_yaxes(range=[0, 100])
    fig.update_layout(
        margin=dict(l=32, r=8, t=6, b=28),
        showlegend=False,
    )
    return fig


@_wellbeing_result_callback(
    Output("q-gauge", "figure"),
    Output("q-explain", "children"),
    Output("q-trend", "figure"),
    Input("q-user", "value"),
    Input("btn-save-q", "n_clicks"),
    # ── Athlete-specific states ──────────────────────────────────────────
    State("q-user", "value"),
    State("q-session", "value"),
    State("q-competition", "value"),
    State("q-weight", "value"),
    State("q-injury", "value"),
    *[State(f"q-{k}", "value") for k, _ in Q.questions()],
    # ── Coach-specific states (cq_* keys — never overlap with athlete keys) ─
    *[State(f"q-{k}", "value") for k, _ in Q.coach_questions()],
    prevent_initial_call=False,
)
def save_wellbeing(input_user_id, n, user_id, session_id, competition, weight, injury, *values):
    trigger = callback_context.triggered_id if callback_context.triggered else None
    if trigger != "btn-save-q":
        uid = _safe_int(input_user_id)
        trend = _build_trend_fig(uid) if uid else empty_figure("", "Sin registros recientes", height=150)
        return no_update, no_update, trend
    if not n:
        raise PreventUpdate

    n_athlete_q = len(Q.questions())
    athlete_q_values = values[:n_athlete_q]
    coach_q_values = values[n_athlete_q:]

    role = _to_str(session.get("role"))

    if role == "coach":
        # ── Coach path ────────────────────────────────────────────────────
        coach_id = _safe_int(session.get("user_id"))
        if not coach_id:
            raise PreventUpdate

        coach_defs = {q["key"]: q for q in Q.coach_question_defs()}
        ans = {}
        for (k, _), v in zip(Q.coach_questions(), coach_q_values):
            default = coach_defs.get(k, {}).get("default", 3)
            ans[k] = default if v is None else v

        breakdown = Q.coach_score_breakdown(ans)
        score = breakdown["score"]

        db.save_questionnaire(
            coach_id,
            ans,
            score,
            None,
            None,
            session_id=None,
        )
        AE.invalidate_cache(coach_id)

        fig = _build_gauge_fig(score)

        if score >= 80:
            estado = "El equipo llega en muy buen estado — sesión exigente viable"
        elif score >= 65:
            estado = "Buen estado general del equipo, con algún punto a vigilar"
        elif score >= 50:
            estado = "Equipo con señales de fatiga — ajustar la carga planificada"
        else:
            estado = "Equipo comprometido hoy — priorizar recuperación y técnica suave"

        top_positive = sorted(
            [d for d in breakdown["details"] if d["dimension"] == "positive"],
            key=lambda x: x["score"], reverse=True,
        )[:2]
        top_risks = sorted(
            [d for d in breakdown["details"] if d["dimension"] == "risk"],
            key=lambda x: x["score"], reverse=True,
        )[:2]

        pos_txt  = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_positive) or "—"
        risk_txt = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_risks) or "—"

        coach_sport = _to_str(session.get("sport") or "")
        coach_rec = _build_coach_rec(score, coach_sport, breakdown["details"])

        explain = html.Div([
            html.P([html.Strong("Lectura del equipo: "), estado]),
            html.P(
                "Este valor combina el 65% de indicadores positivos (energía, motivación, dinámica) "
                "con el 35% de alertas (carga acumulada, molestias del equipo)."
            ),
            html.Ul([
                html.Li(f"Indicadores positivos: {breakdown['positive_avg']:.0f}/100"),
                html.Li(f"Alertas del equipo: {breakdown['risk_avg']:.0f}/100"),
                html.Li(f"Puntos fuertes hoy: {pos_txt}."),
                html.Li(f"Lo más a vigilar hoy: {risk_txt}."),
            ]),
            coach_rec,
        ])
        return fig, explain, no_update

    else:
        # ── Athlete path ──────────────────────────────────────────────────
        if not user_id:
            raise PreventUpdate

        sport = _athlete_sport(user_id)
        comp_flag = competition == "si"
        weight_flag = weight == "si"
        injury_flag = injury == "si"

        ans = {}
        for (k, _), v in zip(Q.questions(), athlete_q_values):
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
        AE.invalidate_cache(int(user_id))

        # ── Notificación al coach si bienestar < 50 ──────────────────────
        if wellness < 50:
            try:
                import notifications as _N
                if _N.is_configured():
                    _prefs = db.get_notification_prefs(int(user_id))
                    if _prefs.get("low_wellness_alert", 1):
                        _coach = db.get_user_coach(int(user_id))
                        if _coach:
                            _coach_email = _coach.get("email") or _coach.get("correo")
                            _athlete = db.get_user_by_id(int(user_id))
                            if _coach_email and _athlete:
                                threading.Thread(
                                    target=_N.notify_coach_low_wellness,
                                    args=(
                                        _athlete.get("name", "Atleta"),
                                        sport,
                                        wellness,
                                        _coach_email,
                                        _coach.get("name", "Coach"),
                                    ),
                                    daemon=True,
                                ).start()
            except Exception:
                pass

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

        top_positive = sorted([d for d in breakdown["details"] if d["dimension"] == "positive"], key=lambda x: x["score"], reverse=True)[:2]
        top_risks = sorted([d for d in breakdown["details"] if d["dimension"] == "risk"], key=lambda x: x["score"], reverse=True)[:2]

        positives_txt = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_positive) if top_positive else "sin puntos fuertes claros"
        risks_txt = ", ".join(d["label"].replace("¿", "").replace("?", "") for d in top_risks) if top_risks else "sin señales de riesgo destacadas"

        rec_card = _build_action_rec(wellness, sport, breakdown["details"])

        _quick_motivation = ""
        if wellness < 65:
            _positives = [d["label"].replace("¿", "").replace("?", "") for d in top_positive]
            _risks = [d["label"].replace("¿", "").replace("?", "") for d in top_risks]
            _quick_motivation = _build_fast_wellbeing_message(
                wellness,
                sport,
                _positives,
                _risks,
            )

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
            rec_card,
            *([html.Div(
                style={
                    "marginTop": "10px",
                    "padding": "10px 14px",
                    "background": "rgba(47,183,196,0.07)",
                    "borderLeft": "3px solid var(--neon, #2fb7c4)",
                    "borderRadius": "0 6px 6px 0",
                    "fontSize": "12px",
                    "color": "var(--ink, #cdd8e8)",
                    "lineHeight": "1.5",
                },
                children=[
                    html.Span("✦ ", style={"color": "var(--neon, #2fb7c4)", "fontWeight": "700"}),
                    _quick_motivation,
                ],
            )] if _quick_motivation else []),
        ])
        trend_fig = _build_trend_fig(int(user_id))
        return fig, explain, trend_fig


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
    import inspect
    athletes = []
    if role == "coach" and uid:
        coach_sport = (_to_str(session.get("sport")) or "").strip() or None
        for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
            if hasattr(db, fn):
                try:
                    sig = inspect.signature(getattr(db, fn))
                    if "sport" in sig.parameters:
                        athletes = getattr(db, fn)(int(uid), sport=coach_sport) or []
                    else:
                        athletes = getattr(db, fn)(int(uid)) or []
                    if athletes:
                        break
                except Exception:
                    pass
        if coach_sport:
            athletes = [a for a in athletes if not a.get("sport") or a["sport"] == coach_sport]
        if team_id not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(team_id))
            athletes = [a for a in athletes if int(a.get("id")) in member_ids] if member_ids else []
    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
    elif role == "admin":
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]
    return [a for a in athletes if a and a.get("id") is not None]


def _history_options(role, uid, team_id="ALL"):
    athletes = _history_athletes_for_role(role, uid, team_id)
    athlete_ids = [_safe_int(u.get("id")) for u in athletes if _safe_int(u.get("id"))]
    qs_bulk = {}
    if athlete_ids and hasattr(db, "list_questionnaires_bulk"):
        try:
            qs_bulk = db.list_questionnaires_bulk(athlete_ids) or {}
        except Exception:
            qs_bulk = {}
    options = []
    for u in athletes:
        try:
            uid_int = int(u.get("id"))
            qs = qs_bulk.get(uid_int)
            if qs is None:
                qs = db.list_questionnaires(uid_int) or []
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

    default_team = "ALL"
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

    if role == "coach":
        hist_title = "Histórico del equipo"
        hist_sub = "Selecciona un deportista para ver cómo ha evolucionado su estado a lo largo del tiempo."
    else:
        hist_title = "Histórico de wellbeing"
        hist_sub = "Aquí puedes ver cómo ha ido cambiando el estado del día y qué señales merece la pena revisar con más calma."

    shell_cls = "coach-shell" if role == "coach" else ""

    return html.Div(className=shell_cls, children=[

        dcc.Download(id="dl-wellbeing-csv"),

        # ── Encabezado ──────────────────────────────────────────────────────
        html.Div(className="page-head", children=[
            html.H2(hist_title),
            html.P(hist_sub, className="text-muted"),
        ]),
        html.Div(className="ecg-divider", style={"marginBottom": "20px"}),

        # ── Card: selector ───────────────────────────────────────────────────
        html.Div(className="card", children=[
            html.H4(
                "Tus check-ins guardados" if role == "deportista" else "Qué deportista quieres revisar",
                className="card-title",
            ),
            *(
                [html.P(
                    "Aquí puedes ver cómo ha evolucionado tu estado a lo largo del tiempo.",
                    className="text-muted",
                )]
                if role == "deportista" else []
            ),
            team_selector,
            html.Div(
                className="filter-item",
                style={"marginTop": "8px", **({"display": "none"} if role == "deportista" else {})},
                children=[
                    html.Label("Deportista"),
                    dcc.Dropdown(
                        id="h-user",
                        options=[],
                        value=None,
                        placeholder="Cargando deportistas…",
                    ),
                ],
            ),
            *(
                [html.Small(
                    "Solo aparecen deportistas con check-ins guardados.",
                    className="text-muted", style={"marginTop": "8px", "display": "block"},
                )]
                if role != "deportista" else []
            ),
        ]),

        dcc.Store(id="h-history-data"),

        html.Div(
            id="h-summary",
            className="kpis kpis--auto kpis--tight wellbeing-history-kpis",
            children=_history_summary([], [], [], []),
        ),

        # ── Tabla de últimos registros ────────────────────────────────────────
        html.Div(className="card", style={"marginTop": "16px"}, children=[
            html.Div(className="card-title-row", children=[
                html.H4("Últimos registros", className="card-title"),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                    html.Span("Los 8 check-ins más recientes", className="text-muted",
                              style={"fontSize": "12px"}),
                    dcc.Dropdown(
                        id="dl-wellbeing-period",
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
                    html.Button("↓ Excel", id="btn-dl-wellbeing", className="btn btn-ghost btn-xs"),
                ]),
            ]),
            html.Div(
                id="h-recent-table",
                children=html.P("Cargando historial...", className="text-muted", style={"padding": "8px 0"}),
            ),
        ]),

        # ── Gráficas ─────────────────────────────────────────────────────────
        html.Div(className="wellbeing-history-grid", style={"marginTop": "16px"}, children=[
            html.Div(className="card", children=[
                html.H4("Tendencia de estado", className="card-title"),
                dcc.Graph(id="h-wellness",
                          figure=placeholder_figure(420),
                          config=graph_config(), style={"height": "420px", "width": "100%"}),
            ]),

            html.Div(className="card", children=[
                html.H4("Carga y señales del contexto", className="card-title"),
                dcc.Graph(id="h-load",
                          figure=placeholder_figure(420),
                          config=graph_config(), style={"height": "420px", "width": "100%"}),
                html.Details(className="collapsible-card wellbeing-help", style={"marginTop": "24px"}, children=[
                    html.Summary(className="collapsible-card__summary", children=[
                        html.Div(className="collapsible-card__head", children=[
                            html.Div("Cómo leer esta gráfica", className="card-title"),
                            html.Div("Te contamos cuándo estás viendo carga real y cuándo una lectura más contextual.",
                                     className="text-muted"),
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
    if not uid:
        return [], None
    options = _history_options(role, uid, team_id)
    return options, (options[0]["value"] if options else None)


def _build_recent_table(rows: list) -> html.Div:
    """Tabla compacta con los últimos 8 check-ins."""
    if not rows:
        return html.P("Sin registros todavía.", className="text-muted",
                      style={"padding": "8px 0"})
    recent = rows[:8]
    trs = []
    for r in recent:
        ts  = (r.get("ts") or "")[:16].replace("T", " ")
        ws  = r.get("wellness_score")
        score_val = f"{float(ws):.0f}" if ws is not None else "—"
        lbl = _score_label(float(ws) if ws is not None else None)
        cls = "tc-up" if ws is not None and float(ws) >= 65 else ("tc-down" if ws is not None else "")
        trs.append(html.Tr([
            html.Td(ts or "—", className="text-muted text-xs"),
            html.Td(score_val, className=cls, style={"fontWeight": "700", "fontSize": "14px"}),
            html.Td(lbl, className="text-muted text-xs"),
        ]))
    return html.Table(
        className="tbl-compact",
        children=[
            html.Thead(html.Tr([
                html.Th("Fecha"), html.Th("Score"), html.Th("Estado"),
            ])),
            html.Tbody(trs),
        ],
    )


@callback(
    Output("h-history-data", "data"),
    Input("h-user", "value"),
    prevent_initial_call=False,
)
def load_history_data(user_id):
    _role = _to_str(session.get("role")) or ""
    if not user_id:
        return {"status": "missing_user", "role": _role}

    uid = _safe_int(user_id)
    if not uid or not _can_access_athlete(uid):
        return {"status": "forbidden", "role": _role}

    try:
        rows = db.list_questionnaires(uid) or []
    except Exception:
        rows = []

    if not rows:
        return {"status": "empty", "role": _role, "recent": []}

    pts = []
    carga_pts = []
    cap_pts = []
    risk_pts = []
    sport_default = _athlete_sport(uid)

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

    def _pack(points):
        return [{"label": p[1], "value": p[2]} for p in points]

    recent = [
        {
            "ts": r.get("ts"),
            "wellness_score": r.get("wellness_score"),
        }
        for r in rows[:8]
    ]

    return {
        "status": "ok",
        "role": _role,
        "pts": _pack(pts),
        "cap_pts": _pack(cap_pts),
        "risk_pts": _pack(risk_pts),
        "carga_pts": _pack(carga_pts),
        "recent": recent,
    }


def _history_points_from_payload(items):
    points = []
    for item in items or []:
        try:
            points.append((None, item.get("label") or "", float(item.get("value"))))
        except Exception:
            continue
    return points


def _history_payload_points(data):
    data = data or {}
    return (
        _history_points_from_payload(data.get("pts")),
        _history_points_from_payload(data.get("cap_pts")),
        _history_points_from_payload(data.get("risk_pts")),
        _history_points_from_payload(data.get("carga_pts")),
    )


@callback(
    Output("h-summary", "children"),
    Output("h-recent-table", "children"),
    Input("h-history-data", "data"),
    prevent_initial_call=True,
)
def render_history_summary(data):
    data = data or {}
    status = data.get("status")
    role = data.get("role") or _to_str(session.get("role")) or ""
    if status == "missing_user":
        msg = "Cargando historial..." if role == "deportista" else "Selecciona un deportista para ver sus registros."
        return _history_summary([], [], [], []), html.P(msg, className="text-muted", style={"padding": "8px 0"})
    if status == "forbidden":
        return (
            _history_summary([], [], [], []),
            html.P("No tienes permisos para ver este deportista.", className="text-muted", style={"padding": "8px 0"}),
        )
    if status == "empty":
        return (
            _history_summary([], [], [], []),
            html.P("Sin check-ins guardados todavía.", className="text-muted", style={"padding": "8px 0"}),
        )

    pts, cap_pts, risk_pts, carga_pts = _history_payload_points(data)
    return _history_summary(pts, cap_pts, risk_pts, carga_pts), _build_recent_table(data.get("recent") or [])


@callback(
    Output("h-wellness", "figure"),
    Output("h-load", "figure"),
    Input("h-history-data", "data"),
    prevent_initial_call=True,
)
def render_history_charts(data):
    data = data or {}
    status = data.get("status")
    role = data.get("role") or _to_str(session.get("role")) or ""
    if status == "missing_user":
        chart_msg = "Cargando..." if role == "deportista" else "Selecciona un deportista con check-ins guardados."
        return (
            _empty_chart("Estado del día", chart_msg),
            _empty_chart("Carga y señales del contexto", "Aquí verás la evolución cuando existan registros."),
        )
    if status == "forbidden":
        return (
            _empty_chart("Estado del día", "No tienes permisos para ver este deportista."),
            _empty_chart("Carga y contexto", "Selecciona un deportista autorizado."),
        )
    if status == "empty":
        return (
            _empty_chart("Estado del día", "Este deportista todavía no tiene check-ins guardados."),
            _empty_chart("Carga y señales del contexto", "Aún no hay datos suficientes para mostrar evolución."),
        )

    pts, cap_pts, risk_pts, carga_pts = _history_payload_points(data)
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

    return fig_w, fig_l


# ── CSV export — bienestar / sesiones ────────────────────────────────────────

def _fmt_ts(ts: str) -> str:
    if not ts:
        return ""
    return str(ts).replace("T", " ")[:16]


@callback(
    Output("dl-wellbeing-csv", "data"),
    Input("btn-dl-wellbeing", "n_clicks"),
    State("h-user", "value"),
    State("dl-wellbeing-period", "value"),
    prevent_initial_call=True,
)
def download_wellbeing_csv(_, selected_uid, period):
    from datetime import datetime as _dt, timedelta
    from flask import session as _sess
    from report_utils import xlsx_table, safe_filename_stem as _sfs
    uid = _safe_int(selected_uid or _sess.get("user_id"))
    if not uid or not _can_access_athlete(uid):
        raise PreventUpdate
    try:
        rows = db.list_questionnaires(uid)
    except Exception:
        raise PreventUpdate
    if not rows:
        raise PreventUpdate

    try:
        days = int(period or 0)
    except (ValueError, TypeError):
        days = 0
    if days > 0:
        cutoff = (_dt.utcnow().date() - timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get("ts") or "")[:10] >= cutoff]

    user_row = db.get_user_by_id(int(uid))
    athlete_name = (user_row.get("name") or "—") if user_row else "—"
    athlete_sport = ((user_row.get("sport") or "—").title()) if user_row else "—"
    period_label = f"Últimos {days} días" if days > 0 else "Completo"
    meta = [
        ("Atleta",   athlete_name),
        ("Deporte",  athlete_sport),
        ("Período",  period_label),
        ("Registros", str(len(rows))),
        ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
    ]

    headers = [
        "Fecha y hora",
        "Bienestar (0-100)",
        "RPE percibido (0-10)",
        "Duración entrenamiento (min)",
        "Carga estimada (UA)",
    ]
    data = []
    for r in rows:
        rpe = r.get("rpe")
        dur = r.get("duration_min")
        carga = round(float(rpe) * float(dur), 1) if rpe is not None and dur is not None else ""
        data.append([
            _fmt_ts(r.get("ts")),
            round(r.get("wellness_score"), 1) if r.get("wellness_score") is not None else "",
            rpe if rpe is not None else "",
            dur if dur is not None else "",
            carga,
        ])

    xl = xlsx_table(
        "Historial de bienestar",
        meta, headers, data,
        sheet_name="Bienestar",
        col_types={1: "number2", 2: "number2", 3: "int", 4: "number2"},
    )
    period_tag = f"_{days}d" if days > 0 else "_completo"
    safe_name = _sfs(athlete_name, "atleta")
    fname = f"combatiq_bienestar_{safe_name}{period_tag}_{_dt.utcnow().strftime('%Y%m%d')}.xlsx"
    return dcc.send_bytes(lambda b: b.write(xl), fname)
