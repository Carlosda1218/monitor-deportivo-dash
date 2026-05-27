# views/compare_view.py

import os
import io
import base64
import uuid
import re
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, empty_figure, graph_config, placeholder_figure

import dash
from dash import html, dcc, Input, Output, State
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate
from flask import session

# ---------------- ReportLab (opcional) ----------------
_REPORTLAB_OK = True
_REPORTLAB_ERR = ""
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.utils import simpleSplit, ImageReader
except Exception as e:
    _REPORTLAB_OK = False
    _REPORTLAB_ERR = str(e)

# Reutilizamos helpers de la vista de señales
from .signals_view import (
    read_ecg_csv,
    detect_r_peaks,
    ecg_metrics_from_peaks,
    smooth,
    read_imu_csv,
    imu_metrics_from_mag,
)

_ALLOWED_EXTS = {".csv"}
_MAX_CSV_UPLOAD_BYTES = 25 * 1024 * 1024
_MAX_CSV_UPLOAD_B64_CHARS = int(_MAX_CSV_UPLOAD_BYTES * 1.40) + 2048


# ================= Helpers base =================

def _safe_int(x):
    try:
        return int(x)
    except Exception:
        return None


def _safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _fmt_pct(pct):
    """Evita el error NoneType.__format__."""
    try:
        return f"{float(pct):+.1f}%"
    except Exception:
        return "—"


def _duration_from_saved_metrics(metrics: dict):
    """Estimacion ligera para no releer CSV cuando ya hay metricas persistidas."""
    if not metrics:
        return None
    bpm = _safe_float(metrics.get("bpm"), 0.0) or 0.0
    peaks = _safe_int(metrics.get("n_peaks")) or 0
    if bpm <= 0 or peaks <= 0:
        return None
    return round((peaks / bpm) * 60.0, 1)


def _row_from_saved_ecg_metrics(file_row: dict, label: str, metrics: dict):
    if not metrics:
        return None
    bpm = _safe_float(metrics.get("bpm"), 0.0) or 0.0
    sdnn = _safe_float(metrics.get("sdnn"), 0.0) or 0.0
    rmssd = _safe_float(metrics.get("rmssd"), 0.0) or 0.0
    return {
        "label": label,
        "filename": file_row.get("filename"),
        "duration_s": _duration_from_saved_metrics(metrics),
        "n_beats": _safe_int(metrics.get("n_peaks")) or 0,
        "bpm": int(round(bpm)) if bpm > 0 else 0,
        "sdnn_ms": int(round(sdnn)) if sdnn > 0 else 0,
        "rmssd_ms": int(round(rmssd)) if rmssd > 0 else 0,
    }


def _sanitize_filename(filename: str, default: str = "file.csv") -> str:
    """
    Nombre seguro:
    - basename
    - solo [a-zA-Z0-9._-]
    - espacios -> _
    - fuerza extensión .csv
    - limita longitud
    """
    name = (filename or "").strip()
    name = os.path.basename(name)
    if not name:
        name = default

    name = name.replace(" ", "_")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)

    if name in (".", "..") or not name:
        name = default

    base, ext = os.path.splitext(name)
    ext = (ext or "").lower()
    if ext not in _ALLOWED_EXTS:
        ext = ".csv"
        base = base if base else os.path.splitext(default)[0]

    base = (base or "file")[:80]
    return f"{base}{ext}"


def _b64_to_bytes(content: str) -> bytes:
    if not content:
        raise ValueError("Contenido vacío")
    try:
        _, b64 = content.split(",", 1)
        data = base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e
    if len(data) > _MAX_CSV_UPLOAD_BYTES:
        raise ValueError("Archivo demasiado grande. Máximo: 25 MB")
    return data


def _save_unique(dirpath: str, filename: str, data: bytes, prefix: str = "cmp_") -> str:
    """
    Guarda evitando sobrescritura: cmp_<uuid>_<filename_sanitizado>.csv
    Devuelve el nombre final.
    """
    os.makedirs(dirpath, exist_ok=True)
    safe = _sanitize_filename(filename or "file.csv")
    token = uuid.uuid4().hex[:8]
    final_name = f"{prefix}{token}_{safe}"
    full = os.path.join(dirpath, final_name)
    with open(full, "wb") as f:
        f.write(data)
    return final_name


def _session_label(s: dict):
    if not s:
        return "—"
    sid = s.get("id", "—")
    ts = (s.get("ts_start") or "")[:19].replace("T", " ")
    st = (s.get("status") or "—")
    return f"#{sid} · {ts} · {st}"


def _delta_and_pct(cur, prev):
    try:
        cur = float(cur)
        prev = float(prev)
    except Exception:
        return None, None
    d = cur - prev
    pct = (d / prev * 100.0) if abs(prev) > 1e-9 else None
    return d, pct


def _badge(text: str, kind: str = "neutral"):
    cls_map = {"good": "badge--good", "bad": "badge--bad", "neutral": "badge--neutral"}
    return html.Div(text, className=cls_map.get(kind, "badge--neutral"))


# Mapeo de aliases: en DB/UI a veces usamos códigos más específicos (IMU_GLOVE, EMG_ARM, etc.).
_SENSOR_ALIASES = {
    "ECG": {"ECG"},
    "IMU": {"IMU", "IMU_GLOVE", "IMU_HEAD", "IMU_WRIST", "IMU_FOOT", "IMU_ANKLE"},
    "EMG": {"EMG", "EMG_ARM", "EMG_LEG"},
    "RESP_BELT": {"RESP_BELT", "RESP", "RESPIRATION", "RESP_CHEST"},
}

def _has_sensor(db, user_id: int, code: str) -> bool:
    """Chequea si el atleta tiene asignado el sensor, tolerando aliases."""
    try:
        codes = set(db.get_user_sensors(int(user_id)) or [])
        want = _SENSOR_ALIASES.get(code, {code})
        return bool(codes & want)
    except Exception:
        return False

def _coach_sport():
    return str(session.get("sport") or "").strip() or None


def _can_access_athlete(db, athlete_id: int) -> bool:
    aid = _safe_int(athlete_id)
    actor_id = _safe_int(session.get("user_id"))
    role = str(session.get("role") or "")
    if not (aid and actor_id):
        return False
    if role == "admin":
        return True
    if role == "deportista":
        return aid == actor_id
    if role == "coach":
        try:
            return bool(db.coach_has_athlete(actor_id, aid, sport=_coach_sport()))
        except Exception:
            try:
                roster = db.list_roster_for_coach(actor_id, sport=_coach_sport()) or []
                return any(_safe_int(a.get("id")) == aid for a in roster)
            except Exception:
                return False
    return False


def _ecg_data_path(filename: str):
    fname = os.path.basename(str(filename or ""))
    base = os.path.abspath(os.path.join("data", "ecg"))
    candidate = os.path.abspath(os.path.join(base, fname))
    try:
        if os.path.commonpath([base, candidate]) != base:
            return None
    except Exception:
        return None
    return candidate if os.path.exists(candidate) else None


def _latest_by_session(db, kind: str, session_id: int, user_id: int = None):
    """
    kind in {"imu","emg","resp"}
    Devuelve la fila más reciente por session_id (si existe).
    """
    if not session_id:
        return None

    fn_map = {
        "imu": "list_imu_metrics_by_session",
    }
    fn_name = fn_map.get(kind)
    if not fn_name or not hasattr(db, fn_name):
        return None

    try:
        rows = getattr(db, fn_name)(int(session_id)) or []
    except Exception:
        return None

    if not rows:
        return None

    if user_id is not None:
        uid = _safe_int(user_id)
        rows = [r for r in rows if _safe_int(r.get("user_id")) == uid]
        if not rows:
            return None

    def key(r):
        ts = r.get("ts")
        rid = r.get("id", 0)
        return (ts or "", rid)

    rows_sorted = sorted(rows, key=key, reverse=True)
    return rows_sorted[0]


def _ecg_row_for_session(db, user_id: int, session_id: int, label: str):
    """
    Toma el archivo ECG más reciente de esa sesión (si hay) y calcula métricas.
    """
    if not session_id:
        return None

    files = []
    if hasattr(db, "list_ecg_files_by_session"):
        try:
            files = db.list_ecg_files_by_session(int(session_id)) or []
        except Exception:
            files = []
    else:
        try:
            allf = db.list_ecg_files(int(user_id)) or []
        except Exception:
            allf = []
        files = [f for f in allf if _safe_int(f.get("session_id")) == int(session_id)]

    if not files:
        return None

    uid = _safe_int(user_id)
    files = [f for f in files if _safe_int(f.get("user_id")) == uid]
    if not files:
        return None

    files = sorted(files, key=lambda r: _safe_int(r.get("id")) or 0, reverse=True)
    f = files[0]
    fname = f.get("filename")
    if not fname:
        return None

    path = _ecg_data_path(fname)
    if not path:
        return None

    if hasattr(db, "get_latest_ecg_metrics_for_file"):
        try:
            saved = db.get_latest_ecg_metrics_for_file(int(f.get("id")))
            saved_row = _row_from_saved_ecg_metrics(f, label, saved)
            if saved_row:
                return saved_row
        except Exception:
            pass

    try:
        fs0 = int(f.get("fs") or 250)
    except Exception:
        fs0 = 250

    try:
        t, x, fs = read_ecg_csv(path, fs_default=fs0)
    except Exception:
        return None

    if x is None or len(x) < 5:
        return None

    try:
        xs = smooth(x, win_ms=40, fs=fs)
        peaks = detect_r_peaks(xs, fs, sens=0.6)
        bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
    except Exception:
        bpm, sdnn, rmssd = 0.0, 0.0, 0.0
        peaks = np.array([], dtype=int)

    duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    n_beats = int(len(peaks))

    return {
        "label": label,
        "filename": fname,
        "duration_s": round(duration_s, 1),
        "n_beats": n_beats,
        "bpm": int(round(bpm)) if bpm > 0 else 0,
        "sdnn_ms": int(round(sdnn)) if sdnn > 0 else 0,
        "rmssd_ms": int(round(rmssd)) if rmssd > 0 else 0,
    }


# ================= Heurísticas (badges) =================

def _ecg_recovery_badge(ecg_cur: dict, ecg_prev: dict):
    """
    Heurística simple:
    - mejor si RMSSD ↑ y SDNN ↑ y BPM ↓
    """
    if not (ecg_cur and ecg_prev):
        return None

    bpm_d, _ = _delta_and_pct(ecg_cur.get("bpm", 0), ecg_prev.get("bpm", 0))
    sdnn_d, _ = _delta_and_pct(ecg_cur.get("sdnn_ms", 0), ecg_prev.get("sdnn_ms", 0))
    rmssd_d, _ = _delta_and_pct(ecg_cur.get("rmssd_ms", 0), ecg_prev.get("rmssd_ms", 0))

    score = 0
    if bpm_d is not None and bpm_d < 0:
        score += 1
    if sdnn_d is not None and sdnn_d > 0:
        score += 1
    if rmssd_d is not None and rmssd_d > 0:
        score += 1

    if score >= 2:
        return _badge("Recuperación cardiovascular mejor que en la sesión anterior.", "good")
    if score == 1:
        return _badge("La recuperación del día viene mezclada frente a la sesión anterior. Revísala junto con sueño, carga y sensaciones.", "neutral")
    return _badge("La recuperación cardiovascular llega peor que en la sesión anterior. Conviene interpretar la carga con cuidado.", "bad")


def _load_badge(cur: float, prev: float, label="Carga de la sesión"):
    d, pct = _delta_and_pct(cur, prev)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)
    prevv = _safe_float(prev, 0.0) or 0.0
    base = abs(prevv) if abs(prevv) > 1e-9 else max(1.0, abs(_safe_float(cur, 0.0) or 0.0))
    thr = 0.01 * base

    if d <= -thr:
        return _badge(f"{label} más baja ({pct_s}).", "good")
    if d >= thr:
        return _badge(f"{label} más alta ({pct_s}).", "bad")
    return _badge(f"{label} estable ({pct_s}).", "neutral")


def _fatigue_badge(cur_fat: float, prev_fat: float):
    d, pct = _delta_and_pct(cur_fat, prev_fat)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)

    # fatiga menor = mejor
    if d < -0.5:
        return _badge(f"Menor fatiga muscular ({pct_s}).", "good")
    if d > 0.5:
        return _badge(f"Mayor fatiga muscular ({pct_s}).", "bad")
    return _badge(f"Fatiga muscular parecida ({pct_s}).", "neutral")


def _resp_badge(cur_br: float, prev_br: float):
    d, pct = _delta_and_pct(cur_br, prev_br)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)

    # respiraciones/min menor = generalmente mejor (menos estrés)
    if d < -0.3:
        return _badge(f"Respiración más calmada ({pct_s}).", "good")
    if d > 0.3:
        return _badge(f"Respiración más alta ({pct_s}).", "bad")
    return _badge(f"Respiración parecida ({pct_s}).", "neutral")


def _overall_summary(ecg_b=None, imu_b=None):
    texts = []
    for b in [ecg_b, imu_b]:
        if isinstance(b, html.Div):
            try:
                texts.append(str(b.children))
            except Exception:
                pass

    if not texts:
        return "Aún faltan señales suficientes para sacar una lectura completa de esta comparación."

    red = sum(1 for t in texts if "🔴" in t)
    green = sum(1 for t in texts if "🟢" in t)

    if green >= 2 and red == 0:
        return "La sesión va en buena línea frente a la anterior: mejor recuperación y carga bien controlada."
    if red >= 2:
        return "La sesión deja señales de fatiga frente a la anterior. Conviene bajar un poco la carga y priorizar recuperación."
    return "La lectura es mixta: hay cosas que mejoran y otras que piden atención según el tipo de trabajo."


def _recommendations(ecg_b=None, imu_b=None):
    recs = []

    def txt(div):
        try:
            return str(div.children)
        except Exception:
            return ""

    t_ecg = txt(ecg_b)
    t_imu = txt(imu_b)
    if "peor" in t_ecg or "cuidado" in t_ecg:
        recs.append("Baja un poco la intensidad durante 24–48 h y prioriza sueño, hidratación y recuperación entre sesiones.")
    if "más alta" in t_imu:
        recs.append("La sesión pegó fuerte en ritmo e impacto. Si toca seguir, mejor técnica controlada o trabajo con menos choque.")
    if not recs:
        recs.append("La sesión va en buena línea. Puedes mantener el plan con progresión controlada.")
        recs.append("Sigue registrando sensaciones y wellbeing para entender mejor cada día de entrenamiento.")

    return recs


def _report_text(text: str) -> str:
    s = str(text or "").strip()
    replacements = {
        "🟢": "Positivo:",
        "🟡": "Mixto:",
        "🔴": "Atención:",
    }
    for old, new in replacements.items():
        s = s.replace(old, f"{new} ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s



def _compare_bar_fig(title: str, x_labels, series, y_title: str = "Valor"):
    """Crea una figura de barras comparativa con estilo CombatIQ (desktop)."""
    fig = go.Figure()
    for s in series:
        # s: dict(name=..., y=[...])
        fig.add_trace(go.Bar(x=x_labels, y=s.get("y", []), name=s.get("name", "")))
    fig.update_layout(barmode="group")
    apply_chart_style(fig, title=title, x_title="Sesión", y_title=y_title, height=380)
    return fig


def _apply_pdf_chart_style(fig: go.Figure, title: str, y_title: str = "Valor"):
    apply_chart_style(fig, title=title, x_title="Sesión", y_title=y_title, height=360)
    fig.update_layout(
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(color="#0E1522"),
        title=dict(
            text=title,
            x=0.01,
            xanchor="left",
            y=0.98,
            yanchor="top",
            font=dict(size=14, color="#0E1522"),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#D6DEE8",
            borderwidth=1,
            font=dict(size=10, color="#334155"),
        ),
        hoverlabel=dict(
            bgcolor="#ffffff",
            bordercolor="#0ea5e9",
            font=dict(size=12, color="#0E1522"),
        ),
    )
    fig.update_xaxes(
        gridcolor="rgba(148,163,184,0.18)",
        linecolor="#CBD5E1",
        tickcolor="#CBD5E1",
        tickfont=dict(size=10.5, color="#334155"),
        title=dict(font=dict(size=12, color="#334155")),
        showspikes=False,
    )
    fig.update_yaxes(
        gridcolor="rgba(148,163,184,0.18)",
        linecolor="#CBD5E1",
        tickcolor="#CBD5E1",
        tickfont=dict(size=10.5, color="#334155"),
        title=dict(font=dict(size=12, color="#334155")),
        showspikes=False,
    )
    return fig

# ================= Vista =================

class CompareView:
    """
    Vista de histórico y comparación.

    1) Comparación principal por sesión (seleccionada vs anterior) usando session_id
    2) Comparación avanzada por archivos (ECG)
    3) Herramientas avanzadas por multi-upload (IMU/EMG/Resp)
    """

    def __init__(self, app: dash.Dash, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module
        self._register_callbacks()

    # ====================== LAYOUT ======================

    def layout(self):
        if not session.get("user_id"):
            return html.Div("Inicia sesión para ver esta página.")

        role  = (session.get("role") or "")
        uid   = session.get("user_id")
        sport = session.get("sport") or ""
        try:
            uid_int = int(uid)
        except Exception:
            uid_int = None

        sport_str = (sport if isinstance(sport, str)
                     else sport.decode("utf-8", "replace") if isinstance(sport, bytes) else "").strip() or None
        my_name = session.get("name") or "Deportista"

        # ── Coach: selector de atleta visible ──────────────────────────
        if role == "coach" and uid_int:
            try:
                _athletes = db.list_roster_for_coach(uid_int, sport=sport_str) or []
            except Exception:
                _athletes = []
            _user_opts   = [{"label": f"{a['name']} · {a.get('sport', '-')}", "value": a["id"]}
                             for a in _athletes if a and a.get("id")]
            _default_uid = _user_opts[0]["value"] if _user_opts else None
            user_filter_block = html.Div(className="filters-bar filters-bar--1", children=[
                html.Div([
                    html.Label("Deportista"),
                    dcc.Dropdown(
                        id="cmp-user",
                        options=_user_opts,
                        value=_default_uid,
                        placeholder="Selecciona un deportista…",
                    ),
                ], className="filter-item"),
            ])
            chips_empty_init = "Selecciona un deportista para ver sus sesiones."
        else:
            # ── Deportista: selector oculto apuntando a sí mismo ─────────
            _default_uid = uid_int
            user_filter_block = html.Div([
                dcc.Dropdown(
                    id="cmp-user",
                    options=[{"label": my_name, "value": uid_int}] if uid_int else [],
                    value=uid_int,
                )
            ], style={"display": "none"})
            chips_empty_init = "Cargando sesiones…"

        # ── Bloque chips de sesión + filtro de métricas ──
        pdf_note = None
        if not _REPORTLAB_OK:
            pdf_note = html.Div(
                "PDF no disponible. Instala reportlab (pip install reportlab).",
                className="text-danger text-xs",
                style={"marginTop": "6px"},
            )

        session_chips_block = html.Div(className="card", children=[
            # Selector de atleta (solo visible para coaches)
            user_filter_block,
            # Chips de sesiones
            html.Div(style={"marginTop": "16px"}, children=[
                html.Div(className="compare-chips-header", children=[
                    html.Span("Sesiones a comparar", style={"fontWeight": "700", "fontSize": "14px"}),
                    html.Span(id="cmp-chips-count", className="text-muted text-xs"),
                    html.Button("Limpiar", id="btn-cmp-chips-clear", n_clicks=0,
                                className="btn btn-ghost btn-xs", style={"marginLeft": "auto"}),
                ]),
                dcc.Checklist(
                    id="cmp-sessions-chips",
                    options=[],
                    value=[],
                    className="session-chips",
                ),
                html.P(chips_empty_init,
                       id="cmp-chips-empty", className="text-muted text-xs",
                       style={"marginTop": "6px"}),
            ]),
            # Guía colapsable de métricas
            html.Details([
                html.Summary("¿Cómo interpreto estos datos?", style={
                    "cursor": "pointer", "fontWeight": "600", "fontSize": "12px",
                    "color": "var(--muted)", "padding": "8px 0", "userSelect": "none",
                }),
                html.Dl([
                    html.Dt("Ritmo cardíaco"),
                    html.Dd("Cuántas veces late tu corazón por minuto. Sube con el esfuerzo y baja cuando descansas."),
                    html.Dt("Recuperación · RMSSD"),
                    html.Dd("Cómo se recupera tu sistema nervioso entre latido y latido. Más alto significa que descansaste bien."),
                    html.Dt("Acciones"),
                    html.Dd("Número de golpes o movimientos detectados por el sensor durante la sesión."),
                    html.Dt("Potencia de golpe · g"),
                    html.Dd("Fuerza media de cada impacto comparada con la gravedad. Más alto = más explosividad."),
                    html.Dt("Bienestar"),
                    html.Dd("Tu nota del día: energía, ánimo y sueño del 1 (mal) al 10 (perfecto)."),
                    html.Dt("Esfuerzo percibido · RPE"),
                    html.Dd("Cuánto te costó la sesión en tu propia percepción, del 1 al 10."),
                ], style={"marginTop": "8px", "fontSize": "12px", "lineHeight": "1.55",
                          "color": "var(--muted)", "paddingLeft": "4px"}),
            ], style={"marginTop": "12px", "borderTop": "1px solid var(--line)", "paddingTop": "10px"}),
            # Filtro de métricas
            html.Div(style={"marginTop": "14px", "borderTop": "1px solid var(--line)", "paddingTop": "12px"}, children=[
                html.Span("Métricas a mostrar", style={"fontWeight": "700", "fontSize": "13px",
                                                       "display": "block", "marginBottom": "8px"}),
                dcc.Checklist(
                    id="cmp-metrics-filter",
                    options=[
                        {"label": " Bienestar",              "value": "wellness"},
                        {"label": " Ritmo cardíaco",         "value": "bpm"},
                        {"label": " Recuperación · RMSSD",   "value": "rmssd"},
                        {"label": " Acciones",               "value": "hits"},
                        {"label": " Potencia · g",           "value": "force"},
                        {"label": " Esfuerzo · RPE",         "value": "rpe"},
                    ],
                    value=["wellness", "bpm", "rmssd", "hits"],
                    inline=True,
                    className="metrics-filter-checks",
                ),
            ]),
            # Botón de análisis IA
            html.Div(style={"marginTop": "14px", "borderTop": "1px solid var(--line)", "paddingTop": "12px"}, children=[
                html.Button(
                    "Analizar sesiones seleccionadas",
                    id="btn-cmp-run",
                    n_clicks=0,
                    className="btn btn-primary btn-sm",
                    title="Genera un análisis inteligente de las sesiones seleccionadas",
                ),
            ]),
            # Descarga PDF
            html.Div(style={"marginTop": "14px", "display": "flex", "alignItems": "center", "gap": "10px"}, children=[
                html.Button("Descargar informe (PDF)", id="btn-cmp-report",
                            className="btn btn-primary btn-xs", disabled=(not _REPORTLAB_OK)),
                html.Span(id="cmp-report-msg", className="compare-report-msg text-xs"),
                dcc.Download(id="cmp-report-dl"),
            ]),
            pdf_note,
            # Stores
            dcc.Store(id="cmp-session-ids",    data={"cur": None, "prev": None}),
            dcc.Store(id="cmp-sessions-multi", data=[]),
            # IDs hidden para backward-compat con callbacks existentes
            html.Div(style={"display": "none"}, children=[
                dcc.Dropdown(id="cmp-session", options=[], value=None),
                html.Div(id="cmp-prev-label"),
            ]),
        ])

        # ── Gráfica unificada de comparación ──
        overview_block = html.Div(className="card", style={"marginTop": "16px"}, children=[
            html.H4("Vista unificada de sesiones", className="card-title"),
            html.P("Compara las métricas principales de las sesiones seleccionadas en una sola gráfica.",
                   className="text-muted"),
            dcc.Graph(
                id="cmp-overview-chart",
                figure=placeholder_figure(400),
                config=graph_config(),
                style={"height": "400px", "width": "100%"},
            ),
        ])

        # ----------- styles ----------
        def _sess_table_style():
            return dict(
                sort_action="native",
                page_size=10,
                fixed_rows={"headers": True},
                style_table={"overflowX": "auto", "overflowY": "auto", "maxHeight": "360px"},
                style_cell={"whiteSpace": "nowrap", "textOverflow": "ellipsis", "maxWidth": "280px"},
            )

        # ----------- BLOQUE: Sesión seleccionada vs anterior -----------
        session_compare_block = html.Div(
            className="compare-session-stack",
            style={"marginTop": "16px"},
            children=[
                html.Div(className="page-head", children=[
                    html.H3("Cómo cambió la sesión", className="card-title"),
                    html.P(
                        "Compara una sesión real con la anterior para ver cómo cambió el ritmo, la carga y la recuperación.",
                        className="text-muted",
                    ),
                ]),
                html.Div(className="compare-overview-grid", children=[
                    html.Div(className="inner-card compare-overview-card", style={"padding": "16px"}, children=[
                        html.H4("Lo más importante", className="card-title"),
                        html.Div(id="cmp-overall", style={"marginTop": "10px", "fontWeight": "bold"}),
                    ]),
                    html.Div(className="inner-card compare-overview-card", style={"padding": "16px"}, children=[
                        html.H4("Qué te conviene hacer ahora", className="card-title"),
                        html.Ul(id="cmp-recs", className="list-compact", style={"marginTop": "10px"}),
                    ]),
                ]),

                html.Div(className="compare-core-grid", children=[
                    html.Div(className="inner-card compare-core-card", style={"padding": "16px"}, children=[
                        html.H4("Recuperación cardiovascular", className="card-title"),
                        html.P("Compara cómo respondió el cuerpo hoy frente a la sesión anterior.", className="text-muted", style={"marginBottom": "12px"}),
                        html.Div(
                            className="dt-pro",
                            children=DataTable(
                                id="cmp-ecg-sess-table",
                                columns=[
                                    {"name": "Sesión", "id": "label"},
                                    {"name": "Archivo", "id": "filename"},
                                    {"name": "Tiempo útil (s)", "id": "duration_s"},
                                    {"name": "Latidos", "id": "n_beats"},
                                    {"name": "Ritmo cardíaco", "id": "bpm"},
                                    {"name": "Variabilidad", "id": "sdnn_ms"},
                                    {"name": "Recuperación", "id": "rmssd_ms"},
                                ],
                                data=[],
                                **_sess_table_style()
                            ),
                        ),
                        html.Div(id="cmp-ecg-sess-badge"),
                        dcc.Graph(id="cmp-ecg-sess-bars", figure=placeholder_figure(380), config=graph_config(), style={"height": "380px", "width": "100%"}),
                    ]),
                    html.Div(className="inner-card compare-core-card", style={"padding": "16px"}, children=[
                        html.H4("Ritmo e impacto", className="card-title"),
                        html.P("Compara cuánto se movió el deportista, con qué ritmo y con qué nivel de explosividad.", className="text-muted", style={"marginBottom": "12px"}),
                        html.Div(
                            className="dt-pro",
                            children=DataTable(
                                id="cmp-imu-sess-table",
                                columns=[
                                    {"name": "Sesión", "id": "label"},
                                    {"name": "Archivo", "id": "filename"},
                                    {"name": "Acciones", "id": "n_hits"},
                                    {"name": "Ritmo", "id": "hits_per_min"},
                                    {"name": "Explosividad media", "id": "mean_int_g"},
                                    {"name": "Pico de explosividad", "id": "max_int_g"},
                                    {"name": "Carga de la sesión", "id": "load_index"},
                                ],
                                data=[],
                                **_sess_table_style()
                            ),
                        ),
                        html.Div(id="cmp-imu-sess-badge"),
                        dcc.Graph(id="cmp-imu-sess-bars", figure=placeholder_figure(380), config=graph_config(), style={"height": "380px", "width": "100%"}),
                    ]),
                ]),

            ],
        )

        # ---------- BLOQUE ECG clásico ----------
        ecg_block = html.Div(
            className="card compare-advanced-tool",
            style={"marginTop": "0"},
            children=[
                html.H4("Comparación avanzada por archivos · ECG / HRV", className="card-title"),
                html.Small(
                    "Úsalo como apoyo técnico cuando quieras revisar archivos ECG concretos fuera del flujo principal por sesión.",
                    className="text-muted",
                ),
                html.Br(),
                html.H4("Archivos ECG del deportista"),
                dcc.Store(id="cmp-ecg-refresh", data=0),
                dcc.ConfirmDialog(id="cmp-ecg-delete-confirm"),
                html.Div(
                    style={"display": "flex", "gap": "10px", "alignItems": "center", "margin": "10px 0 12px 0", "flexWrap": "wrap"},
                    children=[
                        html.Button(
                            "Eliminar seleccionados",
                            id="cmp-ecg-delete-btn",
                            n_clicks=0,
                            className="btn btn-danger btn-xs",
                        ),
                        html.Div(id="cmp-ecg-delete-status", className="text-muted text-xs"),
                    ],
                ),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-ecg-table",
columns=[
                        {"name": "ID", "id": "id"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Duración (s)", "id": "duration_s"},
                        {"name": "Latidos", "id": "n_beats"},
                        {"name": "BPM", "id": "bpm"},
                        {"name": "SDNN (ms)", "id": "sdnn_ms"},
                        {"name": "RMSSD (ms)", "id": "rmssd_ms"},
                    ],
                    data=[],
                    row_selectable="multi",
                    selected_rows=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={"whiteSpace": "nowrap", "textOverflow": "ellipsis", "maxWidth": "280px"},
                ),
                ),
                html.Div(
                    "Selecciona 2–4 filas para comparar (si seleccionas más, se toman las primeras 4).",
                    className="text-muted text-xs",
                    style={"marginTop": "6px"},
                ),
                html.Br(),
                dcc.Graph(id="cmp-ecg-bars", figure=empty_figure("Comparativa ECG", "Selecciona atletas para ver la comparativa.", height=420), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        # ---------- BLOQUES multi-upload ----------
        imu_block = html.Div(
            className="card compare-advanced-tool",
            style={"marginTop": "0"},
            children=[
                html.H4("Herramienta avanzada · IMU por archivos", className="card-title"),
                html.Small(
                    "Herramienta avanzada para contrastar archivos IMU concretos cuando necesites una revisión técnica adicional.",
                    className="text-muted",
                ),
                html.Br(),
                dcc.Upload(
                    id="cmp-imu-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos IMU (.csv)"),
                    multiple=True,
                    className="upload-dropzone",
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-imu-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "Golpes", "id": "n_hits"},
                        {"name": "Golpes/min", "id": "hits_per_min"},
                        {"name": "Intensidad media (g)", "id": "mean_int_g"},
                        {"name": "Intensidad máx (g)", "id": "max_int_g"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={"whiteSpace": "nowrap"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-imu-bars", figure=empty_figure("Comparativa IMU", "Selecciona atletas para ver la comparativa.", height=420), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        # Todos los componentes avanzados se mantienen ocultos para no romper callbacks
        hidden_all = html.Div(style={"display": "none"}, children=[
            # ECG avanzado
            dcc.Store(id="cmp-ecg-refresh", data=0),
            dcc.ConfirmDialog(id="cmp-ecg-delete-confirm"),
            html.Button(id="cmp-ecg-delete-btn", n_clicks=0),
            html.Div(id="cmp-ecg-delete-status"),
            DataTable(id="cmp-ecg-table", row_selectable="multi", selected_rows=[],
                      columns=[{"name": "ID", "id": "id"}, {"name": "Archivo", "id": "filename"},
                                {"name": "Duración (s)", "id": "duration_s"}, {"name": "Latidos", "id": "n_beats"},
                                {"name": "BPM", "id": "bpm"}, {"name": "SDNN (ms)", "id": "sdnn_ms"},
                                {"name": "RMSSD (ms)", "id": "rmssd_ms"}], data=[]),
            dcc.Graph(id="cmp-ecg-bars"),
            # IMU avanzado
            dcc.Upload(id="cmp-imu-upload", children=html.Div(""), multiple=True),
            DataTable(id="cmp-imu-table", columns=[{"name": "Sesión", "id": "session"},
                      {"name": "Golpes", "id": "n_hits"}, {"name": "Golpes/min", "id": "hits_per_min"},
                      {"name": "Intensidad media (g)", "id": "mean_int_g"}, {"name": "Intensidad máx (g)", "id": "max_int_g"}], data=[]),
            dcc.Graph(id="cmp-imu-bars"),
            # EMG/RESP sesión (ocultos — reservados para reactivación futura)
            html.Div(id="cmp-emg-sess-table", style={"display": "none"}),
            html.Div(id="cmp-emg-sess-badge", style={"display": "none"}),
            dcc.Graph(id="cmp-emg-sess-bars", style={"display": "none"}),
            html.Div(id="cmp-resp-sess-table", style={"display": "none"}),
            html.Div(id="cmp-resp-sess-badge", style={"display": "none"}),
            dcc.Graph(id="cmp-resp-sess-bars", style={"display": "none"}),
            dcc.Upload(id="cmp-emg-upload", children=html.Div(""), multiple=True, style={"display": "none"}),
            DataTable(id="cmp-emg-table", columns=[], data=[], style_table={"display": "none"}),
            dcc.Graph(id="cmp-emg-bars", style={"display": "none"}),
            dcc.Upload(id="cmp-resp-upload", children=html.Div(""), multiple=True, style={"display": "none"}),
            DataTable(id="cmp-resp-table", columns=[], data=[], style_table={"display": "none"}),
            dcc.Graph(id="cmp-resp-bars", style={"display": "none"}),
        ])

        shell_cls = "coach-shell" if role == "coach" else ""

        if role == "coach":
            cmp_title = "Comparar rendimiento"
            cmp_sub = "Selecciona a un atleta y una sesión para ver su evolución técnica y fisiológica en el tiempo."
            cmp_pills = [
                html.Span(
                    (session.get("sport") or "Combate").title(),
                    className="session-pill",
                ),
                html.Span("Coach · Comparar rendimiento", className="session-pill session-pill--muted"),
            ]
        else:
            cmp_title = "Comparar sesiones"
            cmp_sub = "Revisa cómo vienen cambiando las sesiones y baja al detalle técnico solo cuando de verdad haga falta."
            cmp_pills = None

        page_head_children = []
        if cmp_pills:
            page_head_children.append(html.Div(className="session-pill-row", children=cmp_pills))
        page_head_children += [
            html.H2(cmp_title),
            html.P(cmp_sub, className="text-muted"),
        ]

        return html.Div(
            className=shell_cls,
            children=[
                html.Div(className="page-head", children=page_head_children),
                session_chips_block,
                dcc.Loading(
                    html.Div(id="cmp-analysis-output"),
                    type="circle",
                    color="var(--neon)",
                ),
                dcc.Loading(
                    html.Div(id="cmp-trend-panel"),
                    type="dot",
                    color="var(--neon)",
                    style={"minHeight": "0"},
                ),
                overview_block,
                # Coaches: detalle técnico colapsado. Deportistas: IDs ocultos para no romper callbacks.
                html.Details(id="cmp-detail-toggle", open=False, children=[
                    html.Summary("Ver detalle técnico de sensores →", style={
                        "cursor": "pointer", "fontWeight": "600", "fontSize": "13px",
                        "color": "var(--muted)", "padding": "14px 0 8px",
                        "listStyle": "none", "WebkitAppearance": "none",
                    }),
                    session_compare_block,
                ], style={"marginTop": "8px"}) if role == "coach" else
                html.Div(style={"display": "none"}, children=[
                    html.Details(id="cmp-detail-toggle", open=False, children=[]),
                    html.Div(id="cmp-overall"),
                    html.Ul(id="cmp-recs"),
                    DataTable(id="cmp-ecg-sess-table",
                              columns=[{"name": "Sesión", "id": "label"}, {"name": "Archivo", "id": "filename"},
                                       {"name": "Tiempo (s)", "id": "duration_s"}, {"name": "Latidos", "id": "n_beats"},
                                       {"name": "Ritmo cardíaco", "id": "bpm"}, {"name": "Variabilidad", "id": "sdnn_ms"},
                                       {"name": "Recuperación", "id": "rmssd_ms"}], data=[]),
                    dcc.Graph(id="cmp-ecg-sess-bars"),
                    html.Div(id="cmp-ecg-sess-badge"),
                    DataTable(id="cmp-imu-sess-table",
                              columns=[{"name": "Sesión", "id": "label"}, {"name": "Archivo", "id": "filename"},
                                       {"name": "Acciones", "id": "n_hits"}, {"name": "Ritmo", "id": "hits_per_min"},
                                       {"name": "Potencia media", "id": "mean_int_g"}, {"name": "Pico", "id": "max_int_g"},
                                       {"name": "Carga", "id": "load_index"}], data=[]),
                    dcc.Graph(id="cmp-imu-sess-bars"),
                    html.Div(id="cmp-imu-sess-badge"),
                ]),
                hidden_all,
            ]
        )

    # ====================== CALLBACKS ======================

    def _register_callbacks(self):
        app = self.app
        db = self.db

        def _session_ids_for_user(uid, limit=200):
            if not uid or not _can_access_athlete(db, uid):
                return set()
            try:
                sessions = db.list_sessions(int(uid), limit=limit) or []
            except Exception:
                sessions = []
            return {_safe_int(s.get("id")) for s in sessions if _safe_int(s.get("id"))}

        def _filter_session_ids(uid, raw_ids, limit=6):
            allowed = _session_ids_for_user(uid)
            out = []
            for sid in raw_ids or []:
                sid_int = _safe_int(sid)
                if sid_int and sid_int in allowed and sid_int not in out:
                    out.append(sid_int)
                if len(out) >= limit:
                    break
            return out

        # ── Helpers para el overview chart ──────────────────────────────────
        _METRIC_LABELS = {
            "wellness": "Bienestar",
            "bpm":      "Ritmo cardíaco",
            "rmssd":    "Recuperación · RMSSD",
            "hits":     "Acciones",
            "force":    "Potencia de golpe (g)",
            "rpe":      "Esfuerzo percibido · RPE",
        }

        def _wellness_for_session(uid, session_id):
            try:
                qs = db.list_questionnaires(int(uid)) or []
                linked = [q for q in qs if _safe_int(q.get("session_id")) == session_id]
                if linked:
                    lq = max(linked, key=lambda q: q.get("ts") or "")
                    return _safe_float(lq.get("wellness_score")), _safe_float(lq.get("rpe"))
                if hasattr(db, "list_sessions"):
                    sessions_all = db.list_sessions(int(uid), limit=200) or []
                    s = next((s for s in sessions_all if s.get("id") == session_id), None)
                    if s and s.get("ts_start"):
                        from datetime import datetime as _dt2, timedelta as _td
                        try:
                            sess_dt = _dt2.fromisoformat(s["ts_start"][:19])
                            day_qs = []
                            for q in qs:
                                qt_str = (q.get("ts") or "")[:19]
                                if not qt_str:
                                    continue
                                try:
                                    qt = _dt2.fromisoformat(qt_str)
                                    if abs((qt - sess_dt).total_seconds()) < 86400:
                                        day_qs.append(q)
                                except Exception:
                                    pass
                            if day_qs:
                                lq = max(day_qs, key=lambda q: q.get("ts") or "")
                                return _safe_float(lq.get("wellness_score")), _safe_float(lq.get("rpe"))
                        except Exception:
                            pass
            except Exception:
                pass
            return None, None

        def _build_session_opts(uid):
            if not uid or not _can_access_athlete(db, uid) or not hasattr(db, "list_sessions"):
                return [], []
            try:
                sessions = db.list_sessions(int(uid), limit=30) or []
            except Exception:
                sessions = []
            sessions = sorted(
                sessions,
                key=lambda s: ((s.get("ts_start") or ""), _safe_int(s.get("id")) or 0),
                reverse=True,
            )
            opts = []
            for s in sessions:
                sid = s.get("id")
                if sid is None:
                    continue
                ts  = (s.get("ts_start") or "")[:10]
                st  = (s.get("status") or "")
                lbl = f"#{sid} · {ts}" + (f" · {st}" if st and st != "—" else "")
                opts.append({"label": lbl, "value": sid})
            return opts, sessions

        def _get_prev_id(uid, chosen_id, sessions):
            if not chosen_id:
                return None, "—"
            if hasattr(db, "get_previous_session"):
                try:
                    ps = db.get_previous_session(int(uid), int(chosen_id))
                    return (ps.get("id") if ps else None), (_session_label(ps) if ps else "—")
                except Exception:
                    pass
            ids = [(_safe_int(s.get("id")) or 0) for s in sessions]
            try:
                idx = ids.index(int(chosen_id))
                if idx + 1 < len(sessions):
                    ps = sessions[idx + 1]
                    return _safe_int(ps.get("id")), _session_label(ps)
            except Exception:
                pass
            return None, "—"

        # ---------- Poblar chips + backward-compat dropdown ----------
        @app.callback(
            Output("cmp-sessions-chips",   "options"),
            Output("cmp-sessions-chips",   "value"),
            Output("cmp-chips-empty",      "children"),
            Output("cmp-session",          "options"),
            Output("cmp-session",          "value"),
            Output("cmp-session-ids",      "data"),
            Output("cmp-prev-label",       "children"),
            Input("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def populate_session_ui(user_id):
            uid = _safe_int(user_id)
            if not uid:
                return [], [], "", [], None, {"cur": None, "prev": None}, ""
            opts, sessions = _build_session_opts(uid)
            if not opts:
                return [], [], "Sin sesiones registradas todavía.", [], None, {"cur": None, "prev": None}, ""
            chosen   = opts[0]["value"]
            default  = [chosen]
            prev_id, _ = _get_prev_id(uid, chosen, sessions)
            return opts, default, "", opts, chosen, {"cur": chosen, "prev": prev_id}, ""

        # ---------- Cambio de chips → actualiza sesión activa ----------
        @app.callback(
            Output("cmp-session",          "value",    allow_duplicate=True),
            Output("cmp-session-ids",      "data",     allow_duplicate=True),
            Output("cmp-sessions-multi",   "data"),
            Input("cmp-sessions-chips",    "value"),
            State("cmp-user",              "value"),
            prevent_initial_call=True,
        )
        def chips_changed(selected, user_id):
            uid = _safe_int(user_id)
            if not selected:
                return None, {"cur": None, "prev": None}, []
            allowed_selected = _filter_session_ids(uid, selected, limit=6)
            if not allowed_selected:
                return None, {"cur": None, "prev": None}, []
            chosen = allowed_selected[0]
            _, sessions = _build_session_opts(uid) if uid else ([], [])
            prev_id, _ = _get_prev_id(uid, chosen, sessions) if uid else (None, "—")
            return chosen, {"cur": chosen, "prev": prev_id}, allowed_selected

        # ---------- Limpiar chips ----------
        @app.callback(
            Output("cmp-sessions-chips", "value", allow_duplicate=True),
            Input("btn-cmp-chips-clear", "n_clicks"),
            prevent_initial_call=True,
        )
        def clear_chips(_):
            return []

        # ---------- Contador de chips ----------
        @app.callback(
            Output("cmp-chips-count", "children"),
            Input("cmp-sessions-chips", "value"),
            prevent_initial_call=True,
        )
        def chips_count(vals):
            n = len(vals or [])
            if n == 0:
                return "ninguna seleccionada"
            return "1 sesión" if n == 1 else f"{n} sesiones"

        # ---------- Gráfica unificada ----------
        @app.callback(
            Output("cmp-overview-chart", "figure"),
            Input("cmp-sessions-multi",  "data"),
            Input("cmp-metrics-filter",  "value"),
            State("cmp-user",            "value"),
            prevent_initial_call=True,
        )
        def update_overview_chart(session_ids, selected_metrics, user_id):
            from ui_charts import PS_PALETTE
            uid = _safe_int(user_id)
            if not session_ids or not uid or not _can_access_athlete(db, uid):
                return empty_figure("Comparativa de sesiones",
                                    "Selecciona sesiones para comparar.", height=400)

            selected_metrics = selected_metrics or ["wellness", "bpm", "rmssd", "hits"]
            sids = _filter_session_ids(uid, session_ids, limit=6)
            if not sids:
                return empty_figure("Comparativa de sesiones",
                                    "Selecciona sesiones válidas para comparar.", height=400)

            rows = []
            for sid in sids:
                row = {"label": f"#{sid}"}
                ecg = _ecg_row_for_session(db, uid, sid, f"#{sid}")
                if ecg:
                    row["bpm"]   = _safe_float(ecg.get("bpm"))
                    row["rmssd"] = _safe_float(ecg.get("rmssd_ms"))
                imu = _latest_by_session(db, "imu", sid, user_id=uid)
                if imu:
                    row["hits"]  = _safe_float(imu.get("n_hits"))
                    row["force"] = _safe_float(imu.get("mean_int_g"))
                w, rpe = _wellness_for_session(uid, sid)
                if w   is not None: row["wellness"] = w
                if rpe is not None: row["rpe"]      = rpe
                rows.append(row)

            if not rows:
                return empty_figure("Comparativa de sesiones", "Sin datos disponibles.", height=400)

            fig    = go.Figure()
            labels = [r["label"] for r in rows]

            for metric in selected_metrics:
                values = [r.get(metric) for r in rows]
                if all(v is None for v in values):
                    continue
                non_none = [v for v in values if v is not None]
                max_v = max(non_none) if non_none and max(non_none) != 0 else 1.0
                normalized = [(v / max_v * 100) if v is not None else None for v in values]
                fig.add_trace(go.Bar(
                    name=_METRIC_LABELS.get(metric, metric),
                    x=labels,
                    y=[n if n is not None else 0 for n in normalized],
                    text=[f"{v:.1f}" if v is not None else "—" for v in values],
                    textposition="outside",
                    customdata=values,
                    hovertemplate=(
                        f"<b>{_METRIC_LABELS.get(metric, metric)}</b>"
                        "<br>Valor: %{customdata:.1f}"
                        "<extra></extra>"
                    ),
                ))

            if not fig.data:
                return empty_figure("Comparativa de sesiones",
                                    "Sin datos disponibles para las métricas seleccionadas.", height=400)

            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa de sesiones seleccionadas",
                              y_title="% del máximo por métrica", height=400)
            try:
                fig.update_layout(legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            except Exception:
                pass
            return fig

        # ---------- ECG + IMU + Resumen — un solo callback (antes eran 3) ----------
        @app.callback(
            Output("cmp-ecg-sess-table",  "data"),
            Output("cmp-ecg-sess-bars",   "figure"),
            Output("cmp-ecg-sess-badge",  "children"),
            Output("cmp-imu-sess-table",  "data"),
            Output("cmp-imu-sess-bars",   "figure"),
            Output("cmp-imu-sess-badge",  "children"),
            Output("cmp-overall",         "children"),
            Output("cmp-recs",            "children"),
            Input("cmp-session-ids",      "data"),
            Input("cmp-detail-toggle",    "open"),
            State("cmp-user",             "value"),
            prevent_initial_call=False,
        )
        def session_compare_all(store, detail_open, user_id):
            def _no_data():
                return ([], placeholder_figure(380), None, [], placeholder_figure(380), None, "", [])

            role = str(session.get("role") or "")
            if role != "coach" or not detail_open:
                return _no_data()

            uid = _safe_int(user_id)
            if not uid or not store or not _can_access_athlete(db, uid):
                return _no_data()
            cur_id  = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return _no_data()
            allowed = _session_ids_for_user(uid)
            if cur_id not in allowed:
                return _no_data()
            if prev_id and prev_id not in allowed:
                prev_id = None

            has_ecg = _has_sensor(db, uid, "ECG")
            has_imu = _has_sensor(db, uid, "IMU")

            # ── ECG ──────────────────────────────────────────────────────────
            if has_ecg:
                ecg_cur  = _ecg_row_for_session(db, uid, cur_id,  "Esta sesión")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Sesión anterior") if prev_id else None
                ecg_rows = [r for r in [ecg_cur, ecg_prev] if r]
                if ecg_cur and ecg_prev:
                    ecg_fig = _compare_bar_fig(
                        "Recuperación cardiovascular: esta sesión vs la anterior",
                        ["Esta sesión", "Sesión anterior"],
                        series=[
                            {"name": "Ritmo cardíaco", "y": [ecg_cur["bpm"],    ecg_prev["bpm"]]},
                            {"name": "Variabilidad",   "y": [ecg_cur["sdnn_ms"],ecg_prev["sdnn_ms"]]},
                            {"name": "Recuperación",   "y": [ecg_cur["rmssd_ms"],ecg_prev["rmssd_ms"]]},
                        ],
                        y_title="Valor",
                    )
                    ecg_badge = _ecg_recovery_badge(ecg_cur, ecg_prev)
                else:
                    ecg_fig   = go.Figure()
                    apply_chart_style(ecg_fig, title="Recuperación cardiovascular: faltan datos",
                                      x_title="Sesión", y_title="Valor", height=380)
                    ecg_badge = _badge("Aún no hay suficientes datos cardiovasculares en ambas sesiones.", "neutral")
            else:
                ecg_rows  = []
                ecg_fig   = placeholder_figure(380)
                ecg_badge = _badge("Este deportista aún no tiene activada la lectura cardiovascular.", "neutral")
                ecg_cur = ecg_prev = None

            # ── IMU ──────────────────────────────────────────────────────────
            def _imu_dict(row, label):
                hpm = float(row.get("hits_per_min", 0) or 0)
                mi  = float(row.get("mean_int_g",   0) or 0)
                return {
                    "label": label,
                    "filename": row.get("filename", "—"),
                    "n_hits":       int(row.get("n_hits", 0) or 0),
                    "hits_per_min": round(hpm, 1),
                    "mean_int_g":   round(mi,  2),
                    "max_int_g":    round(float(row.get("max_int_g", 0) or 0), 2),
                    "load_index":   round(hpm * mi, 2),
                }

            if has_imu:
                imu_cur_row  = _latest_by_session(db, "imu", cur_id, user_id=uid)
                imu_prev_row = _latest_by_session(db, "imu", prev_id, user_id=uid) if prev_id else None
                imu_cur  = _imu_dict(imu_cur_row,  "Esta sesión")     if imu_cur_row  else None
                imu_prev = _imu_dict(imu_prev_row, "Sesión anterior") if imu_prev_row else None
                imu_rows = [r for r in [imu_cur, imu_prev] if r]
                if imu_cur and imu_prev:
                    imu_fig = _compare_bar_fig(
                        "Ritmo e impacto: esta sesión vs la anterior",
                        ["Esta sesión", "Sesión anterior"],
                        series=[
                            {"name": "Ritmo de acción",   "y": [imu_cur["hits_per_min"], imu_prev["hits_per_min"]]},
                            {"name": "Explosividad media","y": [imu_cur["mean_int_g"],   imu_prev["mean_int_g"]]},
                            {"name": "Carga de la sesión","y": [imu_cur["load_index"],   imu_prev["load_index"]]},
                        ],
                        y_title="Valor",
                    )
                    imu_badge = _load_badge(imu_cur["load_index"], imu_prev["load_index"],
                                            label="Carga de la sesión")
                else:
                    imu_fig   = go.Figure()
                    apply_chart_style(imu_fig, title="Ritmo e impacto: faltan datos",
                                      x_title="Sesión", y_title="Valor", height=380)
                    imu_badge = _badge("Aún no hay suficientes datos de movimiento en ambas sesiones.", "neutral")
            else:
                imu_rows  = []
                imu_fig   = placeholder_figure(380)
                imu_badge = _badge("Este deportista aún no tiene activada la lectura de movimiento.", "neutral")
                imu_cur = imu_prev = None

            # ── Resumen + recomendaciones ────────────────────────────────────
            ecg_b = _ecg_recovery_badge(ecg_cur, ecg_prev) if (ecg_cur and ecg_prev) else None
            imu_b = (_load_badge(imu_cur["load_index"], imu_prev["load_index"], label="Carga de la sesión")
                     if (imu_cur and imu_prev) else None)
            overall = _overall_summary(ecg_b, imu_b)
            recs    = _recommendations(ecg_b, imu_b)

            return (ecg_rows, ecg_fig, ecg_badge,
                    imu_rows, imu_fig, imu_badge,
                    overall, [html.Li(r) for r in recs])

        # ---------- Reporte PDF con imágenes ----------
        @app.callback(
            Output("cmp-report-dl", "data"),
            Output("cmp-report-msg", "children"),
            Input("btn-cmp-report", "n_clicks"),
            State("cmp-session-ids", "data"),
            State("cmp-sessions-chips", "value"),
            State("cmp-user", "value"),
            prevent_initial_call=True,
        )
        def download_report(n, store, chips_selected, user_id):
            if not n:
                raise PreventUpdate

            if not _REPORTLAB_OK:
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(txt.encode("utf-8"), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            uid = _safe_int(user_id)
            if not uid or not _can_access_athlete(db, uid):
                return dash.no_update, "Selecciona un deportista para generar el informe."

            # Resolver sesión: store tiene prioridad; chips como fallback
            cur_id  = _safe_int((store or {}).get("cur"))
            prev_id = _safe_int((store or {}).get("prev"))
            if not cur_id and chips_selected:
                sel = [_safe_int(s) for s in chips_selected if _safe_int(s)]
                if sel:
                    cur_id  = sel[0]
                    prev_id = sel[1] if len(sel) > 1 else None
            if not cur_id:
                return dash.no_update, "Selecciona al menos una sesión para generar el informe."
            allowed = _session_ids_for_user(uid)
            if cur_id not in allowed:
                return dash.no_update, "La sesión seleccionada no pertenece a este deportista."
            if prev_id and prev_id not in allowed:
                prev_id = None

            try:
                athlete = db.get_user_by_id(int(uid))
            except Exception:
                athlete = None

            # ── Calcular datos de sensores ────────────────────────────────
            ecg_cur = ecg_prev = None
            imu_cur = imu_prev = None
            ecg_badge = imu_badge = None

            if _has_sensor(db, uid, "ECG"):
                ecg_cur  = _ecg_row_for_session(db, uid, cur_id,  "Esta sesión")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Sesión anterior") if prev_id else None
                if ecg_cur and ecg_prev:
                    ecg_badge = _ecg_recovery_badge(ecg_cur, ecg_prev)

            if _has_sensor(db, uid, "IMU"):
                r = _latest_by_session(db, "imu", cur_id, user_id=uid)
                p = _latest_by_session(db, "imu", prev_id, user_id=uid) if prev_id else None
                def _imu_dict_pdf(row):
                    hpm = float(row.get("hits_per_min", 0) or 0)
                    mi  = float(row.get("mean_int_g",   0) or 0)
                    return {"hits_per_min": hpm, "mean_int_g": mi, "load_index": hpm * mi,
                            "n_hits": int(row.get("n_hits", 0) or 0),
                            "max_int_g": float(row.get("max_int_g", 0) or 0),
                            "filename": row.get("filename", "—")}
                imu_cur  = _imu_dict_pdf(r) if r else None
                imu_prev = _imu_dict_pdf(p) if p else None
                if imu_cur and imu_prev:
                    imu_badge = _load_badge(imu_cur["load_index"], imu_prev["load_index"], label="Carga de la sesión")

            overall    = _overall_summary(ecg_badge, imu_badge)
            recs       = _recommendations(ecg_badge, imu_badge)

            # ── Tabla ─────────────────────────────────────────────────────
            headers    = ["Métrica", "Esta sesión", "Sesión anterior", "Δ", "%"]
            col_widths = [6.0 * cm, 3.0 * cm, 3.0 * cm, 2.2 * cm, 2.0 * cm]

            def _row_metric(label, cur, prev, unit="", decimals=2):
                if cur is None or prev is None:
                    return [label, "—", "—", "—", "—"]
                d, pct = _delta_and_pct(cur, prev)
                return [label,
                        f"{float(cur):.{decimals}f}{unit}",
                        f"{float(prev):.{decimals}f}{unit}",
                        f"{d:+.{decimals}f}{unit}" if d is not None else "—",
                        _fmt_pct(pct)]

            table_rows = []
            if ecg_cur and ecg_prev:
                table_rows += [
                    _row_metric("Ritmo cardíaco",  ecg_cur.get("bpm"),      ecg_prev.get("bpm"),      decimals=0),
                    _row_metric("Variabilidad",    ecg_cur.get("sdnn_ms"),  ecg_prev.get("sdnn_ms"),  unit=" ms", decimals=0),
                    _row_metric("Recuperación",    ecg_cur.get("rmssd_ms"), ecg_prev.get("rmssd_ms"), unit=" ms", decimals=0),
                ]
            if imu_cur and imu_prev:
                table_rows += [
                    _row_metric("Ritmo de acción",    imu_cur["hits_per_min"], imu_prev["hits_per_min"], decimals=1),
                    _row_metric("Explosividad media", imu_cur["mean_int_g"],   imu_prev["mean_int_g"],   unit=" g"),
                    _row_metric("Carga de la sesión", imu_cur["load_index"],   imu_prev["load_index"]),
                ]

            availability_lines = []
            for _lbl, _c, _p in [("Recuperación cardiovascular", ecg_cur, ecg_prev),
                                  ("Ritmo e impacto", imu_cur, imu_prev)]:
                if _c and _p:
                    availability_lines.append(f"{_lbl}: comparación disponible.")
                elif _c or _p:
                    availability_lines.append(f"{_lbl}: datos parciales, no suficientes para comparar.")
            if not availability_lines:
                availability_lines.append("No hay métricas comparables guardadas por sesión.")

            # ── Generar PDF ───────────────────────────────────────────────
            try:
                from report_utils import CombatIQPDF
                name          = (athlete or {}).get("name", "Deportista")
                sport_val     = (athlete or {}).get("sport", "")
                overall_clean = _report_text(overall)
                recs_clean    = [_report_text(r) for r in recs]

                def _badge_txt(b):
                    return _report_text(b.children) if b is not None else None

                pdf = CombatIQPDF()
                pdf.header(
                    "Informe comparativo de sesión",
                    f"Sesión #{cur_id} comparada con sesión #{prev_id if prev_id else 'sin sesión anterior'}",
                    name, sport_val,
                    session=f"Sesión #{cur_id}  /  Anterior: #{prev_id if prev_id else '—'}",
                )

                _kpi_cards = []
                if ecg_cur:
                    _kpi_cards += [{"label": "FC media",   "value": f"{ecg_cur.get('bpm',0):.0f}",    "unit": "lpm"},
                                   {"label": "RMSSD",      "value": f"{ecg_cur.get('rmssd_ms',0):.0f}", "unit": "ms"}]
                if imu_cur:
                    _kpi_cards += [{"label": "Golpes/min", "value": f"{imu_cur.get('hits_per_min',0):.1f}", "unit": ""},
                                   {"label": "Potencia",   "value": f"{imu_cur.get('mean_int_g',0):.2f}",   "unit": "g"}]
                if _kpi_cards:
                    pdf.metric_table(_kpi_cards[:4])
                    pdf.spacer(0.1)

                pdf.card("Estado de la sesión", [overall_clean],
                         subtitle="Lectura consolidada de los datos comparables disponibles.")
                pdf.card("Disponibilidad de datos", availability_lines,
                         subtitle="Señales que entran en esta comparación.")

                _active = [(lbl, _badge_txt(b)) for lbl, b in
                           [("Recuperación cardiovascular", ecg_badge), ("Carga e impacto", imu_badge)]
                           if _badge_txt(b)]
                if _active:
                    pdf.section_title("Lectura por señal")
                    for _lbl, _txt in _active:
                        pdf.card(_lbl, [_txt], fill=None, gap=0.18)

                pdf.section_title("Tabla comparativa", "Esta sesión vs la anterior")
                if table_rows:
                    pdf.table(headers, table_rows, col_widths=col_widths)
                else:
                    pdf.card("Sin datos comparables",
                             ["Guarda señales asociadas a sesiones para ver el detalle cuantitativo."])

                # Figuras
                figs = []
                if ecg_cur and ecg_prev:
                    fig = go.Figure()
                    x = ["Esta sesión", "Sesión anterior"]
                    fig.add_trace(go.Bar(x=x, y=[ecg_cur["bpm"],      ecg_prev["bpm"]],      name="Ritmo cardíaco"))
                    fig.add_trace(go.Bar(x=x, y=[ecg_cur["sdnn_ms"],  ecg_prev["sdnn_ms"]],  name="Variabilidad"))
                    fig.add_trace(go.Bar(x=x, y=[ecg_cur["rmssd_ms"], ecg_prev["rmssd_ms"]], name="Recuperación"))
                    fig.update_layout(barmode="group")
                    _apply_pdf_chart_style(fig, "Recuperación cardiovascular", y_title="Valor")
                    figs.append(("Recuperación cardiovascular", fig))
                if imu_cur and imu_prev:
                    fig = go.Figure()
                    x = ["Esta sesión", "Sesión anterior"]
                    fig.add_trace(go.Bar(x=x, y=[imu_cur["hits_per_min"], imu_prev["hits_per_min"]], name="Ritmo"))
                    fig.add_trace(go.Bar(x=x, y=[imu_cur["mean_int_g"],   imu_prev["mean_int_g"]],   name="Potencia media"))
                    fig.add_trace(go.Bar(x=x, y=[imu_cur["load_index"],   imu_prev["load_index"]],   name="Carga"))
                    fig.update_layout(barmode="group")
                    _apply_pdf_chart_style(fig, "Ritmo e impacto", y_title="Valor")
                    figs.append(("Ritmo e impacto", fig))

                if figs:
                    pdf.section_title("Gráficas comparativas")
                    for _ft, _fig in figs:
                        try:
                            pdf.chart(_fig, _ft, max_h_cm=7.5)
                        except Exception:
                            pdf.card(f"Gráfica: {_ft}", ["No se pudo generar la imagen."])
                else:
                    pdf.card("Gráficas comparativas",
                             ["No hay suficientes datos comparables para las gráficas."])

                if recs_clean:
                    pdf.section_title("Recomendaciones")
                    pdf.card("", [f"• {r}" for r in recs_clean],
                             subtitle="Sugerencias a partir de la comparación disponible.")

                pdf.card("Nota de uso",
                         ["Interpretación heurística para el entrenamiento.",
                          "Úsala como apoyo a la decisión, no como diagnóstico clínico."])

                pdf_bytes = pdf.finish()
                filename  = f"CombatIQ_Informe_Sesion_{cur_id}.pdf"
                return dcc.send_bytes(pdf_bytes, filename), ""
            except Exception as exc:
                return dash.no_update, f"Error al generar el PDF: {str(exc)[:150]}"

        # ---------- Análisis IA de sesiones seleccionadas ----------
        def _collect_session_metrics(uid, session_id):
            """Recoge métricas IMU, ECG y wellness de una sesión para el análisis IA."""
            if not uid or not session_id:
                return None
            m = {"session_id": session_id, "ts": None, "imu": None, "ecg": None, "wellness": None}
            try:
                if hasattr(db, "list_sessions"):
                    all_s = db.list_sessions(int(uid), limit=200) or []
                    s = next((s for s in all_s if s.get("id") == session_id), None)
                    if s:
                        m["ts"] = (s.get("ts_start") or "")[:10]
            except Exception:
                pass
            imu_row = _latest_by_session(db, "imu", session_id, user_id=uid)
            if imu_row:
                m["imu"] = {
                    "n_hits":       _safe_float(imu_row.get("n_hits"),       0),
                    "hits_per_min": _safe_float(imu_row.get("hits_per_min"), 0),
                    "mean_int_g":   _safe_float(imu_row.get("mean_int_g"),   0),
                    "max_int_g":    _safe_float(imu_row.get("max_int_g"),    0),
                }
            ecg = _ecg_row_for_session(db, uid, session_id, f"#{session_id}")
            if ecg:
                m["ecg"] = {
                    "bpm":   _safe_float(ecg.get("bpm"),      0),
                    "rmssd": _safe_float(ecg.get("rmssd_ms"), 0),
                }
            w, _ = _wellness_for_session(uid, session_id)
            if w is not None:
                m["wellness"] = w
            return m

        @app.callback(
            Output("cmp-analysis-output", "children"),
            Input("btn-cmp-run", "n_clicks"),
            State("cmp-sessions-multi", "data"),
            State("cmp-sessions-chips", "value"),
            State("cmp-user", "value"),
            prevent_initial_call=True,
        )
        def run_comparison(n_clicks, session_ids, chips_selected, user_id):
            if not n_clicks:
                raise PreventUpdate
            uid  = _safe_int(user_id)
            raw  = session_ids if session_ids else (chips_selected or [])
            sids = _filter_session_ids(uid, raw, limit=6)
            if not uid or not _can_access_athlete(db, uid) or not sids:
                return html.Div("Selecciona al menos una sesión para analizar.", className="text-muted text-sm",
                                style={"padding": "12px 0"})
            try:
                athlete = db.get_user_by_id(int(uid))
            except Exception:
                athlete = None
            name      = (athlete or {}).get("name", "el atleta")
            sport_val = (athlete or {}).get("sport", "combate")
            sessions_data = [m for sid in sids for m in [_collect_session_metrics(uid, sid)] if m]
            if not sessions_data:
                return html.Div("No hay datos suficientes en las sesiones seleccionadas.", className="text-muted text-sm",
                                style={"padding": "12px 0"})
            try:
                from ai_insights import generate_session_comparison
                analysis = generate_session_comparison(sessions_data, athlete_name=name, sport=sport_val)
            except Exception as e:
                analysis = f"No se pudo generar el análisis: {e}"
            return html.Div([
                html.H4("Análisis de sesiones", className="card-title", style={"marginTop": "0", "marginBottom": "12px"}),
                dcc.Markdown(analysis, className="ai-analysis-text"),
            ], className="card", style={"marginTop": "16px"})

        @app.callback(
            Output("cmp-trend-panel", "children"),
            Input("btn-cmp-run", "n_clicks"),
            State("cmp-sessions-multi", "data"),
            State("cmp-sessions-chips", "value"),
            State("cmp-user", "value"),
            prevent_initial_call=True,
        )
        def run_trend_analysis(n_clicks, session_ids, chips_selected, user_id):
            if not n_clicks:
                raise PreventUpdate
            uid  = _safe_int(user_id)
            raw  = session_ids if session_ids else (chips_selected or [])
            sids = _filter_session_ids(uid, raw, limit=6)
            if not uid or not _can_access_athlete(db, uid) or len(sids) < 2:
                return html.Div()
            try:
                athlete = db.get_user_by_id(int(uid))
            except Exception:
                athlete = None
            name      = (athlete or {}).get("name", "el atleta")
            sport_val = (athlete or {}).get("sport", "combate")
            sessions_data = [m for sid in sids for m in [_collect_session_metrics(uid, sid)] if m]
            if len(sessions_data) < 2:
                return html.Div()
            try:
                from ai_insights import analyze_trend as _analyze_trend
                result = _analyze_trend(sessions_data, athlete_name=name, sport=sport_val)
            except Exception as e:
                return html.Div(f"Error en análisis de patrones: {e}",
                                className="text-muted", style={"padding": "8px", "fontSize": "12px"})

            if result.get("error"):
                return html.Div()

            patterns  = result.get("patterns", [])
            narrative = result.get("narrative", "")

            if not patterns:
                return html.Div()

            _SEV_COLOR  = {"positivo": "#27c98f", "vigilar": "#f0a832", "corregir": "#e45a5a"}
            _SEV_LABEL  = {"positivo": "OK", "vigilar": "OBS", "corregir": "FIX"}
            pattern_items = []
            for p in patterns:
                sev   = p.get("severity", "vigilar")
                color = _SEV_COLOR.get(sev, "#8fa3bf")
                label = _SEV_LABEL.get(sev, sev[:3].upper())
                pattern_items.append(html.Div(
                    style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                           "alignItems": "flex-start"},
                    children=[
                        html.Span(label, style={
                            "fontSize": "9px", "fontWeight": "700", "color": color,
                            "border": f"1px solid {color}", "borderRadius": "4px",
                            "padding": "2px 5px", "whiteSpace": "nowrap",
                            "minWidth": "30px", "textAlign": "center", "flexShrink": "0",
                        }),
                        html.Div([
                            html.Div(p.get("pattern", ""),
                                     style={"fontSize": "12px", "color": "var(--ink)", "lineHeight": "1.4"}),
                            html.Div(p.get("evidence", ""),
                                     style={"fontSize": "11px", "color": "var(--muted)", "marginTop": "2px"}),
                            *([html.Div(f"▶ {p['action']}",
                                        style={"fontSize": "11px", "color": "var(--neon)", "marginTop": "3px"})]
                              if p.get("action") else []),
                        ], style={"flex": "1"}),
                    ],
                ))

            return html.Div(
                className="card",
                style={"marginTop": "12px"},
                children=[
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "12px"},
                        children=[
                            html.H4("Patrones detectados", className="card-title",
                                    style={"margin": "0"}),
                            html.Span("OPUS IA", style={
                                "fontSize": "9px", "fontWeight": "700",
                                "color": "var(--neon)", "border": "1px solid var(--neon)",
                                "borderRadius": "4px", "padding": "2px 6px",
                            }),
                        ],
                    ),
                    *pattern_items,
                    *([html.P(narrative,
                              style={"fontSize": "11px", "color": "var(--muted)",
                                     "borderTop": "1px solid var(--border)",
                                     "paddingTop": "8px", "marginTop": "4px",
                                     "lineHeight": "1.5"})]
                      if narrative else []),
                ],
            )

        # ---------- ECG clásico: cargar archivos por deportista ----------
        @app.callback(
            Output("cmp-ecg-table", "data"),
            Input("cmp-user", "value"),
            Input("cmp-ecg-refresh", "data"),
            prevent_initial_call=True,
        )
        def load_ecg_files_table(user_id, _refresh_token):
            uid = _safe_int(user_id)
            if not uid or not _can_access_athlete(db, uid):
                return []

            files = db.list_ecg_files(int(uid)) or []
            metrics_by_file = {}
            if hasattr(db, "list_latest_ecg_metrics_for_files"):
                try:
                    metrics_by_file = db.list_latest_ecg_metrics_for_files(
                        [f.get("id") for f in files if f.get("id") is not None]
                    )
                except Exception:
                    metrics_by_file = {}
            rows = []

            for f in files:
                fname = f.get("filename")
                if not fname:
                    continue
                path = _ecg_data_path(fname)
                if not path:
                    continue

                saved_row = _row_from_saved_ecg_metrics(
                    f, "", metrics_by_file.get(_safe_int(f.get("id")))
                )
                if saved_row:
                    saved_row["id"] = f.get("id")
                    saved_row.pop("label", None)
                    rows.append(saved_row)
                    continue

                try:
                    fs0 = int(f.get("fs") or 250)
                except Exception:
                    fs0 = 250

                try:
                    t, x, fs = read_ecg_csv(path, fs_default=fs0)
                except Exception:
                    continue

                if x is None or len(x) < 5:
                    continue

                try:
                    xs = smooth(x, win_ms=40, fs=fs)
                    peaks = detect_r_peaks(xs, fs, sens=0.6)
                    bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
                    if hasattr(db, "save_ecg_metrics_latest"):
                        db.save_ecg_metrics_latest(
                            int(f.get("id")), bpm, sdnn, rmssd, int(len(peaks))
                        )
                except Exception:
                    bpm, sdnn, rmssd = 0.0, 0.0, 0.0
                    peaks = np.array([], dtype=int)

                duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                n_beats = int(len(peaks))

                rows.append(
                    {
                        "id": f.get("id"),
                        "filename": fname,
                        "duration_s": round(duration_s, 1),
                        "n_beats": n_beats,
                        "bpm": int(round(bpm)) if bpm > 0 else 0,
                        "sdnn_ms": int(round(sdnn)) if sdnn > 0 else 0,
                        "rmssd_ms": int(round(rmssd)) if rmssd > 0 else 0,
                    }
                )

            return rows

        @app.callback(
            Output("cmp-ecg-delete-confirm", "displayed"),
            Output("cmp-ecg-delete-confirm", "message"),
            Input("cmp-ecg-delete-btn", "n_clicks"),
            State("cmp-ecg-table", "data"),
            State("cmp-ecg-table", "selected_rows"),
            prevent_initial_call=True,
        )
        def ask_delete_ecg_files(n_clicks, data, selected_rows):
            if not n_clicks:
                raise PreventUpdate
            data = data or []
            selected_rows = selected_rows or []
            selected = [data[i] for i in selected_rows if 0 <= i < len(data)]
            if not selected:
                return False, "Selecciona al menos un archivo ECG para eliminar."
            count = len(selected)
            label = "archivo" if count == 1 else "archivos"
            return True, f"¿Eliminar {count} {label} ECG seleccionado(s)? Esta acción borra el registro y el archivo físico."

        @app.callback(
            Output("cmp-ecg-refresh", "data"),
            Output("cmp-ecg-delete-status", "children"),
            Input("cmp-ecg-delete-confirm", "submit_n_clicks"),
            State("cmp-ecg-refresh", "data"),
            State("cmp-user", "value"),
            State("cmp-ecg-table", "data"),
            State("cmp-ecg-table", "selected_rows"),
            prevent_initial_call=True,
        )
        def delete_selected_ecg_files(submit_n_clicks, refresh_token, user_id, data, selected_rows):
            if not submit_n_clicks:
                raise PreventUpdate
            if not hasattr(db, "delete_ecg_file"):
                return refresh_token or 0, "Tu base de datos aún no expone delete_ecg_file()."
            uid = _safe_int(user_id)
            if not uid or not _can_access_athlete(db, uid):
                return refresh_token or 0, "No tienes permisos para eliminar archivos de este deportista."
            try:
                allowed_file_ids = {
                    _safe_int(f.get("id"))
                    for f in (db.list_ecg_files(int(uid)) or [])
                    if _safe_int(f.get("id"))
                }
            except Exception:
                allowed_file_ids = set()

            data = data or []
            selected_rows = selected_rows or []
            selected = [data[i] for i in selected_rows if 0 <= i < len(data)]
            if not selected:
                return refresh_token or 0, "Selecciona al menos un archivo ECG para eliminar."

            deleted = 0
            failed = 0
            for row in selected:
                fid = _safe_int(row.get("id"))
                if not fid:
                    failed += 1
                    continue
                if fid not in allowed_file_ids:
                    failed += 1
                    continue
                try:
                    ok = db.delete_ecg_file(int(fid))
                    deleted += 1 if ok else 0
                    failed += 0 if ok else 1
                except Exception:
                    failed += 1

            if deleted and not failed:
                msg = f"Se eliminaron {deleted} archivo(s) ECG."
            elif deleted and failed:
                msg = f"Se eliminaron {deleted} archivo(s) ECG y {failed} no pudieron borrarse."
            else:
                msg = "No se pudo eliminar ningún archivo ECG."
            return (refresh_token or 0) + 1, msg

        # ---------- ECG clásico: gráfico de barras ----------
        @app.callback(
            Output("cmp-ecg-bars", "figure"),
            Input("cmp-ecg-table", "data"),
            Input("cmp-ecg-table", "selected_rows"),
            prevent_initial_call=True,
        )
        def ecg_compare_bars(data, selected_rows):
            if not data:
                return go.Figure()

            if not selected_rows:
                selected_rows = list(range(min(4, len(data))))
            selected_rows = selected_rows[:4]

            sel = [data[i] for i in selected_rows if 0 <= i < len(data)]
            if not sel:
                return go.Figure()

            labels = [row.get("filename", "sesión") for row in sel]
            bpm = [row.get("bpm", 0) for row in sel]
            sdnn = [row.get("sdnn_ms", 0) for row in sel]
            rmssd = [row.get("rmssd_ms", 0) for row in sel]

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=bpm, name="BPM"))
            fig.add_trace(go.Bar(x=labels, y=sdnn, name="SDNN (ms)"))
            fig.add_trace(go.Bar(x=labels, y=rmssd, name="RMSSD (ms)"))

            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa HRV entre archivos", x_title="Archivo", y_title="Valor", height=420)
            return fig

        # ---------- Helper multi-upload ----------
        def _decode_multiple_uploads(contents, filenames, subfolder):
            if not contents:
                return []

            if not isinstance(contents, list):
                contents = [contents]
            if not isinstance(filenames, list):
                filenames = [filenames]

            n = min(len(contents), len(filenames)) if filenames else len(contents)
            n = min(n, 6)
            if n <= 0:
                return []

            dirpath = os.path.join("data", subfolder)
            os.makedirs(dirpath, exist_ok=True)

            result = []
            for idx in range(n):
                c = contents[idx]
                fn = filenames[idx] if idx < len(filenames) else f"session_{idx}.csv"
                if not c:
                    continue
                if isinstance(c, str) and len(c) > _MAX_CSV_UPLOAD_B64_CHARS:
                    continue

                try:
                    data = _b64_to_bytes(c)
                except Exception:
                    continue

                safe_label = _sanitize_filename(fn or f"session_{idx}.csv")
                try:
                    saved_name = _save_unique(dirpath, safe_label, data, prefix="cmp_")
                except Exception:
                    continue

                path = os.path.join(dirpath, saved_name)
                session_name = safe_label
                result.append((session_name, path))

            return result

        # ---------- IMU: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-imu-table", "data"),
            Output("cmp-imu-bars", "figure"),
            Input("cmp-imu-upload", "contents"),
            State("cmp-imu-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_imu(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="imu_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            hits_per_min_list = []
            mean_int_list = []

            for session_name, path in sessions:
                try:
                    t, mag, fs = read_imu_csv(path)
                except Exception:
                    continue
                finally:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                if mag is None or len(mag) == 0:
                    continue

                n_hits, hits_per_min, mean_int, max_int, _ = imu_metrics_from_mag(mag, t, fs)

                row = {
                    "session": session_name,
                    "n_hits": int(n_hits),
                    "hits_per_min": round(float(hits_per_min), 1),
                    "mean_int_g": round(float(mean_int), 2),
                    "max_int_g": round(float(max_int), 2),
                }
                rows.append(row)

                labels.append(session_name)
                hits_per_min_list.append(row["hits_per_min"])
                mean_int_list.append(row["mean_int_g"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=hits_per_min_list, name="Golpes/min"))
            fig.add_trace(go.Bar(x=labels, y=mean_int_list, name="Intensidad media (g)"))
            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa IMU (golpes/min e intensidad)", x_title="Archivo", y_title="Valor", height=420)

            return rows, fig
