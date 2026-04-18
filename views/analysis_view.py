# views/analysis_view.py
"""
Vista "Análisis Profesional" para CombatIQ.

Muestra:
  - Panel de alertas automáticas
  - ACWR con gráfica de carga semanal
  - HRV Readiness con tendencia
  - Wellness trend
  - Tendencias IMU
  - Análisis narrativo generado por Claude (ai_insights)

Roles:
  - deportista → analiza su propio perfil
  - coach      → selector de atleta del equipo
"""

from __future__ import annotations

import json
from datetime import datetime

import plotly.graph_objects as go

import dash
from dash import html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate
from flask import session

import db as _db
import analysis_engine as AE
import ai_insights as AI
from ui_charts import apply_chart_style, graph_config


# ─── helpers internos ─────────────────────────────────────────────────────────

def _safe_int(x):
    try:
        return int(x)
    except Exception:
        return None


def _to_str(x) -> str:
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return str(x) if x is not None else ""


def _get_ecg_metrics_for_file(file_id: int):
    with _db._get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT bpm, sdnn, rmssd FROM ecg_metrics WHERE ecg_file_id=? ORDER BY id DESC LIMIT 1",
            (int(file_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"bpm": row[0], "sdnn": row[1], "rmssd": row[2]}


# ─── sub-componentes de UI ────────────────────────────────────────────────────

def _alert_card(alert: dict) -> html.Div:
    level = alert.get("level", "ok")
    icon, color = AE.alert_badge(level)
    return html.Div(
        className=f"analysis-alert analysis-alert--{level}",
        children=[
            html.Span(icon, className="analysis-alert__icon", style={"color": color}),
            html.Div([
                html.Strong(alert.get("title", ""), className="analysis-alert__title"),
                html.P(alert.get("message", ""), className="analysis-alert__msg"),
            ], className="analysis-alert__body"),
        ],
    )


def _kpi(label: str, value: str, sub: str = "", color: str = "var(--ink)") -> html.Div:
    return html.Div(
        className="kpi",
        children=[
            html.Div(value, className="kpi__value", style={"color": color}),
            html.Div(label, className="kpi__label"),
            html.Div(sub, className="kpi__sub") if sub else None,
        ],
    )


def _section_title(text: str) -> html.H4:
    return html.H4(text, className="card-title")


def _no_data_msg(msg: str) -> html.P:
    return html.P(msg, className="text-muted", style={"padding": "12px 0"})


# ─── gráficas ─────────────────────────────────────────────────────────────────

def _build_acwr_chart(acwr_data: dict) -> go.Figure:
    data_7d = acwr_data.get("data_7d", [])
    dates = [d["date"] for d in data_7d]
    loads = [d["load"] for d in data_7d]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=loads,
        name="Carga diaria",
        marker_color="var(--neon)" if acwr_data.get("zone") in ("optimal", "low") else "#f0a500",
    ))
    apply_chart_style(fig, height=220)
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Carga (UA)",
        showlegend=False,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def _build_hrv_chart(hrv_data: dict) -> go.Figure:
    trend = hrv_data.get("trend_data", [])
    dates = [d["date"] for d in trend]
    values = [d["rmssd"] for d in trend]
    baseline = hrv_data.get("baseline_rmssd")

    fig = go.Figure()
    if values:
        fig.add_trace(go.Scatter(
            x=dates, y=values,
            mode="lines+markers",
            name="RMSSD",
            line=dict(color="var(--neon)", width=2),
            marker=dict(size=5),
        ))
    if baseline and dates:
        fig.add_hline(
            y=baseline,
            line_dash="dash",
            line_color="var(--muted)",
            annotation_text=f"Baseline {baseline:.0f}ms",
            annotation_position="bottom right",
        )
    apply_chart_style(fig, height=220)
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="RMSSD (ms)",
        showlegend=False,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def _build_wellness_chart(wellness_data: dict) -> go.Figure:
    data = wellness_data.get("data", [])
    dates  = [d["date"]  for d in data]
    scores = [d["score"] for d in data]

    fig = go.Figure()
    if scores:
        colors_pts = [
            "var(--punch)" if s < 50 else ("var(--neon)" if s >= 70 else "#f0a500")
            for s in scores
        ]
        fig.add_trace(go.Scatter(
            x=dates, y=scores,
            mode="lines+markers",
            name="Wellness",
            line=dict(color="var(--neon)", width=2),
            marker=dict(size=7, color=colors_pts),
        ))
        fig.add_hline(y=50, line_dash="dot", line_color="var(--muted)")
    apply_chart_style(fig, height=200)
    fig.update_layout(
        xaxis_title=None,
        yaxis=dict(range=[0, 100]),
        showlegend=False,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


# ─── Panels ───────────────────────────────────────────────────────────────────

def _alerts_panel(alerts: list) -> html.Div:
    if not alerts:
        return html.Div()
    return html.Div(
        className="card",
        children=[
            _section_title("Alertas automáticas"),
            html.Div(
                [_alert_card(a) for a in alerts],
                className="analysis-alerts-list",
            ),
        ],
    )


def _acwr_panel(acwr_data: dict) -> html.Div:
    ratio   = acwr_data.get("ratio")
    zone    = acwr_data.get("zone", "no_data")
    color   = AE.zone_color(zone)
    ratio_str = f"{ratio:.2f}" if ratio is not None else "—"

    return html.Div(
        className="card",
        children=[
            _section_title("Carga de entrenamiento (ACWR)"),
            html.P(acwr_data.get("label", ""), className="text-muted"),
            html.Div(
                className="kpis",
                children=[
                    _kpi("Ratio aguda/crónica", ratio_str, "óptimo 0.8–1.3", color),
                    _kpi("Carga aguda (7d)", f"{acwr_data.get('acute_load', 0):.0f} UA"),
                    _kpi("Carga crónica (28d)", f"{acwr_data.get('chronic_load', 0):.0f} UA"),
                    _kpi("Tendencia", acwr_data.get("trend", "—").capitalize()),
                ],
            ),
            dcc.Graph(
                figure=_build_acwr_chart(acwr_data),
                config=graph_config(),
                style={"marginTop": "8px"},
            ) if acwr_data.get("data_7d") else _no_data_msg("Sin datos de carga disponibles."),
        ],
    )


def _hrv_panel(hrv_data: dict) -> html.Div:
    zone  = hrv_data.get("zone", "no_data")
    color = AE.zone_color(zone)
    today = hrv_data.get("today_rmssd")
    base  = hrv_data.get("baseline_rmssd")
    delta = hrv_data.get("delta_pct")

    return html.Div(
        className="card",
        children=[
            _section_title("Variabilidad cardiaca (HRV Readiness)"),
            html.P(hrv_data.get("label", ""), className="text-muted"),
            html.Div(
                className="kpis",
                children=[
                    _kpi("RMSSD hoy", f"{today:.0f} ms" if today else "—", color=color),
                    _kpi("Baseline 30d", f"{base:.0f} ms" if base else "—"),
                    _kpi("Variación", f"{delta:+.1f}%" if delta is not None else "—", color=color),
                    _kpi("FC (última)", f"{hrv_data.get('today_bpm') or '—':.0f} bpm"
                         if hrv_data.get("today_bpm") else "—"),
                ],
            ),
            dcc.Graph(
                figure=_build_hrv_chart(hrv_data),
                config=graph_config(),
                style={"marginTop": "8px"},
            ) if hrv_data.get("trend_data") else _no_data_msg("Sube registros ECG para ver la tendencia HRV."),
        ],
    )


def _wellness_panel(wellness_data: dict) -> html.Div:
    latest  = wellness_data.get("latest_score")
    avg     = wellness_data.get("avg_score")
    trend   = wellness_data.get("trend", "no_data")
    color   = AE.zone_color(trend)

    return html.Div(
        className="card",
        children=[
            _section_title("Bienestar subjetivo (Wellness)"),
            html.P(wellness_data.get("label", ""), className="text-muted"),
            html.Div(
                className="kpis",
                children=[
                    _kpi("Hoy", f"{latest:.0f}/100" if latest else "—", color=color),
                    _kpi("Promedio 14d", f"{avg:.0f}/100" if avg else "—"),
                    _kpi("Días bajos (<50)", str(wellness_data.get("low_days", 0))),
                    _kpi("Tendencia", trend.replace("_", " ").capitalize()),
                ],
            ),
            dcc.Graph(
                figure=_build_wellness_chart(wellness_data),
                config=graph_config(),
                style={"marginTop": "8px"},
            ) if wellness_data.get("data") else _no_data_msg("Completa el check-in diario para ver tu wellness."),
        ],
    )


def _imu_panel(imu_data: dict) -> html.Div:
    return html.Div(
        className="card",
        children=[
            _section_title("Volumen e intensidad técnica (IMU)"),
            html.P(imu_data.get("label", ""), className="text-muted"),
            html.Div(
                className="kpis",
                children=[
                    _kpi("Acciones (28d)", str(imu_data.get("total_hits", 0))),
                    _kpi("Golpes/min (media)", f"{imu_data.get('avg_hits_per_min', 0):.1f}"),
                    _kpi("Intensidad media", f"{imu_data.get('avg_intensity', 0):.1f}g"),
                    _kpi("Pico máximo", f"{imu_data.get('peak_intensity', 0):.1f}g"),
                ],
            ) if imu_data.get("total_hits", 0) > 0 else _no_data_msg(
                "Sin datos IMU. Sube archivos de sesión con métricas de golpes/patadas."
            ),
        ],
    )


def _ai_panel(report: dict, athlete_name: str, sport: str) -> html.Div:
    """Panel con el análisis narrativo de Claude."""
    note = AI.generate_coaching_note(
        report=report,
        athlete_name=athlete_name,
        sport=sport,
    )
    return html.Div(
        className="card",
        children=[
            _section_title("Análisis narrativo (IA)"),
            html.Div(
                # Renderizamos como pre-wrap para conservar saltos de línea del markdown
                dcc.Markdown(note, className="ai-note"),
            ),
        ],
    )


# ─── Layout principal ─────────────────────────────────────────────────────────

class AnalysisView:
    def __init__(self, app: dash.Dash, db, sensors):
        self.app = app
        self.db  = db
        self.S   = sensors
        self._register_callbacks()

    # ── layout ──

    def layout(self) -> html.Div:
        uid   = _safe_int(session.get("user_id"))
        role  = _to_str(session.get("role"))
        sport = _to_str(session.get("sport") or "")

        if not uid:
            return html.Div(
                html.P("Inicia sesión para ver tu análisis.", className="text-muted"),
                className="page-shell",
            )

        page_head = html.Div(
            className="page-head",
            children=[
                html.H2("Análisis profesional"),
                html.P(
                    "Carga de entrenamiento · HRV · Bienestar · Tendencias técnicas · IA",
                    className="text-muted",
                ),
            ],
        )

        if role == "coach":
            return self._layout_coach(uid, sport, page_head)
        else:
            return self._layout_athlete(uid, sport, page_head)

    # ── layout atleta ──

    def _layout_athlete(self, uid: int, sport: str, page_head) -> html.Div:
        user = self.db.get_user_by_id(uid)
        name = user.get("name", "Atleta") if user else "Atleta"

        report = AE.full_report(uid=uid, db_module=self.db)

        return html.Div([
            page_head,
            html.Hr(className="ecg-divider"),
            _alerts_panel(report["alerts"]),
            _acwr_panel(report["acwr"]),
            _hrv_panel(report["hrv"]),
            _wellness_panel(report["wellness"]),
            _imu_panel(report["imu"]),
            _ai_panel(report, athlete_name=name, sport=sport),
        ])

    # ── layout coach ──

    def _layout_coach(self, coach_id: int, coach_sport: str, page_head) -> html.Div:
        athletes = self.db.list_athletes_for_coach(coach_id, sport=coach_sport or None)
        opts = [{"label": a["name"], "value": a["id"]} for a in athletes if a.get("id")]

        selector = html.Div(
            className="card",
            children=[
                _section_title("Seleccionar atleta"),
                dcc.Dropdown(
                    id="analysis-athlete-select",
                    options=opts,
                    placeholder="Elige un atleta…",
                    clearable=False,
                    className="dash-dropdown",
                ),
            ],
        )

        content_area = html.Div(id="analysis-content", children=[
            html.P("Selecciona un atleta para ver su análisis.", className="text-muted"),
        ])

        return html.Div([
            page_head,
            html.Hr(className="ecg-divider"),
            selector,
            content_area,
        ])

    # ── callbacks ──

    def _register_callbacks(self):
        @self.app.callback(
            Output("analysis-content", "children"),
            Input("analysis-athlete-select", "value"),
            prevent_initial_call=True,
        )
        def update_analysis(athlete_id):
            if athlete_id is None:
                raise PreventUpdate

            uid   = _safe_int(athlete_id)
            sport = _to_str(session.get("sport") or "")
            user  = self.db.get_user_by_id(uid)
            if not user:
                return html.P("Atleta no encontrado.", className="text-muted")

            name       = user.get("name", "Atleta")
            sport_atl  = user.get("sport") or sport

            report = AE.full_report(uid=uid, db_module=self.db)

            return [
                _alerts_panel(report["alerts"]),
                _acwr_panel(report["acwr"]),
                _hrv_panel(report["hrv"]),
                _wellness_panel(report["wellness"]),
                _imu_panel(report["imu"]),
                _ai_panel(report, athlete_name=name, sport=sport_atl),
            ]
