# views/signals_view.py

import os
import io
import base64
import csv
import re
import uuid
import json
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, graph_config

import dash
from dash import html, dcc, Input, Output, State
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


# ========= Helpers comunes =========

def smooth(x: np.ndarray, win_ms: int, fs: int):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return x

    win = max(3, int(round(win_ms * fs / 1000)))
    if win % 2 == 0:
        win += 1
    if win >= len(x):
        win = max(3, (len(x) // 2) * 2 + 1)
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
):
    if not _REPORTLAB_OK:
        raise RuntimeError(str(_REPORTLAB_ERR))

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

    def draw_card(ypos, title, body_lines, subtitle=None, fill=SURFACE, accent=ACCENT):
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

    def fig_to_png_bytes(current_fig):
        try:
            return current_fig.to_image(format="png", scale=2), None
        except Exception as e:
            return None, str(e)

    def draw_plot_card(ypos, title, png_bytes=None, fallback_text=None):
        card_h = 9.8 * cm
        ypos = page_break(ypos, card_h + 10)
        bottom = ypos - card_h

        c.setFillColor(WHITE)
        c.roundRect(x0, bottom, usable_w, card_h, 12, fill=1, stroke=0)
        c.setStrokeColor(BORDER)
        c.setLineWidth(1)
        c.roundRect(x0, bottom, usable_w, card_h, 12, fill=0, stroke=1)
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x0 + 12, ypos - 16, _report_text(title))

        if png_bytes:
            img = ImageReader(io.BytesIO(png_bytes))
            img_w, img_h = img.getSize()
            max_w = usable_w - 24
            max_h = card_h - 38
            scale = min(max_w / img_w, max_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            c.drawImage(
                img,
                x0 + 12,
                bottom + 12,
                width=draw_w,
                height=draw_h,
                mask="auto",
            )
        else:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 10)
            wrapped = simpleSplit(_report_text(fallback_text or ""), "Helvetica", 10, usable_w - 24)
            cursor = ypos - 38
            for line in wrapped:
                c.drawString(x0 + 12, cursor, line)
                cursor -= 13

        return bottom - 14

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
    c.drawString(x0 + 14, y - 46, _report_text(report_title))
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 10)
    meta_lines = [
        f"Deportista: {athlete_name}",
        f"Deporte: {sport or '-'}",
        f"Señal: {source_label}",
        f"Sesión: {session_label}",
        f"Generado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
    ]
    meta_y = y - 66
    for line in meta_lines:
        c.drawString(x0 + 14, meta_y, _report_text(line))
        meta_y -= 12
    y = bottom - 16

    y = draw_card(
        y,
        "Resumen rápido",
        [summary],
        subtitle=report_subtitle,
        fill=SURFACE_ALT,
    )

    y = draw_card(
        y,
        "Indicadores clave",
        metric_lines,
        subtitle="Lectura breve de los valores principales de esta señal.",
    )

    png_bytes = None
    png_error = None
    if fig is not None:
        png_bytes, png_error = fig_to_png_bytes(fig)
    y = draw_plot_card(
        y,
        figure_title or "Grafica principal",
        png_bytes=png_bytes,
        fallback_text=(
            "No se pudo incrustar la gráfica en el PDF. "
            "Si quieres incluirla, instala kaleido en tu entorno con: python -m pip install -U kaleido."
            if png_error or fig is None
            else ""
        ),
    )

    y = draw_card(
        y,
        "Cómo leer esta gráfica",
        explain_lines,
        subtitle="La idea es que puedas entender rápido qué muestra la señal y para qué sirve.",
    )

    y = draw_card(
        y,
        "Nota de uso",
        note_lines,
    )

    draw_footer()
    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


# ========= Upload safety helpers =========

_ALLOWED_EXTS = {".csv"}


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
        return base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e


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


def detect_r_peaks(x: np.ndarray, fs: int, sens: float = 0.6):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
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


def fig_ecg(t_line, x_line, peaks_t=None, peaks_y=None, title="ECG"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=x_line, mode="lines", name="ECG",
        line=dict(color="#00f28a", width=2)
    ))
    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Picos R",
            marker=dict(size=7, symbol="x", color="#00f28a")
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
    return [
        kpi_card("Ritmo cardíaco", f"{bpm:.0f}"),
        kpi_card("Variabilidad", f"{sdnn:.0f}", " ms"),
        kpi_card("Recuperación", f"{rmssd:.0f}", " ms"),
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
        line=dict(color="#00f28a", width=2)
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
            marker=dict(symbol="x", size=8, color="#00f28a")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="|a| (m/s²)",
        height=420,
    )
    return fig

def kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int):
    return [
        kpi_card("Acciones detectadas", f"{n_hits}"),
        kpi_card("Ritmo de acción", f"{hits_per_min:.1f}"),
        kpi_card("Explosividad media", f"{mean_int:.2f}", " g"),
        kpi_card("Pico de explosividad", f"{max_int:.2f}", " g"),
    ]
# ========= EMG =========

def read_emg_csv(path: str, fs_default: int = 1000):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)
    data_rows = rows[1:] if has_header else rows

    time_idx = None
    emg_idx = None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_idx = i
            if name in ("emg", "ch1", "signal") and emg_idx is None:
                emg_idx = i
        if emg_idx is None:
            for i, name in enumerate(header):
                if i != time_idx:
                    emg_idx = i
                    break
    else:
        time_idx, emg_idx = None, 0
        data_rows = rows

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, x_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(to_float(r[emg_idx]))
        except Exception:
            continue

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
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


def emg_metrics(x: np.ndarray, fs: int):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0.0, 0.0, 0.0

    x0 = x - np.mean(x)
    rms_global = float(np.sqrt(np.mean(x0 ** 2)))
    peak = float(np.max(np.abs(x0)))

    n = len(x0)
    if n < 30:
        fatigue = 0.0
    else:
        third = n // 3
        first_rms = float(np.sqrt(np.mean(x0[:third] ** 2)))
        last_rms = float(np.sqrt(np.mean(x0[-third:] ** 2)))
        if first_rms > 1e-6:
            fatigue = max(0.0, min(100.0, 100.0 * (1.0 - last_rms / first_rms)))
        else:
            fatigue = 0.0

    return rms_global, peak, fatigue


def fig_emg(t_line, env_line, fs: int, thr=None, title="EMG"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=env_line, mode="lines", name="EMG (envolvente)",
        line=dict(color="#00f28a", width=2)
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
                text=f"Umbral (P90) ≈ {float(thr):.3f}",
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

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_emg(rms, peak, fatigue):
    return [
        kpi_card("RMS global", f"{rms:.3f}"),
        kpi_card("Pico absoluto", f"{peak:.3f}"),
        kpi_card("Fatiga estimada", f"{fatigue:.1f}", " %"),
    ]


# ========= Respiración =========

def read_resp_csv(path: str, fs_default: int = 25):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)
    data_rows = rows[1:] if has_header else rows

    time_idx, resp_idx = None, None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_idx = i
            if name in ("resp", "breath", "band") and resp_idx is None:
                resp_idx = i
        if resp_idx is None:
            for i, name in enumerate(header):
                if i != time_idx:
                    resp_idx = i
                    break
    else:
        time_idx, resp_idx = None, 0

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, x_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(to_float(r[resp_idx]))
        except Exception:
            continue

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
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


def resp_metrics(t: np.ndarray, x: np.ndarray, fs: int, sens: float = 0.6):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0, 0.0, 0.0, np.array([], dtype=int)

    x0 = x - np.mean(x)
    env = smooth(x0, win_ms=250, fs=fs)
    thr = np.quantile(env, sens)
    dist = int(0.8 * fs)
    peaks = _find_peaks_simple(env, height=thr, distance=dist)

    n_breaths = int(len(peaks))
    duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    br_min = (n_breaths / (duration / 60.0)) if duration > 0 else 0.0

    if n_breaths > 1:
        periods = np.diff(t[peaks])
        mean_period = float(np.mean(periods))
    else:
        mean_period = 0.0

    return n_breaths, br_min, mean_period, peaks


def fig_resp(t_line, env_line, peaks_t=None, peaks_y=None, thr=None, title="Respiración"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=env_line, mode="lines", name="Resp (filtrada)",
        line=dict(color="#00f28a", width=2)
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
                text=f"Umbral (P90) ≈ {float(thr):.3f}",
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
            mode="markers", name="Inhalaciones",
            marker=dict(symbol="x", size=8, color="#00f28a")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_resp(n_breaths, br_min, mean_period):
    return [
        kpi_card("Respiraciones", f"{n_breaths}"),
        kpi_card("Ritmo respiratorio", f"{br_min:.1f}"),
        kpi_card("Tiempo entre respiraciones", f"{mean_period:.2f}", " s"),
    ]


# ========= Clase principal =========

class SignalsView:
    """
    Vista de análisis de sesión enfocada en el core comercial del MVP:
    ECG/HRV e IMU como flujo operativo principal.

    EMG y respiración quedan desactivados del frente de la app,
    pero se conservan en el código para una posible reactivación futura.
    """

    # Soporta distintos nombres por si en DB guardaste códigos distintos
    _SENSOR_ALIASES = {
        "ECG": {"ECG"},
        "IMU": {"IMU", "IMU_ARM", "IMU_LEG", "IMU_HEAD"},
        "EMG": {"EMG", "EMG_ARM", "EMG_LEG"},
        "RESP_BELT": {"RESP_BELT", "RESP", "RESPIRATION", "BREATH"},
    }

    def __init__(self, app: dash.Dash, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module
        self._register_callbacks()

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

    def layout(self):
        role = (session.get("role") or "no autenticado")
        uid = session.get("user_id")

        if role == "coach" and uid:
            coach_sport = str(session.get("sport") or "").strip() or None
            athletes = self.db.list_athletes_for_coach(int(uid), sport=coach_sport)
        elif role == "deportista" and uid:
            u = self.db.get_user_by_id(int(uid))
            athletes = [u] if u and u.get("role") == "deportista" else []
        else:
            athletes = [u for u in self.db.list_users()
                        if (u.get("role", "deportista") == "deportista")]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]
        default_user = options_users[0]["value"] if options_users else None

        current_sport = None
        if default_user:
            try:
                current_sport = (self.db.get_user_by_id(int(default_user)) or {}).get("sport")
            except Exception:
                current_sport = None
        imu_profile = self._imu_profile(current_sport)

        sensors_text = "Inicia sesión como deportista para ver tus sensores."
        if uid and role == "deportista":
            try:
                codes = self.db.get_user_sensors(int(uid)) or []
            except Exception:
                codes = []
            if codes:
                labels = [self.S.catalog()[c]["short"] for c in codes if c in self.S.catalog()]
                sensors_text = " · ".join(labels) if labels else "Sensores asignados (sin etiquetas)."
            else:
                sensors_text = "Sin sensores asignados aún."

        # ── Selector de deportista — visible para coach, oculto para atleta ──
        if role == "deportista":
            # El ID debe existir para los callbacks; lo ocultamos visualmente
            user_selector = html.Div(
                style={"display": "none"},
                children=[dcc.Dropdown(id="ecg-user", options=options_users, value=default_user)]
            )
        else:
            user_selector = html.Div(className="filter-item", children=[
                html.Label("Deportista del equipo"),
                dcc.Dropdown(
                    id="ecg-user",
                    options=options_users,
                    placeholder="Selecciona deportista...",
                )
            ])

        # ── Textos que cambian por rol ──────────────────────────────────────
        if role == "deportista":
            page_title    = "Mis lecturas"
            page_sub      = "Revisa tu ECG, movimiento y esfuerzo de las últimas sesiones."
            flow_subtitle = "Selecciona tu sesión activa y revisa qué dicen las señales."
            flow_pills    = [
                html.Span("1. Tu sesión", className="pill"),
                html.Span("2. Contexto", className="pill"),
                html.Span("3. Lectura", className="pill"),
            ]
            before_analyze_sub = "Confirma qué lectura tienes disponible hoy antes de abrir los gráficos."
            focus_title = "Cómo entrar hoy"
            focus_sub = "Empieza por tu sesión activa y baja al detalle solo cuando ya tengas claro qué quieres revisar."
            focus_points = [
                "Confirma tu sesión del día antes de abrir las señales.",
                "Mira primero si tienes base de recuperación y movimiento cargada.",
                "Entra a ECG o IMU cuando quieras validar cómo respondió tu cuerpo.",
            ]
            session_config_sub = "Ábrelo solo si necesitas ajustar tipo de trabajo, objetivo o estructura antes de guardar la sesión."
            session_config_open = True
            ecg_entry_sub = "Aquí puedes cargar o revisar tu lectura cardiovascular del día para entender esfuerzo y recuperación."
            ecg_download_sub = "Descarga la señal completa o un informe breve si quieres guardar la lectura con una explicación clara."
            imu_entry_sub = "Revisa tu movimiento con una lectura más clara de ritmo, acciones y respuesta durante la sesión."
            imu_download_sub = "Guarda los datos o un informe breve cuando quieras compartir la lectura o dejar registro."
        else:
            page_title    = "Análisis del equipo"
            page_sub      = "Selecciona un deportista de tu plantilla y revisa sus lecturas de señales."
            flow_subtitle = "Elige al deportista, confirma la sesión del día y revisa qué dicen las señales."
            flow_pills    = [
                html.Span("1. Deportista", className="pill"),
                html.Span("2. Sesión", className="pill"),
                html.Span("3. Contexto", className="pill"),
                html.Span("4. Lectura", className="pill"),
            ]
            before_analyze_sub = "Elige al deportista y confirma qué lectura tienes disponible hoy antes de abrir los gráficos."
            focus_title = "Qué revisar primero"
            focus_sub = "La idea es entrar con contexto y no abrir toda la pantalla de golpe. Primero confirma a quién estás leyendo y luego decide dónde profundizar."
            focus_points = [
                "Selecciona al deportista y confirma la sesión que vas a revisar.",
                "Comprueba si ya tienes base útil de recuperación o movimiento cargada.",
                "Entra a ECG o IMU solo cuando ya sepas qué señal te ayudará a decidir mejor.",
            ]
            session_config_sub = "Ábrelo solo si necesitas ajustar el contexto de trabajo antes de guardar o continuar la lectura."
            session_config_open = False
            ecg_entry_sub = "Trabaja sobre la lectura cardiovascular del deportista seleccionado para revisar carga y recuperación con más contexto."
            ecg_download_sub = "Si necesitas respaldar la lectura, aquí puedes bajar los datos o un informe breve con la gráfica explicada."
            imu_entry_sub = "Esta vista te ayuda a revisar ritmo, acciones y respuesta del movimiento del deportista seleccionado."
            imu_download_sub = "Usa estas descargas cuando quieras compartir la lectura o guardar un respaldo útil para el seguimiento."

        # ✅ Sesión activa (más protagonista en el flujo)
        session_block = html.Div(
            className="card card--session",
            children=[
                html.H4("Flujo de sesión", className="card-title"),
                html.P(flow_subtitle, className="text-muted"),
                html.Div(className="session-pill-row", children=flow_pills),
                html.Label("Sesión del día"),
                dcc.Dropdown(
                    id="signals-session",
                    options=[],
                    placeholder="Selecciona una sesión abierta o crea una nueva...",
                    clearable=True,
                ),
                analysis_fold(
                    "Ajustar contexto de la sesión",
                    session_config_sub,
                    [
                        html.Div(className="filters-bar filters-bar--3", children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Tipo de sesión"),
                                dcc.Dropdown(
                                    id="session-type",
                                    options=[
                                        {"label": "Técnica", "value": "tecnica"},
                                        {"label": "Sparring / combate controlado", "value": "sparring"},
                                        {"label": "Acondicionamiento físico", "value": "acondicionamiento"},
                                        {"label": "Simulación competitiva", "value": "simulacion_competitiva"},
                                        {"label": "Evaluación / test", "value": "evaluacion"},
                                        {"label": "Recuperación / readaptación", "value": "recuperacion"},
                                    ],
                                    value="sparring",
                                    clearable=False,
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Objetivo principal"),
                                dcc.Dropdown(
                                    id="session-goal",
                                    options=[
                                        {"label": "Técnica", "value": "tecnica"},
                                        {"label": "Intensidad", "value": "intensidad"},
                                        {"label": "Volumen", "value": "volumen"},
                                        {"label": "Simulación", "value": "simulacion"},
                                        {"label": "Evaluación", "value": "evaluacion"},
                                        {"label": "Recuperación", "value": "recuperacion"},
                                    ],
                                    value="intensidad",
                                    clearable=False,
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Formato"),
                                dcc.Dropdown(
                                    id="session-structure",
                                    options=[
                                        {"label": "Por rounds", "value": "rounds"},
                                        {"label": "Por bloques", "value": "bloques"},
                                        {"label": "Libre", "value": "libre"},
                                    ],
                                    value="rounds",
                                    clearable=False,
                                ),
                            ]),
                        ]),
                        html.Div(className="filters-bar filters-bar--3", children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Rounds"),
                                dcc.Input(id="session-rounds", type="number", min=1, step=1, value=3, debounce=True),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Duración round (min)"),
                                dcc.Input(id="session-round-min", type="number", min=1, step=1, value=2, debounce=True),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Descanso (s)"),
                                dcc.Input(id="session-rest-sec", type="number", min=0, step=15, value=60, debounce=True),
                            ]),
                        ]),
                        html.Div(className="analysis-action-row", children=[
                            html.Button("Nueva sesión", id="btn-new-session", className="btn btn-primary"),
                            html.Button("Cerrar sesión", id="btn-close-session", className="btn btn-ghost"),
                        ]),
                    ],
                    open=session_config_open,
                ),
                html.Div(id="session-msg", className="text-danger form-msg"),
            ],
        )

        # ----- ECG -----
        ecg_block = html.Div(className="grid-2col", children=[
            html.Div(className="stack-8", children=[
                html.P(ecg_entry_sub, className="text-muted"),
                html.Label("Subir archivo ECG (.csv)"),
                dcc.Upload(
                    id="ecg-upload",
                    children=html.Div("Arrastra o elige un archivo"),
                    multiple=False,
                    className="upload-zone",
                ),
                html.Button("Cargar ECG de ejemplo", id="btn-ecg-demo", className="btn btn-ghost btn-demo"),
                html.Label("Ficheros ECG del usuario"),
                dcc.Dropdown(id="ecg-file", placeholder="No hay archivos aún..."),
                analysis_fold(
                    "Ajustes de visualización",
                    "Abre este bloque si quieres afinar la lectura o cambiar solo la parte visual de la gráfica.",
                    [
                        html.Div(className="filters-bar filters-bar--2", children=[
                            html.Div(className="filter-item", children=[
                                html.Label("Ventana (s)"),
                                dcc.Dropdown(
                                    id="ecg-winlen",
                                    options=[
                                        {"label": "5s", "value": 5},
                                        {"label": "10s", "value": 10},
                                        {"label": "20s", "value": 20},
                                        {"label": "30s", "value": 30},
                                        {"label": "60s", "value": 60},
                                        {"label": "120s", "value": 120},
                                        {"label": "Todo", "value": -1},
                                    ],
                                    value=10,
                                    clearable=False,
                                ),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Calidad de vista"),
                                dcc.Dropdown(
                                    id="ecg-quality",
                                    options=[
                                        {"label": "Alta", "value": "high"},
                                        {"label": "Media", "value": "med"},
                                        {"label": "Ligera", "value": "low"},
                                    ],
                                    value="med",
                                    clearable=False,
                                ),
                            ]),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Rango visible (s)"),
                            dcc.RangeSlider(
                                id="ecg-window",
                                min=0,
                                max=10,
                                step=0.05,
                                value=[0, 10],
                                marks={0: "0", 10: "10"},
                                tooltip={"placement": "bottom"},
                                updatemode="mouseup",
                                allowCross=False,
                            ),
                            html.Small(
                                "Este rango solo cambia lo que ves en pantalla; la lectura usa la señal completa.",
                                className="text-muted"
                            ),
                        ]),
                        dcc.Checklist(
                            options=[{"label": " Mostrar picos R", "value": "r"}],
                            value=[], id="ecg-showr"
                        ),
                        html.Label("Sensibilidad de detección"),
                        dcc.Slider(
                            id="ecg-sens",
                            min=0.3, max=0.95, step=0.05,
                            value=0.6, tooltip={"placement": "bottom"},
                            updatemode="mouseup"
                        ),
                        html.Label("Suavizado (ms)"),
                        dcc.Slider(
                            id="ecg-smooth",
                            min=20, max=120, step=5,
                            value=40, tooltip={"placement": "bottom"},
                            updatemode="mouseup"
                        ),
                    ],
                    open=False,
                ),
                analysis_fold(
                    "Descargas útiles",
                    ecg_download_sub,
                    [
                        html.Div(className="export-actions", children=[
                            html.Button("Descargar PNG", id="btn-dl-png", className="btn btn-primary"),
                            html.Button("Descargar datos (CSV)", id="btn-dl-peaks", className="btn btn-ghost"),
                            html.Button("Descargar informe (PDF)", id="btn-dl-ecg-report", className="btn btn-ghost"),
                        ]),
                        html.Small(
                            "Si la imagen no sale, revisa que kaleido esté disponible en tu entorno.",
                            className="text-muted"
                        ),
                        html.Div(id="ecg-export-msg", className="export-status"),
                    ],
                    open=False,
                ),
                html.Div(id="ecg-msg", className="text-danger form-msg")
            ]),
            html.Div(children=[
                html.Div(id="ecg-kpis", className="kpis"),
                html.Div(className="ecg-divider"),
                dcc.Graph(id="ecg-graph", figure=go.Figure(), config=graph_config(), className="signal-graph")
            ])
        ])

        # ----- IMU -----
        imu_block = html.Div(
            className="grid-2col grid-2col--signal",
            children=[
                html.Div(className="stack-8", children=[
                    html.Label("Lectura IMU del deporte"),
                    html.Div(id="imu-sport-banner", className="muted sport-banner", children=imu_profile["headline"]),
                    html.P(imu_entry_sub, className="text-muted"),
                    dcc.Tabs(
                        id="imu-tabs",
                        value=(imu_profile["tabs"][0]["value"] if imu_profile["tabs"] else "imu-arm"),
                        children=[dcc.Tab(label=tab["label"], value=tab["value"]) for tab in imu_profile["tabs"]],
                        className="signal-tabs",
                    ),

                    html.Label("Archivo IMU (.csv)"),
                    dcc.Upload(
                        id="imu-upload",
                        children=html.Div("Arrastra o elige un archivo de IMU"),
                        multiple=False,
                        className="upload-zone",
                    ),
                    html.Div(className="analysis-action-row", children=[
                        html.Button("Analizar archivo", id="btn-imu-analyze", className="btn btn-primary"),
                        html.Button("Cargar IMU de ejemplo", id="btn-imu-demo", className="btn btn-ghost"),
                    ]),
                    html.Div(id="imu-msg", className="text-danger form-msg"),
                    analysis_fold(
                        "Ajustes de visualización",
                        "Úsalo solo si quieres cambiar la ventana visible o afinar cómo se muestra la lectura.",
                        [
                            html.Div(className="filters-bar filters-bar--2", children=[
                                html.Div(className="filter-item", children=[
                                    html.Label("Ventana (s)"),
                                    dcc.Dropdown(
                                        id="imu-winlen",
                                        options=[
                                            {"label": "5s", "value": 5},
                                            {"label": "10s", "value": 10},
                                            {"label": "20s", "value": 20},
                                            {"label": "30s", "value": 30},
                                            {"label": "60s", "value": 60},
                                            {"label": "120s", "value": 120},
                                            {"label": "Todo", "value": -1},
                                        ],
                                        value=10,
                                        clearable=False,
                                    ),
                                ]),
                                html.Div(className="filter-item", children=[
                                    html.Label("Calidad de vista"),
                                    dcc.Dropdown(
                                        id="imu-quality",
                                        options=[
                                            {"label": "Alta", "value": "high"},
                                            {"label": "Media", "value": "med"},
                                            {"label": "Ligera", "value": "low"},
                                        ],
                                        value="med",
                                        clearable=False,
                                    ),
                                ]),
                            ]),
                            html.Div(className="filter-item", children=[
                                html.Label("Rango visible (s)"),
                                dcc.RangeSlider(
                                    id="imu-window",
                                    min=0,
                                    max=10,
                                    step=0.05,
                                    value=[0, 10],
                                    marks={0: "0", 10: "10"},
                                    tooltip={"placement": "bottom"},
                                    updatemode="mouseup",
                                    allowCross=False,
                                ),
                                html.Small(
                                    "Este rango solo cambia lo que ves en pantalla; la lectura usa la señal completa.",
                                    className="text-muted"
                                ),
                            ]),
                            html.P(
                                id="imu-format-help",
                                children=imu_profile["format_help"],
                                className="muted text-hint",
                            ),
                        ],
                        open=False,
                    ),
                    analysis_fold(
                        "Descargas útiles",
                        imu_download_sub,
                        [
                            html.Div(className="export-actions", children=[
                                html.Button("Descargar datos (CSV)", id="btn-dl-imu-data", className="btn btn-primary"),
                                html.Button("Descargar informe (PDF)", id="btn-dl-imu-report", className="btn btn-ghost"),
                            ]),
                            html.Div(id="imu-export-msg", className="export-status"),
                        ],
                        open=False,
                    ),
                ]),
                html.Div(children=[
                    html.Div(id="imu-kpis", className="kpis"),
                    html.Div(className="ecg-divider"),
                    dcc.Graph(id="imu-graph", figure=go.Figure(), config=graph_config(), className="signal-graph"),
                ]),
            ],
        )

        # ----- EMG -----
        emg_block = html.Div(
            className="grid-2col grid-2col--signal",
            children=[
                html.Div(children=[
                    html.Label("Canal EMG"),
                    dcc.Tabs(
                        id="emg-tabs",
                        value="emg-arm",
                        children=[
                            dcc.Tab(label="EMG brazo", value="emg-arm"),
                            dcc.Tab(label="EMG pierna", value="emg-leg"),
                        ],
                        className="signal-tabs",
                    ),

                    html.Label("Archivo EMG (.csv)"),
                    dcc.Upload(
                        id="emg-upload",
                        children=html.Div("Arrastra o elige un archivo de EMG"),
                        multiple=False,
                        className="upload-zone",
                    ),
                    html.Br(),
                    html.Label("Ventana RMS (ms)"),
                    dcc.Slider(
                        id="emg-win",
                        min=20,
                        max=250,
                        step=10,
                        value=100,
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup"
                    ),
                    html.Br(),
                    html.Button("Analizar EMG", id="btn-emg-analyze", className="btn btn-primary"),
                    html.Div(id="emg-msg", className="text-danger form-msg"),
                    html.Div(className="filters-bar filters-bar--2", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Ventana (s)"),
                            dcc.Dropdown(
                                id="emg-winlen",
                                options=[
                                    {"label": "5s", "value": 5},
                                    {"label": "10s", "value": 10},
                                    {"label": "20s", "value": 20},
                                    {"label": "30s", "value": 30},
                                    {"label": "60s", "value": 60},
                                    {"label": "120s", "value": 120},
                                    {"label": "Todo", "value": -1},
                                ],
                                value=10,
                                clearable=False,
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Calidad render"),
                            dcc.Dropdown(
                                id="emg-quality",
                                options=[
                                    {"label": "Alta", "value": "high"},
                                    {"label": "Media", "value": "med"},
                                    {"label": "Ligera", "value": "low"},
                                ],
                                value="med",
                                clearable=False,
                            ),
                        ]),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Rango visible (s)"),
                        dcc.RangeSlider(
                            id="emg-window",
                            min=0,
                            max=10,
                            step=0.05,
                            value=[0, 10],
                            marks={0: "0", 10: "10"},
                            tooltip={"placement": "bottom"},
                            updatemode="mouseup",
                            allowCross=False,
                        ),
                        html.Small(
                            "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                            className="text-muted"
                        ),
                    ]),
                    html.Br(),
                    html.P(
                        [
                            "Formato recomendado: ",
                            html.Code("time,emg"),
                            " o ",
                            html.Code("time,ch1"),
                            ". La lógica es la misma para brazo y pierna, pero ",
                            "la interpretación cambia (brazo: golpes / guardia, pierna: patadas / desplazamientos).",
                        ],
                        className="muted text-hint",
                    ),
                ]),
                html.Div(children=[
                    html.Div(id="emg-kpis", className="kpis"),
                    html.Div(className="ecg-divider"),
                    dcc.Graph(id="emg-graph", figure=go.Figure(), config=graph_config(), className="signal-graph"),
                ]),
            ],
        )

        # ----- RESP -----
        resp_block = html.Div(
            className="grid-2col grid-2col--signal",
            children=[
            html.Div(children=[
                html.Label("Archivo respiración (.csv)"),
                dcc.Upload(
                    id="resp-upload",
                    children=html.Div("Arrastra o elige un archivo de banda respiratoria"),
                    multiple=False,
                    className="upload-zone",
                ),
                html.Br(),
                html.Div(className="filters-bar filters-bar--3", children=[
                    html.Div(className="filter-item", children=[
                        html.Label("Sensibilidad detección"),
                        dcc.Slider(
                            id="resp-sens",
                            min=0.3, max=0.95, step=0.05,
                            value=0.6,
                            marks={0.3: "0.30", 0.5: "0.50", 0.6: "0.60", 0.7: "0.70", 0.95: "0.95"},
                            tooltip={"placement": "bottom", "always_visible": True},
                            updatemode="mouseup"
                        ),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Ventana (s)"),
                        dcc.Dropdown(
                            id="resp-winlen",
                            options=[
                                {"label": "5s", "value": 5},
                                {"label": "10s", "value": 10},
                                {"label": "20s", "value": 20},
                                {"label": "30s", "value": 30},
                                {"label": "60s", "value": 60},
                                {"label": "120s", "value": 120},
                                {"label": "Todo", "value": -1},
                            ],
                            value=30,
                            clearable=False,
                        ),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Calidad render"),
                        dcc.Dropdown(
                            id="resp-quality",
                            options=[
                                {"label": "Alta", "value": "high"},
                                {"label": "Media", "value": "med"},
                                {"label": "Ligera", "value": "low"},
                            ],
                            value="med",
                            clearable=False,
                        ),
                    ]),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("Rango visible (s)"),
                    dcc.RangeSlider(
                        id="resp-window",
                        min=0,
                        max=10,
                        step=0.05,
                        value=[0, 10],
                        marks={0: "0", 10: "10"},
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup",
                        allowCross=False,
                    ),
                    html.Small(
                        "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                        className="text-muted"
                    ),
                ]),

                html.Button("Analizar respiración", id="btn-resp-analyze", className="btn btn-primary"),
                html.Div(id="resp-msg", className="text-danger form-msg"),
                html.Br(),
                html.P(
                    "Formato recomendado: 'time,resp' con la banda torácica en unidades arbitrarias.",
                    className="muted text-hint",
                ),
            ]),
            html.Div(children=[
                html.Div(id="resp-kpis", className="kpis"),
                html.Div(className="ecg-divider"),
                dcc.Graph(id="resp-graph", figure=go.Figure(), config=graph_config(), className="signal-graph")
            ])
        ])

        # ✅ wrappers para bloquear interacción SIN romper callbacks
        def _wrap(lock_msg_id: str, lock_wrap_id: str, inner):
            return html.Div([
                html.Div(id=lock_msg_id, className="text-danger form-msg--below"),
                html.Div(id=lock_wrap_id, children=[inner]),
            ])

        return html.Div(className="analysis-shell", children=[
            # ✅ descargas (no visibles)
            dcc.Download(id="dl-png"),
            dcc.Download(id="dl-peaks"),
            dcc.Download(id="dl-ecg-report"),
            dcc.Download(id="dl-imu-data"),
            dcc.Download(id="dl-imu-report"),
            dcc.Store(id="dl-png-clicks", data=0),
            dcc.Store(id="imu-meta", data=None),
            dcc.Store(id="emg-meta", data=None),
            dcc.Store(id="resp-meta", data=None),

            html.Div(className="profile-hero-grid", children=[
                html.Div(className="page-head", children=[
                    html.H2(page_title),
                    html.P(page_sub, className="text-muted"),
                ]),
                html.Div(className="card analysis-surface-card profile-focus-card analysis-focus-card", children=[
                    html.Div("Entrada clara", className="analysis-mini-label"),
                    html.H4(focus_title, className="card-title"),
                    html.P(focus_sub, className="text-muted"),
                    html.Ul(
                        className="analysis-focus-list",
                        children=[html.Li(point) for point in focus_points],
                    ),
                ]),
            ]),

            html.Div(className="ecg-divider ecg-divider--spaced"),
            dcc.Tabs(
                id="analysis-main-tabs",
                value="analysis-flow",
                className="combatiq-tabs",
                children=[
                    dcc.Tab(
                        label="1. Flujo de la sesión",
                        value="analysis-flow",
                        className="combatiq-tab",
                        selected_className="combatiq-tab--active",
                    ),
                    dcc.Tab(
                        label="2. Recuperación cardiovascular",
                        value="analysis-ecg",
                        className="combatiq-tab",
                        selected_className="combatiq-tab--active",
                    ),
                    dcc.Tab(
                        label="3. Movimiento y ritmo",
                        value="analysis-imu",
                        className="combatiq-tab",
                        selected_className="combatiq-tab--active",
                    ),
                ],
            ),

            html.Div(id="analysis-view-flow", className="analysis-view-stack", children=[
                html.Div(className="analysis-top-grid", children=[
                    html.Div(className="card analysis-summary-card analysis-surface-card", children=[
                        html.H4("Antes de analizar", className="card-title"),
                        html.P(before_analyze_sub, className="text-muted"),
                        user_selector,
                        html.Div(className="analysis-sensor-box", children=[
                            html.Div("Sensores del día", className="analysis-mini-label"),
                            html.Div(sensors_text, className="text-muted"),
                            html.Div(id="signals-sensors-banner", className="text-muted banner-row"),
                        ]),
                    ]),
                    html.Div(className="analysis-surface-wrap", children=[session_block]),
                ]),
            ]),

            html.Div(id="analysis-view-ecg", className="analysis-view-stack", style={"display": "none"}, children=[
                analysis_section(
                    "Recuperación cardiovascular",
                    "Te ayuda a entender cómo llegó el cuerpo a la sesión y cómo respondió a la carga del día.",
                    _wrap("ecg-lock-msg", "ecg-lock-wrapper", ecg_block),
                    class_name="analysis-surface-card",
                ),
            ]),

            html.Div(id="analysis-view-imu", className="analysis-view-stack", style={"display": "none"}, children=[
                analysis_section(
                    "Movimiento y ritmo por deporte",
                    "La lectura del movimiento se adapta al deporte para que el análisis tenga sentido real en taekwondo o boxeo.",
                    _wrap("imu-lock-msg", "imu-lock-wrapper", imu_block),
                    class_name="analysis-surface-card",
                ),
            ]),


            html.Div(style={"display": "none"}, children=[
                analysis_section(
                    "EMG (brazo / pierna)",
                    "Activación muscular y señales de fatiga en esfuerzos repetidos.",
                    _wrap("emg-lock-msg", "emg-lock-wrapper", emg_block),
                ),

                analysis_section(
                    "Respiración (banda torácica)",
                    "Control del ritmo respiratorio y patrón de recuperación durante la sesión.",
                    _wrap("resp-lock-msg", "resp-lock-wrapper", resp_block),
                ),
            ]),
        ])

    # ---------- Callbacks ----------

    def _register_callbacks(self):
        app = self.app
        db = self.db

        def _safe_int(x):
            try:
                return int(x)
            except Exception:
                return None

        @app.callback(
            Output("analysis-view-flow", "style"),
            Output("analysis-view-ecg", "style"),
            Output("analysis-view-imu", "style"),
            Input("analysis-main-tabs", "value"),
            prevent_initial_call=False,
        )
        def switch_analysis_view(tab_value):
            base = {"display": "none"}
            if tab_value == "analysis-ecg":
                return base, {}, base
            if tab_value == "analysis-imu":
                return base, base, {}
            return {}, base, base

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
            stype = payload.get("session_type") or "sesión"
            goal = payload.get("session_goal") or ""
            structure = payload.get("session_structure") or ""
            rounds_count = payload.get("rounds_count")
            parts = [stype.replace("_", " ")]
            if goal:
                parts.append(f"objetivo {str(goal).replace('_', ' ')}")
            if structure == "rounds" and rounds_count:
                parts.append(f"{rounds_count} rounds")
            elif structure:
                parts.append(str(structure).replace("_", " "))
            return " · ".join(parts)

        def _list_session_options(athlete_id: int):
            try:
                sessions = db.list_sessions(int(athlete_id), limit=50) or []
            except Exception:
                return []
            opts = []
            for s in sessions:
                sid = s.get("id")
                ts = (s.get("ts_start") or "")[:19].replace("T", " ")
                st = (s.get("status") or "—")
                ctx = _session_context_text(s)
                label = f"#{sid} · {ts} · {st}"
                if ctx:
                    label += f" · {ctx}"
                opts.append({"label": label, "value": sid})
            return opts

        # ✅ (PASO 3) GATING POR SENSORES (sin romper callbacks)
        @app.callback(
            Output("signals-sensors-banner", "children"),
            Output("ecg-lock-msg", "children"),
            Output("ecg-lock-wrapper", "style"),
            Output("imu-lock-msg", "children"),
            Output("imu-lock-wrapper", "style"),
            Output("emg-lock-msg", "children"),
            Output("emg-lock-wrapper", "style"),
            Output("resp-lock-msg", "children"),
            Output("resp-lock-wrapper", "style"),
            Input("ecg-user", "value"),
        )
        def gate_sections(user_id):
            if not user_id:
                return "", "", {}, "", {}, "", {}, "", {}

            uid = _safe_int(user_id)
            if not uid:
                return "", "", {}, "", {}, "", {}, "", {}

            ecg_ok = _has_sensor(uid, "ECG")
            imu_ok = _has_sensor(uid, "IMU")
            emg_ok = _has_sensor(uid, "EMG")
            resp_ok = _has_sensor(uid, "RESP_BELT")

            enabled = []
            missing = []
            for key, ok in [("ECG", ecg_ok), ("IMU", imu_ok), ("EMG", emg_ok), ("RESP_BELT", resp_ok)]:
                (enabled if ok else missing).append(key)

            core_enabled = [k for k in enabled if k in ("ECG", "IMU")]
            core_missing = [k for k in missing if k in ("ECG", "IMU")]

            banner = f"Lectura disponible hoy: {', '.join(core_enabled) if core_enabled else '—'}"
            if core_missing:
                banner += f" · Falta activar: {', '.join(core_missing)}"
            banner += " · EMG y respiración quedan fuera del frente principal por ahora."

            ecg_msg = "" if ecg_ok else "Activa ECG en Sensores para ver la lectura cardiovascular del día."
            imu_msg = "" if imu_ok else "Activa IMU en Sensores para revisar ritmo, impacto y explosividad."
            emg_msg = "" if emg_ok else "🔒 EMG no asignado para este deportista."
            resp_msg = "" if resp_ok else "🔒 Respiración no asignada para este deportista."

            return (
                banner,
                ecg_msg, _lock_style(ecg_ok),
                imu_msg, _lock_style(imu_ok),
                emg_msg, _lock_style(emg_ok),
                resp_msg, _lock_style(resp_ok),
            )

        @app.callback(
            Output("imu-tabs", "children"),
            Output("imu-tabs", "value"),
            Output("imu-sport-banner", "children"),
            Output("imu-format-help", "children"),
            Input("ecg-user", "value"),
            State("imu-tabs", "value"),
        )
        def adapt_imu_by_sport(user_id, current_tab):
            uid = _safe_int(user_id)
            sport = None
            if uid:
                try:
                    sport = (db.get_user_by_id(int(uid)) or {}).get("sport")
                except Exception:
                    sport = None
            profile = self._imu_profile(sport)
            tabs = [dcc.Tab(label=tab["label"], value=tab["value"]) for tab in profile["tabs"]]
            valid_values = {tab["value"] for tab in profile["tabs"]}
            value = current_tab if current_tab in valid_values else (profile["tabs"][0]["value"] if profile["tabs"] else "imu-arm")
            return tabs, value, f"{profile['headline']} · {profile['subline']}", profile["format_help"]

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
        )
        def session_ui(user_id, n_new, n_close, current_session_id, session_type, session_goal, session_structure, rounds_count, round_min, rest_sec):
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

            # Cerrar sesión
            if trig.startswith("btn-close-session") and n_close:
                sid = _safe_int(current_session_id)
                if not sid:
                    opts = _list_session_options(uid)
                    return opts, None, "Selecciona una sesión para cerrarla."
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

            # Cambio de usuario (o carga): listar sesiones y autoseleccionar open si existe
            opts = _list_session_options(uid)
            chosen = None
            try:
                sessions = db.list_sessions(int(uid), limit=50) or []
                open_s = next((s for s in sessions if (s.get("status") == "open")), None)
                if open_s:
                    chosen = open_s.get("id")
            except Exception:
                chosen = None

            return opts, chosen, ""

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
            if not _has_sensor(uid, "ECG"):
                return []
            return _list_ecg_options(uid)

        @app.callback(
            Output("ecg-file", "options", allow_duplicate=True),
            Output("ecg-file", "value", allow_duplicate=True),
            Output("ecg-msg", "children", allow_duplicate=True),
            Input("btn-ecg-demo", "n_clicks"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            prevent_initial_call=True
        )
        def load_demo(n, user_id, session_id):
            if not user_id:
                return dash.no_update, dash.no_update, "Selecciona usuario."
            uid = _safe_int(user_id)
            if not uid:
                return dash.no_update, dash.no_update, "Usuario inválido."
            if not _has_sensor(uid, "ECG"):
                return dash.no_update, dash.no_update, "Este deportista no tiene ECG asignado."

            os.makedirs(os.path.join("data", "ecg"), exist_ok=True)
            demo_path = os.path.join("data", "ecg", "ecg_example.csv")
            if not os.path.exists(demo_path):
                return dash.no_update, dash.no_update, "No encuentro data/ecg/ecg_example.csv"

            sid = _safe_int(session_id)

            # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
            if not sid and hasattr(db, "ensure_open_session"):
                try:
                    actor_id = _safe_int(session.get("user_id"))
                    athlete = db.get_user_by_id(int(uid))
                    sport = athlete.get("sport") if athlete else None
                    sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                except Exception:
                    sid = None

            try:
                ecg_id = db.add_ecg_file(uid, "ecg_example.csv", 250, session_id=sid)
            except TypeError:
                ecg_id = db.add_ecg_file(uid, "ecg_example.csv", 250)

            opts = _list_ecg_options(uid)
            return opts, ecg_id, "ECG de ejemplo asociado."

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

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            if not os.path.exists(path):
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
            prevent_initial_call=True
        )
        def render_ecg(ecg_id, showr_list, sens, smooth_ms, win_range, quality, user_id):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            if not os.path.exists(path):
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            if x is None or len(x) == 0:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            try:
                xs = smooth(x, int(smooth_ms or 0), fs) if smooth_ms and smooth_ms > 0 else x
            except Exception:
                xs = x

            show_r = "r" in (showr_list or [])
            try:
                peaks = detect_r_peaks(xs, fs, sens or 0.6)
            except Exception:
                peaks = None

            bpm, sdnn, rmssd = ecg_metrics_from_peaks(
                peaks if peaks is not None else np.array([]),
                fs
            )

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

            # Ventana visible (solo visualización)
            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, float(t[-1] - t[0])
            except Exception:
                t0, t1 = 0.0, float(t[-1] - t[0])

            t0 = max(0.0, t0)
            t1 = max(t0 + 1e-6, t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
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

            fig = fig_ecg(t_line, x_line, peaks_t=peaks_t, peaks_y=peaks_y, title=row["filename"])
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
                return dcc.send_string("Instala 'kaleido' para exportar PNG", "README.txt"), n

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

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))

            try:
                xs = smooth(x, int(smooth_ms or 0), fs) if smooth_ms and smooth_ms > 0 else x
            except Exception:
                xs = x

            peaks = detect_r_peaks(xs, fs, sens or 0.6)
            peak_set = {int(idx) for idx in peaks.tolist()} if peaks is not None and len(peaks) > 0 else set()

            sio = io.StringIO()
            w = csv.writer(sio)
            w.writerow(["time_s", "ecg_raw", "ecg_suavizado", "r_peak"])
            for idx in range(len(t)):
                w.writerow([
                    f"{t[idx]:.6f}",
                    f"{x[idx]:.6f}",
                    f"{xs[idx]:.6f}",
                    1 if idx in peak_set else 0,
                ])
            csv_str = sio.getvalue()
            base = os.path.splitext(os.path.basename(row["filename"]))[0]
            filename = f"CombatIQ_ECG_{base}_datos.csv"
            return dcc.send_bytes(lambda b: b.write(csv_str.encode("utf-8-sig")), filename)

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

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                return dash.no_update, "No encuentro el archivo ECG seleccionado."

            path = os.path.join("data", "ecg", row["filename"])
            if not os.path.exists(path):
                return dash.no_update, "No encuentro el archivo ECG en disco."

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception as e:
                return dash.no_update, f"No pude leer el archivo ECG: {e}"

            try:
                xs = smooth(x, int(smooth_ms or 0), fs) if smooth_ms and smooth_ms > 0 else x
            except Exception:
                xs = x

            try:
                peaks = detect_r_peaks(xs, fs, sens or 0.6)
            except Exception:
                peaks = np.array([], dtype=int)

            bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
            n_peaks = int(len(peaks)) if peaks is not None else 0

            athlete = db.get_user_by_id(int(uid)) or {}
            athlete_name = athlete.get("name", "Deportista")
            sport = athlete.get("sport", "-")

            sid = _safe_int(session_id) or _safe_int(row.get("session_id"))
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
                    "No hubo latidos utiles suficientes para una lectura completa. "
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

            pdf_bytes = _build_signal_report_pdf(
                report_title="Informe de recuperación cardiovascular",
                report_subtitle="Lectura breve del archivo ECG con explicación de la gráfica y de los indicadores clave.",
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
            )

            base = os.path.splitext(os.path.basename(row["filename"]))[0]
            filename = f"CombatIQ_ECG_{base}_informe.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

        # --- IMU ---
        @app.callback(
            Output("imu-graph", "figure"),
            Output("imu-kpis", "children"),
            Output("imu-msg", "children"),
            Output("imu-meta", "data"),
            Output("imu-window", "max"),
            Output("imu-window", "value"),
            Output("imu-window", "marks"),
            Input("btn-imu-analyze", "n_clicks"),
            Input("btn-imu-demo", "n_clicks"),
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
        def imu_pro(n_clicks, n_demo, win_range, quality, winlen, content, filename, imu_kind, user_id, session_id, meta):
            # trigger detect
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "IMU"):
                return go.Figure(), [], "Este deportista no tiene IMU asignado.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            athlete_sport = None
            try:
                athlete_sport = (db.get_user_by_id(int(uid)) or {}).get("sport")
            except Exception:
                athlete_sport = None
            imu_profile = self._imu_profile(athlete_sport)

            # 1) Si se presiona analizar o demo: generamos meta + slider
            if trig.startswith("btn-imu-analyze") or trig.startswith("btn-imu-demo"):
                prefix = {"imu-arm": "arm_", "imu-leg": "leg_", "imu-head": "head_"}.get(imu_kind or "imu-arm", "arm_")

                if trig.startswith("btn-imu-analyze"):
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
                else:
                    if not n_demo:
                        raise PreventUpdate
                    sample_name = f"{prefix}imu_example.csv"
                    sample_path = os.path.join("data", "imu", sample_name)
                    fallback_path = os.path.join("data", "imu", "imu_example.csv")
                    if os.path.exists(sample_path):
                        save_path = sample_path
                    elif os.path.exists(fallback_path):
                        save_path = fallback_path
                    else:
                        return go.Figure(), [], "No encuentro el IMU de ejemplo en data/imu.", dash.no_update, dash.no_update, dash.no_update, dash.no_update
                    final_name = os.path.basename(save_path)
                    shown_name = "IMU de ejemplo"

                t, mag, fs = read_imu_csv(save_path)
                if len(mag) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

                sid = _safe_int(session_id)
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

            save_path = meta.get("path")
            title = meta.get("title") or "IMU"
            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo IMU (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # Re-leer y recalcular (mismo algoritmo; NO guardamos a DB aquí)
            t, mag, fs = read_imu_csv(save_path)
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
            prevent_initial_call=True,
        )
        def download_imu_data(n, meta):
            if not n:
                raise PreventUpdate
            if not meta or not meta.get("path"):
                raise PreventUpdate

            path = meta.get("path")
            if not path or not os.path.exists(path):
                raise PreventUpdate

            t, mag, fs = read_imu_csv(path)
            n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)
            peak_set = {int(idx) for idx in peaks.tolist()} if peaks is not None and len(peaks) > 0 else set()

            sio = io.StringIO()
            w = csv.writer(sio)
            w.writerow(["time_s", "magnitud_ms2", "magnitud_g", "accion_detectada"])
            for idx in range(len(t)):
                w.writerow([
                    f"{t[idx]:.6f}",
                    f"{mag[idx]:.6f}",
                    f"{(mag[idx] / 9.81):.6f}",
                    1 if idx in peak_set else 0,
                ])
            csv_bytes = sio.getvalue().encode("utf-8-sig")

            base = os.path.splitext(os.path.basename(meta.get("filename") or os.path.basename(path)))[0]
            filename = f"CombatIQ_IMU_{base}_datos.csv"
            return dcc.send_bytes(lambda b: b.write(csv_bytes), filename)

        @app.callback(
            Output("dl-imu-report", "data"),
            Output("imu-export-msg", "children"),
            Input("btn-dl-imu-report", "n_clicks"),
            State("imu-meta", "data"),
            State("imu-graph", "figure"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            prevent_initial_call=True,
        )
        def download_imu_report(n, meta, fig_dict, user_id, session_id):
            if not n:
                raise PreventUpdate
            if not meta or not meta.get("path"):
                return dash.no_update, "Carga o analiza un archivo IMU antes de exportar el informe."

            if not _REPORTLAB_OK:
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Activa reportlab para exportar el informe en PDF."

            path = meta.get("path")
            if not path or not os.path.exists(path):
                return dash.no_update, "No encuentro el archivo IMU que quieres exportar."

            try:
                t, mag, fs = read_imu_csv(path)
            except Exception as e:
                return dash.no_update, f"No pude leer el archivo IMU: {e}"

            n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

            uid = _safe_int(user_id) or _safe_int(meta.get("uid"))
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

            sid = _safe_int(session_id) or _safe_int(meta.get("session_id"))
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
                f"Acciones detectadas: {n_hits}. Son picos de movimiento relevantes dentro del archivo, no técnicas confirmadas.",
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
            else:
                peaks_t = t[peaks] if peaks is not None and len(peaks) > 0 else None
                peaks_y = mag[peaks] if peaks is not None and len(peaks) > 0 else None
                thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
                fig = fig_imu(t, mag, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=meta.get("title") or "IMU")
            _apply_signal_pdf_chart_style(
                fig,
                "Movimiento y ritmo",
                "Tiempo (s)",
                "Magnitud (m/s2)",
            )

            source_label = meta.get("filename") or os.path.basename(path)
            pdf_bytes = _build_signal_report_pdf(
                report_title="Informe de movimiento y ritmo",
                report_subtitle="Lectura breve del archivo IMU con explicación de la gráfica y de los indicadores clave.",
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
            )

            base = os.path.splitext(os.path.basename(source_label))[0]
            filename = f"CombatIQ_IMU_{base}_informe.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

# --- EMG ---
        @app.callback(
            Output("emg-graph", "figure"),
            Output("emg-kpis", "children"),
            Output("emg-msg", "children"),
            Output("emg-meta", "data"),
            Output("emg-window", "max"),
            Output("emg-window", "value"),
            Output("emg-window", "marks"),
            Input("btn-emg-analyze", "n_clicks"),
            Input("emg-window", "value"),
            Input("emg-quality", "value"),
            Input("emg-winlen", "value"),
            Input("emg-win", "value"),
            State("emg-upload", "contents"),
            State("emg-upload", "filename"),
            State("emg-tabs", "value"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("emg-meta", "data"),
            prevent_initial_call=True,
        )
        def emg_pro(n_clicks, win_range, quality, winlen, win_ms, content, filename, emg_kind, user_id, session_id, meta):
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "EMG"):
                return go.Figure(), [], "Este deportista no tiene EMG asignado.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # 1) Analizar: guardar, calcular métricas, preparar slider, guardar en DB
            if trig.startswith("btn-emg-analyze"):
                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de EMG.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "emg"), exist_ok=True)

                base_name = filename or "emg.csv"
                prefix = {"emg-arm": "arm_", "emg-leg": "leg_"}.get(emg_kind or "emg-arm", "arm_")

                try:
                    final_name = _save_unique(os.path.join("data", "emg"), prefix + base_name, data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "emg", final_name)

                t, x, fs = read_emg_csv(save_path)
                if len(x) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                # Métricas globales (no cambian con la ventana)
                rms_global, peak, fatigue = emg_metrics(x, fs)

                sid = _safe_int(session_id)
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                try:
                    db.save_emg_metrics(uid, final_name, rms_global, peak, fatigue, session_id=sid)
                except TypeError:
                    try:
                        db.save_emg_metrics(uid, final_name, rms_global, peak, fatigue)
                    except Exception:
                        pass
                except Exception:
                    pass

                shown_name = filename or final_name
                if emg_kind == "emg-leg":
                    title = f"EMG pierna · {shown_name}"
                    extra_msg = "Interpretación: activación y fatiga de pierna (patadas, desplazamientos)."
                else:
                    title = f"EMG brazo · {shown_name}"
                    extra_msg = "Interpretación: activación y fatiga de brazo (golpes, guardia)."

                # Meta para re-render (sin guardar otra vez)
                meta = {
                    "path": save_path,
                    "title": title,
                    "uid": int(uid),
                    "kind": (emg_kind or "emg-arm"),
                    "fs": int(fs),
                    "rms": float(rms_global),
                    "peak": float(peak),
                    "fatigue": float(fatigue),
                }

                # Slider setup
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Render window (solo visual)
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                x_win = x[i0:i1]

                rect = np.abs(x_win - np.mean(x_win))
                env = smooth(rect, win_ms=int(win_ms or 100), fs=fs)

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                env_line = env[::step]

                thr = float(np.quantile(env, 0.90)) if len(env) > 0 else None
                fig = fig_emg(t_line, env_line, fs, thr=thr, title=title)
                kpis = kpi_grid_emg(rms_global, peak, fatigue)
                msg = (f"Archivo {shown_name} analizado. RMS: {rms_global:.3f}, "
                       f"pico: {peak:.3f}, fatiga: {fatigue:.1f}%. {extra_msg}")

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Re-render: ventana / calidad / win_ms / winlen (sin guardar)
            if not meta or not meta.get("path"):
                raise PreventUpdate

            save_path = meta.get("path")
            title = meta.get("title") or "EMG"
            fs = int(meta.get("fs") or 1000)

            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo EMG (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            t, x, _fs2 = read_emg_csv(save_path)
            if len(x) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # KPIs desde meta (no recalculamos pesado en sliders)
            rms_global = float(meta.get("rms") or 0.0)
            peak = float(meta.get("peak") or 0.0)
            fatigue = float(meta.get("fatigue") or 0.0)

            # Si cambia winlen, resetea ventana al final
            if trig.startswith("emg-winlen"):
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
            x_win = x[i0:i1]

            rect = np.abs(x_win - np.mean(x_win))
            env = smooth(rect, win_ms=int(win_ms or 100), fs=fs)

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            env_line = env[::step]

            thr = float(np.quantile(env, 0.90)) if len(env) > 0 else None
            fig = fig_emg(t_line, env_line, fs, thr=thr, title=title)
            kpis = kpi_grid_emg(rms_global, peak, fatigue)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks

# --- RESP ---
        @app.callback(
            Output("resp-graph", "figure"),
            Output("resp-kpis", "children"),
            Output("resp-msg", "children"),
            Output("resp-meta", "data"),
            Output("resp-window", "max"),
            Output("resp-window", "value"),
            Output("resp-window", "marks"),
            Input("btn-resp-analyze", "n_clicks"),
            Input("resp-window", "value"),
            Input("resp-quality", "value"),
            Input("resp-winlen", "value"),
            Input("resp-sens", "value"),
            State("resp-upload", "contents"),
            State("resp-upload", "filename"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("resp-meta", "data"),
            prevent_initial_call=True
        )
        def resp_pro(n_clicks, win_range, quality, winlen, sens, content, filename, user_id, session_id, meta):
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "RESP_BELT"):
                return go.Figure(), [], "Este deportista no tiene Respiración asignada.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # 1) Analizar (guardar + DB)
            if trig.startswith("btn-resp-analyze"):
                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de respiración.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "resp"), exist_ok=True)

                try:
                    final_name = _save_unique(os.path.join("data", "resp"), filename or "resp.csv", data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "resp", final_name)

                t, x, fs = read_resp_csv(save_path)
                if len(x) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                n_breaths, br_min, mean_period, peaks = resp_metrics(t, x, fs, sens=sens or 0.6)

                sid = _safe_int(session_id)
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                try:
                    db.save_resp_metrics(uid, final_name, n_breaths, br_min, mean_period, session_id=sid)
                except TypeError:
                    try:
                        db.save_resp_metrics(uid, final_name, n_breaths, br_min, mean_period)
                    except Exception:
                        pass
                except Exception:
                    pass

                shown_name = filename or final_name
                title = f"Respiración · {shown_name}"

                # Preprocesado para render (filtrado/centrado)
                x0 = x - np.mean(x)
                env_full = smooth(x0, win_ms=250, fs=fs)

                meta = {
                    "path": save_path,
                    "title": title,
                    "uid": int(uid),
                    "fs": int(fs),
                    "sens": float(sens or 0.6),
                    "n": int(n_breaths),
                    "br": float(br_min),
                    "mp": float(mean_period),
                    "peaks": [int(p) for p in (peaks.tolist() if hasattr(peaks, "tolist") else (peaks if peaks is not None else []))],
                }

                # Slider setup
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 30)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Ventana (solo visual)
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                env_win = env_full[i0:i1]

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                env_line = env_win[::step]

                peaks_t = None
                peaks_y = None
                if peaks is not None and len(peaks) > 0:
                    try:
                        pw = np.array(peaks, dtype=int)
                        pw = pw[(pw >= i0) & (pw < i1)]
                        peaks_t = t[pw]
                        peaks_y = env_full[pw]
                    except Exception:
                        peaks_t, peaks_y = None, None

                thr = float(np.quantile(env_full, 0.90)) if len(env_full) > 0 else None
                fig = fig_resp(t_line, env_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
                kpis = kpi_grid_resp(n_breaths, br_min, mean_period)
                msg = f"Archivo {shown_name} analizado. Se detectaron {n_breaths} respiraciones."

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Re-render (ventana / calidad / winlen / sens) — sin guardar
            if not meta or not meta.get("path"):
                raise PreventUpdate

            save_path = meta.get("path")
            title = meta.get("title") or "Respiración"

            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo de respiración (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            t, x, fs = read_resp_csv(save_path)
            if len(x) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # Recalcula métricas si cambia sensibilidad (NO guardamos)
            n_breaths, br_min, mean_period, peaks = resp_metrics(t, x, fs, sens=sens or float(meta.get("sens") or 0.6))

            x0 = x - np.mean(x)
            env_full = smooth(x0, win_ms=250, fs=fs)

            # Si cambia winlen, resetea ventana al final
            if trig.startswith("resp-winlen"):
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 30)
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
            env_win = env_full[i0:i1]

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            env_line = env_win[::step]

            peaks_t = None
            peaks_y = None
            if peaks is not None and len(peaks) > 0:
                try:
                    pw = np.array(peaks, dtype=int)
                    pw = pw[(pw >= i0) & (pw < i1)]
                    peaks_t = t[pw]
                    peaks_y = env_full[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            thr = float(np.quantile(env_full, 0.90)) if len(env_full) > 0 else None
            fig = fig_resp(t_line, env_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
            kpis = kpi_grid_resp(n_breaths, br_min, mean_period)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks
