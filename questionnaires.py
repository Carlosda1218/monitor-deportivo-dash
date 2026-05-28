from __future__ import annotations

from typing import Dict, List

QUESTION_DEFS: List[dict] = [
    # Base del día
    {"key": "energia", "label": "¿Cómo sientes tu energía hoy para rendir?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "base", "dimension": "positive", "weight": 14},
    {"key": "recuperacion", "label": "¿Cómo sientes tu recuperación hoy?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "base", "dimension": "positive", "weight": 14},
    {"key": "sueno_calidad", "label": "¿Cómo dormiste anoche?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "base", "dimension": "positive", "weight": 8},
    {"key": "sueno_horas", "label": "¿Cuántas horas dormiste?", "min": 0, "max": 12, "step": 1, "default": 8, "group": "base", "dimension": "positive", "weight": 6},
    {"key": "listo_rendir", "label": "¿Qué tan listo/a te sientes hoy para una sesión exigente o competir?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "base", "dimension": "positive", "weight": 10},
    {"key": "fatiga_general", "label": "¿Qué tan fatigado/a llegas hoy?", "min": 1, "max": 5, "step": 1, "default": 2, "group": "base", "dimension": "risk", "weight": 10},
    {"key": "cuerpo_pesado", "label": "¿Qué tan pesado sientes el cuerpo hoy?", "min": 1, "max": 5, "step": 1, "default": 2, "group": "base", "dimension": "risk", "weight": 8},

    # Taekwondo
    {"key": "tkd_explosividad", "label": "¿Te sientes explosivo/a hoy para salir rápido en las acciones?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "taekwondo", "dimension": "positive", "weight": 8},
    {"key": "tkd_agilidad", "label": "¿Qué tan ágil te sientes hoy para entrar, salir y cambiar de dirección?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "taekwondo", "dimension": "positive", "weight": 6},
    {"key": "tkd_ritmo", "label": "¿Sientes que puedes sostener ritmo de combate durante varios rounds?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "taekwondo", "dimension": "positive", "weight": 7},
    {"key": "tkd_molestia_inferior", "label": "¿Sientes alguna molestia en pierna, tobillo, rodilla o cadera que pueda afectar tus patadas o desplazamientos?", "min": 1, "max": 5, "step": 1, "default": 1, "group": "taekwondo", "dimension": "risk", "weight": 9},

    # Boxeo
    {"key": "box_ritmo", "label": "¿Sientes que hoy puedes sostener buen ritmo de golpeo durante los rounds?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "boxeo", "dimension": "positive", "weight": 8},
    {"key": "box_rapidez", "label": "¿Qué tan rápido/a te sientes hoy de manos y reacción?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "boxeo", "dimension": "positive", "weight": 6},
    {"key": "box_precision", "label": "¿Sientes que puedes mantener precisión cuando sube la intensidad?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "boxeo", "dimension": "positive", "weight": 7},
    {"key": "box_molestia_superior", "label": "¿Sientes alguna molestia en hombros, manos, muñecas o guardia que pueda afectar tu boxeo?", "min": 1, "max": 5, "step": 1, "default": 1, "group": "boxeo", "dimension": "risk", "weight": 9},

    # Competencia
    {"key": "comp_frescura", "label": "¿Te sientes fresco/a para competir o para una sesión clave?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "competencia", "dimension": "positive", "weight": 6},
    {"key": "comp_claridad", "label": "¿Qué tan claro/a y enfocado/a te sientes hoy para decidir bajo presión?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "competencia", "dimension": "positive", "weight": 6},
    {"key": "comp_tension", "label": "¿Qué tanta tensión o presión sientes hoy por la competencia o la sesión?", "min": 1, "max": 5, "step": 1, "default": 2, "group": "competencia", "dimension": "risk", "weight": 6},

    # Peso
    {"key": "peso_afecta", "label": "¿El control del peso te está afectando hoy para rendir?", "min": 1, "max": 5, "step": 1, "default": 1, "group": "peso", "dimension": "risk", "weight": 6},

    # Molestia / lesión contextual
    {"key": "molestia_general", "label": "¿Qué tanta molestia tienes hoy en la zona que más te preocupa?", "min": 1, "max": 5, "step": 1, "default": 1, "group": "molestia", "dimension": "risk", "weight": 7},
    {"key": "limitacion_molestia", "label": "¿Qué tanto te limita esa molestia para entrenar o competir hoy?", "min": 1, "max": 5, "step": 1, "default": 1, "group": "molestia", "dimension": "risk", "weight": 8},
    {"key": "evolucion_molestia", "label": "Comparado con la última sesión, ¿esa molestia va mejor o peor?", "min": 1, "max": 5, "step": 1, "default": 3, "group": "molestia", "dimension": "positive", "weight": 3},
]


def question_defs() -> List[dict]:
    return QUESTION_DEFS


def questions():
    return [(q["key"], q["label"]) for q in QUESTION_DEFS]


def question_meta(key: str) -> dict:
    for q in QUESTION_DEFS:
        if q["key"] == key:
            return q
    raise KeyError(key)


def _norm_1_5(value: float) -> float:
    value = min(max(float(value), 1.0), 5.0)
    return ((value - 1.0) / 4.0) * 100.0


def _sleep_hours_score(hours: float) -> float:
    h = min(max(float(hours), 0.0), 12.0)
    if 7.0 <= h <= 9.0:
        return 100.0
    if h < 7.0:
        return max(0.0, 100.0 - (7.0 - h) * 20.0)
    return max(0.0, 100.0 - (h - 9.0) * 20.0)


def _score_item(qdef: dict, value) -> float:
    if qdef["key"] == "sueno_horas":
        return _sleep_hours_score(value)
    return _norm_1_5(value)


_SPORT_NORM: dict[str, str] = {
    "taekwondo": "taekwondo",
    "tkd":       "taekwondo",
    "box":       "boxeo",
    "boxeo":     "boxeo",
    "boxing":    "boxeo",
}


def norm_sport(sport: str | None) -> str:
    """Normaliza variantes de nombre de deporte al valor canónico usado en grupos."""
    return _SPORT_NORM.get((sport or "").strip().lower(), (sport or "").strip().lower())


def active_question_defs(sport: str | None = None, competition: bool = False, weight: bool = False, injury: bool = False) -> List[dict]:
    sport = norm_sport(sport)
    active = []
    for q in QUESTION_DEFS:
        group = q["group"]
        if group == "base":
            active.append(q)
        elif group in ("taekwondo", "boxeo"):
            if sport == group:
                active.append(q)
        elif group == "competencia" and competition:
            active.append(q)
        elif group == "peso" and weight:
            active.append(q)
        elif group == "molestia" and injury:
            active.append(q)
    return active


def score_breakdown(ans: Dict, sport: str | None = None, competition: bool = False, weight: bool = False, injury: bool = False) -> dict:
    active = active_question_defs(sport=sport, competition=competition, weight=weight, injury=injury)

    positives = []
    risks = []
    details = []
    pos_w = 0.0
    risk_w = 0.0
    pos_sum = 0.0
    risk_sum = 0.0

    for q in active:
        raw = ans.get(q["key"], q.get("default", 3))
        item_score = _score_item(q, raw)
        item = {
            "key": q["key"],
            "label": q["label"],
            "raw": raw,
            "score": item_score,
            "dimension": q["dimension"],
            "weight": q["weight"],
            "group": q["group"],
        }
        details.append(item)
        if q["dimension"] == "positive":
            positives.append(item)
            pos_sum += item_score * q["weight"]
            pos_w += q["weight"]
        else:
            risks.append(item)
            risk_sum += item_score * q["weight"]
            risk_w += q["weight"]

    positive_avg = (pos_sum / pos_w) if pos_w else 50.0
    risk_avg = (risk_sum / risk_w) if risk_w else 0.0

    total = 0.70 * positive_avg + 0.30 * (100.0 - risk_avg)
    total = max(0.0, min(100.0, total))
    return {
        "score": float(total),
        "positive_avg": float(positive_avg),
        "risk_avg": float(risk_avg),
        "positive_share": 70,
        "risk_share": 30,
        "details": details,
        "active_keys": [q["key"] for q in active],
    }


def wellness_score(ans: Dict, sport: str | None = None, competition: bool = False, weight: bool = False, injury: bool = False) -> float:
    return score_breakdown(ans, sport=sport, competition=competition, weight=weight, injury=injury)["score"]


# ── Cuestionario del COACH ──────────────────────────────────────────────────
# El coach no responde preguntas sobre sí mismo sino observaciones del equipo.

COACH_QUESTION_DEFS: List[dict] = [
    # Observación del equipo
    {"key": "cq_energia_equipo",   "label": "¿Cómo ves la energía y disposición del equipo hoy?",                                             "min": 1, "max": 5, "step": 1, "default": 3, "group": "equipo",  "dimension": "positive", "weight": 16},
    {"key": "cq_motivacion",       "label": "¿El grupo llega motivado y concentrado a la sesión?",                                             "min": 1, "max": 5, "step": 1, "default": 3, "group": "equipo",  "dimension": "positive", "weight": 14},
    {"key": "cq_cohesion",         "label": "¿Cómo está el ambiente y la dinámica del grupo hoy?",                                             "min": 1, "max": 5, "step": 1, "default": 3, "group": "equipo",  "dimension": "positive", "weight": 10},
    # Alertas y carga
    {"key": "cq_carga_acumulada",  "label": "¿Con cuánta carga acumulada llega el equipo? (1 = muy fresco · 5 = muy cargado)",                "min": 1, "max": 5, "step": 1, "default": 2, "group": "alertas", "dimension": "risk",     "weight": 14},
    {"key": "cq_molestias_equipo", "label": "¿Cuántos deportistas llegan hoy con molestias o limitaciones? (1 = ninguno · 5 = varios)",       "min": 1, "max": 5, "step": 1, "default": 1, "group": "alertas", "dimension": "risk",     "weight": 12},
    # Sesión planificada (sin peso en el score — son contexto)
    {"key": "cq_intensidad_sesion","label": "¿Cuál es la intensidad de la sesión planificada? (1 = recuperación activa · 5 = máxima exigencia)", "min": 1, "max": 5, "step": 1, "default": 3, "group": "sesion",  "dimension": "positive", "weight": 0},
    {"key": "cq_complejidad_tecnica","label": "¿Qué tan técnica o tácticamente exigente es lo planificado? (1 = básico · 5 = muy complejo)",    "min": 1, "max": 5, "step": 1, "default": 3, "group": "sesion",  "dimension": "positive", "weight": 0},
]


def coach_question_defs() -> List[dict]:
    return COACH_QUESTION_DEFS


def coach_questions():
    return [(q["key"], q["label"]) for q in COACH_QUESTION_DEFS]


def coach_score_breakdown(ans: Dict) -> dict:
    """Score 0-100 that represents the team's readiness as observed by the coach."""
    pos_sum = pos_w = risk_sum = risk_w = 0.0
    details = []
    for q in COACH_QUESTION_DEFS:
        if q["weight"] == 0:
            continue
        raw = ans.get(q["key"], q.get("default", 3))
        item_score = _norm_1_5(raw)
        item = {
            "key": q["key"],
            "label": q["label"],
            "raw": raw,
            "score": item_score,
            "dimension": q["dimension"],
            "weight": q["weight"],
            "group": q["group"],
        }
        details.append(item)
        if q["dimension"] == "positive":
            pos_sum += item_score * q["weight"]
            pos_w += q["weight"]
        else:
            risk_sum += item_score * q["weight"]
            risk_w += q["weight"]

    positive_avg = (pos_sum / pos_w) if pos_w else 50.0
    risk_avg = (risk_sum / risk_w) if risk_w else 0.0
    total = max(0.0, min(100.0, 0.65 * positive_avg + 0.35 * (100.0 - risk_avg)))

    return {
        "score": float(total),
        "positive_avg": float(positive_avg),
        "risk_avg": float(risk_avg),
        "details": details,
        "active_keys": [q["key"] for q in COACH_QUESTION_DEFS],
    }
