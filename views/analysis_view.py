# views/analysis_view.py
"""
Monitor de Combate — CombatIQ
Simulación ECG+IMU en tiempo real con timer oficial por deporte/categoría.
"""
from __future__ import annotations

import json
import math
import os
import random
from datetime import datetime

import plotly.graph_objects as go
import dash
from dash import html, dcc, Input, Output, State, no_update
from dash.exceptions import PreventUpdate
from flask import session

import db as _db
import ai_insights as AI
from ui_charts import apply_chart_style, graph_config


# ── Sport configurations (official rules) ─────────────────────────────────────

SPORT_CONFIGS: dict[str, dict] = {
    "tkd": {
        "label": "Taekwondo (WT)",
        "rounds": 3, "fight_s": 120, "rest_s": 60,
        # WT Electronic Scoring Protector (ESP) calibration thresholds (g-force at IMU sensor)
        # Trunk kick: ≥5g ≈ 54-59N at hogu per WT ESP data; head: no WT force minimum (~2.5g practical)
        "scoring_g_trunk": 5.0,
        "scoring_g_head": 2.5,
        "touch_g_min": 1.5,
        "point_values": {"trunk": 1, "trunk_spin": 2, "head": 3, "head_spin": 4},
    },
    "box_elite_m": {
        "label": "Boxeo Elite Masculino (IBA)",
        "rounds": 3, "fight_s": 180, "rest_s": 60,
    },
    "box_elite_f": {
        "label": "Boxeo Elite Femenino (IBA)",
        "rounds": 3, "fight_s": 180, "rest_s": 60,
    },
    "box_junior_m": {
        "label": "Boxeo Junior (IBA)",
        "rounds": 3, "fight_s": 120, "rest_s": 60,
    },
    "box_youth": {
        "label": "Boxeo Youth (IBA)",
        "rounds": 3, "fight_s": 180, "rest_s": 60,
    },
    "box_pro_m": {
        "label": "Boxeo Profesional Masculino",
        "rounds": 12, "fight_s": 180, "rest_s": 60,
    },
}

TICK_S      = 0.5
ECG_HZ      = 20
ECG_BUF_MAX = int(10 * ECG_HZ)   # 10 s at 20 Hz — sliding window for display (was 15 s)
ECG_FULL_DECIMATE = 5             # keep 1 of every N points in full-combat store (→ 4 Hz)
ECG_FULL_HZ       = max(1, int(round(ECG_HZ / ECG_FULL_DECIMATE)))
ECG_FULL_MAX      = 12_500        # cap decimated ECG history (~52 min @ 4 Hz)
IMU_FULL_MAX = 5_000              # server-side hit history for reports/saves
IMU_BUF_MAX = 300                 # was 500 — limits serialization size
DISPLAY_WIN = 8.0                 # seconds of ECG visible

# Server-side ECG full accumulator.
# Storing up to 10k points in a dcc.Store would serialize ~200 KB of JSON every
# 500 ms tick — flooding the WebSocket and freezing the browser. Instead we keep
# the data in process memory and read it server-side in save_session.
_ecg_full_cache: dict = {}  # uid_key → list[{t, y}]


# ── State helpers ──────────────────────────────────────────────────────────────

_imu_full_cache: dict = {}  # uid_key -> list[hit events]


def _combat_uid_key(value=None) -> str:
    return str(value or "_anon")


def _reset_combat_caches(uid_key: str) -> None:
    _ecg_full_cache.pop(uid_key, None)
    _imu_full_cache.pop(uid_key, None)


def _record_full_imu_hit(uid_key: str, event: dict) -> None:
    """Keep full hit history server-side without growing the browser store."""
    buf = _imu_full_cache.get(uid_key, [])
    buf.append(dict(event))
    if len(buf) > IMU_FULL_MAX:
        buf = buf[-IMU_FULL_MAX:]
    _imu_full_cache[uid_key] = buf


def _full_imu_data(state: dict | None, imu_data: list | None) -> list:
    uid_key = _combat_uid_key((state or {}).get("athlete_id"))
    return _imu_full_cache.get(uid_key) or (imu_data or [])


def _initial_state(sport_key: str = "tkd", athlete_id=None) -> dict:
    cfg = SPORT_CONFIGS.get(sport_key, SPORT_CONFIGS["tkd"])
    return {
        "active": False,
        "phase": "idle",           # idle | fight | rest | summary
        "sport_key": sport_key,
        "total_rounds": cfg["rounds"],
        "fight_s": cfg["fight_s"],
        "rest_s": cfg["rest_s"],
        "current_round": 1,
        "elapsed_s": 0.0,
        "combat_t": 0.0,           # total seconds since combat start
        "current_bpm": 70.0,
        "current_rmssd": 65.0,
        "peak_bpm": 0.0,
        "status": "idle",
        "total_impacts": 0,
        "round_impacts": [0],
        "rounds_completed": 0,
        "athlete_id": athlete_id,
        # Scoring counters — populated only for sports with scoring_g_trunk threshold
        "score_dado": 0,
        "score_recibido": 0,
        "puntuables_dado": 0,
        "puntuables_recibido": 0,
        "touches_dado": 0,
        "touches_recibido": 0,
        # Per-round ECG stats accumulators
        "round_stats": [],         # [{"round": int, "avg_bpm": float, "peak_bpm": float}]
        "fight_bpm_sum": 0.0,
        "fight_bpm_count": 0,
        "round_peak_bpm_cur": 0.0,
    }


def _demo_data(sport_key: str = "tkd", athlete_id=None) -> tuple:
    """Return (state, ecg_data, imu_data) pre-filled to showcase every UI state."""
    cfg = SPORT_CONFIGS.get(sport_key, SPORT_CONFIGS["tkd"])
    state = _initial_state(sport_key, athlete_id)
    state.update({
        "active": True,
        "phase": "fight",
        "current_round": 2,
        "elapsed_s": 45.0,
        "combat_t": cfg["fight_s"] + cfg["rest_s"] + 45.0,
        "current_bpm": 162.0,
        "current_rmssd": 22.0,
        "peak_bpm": 174.0,
        "status": "red",
        "total_impacts": 18,
        "round_impacts": [9, 9],
        "rounds_completed": 1,
        "round_stats": [{"round": 1, "avg_bpm": 152.4, "peak_bpm": 168.0}],
        "fight_bpm_sum": 162.0 * 45 / TICK_S * 0.6,
        "fight_bpm_count": int(45 / TICK_S),
        "round_peak_bpm_cur": 162.0,
    })

    # ECG — 300 pts of high-intensity PQRST at 162 BPM
    t0 = state["combat_t"] - (ECG_BUF_MAX / ECG_HZ)
    ecg = [
        {"t": round(t0 + i / ECG_HZ, 3),
         "y": round(_ecg_amp(t0 + i / ECG_HZ, 162.0), 4)}
        for i in range(ECG_BUF_MAX)
    ]

    # IMU — ruido continuo + impactos en R1 y R2
    combat_t0 = cfg["fight_s"] + cfg["rest_s"]
    imu = []
    # Ruido continuo tick a tick (0→combat_t actual)
    for tick in range(0, int(state["combat_t"]), 1):
        rnd = 1 if tick < cfg["fight_s"] else 2
        imu.append({"t": float(tick), "intensity": round(random.uniform(0.3, 1.4), 2),
                    "round": rnd, "type": "ruido"})
    # Impactos reales R1
    for t, htype, g in [
        (8,  "dado",     random.uniform(5, 13)),
        (15, "recibido", random.uniform(4, 11)),
        (22, "dado",     random.uniform(6, 14)),
        (38, "recibido", random.uniform(4, 10)),
        (55, "dado",     random.uniform(7, 13)),
        (74, "recibido", random.uniform(5, 11)),
        (89, "dado",     random.uniform(6, 12)),
        (102,"recibido", random.uniform(4, 10)),
        (115,"dado",     random.uniform(8, 14)),
    ]:
        imu.append({"t": float(t), "intensity": round(g, 2), "round": 1, "type": htype})
    # Impactos reales R2 (offset by fight+rest)
    for dt, htype, g in [
        (5,  "dado",     random.uniform(6, 14)),
        (12, "recibido", random.uniform(5, 12)),
        (27, "dado",     random.uniform(7, 13)),
        (35, "recibido", random.uniform(4, 11)),
        (44, "dado",     random.uniform(8, 14)),
    ]:
        imu.append({"t": round(combat_t0 + dt, 2), "intensity": round(g, 2),
                    "round": 2, "type": htype})
    imu.sort(key=lambda e: e["t"])
    if len(imu) > IMU_BUF_MAX:
        imu = imu[-IMU_BUF_MAX:]

    # Classify scoring for sports with IMU thresholds (e.g. TKD)
    _cfg_d  = SPORT_CONFIGS.get(sport_key, {})
    _g_tr_d = _cfg_d.get("scoring_g_trunk")
    if _g_tr_d is not None:
        _pv_d = _cfg_d.get("point_values",
                            {"trunk": 1, "trunk_spin": 2, "head": 3, "head_spin": 4})
        _s_dado = _s_recv = _p_dado = _p_recv = _t_dado = _t_recv = 0
        for _evt in imu:
            if _evt.get("type") in ("dado", "recibido"):
                _is_s = _evt["intensity"] >= _g_tr_d
                _pts_e = 0
                _tech_e = None
                if _is_s:
                    _r = random.random()
                    if _r < 0.05:
                        _tech_e, _pts_e = "head_spin", _pv_d.get("head_spin", 4)
                    elif _r < 0.15:
                        _tech_e, _pts_e = "head",      _pv_d.get("head", 3)
                    elif _r < 0.30:
                        _tech_e, _pts_e = "trunk_spin", _pv_d.get("trunk_spin", 2)
                    else:
                        _tech_e, _pts_e = "trunk",     _pv_d.get("trunk", 1)
                _evt["scoring"]   = _is_s
                _evt["pts"]       = _pts_e
                _evt["technique"] = _tech_e
                if _is_s:
                    if _evt["type"] == "dado":
                        _s_dado += _pts_e; _p_dado += 1
                    else:
                        _s_recv += _pts_e; _p_recv += 1
                else:
                    if _evt["type"] == "dado":
                        _t_dado += 1
                    else:
                        _t_recv += 1
            else:
                _evt.setdefault("scoring", False)
                _evt.setdefault("pts", 0)
                _evt.setdefault("technique", None)
        state.update({
            "score_dado": _s_dado, "score_recibido": _s_recv,
            "puntuables_dado": _p_dado, "puntuables_recibido": _p_recv,
            "touches_dado": _t_dado, "touches_recibido": _t_recv,
        })

    return state, ecg, imu


def _sport_key_from_sport(sport: str) -> str:
    s = (sport or "").lower()
    if "taekwondo" in s or "tkd" in s:
        return "tkd"
    if "box" in s:
        return "box_elite_m"
    return "tkd"


# ── Per-round ECG stats helper ────────────────────────────────────────────────

def _finalize_round(new_state: dict, round_num: int) -> None:
    """Append accumulated ECG stats for the completed fight round to round_stats."""
    bpm_sum   = new_state.get("fight_bpm_sum", 0.0)
    bpm_count = new_state.get("fight_bpm_count", 0)
    peak_bpm  = new_state.get("round_peak_bpm_cur", 0.0)
    avg_bpm   = round(bpm_sum / max(bpm_count, 1), 1)
    stats = list(new_state.get("round_stats", []))
    stats.append({"round": round_num, "avg_bpm": avg_bpm, "peak_bpm": round(peak_bpm, 1)})
    new_state["round_stats"]        = stats
    new_state["fight_bpm_sum"]      = 0.0
    new_state["fight_bpm_count"]    = 0
    new_state["round_peak_bpm_cur"] = 0.0


# ── ECG + IMU simulation ───────────────────────────────────────────────────────

def _ecg_amp(t: float, bpm: float) -> float:
    """PQRST-approximated ECG amplitude at time t (s) given BPM."""
    period = 60.0 / max(bpm, 30)
    ph = (t % period) / period
    r  =  1.00 * math.exp(-((ph - 0.44) / 0.030) ** 2)
    q  = -0.20 * math.exp(-((ph - 0.39) / 0.025) ** 2)
    s  = -0.15 * math.exp(-((ph - 0.50) / 0.025) ** 2)
    tw =  0.35 * math.exp(-((ph - 0.64) / 0.075) ** 2)
    pw =  0.12 * math.exp(-((ph - 0.16) / 0.060) ** 2)
    return r + q + s + tw + pw + random.gauss(0, 0.035)


def _sim_bpm(phase: str, elapsed_s: float, current_round: int,
             fight_s: int, prev_bpm: float) -> float:
    if phase == "fight":
        peak = 155 + current_round * 4          # R1→159, R2→163, R3→167 …
        t0   = 80.0 if current_round == 1 else min(prev_bpm, 115.0)
        bpm  = peak - (peak - t0) * math.exp(-elapsed_s / (fight_s * 0.35))
        return max(60.0, min(210.0, bpm + random.gauss(0, 2)))
    if phase == "rest":
        bpm = 72 + (prev_bpm - 72) * math.exp(-elapsed_s / 28)
        return max(50.0, min(210.0, bpm + random.gauss(0, 1.5)))
    return 70.0


def _sem_status(bpm: float, rmssd: float) -> str:
    if bpm >= 192 or rmssd < 12:
        return "red"
    if bpm >= 176 or rmssd < 22:
        return "yellow"
    return "green"


def _advance_tick(state: dict, ecg: list, imu: list) -> tuple:
    """Advance simulation by one TICK_S step. Returns (new_state, new_ecg, new_imu).
    Full ECG history is accumulated server-side in _ecg_full_cache to avoid
    serializing up to 200 KB of JSON over the WebSocket every tick."""
    if not state.get("active") or state.get("phase") in ("idle", "summary"):
        return state, ecg, imu

    new      = dict(state)
    elapsed  = state["elapsed_s"] + TICK_S
    combat_t = state["combat_t"] + TICK_S
    new["combat_t"] = combat_t
    phase = state["phase"]

    # Phase transitions
    if phase == "fight" and elapsed >= state["fight_s"]:
        new["rounds_completed"] = state.get("rounds_completed", 0) + 1
        _finalize_round(new, state["current_round"])
        if state["current_round"] >= state["total_rounds"]:
            new.update({"phase": "summary", "active": False, "elapsed_s": 0.0})
            return new, ecg, imu
        new.update({"phase": "rest", "elapsed_s": 0.0})
    elif phase == "rest" and elapsed >= state["rest_s"]:
        nr = state["current_round"] + 1
        new.update({
            "phase": "fight", "elapsed_s": 0.0, "current_round": nr,
            "round_impacts":    state.get("round_impacts", [0]) + [0],
            "fight_bpm_sum":    0.0,
            "fight_bpm_count":  0,
            "round_peak_bpm_cur": 0.0,
        })
    else:
        new["elapsed_s"] = elapsed

    # Vitals
    bpm   = _sim_bpm(new["phase"], new["elapsed_s"], new["current_round"],
                     new["fight_s"], state["current_bpm"])
    rmssd = max(8.0, 4500.0 / bpm + random.gauss(0, 3))
    new.update({
        "current_bpm": bpm,
        "current_rmssd": rmssd,
        "peak_bpm": max(state.get("peak_bpm", 0.0), bpm),
        "status": _sem_status(bpm, rmssd),
    })

    # Accumulate ECG stats only during active fight phase
    if new.get("phase") == "fight":
        new["fight_bpm_sum"]      = new.get("fight_bpm_sum", 0.0) + bpm
        new["fight_bpm_count"]    = new.get("fight_bpm_count", 0) + 1
        new["round_peak_bpm_cur"] = max(new.get("round_peak_bpm_cur", 0.0), bpm)

    # ECG points
    n_pts  = max(1, int(TICK_S * ECG_HZ))
    t0     = combat_t - TICK_S
    new_pts = [
        {"t": round(t0 + i / ECG_HZ, 3),
         "y": round(_ecg_amp(t0 + i / ECG_HZ, bpm), 4)}
        for i in range(n_pts)
    ]
    new_ecg = (ecg or []) + new_pts
    if len(new_ecg) > ECG_BUF_MAX:
        new_ecg = new_ecg[-ECG_BUF_MAX:]

    # IMU — continuo: ruido de movimiento siempre presente + impactos en fight
    new_imu = list(imu or [])
    uid_key = _combat_uid_key(state.get("athlete_id"))
    # Ruido de movimiento base (aceleración corporal, siempre visible)
    noise_intensity = round(random.uniform(0.3, 1.4), 2)
    new_imu.append({
        "t": round(combat_t, 2),
        "intensity": noise_intensity,
        "round": new["current_round"],
        "type": "ruido",
    })
    # Impacto de combate (~18% por tick durante fight ≈ un impacto cada ~3s)
    if new["phase"] == "fight" and random.random() < 0.18:
        sk_local  = new.get("sport_key", "tkd")
        cfg_local = SPORT_CONFIGS.get(sk_local, {})
        g_trunk   = cfg_local.get("scoring_g_trunk")
        g_min     = cfg_local.get("touch_g_min", 1.5)

        # TKD: ~40% toques (sub-threshold), ~60% puntuables — realistic WT match ratio
        if g_trunk is not None and random.random() < 0.40:
            intensity = round(random.uniform(g_min, g_trunk - 0.01), 2)
        else:
            intensity = round(random.uniform(g_trunk or 4.5, 14.0), 2)

        hit_type   = "dado" if random.random() < 0.55 else "recibido"
        is_scoring = g_trunk is not None and intensity >= g_trunk
        pts        = 0
        technique  = None

        if is_scoring:
            pv  = cfg_local.get("point_values",
                                {"trunk": 1, "trunk_spin": 2, "head": 3, "head_spin": 4})
            r2  = random.random()
            if r2 < 0.05:
                technique, pts = "head_spin", pv.get("head_spin", 4)
            elif r2 < 0.15:
                technique, pts = "head",      pv.get("head", 3)
            elif r2 < 0.30:
                technique, pts = "trunk_spin", pv.get("trunk_spin", 2)
            else:
                technique, pts = "trunk",     pv.get("trunk", 1)

        hit_event = {
            "t":         round(combat_t, 2),
            "intensity": intensity,
            "round":     new["current_round"],
            "type":      hit_type,
            "scoring":   is_scoring,
            "pts":       pts,
            "technique": technique,
        }
        new_imu.append(hit_event)
        _record_full_imu_hit(uid_key, hit_event)
        ri = list(new.get("round_impacts", [0]))
        if ri:
            ri[-1] += 1
        else:
            ri = [1]
        score_updates: dict = {
            "round_impacts": ri,
            "total_impacts": new.get("total_impacts", 0) + 1,
        }
        if is_scoring:
            if hit_type == "dado":
                score_updates["score_dado"]      = new.get("score_dado", 0) + pts
                score_updates["puntuables_dado"] = new.get("puntuables_dado", 0) + 1
            else:
                score_updates["score_recibido"]       = new.get("score_recibido", 0) + pts
                score_updates["puntuables_recibido"]  = new.get("puntuables_recibido", 0) + 1
        else:
            if hit_type == "dado":
                score_updates["touches_dado"]    = new.get("touches_dado", 0) + 1
            else:
                score_updates["touches_recibido"] = new.get("touches_recibido", 0) + 1
        new.update(score_updates)
    if len(new_imu) > IMU_BUF_MAX:
        new_imu = new_imu[-IMU_BUF_MAX:]

    # Full ECG accumulator — written server-side to avoid WebSocket serialization
    buf = _ecg_full_cache.get(uid_key, [])
    buf += [p for i, p in enumerate(new_pts) if i % ECG_FULL_DECIMATE == 0]
    if len(buf) > ECG_FULL_MAX:
        buf = buf[-ECG_FULL_MAX:]
    _ecg_full_cache[uid_key] = buf

    return new, new_ecg, new_imu


# ── Figures ────────────────────────────────────────────────────────────────────

_STATUS_COLOR = {
    "green": "#27c98f",
    "yellow": "#f0a832",
    "red": "#e45a5a",
    "idle": "#8fa3bf",
}


def _ecg_figure(ecg_data: list, status: str, bpm: float) -> go.Figure:
    color = _STATUS_COLOR.get(status, "#8fa3bf")
    fig   = go.Figure()

    if ecg_data:
        ts = [p["t"] for p in ecg_data]
        ys = [p["y"] for p in ecg_data]
        t_max = ts[-1]
        t_min = t_max - DISPLAY_WIN
        pairs = [(t, y) for t, y in zip(ts, ys) if t >= t_min]
        if pairs:
            xs2, ys2 = zip(*pairs)
            fig.add_trace(go.Scatter(
                x=list(xs2), y=list(ys2),
                mode="lines",
                line=dict(color=color, width=1.5),
                hoverinfo="skip",
            ))
    else:
        fig.add_trace(go.Scatter(
            x=[0, DISPLAY_WIN], y=[0, 0],
            mode="lines",
            line=dict(color=color, width=1, dash="dot"),
            hoverinfo="skip",
        ))

    apply_chart_style(fig, title="ECG en tiempo real", height=180)
    fig.update_layout(
        margin=dict(l=28, r=12, t=36, b=16),
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False,
                   range=[-0.5, 1.3]),
        showlegend=False,
    )
    return fig


_ROUND_COLORS = [
    "#2fb7c4",  # R1 — neon
    "#f0a832",  # R2 — amber
    "#27c98f",  # R3 — green
    "#8b5cf6",  # R4 — violet
    "#e45a5a",  # R5+ — punch
]


_HIT_STYLES = {
    "dado":     {"color": "#27c98f", "label": "Golpes dados",     "width": 2.0},
    "recibido": {"color": "#e45a5a", "label": "Golpes recibidos", "width": 2.0},
    "ruido":    {"color": "rgba(143,163,191,0.35)", "label": None, "width": 1.0},
}


def _stem_xy(times: list, intensities: list) -> tuple:
    """Build x/y arrays for a stem plot (vertical spike per point)."""
    xs, ys = [], []
    for t, g in zip(times, intensities):
        xs.extend([t, t, None])
        ys.extend([0.0, g, None])
    return xs, ys


def _imu_figure(imu_data: list) -> go.Figure:
    fig = go.Figure()
    if imu_data:
        groups: dict = {k: {"t": [], "g": [], "hover": []} for k in _HIT_STYLES}
        for evt in imu_data:
            hit_type = evt.get("type", "ruido")
            if hit_type not in groups:
                hit_type = "ruido"
            rnd = evt["round"]
            groups[hit_type]["t"].append(evt["t"])
            groups[hit_type]["g"].append(evt["intensity"])
            groups[hit_type]["hover"].append(
                f"R{rnd} · {evt['t']:.1f}s — {evt['intensity']:.2f} g"
            )

        for hit_type, data in groups.items():
            if not data["t"]:
                continue
            style = _HIT_STYLES[hit_type]
            xs, ys = _stem_xy(data["t"], data["g"])
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="lines",
                name=style["label"] or "",
                showlegend=style["label"] is not None,
                line=dict(color=style["color"], width=style["width"]),
                hoverinfo="skip",
            ))
            # Dot at spike tip (only for real hits)
            if hit_type != "ruido":
                fig.add_trace(go.Scatter(
                    x=data["t"], y=data["g"],
                    mode="markers",
                    name=style["label"],
                    showlegend=False,
                    marker=dict(color=style["color"], size=5, symbol="circle"),
                    customdata=data["hover"],
                    hovertemplate="%{customdata}<extra></extra>",
                ))

    apply_chart_style(fig, title="Aceleración IMU (g)", height=190)
    fig.update_layout(
        margin=dict(l=28, r=12, t=36, b=16),
        xaxis=dict(showgrid=False, title=dict(text="tiempo (s)", font=dict(size=10))),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            zeroline=True, zerolinecolor="rgba(255,255,255,0.10)",
            title=dict(text="g", font=dict(size=10)),
        ),
        showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center",
                    font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
    )
    return fig


# ── Layout helpers ─────────────────────────────────────────────────────────────

def _fmt_timer(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60}:{s % 60:02d}"


def _combat_config_card(default_sk: str,
                        athlete_opts: list | None = None) -> html.Div:
    rows: list = []
    if athlete_opts is not None:
        rows.append(html.Div([
            html.Label("Atleta", className="auth-label"),
            dcc.Dropdown(
                id="combat-athlete-select",
                options=athlete_opts,
                placeholder="Selecciona un atleta…",
                clearable=False,
                className="dash-dropdown",
            ),
        ], style={"marginBottom": "12px"}))

    rows.append(
        html.Div(style={"display": "grid",
                        "gridTemplateColumns": "1fr auto",
                        "gap": "12px",
                        "alignItems": "end"}, children=[
            html.Div([
                html.Label("Deporte / Categoría", className="auth-label"),
                dcc.Dropdown(
                    id="combat-sport",
                    options=[{"label": v["label"], "value": k}
                             for k, v in SPORT_CONFIGS.items()
                             if (default_sk == "tkd" and k == "tkd")
                             or (default_sk != "tkd" and k.startswith("box"))],
                    value=default_sk,
                    clearable=False,
                    className="dash-dropdown",
                ),
            ]),
            html.Button(
                "Iniciar combate",
                id="btn-combat-start",
                n_clicks=0,
                className="btn btn--primary",
                style={"whiteSpace": "nowrap"},
            ),
        ]),
    )
    _cfg0 = SPORT_CONFIGS.get(default_sk, SPORT_CONFIGS["tkd"])
    rows.append(html.Div(id="combat-sport-info",
                         className="text-muted",
                         style={"marginTop": "10px", "fontSize": "12px"},
                         children=(
                             f"{_cfg0['rounds']} rounds × {_cfg0['fight_s'] // 60} min combate"
                             f" | {_cfg0['rest_s']} s descanso · Reglamento oficial"
                         )))

    return html.Details(
        id="combat-config-card",
        className="card collapsible-card",
        open=True,
        children=[
            html.Summary(className="collapsible-card__summary", children=[
                html.Div(className="collapsible-card__head", children=[
                    html.Span("Configurar sesión", className="card-title"),
                    html.Span("Deporte · rounds · atleta", className="text-muted"),
                ]),
                html.Span("⌄", className="collapsible-card__chevron"),
            ]),
            html.Div(className="collapsible-card__body", children=[
                html.P(
                    "El formato de rounds se configura automáticamente según reglamento oficial.",
                    className="text-muted",
                    style={"marginBottom": "14px"},
                ),
            ] + rows),
        ],
    )


def _combat_shell(sport_key: str = "tkd") -> html.Div:
    cfg = SPORT_CONFIGS.get(sport_key, SPORT_CONFIGS["tkd"])
    gcfg = {**graph_config(), "responsive": True}
    return html.Div(
        id="combat-shell",
        className="combat-shell",
        style={"display": "none"},
        children=[
            # Header: round indicator + countdown + phase badge
            html.Div(className="combat-header-bar", children=[
                html.Div(id="combat-round-display",
                         className="combat-round-info",
                         children=f"Ronda 1 / {cfg['rounds']}"),
                html.Div(id="combat-timer-display",
                         className="combat-timer",
                         children=_fmt_timer(cfg["fight_s"])),
                html.Div(id="combat-phase-badge",
                         className="combat-phase-badge combat-phase-badge--fight",
                         children="COMBATE"),
            ]),

            # Status bar: semáforo + vitals
            html.Div(className="combat-status-bar", children=[
                html.Div(className="combat-semaforo-wrap", children=[
                    html.Div(id="combat-semaforo",
                             className="combat-semaforo combat-semaforo--idle"),
                    html.Div(id="combat-semaforo-label",
                             className="combat-semaforo-label",
                             children="—"),
                ]),
                html.Div(className="combat-vitals", children=[
                    html.Div(className="combat-vital-item", children=[
                        html.Div("BPM", className="combat-vital-label"),
                        html.Div(id="combat-bpm-val",
                                 className="combat-vital-value", children="—"),
                    ]),
                    html.Div(className="combat-vital-item", children=[
                        html.Div("RMSSD (ms)", className="combat-vital-label"),
                        html.Div(id="combat-rmssd-val",
                                 className="combat-vital-value", children="—"),
                    ]),
                    html.Div(className="combat-vital-item", children=[
                        html.Div("Impactos", className="combat-vital-label"),
                        html.Div(id="combat-impacts-val",
                                 className="combat-vital-value", children="0"),
                    ]),
                ]),
            ]),

            # Live charts
            html.Div(className="combat-charts-grid card", children=[
                dcc.Graph(id="combat-ecg-chart",
                          figure=_ecg_figure([], "idle", 70),
                          config=gcfg,
                          style={"height": "180px", "width": "100%"}),
                dcc.Graph(id="combat-imu-chart",
                          figure=_imu_figure([]),
                          config=gcfg,
                          style={"height": "180px", "width": "100%"}),
            ]),

            # Action buttons
            html.Div(className="combat-actions", children=[
                html.Button("⏭ Fin de round",
                            id="btn-combat-phase-toggle",
                            n_clicks=0, className="btn"),
                html.Button("⬛ Terminar combate",
                            id="btn-combat-stop",
                            n_clicks=0, className="btn-combat-stop"),
                html.Button("🔬 Modo demo",
                            id="btn-combat-demo",
                            n_clicks=0, className="btn btn-ghost btn-xs",
                            title="Carga datos de ejemplo para ver todos los estados del monitor"),
            ]),
        ],
    )


def _combat_summary() -> html.Div:
    return html.Div(
        id="combat-summary",
        style={"display": "none"},
        children=[html.Div(className="card", children=[
            html.H4("Resumen del combate", className="card-title"),
            html.Div(id="combat-summary-content"),
            html.Div(className="combat-actions", style={"marginTop": "16px"}, children=[
                html.Button("Nueva sesión", id="btn-new-session",
                            n_clicks=0, className="btn"),
                html.Button("⬇ PDF", id="btn-combat-pdf",
                            n_clicks=0, className="btn btn-ghost btn-xs"),
                html.Button("Guardar sesión", id="btn-save-session",
                            n_clicks=0, className="btn btn--primary"),
            ]),
        ])],
    )


def _compat_hidden() -> html.Div:
    """Legacy components required by old callbacks that still exist in the registry."""
    return html.Div(style={"display": "none"}, children=[
        dcc.Store(id="ai-report-store", data=None),
        dcc.Dropdown(id="analysis-athlete-select", options=[], value=None),
        html.Div(id="ai-note-output"),
        html.Div(id="analysis-content"),
    ])


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _classify_scoring_totals(imu_data: list, sport_key: str) -> dict:
    """Compute scoring/touch counts and estimated pts from classified IMU events."""
    hits = [e for e in (imu_data or []) if e.get("type") in ("dado", "recibido")]
    p_dado = sum(1 for e in hits if e.get("scoring") and e.get("type") == "dado")
    p_recv = sum(1 for e in hits if e.get("scoring") and e.get("type") == "recibido")
    t_dado = sum(1 for e in hits if not e.get("scoring") and e.get("type") == "dado")
    t_recv = sum(1 for e in hits if not e.get("scoring") and e.get("type") == "recibido")
    pts_dado = sum(e.get("pts", 0) for e in hits if e.get("scoring") and e.get("type") == "dado")
    pts_recv = sum(e.get("pts", 0) for e in hits if e.get("scoring") and e.get("type") == "recibido")
    return {
        "p_dado": p_dado, "p_recv": p_recv,
        "t_dado": t_dado, "t_recv": t_recv,
        "pts_dado": pts_dado, "pts_recv": pts_recv,
    }


def _build_scoring_section(state: dict, imu_data: list) -> list:
    """Return a list of Dash elements for the TKD scoring breakdown; empty list if not applicable."""
    sk  = state.get("sport_key", "tkd")
    cfg = SPORT_CONFIGS.get(sk, {})
    if "scoring_g_trunk" not in cfg:
        return []
    sc = _classify_scoring_totals(imu_data, sk)
    return [
        html.Div(className="ecg-divider ecg-divider--spaced"),
        html.Details(className="card collapsible-card", open=False, children=[
            html.Summary(className="collapsible-card__summary", children=[
                html.Div(className="collapsible-card__head", children=[
                    html.Span("Puntuación estimada (WT)", className="card-title"),
                    html.Span(
                        f"{sc['pts_dado']} pts marcados · {sc['pts_recv']} pts recibidos",
                        className="text-muted",
                    ),
                ]),
                html.Span("⌄", className="collapsible-card__chevron"),
            ]),
            html.Div(className="collapsible-card__body", children=[
                html.Div(className="combat-summary-grid", children=[
                    html.Div(className="kpi", children=[
                        html.Div("Pts marcados", className="kpi-label"),
                        html.Div(str(sc["pts_dado"]), className="kpi-value",
                                 style={"color": "#27c98f"}),
                        html.Div(className="kpi-ecg-line"),
                    ]),
                    html.Div(className="kpi", children=[
                        html.Div("Pts recibidos", className="kpi-label"),
                        html.Div(str(sc["pts_recv"]), className="kpi-value",
                                 style={"color": "#e45a5a"}),
                        html.Div(className="kpi-ecg-line"),
                    ]),
                    html.Div(className="kpi", children=[
                        html.Div("Puntuables", className="kpi-label"),
                        html.Div(f"{sc['p_dado']}D / {sc['p_recv']}R", className="kpi-value",
                                 style={"fontSize": "18px"}),
                        html.Div(className="kpi-ecg-line"),
                    ]),
                    html.Div(className="kpi", children=[
                        html.Div("Toques", className="kpi-label"),
                        html.Div(f"{sc['t_dado']}D / {sc['t_recv']}R", className="kpi-value",
                                 style={"fontSize": "18px"}),
                        html.Div(className="kpi-ecg-line"),
                    ]),
                ]),
                html.P(
                    f"Estimación WT-ESP: ≥{cfg['scoring_g_trunk']}g tronco, "
                    f"≥{cfg['scoring_g_head']}g cabeza. "
                    "Tronco=1pt, giro=2pt, cabeza=3pt, giro cabeza=4pt. "
                    "No reemplaza marcador oficial.",
                    className="text-muted",
                    style={"fontSize": "11px", "marginTop": "6px"},
                ),
            ]),
        ]),
    ]


# ── View class ─────────────────────────────────────────────────────────────────

class AnalysisView:

    def __init__(self, app: dash.Dash, db, sensors):
        self.app = app
        self.db  = db
        self.S   = sensors
        self._register_callbacks()

    # ── Layout ────────────────────────────────────────────────────────────────

    def layout(self) -> html.Div:
        uid  = session.get("user_id")
        role = str(session.get("role") or "")
        sport = str(session.get("sport") or "")

        if not uid:
            return html.Div(
                html.P("Inicia sesión para acceder al monitor.", className="text-muted"),
                className="page-content",
            )

        uid_int = int(uid)
        if role == "coach":
            return self._layout_coach(uid_int, sport)
        return self._layout_athlete(uid_int, sport)

    def _layout_athlete(self, uid: int, sport: str) -> html.Div:
        user  = self.db.get_user_by_id(uid)
        sport = sport or (user or {}).get("sport") or ""
        sk    = _sport_key_from_sport(sport)
        cfg   = SPORT_CONFIGS[sk]
        sport_label = sport.title() if sport else "Combate"

        page_head = html.Div(className="page-head", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport_label, className="session-pill"),
                html.Span("Monitor de Combate",
                          className="session-pill session-pill--muted"),
            ]),
            html.H2("Monitor de Combate"),
            html.P(
                f"Simulación ECG+IMU en tiempo real · "
                f"{cfg['rounds']} rounds × {cfg['fight_s']//60} min · "
                "Listo para BLE",
                className="text-muted",
            ),
        ])

        return html.Div([
            page_head,
            html.Div(className="ecg-divider ecg-divider--spaced"),
            _combat_config_card(sk),
            _combat_shell(sk),
            _combat_summary(),
            dcc.Store(id="combat-state",      data=_initial_state(sk, uid)),
            dcc.Store(id="combat-ecg-data",   data=[]),
            # combat-ecg-full removed — accumulated server-side in _ecg_full_cache
            dcc.Store(id="combat-imu-data",   data=[]),
            dcc.Store(id="combat-athlete-id", data=uid),
            dcc.Download(id="dl-combat-pdf"),
            dcc.Interval(id="combat-tick",
                         interval=int(TICK_S * 1000),
                         n_intervals=0, disabled=True),
            _compat_hidden(),
        ], className="page-content")

    def _layout_coach(self, coach_id: int, coach_sport: str) -> html.Div:
        athletes = self.db.list_roster_for_coach(coach_id, sport=coach_sport or None)
        opts     = [{"label": a["name"], "value": a["id"]}
                    for a in athletes if a.get("id")]
        sk       = _sport_key_from_sport(coach_sport)
        sport_label = coach_sport.title() if coach_sport else "Combate"

        page_head = html.Div(className="page-head", children=[
            html.Div(className="session-pill-row", children=[
                html.Span(sport_label, className="session-pill"),
                html.Span("Monitor · Coach",
                          className="session-pill session-pill--muted"),
            ]),
            html.H2("Monitor de Combate"),
            html.P(
                "Selecciona un atleta y configura el combate. "
                "Simulación ECG+IMU en tiempo real.",
                className="text-muted",
            ),
        ])

        return html.Div([
            page_head,
            html.Div(className="ecg-divider ecg-divider--spaced"),
            _combat_config_card(sk, athlete_opts=opts),
            _combat_shell(sk),
            _combat_summary(),
            dcc.Store(id="combat-state",      data=_initial_state(sk)),
            dcc.Store(id="combat-ecg-data",   data=[]),
            # combat-ecg-full removed — accumulated server-side in _ecg_full_cache
            dcc.Store(id="combat-imu-data",   data=[]),
            dcc.Store(id="combat-athlete-id", data=None),
            dcc.Interval(id="combat-tick",
                         interval=int(TICK_S * 1000),
                         n_intervals=0, disabled=True),
            _compat_hidden(),
        ], className="page-content")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _register_callbacks(self):

        # ── legacy compat ──────────────────────────────────────────────────

        @self.app.callback(
            Output("ai-note-output", "children"),
            Input("ai-report-store", "data"),
            prevent_initial_call=True,
        )
        def _compat_ai_note(data):
            if not data:
                raise PreventUpdate
            note = AI.generate_athlete_note(
                data.get("report", {}),
                athlete_name=data.get("athlete_name", "Atleta"),
                sport=data.get("sport", ""),
                extra=data.get("extra", {}),
            )
            return dcc.Markdown(note, className="ai-note")

        @self.app.callback(
            Output("analysis-content", "children"),
            Input("analysis-athlete-select", "value"),
            prevent_initial_call=True,
        )
        def _compat_analysis(_):
            raise PreventUpdate

        # ── sport info label ───────────────────────────────────────────────

        @self.app.callback(
            Output("combat-sport-info", "children"),
            Input("combat-sport", "value"),
            prevent_initial_call=True,
        )
        def update_sport_info(sk):
            if not sk or sk not in SPORT_CONFIGS:
                return ""
            cfg = SPORT_CONFIGS[sk]
            return (
                f"{cfg['rounds']} rounds × {cfg['fight_s'] // 60} min combate"
                f" | {cfg['rest_s']} s descanso · Reglamento oficial"
            )

        # ── coach athlete selector ─────────────────────────────────────────

        @self.app.callback(
            Output("combat-athlete-id", "data"),
            Input("combat-athlete-select", "value"),
            prevent_initial_call=True,
        )
        def update_athlete_id(athlete_id):
            return athlete_id if athlete_id else no_update

        # ── start combat ───────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state", "data"),
            Output("combat-tick", "disabled"),
            Input("btn-combat-start", "n_clicks"),
            State("combat-sport", "value"),
            State("combat-athlete-id", "data"),
            prevent_initial_call=True,
        )
        def start_combat(n, sport_key, athlete_id):
            if not n:
                raise PreventUpdate
            _reset_combat_caches(_combat_uid_key(athlete_id))
            sk = sport_key or "tkd"
            st = _initial_state(sk, athlete_id)
            st.update({"active": True, "phase": "fight"})
            return st, False        # enable interval

        # ── tick advance ───────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state",    "data", allow_duplicate=True),
            Output("combat-ecg-data", "data"),
            Output("combat-imu-data", "data"),
            Output("combat-tick",     "disabled", allow_duplicate=True),
            Input("combat-tick", "n_intervals"),
            State("combat-state",    "data"),
            State("combat-ecg-data", "data"),
            State("combat-imu-data", "data"),
            prevent_initial_call=True,
        )
        def tick_advance(_, state, ecg, imu):
            if not state or not state.get("active"):
                raise PreventUpdate
            ns, ne, ni = _advance_tick(state, ecg or [], imu or [])
            return ns, ne, ni, not bool(ns.get("active"))

        # ── phase toggle ───────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state", "data", allow_duplicate=True),
            Input("btn-combat-phase-toggle", "n_clicks"),
            State("combat-state", "data"),
            prevent_initial_call=True,
        )
        def phase_toggle(n, state):
            if not n or not state or not state.get("active"):
                raise PreventUpdate
            new = dict(state)
            if state["phase"] == "fight":
                _finalize_round(new, state["current_round"])
                new["rounds_completed"] = state.get("rounds_completed", 0) + 1
                if state["current_round"] >= state["total_rounds"]:
                    new.update({"phase": "summary", "active": False, "elapsed_s": 0.0})
                else:
                    new.update({"phase": "rest", "elapsed_s": 0.0})
            elif state["phase"] == "rest":
                nr = state["current_round"] + 1
                new.update({
                    "phase": "fight", "elapsed_s": 0.0, "current_round": nr,
                    "round_impacts":    state.get("round_impacts", [0]) + [0],
                    "fight_bpm_sum":    0.0,
                    "fight_bpm_count":  0,
                    "round_peak_bpm_cur": 0.0,
                })
            return new

        # ── stop combat ────────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state", "data",          allow_duplicate=True),
            Output("combat-tick",  "disabled",      allow_duplicate=True),
            Input("btn-combat-stop", "n_clicks"),
            State("combat-state", "data"),
            prevent_initial_call=True,
        )
        def stop_combat(n, state):
            if not n or not state:
                raise PreventUpdate
            new = dict(state)
            if state.get("phase") == "fight":
                _finalize_round(new, state.get("current_round", 1))
            new.update({"active": False, "phase": "summary"})
            return new, True

        # ── demo mode ──────────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state",    "data",     allow_duplicate=True),
            Output("combat-ecg-data", "data",     allow_duplicate=True),
            Output("combat-imu-data", "data",     allow_duplicate=True),
            Output("combat-tick",     "disabled", allow_duplicate=True),
            Input("btn-combat-demo", "n_clicks"),
            State("combat-sport",      "value"),
            State("combat-athlete-id", "data"),
            prevent_initial_call=True,
        )
        def load_demo(n, sport_key, athlete_id):
            if not n:
                raise PreventUpdate
            st, ecg, imu = _demo_data(sport_key or "tkd", athlete_id)
            uid_key = _combat_uid_key(athlete_id)
            _ecg_full_cache[uid_key] = [
                p for i, p in enumerate(ecg) if i % ECG_FULL_DECIMATE == 0
            ][-ECG_FULL_MAX:]
            _imu_full_cache[uid_key] = [
                e for e in imu if e.get("type") in ("dado", "recibido")
            ][-IMU_FULL_MAX:]
            return st, ecg, imu, False

        # ── new session ────────────────────────────────────────────────────

        @self.app.callback(
            Output("combat-state",    "data",     allow_duplicate=True),
            Output("combat-ecg-data", "data",     allow_duplicate=True),
            Output("combat-imu-data", "data",     allow_duplicate=True),
            Output("combat-tick",     "disabled", allow_duplicate=True),
            Input("btn-new-session", "n_clicks"),
            State("combat-sport",       "value"),
            State("combat-athlete-id",  "data"),
            prevent_initial_call=True,
        )
        def new_session(n, sport_key, athlete_id):
            if not n:
                raise PreventUpdate
            _reset_combat_caches(_combat_uid_key(athlete_id))
            return _initial_state(sport_key or "tkd", athlete_id), [], [], True

        # ── save session ───────────────────────────────────────────────────

        _ECG_DIR = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "ecg"
        )

        @self.app.callback(
            Output("combat-summary-content", "children", allow_duplicate=True),
            Input("btn-save-session",  "n_clicks"),
            State("combat-state",      "data"),
            State("combat-athlete-id", "data"),
            State("combat-imu-data",   "data"),
            prevent_initial_call=True,
        )
        def save_session(n, state, athlete_id, imu_data):
            if not n or not state:
                raise PreventUpdate
            if str(session.get("role") or "") == "coach" and not athlete_id:
                return html.P(
                    "Selecciona un atleta antes de guardar la sesión.",
                    className="text-muted",
                )
            uid = athlete_id or session.get("user_id")
            if not uid:
                return html.P("No autenticado.", className="text-muted")
            uid_key = _combat_uid_key(state.get("athlete_id") or uid)
            ecg_data = (
                _ecg_full_cache.get(uid_key)
                or _ecg_full_cache.get(_combat_uid_key(state.get("athlete_id")))
                or []
            )
            imu_source = _imu_full_cache.get(uid_key) or _full_imu_data(state, imu_data)
            try:
                save_warnings = []
                sk = state.get("sport_key", "tkd")
                label = SPORT_CONFIGS.get(sk, {}).get("label", "Combate")
                rounds_done = state.get("rounds_completed", 0)
                peak_bpm = state.get("peak_bpm", 0)
                total_impact = state.get("total_impacts", 0)
                notes = (
                    f"Combat Monitor - {label} | "
                    f"{rounds_done} rounds | "
                    f"Peak BPM {peak_bpm:.0f} | "
                    f"{total_impact} impactos"
                )

                session_id = _db.create_session(
                    athlete_id=int(uid),
                    created_by=int(session.get("user_id") or uid),
                    sport=label,
                    notes=notes,
                )

                if ecg_data and len(ecg_data) >= 10 and session_id:
                    try:
                        os.makedirs(_ECG_DIR, exist_ok=True)
                        ts_tag = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                        ecg_fname = f"combat_{uid}_{ts_tag}.csv"
                        ecg_path = os.path.join(_ECG_DIR, ecg_fname)
                        with open(ecg_path, "w", newline="", encoding="utf-8") as fh:
                            fh.write("time,ecg\n")
                            for pt in ecg_data:
                                fh.write(f"{pt['t']:.4f},{pt['y']:.4f}\n")
                        _db.add_ecg_file(
                            uid=int(uid),
                            filename=ecg_fname,
                            fs=ECG_FULL_HZ,
                            session_id=session_id,
                        )
                    except Exception as exc:
                        save_warnings.append(f"ECG no se pudo guardar: {exc}")

                if imu_source and session_id:
                    try:
                        hit_events = [
                            e for e in (imu_source or [])
                            if e.get("type") in ("dado", "recibido")
                        ]
                        metric_events = hit_events or (imu_source or [])
                        n_hits = len(hit_events)
                        combat_t = state.get("combat_t", 1) or 1
                        hpm = round(n_hits / (combat_t / 60), 2)
                        intensities = [e.get("intensity", 0) for e in metric_events]
                        mean_int = round(sum(intensities) / len(intensities), 2) if intensities else 0.0
                        max_int = round(max(intensities), 2) if intensities else 0.0
                        ts_tag2 = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                        imu_stem = f"combat_{uid}_{ts_tag2}_imu"
                        _db.save_imu_metrics(
                            user_id=int(uid),
                            filename=imu_stem,
                            n_hits=n_hits,
                            hits_per_min=hpm,
                            mean_int_g=mean_int,
                            max_int_g=max_int,
                            session_id=session_id,
                        )
                        try:
                            os.makedirs(_ECG_DIR, exist_ok=True)
                            hits = [e for e in imu_source if e.get("type") in ("dado", "recibido")]
                            noise = [e for e in (imu_data or []) if e.get("type") == "ruido"]
                            noise_sampled = noise[::4]
                            sidecar = sorted(hits + noise_sampled, key=lambda e: e.get("t", 0))
                            imu_path = os.path.join(_ECG_DIR, f"{imu_stem}.json")
                            with open(imu_path, "w", encoding="utf-8") as fh:
                                json.dump(sidecar, fh, ensure_ascii=False)
                        except Exception as exc:
                            save_warnings.append(f"IMU sidecar no se pudo guardar: {exc}")
                    except Exception as exc:
                        save_warnings.append(f"IMU no se pudo guardar: {exc}")

                if session_id:
                    try:
                        _db.close_session(session_id)
                    except Exception as exc:
                        save_warnings.append(f"La sesión se creó pero no se pudo cerrar: {exc}")

                sid_str = f" #{session_id}" if session_id else ""
                warning_block = []
                if save_warnings:
                    warning_block.append(
                        html.Div(
                            [
                                html.Div(
                                    "Se guardó la sesión, pero hubo advertencias:",
                                    style={"fontWeight": "600", "marginBottom": "6px"},
                                ),
                                html.Ul(
                                    [html.Li(msg) for msg in save_warnings],
                                    style={"margin": "0 0 0 18px", "padding": "0"},
                                ),
                            ],
                            style={
                                "marginTop": "10px",
                                "padding": "10px 12px",
                                "borderRadius": "10px",
                                "background": "rgba(240,168,50,0.12)",
                                "border": "1px solid rgba(240,168,50,0.35)",
                                "color": "var(--ink)",
                                "fontSize": "12px",
                            },
                        )
                    )
                return html.Div([
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "10px", "marginBottom": "6px"},
                        children=[
                            html.Span("OK", style={"fontSize": "20px", "color": "var(--neon)"}),
                            html.Span(
                                f"Sesión{sid_str} guardada en el historial.",
                                style={"fontWeight": "600", "color": "var(--neon)", "fontSize": "14px"},
                            ),
                        ],
                    ),
                    html.P(
                        f"{label} | {rounds_done} rounds | Pico {peak_bpm:.0f} BPM | {total_impact} impactos",
                        className="text-muted",
                        style={"fontSize": "12px", "margin": "0"},
                    ),
                    *warning_block,
                ])
            except Exception as exc:
                return html.P(f"No se pudo guardar: {exc}", className="text-muted")

        @self.app.callback(
            Output("dl-combat-pdf", "data"),
            Input("btn-combat-pdf", "n_clicks"),
            State("combat-state",    "data"),
            State("combat-imu-data", "data"),
            prevent_initial_call=True,
        )
        def download_combat_pdf(n, state, imu_data):
            if not n or not state:
                raise PreventUpdate
            try:
                from report_utils import CombatIQPDF, safe_filename_stem
                from reportlab.lib.units import cm as _cm
            except ImportError:
                return dcc.send_string(
                    "Instala reportlab: pip install reportlab\n", "instalar_reportlab.txt"
                )

            sk          = state.get("sport_key", "tkd")
            sk_label    = SPORT_CONFIGS.get(sk, {}).get("label", "Combate")
            rounds_done = state.get("rounds_completed", state.get("current_round", 1))
            peak_bpm    = state.get("peak_bpm", 0.0)
            total_dur   = state.get("combat_t", 0.0)
            round_stats = state.get("round_stats", [])
            imu_report_data = _full_imu_data(state, imu_data)

            # Athlete info from combat state; coaches export for the selected athlete.
            from flask import session as _fsess
            _uid = state.get("athlete_id") or _fsess.get("user_id")
            _athlete_name = "Deportista"
            try:
                if _uid:
                    _ath = self.db.get_user_by_id(int(_uid))
                    if _ath:
                        _athlete_name = _ath.get("name") or "Deportista"
            except Exception:
                pass

            # IMU hits per round
            round_hits: dict = {}
            for evt in (imu_report_data or []):
                if evt.get("type") not in ("dado", "recibido"):
                    continue
                rnd = evt.get("round", 1)
                if rnd not in round_hits:
                    round_hits[rnd] = {"dado": 0, "recibido": 0}
                round_hits[rnd][evt["type"]] += 1

            total_dado     = sum(v["dado"]     for v in round_hits.values())
            total_recibido = sum(v["recibido"] for v in round_hits.values())
            total_impacts  = total_dado + total_recibido

            dur_min = int(total_dur // 60)
            dur_sec = int(total_dur % 60)
            dur_str = f"{dur_min}:{dur_sec:02d}"

            cfg = SPORT_CONFIGS.get(sk, {})
            n_rounds_cfg = cfg.get("rounds", "?")
            fight_s_cfg  = cfg.get("fight_s", 0)
            rest_s_cfg   = cfg.get("rest_s", 0)

            from datetime import datetime as _dt
            pdf = CombatIQPDF()
            pdf.header(
                "Resumen de Combate",
                f"{sk_label} · {rounds_done} round{'s' if rounds_done != 1 else ''} completado{'s' if rounds_done != 1 else ''}",
                _athlete_name,
                sk_label,
                session=f"Duración total: {dur_str}",
                source=f"CombatIQ — {_dt.now().strftime('%d/%m/%Y %H:%M')}",
            )

            # Status badge based on peak BPM
            if peak_bpm >= 170:
                _bpm_status, _bpm_label = "alert", f"Zona de esfuerzo máximo — pico {peak_bpm:.0f} lpm"
            elif peak_bpm >= 150:
                _bpm_status, _bpm_label = "warn",  f"Zona de alta intensidad — pico {peak_bpm:.0f} lpm"
            else:
                _bpm_status, _bpm_label = "ok",    f"Zona moderada-alta — pico {peak_bpm:.0f} lpm"
            pdf.status_badge(_bpm_label, _bpm_status)

            pdf.metric_row([
                {"label": "Rondas",       "value": str(rounds_done),  "unit": f"de {n_rounds_cfg}"},
                {"label": "Pico BPM",     "value": f"{peak_bpm:.0f}", "unit": "lpm"},
                {"label": "Impactos IMU", "value": str(total_impacts), "unit": f"{total_dado}D / {total_recibido}R"},
                {"label": "Duración",     "value": dur_str,            "unit": "min:seg"},
            ])
            pdf.spacer(0.18)

            # Context card — lectura del combate
            bpm_interp = (
                "FC pico muy alta (>170 lpm): esfuerzo máximo registrado. Vigilar recuperación post-combate."
                if peak_bpm >= 170 else
                "FC pico alta (150-170 lpm): intensidad elevada, dentro del rango esperado para combate."
                if peak_bpm >= 150 else
                "FC pico moderada (<150 lpm): carga cardiovascular controlada en esta sesión."
            )
            imu_interp = (
                f"IMU: {total_impacts} impactos registrados — {total_dado} dados, {total_recibido} recibidos. "
                "Los datos IMU capturan acciones de impacto mediante acelerómetro; no es un conteo oficial de puntos."
            )
            pdf.card(
                "Lectura del combate",
                [bpm_interp, imu_interp],
                subtitle=f"Reglamento: {n_rounds_cfg} rounds × {fight_s_cfg//60} min combate | {rest_s_cfg}s descanso",
            )
            pdf.spacer(0.12)

            # Round breakdown table
            pdf.section_title("Desglose por round", "Impactos IMU y frecuencia cardíaca por ronda")
            if round_stats or round_hits:
                rs_map = {r["round"]: r for r in round_stats}
                all_rounds = sorted(set(list(round_hits.keys()) + list(rs_map.keys())))
                rows = []
                for rnd in all_rounds:
                    d  = round_hits.get(rnd, {}).get("dado", 0)
                    rv = round_hits.get(rnd, {}).get("recibido", 0)
                    es = rs_map.get(rnd, {})
                    avg_v  = es.get("avg_bpm")
                    peak_v = es.get("peak_bpm")
                    rows.append([
                        f"Round {rnd}",
                        str(d),
                        str(rv),
                        str(d + rv),
                        f"{avg_v:.0f}" if isinstance(avg_v, (int, float)) else "—",
                        f"{peak_v:.0f}" if isinstance(peak_v, (int, float)) else "—",
                    ])
                all_avgs  = [r["avg_bpm"]  for r in round_stats if isinstance(r.get("avg_bpm"),  (int, float))]
                all_peaks = [r["peak_bpm"] for r in round_stats if isinstance(r.get("peak_bpm"), (int, float))]
                rows.append([
                    "TOTAL",
                    str(total_dado),
                    str(total_recibido),
                    str(total_impacts),
                    f"~{sum(all_avgs)/len(all_avgs):.0f}" if all_avgs else "—",
                    f"{max(all_peaks):.0f}" if all_peaks else "—",
                ])
                pdf.table(
                    ["Round", "Dados", "Recibidos", "Total", "BPM prom.", "BPM pico"],
                    rows,
                    col_widths=[2.4*_cm, 2.2*_cm, 2.8*_cm, 2.2*_cm, 3.0*_cm, 2.8*_cm],
                )
            else:
                pdf.card("Sin datos de round", [
                    "No se registraron datos de round en esta sesión.",
                    "Activa el simulador de combate con sensores para ver el desglose.",
                ])

            # Scoring breakdown (TKD / sports with IMU scoring thresholds)
            cfg_s2 = SPORT_CONFIGS.get(sk, {})
            if "scoring_g_trunk" in cfg_s2:
                sc = _classify_scoring_totals(imu_report_data, sk)
                pdf.spacer(0.15)
                pdf.section_title(
                    "Puntuación estimada (WT)",
                    "Basado en umbrales WT-ESP · no reemplaza marcador oficial",
                )
                pdf.metric_row([
                    {"label": "Pts marcados",     "value": str(sc["pts_dado"]),
                     "unit": f"{sc['p_dado']} puntuables"},
                    {"label": "Pts recibidos",    "value": str(sc["pts_recv"]),
                     "unit": f"{sc['p_recv']} puntuables"},
                    {"label": "Toques dados",     "value": str(sc["t_dado"]),
                     "unit": "sin puntuar"},
                    {"label": "Toques recibidos", "value": str(sc["t_recv"]),
                     "unit": "sin puntuar"},
                ])
                pdf.spacer(0.12)
                pdf.card(
                    "Metodología WT-ESP",
                    [
                        f"Umbral tronco: ≥{cfg_s2['scoring_g_trunk']}g "
                        "(≈54-59N sobre el hogu, sistema electrónico WT).",
                        f"Umbral cabeza: ≥{cfg_s2['scoring_g_head']}g "
                        "(sin mínimo de fuerza oficial en WT; valor de referencia práctico).",
                        "Puntos estimados por técnica: patada tronco=1pt, giro tronco=2pt, "
                        "cabeza=3pt, giro cabeza=4pt.",
                        "Los valores son estimaciones de la simulación IMU; "
                        "el marcador oficial requiere el sistema hogu homologado WT.",
                    ],
                    subtitle="Referencia: Reglamento Competición World Taekwondo 2023",
                )

            # IMU legend
            pdf.spacer(0.15)
            pdf.card(
                "Glosario",
                [
                    "Dados: impactos detectados por el sensor como acciones ofensivas (golpe o patada ejecutada).",
                    "Recibidos: impactos detectados como acciones recibidas por el deportista.",
                    "BPM prom.: frecuencia cardíaca media durante el round (requiere ECG activo).",
                    "BPM pico: frecuencia cardíaca máxima del round (requiere ECG activo).",
                ],
                subtitle="Los datos IMU son indicativos y requieren validación con el coach.",
                accent=None,
            )

            # Post-combat recommendations
            pdf.spacer(0.15)
            rec_lines = []
            if peak_bpm >= 170:
                rec_lines.append("FC muy alta: priorizar recuperación activa (hidratación, estiramientos, 24-48h sin impacto).")
            elif peak_bpm >= 150:
                rec_lines.append("FC alta: recuperación normal; monitorear cómo responde el deportista al día siguiente.")
            else:
                rec_lines.append("FC moderada: buena señal de control de carga; el deportista puede mantener el volumen planificado.")
            if total_impacts > 60:
                rec_lines.append("Carga de impacto elevada: revisar protección y técnica antes de la próxima sesión de esparring.")
            elif total_impacts > 30:
                rec_lines.append("Carga de impacto media: adecuado para sesiones de entrenamiento regular.")
            else:
                rec_lines.append("Carga de impacto baja: útil para sesiones técnicas o de readaptación.")
            pdf.card(
                "Sugerencias post-sesión",
                rec_lines,
                subtitle="Orientaciones basadas en los datos registrados. No sustituyen criterio del entrenador.",
                accent=None,
            )

            pdf_bytes = pdf.finish()
            safe_label = safe_filename_stem(sk_label, "combate")
            fname = f"CombatIQ_combate_{safe_label}_{_dt.now().strftime('%Y%m%d_%H%M')}.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), fname)

        # ── render all display from stores ─────────────────────────────────

        @self.app.callback(
            Output("combat-config-card",        "style"),
            Output("combat-shell",              "style"),
            Output("combat-summary",            "style"),
            Output("combat-timer-display",      "children"),
            Output("combat-round-display",      "children"),
            Output("combat-phase-badge",        "children"),
            Output("combat-phase-badge",        "className"),
            Output("combat-semaforo",           "className"),
            Output("combat-semaforo-label",     "children"),
            Output("combat-bpm-val",            "children"),
            Output("combat-rmssd-val",          "children"),
            Output("combat-impacts-val",        "children"),
            Output("combat-ecg-chart",          "figure"),
            Output("combat-imu-chart",          "figure"),
            Output("combat-summary-content",    "children"),
            Output("btn-combat-phase-toggle",   "children"),
            Input("combat-state",    "data"),
            State("combat-ecg-data", "data"),
            State("combat-imu-data", "data"),
            prevent_initial_call=True,
        )
        def render_combat_ui(state, ecg_data, imu_data):
            if not state:
                state = _initial_state("tkd")

            phase  = state.get("phase",  "idle")
            active = state.get("active", False)

            # Visibility
            if phase == "idle":
                cfg_s  = {}
                shl_s  = {"display": "none"}
                smy_s  = {"display": "none"}
            elif phase == "summary":
                cfg_s  = {"display": "none"}
                shl_s  = {"display": "none"}
                smy_s  = {}
            else:                                   # fight | rest
                cfg_s  = {"display": "none"}
                shl_s  = {}
                smy_s  = {"display": "none"}

            # Timer countdown
            elapsed   = state.get("elapsed_s", 0.0)
            fight_s   = state.get("fight_s",   120)
            rest_s    = state.get("rest_s",    60)
            if phase == "fight":
                remaining = fight_s - elapsed
            elif phase == "rest":
                remaining = rest_s - elapsed
            else:
                remaining = fight_s
            timer_str = _fmt_timer(remaining)

            # Round
            cur_r  = state.get("current_round",  1)
            tot_r  = state.get("total_rounds",   3)
            round_str = f"Ronda {cur_r} / {tot_r}"

            # Phase badge + toggle label
            if phase == "fight":
                badge_txt  = "COMBATE"
                badge_cls  = "combat-phase-badge combat-phase-badge--fight"
                toggle_lbl = "⏭ Fin de round"
            elif phase == "rest":
                badge_txt  = "DESCANSO"
                badge_cls  = "combat-phase-badge combat-phase-badge--rest"
                toggle_lbl = "▶ Siguiente round"
            else:
                badge_txt  = "EN ESPERA"
                badge_cls  = "combat-phase-badge combat-phase-badge--idle"
                toggle_lbl = "⏭ Siguiente"

            # Semáforo
            st_val   = state.get("status", "idle")
            sem_cls  = f"combat-semaforo combat-semaforo--{st_val}"
            sem_lbl  = {"green": "NORMAL", "yellow": "ATENCIÓN",
                        "red": "CRÍTICO", "idle": "—"}.get(st_val, "—")

            # Vitals
            bpm     = state.get("current_bpm",   0.0)
            rmssd   = state.get("current_rmssd", 0.0)
            impacts = state.get("total_impacts",  0)
            bpm_s   = f"{bpm:.0f}"   if active else "—"
            rmssd_s = f"{rmssd:.0f}" if active else "—"
            sk_live  = state.get("sport_key", "tkd")
            cfg_live = SPORT_CONFIGS.get(sk_live, {})
            if active and "scoring_g_trunk" in cfg_live:
                _sp = state.get("score_dado", 0)
                _sr = state.get("score_recibido", 0)
                impacts_s = html.Span([
                    html.Span(f"↑{_sp}", style={"color": "#27c98f", "fontWeight": "700"}),
                    html.Span(" / "),
                    html.Span(f"↓{_sr}", style={"color": "#e45a5a", "fontWeight": "700"}),
                    html.Span(" pts", style={"fontSize": "11px", "color": "var(--muted)"}),
                ])
            else:
                impacts_s = str(impacts)

            # Charts
            ecg_fig = _ecg_figure(ecg_data or [], st_val, bpm)
            imu_fig = _imu_figure(imu_data or [])

            # Summary content
            if phase == "summary":
                sk_label    = SPORT_CONFIGS.get(state.get("sport_key", "tkd"), {}).get("label", "Combate")
                rounds_done = state.get("rounds_completed", cur_r)
                peak_bpm    = state.get("peak_bpm", bpm)
                total_dur   = state.get("combat_t", 0.0)
                summary_imu_data = _full_imu_data(state, imu_data)

                # ── Round breakdown: IMU hits + ECG stats per round ────────
                round_hits: dict = {}
                for evt in (summary_imu_data or []):
                    htype = evt.get("type")
                    if htype not in ("dado", "recibido"):
                        continue
                    rnd = evt.get("round", 1)
                    if rnd not in round_hits:
                        round_hits[rnd] = {"dado": 0, "recibido": 0}
                    round_hits[rnd][htype] += 1

                round_stats_map = {rs["round"]: rs
                                   for rs in state.get("round_stats", [])}

                _COL = "gridTemplateColumns"
                _HDR = {"fontSize": "11px", "color": "var(--muted)",
                        "textTransform": "uppercase", "letterSpacing": "0.05em"}
                _COLS = "50px 1fr 1fr 68px 68px"

                all_rounds = sorted(set(list(round_hits.keys()) +
                                        list(round_stats_map.keys())))
                round_rows = []
                for rnd in all_rounds:
                    d      = round_hits.get(rnd, {}).get("dado", 0)
                    r_hits = round_hits.get(rnd, {}).get("recibido", 0)
                    ecg_s  = round_stats_map.get(rnd, {})
                    avg_v  = ecg_s.get("avg_bpm")
                    peak_v = ecg_s.get("peak_bpm")
                    avg_s  = f"{avg_v:.0f}" if isinstance(avg_v, (int, float)) else "—"
                    peak_s = f"{peak_v:.0f}" if isinstance(peak_v, (int, float)) else "—"
                    round_rows.append(
                        html.Div(
                            style={"display": "grid", _COL: _COLS,
                                   "gap": "8px", "alignItems": "center",
                                   "padding": "6px 0",
                                   "borderBottom": "1px solid var(--line)"},
                            children=[
                                html.Span(f"R{rnd}",
                                          style={"fontWeight": "700",
                                                 "color": "var(--muted)",
                                                 "fontSize": "13px"}),
                                html.Span(
                                    [html.Span("● ", style={"color": "#27c98f"}),
                                     f"{d} dados"],
                                    style={"fontSize": "13px"}),
                                html.Span(
                                    [html.Span("● ", style={"color": "#e45a5a"}),
                                     f"{r_hits} recibidos"],
                                    style={"fontSize": "13px"}),
                                html.Span(avg_s,
                                          style={"fontSize": "13px",
                                                 "color": "var(--neon)",
                                                 "fontWeight": "600",
                                                 "textAlign": "right"}),
                                html.Span(peak_s,
                                          style={"fontSize": "13px",
                                                 "color": "var(--amber)",
                                                 "fontWeight": "600",
                                                 "textAlign": "right"}),
                            ],
                        )
                    )

                # Overall ECG averages
                all_ecg = list(round_stats_map.values())
                if all_ecg:
                    ovr_avg  = round(sum(r["avg_bpm"]  for r in all_ecg) / len(all_ecg), 1)
                    ovr_peak = round(max(r["peak_bpm"] for r in all_ecg), 1)
                    round_rows.append(
                        html.Div(
                            style={"display": "grid", _COL: _COLS,
                                   "gap": "8px", "alignItems": "center",
                                   "padding": "6px 0",
                                   "borderTop": "2px solid var(--line)",
                                   "marginTop": "2px"},
                            children=[
                                html.Span("Total",
                                          style={"fontWeight": "700",
                                                 "color": "var(--ink)",
                                                 "fontSize": "12px"}),
                                html.Span(
                                    [html.Span("● ", style={"color": "#27c98f"}),
                                     f"{sum(v.get('dado', 0) for v in round_hits.values())} dados"],
                                    style={"fontSize": "12px"}),
                                html.Span(
                                    [html.Span("● ", style={"color": "#e45a5a"}),
                                     f"{sum(v.get('recibido', 0) for v in round_hits.values())} recibidos"],
                                    style={"fontSize": "12px"}),
                                html.Span(f"~{ovr_avg:.0f}",
                                          style={"fontSize": "12px",
                                                 "color": "var(--neon)",
                                                 "fontWeight": "700",
                                                 "textAlign": "right"}),
                                html.Span(f"{ovr_peak:.0f}",
                                          style={"fontSize": "12px",
                                                 "color": "var(--amber)",
                                                 "fontWeight": "700",
                                                 "textAlign": "right"}),
                            ],
                        )
                    )

                breakdown_block = html.Div([
                    html.Div(
                        style={"display": "grid", _COL: _COLS,
                               "gap": "8px", "padding": "4px 0 6px",
                               "borderBottom": "2px solid var(--line)",
                               "marginBottom": "2px"},
                        children=[
                            html.Span("Round",    style=_HDR),
                            html.Span("Dados",    style=_HDR),
                            html.Span("Recibidos",style=_HDR),
                            html.Span("BPM prom.",
                                      style={**_HDR, "textAlign": "right"}),
                            html.Span("BPM pico",
                                      style={**_HDR, "textAlign": "right"}),
                        ],
                    ),
                ] + round_rows) if round_rows else html.P("Sin datos de combate.",
                                                           className="text-muted",
                                                           style={"fontSize": "13px"})

                # ── IMU final chart ─────────────────────────────────────────
                gcfg = {**graph_config(), "responsive": True}
                imu_summary_fig = _imu_figure(summary_imu_data or [])

                smry_content = html.Div([
                    html.P(sk_label, className="text-muted",
                           style={"marginBottom": "12px"}),
                    # KPI grid
                    html.Div(className="combat-summary-grid", children=[
                        html.Div(className="kpi", children=[
                            html.Div("Rondas",      className="kpi-label"),
                            html.Div(str(rounds_done), className="kpi-value"),
                            html.Div(className="kpi-ecg-line"),
                        ]),
                        html.Div(className="kpi", children=[
                            html.Div("Pico BPM",    className="kpi-label"),
                            html.Div(f"{peak_bpm:.0f}", className="kpi-value"),
                            html.Div(className="kpi-ecg-line"),
                        ]),
                        html.Div(className="kpi", children=[
                            html.Div("Impactos IMU", className="kpi-label"),
                            html.Div(str(impacts),   className="kpi-value"),
                            html.Div(className="kpi-ecg-line"),
                        ]),
                        html.Div(className="kpi", children=[
                            html.Div("Duración",    className="kpi-label"),
                            html.Div(_fmt_timer(total_dur), className="kpi-value"),
                            html.Div(className="kpi-ecg-line"),
                        ]),
                    ]),
                    # Round breakdown
                    html.Div(className="ecg-divider ecg-divider--spaced"),
                    html.H4("Desglose por round",
                            style={"fontSize": "13px", "marginBottom": "8px",
                                   "color": "var(--muted)", "fontWeight": "600",
                                   "textTransform": "uppercase",
                                   "letterSpacing": "0.06em"}),
                    breakdown_block,
                    # IMU chart
                    html.Div(className="ecg-divider ecg-divider--spaced"),
                    html.H4("Impactos de combate",
                            style={"fontSize": "13px", "marginBottom": "4px",
                                   "color": "var(--muted)", "fontWeight": "600",
                                   "textTransform": "uppercase",
                                   "letterSpacing": "0.06em"}),
                    dcc.Graph(
                        figure=imu_summary_fig,
                        config=gcfg,
                        style={"height": "200px", "width": "100%"},
                    ),
                    # ── Scoring breakdown (TKD / sports with IMU thresholds) ──
                    *_build_scoring_section(state, summary_imu_data),
                ])
            else:
                smry_content = no_update

            return (
                cfg_s, shl_s, smy_s,
                timer_str, round_str,
                badge_txt, badge_cls,
                sem_cls, sem_lbl,
                bpm_s, rmssd_s, impacts_s,
                ecg_fig, imu_fig,
                smry_content,
                toggle_lbl,
            )
