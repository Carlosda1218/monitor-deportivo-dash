# pages/sesiones.py
"""Histórico de sesiones de Combat Monitor."""
import os
import csv
import json
import re
from functools import lru_cache

import plotly.graph_objects as go
import dash
from dash import html, dcc, Input, Output, State, no_update, ctx
from dash.exceptions import PreventUpdate
from flask import session

import db
from ui_charts import apply_chart_style

_ECG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ecg")
_SPARKLINE_LIMIT = 24

_SPORT_ICON = {"taekwondo": "🥋", "boxeo": "🥊", "box": "🥊"}


def _sport_icon(sport: str) -> str:
    s = (sport or "").lower()
    for k, v in _SPORT_ICON.items():
        if k in s:
            return v
    return "🥊"


def _parse_combat_notes(notes: str) -> dict:
    rounds_m = re.search(r"(\d+) rounds?", notes or "")
    bpm_m    = re.search(r"Peak BPM (\d+)", notes or "")
    imp_m    = re.search(r"(\d+) impactos?", notes or "")
    return {
        "rounds": int(rounds_m.group(1)) if rounds_m else None,
        "peak_bpm": int(bpm_m.group(1)) if bpm_m else None,
        "impacts": int(imp_m.group(1)) if imp_m else None,
    }


def _data_path(filename: str):
    fname = os.path.basename(str(filename or ""))
    if not fname:
        return None
    return os.path.join(_ECG_DIR, fname)


@lru_cache(maxsize=256)
def _load_imu_counts(session_id: int) -> dict:
    """Return {dado: N, recibido: M} from IMU JSON sidecar."""
    try:
        rows = db.list_imu_metrics_by_session(session_id) or []
        if not rows:
            return {}
        stem  = os.path.splitext(os.path.basename(rows[0].get("filename", "")))[0]
        fpath = _data_path(f"{stem}.json")
        if not fpath:
            return {}
        if not os.path.exists(fpath):
            return {}
        with open(fpath) as fh:
            events = json.load(fh)
        dado     = sum(1 for e in events if e.get("type") == "dado")
        recibido = sum(1 for e in events if e.get("type") == "recibido")
        return {"dado": dado, "recibido": recibido}
    except Exception:
        return {}


@lru_cache(maxsize=128)
def _ecg_sparkline(session_id: int) -> go.Figure:
    """Tiny ECG waveform with no axes — for card thumbnail."""
    fig = go.Figure()
    try:
        files = db.list_ecg_files_by_session(session_id) or []
        if files:
            fpath = _data_path(files[0].get("filename"))
            if not fpath:
                return fig
            if os.path.exists(fpath):
                ts, ys = [], []
                with open(fpath, newline="") as fh:
                    for row in csv.DictReader(fh):
                        try:
                            ts.append(float(row.get("time", row.get("t", 0))))
                            ys.append(float(row.get("ecg",  row.get("y", 0))))
                        except (ValueError, TypeError):
                            pass
                # Downsample to ~300 pts for display
                step = max(1, len(ts) // 300)
                ts = ts[::step]; ys = ys[::step]
                if ts:
                    fig.add_trace(go.Scatter(
                        x=ts, y=ys, mode="lines",
                        line={"color": "#2fb7c4", "width": 1},
                        hoverinfo="skip",
                    ))
    except Exception:
        pass
    fig.update_layout(
        height=60, margin={"t": 0, "b": 0, "l": 0, "r": 0},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"visible": False, "fixedrange": True},
        yaxis={"visible": False, "fixedrange": True},
        showlegend=False,
    )
    return fig


def _hit_bar(dado: int, recibido: int) -> html.Div:
    total = dado + recibido
    if total == 0:
        return html.Div()
    pct_dado = round(dado / total * 100)
    pct_rec  = 100 - pct_dado
    return html.Div([
        html.Div(
            style={"display": "flex", "gap": "6px", "alignItems": "center",
                   "marginBottom": "4px"},
            children=[
                html.Span(f"● {dado} dados",
                          style={"fontSize": "11px", "color": "#27c98f"}),
                html.Span("·", style={"color": "var(--muted)", "fontSize": "11px"}),
                html.Span(f"● {recibido} recibidos",
                          style={"fontSize": "11px", "color": "#e45a5a"}),
            ],
        ),
        html.Div(
            style={"height": "6px", "borderRadius": "3px",
                   "background": "var(--line)", "overflow": "hidden"},
            children=[
                html.Div(
                    style={"width": f"{pct_dado}%", "height": "100%",
                           "background": "linear-gradient(90deg,#27c98f,#2fb7c4)",
                           "borderRadius": "3px"},
                ),
            ],
        ),
    ])


def _session_card(s: dict, show_sparkline: bool = True, show_delete: bool = True) -> html.Div:
    sid           = s["id"]
    notes         = s.get("notes") or ""
    ts_raw        = (s.get("ts_start") or "")
    sport         = s.get("sport") or "Combate"
    icon          = _sport_icon(sport)
    athlete_name  = s.get("_athlete_name")  # set for coach view

    try:
        from datetime import datetime as _dt
        dt       = _dt.fromisoformat(ts_raw[:19])
        date_str = dt.strftime("%d/%m/%Y")
        time_str = dt.strftime("%H:%M")
    except Exception:
        date_str = ts_raw[:10]
        time_str = ""

    parsed   = _parse_combat_notes(notes)
    rounds   = parsed["rounds"]
    peak_bpm = parsed["peak_bpm"]
    impacts  = parsed["impacts"]
    imu      = _load_imu_counts(sid)
    dado     = imu.get("dado", 0)
    recibido = imu.get("recibido", 0)

    kpis = []
    for label, val, unit in [
        ("Rounds",   str(rounds)   if rounds   else "—", ""),
        ("Pico BPM", str(peak_bpm) if peak_bpm else "—", "bpm"),
        ("Impactos", str(impacts)  if impacts  else "—", ""),
        ("Dados",    str(dado),  ""),
        ("Recibidos",str(recibido), ""),
    ]:
        kpis.append(html.Div(
            style={"textAlign": "center"},
            children=[
                html.Div(val + (f" {unit}" if unit else ""),
                         style={"fontSize": "18px", "fontWeight": "700",
                                "color": "var(--ink)", "lineHeight": "1.1"}),
                html.Div(label,
                         style={"fontSize": "10px", "color": "var(--muted)",
                                "textTransform": "uppercase",
                                "letterSpacing": "0.06em", "marginTop": "2px"}),
            ],
        ))

    sparkline = dcc.Graph(
        figure=_ecg_sparkline(sid),
        config={"displayModeBar": False, "staticPlot": True},
        style={"height": "60px", "marginBottom": "8px"},
    ) if show_sparkline else html.Div()

    return html.Div(
        className="card",
        style={"marginBottom": "12px"},
        children=[
            # Header
            html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "flex-start", "marginBottom": "12px"},
                children=[
                    html.Div([
                        html.Span(f"{icon} {sport}",
                                  style={"fontWeight": "700", "fontSize": "14px"}),
                        *([html.Span(
                            f" · {athlete_name}",
                            style={"fontWeight": "600", "fontSize": "13px",
                                   "color": "var(--neon)"},
                        )] if athlete_name else []),
                        html.Div(f"{date_str}  {time_str}",
                                 style={"fontSize": "12px", "color": "var(--muted)",
                                        "marginTop": "2px"}),
                    ]),
                    html.Span(f"#{sid}",
                              style={"fontSize": "11px", "color": "var(--muted)",
                                     "fontFamily": "monospace", "padding": "2px 8px",
                                     "background": "var(--line)", "borderRadius": "6px"}),
                ],
            ),
            # KPI row
            html.Div(
                style={"display": "grid",
                       "gridTemplateColumns": "repeat(5, 1fr)",
                       "gap": "8px", "marginBottom": "12px"},
                children=kpis,
            ),
            # Hit bar
            _hit_bar(dado, recibido),
            html.Div(className="ecg-divider", style={"margin": "10px 0"}),
            # ECG sparkline
            sparkline,
            # Actions
            html.Div(
                style={"display": "flex", "gap": "8px", "flexWrap": "wrap"},
                children=[
                    dcc.Link(
                        html.Button("📊 Ver análisis", className="btn btn--primary",
                                    style={"fontSize": "12px", "padding": "6px 14px"}),
                        href=f"/ecg?session={sid}&tab=signals",
                    ),
                    dcc.Link(
                        html.Button("🎬 Replay de combate", className="btn",
                                    style={"fontSize": "12px", "padding": "6px 14px"}),
                        href=f"/ecg?session={sid}&tab=replay",
                    ),
                    *([html.Button(
                        "🗑 Eliminar",
                        id={"type": "btn-del-session", "index": sid},
                        n_clicks=0,
                        className="btn",
                        style={"fontSize": "12px", "padding": "6px 14px",
                               "color": "var(--punch)"},
                    )] if show_delete else []),
                ],
            ),
            # Confirm delete area (hidden by default, only when show_delete=True)
            *([] if not show_delete else [html.Div(
                id={"type": "del-confirm", "index": sid},
                style={"display": "none", "marginTop": "10px"},
                children=[
                    html.Div(
                        className="card",
                        style={"background": "rgba(228,90,90,0.08)",
                               "border": "1px solid rgba(228,90,90,0.3)",
                               "padding": "10px 14px"},
                        children=[
                            html.P("¿Eliminar esta sesión? Esta acción no se puede deshacer.",
                                   style={"margin": "0 0 10px", "fontSize": "13px",
                                          "color": "var(--punch)"}),
                            html.Div(style={"display": "flex", "gap": "8px"}, children=[
                                html.Button(
                                    "Sí, eliminar",
                                    id={"type": "btn-del-confirm", "index": sid},
                                    n_clicks=0,
                                    className="btn",
                                    style={"background": "var(--punch)",
                                           "color": "#fff", "fontSize": "12px"},
                                ),
                                html.Button(
                                    "Cancelar",
                                    id={"type": "btn-del-cancel", "index": sid},
                                    n_clicks=0,
                                    className="btn",
                                    style={"fontSize": "12px"},
                                ),
                            ]),
                        ],
                    ),
                ],
            )]),
        ],
    )


def _load_combat_sessions(uid_int: int, role: str, sport: str = None) -> list:
    """Return sorted Combat Monitor sessions for a user (or all athletes for a coach)."""
    sessions_raw = []
    try:
        if role == "coach":
            athletes = db.list_roster_for_coach(uid_int, sport=sport) or []
            _ids = [int(a["id"]) for a in athletes if a.get("id")]
            _name_map = {int(a["id"]): a.get("name") or "Deportista" for a in athletes if a.get("id")}
            if _ids:
                sessions_raw = db.list_sessions_for_team(_ids, limit=200) or []
                for s in sessions_raw:
                    s["_athlete_name"] = _name_map.get(s.get("athlete_id"), "Deportista")
        else:
            sessions_raw = db.list_sessions(uid_int, limit=50) or []
    except Exception:
        sessions_raw = []
    combat = [s for s in sessions_raw
              if (s.get("notes") or "").startswith("Combat Monitor")]
    combat.sort(key=lambda s: s.get("ts_start") or "", reverse=True)
    return combat


def layout() -> html.Div:
    uid  = session.get("user_id")
    role = session.get("role") or ""

    if not uid:
        return html.Div(
            html.P("Inicia sesión para ver tu historial.", className="text-muted"),
            className="page-content",
        )

    uid_int    = int(uid)
    coach_sport = str(session.get("sport") or "").strip() or None
    combat     = _load_combat_sessions(uid_int, role, sport=coach_sport)

    _title = "Historial del equipo" if role == "coach" else "Historial de combates"
    _sub   = (
        f"{len(combat)} {'sesión' if len(combat) == 1 else 'sesiones'} del equipo · ECG y datos IMU incluidos"
        if role == "coach" else
        f"{len(combat)} {'sesión guardada' if len(combat) == 1 else 'sesiones guardadas'} · ECG y datos IMU incluidos"
    )
    page_head = html.Div(className="page-head", children=[
        html.H2(_title),
        html.P(_sub, className="text-muted"),
        html.Div(className="btn-save-row", children=[
            html.Button(
                "Exportar historial (Excel)",
                id="btn-export-sessions-history",
                n_clicks=0,
                className="btn btn-ghost",
                disabled=not bool(combat),
            ),
        ]),
    ])

    if not combat:
        empty = html.Div(className="card", style={"textAlign": "center", "padding": "40px"}, children=[
            html.Div("🥊", style={"fontSize": "40px", "marginBottom": "12px"}),
            html.H4("Sin sesiones guardadas aún"),
            html.P(
                "Completa un combate en el Monitor y guarda la sesión para verla aquí.",
                className="text-muted",
            ),
            dcc.Link(
                html.Button("Ir al Monitor de Combate", className="btn btn--primary"),
                href="/analisis",
            ),
        ])
        return html.Div([dcc.Download(id="dl-sessions-history"), page_head, html.Div(className="ecg-divider ecg-divider--spaced"), empty],
                        className="page-content")

    cards = [
        _session_card(s, show_sparkline=(i < _SPARKLINE_LIMIT))
        for i, s in enumerate(combat)
    ]

    return html.Div([
        dcc.Download(id="dl-sessions-history"),
        page_head,
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Div(
            id="sesiones-msg",
            style={"marginBottom": "8px"},
            **{"role": "status", "aria-live": "polite"},
        ),
        html.Div(id="sesiones-list", children=cards),
    ], className="page-content")


def register_callbacks(app: dash.Dash) -> None:

    @app.callback(
        Output("dl-sessions-history", "data"),
        Input("btn-export-sessions-history", "n_clicks"),
        prevent_initial_call=True,
    )
    def export_sessions_history(n_clicks):
        if not n_clicks:
            raise PreventUpdate

        uid = session.get("user_id")
        role = session.get("role") or ""
        if not uid:
            raise PreventUpdate

        coach_sport = str(session.get("sport") or "").strip() or None
        combat = _load_combat_sessions(int(uid), role, sport=coach_sport)
        if not combat:
            raise PreventUpdate

        from datetime import datetime as _dt
        from report_utils import safe_filename_stem, xlsx_table

        owner = db.get_user_by_id(int(uid)) if hasattr(db, "get_user_by_id") else None
        owner_name = (owner or {}).get("name") or session.get("name") or "CombatIQ"
        owner_sport = coach_sport or (owner or {}).get("sport") or session.get("sport") or "Combate"
        exported_at = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        meta = [
            ("Exportado", exported_at),
            ("Perfil", "Coach" if role == "coach" else "Deportista"),
            ("Nombre", owner_name),
            ("Deporte", owner_sport),
            ("Sesiones incluidas", str(len(combat))),
        ]
        headers = [
            "ID",
            "Fecha",
            "Hora",
            "Atleta",
            "Deporte",
            "Rounds",
            "Pico BPM",
            "Impactos",
            "Golpes dados",
            "Golpes recibidos",
            "Notas",
        ]
        rows = []
        for s in combat:
            sid = int(s.get("id") or 0)
            notes = s.get("notes") or ""
            parsed = _parse_combat_notes(notes)
            imu = _load_imu_counts(sid)
            ts_raw = s.get("ts_start") or ""
            try:
                dt = _dt.fromisoformat(ts_raw[:19])
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
            except Exception:
                date_str = ts_raw[:10]
                time_str = ""
            rows.append([
                sid,
                date_str,
                time_str,
                s.get("_athlete_name") or owner_name,
                s.get("sport") or owner_sport,
                parsed.get("rounds") or "",
                parsed.get("peak_bpm") or "",
                parsed.get("impacts") or "",
                imu.get("dado", 0),
                imu.get("recibido", 0),
                re.sub(r"\s+", " ", notes).strip(),
            ])

        xlsx_bytes = xlsx_table(
            "Historial de combates",
            meta,
            headers,
            rows,
            sheet_name="Combates",
            col_types={0: "int", 5: "int", 6: "int", 7: "int", 8: "int", 9: "int"},
        )
        safe_owner = safe_filename_stem(owner_name, "combatiq")
        filename = f"CombatIQ_historial_combates_{safe_owner}_{_dt.utcnow().strftime('%Y%m%d')}.xlsx"
        return dcc.send_bytes(lambda b: b.write(xlsx_bytes), filename)

    @app.callback(
        Output("sesiones-list",    "children"),
        Output("sesiones-msg",     "children"),
        Input({"type": "btn-del-confirm", "index": dash.ALL}, "n_clicks"),
        State({"type": "btn-del-confirm", "index": dash.ALL}, "id"),
        prevent_initial_call=True,
    )
    def confirm_delete(n_list, id_list):
        triggered = ctx.triggered_id
        if not triggered:
            raise PreventUpdate
        sid = triggered.get("index")
        if not sid:
            raise PreventUpdate
        # Check any click happened
        idx = next((i for i, id_ in enumerate(id_list) if id_.get("index") == sid), None)
        if idx is None or not n_list[idx]:
            raise PreventUpdate

        try:
            session_row = db.get_session(int(sid))
            uid = session.get("user_id")
            role = session.get("role") or ""
            if not uid or not session_row:
                raise PermissionError("Sesion no disponible.")
            athlete_id = int(session_row.get("athlete_id"))
            if role == "admin":
                pass
            elif role == "coach":
                coach_sport = str(session.get("sport") or "").strip() or None
                if not db.coach_has_athlete(int(uid), athlete_id, sport=coach_sport):
                    raise PermissionError("No tienes permisos para eliminar esta sesion.")
            elif athlete_id != int(uid):
                raise PermissionError("No tienes permisos para eliminar esta sesion.")
            db.delete_session(int(sid))
            _load_imu_counts.cache_clear()
            _ecg_sparkline.cache_clear()
        except Exception as e:
            return no_update, html.P(f"Error al eliminar: {e}",
                                     style={"color": "var(--punch)", "fontSize": "13px"})

        # Reload sessions for current user
        uid  = session.get("user_id")
        role = session.get("role") or ""
        if not uid:
            return [], ""
        coach_sport = str(session.get("sport") or "").strip() or None
        combat = _load_combat_sessions(int(uid), role, sport=coach_sport)

        if not combat:
            empty = html.Div(className="card", style={"textAlign": "center", "padding": "40px"}, children=[
                html.Div("🥊", style={"fontSize": "40px", "marginBottom": "12px"}),
                html.H4("Sin sesiones guardadas aún"),
                html.P("Completa un combate en el Monitor y guarda la sesión.", className="text-muted"),
                dcc.Link(html.Button("Ir al Monitor", className="btn btn--primary"), href="/analisis"),
            ])
            return [empty], html.P(f"Sesión #{sid} eliminada.", style={"color": "var(--muted)", "fontSize": "13px"})

        cards = [
            _session_card(s, show_sparkline=(i < _SPARKLINE_LIMIT))
            for i, s in enumerate(combat)
        ]
        msg   = html.P(f"Sesión #{sid} eliminada.", style={"color": "var(--muted)", "fontSize": "13px"})
        return cards, msg

    @app.callback(
        Output({"type": "del-confirm", "index": dash.ALL}, "style"),
        Input({"type": "btn-del-session", "index": dash.ALL}, "n_clicks"),
        Input({"type": "btn-del-cancel",  "index": dash.ALL}, "n_clicks"),
        State({"type": "del-confirm",     "index": dash.ALL}, "id"),
        prevent_initial_call=True,
    )
    def toggle_delete_confirm(del_clicks, cancel_clicks, id_list):
        triggered = ctx.triggered_id
        if not triggered:
            raise PreventUpdate
        t_type  = triggered.get("type")
        t_index = triggered.get("index")
        styles = []
        for id_ in id_list:
            if id_.get("index") == t_index and t_type == "btn-del-session":
                styles.append({"display": "block", "marginTop": "10px"})
            else:
                styles.append({"display": "none", "marginTop": "10px"})
        return styles
