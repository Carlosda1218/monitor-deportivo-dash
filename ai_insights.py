"""
ai_insights.py
==============
Integración con la API de Claude para generar análisis narrativos
de coaching basados en el informe del analysis_engine.

Uso principal:
    from ai_insights import generate_coaching_note
    note = generate_coaching_note(report, athlete_name="Carlos", sport="taekwondo")

La función es síncrona y retorna un string con el análisis.
Si no hay API key configurada, retorna un mensaje de aviso en lugar de fallar.

Configuración:
    Variable de entorno: ANTHROPIC_API_KEY
    O bien: archivo .env en la raíz del proyecto con ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

# ── Caché en memoria ──────────────────────────────────────────────────────────
_CACHE_TTL = 600  # segundos (10 min)
_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, note)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


_AI_TIMEOUT_SECONDS = max(2.0, _env_float("COMBATIQ_AI_TIMEOUT_SECONDS", 8.0))

# Modelos y timeouts por nivel de análisis
_MODEL_OPUS   = "claude-opus-4-7"    # análisis de combate premium (tool use + thinking)
_MODEL_SONNET = "claude-sonnet-4-6"  # coaching notes, duel insight
_MODEL_HAIKU  = "claude-haiku-4-5"   # team summaries (volumen alto, latencia baja)

_TIMEOUT_HEAVY  = max(60.0, _env_float("COMBATIQ_AI_TIMEOUT_HEAVY",  60.0))
_TIMEOUT_MEDIUM = max(20.0, _env_float("COMBATIQ_AI_TIMEOUT_MEDIUM", 20.0))
_TIMEOUT_LIGHT  = max(8.0,  _env_float("COMBATIQ_AI_TIMEOUT_LIGHT",   8.0))

_TIMEOUT_BY_MODEL = {
    _MODEL_OPUS:   _TIMEOUT_HEAVY,
    _MODEL_SONNET: _TIMEOUT_MEDIUM,
    _MODEL_HAIKU:  _TIMEOUT_LIGHT,
}


def _anthropic_client(api_key: str, timeout: float = None):
    import anthropic
    t = timeout if timeout is not None else _AI_TIMEOUT_SECONDS
    return anthropic.Anthropic(api_key=api_key, timeout=t, max_retries=0)


def _message_kwargs(model: str, max_tokens: int, messages: list, prompt_or_none=None) -> dict:
    """Construye kwargs para messages.create con thinking si el modelo lo soporta."""
    kw = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if not model.startswith("claude-haiku"):
        kw["thinking"] = {"type": "adaptive"}
    return kw


def _cache_key(report: dict, athlete_name: str, sport: str) -> str:
    acwr = report.get("acwr", {}).get("ratio", 0)
    hrv  = report.get("hrv", {}).get("today_rmssd", 0)
    well = report.get("wellness", {}).get("latest_score", 0)
    payload = f"{athlete_name}|{sport}|{report.get('generated_at', '')}|{acwr:.3f}|{hrv}|{well}"
    return hashlib.md5(payload.encode()).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: str) -> None:
    _cache[key] = (time.time(), value)
    # Evita crecimiento indefinido: limpia entradas caducadas si hay más de 50
    if len(_cache) > 50:
        now = time.time()
        expired = [k for k, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
        for k in expired:
            _cache.pop(k, None)
        while len(_cache) > 50:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)


def _audience_kind(audience: str = "") -> str:
    """Normaliza el rol que recibira la lectura IA."""
    value = (audience or "").strip().lower()
    if value in {"coach", "entrenador", "admin"}:
        return "coach"
    return "athlete"


def _viewer_name(name: str = "", fallback: str = "usuario") -> str:
    text = (name or "").strip()
    return text or fallback


def _load_api_key() -> Optional[str]:
    """Intenta cargar la API key desde env o .env."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # Intenta leer .env manualmente (sin dependencia de python-dotenv)
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _extra_sections(extra: dict) -> str:
    """Builds optional prompt sections from competition, weight, and nutrition context."""
    parts = []

    comp = extra.get("competition")
    if comp:
        days = comp.get("days_until")
        loc  = f" en {comp['location']}" if comp.get("location") else ""
        days_str = f"{days} días" if days is not None else "próximamente"
        parts.append(
            f"COMPETENCIA PRÓXIMA:\n"
            f"  Evento: {comp.get('name', 'Competencia')}{loc}\n"
            f"  Fecha: {comp.get('event_date', '—')} ({days_str})"
        )
    else:
        parts.append("COMPETENCIA PRÓXIMA:\n  Sin evento registrado en los próximos 30 días")

    weight = extra.get("weight")
    if weight:
        curr   = weight.get("current_kg")
        target = weight.get("target_kg")
        trend  = weight.get("trend_7d")
        curr_str   = f"{curr:.1f} kg" if curr is not None else "sin datos"
        target_str = f" · Objetivo: {target:.1f} kg" if target is not None else ""
        diff_str   = f" · Diferencia objetivo: {curr - target:+.1f} kg" if (curr and target) else ""
        trend_str  = f" · Tendencia 7d: {trend:+.1f} kg" if trend is not None else ""
        parts.append(
            f"PESO:\n"
            f"  Peso actual: {curr_str}{target_str}{diff_str}{trend_str}\n"
            f"  Registros últimos 7 días: {weight.get('days_logged', 0)}"
        )
    else:
        parts.append("PESO:\n  Sin registros recientes")

    nutri = extra.get("nutrition")
    if nutri:
        adh  = nutri.get("avg_adherence_pct")
        kcal = nutri.get("avg_kcal")
        adh_str  = f"{adh:.0f}%" if adh is not None else "sin datos"
        kcal_str = f" · {kcal:.0f} kcal/día (media)" if kcal is not None else ""
        parts.append(
            f"NUTRICIÓN (últimos 7 días):\n"
            f"  Adherencia media: {adh_str}{kcal_str}\n"
            f"  Días registrados: {nutri.get('days_logged', 0)}"
        )
    else:
        parts.append("NUTRICIÓN:\n  Sin registros recientes")

    return "\n\n".join(parts)


def _sport_context_block(sport: str) -> str:
    """Bloque de contexto deportivo para guiar recomendaciones específicas al deporte."""
    s = (sport or "").lower()
    if s in ("taekwondo", "tkd"):
        return (
            "CONTEXTO TAEKWONDO:\n"
            "  Técnicas de referencia: Dolio chagui, Ap chagui, Bandal chagui, Dobit chagui.\n"
            "  Recuperación activa: shadow TKD suave, estiramientos cadera/isquiotibiales, foam rolling piernas.\n"
            "  Fuerza específica: sentadilla búlgara, hip thrust, pliometría de piernas (drop jumps, box jumps).\n"
            "  Indicadores tácticos: simetría pierna dominante/no-dominante, velocidad de pívot, distancia de combate."
        )
    elif s in ("boxeo", "box"):
        return (
            "CONTEXTO BOXEO:\n"
            "  Técnicas de referencia: jab (1), cross (2), hook (3), uppercut (4), combinaciones sobre saco/sparring.\n"
            "  Recuperación activa: saltar la cuerda suave, shadow boxing sin contacto, estiramientos hombros/antebrazos.\n"
            "  Fuerza específica: press de banca, remo, rotaciones de core, medicine ball slams.\n"
            "  Indicadores tácticos: ritmo de golpeo, defensa/esquives, clinch, gestión de distancia."
        )
    return ""


def _build_prompt(report: dict, athlete_name: str, sport: str, role: str = "coach", extra: dict = None) -> str:
    """
    Construye el prompt para Claude a partir del informe estructurado.
    """
    acwr    = report.get("acwr", {})
    hrv     = report.get("hrv", {})
    well    = report.get("wellness", {})
    imu     = report.get("imu", {})
    alerts  = report.get("alerts", [])

    # Alertas activas (solo warning y danger)
    active_alerts = [a for a in alerts if a.get("level") in ("warning", "danger")]
    alert_lines = "\n".join(
        f"  - [{(a.get('level') or 'warning').upper()}] {a.get('title', 'Alerta')}: {a.get('message', '')}"
        for a in active_alerts
    ) or "  - Sin alertas críticas activas"

    # Construir contexto limpio
    acwr_ratio = acwr.get("ratio")
    acwr_str   = f"{acwr_ratio:.2f}" if acwr_ratio else "sin datos"
    hrv_delta  = hrv.get("delta_pct")
    hrv_str    = f"{hrv_delta:+.1f}% vs baseline" if hrv_delta is not None else "sin datos"
    well_score = well.get("latest_score")
    well_str   = f"{well_score:.0f}/100" if well_score is not None else "sin datos"
    imu_hits   = imu.get("total_hits", 0)
    imu_int    = imu.get("avg_intensity", 0.0)

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )

    extra_block  = _extra_sections(extra or {})
    sport_block  = _sport_context_block(sport)
    sport_section = f"\n{sport_block}\n" if sport_block else ""

    return f"""Eres el asistente de análisis deportivo de CombatIQ, especializado en deportes de combate.

Tienes los datos de rendimiento de {athlete_name}, atleta de {sport_label}.

== DATOS DEL INFORME ==

CARGA DE ENTRENAMIENTO (ACWR):
  Ratio aguda/crónica: {acwr_str}
  Carga aguda (7d): {acwr.get('acute_load', 0):.0f} UA
  Carga crónica (28d): {acwr.get('chronic_load', 0):.0f} UA
  Zona: {acwr.get('zone', 'desconocida')}
  Tendencia: {acwr.get('trend', 'desconocida')}

VARIABILIDAD CARDIACA (HRV):
  RMSSD hoy: {hrv.get('today_rmssd', 'N/D')} ms
  Baseline 30d: {hrv.get('baseline_rmssd', 'N/D')} ms
  Variación: {hrv_str}
  Estado SNC: {hrv.get('zone', 'desconocido')}

BIENESTAR SUBJETIVO (wellness):
  Puntuación hoy: {well_str}
  Promedio 14d: {well.get('avg_score', 'N/D')}
  Días bajo 50/100: {well.get('low_days', 0)}
  Tendencia: {well.get('trend', 'desconocida')}

MÉTRICAS TÉCNICAS (IMU, últimos 28 días):
  Total acciones registradas: {imu_hits}
  Intensidad media: {imu_int:.1f}g
  Intensidad máxima: {imu.get('peak_intensity', 0.0):.1f}g
  Tendencia volumen: {imu.get('trend', 'desconocida')}

{extra_block}
{sport_section}
ALERTAS ACTIVAS:
{alert_lines}

== INSTRUCCIONES ==

Redacta un análisis de coaching profesional con estas secciones:

1. **Estado actual** (2-3 frases): síntesis directa de cómo llega el atleta hoy.
2. **Puntos de atención** (lista de 2-4 ítems): lo más urgente a vigilar o corregir.
3. **Recomendaciones concretas** (lista de 3-5 ítems): acciones específicas para las próximas 48-72h; nombra técnicas y ejercicios propios del deporte usando el contexto arriba.
4. **Proyección a 7 días** (1-2 frases): qué esperar si se siguen las recomendaciones.

Tono: profesional, directo, orientado al rendimiento. Sin frases genéricas. Usa los datos numéricos cuando refuercen el argumento. Si hay competencia próxima, prioriza la gestión de carga y peso. Máximo 350 palabras."""


def _build_prompt_athlete(report: dict, athlete_name: str, sport: str, extra: dict = None) -> str:
    """Prompt orientado al atleta — tono directo, segunda persona, acciones concretas."""
    acwr   = report.get("acwr", {})
    hrv    = report.get("hrv", {})
    well   = report.get("wellness", {})
    imu    = report.get("imu", {})
    alerts = report.get("alerts", [])

    active_alerts = [a for a in alerts if a.get("level") in ("warning", "danger")]
    alert_lines = "\n".join(
        f"  - [{(a.get('level') or 'warning').upper()}] {a.get('title', 'Alerta')}: {a.get('message', '')}"
        for a in active_alerts
    ) or "  - Sin alertas críticas"

    acwr_ratio = acwr.get("ratio")
    acwr_str   = f"{acwr_ratio:.2f}" if acwr_ratio else "sin datos"
    hrv_delta  = hrv.get("delta_pct")
    hrv_str    = f"{hrv_delta:+.1f}% vs tu baseline" if hrv_delta is not None else "sin datos"
    well_score = well.get("latest_score")
    well_str   = f"{well_score:.0f}/100" if well_score is not None else "sin datos"

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )

    extra_block  = _extra_sections(extra or {})
    sport_block  = _sport_context_block(sport)
    sport_section = f"\n{sport_block}\n" if sport_block else ""

    return f"""Eres el asistente personal de rendimiento de CombatIQ. Hablas directamente con {athlete_name}, atleta de {sport_label}.

Actúa como un preparador físico de élite hablando de tú a tú con su atleta — directo, motivador, concreto.

== TUS DATOS DE HOY ==

CARGA (ACWR): {acwr_str} · Zona: {acwr.get('zone', 'desconocida')} · Tendencia: {acwr.get('trend', 'desconocida')}
HRV: {hrv_str} · Estado SNC: {hrv.get('zone', 'desconocido')}
BIENESTAR: {well_str} · Tendencia: {well.get('trend', 'desconocida')}
ACCIONES IMU (28d): {imu.get('total_hits', 0)} · Intensidad media: {imu.get('avg_intensity', 0.0):.1f}g

{extra_block}
{sport_section}
ALERTAS:
{alert_lines}

== INSTRUCCIONES ==

Escríbele a {athlete_name} un mensaje de coaching personal con estas secciones:

1. **Cómo llegas hoy** (2 frases): dile exactamente en qué estado está su cuerpo, con los datos reales.
2. **Lo que debes vigilar** (2-3 ítems): sus puntos de riesgo o atención más urgentes.
3. **Tu plan para las próximas 48h** (3-4 ítems): acciones concretas nombradas con técnicas y ejercicios propios del deporte — entrena, descansa, come, controla el peso si hay competencia próxima.
4. **Mensaje motivador** (1 frase): algo específico a su situación real, sin frases genéricas.

Tono: cercano, directo, usa los datos numéricos cuando refuercen el mensaje. Si hay competencia próxima, menciónala y ajusta el plan. Sin palabrería. Máximo 280 palabras."""


def generate_athlete_note(
    report: dict,
    athlete_name: str = "atleta",
    sport: str = "combate",
    model: str = None,
    extra: dict = None,
) -> str:
    """
    Genera una nota de coaching en segunda persona para el atleta.

    Versión orientada al deportista (tono directo, acciones concretas para las próximas 48h).
    Incluye contexto de competencia próxima, peso y nutrición si está disponible.
    """
    api_key = _load_api_key()
    if not api_key:
        return (
            "**Análisis AI no disponible**\n\n"
            "Configura tu `ANTHROPIC_API_KEY` en el archivo `.env` para activar tu asesor personal."
        )

    key = _cache_key(report, f"athlete_{athlete_name}", sport)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _model = model or _MODEL_SONNET
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        prompt = _build_prompt_athlete(report, athlete_name=athlete_name, sport=sport, extra=extra or {})
        kw = _message_kwargs(_model, 600, [{"role": "user", "content": prompt}])
        message = client.messages.create(**kw)
        if not message.content:
            raise ValueError("Respuesta vacía de la API")
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else ""
        if not note:
            raise ValueError("Sin contenido de texto en la respuesta")
        _cache_set(key, note)
        return note
    except Exception as exc:
        return f"**Error al generar análisis AI:** {exc}"


def _build_prompt_team(team_data: dict, sport: str) -> str:
    """Prompt para resumen operativo del equipo orientado al coach."""
    athlete_count = team_data.get("athlete_count", 0)
    checkins = team_data.get("checkins", 0)
    ecg_ready = team_data.get("ecg_ready", 0)
    red_athletes = team_data.get("red_athletes", [])
    focus = team_data.get("focus_names", [])
    pending = team_data.get("pending_names", [])
    upcoming = team_data.get("upcoming_comps", [])

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )
    red_str = ", ".join(red_athletes) if red_athletes else "ninguno"
    focus_str = ", ".join(focus) if focus else "sin datos disponibles"
    pending_str = ", ".join(pending) if pending else "todos tienen check-in"

    comp_lines = []
    for c in upcoming[:5]:
        comp_lines.append(f"  - {c.get('name', '?')}: {c.get('event', '?')} en {c.get('days', '?')}d ({c.get('date', '?')})")
    comp_str = "\n".join(comp_lines) if comp_lines else "  - Sin competencias próximas en 60 días"

    return f"""Eres el asistente de CombatIQ para el análisis diario del equipo de {sport_label}.

DATOS DEL EQUIPO HOY:
  Total atletas: {athlete_count}
  Con check-in reciente: {checkins} de {athlete_count}
  Con señales ECG disponibles: {ecg_ready} de {athlete_count}
  En racha roja (bienestar bajo 3+ días consecutivos): {red_str}
  Prioridad del día (check-in o ECG listos): {focus_str}
  Pendientes de check-in: {pending_str}

COMPETENCIAS PRÓXIMAS (60 días):
{comp_str}

INSTRUCCIONES:
Redacta un resumen operativo del equipo para el coach. Máximo 200 palabras.

Estructura:
1. **Estado del equipo hoy** (2 frases): cuántos tienen lectura, qué dice la cobertura de datos.
2. **Prioridades inmediatas** (2-3 ítems): a quién atender primero y por qué.
3. **Recomendación del día** (1-2 frases): enfoque del entrenamiento colectivo considerando el contexto.

Tono: directo, operativo, sin frases genéricas. Usa los datos numéricos reales. Si hay atletas en racha roja o competencias próximas, priorízalos."""


def generate_team_summary(
    team_data: dict,
    sport: str = "combate",
    model: str = None,
    coach_id: int = 0,
) -> str:
    """Genera un resumen operativo del equipo para el coach. Retorna '' si no hay API key."""
    api_key = _load_api_key()
    if not api_key:
        return ""

    from datetime import datetime
    day = datetime.utcnow().strftime("%Y-%m-%d")
    key = hashlib.md5(f"team|{coach_id}|{sport}|{day}".encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _model = model or _MODEL_HAIKU  # team summaries: volumen alto → Haiku
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_LIGHT))
        prompt = _build_prompt_team(team_data, sport=sport)
        kw = _message_kwargs(_model, 400, [{"role": "user", "content": prompt}])
        message = client.messages.create(**kw)
        if not message.content:
            raise ValueError("Respuesta vacía de la API")
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else ""
        _cache_set(key, note)
        return note
    except Exception:
        return ""


def _predefined_session_analysis(sessions_data: list, sport: str, athlete_name: str) -> str:
    """Genera análisis predefinido inteligente sin llamar a la API."""
    if not sessions_data:
        return "No hay sesiones seleccionadas para analizar."

    sport_key = (sport or "").lower()
    sorted_sess = sorted(sessions_data, key=lambda s: s.get("ts") or "")
    lines = [f"**Análisis comparativo — {athlete_name}** ({len(sessions_data)} sesión(es))\n"]

    imu_data = [(s["session_id"], (s.get("ts") or "")[:10], s.get("imu") or {}) for s in sorted_sess]
    n_hits_vals = [(sid, ts, d.get("n_hits") or 0) for sid, ts, d in imu_data if d.get("n_hits")]
    force_vals  = [(sid, ts, d.get("mean_int_g") or 0) for sid, ts, d in imu_data if d.get("mean_int_g")]

    if n_hits_vals:
        if len(n_hits_vals) >= 2:
            delta = n_hits_vals[-1][2] - n_hits_vals[0][2]
            pct   = (delta / n_hits_vals[0][2] * 100) if n_hits_vals[0][2] > 0 else 0
            if delta > 2:
                lines.append(f"📈 **Volumen creciente:** de {n_hits_vals[0][2]} a {n_hits_vals[-1][2]} impactos (+{delta}, +{pct:.0f}%).")
            elif delta < -2:
                lines.append(f"📉 **Volumen decreciente:** de {n_hits_vals[0][2]} a {n_hits_vals[-1][2]} impactos ({delta}, {pct:.0f}%). Revisa si es descarga planificada o fatiga.")
            else:
                avg = sum(v[2] for v in n_hits_vals) / len(n_hits_vals)
                lines.append(f"➡️ **Volumen estable:** media de {avg:.0f} impactos/sesión en el período.")
        else:
            lines.append(f"🎯 **{n_hits_vals[0][2]} impactos** registrados en la sesión analizada.")

    if force_vals and len(force_vals) >= 2:
        delta_f = force_vals[-1][2] - force_vals[0][2]
        if delta_f > 0.25:
            lines.append(f"💥 **Explosividad en alza:** de {force_vals[0][2]:.2f}g a {force_vals[-1][2]:.2f}g de fuerza media.")
        elif delta_f < -0.25:
            lines.append(f"⚠️ **Explosividad bajando:** de {force_vals[0][2]:.2f}g a {force_vals[-1][2]:.2f}g. Posible fatiga acumulada o cambio técnico.")

    if len(n_hits_vals) >= 3:
        best  = max(n_hits_vals, key=lambda x: x[2])
        worst = min(n_hits_vals, key=lambda x: x[2])
        if best[0] != worst[0]:
            lines.append(f"🏆 **Mejor sesión:** #{best[0]} ({best[1]}) con {best[2]} impactos. Peor: #{worst[0]} ({worst[1]}) con {worst[2]}.")

    wellness_vals = [(s["session_id"], s.get("wellness")) for s in sorted_sess if s.get("wellness") is not None]
    if wellness_vals:
        avg_w = sum(v[1] for v in wellness_vals) / len(wellness_vals)
        if avg_w >= 70:
            lines.append(f"✅ **Bienestar alto** ({avg_w:.0f}/100 promedio). Buenas condiciones de entrenamiento.")
        elif avg_w >= 55:
            lines.append(f"🟡 **Bienestar moderado** ({avg_w:.0f}/100 promedio). Hay margen de mejora en la recuperación.")
        else:
            lines.append(f"🔴 **Bienestar bajo** ({avg_w:.0f}/100 promedio). Revisa la carga y los días de descanso.")

    if sport_key in ("taekwondo", "tkd") and force_vals:
        avg_f = sum(v[2] for v in force_vals) / len(force_vals)
        if avg_f > 3.0:
            lines.append(f"🥋 **TKD — Potencia sólida** ({avg_f:.2f}g). Trabaja variedad de técnicas para no ser predecible.")
        elif avg_f < 2.0:
            lines.append(f"🥋 **TKD — Fuerza de impacto baja** ({avg_f:.2f}g). Prioriza explosividad y velocidad de ejecución.")
    elif sport_key in ("boxeo", "box") and n_hits_vals:
        avg_h = sum(v[2] for v in n_hits_vals) / len(n_hits_vals)
        if avg_h > 35:
            lines.append(f"🥊 **Boxeo — Volumen alto.** Verifica que la calidad técnica se mantiene al final de los rounds.")
        elif avg_h < 20:
            lines.append(f"🥊 **Boxeo — Volumen bajo.** Trabaja el ritmo de golpeo si es recurrente.")

    lines.append("\n**Recomendaciones:**")
    if len(sorted_sess) >= 2 and n_hits_vals:
        trend = n_hits_vals[-1][2] - n_hits_vals[0][2] if len(n_hits_vals) >= 2 else 0
        if trend > 5:
            lines.append("- La progresión es positiva. Mantén el ritmo y añade variedad técnica para evitar adaptación.")
        elif trend < -5:
            lines.append("- El volumen baja. Evalúa si es una reducción planificada o señal de fatiga.")
        else:
            lines.append("- Volumen estable. Buen momento para introducir carga de calidad o trabajar un aspecto técnico específico.")
    if wellness_vals and sum(v[1] for v in wellness_vals) / len(wellness_vals) < 60:
        lines.append("- Bienestar bajo en el período: prioriza sueño, hidratación y recuperación activa.")
    if not n_hits_vals and not wellness_vals:
        lines.append("- Sin datos de sensores suficientes. Conecta el IMU para obtener métricas de entrenamiento.")

    return "\n".join(lines)


def _build_comparison_prompt(sessions_data: list, athlete_name: str, sport: str) -> str:
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )
    rows = []
    for s in sessions_data:
        imu = s.get("imu") or {}
        ecg = s.get("ecg") or {}
        parts = []
        if imu.get("n_hits"):    parts.append(f"{imu['n_hits']} impactos, {imu.get('mean_int_g', 0):.2f}g")
        if ecg.get("bpm"):       parts.append(f"{ecg['bpm']:.0f} BPM, RMSSD {ecg.get('rmssd', 0):.0f}ms")
        if s.get("wellness") is not None: parts.append(f"bienestar {s['wellness']:.0f}/100")
        rows.append(f"  Sesión #{s['session_id']} ({(s.get('ts') or '?')[:10]}): " +
                    (" · ".join(parts) if parts else "sin datos de sensores"))
    return (
        f"Eres el asistente de análisis deportivo de CombatIQ.\n\n"
        f"Analiza estas sesiones de {athlete_name} ({sport_label}):\n\n"
        + "\n".join(rows) +
        "\n\nGenera un análisis de coaching breve con:\n"
        "1. **Tendencia principal** (1-2 frases).\n"
        "2. **Puntos de atención** (2-3 ítems).\n"
        "3. **Recomendaciones** (2-3 ítems concretos).\n\n"
        "Tono: directo, basado en los datos. Máximo 250 palabras. En español."
    )


def generate_session_comparison(
    sessions_data: list,
    athlete_name: str = "el atleta",
    sport: str = "combate",
    model: str = None,
) -> str:
    """
    Genera análisis comparativo de sesiones.
    sessions_data: lista de dicts {session_id, ts, imu, ecg, wellness}.
    Si no hay API key usa análisis predefinido inteligente.
    """
    predefined = _predefined_session_analysis(sessions_data, sport, athlete_name)

    api_key = _load_api_key()
    if not api_key:
        return predefined

    key = hashlib.md5(
        f"cmp|{athlete_name}|{sport}|{'|'.join(str(s.get('session_id')) for s in sessions_data)}".encode()
    ).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _model = model or _MODEL_SONNET
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        prompt = _build_comparison_prompt(sessions_data, athlete_name, sport)
        kw = _message_kwargs(_model, 700, [{"role": "user", "content": prompt}])
        message = client.messages.create(**kw)
        if not message.content:
            return predefined
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else ""
        if not note:
            return predefined
        _cache_set(key, note)
        return note
    except Exception:
        return predefined


def generate_coaching_note(
    report: dict,
    athlete_name: str = "el atleta",
    sport: str = "combate",
    model: str = None,
    extra: dict = None,
) -> str:
    """
    Genera una nota de coaching narrativa usando la API de Claude.

    Args:
        report:        salida de analysis_engine.full_report()
        athlete_name:  nombre del atleta (para personalizar el texto)
        sport:         deporte ("taekwondo" | "boxeo")
        model:         modelo Claude a usar (por defecto Haiku — rápido y económico)
        extra:         contexto adicional: competencia próxima, peso, nutrición

    Returns:
        Texto markdown con el análisis. Si la API key no está configurada,
        retorna un mensaje de aviso sin lanzar excepción.
    """
    api_key = _load_api_key()
    if not api_key:
        return (
            "**Análisis AI no disponible**\n\n"
            "Para activar el análisis narrativo con IA configura tu `ANTHROPIC_API_KEY` "
            "en el archivo `.env` de la raíz del proyecto:\n\n"
            "```\nANTHROPIC_API_KEY=sk-ant-...\n```\n\n"
            "Mientras tanto, puedes revisar los indicadores numéricos arriba."
        )

    key = _cache_key(report, athlete_name, sport)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _model = model or _MODEL_SONNET
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        prompt = _build_prompt(report, athlete_name=athlete_name, sport=sport, extra=extra or {})
        kw = _message_kwargs(_model, 700, [{"role": "user", "content": prompt}])
        message = client.messages.create(**kw)
        if not message.content:
            raise ValueError("Respuesta vacía de la API")
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else ""
        if not note:
            raise ValueError("Sin contenido de texto en la respuesta")
        _cache_set(key, note)
        return note

    except Exception as exc:
        return (
            f"**Error al generar análisis AI:** {exc}\n\n"
            "Verifica tu API key y la conexión a internet."
        )


# ── Duel biomechanics insight ──────────────────────────────────────────────────


def _predefined_duel_insight(
    duel_data: dict,
    sport: str,
    audience: str = "coach",
    athlete_name: str = "",
    coach_name: str = "",
) -> str:
    """Role-specific deterministic duel interpretation when the API is unavailable."""
    metrics = duel_data.get("metrics", {}) or {}
    frames = duel_data.get("frames_paired", 0)
    avg_d = float(metrics.get("avg_distance", 0.0) or 0.0)
    exchanges = int(metrics.get("exchange_count", 0) or 0)
    red_rom = float(metrics.get("red_lower_rom", 0.0) or 0.0)
    blue_rom = float(metrics.get("blue_lower_rom", 0.0) or 0.0)
    pressure = (metrics.get("pressure_label") or "").lower()
    sport_key = (sport or "").lower()
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo"}.get(sport_key, sport or "combate")

    role = _audience_kind(audience)
    athlete_ref = _viewer_name(athlete_name, "atleta")
    coach_ref = _viewer_name(coach_name, "coach")
    if role == "coach":
        lines = [f"**Lectura tactica para coach - {sport_label}**\n"]
        lines.append(
            f"{coach_ref}, usa esta lectura como mapa de revision para {athlete_ref}: "
            "distancia, iniciativa y momentos que conviene volver a mirar en video."
        )
    else:
        lines = [f"**Tu lectura del combate - {sport_label}**\n"]
        lines.append(
            f"{athlete_ref}, esta lectura traduce las graficas a decisiones simples: "
            "cuando presionaste, cuando te cerraron la distancia y que trabajar en la proxima sesion."
        )

    if avg_d < 0.20:
        lines.append("**Distancia:** combate muy cerrado, con muchos momentos de intercambio directo.")
    elif avg_d < 0.32:
        lines.append("**Distancia:** rango corto, ideal para revisar entradas, salidas y contraataques.")
    elif avg_d < 0.46:
        lines.append("**Distancia:** rango medio, con alternancia entre cerrar y volver a abrir espacio.")
    else:
        lines.append("**Distancia:** combate largo, con menos choque directo y mas gestion del espacio.")

    if exchanges >= 15:
        lines.append(f"**Ritmo:** muy activo, con {exchanges} posibles intercambios detectados.")
    elif exchanges >= 7:
        lines.append(f"**Ritmo:** actividad media, con {exchanges} intercambios a revisar.")
    else:
        lines.append(f"**Ritmo:** pocos intercambios ({exchanges}); lectura mas tactica que de volumen.")

    if "rojo" in pressure:
        lines.append("**Iniciativa:** el peto rojo presiono mas hacia el azul.")
    elif "azul" in pressure:
        lines.append("**Iniciativa:** el peto azul fue mas activo buscando el intercambio.")
    else:
        lines.append("**Iniciativa:** presion equilibrada; no hay dominio claro en avance.")

    lines.append("\n**Lectura de piernas:**")
    if red_rom > 0 and blue_rom > 0:
        if red_rom > 155 and blue_rom > 155:
            lines.append(f"Ambos mostraron extension alta: rojo {red_rom:.0f} deg y azul {blue_rom:.0f} deg.")
        elif red_rom > blue_rom + 6:
            lines.append(f"Rojo alcanzo mayor amplitud de pierna ({red_rom:.0f} deg vs {blue_rom:.0f} deg).")
        elif blue_rom > red_rom + 6:
            lines.append(f"Azul alcanzo mayor amplitud de pierna ({blue_rom:.0f} deg vs {red_rom:.0f} deg).")
        else:
            lines.append(f"Amplitud similar en ambos: rojo {red_rom:.0f} deg y azul {blue_rom:.0f} deg.")
    else:
        lines.append("Sin suficientes datos de extension de pierna; conviene revisar frames clave antes de concluir.")

    if role == "coach":
        lines.append("\n**Plan para el coach:**")
        lines.append("- Revisa los 3 momentos de menor distancia y decide si son entradas limpias, choques o salidas tardias.")
        lines.append("- Convierte la tendencia de presion en una tarea: entrar, cortar salida o contraatacar despues del primer paso.")
        lines.append("- Si la confianza baja, valida manualmente el peto antes de ajustar la carga de entrenamiento.")
    else:
        lines.append("\n**Que haces ahora:**")
        lines.append("- Mira los momentos donde la distancia baja: ahi esta tu toma de decision real.")
        lines.append("- Trabaja una salida despues de atacar y una respuesta cuando el rival te cierre el espacio.")
        lines.append("- Si el sistema marca baja confianza, usa la lectura como pista, no como veredicto.")

    if frames < 20:
        lines.append(f"\nNota de confianza: solo {frames} frames con ambos visibles; el analisis es indicativo.")

    return "\n".join(lines)


def _speed_block(speed_data: dict) -> str:
    """Formats yolo_tracker speed data for Claude prompts."""
    if not speed_data or speed_data.get("error"):
        return ""
    lines = ["VELOCIDADES REALES (YOLOv8 + OpenVINO):"]
    for color, label in (("azul", "Peto azul"), ("rojo", "Peto rojo")):
        d = speed_data.get(color) or {}
        if not d or d.get("error"):
            continue
        max_k  = d.get("max_kick_ms", 0)
        avg_dp = d.get("avg_displacement_ms", 0)
        peaks  = d.get("peak_kicks") or []
        lines.append(
            f"  {label}: vel.pateo máx={max_k:.1f}m/s · "
            f"desplazamiento medio={avg_dp:.1f}m/s · picos={len(peaks)}"
        )
    # Context reference values for TKD WT
    lines.append("  (Referencia élite WTF: pateo 10-16 m/s · desplazamiento 2-4 m/s)")
    return "\n".join(lines)


def _build_duel_insight_prompt(
    duel_data: dict,
    sport: str,
    audience: str = "coach",
    athlete_name: str = "",
    coach_name: str = "",
) -> str:
    """Builds an audience-specific prompt for the red-vs-blue duel reading."""
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )
    coaching = duel_data.get("coaching", {}) or {}
    metrics = duel_data.get("metrics", {}) or {}
    frames = duel_data.get("frames_paired", 0)

    avg_d = float(metrics.get("avg_distance", 0.0) or 0.0)
    min_d = float(metrics.get("min_distance", 0.0) or 0.0)
    exchanges = int(metrics.get("exchange_count", 0) or 0)
    red_pres = float(metrics.get("red_pressure", 0.0) or 0.0)
    blue_pres = float(metrics.get("blue_pressure", 0.0) or 0.0)
    pressure = metrics.get("pressure_label", "")
    red_rom = float(metrics.get("red_lower_rom", 0.0) or 0.0)
    blue_rom = float(metrics.get("blue_lower_rom", 0.0) or 0.0)
    conf = float(coaching.get("confidence_score", 0.0) or 0.0)
    red_vel = float(metrics.get("red_peak_ang_vel", 0.0) or 0.0)
    blue_vel = float(metrics.get("blue_peak_ang_vel", 0.0) or 0.0)
    red_avg_vel = float(metrics.get("red_avg_ang_vel", 0.0) or 0.0)
    blue_avg_vel = float(metrics.get("blue_avg_ang_vel", 0.0) or 0.0)

    rounds = duel_data.get("rounds", []) or []
    rows = []
    for r in [r for r in rounds if r.get("phase") == "fight"]:
        rn = r.get("round", "?")
        dur = float(r.get("t_end", 0) or 0) - float(r.get("t_start", 0) or 0)
        rows.append(
            f"  Round {rn}: {dur:.0f}s - {r.get('exchange_count', 0)} intercambios - "
            f"rojo {float(r.get('red_peak_ang_vel', 0) or 0):.0f} deg/s / "
            f"azul {float(r.get('blue_peak_ang_vel', 0) or 0):.0f} deg/s"
        )
    rounds_block = "Rounds detectados:\n" + "\n".join(rows) + "\n" if rows else ""

    sport_block = _sport_context_block(sport)
    sport_section = f"\n{sport_block}\n" if sport_block else ""
    speed_blk = _speed_block(duel_data.get("speed_data"))
    speed_sect = f"\n{speed_blk}\n" if speed_blk else ""

    role = _audience_kind(audience)
    athlete_ref = _viewer_name(athlete_name, "el atleta")
    coach_ref = _viewer_name(coach_name, "coach")
    if role == "coach":
        audience_block = (
            f"AUDIENCIA: coach. Habla directamente a {coach_ref}. "
            f"Refierete a {athlete_ref} como su atleta cuando corresponda. "
            "No escribas un segundo mensaje para el atleta dentro del mismo texto."
        )
        action_title = "3 decisiones de entrenamiento"
        action_style = "decisiones para observar, corregir o cargar en la siguiente sesion"
    else:
        audience_block = (
            f"AUDIENCIA: atleta. Habla directamente a {athlete_ref} en segunda persona. "
            "No des instrucciones al coach ni mezcles ambos roles."
        )
        action_title = "3 cosas que haces ahora"
        action_style = "acciones simples que el atleta pueda ejecutar en entrenamiento"

    return f"""Eres el analista de CombatIQ, especializado en deportes de combate.
Tu trabajo es traducir datos biomecanicos de un combate de {sport_label} a lenguaje deportivo claro para una sola audiencia.
{audience_block}

== DATOS DEL COMBATE ==
Frames con ambos atletas visibles: {frames}
Confianza del analisis: {conf:.0f}%
Distancia media entre atletas: {avg_d * 170:.0f} cm estimados ({avg_d:.3f} norm)
Distancia minima registrada: {min_d * 170:.0f} cm estimados
Intercambios detectados: {exchanges}
Presion rojo hacia azul: {red_pres:.1%}
Presion azul hacia rojo: {blue_pres:.1%}
Tendencia: {pressure}
ROM pierna rojo (rodilla+cadera): {red_rom:.1f} deg
ROM pierna azul (rodilla+cadera): {blue_rom:.1f} deg
Velocidad angular pico rojo: {red_vel:.0f} deg/s
Velocidad angular pico azul: {blue_vel:.0f} deg/s
Velocidad angular media rojo: {red_avg_vel:.0f} deg/s
Velocidad angular media azul: {blue_avg_vel:.0f} deg/s
{rounds_block}{speed_sect}{sport_section}
== INSTRUCCIONES ==
Escribe con estas 4 secciones. No declares ganador ni puntuacion oficial.
Evita jerga biomecanica pesada; explica que significa cada dato en combate real.
Si la confianza es baja o hay pocos frames, dilo con claridad antes de recomendar.

1. **Lo que paso** (3-4 frases): tactica, distancia, iniciativa e intercambios.
2. **Lo que dicen las piernas** (2 frases): amplitud, velocidad y que puede significar en {sport_label}.
3. **Velocidad y rounds** (2 frases): compara velocidad, desplazamiento y posible caida/mejora por round si existe.
4. **{action_title}** (lista): {action_style}, con ejercicios o focos concretos del deporte.

Tono: directo, util, humano y defendible ante atleta, coach e inversor. Maximo 320 palabras. En espanol."""


def generate_duel_insight(
    duel_data: dict,
    sport: str = "taekwondo",
    model: str = None,
    audience: str = "coach",
    athlete_name: str = "",
    coach_name: str = "",
) -> str:
    """
    Genera interpretación narrativa atleta-amigable del análisis rojo vs azul.

    duel_data: output de analyze_posture_video(target='duel') — el dict completo.
    Usa fallback determinista si no hay API key configurada.
    """
    api_key = _load_api_key()
    if not api_key:
        return _predefined_duel_insight(
            duel_data,
            sport,
            audience=audience,
            athlete_name=athlete_name,
            coach_name=coach_name,
        )

    metrics  = duel_data.get("metrics", {})
    frames   = duel_data.get("frames_paired", 0)
    key = hashlib.md5(
        (
            f"duel|{sport}|{_audience_kind(audience)}|{athlete_name}|{coach_name}|"
            f"{frames}|{metrics.get('avg_distance', 0):.3f}|{metrics.get('exchange_count', 0)}"
        ).encode()
    ).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _model = model or _MODEL_SONNET
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        prompt = _build_duel_insight_prompt(
            duel_data,
            sport=sport,
            audience=audience,
            athlete_name=athlete_name,
            coach_name=coach_name,
        )
        kw = _message_kwargs(_model, 600, [{"role": "user", "content": prompt}])
        message = client.messages.create(**kw)
        if not message.content:
            return _predefined_duel_insight(
                duel_data,
                sport,
                audience=audience,
                athlete_name=athlete_name,
                coach_name=coach_name,
            )
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else ""
        if not note:
            return _predefined_duel_insight(
                duel_data,
                sport,
                audience=audience,
                athlete_name=athlete_name,
                coach_name=coach_name,
            )
        _cache_set(key, note)
        return note
    except Exception:
        return _predefined_duel_insight(
            duel_data,
            sport,
            audience=audience,
            athlete_name=athlete_name,
            coach_name=coach_name,
        )


# ── Análisis estructurado de sesión de combate (tool use + Opus) ───────────────

_COMBAT_TOOLS = [
    {
        "name": "flag_finding",
        "description": (
            "Registra un hallazgo técnico, físico, táctico o psicológico significativo "
            "detectado en los datos de la sesión de combate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["tecnica", "fisico", "tactica", "psicologico"],
                    "description": "Categoría del hallazgo"
                },
                "finding": {
                    "type": "string",
                    "description": "Descripción concisa del hallazgo (1 frase)"
                },
                "evidence": {
                    "type": "string",
                    "description": "Dato cuantitativo que lo respalda (ej: 'R3: 1 dado vs 7 recibido')"
                },
                "severity": {
                    "type": "string",
                    "enum": ["positivo", "observar", "corregir", "urgente"],
                    "description": "Nivel de atención requerido"
                },
                "drill": {
                    "type": "string",
                    "description": "Ejercicio correctivo específico si aplica (puede omitirse en hallazgos positivos)"
                }
            },
            "required": ["category", "finding", "evidence", "severity"]
        }
    },
    {
        "name": "injury_risk",
        "description": "Señala un patrón que indica riesgo de lesión basado en los datos biométricos o de impacto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_type": {
                    "type": "string",
                    "description": "Tipo de riesgo (ej: 'sobrecarga_rodilla', 'fatiga_central', 'asimetria')"
                },
                "indicator": {
                    "type": "string",
                    "description": "Qué dato lo señala"
                },
                "value": {
                    "type": "string",
                    "description": "Valor observado"
                },
                "recommendation": {
                    "type": "string",
                    "description": "Acción preventiva concreta"
                },
                "severity": {
                    "type": "string",
                    "enum": ["bajo", "medio", "alto"],
                    "description": "Urgencia del riesgo"
                }
            },
            "required": ["risk_type", "indicator", "value", "recommendation", "severity"]
        }
    },
    {
        "name": "training_recommendation",
        "description": "Genera una recomendación de entrenamiento específica y accionable para las próximas sesiones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Prioridad (1=más urgente)"
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["48h", "semana", "mes"],
                    "description": "Cuándo implementarlo"
                },
                "area": {
                    "type": "string",
                    "description": "Área a trabajar (ej: 'explosividad_pierna_izquierda', 'recuperacion_cardio')"
                },
                "drill": {
                    "type": "string",
                    "description": "Ejercicio o técnica específica con nombre del deporte"
                },
                "sets_reps": {
                    "type": "string",
                    "description": "Volumen sugerido (ej: '3x8', '20 min', '5 rounds de 2 min')"
                },
                "rationale": {
                    "type": "string",
                    "description": "Por qué esta recomendación, en 1 frase, citando el dato que la justifica"
                }
            },
            "required": ["priority", "timeframe", "area", "drill", "rationale"]
        }
    }
]


def _build_combat_session_prompt(
    session_data: dict,
    sport: str,
    audience: str = "coach",
    viewer_name: str = "",
) -> str:
    """Prompt role-specific for the Replay ECG/IMU combat-session AI panel."""
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )
    name = session_data.get("athlete_name", "el atleta")
    ecg = session_data.get("ecg", {}) or {}
    imu = session_data.get("imu", {}) or {}
    imu_by_round = imu.get("by_round", {}) or {}
    role = _audience_kind(audience)
    viewer_ref = _viewer_name(viewer_name, "coach" if role == "coach" else name)

    rows = []
    for rn in sorted(imu_by_round.keys()):
        r = imu_by_round[rn] or {}
        rows.append(
            f"  R{rn}: {r.get('dado', 0)} dado ({float(r.get('dado_g', 0) or 0):.1f}g) / "
            f"{r.get('recibido', 0)} recibido ({float(r.get('recibido_g', 0) or 0):.1f}g)"
        )
    round_block = "DATOS POR ROUND:\n" + "\n".join(rows) + "\n\n" if rows else ""
    sport_block = _sport_context_block(sport)
    sport_section = f"\n{sport_block}\n" if sport_block else ""

    if role == "coach":
        audience_block = (
            f"AUDIENCIA: coach. Habla directamente a {viewer_ref}. "
            f"Tu objetivo es convertir los datos de {name} en decisiones de entrenamiento. "
            "No escribas un segundo mensaje para el atleta dentro del mismo texto."
        )
        final_instruction = (
            "4. Cierra con 3-4 frases para el entrenador: que viste, que te preocupa "
            "y que trabajar en la proxima micro-sesion."
        )
        recommendation_style = "decisiones de entrenamiento para el coach"
    else:
        audience_block = (
            f"AUDIENCIA: atleta. Habla directamente a {viewer_ref} en segunda persona. "
            "Traduce ECG e IMU a acciones simples para entrenar; no hables como reporte medico "
            "ni des instrucciones al coach dentro del mismo texto."
        )
        final_instruction = (
            "4. Cierra con 3-4 frases para el atleta: que hizo bien, que ajustar "
            "y que tarea concreta llevar a la proxima sesion."
        )
        recommendation_style = "acciones que el atleta pueda ejecutar"

    return f"""Eres un entrenador de alto rendimiento especializado en {sport_label}.
{audience_block}

DATOS DE LA SESION DE {name}:
ECG:
  FC media sesion: {ecg.get('bpm', 0)} bpm
  SDNN: {ecg.get('sdnn', 0)} ms
  RMSSD: {ecg.get('rmssd', 0)} ms

IMU - impactos de combate:
  Total dado: {imu.get('total_dado', 0)}
  Total recibido: {imu.get('total_recibido', 0)}
  Intensidad media: {float(imu.get('avg_intensity', 0) or 0):.1f}g
  Pico: {float(imu.get('peak_intensity', 0) or 0):.1f}g

{round_block}{sport_section}
INSTRUCCIONES:
Usa lenguaje deportivo de gimnasio, no jerga medica. Los numeros son evidencia:
explicalos como ritmo, carga, intercambio, defensa y recuperacion.

1. Usa flag_finding para cada hallazgo importante (minimo 2 positivos reales + problemas concretos).
2. Usa injury_risk solo si hay algo que realmente preocupe para manana o la semana.
3. Usa training_recommendation para 3-5 {recommendation_style}, con ejercicio, volumen y razon.
{final_instruction}"""


def analyze_combat_session(
    session_data: dict,
    sport: str = "taekwondo",
    audience: str = "coach",
    viewer_name: str = "",
) -> dict:
    """
    Análisis estructurado de sesión de combate con tool use + Opus.

    session_data puede contener:
        athlete_name, age, weight_category, experience_years, days_to_competition,
        ecg: {bpm, sdnn, rmssd, by_round: {1: {bpm}, 2: ...}},
        imu: {total_dado, total_recibido, avg_intensity, peak_intensity,
              by_round: {1: {dado, dado_g, recibido, recibido_g}, ...}},
        previous_sessions: [{ts, dado, recibido, avg_g}, ...]
        session_id: int

    Retorna:
        {findings, risks, recommendations, narrative, model_used, session_id}
        o {findings:[], risks:[], recommendations:[], narrative:"", error:"...", model_used:"none"}
    """
    import json as _json

    _empty = {
        "findings": [], "risks": [], "recommendations": [],
        "narrative": "", "model_used": "none",
        "session_id": session_data.get("session_id"),
    }

    api_key = _load_api_key()
    if not api_key:
        _empty["error"] = "ANTHROPIC_API_KEY no configurada"
        return _empty

    cache_key = hashlib.md5(
        f"combat_analysis|{sport}|{session_data.get('session_id')}|"
        f"{_audience_kind(audience)}|{viewer_name}|"
        f"{session_data.get('ecg', {}).get('bpm', 0)}|"
        f"{session_data.get('imu', {}).get('total_dado', 0)}".encode()
    ).hexdigest()
    cached_str = _cache_get(cache_key)
    if cached_str:
        try:
            return _json.loads(cached_str)
        except Exception:
            pass

    try:
        client  = _anthropic_client(api_key, timeout=_TIMEOUT_HEAVY)
        prompt  = _build_combat_session_prompt(
            session_data,
            sport,
            audience=audience,
            viewer_name=viewer_name,
        )
        messages = [{"role": "user", "content": prompt}]

        findings        = []
        risks           = []
        recommendations = []
        narrative       = ""

        # Bucle de tool use (máx 8 vueltas para evitar loop infinito)
        for _turn in range(8):
            response = client.messages.create(
                model=_MODEL_OPUS,
                max_tokens=2048,
                thinking={"type": "adaptive"},
                tools=_COMBAT_TOOLS,
                messages=messages,
            )

            tool_results  = []
            text_parts    = []
            made_tool_call = False

            for block in response.content:
                if block.type == "tool_use":
                    made_tool_call = True
                    inp = block.input
                    if block.name == "flag_finding":
                        findings.append(inp)
                    elif block.name == "injury_risk":
                        risks.append(inp)
                    elif block.name == "training_recommendation":
                        recommendations.append(inp)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "OK",
                    })
                elif block.type == "text" and block.text:
                    text_parts.append(block.text)

            if response.stop_reason == "end_turn" or not made_tool_call:
                narrative = " ".join(text_parts).strip()
                break

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        result = {
            "findings": findings,
            "risks": risks,
            "recommendations": sorted(recommendations, key=lambda r: r.get("priority", 5)),
            "narrative": narrative,
            "model_used": _MODEL_OPUS,
            "session_id": session_data.get("session_id"),
        }
        _cache_set(cache_key, _json.dumps(result, ensure_ascii=False))
        return result

    except Exception as exc:
        _empty["error"] = str(exc)
        return _empty


# ── Claude Vision: análisis de frame en momento de impacto ────────────────────

def analyze_event_frame(
    frame_b64: str,
    event_data: dict,
    sport: str = "taekwondo",
    model: str = None,
    audience: str = "coach",
    athlete_name: str = "",
    viewer_name: str = "",
) -> str:
    """
    Analyzes a video frame at an impact event using Claude Vision.

    frame_b64:  JPEG frame as base64 string (no data-URI prefix)
    event_data: {type, intensity_g, timestamp_s, round}
    sport:      "taekwondo" | "boxeo"
    Returns:    2-3 sentence natural language analysis for a coach/athlete.
    """
    api_key = _load_api_key()
    if not api_key:
        return "Análisis visual no disponible — configura ANTHROPIC_API_KEY en .env"

    import json as _json

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )
    ev_type   = event_data.get("type", "impacto")
    intensity = event_data.get("intensity_g", event_data.get("intensity", 0))
    ts        = event_data.get("timestamp_s", event_data.get("t", 0))
    rn        = event_data.get("round", "?")

    type_label = {"dado": "golpe dado", "recibido": "golpe recibido"}.get(ev_type, ev_type)
    role = _audience_kind(audience)
    athlete_ref = _viewer_name(athlete_name, "el atleta")
    viewer_ref = _viewer_name(viewer_name, "coach" if role == "coach" else athlete_ref)
    if role == "coach":
        audience_line = (
            f"Habla directamente a {viewer_ref} como coach. "
            f"Convierte el fotograma en una correccion para {athlete_ref}."
        )
    else:
        audience_line = (
            f"Habla directamente a {viewer_ref} como atleta. "
            "Dale una lectura clara y una accion que pueda entrenar."
        )

    prompt = (
        f"{audience_line}\n\n"
        f"Eres entrenador de {sport_label} mirando el fotograma del video en el momento t={ts:.1f}s "
        f"(round {rn}), justo cuando se registró un {type_label} de {intensity:.1f}g.\n\n"
        f"Describe en 2-3 frases cortas lo que ves: postura del atleta, altura y trayectoria de la técnica, "
        f"y una cosa concreta que se deberia trabajar a partir de esta imagen. "
        f"Lenguaje directo de vestuario — sin tecnicismos biomecánicos."
    )

    _model = model or _MODEL_SONNET
    cache_key = hashlib.md5(
        (
            "analyze_event_frame|"
            f"{_model}|{sport_label}|{audience}|{athlete_name}|{viewer_name}|"
            f"{_json.dumps(event_data or {}, sort_keys=True, ensure_ascii=False)}|"
            f"{frame_b64}"
        ).encode("utf-8")
    ).hexdigest()
    cached_note = _cache_get(cache_key)
    if cached_note is not None:
        return cached_note

    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        message = client.messages.create(
            model=_model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": frame_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text_blocks = [b for b in message.content if hasattr(b, "text") and b.text]
        note = text_blocks[-1].text.strip() if text_blocks else "Sin respuesta del modelo."
        _cache_set(cache_key, note)
        return note
    except Exception as exc:
        return f"Error en análisis visual: {exc}"


def detect_video_events(
    video_path: str,
    sport: str = "taekwondo",
    max_frames: int = 15,
    sample_interval: float = 4.0,
    motion_threshold: float = 12.0,
    imu_events: list = None,
    target_vest: str = "azul",
) -> list:
    """
    Genera los eventos detectados desde el video usando Claude Vision.

    Cuando se proveen imu_events (lista de {t, intensity, type, round} sin ruido),
    extrae frames en esos timestamps exactos y Claude confirma/clasifica lo que
    ve en el video con la intensidad del sensor como contexto.

    Sin imu_events → fallback: muestrea frames por intervalo con filtro de movimiento.

    Retorna [{time, type, text, intensity_g, source="vision", auto=True}]
    """
    import json as _json

    try:
        import cv2 as _cv2
        import numpy as _np
    except ImportError:
        return []

    api_key = _load_api_key()
    if not api_key:
        return []

    try:
        video_mtime = os.path.getmtime(video_path)
        video_size = os.path.getsize(video_path)
    except Exception:
        video_mtime = 0
        video_size = 0

    imu_signature = ""
    if imu_events:
        chunks = []
        for ev in (imu_events or [])[:max_frames]:
            try:
                chunks.append(
                    f"{float(ev.get('t', 0) or 0):.1f}:"
                    f"{ev.get('type', '')}:"
                    f"{float(ev.get('intensity', 0) or 0):.2f}:"
                    f"{ev.get('round', '')}"
                )
            except Exception:
                chunks.append(str(ev)[:80])
        imu_signature = "|".join(chunks)
    video_events_cache_key = hashlib.md5(
        (
            "detect_video_events|"
            f"{os.path.abspath(video_path)}|{video_mtime:.3f}|{video_size}|"
            f"{sport}|{max_frames}|{sample_interval}|{motion_threshold}|"
            f"{target_vest}|{imu_signature}"
        ).encode()
    ).hexdigest()
    cached_events = _cache_get(video_events_cache_key)
    if cached_events is not None:
        try:
            return _json.loads(cached_events)
        except Exception:
            pass

    cap = _cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps          = cap.get(_cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
    duration     = total_frames / fps

    def _extract_frame(t_s: float):
        fn = int(min(t_s, duration - 0.1) * fps)
        cap.set(_cv2.CAP_PROP_POS_FRAMES, fn)
        ret, frame = cap.read()
        if not ret:
            return None
        _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 70])
        return base64.b64encode(buf).decode("utf-8")

    # ── Modo IMU-anclado: frames en los timestamps de los sensores ────────────
    if imu_events:
        impacts = [e for e in imu_events if e.get("type") != "ruido"][:max_frames]
        frames_data = []  # [(t_s, b64, imu_ev)]
        for ev in impacts:
            t_s = round(float(ev.get("t", 0)), 1)
            b64 = _extract_frame(t_s)
            if b64:
                frames_data.append((t_s, b64, ev))
        cap.release()

        if not frames_data:
            return []

        sport_label = {
            "taekwondo": "Taekwondo WT", "boxeo": "Boxeo", "box": "Boxeo",
        }.get((sport or "").lower(), sport or "combate")

        content: list[dict] = []
        for t_s, b64, ev in frames_data:
            m, s = int(t_s) // 60, int(t_s) % 60
            intensity = float(ev.get("intensity", 0))
            ev_type   = ev.get("type", "dado")
            rn        = ev.get("round", "?")
            sensor_ctx = (
                f"[t={t_s}s ({m}:{s:02d}) · round {rn} · "
                f"sensor: {'golpe dado' if ev_type=='dado' else 'golpe recibido'} {intensity:.1f}g]"
            )
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
            content.append({"type": "text", "text": sensor_ctx})

        _vest_label = "PETO AZUL" if (target_vest or "azul").lower() != "rojo" else "PETO ROJO"
        content.append({"type": "text", "text": (
            f"Eres analista de {sport_label}. El atleta seguido es el de {_vest_label}.\n\n"
            f"Para cada fotograma: confirma si el sensor IMU es correcto según lo que ves.\n"
            f"Identifica quién ejecuta la acción: azul o rojo.\n\n"
            f"Responde SOLO con array JSON sin markdown:\n"
            f'[{{"t":<s>,"type":"attack"|"defense"|"clinch"|"none","athlete":"azul"|"rojo","label":"<técnica 1-3 palabras>","intensity_g":<float>}}]\n\n'
            f"Incluye TODOS los fotogramas (incluso none). Sin explicaciones."
        )})

    # ── Modo fallback: muestreo por intervalo con filtro de movimiento ────────
    else:
        step      = max(1, int(sample_interval * fps))
        prev_gray = None
        frames_data = []  # [(t_s, b64)]

        for fn in range(0, total_frames, step):
            if len(frames_data) >= max_frames:
                break
            cap.set(_cv2.CAP_PROP_POS_FRAMES, fn)
            ret, frame = cap.read()
            if not ret:
                break
            t_s = round(fn / fps, 1)

            gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                diff   = _cv2.absdiff(gray, prev_gray)
                motion = float(_np.mean(diff))
                if motion < motion_threshold:
                    prev_gray = gray
                    continue
            prev_gray = gray

            _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 70])
            b64 = base64.b64encode(buf).decode("utf-8")
            frames_data.append((t_s, b64))

        cap.release()

        if not frames_data:
            return []

        sport_label = {
            "taekwondo": "Taekwondo WT", "boxeo": "Boxeo", "box": "Boxeo",
        }.get((sport or "").lower(), sport or "combate")

        content = []
        for t_s, b64 in frames_data:
            m, s = int(t_s) // 60, int(t_s) % 60
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
            content.append({"type": "text", "text": f"[t={t_s}s ({m}:{s:02d})]"})

        _vest_label = "PETO AZUL" if (target_vest or "azul").lower() != "rojo" else "PETO ROJO"
        content.append({"type": "text", "text": (
            f"Eres analista de {sport_label}. El atleta seguido es el de {_vest_label}.\n\n"
            f"Para cada fotograma identifica si hay un evento de combate.\n"
            f"Indica quién lo ejecuta: azul o rojo.\n\n"
            f"Responde SOLO con array JSON sin markdown:\n"
            f'[{{"t":<s>,"type":"attack"|"defense"|"clinch"|"none","athlete":"azul"|"rojo","label":"<técnica 1-3 palabras>","intensity_g":0}}]\n\n'
            f"Omite los type \"none\". Si no hay eventos, devuelve []."
        )})

    # ── Llamada a Claude ──────────────────────────────────────────────────────
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_MEDIUM)
        msg = client.messages.create(
            model=_MODEL_HAIKU,
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        raw = next((b.text for b in msg.content if hasattr(b, "text") and b.text), "[]")
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = _json.loads(raw) if raw else []

        # Build round lookup from IMU anchors (only available in IMU mode)
        _round_by_t: dict[float, int] = {}
        if imu_events:
            for _imu_ev in (imu_events or []):
                _t = round(float(_imu_ev.get("t", 0)), 1)
                _rn = _imu_ev.get("round")
                if _rn:
                    _round_by_t[_t] = int(_rn)

        _TYPE_MAP = {"attack": "attack", "defense": "defense", "clinch": "general"}
        events = []
        for ev in parsed:
            ev_type = ev.get("type", "none")
            if ev_type == "none" or ev_type not in _TYPE_MAP:
                continue
            intensity = float(ev.get("intensity_g", 0))
            t_out     = round(float(ev.get("t", 0)), 1)
            athlete   = ev.get("athlete", "")
            label     = ev.get("label", ev.get("text", ""))
            # Short display: "patada cabeza · azul (3.2g)"
            parts = [label]
            if athlete:
                parts.append(athlete)
            if intensity > 0:
                parts.append(f"{intensity:.1f}g")
            text = " · ".join(p for p in parts if p)
            events.append({
                "time":        t_out,
                "type":        _TYPE_MAP[ev_type],
                "text":        text,
                "intensity_g": intensity,
                "athlete":     athlete,
                "round":       _round_by_t.get(t_out),
                "source":      "vision",
                "auto":        True,
            })
        events = sorted(events, key=lambda e: e["time"])
        _cache_set(video_events_cache_key, _json.dumps(events, ensure_ascii=False))
        return events
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning("detect_video_events error: %s", exc)
        return []


# ── Chat asistente del coach ───────────────────────────────────────────────────

def _chat_local_fallback(message: str, context: dict = None, reason: str = "") -> str:
    """Respuesta util cuando la IA externa no esta disponible."""
    ctx = context or {}
    role = ctx.get("role", "coach")
    sport = ctx.get("sport") or "combate"
    sport_label = {"taekwondo": "Taekwondo", "tkd": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        str(sport).lower(),
        sport,
    )
    msg_l = (message or "").lower()
    asks_summary = any(w in msg_l for w in ("resumen", "hoy", "puedo hacer", "hacer hoy", "recomienda", "plan"))
    reason_txt = " (clave de IA no configurada)" if "ANTHROPIC_API_KEY" in (reason or "") else ""

    def _score_txt(value):
        if value is None or value == "":
            return "sin dato"
        try:
            return f"{float(value):.0f}/100"
        except Exception:
            return str(value)

    def _trend_brief(values):
        nums = []
        for v in values or []:
            try:
                nums.append(float(v))
            except Exception:
                pass
        if len(nums) < 2:
            return "sin tendencia suficiente"
        delta = nums[-1] - nums[0]
        if delta > 5:
            return "subiendo"
        if delta < -5:
            return "bajando"
        return "estable"

    if role == "deportista":
        athlete = ctx.get("athlete_name") or "Atleta"
        data = ctx.get("athlete_ctx") or {}
        wellness = data.get("wellness")
        trend = _trend_brief(data.get("wellness_trend"))
        ecg = data.get("ecg_last")
        comp = data.get("next_comp")
        last_session = data.get("last_session_date") or "sin fecha reciente"

        if asks_summary:
            return (
                f"Modo local: no pude conectar con la IA externa{reason_txt}, pero con tus datos puedo orientarte. "
                f"{athlete}, hoy tienes bienestar {_score_txt(wellness)}, tendencia {trend}, ultima sesion {last_session}"
                f"{f', ECG {ecg}' if ecg else ''}{f' y proxima competencia {comp}' if comp else ''}. "
                f"Para hoy: 1) si te sientes por debajo de 60/100, baja volumen y trabaja tecnica limpia; "
                f"2) haz 10-15 min de movilidad especifica de {sport_label}; "
                f"3) registra RPE y sensaciones al terminar para ajustar la carga de manana."
            )

        return (
            f"Modo local: la IA externa no respondio{reason_txt}. Puedo ayudarte con los datos cargados: "
            f"bienestar {_score_txt(wellness)}, tendencia {trend}, ultima sesion {last_session}. "
            f"Preguntame por un resumen de hoy, carga, recuperacion o preparacion para {sport_label}."
        )

    athletes = ctx.get("athletes") or []
    coach = ctx.get("coach_name") or "Coach"
    valid_scores = []
    for a in athletes:
        try:
            if a.get("wellness") is not None:
                valid_scores.append((float(a.get("wellness")), a))
        except Exception:
            pass
    valid_scores.sort(key=lambda item: item[0])
    low = [item for item in valid_scores if item[0] < 50]
    avg = sum(item[0] for item in valid_scores) / len(valid_scores) if valid_scores else None

    if asks_summary:
        if not athletes:
            return (
                f"Modo local: no pude conectar con la IA externa{reason_txt}. "
                "No tengo atletas con datos cargados en este contexto; revisa roster, check-ins y sesiones recientes."
            )
        focus = ", ".join(a.get("name", "Atleta") for _, a in low[:3]) or (
            valid_scores[0][1].get("name", "Atleta") if valid_scores else "sin foco critico"
        )
        avg_txt = f"{avg:.0f}/100" if avg is not None else "sin promedio"
        return (
            f"Modo local: no pude conectar con la IA externa{reason_txt}, pero aqui tienes lectura de equipo. "
            f"{coach}, tu grupo de {sport_label} tiene {len(athletes)} atletas en contexto y bienestar medio {avg_txt}. "
            f"Prioridad: revisar {focus}. "
            "Plan de hoy: confirma check-in, reduce carga en atletas bajo 50/100, y usa ECG/RPE para decidir quien puede hacer intensidad."
        )

    return (
        f"Modo local: la IA externa no respondio{reason_txt}. "
        f"Tengo {len(athletes)} atletas en contexto para {sport_label}; puedo resumir bienestar, detectar prioridades "
        "o proponer una accion de entrenamiento si me preguntas por el foco del dia."
    )


def generate_chat_response(
    message: str,
    history: list,
    context: dict = None,
    model: str = None,
) -> str:
    """
    Genera respuesta del asistente IA para el chat del coach.

    message:  último mensaje del coach
    history:  lista de {role, content} previos (para continuidad)
    context:  {coach_name, sport, athletes: [{name, wellness, last_session_date}]}
    Returns:  texto de respuesta (string)
    """
    api_key = _load_api_key()
    if not api_key:
        return _chat_local_fallback(message, context, reason="ANTHROPIC_API_KEY no configurada")

    ctx      = context or {}
    sport    = ctx.get("sport", "combate")
    role     = ctx.get("role", "coach")

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )

    def _trend_txt(trend: list) -> str:
        if not trend:
            return "sin historial"
        arrows = []
        for i in range(1, len(trend)):
            diff = trend[i] - trend[i - 1]
            arrows.append("↑" if diff > 3 else ("↓" if diff < -3 else "→"))
        scores = " → ".join(str(int(v)) for v in trend)
        direction = "".join(arrows)
        return f"{scores} ({direction})"

    if role == "deportista":
        athlete_nm  = ctx.get("athlete_name", "Atleta")
        athlete_ctx = ctx.get("athlete_ctx") or {}
        wellness    = athlete_ctx.get("wellness")
        n_sessions  = athlete_ctx.get("n_sessions", 0)
        last_date   = athlete_ctx.get("last_session_date", "")
        trend       = athlete_ctx.get("wellness_trend", [])
        ecg         = athlete_ctx.get("ecg_last")
        next_comp   = athlete_ctx.get("next_comp")
        w_txt       = f"{wellness}/100" if wellness is not None else "sin datos"
        system = (
            f"Eres el asistente IA de CombatIQ para {athlete_nm}, atleta de {sport_label}.\n\n"
            f"DATOS ACTUALES:\n"
            f"  - Bienestar hoy: {w_txt}\n"
            f"  - Tendencia bienestar (últimas sesiones): {_trend_txt(trend)}\n"
            f"  - Sesiones registradas: {n_sessions}\n"
            f"  - Última sesión: {last_date or 'N/A'}\n"
            + (f"  - ECG última sesión: {ecg}\n" if ecg else "")
            + (f"  - Próxima competencia: {next_comp}\n" if next_comp else "  - Sin competencias próximas registradas\n")
            + f"\nResponde como un asistente personal de alto rendimiento. Interpreta los datos con contexto "
            f"(tendencia, ECG, competición cercana). Sé motivador pero honesto. "
            f"Si no tienes el dato, dilo. Máximo 3-4 frases. En español."
        )
    else:
        coach_nm = ctx.get("coach_name", "Coach")
        athletes = ctx.get("athletes", [])
        if athletes:
            roster_lines = []
            for a in athletes[:15]:
                line = f"  - {a['name']}: bienestar {a.get('wellness', '?')}/100"
                trend = a.get("wellness_trend", [])
                if trend:
                    line += f" (tendencia: {_trend_txt(trend)})"
                if a.get("last_session_date"):
                    line += f", última sesión {a['last_session_date']}"
                if a.get("ecg_last"):
                    line += f", ECG: {a['ecg_last']}"
                if a.get("next_comp"):
                    line += f", próxima comp: {a['next_comp']}"
                if a.get("red_streak"):
                    line += " ⚠ racha roja"
                roster_lines.append(line)
            roster_block = f"PLANTILLA ({len(athletes)} atletas):\n" + "\n".join(roster_lines)
        else:
            roster_block = "PLANTILLA: sin datos cargados"
        system = (
            f"Eres el asistente IA de CombatIQ para {coach_nm}, entrenador/a de {sport_label}.\n\n"
            f"{roster_block}\n\n"
            f"Responde como un analista de alto rendimiento. Usa los datos de bienestar, tendencia, "
            f"ECG y competiciones para dar respuestas precisas. Si preguntan por un atleta específico, "
            f"cita los números. Si no tienes el dato, dilo sin inventar. "
            f"Máximo 3-4 frases por respuesta. En español."
        )

    messages = list(history[-6:]) + [{"role": "user", "content": message}]

    _model = model or _MODEL_HAIKU   # chat conversacional → Haiku (rápido, bajo costo)
    try:
        client  = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_LIGHT))
        response = client.messages.create(
            model=_model,
            max_tokens=250,
            system=system,
            messages=messages,
        )
        text_blocks = [b for b in response.content if hasattr(b, "text") and b.text]
        return text_blocks[-1].text.strip() if text_blocks else "Sin respuesta."
    except Exception as exc:
        logging.getLogger(__name__).warning("generate_chat_response fallback: %s", exc)
        return _chat_local_fallback(message, context, reason="sin conexion con IA externa")


# ── Alerta narrativa (Haiku) ───────────────────────────────────────────────────

def generate_alert_note(
    alert: dict,
    athlete_name: str,
    sport: str = "combate",
    model: str = None,
) -> str:
    """
    Genera 1 frase de contexto para el coach sobre una alerta activa.
    Returns empty string on any error (non-critical feature).
    """
    api_key = _load_api_key()
    if not api_key:
        return ""

    level   = (alert.get("level") or "warning").lower()
    title   = alert.get("title", "Alerta")
    message = alert.get("message") or alert.get("msg") or ""
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )

    prompt = (
        f"En una sola frase directa (máximo 20 palabras), dile al coach qué significa esta alerta "
        f"para {athlete_name} ({sport_label}) y qué debería hacer hoy:\n"
        f"Alerta [{level.upper()}]: {title} — {message}"
    )

    _model = model or _MODEL_HAIKU
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_LIGHT))
        response = client.messages.create(
            model=_model,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks = [b for b in response.content if hasattr(b, "text") and b.text]
        return text_blocks[-1].text.strip() if text_blocks else ""
    except Exception:
        return ""


# ── Wellbeing motivacional (Haiku) ────────────────────────────────────────────

def generate_wellbeing_message(
    wellness: float,
    sport: str = "combate",
    athlete_name: str = "atleta",
    positives: list = None,
    risks: list = None,
    model: str = None,
) -> str:
    """
    Genera 1-2 frases motivadoras personalizadas para el atleta tras guardar el check-in.
    Solo se llama cuando wellness < 65. Returns empty string on error.
    """
    api_key = _load_api_key()
    if not api_key:
        return ""

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )
    positives_str = ", ".join(positives or []) or "sin puntos fuertes destacados"
    risks_str     = ", ".join(risks or []) or "fatiga general"

    if wellness >= 50:
        tone = "de atención pero sin alarma"
    else:
        tone = "de cuidado real, el cuerpo pide descanso"

    prompt = (
        f"Escríbele a {athlete_name} ({sport_label}) 1-2 frases motivadoras personalizadas "
        f"después de su check-in de bienestar ({wellness:.0f}/100, estado {tone}). "
        f"Sus puntos fuertes hoy: {positives_str}. Señales a vigilar: {risks_str}. "
        f"Tono cercano, sin sermones. Que suene como tu entrenador de confianza. "
        f"Máximo 30 palabras. En español."
    )

    _model = model or _MODEL_HAIKU
    try:
        client = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_LIGHT))
        response = client.messages.create(
            model=_model,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks = [b for b in response.content if hasattr(b, "text") and b.text]
        return text_blocks[-1].text.strip() if text_blocks else ""
    except Exception:
        return ""


# ── Narrativa para informe PDF (Sonnet) ───────────────────────────────────────

def generate_pdf_narrative(
    athlete_data: dict,
    model: str = None,
) -> str:
    """
    Genera 2-3 párrafos narrativos para el PDF de informe del atleta.

    athlete_data: {
        name, sport, avg_wellness, last_wellness, streak,
        trend_txt, last_weight, target_weight,
        ecg: {bpm, rmssd, sdnn},
        next_comp: {name, days_until},
        n_sessions_month, low_wellness_days,
    }
    Returns: plain text (no markdown) para insertar en el PDF. Empty str on error.
    """
    api_key = _load_api_key()
    if not api_key:
        return ""

    name   = athlete_data.get("name", "el atleta")
    sport  = athlete_data.get("sport", "combate")
    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )

    avg_w   = athlete_data.get("avg_wellness")
    last_w  = athlete_data.get("last_wellness")
    streak  = athlete_data.get("streak", 0)
    trend   = athlete_data.get("trend_txt", "estable")
    lw      = athlete_data.get("last_weight")
    tw      = athlete_data.get("target_weight")
    ecg     = athlete_data.get("ecg") or {}
    comp    = athlete_data.get("next_comp") or {}
    n_sess  = athlete_data.get("n_sessions_month", 0)
    low_days = athlete_data.get("low_wellness_days", 0)

    lines = []
    if last_w is not None:
        lines.append(f"Bienestar actual: {last_w:.0f}/100 (promedio: {avg_w:.0f}/100 en {n_sess} sesiones)")
    if streak > 0:
        lines.append(f"Racha de check-ins: {streak} días consecutivos")
    if trend:
        lines.append(f"Tendencia bienestar: {trend}")
    if ecg.get("rmssd"):
        lines.append(f"RMSSD {ecg['rmssd']:.0f} ms, FC media {ecg.get('bpm', 0):.0f} bpm")
    if lw and tw:
        diff = float(lw) - float(tw)
        lines.append(f"Peso: {lw:.1f} kg vs objetivo {tw:.1f} kg ({diff:+.1f} kg)")
    if comp.get("name"):
        lines.append(f"Próxima competencia: {comp['name']} en {comp.get('days_until', '?')} días")
    if low_days:
        lines.append(f"Días con bienestar bajo 50: {low_days}")

    data_block = "\n".join(f"  - {l}" for l in lines) or "  - Sin datos suficientes"

    prompt = (
        f"Eres el analista de rendimiento de CombatIQ. Redacta la sección narrativa del "
        f"informe mensual de {name} ({sport_label}).\n\n"
        f"DATOS DEL PERÍODO:\n{data_block}\n\n"
        f"Escribe 2-3 párrafos cortos (máx. 200 palabras) en español que:\n"
        f"1. Sinteticen el estado del atleta de forma honesta.\n"
        f"2. Destaquen 2 puntos de atención concretos (si los hay).\n"
        f"3. Cierren con 1 orientación para las próximas semanas.\n"
        f"Tono profesional pero directo — sin frases genéricas ni relleno.\n"
        f"NO uses markdown ni asteriscos — texto plano para el PDF."
    )

    _model = model or _MODEL_SONNET
    try:
        client   = _anthropic_client(api_key, timeout=_TIMEOUT_BY_MODEL.get(_model, _TIMEOUT_MEDIUM))
        response = client.messages.create(
            model=_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks = [b for b in response.content if hasattr(b, "text") and b.text]
        return text_blocks[-1].text.strip() if text_blocks else ""
    except Exception:
        return ""


# ── Detección de patrones multi-sesión (Opus + tool use) ─────────────────────

_TREND_TOOLS = [
    {
        "name": "flag_pattern",
        "description": "Registra un patrón significativo detectado en el historial de sesiones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Descripción del patrón en 1 frase, como se lo dirías al coach"
                },
                "evidence": {
                    "type": "string",
                    "description": "Los datos concretos que lo demuestran (fechas, valores, tendencia)"
                },
                "severity": {
                    "type": "string",
                    "enum": ["positivo", "vigilar", "corregir"],
                    "description": "¿Es un logro, algo a monitorear o algo a corregir?"
                },
                "action": {
                    "type": "string",
                    "description": "Qué debería hacer el coach o el atleta (1 frase)"
                }
            },
            "required": ["pattern", "evidence", "severity"]
        }
    }
]


def analyze_trend(
    sessions: list,
    athlete_name: str = "el atleta",
    sport: str = "combate",
    model: str = None,
) -> dict:
    """
    Detecta patrones ocultos a lo largo de múltiples sesiones usando Opus + tool use.

    sessions: [{session_id, ts, imu: {n_hits, mean_int_g, ...},
                ecg: {bpm, rmssd}, wellness: float}, ...]
    Returns:
        {patterns: [{pattern, evidence, severity, action}],
         narrative: str, model_used: str, error: str (optional)}
    """
    import json as _json

    _empty = {"patterns": [], "narrative": "", "model_used": "none"}

    api_key = _load_api_key()
    if not api_key:
        _empty["error"] = "ANTHROPIC_API_KEY no configurada"
        return _empty

    if not sessions or len(sessions) < 2:
        _empty["error"] = "Se necesitan al menos 2 sesiones para detectar patrones"
        return _empty

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo", "box": "Boxeo"}.get(
        (sport or "").lower(), sport or "combate"
    )

    rows = []
    for s in sorted(sessions, key=lambda x: x.get("ts") or ""):
        sid  = s.get("session_id", "?")
        ts   = (s.get("ts") or "?")[:10]
        imu  = s.get("imu") or {}
        ecg  = s.get("ecg") or {}
        well = s.get("wellness")
        parts = [f"Sesión #{sid} ({ts})"]
        if imu.get("n_hits"):
            parts.append(f"{imu['n_hits']:.0f} impactos / {imu.get('mean_int_g', 0):.1f}g media")
        if ecg.get("bpm"):
            parts.append(f"FC {ecg['bpm']:.0f} bpm / RMSSD {ecg.get('rmssd', 0):.0f}ms")
        if well is not None:
            parts.append(f"bienestar {well:.0f}/100")
        rows.append("  " + " — ".join(parts))
    sessions_block = "\n".join(rows)

    prompt = (
        f"Eres un analista de rendimiento de combate especializado en {sport_label}.\n\n"
        f"Analiza {len(sessions)} sesiones de {athlete_name} y detecta patrones que los "
        f"números individuales no revelan: tendencias, ciclos, caídas recurrentes, "
        f"correlaciones entre bienestar y rendimiento.\n\n"
        f"SESIONES (cronológico):\n{sessions_block}\n\n"
        f"Usa flag_pattern para cada patrón significativo (mínimo 2, máximo 6).\n"
        f"Busca: ¿el bienestar predice el rendimiento? ¿Los impactos recibidos aumentan "
        f"cuando la FC es alta? ¿Hay mejora sostenida o estancamiento? ¿Algún ciclo semanal?\n\n"
        f"Cierra con 2-3 frases para el coach sintetizando lo más importante."
    )

    _model = model or _MODEL_OPUS
    try:
        client   = _anthropic_client(api_key, timeout=_TIMEOUT_HEAVY)
        messages = [{"role": "user", "content": prompt}]
        patterns  = []
        narrative = ""

        for _turn in range(10):  # máx 10 turnos — igual que analyze_combat_session
            response = client.messages.create(
                model=_model,
                max_tokens=1024,
                thinking={"type": "adaptive"},
                tools=_TREND_TOOLS,
                messages=messages,
            )

            tool_results   = []
            text_parts     = []
            made_tool_call = False

            for block in response.content:
                if block.type == "tool_use":
                    made_tool_call = True
                    patterns.append(block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "OK",
                    })
                elif block.type == "text" and block.text:
                    text_parts.append(block.text)

            if response.stop_reason == "end_turn" or not made_tool_call:
                narrative = " ".join(text_parts).strip()
                break

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})

        return {"patterns": patterns, "narrative": narrative, "model_used": _model}

    except Exception as exc:
        _empty["error"] = str(exc)
        return _empty
