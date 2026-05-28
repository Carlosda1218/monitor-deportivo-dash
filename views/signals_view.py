# views/signals_view.py

import os
import io
import base64
import csv
import re
import uuid
import json
import time
from datetime import datetime

# ── Mapas de valores → etiquetas con tildes ────────────────────────────────
# Los valores en DB son ASCII (sin tilde); este mapa restaura la etiqueta
# legible. También cubre variantes corruptas (ó→?, é→?, etc.) de sesiones
# antiguas almacenadas con encoding incorrecto.
SESSION_TYPE_LABELS: dict[str, str] = {
    "sparring":               "Sparring",
    "tecnica":                "Técnica",
    "t?cnica":                "Técnica",
    "acondicionamiento":      "Acondicionamiento",
    "simulacion_competitiva": "Simulación competitiva",
    "simulaci?n_competitiva": "Simulación competitiva",
    "simulaci?n competitiva": "Simulación competitiva",
    "evaluacion":             "Evaluación",
    "evaluaci?n":             "Evaluación",
    "recuperacion":           "Recuperación",
    "recuperaci?n":           "Recuperación",
}
SESSION_GOAL_LABELS: dict[str, str] = {
    "tecnica":      "Técnica",
    "t?cnica":      "Técnica",
    "intensidad":   "Intensidad",
    "volumen":      "Volumen",
    "simulacion":   "Simulación",
    "simulaci?n":   "Simulación",
    "evaluacion":   "Evaluación",
    "evaluaci?n":   "Evaluación",
    "recuperacion": "Recuperación",
    "recuperaci?n": "Recuperación",
}
SESSION_STRUCTURE_LABELS: dict[str, str] = {
    "rounds":  "por rounds",
    "bloques": "por bloques",
    "libre":   "libre",
}

def _session_value_label(value: str, mapping: dict) -> str:
    """Devuelve la etiqueta correcta para un value de sesión, con fallback limpio."""
    if not value:
        return ""
    key = str(value).lower().strip()
    if key in mapping:
        return mapping[key]
    # Fallback: reemplazar _ por espacio y capitalizar
    return key.replace("_", " ").capitalize()

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, empty_figure, graph_config, placeholder_figure

import dash
from dash import html, dcc, Input, Output, State, ALL, ctx, no_update
from dash.exceptions import PreventUpdate
from flask import session

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.utils import simpleSplit, ImageReader

    _REPORTLAB_OK = True
    _REPORTLAB_ERR = None
except Exception as e:
    _REPORTLAB_OK = False
    _REPORTLAB_ERR = e


# ========= Cache temporal para analisis pesados =========

_POSE_JOB_CACHE: dict[str, dict] = {}
_POSE_JOB_TTL_SECONDS = 4 * 60 * 60
_POSE_JOB_MAX_ITEMS = 8
_POSE_ANALYSIS_VERSION = "chamber_angle_v1_2026_05_28"
_REPLAY_FRAME_CACHE: dict[tuple, dict] = {}
_REPLAY_FRAME_CACHE_TTL_SECONDS = 20 * 60
_REPLAY_FRAME_CACHE_MAX_ITEMS = 64


def _pose_cache_prune() -> None:
    now = time.time()
    expired = [
        key for key, payload in _POSE_JOB_CACHE.items()
        if now - float(payload.get("_ts", 0.0) or 0.0) > _POSE_JOB_TTL_SECONDS
    ]
    for key in expired:
        _POSE_JOB_CACHE.pop(key, None)
    while len(_POSE_JOB_CACHE) > _POSE_JOB_MAX_ITEMS:
        oldest = min(_POSE_JOB_CACHE, key=lambda k: float(_POSE_JOB_CACHE[k].get("_ts", 0.0) or 0.0))
        _POSE_JOB_CACHE.pop(oldest, None)


def _pose_cache_put(payload: dict, job_id: str | None = None) -> str:
    _pose_cache_prune()
    key = job_id or uuid.uuid4().hex
    payload["_ts"] = time.time()
    _POSE_JOB_CACHE[key] = payload
    return key


def _pose_cache_get(job_id: str | None) -> dict | None:
    if not job_id:
        return None
    payload = _POSE_JOB_CACHE.get(str(job_id))
    if not payload:
        return None
    if time.time() - float(payload.get("_ts", 0.0) or 0.0) > _POSE_JOB_TTL_SECONDS:
        _POSE_JOB_CACHE.pop(str(job_id), None)
        return None
    payload["_ts"] = time.time()
    return payload


def _slim_pose_report(report_data: dict, job_id: str) -> dict:
    """Evita mandar frames/base64 completos al navegador; el PDF resuelve por job_id."""
    if not report_data:
        return {"job_id": job_id}
    return {
        "job_id": job_id,
        "athlete_name": report_data.get("athlete_name"),
        "sport": report_data.get("sport"),
        "filename": report_data.get("filename"),
        "summary": report_data.get("summary") or {},
        "biomech": report_data.get("biomech") or {},
        "target": report_data.get("target") or {},
        "user_id": report_data.get("user_id"),
        "fps": report_data.get("fps"),
        "total_frames": report_data.get("total_frames"),
        "processing_s": report_data.get("processing_s"),
        "time_limited": bool(report_data.get("time_limited")),
        "has_duel": bool(report_data.get("duel")),
        "analyzer_version": report_data.get("analyzer_version") or _POSE_ANALYSIS_VERSION,
    }


def _resolve_pose_report_data(pose_data: dict | None) -> dict | None:
    if not pose_data:
        return None
    cached = _pose_cache_get(pose_data.get("job_id"))
    if cached and cached.get("report_data"):
        return cached["report_data"]
    return pose_data


def _replay_frame_cache_prune() -> None:
    now = time.time()
    expired = [
        key for key, payload in _REPLAY_FRAME_CACHE.items()
        if now - float(payload.get("_ts", 0.0) or 0.0) > _REPLAY_FRAME_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _REPLAY_FRAME_CACHE.pop(key, None)
    while len(_REPLAY_FRAME_CACHE) > _REPLAY_FRAME_CACHE_MAX_ITEMS:
        oldest = min(_REPLAY_FRAME_CACHE, key=lambda k: float(_REPLAY_FRAME_CACHE[k].get("_ts", 0.0) or 0.0))
        _REPLAY_FRAME_CACHE.pop(oldest, None)


def _extract_replay_frame_b64(video_path: str, ts: float, quality: int = 80) -> str | None:
    if not video_path:
        return None
    try:
        abs_path = os.path.abspath(video_path)
        mtime = os.path.getmtime(abs_path)
        size = os.path.getsize(abs_path)
    except Exception:
        return None

    key = (abs_path, round(float(ts or 0.0), 2), int(quality), mtime, size)
    cached = _REPLAY_FRAME_CACHE.get(key)
    if cached and time.time() - float(cached.get("_ts", 0.0) or 0.0) <= _REPLAY_FRAME_CACHE_TTL_SECONDS:
        cached["_ts"] = time.time()
        return cached.get("frame_b64")

    try:
        import cv2

        cap = cv2.VideoCapture(abs_path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_no = int(max(0.0, float(ts or 0.0)) * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            return None
        frame_b64 = base64.b64encode(buf).decode("utf-8")
        _replay_frame_cache_prune()
        _REPLAY_FRAME_CACHE[key] = {"_ts": time.time(), "frame_b64": frame_b64}
        return frame_b64
    except Exception:
        return None


# ========= Helpers comunes =========

def smooth(x: np.ndarray, win_ms: int, fs: int):
    x = np.asarray(x, dtype=float)
    if x.size < 3:
        return x

    win = max(3, int(round(win_ms * fs / 1000)))
    if win % 2 == 0:
        win += 1
    if win >= len(x):
        win = len(x) if len(x) % 2 == 1 else len(x) - 1
        if win < 3:
            return x
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def _find_peaks_simple(s, height, distance):
    s = np.asarray(s)
    n = len(s)
    if n < 3:
        return np.array([], dtype=int)
    cand = np.where((s[1:-1] > s[:-2]) & (s[1:-1] >= s[2:]) & (s[1:-1] >= height))[0] + 1
    if cand.size == 0:
        return cand
    order = cand[np.argsort(s[cand])[::-1]]
    kept = []
    blocked = np.zeros(n, dtype=bool)
    for idx in order:
        a = max(0, idx - distance)
        b = min(n, idx + distance + 1)
        if blocked[a:b].any():
            continue
        kept.append(idx)
        blocked[a:b] = True
    return np.array(sorted(kept), dtype=int)


def kpi_card(label, value, suffix=""):
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(f"{value}{suffix}", className="kpi-value"),
        html.Div(className="kpi-ecg-line")
    ])


def analysis_section(title, subtitle, inner, accent=None, class_name=""):
    return html.Div(
        className=f"card analysis-section-card {class_name}".strip(),
        children=[
            html.H4(title, className="card-title"),
            html.P(subtitle, className="text-muted"),
            inner,
        ],
    )


def analysis_fold(title, subtitle, children, open=False):
    return html.Details(
        className="card collapsible-card analysis-fold",
        open=open,
        children=[
            html.Summary(
                className="collapsible-card__summary",
                children=[
                    html.Div(
                        className="collapsible-card__head",
                        children=[
                            html.H4(title, className="card-title"),
                            html.P(subtitle, className="text-muted"),
                        ],
                    ),
                    html.Span("›", className="collapsible-card__chevron"),
                ],
            ),
            html.Div(className="collapsible-card__body", children=children),
        ],
    )


def _graph_interpretation(lines: list[str], open: bool = False):
    """Small explanation fold placed directly under a chart."""
    clean_lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if not clean_lines:
        return html.Div()
    return html.Details(
        className="collapsible-card graph-interpretation",
        open=open,
        style={"marginTop": "8px"},
        children=[
            html.Summary(className="collapsible-card__summary", children=[
                html.Div(className="collapsible-card__head", children=[
                    html.Span("Cómo interpreto esta gráfica", className="card-title"),
                    html.Span("Lectura visual, no recomendación", className="text-muted"),
                ]),
                html.Span("›", className="collapsible-card__chevron"),
            ]),
            html.Div(
                className="collapsible-card__body",
                children=html.Ul([html.Li(line) for line in clean_lines], className="list-compact"),
            ),
        ],
    )


def _frame_label(t_s, fps) -> str:
    try:
        t = float(t_s or 0.0)
    except Exception:
        t = 0.0
    try:
        frame = int(round(t * float(fps or 0.0)))
    except Exception:
        frame = 0
    return f"t={t:.1f}s · frame ~{frame}"


def _frame_ref(record: dict, fps) -> str:
    if not isinstance(record, dict):
        return _frame_label(0.0, fps)
    try:
        t = float(record.get("t") or 0.0)
    except Exception:
        t = 0.0
    frame = record.get("frame")
    try:
        frame_txt = f"frame {int(frame)}" if frame is not None else f"frame ~{int(round(t * float(fps or 0.0)))}"
    except Exception:
        frame_txt = "frame ~0"
    return f"t={t:.1f}s · {frame_txt}"


def _evidence_list(items: list[dict]) -> html.Div:
    """Render frame/time evidence for coaching statements."""
    clean = [item for item in (items or []) if item]
    if not clean:
        return html.Div()
    return html.Div(
        className="frame-evidence",
        style={"marginTop": "12px"},
        children=[
            html.H5("Evidencia en frames", style={"margin": "10px 0 6px"}),
            html.Ul(
                [
                    html.Li([
                        html.Strong(item.get("moment", "Momento") + ": "),
                        item.get("why", ""),
                        html.Br(),
                        html.Span(
                            item.get("frame", ""),
                            className="text-muted",
                            style={"fontSize": "11px"},
                        ),
                    ])
                    for item in clean
                ],
                className="list-compact",
            ),
        ],
    )


def _duel_frame_evidence(duel_frames: list, fps, pressure_label: str = "") -> list[dict]:
    frames = [f for f in (duel_frames or []) if isinstance(f, dict)]
    if not frames:
        return []
    evidence = []

    closest = min(frames, key=lambda f: float(f.get("distance", 999) or 999))
    evidence.append({
        "moment": "Distancia más corta",
        "why": f"Se usa para revisar cierre de espacio porque la distancia bajó a {float(closest.get('distance', 0) or 0):.3f}.",
        "frame": _frame_ref(closest, fps),
    })

    exchanges = [f for f in frames if f.get("exchange")]
    if exchanges:
        exchange = max(
            exchanges,
            key=lambda f: float(f.get("red_move", 0) or 0) + float(f.get("blue_move", 0) or 0),
        )
        evidence.append({
            "moment": "Posible intercambio",
            "why": "Aquí se cruzan cercanía y movimiento simultáneo; por eso se marca para confirmar en video.",
            "frame": _frame_ref(exchange, fps),
        })

    label = (pressure_label or "").lower()
    if "rojo" in label:
        pressure = max(frames, key=lambda f: float(f.get("red_toward", 0) or 0))
        evidence.append({
            "moment": "Presión del rojo",
            "why": "El centro del peto rojo avanza más hacia el rival en este tramo.",
            "frame": _frame_ref(pressure, fps),
        })
    elif "azul" in label:
        pressure = max(frames, key=lambda f: float(f.get("blue_toward", 0) or 0))
        evidence.append({
            "moment": "Presión del azul",
            "why": "El centro del peto azul avanza más hacia el rival en este tramo.",
            "frame": _frame_ref(pressure, fps),
        })

    peak = max(
        frames,
        key=lambda f: max(float(f.get("red_ang_vel_max", 0) or 0), float(f.get("blue_ang_vel_max", 0) or 0)),
    )
    evidence.append({
        "moment": "Pico de velocidad angular",
        "why": (
            f"Rojo {float(peak.get('red_ang_vel_max', 0) or 0):.0f}°/s · "
            f"azul {float(peak.get('blue_ang_vel_max', 0) or 0):.0f}°/s; es un momento útil para revisar acción explosiva."
        ),
        "frame": _frame_ref(peak, fps),
    })
    return evidence[:4]


def _single_frame_evidence(frames: list, fps, metrics: dict) -> list[dict]:
    valid = [f for f in (frames or []) if isinstance(f, dict)]
    if not valid:
        return []
    evidence = []

    def _abs_delta(frame, left, right):
        try:
            return abs(float(frame.get(left)) - float(frame.get(right)))
        except Exception:
            return 0.0

    lower_frame = max(
        valid,
        key=lambda f: max(
            float(f.get("knee_l", 0) or 0),
            float(f.get("knee_r", 0) or 0),
            float(f.get("hip_l", 0) or 0),
            float(f.get("hip_r", 0) or 0),
        ),
    )
    evidence.append({
        "moment": "Mayor amplitud de tren inferior",
        "why": "Aquí aparece el mayor valor de rodilla/cadera dentro de los frames válidos.",
        "frame": _frame_ref(lower_frame, fps),
    })

    lower_asym = max(float((metrics or {}).get("knee_asym", 0) or 0), float((metrics or {}).get("hip_asym", 0) or 0))
    if lower_asym > 10:
        asym_frame = max(
            valid,
            key=lambda f: _abs_delta(f, "knee_l", "knee_r") + _abs_delta(f, "hip_l", "hip_r"),
        )
        evidence.append({
            "moment": "Diferencia entre lados",
            "why": f"La asimetría de pierna llega a {lower_asym:.1f}°; este frame ayuda a revisar si fue técnica o ruido.",
            "frame": _frame_ref(asym_frame, fps),
        })

    warning_frame = next((f for f in valid if f.get("landmark_warnings")), None)
    if warning_frame:
        evidence.append({
            "moment": "Frame con puntos dudosos",
            "why": "CombatIQ descartó o limpió landmarks en este momento para no medir a otra persona u oclusión.",
            "frame": _frame_ref(warning_frame, fps),
        })

    quality_frame = min(valid, key=lambda f: float(f.get("pose_quality", 1) or 1))
    if float(quality_frame.get("pose_quality", 1) or 1) < 0.75:
        evidence.append({
            "moment": "Menor calidad de pose",
            "why": "Este frame baja la confianza de lectura; conviene revisar encuadre, luz o cuerpos cruzados.",
            "frame": _frame_ref(quality_frame, fps),
        })

    return evidence[:4]


def _report_text(value) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "•": "-",
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "’": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def _fmt_num(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}".replace(".", ",")
    except Exception:
        return "-"


def _apply_signal_pdf_chart_style(fig: go.Figure, title: str, x_title: str, y_title: str):
    apply_chart_style(fig, title=title, x_title=x_title, y_title=y_title, height=330)
    fig.update_layout(
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(color="#0E1522"),
        margin=dict(l=44, r=18, t=52, b=44),
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
        title=dict(text=x_title, font=dict(size=12, color="#334155")),
        showspikes=False,
    )
    fig.update_yaxes(
        gridcolor="rgba(148,163,184,0.18)",
        linecolor="#CBD5E1",
        tickcolor="#CBD5E1",
        tickfont=dict(size=10.5, color="#334155"),
        title=dict(text=y_title, font=dict(size=12, color="#334155")),
        showspikes=False,
    )
    return fig


def _build_signal_report_pdf(
    *,
    report_title: str,
    report_subtitle: str,
    athlete_name: str,
    sport: str,
    session_label: str,
    source_label: str,
    summary: str,
    metric_lines,
    explain_lines,
    note_lines,
    fig: go.Figure | None = None,
    figure_title: str | None = None,
    raw_metrics: list | None = None,
    status: str | None = None,
    status_label: str | None = None,
):
    """
    Genera el PDF de informe de señal usando el motor visual CombatIQPDF.

    raw_metrics: lista de dicts {label, value, unit, color (opt)} para las
                 tarjetas métricas. Si se pasa, se muestra una fila de KPIs.
    status / status_label: si se pasan, se muestra una pastilla de estado
                 (status = 'ok' | 'warn' | 'alert').
    """
    if not _REPORTLAB_OK:
        raise RuntimeError(str(_REPORTLAB_ERR))

    from report_utils import CombatIQPDF

    pdf = CombatIQPDF()

    pdf.header(
        report_title,
        report_subtitle,
        athlete_name,
        sport,
        session=session_label,
        source=source_label,
    )

    if status and status_label:
        pdf.status_badge(status_label, status)

    if raw_metrics:
        pdf.metric_table(raw_metrics)
        pdf.spacer(0.1)

    pdf.card(
        "Resumen rapido",
        [summary],
        subtitle=report_subtitle if not (status and status_label) else None,
    )

    pdf.card(
        "Indicadores clave",
        metric_lines,
        subtitle="Lectura de los valores principales de esta señal.",
    )

    if fig is not None:
        pdf.chart(fig, figure_title or "Gráfica principal")

    pdf.card(
        "Como leer esta grafica",
        explain_lines,
        subtitle="Guia rapida para interpretar la señal y su utilidad practica.",
    )

    pdf.card(
        "Nota de uso",
        note_lines,
        fill=None,
        accent=None,
    )

    return pdf.finish()


def _figure_png_fallback(fig_dict, title: str = "Gráfica CombatIQ") -> bytes | None:
    """Basic PNG renderer used when Plotly/Kaleido cannot export images."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    width, height = 1200, 700
    bg = "#0D1B2A"
    surface = "#111827"
    line = "#D1DCE8"
    muted = "#8FA3BF"
    text = "#E8ECF0"
    teal = "#2FB7C4"

    def _font(size, bold=False):
        candidates = [
            r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _num_list(values):
        out = []
        for value in values or []:
            try:
                out.append(float(value))
            except Exception:
                out.append(None)
        return out

    def _hex(value, fallback):
        if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()):
            return value.strip()
        return fallback

    fig_dict = fig_dict or {}
    layout = fig_dict.get("layout") or {}
    layout_title = layout.get("title") or {}
    if isinstance(layout_title, dict):
        title = layout_title.get("text") or title
    elif layout_title:
        title = str(layout_title)

    traces = []
    for trace in (fig_dict.get("data") or [])[:6]:
        xs = _num_list(trace.get("x"))
        ys = _num_list(trace.get("y"))
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if not pairs:
            continue
        if len(pairs) > 1200:
            step = max(1, len(pairs) // 1200)
            pairs = pairs[::step]
        color = _hex(((trace.get("line") or {}).get("color")), teal)
        traces.append({
            "name": str(trace.get("name") or "Serie")[:28],
            "pairs": pairs,
            "color": color,
        })

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    f_title = _font(34, True)
    f_body = _font(20)
    f_small = _font(16)

    draw.rounded_rectangle((34, 34, width - 34, height - 34), radius=30, fill=surface, outline="#243447", width=2)
    draw.rectangle((34, 34, 74, height - 34), fill=teal)
    draw.text((104, 76), "CombatIQ", fill=teal, font=f_title)
    draw.text((104, 122), str(title)[:80], fill=text, font=f_body)

    x0, y0, x1, y1 = 104, 190, width - 96, height - 118
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill="#0B1220", outline="#243447", width=2)

    if not traces:
        draw.text((x0 + 36, y0 + 145), "Sin datos suficientes para dibujar la grafica.", fill=muted, font=f_body)
    else:
        all_x = [x for trace in traces for x, _ in trace["pairs"]]
        all_y = [y for trace in traces for _, y in trace["pairs"]]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        if min_x == max_x:
            max_x = min_x + 1.0
        if min_y == max_y:
            max_y = min_y + 1.0

        pad_l, pad_r, pad_t, pad_b = 72, 28, 34, 56
        px0, py0, px1, py1 = x0 + pad_l, y0 + pad_t, x1 - pad_r, y1 - pad_b
        for i in range(5):
            yy = py0 + (py1 - py0) * i / 4
            draw.line((px0, yy, px1, yy), fill="#1F2D3D", width=1)
        draw.line((px0, py1, px1, py1), fill=line, width=2)
        draw.line((px0, py0, px0, py1), fill=line, width=2)

        def _point(x, y):
            px = px0 + (x - min_x) / (max_x - min_x) * (px1 - px0)
            py = py1 - (y - min_y) / (max_y - min_y) * (py1 - py0)
            return px, py

        for trace in traces:
            pts = [_point(x, y) for x, y in trace["pairs"]]
            if len(pts) >= 2:
                draw.line(pts, fill=trace["color"], width=3, joint="curve")
            elif pts:
                px, py = pts[0]
                draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=trace["color"])

        draw.text((px0, py1 + 18), f"{min_x:.1f}", fill=muted, font=f_small)
        draw.text((px1 - 58, py1 + 18), f"{max_x:.1f}", fill=muted, font=f_small)
        draw.text((x0 + 20, py0 - 4), f"{max_y:.1f}", fill=muted, font=f_small)
        draw.text((x0 + 20, py1 - 12), f"{min_y:.1f}", fill=muted, font=f_small)

        leg_x, leg_y = x0 + 28, y1 + 24
        for idx, trace in enumerate(traces[:4]):
            lx = leg_x + idx * 245
            draw.line((lx, leg_y + 10, lx + 36, leg_y + 10), fill=trace["color"], width=5)
            draw.text((lx + 46, leg_y), trace["name"], fill=muted, font=f_small)

    draw.text((width - 250, height - 78), "combatiq.app", fill=muted, font=f_small)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ========= Auto-annotation generator =========

def _generate_auto_annotations(ecg_pts: list, imu_pts: list) -> list:
    """
    Genera anotaciones objetivas a partir de señales ECG e IMU.
    Devuelve lista de dicts {time, type, text, auto=True}.
    """
    anns = []

    # ── ECG: HR zones via R-peaks ─────────────────────────────────────────
    if len(ecg_pts) > 50:
        ts = np.array([p["t"] for p in ecg_pts])
        ys = np.array([p["y"] for p in ecg_pts])

        dt = float(np.median(np.diff(ts))) if len(ts) > 1 else 0
        fs = max(50, min(500, int(round(1.0 / dt)))) if dt > 0 else 250

        max_y = float(np.max(np.abs(ys))) if ys.size else 0
        peaks_idx = _find_peaks_simple(ys, height=max_y * 0.5, distance=int(fs * 0.35))

        if len(peaks_idx) > 2:
            peak_times = ts[peaks_idx]
            rr = np.diff(peak_times)
            hr = 60.0 / np.where(rr > 0, rr, np.inf)

            # Zones: very high >175, high 155–175, recovery <120
            def _find_runs(mask):
                runs = []
                i = 0
                while i < len(mask):
                    if mask[i]:
                        j = i
                        while j < len(mask) and mask[j]:
                            j += 1
                        runs.append((i, j))
                        i = j
                    else:
                        i += 1
                return runs

            for s, e in _find_runs(hr > 175):
                t0 = float(peak_times[s])
                peak_hr = int(np.max(hr[s:e]))
                anns.append({"time": round(t0, 1), "type": "general",
                              "text": f"FC máxima: {peak_hr} bpm", "auto": True})

            for s, e in _find_runs((hr >= 155) & (hr <= 175)):
                t0 = float(peak_times[s])
                avg_hr = int(np.mean(hr[s:e]))
                anns.append({"time": round(t0, 1), "type": "general",
                              "text": f"FC alta: {avg_hr} bpm", "auto": True})

            for s, e in _find_runs(hr < 120):
                t0 = float(peak_times[s])
                t1 = float(peak_times[min(e, len(peak_times) - 1)])
                if t1 - t0 >= 3.0:
                    min_hr = int(np.min(hr[s:e]))
                    anns.append({"time": round(t0, 1), "type": "general",
                                 "text": f"Recuperación — FC {min_hr} bpm", "auto": True})

            # Fatiga alta: FC sostenida > 155 bpm durante ≥ 8 s
            for s, e in _find_runs(hr > 155):
                t0 = float(peak_times[s])
                t1 = float(peak_times[min(e, len(peak_times) - 1)])
                if t1 - t0 >= 8.0:
                    avg_hr = int(np.mean(hr[s:e]))
                    anns.append({"time": round(t0, 1), "type": "fatiga_alta",
                                 "text": f"Alta intensidad sostenida — FC {avg_hr} bpm",
                                 "auto": True})

            # Caída de BPM: descenso ≥ 30 bpm en ventana de 10 s (transición a descanso)
            if len(peak_times) > 4:
                WINDOW_S = 10.0
                for i in range(len(hr) - 1):
                    window_end = np.searchsorted(peak_times, peak_times[i] + WINDOW_S)
                    if window_end >= len(hr):
                        break
                    drop = hr[i] - hr[window_end]
                    if drop >= 30 and hr[i] > 130:
                        anns.append({
                            "time": round(float(peak_times[i]), 1),
                            "type": "caida_bpm",
                            "text": f"Caída FC: {int(hr[i])} → {int(hr[window_end])} bpm",
                            "auto": True,
                        })

    # ── IMU: high-impact events ───────────────────────────────────────────
    if imu_pts:
        MIN_G = 3.0
        GROUP_WINDOW = 3.0
        sig = sorted(
            [p for p in imu_pts if p.get("intensity", 0) >= MIN_G and p.get("type") != "ruido"],
            key=lambda x: x.get("t", 0),
        )
        if sig:
            groups = [[sig[0]]]
            for pt in sig[1:]:
                if pt["t"] - groups[-1][-1]["t"] <= GROUP_WINDOW:
                    groups[-1].append(pt)
                else:
                    groups.append([pt])
            for grp in groups:
                max_g   = max(p["intensity"] for p in grp)
                t0      = grp[0]["t"]
                hit_type = grp[0].get("type", "dado")
                ann_type = "attack" if hit_type == "dado" else "defense"
                text = (f"{len(grp)} impactos · máx {max_g:.1f} g"
                        if len(grp) > 1 else f"Impacto: {max_g:.1f} g")
                anns.append({"time": round(float(t0), 1), "type": ann_type,
                             "text": text, "auto": True})

    return sorted(anns, key=lambda a: a["time"])


# ========= Upload safety helpers =========

_ALLOWED_EXTS = {".csv"}
_MAX_CSV_UPLOAD_BYTES = 25 * 1024 * 1024


def _sanitize_filename(filename: str, default: str = "file.csv") -> str:
    """
    - Quita rutas (basename)
    - Permite solo [a-zA-Z0-9._-]
    - Normaliza espacios a _
    - Fuerza extensión permitida (csv)
    - Limita longitud
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


def _save_unique(dirpath: str, filename: str, data: bytes) -> str:
    """
    Guarda evitando sobrescritura. Si ya existe, agrega sufijo _<id>.
    Devuelve el nombre final.
    """
    os.makedirs(dirpath, exist_ok=True)

    safe = _sanitize_filename(filename, default="file.csv")
    base, ext = os.path.splitext(safe)
    candidate = safe
    full = os.path.join(dirpath, candidate)

    if os.path.exists(full):
        suffix = uuid.uuid4().hex[:8]
        candidate = f"{base}_{suffix}{ext}"
        full = os.path.join(dirpath, candidate)

    with open(full, "wb") as f:
        f.write(data)

    return candidate


def _b64_to_bytes(content: str):
    if not content:
        raise ValueError("Contenido vacío")
    try:
        _, b64 = content.split(",", 1)
        data = base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e
    if len(data) > _MAX_CSV_UPLOAD_BYTES:
        raise ValueError("Archivo demasiado grande. Maximo: 25 MB")
    return data


# ========= ECG =========

def read_ecg_csv(path: str, fs_default: int = 250):
    with open(path, newline='', encoding='utf-8', errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header) and ("ecg" in header or "time" in header or "tiempo" in header)
    data_rows = rows[1:] if has_header else rows

    time_col = None
    ecg_col = None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_col = i
            if name == "ecg":
                ecg_col = i
    if ecg_col is None:
        ecg_col = 0

    x_vals, t_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(float((r[ecg_col] or "").replace(",", ".")))
        except Exception:
            continue
        if time_col is not None and time_col < len(r):
            try:
                t_vals.append(float((r[time_col] or "").replace(",", ".")))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    x = np.array(x_vals, dtype=float)
    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(x)) / fs
    return t, x, fs


# ✅ Cache ligero para evitar lecturas repetidas del mismo archivo con sliders
_ECG_CACHE = {}
_ECG_CACHE_MAX = 16
_ECG_PROCESS_CACHE = {}
_ECG_PROCESS_CACHE_MAX = 32


def _cached_read_ecg_csv(path: str, fs_default: int = 250):
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    key = (path, mtime, int(fs_default or 250))
    if key in _ECG_CACHE:
        return _ECG_CACHE[key]
    out = read_ecg_csv(path, fs_default=fs_default)
    if len(_ECG_CACHE) >= _ECG_CACHE_MAX:
        try:
            _ECG_CACHE.pop(next(iter(_ECG_CACHE)))
        except Exception:
            _ECG_CACHE.clear()
    _ECG_CACHE[key] = out
    return out


def _cached_ecg_process(path: str, x: np.ndarray, fs: int, smooth_ms: int = 0, sens: float = 0.6):
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    smooth_key = int(smooth_ms or 0)
    sens_key = round(float(sens or 0.6), 3)
    key = (path, mtime, int(fs or 250), len(x), smooth_key, sens_key)
    if key in _ECG_PROCESS_CACHE:
        return _ECG_PROCESS_CACHE[key]

    try:
        xs = smooth(x, smooth_key, fs) if smooth_key > 0 else x
    except Exception:
        xs = x
    try:
        peaks = detect_r_peaks(xs, fs, sens_key)
    except Exception:
        peaks = np.array([], dtype=int)

    if len(_ECG_PROCESS_CACHE) >= _ECG_PROCESS_CACHE_MAX:
        try:
            _ECG_PROCESS_CACHE.pop(next(iter(_ECG_PROCESS_CACHE)))
        except Exception:
            _ECG_PROCESS_CACHE.clear()
    _ECG_PROCESS_CACHE[key] = (xs, peaks)
    return xs, peaks


def detect_r_peaks(x: np.ndarray, fs: int, sens: float = 0.6):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return np.array([], dtype=int)
    # A 180 bpm los picos R estan a 1.33 muestras de distancia a 4 Hz —
    # la deteccion de picos es matematicamente imposible por debajo de ~50 Hz.
    if fs < 50:
        return np.array([], dtype=int)

    z = (x - np.median(x))
    env = smooth(np.abs(z), win_ms=80, fs=fs)
    thr = np.quantile(env, sens)
    dist = int(0.25 * fs)
    peaks = _find_peaks_simple(env, height=thr, distance=dist)
    return peaks


def ecg_metrics_from_peaks(peaks: np.ndarray, fs: int):
    if peaks is None or len(peaks) < 2:
        return 0.0, 0.0, 0.0
    rr = np.diff(peaks) / fs
    bpm = 60.0 / np.mean(rr)
    sdnn = 1000 * np.std(rr)
    rmssd = 1000 * np.sqrt(np.mean(np.diff(rr) ** 2))
    return float(bpm), float(sdnn), float(rmssd)


def fig_ecg(t_line, x_line, peaks_t=None, peaks_y=None, title="ECG", bands=None):
    fig = go.Figure()

    if bands:
        for band in bands:
            label = band.get("label") or ""
            vrect_kwargs = dict(
                x0=band["x0"], x1=band["x1"],
                fillcolor=band["color"],
                opacity=band.get("opacity", 0.10),
                layer="below", line_width=0,
            )
            if label:
                vrect_kwargs["annotation_text"] = label
                vrect_kwargs["annotation_position"] = "top left"
                vrect_kwargs["annotation_font_size"] = 10
                vrect_kwargs["annotation_font_color"] = band.get("label_color") or "#888"
            fig.add_vrect(**vrect_kwargs)

    fig.add_trace(go.Scatter(
        x=t_line, y=x_line, mode="lines", name="ECG",
        line=dict(color="#2fb7c4", width=2)
    ))
    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Picos R",
            marker=dict(size=7, symbol="x", color="#2fb7c4")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_ecg(bpm, sdnn, rmssd):
    if bpm and bpm > 0:
        bpm_str   = f"{bpm:.0f}"
        sdnn_str  = f"{sdnn:.0f}"
        rmssd_str = f"{rmssd:.0f}"
        sdnn_unit  = " ms"
        rmssd_unit = " ms"
    else:
        bpm_str = rmssd_str = sdnn_str = "—"
        sdnn_unit = rmssd_unit = ""
    return [
        kpi_card("Ritmo cardíaco", bpm_str),
        kpi_card("Variabilidad", sdnn_str, sdnn_unit),
        kpi_card("Recuperación", rmssd_str, rmssd_unit),
    ]


# ========= IMU =========

def read_imu_csv(path: str, fs_default: int = 100):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)

    if len(header) == 1 and (";" in header[0] or "\t" in header[0]):
        with open(path, encoding="utf-8", errors="ignore") as f:
            raw_lines = f.read().splitlines()
        sep = ";" if ";" in header[0] else "\t"
        rows = [line.split(sep) for line in raw_lines]
        header = [h.strip().lower() for h in rows[0]]
        has_header = any(header)

    if has_header and any(c in header for c in ("ax", "ay", "az")):
        data_rows = rows[1:]
        try:
            time_idx = header.index("time") if "time" in header else None
        except ValueError:
            time_idx = None
        try:
            ax_idx = header.index("ax")
            ay_idx = header.index("ay")
            az_idx = header.index("az")
        except ValueError:
            ax_idx, ay_idx, az_idx = 0, 1, 2
    else:
        data_rows = rows
        time_idx, ax_idx, ay_idx, az_idx = None, 0, 1, 2

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, mag_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            ax = to_float(r[ax_idx]) if ax_idx < len(r) else None
            ay = to_float(r[ay_idx]) if ay_idx < len(r) else None
            az = to_float(r[az_idx]) if az_idx < len(r) else None
            if None in (ax, ay, az):
                continue
        except Exception:
            continue

        mag = (ax ** 2 + ay ** 2 + az ** 2) ** 0.5
        mag_vals.append(mag)

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    mag = np.array(mag_vals, dtype=float)

    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(mag)) / fs

    return t, mag, fs


_IMU_CACHE = {}
_IMU_CACHE_MAX = 12


def _cached_read_imu_csv(path: str, fs_default: int = 100):
    """Avoid re-reading the same IMU file on every slider/export interaction."""
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    key = (path, mtime, int(fs_default or 100))
    if key in _IMU_CACHE:
        return _IMU_CACHE[key]
    out = read_imu_csv(path, fs_default=fs_default)
    if len(_IMU_CACHE) >= _IMU_CACHE_MAX:
        try:
            _IMU_CACHE.pop(next(iter(_IMU_CACHE)))
        except Exception:
            _IMU_CACHE.clear()
    _IMU_CACHE[key] = out
    return out


def imu_metrics_from_mag(mag: np.ndarray, t: np.ndarray, fs: int):
    if mag is None or len(mag) < 5:
        return 0, 0.0, 0.0, 0.0, np.array([], dtype=int)

    thr = float(np.quantile(mag, 0.90))
    dist = max(1, int(0.1 * fs))
    peaks = _find_peaks_simple(mag, height=thr, distance=dist)

    duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    n_hits = int(len(peaks))
    hits_per_min = (n_hits / (duration / 60.0)) if duration > 0 else 0.0

    if n_hits > 0:
        mean_int = float(np.mean(mag[peaks])) / 9.81  # g
        max_int = float(np.max(mag[peaks])) / 9.81
    else:
        mean_int = 0.0
        max_int = 0.0

    return n_hits, hits_per_min, mean_int, max_int, peaks


def fig_imu(t_line, mag_line, peaks_t=None, peaks_y=None, thr=None, title="Golpes / IMU"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=mag_line, mode="lines", name="|a| (m/s²)",
        line=dict(color="#2fb7c4", width=2)
    ))

    # Umbral visual (P90) — no cambia el algoritmo, solo ayuda a lectura
    if thr is not None and len(t_line) > 1:
        try:
            fig.add_shape(
                type="line",
                x0=float(t_line[0]), x1=float(t_line[-1]),
                y0=float(thr), y1=float(thr),
                line=dict(color="rgba(0,242,138,0.35)", width=2, dash="dash"),
            )
            fig.add_annotation(
                x=float(t_line[0]),
                y=float(thr),
                text=f"Umbral (P90) ≈ {float(thr)/9.81:.2f} g",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=12, color="rgba(231,236,243,0.85)"),
                bgcolor="rgba(15,22,35,0.35)",
                bordercolor="rgba(44,61,85,0.55)",
                borderwidth=1,
                borderpad=4,
            )
        except Exception:
            pass

    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Eventos detectados",
            marker=dict(symbol="x", size=8, color="#2fb7c4")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="|a| (m/s²)",
        height=420,
    )
    return fig

def _speed_section(speed_data: dict) -> list:
    """Builds velocity KPI cards + charts from yolo_tracker result."""
    if not speed_data:
        return [html.Div(
            html.P("Velocidades YOLO: no disponibles (video no procesado o error de modelo)",
                   style={"fontSize": "11px", "color": "var(--text-muted)", "fontStyle": "italic"}),
            className="card", style={"padding": "10px 14px", "marginTop": "8px"},
        )]
    if speed_data.get("error"):
        return [html.Div(
            html.P(f"Velocidades YOLO: error — {speed_data['error']}",
                   style={"fontSize": "11px", "color": "#e45a5a"}),
            className="card", style={"padding": "10px 14px", "marginTop": "8px"},
        )]

    children = []
    colors   = {"azul": "#3b82f6", "rojo": "#ef4444"}
    labels   = {"azul": "Peto azul", "rojo": "Peto rojo"}

    # ── KPI cards ─────────────────────────────────────────────────────────
    kpi_items = []
    for color in ("azul", "rojo"):
        d = speed_data.get(color) or {}
        if not d or d.get("error"):
            continue
        max_k  = d.get("max_kick_ms", 0)
        avg_dp = d.get("avg_displacement_ms", 0)
        kpi_items += [
            kpi_card(f"Vel. pateo máx · {labels[color]}", f"{max_k:.1f}", " m/s"),
            kpi_card(f"Vel. desplaz. media · {labels[color]}", f"{avg_dp:.1f}", " m/s"),
        ]
    if kpi_items:
        children.append(html.Div(kpi_items, className="kpi-grid"))

    # ── Kick speed chart ───────────────────────────────────────────────────
    kick_traces = []
    for color in ("azul", "rojo"):
        d = speed_data.get(color) or {}
        ks = d.get("kick_speeds") or []
        if not ks:
            continue
        xs = [e["t"] for e in ks]
        ys = [e["speed_ms"] for e in ks]
        kick_traces.append(go.Scatter(
            x=xs, y=ys, mode="lines", name=labels[color],
            line={"color": colors[color], "width": 1.5},
        ))
        # Peak markers
        peaks = d.get("peak_kicks") or []
        if peaks:
            kick_traces.append(go.Scatter(
                x=[p["t"] for p in peaks],
                y=[p["speed_ms"] for p in peaks],
                mode="markers", name=f"Pico {labels[color]}",
                marker={"color": colors[color], "size": 7, "symbol": "star"},
                showlegend=False,
            ))

    if kick_traces:
        fig_kick = go.Figure(kick_traces)
        fig_kick = apply_chart_style(fig_kick, height=200)
        fig_kick.update_layout(
            title={"text": "Velocidad de pateo (m/s)", "font": {"size": 12}},
            yaxis_title="m/s", xaxis_title="Tiempo (s)",
            legend={"orientation": "h", "y": 1.1, "font": {"size": 10}},
        )
        children.append(dcc.Graph(figure=fig_kick, config={"displayModeBar": False},
                                  style={"marginTop": "12px"}))
        children.append(_graph_interpretation([
            "Cada línea muestra velocidad estimada de pateo por peto a lo largo del video.",
            "Los marcadores de pico señalan acciones rápidas que conviene revisar en video antes de asumir que fueron técnicas efectivas.",
            "Si hay saltos extremos o picos pegados al límite fisiológico, interpreta la lectura como tendencia y confirma con el frame anotado.",
        ]))

    # ── Displacement speed chart ───────────────────────────────────────────
    disp_traces = []
    for color in ("azul", "rojo"):
        d = speed_data.get(color) or {}
        ds = d.get("displacement_speeds") or []
        if not ds:
            continue
        disp_traces.append(go.Scatter(
            x=[e["t"] for e in ds], y=[e["speed_ms"] for e in ds],
            mode="lines", name=labels[color],
            line={"color": colors[color], "width": 1.5, "dash": "dot"},
        ))

    if disp_traces:
        fig_disp = go.Figure(disp_traces)
        fig_disp = apply_chart_style(fig_disp, height=160)
        fig_disp.update_layout(
            title={"text": "Velocidad de desplazamiento (m/s)", "font": {"size": 12}},
            yaxis_title="m/s", xaxis_title="Tiempo (s)",
            legend={"orientation": "h", "y": 1.1, "font": {"size": 10}},
        )
        children.append(dcc.Graph(figure=fig_disp, config={"displayModeBar": False},
                                  style={"marginTop": "8px"}))
        children.append(_graph_interpretation([
            "La curva muestra qué tan rápido se desplaza cada peto, no quién gana el intercambio.",
            "Subidas simultáneas suelen coincidir con entradas, salidas o presión de ambos atletas.",
            "Una velocidad media estable ayuda a revisar control de distancia y capacidad de sostener ritmo.",
        ]))

    if not children:
        return []

    return [html.Details(
        className="card collapsible-card",
        open=True,
        children=[
            html.Summary(className="collapsible-card__summary", children=[
                html.Div(className="collapsible-card__head", children=[
                    html.Span("Velocidades", className="card-title"),
                    html.Span("Pateo · desplazamiento · picos", className="text-muted"),
                ]),
                html.Span("⌄", className="collapsible-card__chevron"),
            ]),
            html.Div(className="collapsible-card__body", children=children),
        ],
    )]


def _biomech_yolo_section(speed_data: dict) -> list:
    """Displays YOLO biomechanics: ROM, bilateral asymmetry, peak angular velocity."""
    if not speed_data:
        return []
    biomech = speed_data.get("yolo_biomech") or {}
    if not biomech:
        return []
    has_data = any(
        isinstance(biomech.get(c), dict) and not biomech[c].get("error")
        for c in ("azul", "rojo")
    )
    if not has_data:
        return []

    colors       = {"azul": "#3b82f6", "rojo": "#ef4444"}
    labels       = {"azul": "Peto azul", "rojo": "Peto rojo"}
    joint_labels = {
        "knee_l": "Rodilla izq.", "knee_r": "Rodilla der.",
        "hip_l":  "Cadera izq.",  "hip_r":  "Cadera der.",
        "elbow_l": "Codo izq.",   "elbow_r": "Codo der.",
    }
    joint_order = ["knee_l", "knee_r", "hip_l", "hip_r", "elbow_l", "elbow_r"]
    children = []

    # ── ROM KPI cards ──────────────────────────────────────────────────────
    kpi_items = []
    for color in ("azul", "rojo"):
        d = biomech.get(color) or {}
        if not d or d.get("error"):
            continue
        rom = d.get("rom") or {}
        for jname in ("knee_r", "knee_l", "hip_r", "hip_l"):
            if jname in rom:
                kpi_items.append(kpi_card(
                    f"ROM {joint_labels[jname]} · {labels[color]}",
                    f"{rom[jname]:.0f}", "°",
                ))
    if kpi_items:
        children.append(html.Div(kpi_items[:6], className="kpi-grid"))

    # ── Asymmetry table ────────────────────────────────────────────────────
    asym_rows = []
    for color in ("azul", "rojo"):
        d = biomech.get(color) or {}
        if not d or d.get("error"):
            continue
        for group, val in (d.get("asymmetry") or {}).items():
            quality = ("Simétrico" if val < 15
                       else ("Leve asim." if val < 30 else "Asimétrico"))
            asym_rows.append(html.Tr([
                html.Td(labels[color], style={"color": colors[color], "fontWeight": "600"}),
                html.Td(group.capitalize()),
                html.Td(f"{val:.0f}°"),
                html.Td(quality),
            ]))
    if asym_rows:
        children.append(html.Div([
            html.P("Asimetría bilateral", style={
                "fontSize": "11px", "fontWeight": "600",
                "margin": "8px 0 4px",
            }),
            html.Table(
                [html.Thead(html.Tr([
                    html.Th("Atleta", style={"textAlign": "left"}),
                    html.Th("Articulación", style={"textAlign": "left"}),
                    html.Th("|ΔROM|", style={"textAlign": "right"}),
                    html.Th("Valoración", style={"textAlign": "left"}),
                ])),
                 html.Tbody(asym_rows)],
                style={"fontSize": "11px", "width": "100%", "borderCollapse": "collapse"},
            ),
        ]))

    # ── ROM bar chart ──────────────────────────────────────────────────────
    bar_traces = []
    for color in ("azul", "rojo"):
        d = biomech.get(color) or {}
        if not d or d.get("error"):
            continue
        rom = d.get("rom") or {}
        x_labels = [joint_labels[j] for j in joint_order if j in rom]
        y_vals   = [rom[j]          for j in joint_order if j in rom]
        if x_labels:
            bar_traces.append(go.Bar(
                name=labels[color], x=x_labels, y=y_vals,
                marker_color=colors[color], opacity=0.85,
            ))
    if bar_traces:
        fig_rom = go.Figure(bar_traces)
        fig_rom = apply_chart_style(fig_rom, height=200)
        fig_rom.update_layout(
            title={"text": "Rango de movimiento articular (°)", "font": {"size": 12}},
            yaxis_title="ROM (°)", barmode="group",
            legend={"orientation": "h", "y": 1.15, "font": {"size": 10}},
        )
        children.append(dcc.Graph(figure=fig_rom, config={"displayModeBar": False},
                                  style={"marginTop": "10px"}))
        children.append(_graph_interpretation([
            "Cada barra resume el rango de movimiento articular detectado por YOLO para rojo y azul.",
            "Diferencias grandes entre lados pueden indicar pierna dominante, guardia, o ruido por oclusión.",
            "ROM alto no equivale automáticamente a mejor técnica: debe revisarse junto al momento del video.",
        ]))

    if not children:
        return []

    return [html.Details(
        className="card collapsible-card",
        open=False,
        children=[
            html.Summary(className="collapsible-card__summary", children=[
                html.Div(className="collapsible-card__head", children=[
                    html.Span("Biomecánica YOLO", className="card-title"),
                    html.Span("ROM · asimetría · vel. angular", className="text-muted"),
                ]),
                html.Span("⌄", className="collapsible-card__chevron"),
            ]),
            html.Div(className="collapsible-card__body", children=children),
        ],
    )]


def kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int):
    return [
        kpi_card("Acciones detectadas", f"{n_hits}"),
        kpi_card("Ritmo de acción", f"{hits_per_min:.1f}"),
        kpi_card("Explosividad media", f"{mean_int:.2f}", " g"),
        kpi_card("Pico de explosividad", f"{max_int:.2f}", " g"),
    ]


# ========= Clase principal =========

class SignalsView:
    """Vista de análisis de sesión: ECG/HRV e IMU en tiempo real."""

    _callbacks_registered = False

    # Soporta distintos nombres por si en DB guardaste códigos distintos
    _SENSOR_ALIASES = {
        "ECG": {"ECG"},
        "IMU": {
            "IMU", "IMU_ARM", "IMU_LEG", "IMU_HEAD",
            "IMU_WRIST", "IMU_FOOT", "IMU_GLOVE", "IMU_ANKLE",
        },
    }

    def __init__(self, app: dash.Dash, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module
        if not SignalsView._callbacks_registered:
            self._register_callbacks()
            SignalsView._callbacks_registered = True

    def _safe_int(self, x):
        try:
            return int(x)
        except Exception:
            return None

    def _sparse_marks(self, max_s: float) -> dict:
        """Genera marcas legibles para el RangeSlider (evita solape de números)."""
        try:
            ms = float(max_s)
        except Exception:
            ms = 10.0
        if ms <= 0:
            ms = 10.0

        if ms <= 20:
            step = 1
        elif ms <= 90:
            step = 5
        elif ms <= 300:
            step = 15
        elif ms <= 900:
            step = 30
        else:
            step = 60

        end = int(round(ms))
        marks = {0: "0"}
        for v in range(step, end, step):
            marks[v] = str(v)
        marks[end] = str(end)
        return marks

    def _has_sensor(self, user_id: int, sensor_key: str) -> bool:
        try:
            codes = set(self.db.get_user_sensors(int(user_id)) or [])
        except Exception:
            codes = set()
        aliases = self._SENSOR_ALIASES.get(sensor_key, {sensor_key})
        return len(codes.intersection(aliases)) > 0

    def _normalize_sport(self, sport):
        s = (sport or "").strip().lower()
        if s in {"taekwondo", "tkd"}:
            return "taekwondo"
        if s in {"boxeo", "boxing", "box"}:
            return "boxeo"
        return "general"

    def _imu_profile(self, sport):
        sport_key = self._normalize_sport(sport)
        if sport_key == "taekwondo":
            return {
                "sport_key": "taekwondo",
                "headline": "IMU adaptado a taekwondo",
                "subline": "Prioriza pateo, desplazamiento y lectura del tronco para sesiones de combate.",
                "tabs": [
                    {"label": "Patadas (IMU pierna)", "value": "imu-leg"},
                    {"label": "Desplazamiento / guardia (IMU cintura)", "value": "imu-arm"},
                    {"label": "Tronco / impacto (IMU peto)", "value": "imu-head"},
                ],
                "format_help": [
                    "Formato recomendado: primera fila con cabeceras ",
                    html.Code("time,ax,ay,az"),
                    ". En taekwondo el IMU visible se adapta al trabajo real del deporte: ",
                    "pierna = pateo y explosividad; ",
                    "cintura = desplazamiento y control de guardia; ",
                    "tronco/peto = lectura de impacto y estabilidad.",
                ],
                "title_prefixes": {
                    "imu-leg": "Patadas detectadas",
                    "imu-arm": "Desplazamiento y guardia",
                    "imu-head": "Tronco / impacto en peto",
                },
                "message_suffixes": {
                    "imu-leg": "Esta vista está orientada al trabajo de pierna: pateo, explosividad y ritmo de ataque.",
                    "imu-arm": "Esta vista está orientada al desplazamiento, al control de base y a la guardia.",
                    "imu-head": "Esta vista está orientada a la estabilidad del tronco y a los impactos al peto.",
                },
                "chart_mode": {
                    "imu-leg":  "events",
                    "imu-arm":  "density",
                    "imu-head": "events",
                },
            }
        if sport_key == "boxeo":
            return {
                "sport_key": "boxeo",
                "headline": "IMU adaptado a boxeo",
                "subline": "Prioriza tren superior, guardia e impactos propios del trabajo de manos.",
                "tabs": [
                    {"label": "Golpes de mano (IMU guante)", "value": "imu-arm"},
                    {"label": "Guardia / tronco (IMU pecho)", "value": "imu-head"},
                ],
                "format_help": [
                    "Formato recomendado: primera fila con cabeceras ",
                    html.Code("time,ax,ay,az"),
                    ". En boxeo el IMU visible se centra en el trabajo real del deporte: ",
                    "guante = ritmo e intensidad de golpeo; ",
                    "tronco/pecho = guardia, carga y estabilidad del tren superior.",
                ],
                "title_prefixes": {
                    "imu-arm": "Golpes de mano",
                    "imu-head": "Guardia y tronco",
                    "imu-leg": "Desplazamiento",
                },
                "message_suffixes": {
                    "imu-arm": "Esta vista está orientada al volumen, al ritmo y a la intensidad de manos.",
                    "imu-head": "Esta vista está orientada a la guardia, la carga y la estabilidad del tren superior.",
                    "imu-leg": "Esta vista queda como apoyo secundario para desplazamiento.",
                },
                "chart_mode": {
                    "imu-arm":  "events",
                    "imu-head": "events",
                    "imu-leg":  "density",
                },
            }
        return {
            "sport_key": "general",
            "headline": "IMU del deportista",
            "subline": "La vista visible del IMU se adapta al deporte seleccionado cuando aplica.",
            "tabs": [
                {"label": "Golpes brazo (IMU guante)", "value": "imu-arm"},
                {"label": "Patadas (IMU pierna)", "value": "imu-leg"},
                {"label": "Impactos cabeza (IMU casco)", "value": "imu-head"},
            ],
            "format_help": [
                "Formato recomendado: primera fila con cabeceras ",
                html.Code("time,ax,ay,az"),
                ". La lectura visible del IMU cambia según el deporte y la zona priorizada.",
            ],
            "title_prefixes": {
                "imu-arm": "Golpes de brazo",
                "imu-leg": "Patadas detectadas",
                "imu-head": "Impactos en la cabeza",
            },
            "message_suffixes": {
                "imu-arm": "Esta vista está orientada al trabajo del tren superior.",
                "imu-leg": "Esta vista está orientada al trabajo del tren inferior.",
                "imu-head": "Esta vista está orientada a impactos o estabilidad de la cabeza.",
            },
        }

    # ---------- Layout ----------

    def layout(self) -> html.Div:
        """Top-level layout: Video Replay tab + Signal Analysis tab."""
        uid   = session.get("user_id")
        role  = str(session.get("role") or "")
        sport = str(session.get("sport") or "")

        if not uid:
            return html.Div(
                html.P("Inicia sesión para acceder al análisis.", className="text-muted"),
                className="page-content",
            )

        sport_label = sport.title() if sport else "Combate"

        def _tab(label, value, content):
            return dcc.Tab(
                label=label, value=value,
                className="combatiq-tab",
                selected_className="combatiq-tab--active",
                children=[html.Div(content, style={"paddingTop": "16px"})],
            )

        page_head = html.Div(className="page-head", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport_label, className="session-pill"),
                html.Span(
                    "Coach · Señales" if role == "coach" else "Atleta · Señales",
                    className="session-pill session-pill--muted",
                ),
            ]),
            html.H2("Señales ECG / IMU"),
            html.P(
                "Replay de combate con anotaciones y señales ECG/IMU explicadas.",
                className="text-muted",
            ),
        ])

        return html.Div([
            dcc.Location(id="ecg-url", refresh=False),
            page_head,
            html.Div(className="ecg-divider ecg-divider--spaced"),
            dcc.Tabs(
                id="ecg-main-tabs",
                value="tab-signals",
                className="combatiq-tabs",
                children=[
                    _tab("Replay de combate",    "tab-replay",   self._layout_replay()),
                    _tab("Señales ECG / IMU",   "tab-signals",  [self._layout_signals()]),
                    _tab("Análisis Biomecánico", "tab-biomech",  self._layout_biomech()),
                ],
            ),
            # lazy-load trigger: fired once on mount, populates sessions + KPIs
            dcc.Store(id="signals-lazy-trigger", data={"uid": uid, "role": role}),
            # pose analysis results
            dcc.Store(id="pose-results",         data=None, storage_type="session"),
            dcc.Store(id="pose-mediapipe-store", data=None),
            dcc.Store(id="pose-speed-store",     data=None),
            dcc.Store(id="pose-ai-note-store",   data=None),  # Step 4: AI duel insight async
            # stores and helpers for video replay (always in DOM)
            dcc.Store(id="replay-video-store",   data=None),
            dcc.Store(id="replay-upload-result", data=None),
            dcc.Store(id="replay-annotations",   data=[]),
            dcc.Store(id="replay-auto-events",   data=[]),
            dcc.Download(id="dl-replay-ecg-csv"),
            dcc.Download(id="dl-replay-imu-csv"),
            dcc.Store(id="replay-seek-target",   data=None),
            html.Div(id="replay-seek-dummy",     style={"display": "none"}),
            # sensor replay stores
            dcc.Store(id="replay-sensor-ecg",   data=[]),
            dcc.Store(id="replay-sensor-imu",   data=[]),
            dcc.Store(id="replay-video-time",   data=None),
            dcc.Store(id="replay-time-offset",  data=0.0),
            dcc.Interval(id="replay-time-poll", interval=500, disabled=True),
            dcc.Store(id="replay-visible-count", data=0),
            dcc.Store(id="replay-sensor-collapsed", data=False),
            # simulated ECG/IMU from pose analysis (populated when duel result is available)
            dcc.Store(id="replay-sim-ecg",        data=[]),
            dcc.Store(id="replay-sim-imu",        data=[]),
            dcc.Store(id="replay-sim-rest-bands", data=[]),
            dcc.Store(id="replay-ai-store",       data=None),
            dcc.Store(id="replay-vision-event",   data=None),
            dcc.Store(id="replay-vision-events",  data=[]),
        ], className="page-content")

    def _layout_replay(self) -> list:
        """Video Replay tab content."""
        # Session options are populated lazily via signals-lazy-trigger callback
        session_opts = []

        session_bar = html.Div(
            className="card",
            style={"marginBottom": "0", "position": "relative", "zIndex": "20"},
            children=[
                html.Div(
                    style={"display": "grid",
                           "gridTemplateColumns": "1fr 1fr",
                           "gap": "12px",
                           "alignItems": "end"},
                    children=[
                        html.Div([
                            html.Label("Vincular sesión (opcional)", className="auth-label"),
                            dcc.Dropdown(
                                id="replay-session-select",
                                options=session_opts,
                                placeholder="Elige una sesión para contexto…",
                                clearable=True,
                                className="dash-dropdown",
                            ),
                        ]),
                        html.Div(
                            id="replay-session-info",
                            style={"fontSize": "12px", "color": "var(--muted)",
                                   "paddingTop": "4px"},
                            children=html.P("—", className="text-muted",
                                            style={"margin": "0"}),
                        ),
                    ],
                ),
                # ── Rename session row (visible when a session is selected) ──
                html.Div(
                    id="replay-rename-row",
                    style={"display": "none", "marginTop": "10px",
                           "display": "flex", "gap": "8px", "alignItems": "center",
                           "flexWrap": "wrap"},
                    children=[
                        html.Label("Nombre de sesión:", className="auth-label",
                                   style={"margin": "0", "whiteSpace": "nowrap",
                                          "fontSize": "11px", "color": "var(--muted)"}),
                        dcc.Input(
                            id="replay-rename-input",
                            type="text",
                            placeholder="Ej: Combate semifinal — mayo 2026",
                            debounce=False,
                            maxLength=150,
                            style={"flex": "1", "minWidth": "180px",
                                   "background": "var(--card)", "color": "var(--ink)",
                                   "border": "1px solid var(--line)", "borderRadius": "6px",
                                   "padding": "5px 10px", "fontSize": "12px"},
                        ),
                        html.Button(
                            "Guardar nombre",
                            id="btn-replay-rename",
                            n_clicks=0,
                            className="btn btn-ghost",
                            style={"fontSize": "11px", "padding": "5px 12px",
                                   "whiteSpace": "nowrap"},
                        ),
                        html.Span(id="replay-rename-msg",
                                  style={"fontSize": "11px", "color": "var(--neon)",
                                         "fontWeight": "600"}),
                    ],
                ),
            ],
        )

        _ANN_TYPES = [
            {"label": "Ataque",           "value": "attack"},
            {"label": "Defensa",          "value": "defense"},
            {"label": "Técnica correcta", "value": "good_tech"},
            {"label": "Error técnico",    "value": "tech_error"},
            {"label": "Lesión / dolor",   "value": "injury"},
            {"label": "General",          "value": "general"},
        ]
        # ── Sensor panel (right column, collapsible) ─────────────────────
        _btn_sm = {
            "padding": "2px 10px", "fontSize": "14px",
            "background": "var(--card)", "border": "1px solid var(--line)",
            "borderRadius": "6px", "cursor": "pointer", "color": "var(--ink)",
        }
        sensor_panel = html.Div(
            id="replay-sensor-panel",
            style={},
            children=[
                # ── Panel header (always visible) ─────────────────────────
                html.Div(className="card", style={"marginBottom": "10px",
                                                   "padding": "10px 14px"}, children=[
                    html.Div(
                        style={"display": "flex", "justifyContent": "space-between",
                               "alignItems": "center"},
                        children=[
                            html.Div([
                                html.H4("Sensores ECG / IMU", className="card-title",
                                        style={"margin": "0 0 4px", "fontSize": "13px"}),
                                html.Div(id="replay-sensor-status"),
                            ]),
                            html.Div(style={"display": "flex", "alignItems": "center",
                                            "gap": "8px"}, children=[
                                html.Span(id="replay-sensor-time-badge",
                                          style={"fontSize": "12px", "color": "var(--neon)",
                                                 "fontFamily": "monospace",
                                                 "fontWeight": "700"}),
                                html.Button(
                                    "▼ Ocultar",
                                    id="btn-sensor-toggle",
                                    n_clicks=0,
                                    className="btn btn-ghost",
                                    style={"fontSize": "11px", "padding": "3px 10px",
                                           "whiteSpace": "nowrap"},
                                ),
                            ]),
                        ],
                    ),
                ]),

                # ── Collapsible body ──────────────────────────────────────
                html.Div(id="replay-sensor-body", children=[
                    html.Div(className="card", style={"marginBottom": "10px"}, children=[
                        html.H4("ECG en tiempo de combate", className="card-title",
                                style={"margin": "0 0 8px", "fontSize": "13px"}),
                        dcc.Graph(
                            id="replay-sensor-ecg-chart",
                            config={"displayModeBar": False},
                            style={"height": "180px"},
                            figure=placeholder_figure(180),
                        ),
                        _graph_interpretation([
                            "Esta gráfica sincroniza la señal cardiovascular con el tiempo del video.",
                            "La línea/cursor vertical marca el segundo exacto que estás viendo en el replay.",
                            "Úsala para ubicar si un intercambio coincide con mayor demanda cardiovascular.",
                        ]),
                        html.Button(
                            "Descargar ECG (Excel)",
                            id="btn-replay-dl-ecg",
                            n_clicks=0,
                            className="btn btn-ghost",
                            style={"marginTop": "6px", "fontSize": "11px",
                                   "padding": "4px 10px", "width": "100%"},
                        ),
                    ]),
                    html.Div(className="card", style={"marginBottom": "10px"}, children=[
                        html.H4("IMU — Impactos", className="card-title",
                                style={"marginBottom": "8px", "fontSize": "13px"}),
                        dcc.Graph(
                            id="replay-sensor-imu-chart",
                            config={"displayModeBar": False},
                            style={"height": "160px"},
                            figure=placeholder_figure(160),
                        ),
                        _graph_interpretation([
                            "Cada pico o línea vertical representa un evento IMU asociado al combate.",
                            "La sincronización con el video ayuda a confirmar si el evento fue ataque, defensa, contacto o desplazamiento.",
                            "La intensidad orienta la revisión, pero la técnica se confirma mirando el video.",
                        ]),
                        html.Button(
                            "Descargar IMU (Excel)",
                            id="btn-replay-dl-imu",
                            n_clicks=0,
                            className="btn btn-ghost",
                            style={"marginTop": "6px", "fontSize": "11px",
                                   "padding": "4px 10px", "width": "100%"},
                        ),
                    ]),
                    html.Div(className="card", style={"padding": "12px 14px"}, children=[
                        html.P("Línea vertical = tiempo actual del video.",
                               style={"margin": "0 0 8px", "fontSize": "11px",
                                      "color": "var(--muted)"}),
                        html.Div(
                            style={"display": "flex", "alignItems": "center",
                                   "gap": "8px", "flexWrap": "wrap"},
                            children=[
                                html.Span("Sincronización",
                                          style={"fontSize": "11px", "color": "var(--muted)",
                                                 "whiteSpace": "nowrap"}),
                                html.Button("−", id="btn-offset-minus", n_clicks=0,
                                            style=_btn_sm),
                                dcc.Input(
                                    id="replay-offset-adj",
                                    type="number", value=0, step=1, debounce=False,
                                    style={"width": "64px", "textAlign": "center",
                                           "background": "var(--card)",
                                           "border": "1px solid var(--line)",
                                           "borderRadius": "6px", "color": "var(--ink)",
                                           "padding": "3px 6px", "fontSize": "12px"},
                                ),
                                html.Button("+", id="btn-offset-plus", n_clicks=0,
                                            style=_btn_sm),
                                html.Span("s", style={"fontSize": "11px",
                                                      "color": "var(--muted)"}),
                                html.Button("↺", id="btn-offset-reset", n_clicks=0,
                                            title="Restablecer sincronización",
                                            style={**_btn_sm, "background": "transparent",
                                                   "color": "var(--neon)"}),
                            ],
                        ),
                    ]),
                    # ── Simulated ECG/IMU from pose analysis ──────────────────
                    html.Div(
                        id="replay-sim-section",
                        style={"display": "none"},
                        children=[
                            html.Div(className="card", style={"marginTop": "10px",
                                                               "marginBottom": "10px"}, children=[
                                html.Div(style={"display": "flex", "justifyContent": "space-between",
                                                "alignItems": "center", "marginBottom": "8px"}, children=[
                                    html.H4("FC estimada — análisis de video",
                                            className="card-title",
                                            style={"margin": "0", "fontSize": "13px"}),
                                    html.Span("⚠ SIMULADO",
                                              style={"fontSize": "10px", "fontStyle": "italic",
                                                     "color": "var(--text-muted)", "fontWeight": "600"}),
                                ]),
                                dcc.Graph(
                                    id="replay-sim-hr-chart",
                                    config={"displayModeBar": False},
                                    style={"height": "160px"},
                                    figure=placeholder_figure(160),
                                ),
                                _graph_interpretation([
                                    "Esta lectura se estima desde movimiento; no sustituye una banda ECG real.",
                                    "La forma de la curva ayuda a revisar demanda estimada durante rounds y descansos.",
                                    "Úsala como pista para sincronizar video y esfuerzo, no como valor clínico.",
                                ]),
                            ]),
                            html.Div(className="card", style={"marginBottom": "10px"}, children=[
                                html.Div(style={"display": "flex", "justifyContent": "space-between",
                                                "alignItems": "center", "marginBottom": "8px"}, children=[
                                    html.H4("Impactos estimados — análisis de video",
                                            className="card-title",
                                            style={"margin": "0", "fontSize": "13px"}),
                                    html.Span("⚠ SIMULADO",
                                              style={"fontSize": "10px", "fontStyle": "italic",
                                                     "color": "var(--text-muted)", "fontWeight": "600"}),
                                ]),
                                dcc.Graph(
                                    id="replay-sim-imu-chart",
                                    config={"displayModeBar": False},
                                    style={"height": "140px"},
                                    figure=placeholder_figure(140),
                                ),
                                _graph_interpretation([
                                    "Los eventos se infieren desde movimiento detectado en video.",
                                    "La gráfica ayuda a localizar acciones probables para revisar en replay.",
                                    "Cuando haya sensores reales, esta lectura debe compararse contra IMU real.",
                                ]),
                            ]),
                        ],
                    ),
                ]),
            ],
        )

        # ── Main two-column grid ──────────────────────────────────────────
        left_col = html.Div(
            style={"display": "flex", "flexDirection": "column", "gap": "12px"},
            children=[
                # Upload zone (hidden when video is loaded)
                html.Div(id="replay-upload-zone", children=[
                    html.Div(className="card", children=[
                        html.H4("Cargar video de sesión", className="card-title"),
                        html.P(
                            "Sube el video del combate o sesión para revisar momentos clave "
                            "y agregar anotaciones técnicas.",
                            className="text-muted",
                            style={"marginBottom": "14px"},
                        ),
                        html.Div(
                            id="replay-upload-dropzone",
                            className="upload-zone",
                            style={"cursor": "pointer", "textAlign": "center", "padding": "44px 20px"},
                            children=[
                                html.Div("📹", style={"fontSize": "36px", "marginBottom": "8px"}),
                                html.Div("Arrastra un video o haz clic para seleccionar",
                                         style={"fontSize": "14px", "color": "var(--muted)"}),
                                html.Div("MP4 · MOV · AVI · MKV · WebM · máx. 300 MB",
                                         style={"fontSize": "12px", "color": "var(--muted)",
                                                "marginTop": "6px"}),
                            ],
                        ),
                        html.Div(id="replay-upload-err", className="text-muted",
                                 style={"marginTop": "8px", "fontSize": "12px"}),
                    ]),
                ]),

                # Video player (hidden until video loaded)
                html.Div(id="replay-player-card", style={"display": "none"}, children=[
                    html.Div(className="card", children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between",
                                   "alignItems": "center", "marginBottom": "12px"},
                            children=[
                                html.H4("Replay", className="card-title",
                                        style={"margin": "0"}),
                                html.Button("✕ Cambiar video", id="btn-replay-clear",
                                            n_clicks=0, className="btn",
                                            style={"fontSize": "12px", "padding": "5px 12px"}),
                            ],
                        ),
                        html.Video(
                            id="replay-player",
                            controls=True,
                            style={"width": "100%", "borderRadius": "10px",
                                   "background": "#000", "maxHeight": "400px",
                                   "display": "block"},
                        ),
                    ]),
                ]),

                # Live event timeline (full width, synchronized with video)
                html.Div(className="card", style={"marginTop": "12px"}, children=[
                    html.Div(
                        style={"display": "flex", "justifyContent": "space-between",
                               "alignItems": "center", "marginBottom": "10px"},
                        children=[
                            html.Div([
                                html.H4("Eventos detectados", className="card-title",
                                        style={"margin": "0"}),
                                html.P(
                                    "El evento activo se resalta en tiempo real "
                                    "· haz clic para saltar a ese momento.",
                                    className="text-muted",
                                    style={"margin": "4px 0 0", "fontSize": "12px"},
                                ),
                            ]),
                            html.Button(
                                "Analizar IA video",
                                id="btn-replay-scan-vision",
                                n_clicks=0,
                                className="btn btn-ghost",
                                style={"fontSize": "11px", "padding": "4px 12px"},
                            ),
                            html.Button(
                                "Limpiar",
                                id="btn-replay-clear-ann",
                                n_clicks=0,
                                className="btn btn-ghost",
                                style={"fontSize": "11px", "padding": "4px 12px"},
                            ),
                        ],
                    ),
                    html.Div(
                        id="replay-annotations-list",
                        className="replay-ann-list",
                        style={"maxHeight": "220px", "overflowY": "auto",
                               "scrollBehavior": "smooth"},
                        children=[html.P("Selecciona una sesión para ver los eventos.",
                                         className="text-muted")],
                    ),
                    dcc.Loading(
                        type="dot",
                        color="var(--neon)",
                        style={"marginTop": "4px"},
                        children=html.Div(id="replay-vision-scan-status"),
                    ),
                    dcc.Loading(
                        type="dot",
                        color="var(--neon)",
                        style={"marginTop": "8px"},
                        children=html.Div(
                            id="replay-vision-panel",
                            style={"marginTop": "8px"},
                        ),
                    ),
                ]),
            ],
        )

        return [
            # ── Session context bar ───────────────────────────────────────
            session_bar,

            # ── Two-column layout ─────────────────────────────────────────
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "55fr 45fr",
                    "gap": "16px",
                    "marginTop": "12px",
                    "alignItems": "start",
                    "position": "relative",
                    "zIndex": "1",
                },
                children=[
                    left_col,
                    sensor_panel,
                ],
            ),

            # ── AI combat analysis panel (full width) ─────────────────────
            html.Div(
                className="card",
                style={
                    "marginTop": "16px",
                    "display": "flex",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "gap": "12px",
                    "flexWrap": "wrap",
                },
                children=[
                    html.Div([
                        html.H4("Lectura IA de combate", className="card-title",
                                style={"margin": "0"}),
                        html.P(
                            "Genera una síntesis deportiva desde ECG, IMU y rounds cuando quieras revisarla.",
                            className="text-muted",
                            style={"margin": "4px 0 0", "fontSize": "12px"},
                        ),
                    ]),
                    html.Button(
                        "Generar lectura IA",
                        id="btn-replay-ai-analysis",
                        n_clicks=0,
                        className="btn btn-ghost btn-sm",
                    ),
                ],
            ),
            dcc.Loading(
                type="circle",
                color="var(--neon)",
                style={"marginTop": "16px"},
                children=html.Div(
                    id="replay-ai-panel",
                    children=html.P(
                        "Selecciona una sesión Combat Monitor para generar la lectura IA.",
                        className="text-muted",
                        style={"padding": "12px", "fontSize": "12px"},
                    ),
                ),
            ),
        ]

    # ── AI panel rendering helpers ────────────────────────────────────────────

    @staticmethod
    def _ai_badge(label: str, color: str) -> html.Span:
        return html.Span(label, style={
            "fontSize": "9px", "fontWeight": "700", "color": color,
            "border": f"1px solid {color}", "borderRadius": "4px",
            "padding": "2px 5px", "whiteSpace": "nowrap",
            "minWidth": "30px", "textAlign": "center", "flexShrink": "0",
        })

    def _render_finding(self, f: dict) -> html.Div:
        sev = f.get("severity", "observar")
        color = {"positivo": "#27c98f", "observar": "#8fa3bf",
                 "corregir": "#f59e0b", "urgente":  "#e45a5a"}.get(sev, "#8fa3bf")
        label = {"positivo": "OK", "observar": "OBS",
                 "corregir": "FIX", "urgente":  "URG"}.get(sev, sev[:3].upper())
        children = [
            html.Span(f.get("finding", ""),
                      style={"fontSize": "12px", "color": "var(--ink)", "lineHeight": "1.4"}),
            html.Div(f.get("evidence", ""),
                     style={"fontSize": "11px", "color": "var(--muted)", "marginTop": "2px"}),
        ]
        if f.get("drill"):
            children.append(html.Div(
                f"▶ {f['drill']}",
                style={"fontSize": "11px", "color": "var(--neon)", "marginTop": "3px"},
            ))
        return html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                                "alignItems": "flex-start"}, children=[
            self._ai_badge(label, color),
            html.Div(children, style={"flex": "1"}),
        ])

    def _render_risk(self, r: dict) -> html.Div:
        sev = r.get("severity", "medio")
        color = {"alto": "#e45a5a", "medio": "#f59e0b", "bajo": "#8fa3bf"}.get(sev, "#8fa3bf")
        label = {"alto": "ALTO", "medio": "MED", "bajo": "BAJO"}.get(sev, sev[:4].upper())
        return html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "8px",
                                "alignItems": "flex-start"}, children=[
            self._ai_badge(label, color),
            html.Div([
                html.Span(r.get("risk_type", "").replace("_", " ").title(),
                          style={"fontSize": "12px", "color": "var(--ink)", "fontWeight": "600"}),
                html.Div(r.get("value", ""),
                         style={"fontSize": "11px", "color": "var(--muted)", "marginTop": "1px"}),
                html.Div(f"→ {r.get('recommendation', '')}",
                         style={"fontSize": "11px", "color": "var(--ink)", "marginTop": "3px"}),
            ], style={"flex": "1"}),
        ])

    def _render_recommendation(self, r: dict) -> html.Div:
        pri = r.get("priority", "?")
        tf  = r.get("timeframe", "")
        tf_color = {"48h": "#27c98f", "semana": "var(--neon)", "mes": "#8fa3bf"}.get(tf, "var(--muted)")
        sets_str = f"  ·  {r['sets_reps']}" if r.get("sets_reps") else ""
        return html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                                "alignItems": "flex-start"}, children=[
            html.Span(f"P{pri}", style={
                "fontSize": "11px", "fontWeight": "800", "color": "var(--neon)",
                "minWidth": "22px", "textAlign": "center", "flexShrink": "0",
                "marginTop": "1px",
            }),
            html.Div([
                html.Div(style={"display": "flex", "gap": "6px", "alignItems": "center",
                                "flexWrap": "wrap"}, children=[
                    html.Span(r.get("drill", ""),
                              style={"fontSize": "12px", "color": "var(--ink)", "fontWeight": "500"}),
                    html.Span(tf + sets_str,
                              style={"fontSize": "10px", "color": tf_color, "fontWeight": "600"}),
                ]),
                html.Div(r.get("rationale", ""),
                         style={"fontSize": "11px", "color": "var(--muted)", "marginTop": "2px"}),
            ], style={"flex": "1"}),
        ])

    def render_ai_panel_content(self, result: dict) -> html.Div:
        """Convierte el resultado de analyze_combat_session en componentes Dash."""
        findings = result.get("findings", [])
        risks    = result.get("risks", [])
        recs     = result.get("recommendations", [])
        narrative = result.get("narrative", "")

        col_style = {
            "background": "var(--card)", "borderRadius": "10px",
            "padding": "14px 16px", "flex": "1", "minWidth": "0",
        }
        title_style = {
            "fontSize": "11px", "fontWeight": "700", "color": "var(--muted)",
            "textTransform": "uppercase", "letterSpacing": "0.05em",
            "marginBottom": "10px",
        }

        findings_col = html.Div(style=col_style, children=[
            html.Div(f"Hallazgos  ({len(findings)})", style=title_style),
            *[self._render_finding(f) for f in findings],
        ])
        risks_col = html.Div(style=col_style, children=[
            html.Div(f"Riesgos  ({len(risks)})", style=title_style),
            *([self._render_risk(r) for r in risks] if risks else [
                html.P("Sin señales de riesgo.", style={"fontSize": "12px",
                                                        "color": "var(--muted)"})
            ]),
        ])
        recs_col = html.Div(style=col_style, children=[
            html.Div(f"Plan de acción  ({len(recs)})", style=title_style),
            *[self._render_recommendation(r) for r in recs],
        ])

        header = html.Div(style={"display": "flex", "justifyContent": "space-between",
                                  "alignItems": "center", "marginBottom": "12px"}, children=[
            html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                html.Span("Análisis de combate",
                          style={"fontSize": "14px", "fontWeight": "700",
                                 "color": "var(--ink)"}),
                html.Span("Claude Opus 4.7",
                          style={"fontSize": "10px", "color": "var(--neon)",
                                 "border": "1px solid var(--neon)", "borderRadius": "4px",
                                 "padding": "1px 6px", "fontWeight": "600"}),
            ]),
        ])

        grid = html.Div(style={"display": "flex", "gap": "12px", "flexWrap": "wrap",
                                "marginBottom": "12px"}, children=[
            findings_col, risks_col, recs_col,
        ])

        narrative_block = html.Div(style={
            "background": "var(--card)", "borderRadius": "10px",
            "padding": "14px 16px", "borderLeft": "3px solid var(--neon)",
        }, children=[
            html.Div("Síntesis", style={**title_style, "marginBottom": "6px"}),
            html.P(narrative, style={"fontSize": "12px", "color": "var(--ink)",
                                      "lineHeight": "1.6", "margin": "0"}),
        ]) if narrative else None

        return html.Div(
            className="card",
            style={"marginTop": "4px"},
            children=[header, grid] + ([narrative_block] if narrative_block else []),
        )

    def _layout_biomech(self) -> list:
        """Dedicated Biomechanics tab — pose analysis controls + results."""
        pose_card = html.Div(
            className="card",
            style={"marginTop": "8px"},
            children=[
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between",
                           "alignItems": "center", "marginBottom": "10px"},
                    children=[
                        html.Div([
                            html.H4("Análisis Biomecánico", className="card-title",
                                    style={"margin": "0"}),
                            html.P(
                                "Carga el video en 'Replay de combate', elige objetivo y presiona Analizar. "
                                "Incluye análisis MediaPipe + YOLO velocidades (~3-4 min).",
                                className="text-muted",
                                style={"margin": "4px 0 0", "fontSize": "12px"},
                            ),
                        ]),
                        html.Div(
                            style={"display": "flex", "gap": "8px", "flexWrap": "wrap",
                                   "alignItems": "center"},
                            children=[
                                dcc.Dropdown(
                                    id="pose-target-select",
                                    options=[
                                        {"label": "Auto (mejor pose)", "value": "auto"},
                                        {"label": "Peto rojo", "value": "red"},
                                        {"label": "Peto azul", "value": "blue"},
                                        {"label": "Rojo vs azul", "value": "duel"},
                                        {"label": "Atleta izquierda", "value": "left"},
                                        {"label": "Atleta derecha", "value": "right"},
                                    ],
                                    value="auto",
                                    clearable=False,
                                    persistence=True,
                                    persistence_type="session",
                                    className="dash-dropdown",
                                    style={"minWidth": "190px", "fontSize": "12px"},
                                ),
                                html.Div(
                                    style={"display": "flex", "alignItems": "center",
                                           "gap": "4px"},
                                    children=[
                                        html.Span("Rounds:",
                                                  style={"fontSize": "11px",
                                                         "color": "var(--muted)",
                                                         "whiteSpace": "nowrap"}),
                                        dcc.Input(
                                            id="pose-num-rounds",
                                            type="number",
                                            min=1, max=12, step=1,
                                            placeholder="auto",
                                            debounce=False,
                                            persistence=True,
                                            persistence_type="session",
                                            style={"width": "58px", "textAlign": "center",
                                                   "background": "var(--card)",
                                                   "border": "1px solid var(--line)",
                                                   "borderRadius": "6px",
                                                   "color": "var(--ink)",
                                                   "padding": "4px 6px",
                                                   "fontSize": "12px"},
                                        ),
                                    ],
                                ),
                                html.Button(
                                    "Analizar postura",
                                    id="btn-analyze-pose",
                                    n_clicks=0,
                                    className="btn btn-ghost btn-sm",
                                ),
                                html.Button(
                                    "Descargar lectura (PDF)",
                                    id="btn-dl-pose-report",
                                    n_clicks=0,
                                    className="btn btn-ghost btn-sm",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    id="pose-progress",
                    style={"fontSize": "12px", "color": "var(--neon)",
                           "minHeight": "20px", "marginBottom": "4px"},
                ),
                dcc.Loading(
                    id="pose-loading",
                    type="circle",
                    color="var(--neon)",
                    children=html.Div(
                        id="pose-output",
                        children=html.P(
                            "Carga un video en la pestaña 'Replay de combate', elige objetivo y presiona 'Analizar postura'.",
                            className="text-muted",
                            style={"fontSize": "13px"},
                        ),
                    ),
                ),
                html.Div(id="pose-report-msg", className="export-status"),
                # ── Save session row (shown after analysis completes) ──────
                html.Div(
                    id="pose-save-row",
                    style={"display": "none", "marginTop": "10px",
                           "gap": "8px", "alignItems": "center",
                           "flexWrap": "wrap"},
                    children=[
                        dcc.Input(
                            id="pose-session-name",
                            type="text",
                            placeholder="Nombre de la sesión (opcional)…",
                            debounce=False,
                            maxLength=200,
                            style={"flex": "1", "minWidth": "220px",
                                   "background": "var(--card)",
                                   "border": "1px solid var(--line)",
                                   "borderRadius": "6px",
                                   "color": "var(--ink)",
                                   "padding": "5px 10px",
                                   "fontSize": "12px"},
                        ),
                        html.Button(
                            "Guardar sesión de video",
                            id="btn-save-pose-session",
                            n_clicks=0,
                            className="btn btn-ghost btn-sm",
                        ),
                        html.Span(id="pose-save-msg",
                                  style={"fontSize": "11px", "color": "var(--muted)"}),
                    ],
                ),
                dcc.Download(id="dl-pose-report"),
            ],
        )
        return [pose_card]

    def _layout_signals(self):
        role = (session.get("role") or "no autenticado")
        imu_profile = self._imu_profile(None)

        # ── Selector de deportista ─────────────────────────────────────────────
        if role == "deportista":
            user_selector = html.Div(
                style={"display": "none"},
                children=[dcc.Dropdown(id="ecg-user", options=[], value=None)],
            )
        else:
            user_selector = html.Div(
                style={"flex": "1", "minWidth": "200px"},
                children=[
                    html.Label("Deportista", className="kpi-label",
                               style={"marginBottom": "4px", "display": "block"}),
                    dcc.Dropdown(id="ecg-user", options=[],
                                 placeholder="Selecciona deportista…"),
                ],
            )

        sport_label = str(session.get("sport") or "Combate").title()

        # ── Textos por rol ─────────────────────────────────────────────────────
        if role == "deportista":
            page_title = "Mis señales"
            page_sub   = "Revisa el ECG y movimiento de tus sesiones de combate."
        else:
            page_title = "Señales del equipo"
            page_sub   = "Selecciona un deportista y revisa sus señales de la sesión."

        shell_cls = "analysis-shell coach-shell" if role == "coach" else "analysis-shell"
        return html.Div(className=shell_cls, children=[

            # ── Stores / downloads (siempre en DOM) ───────────────────────────
            dcc.Download(id="dl-ecg-report"),
            dcc.Download(id="dl-imu-data"),
            dcc.Download(id="dl-imu-report"),
            dcc.Store(id="imu-meta", data=None),
            html.Div(id="analysis-view-flow", style={"display": "none"}),
            html.Div(id="analysis-view-ecg",  style={"display": "none"}),
            html.Div(id="analysis-view-imu",  style={"display": "none"}),

            # IDs ocultos requeridos por callbacks
            html.Div(style={"display": "none"}, children=[
                dcc.Upload(id="ecg-upload", children=html.Div(""), multiple=False),
                dcc.Dropdown(id="ecg-file", placeholder=""),
                dcc.Upload(id="imu-upload", children=html.Div(""), multiple=False),
                html.Button(id="btn-imu-analyze", n_clicks=0),
                html.Div(id="signals-sensors-banner"),
                dcc.Dropdown(id="session-type", value="sparring", options=[
                    {"label": "Sparring",              "value": "sparring"},
                    {"label": "Técnica",               "value": "tecnica"},
                    {"label": "Acondicionamiento",     "value": "acondicionamiento"},
                    {"label": "Simulación competitiva","value": "simulacion_competitiva"},
                    {"label": "Evaluación / test",     "value": "evaluacion"},
                    {"label": "Recuperación",          "value": "recuperacion"},
                ]),
                dcc.Dropdown(id="session-goal", value="intensidad", options=[
                    {"label": "Técnica",      "value": "tecnica"},
                    {"label": "Intensidad",   "value": "intensidad"},
                    {"label": "Volumen",      "value": "volumen"},
                    {"label": "Simulación",   "value": "simulacion"},
                    {"label": "Evaluación",   "value": "evaluacion"},
                    {"label": "Recuperación", "value": "recuperacion"},
                ]),
                dcc.Dropdown(id="session-structure", value="rounds", options=[
                    {"label": "Por rounds",  "value": "rounds"},
                    {"label": "Por bloques", "value": "bloques"},
                    {"label": "Libre",       "value": "libre"},
                ]),
                dcc.Input(id="session-rounds",    type="number", value=3),
                dcc.Input(id="session-round-min", type="number", value=2),
                dcc.Input(id="session-rest-sec",  type="number", value=60),
            ]),

            # ── Cabecera ───────────────────────────────────────────────────────
            html.Div(className="page-head", children=[
                html.Div(className="session-pill-row", children=[
                    html.Span(sport_label, className="session-pill"),
                    html.Span(
                        "Coach · Señales" if role == "coach" else "Atleta · Señales",
                        className="session-pill session-pill--muted",
                    ),
                ]),
                html.H2(page_title),
                html.P(page_sub, className="text-muted"),
            ]),

            # ── KPIs del último registro ───────────────────────────────────────
            html.Div(className="kpis session-kpis", children=[
                html.Div(className="kpi", children=[
                    html.Div("Cardio (ECG)",    className="kpi-label"),
                    html.Div("—",               id="kpi-ecg-value",     className="kpi-value"),
                    html.Div("Sin lectura ECG", id="kpi-ecg-sub",       className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Movimiento (IMU)", className="kpi-label"),
                    html.Div("—",                id="kpi-imu-value",    className="kpi-value"),
                    html.Div("Sin lectura IMU",  id="kpi-imu-sub",      className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Bienestar",   className="kpi-label"),
                    html.Div("—",          id="kpi-wellness-value", className="kpi-value"),
                    html.Div("Sin check-in",id="kpi-wellness-sub",  className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ]),
            ]),

            html.Div(className="ecg-divider ecg-divider--spaced"),

            # ── Selector de sesión ─────────────────────────────────────────────
            html.Div(className="card", children=[
                html.H4("Sesión del día", className="card-title"),
                html.Div(
                    style={"display": "flex", "gap": "12px",
                           "flexWrap": "wrap", "alignItems": "flex-end"},
                    children=[
                        user_selector,
                        html.Div(style={"flex": "2", "minWidth": "260px"}, children=[
                            dcc.Dropdown(
                                id="signals-session",
                                options=[],
                                placeholder="Selecciona una sesión…",
                                clearable=True,
                            ),
                        ]),
                        html.Button("Nueva sesión", id="btn-new-session",
                                    className="btn btn-ghost btn-xs", n_clicks=0),
                        html.Button("Cerrar sesión", id="btn-close-session",
                                    className="btn btn-ghost btn-xs", n_clicks=0),
                    ],
                ),
                html.Div(id="session-msg",          className="text-muted",
                         style={"fontSize": "13px", "marginTop": "6px"}),
                html.Div(id="signals-sensors-text", className="text-muted",
                         style={"fontSize": "12px"}),
            ]),

            html.Div(className="ecg-divider ecg-divider--spaced"),

            # ── Señales: ECG + IMU en dos columnas ────────────────────────────
            html.Div(className="signals-two-col", children=[

            # ── Frecuencia cardíaca — ECG ──────────────────────────────────────
            html.Div(className="card", children=[
                html.Div(style={"display": "flex", "alignItems": "center",
                                "gap": "10px", "marginBottom": "8px"}, children=[
                    html.H4("Frecuencia cardíaca — ECG", className="card-title",
                            style={"margin": 0}),
                    html.Div(id="ecg-lock-msg", className="text-muted",
                             style={"fontSize": "12px"}),
                ]),
                html.Div(id="ecg-lock-wrapper", children=[
                    html.Div(id="ecg-msg", className="text-muted",
                             style={"fontSize": "13px", "marginBottom": "6px"}),
                    html.Div(id="ecg-kpis", className="kpis"),
                    dcc.Graph(
                        id="ecg-graph",
                        figure=placeholder_figure(320),
                        config=graph_config(),
                    ),
                    _graph_interpretation([
                        "La curva muestra la señal ECG o cardiovascular cargada para la sesión.",
                        "Las bandas por round ayudan a ubicar cambios de intensidad y recuperación entre segmentos.",
                        "Si la señal es simulada o normalizada, úsala como contexto deportivo; no como ECG clínico diagnóstico.",
                    ]),
                    analysis_fold(
                        "Ajustes de visualización",
                        "Afina la lectura o cambia la ventana visible.",
                        [
                            html.Div(className="filters-bar filters-bar--2", children=[
                                html.Div(className="filter-item", children=[
                                    html.Label("Ventana (s)"),
                                    dcc.Dropdown(
                                        id="ecg-winlen",
                                        options=[
                                            {"label": "5s",   "value": 5},
                                            {"label": "10s",  "value": 10},
                                            {"label": "20s",  "value": 20},
                                            {"label": "30s",  "value": 30},
                                            {"label": "60s",  "value": 60},
                                            {"label": "120s", "value": 120},
                                            {"label": "Todo", "value": -1},
                                        ],
                                        value=10, clearable=False,
                                    ),
                                ]),
                                html.Div(className="filter-item", children=[
                                    html.Label("Calidad de vista"),
                                    dcc.Dropdown(
                                        id="ecg-quality",
                                        options=[
                                            {"label": "Alta",   "value": "high"},
                                            {"label": "Media",  "value": "med"},
                                            {"label": "Ligera", "value": "low"},
                                        ],
                                        value="med", clearable=False,
                                    ),
                                ]),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Rango visible (s)"),
                                dcc.RangeSlider(
                                    id="ecg-window", min=0, max=10, step=0.05,
                                    value=[0, 10], marks={0: "0", 10: "10"},
                                    tooltip={"placement": "bottom"},
                                    updatemode="mouseup", allowCross=False,
                                ),
                                html.Small(
                                    "Este rango solo cambia lo que ves en pantalla; "
                                    "la lectura usa la señal completa.",
                                    className="text-muted",
                                ),
                            ]),
                            dcc.Checklist(
                                options=[{"label": " Mostrar picos R", "value": "r"}],
                                value=[], id="ecg-showr",
                            ),
                            html.Label("Sensibilidad de detección"),
                            dcc.Slider(id="ecg-sens", min=0.3, max=0.95, step=0.05,
                                       value=0.6, tooltip={"placement": "bottom"},
                                       updatemode="mouseup"),
                            html.Label("Suavizado (ms)"),
                            dcc.Slider(id="ecg-smooth", min=20, max=120, step=5,
                                       value=40, tooltip={"placement": "bottom"},
                                       updatemode="mouseup"),
                        ],
                        open=False,
                    ),
                    analysis_fold(
                        "Descargas",
                        "Guarda la señal o un informe con la lectura explicada.",
                        [
                            html.Div(className="export-actions", children=[
                                html.Button("Descargar PNG", id="btn-dl-png",
                                            className="btn btn-primary"),
                                html.Button("Descargar datos (Excel)", id="btn-dl-peaks",
                                            className="btn btn-ghost"),
                                html.Button("Descargar informe (PDF)",
                                            id="btn-dl-ecg-report",
                                            className="btn btn-ghost"),
                            ]),
                            html.Div(id="ecg-export-msg", className="export-status"),
                        ],
                        open=False,
                    ),
                ]),
            ]),

            # ── Movimiento — IMU ───────────────────────────────────────────────
            html.Div(className="card", children=[
                html.Div(style={"display": "flex", "alignItems": "center",
                                "gap": "10px", "marginBottom": "8px"}, children=[
                    html.H4("Movimiento — IMU", className="card-title",
                            style={"margin": 0}),
                    html.Div(id="imu-lock-msg", className="text-muted",
                             style={"fontSize": "12px"}),
                ]),
                html.Div(id="imu-lock-wrapper", children=[
                    html.Div(id="imu-sport-banner", className="muted sport-banner",
                             children=imu_profile["headline"]),
                    dcc.RadioItems(
                        id="imu-tabs",
                        options=[{"label": tab["label"], "value": tab["value"]}
                                 for tab in imu_profile["tabs"]],
                        value=(imu_profile["tabs"][0]["value"]
                               if imu_profile["tabs"] else "imu-arm"),
                        className="imu-mode-pills",
                        inputStyle={
                            "position": "absolute",
                            "opacity": "0",
                            "width": "0",
                            "height": "0",
                            "margin": "0",
                        },
                    ),
                    html.Div(id="imu-msg", className="text-muted",
                             style={"fontSize": "13px", "marginTop": "8px",
                                    "marginBottom": "6px"}),
                    html.Div(id="imu-kpis", className="kpis"),
                    dcc.Graph(
                        id="imu-graph",
                        figure=placeholder_figure(320),
                        config=graph_config(),
                    ),
                    _graph_interpretation([
                        "La gráfica IMU muestra intensidad, ritmo o densidad de movimiento según la pestaña seleccionada.",
                        "Los picos o barras sirven para ubicar acciones a revisar, no para confirmar puntos oficiales.",
                        "La lectura gana sentido cuando se compara con video, rounds y contexto del entrenamiento.",
                    ]),
                    analysis_fold(
                        "Ajustes de visualización",
                        "Cambia la ventana visible o afina cómo se muestra la lectura.",
                        [
                            html.Div(className="filters-bar filters-bar--2", children=[
                                html.Div(className="filter-item", children=[
                                    html.Label("Ventana (s)"),
                                    dcc.Dropdown(
                                        id="imu-winlen",
                                        options=[
                                            {"label": "5s",   "value": 5},
                                            {"label": "10s",  "value": 10},
                                            {"label": "20s",  "value": 20},
                                            {"label": "30s",  "value": 30},
                                            {"label": "60s",  "value": 60},
                                            {"label": "120s", "value": 120},
                                            {"label": "Todo", "value": -1},
                                        ],
                                        value=10, clearable=False,
                                    ),
                                ]),
                                html.Div(className="filter-item", children=[
                                    html.Label("Calidad de vista"),
                                    dcc.Dropdown(
                                        id="imu-quality",
                                        options=[
                                            {"label": "Alta",   "value": "high"},
                                            {"label": "Media",  "value": "med"},
                                            {"label": "Ligera", "value": "low"},
                                        ],
                                        value="med", clearable=False,
                                    ),
                                ]),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Rango visible (s)"),
                                dcc.RangeSlider(
                                    id="imu-window", min=0, max=10, step=0.05,
                                    value=[0, 10], marks={0: "0", 10: "10"},
                                    tooltip={"placement": "bottom"},
                                    updatemode="mouseup", allowCross=False,
                                ),
                                html.Small(
                                    "Este rango solo cambia lo que ves en pantalla; "
                                    "la lectura usa la señal completa.",
                                    className="text-muted",
                                ),
                            ]),
                            html.P(id="imu-format-help",
                                   children=imu_profile["format_help"],
                                   className="muted text-hint"),
                        ],
                        open=False,
                    ),
                    analysis_fold(
                        "Descargas",
                        "Guarda los datos o un informe cuando quieras compartir la lectura.",
                        [
                            html.Div(className="export-actions", children=[
                                html.Button("Descargar datos (Excel)",
                                            id="btn-dl-imu-data",
                                            className="btn btn-primary"),
                                html.Button("Descargar informe (PDF)",
                                            id="btn-dl-imu-report",
                                            className="btn btn-ghost"),
                            ]),
                            html.Div(id="imu-export-msg", className="export-status"),
                        ],
                        open=False,
                    ),
                ]),
            ]),

            ]),  # end signals-two-col
        ])

    # ---------- Callbacks ----------

    def _register_callbacks(self):
        app = self.app
        db = self.db

        # ── Lazy-load: populate replay sessions dropdown ──────────────────────
        import re as _re_lazy
        from datetime import datetime as _dt_lazy

        @app.callback(
            Output("replay-session-select", "options"),
            Input("signals-lazy-trigger", "data"),
            prevent_initial_call=False,
        )
        def populate_replay_sessions(trigger):
            if not trigger:
                raise PreventUpdate
            uid = _safe_int(session.get("user_id"))
            role = str(session.get("role") or "")
            if not uid:
                return []
            try:
                if role == "deportista":
                    sessions_raw = db.list_sessions(int(uid), limit=40) or []
                elif role == "coach":
                    coach_sport = _coach_sport()
                    roster = db.list_roster_for_coach(int(uid), sport=coach_sport) or []
                    athlete_ids = [
                        int(aid) for aid in (_safe_int(a.get("id")) for a in roster) if aid
                    ]
                    if hasattr(db, "list_sessions_for_team"):
                        sessions_raw = db.list_sessions_for_team(athlete_ids, limit=200) or []
                    else:
                        sessions_raw = []
                        for aid in athlete_ids:
                            sessions_raw.extend(db.list_sessions(int(aid), limit=20) or [])
                elif role == "admin":
                    athletes = [
                        u for u in (db.list_users() or [])
                        if u.get("role", "deportista") == "deportista"
                    ]
                    athlete_ids = [
                        int(aid) for aid in (_safe_int(a.get("id")) for a in athletes) if aid
                    ]
                    if hasattr(db, "list_sessions_for_team"):
                        sessions_raw = db.list_sessions_for_team(athlete_ids, limit=300) or []
                    else:
                        sessions_raw = []
                        for aid in athlete_ids:
                            sessions_raw.extend(db.list_sessions(int(aid), limit=10) or [])
                else:
                    return []
                sessions_raw = sorted(sessions_raw, key=lambda s: s.get("ts_start") or "", reverse=True)
                opts = []
                for s in sessions_raw[:40]:
                    sid   = s.get("id")
                    notes = (s.get("notes") or "")
                    if sid is None or not notes.startswith("Combat Monitor"):
                        continue
                    ts_raw = (s.get("ts_start") or "")
                    try:
                        date_str = _dt_lazy.fromisoformat(ts_raw[:19]).strftime("%d/%m %H:%M")
                    except Exception:
                        date_str = ts_raw[:16].replace("T", " ")
                    rounds_m = _re_lazy.search(r'(\d+) rounds?', notes)
                    bpm_m    = _re_lazy.search(r'Peak BPM (\d+)', notes)
                    imp_m    = _re_lazy.search(r'(\d+) impactos?', notes)
                    sport_s  = (s.get("sport") or "Combate")
                    parts    = [sport_s]
                    if rounds_m: parts.append(f"{rounds_m.group(1)} rounds")
                    if bpm_m:    parts.append(f"BPM {bpm_m.group(1)}")
                    if imp_m:    parts.append(f"{imp_m.group(1)} golpes")
                    opts.append({"label": f"🥊 📊 {' · '.join(parts)} — {date_str}", "value": sid})
                return opts
            except Exception:
                return []

        # ── Lazy-load: populate athlete selector + KPIs + sensors ─────────────
        @app.callback(
            Output("ecg-user",          "options"),
            Output("ecg-user",          "value"),
            Output("kpi-ecg-value",     "children"),
            Output("kpi-ecg-sub",       "children"),
            Output("kpi-imu-value",     "children"),
            Output("kpi-imu-sub",       "children"),
            Output("kpi-wellness-value","children"),
            Output("kpi-wellness-sub",  "children"),
            Output("signals-sensors-text", "children"),
            Input("signals-lazy-trigger", "data"),
            prevent_initial_call=False,
        )
        def populate_signals_kpis(trigger):
            if not trigger:
                raise PreventUpdate
            uid  = _safe_int(session.get("user_id"))
            role = str(session.get("role") or "")

            # ── Athlete dropdown ──────────────────────────────────────────────
            options_users = []
            default_user  = None
            if uid:
                try:
                    if role == "coach":
                        coach_sport = str(session.get("sport") or "").strip() or None
                        athletes = db.list_roster_for_coach(int(uid), sport=coach_sport)
                    elif role == "deportista":
                        u = db.get_user_by_id(int(uid))
                        athletes = [u] if u and u.get("role") == "deportista" else []
                    elif role == "admin":
                        athletes = [u for u in db.list_users()
                                    if u.get("role", "deportista") == "deportista"]
                    else:
                        athletes = []
                    options_users = [
                        {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
                        for u in athletes
                    ]
                    default_user = options_users[0]["value"] if options_users else None
                except Exception:
                    pass

            # ── Sensors text ──────────────────────────────────────────────────
            sensors_text = "Inicia sesión como deportista para ver tus sensores."
            if uid and role == "deportista":
                try:
                    codes = db.get_user_sensors(int(uid)) or []
                    if codes:
                        labels = [self.S.catalog()[c]["short"] for c in codes if c in self.S.catalog()]
                        sensors_text = " · ".join(labels) if labels else "Sensores asignados (sin etiquetas)."
                    else:
                        sensors_text = "Sin sensores asignados aún."
                except Exception:
                    sensors_text = "Sin sensores asignados aún."

            # ── KPIs ──────────────────────────────────────────────────────────
            ecg_val  = "—"; ecg_sub  = "Sin lectura ECG"
            imu_val  = "—"; imu_sub  = "Sin lectura IMU"
            well_val = "—"; well_sub = "Sin check-in"
            if uid and role == "deportista":
                try:
                    _ecg = db.get_last_ecg_metrics(int(uid))
                    if _ecg and _ecg.get("bpm"):
                        ecg_val = f"{float(_ecg['bpm']):.0f} bpm"
                        parts = []
                        if _ecg.get("sdnn"):  parts.append(f"SDNN {_ecg['sdnn']:.0f} ms")
                        if _ecg.get("rmssd"): parts.append(f"RMSSD {_ecg['rmssd']:.0f} ms")
                        ecg_sub = " · ".join(parts) or "Cardio disponible"
                except Exception:
                    pass
                try:
                    _imus = db.list_imu_metrics(int(uid)) or []
                    if _imus:
                        imu_val = f"{float(_imus[0].get('hits_per_min', 0)):.0f} acc/min"
                        imu_sub = f"{_imus[0].get('n_hits', 0)} acciones · {float(_imus[0].get('mean_int_g', 0)):.1f} g medio"
                except Exception:
                    pass
                try:
                    _qs = db.list_questionnaires(int(uid)) or []
                    if _qs:
                        _wval = _qs[0].get("wellness_score")
                        if _wval is not None:
                            well_val = f"{float(_wval):.0f} / 100"
                            well_sub = "Último check-in"
                except Exception:
                    pass

            return (
                options_users, default_user,
                ecg_val, ecg_sub,
                imu_val, imu_sub,
                well_val, well_sub,
                sensors_text,
            )

        # ── Video Replay callbacks ─────────────────────────────────────────────

        _UPLOADS_DIR = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "uploads"
        )

        @app.callback(
            Output("replay-video-store",  "data"),
            Output("replay-player",       "src"),
            Output("replay-upload-zone",  "style"),
            Output("replay-player-card",  "style"),
            Output("replay-upload-err",   "children"),
            Input("replay-upload-result", "data"),
            prevent_initial_call=True,
        )
        def upload_video(result):
            if not result or not result.get("url"):
                raise PreventUpdate
            url      = result["url"]
            filename = result.get("filename", os.path.basename(url))
            return (
                {"filename": filename, "url": url},
                url,
                {"display": "none"},
                {},
                "",
            )

        @app.callback(
            Output("replay-auto-events",        "data",     allow_duplicate=True),
            Output("replay-vision-scan-status", "children", allow_duplicate=True),
            Input("btn-replay-scan-vision",     "n_clicks"),
            Input("replay-video-store",         "data"),
            Input("replay-session-select",      "value"),
            State("pose-target-select",         "value"),
            prevent_initial_call=True,
        )
        def scan_video_events(n_scan, video_store, session_id, pose_target):
            """Claude analiza el video (anclado en IMU si hay sesión) y reemplaza los eventos."""
            if ctx.triggered_id != "btn-replay-scan-vision":
                if (video_store or {}).get("filename"):
                    return no_update, html.Span(
                        "Video listo. Pulsa “Analizar IA video” para detectar eventos visuales.",
                        style={"fontSize": "10px", "color": "var(--muted)",
                               "opacity": "0.85", "marginTop": "4px", "display": "block"},
                    )
                return no_update, html.Span()
            if not n_scan:
                raise PreventUpdate
            if not (video_store or {}).get("filename"):
                return no_update, html.Span(
                    "Carga un video antes de analizar eventos con IA.",
                    style={"fontSize": "10px", "color": "var(--muted)",
                           "opacity": "0.85", "marginTop": "4px", "display": "block"},
                )

            filename = video_store["filename"]
            base_dir = os.path.dirname(os.path.abspath(__file__))
            if os.path.basename(base_dir) == "views":
                base_dir = os.path.dirname(base_dir)

            video_path = None
            for uploads_dir in [
                os.path.join(base_dir, "data", "uploads"),
                os.path.join(base_dir, "data", "uploads_legacy"),
                os.path.join(base_dir, "assets", "uploads"),
            ]:
                candidate = os.path.join(uploads_dir, os.path.basename(filename))
                if os.path.isfile(candidate):
                    video_path = candidate
                    break

            if not video_path:
                return no_update, html.Span()

            sport = "taekwondo"
            athlete_name = "Atleta"
            if session_id:
                try:
                    import db as _db
                    s = _db.get_session(int(session_id)) or {}
                    sport = (s.get("sport") or "taekwondo").lower()
                except Exception:
                    pass

            # Cargar eventos IMU de la sesión para usarlos como ancla
            imu_events = None
            if session_id:
                try:
                    imu_full = _load_replay_imu_points(int(session_id))
                    if imu_full:
                        imu_events = [e for e in imu_full if e.get("type") != "ruido"]
                except Exception:
                    pass

            # Derive vest target: selector values "red"/"rojo" → "rojo", else "azul"
            _target_vest = "rojo" if (pose_target or "").lower() in ("red", "rojo") else "azul"
            try:
                from ai_insights import detect_video_events as _detect
                vision_events = _detect(
                    video_path, sport=sport,
                    imu_events=imu_events,
                    target_vest=_target_vest,
                )
            except Exception:
                vision_events = []

            if not vision_events:
                return no_update, html.Span()

            n = len(vision_events)
            mode = "IMU + video" if imu_events else "video"
            status = html.Span(
                f"✦ IA analizó {n} evento(s) desde {mode}",
                style={"fontSize": "10px", "color": "var(--neon)",
                       "opacity": "0.8", "marginTop": "4px", "display": "block"},
            )
            return vision_events, status

        @app.callback(
            Output("replay-video-store",   "data",  allow_duplicate=True),
            Output("replay-player",        "src",   allow_duplicate=True),
            Output("replay-upload-zone",   "style", allow_duplicate=True),
            Output("replay-player-card",   "style", allow_duplicate=True),
            Output("replay-annotations",   "data",  allow_duplicate=True),
            Output("replay-vision-events", "data",  allow_duplicate=True),
            Output("replay-vision-scan-status", "children", allow_duplicate=True),
            Input("btn-replay-clear", "n_clicks"),
            prevent_initial_call=True,
        )
        def clear_video(n):
            if not n:
                raise PreventUpdate
            return None, "", {}, {"display": "none"}, [], [], html.Span()

        # (manual add_annotation removed — events are auto-generated from signals)

        @app.callback(
            Output("replay-auto-events",        "data", allow_duplicate=True),
            Output("replay-vision-events",      "data", allow_duplicate=True),
            Output("replay-vision-scan-status", "children", allow_duplicate=True),
            Output("replay-visible-count",      "data", allow_duplicate=True),
            Input("btn-replay-clear-ann", "n_clicks"),
            prevent_initial_call=True,
        )
        def clear_annotations(n):
            if not n:
                raise PreventUpdate
            return [], [], html.Span(), 0

        @app.callback(
            Output("dl-replay-ecg-csv", "data"),
            Input("btn-replay-dl-ecg",  "n_clicks"),
            State("replay-sensor-ecg",  "data"),
            State("replay-session-select", "value"),
            prevent_initial_call=True,
        )
        def download_replay_ecg(n, ecg_pts, session_id):
            if not n:
                raise PreventUpdate
            if session_id and _get_authorized_session(session_id):
                ecg_pts = _load_replay_ecg_points(session_id) or ecg_pts
            if not ecg_pts:
                raise PreventUpdate
            from flask import session as _fsess
            from datetime import datetime as _dt
            try:
                from report_utils import xlsx_table
                session_row = _get_authorized_session(session_id) if session_id else None
                _uid = session_row.get("athlete_id") if session_row else _fsess.get("user_id")
                _athlete_name, _sport = "Deportista", "—"
                if _uid:
                    _ath = db.get_user_by_id(int(_uid)) or {}
                    _athlete_name = _ath.get("name") or "Deportista"
                    _sport = (_ath.get("sport") or "—").title()
                _dur = round(ecg_pts[-1]["t"] - ecg_pts[0]["t"], 1) if len(ecg_pts) > 1 else 0
                meta = [
                    ("Atleta",    _athlete_name),
                    ("Deporte",   _sport),
                    ("Señal",     "ECG — electrocardiograma"),
                    ("Muestras",  str(len(ecg_pts))),
                    ("Duración",  f"{_dur:.1f} s"),
                    ("Columnas",  "Tiempo (s) | Amplitud ECG (a.u.)"),
                    ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
                ]
                data = [[round(p["t"], 4), round(p["y"], 6)] for p in ecg_pts]
                xl = xlsx_table(
                    "Señal ECG — datos brutos de sesión",
                    meta, ["Tiempo (s)", "Amplitud ECG (a.u.)"], data,
                    sheet_name="ECG",
                    col_types={0: "number", 1: "number"},
                )
                fname = f"CombatIQ_ECG_replay_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
                return dcc.send_bytes(lambda b: b.write(xl), fname)
            except Exception:
                # Fallback to CSV if xlsx fails
                lines = ["time,ecg"] + [f"{p['t']:.4f},{p['y']:.6f}" for p in ecg_pts]
                return {"content": "\n".join(lines), "filename": "ecg_sesion.csv",
                        "type": "text/csv", "base64": False}

        @app.callback(
            Output("dl-replay-imu-csv", "data"),
            Input("btn-replay-dl-imu",  "n_clicks"),
            State("replay-sensor-imu",  "data"),
            State("replay-session-select", "value"),
            prevent_initial_call=True,
        )
        def download_replay_imu(n, imu_pts, session_id):
            if not n:
                raise PreventUpdate
            if session_id and _get_authorized_session(session_id):
                imu_pts = _load_replay_imu_points(session_id) or imu_pts
            if not imu_pts:
                raise PreventUpdate
            from flask import session as _fsess
            from datetime import datetime as _dt
            try:
                from report_utils import xlsx_table
                session_row = _get_authorized_session(session_id) if session_id else None
                _uid = session_row.get("athlete_id") if session_row else _fsess.get("user_id")
                _athlete_name, _sport = "Deportista", "—"
                if _uid:
                    _ath = db.get_user_by_id(int(_uid)) or {}
                    _athlete_name = _ath.get("name") or "Deportista"
                    _sport = (_ath.get("sport") or "—").title()
                _dur = round(imu_pts[-1].get("t", 0) - imu_pts[0].get("t", 0), 1) if len(imu_pts) > 1 else 0
                _n_impacts = sum(1 for p in imu_pts if p.get("type") in ("dado", "recibido"))
                meta = [
                    ("Atleta",    _athlete_name),
                    ("Deporte",   _sport),
                    ("Señal",     "IMU — acelerómetro inercial"),
                    ("Muestras",  str(len(imu_pts))),
                    ("Duración",  f"{_dur:.1f} s"),
                    ("Impactos",  f"{_n_impacts} (dado + recibido)"),
                    ("Columnas",  "Tiempo (s) | Intensidad (g) | Tipo de acción"),
                    ("Exportado", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
                ]
                data = [
                    [round(p.get("t", 0), 4),
                     round(p.get("intensity", 0), 4),
                     p.get("type", "ruido")]
                    for p in imu_pts
                ]
                xl = xlsx_table(
                    "Señal IMU — datos brutos de sesión",
                    meta, ["Tiempo (s)", "Intensidad (g)", "Tipo de acción"], data,
                    sheet_name="IMU",
                    col_types={0: "number", 1: "number"},
                )
                fname = f"CombatIQ_IMU_replay_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
                return dcc.send_bytes(lambda b: b.write(xl), fname)
            except Exception:
                # Fallback to CSV if xlsx fails
                lines = ["time,intensity,type"] + [
                    f"{p.get('t',0):.4f},{p.get('intensity',0):.4f},{p.get('type','ruido')}"
                    for p in imu_pts
                ]
                return {"content": "\n".join(lines), "filename": "imu_sesion.csv",
                        "type": "text/csv", "base64": False}

        @app.callback(
            Output("replay-annotations-list", "children"),
            Output("replay-visible-count",    "data"),
            Input("replay-auto-events",       "data"),
            Input("replay-video-time",        "data"),
            State("replay-visible-count",     "data"),
            prevent_initial_call=True,
        )
        def render_annotations(all_events, video_time, prev_count):
            _TYPES = {
                "attack":      ("🗡️",  "Ataque",                "var(--punch)"),
                "defense":     ("🛡️",  "Defensa",               "var(--neon)"),
                "good_tech":   ("✅",  "Técnica correcta",       "var(--green)"),
                "tech_error":  ("⚠️",  "Error técnico",          "var(--amber)"),
                "injury":      ("🩹",  "Lesión",                 "var(--punch)"),
                "fatiga_alta": ("🔥",  "Fatiga alta",            "var(--amber)"),
                "caida_bpm":   ("📉",  "Caída FC",               "var(--neon)"),
                "general":     ("📌",  "General",                "var(--muted)"),
            }
            if not all_events:
                return html.P("Selecciona una sesión para ver los eventos.",
                              className="text-muted"), 0
            t = float(video_time or 0)
            visible = [(i, e) for i, e in enumerate(all_events) if e.get("time", 0) <= t]
            new_count = len(visible)

            # Debounce: skip full re-render if no new events became visible
            if new_count == (prev_count or 0) and video_time is not None:
                raise PreventUpdate

            if not visible:
                return html.P("Reproduce el video para ver los eventos en tiempo real.",
                              className="text-muted"), 0

            items = []
            for i, ann in visible:
                ann_t = ann.get("time", 0)
                icon, label, color = _TYPES.get(ann.get("type", "general"),
                                                ("📌", "General", "var(--muted)"))
                m, s_val = int(ann_t) // 60, int(ann_t) % 60
                is_vision = ann.get("source") == "vision"
                is_auto   = ann.get("auto", False) and not is_vision
                if is_vision:
                    badge = html.Span(
                        "IA video",
                        style={"fontSize": "9px", "background": "rgba(139,92,246,0.15)",
                               "color": "#8b5cf6", "borderRadius": "4px",
                               "padding": "1px 5px", "fontWeight": "600",
                               "letterSpacing": "0.04em"},
                    )
                elif is_auto:
                    badge = html.Span(
                        "sensor",
                        style={"fontSize": "9px", "background": "rgba(47,183,196,0.15)",
                               "color": "var(--neon)", "borderRadius": "4px",
                               "padding": "1px 5px", "fontWeight": "600",
                               "letterSpacing": "0.04em"},
                    )
                else:
                    badge = None
                rn = ann.get("round")
                ts_label = f"R{rn} · {m}:{s_val:02d}" if rn and str(rn) not in ("0", "?") else f"{m}:{s_val:02d}"
                items.append(html.Div(
                    id={"type": "ann-seek-btn", "index": i},
                    className="replay-ann-item",
                    n_clicks=0,
                    title="Clic para ir a este momento",
                    style={"borderLeft": f"3px solid {color}"},
                    children=[
                        html.Span(ts_label, className="replay-ann-ts"),
                        html.Span(f"{icon} {label}",
                                  style={"color": color, "fontSize": "11px",
                                         "fontWeight": "700"}),
                        *([badge] if badge else []),
                        html.Span(ann.get("text", ""), className="replay-ann-desc"),
                    ],
                ))
            return items, new_count

        @app.callback(
            Output("replay-seek-target",  "data"),
            Output("replay-vision-event", "data"),
            Input({"type": "ann-seek-btn", "index": ALL}, "n_clicks"),
            State("replay-auto-events",   "data"),
            prevent_initial_call=True,
        )
        def seek_to_annotation(clicks, anns):
            if not any(c for c in (clicks or []) if c):
                raise PreventUpdate
            triggered = ctx.triggered_id
            if not triggered or not isinstance(triggered, dict):
                raise PreventUpdate
            idx = triggered.get("index")
            if idx is None or not anns or idx >= len(anns):
                raise PreventUpdate
            ann = anns[idx]
            # Only trigger vision for impact events (attack/defense)
            vision_payload = None
            if ann.get("type") in ("attack", "defense"):
                # Extract intensity: vision events store it in intensity_g,
                # sensor events encode it in the text ("Impacto: 4.1 g")
                intensity = float(ann.get("intensity_g", 0))
                if not intensity:
                    import re as _re
                    m = _re.search(r"(\d+\.?\d*)\s*g", ann.get("text", ""))
                    if m:
                        intensity = float(m.group(1))
                vision_payload = {
                    "timestamp_s": ann["time"],
                    "type":        "dado" if ann.get("type") == "attack" else "recibido",
                    "text":        ann.get("text", ""),
                    "intensity_g": intensity,
                    "round":       ann.get("round", "?"),
                }
            return ann["time"], vision_payload

        @app.callback(
            Output("replay-vision-panel", "children"),
            Input("replay-vision-event",  "data"),
            State("replay-video-store",   "data"),
            State("replay-session-select","value"),
            prevent_initial_call=True,
        )
        def analyze_vision_event(event_data, video_store, session_id):
            if not event_data:
                return html.Div()

            ts       = float(event_data.get("timestamp_s", 0))
            ev_type  = event_data.get("type", "impacto")
            ev_text  = event_data.get("text", "")
            filename = (video_store or {}).get("filename")

            if not filename:
                return html.Div(
                    html.P("Sin video cargado — sube un video para ver el análisis visual.",
                           className="text-muted", style={"fontSize": "11px", "marginTop": "6px"}),
                )

            # Resolve full disk path
            import os as _os
            base_dir = _os.path.dirname(_os.path.abspath(__file__))
            # walk one level up if we're in views/
            if _os.path.basename(base_dir) == "views":
                base_dir = _os.path.dirname(base_dir)
            video_path = None
            for uploads_dir in [
                _os.path.join(base_dir, "data", "uploads"),
                _os.path.join(base_dir, "data", "uploads_legacy"),
                _os.path.join(base_dir, "assets", "uploads"),
            ]:
                candidate = _os.path.join(uploads_dir, _os.path.basename(filename))
                if _os.path.isfile(candidate):
                    video_path = candidate
                    break

            frame_b64 = _extract_replay_frame_b64(video_path, ts, quality=80) if video_path else None

            # Get sport for context
            sport = "taekwondo"
            athlete_name = "Atleta"
            if session_id:
                try:
                    import db as _db
                    s = _db.get_session(int(session_id)) or {}
                    sport = (s.get("sport") or "taekwondo").lower()
                    aid = _safe_int(s.get("athlete_id"))
                    if aid:
                        athlete_row = _db.get_user_by_id(int(aid)) or {}
                        athlete_name = athlete_row.get("name") or athlete_name
                except Exception:
                    pass
            _viewer_role = str(session.get("role") or "deportista")
            _viewer_name = session.get("name") or ("Coach" if _viewer_role == "coach" else athlete_name)

            if frame_b64 is None:
                analysis = ("No se pudo extraer el fotograma. "
                            "Asegúrate de que el video esté en data/uploads/.")
            else:
                try:
                    from ai_insights import analyze_event_frame as _analyze_frame
                    analysis = _analyze_frame(
                        frame_b64,
                        event_data,
                        sport=sport,
                        audience=_viewer_role,
                        athlete_name=athlete_name,
                        viewer_name=_viewer_name,
                    )
                except Exception as exc:
                    analysis = f"Error en análisis visual: {exc}"

            m, s_val = int(ts) // 60, int(ts) % 60
            type_label = "Ataque" if ev_type == "dado" else "Defensa"
            return html.Div(
                style={
                    "borderTop": "1px solid var(--border)",
                    "paddingTop": "8px",
                    "marginTop": "6px",
                },
                children=[
                    html.Div(
                        style={"display": "flex", "gap": "6px", "alignItems": "center",
                               "marginBottom": "6px"},
                        children=[
                            html.Span("IA VISUAL", style={
                                "fontSize": "9px", "fontWeight": "700",
                                "color": "var(--neon)", "border": "1px solid var(--neon)",
                                "borderRadius": "4px", "padding": "2px 5px",
                            }),
                            html.Span(f"{type_label} · {m}:{s_val:02d}",
                                      style={"fontSize": "11px", "color": "var(--muted)"}),
                            html.Span(ev_text,
                                      style={"fontSize": "11px", "color": "var(--muted)"}),
                        ],
                    ),
                    html.P(analysis,
                           style={"fontSize": "11px", "color": "var(--ink)",
                                  "lineHeight": "1.5", "margin": "0"}),
                ],
            )

        app.clientside_callback(
            """
            function(seek_t) {
                if (seek_t === null || seek_t === undefined) {
                    return window.dash_clientside.no_update;
                }
                var wrap = document.getElementById('replay-player');
                if (!wrap) return window.dash_clientside.no_update;
                var vid = (wrap.tagName === 'VIDEO') ? wrap : wrap.querySelector('video');
                if (vid) { vid.currentTime = seek_t; vid.pause(); }
                return window.dash_clientside.no_update;
            }
            """,
            Output("replay-seek-dummy", "children"),
            Input("replay-seek-target", "data"),
            prevent_initial_call=True,
        )

        # Botones ± y reset del ajuste de sincronización
        @app.callback(
            Output("replay-offset-adj", "value"),
            Input("btn-offset-minus",  "n_clicks"),
            Input("btn-offset-plus",   "n_clicks"),
            Input("btn-offset-reset",  "n_clicks"),
            State("replay-offset-adj", "value"),
            prevent_initial_call=True,
        )
        def adjust_offset(n_minus, n_plus, n_reset, current):
            triggered = ctx.triggered_id
            val = int(current or 0)
            if triggered == "btn-offset-plus":
                return val + 1
            if triggered == "btn-offset-minus":
                return val - 1
            if triggered == "btn-offset-reset":
                return 0
            raise PreventUpdate

        # Clientside: call video.load() when src changes — Dash/React no lo hace automáticamente
        app.clientside_callback(
            """
            function(src) {
                if (!src) return window.dash_clientside.no_update;
                setTimeout(function() {
                    var el = document.getElementById('replay-player');
                    if (!el) return;
                    var vid = (el.tagName === 'VIDEO') ? el : el.querySelector('video');
                    if (vid) { vid.load(); }
                }, 150);
                return window.dash_clientside.no_update;
            }
            """,
            Output("replay-seek-dummy", "children", allow_duplicate=True),
            Input("replay-player", "src"),
            prevent_initial_call=True,
        )

        # Clientside: poll video currentTime — only when video is actually playing
        app.clientside_callback(
            """
            function(n_intervals, auto_offset, manual_adj) {
                var el = document.getElementById('replay-player');
                if (!el) return window.dash_clientside.no_update;
                var vid = (el.tagName === 'VIDEO') ? el : el.querySelector('video');
                if (!vid || vid.paused || vid.ended) return window.dash_clientside.no_update;
                return (vid.currentTime || 0.0) + (auto_offset || 0.0) + (manual_adj || 0.0);
            }
            """,
            Output("replay-video-time", "data", allow_duplicate=True),
            Input("replay-time-poll",   "n_intervals"),
            State("replay-time-offset", "data"),
            State("replay-offset-adj",  "value"),
            prevent_initial_call=True,
        )

        @app.callback(
            Output("replay-session-info", "children"),
            Input("replay-session-select", "value"),
            prevent_initial_call=True,
        )
        def load_session_info(session_id):
            if not session_id:
                return html.P("—", className="text-muted", style={"margin": "0"})
            s = _get_authorized_session(session_id)
            if not s:
                return html.P("Sesión no encontrada.", className="text-muted",
                              style={"margin": "0"})
            notes  = s.get("notes") or ""
            sport  = s.get("sport") or "—"
            ts     = (s.get("ts_start") or "")[:16].replace("T", " ")
            status = s.get("status") or "—"
            is_combat = notes.startswith("Combat Monitor")
            return html.Div([
                html.Div(style={"display": "flex", "gap": "10px",
                                "flexWrap": "wrap", "marginBottom": "4px"},
                         children=[
                    html.Span(sport,
                              className="session-pill",
                              style={"fontSize": "11px"}),
                    html.Span(ts,
                              style={"fontSize": "11px", "color": "var(--muted)"}),
                    html.Span(("🥊 Combat Monitor" if is_combat else status.upper()),
                              style={"fontSize": "11px",
                                     "color": "var(--neon)" if is_combat else "var(--muted)",
                                     "fontWeight": "600"}),
                ]),
                html.P(notes[:120] + ("…" if len(notes) > 120 else ""),
                       style={"fontSize": "11px", "color": "var(--muted)",
                              "margin": "0"}),
            ])

        # ── AI combat analysis ────────────────────────────────────────────────

        @app.callback(
            Output("replay-ai-panel", "children"),
            Input("btn-replay-ai-analysis", "n_clicks"),
            Input("replay-session-select", "value"),
            prevent_initial_call=True,
        )
        def render_ai_combat_analysis(n_ai, session_id):
            if not session_id:
                return html.P(
                    "Selecciona una sesión Combat Monitor para generar la lectura IA.",
                    className="text-muted",
                    style={"padding": "12px", "fontSize": "12px"},
                )
            s = _get_authorized_session(session_id)
            if not s:
                return []
            notes = s.get("notes") or ""
            if not notes.startswith("Combat Monitor"):
                return []
            if ctx.triggered_id != "btn-replay-ai-analysis":
                return html.P(
                    "Sesión lista. Pulsa “Generar lectura IA” para crear el análisis contextual.",
                    className="text-muted",
                    style={"padding": "12px", "fontSize": "12px"},
                )
            if not n_ai:
                raise PreventUpdate

            # Athlete info
            athlete_id = s.get("athlete_id")
            athlete_row = None
            if athlete_id:
                try:
                    athlete_row = db.get_user_by_id(int(athlete_id))
                except Exception:
                    pass
            athlete_name = "Atleta"
            if athlete_row:
                athlete_name = (
                    athlete_row.get("name") or
                    (athlete_row.get("email") or "atleta").split("@")[0]
                )
            sport = (s.get("sport") or "taekwondo").lower()

            # ECG metrics from DB
            ecg_metrics = {}
            if athlete_id:
                try:
                    m = db.get_last_ecg_metrics(int(athlete_id))
                    if m:
                        ecg_metrics = m
                except Exception:
                    pass

            # IMU events → per-round stats
            imu_events = _load_replay_imu_points(session_id)
            impacts = [e for e in imu_events if e.get("type") != "ruido"]
            by_round = {}
            for e in impacts:
                rn = e.get("round", 1)
                if rn not in by_round:
                    by_round[rn] = {"dado": 0, "dado_g": [], "recibido": 0, "recibido_g": []}
                if e.get("type") == "dado":
                    by_round[rn]["dado"] += 1
                    by_round[rn]["dado_g"].append(e.get("intensity", 0))
                elif e.get("type") == "recibido":
                    by_round[rn]["recibido"] += 1
                    by_round[rn]["recibido_g"].append(e.get("intensity", 0))
            by_round_clean = {}
            for rn, d in by_round.items():
                by_round_clean[rn] = {
                    "dado":      d["dado"],
                    "dado_g":    round(sum(d["dado_g"]) / len(d["dado_g"]), 2) if d["dado_g"] else 0,
                    "recibido":  d["recibido"],
                    "recibido_g": round(sum(d["recibido_g"]) / len(d["recibido_g"]), 2) if d["recibido_g"] else 0,
                }

            intensities = [e.get("intensity", 0) for e in impacts]
            session_data = {
                "session_id": int(session_id),
                "athlete_name": athlete_name,
                "sport": sport,
                "ecg": {
                    "bpm":   ecg_metrics.get("bpm", 0),
                    "sdnn":  ecg_metrics.get("sdnn", 0),
                    "rmssd": ecg_metrics.get("rmssd", 0),
                },
                "imu": {
                    "total_dado":     sum(1 for e in impacts if e.get("type") == "dado"),
                    "total_recibido": sum(1 for e in impacts if e.get("type") == "recibido"),
                    "avg_intensity":  round(sum(intensities) / len(intensities), 2) if intensities else 0,
                    "peak_intensity": round(max(intensities), 2) if intensities else 0,
                    "by_round":       by_round_clean,
                },
            }

            try:
                from ai_insights import analyze_combat_session as _analyze
                _viewer_role = str(session.get("role") or "deportista")
                _viewer_name = session.get("name") or ("Coach" if _viewer_role == "coach" else athlete_name)
                result = _analyze(
                    session_data,
                    sport=sport,
                    audience=_viewer_role,
                    viewer_name=_viewer_name,
                )
            except Exception as exc:
                return html.P(f"Error en análisis IA: {exc}",
                              className="text-muted", style={"padding": "12px"})

            if result.get("error"):
                return html.P(f"Error en análisis IA: {result['error']}",
                              className="text-muted", style={"padding": "12px"})

            return self.render_ai_panel_content(result)

        # ── Session rename ─────────────────────────────────────────────────────
        @app.callback(
            Output("replay-rename-row",  "style"),
            Output("replay-rename-input", "value"),
            Input("replay-session-select", "value"),
            prevent_initial_call=True,
        )
        def toggle_rename_row(session_id):
            if not session_id:
                return {"display": "none"}, ""
            s = _get_authorized_session(session_id)
            if not s:
                return {"display": "none"}, ""
            current_name = (s.get("notes") or "")[:150]
            return (
                {"display": "flex", "marginTop": "10px", "gap": "8px",
                 "alignItems": "center", "flexWrap": "wrap"},
                current_name,
            )

        @app.callback(
            Output("replay-rename-msg", "children"),
            Input("btn-replay-rename",  "n_clicks"),
            State("replay-session-select", "value"),
            State("replay-rename-input",   "value"),
            prevent_initial_call=True,
        )
        def save_session_rename(n, session_id, new_name):
            if not n or not session_id:
                raise PreventUpdate
            if not (new_name or "").strip():
                return "El nombre no puede estar vacío."
            s = _get_authorized_session(session_id)
            if not s:
                return "Sin permiso."
            ok = db.rename_session(int(session_id), new_name.strip())
            return "Guardado ✓" if ok else "Error al guardar."

        # ── Sensor replay: load ECG + IMU from session ────────────────────────

        _ECG_DATA_DIR = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "ecg"
        )

        def _sensor_status_badge(has_ecg: bool, has_imu: bool) -> html.Div:
            if has_ecg or has_imu:
                tags = []
                if has_ecg:
                    tags.append(html.Span("📈 ECG", style={
                        "background": "rgba(47,183,196,0.15)",
                        "color": "var(--neon)", "borderRadius": "6px",
                        "padding": "2px 8px", "fontSize": "11px",
                        "fontWeight": "600"}))
                if has_imu:
                    tags.append(html.Span("📡 IMU", style={
                        "background": "rgba(39,201,143,0.15)",
                        "color": "var(--green)", "borderRadius": "6px",
                        "padding": "2px 8px", "fontSize": "11px",
                        "fontWeight": "600"}))
                return html.Div(
                    style={"display": "flex", "gap": "6px",
                           "alignItems": "center", "flexWrap": "wrap"},
                    children=[html.Span("Datos disponibles:",
                                        style={"fontSize": "11px",
                                               "color": "var(--muted)"})] + tags)
            return html.Div(
                html.P("Selecciona una sesión 🥊 Combat Monitor para ver los sensores.",
                       style={"fontSize": "12px", "color": "var(--muted)",
                              "margin": "0", "fontStyle": "italic"}),
                className="card",
                style={"padding": "10px 14px"})

        # ── Sensor panel collapse ──────────────────────────────────────────
        @app.callback(
            Output("replay-sensor-body",      "style"),
            Output("btn-sensor-toggle",       "children"),
            Output("replay-sensor-collapsed", "data"),
            Input("btn-sensor-toggle",        "n_clicks"),
            State("replay-sensor-collapsed",  "data"),
            prevent_initial_call=True,
        )
        def toggle_sensor_panel(n, collapsed):
            if not n:
                raise PreventUpdate
            now_collapsed = not bool(collapsed)
            body_style    = {"display": "none"} if now_collapsed else {}
            btn_label     = "▶ Mostrar" if now_collapsed else "▼ Ocultar"
            return body_style, btn_label, now_collapsed

        @app.callback(
            Output("replay-sensor-ecg",      "data"),
            Output("replay-sensor-imu",      "data"),
            Output("replay-time-poll",       "disabled"),
            Output("replay-sensor-status",   "children"),
            Output("replay-time-offset",     "data"),
            Output("replay-auto-events",     "data"),
            Input("replay-session-select",   "value"),
            Input("replay-video-store",      "data"),
            prevent_initial_call=True,
        )
        def load_session_sensors(session_id, video_store):
            empty_status = _sensor_status_badge(False, False)
            if not session_id:
                return [], [], True, empty_status, 0.0, []
            if not _get_authorized_session(session_id):
                return [], [], True, empty_status, 0.0, []
            ecg_full = _load_replay_ecg_points(session_id)
            imu_full = _load_replay_imu_points(session_id)
            # Keep browser stores small enough to avoid freezes on long sessions.
            ecg_pts = _decimate_points(ecg_full, 6000)
            imu_pts = _decimate_points(imu_full, 2500)
            # Auto-sync: offset = primer instante con actividad ECG significativa
            time_offset = 0.0
            if ecg_full:
                ys = [abs(p["y"]) for p in ecg_full]
                max_y = max(ys) if ys else 0
                threshold = max_y * 0.12   # 12% del pico
                if threshold > 0:
                    for p in ecg_full:
                        if abs(p["y"]) >= threshold:
                            # retrocede 3s para dar contexto visual
                            time_offset = round(max(0.0, p["t"] - 3.0), 2)
                            break
            elif imu_full:
                imu_times = [p.get("t", 0) for p in imu_full]
                time_offset = round(max(0.0, min(imu_times)), 2)
            has_video = bool((video_store or {}).get("url"))
            poll_disabled = not (bool(ecg_full or imu_full) and has_video)
            status = _sensor_status_badge(bool(ecg_full), bool(imu_full))
            auto_anns = _generate_auto_annotations(ecg_pts, imu_pts)
            return ecg_pts, imu_pts, poll_disabled, status, time_offset, auto_anns

        @app.callback(
            Output("replay-sensor-ecg-chart", "figure"),
            Output("replay-sensor-imu-chart", "figure"),
            Input("replay-sensor-ecg", "data"),
            Input("replay-sensor-imu", "data"),
            Input("replay-sim-rest-bands", "data"),
            State("replay-time-offset", "data"),
            prevent_initial_call=True,
        )
        def render_sensor_charts(ecg_pts, imu_pts, rest_bands, time_offset):
            # Rest bands from pose analysis shifted to sensor-time axis
            t_off = float(time_offset or 0.0)
            # shapes[0] is always reserved for the clientside cursor line —
            # without it Plotly.relayout({'shapes[0]': cursor}) fails silently.
            _cursor_placeholder = {
                "type": "line", "x0": 0, "x1": 0, "y0": 0, "y1": 0,
                "opacity": 0, "line": {"width": 0},
            }
            if rest_bands:
                _shifted = [{"t0": b["t0"] + t_off, "t1": b["t1"] + t_off}
                            for b in rest_bands]
                band_shapes = [_cursor_placeholder] + _rest_band_shapes(_shifted)
            else:
                band_shapes = [_cursor_placeholder]

            # ── ECG figure ────────────────────────────────────────────────
            if ecg_pts:
                xs = [p["t"] for p in ecg_pts]
                ys = [p["y"] for p in ecg_pts]
                ecg_fig = go.Figure(go.Scatter(
                    x=xs, y=ys, mode="lines",
                    line={"color": "#27c98f", "width": 1.2},
                    hoverinfo="skip",
                ))
            else:
                ecg_fig = empty_figure(message="Sin ECG para esta sesión", height=180)
            ecg_fig = apply_chart_style(ecg_fig, height=180)
            ecg_fig.update_layout(
                margin={"t": 4, "b": 24, "l": 28, "r": 8},
                xaxis_title="t (s)",
                yaxis={"showticklabels": False, "fixedrange": True},
                xaxis={"fixedrange": True},
                shapes=band_shapes,
            )

            # ── IMU stem figure ───────────────────────────────────────────
            _HIT_COLORS = {
                "dado":     "#27c98f",
                "recibido": "#e45a5a",
                "ruido":    "rgba(143,163,191,0.35)",
            }
            if imu_pts:
                traces = {}
                for pt in imu_pts:
                    htype = pt.get("type", "ruido")
                    if htype not in traces:
                        traces[htype] = {"xs": [], "ys": []}
                    t = pt.get("t", 0)
                    g = pt.get("intensity", 0)
                    traces[htype]["xs"].extend([t, t, None])
                    traces[htype]["ys"].extend([0.0, g, None])

                _LABELS = {"dado": "Dado", "recibido": "Recibido", "ruido": "Movimiento"}
                imu_fig = go.Figure()
                for htype, data in traces.items():
                    imu_fig.add_trace(go.Scatter(
                        x=data["xs"], y=data["ys"],
                        mode="lines",
                        line={"color": _HIT_COLORS.get(htype, "#aaa"), "width": 1.5},
                        name=_LABELS.get(htype, htype),
                        hoverinfo="skip",
                    ))
            else:
                imu_fig = empty_figure(message="Sin IMU para esta sesión", height=160)
            imu_fig = apply_chart_style(imu_fig, height=160)
            imu_fig.update_layout(
                margin={"t": 4, "b": 24, "l": 28, "r": 8},
                xaxis_title="t (s)",
                yaxis_title="g",
                showlegend=bool(imu_pts),
                legend={"font": {"size": 9}, "orientation": "h",
                        "y": 1.12, "x": 0},
                xaxis={"fixedrange": True},
                yaxis={"fixedrange": True},
                shapes=band_shapes,
            )

            return ecg_fig, imu_fig

        # Clientside: draw cursor via Plotly.relayout (avoids cloning full figure JSON)
        app.clientside_callback(
            """
            function(video_time) {
                if (video_time === null || video_time === undefined) {
                    return window.dash_clientside.no_update;
                }
                var t = parseFloat(video_time);
                var cursor = {
                    type: 'line', x0: t, x1: t, y0: 0, y1: 1, yref: 'paper',
                    line: {color: 'rgba(255,180,50,0.85)', width: 1.5, dash: 'dot'}
                };
                var ids = [
                    'replay-sensor-ecg-chart', 'replay-sensor-imu-chart',
                    'replay-sim-hr-chart',      'replay-sim-imu-chart'
                ];
                ids.forEach(function(id) {
                    var el = document.getElementById(id);
                    if (el) try { Plotly.relayout(el, {'shapes[0]': cursor}); } catch(e) {}
                });
                return window.dash_clientside.no_update;
            }
            """,
            Output("replay-seek-dummy", "children", allow_duplicate=True),
            Input("replay-video-time", "data"),
            prevent_initial_call=True,
        )

        # Clientside: update time badge from video-time store
        app.clientside_callback(
            """
            function(t) {
                if (t == null) return '—';
                var m = Math.floor(t / 60);
                var s = Math.floor(t % 60);
                return m + ':' + (s < 10 ? '0' : '') + s;
            }
            """,
            Output("replay-sensor-time-badge", "children"),
            Input("replay-video-time", "data"),
            prevent_initial_call=True,
        )

        # ── Simulated ECG/IMU from pose duel result ────────────────────────────
        @app.callback(
            Output("replay-sim-ecg",        "data"),
            Output("replay-sim-imu",        "data"),
            Output("replay-sim-rest-bands", "data"),
            Output("replay-sim-section",    "style"),
            Output("replay-time-poll",      "disabled", allow_duplicate=True),
            Input("pose-results",           "data"),
            State("replay-video-store",     "data"),
            prevent_initial_call=True,
        )
        def populate_sim_from_pose(pose_data, video_store):
            hidden = {"display": "none"}
            if not pose_data:
                return [], [], [], hidden, no_update
            pose_full = _resolve_pose_report_data(pose_data)
            if not pose_full:
                return [], [], [], hidden, no_update
            try:
                from pose_analyzer import simulate_duel_ecg_imu as _sim_fn
                sim = _sim_fn(pose_full, for_target="blue")
            except Exception:
                return [], [], [], hidden, no_update

            ecg_raw    = sim.get("ecg", [])
            imu_raw    = sim.get("imu", [])
            rest_bands = sim.get("rest_bands", [])
            if not ecg_raw and not imu_raw:
                return [], [], [], hidden, no_update

            ecg_pts = [{"t": float(e["t"]), "y": float(e["hr"])} for e in ecg_raw]
            _ev_map = {"impacto": "dado", "movimiento": "ruido"}
            imu_pts = [
                {"t": float(e["t"]), "intensity": float(e["g"]),
                 "type": _ev_map.get(e.get("event", "ruido"), "ruido")}
                for e in imu_raw
            ]

            has_video = bool((video_store or {}).get("url"))
            return ecg_pts, imu_pts, rest_bands, {"display": "block"}, not has_video

        def _rest_band_shapes(rest_bands: list) -> list:
            """Build Plotly rect shapes for rest periods (amber semi-transparent bands)."""
            shapes = []
            for b in (rest_bands or []):
                shapes.append({
                    "type": "rect",
                    "x0": b["t0"], "x1": b["t1"],
                    "y0": 0, "y1": 1, "yref": "paper",
                    "fillcolor": "rgba(240,168,50,0.12)",
                    "line": {"width": 0},
                    "layer": "below",
                })
            return shapes

        @app.callback(
            Output("replay-sim-hr-chart",  "figure"),
            Output("replay-sim-imu-chart", "figure"),
            Input("replay-sim-ecg",        "data"),
            Input("replay-sim-imu",        "data"),
            Input("replay-sim-rest-bands", "data"),
            prevent_initial_call=True,
        )
        def render_sim_charts(ecg_pts, imu_pts, rest_bands):
            _raw_bands = _rest_band_shapes(rest_bands)
            _placeholder = {
                "type": "line", "x0": 0, "x1": 0, "y0": 0, "y1": 0,
                "opacity": 0, "line": {"width": 0},
            }
            if _raw_bands:
                band_shapes = [_placeholder] + _raw_bands
            else:
                band_shapes = [_placeholder]

            if ecg_pts:
                xs = [p["t"] for p in ecg_pts]
                ys = [p["y"] for p in ecg_pts]
                hr_fig = go.Figure(go.Scatter(
                    x=xs, y=ys, mode="lines+markers",
                    line={"color": "#27c98f", "width": 1.6},
                    marker={"size": 3},
                    hovertemplate="t=%{x}s  FC=%{y:.0f} bpm<extra></extra>",
                ))
            else:
                hr_fig = empty_figure(message="Sin datos — ejecuta el análisis de video primero", height=160)
            hr_fig = apply_chart_style(hr_fig, height=160)
            hr_fig.update_layout(
                margin={"t": 4, "b": 24, "l": 36, "r": 8},
                xaxis_title="t (s)", yaxis_title="bpm",
                xaxis={"fixedrange": True},
                yaxis={"fixedrange": True, "range": [70, 210]},
                showlegend=False,
                shapes=band_shapes,  # cursor added by clientside; index 0 reserved
            )

            if imu_pts:
                _HIT_C = {"dado": "#e45a5a", "ruido": "rgba(143,163,191,0.35)"}
                _HIT_L = {"dado": "Impacto", "ruido": "Movimiento"}
                traces: dict[str, dict] = {}
                for pt in imu_pts:
                    htype = pt.get("type", "ruido")
                    if htype not in traces:
                        traces[htype] = {"xs": [], "ys": []}
                    t = pt.get("t", 0)
                    g = pt.get("intensity", 0)
                    traces[htype]["xs"].extend([t, t, None])
                    traces[htype]["ys"].extend([0.0, g, None])
                imu_fig = go.Figure()
                for htype, data in traces.items():
                    imu_fig.add_trace(go.Scatter(
                        x=data["xs"], y=data["ys"], mode="lines",
                        line={"color": _HIT_C.get(htype, "#aaa"), "width": 1.5},
                        name=_HIT_L.get(htype, htype), hoverinfo="skip",
                    ))
            else:
                imu_fig = empty_figure(message="Sin datos — ejecuta el análisis de video primero", height=140)
            imu_fig = apply_chart_style(imu_fig, height=140)
            imu_fig.update_layout(
                margin={"t": 4, "b": 24, "l": 36, "r": 8},
                xaxis_title="t (s)", yaxis_title="g",
                xaxis={"fixedrange": True},
                yaxis={"fixedrange": True},
                showlegend=bool(imu_pts),
                legend={"font": {"size": 9}, "orientation": "h", "y": 1.12, "x": 0},
                shapes=band_shapes,
            )

            return hr_fig, imu_fig

        # ── End of Video Replay callbacks ──────────────────────────────────────

        def _safe_int(x):
            try:
                return int(x)
            except Exception:
                return None

        def _coach_sport():
            return str(session.get("sport") or "").strip() or None

        def _can_access_athlete(athlete_id: int) -> bool:
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
                    return bool(db.coach_has_athlete(int(actor_id), int(aid), sport=_coach_sport()))
                except Exception:
                    try:
                        roster = db.list_roster_for_coach(int(actor_id), sport=_coach_sport()) or []
                        return any(_safe_int(a.get("id")) == aid for a in roster)
                    except Exception:
                        return False
            return False

        def _get_authorized_session(session_id: int):
            sid = _safe_int(session_id)
            if not sid:
                return None
            try:
                row = db.get_session(int(sid))
            except Exception:
                return None
            if not row or not _can_access_athlete(row.get("athlete_id")):
                return None
            return row

        def _session_belongs_to_user(session_id: int, user_id: int) -> bool:
            row = _get_authorized_session(session_id)
            return bool(row and _safe_int(row.get("athlete_id")) == _safe_int(user_id))

        def _path_under(path_value: str, base_dir: str):
            if not path_value:
                return None
            try:
                base = os.path.abspath(base_dir)
                candidate = os.path.abspath(path_value)
                common = os.path.commonpath([base, candidate])
            except Exception:
                return None
            if common != base or not os.path.exists(candidate):
                return None
            return candidate

        def _ecg_data_path(filename: str):
            fname = os.path.basename(str(filename or ""))
            return _path_under(os.path.join("data", "ecg", fname), os.path.join("data", "ecg"))

        _replay_ecg_cache = {}
        _replay_imu_cache = {}
        _replay_cache_max = 12

        def _cache_put(cache: dict, key, value):
            if len(cache) >= _replay_cache_max:
                try:
                    cache.pop(next(iter(cache)))
                except Exception:
                    cache.clear()
            cache[key] = value
            return value

        def _decimate_points(points: list, max_points: int):
            if not points or len(points) <= max_points:
                return points or []
            step = max(1, int(np.ceil(len(points) / float(max_points))))
            sampled = list(points[::step])
            if sampled and sampled[-1] != points[-1]:
                sampled.append(points[-1])
            return sampled

        def _load_replay_ecg_points(session_id: int) -> list:
            if not _get_authorized_session(session_id):
                return []
            ecg_pts = []
            try:
                ecg_files = db.list_ecg_files_by_session(int(session_id)) or []
                if ecg_files:
                    fname = os.path.basename(ecg_files[0].get("filename", ""))
                    fpath = _path_under(os.path.join(_ECG_DATA_DIR, fname), _ECG_DATA_DIR)
                    if fpath:
                        key = (int(session_id), fpath, os.path.getmtime(fpath))
                        if key in _replay_ecg_cache:
                            return _replay_ecg_cache[key]
                        with open(fpath, newline="", encoding="utf-8", errors="ignore") as fh:
                            reader = csv.DictReader(fh)
                            for row in reader:
                                try:
                                    ecg_pts.append({
                                        "t": float(row.get("time", row.get("t", 0))),
                                        "y": float(row.get("ecg", row.get("y", 0))),
                                    })
                                except (ValueError, TypeError):
                                    pass
            except Exception:
                return []
            return _cache_put(_replay_ecg_cache, key, ecg_pts) if "key" in locals() else ecg_pts

        def _load_replay_imu_points(session_id: int) -> list:
            if not _get_authorized_session(session_id):
                return []
            try:
                imu_files = db.list_imu_metrics_by_session(int(session_id)) or []
                if not imu_files:
                    return []
                stem = os.path.basename(imu_files[0].get("filename", ""))
                imu_path = _path_under(os.path.join(_ECG_DATA_DIR, f"{stem}.json"), _ECG_DATA_DIR)
                if not imu_path:
                    return []
                key = (int(session_id), imu_path, os.path.getmtime(imu_path))
                if key in _replay_imu_cache:
                    return _replay_imu_cache[key]
                with open(imu_path, encoding="utf-8", errors="ignore") as fh:
                    data = json.load(fh)
                data = data if isinstance(data, list) else []
                return _cache_put(_replay_imu_cache, key, data)
            except Exception:
                return []

        def _has_sensor(uid: int, sensor_key: str) -> bool:
            try:
                codes = set(db.get_user_sensors(int(uid)) or [])
            except Exception:
                codes = set()
            aliases = self._SENSOR_ALIASES.get(sensor_key, {sensor_key})
            return len(codes.intersection(aliases)) > 0

        def _lock_style(is_enabled: bool):
            if is_enabled:
                return {}
            return {
                "opacity": 0.35,
                "pointerEvents": "none",
                "filter": "grayscale(1)",
            }

        def _list_ecg_options(user_id: int):
            files = db.list_ecg_files(user_id) or []
            return [{"label": f["filename"], "value": f["id"]} for f in files if f.get("filename")]

        def _session_notes_payload(session_type, session_goal, session_structure, rounds_count, round_min, rest_sec):
            payload = {
                "session_type": session_type or None,
                "session_goal": session_goal or None,
                "session_structure": session_structure or None,
                "rounds_count": _safe_int(rounds_count),
                "round_min": _safe_int(round_min),
                "rest_sec": _safe_int(rest_sec),
            }
            return json.dumps(payload, ensure_ascii=False)

        def _session_context_text(s):
            notes = s.get("notes")
            if not notes:
                return ""
            try:
                payload = json.loads(notes)
            except Exception:
                return ""
            stype     = payload.get("session_type") or ""
            goal      = payload.get("session_goal") or ""
            structure = payload.get("session_structure") or ""
            rounds_count = payload.get("rounds_count")
            parts = [_session_value_label(stype, SESSION_TYPE_LABELS) or "Sesión"]
            if goal:
                parts.append(f"objetivo {_session_value_label(goal, SESSION_GOAL_LABELS)}")
            if structure == "rounds" and rounds_count:
                parts.append(f"{rounds_count} rounds")
            elif structure:
                parts.append(_session_value_label(structure, SESSION_STRUCTURE_LABELS))
            return " · ".join(parts)

        def _session_label(s: dict) -> str:
            sid   = s.get("id", "?")
            notes = s.get("notes") or ""
            ts    = (s.get("ts_start") or "")
            # Format date as dd/mm HH:MM
            date_str = ""
            if ts:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(ts[:19])
                    date_str = dt.strftime("%d/%m %H:%M")
                except Exception:
                    date_str = ts[:16].replace("T", " ")
            st = s.get("status") or ""
            open_tag = " 🔴 abierta" if st == "open" else ""

            if notes.startswith("Combat Monitor"):
                # Parse: "Combat Monitor — Taekwondo (WT) · 3 rounds · Peak BPM 174 · 19 impactos"
                import re as _re
                rounds_m = _re.search(r'(\d+) rounds?', notes)
                bpm_m    = _re.search(r'Peak BPM (\d+)', notes)
                imp_m    = _re.search(r'(\d+) impactos?', notes)
                sport    = s.get("sport") or "Combate"
                rounds_s = f"{rounds_m.group(1)} rounds" if rounds_m else ""
                bpm_s    = f"BPM {bpm_m.group(1)}" if bpm_m else ""
                imp_s    = f"{imp_m.group(1)} golpes" if imp_m else ""
                parts    = [p for p in [sport, rounds_s, bpm_s, imp_s] if p]
                return f"🥊 📊 {' · '.join(parts)} — {date_str}{open_tag}"

            ctx = _session_context_text(s)
            base = ctx if ctx else (notes[:40] + "…" if len(notes) > 40 else notes) if notes else "Sesión"
            return f"#{sid} · {base} — {date_str}{open_tag}"

        def _list_session_options(athlete_id: int):
            try:
                sessions = db.list_sessions(int(athlete_id), limit=50) or []
            except Exception:
                return []
            return [{"label": _session_label(s), "value": s.get("id")}
                    for s in sessions if s.get("id") is not None]

        # ✅ (PASO 3) GATING POR SENSORES (sin romper callbacks)
        @app.callback(
            Output("signals-sensors-banner", "children"),
            Output("ecg-lock-msg", "children"),
            Output("ecg-lock-wrapper", "style"),
            Output("imu-lock-msg", "children"),
            Output("imu-lock-wrapper", "style"),
            Input("ecg-user", "value"),
            prevent_initial_call=True,
        )
        def gate_sections(user_id):
            if not user_id:
                return "", "", {}, "", {}

            uid = _safe_int(user_id)
            if not uid:
                return "", "", {}, "", {}
            if not _can_access_athlete(uid):
                return "No tienes permisos para ver sensores de este deportista.", "", _lock_style(False), "", _lock_style(False)

            ecg_ok = _has_sensor(uid, "ECG")
            imu_ok = _has_sensor(uid, "IMU")

            enabled = [k for k, ok in [("ECG", ecg_ok), ("IMU", imu_ok)] if ok]
            missing = [k for k, ok in [("ECG", ecg_ok), ("IMU", imu_ok)] if not ok]

            banner = f"Lectura disponible hoy: {', '.join(enabled) if enabled else '—'}"
            if missing:
                banner += f" · Falta activar: {', '.join(missing)}"

            ecg_msg = "" if ecg_ok else "Activa ECG en Sensores para ver la lectura cardiovascular del día."
            imu_msg = "" if imu_ok else "Activa IMU en Sensores para revisar ritmo, impacto y explosividad."

            return (
                banner,
                ecg_msg, _lock_style(ecg_ok),
                imu_msg, _lock_style(imu_ok),
            )

        @app.callback(
            Output("imu-tabs", "options"),
            Output("imu-tabs", "value"),
            Output("imu-sport-banner", "children"),
            Output("imu-format-help", "children"),
            Input("ecg-user", "value"),
            State("imu-tabs", "value"),
            prevent_initial_call=True,
        )
        def adapt_imu_by_sport(user_id, current_tab):
            uid = _safe_int(user_id)
            sport = None
            if uid and _can_access_athlete(uid):
                try:
                    sport = (db.get_user_by_id(int(uid)) or {}).get("sport")
                except Exception:
                    sport = None
            profile = self._imu_profile(sport)
            options = [{"label": tab["label"], "value": tab["value"]} for tab in profile["tabs"]]
            valid_values = {tab["value"] for tab in profile["tabs"]}
            value = current_tab if current_tab in valid_values else (profile["tabs"][0]["value"] if profile["tabs"] else "imu-arm")
            return options, value, f"{profile['headline']} · {profile['subline']}", profile["format_help"]

        # ✅ Sesiones: cargar / crear / cerrar con un solo callback (sin outputs duplicados)
        @app.callback(
            Output("signals-session", "options"),
            Output("signals-session", "value"),
            Output("session-msg", "children"),
            Input("ecg-user", "value"),
            Input("btn-new-session", "n_clicks"),
            Input("btn-close-session", "n_clicks"),
            State("signals-session", "value"),
            State("session-type", "value"),
            State("session-goal", "value"),
            State("session-structure", "value"),
            State("session-rounds", "value"),
            State("session-round-min", "value"),
            State("session-rest-sec", "value"),
            State("ecg-url", "search"),
            prevent_initial_call=True,
        )
        def session_ui(user_id, n_new, n_close, current_session_id, session_type, session_goal, session_structure, rounds_count, round_min, rest_sec, url_search):
            if not user_id:
                return [], None, ""

            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            uid = _safe_int(user_id)
            if not uid:
                return [], None, ""
            if not _can_access_athlete(uid):
                return [], None, "No tienes permisos para gestionar sesiones de este deportista."

            # Cerrar sesión
            if trig.startswith("btn-close-session") and n_close:
                sid = _safe_int(current_session_id)
                if not sid:
                    opts = _list_session_options(uid)
                    return opts, None, "Selecciona una sesión para cerrarla."
                if not _session_belongs_to_user(sid, uid):
                    opts = _list_session_options(uid)
                    return opts, None, "No tienes permisos para cerrar esa sesión."
                try:
                    db.close_session(int(sid))
                except Exception:
                    opts = _list_session_options(uid)
                    return opts, None, "No se pudo cerrar la sesión (DB)."
                opts = _list_session_options(uid)
                return opts, None, f"Sesión #{sid} cerrada."

            # Crear sesión nueva
            if trig.startswith("btn-new-session") and n_new:
                created_by = session.get("user_id")
                created_by = _safe_int(created_by) if created_by is not None else None
                sport = None
                try:
                    u = db.get_user_by_id(int(uid))
                    sport = (u or {}).get("sport")
                except Exception:
                    sport = None
                notes = _session_notes_payload(session_type, session_goal, session_structure, rounds_count, round_min, rest_sec)
                try:
                    sid = db.create_session(int(uid), created_by=created_by, sport=sport, notes=notes)
                except Exception:
                    opts = _list_session_options(uid)
                    return opts, None, "No se pudo crear la sesión (DB)."
                opts = _list_session_options(uid)
                details = []
                if session_type:
                    details.append(str(session_type).replace("_", " "))
                if session_structure == "rounds" and _safe_int(rounds_count):
                    details.append(f"{_safe_int(rounds_count)} rounds")
                elif session_structure:
                    details.append(str(session_structure).replace("_", " "))
                extra = f" ({' · '.join(details)})" if details else ""
                return opts, sid, f"Sesión #{sid} creada y activada{extra}."

            # Cambio de usuario (o carga): listar sesiones y autoseleccionar
            opts = _list_session_options(uid)
            chosen = None

            # Pre-selección desde URL params (?session=N&tab=signals)
            if url_search:
                try:
                    from urllib.parse import parse_qs as _pqs
                    _p = _pqs((url_search or "").lstrip("?"))
                    _tab = (_p.get("tab", [None])[0] or "")
                    _sid = _p.get("session", [None])[0]
                    if _sid and _tab != "replay":
                        sid_from_url = _safe_int(_sid)
                        if sid_from_url and _session_belongs_to_user(sid_from_url, uid):
                            chosen = sid_from_url
                except Exception:
                    chosen = None

            if chosen is None:
                try:
                    sessions = db.list_sessions(int(uid), limit=50) or []
                    open_s = next((s for s in sessions if (s.get("status") == "open")), None)
                    if open_s:
                        chosen = open_s.get("id")
                    elif sessions:
                        chosen = sessions[0].get("id")
                except Exception:
                    chosen = None

            return opts, chosen, ""

        # ── Auto-select ECG file when a Combat Monitor session is chosen ──────

        @app.callback(
            Output("ecg-file",  "value",    allow_duplicate=True),
            Output("ecg-msg",   "children", allow_duplicate=True),
            Output("ecg-graph", "figure",   allow_duplicate=True),
            Output("ecg-kpis",  "children", allow_duplicate=True),
            Input("signals-session", "value"),
            State("ecg-user",        "value"),
            prevent_initial_call=True,
        )
        def auto_select_ecg_for_session(session_id, user_id):
            _no_render = no_update, no_update
            if not session_id:
                return (
                    None,
                    "Selecciona una sesión para ver la señal ECG.",
                    empty_figure(message="Selecciona una sesión para ver ECG", height=420),
                    [],
                )
            sid = _safe_int(session_id)
            if not sid:
                return (
                    None,
                    "Selecciona una sesión válida para ver la señal ECG.",
                    empty_figure(message="Selecciona una sesión válida", height=420),
                    [],
                )
            uid = _safe_int(user_id)
            if not (uid and _session_belongs_to_user(sid, uid)):
                return no_update, "No tienes permisos para cargar esa sesión.", *_no_render
            try:
                rows = db.list_ecg_files_by_session(sid) or []
            except Exception:
                return no_update, "", *_no_render
            if not rows:
                return no_update, "Esta sesión no tiene señal ECG registrada.", *_no_render
            row = rows[0]
            fid = row["id"]

            # Direct render (same logic as render_ecg) so the graph updates immediately
            try:
                path = _ecg_data_path(row["filename"])
                if not path:
                    return fid, "ECG cargado de la sesión.", *_no_render
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
                if x is None or len(x) == 0:
                    return fid, "ECG cargado de la sesión.", *_no_render
                xs, peaks = _cached_ecg_process(path, x, fs, 0, 0.6)
                bpm, sdnn, rmssd = ecg_metrics_from_peaks(
                    peaks if peaks is not None else np.array([]), fs
                )
                if bpm == 0.0:
                    try:
                        stored = db.get_last_ecg_metrics(uid)
                        if stored and stored.get("bpm", 0) > 0:
                            bpm   = float(stored["bpm"])
                            sdnn  = float(stored.get("sdnn", 0.0))
                            rmssd = float(stored.get("rmssd", 0.0))
                    except Exception:
                        pass
                t_rel = t - t[0]
                dur = float(t_rel[-1]) if len(t_rel) > 0 else 0.0
                wt_bands = _wt_bands(dur) if 300.0 <= dur <= 420.0 else None
                max_pts = 4000
                step = int(np.ceil(len(t_rel) / max_pts)) if len(t_rel) > max_pts else 1
                fig = fig_ecg(t_rel[::step], xs[::step], title=row["filename"], bands=wt_bands)
                kpis = kpi_grid_ecg(bpm, sdnn, rmssd)
                return fid, "ECG cargado de la sesión.", fig, kpis
            except Exception:
                return fid, "ECG cargado de la sesión.", *_no_render

        # ── Auto-load IMU stem from Combat Monitor session sidecar ─────────────

        _SIGNALS_ECG_DIR = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "ecg"
        )
        _HIT_STYLES_REPLAY = {
            "dado":     {"color": "#27c98f", "label": "Dado",      "width": 2.0},
            "recibido": {"color": "#e45a5a", "label": "Recibido",  "width": 2.0},
            "ruido":    {"color": "rgba(143,163,191,0.30)", "label": None, "width": 1.0},
        }

        _MSG_NO_IMU_COMBAT = "Esta sesión no tiene datos IMU registrados."
        _MSG_NO_SIDECAR    = "Esta sesión no tiene datos IMU registrados."
        _MSG_IMU_EMPTY     = "El archivo IMU no contiene eventos."

        # Qué tipos de eventos mostrar por tab según el deporte
        _IMU_TAB_FILTER = {
            "taekwondo": {
                "imu-leg":  ["dado"],                    # Patadas — ataques propios
                "imu-arm":  ["dado", "recibido", "ruido"],  # Desplazamiento — actividad total
                "imu-head": ["recibido"],                # Tronco/peto — impactos recibidos
            },
            "boxeo": {
                "imu-arm":  ["dado"],                    # Golpes de mano
                "imu-head": ["recibido"],                # Guardia/tronco — recibidos
                "imu-leg":  ["dado", "recibido", "ruido"],  # Desplazamiento — total
            },
        }
        _IMU_TAB_FILTER["general"] = {k: ["dado", "recibido", "ruido"]
                                       for k in ("imu-arm", "imu-leg", "imu-head")}

        def _as_float(value, default=0.0):
            try:
                return float(value)
            except Exception:
                return default

        def _load_imu_event_sidecar(path_value: str) -> list:
            sidecar_path = _path_under(path_value, _SIGNALS_ECG_DIR)
            if not sidecar_path:
                return []
            try:
                with open(sidecar_path, encoding="utf-8", errors="ignore") as fh:
                    data = json.load(fh)
                return data if isinstance(data, list) else []
            except Exception:
                return []

        def _imu_sidecar_path_from_row(row: dict):
            stem = os.path.basename((row or {}).get("filename", ""))
            if not stem:
                return None
            return _path_under(os.path.join(_SIGNALS_ECG_DIR, f"{stem}.json"), _SIGNALS_ECG_DIR)

        def _build_session_imu_meta(row: dict, sid: int, uid: int, kind: str,
                                    athlete_sport: str | None, title: str):
            sidecar_path = _imu_sidecar_path_from_row(row)
            uid = _safe_int(uid)
            if not sidecar_path or not uid:
                return None
            return {
                "source": "session_events",
                "format": "event_json",
                "path": sidecar_path,
                "title": title,
                "uid": int(uid),
                "kind": kind,
                "sport": self._normalize_sport(athlete_sport),
                "filename": os.path.basename(sidecar_path),
                "session_id": int(sid),
                "n_hits": int(_as_float(row.get("n_hits"), 0)),
                "hits_per_min": _as_float(row.get("hits_per_min"), 0.0),
                "mean_int_g": _as_float(row.get("mean_int_g"), 0.0),
                "max_int_g": _as_float(row.get("max_int_g"), 0.0),
            }

        def _session_imu_meta_from_state(session_id, user_id, imu_kind=None):
            sid = _safe_int(session_id)
            if not sid:
                return None
            session_row = _get_authorized_session(sid)
            if not session_row:
                return None
            uid = _safe_int(user_id) or _safe_int(session_row.get("athlete_id"))
            if not uid or not _session_belongs_to_user(sid, uid):
                return None
            try:
                athlete = db.get_user_by_id(int(uid)) or {}
            except Exception:
                athlete = {}
            athlete_sport = athlete.get("sport")
            imu_profile = self._imu_profile(athlete_sport)
            kind = imu_kind or (imu_profile["tabs"][0]["value"] if imu_profile["tabs"] else "imu-arm")
            tab_title = imu_profile["title_prefixes"].get(kind, "Impactos")
            try:
                rows = db.list_imu_metrics_by_session(int(sid)) or []
            except Exception:
                rows = []
            if not rows:
                return None
            return _build_session_imu_meta(
                rows[0],
                sid,
                uid,
                kind,
                athlete_sport,
                f"{tab_title} - sesión #{sid}",
            )

        @app.callback(
            Output("imu-graph", "figure",   allow_duplicate=True),
            Output("imu-kpis",  "children", allow_duplicate=True),
            Output("imu-msg",   "children", allow_duplicate=True),
            Output("imu-meta",  "data",     allow_duplicate=True),
            Input("signals-session", "value"),
            Input("imu-tabs",        "value"),   # re-render al cambiar de pill
            State("ecg-user",        "value"),
            prevent_initial_call=True,
        )
        def auto_load_imu_for_session(session_id, imu_kind, user_id):
            _empty = empty_figure(message="Sin datos IMU", height=360)
            if not session_id:
                return (
                    empty_figure(message="Selecciona una sesión para ver IMU", height=360),
                    [],
                    "Selecciona una sesión para ver la señal IMU.",
                    None,
                )
            sid = _safe_int(session_id)
            if not sid:
                return (
                    empty_figure(message="Selecciona una sesión válida", height=360),
                    [],
                    "Selecciona una sesión válida para ver la señal IMU.",
                    None,
                )
            uid = _safe_int(user_id)
            if uid:
                if not _session_belongs_to_user(sid, uid):
                    return _empty, [], "No tienes permisos para ver esa sesión.", None
            elif not _get_authorized_session(sid):
                return _empty, [], "No tienes permisos para ver esa sesión.", None

            # Solo actúa sobre sesiones Combat Monitor
            try:
                s = db.get_session(sid)
            except Exception:
                raise PreventUpdate
            if not s or not (s.get("notes") or "").startswith("Combat Monitor"):
                return _empty, [], _MSG_NO_IMU_COMBAT, None

            # Perfil IMU del deportista para títulos y filtros
            athlete_sport = None
            athlete_uid = uid or _safe_int(s.get("athlete_id"))
            if user_id:
                try:
                    athlete_sport = (db.get_user_by_id(int(_safe_int(user_id))) or {}).get("sport")
                except Exception:
                    pass
            elif athlete_uid:
                try:
                    athlete_sport = (db.get_user_by_id(int(athlete_uid)) or {}).get("sport")
                except Exception:
                    pass
            imu_profile  = self._imu_profile(athlete_sport)
            sport_key    = imu_profile.get("sport_key", "general")
            kind         = imu_kind or (imu_profile["tabs"][0]["value"] if imu_profile["tabs"] else "imu-arm")
            tab_title    = imu_profile["title_prefixes"].get(kind, "Impactos")
            tab_subtitle = imu_profile["message_suffixes"].get(kind, "")

            # Obtener fila de métricas de la BD
            try:
                imu_rows = db.list_imu_metrics_by_session(sid) or []
            except Exception:
                return _empty, [], _MSG_NO_SIDECAR, None
            if not imu_rows:
                return _empty, [], _MSG_NO_SIDECAR, None

            # KPIs siempre desde la BD (disponibles aunque falte el sidecar)
            row      = imu_rows[0]
            n_hits   = row.get("n_hits",       0)   or 0
            hpm      = row.get("hits_per_min",  0.0) or 0.0
            mean_int = row.get("mean_int_g",    0.0) or 0.0
            max_int  = row.get("max_int_g",     0.0) or 0.0
            kpis     = kpi_grid_imu(n_hits, hpm, mean_int, max_int)
            meta     = _build_session_imu_meta(
                row,
                sid,
                athlete_uid or row.get("user_id"),
                kind,
                athlete_sport,
                f"{tab_title} - sesión #{sid}",
            )

            # Buscar archivo sidecar
            sidecar_path = _imu_sidecar_path_from_row(row)

            if not sidecar_path:
                # Sin sidecar: mostramos KPIs de la BD y mensaje claro
                msg_fig = empty_figure(
                    message=f"Métricas guardadas — {n_hits} eventos · {mean_int:.1f} g medio",
                    height=360,
                )
                return msg_fig, kpis, (
                    f"Los datos individuales de esta sesión no están disponibles. "
                    f"Métricas totales: {n_hits} eventos, {hpm:.1f} acc/min, "
                    f"{mean_int:.2f} g medio, {max_int:.2f} g máx."
                ), None

            events = _load_imu_event_sidecar(sidecar_path)
            if not events:
                return _empty, kpis, _MSG_IMU_EMPTY, None

            # ── Desplazamiento: vista de densidad de actividad ─────────────────
            chart_modes = imu_profile.get("chart_mode", {})
            if chart_modes.get(kind) == "density":
                meaningful = [e for e in events if e.get("type") in ("dado", "recibido")]
                all_times  = [e.get("t", 0) for e in meaningful]
                if not all_times:
                    return empty_figure(message="Sin actividad registrada", height=360), kpis, tab_subtitle, meta
                max_t    = max(all_times)
                min_t    = min(all_times)
                bin_size = 5.0
                bins     = np.arange(0, max_t + bin_size, bin_size)
                counts, edges = np.histogram(all_times, bins=bins)
                bin_centers   = (edges[:-1] + edges[1:]) / 2
                n_dado    = sum(1 for e in meaningful if e.get("type") == "dado")
                n_recib   = sum(1 for e in meaningful if e.get("type") == "recibido")
                n_total   = n_dado + n_recib
                efectividad = n_dado / n_total * 100 if n_total > 0 else 0
                duration    = max_t - min_t if max_t > min_t else max_t
                event_rate  = n_total / duration * 60 if duration > 0 else 0
                den_fig = go.Figure()
                den_fig.add_trace(go.Bar(
                    x=bin_centers,
                    y=counts,
                    name="Actividad",
                    marker=dict(color="#f0a832", opacity=0.85, line=dict(width=0)),
                    hovertemplate="%{x:.0f}s — %{y} acciones<extra></extra>",
                ))
                apply_chart_style(
                    den_fig,
                    title=f"{tab_title} — ritmo de actividad",
                    x_title="tiempo (s)",
                    y_title="acciones / 5 s",
                    height=420,
                )
                den_fig.add_annotation(
                    text=f"Efectividad: {efectividad:.0f}%  ·  Ritmo: {event_rate:.1f} acc/min",
                    showarrow=False,
                    x=0.5, y=1.14,
                    xref="paper", yref="paper",
                    font=dict(size=12, color="#C9D3E3"),
                    bgcolor="rgba(14,21,32,0.55)",
                    borderpad=5,
                )
                return den_fig, kpis, tab_subtitle, meta

            # Filtrar eventos según el tab y el deporte
            allowed = (_IMU_TAB_FILTER.get(sport_key) or _IMU_TAB_FILTER["general"]).get(
                kind, ["dado", "recibido", "ruido"]
            )

            fig    = go.Figure()
            groups = {k: {"t": [], "g": [], "hover": []} for k in _HIT_STYLES_REPLAY}
            for evt in events:
                htype = evt.get("type", "ruido")
                if htype not in groups:
                    htype = "ruido"
                if htype not in allowed:
                    continue
                rnd = evt.get("round", "?")
                groups[htype]["t"].append(evt.get("t", 0))
                groups[htype]["g"].append(evt.get("intensity", 0))
                groups[htype]["hover"].append(
                    f"R{rnd} · {evt.get('t', 0):.1f}s — {evt.get('intensity', 0):.2f} g"
                )

            has_data = any(bool(v["t"]) for v in groups.values())
            if not has_data:
                no_data_fig = empty_figure(
                    message=f"Sin eventos para '{tab_title}'", height=360
                )
                return no_data_fig, kpis, tab_subtitle, meta

            for htype, data in groups.items():
                if not data["t"]:
                    continue
                style = _HIT_STYLES_REPLAY[htype]
                xs, ys = [], []
                for t, g in zip(data["t"], data["g"]):
                    xs.extend([t, t, None])
                    ys.extend([0.0, g, None])
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines",
                    name=style["label"] or "",
                    showlegend=style["label"] is not None,
                    line=dict(color=style["color"], width=style["width"]),
                    hoverinfo="skip",
                ))
                if htype != "ruido":
                    fig.add_trace(go.Scatter(
                        x=data["t"], y=data["g"],
                        mode="markers", showlegend=False,
                        marker=dict(color=style["color"], size=5),
                        customdata=data["hover"],
                        hovertemplate="%{customdata}<extra></extra>",
                    ))

            apply_chart_style(fig, title=f"{tab_title} — sesión de combate",
                              x_title="tiempo (s)", y_title="intensidad (g)", height=420)
            fig.update_layout(
                showlegend=True,
                legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center",
                            font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
            )
            return fig, kpis, tab_subtitle, meta

        @app.callback(
            Output("ecg-file", "options"),
            Input("ecg-user", "value"),
            prevent_initial_call=True
        )
        def refresh_user_files(user_id):
            if not user_id:
                raise PreventUpdate
            uid = _safe_int(user_id)
            if not uid:
                raise PreventUpdate
            if not _can_access_athlete(uid):
                raise PreventUpdate
            if not _has_sensor(uid, "ECG"):
                return []
            return _list_ecg_options(uid)

        @app.callback(
            Output("ecg-file", "options", allow_duplicate=True),
            Output("ecg-file", "value", allow_duplicate=True),
            Output("ecg-msg", "children"),
            Input("ecg-upload", "contents"),
            State("ecg-upload", "filename"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            prevent_initial_call=True
        )
        def on_upload(content, filename, user_id, session_id):
            if not user_id:
                return dash.no_update, dash.no_update, "Selecciona usuario antes de subir."
            uid = _safe_int(user_id)
            if not uid:
                return dash.no_update, dash.no_update, "Usuario inválido."
            if not _can_access_athlete(uid):
                return dash.no_update, dash.no_update, "No tienes permisos para subir datos de este deportista."
            if not _has_sensor(uid, "ECG"):
                return dash.no_update, dash.no_update, "Este deportista no tiene ECG asignado."
            if not content:
                raise PreventUpdate

            try:
                data = _b64_to_bytes(content)
            except Exception:
                return dash.no_update, dash.no_update, "No se pudo leer el archivo (base64 inválido)."

            try:
                final_name = _save_unique(os.path.join("data", "ecg"), filename or "ecg.csv", data)
            except Exception:
                return dash.no_update, dash.no_update, "Error guardando el archivo en disco."

            save_path = os.path.join("data", "ecg", final_name)

            try:
                _, x, fs = read_ecg_csv(save_path, fs_default=250)
            except Exception as e:
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return dash.no_update, dash.no_update, f"Error leyendo el CSV: {e}"

            if x is None or len(x) == 0:
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return dash.no_update, dash.no_update, "El archivo no contiene datos de ECG válidos."

            sid = _safe_int(session_id)
            if sid and not _session_belongs_to_user(sid, uid):
                return dash.no_update, dash.no_update, "La sesión seleccionada no pertenece a este deportista."

            # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
            auto_note = ""
            if not sid and hasattr(db, "ensure_open_session"):
                try:
                    actor_id = _safe_int(session.get("user_id"))
                    athlete = db.get_user_by_id(int(uid))
                    sport = athlete.get("sport") if athlete else None
                    sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    auto_note = " (Se creó sesión abierta automáticamente.)"
                except Exception:
                    sid = None

            try:
                ecg_id = db.add_ecg_file(uid, final_name, int(fs), session_id=sid)
            except TypeError:
                # DB legacy sin session_id
                ecg_id = db.add_ecg_file(uid, final_name, int(fs))

            opts = _list_ecg_options(uid)
            return opts, ecg_id, f"Archivo {final_name} guardado."

        @app.callback(
            Output("ecg-window", "max"),
            Output("ecg-window", "value"),
            Output("ecg-window", "marks"),
            Input("ecg-file", "value"),
            Input("ecg-winlen", "value"),
            State("ecg-user", "value"),
            prevent_initial_call=True
        )
        def sync_ecg_window(ecg_id, winlen, user_id):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate
            if not _can_access_athlete(uid):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = _ecg_data_path(row["filename"])
            if not path:
                raise PreventUpdate

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception:
                raise PreventUpdate

            if t is None or len(t) < 2:
                raise PreventUpdate

            dur = float(t[-1] - t[0])
            if dur <= 0:
                raise PreventUpdate

            wl = int(winlen or 10)
            if wl <= 0:
                return dur, [0.0, dur], self._sparse_marks(dur)

            start = max(0.0, dur - float(wl))
            return dur, [start, dur], self._sparse_marks(dur)


        _WT_FIGHT = [(0, 120, "R1"), (180, 300, "R2"), (360, 375.4, "R3")]
        _WT_REST  = [(120, 180), (300, 360)]

        def _wt_bands(dur):
            bands = []
            for t0, t1, label in _WT_FIGHT:
                if t0 < dur:
                    bands.append({"x0": t0, "x1": min(t1, dur), "color": "#2fb7c4",
                                  "opacity": 0.10, "label": label, "label_color": "#2fb7c4"})
            for t0, t1 in _WT_REST:
                if t0 < dur:
                    bands.append({"x0": t0, "x1": min(t1, dur), "color": "#888888",
                                  "opacity": 0.07, "label": "", "label_color": ""})
            return bands

        @app.callback(
            Output("ecg-graph", "figure"),
            Output("ecg-kpis", "children"),
            Input("ecg-file", "value"),
            Input("ecg-showr", "value"),
            Input("ecg-sens", "value"),
            Input("ecg-smooth", "value"),
            Input("ecg-window", "value"),
            Input("ecg-quality", "value"),
            State("ecg-user", "value"),
        )
        def render_ecg(ecg_id, showr_list, sens, smooth_ms, win_range, quality, user_id):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate
            if not _can_access_athlete(uid):
                raise PreventUpdate

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = _ecg_data_path(row["filename"])
            if not path:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            if x is None or len(x) == 0:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            xs, peaks = _cached_ecg_process(path, x, fs, smooth_ms or 0, sens or 0.6)
            show_r = "r" in (showr_list or [])

            bpm, sdnn, rmssd = ecg_metrics_from_peaks(
                peaks if peaks is not None else np.array([]),
                fs
            )

            # Fallback to stored metrics when live computation fails (e.g. fs < 50 Hz)
            if bpm == 0.0 and uid:
                try:
                    stored = db.get_last_ecg_metrics(uid)
                    if stored and stored.get("bpm", 0) > 0:
                        bpm   = float(stored["bpm"])
                        sdnn  = float(stored.get("sdnn", 0.0))
                        rmssd = float(stored.get("rmssd", 0.0))
                except Exception:
                    pass

            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            should_save = (trig.startswith("ecg-file.") or trig.startswith("ecg-showr."))
            if should_save and peaks is not None and len(peaks) > 1:
                try:
                    if hasattr(db, "save_ecg_metrics_latest"):
                        db.save_ecg_metrics_latest(fid, bpm, sdnn, rmssd, int(len(peaks)))
                    else:
                        db.save_ecg_metrics(fid, bpm, sdnn, rmssd, int(len(peaks)))
                except Exception:
                    pass

            # Ventana visible — slider opera en tiempo relativo (0..dur)
            # t puede tener offset absoluto (p.ej. 258s..273s); normalizar para buscar
            t_rel = t - t[0]
            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, float(t_rel[-1])
            except Exception:
                t0, t1 = 0.0, float(t_rel[-1])

            t0 = max(0.0, t0)
            t1 = max(t0 + 1e-6, t1)

            i0 = int(np.searchsorted(t_rel, t0, side="left"))
            i1 = int(np.searchsorted(t_rel, t1, side="right"))
            i0 = max(0, min(i0, len(t_rel) - 1))
            i1 = max(i0 + 1, min(i1, len(t_rel)))

            t_win = t_rel[i0:i1]   # tiempo relativo para que eje-x coincida con slider
            x_win = xs[i0:i1]

            # Downsampling para render pro (no afecta métricas)
            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            x_line = x_win[::step]

            peaks_t = None
            peaks_y = None
            if show_r and peaks is not None and len(peaks) > 0:
                try:
                    pw = peaks[(peaks >= i0) & (peaks < i1)] - i0
                    peaks_t = t_win[pw]
                    peaks_y = x_win[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            # WT combat zone shading when ECG covers a full 3-round match (~375s)
            dur = float(t_rel[-1]) if len(t_rel) > 0 else 0.0
            wt_bands = _wt_bands(dur) if 300.0 <= dur <= 420.0 else None

            fig = fig_ecg(t_line, x_line, peaks_t=peaks_t, peaks_y=peaks_y,
                          title=row["filename"], bands=wt_bands)
            kpis = kpi_grid_ecg(bpm, sdnn, rmssd)
            return fig, kpis

        @app.callback(
            Output("dl-png", "data"),
            Output("dl-png-clicks", "data"),
            Input("btn-dl-png", "n_clicks"),
            State("dl-png-clicks", "data"),
            State("ecg-graph", "figure"),
            prevent_initial_call=True
        )
        def download_png(n, last_n, fig_dict):
            if not n or (last_n is not None and n <= last_n):
                raise PreventUpdate

            fig = go.Figure(fig_dict)
            try:
                buf = fig.to_image(format="png", scale=2)
            except Exception:
                buf = _figure_png_fallback(fig_dict, "Gráfica ECG")
                if not buf:
                    return dcc.send_string("No se pudo exportar la grafica PNG.", "README.txt"), n

            return dcc.send_bytes(lambda b: b.write(buf), "ecg.png"), n

        @app.callback(
            Output("dl-peaks", "data"),
            Input("btn-dl-peaks", "n_clicks"),
            State("ecg-file", "value"),
            State("ecg-user", "value"),
            State("ecg-sens", "value"),
            State("ecg-smooth", "value"),
            prevent_initial_call=True
        )
        def download_peaks(n, ecg_id, user_id, sens, smooth_ms):
            if not n or not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate
            if not _can_access_athlete(uid):
                raise PreventUpdate

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = _ecg_data_path(row["filename"])
            if not path:
                raise PreventUpdate
            t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))

            xs, peaks = _cached_ecg_process(path, x, fs, smooth_ms or 0, sens or 0.6)
            peak_set = {int(idx) for idx in peaks.tolist()} if peaks is not None and len(peaks) > 0 else set()

            user_obj = db.get_user_by_id(uid) if hasattr(db, "get_user_by_id") else None
            athlete_name = (user_obj.get("name") or f"Atleta_{uid}") if user_obj else f"Atleta_{uid}"
            from datetime import datetime as _dt
            export_date = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            duration_s = round(float(t[-1]) - float(t[0]), 2) if len(t) > 1 else 0
            n_peaks = len(peak_set)

            from report_utils import safe_filename_stem, xlsx_table
            fc_est = round(n_peaks / (duration_s / 60)) if duration_s > 0 else 0
            meta = [
                ("Atleta",              athlete_name),
                ("Archivo",             row["filename"]),
                ("Frecuencia muestreo", f"{fs} Hz"),
                ("Duración total",      f"{duration_s} s  ({len(t)} muestras)"),
                ("Picos R detectados",  f"{n_peaks}  —  FC estimada: {fc_est} lpm"),
                ("Exportado",           export_date),
            ]
            headers_xl = [
                "Tiempo (s)",
                "ECG cruda (u.a.)",
                "ECG suavizada (u.a.)",
                "Pico R detectado",
            ]
            data_xl = [
                [
                    round(float(t[idx]), 6),
                    round(float(x[idx]), 6),
                    round(float(xs[idx]), 6),
                    1 if idx in peak_set else 0,
                ]
                for idx in range(len(t))
            ]
            xlsx_bytes = xlsx_table(
                f"Señal ECG — {athlete_name}",
                meta,
                headers_xl,
                data_xl,
                sheet_name="ECG",
                col_types={0: "number", 1: "number", 2: "number", 3: "int"},
            )
            safe_name  = safe_filename_stem(athlete_name, "deportista")
            export_day = _dt.utcnow().strftime("%Y%m%d")
            base       = os.path.splitext(os.path.basename(row["filename"]))[0]
            filename   = f"CombatIQ_ECG_{safe_name}_{export_day}_{base}.xlsx"
            return dcc.send_bytes(lambda b: b.write(xlsx_bytes), filename)

        @app.callback(
            Output("dl-ecg-report", "data"),
            Output("ecg-export-msg", "children"),
            Input("btn-dl-ecg-report", "n_clicks"),
            State("ecg-file", "value"),
            State("ecg-user", "value"),
            State("ecg-sens", "value"),
            State("ecg-smooth", "value"),
            State("ecg-graph", "figure"),
            State("signals-session", "value"),
            prevent_initial_call=True
        )
        def download_ecg_report(n, ecg_id, user_id, sens, smooth_ms, fig_dict, session_id):
            if not n:
                raise PreventUpdate
            if not (user_id and ecg_id):
                return dash.no_update, "Elige un archivo ECG antes de exportar el informe."

            if not _REPORTLAB_OK:
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                return dash.no_update, "No se pudo identificar el archivo ECG."
            if not _can_access_athlete(uid):
                return dash.no_update, "No tienes permisos para exportar datos de este deportista."

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                return dash.no_update, "No encuentro el archivo ECG seleccionado."

            path = _ecg_data_path(row["filename"])
            if not path:
                return dash.no_update, "No encuentro el archivo ECG en disco."

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception as e:
                return dash.no_update, f"No pude leer el archivo ECG: {e}"

            xs, peaks = _cached_ecg_process(path, x, fs, smooth_ms or 0, sens or 0.6)

            bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
            n_peaks = int(len(peaks)) if peaks is not None else 0

            athlete = db.get_user_by_id(int(uid)) or {}
            athlete_name = athlete.get("name", "Deportista")
            sport = athlete.get("sport", "-")

            sid = _safe_int(row.get("session_id"))
            sid_from_state = _safe_int(session_id)
            if sid_from_state and _session_belongs_to_user(sid_from_state, uid):
                sid = sid_from_state
            session_label = "Sin sesión asociada"
            if sid:
                session_label = f"Sesión #{sid}"
                try:
                    current_session = db.get_session(int(sid)) if hasattr(db, "get_session") else None
                except Exception:
                    current_session = None
                if current_session:
                    ts_start = (current_session.get("ts_start") or "")[:10]
                    session_type = (current_session.get("session_type") or "").replace("_", " ").strip()
                    parts = [f"Sesión #{sid}"]
                    if session_type:
                        parts.append(session_type)
                    if ts_start:
                        parts.append(ts_start)
                    session_label = " | ".join(parts)

            if n_peaks < 2:
                summary = (
                    "No hubo latidos útiles suficientes para una lectura completa. "
                    "Conviene revisar la calidad del archivo o ajustar la sensibilidad antes de sacar conclusiones."
                )
            elif rmssd >= 45 and sdnn >= 50:
                summary = (
                    "La señal sugiere una recuperación cardiovascular favorable. "
                    "Puede leerse como una base sólida para entrar a la sesión con buena respuesta."
                )
            elif rmssd >= 25 and sdnn >= 30:
                summary = (
                    "La lectura se ve estable. Lo más útil es interpretarla junto con la carga del día y la sensación del deportista."
                )
            else:
                summary = (
                    "La señal apunta a un día más sensible. Conviene vigilar la carga y revisar esta lectura junto con el check-in del deportista."
                )

            metric_lines = [
                f"Ritmo cardíaco: {_fmt_num(bpm, 0)} lpm. Frecuencia media estimada durante este registro.",
                f"Variabilidad: {_fmt_num(sdnn, 0)} ms. Ayuda a ver cuánto cambia el tiempo entre latidos.",
                f"Recuperación: {_fmt_num(rmssd, 0)} ms. Sirve como apoyo para leer cómo llega el deportista a la sesión.",
                f"Latidos útiles detectados: {n_peaks}. Son las referencias usadas para construir esta lectura.",
            ]

            explain_lines = [
                "La línea muestra la señal registrada a lo largo del tiempo.",
                "Si ves cruces sobre la línea, marcan los picos usados para calcular ritmo, variabilidad y recuperación.",
                "Lo más útil no es un número suelto, sino comparar esta lectura con otras sesiones del mismo deportista.",
            ]

            note_lines = [
                "Usa este informe como apoyo para el entrenamiento y la toma de decisiones del día.",
                "No sustituye una valoración clínica ni un diagnóstico médico.",
            ]

            if fig_dict:
                fig = go.Figure(fig_dict)
            else:
                peaks_t = t[peaks] if peaks is not None and len(peaks) > 0 else None
                peaks_y = xs[peaks] if peaks is not None and len(peaks) > 0 else None
                fig = fig_ecg(t, xs, peaks_t=peaks_t, peaks_y=peaks_y, title=row["filename"])
            _apply_signal_pdf_chart_style(
                fig,
                "Recuperacion cardiovascular",
                "Tiempo (s)",
                "Amplitud (a.u.)",
            )

            # Status del badge según métricas
            if n_peaks < 2:
                _status, _status_lbl = "alert", "Señal insuficiente — revisar calidad del archivo"
            elif rmssd >= 45 and sdnn >= 50:
                _status, _status_lbl = "ok",   "Recuperacion cardiovascular favorable"
            elif rmssd >= 25 and sdnn >= 30:
                _status, _status_lbl = "warn",  "Lectura estable — monitorear en contexto"
            else:
                _status, _status_lbl = "alert", "Dia sensible — vigilar carga de entrenamiento"

            pdf_bytes = _build_signal_report_pdf(
                report_title="Informe de recuperación cardiovascular",
                report_subtitle="Lectura del archivo ECG con indicadores clave de ritmo, variabilidad y recuperación.",
                athlete_name=athlete_name,
                sport=sport,
                session_label=session_label,
                source_label=row["filename"],
                summary=summary,
                metric_lines=metric_lines,
                explain_lines=explain_lines,
                note_lines=note_lines,
                fig=fig,
                figure_title="Gráfica ECG del archivo seleccionado",
                raw_metrics=[
                    {"label": "Ritmo cardiaco", "value": f"{_fmt_num(bpm, 0)}", "unit": "lpm"},
                    {"label": "Variabilidad",   "value": f"{_fmt_num(sdnn, 0)}", "unit": "ms (SDNN)"},
                    {"label": "Recuperacion",   "value": f"{_fmt_num(rmssd, 0)}", "unit": "ms (RMSSD)"},
                    {"label": "Latidos útiles", "value": str(n_peaks), "unit": "picos R"},
                ],
                status=_status,
                status_label=_status_lbl,
            )

            base = os.path.splitext(os.path.basename(row["filename"]))[0]
            filename = f"CombatIQ_ECG_{base}_informe.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

        # --- IMU ---
        @app.callback(
            Output("imu-graph", "figure",   allow_duplicate=True),
            Output("imu-kpis",  "children", allow_duplicate=True),
            Output("imu-msg",   "children", allow_duplicate=True),
            Output("imu-meta", "data"),
            Output("imu-window", "max"),
            Output("imu-window", "value"),
            Output("imu-window", "marks"),
            Input("btn-imu-analyze", "n_clicks"),
            Input("imu-window", "value"),
            Input("imu-quality", "value"),
            Input("imu-winlen", "value"),
            State("imu-upload", "contents"),
            State("imu-upload", "filename"),
            State("imu-tabs", "value"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("imu-meta", "data"),
            prevent_initial_call=True,
        )
        def imu_pro(n_clicks, win_range, quality, winlen, content, filename, imu_kind, user_id, session_id, meta):
            # trigger detect
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            # Si la sesión activa es de Combat Monitor, auto_load_imu_for_session
            # ya gestiona la gráfica — este callback no debe interferir (excepto
            # si el usuario pulsa "Analizar" con un archivo subido manualmente).
            if session_id and not trig.startswith("btn-imu-analyze"):
                try:
                    _s = _get_authorized_session(session_id)
                    if _s and (_s.get("notes") or "").startswith("Combat Monitor"):
                        raise PreventUpdate
                except PreventUpdate:
                    raise
                except Exception:
                    pass

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update
            if not _can_access_athlete(uid):
                return go.Figure(), [], "No tienes permisos para analizar datos de este deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "IMU"):
                return go.Figure(), [], "Este deportista no tiene IMU asignado.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            athlete_sport = None
            try:
                athlete_sport = (db.get_user_by_id(int(uid)) or {}).get("sport")
            except Exception:
                athlete_sport = None
            imu_profile = self._imu_profile(athlete_sport)

            # 1) Si se presiona analizar: generamos meta + slider
            if trig.startswith("btn-imu-analyze"):
                prefix = {"imu-arm": "arm_", "imu-leg": "leg_", "imu-head": "head_"}.get(imu_kind or "imu-arm", "arm_")

                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de IMU.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "imu"), exist_ok=True)

                base_name = filename or "imu.csv"
                try:
                    final_name = _save_unique(os.path.join("data", "imu"), prefix + base_name, data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "imu", final_name)
                shown_name = filename or final_name

                t, mag, fs = _cached_read_imu_csv(save_path)
                if len(mag) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

                sid = _safe_int(session_id)
                if sid and not _session_belongs_to_user(sid, uid):
                    return go.Figure(), [], "La sesión seleccionada no pertenece a este deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update
                # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                # Guardado solo al ANALIZAR (no al mover sliders)
                try:
                    db.save_imu_metrics(uid, final_name, n_hits, hits_per_min, mean_int, max_int, session_id=sid)
                except TypeError:
                    try:
                        db.save_imu_metrics(uid, final_name, n_hits, hits_per_min, mean_int, max_int)
                    except Exception:
                        pass
                except Exception:
                    pass

                title_prefix = imu_profile["title_prefixes"].get(imu_kind or "imu-arm", "Lectura IMU")
                title = f"{title_prefix} · {shown_name}"

                meta = {
                    "path": save_path,
                    "title": title,
                    "uid": int(uid),
                    "kind": (imu_kind or "imu-arm"),
                    "sport": self._normalize_sport(athlete_sport),
                    "filename": final_name,
                    "session_id": sid,
                }

                # Slider setup (como ECG Pro)
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                # usamos esa ventana para graficar al analizar
                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Render (misma lógica que en sliders)
                sport_msg = imu_profile["message_suffixes"].get(imu_kind or "imu-arm", "")
                msg = (
                    f"{shown_name} ya está listo. "
                    f"Se detectaron {n_hits} acciones de movimiento en este archivo, "
                    f"lo que equivale a {hits_per_min:.1f} por minuto. {sport_msg}"
                )

                # build fig
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                mag_win = mag[i0:i1]

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                mag_line = mag_win[::step]

                peaks_t = None
                peaks_y = None
                if peaks is not None and len(peaks) > 0:
                    try:
                        pw = peaks[(peaks >= i0) & (peaks < i1)]
                        peaks_t = t[pw]
                        peaks_y = mag[pw]
                    except Exception:
                        peaks_t, peaks_y = None, None

                thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
                fig = fig_imu(t_line, mag_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
                kpis = kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int)

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Si no es analizar: necesitamos meta para re-render (ventana/calidad)
            if not meta or not meta.get("path"):
                raise PreventUpdate
            meta_uid = _safe_int(meta.get("uid"))
            if not (meta_uid and meta_uid == uid and _can_access_athlete(meta_uid)):
                return go.Figure(), [], "No tienes permisos para este archivo IMU.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            save_path = _path_under(meta.get("path"), os.path.join("data", "imu"))
            title = meta.get("title") or "IMU"
            if not save_path:
                return go.Figure(), [], "No encuentro el archivo IMU (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # Re-leer y recalcular (mismo algoritmo; NO guardamos a DB aquí)
            t, mag, fs = _cached_read_imu_csv(save_path)
            if len(mag) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

            # si cambia winlen, reseteamos ventana al final (como ECG)
            if trig.startswith("imu-winlen"):
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    win_range = [0.0, dur if dur > 0 else 10.0]
                else:
                    win_range = [max(0.0, dur - float(wl)), dur]
                slider_max = dur if dur > 0 else 10.0
                slider_value = win_range
                slider_marks = self._sparse_marks(slider_max)
            else:
                slider_max = dash.no_update
                slider_value = dash.no_update
                slider_marks = dash.no_update

            # Ventana (solo visual)
            dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
            dur = max(0.0, dur)
            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, dur
            except Exception:
                t0, t1 = 0.0, dur

            t0 = max(0.0, t0)
            t1 = min(max(t0 + 1e-6, t1), dur if dur > 0 else t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
            mag_win = mag[i0:i1]

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            mag_line = mag_win[::step]

            peaks_t = None
            peaks_y = None
            if peaks is not None and len(peaks) > 0:
                try:
                    pw = peaks[(peaks >= i0) & (peaks < i1)]
                    peaks_t = t[pw]
                    peaks_y = mag[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
            fig = fig_imu(t_line, mag_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
            kpis = kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks

        @app.callback(
            Output("dl-imu-data", "data"),
            Input("btn-dl-imu-data", "n_clicks"),
            State("imu-meta", "data"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("imu-tabs", "value"),
            prevent_initial_call=True,
        )
        def download_imu_data(n, meta, user_id, session_id, imu_kind):
            if not n:
                raise PreventUpdate
            if not meta or not meta.get("path"):
                meta = _session_imu_meta_from_state(session_id, user_id, imu_kind)
            if not meta or not meta.get("path"):
                raise PreventUpdate

            uid_imu = _safe_int(meta.get("uid"))
            if not (uid_imu and _can_access_athlete(uid_imu)):
                raise PreventUpdate

            from report_utils import xlsx_table
            from datetime import datetime as _dt2
            usr_imu   = db.get_user_by_id(uid_imu) if uid_imu else {}
            name_imu  = (usr_imu or {}).get("name", "Deportista")

            if (meta.get("source") == "session_events"
                    or meta.get("format") == "event_json"):
                path = _path_under(meta.get("path"), _SIGNALS_ECG_DIR)
                if not path:
                    raise PreventUpdate
                events = _load_imu_event_sidecar(path)
                if not events:
                    raise PreventUpdate

                events = sorted(events, key=lambda e: _as_float(e.get("t"), 0.0))
                meaningful = [e for e in events if e.get("type") in ("dado", "recibido")]
                n_hits = int(_as_float(meta.get("n_hits"), len(meaningful)))
                hits_per_min = _as_float(meta.get("hits_per_min"), 0.0)
                mean_int = _as_float(meta.get("mean_int_g"), 0.0)
                max_int = _as_float(meta.get("max_int_g"), 0.0)
                src_name = meta.get("filename") or os.path.basename(path)
                meta_xl = [
                    ("Atleta",             name_imu),
                    ("Fuente",             f"Sesión #{meta.get('session_id') or '-'}"),
                    ("Archivo",            src_name),
                    ("Eventos exportados", len(events)),
                    ("Acciones detectadas", n_hits),
                    ("Ritmo de acción",    f"{hits_per_min:.1f} / min"),
                    ("Explosividad media", f"{mean_int:.2f} g"),
                    ("Pico de intensidad", f"{max_int:.2f} g"),
                    ("Exportado",          _dt2.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
                ]
                headers_imu = [
                    "Tiempo (s)",
                    "Round",
                    "Tipo de acción",
                    "Intensidad (g)",
                ]
                data_imu = [
                    [
                        round(_as_float(evt.get("t"), 0.0), 3),
                        evt.get("round", ""),
                        evt.get("type", ""),
                        round(_as_float(evt.get("intensity"), 0.0), 3),
                    ]
                    for evt in events
                ]
                xlsx_imu = xlsx_table(
                    f"Eventos IMU — {name_imu}",
                    meta_xl,
                    headers_imu,
                    data_imu,
                    sheet_name="IMU",
                    col_types={0: "number", 1: "text", 2: "text", 3: "number"},
                )
                base = os.path.splitext(os.path.basename(src_name))[0]
                filename = f"CombatIQ_IMU_{base}_eventos.xlsx"
                return dcc.send_bytes(lambda b: b.write(xlsx_imu), filename)

            path = _path_under(meta.get("path"), os.path.join("data", "imu"))
            if not path:
                raise PreventUpdate

            t, mag, fs = _cached_read_imu_csv(path)
            n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)
            peak_set = {int(idx) for idx in peaks.tolist()} if peaks is not None and len(peaks) > 0 else set()

            src_name = meta.get("filename") or os.path.basename(path)
            meta_xl = [
                ("Atleta",             name_imu),
                ("Archivo",            src_name),
                ("Acciones detectadas", n_hits),
                ("Ritmo de acción",    f"{hits_per_min:.1f} / min"),
                ("Explosividad media", f"{mean_int:.2f} g"),
                ("Pico de intensidad", f"{max_int:.2f} g"),
                ("Exportado",          _dt2.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
            ]
            headers_imu = [
                "Tiempo (s)",
                "Magnitud (m/s²)",
                "Magnitud (g)",
                "Acción detectada",
            ]
            data_imu = [
                [
                    round(float(t[idx]), 6),
                    round(float(mag[idx]), 6),
                    round(float(mag[idx] / 9.81), 6),
                    1 if idx in peak_set else 0,
                ]
                for idx in range(len(t))
            ]
            xlsx_imu = xlsx_table(
                f"Señal IMU — {name_imu}",
                meta_xl,
                headers_imu,
                data_imu,
                sheet_name="IMU",
                col_types={0: "number", 1: "number", 2: "number", 3: "int"},
            )
            base     = os.path.splitext(os.path.basename(src_name))[0]
            filename = f"CombatIQ_IMU_{base}_datos.xlsx"
            return dcc.send_bytes(lambda b: b.write(xlsx_imu), filename)

        @app.callback(
            Output("dl-imu-report", "data"),
            Output("imu-export-msg", "children"),
            Input("btn-dl-imu-report", "n_clicks"),
            State("imu-meta", "data"),
            State("imu-graph", "figure"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("imu-tabs", "value"),
            prevent_initial_call=True,
        )
        def download_imu_report(n, meta, fig_dict, user_id, session_id, imu_kind):
            if not n:
                raise PreventUpdate
            if not meta or not meta.get("path"):
                meta = _session_imu_meta_from_state(session_id, user_id, imu_kind)
            if not meta or not meta.get("path"):
                return dash.no_update, "Carga o analiza una sesión con IMU antes de exportar el informe."

            if not _REPORTLAB_OK:
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            meta_uid = _safe_int(meta.get("uid"))
            uid = _safe_int(user_id) or meta_uid
            if not (uid and meta_uid and uid == meta_uid and _can_access_athlete(uid)):
                return dash.no_update, "No tienes permisos para exportar este archivo IMU."

            is_event_source = (
                meta.get("source") == "session_events"
                or meta.get("format") == "event_json"
            )
            events = []
            t = mag = peaks = None
            y_axis_label = "Magnitud (m/s²)"
            report_source_label = "Lectura del archivo IMU con indicadores de acción, ritmo y explosividad."

            if is_event_source:
                path = _path_under(meta.get("path"), _SIGNALS_ECG_DIR)
                if not path:
                    return dash.no_update, "No encuentro los eventos IMU de esta sesión."
                events = _load_imu_event_sidecar(path)
                if not events:
                    return dash.no_update, "La sesión no tiene eventos IMU exportables."
                meaningful = [e for e in events if e.get("type") in ("dado", "recibido")]
                event_times = [_as_float(e.get("t"), 0.0) for e in meaningful]
                event_g = [_as_float(e.get("intensity"), 0.0) for e in meaningful]
                n_hits = int(_as_float(meta.get("n_hits"), len(meaningful)))
                hits_per_min = _as_float(meta.get("hits_per_min"), 0.0)
                mean_int = _as_float(meta.get("mean_int_g"), 0.0)
                max_int = _as_float(meta.get("max_int_g"), 0.0)
                if hits_per_min <= 0 and len(event_times) > 1:
                    duration = max(event_times) - min(event_times)
                    hits_per_min = n_hits / (duration / 60.0) if duration > 0 else 0.0
                if mean_int <= 0 and event_g:
                    mean_int = float(np.mean(event_g))
                if max_int <= 0 and event_g:
                    max_int = float(np.max(event_g))
                y_axis_label = "Intensidad (g)"
                report_source_label = "Lectura de eventos IMU de la sesión con indicadores de acción, ritmo y explosividad."
            else:
                path = _path_under(meta.get("path"), os.path.join("data", "imu"))
                if not path:
                    return dash.no_update, "No encuentro el archivo IMU que quieres exportar."

                try:
                    t, mag, fs = _cached_read_imu_csv(path)
                except Exception as e:
                    return dash.no_update, f"No pude leer el archivo IMU: {e}"

                n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

            athlete = db.get_user_by_id(int(uid)) if uid else {}
            athlete = athlete or {}
            athlete_name = athlete.get("name", "Deportista")
            sport = athlete.get("sport") or meta.get("sport") or "-"
            sport_key = self._normalize_sport(sport)
            imu_kind = meta.get("kind") or "imu-arm"

            context_map = {
                ("taekwondo", "imu-leg"): "trabajo de pierna, pateo y ritmo de ataque",
                ("taekwondo", "imu-arm"): "desplazamiento, control de base y guardia",
                ("taekwondo", "imu-head"): "tronco, estabilidad e impacto en peto",
                ("boxeo", "imu-arm"): "golpeo, ritmo de manos y continuidad ofensiva",
                ("boxeo", "imu-head"): "guardia, tronco y control del impacto",
            }
            context_label = context_map.get((sport_key, imu_kind), "movimiento y ritmo del deportista")

            sid = _safe_int(meta.get("session_id"))
            sid_from_state = _safe_int(session_id)
            if sid_from_state and _session_belongs_to_user(sid_from_state, uid):
                sid = sid_from_state
            session_label = "Sin sesión asociada"
            if sid:
                session_label = f"Sesión #{sid}"
                try:
                    current_session = db.get_session(int(sid)) if hasattr(db, "get_session") else None
                except Exception:
                    current_session = None
                if current_session:
                    ts_start = (current_session.get("ts_start") or "")[:10]
                    session_type = (current_session.get("session_type") or "").replace("_", " ").strip()
                    parts = [f"Sesión #{sid}"]
                    if session_type:
                        parts.append(session_type)
                    if ts_start:
                        parts.append(ts_start)
                    session_label = " | ".join(parts)

            if hits_per_min >= 45 and mean_int >= 1.2:
                summary = f"El archivo muestra un ritmo alto con acciones explosivas, orientado a {context_label}."
            elif hits_per_min >= 25:
                summary = f"La lectura se ve estable y con buen ritmo de trabajo, orientada a {context_label}."
            else:
                summary = f"El registro refleja un ritmo más controlado, útil para seguir {context_label} sin una carga tan alta."

            metric_lines = [
                f"Acciones detectadas: {n_hits}. Son eventos de movimiento relevantes dentro de la señal, no técnicas confirmadas.",
                f"Ritmo de acción: {_fmt_num(hits_per_min, 1)} por minuto. Equivale al ritmo medio de este registro.",
                f"Explosividad media: {_fmt_num(mean_int, 2)} g. Resume la intensidad media de las acciones detectadas.",
                f"Pico de explosividad: {_fmt_num(max_int, 2)} g. Marca la acción más intensa del archivo.",
            ]

            explain_lines = [
                "La línea muestra la magnitud del movimiento a lo largo del tiempo.",
                "Las cruces marcan las acciones detectadas por el algoritmo dentro de esta señal.",
                "La línea discontinua funciona como umbral visual de apoyo para leer la gráfica.",
                "Esta lectura ayuda a seguir ritmo, carga y explosividad, pero no cuenta técnicas exactas por sí sola.",
            ]

            note_lines = [
                "Usa este informe como apoyo para interpretar la sesión y comparar archivos del mismo deportista.",
                "La lectura gana valor cuando se combina con el contexto de la sesión y el check-in del día.",
            ]

            if fig_dict:
                fig = go.Figure(fig_dict)
                try:
                    layout = fig_dict.get("layout") or {}
                    y_axis_label = (((layout.get("yaxis") or {}).get("title") or {}).get("text")
                                    or y_axis_label)
                except Exception:
                    pass
            elif is_event_source:
                fig = go.Figure()
                groups = {k: {"t": [], "g": []} for k in _HIT_STYLES_REPLAY}
                for evt in events:
                    htype = evt.get("type", "ruido")
                    if htype not in groups:
                        htype = "ruido"
                    groups[htype]["t"].append(_as_float(evt.get("t"), 0.0))
                    groups[htype]["g"].append(_as_float(evt.get("intensity"), 0.0))
                for htype, data in groups.items():
                    if not data["t"]:
                        continue
                    style = _HIT_STYLES_REPLAY[htype]
                    xs, ys = [], []
                    for ts, inten in zip(data["t"], data["g"]):
                        xs.extend([ts, ts, None])
                        ys.extend([0.0, inten, None])
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, mode="lines",
                        name=style["label"] or "Ruido",
                        line=dict(color=style["color"], width=style["width"]),
                    ))
            else:
                peaks_t = t[peaks] if peaks is not None and len(peaks) > 0 else None
                peaks_y = mag[peaks] if peaks is not None and len(peaks) > 0 else None
                thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
                fig = fig_imu(t, mag, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=meta.get("title") or "IMU")
            _apply_signal_pdf_chart_style(
                fig,
                "Movimiento y ritmo",
                "Tiempo (s)",
                y_axis_label,
            )

            source_label = meta.get("filename") or os.path.basename(path)
            if hits_per_min >= 45 and mean_int >= 1.2:
                _imu_status, _imu_lbl = "ok",   "Sesión de alta intensidad y buen ritmo"
            elif hits_per_min >= 25:
                _imu_status, _imu_lbl = "warn",  "Ritmo estable — revisar junto al check-in del día"
            else:
                _imu_status, _imu_lbl = "warn",  "Ritmo controlado — carga moderada registrada"

            pdf_bytes = _build_signal_report_pdf(
                report_title="Informe de movimiento y ritmo",
                report_subtitle=report_source_label,
                athlete_name=athlete_name,
                sport=sport,
                session_label=session_label,
                source_label=source_label,
                summary=summary,
                metric_lines=metric_lines,
                explain_lines=explain_lines,
                note_lines=note_lines,
                fig=fig,
                figure_title="Gráfica IMU del archivo seleccionado",
                raw_metrics=[
                    {"label": "Acciones",          "value": str(n_hits), "unit": "detectadas"},
                    {"label": "Ritmo de acción",   "value": f"{_fmt_num(hits_per_min, 1)}", "unit": "/ min"},
                    {"label": "Explosividad media","value": f"{_fmt_num(mean_int, 2)}", "unit": "g"},
                    {"label": "Pico intensidad",   "value": f"{_fmt_num(max_int, 2)}", "unit": "g"},
                ],
                status=_imu_status,
                status_label=_imu_lbl,
            )

            base = os.path.splitext(os.path.basename(source_label))[0]
            filename = f"CombatIQ_IMU_{base}_informe.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

        # ── Pre-selección desde URL params (?session=N&tab=replay|signals) ────
        @app.callback(
            Output("ecg-main-tabs",        "value"),
            Output("replay-session-select", "value", allow_duplicate=True),
            Input("ecg-url", "search"),
            prevent_initial_call="initial_duplicate",
        )
        def _apply_url_preselect(search):
            if not search:
                raise PreventUpdate
            try:
                from urllib.parse import parse_qs as _pqs
                _p   = _pqs((search or "").lstrip("?"))
                tab  = (_p.get("tab",     [None])[0] or "")
                sid  = (_p.get("session", [None])[0] or None)
            except Exception:
                raise PreventUpdate

            tab_val    = ("tab-replay"   if tab == "replay"
                          else "tab-biomech" if tab == "biomech"
                          else "tab-signals")
            replay_sid = _safe_int(sid) if tab == "replay" and sid else None
            replay_val = replay_sid if replay_sid and _get_authorized_session(replay_sid) else no_update
            return tab_val, replay_val

        # ── Biomecánica: analizar postura desde video ─────────────────────────
        # Sprint 5: 3 callbacks encadenados vía dcc.Store para evitar congelamientos.
        # Paso 1 (~60 s MediaPipe) → Paso 2 (~120 s YOLO) → Paso 3 (IA + render).

        # Callback 1/3 — MediaPipe
        @app.callback(
            Output("pose-mediapipe-store", "data"),
            Output("pose-output",          "children"),
            Output("pose-progress",        "children"),
            Input("btn-analyze-pose",      "n_clicks"),
            State("replay-video-store",    "data"),
            State("replay-session-select", "value"),
            State("pose-target-select",    "value"),
            State("pose-num-rounds",       "value"),
            prevent_initial_call=True,
        )
        def _pose_step1_mediapipe(n, video_store, replay_session_id, pose_target, num_rounds_raw):
            if not n:
                raise PreventUpdate
            if not video_store or not video_store.get("filename"):
                return None, html.P(
                    "Carga un video primero usando el botón de subida.",
                    className="text-danger",
                    style={"fontSize": "13px"},
                ), ""

            import os as _os
            filename = _os.path.basename(str(video_store.get("filename") or ""))
            base_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            try:
                alias_path = _os.path.join(base_dir, "data", "upload_aliases.json")
                with open(alias_path, encoding="utf-8") as alias_fh:
                    aliases = json.load(alias_fh) or {}
                filename = _os.path.basename(str(aliases.get(filename, filename)))
            except Exception:
                pass
            upload_dirs = [
                _os.path.join(base_dir, "data", "uploads"),
                _os.path.join(base_dir, "data", "uploads_legacy"),
                _os.path.join(base_dir, "assets", "uploads"),
            ]
            video_path = None
            for uploads_dir in upload_dirs:
                video_path = _path_under(_os.path.join(uploads_dir, filename), uploads_dir)
                if video_path:
                    break
            if not video_path:
                return None, html.P("No encuentro el video subido.", className="text-danger"), ""

            athlete_name    = session.get("name") or "Deportista"
            _viewer_role    = str(session.get("role") or "deportista")
            _viewer_name    = session.get("name") or ("Coach" if _viewer_role == "coach" else athlete_name)
            _coaching_title = "IA de coaching" if _viewer_role == "coach" else "Tu lectura biomecánica"
            _actions_header = "Acciones sugeridas" if _viewer_role == "coach" else "Qué trabajar"
            _drills_header  = "Ejercicios de apoyo" if _viewer_role == "coach" else "Ejercicios para ti"
            _duel_title     = "IA táctica" if _viewer_role == "coach" else "Tu lectura táctica"
            try:
                sid = _safe_int(replay_session_id)
                if sid:
                    session_row = db.get_session(int(sid)) or {}
                    athlete_id = _safe_int(session_row.get("athlete_id"))
                    if athlete_id:
                        athlete = db.get_user_by_id(int(athlete_id)) or {}
                        athlete_name = athlete.get("name") or athlete_name
            except Exception:
                athlete_name = session.get("name") or athlete_name

            try:
                _num_rounds = int(num_rounds_raw) if num_rounds_raw else None
            except (TypeError, ValueError):
                _num_rounds = None

            try:
                from pose_analyzer import analyze_video
                result = analyze_video(
                    video_path,
                    sport=session.get("sport"),
                    target=pose_target,
                    num_rounds=_num_rounds,
                )
            except Exception as e:
                return None, html.P(f"Error al analizar: {e}", className="text-danger"), ""

            if result.get("error"):
                return None, html.P(result["error"], className="text-danger", style={"fontSize": "13px"}), ""

            store_data = {
                "video_path":     video_path,
                "result":         result,
                "filename":       filename,
                "session_sport":  session.get("sport") or "",
                "viewer_role":    _viewer_role,
                "viewer_name":    _viewer_name,
                "athlete_name":   athlete_name,
                "coaching_title": _coaching_title,
                "actions_header": _actions_header,
                "drills_header":  _drills_header,
                "duel_title":     _duel_title,
            }
            job_id = _pose_cache_put(store_data)
            return (
                {"job_id": job_id, "stage": "mediapipe"},
                html.P("Análisis de pose completado — calculando velocidades YOLO…",
                       className="text-muted", style={"fontSize": "13px"}),
                html.Span([
                    html.Span("✓ Pose OK", style={"color": "var(--neon)", "marginRight": "12px"}),
                    html.Span("Calculando velocidades YOLO…", style={"color": "var(--muted)"}),
                ]),
            )

        # Callback 2/3 — YOLO
        @app.callback(
            Output("pose-speed-store",    "data"),
            Output("pose-output",         "children", allow_duplicate=True),
            Output("pose-progress",       "children", allow_duplicate=True),
            Input("pose-mediapipe-store", "data"),
            prevent_initial_call=True,
        )
        def _pose_step2_yolo(mediapipe_data):
            job_id = (mediapipe_data or {}).get("job_id")
            cached = _pose_cache_get(job_id)
            if not cached or not cached.get("video_path"):
                raise PreventUpdate

            video_path = cached["video_path"]
            speed_data = None
            try:
                from yolo_tracker import analyze_duel_speeds as _yolo_speeds
                speed_data = _yolo_speeds(video_path, sample_every=3)
            except Exception as _yolo_exc:
                speed_data = {"error": str(_yolo_exc)}

            cached["speed_data"] = speed_data
            _pose_cache_put(cached, job_id=job_id)
            return (
                {"job_id": job_id, "stage": "yolo"},
                html.P("YOLO completado — generando lectura de IA y construyendo resultados…",
                       className="text-muted", style={"fontSize": "13px"}),
                html.Span([
                    html.Span("✓ Pose OK", style={"color": "var(--neon)", "marginRight": "12px"}),
                    html.Span("✓ YOLO OK", style={"color": "var(--neon)", "marginRight": "12px"}),
                    html.Span("Generando lectura de IA…", style={"color": "var(--muted)"}),
                ]),
            )

        # Callback 3/3 — renderizado (sin bloqueo de IA)
        # La nota de IA del duelo se genera en step4 para no bloquear la UI.
        @app.callback(
            Output("pose-output",        "children", allow_duplicate=True),
            Output("pose-results",       "data"),
            Output("ecg-main-tabs",      "value",    allow_duplicate=True),
            Output("pose-progress",      "children", allow_duplicate=True),
            Output("pose-ai-note-store", "data"),
            Input("pose-speed-store", "data"),
            prevent_initial_call=True,
        )
        def _pose_step3_render(speed_store_data):
            job_id = (speed_store_data or {}).get("job_id")
            speed_store_data = _pose_cache_get(job_id)
            if not speed_store_data or not speed_store_data.get("result"):
                raise PreventUpdate

            result          = speed_store_data["result"]
            filename        = speed_store_data.get("filename", "")
            _viewer_role    = speed_store_data.get("viewer_role", "deportista")
            _viewer_name    = speed_store_data.get("viewer_name", "")
            athlete_name    = speed_store_data.get("athlete_name", "Deportista")
            _coaching_title = speed_store_data.get("coaching_title", "IA de coaching")
            _actions_header = speed_store_data.get("actions_header", "Acciones sugeridas")
            _drills_header  = speed_store_data.get("drills_header",  "Ejercicios de apoyo")
            _duel_title     = speed_store_data.get("duel_title",     "IA táctica")
            speed_data      = speed_store_data.get("speed_data")
            _session_sport  = speed_store_data.get("session_sport") or ""

            frames  = result.get("frames", [])
            summary = result.get("summary", {})
            biomech = result.get("biomech", {}) or {}
            metrics = biomech.get("metrics", {}) or {}
            insights = biomech.get("insights", []) or []
            report_data = {
                "user_id": _safe_int(session.get("user_id") or session.get("id")),
                "athlete_name": athlete_name,
                "sport": _session_sport or biomech.get("sport") or "Combate",
                "filename": filename,
                "summary": summary,
                "biomech": biomech,
                "duel": result.get("duel"),
                "target": result.get("target") or biomech.get("target"),
                "fps": result.get("fps"),
                "total_frames": result.get("total_frames"),
                "processing_s": result.get("processing_s"),
                "time_limited": bool(result.get("time_limited")),
            }
            speed_store_data["report_data"] = report_data
            _pose_cache_put(speed_store_data, job_id=job_id)

            # ── KPI cards ─────────────────────────────────────────────────
            def _kpi(label, stats, color="var(--neon)"):
                stats = stats or {}
                max_v = stats.get("max", "—")
                avg_v = stats.get("avg", "—")
                min_v = stats.get("min", "—")
                return html.Div(className="kpi", children=[
                    html.Div(label, className="kpi-label"),
                    html.Div(f"{max_v}°" if max_v != "—" else "—", className="kpi-value",
                             style={"color": color}),
                    html.Div(f"avg {avg_v}° · min {min_v}°" if avg_v != "—" else "Sin dato suficiente",
                             className="kpi-sub"),
                ])

            def _metric_card(label, value, sub, color="var(--neon)"):
                return html.Div(className="kpi", children=[
                    html.Div(label, className="kpi-label"),
                    html.Div(value, className="kpi-value", style={"color": color, "fontSize": "22px"}),
                    html.Div(sub, className="kpi-sub"),
                    html.Div(className="kpi-ecg-line"),
                ])

            def _to_int(value, default=0):
                try:
                    return int(float(value))
                except Exception:
                    return default

            def _level_color(level):
                return {
                    "ok": "var(--neon)",
                    "warn": "#f0a832",
                    "alert": "var(--punch)",
                }.get(level, "var(--muted)")

            def _joint_reliability_row(smry: dict, n_frames: int) -> html.Div:
                wb = smry.get("warning_breakdown") or {}
                if not wb or n_frames < 1:
                    return html.Div()
                _JOINT_LABELS = {
                    "brazo_izquierdo_fuera_de_peto": "Brazo izq",
                    "brazo_derecho_fuera_de_peto":   "Brazo der",
                    "pose_contaminada":              "Pose mezclada",
                    "sin_evidencia_atleta":          "Sin atleta claro",
                    "cuerpo_cruzado":                "Cuerpo cruzado",
                    "esqueleto_colapsado":           "Esqueleto dudoso",
                    "casco_sin_peto_coherente":      "Casco dudoso",
                    "cuerpo_recortado":              "Cuerpo recortado",
                    "oclusion_parcial":              "Oclusión",
                    "color_contrario_en_pose":       "Color cruzado",
                    "peto_no_aislado":               "Peto parcial",
                    "casco_contrario":               "Casco cruzado",
                }
                chips = []
                for key, label in _JOINT_LABELS.items():
                    count = wb.get(key, 0)
                    pct = round(count / n_frames * 100)
                    if pct >= 30:
                        color, icon = "var(--punch)", "⚠"
                        tip = f"{label}: ocluido en {pct}% de frames"
                    elif pct >= 10:
                        color, icon = "#f0a832", "~"
                        tip = f"{label}: ruido en {pct}% de frames"
                    else:
                        color, icon = "var(--neon)", "✓"
                        tip = f"{label}: limpio"
                    chips.append(html.Span(
                        f"{icon} {label}",
                        title=tip,
                        style={
                            "color": color,
                            "border": f"1px solid {color}",
                            "borderRadius": "12px",
                            "padding": "2px 8px",
                            "fontSize": "11px",
                            "marginRight": "6px",
                            "whiteSpace": "nowrap",
                        },
                    ))
                return html.Div(
                    children=[
                        html.Span("Articulaciones: ", style={"fontSize": "11px", "color": "var(--muted)", "marginRight": "4px"}),
                        *chips,
                    ],
                    style={"display": "flex", "flexWrap": "wrap", "alignItems": "center", "marginBottom": "10px", "marginTop": "6px"},
                )

            duel = result.get("duel") or {}
            if duel:
                import plotly.graph_objects as go

                duel_frames = duel.get("frames") or []
                duel_metrics = duel.get("metrics") or {}
                duel_insights = duel.get("insights") or []
                duel_coaching = duel.get("coaching") or {}
                red_data = duel.get("red") or {}
                blue_data = duel.get("blue") or {}
                red_target = red_data.get("target") or {}
                blue_target = blue_data.get("target") or {}
                duel_evidence = _duel_frame_evidence(
                    duel_frames,
                    result.get("fps", 0),
                    duel_metrics.get("pressure_label", ""),
                )

                confidence_label = duel_coaching.get("confidence_label") or "Sin dato"
                confidence_score = duel_coaching.get("confidence_score")
                confidence_color = (
                    "var(--neon)" if confidence_label == "Alta"
                    else "#f0a832" if confidence_label == "Media"
                    else "var(--punch)"
                )

                distance_fig = go.Figure()
                ts_duel = [f.get("t") for f in duel_frames]
                distance_fig.add_trace(go.Scatter(
                    x=ts_duel,
                    y=[f.get("distance") for f in duel_frames],
                    name="Distancia rojo-azul",
                    mode="lines",
                    line={"color": "#22d3ee", "width": 2.4},
                    hovertemplate="%{x:.1f}s · distancia %{y:.3f}<extra></extra>",
                ))
                exchange_frames = [f for f in duel_frames if f.get("exchange")]
                if exchange_frames:
                    distance_fig.add_trace(go.Scatter(
                        x=[f.get("t") for f in exchange_frames],
                        y=[f.get("distance") for f in exchange_frames],
                        name="Posible intercambio",
                        mode="markers",
                        marker={"color": "#f97316", "size": 9, "symbol": "diamond"},
                        hovertemplate="%{x:.1f}s · posible intercambio<extra></extra>",
                    ))
                distance_fig.update_layout(
                    title={"text": "Distancia táctica en el tiempo", "font": {"size": 13, "color": "#d8e3ef"}, "x": 0.02},
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font={"color": "#a7b1bc", "size": 11},
                    legend={"orientation": "h", "y": -0.24, "font": {"size": 10}},
                    margin={"l": 42, "r": 12, "t": 46, "b": 68},
                    height=285,
                    hovermode="x unified",
                    xaxis={"title": "Tiempo (s)", "gridcolor": "#31445f", "zeroline": False},
                    yaxis={"title": "Distancia normalizada", "gridcolor": "#31445f", "zeroline": False},
                )

                duel_reading = html.Div(
                    className="card",
                    style={"marginBottom": "16px", "borderLeft": "4px solid var(--neon)"},
                    children=[
                        html.H4("Combate rojo vs azul", className="card-title"),
                        html.P(
                            "Lectura táctica de relación entre atletas. No declara ganador ni puntos oficiales: ubica presión, distancia e intercambios para revisar con el coach.",
                            className="text-muted",
                        ),
                        html.Div(className="kpis session-kpis", children=[
                            _metric_card("Frames pareados", str(duel.get("frames_paired", len(duel_frames))), "Rojo y azul visibles"),
                            _metric_card("Distancia media",
                                         f"~{duel_metrics.get('avg_distance', 0) * 170:.0f} cm",
                                         "Centro corporal (estimado)"),
                            _metric_card("Intercambios", str(duel_metrics.get("exchange_count", 0)), "Cercanía + movimiento", "#f0a832"),
                            _metric_card("Tendencia", duel_metrics.get("pressure_label", "Sin dato"), "Presión estimada"),
                        ]),
                        html.Ul(
                            [
                                html.Li([
                                    html.Strong(f"{item.get('title', 'Lectura')}: ", style={"color": _level_color(item.get("level"))}),
                                    item.get("text", ""),
                                ])
                                for item in duel_insights
                            ] or [html.Li("No hay suficientes datos para lectura táctica dual.")],
                            className="list-compact",
                        ),
                    ],
                )

                duel_compare = html.Div(
                    className="card",
                    style={"marginBottom": "16px"},
                    children=[
                        html.H4("Comparativa rápida", className="card-title"),
                        html.Div(className="kpis session-kpis", children=[
                            _metric_card("Rojo confianza", f"{round(float(red_target.get('confidence', 0) or 0) * 100)}%", "Selección + cobertura", "#ff5c7a"),
                            _metric_card("Azul confianza", f"{round(float(blue_target.get('confidence', 0) or 0) * 100)}%", "Selección + cobertura", "#38bdf8"),
                            _metric_card("Ext. máx. pierna rojo", f"{duel_metrics.get('red_lower_rom', 0)}°", "Ángulo máx. rodilla+cadera", "#ff5c7a"),
                            _metric_card("Ext. máx. pierna azul", f"{duel_metrics.get('blue_lower_rom', 0)}°", "Ángulo máx. rodilla+cadera", "#38bdf8"),
                            _metric_card("Vel. máx rojo", f"{duel_metrics.get('red_peak_ang_vel', 0):.0f}°/s", "Velocidad angular pico", "#ff5c7a"),
                            _metric_card("Vel. máx azul", f"{duel_metrics.get('blue_peak_ang_vel', 0):.0f}°/s", "Velocidad angular pico", "#38bdf8"),
                        ]),
                    ],
                )

                duel_coaching_card = html.Div(
                    className="card",
                    style={"marginBottom": "16px", "borderLeft": f"4px solid {confidence_color}"},
                    children=[
                        html.H4(_duel_title, className="card-title"),
                        html.P(
                            f"Confianza de lectura: {confidence_label}"
                            + (f" ({confidence_score}%)" if confidence_score is not None else ""),
                            className="text-muted",
                            style={"color": confidence_color, "fontWeight": "700"},
                        ),
                        html.P(
                            (
                                f"{_viewer_name}, usa esta lectura para decidir que revisar con {athlete_name}."
                                if _viewer_role == "coach"
                                else f"{athlete_name}, esta lectura esta escrita para que sepas que ajustar en tu siguiente sesion."
                            ),
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "8px"},
                        ),
                        html.P(duel_coaching.get("graph_explanation") or "", className="text-muted"),
                        html.Div(
                            [html.Strong("Qué significa: "), duel_coaching.get("meaning") or ""],
                            className="text-muted",
                            style={"fontSize": "13px", "marginBottom": "10px"},
                        ),
                        html.H5("Decisiones para el coach" if _viewer_role == "coach" else "Que trabajar ahora", style={"margin": "8px 0 6px"}),
                        html.Ul(
                            [html.Li(item) for item in (duel_coaching.get("actions") or [])]
                            or [html.Li("Revisar manualmente los momentos de menor distancia.")],
                            className="list-compact",
                        ),
                        _evidence_list(duel_evidence),
                    ],
                )

                duel_chart = html.Div(
                    className="card",
                    style={"marginBottom": "16px"},
                    children=[
                        html.H4("Gráfica de distancia", className="card-title"),
                        html.P(
                            "Cuando la curva baja, los atletas se acercan. Los diamantes naranjas marcan posibles intercambios por cercanía y movimiento simultáneo.",
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "10px"},
                        ),
                        dcc.Graph(figure=distance_fig, config={"displayModeBar": False}),
                        _graph_interpretation([
                            "La curva representa la distancia normalizada entre el centro corporal de rojo y azul.",
                            "Cuando la curva baja, el combate entra en zona de cercanía: ahí se revisan entradas, salidas, choques o contraataques.",
                            "Los diamantes naranjas no son puntos oficiales; son momentos probables de intercambio por cercanía y movimiento simultáneo.",
                        ], open=True),
                    ],
                )

                # ── Round breakdown ───────────────────────────────────────
                rounds_section = []
                rounds_data = (result.get("duel") or {}).get("rounds") or []
                if rounds_data:
                    fight_rounds = [r for r in rounds_data if r.get("phase") == "fight"]
                    rest_rounds  = [r for r in rounds_data if r.get("phase") == "rest"]
                    rows = []
                    for seg in rounds_data:
                        ph = seg.get("phase", "fight")
                        rnd = seg.get("round", "—")
                        t0  = seg.get("t_start", 0)
                        t1  = seg.get("t_end", 0)
                        dur = round(t1 - t0, 1)
                        exc = seg.get("exchange_count", 0)
                        pk_r = seg.get("red_peak_ang_vel", 0)
                        pk_b = seg.get("blue_peak_ang_vel", 0)
                        label = (f"Round {rnd}" if ph == "fight"
                                 else f"Descanso R{rnd}→{rnd+1}")
                        color = "var(--neon)" if ph == "fight" else "#f0a832"
                        rows.append(html.Tr([
                            html.Td(html.Span(label,
                                             style={"color": color, "fontWeight": "700",
                                                    "fontSize": "11px"})),
                            html.Td(f"{int(t0//60)}:{int(t0%60):02d} – {int(t1//60)}:{int(t1%60):02d}",
                                    style={"fontSize": "11px", "color": "var(--muted)",
                                           "whiteSpace": "nowrap"}),
                            html.Td(f"{dur}s", style={"fontSize": "11px",
                                                       "textAlign": "right"}),
                            html.Td(str(exc), style={"fontSize": "11px",
                                                      "textAlign": "center"}),
                            html.Td(f"{pk_r:.0f}°/s",
                                    style={"fontSize": "11px", "color": "#dc3232",
                                           "textAlign": "right"}),
                            html.Td(f"{pk_b:.0f}°/s",
                                    style={"fontSize": "11px", "color": "#288cff",
                                           "textAlign": "right"}),
                        ]))
                    rounds_section = [
                        html.Div(className="ecg-divider ecg-divider--spaced"),
                        html.Div(className="card", style={"marginBottom": "16px"}, children=[
                            html.H4("Desglose por rounds", className="card-title"),
                            html.Div(
                                style={"overflowX": "auto"},
                                children=html.Table(
                                    style={"width": "100%", "borderCollapse": "collapse"},
                                    children=[
                                        html.Thead(html.Tr([
                                            html.Th("Segmento",
                                                    style={"fontSize": "10px",
                                                           "color": "var(--muted)",
                                                           "fontWeight": "600",
                                                           "paddingBottom": "6px",
                                                           "textAlign": "left"}),
                                            html.Th("Tiempo",
                                                    style={"fontSize": "10px",
                                                           "color": "var(--muted)",
                                                           "paddingBottom": "6px"}),
                                            html.Th("Duración",
                                                    style={"fontSize": "10px",
                                                           "color": "var(--muted)",
                                                           "paddingBottom": "6px",
                                                           "textAlign": "right"}),
                                            html.Th("Intercambios",
                                                    style={"fontSize": "10px",
                                                           "color": "var(--muted)",
                                                           "paddingBottom": "6px",
                                                           "textAlign": "center"}),
                                            html.Th("Vel. Rojo",
                                                    style={"fontSize": "10px",
                                                           "color": "#dc3232",
                                                           "paddingBottom": "6px",
                                                           "textAlign": "right"}),
                                            html.Th("Vel. Azul",
                                                    style={"fontSize": "10px",
                                                           "color": "#288cff",
                                                           "paddingBottom": "6px",
                                                           "textAlign": "right"}),
                                        ])),
                                        html.Tbody(rows),
                                    ],
                                ),
                            ),
                            html.P(
                                f"Detectados: {len(fight_rounds)} rounds de pelea, "
                                f"{len(rest_rounds)} pausas.",
                                className="text-muted",
                                style={"fontSize": "11px", "marginTop": "8px"},
                            ),
                        ]),
                    ]

                # ── Frame gallery (up to 6 key moments) ──────────────────
                skeleton_section = []
                kf_list = result.get("annotated_frames") or []
                kf_meta = result.get("annotated_frames_meta") or []
                if not kf_list and result.get("annotated_frame"):
                    kf_list = [result["annotated_frame"]]
                if kf_list:
                    tiles = [
                        html.Div(
                            [
                                html.Img(
                                    src=src,
                                    style={
                                        "width": "100%", "display": "block",
                                        "borderRadius": "8px",
                                        "border": "1px solid var(--line)",
                                    },
                                ),
                                html.Div(
                                    (
                                        f"t={float(kf_meta[i].get('t', 0.0)):.1f}s · "
                                        f"score {float(kf_meta[i].get('score', 0.0)):.2f}"
                                    )
                                    if i < len(kf_meta) else "",
                                    className="text-muted",
                                    style={"fontSize": "10px", "marginTop": "4px"},
                                ),
                            ],
                            style={"width": "calc(50% - 5px)", "flexShrink": "0"},
                        )
                        for i, src in enumerate(kf_list)
                    ]
                    legend = html.Div(
                        style={"display": "flex", "gap": "14px", "marginTop": "8px", "flexWrap": "wrap"},
                        children=[
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#dc3232","marginRight":"5px","verticalAlign":"middle"}), html.Span("Peto rojo", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#288cff","marginRight":"5px","verticalAlign":"middle"}), html.Span("Peto azul", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"rgba(200,200,50,0.9)","marginRight":"5px","verticalAlign":"middle"}), html.Span("Línea de distancia", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#00dcff","marginRight":"5px","verticalAlign":"middle"}), html.Span("INTERCAMBIO", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                        ],
                    )
                    skeleton_section = [
                        html.Div(className="ecg-divider ecg-divider--spaced"),
                        html.P(
                            f"Momentos clave del combate ({len(kf_list)} frames seleccionados)",
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "8px", "fontWeight": "600"},
                        ),
                        html.Div(tiles, style={"display": "flex", "flexWrap": "wrap", "gap": "10px"}),
                        legend,
                    ]

                # ── AI duel insight — placeholder; llenado async por step4 ────
                # (extraído de step3 para evitar bloquear la UI ~20 s)
                _ai_note_placeholder = html.Div(
                    id="pose-ai-note-container",
                    children=[html.P(
                        "Generando lectura IA del combate…",
                        className="text-muted",
                        style={"fontSize": "12px", "marginTop": "6px"},
                    )],
                )
                _ai_store_payload = {
                    "job_id":       job_id,
                    "viewer_role":  _viewer_role,
                    "viewer_name":  _viewer_name,
                    "athlete_name": athlete_name,
                    "session_sport": _session_sport,
                }

                # ── Simulated ECG / IMU from movement analysis ───────────────
                sim_section = []
                try:
                    from pose_analyzer import simulate_duel_ecg_imu as _sim_duel
                    sim = _sim_duel(result, for_target="blue")
                    ecg_sim    = sim.get("ecg", [])
                    imu_sim    = sim.get("imu", [])
                    sim_note   = sim.get("note", "")
                    max_hr     = sim.get("max_hr", 0)
                    avg_hr     = sim.get("avg_hr", 0)
                    impacts    = sim.get("impacts", 0)
                    rest_bands_sim = sim.get("rest_bands", [])
                    _band_sh   = _rest_band_shapes(rest_bands_sim) if rest_bands_sim else []

                    if ecg_sim or imu_sim:
                        # HR chart with rest shading
                        hr_fig = go.Figure()
                        if ecg_sim:
                            hr_fig.add_trace(go.Scatter(
                                x=[e["t"] for e in ecg_sim],
                                y=[e["hr"] for e in ecg_sim],
                                mode="lines+markers",
                                line={"color": "#27c98f", "width": 1.8},
                                marker={"size": 3},
                                name="FC estimada",
                                hovertemplate="t=%{x}s  HR=%{y:.0f} bpm<extra></extra>",
                            ))
                        hr_fig = apply_chart_style(hr_fig, height=160)
                        hr_fig.update_layout(
                            margin={"t": 4, "b": 24, "l": 36, "r": 8},
                            xaxis_title="t (s)",
                            yaxis_title="bpm",
                            xaxis={"fixedrange": True},
                            yaxis={"fixedrange": True, "range": [70, 210]},
                            showlegend=False,
                            shapes=_band_sh,
                        )

                        # IMU stems with rest shading
                        imu_fig2 = go.Figure()
                        if imu_sim:
                            _IMPACT_COLOR = "#e45a5a"
                            _MOVE_COLOR   = "rgba(143,163,191,0.35)"
                            for _lbl, color, ev_type in [
                                ("Impacto", _IMPACT_COLOR, "impacto"),
                                ("Movimiento", _MOVE_COLOR, "movimiento"),
                            ]:
                                pts = [p for p in imu_sim if p.get("event") == ev_type]
                                if pts:
                                    xs, ys = [], []
                                    for p in pts:
                                        xs.extend([p["t"], p["t"], None])
                                        ys.extend([0.0, p["g"], None])
                                    imu_fig2.add_trace(go.Scatter(
                                        x=xs, y=ys, mode="lines",
                                        line={"color": color, "width": 1.5},
                                        name=_lbl,
                                        hoverinfo="skip",
                                    ))
                        imu_fig2 = apply_chart_style(imu_fig2, height=150)
                        imu_fig2.update_layout(
                            margin={"t": 4, "b": 24, "l": 36, "r": 8},
                            xaxis_title="t (s)",
                            yaxis_title="g",
                            xaxis={"fixedrange": True},
                            yaxis={"fixedrange": True},
                            showlegend=True,
                            legend={"font": {"size": 9}, "orientation": "h",
                                    "y": 1.12, "x": 0},
                            shapes=_band_sh,
                        )

                        sim_section = [
                            html.Div(className="ecg-divider ecg-divider--spaced"),
                            html.Div(
                                style={"display": "flex", "gap": "10px",
                                       "flexWrap": "wrap", "marginBottom": "8px"},
                                children=[
                                    html.Span([
                                        html.Span("FC máx est.", style={"fontSize": "11px", "color": "var(--text-muted)"}),
                                        html.Span(f" {max_hr:.0f} bpm", style={"fontSize": "13px", "fontWeight": "700", "color": "#27c98f", "marginLeft": "4px"}),
                                    ]),
                                    html.Span([
                                        html.Span("FC media est.", style={"fontSize": "11px", "color": "var(--text-muted)"}),
                                        html.Span(f" {avg_hr:.0f} bpm", style={"fontSize": "13px", "fontWeight": "700", "color": "#27c98f", "marginLeft": "4px"}),
                                    ]),
                                    html.Span([
                                        html.Span("Impactos est.", style={"fontSize": "11px", "color": "var(--text-muted)"}),
                                        html.Span(f" {impacts}", style={"fontSize": "13px", "fontWeight": "700", "color": "#e45a5a", "marginLeft": "4px"}),
                                    ]),
                                    html.Span(
                                        "⚠ Simulado desde movimiento — no es sensor real",
                                        style={"fontSize": "10px", "color": "var(--text-muted)",
                                               "fontStyle": "italic", "alignSelf": "center"},
                                    ),
                                ],
                            ),
                            html.P("FC estimada — Peto azul",
                                   style={"fontSize": "11px", "color": "var(--text-muted)", "marginBottom": "4px"}),
                            dcc.Graph(figure=hr_fig, config={"displayModeBar": False}),
                            _graph_interpretation([
                                "Esta curva es una estimación cardiovascular derivada del movimiento; no es ECG real ni diagnóstico.",
                                "Subidas de la línea sugieren tramos de mayor demanda o acumulación de acciones.",
                                "Las bandas de descanso ayudan a revisar si la señal baja o se estabiliza entre rounds.",
                            ]),
                            html.P("Impactos estimados — Peto azul",
                                   style={"fontSize": "11px", "color": "var(--text-muted)",
                                          "marginTop": "10px", "marginBottom": "4px"}),
                            dcc.Graph(figure=imu_fig2, config={"displayModeBar": False}),
                            _graph_interpretation([
                                "Cada línea vertical marca un evento de movimiento o impacto estimado desde la pose.",
                                "La altura en g es una referencia de intensidad simulada, no una medición de sensor real.",
                                "Úsala para ubicar momentos a revisar, no para cuantificar fuerza exacta.",
                            ]),
                            html.P(sim_note, style={"fontSize": "10px", "color": "var(--text-muted)",
                                                    "fontStyle": "italic", "marginTop": "4px"}),
                        ]
                except Exception:
                    pass

                _kf_times = ", ".join(
                    f"{float(item.get('t', 0.0)):.1f}s"
                    for item in (kf_meta or [])
                    if isinstance(item, dict)
                )
                _version_txt = result.get("analyzer_version") or _POSE_ANALYSIS_VERSION
                meta = html.P(
                    f"{summary.get('frames_analyzed', 0)} frames muestreados · "
                    f"{summary.get('paired_frames', len(duel_frames))} frames pareados · "
                    f"{result.get('fps', 0)} fps originales | "
                    f"version {_version_txt}"
                    + (f" | keyframes: {_kf_times}" if _kf_times else ""),
                    className="text-muted",
                    style={"fontSize": "11px", "marginTop": "8px"},
                )
                speed_section  = _speed_section(speed_data)
                biomech_section = _biomech_yolo_section(speed_data)
                rendered_output = html.Div([
                    duel_reading,
                    duel_compare,
                    duel_coaching_card,
                    duel_chart,
                    *speed_section,
                    *biomech_section,
                    *rounds_section,
                    *sim_section,
                    *skeleton_section,
                    _ai_note_placeholder,
                    meta,
                ])
                speed_store_data["rendered_output"] = rendered_output
                speed_store_data["rendered_progress"] = ""
                _pose_cache_put(speed_store_data, job_id=job_id)
                return rendered_output, _slim_pose_report(report_data, job_id), "tab-biomech", "", _ai_store_payload

            sport_name = {
                "taekwondo": "Taekwondo",
                "boxeo": "Boxeo",
                "combate": "Combate",
            }.get(biomech.get("sport"), "Combate")
            quality_pct = round(float(biomech.get("quality_ratio", 0) or 0) * 100)
            lower_asym = max(metrics.get("knee_asym", 0) or 0, metrics.get("hip_asym", 0) or 0)
            target_info = result.get("target") or biomech.get("target") or {}
            target_label = target_info.get("label") or "Automático"
            target_confidence = round(float(target_info.get("confidence", 0) or 0) * 100)
            track_continuity = round(float(target_info.get("continuity", 0) or 0) * 100)
            target_coverage = round(float(target_info.get("coverage", 0) or 0) * 100)
            target_note = html.P(
                (
                    f"Objetivo analizado: {target_label} · "
                    f"confianza de selección {target_confidence}% · "
                    f"cobertura {target_coverage}% · "
                    f"continuidad {track_continuity}% · "
                    f"candidatos vistos {int(target_info.get('candidates_seen', 0) or 0)}"
                ),
                className="text-muted",
                style={"fontSize": "12px", "marginTop": "4px"},
            )
            target_warning = (
                html.P(
                    "Si el peto elegido no coincide con el recuadro, prueba izquierda/derecha o mejora luz y encuadre.",
                    className="text-muted",
                    style={"fontSize": "12px", "marginTop": "4px", "color": "#f0a832"},
                )
                if target_info.get("key") in ("red", "blue") and target_confidence < 35 else None
            )
            time_note = (
                html.P(
                    "Análisis limitado por tiempo: se muestran resultados parciales útiles.",
                    className="text-muted",
                    style={"fontSize": "12px", "marginTop": "8px"},
                )
                if result.get("time_limited") else None
            )
            coaching = biomech.get("coaching") or {}
            confidence_label = coaching.get("confidence_label") or "Sin dato"
            confidence_score = coaching.get("confidence_score")
            confidence_color = (
                "var(--neon)" if confidence_label == "Alta"
                else "#f0a832" if confidence_label == "Media"
                else "var(--punch)"
            )
            single_evidence = _single_frame_evidence(frames, result.get("fps", 0), metrics)
            coaching_card = (
                html.Div(
                    className="card",
                    style={"marginBottom": "16px", "borderLeft": f"4px solid {confidence_color}"},
                    children=[
                        html.H4(_coaching_title, className="card-title"),
                        html.P(
                            f"Confianza de lectura: {confidence_label}"
                            + (f" ({confidence_score}%)" if confidence_score is not None else ""),
                            className="text-muted",
                            style={"color": confidence_color, "fontWeight": "700"},
                        ),
                        html.P(
                            (
                                f"{_viewer_name}, esta tarjeta convierte la grafica en decisiones para entrenar a {athlete_name}."
                                if _viewer_role == "coach"
                                else f"{athlete_name}, esta lectura convierte la grafica en una tarea concreta para tu siguiente sesion."
                            ),
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "8px"},
                        ),
                        html.P(coaching.get("graph_explanation") or "", className="text-muted"),
                        html.Div(
                            [html.Strong("Qué significa: "), coaching.get("meaning") or ""],
                            className="text-muted",
                            style={"fontSize": "13px", "marginBottom": "10px"},
                        ),
                        html.H5(_actions_header, style={"margin": "8px 0 6px"}),
                        html.Ul(
                            [html.Li(item) for item in (coaching.get("actions") or [])]
                            or [html.Li("Repetir la toma con mejor encuadre antes de decidir carga.")],
                            className="list-compact",
                        ),
                        html.H5(_drills_header, style={"margin": "10px 0 6px"}),
                        html.Ul(
                            [
                                html.Li([
                                    html.Strong(f"{drill.get('name', 'Ejercicio')}: "),
                                    f"{drill.get('dose', '')}. {drill.get('why', '')}",
                                ])
                                for drill in (coaching.get("drills") or [])
                            ] or [html.Li("Video corto de control: 2 tomas de 20-30 segundos.")],
                            className="list-compact",
                        ),
                        _evidence_list(single_evidence),
                    ],
                )
                if coaching else None
            )
            sport_reading = html.Div(
                className="card",
                style={"marginBottom": "16px", "borderLeft": "4px solid var(--neon)"},
                children=[
                    html.H4("Lectura deportiva", className="card-title"),
                    html.P(
                        f"Interpretación orientada a {sport_name}. Úsala como apoyo técnico, no como diagnóstico.",
                        className="text-muted",
                    ),
                    target_note,
                    target_warning,
                    html.Div(className="kpis session-kpis", children=[
                        _metric_card("Calidad pose", f"{quality_pct}%", "Frames con persona detectada"),
                        _metric_card("ROM tren inferior", f"{metrics.get('lower_rom', 0)}°", "Rodilla + cadera"),
                        _metric_card("Asimetría pierna", f"{lower_asym}°", "Rodilla/cadera"),
                        _metric_card("ROM tren superior", f"{metrics.get('upper_rom', 0)}°", "Codo + hombro"),
                    ]),
                    # ── Chamber angle TKD-only ────────────────────────────────
                    *([
                        html.H5("⚡ Cámara de pateo (TKD)", style={"margin": "12px 0 6px", "color": "var(--neon)"}),
                        html.P(
                            "Ángulo de rodilla al momento de máxima flexión pre-extensión. "
                            "Referencia WT élite: < 85° (pierna dominante).",
                            className="text-muted",
                            style={"fontSize": "12px", "marginBottom": "8px"},
                        ),
                        html.Div(className="kpis session-kpis", children=[
                            _metric_card(
                                "Cámara pierna izq",
                                f"{metrics.get('chamber_min_l') or '--'}°",
                                f"{metrics.get('kick_count_l') or 0} kick(s)",
                            ),
                            _metric_card(
                                "Cámara pierna der",
                                f"{metrics.get('chamber_min_r') or '--'}°",
                                f"{metrics.get('kick_count_r') or 0} kick(s)",
                            ),
                            _metric_card(
                                "Total kicks",
                                str((metrics.get('kick_count_l') or 0) + (metrics.get('kick_count_r') or 0)),
                                "Eventos detectados",
                            ),
                        ]),
                        html.Div(className="kpis session-kpis", style={"marginTop": "8px"}, children=[
                            _metric_card(
                                "Velocidad pico izq",
                                f"{metrics.get('kick_speed_max_l') or '--'} m/s",
                                "Tobillo en extensión",
                            ),
                            _metric_card(
                                "Velocidad pico der",
                                f"{metrics.get('kick_speed_max_r') or '--'} m/s",
                                "Tobillo en extensión",
                            ),
                            _metric_card(
                                "Ref. WT élite",
                                "≥ 10 m/s",
                                "Dollyo-chagi competición",
                            ),
                        ]),
                    ] if biomech.get("sport") == "taekwondo" and
                       ((metrics.get("kick_count_l") or 0) + (metrics.get("kick_count_r") or 0)) > 0
                    else []),
                    _joint_reliability_row(summary, int(target_info.get("selected_frames") or 0) or len(frames)),
                    html.Ul(
                        [
                            html.Li([
                                html.Strong(f"{item.get('title', 'Lectura')}: ",
                                            style={"color": _level_color(item.get("level"))}),
                                item.get("text", ""),
                            ])
                            for item in insights
                        ] or [html.Li("No hay suficientes datos para generar una lectura deportiva.")],
                        className="list-compact",
                    ),
                    html.Div(className="spacer-10"),
                    html.Div(
                        [
                            html.Strong("Siguiente foco: "),
                            " ".join(biomech.get("focus", [])[:2]) or "Repetir con mejor encuadre.",
                        ],
                        className="text-muted",
                        style={"fontSize": "12px"},
                    ),
                    time_note,
                ],
            )

            kpis = html.Div(className="kpis session-kpis", style={"marginBottom": "16px"}, children=[
                _kpi("Rodilla izq (máx)", summary.get("knee_l", {}),   "var(--neon)"),
                _kpi("Rodilla der (máx)", summary.get("knee_r", {}),   "var(--neon)"),
                _kpi("Codo izq (máx)",    summary.get("elbow_l", {}),  "var(--amber)"),
                _kpi("Codo der (máx)",    summary.get("elbow_r", {}),  "var(--amber)"),
                _kpi("Cadera izq (máx)",  summary.get("hip_l", {}),    "var(--green)"),
                _kpi("Cadera der (máx)",  summary.get("hip_r", {}),    "var(--green)"),
            ])

            # ── Gráficas de ángulos ────────────────────────────────────────
            import plotly.graph_objects as go
            ts = [f["t"] for f in frames]
            selected_frames = _to_int(target_info.get("selected_frames"), len(frames))
            frames_analyzed = _to_int(summary.get("frames_analyzed"), len(frames))
            landmark_warning_frames = _to_int(summary.get("landmark_warning_frames"), 0)
            track_rejections = _to_int(target_info.get("track_rejections"), _to_int(summary.get("track_rejections"), 0))

            def _angle_figure(trace_specs, title):
                fig = go.Figure()
                for key, name, color, dash in trace_specs:
                    line_style = {"color": color, "width": 2.1}
                    if dash:
                        line_style["dash"] = dash
                    fig.add_trace(go.Scatter(
                        x=ts,
                        y=[f.get(key) for f in frames],
                        name=name,
                        mode="lines",
                        connectgaps=False,
                        line=line_style,
                        hovertemplate="%{x:.1f}s · %{y:.1f}°<extra>" + name + "</extra>",
                    ))
                fig.update_layout(
                    title={"text": title, "font": {"size": 13, "color": "#d8e3ef"}, "x": 0.02},
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font={"color": "#a7b1bc", "size": 11},
                    legend={"orientation": "h", "y": -0.26, "font": {"size": 10}},
                    margin={"l": 42, "r": 12, "t": 46, "b": 70},
                    height=285,
                    hovermode="x unified",
                    xaxis={"title": "Tiempo (s)", "gridcolor": "#31445f", "zeroline": False},
                    yaxis={
                        "title": "Ángulo (°)",
                        "gridcolor": "#31445f",
                        "zeroline": False,
                        "range": [0, 185],
                    },
                )
                return fig

            lower_fig = _angle_figure(
                [
                    ("knee_l", "Rodilla izq", "#0d9488", None),
                    ("knee_r", "Rodilla der", "#06b6d4", None),
                    ("hip_l", "Cadera izq", "#34d399", "dash"),
                    ("hip_r", "Cadera der", "#6ee7b7", "dash"),
                ],
                "Tren inferior: base, amplitud y retorno",
            )
            upper_fig = _angle_figure(
                [
                    ("elbow_l", "Codo izq", "#f59e0b", "dot"),
                    ("elbow_r", "Codo der", "#fbbf24", "dot"),
                    ("shoulder_l", "Hombro izq", "#fb7185", "dash"),
                    ("shoulder_r", "Hombro der", "#f97316", "dash"),
                ],
                "Tren superior: guardia, extensión y compensaciones",
            )
            chart = html.Div(
                className="card",
                style={"marginBottom": "16px"},
                children=[
                    html.H4("Gráficas biomecánicas", className="card-title"),
                    html.P(
                        "Lee primero el tren inferior para base y patada/desplazamiento; luego el tren superior para guardia, extensión y compensaciones. "
                        "Los huecos no son un fallo visual: son ángulos que CombatIQ prefirió no inventar cuando el landmark venía dudoso.",
                        className="text-muted",
                        style={"fontSize": "12px", "marginBottom": "10px"},
                    ),
                    html.Div(className="kpis session-kpis", style={"marginBottom": "12px"}, children=[
                        _metric_card("Frames usados", f"{selected_frames}/{frames_analyzed}", "Datos aceptados"),
                        _metric_card("Continuidad", f"{track_continuity}%", "Seguimiento temporal"),
                        _metric_card("Landmarks limpiados", str(landmark_warning_frames), "Puntos dudosos descartados", "#f0a832"),
                        _metric_card("Rechazos tracking", str(track_rejections), "Saltos/color no confiables", "#f0a832"),
                    ]),
                    dcc.Tabs(
                        value="lower",
                        className="combatiq-tabs",
                        children=[
                            dcc.Tab(
                                label="Tren inferior",
                                value="lower",
                                className="combatiq-tab",
                                selected_className="combatiq-tab--active",
                                children=[
                                    dcc.Graph(
                                        figure=lower_fig,
                                        config={"displayModeBar": False},
                                        style={"marginTop": "8px"},
                                    ),
                                    _graph_interpretation([
                                        "Estas líneas muestran cómo cambian rodilla y cadera durante el video.",
                                        "Picos altos suelen indicar mayor amplitud de pierna; caídas o huecos pueden venir de retorno, base baja u oclusión.",
                                        "Compara izquierda contra derecha para detectar diferencia lateral, pero confirma el frame antes de corregir técnica.",
                                    ], open=True),
                                ],
                            ),
                            dcc.Tab(
                                label="Tren superior",
                                value="upper",
                                className="combatiq-tab",
                                selected_className="combatiq-tab--active",
                                children=[
                                    dcc.Graph(
                                        figure=upper_fig,
                                        config={"displayModeBar": False},
                                        style={"marginTop": "8px"},
                                    ),
                                    _graph_interpretation([
                                        "Estas líneas ayudan a leer guardia, extensión de brazos y posibles compensaciones del hombro.",
                                        "Si una curva desaparece o tiene huecos, el sistema prefirió no inventar un ángulo con landmarks dudosos.",
                                        "Úsala para revisar retorno a guardia y simetría del tren superior, no como diagnóstico de lesión.",
                                    ], open=True),
                                ],
                            ),
                        ],
                    ),
                ],
            )

            # ── Skeleton frame ─────────────────────────────────────────────
            skeleton_section = []
            if result.get("annotated_frame"):
                skeleton_section = [
                    html.Div(className="ecg-divider ecg-divider--spaced"),
                    html.P("Mejor frame con pose detectada:", className="text-muted",
                           style={"fontSize": "12px", "marginBottom": "6px"}),
                    html.Img(
                        src=result["annotated_frame"],
                        style={"width": "100%", "borderRadius": "10px",
                               "border": "1px solid var(--line)"},
                    ),
                    html.Div(
                        style={"display": "flex", "gap": "14px", "marginTop": "7px", "flexWrap": "wrap"},
                        children=[
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#50d200","marginRight":"5px","verticalAlign":"middle"}), html.Span("Alta confianza", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#ff9600","marginRight":"5px","verticalAlign":"middle"}), html.Span("Media confianza", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                            html.Span([html.Span(style={"display":"inline-block","width":"9px","height":"9px","borderRadius":"50%","background":"#dc3c3c","marginRight":"5px","verticalAlign":"middle"}), html.Span("Baja / articulación incierta", style={"fontSize":"11px","color":"var(--text-muted)"})]),
                        ],
                    ),
                ]

            meta = html.P(
                f"{summary.get('frames_analyzed', 0)} frames analizados · "
                f"{summary.get('duration_s', 0)} s · "
                f"{result.get('fps', 0)} fps originales | "
                f"version {result.get('analyzer_version') or _POSE_ANALYSIS_VERSION}",
                className="text-muted",
                style={"fontSize": "11px", "marginTop": "8px"},
            )

            speed_section   = _speed_section(speed_data)
            biomech_section = _biomech_yolo_section(speed_data)
            output_children = [sport_reading]
            if coaching_card:
                output_children.append(coaching_card)
            output_children.extend([kpis, *speed_section, *biomech_section, chart, *skeleton_section, meta])
            rendered_output = html.Div(output_children)
            speed_store_data["rendered_output"] = rendered_output
            speed_store_data["rendered_progress"] = ""
            _pose_cache_put(speed_store_data, job_id=job_id)
            # Análisis individual: no hay nota de IA de duelo → pose-ai-note-store a None
            return rendered_output, _slim_pose_report(report_data, job_id), "tab-biomech", "", None

        # Callback 4/4 — AI duel insight (async, no bloquea UI)
        @app.callback(
            Output("pose-ai-note-container", "children"),
            Input("pose-ai-note-store",      "data"),
            prevent_initial_call=True,
        )
        def _pose_step4_ai_duel(store):
            job_id = (store or {}).get("job_id")
            if not job_id:
                raise PreventUpdate
            cached = _pose_cache_get(job_id)
            if not cached or not cached.get("result"):
                raise PreventUpdate
            result = cached["result"]
            if not result.get("duel"):
                raise PreventUpdate
            _viewer_role  = (store or {}).get("viewer_role",  "deportista")
            _viewer_name  = (store or {}).get("viewer_name",  "")
            athlete_name  = (store or {}).get("athlete_name", "Deportista")
            _session_sport = (store or {}).get("session_sport", "")
            duel_frames   = (result.get("duel") or {}).get("frames") or []
            fps_val       = result.get("fps", 0)
            pressure_lbl  = ((result.get("duel") or {}).get("metrics") or {}).get("pressure_label", "")
            duel_evidence = _duel_frame_evidence(duel_frames, fps_val, pressure_lbl)
            try:
                import ai_insights as _AI_duel
                duel_payload = result.get("duel") or {}
                sport_for_ai = (result.get("biomech") or {}).get("sport") or "taekwondo"
                ai_note = _AI_duel.generate_duel_insight(
                    duel_payload,
                    sport=sport_for_ai,
                    audience=_viewer_role,
                    athlete_name=athlete_name,
                    coach_name=_viewer_name,
                )
                if ai_note:
                    return [
                        html.Div(className="ecg-divider ecg-divider--spaced"),
                        html.Div(
                            [
                                html.P(
                                    "Lectura del combate (IA)",
                                    style={"fontWeight": "700", "fontSize": "13px",
                                           "color": "var(--accent)", "marginBottom": "8px"},
                                ),
                                dcc.Markdown(
                                    ai_note,
                                    style={"fontSize": "13px", "lineHeight": "1.65",
                                           "color": "var(--text)"},
                                ),
                                _evidence_list(duel_evidence),
                            ],
                            style={
                                "background": "var(--card-bg)",
                                "border": "1px solid var(--line)",
                                "borderRadius": "10px",
                                "padding": "14px 16px",
                            },
                        ),
                    ]
            except Exception:
                pass
            return []

        @app.callback(
            Output("pose-output",   "children", allow_duplicate=True),
            Output("pose-progress", "children", allow_duplicate=True),
            Input("ecg-main-tabs",  "value"),
            Input("pose-results",   "data"),
            prevent_initial_call=True,
        )
        def restore_pose_output(tab_value, pose_data):
            """Keep the completed biomech result visible when the tab/layout remounts."""
            if tab_value != "tab-biomech" or not pose_data:
                raise PreventUpdate

            current_uid = _safe_int(session.get("user_id") or session.get("id"))
            pose_uid = _safe_int((pose_data or {}).get("user_id"))
            if pose_uid and current_uid and pose_uid != current_uid:
                return html.P(
                    "La lectura anterior pertenece a otra sesión. Ejecuta un nuevo análisis para este usuario.",
                    className="text-muted",
                    style={"fontSize": "13px"},
                ), ""

            if (pose_data or {}).get("analyzer_version") != _POSE_ANALYSIS_VERSION:
                return html.Div(
                    className="card",
                    style={"borderLeft": "4px solid #f0a832"},
                    children=[
                        html.H4("Lectura biomecánica anterior", className="card-title"),
                        html.P(
                            "Actualizamos el filtro de confianza para evitar falsos positivos de atleta. "
                            "Ejecuta de nuevo el análisis para regenerar frames y gráficas con la versión actual.",
                            className="text-muted",
                        ),
                    ],
                ), "Lectura anterior invalidada por mejora del analizador."

            job_id = (pose_data or {}).get("job_id")
            cached = _pose_cache_get(job_id)
            if cached and cached.get("rendered_output") is not None:
                return cached["rendered_output"], cached.get("rendered_progress", "")

            summary = (pose_data or {}).get("summary") or {}
            target = (pose_data or {}).get("target") or {}
            filename = (pose_data or {}).get("filename") or "video analizado"
            return html.Div(
                className="card",
                style={"borderLeft": "4px solid #f0a832"},
                children=[
                    html.H4("Lectura biomecánica preservada", className="card-title"),
                    html.P(
                        "CombatIQ conserva la referencia del análisis, pero la vista completa ya no está en caché del servidor. "
                        "Para mostrar gráficas e imágenes de nuevo, repite el análisis.",
                        className="text-muted",
                    ),
                    html.Ul(
                        [
                            html.Li(f"Video: {filename}"),
                            html.Li(f"Objetivo: {target.get('label') or 'Automático'}"),
                            html.Li(f"Frames analizados: {summary.get('frames_analyzed', 'sin dato')}"),
                        ],
                        className="list-compact",
                    ),
                ],
            ), "Referencia recuperada; cache visual no disponible."

        @app.callback(
            Output("pose-results",         "data",     allow_duplicate=True),
            Output("pose-mediapipe-store", "data",     allow_duplicate=True),
            Output("pose-speed-store",     "data",     allow_duplicate=True),
            Output("pose-ai-note-store",   "data",     allow_duplicate=True),
            Output("pose-output",          "children", allow_duplicate=True),
            Output("pose-progress",        "children", allow_duplicate=True),
            Output("pose-report-msg",      "children", allow_duplicate=True),
            Input("pose-target-select",    "value"),
            State("pose-results",          "data"),
            prevent_initial_call=True,
        )
        def reset_pose_when_target_changes(pose_target, pose_data):
            """Changing target means the previous biomech reading no longer applies."""
            job_id = (pose_data or {}).get("job_id")
            if job_id:
                _POSE_JOB_CACHE.pop(str(job_id), None)
            label = {
                "auto": "Auto (mejor pose)",
                "red": "Peto rojo",
                "blue": "Peto azul",
                "duel": "Rojo vs azul",
                "left": "Atleta izquierda",
                "right": "Atleta derecha",
            }.get(str(pose_target or "auto"), "nuevo objetivo")
            return (
                None,
                None,
                None,
                None,  # pose-ai-note-store
                html.P(
                    f"Cambiaste el objetivo a {label}. Presiona 'Analizar postura' para generar una nueva lectura.",
                    className="text-muted",
                    style={"fontSize": "13px"},
                ),
                "",
                "",
            )

        @app.callback(
            Output("dl-pose-report", "data"),
            Output("pose-report-msg", "children"),
            Input("btn-dl-pose-report", "n_clicks"),
            State("pose-results", "data"),
            prevent_initial_call=True,
        )
        def download_pose_report(n, pose_data):
            if not n:
                raise PreventUpdate
            if not pose_data:
                return dash.no_update, "Analiza un video primero para descargar la lectura."
            pose_data = _resolve_pose_report_data(pose_data)
            if not pose_data:
                return dash.no_update, "La lectura ya no esta en memoria. Vuelve a ejecutar el analisis para exportarla."

            if not _REPORTLAB_OK:
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            def _num(value, default=0.0):
                try:
                    return float(value)
                except Exception:
                    return default

            def _angle_stats(label, key, summary, metrics):
                stats = summary.get(key, {}) or {}
                return [
                    label,
                    f"{_num(stats.get('avg')):.1f}",
                    f"{_num(stats.get('max')):.1f}",
                    f"{_num(stats.get('min')):.1f}",
                    f"{_num(metrics.get(f'rom_{key}')):.1f}",
                ]

            try:
                from report_utils import CombatIQPDF, safe_filename_stem

                summary = pose_data.get("summary") or {}
                biomech = pose_data.get("biomech") or {}
                metrics = biomech.get("metrics") or {}
                insights = biomech.get("insights") or []
                focus = biomech.get("focus") or []
                coaching = biomech.get("coaching") or {}
                target_info = pose_data.get("target") or biomech.get("target") or {}
                target_label = target_info.get("label") or "Automático"
                athlete_name = pose_data.get("athlete_name") or "Deportista"
                sport_key = (pose_data.get("sport") or biomech.get("sport") or "combate").lower()
                sport_label = {
                    "taekwondo": "Taekwondo",
                    "boxeo": "Boxeo",
                    "combate": "Combate",
                }.get(sport_key, "Combate")
                filename = pose_data.get("filename") or "video"
                quality_pct = round(_num(biomech.get("quality_ratio")) * 100)
                status = "ok" if quality_pct >= 65 else ("warn" if quality_pct >= 40 else "alert")
                duel = pose_data.get("duel") or biomech.get("duel") or {}

                if duel:
                    import plotly.graph_objects as go

                    duel_metrics = duel.get("metrics") or {}
                    duel_coaching = duel.get("coaching") or {}
                    duel_frames = duel.get("frames") or []
                    red_target = ((duel.get("red") or {}).get("target") or {})
                    blue_target = ((duel.get("blue") or {}).get("target") or {})
                    dual_score = _num(duel_coaching.get("confidence_score"))
                    dual_status = "ok" if dual_score >= 75 else ("warn" if dual_score >= 50 else "alert")

                    pdf = CombatIQPDF()
                    pdf.header(
                        "Informe táctico rojo vs azul",
                        "Lectura de distancia, presión, amplitud e intercambios para revisión técnica del combate.",
                        athlete_name,
                        sport_label,
                        session="Análisis rojo vs azul",
                        source=filename,
                    )
                    pdf.status_badge(f"Confianza dual {round(dual_score)}%", dual_status)
                    pdf.metric_table([
                        {"label": "Frames pareados", "value": int(_num(duel.get("frames_paired"))), "unit": "rojo+azul"},
                        {"label": "Distancia media", "value": f"{_num(duel_metrics.get('avg_distance')):.3f}", "unit": "normalizada"},
                        {"label": "Intercambios", "value": int(_num(duel_metrics.get("exchange_count"))), "unit": "posibles"},
                        {"label": "Tendencia", "value": duel_metrics.get("pressure_label", "Sin dato"), "unit": ""},
                    ])
                    pdf.card(
                        "Lectura táctica",
                        [
                            f"{item.get('title', 'Lectura')}: {item.get('text', '')}"
                            for item in (duel.get("insights") or [])
                            if item
                        ] or ["No hubo datos suficientes para una lectura táctica completa."],
                        subtitle="No declara ganador ni puntos oficiales; ubica momentos para revisar con el coach.",
                    )
                    pdf.card(
                        "IA táctica",
                        [
                            f"Confianza de lectura: {duel_coaching.get('confidence_label', 'Sin dato')} ({duel_coaching.get('confidence_score', 0)}%).",
                            duel_coaching.get("graph_explanation") or "",
                            f"Qué significa: {duel_coaching.get('meaning') or ''}",
                        ]
                        + [f"Acción: {item}" for item in (duel_coaching.get("actions") or [])]
                        + [f"Límite: {item}" for item in (duel_coaching.get("limitations") or [])],
                        subtitle="Traducción accionable para coach y atleta.",
                    )

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=[f.get("t") for f in duel_frames],
                        y=[f.get("distance") for f in duel_frames],
                        name="Distancia rojo-azul",
                        mode="lines",
                        line={"color": "#0891b2", "width": 2.4},
                    ))
                    exchange_frames = [f for f in duel_frames if f.get("exchange")]
                    if exchange_frames:
                        fig.add_trace(go.Scatter(
                            x=[f.get("t") for f in exchange_frames],
                            y=[f.get("distance") for f in exchange_frames],
                            name="Posible intercambio",
                            mode="markers",
                            marker={"color": "#f97316", "size": 8, "symbol": "diamond"},
                        ))
                    fig.update_layout(
                        paper_bgcolor="white",
                        plot_bgcolor="white",
                        font={"color": "#0f172a", "size": 11},
                        legend={"orientation": "h", "y": -0.22},
                        margin={"l": 46, "r": 18, "t": 20, "b": 58},
                        height=320,
                        xaxis={"title": "Tiempo (s)", "gridcolor": "#e2e8f0", "zeroline": False},
                        yaxis={"title": "Distancia normalizada", "gridcolor": "#e2e8f0", "zeroline": False},
                    )
                    pdf.chart(fig, "Gráfica de distancia rojo vs azul")

                    pdf.section_title(
                        "Comparativa rojo vs azul",
                        "Confianza, continuidad y amplitud global por peto.",
                    )
                    pdf.table(
                        ["Peto", "Conf.", "Continuidad", "ROM pierna", "ROM superior"],
                        [
                            [
                                "Rojo",
                                f"{round(_num(red_target.get('confidence')) * 100)}%",
                                f"{round(_num(red_target.get('continuity')) * 100)}%",
                                f"{_num(duel_metrics.get('red_lower_rom')):.1f}",
                                f"{_num(duel_metrics.get('red_upper_rom')):.1f}",
                            ],
                            [
                                "Azul",
                                f"{round(_num(blue_target.get('confidence')) * 100)}%",
                                f"{round(_num(blue_target.get('continuity')) * 100)}%",
                                f"{_num(duel_metrics.get('blue_lower_rom')):.1f}",
                                f"{_num(duel_metrics.get('blue_upper_rom')):.1f}",
                            ],
                        ],
                    )
                    pdf.card(
                        "Nota de uso",
                        [
                            "Los intercambios se detectan por cercanía y movimiento simultáneo; deben confirmarse visualmente.",
                            "La presión estimada no equivale a dominio competitivo ni puntuación oficial.",
                        ],
                        subtitle="Control de calidad táctico",
                    )
                    pdf_bytes = pdf.finish()
                    safe_name = safe_filename_stem(athlete_name, "deportista")
                    out_name = f"CombatIQ_rojo_vs_azul_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
                    return dcc.send_bytes(lambda b: b.write(pdf_bytes), out_name), ""

                pdf = CombatIQPDF()
                pdf.header(
                    "Informe biomecánico de combate",
                    "Lectura automatizada de postura, rangos de movimiento y asimetrías relevantes para entrenamiento.",
                    athlete_name,
                    sport_label,
                    session="Análisis de pose",
                    source=filename,
                )
                pdf.status_badge(f"Calidad de pose {quality_pct}%", status)
                _pdf_pose_rows = [
                    {"label": "Frames analizados", "value": int(_num(summary.get("frames_analyzed"))), "unit": "muestras"},
                    {"label": "ROM tren inferior", "value": f"{_num(metrics.get('lower_rom')):.1f}", "unit": "grados"},
                    {"label": "Asimetría pierna", "value": f"{max(_num(metrics.get('knee_asym')), _num(metrics.get('hip_asym'))):.1f}", "unit": "grados"},
                    {"label": "ROM tren superior", "value": f"{_num(metrics.get('upper_rom')):.1f}", "unit": "grados"},
                ]
                # Añadir métricas de cámara de pateo solo en TKD
                if biomech.get("sport") == "taekwondo":
                    _ch_l  = metrics.get("chamber_min_l")
                    _ch_r  = metrics.get("chamber_min_r")
                    _sp_l  = metrics.get("kick_speed_max_l")
                    _sp_r  = metrics.get("kick_speed_max_r")
                    _kicks = (metrics.get("kick_count_l") or 0) + (metrics.get("kick_count_r") or 0)
                    if _kicks > 0:
                        _pdf_pose_rows += [
                            {"label": "Cámara pierna izq (mín)", "value": f"{_ch_l:.1f}" if _ch_l is not None else "--", "unit": "grados"},
                            {"label": "Cámara pierna der (mín)", "value": f"{_ch_r:.1f}" if _ch_r is not None else "--", "unit": "grados"},
                            {"label": "Velocidad pico izq",  "value": f"{_sp_l:.2f}" if _sp_l is not None else "--", "unit": "m/s"},
                            {"label": "Velocidad pico der",  "value": f"{_sp_r:.2f}" if _sp_r is not None else "--", "unit": "m/s"},
                            {"label": "Kicks detectados",    "value": int(_kicks), "unit": "eventos"},
                        ]
                pdf.metric_table(_pdf_pose_rows)

                pdf.card(
                    "Objetivo analizado",
                    [
                        f"Selector usado: {target_label}.",
                        (
                            f"Candidatos vistos: {int(_num(target_info.get('candidates_seen')))}. "
                            f"Frames seleccionados: {int(_num(target_info.get('selected_frames')))}. "
                            f"Confianza media: {round(_num(target_info.get('confidence')) * 100)}%. "
                            f"Continuidad temporal: {round(_num(target_info.get('continuity')) * 100)}%."
                        ),
                        f"Rechazos por tracking/color: {int(_num(target_info.get('track_rejections')))}.",
                    ],
                    subtitle="Selección multipersona del análisis biomecánico.",
                )
                if coaching:
                    drill_lines = [
                        f"{drill.get('name', 'Ejercicio')}: {drill.get('dose', '')}. {drill.get('why', '')}"
                        for drill in (coaching.get("drills") or [])
                        if drill
                    ]
                    pdf.card(
                        _coaching_title,
                        [
                            f"Confianza de lectura: {coaching.get('confidence_label', 'Sin dato')} ({coaching.get('confidence_score', 0)}%).",
                            coaching.get("graph_explanation") or "",
                            f"Qué significa: {coaching.get('meaning') or ''}",
                        ]
                        + [f"Acción: {item}" for item in (coaching.get("actions") or [])]
                        + [f"Ejercicio: {item}" for item in drill_lines],
                        subtitle="Explicación de la gráfica y acciones prácticas sugeridas.",
                    )

                insight_lines = [
                    f"{item.get('title', 'Lectura')}: {item.get('text', '')}"
                    for item in insights
                    if item
                ]
                if not insight_lines:
                    insight_lines = ["No hubo datos suficientes para emitir una lectura deportiva completa."]
                pdf.card(
                    "Lectura deportiva",
                    insight_lines,
                    subtitle=f"Orientada a {sport_label}. Usar como apoyo técnico, no como diagnóstico clínico.",
                )
                pdf.card(
                    "Siguiente foco",
                    focus or ["Repetir el analisis con mejor encuadre y video estable."],
                    subtitle="Prioridades accionables para la siguiente sesión.",
                )
                pdf.card(
                    "Como leer las graficas biomecanicas",
                    [
                        "Tren inferior: rodilla y cadera ayudan a leer base, amplitud de patada, desplazamiento y retorno.",
                        "Tren superior: codo y hombro ayudan a leer guardia, extension, compensaciones y perdida de estructura.",
                        (
                            f"Frames usados: {int(_num(target_info.get('selected_frames'), _num(summary.get('frames_analyzed'))))}/"
                            f"{int(_num(summary.get('frames_analyzed')))}. "
                            f"Landmarks limpiados: {int(_num(summary.get('landmark_warning_frames')))}. "
                            f"Rechazos por tracking/color: {int(_num(target_info.get('track_rejections'), _num(summary.get('track_rejections'))))}."
                        ),
                        "Si aparecen huecos en una curva, significa que CombatIQ descarto puntos dudosos para evitar una lectura falsa.",
                    ],
                    subtitle="Guia de lectura antes de revisar la tabla.",
                )

                rows = [
                    _angle_stats("Rodilla izquierda", "knee_l", summary, metrics),
                    _angle_stats("Rodilla derecha", "knee_r", summary, metrics),
                    _angle_stats("Cadera izquierda", "hip_l", summary, metrics),
                    _angle_stats("Cadera derecha", "hip_r", summary, metrics),
                    _angle_stats("Codo izquierdo", "elbow_l", summary, metrics),
                    _angle_stats("Codo derecho", "elbow_r", summary, metrics),
                    _angle_stats("Hombro izquierdo", "shoulder_l", summary, metrics),
                    _angle_stats("Hombro derecho", "shoulder_r", summary, metrics),
                ]
                pdf.section_title(
                    "Resumen de ángulos",
                    "Promedio, máximo, mínimo y rango de movimiento por articulación.",
                )
                pdf.table(["Articulacion", "Prom.", "Max.", "Min.", "ROM"], rows)
                pdf.card(
                    "Nota de uso",
                    [
                        "La lectura depende de encuadre, luz, distancia y visibilidad completa del cuerpo.",
                        "Para decisiones de carga o retorno competitivo, contrastar con el criterio del coach y del equipo médico.",
                    ],
                    subtitle="Control de calidad",
                )

                pdf_bytes = pdf.finish()
                safe_name = safe_filename_stem(athlete_name, "deportista")
                out_name = f"CombatIQ_biomecanica_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
                return dcc.send_bytes(lambda b: b.write(pdf_bytes), out_name), ""
            except Exception as exc:
                return dash.no_update, f"No pude generar el informe biomecánico: {exc}"

        # ── Show save-session row when analysis completes ─────────────────
        @app.callback(
            Output("pose-save-row", "style"),
            Input("pose-results", "data"),
            prevent_initial_call=True,
        )
        def toggle_pose_save_row(pose_data):
            hidden = {"display": "none"}
            visible = {"display": "flex", "marginTop": "10px", "gap": "8px",
                       "alignItems": "center", "flexWrap": "wrap"}
            return visible if pose_data else hidden

        # ── Save pose session to DB ────────────────────────────────────────
        @app.callback(
            Output("pose-save-msg", "children"),
            Input("btn-save-pose-session", "n_clicks"),
            State("pose-session-name", "value"),
            State("pose-results", "data"),
            prevent_initial_call=True,
        )
        def save_pose_session(n, session_name_raw, pose_data):
            if not n:
                raise PreventUpdate
            if not pose_data:
                return "Analiza un video primero."
            pose_data = _resolve_pose_report_data(pose_data) or pose_data
            uid = _safe_int(session.get("user_id") or session.get("id") or pose_data.get("user_id"))
            if not uid:
                return "Inicia sesión para guardar."
            sport = (session.get("sport") or
                     pose_data.get("sport") or
                     (pose_data.get("biomech") or {}).get("sport") or
                     "combate")
            filename = pose_data.get("filename") or "video"
            name = str(session_name_raw or "").strip() or filename
            summary = pose_data.get("summary") or {}
            target_info = pose_data.get("target") or {}
            duel = pose_data.get("duel") or (pose_data.get("biomech") or {}).get("duel") or {}
            paired_frames = _safe_int(summary.get("paired_frames") or duel.get("frames_paired")) or 0
            analyzed_frames = _safe_int(summary.get("frames_analyzed")) or 0
            target_label = target_info.get("label") or "Video"
            version = pose_data.get("analyzer_version") or _POSE_ANALYSIS_VERSION
            notes = (
                f"Combat Monitor | {sport} (Video) | {target_label} | "
                f"{paired_frames} frames pareados | {analyzed_frames} frames muestreados | "
                f"{name} | archivo {filename} | version {version}"
            )
            try:
                sid = db.create_session(
                    athlete_id=uid,
                    created_by=uid,
                    sport=sport,
                    notes=notes,
                )
                try:
                    db.close_session(int(sid))
                except Exception:
                    pass
                return f"Sesión guardada (ID {sid})."
            except Exception as exc:
                return f"Error al guardar: {exc}"
