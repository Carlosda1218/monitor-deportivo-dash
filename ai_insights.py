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
import os
import time
from typing import Optional

# ── Caché en memoria ──────────────────────────────────────────────────────────
_CACHE_TTL = 600  # segundos (10 min)
_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, note)


def _cache_key(report: dict, athlete_name: str, sport: str) -> str:
    payload = f"{athlete_name}|{sport}|{report.get('generated_at', '')}"
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


def _build_prompt(report: dict, athlete_name: str, sport: str, role: str = "coach") -> str:
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
        f"  - [{a['level'].upper()}] {a['title']}: {a['message']}"
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

    sport_label = {"taekwondo": "Taekwondo", "boxeo": "Boxeo"}.get(
        (sport or "").lower(), sport or "deporte de combate"
    )

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

ALERTAS ACTIVAS:
{alert_lines}

== INSTRUCCIONES ==

Redacta un análisis de coaching profesional con estas secciones:

1. **Estado actual** (2-3 frases): síntesis directa de cómo llega el atleta hoy.
2. **Puntos de atención** (lista de 2-4 ítems): lo más urgente a vigilar o corregir.
3. **Recomendaciones concretas** (lista de 3-5 ítems): acciones específicas para las próximas 48-72h (carga, recuperación, técnica, nutrición).
4. **Proyección a 7 días** (1-2 frases): qué esperar si se siguen las recomendaciones.

Tono: profesional, directo, orientado al rendimiento. Sin frases genéricas. Usa los datos numéricos cuando refuercen el argumento. Máximo 350 palabras."""


def generate_coaching_note(
    report: dict,
    athlete_name: str = "el atleta",
    sport: str = "combate",
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """
    Genera una nota de coaching narrativa usando la API de Claude.

    Args:
        report:        salida de analysis_engine.full_report()
        athlete_name:  nombre del atleta (para personalizar el texto)
        sport:         deporte ("taekwondo" | "boxeo")
        model:         modelo Claude a usar (por defecto Haiku — rápido y económico)

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

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_prompt(report, athlete_name=athlete_name, sport=sport)

        message = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        note = message.content[0].text.strip()
        _cache_set(key, note)
        return note

    except Exception as exc:
        return (
            f"**Error al generar análisis AI:** {exc}\n\n"
            "Verifica tu API key y la conexión a internet."
        )
