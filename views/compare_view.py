# views/compare_view.py

import os
import io
import base64
import uuid
import re
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, graph_config

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
    read_emg_csv,
    emg_metrics,
    read_resp_csv,
    resp_metrics,
)

_ALLOWED_EXTS = {".csv"}


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
        return base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e


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
    color = {
        "good": "#00f28a",
        "bad": "#ff6b6b",
        "neutral": "#e9eef6",
    }.get(kind, "#e9eef6")
    return html.Div(text, style={"color": color, "fontWeight": "bold", "marginTop": "6px"})


# Mapeo de aliases: en DB/UI a veces usamos códigos más específicos (IMU_GLOVE, EMG_ARM, etc.).
_SENSOR_ALIASES = {
    "ECG": {"ECG"},
    "IMU": {"IMU", "IMU_GLOVE", "IMU_HEAD", "IMU_WRIST"},
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

def _latest_by_session(db, kind: str, session_id: int):
    """
    kind in {"imu","emg","resp"}
    Devuelve la fila más reciente por session_id (si existe).
    """
    if not session_id:
        return None

    fn_map = {
        "imu": "list_imu_metrics_by_session",
        "emg": "list_emg_metrics_by_session",
        "resp": "list_resp_metrics_by_session",
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

    files = sorted(files, key=lambda r: _safe_int(r.get("id")) or 0, reverse=True)
    f = files[0]
    fname = f.get("filename")
    if not fname:
        return None

    path = os.path.join("data", "ecg", fname)
    if not os.path.exists(path):
        return None

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


def _overall_summary(ecg_b=None, imu_b=None, emg_b=None, resp_b=None):
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


def _recommendations(ecg_b=None, imu_b=None, emg_b=None, resp_b=None):
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

        role = (session.get("role") or "no autenticado")
        uid = session.get("user_id")

        # Qué deportistas puede ver
        if role == "coach" and uid:
            coach_sport = (session.get("sport") or "")
            if isinstance(coach_sport, bytes):
                coach_sport = coach_sport.decode("utf-8", "replace")
            coach_sport = coach_sport.strip() or None
            athletes = self.db.list_athletes_for_coach(int(uid), sport=coach_sport)
        elif role == "deportista" and uid:
            u = self.db.get_user_by_id(int(uid))
            athletes = [u] if u and u.get("role") == "deportista" else []
        else:
            athletes = [
                u for u in self.db.list_users()
                if (u.get("role", "deportista") == "deportista")
            ]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]
        default_user = options_users[0]["value"] if options_users else None

        # Selector de deportista
        if role == "deportista":
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="cmp-user",
                    options=options_users,
                    value=default_user,
                    disabled=True,
                )
            ], className="filter-item")
        else:
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="cmp-user",
                    options=options_users,
                    value=default_user,
                    placeholder="Selecciona deportista..."
                )
            ], className="filter-item")

        # Selector de sesión (comparación seleccionada vs anterior)
        pdf_note = None
        if not _REPORTLAB_OK:
            pdf_note = html.Div(
                f"La exportación PDF no está activa todavía. Instala reportlab en tu entorno virtual "
                f"(python -m pip install reportlab). Detalle: {_REPORTLAB_ERR}",
                className="text-danger compare-report-msg",
                style={"marginTop": "8px"},
            )

        session_selector = html.Div(
            className="filter-item",
            children=[
                html.Label("Sesión seleccionada (se compara vs la anterior)"),
                dcc.Dropdown(
                    id="cmp-session",
                    options=[],
                    placeholder="Selecciona una sesión...",
                    clearable=True,
                ),
                html.Div(id="cmp-prev-label", className="muted", style={"marginTop": "6px", "opacity": 0.85}),
                html.Div("Incluye resumen, tabla comparativa y gráficas principales de la sesión.", className="text-muted compare-report-note", style={"marginTop": "10px"}),
                html.Div(className="compare-report-row", style={"marginTop": "10px"}, children=[
                    html.Button(
                        "Descargar informe (PDF)",
                        id="btn-cmp-report",
                        className="btn btn-primary",
                        disabled=(not _REPORTLAB_OK),
                    ),
                    html.Span(id="cmp-report-msg", className="compare-report-msg"),
                    dcc.Download(id="cmp-report-dl"),
                ]),
                pdf_note,
                dcc.Store(id="cmp-session-ids", data={"cur": None, "prev": None}),
            ],
        )

        # ----------- styles ----------
        def _sess_table_style():
            return dict(
                sort_action="native",
                page_size=10,
                fixed_rows={"headers": True},
                style_table={"overflowX": "auto", "overflowY": "auto", "maxHeight": "360px"},
                style_cell={
                    "backgroundColor": "#0f131a",
                    "color": "#e9eef6",
                    "border": "1px solid #232a36",
                    "padding": "8px",
                    "fontSize": "13px",
                    "whiteSpace": "nowrap",
                    "textOverflow": "ellipsis",
                    "maxWidth": "280px",
                },
                style_header={
                    "backgroundColor": "#151a21",
                    "fontWeight": "bold",
                    "border": "1px solid #232a36",
                },
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
                        dcc.Graph(id="cmp-ecg-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
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
                        dcc.Graph(id="cmp-imu-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                    ]),
                ]),

                html.Div(style={"display": "none"}, children=[
                html.H4("EMG (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-emg-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "RMS", "id": "rms"},
                        {"name": "Pico", "id": "peak"},
                        {"name": "Fatiga (%)", "id": "fatigue"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-emg-sess-badge"),
                dcc.Graph(id="cmp-emg-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                html.Hr(style={"marginTop": "22px"}),
                ]),

                html.Div(style={"display": "none"}, children=[
                html.H4("Respiración (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-resp-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Respiraciones", "id": "n_breaths"},
                        {"name": "Resp/min", "id": "br_min"},
                        {"name": "Periodo medio (s)", "id": "mean_period"},
                        {"name": "Índice estrés", "id": "stress_index"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-resp-sess-badge"),
                dcc.Graph(id="cmp-resp-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                ]),
            ],
        )

        # ---------- BLOQUE ECG clásico ----------
        ecg_block = html.Div(
            className="compare-advanced-tool",
            style={"marginTop": "0"},
            children=[
                html.H3("Comparación avanzada por archivos · ECG / HRV"),
                html.Small(
                    "Úsalo como apoyo técnico cuando quieras revisar archivos ECG concretos fuera del flujo principal por sesión.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
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
                            style={
                                "background": "#3a1620",
                                "color": "#ffe5ea",
                                "border": "1px solid #6b2434",
                                "borderRadius": "10px",
                                "padding": "10px 14px",
                                "cursor": "pointer",
                                "fontWeight": "700",
                            },
                        ),
                        html.Div(id="cmp-ecg-delete-status", className="muted", style={"fontSize": "13px", "opacity": 0.9}),
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
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                        "textOverflow": "ellipsis",
                        "maxWidth": "280px",
                    },
                    style_header={
                        "backgroundColor": "#151a21",
                        "fontWeight": "bold",
                        "border": "1px solid #232a36",
                    },
                ),
                ),
                html.Div(
                    "Selecciona 2–4 filas para comparar (si seleccionas más, se toman las primeras 4).",
                    className="muted",
                    style={"marginTop": "6px", "fontSize": "13px", "opacity": 0.8},
                ),
                html.Br(),
                dcc.Graph(id="cmp-ecg-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        # ---------- BLOQUES multi-upload ----------
        imu_block = html.Div(
            className="compare-advanced-tool",
            style={"marginTop": "0"},
            children=[
                html.H3("Herramienta avanzada · IMU por archivos"),
                html.Small(
                    "Herramienta avanzada para contrastar archivos IMU concretos cuando necesites una revisión técnica adicional.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-imu-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos IMU (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
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
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-imu-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        emg_block = html.Div(
            style={"marginTop": "24px"},
            children=[
                html.H3("Herramienta avanzada · EMG por archivos"),
                html.Small(
                    "Herramienta avanzada para comparar archivos EMG en contextos controlados o de laboratorio.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-emg-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos EMG (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-emg-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "RMS global", "id": "rms"},
                        {"name": "Pico abs", "id": "peak"},
                        {"name": "Fatiga (%)", "id": "fatigue"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-emg-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        resp_block = html.Div(
            style={"marginTop": "24px", "marginBottom": "40px"},
            children=[
                html.H3("Herramienta avanzada · Respiración por archivos"),
                html.Small(
                    "Herramienta avanzada para comparar archivos de respiración cuando quieras una revisión específica fuera del flujo principal.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-resp-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos de respiración (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-resp-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "Respiraciones", "id": "n_breaths"},
                        {"name": "Resp/min", "id": "br_min"},
                        {"name": "Periodo medio (s)", "id": "mean_period"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-resp-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        advanced_block = html.Details(
            open=False,
            className="collapsible-card compare-advanced",
            style={"marginTop": "28px"},
            children=[
                html.Summary(className="collapsible-card__summary", children=[
                    html.Div(className="collapsible-card__head", children=[
                        html.Div("Opciones avanzadas", className="card-title"),
                        html.Div(
                            "Úsalas solo cuando quieras bajar a comparación por archivos concretos de ECG o IMU.",
                            className="text-muted",
                        ),
                    ]),
                    html.Span("⌄", className="collapsible-card__chevron"),
                ]),
                html.Div(
                    className="collapsible-card__body",
                    children=[
                        html.Div(
                            className="inner-card",
                            style={"padding": "18px", "borderRadius": "14px"},
                            children=[
                                html.Small("Estas comparativas quedan como apoyo técnico opcional. La lectura principal del producto está arriba, por sesión.", style={"opacity": 0.8}),
                                html.Div(className="compare-advanced-grid", children=[
                                    html.Div(className="compare-advanced-item", children=[ecg_block]),
                                    html.Div(className="compare-advanced-item", children=[imu_block]),
                                ]),
                            ],
                        ),
                    ],
                ),
            ],
        )

        hidden_lab_blocks = html.Div(style={"display": "none"}, children=[emg_block, resp_block])

        return html.Div(
            [
            html.Div(className="page-head", children=[
                html.H2("Histórico y comparación"),
                html.P(
                    "Revisa cómo vienen cambiando las sesiones y baja al detalle técnico solo cuando de verdad haga falta.",
                    className="text-muted",
                ),
            ]),
                html.Div(className="card", children=[
                    html.Div(className="filters-bar filters-bar--2", children=[
                        user_selector,
                        session_selector,
                    ]),
                ]),
                session_compare_block,
                advanced_block,
                hidden_lab_blocks,
            ]
        )

    # ====================== CALLBACKS ======================

    def _register_callbacks(self):
        app = self.app
        db = self.db

        # ---------- Session selector: options + prev ----------
        @app.callback(
            Output("cmp-session", "options"),
            Output("cmp-session", "value"),
            Output("cmp-session-ids", "data"),
            Output("cmp-prev-label", "children"),
            Input("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def load_sessions_for_user(user_id):
            uid = _safe_int(user_id)
            if not uid:
                return [], None, {"cur": None, "prev": None}, "Selecciona un deportista."

            if not hasattr(db, "list_sessions"):
                return [], None, {"cur": None, "prev": None}, "Tu DB todavía no expone sesiones (list_sessions)."

            try:
                sessions = db.list_sessions(int(uid), limit=50) or []
            except Exception:
                sessions = []

            # ordenar: más reciente primero (ts_start si existe, si no id)
            def _k(s):
                ts = (s.get("ts_start") or "")
                sid = _safe_int(s.get("id")) or 0
                return (ts, sid)

            sessions = sorted(sessions, key=_k, reverse=True)

            opts = []
            for s in sessions:
                sid = s.get("id")
                if sid is None:
                    continue
                ts = (s.get("ts_start") or "")[:19].replace("T", " ")
                st = (s.get("status") or "—")
                opts.append({"label": f"#{sid} · {ts} · {st}", "value": sid})

            chosen = opts[0]["value"] if opts else None

            prev_id = None
            prev_label = "Sesión anterior: —"

            if chosen:
                # 1) si existe método dedicado
                if hasattr(db, "get_previous_session"):
                    try:
                        ps = db.get_previous_session(int(uid), int(chosen))
                    except Exception:
                        ps = None
                    prev_id = ps.get("id") if ps else None
                    prev_label = f"Sesión anterior: {_session_label(ps) if ps else '—'}"
                else:
                    # 2) fallback: buscamos en la lista ordenada
                    ids = [(_safe_int(s.get("id")) or 0) for s in sessions]
                    try:
                        idx = ids.index(int(chosen))
                        if idx + 1 < len(sessions):
                            ps = sessions[idx + 1]
                            prev_id = _safe_int(ps.get("id"))
                            prev_label = f"Sesión anterior: {_session_label(ps) if ps else '—'}"
                    except Exception:
                        prev_id = None
                        prev_label = "Sesión anterior: —"

            data = {"cur": chosen, "prev": prev_id}
            return opts, chosen, data, prev_label

        # ---------- ECG (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-ecg-sess-table", "data"),
            Output("cmp-ecg-sess-bars", "figure"),
            Output("cmp-ecg-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def ecg_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "ECG"):
                return [], go.Figure(), _badge("Este deportista aún no tiene activada la lectura cardiovascular.", "neutral")

            cur = _ecg_row_for_session(db, uid, cur_id, "Esta sesión")
            prev = _ecg_row_for_session(db, uid, prev_id, "Sesión anterior") if prev_id else None

            rows = []
            if cur:
                rows.append(cur)
            if prev:
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Esta sesión", "Sesión anterior"]
                fig = _compare_bar_fig(
                    "Recuperación cardiovascular: esta sesión vs la anterior",
                    x,
                    series=[
                        {"name": "Ritmo cardíaco", "y": [cur["bpm"], prev["bpm"]]},
                        {"name": "Variabilidad", "y": [cur["sdnn_ms"], prev["sdnn_ms"]]},
                        {"name": "Recuperación", "y": [cur["rmssd_ms"], prev["rmssd_ms"]]},
                    ],
                    y_title="Valor",
                )
                badge = _ecg_recovery_badge(cur, prev)
            else:
                apply_chart_style(fig, title="Recuperación cardiovascular: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("Aún no hay suficientes datos cardiovasculares guardados en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- IMU (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-imu-sess-table", "data"),
            Output("cmp-imu-sess-bars", "figure"),
            Output("cmp-imu-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def imu_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "IMU"):
                return [], go.Figure(), _badge("Este deportista aún no tiene activada la lectura de movimiento.", "neutral")

            cur_row = _latest_by_session(db, "imu", cur_id)
            prev_row = _latest_by_session(db, "imu", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                hpm = float(cur_row.get("hits_per_min", 0) or 0)
                mi = float(cur_row.get("mean_int_g", 0) or 0)
                cur = {
                    "label": "Esta sesión",
                    "filename": cur_row.get("filename", "—"),
                    "n_hits": int(cur_row.get("n_hits", 0) or 0),
                    "hits_per_min": round(hpm, 1),
                    "mean_int_g": round(mi, 2),
                    "max_int_g": round(float(cur_row.get("max_int_g", 0) or 0), 2),
                    "load_index": round(hpm * mi, 2),
                }
                rows.append(cur)

            if prev_row:
                hpm = float(prev_row.get("hits_per_min", 0) or 0)
                mi = float(prev_row.get("mean_int_g", 0) or 0)
                prev = {
                    "label": "Sesión anterior",
                    "filename": prev_row.get("filename", "—"),
                    "n_hits": int(prev_row.get("n_hits", 0) or 0),
                    "hits_per_min": round(hpm, 1),
                    "mean_int_g": round(mi, 2),
                    "max_int_g": round(float(prev_row.get("max_int_g", 0) or 0), 2),
                    "load_index": round(hpm * mi, 2),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Esta sesión", "Sesión anterior"]
                fig = _compare_bar_fig(
                    "Ritmo e impacto: esta sesión vs la anterior",
                    x,
                    series=[
                        {"name": "Ritmo de acción", "y": [cur["hits_per_min"], prev["hits_per_min"]]},
                        {"name": "Explosividad media", "y": [cur["mean_int_g"], prev["mean_int_g"]]},
                        {"name": "Carga de la sesión", "y": [cur["load_index"], prev["load_index"]]},
                    ],
                    y_title="Valor",
                )
                badge = _load_badge(cur["load_index"], prev["load_index"], label="Carga de la sesión")
            else:
                apply_chart_style(fig, title="Ritmo e impacto: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("Aún no hay suficientes datos de movimiento en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- EMG (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-emg-sess-table", "data"),
            Output("cmp-emg-sess-bars", "figure"),
            Output("cmp-emg-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def emg_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "EMG"):
                return [], go.Figure(), _badge("EMG no asignado a este deportista.", "neutral")

            cur_row = _latest_by_session(db, "emg", cur_id)
            prev_row = _latest_by_session(db, "emg", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                cur = {
                    "label": "Seleccionada",
                    "filename": cur_row.get("filename", "—"),
                    "rms": round(float(cur_row.get("rms", 0) or 0), 3),
                    "peak": round(float(cur_row.get("peak", 0) or 0), 3),
                    "fatigue": round(float(cur_row.get("fatigue", 0) or 0), 1),
                }
                rows.append(cur)

            if prev_row:
                prev = {
                    "label": "Anterior",
                    "filename": prev_row.get("filename", "—"),
                    "rms": round(float(prev_row.get("rms", 0) or 0), 3),
                    "peak": round(float(prev_row.get("peak", 0) or 0), 3),
                    "fatigue": round(float(prev_row.get("fatigue", 0) or 0), 1),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "EMG: activación y fatiga (seleccionada vs anterior)",
                    x,
                    series=[
                        {"name": "RMS", "y": [cur["rms"], prev["rms"]]},
                        {"name": "Fatiga (%)", "y": [cur["fatigue"], prev["fatigue"]]},
                    ],
                    y_title="Valor",
                )
                badge = _fatigue_badge(cur["fatigue"], prev["fatigue"])
            else:
                apply_chart_style(fig, title="EMG: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay métricas EMG guardadas en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- RESP (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-resp-sess-table", "data"),
            Output("cmp-resp-sess-bars", "figure"),
            Output("cmp-resp-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def resp_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "RESP_BELT"):
                return [], go.Figure(), _badge("Respiración no asignada a este deportista.", "neutral")

            cur_row = _latest_by_session(db, "resp", cur_id)
            prev_row = _latest_by_session(db, "resp", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                br = float(cur_row.get("br_min", 0) or 0)
                cur = {
                    "label": "Seleccionada",
                    "filename": cur_row.get("filename", "—"),
                    "n_breaths": int(cur_row.get("n_breaths", 0) or 0),
                    "br_min": round(br, 1),
                    "mean_period": round(float(cur_row.get("mean_period", 0) or 0), 2),
                    "stress_index": round(br, 1),  # proxy simple
                }
                rows.append(cur)

            if prev_row:
                br = float(prev_row.get("br_min", 0) or 0)
                prev = {
                    "label": "Anterior",
                    "filename": prev_row.get("filename", "—"),
                    "n_breaths": int(prev_row.get("n_breaths", 0) or 0),
                    "br_min": round(br, 1),
                    "mean_period": round(float(prev_row.get("mean_period", 0) or 0), 2),
                    "stress_index": round(br, 1),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "Respiración: seleccionada vs anterior",
                    x,
                    series=[
                        {"name": "Resp/min", "y": [cur["br_min"], prev["br_min"]]},
                        {"name": "Periodo medio (s)", "y": [cur["mean_period"], prev["mean_period"]]},
                    ],
                    y_title="Valor",
                )
                badge = _resp_badge(cur["br_min"], prev["br_min"])
            else:
                apply_chart_style(fig, title="Respiración: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay métricas de respiración guardadas en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- Resumen + recomendaciones en UI ----------
        @app.callback(
            Output("cmp-overall", "children"),
            Output("cmp-recs", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def session_overall_ui(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store or not store.get("cur"):
                return "", []

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))

            # Re-armamos badges con la misma lógica
            ecg_b = imu_b = emg_b = resp_b = None

            if _has_sensor(db, uid, "ECG"):
                ecg_cur = _ecg_row_for_session(db, uid, cur_id, "Esta sesión")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Sesión anterior") if prev_id else None
                if ecg_cur and ecg_prev:
                    ecg_b = _ecg_recovery_badge(ecg_cur, ecg_prev)

            if _has_sensor(db, uid, "IMU"):
                r = _latest_by_session(db, "imu", cur_id)
                p = _latest_by_session(db, "imu", prev_id) if prev_id else None
                if r and p:
                    hpm_r = float(r.get("hits_per_min", 0) or 0)
                    mi_r = float(r.get("mean_int_g", 0) or 0)
                    hpm_p = float(p.get("hits_per_min", 0) or 0)
                    mi_p = float(p.get("mean_int_g", 0) or 0)
                    imu_b = _load_badge(hpm_r * mi_r, hpm_p * mi_p, label="Carga de la sesión")

            if _has_sensor(db, uid, "EMG"):
                r = _latest_by_session(db, "emg", cur_id)
                p = _latest_by_session(db, "emg", prev_id) if prev_id else None
                if r and p:
                    emg_b = _fatigue_badge(float(r.get("fatigue", 0) or 0), float(p.get("fatigue", 0) or 0))

            if _has_sensor(db, uid, "RESP_BELT"):
                r = _latest_by_session(db, "resp", cur_id)
                p = _latest_by_session(db, "resp", prev_id) if prev_id else None
                if r and p:
                    resp_b = _resp_badge(float(r.get("br_min", 0) or 0), float(p.get("br_min", 0) or 0))

            overall = _overall_summary(ecg_b, imu_b, emg_b, resp_b)
            recs = _recommendations(ecg_b, imu_b, emg_b, resp_b)

            return overall, [html.Li(r) for r in recs]

        # ---------- Reporte PDF con imágenes ----------
        @app.callback(
            Output("cmp-report-dl", "data"),
            Output("cmp-report-msg", "children"),
            Input("btn-cmp-report", "n_clicks"),
            State("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=True,
        )
        def download_report(n, store, user_id):
            if not n:
                raise PreventUpdate

            if not _REPORTLAB_OK:
                # No tronamos la app: devolvemos un txt con instrucciones
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            uid = _safe_int(user_id)
            if not uid or not store or not store.get("cur"):
                return dash.no_update, "Selecciona una sesión para generar el informe."

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))

            athlete = None
            try:
                athlete = db.get_user_by_id(int(uid))
            except Exception:
                athlete = None

            # Recalcular datos como en pantalla
            ecg_cur = ecg_prev = None
            imu_cur = imu_prev = None
            emg_cur = emg_prev = None
            resp_cur = resp_prev = None

            ecg_badge = imu_badge = emg_badge = resp_badge = None

            if _has_sensor(db, uid, "ECG"):
                ecg_cur = _ecg_row_for_session(db, uid, cur_id, "Esta sesión")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Sesión anterior") if prev_id else None
                if ecg_cur and ecg_prev:
                    ecg_badge = _ecg_recovery_badge(ecg_cur, ecg_prev)

            if _has_sensor(db, uid, "IMU"):
                r = _latest_by_session(db, "imu", cur_id)
                p = _latest_by_session(db, "imu", prev_id) if prev_id else None
                if r:
                    hpm = float(r.get("hits_per_min", 0) or 0)
                    mi = float(r.get("mean_int_g", 0) or 0)
                    imu_cur = {
                        "hits_per_min": hpm,
                        "mean_int_g": mi,
                        "load_index": hpm * mi,
                        "n_hits": int(r.get("n_hits", 0) or 0),
                        "max_int_g": float(r.get("max_int_g", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    hpm = float(p.get("hits_per_min", 0) or 0)
                    mi = float(p.get("mean_int_g", 0) or 0)
                    imu_prev = {
                        "hits_per_min": hpm,
                        "mean_int_g": mi,
                        "load_index": hpm * mi,
                        "n_hits": int(p.get("n_hits", 0) or 0),
                        "max_int_g": float(p.get("max_int_g", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if imu_cur and imu_prev:
                    imu_badge = _load_badge(imu_cur["load_index"], imu_prev["load_index"], label="Carga de la sesión")

            if _has_sensor(db, uid, "EMG"):
                r = _latest_by_session(db, "emg", cur_id)
                p = _latest_by_session(db, "emg", prev_id) if prev_id else None
                if r:
                    emg_cur = {
                        "rms": float(r.get("rms", 0) or 0),
                        "peak": float(r.get("peak", 0) or 0),
                        "fatigue": float(r.get("fatigue", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    emg_prev = {
                        "rms": float(p.get("rms", 0) or 0),
                        "peak": float(p.get("peak", 0) or 0),
                        "fatigue": float(p.get("fatigue", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if emg_cur and emg_prev:
                    emg_badge = _fatigue_badge(emg_cur["fatigue"], emg_prev["fatigue"])

            if _has_sensor(db, uid, "RESP_BELT"):
                r = _latest_by_session(db, "resp", cur_id)
                p = _latest_by_session(db, "resp", prev_id) if prev_id else None
                if r:
                    br = float(r.get("br_min", 0) or 0)
                    resp_cur = {
                        "br_min": br,
                        "mean_period": float(r.get("mean_period", 0) or 0),
                        "n_breaths": int(r.get("n_breaths", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    br = float(p.get("br_min", 0) or 0)
                    resp_prev = {
                        "br_min": br,
                        "mean_period": float(p.get("mean_period", 0) or 0),
                        "n_breaths": int(p.get("n_breaths", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if resp_cur and resp_prev:
                    resp_badge = _resp_badge(resp_cur["br_min"], resp_prev["br_min"])

            overall = _overall_summary(ecg_badge, imu_badge, emg_badge, resp_badge)
            recs = _recommendations(ecg_badge, imu_badge, emg_badge, resp_badge)

            # ---- Figuras para PDF
            figs = []
            if ecg_cur and ecg_prev:
                fig = go.Figure()
                x = ["Esta sesión", "Sesión anterior"]
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["bpm"], ecg_prev["bpm"]], name="Ritmo cardíaco"))
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["sdnn_ms"], ecg_prev["sdnn_ms"]], name="Variabilidad"))
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["rmssd_ms"], ecg_prev["rmssd_ms"]], name="Recuperación"))
                fig.update_layout(barmode="group")
                _apply_pdf_chart_style(fig, "Recuperación cardiovascular", y_title="Valor")
                figs.append(("Recuperación cardiovascular", fig))

            if imu_cur and imu_prev:
                fig = go.Figure()
                x = ["Esta sesión", "Sesión anterior"]
                fig.add_trace(go.Bar(x=x, y=[imu_cur["hits_per_min"], imu_prev["hits_per_min"]], name="Ritmo de acción"))
                fig.add_trace(go.Bar(x=x, y=[imu_cur["mean_int_g"], imu_prev["mean_int_g"]], name="Explosividad media"))
                fig.add_trace(go.Bar(x=x, y=[imu_cur["load_index"], imu_prev["load_index"]], name="Carga de la sesión"))
                fig.update_layout(barmode="group")
                _apply_pdf_chart_style(fig, "Ritmo e impacto", y_title="Valor")
                figs.append(("Ritmo e impacto", fig))

            if emg_cur and emg_prev:
                fig = go.Figure()
                x = ["Esta sesión", "Sesión anterior"]
                fig.add_trace(go.Bar(x=x, y=[emg_cur["rms"], emg_prev["rms"]], name="RMS"))
                fig.add_trace(go.Bar(x=x, y=[emg_cur["fatigue"], emg_prev["fatigue"]], name="Fatiga (%)"))
                fig.update_layout(barmode="group")
                _apply_pdf_chart_style(fig, "EMG", y_title="Valor")
                figs.append(("EMG", fig))

            if resp_cur and resp_prev:
                fig = go.Figure()
                x = ["Esta sesión", "Sesión anterior"]
                fig.add_trace(go.Bar(x=x, y=[resp_cur["br_min"], resp_prev["br_min"]], name="Ritmo respiratorio"))
                fig.add_trace(go.Bar(x=x, y=[resp_cur["mean_period"], resp_prev["mean_period"]], name="Periodo (s)"))
                fig.update_layout(barmode="group")
                _apply_pdf_chart_style(fig, "Respiración", y_title="Valor")
                figs.append(("Respiración", fig))

            headers = ["Métrica", "Esta sesión", "Sesión anterior", "Δ", "%"]
            col_widths = [6.0 * cm, 3.0 * cm, 3.0 * cm, 2.2 * cm, 2.0 * cm]

            def _row_metric(label, cur, prev, unit="", decimals=2):
                if cur is None or prev is None:
                    return [label, "—", "—", "—", "—"]
                d, pct = _delta_and_pct(cur, prev)
                cur_s = f"{float(cur):.{decimals}f}{unit}"
                prev_s = f"{float(prev):.{decimals}f}{unit}"
                d_s = f"{d:+.{decimals}f}{unit}" if d is not None else "—"
                pct_s = _fmt_pct(pct)
                return [label, cur_s, prev_s, d_s, pct_s]

            table_rows = []

            if ecg_cur and ecg_prev:
                table_rows += [
                    _row_metric("Ritmo cardíaco", ecg_cur.get("bpm"), ecg_prev.get("bpm"), unit="", decimals=0),
                    _row_metric("Variabilidad", ecg_cur.get("sdnn_ms"), ecg_prev.get("sdnn_ms"), unit=" ms", decimals=0),
                    _row_metric("Recuperación", ecg_cur.get("rmssd_ms"), ecg_prev.get("rmssd_ms"), unit=" ms", decimals=0),
                ]
            if imu_cur and imu_prev:
                table_rows += [
                    _row_metric("Ritmo de acción", imu_cur["hits_per_min"], imu_prev["hits_per_min"], unit="", decimals=1),
                    _row_metric("Explosividad media", imu_cur["mean_int_g"], imu_prev["mean_int_g"], unit=" g", decimals=2),
                    _row_metric("Carga de la sesión", imu_cur["load_index"], imu_prev["load_index"], unit="", decimals=2),
                ]
            if emg_cur and emg_prev:
                table_rows += [
                    _row_metric("EMG RMS", emg_cur["rms"], emg_prev["rms"], unit="", decimals=3),
                    _row_metric("EMG fatiga", emg_cur["fatigue"], emg_prev["fatigue"], unit=" %", decimals=1),
                ]
            if resp_cur and resp_prev:
                table_rows += [
                    _row_metric("Resp/min", resp_cur["br_min"], resp_prev["br_min"], unit="", decimals=1),
                    _row_metric("Periodo resp", resp_cur["mean_period"], resp_prev["mean_period"], unit=" s", decimals=2),
                ]

            availability_lines = []
            for label, cur_data, prev_data in [
                ("Recuperación cardiovascular", ecg_cur, ecg_prev),
                ("Ritmo e impacto", imu_cur, imu_prev),
                ("Activación muscular", emg_cur, emg_prev),
                ("Respiración", resp_cur, resp_prev),
            ]:
                if cur_data and prev_data:
                    availability_lines.append(f"{label}: comparación disponible entre la sesión seleccionada y la anterior.")
                elif cur_data or prev_data:
                    availability_lines.append(f"{label}: hay datos parciales, pero todavía no suficientes para comparar.")
            if not availability_lines:
                availability_lines.append("Todavía no hay métricas comparables guardadas por sesión para construir una lectura completa.")

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=A4)
            width, height = A4
            x0 = 1.7 * cm
            usable_w = width - (2 * x0)
            y = height - 1.7 * cm
            page_num = 1

            BRAND = colors.HexColor("#0E1522")
            ACCENT = colors.HexColor("#0891B2")
            TEXT = colors.HexColor("#0E1522")
            MUTED = colors.HexColor("#475569")
            BORDER = colors.HexColor("#D6DEE8")
            SURFACE = colors.HexColor("#F8FAFC")
            SURFACE_ALT = colors.HexColor("#EEF6FB")
            WHITE = colors.white

            def draw_footer():
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 8)
                c.drawString(x0, 1.2 * cm, "CombatIQ")
                c.drawRightString(width - x0, 1.2 * cm, f"Página {page_num}")
                c.setStrokeColor(BORDER)
                c.setLineWidth(1)
                c.line(x0, 1.7 * cm, width - x0, 1.7 * cm)

            def page_break(ypos, needed=0):
                nonlocal page_num
                if ypos - needed < 2.6 * cm:
                    draw_footer()
                    c.showPage()
                    page_num += 1
                    return height - 1.7 * cm
                return ypos

            def draw_wrapped_text(text, ypos, *, font="Helvetica", size=10, leading=14, color=TEXT, x=None, max_width=None):
                x = x0 if x is None else x
                max_width = usable_w if max_width is None else max_width
                c.setFillColor(color)
                c.setFont(font, size)
                wrapped = simpleSplit(_report_text(text), font, size, max_width)
                for line in wrapped:
                    c.drawString(x, ypos, line)
                    ypos -= leading
                    ypos = page_break(ypos)
                    c.setFillColor(color)
                    c.setFont(font, size)
                return ypos

            def draw_card(ypos, title, body_lines, *, subtitle=None, fill=SURFACE, accent=ACCENT):
                body_lines = [line for line in (body_lines or []) if line]
                text_w = usable_w - 28
                title_lines = simpleSplit(_report_text(title), "Helvetica-Bold", 12, text_w)
                subtitle_lines = simpleSplit(_report_text(subtitle), "Helvetica", 9, text_w) if subtitle else []
                body_wrapped = []
                for line in body_lines:
                    body_wrapped.extend(simpleSplit(_report_text(line), "Helvetica", 10, text_w))

                card_h = 18 + len(title_lines) * 14
                if subtitle_lines:
                    card_h += len(subtitle_lines) * 11 + 4
                if body_wrapped:
                    card_h += 6 + len(body_wrapped) * 13
                card_h += 14

                ypos = page_break(ypos, card_h + 10)
                bottom = ypos - card_h

                c.setFillColor(fill)
                c.roundRect(x0, bottom, usable_w, card_h, 12, fill=1, stroke=0)
                c.setStrokeColor(BORDER)
                c.setLineWidth(1)
                c.roundRect(x0, bottom, usable_w, card_h, 12, fill=0, stroke=1)
                c.setFillColor(accent)
                c.rect(x0, bottom, 5, card_h, fill=1, stroke=0)

                cursor = ypos - 18
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 12)
                for line in title_lines:
                    c.drawString(x0 + 14, cursor, line)
                    cursor -= 14

                if subtitle_lines:
                    c.setFillColor(MUTED)
                    c.setFont("Helvetica", 9)
                    for line in subtitle_lines:
                        c.drawString(x0 + 14, cursor, line)
                        cursor -= 11
                    cursor -= 4

                if body_wrapped:
                    c.setFillColor(TEXT)
                    c.setFont("Helvetica", 10)
                    for line in body_wrapped:
                        c.drawString(x0 + 14, cursor, line)
                        cursor -= 13

                return bottom - 12

            def draw_section_title(ypos, title, subtitle=None):
                ypos = page_break(ypos, 28)
                c.setStrokeColor(ACCENT)
                c.setLineWidth(2)
                c.line(x0, ypos - 4, x0 + 1.2 * cm, ypos - 4)
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 13)
                c.drawString(x0 + 1.45 * cm, ypos, _report_text(title))
                ypos -= 18
                if subtitle:
                    ypos = draw_wrapped_text(subtitle, ypos, size=9, leading=12, color=MUTED)
                return ypos - 4

            def draw_table(ypos, headers, rows, col_widths):
                row_h = 18
                table_w = sum(col_widths)
                ypos = page_break(ypos, row_h * (len(rows) + 2))

                c.setFillColor(BRAND)
                c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=1, stroke=0)
                c.setFillColor(WHITE)
                c.setFont("Helvetica-Bold", 9)

                xx = x0
                for h, w in zip(headers, col_widths):
                    c.drawString(xx + 6, ypos - 12, str(h))
                    xx += w

                c.setStrokeColor(BORDER)
                c.setLineWidth(1)
                c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=0, stroke=1)
                ypos -= row_h

                c.setFont("Helvetica", 9)
                for i, r in enumerate(rows):
                    if ypos - row_h < 2.6 * cm:
                        ypos = page_break(ypos, row_h * 2)
                        c.setFillColor(BRAND)
                        c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=1, stroke=0)
                        c.setFillColor(WHITE)
                        c.setFont("Helvetica-Bold", 9)
                        xx = x0
                        for h, w in zip(headers, col_widths):
                            c.drawString(xx + 6, ypos - 12, str(h))
                            xx += w
                        c.setStrokeColor(BORDER)
                        c.setLineWidth(1)
                        c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=0, stroke=1)
                        ypos -= row_h
                        c.setFont("Helvetica", 9)

                    c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
                    c.rect(x0, ypos - row_h, table_w, row_h, fill=1, stroke=0)
                    c.setFillColor(TEXT)
                    xx = x0
                    for cell, w in zip(r, col_widths):
                        txt = str(cell)
                        if len(txt) > 34:
                            txt = txt[:31] + "..."
                        c.drawString(xx + 6, ypos - 12, txt)
                        xx += w
                    c.setStrokeColor(BORDER)
                    c.setLineWidth(0.8)
                    c.line(x0, ypos - row_h, x0 + table_w, ypos - row_h)
                    ypos -= row_h

                return ypos - 12

            def fig_to_png_bytes(fig):
                try:
                    return fig.to_image(format="png", scale=2), None
                except Exception as e:
                    return None, str(e)

            def draw_plot_image(ypos, title, png_bytes, max_h_cm=8.4):
                ypos = page_break(ypos, 9.4 * cm)
                img = ImageReader(io.BytesIO(png_bytes))
                img_w, img_h = img.getSize()

                max_w = usable_w
                max_h = max_h_cm * cm
                scale = min(max_w / img_w, max_h / img_h)
                draw_w = img_w * scale
                draw_h = img_h * scale

                card_h = draw_h + 32
                bottom = ypos - card_h

                c.setFillColor(WHITE)
                c.roundRect(x0, bottom, usable_w, card_h, 12, fill=1, stroke=0)
                c.setStrokeColor(BORDER)
                c.setLineWidth(1)
                c.roundRect(x0, bottom, usable_w, card_h, 12, fill=0, stroke=1)
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 11)
                c.drawString(x0 + 12, ypos - 16, _report_text(title))
                c.drawImage(img, x0 + 12, bottom + 12, width=draw_w, height=draw_h, mask="auto")
                return bottom - 14

            name = (athlete or {}).get("name", "Deportista")
            sport = (athlete or {}).get("sport", "—")
            generated_at = datetime.utcnow().isoformat()[:19].replace("T", " ")
            overall_clean = _report_text(overall)
            recs_clean = [_report_text(r) for r in recs]

            header_h = 118
            y = page_break(y, header_h + 10)
            bottom = y - header_h
            c.setFillColor(BRAND)
            c.roundRect(x0, bottom, usable_w, header_h, 16, fill=1, stroke=0)
            c.setFillColor(ACCENT)
            c.roundRect(x0, y - 16, usable_w, 16, 16, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 22)
            c.drawString(x0 + 14, y - 30, "CombatIQ")
            c.setFont("Helvetica", 11)
            c.setFillColor(colors.HexColor("#D8E3EF"))
            c.drawString(x0 + 14, y - 46, "Informe comparativo de sesión")
            c.setFillColor(WHITE)
            c.setFont("Helvetica", 10)
            meta_lines = [
                f"Deportista: {name}",
                f"Deporte: {sport}",
                f"Generado: {generated_at} UTC",
                f"Sesión seleccionada: #{cur_id}",
                f"Sesión anterior: #{prev_id if prev_id else '—'}",
            ]
            meta_y = y - 66
            for line in meta_lines:
                c.drawString(x0 + 14, meta_y, line)
                meta_y -= 12
            y = bottom - 16

            y = draw_card(
                y,
                "Resumen ejecutivo",
                [overall_clean],
                subtitle="Lectura breve de la sesión actual frente a la anterior con los datos comparables disponibles.",
                fill=SURFACE_ALT,
            )

            y = draw_card(
                y,
                "Disponibilidad de datos",
                availability_lines,
                subtitle="Te mostramos qué señales entran de verdad en esta comparación.",
            )

            if table_rows:
                y = draw_section_title(y, "Tabla comparativa", "Esta sesión vs la anterior")
                y = draw_table(y, headers, table_rows, col_widths)
            else:
                y = draw_card(
                    y,
                    "Tabla comparativa",
                    ["Todavía no hay métricas comparables suficientes para construir esta tabla. Guarda o carga señales por sesión para enriquecer el informe."],
                    subtitle="Cuando haya datos comparables de recuperación cardiovascular o ritmo e impacto, aquí verás el detalle cuantitativo.",
                )

            if figs:
                y = draw_section_title(y, "Gráficas comparativas", "Visualizan la sesión seleccionada frente a la anterior.")
                any_img_error = False
                for title, fig in figs:
                    png, err = fig_to_png_bytes(fig)
                    if png:
                        y = draw_plot_image(y, title, png, max_h_cm=8.4)
                    else:
                        any_img_error = True
                        y = draw_card(y, f"Gráfica no disponible · {title}", [f"No se pudo renderizar la imagen ({err})."])
                if any_img_error:
                    y = draw_card(
                        y,
                        "Consejo técnico",
                        ["Para habilitar las imágenes de las gráficas en el PDF, instala kaleido con: python -m pip install -U kaleido"],
                    )
            else:
                y = draw_card(
                    y,
                    "Gráficas comparativas",
                    ["Aún no hay suficientes datos comparables para generar las gráficas de esta sesión."],
                )

            y = draw_card(
                y,
                "Recomendaciones",
                [f"- {r}" for r in recs_clean],
                subtitle="Sugerencias breves a partir de la comparación disponible.",
            )

            y = draw_card(
                y,
                "Nota de uso",
                ["Interpretación heurística para entrenamiento. Úsala como apoyo a la decisión, no como diagnóstico."],
            )

            draw_footer()
            c.save()
            pdf_bytes = buf.getvalue()
            buf.close()

            filename = f"CombatIQ_Informe_Sesion_{cur_id}.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

        # ---------- ECG clásico: cargar archivos por deportista ----------
        @app.callback(
            Output("cmp-ecg-table", "data"),
            Input("cmp-user", "value"),
            Input("cmp-ecg-refresh", "data"),
            prevent_initial_call=False,
        )
        def load_ecg_files_table(user_id, _refresh_token):
            uid = _safe_int(user_id)
            if not uid:
                return []

            files = db.list_ecg_files(int(uid)) or []
            rows = []

            for f in files:
                fname = f.get("filename")
                if not fname:
                    continue
                path = os.path.join("data", "ecg", fname)
                if not os.path.exists(path):
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
            State("cmp-ecg-table", "data"),
            State("cmp-ecg-table", "selected_rows"),
            prevent_initial_call=True,
        )
        def delete_selected_ecg_files(submit_n_clicks, refresh_token, data, selected_rows):
            if not submit_n_clicks:
                raise PreventUpdate
            if not hasattr(db, "delete_ecg_file"):
                return refresh_token or 0, "Tu base de datos aún no expone delete_ecg_file()."

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
            prevent_initial_call=False,
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

        # ---------- EMG: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-emg-table", "data"),
            Output("cmp-emg-bars", "figure"),
            Input("cmp-emg-upload", "contents"),
            State("cmp-emg-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_emg(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="emg_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            rms_list = []
            fatigue_list = []

            for session_name, path in sessions:
                try:
                    t, x, fs = read_emg_csv(path)
                except Exception:
                    continue
                if x is None or len(x) == 0:
                    continue

                rms, peak, fatigue = emg_metrics(x, fs)

                row = {
                    "session": session_name,
                    "rms": round(float(rms), 3),
                    "peak": round(float(peak), 3),
                    "fatigue": round(float(fatigue), 1),
                }
                rows.append(row)

                labels.append(session_name)
                rms_list.append(row["rms"])
                fatigue_list.append(row["fatigue"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=rms_list, name="RMS global"))
            fig.add_trace(go.Bar(x=labels, y=fatigue_list, name="Fatiga (%)"))
            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa EMG (RMS y fatiga)", x_title="Archivo", y_title="Valor", height=420)

            return rows, fig

        # ---------- RESP: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-resp-table", "data"),
            Output("cmp-resp-bars", "figure"),
            Input("cmp-resp-upload", "contents"),
            State("cmp-resp-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_resp(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="resp_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            br_min_list = []

            for session_name, path in sessions:
                try:
                    t, x, fs = read_resp_csv(path)
                except Exception:
                    continue
                if x is None or len(x) == 0:
                    continue

                n_breaths, br_min, mean_period, _ = resp_metrics(t, x, fs, sens=0.6)

                row = {
                    "session": session_name,
                    "n_breaths": int(n_breaths),
                    "br_min": round(float(br_min), 1),
                    "mean_period": round(float(mean_period), 2),
                }
                rows.append(row)

                labels.append(session_name)
                br_min_list.append(row["br_min"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=br_min_list, name="Resp/min"))
            apply_chart_style(fig, title="Comparativa respiración (respiraciones/min)", x_title="Archivo", y_title="Respiraciones/min", height=420)

            return rows, fig
