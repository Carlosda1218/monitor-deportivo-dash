"""
analysis_engine.py
==================
Motor de análisis profesional para CombatIQ.

Módulos:
    - acwr()          → Ratio Carga Aguda:Crónica (Workload)
    - hrv_readiness() → Preparación HRV vs baseline rolling 30 días
    - imu_trends()    → Tendencias de volumen e intensidad IMU
    - wellness_trend()→ Tendencia wellness últimos N días
    - athlete_alerts()→ Alertas automáticas con niveles (ok/warning/danger)
    - full_report()   → Informe completo para un atleta (dict listo para la UI)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ─── Caché de informe completo ────────────────────────────────────────────────
_REPORT_TTL = 300  # 5 minutos
_report_cache: dict[int, tuple[float, dict]] = {}  # uid → (ts, report)


def _report_cache_get(uid: int) -> Optional[dict]:
    entry = _report_cache.get(uid)
    if entry and (time.time() - entry[0]) < _REPORT_TTL:
        return entry[1]
    return None


def _report_cache_set(uid: int, report: dict) -> None:
    _report_cache[uid] = (time.time(), report)


def invalidate_cache(uid: int) -> None:
    """Invalida el caché de un atleta. Llamar tras guardar check-in, ECG, RPE o IMU."""
    _report_cache.pop(int(uid), None)


# ─── helpers internos ────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> Optional[datetime]:
    """Parsea timestamps ISO o 'YYYY-MM-DD', devuelve None si falla."""
    if not ts:
        return None
    s = str(ts).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # Recortar al largo esperado solo para formatos sin microsegundos
            trunc = s[:19] if fmt == "%Y-%m-%dT%H:%M:%S" else (s[:10] if fmt == "%Y-%m-%d" else s)
            return datetime.strptime(trunc, fmt)
        except ValueError:
            continue
    return None


def _days_ago(n: int) -> datetime:
    return datetime.utcnow() - timedelta(days=n)


def _filter_recent(rows: List[dict], ts_key: str, days: int) -> List[dict]:
    cutoff = _days_ago(days)
    out = []
    for r in rows:
        dt = _parse_ts(r.get(ts_key, ""))
        if dt and dt >= cutoff:
            out.append(r)
    return out


# ─── ACWR ────────────────────────────────────────────────────────────────────

def acwr(questionnaire_rows: List[dict]) -> dict:
    """
    Acute:Chronic Workload Ratio basado en RPE × duración (session_load).

    Referencia:
        - Aguda  = carga media DIARIA de los últimos  7 días
        - Crónica= carga media DIARIA de los últimos 28 días
        - Ratio óptimo: 0.8 – 1.3
        - Zona de riesgo: > 1.5

    Args:
        questionnaire_rows: salida de db.list_questionnaires(uid)

    Returns:
        dict con keys: ratio, acute_load, chronic_load, acute_days, chronic_days,
                       zone (str: "optimal" | "low" | "high" | "danger"),
                       trend (str: "stable" | "rising" | "falling"),
                       label (str legible),
                       data_7d (list de dicts {date, load})
    """
    now = datetime.utcnow()

    loads_by_day: Dict[str, float] = {}
    for r in questionnaire_rows:
        rpe = r.get("rpe")
        dur = r.get("duration_min")
        ts  = r.get("ts")
        if rpe is None or dur is None or not ts:
            continue
        try:
            rpe = float(rpe)
            dur = float(dur)
        except (TypeError, ValueError):
            continue
        if rpe <= 0 or dur <= 0:
            continue
        dt = _parse_ts(ts)
        if dt is None:
            continue
        day_key = dt.strftime("%Y-%m-%d")
        loads_by_day[day_key] = loads_by_day.get(day_key, 0.0) + rpe * dur

    def _mean_load(days: int) -> float:
        cutoff = now - timedelta(days=days)
        vals = [
            v for k, v in loads_by_day.items()
            if (dt := _parse_ts(k)) is not None and dt >= cutoff
        ]
        return sum(vals) / days if vals else 0.0

    acute   = _mean_load(7)
    chronic = _mean_load(28)
    ratio   = (acute / chronic) if chronic > 0 else None

    # Zona
    if ratio is None:
        zone = "no_data"
        label = "Sin datos suficientes"
    elif ratio < 0.8:
        zone = "low"
        label = f"Carga baja ({ratio:.2f}) — puede haber desacondicionamiento"
    elif ratio <= 1.3:
        zone = "optimal"
        label = f"Carga óptima ({ratio:.2f}) — zona de rendimiento"
    elif ratio <= 1.5:
        zone = "high"
        label = f"Carga elevada ({ratio:.2f}) — vigilar recuperación"
    else:
        zone = "danger"
        label = f"Sobrecarga ({ratio:.2f}) — riesgo de lesión alto"

    # Tendencia: compara últimos 7 días vs los 7 anteriores
    prev_week = _mean_load_window(loads_by_day, days_start=14, days_end=7, now=now)
    if acute == 0 and prev_week == 0:
        trend = "stable"
    elif prev_week == 0:
        trend = "rising"
    elif acute > prev_week * 1.1:
        trend = "rising"
    elif acute < prev_week * 0.9:
        trend = "falling"
    else:
        trend = "stable"

    # Serie últimos 7 días para gráfica
    data_7d = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        data_7d.append({"date": d, "load": round(loads_by_day.get(d, 0.0), 1)})

    return {
        "ratio": round(ratio, 3) if ratio is not None else None,
        "acute_load": round(acute, 1),
        "chronic_load": round(chronic, 1),
        "acute_days": 7,
        "chronic_days": 28,
        "zone": zone,
        "trend": trend,
        "label": label,
        "data_7d": data_7d,
    }


def _mean_load_window(loads_by_day: dict, days_start: int, days_end: int, now: datetime) -> float:
    """Carga media diaria en ventana [now-days_start, now-days_end)."""
    start = now - timedelta(days=days_start)
    end   = now - timedelta(days=days_end)
    vals = []
    for k, v in loads_by_day.items():
        dt = _parse_ts(k)
        if dt and start <= dt < end:
            vals.append(v)
    window = days_start - days_end
    return sum(vals) / window if vals else 0.0


# ─── HRV Readiness ───────────────────────────────────────────────────────────

def hrv_readiness(ecg_files: List[dict], ecg_metrics_fn) -> dict:
    """
    Readiness HRV comparando RMSSD hoy vs baseline rolling 30 días.

    Args:
        ecg_files:      salida de db.list_ecg_files(uid) — lista de archivos
        ecg_metrics_fn: función que dado ecg_file_id devuelve dict con bpm/sdnn/rmssd

    Returns:
        dict con keys: today_rmssd, baseline_rmssd, delta_pct,
                       zone (str), label, trend_data (lista {date, rmssd})
    """
    # Recoge métricas para cada archivo, ordenado por fecha
    records = []
    for f in ecg_files:
        m = ecg_metrics_fn(f["id"])
        if not m or m.get("rmssd") is None:
            continue
        ts = f.get("created_at") or ""
        dt = _parse_ts(ts)
        if dt is None:
            continue
        records.append({"dt": dt, "rmssd": float(m["rmssd"]), "bpm": m.get("bpm"), "sdnn": m.get("sdnn")})

    records.sort(key=lambda x: x["dt"])

    if not records:
        return {
            "today_rmssd": None,
            "baseline_rmssd": None,
            "delta_pct": None,
            "zone": "no_data",
            "label": "Sin registros ECG — sube tu primera medición",
            "trend_data": [],
            "today_date": None,
            "days_since": None,
        }

    # Baseline: media de los últimos 30 días (todos los registros)
    cutoff_30 = _days_ago(30)
    recent_30 = [r for r in records if r["dt"] >= cutoff_30]

    baseline = (
        sum(r["rmssd"] for r in recent_30) / len(recent_30)
        if recent_30 else records[-1]["rmssd"]
    )

    # "Hoy" = registro más reciente
    today_rec = records[-1]
    today_rmssd = today_rec["rmssd"]

    delta_pct = ((today_rmssd - baseline) / baseline * 100) if baseline else 0.0

    # Zona
    if delta_pct >= 10:
        zone = "optimal"
        label = f"HRV elevada (+{delta_pct:.0f}% vs baseline) — sistema nervioso bien recuperado"
    elif delta_pct >= -10:
        zone = "normal"
        label = f"HRV normal ({delta_pct:+.0f}% vs baseline) — dentro del rango habitual"
    elif delta_pct >= -20:
        zone = "warning"
        label = f"HRV baja ({delta_pct:.0f}% vs baseline) — posible fatiga acumulada"
    else:
        zone = "danger"
        label = f"HRV muy baja ({delta_pct:.0f}% vs baseline) — reducir carga, priorizar recuperación"

    # Tendencia para gráfica (últimos 14 días, una lectura por día)
    seen_days: dict = {}
    for r in records:
        day = r["dt"].strftime("%Y-%m-%d")
        seen_days[day] = r["rmssd"]

    trend_data = [
        {"date": k, "rmssd": round(v, 1)}
        for k, v in sorted(seen_days.items())
        if _parse_ts(k) and _parse_ts(k) >= _days_ago(14)
    ]

    today_dt    = today_rec["dt"]
    days_since  = (datetime.utcnow() - today_dt).days

    return {
        "today_rmssd": round(today_rmssd, 1),
        "baseline_rmssd": round(baseline, 1),
        "delta_pct": round(delta_pct, 1),
        "today_bpm": today_rec.get("bpm"),
        "zone": zone,
        "label": label,
        "trend_data": trend_data,
        "today_date": today_dt.strftime("%Y-%m-%d"),
        "days_since": days_since,
    }


# ─── IMU Trends ──────────────────────────────────────────────────────────────

def imu_trends(imu_rows: List[dict], window_days: int = 28) -> dict:
    """
    Tendencias de volumen e intensidad a partir de métricas IMU.

    Calcula:
        - Volumen total (golpes/patadas) por semana
        - Intensidad media y máxima por semana
        - Tendencia (rising/stable/falling)

    Args:
        imu_rows:    salida de db.list_imu_metrics(uid)
        window_days: ventana de análisis (default 28)

    Returns:
        dict con keys: total_hits, avg_hits_per_min, avg_intensity,
                       peak_intensity, weeks (lista), trend, label
    """
    recent = _filter_recent(imu_rows, "ts", window_days)

    if not recent:
        return {
            "total_hits": 0,
            "avg_hits_per_min": 0.0,
            "avg_intensity": 0.0,
            "peak_intensity": 0.0,
            "weeks": [],
            "trend": "no_data",
            "label": "Sin datos IMU en los últimos 28 días",
        }

    total_hits = sum(r.get("n_hits", 0) or 0 for r in recent)
    hpm_vals   = [r["hits_per_min"] for r in recent if r.get("hits_per_min")]
    int_vals   = [r["mean_int_g"]   for r in recent if r.get("mean_int_g")]
    peak_vals  = [r["max_int_g"]    for r in recent if r.get("max_int_g")]

    avg_hpm   = sum(hpm_vals) / len(hpm_vals) if hpm_vals else 0.0
    avg_int   = sum(int_vals) / len(int_vals)  if int_vals  else 0.0
    peak_int  = max(peak_vals)                 if peak_vals else 0.0

    # Agrupar por semana
    weeks_data: Dict[str, dict] = {}
    for r in recent:
        dt = _parse_ts(r.get("ts", ""))
        if dt is None:
            continue
        # Semana como lunes de la semana
        monday = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        if monday not in weeks_data:
            weeks_data[monday] = {"hits": 0, "sessions": 0, "int_sum": 0.0}
        weeks_data[monday]["hits"] += r.get("n_hits", 0) or 0
        weeks_data[monday]["sessions"] += 1
        weeks_data[monday]["int_sum"] += r.get("mean_int_g", 0.0) or 0.0

    weeks = [
        {
            "week_start": k,
            "hits": v["hits"],
            "sessions": v["sessions"],
            "avg_intensity": round(v["int_sum"] / v["sessions"], 2) if v["sessions"] else 0.0,
        }
        for k, v in sorted(weeks_data.items())
    ]

    # Tendencia: compara 2 últimas semanas
    trend = "stable"
    label = f"{total_hits} acciones en {window_days} días — intensidad media {avg_int:.1f}g"
    if len(weeks) >= 2:
        last   = weeks[-1]["hits"]
        before = weeks[-2]["hits"]
        if before > 0:
            if last > before * 1.15:
                trend = "rising"
                label += " — volumen en aumento"
            elif last < before * 0.85:
                trend = "falling"
                label += " — volumen en descenso"

    return {
        "total_hits": int(total_hits),
        "avg_hits_per_min": round(avg_hpm, 1),
        "avg_intensity": round(avg_int, 2),
        "peak_intensity": round(peak_int, 2),
        "weeks": weeks,
        "trend": trend,
        "label": label,
    }


# ─── Wellness Trend ──────────────────────────────────────────────────────────

def wellness_trend(questionnaire_rows: List[dict], days: int = 14) -> dict:
    """
    Tendencia de wellness score en los últimos N días.

    Returns:
        dict con keys: latest_score, avg_score, trend, data (lista {date, score}),
                       low_days (int — días bajo 50), label
    """
    recent = _filter_recent(questionnaire_rows, "ts", days)

    if not recent:
        return {
            "latest_score": None,
            "avg_score": None,
            "trend": "no_data",
            "data": [],
            "low_days": 0,
            "label": "Sin check-ins en los últimos 14 días",
        }

    # Una entrada por día (la más reciente de ese día)
    by_day: Dict[str, tuple] = {}   # day → (ts_str, score)
    for r in recent:
        s = r.get("wellness_score")
        if s is None:
            continue
        dt = _parse_ts(r.get("ts", ""))
        if dt is None:
            continue
        day = dt.strftime("%Y-%m-%d")
        ts_str = r.get("ts", "")
        if day not in by_day or ts_str > by_day[day][0]:
            by_day[day] = (ts_str, float(s))

    scores = {day: v for day, (_, v) in by_day.items()}
    if not scores:
        return {
            "latest_score": None,
            "avg_score": None,
            "trend": "no_data",
            "data": [],
            "low_days": 0,
            "label": "Sin datos de wellness",
        }

    sorted_days = sorted(scores.keys())
    data = [{"date": d, "score": round(scores[d], 1)} for d in sorted_days]
    latest = scores[sorted_days[-1]]
    avg    = sum(scores.values()) / len(scores)
    low_days = sum(1 for v in scores.values() if v < 50)

    # Tendencia: últimos 3 días vs los 3 anteriores (si hay suficientes)
    trend = "stable"
    if len(sorted_days) >= 4:
        half = len(sorted_days) // 2
        first_half = [scores[d] for d in sorted_days[:half]]
        second_half = [scores[d] for d in sorted_days[half:]]
        avg_first  = sum(first_half)  / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        if avg_second > avg_first + 5:
            trend = "improving"
        elif avg_second < avg_first - 5:
            trend = "declining"

    if low_days >= 3:
        label = f"Wellness bajo 3 o más días — revisar carga y sueño"
    elif trend == "improving":
        label = f"Wellness en mejora — promedio {avg:.0f}/100"
    elif trend == "declining":
        label = f"Wellness en descenso — promedio {avg:.0f}/100"
    else:
        label = f"Wellness estable — promedio {avg:.0f}/100"

    return {
        "latest_score": round(latest, 1),
        "avg_score": round(avg, 1),
        "trend": trend,
        "data": data,
        "low_days": low_days,
        "label": label,
    }


# ─── Alertas automáticas ─────────────────────────────────────────────────────

_ALERT_LEVELS = ("ok", "info", "warning", "danger")


def _alert(level: str, key: str, title: str, message: str) -> dict:
    return {"level": level, "key": key, "title": title, "message": message}


def athlete_alerts(
    acwr_data: dict,
    hrv_data: dict,
    wellness_data: dict,
    imu_data: dict,
) -> List[dict]:
    """
    Genera lista de alertas automáticas cruzando todos los módulos.

    Returns:
        Lista de dicts {level, key, title, message} ordenada por severidad.
    """
    alerts = []

    # ── ACWR ──
    zone = acwr_data.get("zone", "no_data")
    if zone == "danger":
        alerts.append(_alert(
            "danger", "acwr_danger",
            "Sobrecarga de entrenamiento",
            f"Ratio carga aguda/crónica = {acwr_data.get('ratio', '?')} (umbral crítico >1.5). "
            "Riesgo de lesión significativo. Reduce la carga esta semana."
        ))
    elif zone == "high":
        alerts.append(_alert(
            "warning", "acwr_high",
            "Carga elevada",
            f"Ratio = {acwr_data.get('ratio', '?')} (zona de precaución 1.3–1.5). "
            "Monitoriza signos de fatiga y prioriza la recuperación."
        ))
    elif zone == "low" and acwr_data.get("chronic_load", 0) > 0:
        alerts.append(_alert(
            "info", "acwr_low",
            "Carga por debajo del nivel habitual",
            "El atleta está entrenando menos de lo acostumbrado. "
            "Verificar si es recuperación planificada o pérdida de adherencia."
        ))

    # Tendencia carga
    if acwr_data.get("trend") == "rising" and zone in ("high", "danger"):
        alerts.append(_alert(
            "warning", "acwr_rising",
            "Carga en aumento sostenido",
            "La carga de esta semana supera en >10% la semana anterior y ya está en zona alta."
        ))

    # ── HRV ──
    hrv_zone = hrv_data.get("zone", "no_data")
    if hrv_zone == "danger":
        alerts.append(_alert(
            "danger", "hrv_danger",
            "HRV muy baja — fatiga del SNC",
            f"RMSSD hoy ({hrv_data.get('today_rmssd', '?')} ms) está "
            f"{abs(hrv_data.get('delta_pct', 0)):.0f}% por debajo del baseline. "
            "Sistema nervioso muy comprometido. Descanso o sesión muy suave."
        ))
    elif hrv_zone == "warning":
        alerts.append(_alert(
            "warning", "hrv_warning",
            "HRV por debajo del baseline",
            f"RMSSD ({hrv_data.get('today_rmssd', '?')} ms) cae "
            f"{abs(hrv_data.get('delta_pct', 0)):.0f}% vs media de 30 días. "
            "Reducir la intensidad planificada."
        ))

    # ── Wellness ──
    low_days = wellness_data.get("low_days", 0)
    if low_days >= 5:
        alerts.append(_alert(
            "danger", "wellness_chronic_low",
            "Bienestar crónicamente bajo",
            f"{low_days} de los últimos 14 días con wellness < 50. "
            "Revisar carga total, sueño, nutrición y estrés extradeportivo."
        ))
    elif low_days >= 3:
        alerts.append(_alert(
            "warning", "wellness_low",
            "Varios días con bienestar bajo",
            f"{low_days} días con wellness < 50 en las últimas 2 semanas. "
            "Ajustar carga y profundizar en el check-in de hoy."
        ))

    if wellness_data.get("trend") == "declining":
        alerts.append(_alert(
            "warning", "wellness_declining",
            "Tendencia wellness a la baja",
            "El wellness promedio de la segunda mitad de la ventana es 5+ puntos menor "
            "que la primera. Puede indicar acumulación de estrés o fatiga."
        ))

    # ── IMU ──
    if imu_data.get("trend") == "rising":
        # Combinación: volumen subiendo + carga alta → alerta cruzada
        if zone in ("high", "danger"):
            alerts.append(_alert(
                "warning", "imu_volume_overload",
                "Volumen técnico y carga interna ambos altos",
                "El número de acciones (golpes/patadas) aumentó esta semana y la carga "
                "interna ya está en zona de riesgo. Monitorizar técnica y calidad de ejecución."
            ))

    # Si no hay alertas, todo bien
    if not alerts:
        alerts.append(_alert(
            "ok", "all_ok",
            "Todo en orden",
            "Carga, HRV y bienestar dentro de rangos normales. Mantener el plan."
        ))

    # Ordenar: danger > warning > info > ok
    order = {"danger": 0, "warning": 1, "info": 2, "ok": 3}
    alerts.sort(key=lambda a: order.get(a["level"], 99))
    return alerts


# ─── Informe completo ─────────────────────────────────────────────────────────

def full_report(
    uid: int,
    db_module,
    questionnaire_rows: Optional[List[dict]] = None,
    imu_rows: Optional[List[dict]] = None,
) -> dict:
    """
    Genera el informe completo de un atleta.

    Args:
        uid:               ID del atleta
        db_module:         módulo db importado (para llamar funciones)
        questionnaire_rows: si None, llama db.list_questionnaires(uid)
        imu_rows:          si None, llama db.list_imu_metrics(uid)

    Returns:
        dict con keys: acwr, hrv, wellness, imu, alerts, generated_at
    """
    # Usa caché si los datos no vienen precargados
    if questionnaire_rows is None and imu_rows is None:
        cached = _report_cache_get(int(uid))
        if cached is not None:
            return cached

    if questionnaire_rows is None:
        questionnaire_rows = db_module.list_questionnaires(int(uid))
    if imu_rows is None:
        imu_rows = db_module.list_imu_metrics(int(uid))

    # ECG metrics por archivo. Preferimos una consulta bulk para evitar N+1
    # cuando el dashboard del coach renderiza tarjetas o informes.
    ecg_files = db_module.list_ecg_files(int(uid))
    metrics_by_file = {}
    if hasattr(db_module, "list_latest_ecg_metrics_for_files"):
        try:
            metrics_by_file = db_module.list_latest_ecg_metrics_for_files(
                [f.get("id") for f in ecg_files if f.get("id") is not None]
            )
        except Exception:
            metrics_by_file = {}

    def _get_metrics_for_file(file_id: int):
        cached = metrics_by_file.get(int(file_id)) if metrics_by_file else None
        if cached is not None:
            return cached
        with db_module._get_conn() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT bpm, sdnn, rmssd FROM ecg_metrics WHERE ecg_file_id=? ORDER BY id DESC LIMIT 1",
                (int(file_id),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"bpm": row[0], "sdnn": row[1], "rmssd": row[2]}

    acwr_data     = acwr(questionnaire_rows)
    hrv_data      = hrv_readiness(ecg_files, _get_metrics_for_file)
    wellness_data = wellness_trend(questionnaire_rows)
    imu_data      = imu_trends(imu_rows)
    alerts        = athlete_alerts(acwr_data, hrv_data, wellness_data, imu_data)

    report = {
        "uid": uid,
        "acwr": acwr_data,
        "hrv": hrv_data,
        "wellness": wellness_data,
        "imu": imu_data,
        "alerts": alerts,
        "generated_at": datetime.utcnow().isoformat(),
    }
    _report_cache_set(int(uid), report)
    return report


# ─── Helpers para la UI ───────────────────────────────────────────────────────

ZONE_COLORS = {
    "optimal":  "var(--neon)",
    "normal":   "var(--neon)",
    "ok":       "var(--neon)",
    "low":      "var(--muted)",
    "high":     "#f0a500",
    "warning":  "#f0a500",
    "improving":"var(--neon)",
    "stable":   "var(--ink)",
    "declining":"#f0a500",
    "danger":   "var(--punch)",
    "no_data":  "var(--muted)",
}

LEVEL_BADGE = {
    "ok":      ("✓", "var(--neon)"),
    "info":    ("i", "var(--accent)"),
    "warning": ("!", "#f0a500"),
    "danger":  ("✕", "var(--punch)"),
}


def zone_color(zone: str) -> str:
    return ZONE_COLORS.get(zone, "var(--muted)")


def alert_badge(level: str) -> tuple:
    """Devuelve (icono_str, color) para un nivel de alerta."""
    return LEVEL_BADGE.get(level, ("·", "var(--muted)"))
