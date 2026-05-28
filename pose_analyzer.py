"""
pose_analyzer.py — Análisis de postura con MediaPipe Tasks API (v0.10+).

Uso:
    from pose_analyzer import analyze_video
    result = analyze_video("assets/uploads/combate.mp4")
"""

import os
import base64
import time
import logging
import numpy as np


import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.core import base_options as base_opts

# Ruta al modelo (debe estar junto a este archivo)
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pose_landmarker_lite.task")
_logger = logging.getLogger(__name__)
_ANALYZER_VERSION = "chamber_angle_v1_2026_05_28"
try:
    _DUEL_KEYFRAME_CANDIDATES = max(6, int(os.getenv("COMBATIQ_DUEL_KEYFRAME_CANDIDATES", "48") or 48))
except (TypeError, ValueError):
    _DUEL_KEYFRAME_CANDIDATES = 48

# IDs de landmarks MediaPipe Pose BlazePose
_L = {
    "L_shoulder": 11, "R_shoulder": 12,
    "L_elbow": 13,    "R_elbow": 14,
    "L_wrist": 15,    "R_wrist": 16,
    "L_hip": 23,      "R_hip": 24,
    "L_knee": 25,     "R_knee": 26,
    "L_ankle": 27,    "R_ankle": 28,
}

# Conexiones para dibujar el skeleton (pares de índice)
_POSE_CONNECTIONS = [
    (11,13),(13,15),  # brazo izq
    (12,14),(14,16),  # brazo der
    (11,12),          # hombros
    (11,23),(12,24),  # torso
    (23,24),          # caderas
    (23,25),(25,27),  # pierna izq
    (24,26),(26,28),  # pierna der
]

_TARGET_LABELS = {
    "auto": "Automático",
    "red": "Peto rojo",
    "blue": "Peto azul",
    "duel": "Rojo vs azul",
    "left": "Atleta izquierda",
    "right": "Atleta derecha",
}

_ANGLE_KEYS = (
    "knee_l", "knee_r", "elbow_l", "elbow_r",
    "hip_l", "hip_r", "shoulder_l", "shoulder_r",
)


# ── Geometría ────────────────────────────────────────────────────────────────

def _angle(a, b, c) -> float:
    a, b, c = np.array(a, dtype=float), np.array(b, dtype=float), np.array(c, dtype=float)
    ba, bc  = a - b, c - b
    norm    = np.linalg.norm(ba) * np.linalg.norm(bc)
    if norm < 1e-8:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(ba, bc) / norm, -1.0, 1.0))))


def _lm_xy(landmarks, name: str, w: int, h: int) -> tuple:
    lm = landmarks[_L[name]]
    return (lm.x * w, lm.y * h)


def _extract_angles(landmarks, w: int, h: int, validity: dict | None = None) -> dict:
    xy = lambda name: _lm_xy(landmarks, name, w, h)
    def joint(a: str, b: str, c: str):
        if validity and not (validity.get(a, True) and validity.get(b, True) and validity.get(c, True)):
            return None
        return round(_angle(xy(a), xy(b), xy(c)), 1)

    return {
        "knee_l":     joint("L_hip",      "L_knee",     "L_ankle"),
        "knee_r":     joint("R_hip",      "R_knee",     "R_ankle"),
        "elbow_l":    joint("L_shoulder", "L_elbow",    "L_wrist"),
        "elbow_r":    joint("R_shoulder", "R_elbow",    "R_wrist"),
        "hip_l":      joint("L_shoulder", "L_hip",      "L_knee"),
        "hip_r":      joint("R_shoulder", "R_hip",      "R_knee"),
        "shoulder_l": joint("L_hip",      "L_shoulder", "L_elbow"),
        "shoulder_r": joint("R_hip",      "R_shoulder", "R_elbow"),
    }


def _normalize_target(target: str | None) -> str:
    value = (target or "auto").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "peto_rojo": "red",
        "rojo": "red",
        "red": "red",
        "peto_azul": "blue",
        "azul": "blue",
        "blue": "blue",
        "rojo_vs_azul": "duel",
        "red_vs_blue": "duel",
        "peto_rojo_vs_peto_azul": "duel",
        "duel": "duel",
        "dual": "duel",
        "combate_rojo_azul": "duel",
        "izquierda": "left",
        "left": "left",
        "atleta_izquierda": "left",
        "derecha": "right",
        "right": "right",
        "atleta_derecha": "right",
    }
    return aliases.get(value, "auto")


def _pose_visibility(landmarks) -> float:
    vals = []
    for lm in landmarks:
        vals.append(float(getattr(lm, "visibility", getattr(lm, "presence", 1.0)) or 0.0))
    return float(np.mean(vals)) if vals else 0.0


def _pose_bbox(landmarks, w: int, h: int) -> tuple[int, int, int, int]:
    xs = [float(lm.x) * w for lm in landmarks if -0.2 <= float(lm.x) <= 1.2]
    ys = [float(lm.y) * h for lm in landmarks if -0.2 <= float(lm.y) <= 1.2]
    if not xs or not ys:
        return 0, 0, 0, 0
    x0, x1 = max(0, int(min(xs))), min(w - 1, int(max(xs)))
    y0, y1 = max(0, int(min(ys))), min(h - 1, int(max(ys)))
    return x0, y0, x1, y1


def _torso_bbox(landmarks, w: int, h: int) -> tuple[int, int, int, int]:
    names = ("L_shoulder", "R_shoulder", "L_hip", "R_hip")
    pts = [_lm_xy(landmarks, name, w, h) for name in names]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    # Keep the ROI tight to the trunk protector. A wide torso box can swallow
    # the referee or the other fighter when bodies overlap in taekwondo bouts.
    pad_x = max(6, min(24, int((x1 - x0) * 0.12)))
    pad_y = max(6, min(24, int((y1 - y0) * 0.10)))
    return (
        max(0, int(x0 - pad_x)),
        max(0, int(y0 - pad_y)),
        min(w - 1, int(x1 + pad_x)),
        min(h - 1, int(y1 + pad_y)),
    )


def _head_bbox(landmarks, w: int, h: int) -> tuple[int, int, int, int]:
    """
    Head/helmet bbox anchored to MediaPipe ear landmarks (indices 7=L_ear, 8=R_ear).
    Ears are visible even when the person faces away from the camera.
    Ear-anchored bbox is tight around the head and does NOT capture background objects
    (scoreboards, banners) that happen to be above the person in the frame.
    Falls back to shoulder estimation with a conservative height factor when ears are
    unreliable (extreme angle, heavy occlusion).
    """
    try:
        L_ear = landmarks[7]
        R_ear = landmarks[8]
        L_vis = float(getattr(L_ear, "visibility", getattr(L_ear, "presence", 0.0)) or 0.0)
        R_vis = float(getattr(R_ear, "visibility", getattr(R_ear, "presence", 0.0)) or 0.0)
        if max(L_vis, R_vis) >= 0.15:
            lx, ly = L_ear.x * w, L_ear.y * h
            rx, ry = R_ear.x * w, R_ear.y * h
            ear_cx  = (lx + rx) / 2
            ear_cy  = (ly + ry) / 2
            ear_span = max(20.0, abs(rx - lx))
            hw  = max(16, int(ear_span * 0.80))   # half-width
            hup = max(20, int(ear_span * 0.65))   # helmet above ear level
            hdn = max(10, int(ear_span * 0.40))   # chin/neck below ear level
            x0 = max(0, int(ear_cx - hw))
            x1 = min(w - 1, int(ear_cx + hw))
            y0 = max(0, int(ear_cy - hup))
            y1 = min(h - 1, int(ear_cy + hdn))
            return x0, y0, x1, y1
    except Exception as exc:
        _logger.debug("No se pudo estimar bbox de cabeza por orejas: %s", exc)
    # Fallback: shoulder estimation with conservative height (avoids background capture)
    L_sh = _lm_xy(landmarks, "L_shoulder", w, h)
    R_sh = _lm_xy(landmarks, "R_shoulder", w, h)
    sh_cx = (L_sh[0] + R_sh[0]) / 2
    sh_cy = (L_sh[1] + R_sh[1]) / 2
    sh_w  = max(14.0, abs(R_sh[0] - L_sh[0]))
    hw  = max(14, int(sh_w * 0.55))
    hh  = max(22, int(sh_w * 0.80))   # tall enough for helmets; ear-based path handles scoreboard
    x0 = max(0, int(sh_cx - hw))
    x1 = min(w - 1, int(sh_cx + hw))
    y1 = max(0, int(sh_cy))
    y0 = max(0, y1 - hh)
    return x0, y0, x1, y1


def _vest_color_scores(rgb: np.ndarray, landmarks, w: int, h: int) -> dict:
    x0, y0, x1, y1 = _torso_bbox(landmarks, w, h)
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = _pose_bbox(landmarks, w, h)
    roi = rgb[y0:y1, x0:x1]
    if roi.size == 0:
        return {"red": 0.0, "blue": 0.0, "yellow": 0.0, "head_red": 0.0, "head_blue": 0.0}

    # Trim horizontal edges (20 % each side) to reduce color bleed from
    # adjacent athletes standing next to the person being evaluated.
    _rh, _rw = roi.shape[:2]
    _trim = max(6, int(_rw * 0.20))
    if _rw > _trim * 2 + 10:
        roi = roi[:, _trim:_rw - _trim]

    # Use channel dominance instead of HSV-only ratios. In real gym footage,
    # shadows on a referee shirt/tie can look "blue" in HSV; a protector must
    # occupy visible torso area with a clearly dominant red/blue channel.
    px = roi.astype(float)
    red, green, blue = px[:, :, 0], px[:, :, 1], px[:, :, 2]
    max_ch = np.maximum.reduce([red, green, blue])
    min_ch = np.minimum.reduce([red, green, blue])
    sat = (max_ch - min_ch) / np.maximum(max_ch, 1.0)
    denom = max(1, int(roi.shape[0] * roi.shape[1]))
    # Three orthogonal guards against yellow/orange shirts being classified as red:
    #   Ratio 1.50  : yellow R=230 G=200 → 200*1.50=300 > 230 → fails ratio.
    #   Green cap 110: warm-shadow yellow R=215 G=138 → 138>110 → fails cap.
    #                 true red hogu R=200 G=60 → 60<110 → passes.
    #   Abs diff 12  : sanity check for extremely dark areas.
    red_mask = (
        (max_ch > 45)
        & (sat > 0.18)
        & (red > green * 1.50)
        & (green < 110)
        & (red > blue * 1.08)
        & ((red - green) > 12)
        & ((red - blue) > 8)
    )
    blue_mask = (
        (max_ch > 65)
        & (blue > 65)
        & (sat > 0.16)
        & (blue > red * 1.10)
        & (blue > green * 1.04)
        & ((blue - red) > 12)
        & ((blue - green) > 6)
    )
    # Yellow: R≈G both high, B clearly lower — referee shirt / corner jacket
    yellow_mask = (
        (max_ch > 80)
        & (red > 100) & (green > 90)
        & (sat > 0.15)
        & ((red - blue) > 40) & ((green - blue) > 35)
        & (np.abs(red - green) < 65)
        & (blue < red * 0.62)
    )

    # Head / helmet region — more reliable than torso for athlete identification.
    # Referees wear no helmet → head scores near 0; athletes' helmets are solid color.
    x0h, y0h, x1h, y1h = _head_bbox(landmarks, w, h)
    roi_h = rgb[y0h:y1h, x0h:x1h]
    if roi_h.size >= 16:
        ph     = roi_h.astype(float)
        rh, gh, bh = ph[:, :, 0], ph[:, :, 1], ph[:, :, 2]
        mx_h   = np.maximum.reduce([rh, gh, bh])
        mn_h   = np.minimum.reduce([rh, gh, bh])
        sat_h  = (mx_h - mn_h) / np.maximum(mx_h, 1.0)
        dh     = max(1, roi_h.shape[0] * roi_h.shape[1])
        # Helmets are vivid — require high saturation to exclude skin/flesh tones.
        # Skin: R≈190, G≈155, B≈140 → sat≈0.26, G > 100 → fails here.
        # Red helmet: R≈210, G≈40, B≈40 → sat≈0.81, G < 110 → passes.
        hred_m = (
            (mx_h > 80) & (sat_h > 0.42)
            & (rh > gh * 1.30) & (rh > bh * 1.25)
            & (gh < 115) & (bh < 115)
            & ((rh - gh) > 35) & ((rh - bh) > 30)
        )
        hblu_m = (
            (mx_h > 80) & (sat_h > 0.42)
            & (bh > rh * 1.30) & (bh > gh * 1.15)
            & (rh < 115)
            & ((bh - rh) > 35) & ((bh - gh) > 18)
        )
        head_red  = round(float(np.count_nonzero(hred_m)) / dh, 4)
        head_blue = round(float(np.count_nonzero(hblu_m)) / dh, 4)
    else:
        head_red = head_blue = 0.0

    return {
        "red":       round(float(np.count_nonzero(red_mask)) / denom, 4),
        "blue":      round(float(np.count_nonzero(blue_mask)) / denom, 4),
        "yellow":    round(float(np.count_nonzero(yellow_mask)) / denom, 4),
        "head_red":  head_red,
        "head_blue": head_blue,
    }


def _target_color_bbox(rgb: np.ndarray, landmarks, w: int, h: int, target: str) -> tuple[int, int, int, int] | None:
    if target not in ("red", "blue"):
        return None
    x0, y0, x1, y1 = _torso_bbox(landmarks, w, h)
    roi = rgb[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    px = roi.astype(float)
    red, green, blue = px[:, :, 0], px[:, :, 1], px[:, :, 2]
    max_ch = np.maximum.reduce([red, green, blue])
    min_ch = np.minimum.reduce([red, green, blue])
    sat = (max_ch - min_ch) / np.maximum(max_ch, 1.0)
    if target == "red":
        mask = (
            (max_ch > 45)
            & (sat > 0.18)
            & (red > green * 1.10)
            & (red > blue * 1.08)
            & ((red - green) > 10)
            & ((red - blue) > 8)
        )
    else:
        mask = (
            (max_ch > 65)
            & (blue > 65)
            & (sat > 0.16)
            & (blue > red * 1.10)
            & (blue > green * 1.04)
            & ((blue - red) > 12)
            & ((blue - green) > 6)
        )

    ys, xs = np.where(mask)
    if len(xs) < max(18, int(mask.size * 0.025)):
        return None
    return x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()), y0 + int(ys.max())


def _target_body_consistency(
    rgb: np.ndarray,
    landmarks,
    w: int,
    h: int,
    target: str,
    colors: dict | None = None,
) -> dict:
    """Score whether the detected skeleton is geometrically attached to the target vest."""
    if target not in ("red", "blue"):
        return {"quality": 1.0, "notes": []}

    colors = colors or _vest_color_scores(rgb, landmarks, w, h)
    other = "blue" if target == "red" else "red"
    target_signal = max(float(colors.get(target, 0.0) or 0.0),
                        float(colors.get(f"head_{target}", 0.0) or 0.0) * 1.8)
    other_signal = max(float(colors.get(other, 0.0) or 0.0),
                       float(colors.get(f"head_{other}", 0.0) or 0.0) * 1.8)
    head_target = float(colors.get(f"head_{target}", 0.0) or 0.0)
    head_other = float(colors.get(f"head_{other}", 0.0) or 0.0)

    notes: list[str] = []
    quality = 1.0
    vest = _target_color_bbox(rgb, landmarks, w, h, target)
    if not vest:
        notes.append("peto_no_aislado")
        quality = 0.70 if head_target >= 0.08 else 0.45
        if head_other > head_target * 1.25 and head_other >= 0.06:
            notes.append("casco_contrario")
            quality -= 0.30
        return {"quality": round(max(0.0, min(1.0, quality)), 3), "notes": notes}

    vx0, vy0, vx1, vy1 = vest
    vest_w = max(1.0, float(vx1 - vx0))
    vest_h = max(1.0, float(vy1 - vy0))
    vest_cx = (vx0 + vx1) / 2.0
    vest_cy = (vy0 + vy1) / 2.0

    l_sh = _lm_xy(landmarks, "L_shoulder", w, h)
    r_sh = _lm_xy(landmarks, "R_shoulder", w, h)
    l_hp = _lm_xy(landmarks, "L_hip", w, h)
    r_hp = _lm_xy(landmarks, "R_hip", w, h)
    shoulder_mid = ((l_sh[0] + r_sh[0]) / 2.0, (l_sh[1] + r_sh[1]) / 2.0)
    hip_mid = ((l_hp[0] + r_hp[0]) / 2.0, (l_hp[1] + r_hp[1]) / 2.0)
    torso_mid = ((shoulder_mid[0] + hip_mid[0]) / 2.0, (shoulder_mid[1] + hip_mid[1]) / 2.0)
    shoulder_span = abs(l_sh[0] - r_sh[0])
    hip_span = abs(l_hp[0] - r_hp[0])
    scale_x = max(vest_w, w * 0.075)
    scale_y = max(vest_h, h * 0.080)

    torso_dx = abs(torso_mid[0] - vest_cx) / scale_x
    shoulder_dx = abs(shoulder_mid[0] - vest_cx) / scale_x
    hip_dx = abs(hip_mid[0] - vest_cx) / scale_x
    torso_dy = abs(torso_mid[1] - vest_cy) / scale_y

    if torso_dx > 1.75:
        quality -= 0.42
        notes.append("torso_lejos_del_peto")
    elif torso_dx > 1.18:
        quality -= 0.22
        notes.append("torso_desalineado")

    if max(shoulder_dx, hip_dx) > 2.10:
        quality -= 0.34
        notes.append("hombro_cadera_fuera_del_peto")
    elif max(shoulder_dx, hip_dx) > 1.45:
        quality -= 0.16
        notes.append("hombro_cadera_desalineados")

    if torso_dy > 1.75:
        quality -= 0.24
        notes.append("torso_fuera_altura_peto")

    torso_span = max(shoulder_span, hip_span)
    shoulder_span_2d = float(np.linalg.norm(np.array(l_sh, dtype=float) - np.array(r_sh, dtype=float)))
    hip_span_2d = float(np.linalg.norm(np.array(l_hp, dtype=float) - np.array(r_hp, dtype=float)))
    torso_len = float(np.linalg.norm(np.array(shoulder_mid, dtype=float) - np.array(hip_mid, dtype=float)))
    if torso_len > max(24.0, h * 0.055):
        shoulder_ratio = shoulder_span_2d / max(torso_len, 1.0)
        hip_ratio = hip_span_2d / max(torso_len, 1.0)
        if shoulder_ratio < 0.26 and hip_ratio < 0.22:
            quality -= 0.55
            notes.append("esqueleto_colapsado")
        elif shoulder_ratio < 0.34 and hip_ratio < 0.25:
            quality -= 0.28
            notes.append("perfil_extremo")

    if torso_span > vest_w * 2.65 + w * 0.045:
        quality -= 0.24
        notes.append("torso_demasiado_ancho")

    if other_signal >= 0.030 and other_signal > target_signal * 0.62:
        quality -= 0.28
        notes.append("color_contrario_en_pose")

    target_torso = float(colors.get(target, 0.0) or 0.0)
    other_torso = float(colors.get(other, 0.0) or 0.0)
    if head_target >= 0.10 and target_torso < 0.080 and other_torso >= 0.080 and other_torso > target_torso * 2.0:
        quality -= 0.42
        notes.append("casco_sin_peto_coherente")

    yellow = float(colors.get("yellow", 0.0) or 0.0)
    if yellow > max(float(colors.get(target, 0.0) or 0.0), float(colors.get(other, 0.0) or 0.0)) * 1.15 and yellow > 0.055:
        if max(head_target, head_other) < 0.08:
            quality -= 0.32
            notes.append("posible_arbitro")

    if head_other >= 0.075 and head_other > head_target * 1.20:
        quality -= 0.35
        notes.append("casco_contrario")

    if head_target >= 0.10 and target_signal >= 0.04 and "casco_contrario" not in notes:
        quality = max(quality, 0.72)

    return {"quality": round(max(0.0, min(1.0, quality)), 3), "notes": notes}


def _landmark_validity(
    rgb: np.ndarray,
    landmarks,
    w: int,
    h: int,
    target: str,
) -> tuple[dict, list[str]]:
    validity = {name: True for name in _L}
    notes: list[str] = []
    vest = _target_color_bbox(rgb, landmarks, w, h, target)
    if not vest:
        return validity, notes

    vx0, vy0, vx1, vy1 = vest
    vest_w = max(1, vx1 - vx0)
    shoulder_mid_y = (_lm_xy(landmarks, "L_shoulder", w, h)[1] + _lm_xy(landmarks, "R_shoulder", w, h)[1]) / 2
    hip_mid_y = (_lm_xy(landmarks, "L_hip", w, h)[1] + _lm_xy(landmarks, "R_hip", w, h)[1]) / 2
    torso_h = max(1.0, hip_mid_y - shoulder_mid_y)

    def arm_is_plausible(elbow_name: str, wrist_name: str) -> bool:
        ex, ey = _lm_xy(landmarks, elbow_name, w, h)
        wx, wy = _lm_xy(landmarks, wrist_name, w, h)
        x_margin = max(20.0, vest_w * 0.40)
        y_top = shoulder_mid_y - max(28.0, torso_h * 0.22)
        elbow_ok = (vx0 - x_margin) <= ex <= (vx1 + x_margin) and ey >= y_top
        wrist_ok = (vx0 - x_margin) <= wx <= (vx1 + x_margin) and wy >= y_top
        return elbow_ok and wrist_ok

    if not arm_is_plausible("L_elbow", "L_wrist"):
        validity["L_elbow"] = False
        validity["L_wrist"] = False
        notes.append("brazo_izquierdo_fuera_de_peto")
    if not arm_is_plausible("R_elbow", "R_wrist"):
        validity["R_elbow"] = False
        validity["R_wrist"] = False
        notes.append("brazo_derecho_fuera_de_peto")

    return validity, notes


def _clean_pose_bbox(landmarks, w: int, h: int, validity: dict | None = None) -> tuple[int, int, int, int]:
    valid_names = [name for name in _L if not validity or validity.get(name, True)]
    if not valid_names:
        return _pose_bbox(landmarks, w, h)
    xs, ys = [], []
    for name in valid_names:
        x, y = _lm_xy(landmarks, name, w, h)
        if -0.2 * w <= x <= 1.2 * w and -0.2 * h <= y <= 1.2 * h:
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        return _pose_bbox(landmarks, w, h)
    pad = 16
    return (
        max(0, int(min(xs) - pad)),
        max(0, int(min(ys) - pad)),
        min(w - 1, int(max(xs) + pad)),
        min(h - 1, int(max(ys) + pad)),
    )


def _describe_pose(rgb: np.ndarray, landmarks, w: int, h: int, idx: int) -> dict:
    x0, y0, x1, y1 = _pose_bbox(landmarks, w, h)
    area = max(0, x1 - x0) * max(0, y1 - y0) / max(1, w * h)
    colors = _vest_color_scores(rgb, landmarks, w, h)
    red_identity = _target_body_consistency(rgb, landmarks, w, h, "red", colors)
    blue_identity = _target_body_consistency(rgb, landmarks, w, h, "blue", colors)
    return {
        "idx": idx,
        "landmarks": landmarks,
        "visibility": round(_pose_visibility(landmarks), 4),
        "area": round(float(area), 4),
        "cx": round(((x0 + x1) / 2) / max(1, w), 4),
        "cy": round(((y0 + y1) / 2) / max(1, h), 4),
        "width": round((x1 - x0) / max(1, w), 4),
        "height": round((y1 - y0) / max(1, h), 4),
        "bbox": (x0, y0, x1, y1),
        "red_score":       colors["red"],
        "blue_score":      colors["blue"],
        "yellow_score":    colors.get("yellow", 0.0),
        "head_red_score":  colors.get("head_red", 0.0),
        "head_blue_score": colors.get("head_blue", 0.0),
        "red_identity_quality": red_identity["quality"],
        "blue_identity_quality": blue_identity["quality"],
        "red_identity_notes": red_identity["notes"],
        "blue_identity_notes": blue_identity["notes"],
    }


def _track_affinity(candidate: dict, track: dict | None) -> float:
    if not track:
        return 1.0
    dx = abs(float(candidate.get("cx", 0.0)) - float(track.get("cx", 0.0)))
    dy = abs(float(candidate.get("cy", 0.0)) - float(track.get("cy", 0.0)))
    center_dist = float(np.sqrt((dx / 0.18) ** 2 + (dy / 0.24) ** 2))
    center_score = max(0.0, 1.0 - min(center_dist, 1.0))
    prev_area = max(float(track.get("area", 0.0) or 0.0), 1e-4)
    curr_area = max(float(candidate.get("area", 0.0) or 0.0), 1e-4)
    scale_score = min(prev_area, curr_area) / max(prev_area, curr_area)
    return round(center_score * 0.70 + scale_score * 0.30, 4)


def _track_jump_reason(candidate: dict, track: dict | None) -> str | None:
    if not track:
        return None
    dx = abs(float(candidate.get("cx", 0.0)) - float(track.get("cx", 0.0)))
    dy = abs(float(candidate.get("cy", 0.0)) - float(track.get("cy", 0.0)))
    prev_area = max(float(track.get("area", 0.0) or 0.0), 1e-4)
    curr_area = max(float(candidate.get("area", 0.0) or 0.0), 1e-4)
    scale_ratio = min(prev_area, curr_area) / max(prev_area, curr_area)
    if dx > 0.30 or dy > 0.34:
        return "salto_de_posicion"
    if scale_ratio < 0.28:
        return "salto_de_escala"
    return None


def _bbox_intersection_ratio(b1, b2) -> float:
    """Intersection over smaller bbox area; useful for detecting body occlusion."""
    if not b1 or not b2:
        return 0.0
    x1, y1 = max(float(b1[0]), float(b2[0])), max(float(b1[1]), float(b2[1]))
    x2, y2 = min(float(b1[2]), float(b2[2])), min(float(b1[3]), float(b2[3]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0.0:
        return 0.0
    a1 = max(1.0, (float(b1[2]) - float(b1[0])) * (float(b1[3]) - float(b1[1])))
    a2 = max(1.0, (float(b2[2]) - float(b2[0])) * (float(b2[3]) - float(b2[1])))
    return round(inter / max(1.0, min(a1, a2)), 3)


def _max_candidate_overlap(candidate: dict, candidates: list[dict]) -> float:
    """How much this candidate overlaps any other detected body in the frame."""
    bbox = candidate.get("bbox")
    idx = candidate.get("idx")
    overlaps = [
        _bbox_intersection_ratio(bbox, other.get("bbox"))
        for other in (candidates or [])
        if other.get("idx") != idx
    ]
    return max(overlaps, default=0.0)


def _bbox_edge_margin(candidate: dict, w: int, h: int) -> float:
    """Normalized distance from bbox to the closest image edge."""
    bbox = candidate.get("bbox")
    if not bbox or w <= 0 or h <= 0:
        return 0.0
    x0, y0, x1, y1 = [float(v) for v in bbox]
    return round(max(0.0, min(x0 / w, y0 / h, (w - x1) / w, (h - y1) / h)), 4)


def _candidate_athlete_evidence(candidate: dict, target: str | None = None, *, affinity: float = 0.0) -> dict:
    """Estimate if a pose has enough combat-athlete evidence to be analyzed.

    MediaPipe can return skeletons for referees, spectators or background-like
    body fragments. This guard keeps the selector from turning those poses into
    red/blue athletes when the vest/helmet evidence is absent.
    """
    rs = float(candidate.get("red_score", 0.0) or 0.0)
    bs = float(candidate.get("blue_score", 0.0) or 0.0)
    ys = float(candidate.get("yellow_score", 0.0) or 0.0)
    hr = float(candidate.get("head_red_score", 0.0) or 0.0)
    hb = float(candidate.get("head_blue_score", 0.0) or 0.0)
    visibility = float(candidate.get("visibility", 0.0) or 0.0)
    area = float(candidate.get("area", 0.0) or 0.0)
    height = float(candidate.get("height", 0.0) or 0.0)

    body_ok = visibility >= 0.20 and area >= 0.006 and height >= 0.12
    generic_vest = max(rs, bs)
    generic_head = max(hr, hb)
    generic_signal = max(generic_vest, generic_head * 1.8)
    referee_like = ys > generic_vest * 1.20 and ys > 0.055 and generic_head < 0.10

    if target in ("red", "blue"):
        other = "blue" if target == "red" else "red"
        torso = rs if target == "red" else bs
        head = hr if target == "red" else hb
        other_torso = bs if target == "red" else rs
        other_head = hb if target == "red" else hr
        signal = max(torso, head * 1.8)
        other_signal = max(other_torso, other_head * 1.8)
        margin = signal - other_signal
        has_helmet = head >= 0.052 and torso >= 0.025
        has_vest = torso >= 0.060 and ys < 0.075
        tracked_hint = affinity >= 0.72 and torso >= 0.020 and signal >= 0.024 and margin >= -0.006
        head_without_torso = head >= 0.10 and torso < 0.025
        torso_contradicts_head = (
            head >= 0.10
            and torso < 0.080
            and other_torso >= 0.080
            and other_torso > torso * 2.0
        )
        if head_without_torso or torso_contradicts_head:
            return {
                "enough": False,
                "signal": round(signal, 4),
                "margin": round(margin, 4),
                "body_ok": bool(body_ok),
                "referee_like": bool(referee_like),
                "reason": "casco_sin_peto_coherente",
                "other": other,
            }
        enough = body_ok and not referee_like and (has_helmet or has_vest or tracked_hint)
        return {
            "enough": bool(enough),
            "signal": round(signal, 4),
            "margin": round(margin, 4),
            "body_ok": bool(body_ok),
            "referee_like": bool(referee_like),
            "reason": None if enough else "sin_evidencia_atleta",
            "other": other,
        }

    has_helmet = generic_head >= 0.052 and generic_vest >= 0.025
    strong_vest = generic_vest >= 0.065 and ys < 0.075
    enough = body_ok and not referee_like and generic_signal >= 0.020 and (has_helmet or strong_vest)
    return {
        "enough": bool(enough),
        "signal": round(generic_signal, 4),
        "margin": 0.0,
        "body_ok": bool(body_ok),
        "referee_like": bool(referee_like),
        "reason": None if enough else "sin_evidencia_atleta",
    }


def _update_track_state(track: dict | None, selected: dict, target: str, frame_idx: int) -> dict:
    color_score = selected.get(f"{target}_score", max(selected.get("red_score", 0.0), selected.get("blue_score", 0.0)))
    if not track:
        return {
            "track_id": f"{target or 'auto'}-principal",
            "cx": float(selected.get("cx", 0.0)),
            "cy": float(selected.get("cy", 0.0)),
            "area": float(selected.get("area", 0.0)),
            "color_score": float(color_score or 0.0),
            "frames": 1,
            "last_frame": int(frame_idx),
        }
    alpha = 0.35
    track["cx"] = round((1 - alpha) * float(track.get("cx", 0.0)) + alpha * float(selected.get("cx", 0.0)), 4)
    track["cy"] = round((1 - alpha) * float(track.get("cy", 0.0)) + alpha * float(selected.get("cy", 0.0)), 4)
    track["area"] = round((1 - alpha) * float(track.get("area", 0.0)) + alpha * float(selected.get("area", 0.0)), 4)
    track["color_score"] = round((1 - alpha) * float(track.get("color_score", 0.0)) + alpha * float(color_score or 0.0), 4)
    track["frames"] = int(track.get("frames", 0) or 0) + 1
    track["last_frame"] = int(frame_idx)
    return track


def _select_pose(candidates: list, target: str, track: dict | None = None, lenient: bool = False) -> tuple:
    if not candidates:
        return None, None
    usable = [c for c in candidates if c["visibility"] >= 0.15] or candidates

    if target in ("left", "right"):
        if track:
            chosen = max(usable, key=lambda c: _track_affinity(c, track))
            affinity = _track_affinity(chosen, track)
            reason = _track_jump_reason(chosen, track)
            chosen["track_affinity"] = affinity
            chosen["selection_score"] = round(chosen["visibility"] + chosen["area"] + affinity * 0.35, 4)
            chosen["selection_confidence"] = round(min(1.0, 0.45 + affinity * 0.45), 3)
            if reason and affinity < 0.38:
                chosen["rejection_reason"] = reason
                return None, chosen
            return chosen["landmarks"], chosen
        top = sorted(usable, key=lambda c: (c["area"], c["visibility"]), reverse=True)[:3]
        chosen = min(top, key=lambda c: c["cx"]) if target == "left" else max(top, key=lambda c: c["cx"])
        chosen["selection_score"] = round(chosen["visibility"] + chosen["area"], 4)
        chosen["selection_confidence"] = 0.75
        chosen["track_affinity"] = 1.0
        return chosen["landmarks"], chosen

    if target in ("red", "blue"):
        other = "blue" if target == "red" else "red"

        def _is_referee(c: dict) -> bool:
            ys = c.get("yellow_score", 0.0)
            vs = max(c.get("red_score", 0.0), c.get("blue_score", 0.0))
            hs = max(c.get("head_red_score", 0.0), c.get("head_blue_score", 0.0))
            # hs < 0.15: real helmets score ≥0.25; background bleed stays <0.12
            return ys > vs * 1.20 and ys > 0.06 and hs < 0.15

        non_ref = [c for c in usable if not _is_referee(c)]
        if non_ref:
            usable = non_ref
        else:
            # All detected candidates appear to be referees.
            # Return None immediately — track affinity must not lower the threshold
            # enough to accept a referee as an athlete.
            dummy = max(usable, key=lambda c: c.get("visibility", 0.0) + c.get("area", 0.0))
            dummy["selection_confidence"] = 0.0
            dummy["rejection_reason"] = "arbitro_detectado"
            return None, dummy

        def _combined(c, t):
            """Helmet-boosted color signal: head is more reliable than torso vest."""
            return max(c.get(f"{t}_score", 0.0), c.get(f"head_{t}_score", 0.0) * 1.8)

        def score(c):
            color  = _combined(c, target)
            margin = max(0.0, color - _combined(c, other))
            affinity = _track_affinity(c, track)
            return color * 7.0 + margin * 4.0 + c["visibility"] * 0.25 + c["area"] * 1.2 + affinity * 0.75

        chosen = max(usable, key=score)
        chosen["selection_score"] = round(float(score(chosen)), 4)
        color_score = chosen[f"{target}_score"]           # torso vest
        margin      = max(0.0, color_score - chosen[f"{other}_score"])
        affinity    = _track_affinity(chosen, track)
        jump_reason = _track_jump_reason(chosen, track)
        chosen["track_affinity"] = affinity
        athlete_evidence = _candidate_athlete_evidence(chosen, target, affinity=affinity)
        chosen["athlete_evidence"] = athlete_evidence.get("signal", 0.0)
        chosen["athlete_evidence_margin"] = athlete_evidence.get("margin", 0.0)
        if not athlete_evidence.get("enough", False):
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = athlete_evidence.get("reason") or "sin_evidencia_atleta"
            return None, chosen
        identity_quality = float(chosen.get(f"{target}_identity_quality", 1.0) or 0.0)
        identity_notes = list(chosen.get(f"{target}_identity_notes", []) or [])
        body_overlap = _max_candidate_overlap(chosen, usable)
        chosen["body_overlap"] = body_overlap
        if body_overlap >= 0.50:
            identity_quality -= 0.34
            identity_notes.append("cuerpo_cruzado")
        elif body_overlap >= 0.28:
            identity_quality -= 0.18
            identity_notes.append("oclusion_parcial")
        identity_quality = max(0.0, min(1.0, identity_quality))
        chosen["identity_quality"] = round(identity_quality, 3)
        chosen["identity_notes"] = identity_notes
        if "esqueleto_colapsado" in identity_notes:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = "esqueleto_colapsado"
            return None, chosen
        if body_overlap >= 0.72 and identity_quality < 0.72:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = "cuerpo_cruzado"
            return None, chosen

        # Helmet validates identity when the torso vest is absent or weak
        # (e.g. white hogu in TKD, or vest partially occluded).
        # effective_* blends torso + helmet so both signal paths are used.
        head_tgt = chosen.get(f"head_{target}_score", 0.0) * 1.8
        head_oth = chosen.get(f"head_{other}_score",  0.0) * 1.8
        effective_color  = max(color_score, head_tgt)
        effective_margin = max(margin, max(0.0, head_tgt - head_oth))

        min_color  = 0.022 if track and affinity >= 0.62 else (0.028 if track else 0.035)
        min_margin = 0.008 if track and affinity >= 0.62 else (0.010 if track else 0.012)
        # Duel mode: pre-filter already removed the referee, so lower the bar for
        # athletes with white hogus (TKD) or partially occluded vests.
        if lenient:
            min_color  = max(0.006, min_color * 0.25)
            min_margin = max(0.001, min_margin * 0.25)

        min_identity = 0.30 if lenient else 0.42
        if track and affinity >= 0.72 and effective_color >= 0.065:
            min_identity = max(0.24, min_identity - 0.10)
        if identity_quality < min_identity:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = "pose_contaminada"
            return None, chosen
        if jump_reason and effective_color < 0.06:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = jump_reason
            return None, chosen
        if track and affinity < 0.30 and effective_color < 0.06:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = "continuidad_baja"
            return None, chosen
        ratio_limit = 0.55 if lenient else 1.10
        if effective_color < min_color or effective_margin < min_margin or effective_color < _combined(chosen, other) * ratio_limit:
            chosen["selection_confidence"] = 0.0
            chosen["rejection_reason"] = "color_insuficiente"
            return None, chosen
        confidence = min(1.0, effective_color * 1.4 + effective_margin * 2.2 + (0.10 if track else 0.0) + affinity * 0.20)
        confidence *= 0.58 + identity_quality * 0.42
        chosen["selection_confidence"] = round(confidence, 3)
        return chosen["landmarks"], chosen

    chosen = max(usable, key=lambda c: c["visibility"] + c["area"] * 2.0 + max(c["red_score"], c["blue_score"]) * 0.15)
    chosen["selection_score"] = round(chosen["visibility"] + chosen["area"], 4)
    chosen["selection_confidence"] = 0.65
    chosen["track_affinity"] = 1.0
    return chosen["landmarks"], chosen


def _select_duel_poses(candidates: list, tracks: dict) -> dict:
    """Select red and blue in the same frame, avoiding one pose being both targets."""
    if not candidates:
        return {}

    # Pre-filter: keep only likely athletes, exclude referees/judges/spectators.
    #
    # Two-tier athlete identification:
    #   Tier 1 — Helmet present (head_signal >= 0.06): definitive athlete signal.
    #            Referees NEVER wear colored helmets in TKD/boxing competition.
    #   Tier 2 — Strong vivid vest without helmet (vest_signal >= 0.09, ys < 0.06):
    #            Covers boxing athletes who don't wear headgear.
    #
    # Anything that passes neither tier is a referee, judge, coach, or spectator.
    _VEST_MIN     = 0.022   # minimum combined signal to even consider a candidate
    _HEAD_ATHLETE = 0.06    # colored helmet → definitive athlete; ear-based bbox makes
    #                         scoreboard bleed ≈0 (fallback bleed <0.03 < 0.06 → safe)
    _VEST_STRONG  = 0.07    # strong vest → athlete when no helmet visible (boxing)
    vest_only = []
    for c in candidates:
        rs = c.get("red_score", 0.0)
        bs = c.get("blue_score", 0.0)
        ys = c.get("yellow_score", 0.0)
        hr = c.get("head_red_score", 0.0)
        hb = c.get("head_blue_score", 0.0)
        vest_signal = max(rs, bs)
        head_signal = max(hr, hb)
        combined    = max(vest_signal, head_signal * 1.8)
        if combined < _VEST_MIN:
            continue
        has_helmet  = head_signal >= _HEAD_ATHLETE
        strong_vest = vest_signal >= _VEST_STRONG and ys < 0.06
        if not has_helmet and not strong_vest:
            continue
        vest_only.append(c)
    if vest_only:
        candidates = vest_only
    else:
        dummy = dict(max(candidates, key=lambda c: c.get("visibility", 0.0) + c.get("area", 0.0)))
        dummy["selection_confidence"] = 0.0
        dummy["rejection_reason"] = "sin_evidencia_atleta"
        return {
            "red": {"landmarks": None, "selected": dict(dummy)},
            "blue": {"landmarks": None, "selected": dict(dummy)},
        }

    # Second guard: the legacy pre-filter can still admit "helmet-only" bodies.
    # For duel analysis we require at least minimal torso/vest support so a
    # referee or white-clad body is not selected from background head color.
    strict_candidates = []
    for c in candidates:
        evidence = _candidate_athlete_evidence(c)
        c["athlete_evidence"] = evidence.get("signal", 0.0)
        if evidence.get("enough", False):
            strict_candidates.append(c)
    if strict_candidates:
        candidates = strict_candidates
    else:
        dummy = dict(max(candidates, key=lambda c: c.get("visibility", 0.0) + c.get("area", 0.0)))
        dummy["selection_confidence"] = 0.0
        dummy["rejection_reason"] = "sin_evidencia_atleta"
        return {
            "red": {"landmarks": None, "selected": dict(dummy)},
            "blue": {"landmarks": None, "selected": dict(dummy)},
        }

    def attempt(first: str, second: str) -> tuple[float, dict]:
        local = [dict(c) for c in candidates]
        first_lms, first_sel = _select_pose(local, first, tracks.get(first), lenient=True)
        remaining = [
            dict(c)
            for c in candidates
            if not (first_sel and c.get("idx") == first_sel.get("idx") and first_lms is not None)
        ]
        second_lms, second_sel = _select_pose(remaining, second, tracks.get(second), lenient=True)

        picked = {
            first: {"landmarks": first_lms, "selected": first_sel},
            second: {"landmarks": second_lms, "selected": second_sel},
        }
        score = 0.0
        for key in ("red", "blue"):
            item = picked.get(key) or {}
            selected = item.get("selected") or {}
            if item.get("landmarks") is not None:
                score += 10.0
                score += float(selected.get("selection_confidence", 0.0) or 0.0) * 2.0
                score += float(selected.get(f"{key}_score", 0.0) or 0.0) * 3.0
                score += float(selected.get("track_affinity", 0.0) or 0.0)
                score += float(selected.get("identity_quality", selected.get(f"{key}_identity_quality", 1.0)) or 0.0) * 1.5
        return score, picked

    attempts = [attempt("red", "blue"), attempt("blue", "red")]
    return max(attempts, key=lambda item: item[0])[1]


def _draw_skeleton(
    rgb: np.ndarray,
    landmarks,
    w: int,
    h: int,
    label: str | None = None,
    validity: dict | None = None,
) -> np.ndarray:
    img = rgb.copy()
    pts = {name: (int(lm.x * w), int(lm.y * h)) for name, lm in
           zip(_L.keys(), [landmarks[i] for i in _L.values()])}

    # Per-landmark visibility from MediaPipe (0–1)
    vis_map = {
        name: float(getattr(landmarks[idx], "visibility", getattr(landmarks[idx], "presence", 1.0)) or 0.0)
        for name, idx in _L.items()
    }

    _C_HIGH = (80, 210, 0)    # RGB verde  — alta confianza
    _C_MED  = (255, 165, 0)   # RGB ámbar  — media confianza
    _C_LOW  = (220, 50, 50)   # RGB rojo   — baja / inválida

    def _conf(name: str) -> int:
        if validity and not validity.get(name, True):
            return 0
        v = vis_map.get(name, 1.0)
        if v >= 0.65:
            return 2
        if v >= 0.30:
            return 1
        return 0

    _colors = [_C_LOW, _C_MED, _C_HIGH]

    # Conexiones — usa el peor nivel de los dos extremos; omite inválidas
    for (i, j) in _POSE_CONNECTIONS:
        for a_name, a_idx in _L.items():
            if a_idx != i:
                continue
            for b_name, b_idx in _L.items():
                if b_idx != j:
                    continue
                conf = min(_conf(a_name), _conf(b_name))
                if conf == 0:
                    continue
                cv2.line(img, pts[a_name], pts[b_name], _colors[conf], 2, cv2.LINE_AA)

    # Landmarks — solo válidos (conf > 0)
    for name, pt in pts.items():
        conf = _conf(name)
        if conf == 0:
            continue
        cv2.circle(img, pt, 5, _colors[conf], -1, cv2.LINE_AA)
        cv2.circle(img, pt, 5, (255, 255, 255), 1, cv2.LINE_AA)

    if label:
        x0, y0, x1, y1 = _clean_pose_bbox(landmarks, w, h, validity)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 210, 180), 2, cv2.LINE_AA)
        cv2.putText(
            img,
            label,
            (x0, max(24, y0 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 210, 180),
            2,
            cv2.LINE_AA,
        )
    return img


def _draw_duel_skeleton(
    rgb: np.ndarray,
    red_landmarks,
    blue_landmarks,
    w: int,
    h: int,
    red_validity: dict | None = None,
    blue_validity: dict | None = None,
    red_ang_vel: float = 0.0,
    blue_ang_vel: float = 0.0,
) -> np.ndarray:
    img = rgb.copy()
    _C_DIM = (110, 110, 110)   # gris — landmark incierto en modo dual

    targets = [
        ("Peto rojo", red_landmarks, red_validity, (220, 50, 50),   red_ang_vel),
        ("Peto azul", blue_landmarks, blue_validity, (40, 140, 255), blue_ang_vel),
    ]
    for label, landmarks, validity, color, ang_vel in targets:
        if landmarks is None:
            continue
        pts = {
            name: (int(lm.x * w), int(lm.y * h))
            for name, lm in zip(_L.keys(), [landmarks[i] for i in _L.values()])
        }
        vis_map = {
            name: float(getattr(landmarks[idx], "visibility", getattr(landmarks[idx], "presence", 1.0)) or 0.0)
            for name, idx in _L.items()
        }

        def _ok(name: str) -> bool:
            if validity and not validity.get(name, True):
                return False
            return vis_map.get(name, 1.0) >= 0.30

        for (i, j) in _POSE_CONNECTIONS:
            for a_name, a_idx in _L.items():
                if a_idx != i:
                    continue
                for b_name, b_idx in _L.items():
                    if b_idx != j:
                        continue
                    if not (_ok(a_name) and _ok(b_name)):
                        continue
                    cv2.line(img, pts[a_name], pts[b_name], color, 2, cv2.LINE_AA)
        for name, pt in pts.items():
            if _ok(name):
                cv2.circle(img, pt, 5, color, -1, cv2.LINE_AA)
                cv2.circle(img, pt, 5, (255, 255, 255), 1, cv2.LINE_AA)

        x0, y0, x1, y1 = _clean_pose_bbox(landmarks, w, h, validity)
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2, cv2.LINE_AA)
        cv2.putText(
            img,
            label,
            (x0, max(24, y0 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
            cv2.LINE_AA,
        )
        # Angular velocity badge inside bbox (top-right corner)
        if ang_vel > 1.0:
            vel_lbl = f"{ang_vel:.0f} deg/s"
            (vw, vh), _ = cv2.getTextSize(vel_lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
            vx = max(x0 + 4, x1 - vw - 6)
            vy = y0 + vh + 6
            # Semi-transparent pill background
            overlay = img.copy()
            cv2.rectangle(overlay, (vx - 3, vy - vh - 2), (vx + vw + 3, vy + 3),
                          (10, 14, 24), -1)
            cv2.addWeighted(overlay, 0.60, img, 0.40, 0, img)
            cv2.putText(img, vel_lbl, (vx, vy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
    return img


def _draw_duel_annotated_frame(
    rgb: np.ndarray,
    red_lms,
    blue_lms,
    w: int,
    h: int,
    red_validity: dict | None = None,
    blue_validity: dict | None = None,
    t: float = 0.0,
    distance: float = 0.0,
    exchange: bool = False,
    red_conf: float = 0.0,
    blue_conf: float = 0.0,
    red_ang_vel: float = 0.0,
    blue_ang_vel: float = 0.0,
) -> np.ndarray:
    """Like _draw_duel_skeleton but adds HUD overlay: time, distance, exchange badge."""
    img = _draw_duel_skeleton(rgb, red_lms, blue_lms, w, h, red_validity, blue_validity,
                               red_ang_vel=red_ang_vel, blue_ang_vel=blue_ang_vel)

    # Semi-transparent bar at top
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, 36), (10, 14, 24), -1)
    cv2.addWeighted(overlay, 0.72, img, 0.28, 0, img)

    # Time stamp
    cv2.putText(img, f"t = {t:.1f}s", (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (190, 190, 190), 1, cv2.LINE_AA)

    # Distance (color-coded: green=close, amber=medium, gray=far)
    d_color = (30, 220, 100) if distance < 0.25 else (255, 160, 30) if distance < 0.42 else (160, 160, 160)
    dist_lbl = f"dist {distance:.3f}"
    (tw, _), _ = cv2.getTextSize(dist_lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
    cv2.putText(img, dist_lbl, (w // 2 - tw // 2, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, d_color, 1, cv2.LINE_AA)

    # Exchange badge (top-right)
    if exchange:
        badge = "INTERCAMBIO"
        (bw, _), _ = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, 0.56, 2)
        cv2.putText(img, badge, (w - bw - 10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.56, (0, 220, 255), 2, cv2.LINE_AA)

    # Confidence badges below athlete bboxes
    for lms, conf, color, validity in [
        (red_lms,  red_conf,  (220, 50, 50),  red_validity),
        (blue_lms, blue_conf, (40, 140, 255), blue_validity),
    ]:
        if lms is None:
            continue
        bbox = _clean_pose_bbox(lms, w, h, validity)
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        lbl = f"{conf:.0%}"
        cv2.putText(img, lbl, (x0 + 4, min(h - 4, y1 + 16)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    # Distance line between hip centers
    if red_lms is not None and blue_lms is not None:
        try:
            def _hip_center(lms_):
                lhip = lms_[_L["L_hip"]]
                rhip = lms_[_L["R_hip"]]
                return (int((lhip.x + rhip.x) / 2 * w), int((lhip.y + rhip.y) / 2 * h))
            rp = _hip_center(red_lms)
            bp = _hip_center(blue_lms)
            cv2.line(img, rp, bp, (200, 200, 50), 1, cv2.LINE_AA)
            mid = ((rp[0] + bp[0]) // 2, (rp[1] + bp[1]) // 2 - 6)
            cv2.putText(img, f"{distance:.2f}", mid,
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, (200, 200, 50), 1, cv2.LINE_AA)
        except Exception as exc:
            _logger.debug("No se pudo dibujar linea de distancia dual: %s", exc)

    return img


def simulate_duel_ecg_imu(duel_result: dict, for_target: str = "blue") -> dict:
    """
    Generates synthetic ECG and IMU signals for one athlete from duel analysis data.

    HR model differentiates fight vs. rest phases using rounds data:
      - Fight: activity-based ramp (155–192 bpm)
      - Rest:  exponential recovery toward ~82 bpm (tau ≈ 28 s)

    Returns:
        ecg:        list of {t, hr, rr_ms}      — sampled at ~1 Hz
        imu:        list of {t, g, event}        — one entry per exchange + background noise
        rest_bands: list of {t0, t1}             — rest period windows for graph shading
        note:       str explaining the simulation
    """
    frames = duel_result.get("frames", [])
    if not frames:
        return {"ecg": [], "imu": [], "rest_bands": [],
                "note": "Sin datos de duelo para simular."}
    rng = np.random.default_rng(42)

    t_key    = "t"
    move_key = f"{for_target}_move"
    opp      = "red" if for_target == "blue" else "blue"

    # ── Phase 1: activity timeline (1-s buckets) ──────────────────────────────
    max_t   = max(f[t_key] for f in frames)
    n_bins  = max(4, int(max_t) + 1)
    activity_bins = [0.0] * n_bins
    count_bins    = [0]   * n_bins

    for f in frames:
        b = min(n_bins - 1, int(f[t_key]))
        move  = float(f.get(move_key, 0.0) or 0.0)
        opp_m = float(f.get(f"{opp}_move", 0.0) or 0.0)
        exch  = 1.0 if f.get("exchange") else 0.0
        activity_bins[b] += move * 12.0 + opp_m * 6.0 + exch * 0.8
        count_bins[b] += 1

    raw = [activity_bins[i] / max(1, count_bins[i]) for i in range(n_bins)]
    ema, alpha = [0.0] * n_bins, 0.30
    ema[0] = raw[0]
    for i in range(1, n_bins):
        ema[i] = alpha * raw[i] + (1 - alpha) * ema[i - 1]
    act_max  = max(ema) or 1.0
    act_norm = [min(1.0, v / act_max) for v in ema]

    # ── Phase 2: HR model with fight/rest phases ──────────────────────────────
    # Get rest windows from rounds data (nested under "duel" key in full result)
    rounds      = (duel_result.get("duel") or {}).get("rounds") or []
    rest_ranges = [(float(r["t_start"]), float(r["t_end"]))
                   for r in rounds if r.get("phase") == "rest"]
    rest_bands  = [{"t0": t0, "t1": t1} for t0, t1 in rest_ranges]

    def _in_rest(t_s: float) -> bool:
        return any(t0 <= t_s <= t1 for t0, t1 in rest_ranges)

    HR_BASE, HR_PEAK, HR_REST = 155.0, 192.0, 82.0
    # Recovery tau: ~28 s → exp(-1/28) ≈ 0.965 per second
    _REST_DECAY = float(np.exp(-1.0 / 28.0))
    _FIGHT_ALPHA = 0.35  # approach speed toward fight HR target

    hr_current = HR_BASE
    hr_series  = []
    for i in range(n_bins):
        t = float(i)
        if _in_rest(t):
            # Exponential recovery toward resting HR
            hr_current = HR_REST + (hr_current - HR_REST) * _REST_DECAY
            hr_current = round(max(HR_REST, hr_current), 1)
        else:
            # Fight: smoothly approach activity-based target
            target     = HR_BASE + (HR_PEAK - HR_BASE) * act_norm[i]
            hr_current = hr_current + _FIGHT_ALPHA * (target - hr_current)
            hr_current = round(min(HR_PEAK, max(HR_BASE - 10, hr_current)), 1)
        hr_series.append(hr_current)

    ecg_output = []
    for i, hr in enumerate(hr_series):
        rr = round(60_000.0 / hr, 1)
        rr_noise = rr + float(rng.uniform(-3.0, 3.0))
        ecg_output.append({"t": float(i), "hr": hr, "rr_ms": round(rr_noise, 1)})

    # ── Phase 3: IMU impact events ────────────────────────────────────────────
    imu_output = []
    for f in frames:
        t_f  = float(f[t_key])
        move = float(f.get(move_key, 0.0) or 0.0)
        opp_m = float(f.get(f"{opp}_move", 0.0) or 0.0)
        exch = f.get("exchange", False)

        if _in_rest(t_f):
            continue  # no impacts during rest
        if exch:
            dist  = float(f.get("distance", 0.30) or 0.30)
            g_val = round(min(6.0, max(1.0,
                (move + opp_m) / max(0.01, dist) * 18.0 +
                float(rng.uniform(0.2, 0.8)))), 2)
            imu_output.append({"t": t_f, "g": g_val, "event": "impacto"})
        elif move > 0.008:
            g_val = round(float(rng.uniform(0.3, 1.2)) * (move * 30.0 + 0.5), 2)
            imu_output.append({"t": t_f, "g": min(2.0, g_val), "event": "movimiento"})

    label = "Peto azul" if for_target == "blue" else "Peto rojo"
    return {
        "ecg":        ecg_output,
        "imu":        imu_output,
        "rest_bands": rest_bands,
        "note": (
            f"SIMULADO — Generado a partir del análisis de movimiento ({label}). "
            "No son datos de sensor real. Usar solo como referencia táctica."
        ),
        "for_target": for_target,
        "max_hr":  max(e["hr"] for e in ecg_output) if ecg_output else 0,
        "avg_hr":  round(float(np.mean([e["hr"] for e in ecg_output])), 1) if ecg_output else 0,
        "impacts": sum(1 for e in imu_output if e["event"] == "impacto"),
    }


# ── Análisis principal ───────────────────────────────────────────────────────

def _sport_key(sport: str | None) -> str:
    s = (sport or "").strip().lower()
    if "taekwondo" in s or "tkd" in s:
        return "taekwondo"
    if "box" in s:
        return "boxeo"
    return "combate"


def _summary_value(summary: dict, key: str, field: str, default: float = 0.0) -> float:
    try:
        return float((summary.get(key) or {}).get(field, default))
    except (TypeError, ValueError):
        return default


def _joint_rom(summary: dict, key: str) -> float:
    return round(_summary_value(summary, key, "max") - _summary_value(summary, key, "min"), 1)


def _detect_chamber_angles(frames_data: list, fps: float) -> dict:
    """
    Detecta eventos de pateo en TKD buscando mínimos locales del ángulo de rodilla.

    Un 'chamber' válido requiere:
    - Antecedente: algún frame previo (ventana 8 frames) con rodilla >= 140° (postura de guardia/parado)
    - Punto mínimo: ángulo de rodilla <= 120° (rodilla recogida = cámara)
    - Posterior: algún frame siguiente (ventana 10 frames) con rodilla >= 130° (extensión / retorno)

    Retorna dict con chamber_l / chamber_r, cada uno con:
      min   – ángulo mínimo de cámara detectado (°), None si no hubo kicks
      avg   – promedio de cámaras detectadas (°)
      count – número de eventos de pateo detectados
    """
    ENTRY_MIN   = 140.0   # rodilla en guardia: >= este valor antes del kick
    CHAMBER_MAX = 120.0   # cámara apretada: rodilla <= este valor
    EXIT_MIN    = 130.0   # retorno tras kick: rodilla >= este valor
    WINDOW_PRE  = 8       # frames de búsqueda hacia atrás (entry)
    WINDOW_POST = 10      # frames de búsqueda hacia adelante (exit)
    MIN_SKIP    = max(2, int(fps * 0.10))  # ~100 ms entre eventos (evita doble conteo)

    result = {}
    for side in ("l", "r"):
        key = f"knee_{side}"
        series = [f[key] for f in frames_data if isinstance(f.get(key), (int, float))]
        if len(series) < 6:
            result[f"chamber_{side}"] = {"min": None, "avg": None, "count": 0}
            continue

        # Media móvil de 3 frames para reducir ruido de landmarks
        smoothed = []
        for i in range(len(series)):
            window = series[max(0, i - 1): i + 2]
            smoothed.append(sum(window) / len(window))

        chambers = []
        i = 1
        while i < len(smoothed) - 1:
            # Mínimo local
            if smoothed[i] <= smoothed[i - 1] and smoothed[i] <= smoothed[i + 1]:
                if smoothed[i] <= CHAMBER_MAX:
                    pre_slice  = smoothed[max(0, i - WINDOW_PRE): i]
                    post_slice = smoothed[i + 1: min(len(smoothed), i + 1 + WINDOW_POST)]
                    entry_ok = any(v >= ENTRY_MIN for v in pre_slice)
                    exit_ok  = any(v >= EXIT_MIN  for v in post_slice)
                    if entry_ok and exit_ok:
                        chambers.append(round(smoothed[i], 1))
                        i += MIN_SKIP
                        continue
            i += 1

        if chambers:
            result[f"chamber_{side}"] = {
                "min":   round(min(chambers), 1),
                "avg":   round(sum(chambers) / len(chambers), 1),
                "count": len(chambers),
            }
        else:
            result[f"chamber_{side}"] = {"min": None, "avg": None, "count": 0}

    return result


def _build_biomech_reading(summary: dict, frames_data: list, sport: str | None = None) -> dict:
    """
    Heurística deportiva sobre los ángulos ya calculados.
    No diagnostica técnica; convierte la lectura en focos útiles para coach/atleta.
    """
    sport_key = _sport_key(sport)
    frames_analyzed = int(summary.get("frames_analyzed") or 0)
    poses_detected = int(summary.get("poses_detected") or len(frames_data or []))
    quality_ratio = round(poses_detected / max(frames_analyzed, 1), 2)

    rom = {k: _joint_rom(summary, k) for k in (
        "knee_l", "knee_r", "elbow_l", "elbow_r", "hip_l", "hip_r", "shoulder_l", "shoulder_r"
    )}
    knee_asym = round(abs(_summary_value(summary, "knee_l", "avg") - _summary_value(summary, "knee_r", "avg")), 1)
    hip_asym = round(abs(_summary_value(summary, "hip_l", "avg") - _summary_value(summary, "hip_r", "avg")), 1)
    elbow_asym = round(abs(_summary_value(summary, "elbow_l", "avg") - _summary_value(summary, "elbow_r", "avg")), 1)
    shoulder_asym = round(abs(_summary_value(summary, "shoulder_l", "avg") - _summary_value(summary, "shoulder_r", "avg")), 1)
    lower_rom = round(float(np.mean([rom["knee_l"], rom["knee_r"], rom["hip_l"], rom["hip_r"]])), 1)
    upper_rom = round(float(np.mean([rom["elbow_l"], rom["elbow_r"], rom["shoulder_l"], rom["shoulder_r"]])), 1)
    elbow_avg = round(float(np.mean([
        _summary_value(summary, "elbow_l", "avg"),
        _summary_value(summary, "elbow_r", "avg"),
    ])), 1)

    insights = []
    if quality_ratio < 0.40:
        insights.append({
            "level": "alert",
            "title": "Muestra limitada",
            "text": "Se detectó pose en pocos frames. Usa mejor encuadre, luz y cuerpo completo antes de sacar conclusiones finas.",
        })
    elif quality_ratio < 0.65:
        insights.append({
            "level": "warn",
            "title": "Muestra usable con cautela",
            "text": "La lectura sirve como orientación, pero conviene repetir con el atleta más visible para comparar técnica.",
        })
    else:
        insights.append({
            "level": "ok",
            "title": "Muestra útil",
            "text": "La pose fue suficientemente visible para revisar rangos y asimetrías generales.",
        })

    if sport_key == "taekwondo":
        if lower_rom >= 35:
            insights.append({
                "level": "ok",
                "title": "Piernas con amplitud",
                "text": "Buen rango de rodilla/cadera para revisar pateo, distancia y cambios de altura.",
            })
        elif lower_rom >= 22:
            insights.append({
                "level": "warn",
                "title": "Amplitud moderada de pierna",
                "text": "Puede faltar recorrido en cadera o rodilla. Revisa cámara lateral y movilidad antes de exigir pateo alto.",
            })
        else:
            insights.append({
                "level": "alert",
                "title": "Poca variación de piernas",
                "text": "El video muestra poco cambio angular en tren inferior; puede ser encuadre pobre o acción muy estática.",
            })
        max_lower_asym = max(knee_asym, hip_asym)
        if max_lower_asym > 18:
            insights.append({
                "level": "alert",
                "title": "Asimetría de pateo",
                "text": "Rodilla/cadera difieren bastante entre lados. Comparar pierna dominante vs no dominante antes de competir.",
            })
        elif max_lower_asym > 10:
            insights.append({
                "level": "warn",
                "title": "Asimetría moderada",
                "text": "Hay diferencia lateral. Puede ser normal por guardia, pero conviene revisar equilibrio y retorno tras patear.",
            })
        else:
            insights.append({
                "level": "ok",
                "title": "Simetría aceptable de tren inferior",
                "text": "No aparece una diferencia lateral grande en rodilla/cadera dentro de esta muestra.",
            })
        # ── Chamber angle insight (TKD exclusivo) ────────────────────────────
        ch_l = summary.get("chamber_l") or {}
        ch_r = summary.get("chamber_r") or {}
        ch_count = (ch_l.get("count") or 0) + (ch_r.get("count") or 0)
        if ch_count > 0:
            ch_mins = [v for v in [ch_l.get("min"), ch_r.get("min")] if v is not None]
            best_chamber = min(ch_mins) if ch_mins else None
            if best_chamber is not None:
                if best_chamber < 85:
                    insights.append({
                        "level": "ok",
                        "title": f"Cámara explosiva ({best_chamber}°)",
                        "text": (
                            f"Se detectaron {ch_count} kick(s). "
                            "La rodilla alcanza menos de 85° antes de la extensión — "
                            "cámara apretada típica de competidores de élite WT."
                        ),
                    })
                elif best_chamber < 100:
                    insights.append({
                        "level": "warn",
                        "title": f"Cámara funcional ({best_chamber}°)",
                        "text": (
                            f"Se detectaron {ch_count} kick(s). "
                            "La cámara es funcional pero hay margen para apretarla "
                            "por debajo de 90° para mayor velocidad y disimulo."
                        ),
                    })
                else:
                    insights.append({
                        "level": "alert",
                        "title": f"Cámara telegráfica ({best_chamber}°)",
                        "text": (
                            f"Se detectaron {ch_count} kick(s). "
                            "La rodilla no se recoge lo suficiente antes de la extensión "
                            "(> 100°), lo que reduce velocidad y anticipa el movimiento al rival."
                        ),
                    })
    elif sport_key == "boxeo":
        if elbow_avg > 150:
            insights.append({
                "level": "warn",
                "title": "Brazos muy extendidos en promedio",
                "text": "Puede indicar golpes largos sin retorno suficiente. Revisa recuperación de guardia tras jab/cross.",
            })
        elif upper_rom >= 30:
            insights.append({
                "level": "ok",
                "title": "Buen rango de tren superior",
                "text": "Hay variación suficiente en codo/hombro para revisar golpeo, defensa y retorno a guardia.",
            })
        else:
            insights.append({
                "level": "warn",
                "title": "Tren superior poco variable",
                "text": "El video muestra poca acción de brazos; puede ser encuadre, sombra suave o baja intensidad.",
            })
        if max(knee_asym, hip_asym) > 16:
            insights.append({
                "level": "warn",
                "title": "Base desigual",
                "text": "La diferencia en rodilla/cadera sugiere revisar balance, transferencia de peso y salida tras golpeo.",
            })
        else:
            insights.append({
                "level": "ok",
                "title": "Base estable",
                "text": "La muestra no marca una asimetría fuerte de base para desplazamiento o golpeo.",
            })
        if max(elbow_asym, shoulder_asym) > 20:
            insights.append({
                "level": "warn",
                "title": "Diferencia entre lados en golpeo",
                "text": "Codo/hombro muestran diferencia lateral. Revisa si mano dominante vuelve a guardia igual que la otra.",
            })
    else:
        if lower_rom >= 28 or upper_rom >= 28:
            insights.append({
                "level": "ok",
                "title": "Movimiento suficiente para revisar técnica",
                "text": "La muestra tiene cambios angulares útiles para comparar postura y rangos de acción.",
            })
        else:
            insights.append({
                "level": "warn",
                "title": "Movimiento limitado",
                "text": "Hay poca variación articular; repetir con una acción más clara puede dar una lectura más útil.",
            })

    focus = [
        "Repetir con cámara fija y cuerpo completo si la calidad baja de 65%.",
        "Comparar lado dominante vs no dominante cuando la asimetría supere 10-15 grados.",
    ]
    if sport_key == "taekwondo":
        focus.append("Priorizar cadera, rodilla y retorno de guardia después de patear.")
    elif sport_key == "boxeo":
        focus.append("Priorizar retorno de manos, transferencia de peso y base tras cada golpe.")
    else:
        focus.append("Priorizar rangos, simetría y estabilidad antes de subir intensidad.")

    metrics: dict = {
        "lower_rom": lower_rom,
        "upper_rom": upper_rom,
        "knee_asym": knee_asym,
        "hip_asym": hip_asym,
        "elbow_asym": elbow_asym,
        "shoulder_asym": shoulder_asym,
        **{f"rom_{k}": v for k, v in rom.items()},
    }
    # Exponer métricas de chamber para TKD (usadas en UI y PDF)
    if sport_key == "taekwondo":
        _ch_l = summary.get("chamber_l") or {}
        _ch_r = summary.get("chamber_r") or {}
        metrics["chamber_min_l"]  = _ch_l.get("min")
        metrics["chamber_min_r"]  = _ch_r.get("min")
        metrics["chamber_avg_l"]  = _ch_l.get("avg")
        metrics["chamber_avg_r"]  = _ch_r.get("avg")
        metrics["kick_count_l"]   = _ch_l.get("count", 0)
        metrics["kick_count_r"]   = _ch_r.get("count", 0)

    return {
        "sport": sport_key,
        "quality_ratio": quality_ratio,
        "metrics": metrics,
        "insights": insights[:4],
        "focus": focus,
    }


def _confidence_label(score: float) -> str:
    if score >= 75:
        return "Alta"
    if score >= 50:
        return "Media"
    return "Baja"


def _build_biomech_coaching(summary: dict, frames_data: list, biomech: dict, target_info: dict) -> dict:
    """Translate pose metrics into plain-language coaching actions."""
    metrics = biomech.get("metrics") or {}
    sport_key = biomech.get("sport") or "combate"
    selected = int(target_info.get("selected_frames") or len(frames_data or []))
    sampled = int(summary.get("frames_analyzed") or selected or 1)
    target_conf = float(target_info.get("confidence") or 0.0)
    track_continuity = float(target_info.get("continuity") or target_conf or 0.0)
    quality_ratio = float(biomech.get("quality_ratio") or 0.0)
    pose_quality = float(summary.get("pose_quality_avg") or 1.0)
    warning_frames = int(summary.get("landmark_warning_frames") or 0)
    warning_ratio = warning_frames / max(selected, 1)

    confidence_score = (
        target_conf * 35.0
        + track_continuity * 15.0
        + quality_ratio * 30.0
        + pose_quality * 20.0
        - min(20.0, warning_ratio * 20.0)
    )
    confidence_score = round(max(0.0, min(100.0, confidence_score)))
    confidence_label = _confidence_label(confidence_score)

    lower_rom = float(metrics.get("lower_rom") or 0.0)
    upper_rom = float(metrics.get("upper_rom") or 0.0)
    knee_asym = float(metrics.get("knee_asym") or 0.0)
    hip_asym = float(metrics.get("hip_asym") or 0.0)
    elbow_asym = float(metrics.get("elbow_asym") or 0.0)
    shoulder_asym = float(metrics.get("shoulder_asym") or 0.0)
    lower_asym = max(knee_asym, hip_asym)
    upper_asym = max(elbow_asym, shoulder_asym)

    target_label = target_info.get("label") or "Objetivo"
    graph_explanation = (
        f"La gráfica resume cambios de ángulo por articulación en el tiempo para {target_label}. "
        f"En esta muestra se usaron {selected} de {sampled} frames; el tren inferior muestra "
        f"{lower_rom:.1f} grados de rango medio y {lower_asym:.1f} grados de diferencia lateral. "
        f"La continuidad del seguimiento fue de {round(track_continuity * 100)}%."
    )
    if warning_frames:
        graph_explanation += (
            f" Se limpiaron landmarks dudosos en {warning_frames} frame(s), por eso algunas líneas "
            "de brazos pueden verse incompletas: es preferible perder un punto a medir una persona externa."
        )

    if confidence_score < 50:
        meaning = (
            "La lectura sirve como orientación inicial, pero no como conclusión fina. "
            "El video tiene ruido suficiente como para priorizar confirmación visual, encuadre y repetición."
        )
    elif sport_key == "taekwondo":
        if lower_asym > 16:
            meaning = (
                "La gráfica sugiere diferencia relevante entre lados en rodilla/cadera. "
                "Puede ser por guardia, pierna dominante o retorno desigual después de patear."
            )
        elif lower_rom < 22:
            meaning = (
                "La acción de piernas aparece limitada en los frames válidos. "
                "Puede indicar poca amplitud real o que el video no capturó bien el momento de pateo."
            )
        else:
            meaning = (
                "La lectura de tren inferior es aprovechable: permite revisar base, amplitud de cadera/rodilla "
                "y retorno de guardia sin depender solo de la percepción del coach."
            )
    elif sport_key == "boxeo":
        if upper_asym > 18:
            meaning = (
                "La gráfica marca diferencia entre lados en brazo/hombro. "
                "Conviene revisar retorno de guardia y transferencia de peso tras jab/cross."
            )
        else:
            meaning = (
                "La lectura ayuda a revisar si el tren superior cambia de forma suficiente durante golpeo, "
                "defensa y retorno de manos."
            )
    else:
        meaning = (
            "La gráfica permite separar lo que parece movimiento real de lo que puede ser ruido del video. "
            "Úsala como apoyo para decidir qué repetir o aislar en el siguiente bloque."
        )

    actions = []
    drills = []
    if confidence_score < 60:
        actions.append("Repetir una toma corta con cámara fija, cuerpo completo y menos personas cruzando el plano.")
        actions.append("Usar el selector de peto y revisar el frame anotado antes de aceptar la lectura.")
    if warning_frames:
        actions.append("Tomar las métricas de brazos con cautela; priorizar rodilla, cadera y desplazamiento si el torso está limpio.")

    if sport_key == "taekwondo":
        if lower_asym > 12:
            actions.append("Comparar pierna dominante contra no dominante en la misma técnica y misma distancia.")
            drills.append({
                "name": "Pateo espejo controlado",
                "dose": "3 series de 6 repeticiones por pierna",
                "why": "igualar cámara de rodilla/cadera y retorno a guardia sin velocidad máxima.",
            })
        if lower_rom < 28:
            actions.append("Separar movilidad de cadera y chamber antes de medir velocidad o potencia.")
            drills.append({
                "name": "Chamber hold + extension",
                "dose": "4 bloques de 20 segundos por pierna",
                "why": "mejorar control de cadera y altura antes de patear rápido.",
            })
        drills.append({
            "name": "Retorno de guardia post-patada",
            "dose": "3 rounds de 45 segundos",
            "why": "evitar que la pierna caiga sin control después del impacto o amague.",
        })
    elif sport_key == "boxeo":
        if upper_asym > 12:
            actions.append("Revisar si la mano dominante vuelve a guardia igual que la mano adelantada.")
        drills.extend([
            {
                "name": "Jab-cross con pausa de guardia",
                "dose": "4 rounds de 1 minuto",
                "why": "hacer visible el retorno de manos y reducir asimetría de hombro/codo.",
            },
            {
                "name": "Paso-golpe-salida",
                "dose": "3 rounds de 90 segundos",
                "why": "conectar transferencia de peso con base estable después del golpe.",
            },
        ])
    else:
        drills.append({
            "name": "Repetición técnica lenta",
            "dose": "3 bloques de 60 segundos",
            "why": "confirmar si la gráfica refleja técnica real o ruido de captura.",
        })

    if not actions:
        actions.append("Usar esta lectura como línea base y repetir con la misma técnica para comparar progreso.")
    if len(drills) < 2:
        drills.append({
            "name": "Video corto de control",
            "dose": "2 tomas de 20-30 segundos",
            "why": "mejorar confianza de medición antes de sacar conclusiones competitivas.",
        })

    limitations = []
    if selected < 12:
        limitations.append("Pocos frames válidos: interpretar tendencias generales, no detalles finos.")
    if confidence_score < 60:
        limitations.append("Confianza media/baja: repetir captura antes de decidir carga alta.")
    if warning_frames:
        limitations.append("Hubo oclusiones o personas externas: algunas articulaciones fueron descartadas.")

    return {
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "graph_explanation": graph_explanation,
        "meaning": meaning,
        "actions": actions[:4],
        "drills": drills[:4],
        "limitations": limitations[:3],
    }


def _summarize_target_frames(
    frames_data: list,
    sampled: int,
    processed: int,
    target_key: str,
    time_limited: bool,
    candidates_seen: int,
    target_misses: int,
    confidences: list,
    continuity_scores: list,
    track_rejections: int,
    rejection_reasons: dict,
) -> dict:
    def _stats(key: str) -> dict:
        vals = [f.get(key) for f in frames_data if isinstance(f.get(key), (int, float))]
        if not vals:
            return {"avg": "-", "max": "-", "min": "-"}
        return {
            "avg": round(float(np.mean(vals)), 1),
            "max": round(float(np.max(vals)), 1),
            "min": round(float(np.min(vals)), 1),
        }

    summary = {k: _stats(k) for k in _ANGLE_KEYS}
    summary["frames_analyzed"] = sampled
    summary["poses_detected"] = processed
    summary["duration_s"] = round(frames_data[-1]["t"], 1) if frames_data else 0
    summary["time_limited"] = time_limited
    summary["target"] = target_key
    summary["pose_candidates"] = candidates_seen
    summary["target_misses"] = target_misses
    summary["target_coverage"] = round(processed / max(sampled, 1), 3)
    summary["track_rejections"] = track_rejections
    summary["track_continuity"] = round(float(np.mean(continuity_scores)), 3) if continuity_scores else 0.0
    summary["track_rejection_reasons"] = rejection_reasons
    pose_qualities = [f.get("pose_quality") for f in frames_data if isinstance(f.get("pose_quality"), (int, float))]
    summary["pose_quality_avg"] = round(float(np.mean(pose_qualities)), 2) if pose_qualities else 1.0
    summary["landmark_warning_frames"] = sum(1 for f in frames_data if f.get("landmark_warnings"))
    _wb: dict = {}
    for _f in frames_data:
        for _note in (_f.get("landmark_warnings") or []):
            _wb[_note] = _wb.get(_note, 0) + 1
    summary["warning_breakdown"] = _wb
    summary["selection_confidence"] = round(float(np.mean(confidences)), 3) if confidences else 0.0
    return summary


def _target_info_from_summary(
    target_key: str,
    summary: dict,
    selected_frames: int,
    candidates_seen: int,
    target_misses: int,
    track_state: dict | None,
) -> dict:
    raw_confidence = float(summary.get("selection_confidence", 0.0) or 0.0)
    sampled_total = int(summary.get("frames_analyzed") or selected_frames or 1)
    coverage = round(selected_frames / max(sampled_total, 1), 3)
    visible_confidence = round(raw_confidence * (0.45 + 0.55 * coverage), 3)
    return {
        "key": target_key,
        "label": _TARGET_LABELS.get(target_key, target_key),
        "candidates_seen": candidates_seen,
        "selected_frames": selected_frames,
        "coverage": coverage,
        "misses": target_misses,
        "confidence": visible_confidence,
        "selection_confidence_raw": raw_confidence,
        "track_id": (track_state or {}).get("track_id"),
        "continuity": summary.get("track_continuity", 0.0),
        "track_rejections": summary.get("track_rejections", 0),
        "rejection_reasons": summary.get("track_rejection_reasons", {}),
    }


def _build_duel_reading(
    duel_frames: list,
    red_summary: dict,
    blue_summary: dict,
    red_target: dict,
    blue_target: dict,
    sampled: int,
    sport: str | None = None,
) -> dict:
    sport_key = _sport_key(sport)
    paired = len(duel_frames)
    distances = [float(f.get("distance", 0.0)) for f in duel_frames if isinstance(f.get("distance"), (int, float))]
    avg_distance = round(float(np.mean(distances)), 3) if distances else 0.0
    min_distance = round(float(np.min(distances)), 3) if distances else 0.0
    max_distance = round(float(np.max(distances)), 3) if distances else 0.0
    distance_variability = round(float(np.std(distances)), 3) if len(distances) > 1 else 0.0
    exchange_frames = [f for f in duel_frames if f.get("exchange")]
    red_pressure = round(float(sum(max(0.0, float(f.get("red_toward", 0.0) or 0.0)) for f in duel_frames)), 3)
    blue_pressure = round(float(sum(max(0.0, float(f.get("blue_toward", 0.0) or 0.0)) for f in duel_frames)), 3)
    red_lower = float((_build_biomech_reading(red_summary, [], sport_key).get("metrics") or {}).get("lower_rom") or 0.0)
    blue_lower = float((_build_biomech_reading(blue_summary, [], sport_key).get("metrics") or {}).get("lower_rom") or 0.0)
    red_upper = float((_build_biomech_reading(red_summary, [], sport_key).get("metrics") or {}).get("upper_rom") or 0.0)
    blue_upper = float((_build_biomech_reading(blue_summary, [], sport_key).get("metrics") or {}).get("upper_rom") or 0.0)

    if red_pressure > blue_pressure * 1.18 and red_pressure - blue_pressure > 0.025:
        pressure_label = "Rojo presiona más"
        pressure_text = "El peto rojo acumula más movimiento hacia el rival en los frames pareados."
    elif blue_pressure > red_pressure * 1.18 and blue_pressure - red_pressure > 0.025:
        pressure_label = "Azul presiona más"
        pressure_text = "El peto azul acumula más movimiento hacia el rival en los frames pareados."
    else:
        pressure_label = "Presión equilibrada"
        pressure_text = "No aparece una diferencia clara de presión; conviene revisar los intercambios manualmente."

    if avg_distance >= 0.38:
        distance_label = "Distancia larga"
        distance_text = "La separación media es amplia; útil para leer entradas, fintas y manejo de rango."
    elif avg_distance <= 0.24:
        distance_label = "Distancia corta"
        distance_text = "La separación media es cerrada; revisar clinch, choque, contraataque o intercambio inmediato."
    else:
        distance_label = "Distancia media"
        distance_text = "La distancia media permite observar presión, retroceso y momentos de entrada."

    if abs(red_lower - blue_lower) >= 8:
        leg_text = (
            "Rojo muestra más amplitud de pierna." if red_lower > blue_lower
            else "Azul muestra más amplitud de pierna."
        )
    else:
        leg_text = "La amplitud de tren inferior está relativamente pareja en esta muestra."

    exchange_count = len(exchange_frames)
    paired_ratio = paired / max(sampled, 1)
    red_conf = float(red_target.get("confidence") or 0.0)
    blue_conf = float(blue_target.get("confidence") or 0.0)
    red_cont = float(red_target.get("continuity") or 0.0)
    blue_cont = float(blue_target.get("continuity") or 0.0)
    confidence_score = round(max(0.0, min(100.0, (
        paired_ratio * 35.0
        + ((red_conf + blue_conf) / 2.0) * 30.0
        + ((red_cont + blue_cont) / 2.0) * 25.0
        + min(10.0, paired * 0.8)
    ))))
    confidence_label = _confidence_label(confidence_score)

    insights = [
        {"level": "ok" if paired_ratio >= 0.35 else "warn", "title": "Frames pareados", "text": f"Se analizaron {paired} momentos con rojo y azul visibles a la vez."},
        {"level": "ok", "title": distance_label, "text": distance_text},
        {"level": "warn" if exchange_count else "ok", "title": "Intercambios detectados", "text": f"Se marcaron {exchange_count} posibles momentos de intercambio por cercanía y movimiento simultáneo."},
        {"level": "ok" if pressure_label == "Presión equilibrada" else "warn", "title": pressure_label, "text": pressure_text},
        {"level": "ok", "title": "Actividad de pierna", "text": leg_text},
    ]

    actions = [
        "Usar la gráfica de distancia para ubicar entradas, retrocesos y momentos de choque antes de revisar ángulos finos.",
        "Confirmar visualmente los intercambios marcados: CombatIQ los detecta por cercanía y movimiento, no por puntuación oficial.",
    ]
    if confidence_score < 60:
        actions.append("Repetir con cámara fija y ambos petos completos en plano para subir confianza del análisis dual.")
    if sport_key == "taekwondo":
        actions.append("Revisar si la presión coincide con intentos de pateo, amagos o salida lateral.")
    elif sport_key == "boxeo":
        actions.append("Revisar si la presión coincide con jab de entrada, paso atrás o contraataque.")

    graph_explanation = (
        "La gráfica principal del modo dual muestra la distancia entre centros corporales de rojo y azul. "
        "Cuando la curva baja y ambos se mueven, CombatIQ marca posible intercambio. "
        f"Confianza de lectura: {confidence_label} ({confidence_score}%)."
    )
    meaning = (
        f"{pressure_label}. {distance_label.lower()} con distancia media {avg_distance:.3f}. "
        "Esto no declara ganador ni puntos: ayuda al coach a encontrar momentos tácticos para revisar en video."
    )
    limitations = [
        "No se juzgan puntos oficiales ni ganador.",
        "La presión se estima por movimiento hacia el rival, no por intención táctica real.",
    ]
    if paired_ratio < 0.35:
        limitations.append("Pocos frames con ambos atletas visibles: interpretar como tendencia, no conclusión.")

    return {
        "sport": sport_key,
        "frames_paired": paired,
        "metrics": {
            "avg_distance": avg_distance,
            "min_distance": min_distance,
            "max_distance": max_distance,
            "distance_variability": distance_variability,
            "exchange_count": exchange_count,
            "red_pressure": red_pressure,
            "blue_pressure": blue_pressure,
            "pressure_label": pressure_label,
            "red_lower_rom": round(red_lower, 1),
            "blue_lower_rom": round(blue_lower, 1),
            "red_upper_rom": round(red_upper, 1),
            "blue_upper_rom": round(blue_upper, 1),
            "red_peak_ang_vel": round(max((f.get("red_ang_vel_max", 0.0) for f in duel_frames), default=0.0), 1),
            "blue_peak_ang_vel": round(max((f.get("blue_ang_vel_max", 0.0) for f in duel_frames), default=0.0), 1),
            "red_avg_ang_vel": round(float(np.mean([f.get("red_ang_vel_max", 0.0) for f in duel_frames])), 1) if duel_frames else 0.0,
            "blue_avg_ang_vel": round(float(np.mean([f.get("blue_ang_vel_max", 0.0) for f in duel_frames])), 1) if duel_frames else 0.0,
        },
        "insights": insights[:5],
        "coaching": {
            "confidence_score": confidence_score,
            "confidence_label": confidence_label,
            "graph_explanation": graph_explanation,
            "meaning": meaning,
            "actions": actions[:4],
            "limitations": limitations[:3],
        },
    }


def _detect_rounds(
    duel_frames: list,
    num_rounds: int | None = None,
    rest_threshold: float = 0.006,
    min_rest_s: float = 7.0,
) -> list[dict]:
    """
    Segment duel_frames into rounds and rest periods.

    Option C logic:
      1. Auto-detect: scan movement timeline; if both athletes have
         combined movement < rest_threshold for >=min_rest_s → rest period.
      2. Fallback: if auto-detect finds fewer boundaries than expected,
         divide video mathematically using num_rounds.

    Returns list of dicts: {round, t_start, t_end, phase, exchange_count,
                             avg_red_move, avg_blue_move, avg_distance,
                             red_peak_ang_vel, blue_peak_ang_vel}
    """
    if not duel_frames:
        return []

    total_t = duel_frames[-1]["t"]
    dt_sample = duel_frames[1]["t"] - duel_frames[0]["t"] if len(duel_frames) > 1 else 1.0

    # ── Auto-detect rest windows ──────────────────────────────────────────────
    rest_start = None
    boundaries: list[tuple[float, float]] = []  # (rest_start, rest_end)
    for f in duel_frames:
        combined_move = float(f.get("red_move", 0.0) or 0.0) + float(f.get("blue_move", 0.0) or 0.0)
        if combined_move < rest_threshold:
            if rest_start is None:
                rest_start = f["t"]
        else:
            if rest_start is not None:
                dur = f["t"] - rest_start
                if dur >= min_rest_s:
                    boundaries.append((rest_start, f["t"]))
                rest_start = None
    if rest_start is not None:
        dur = total_t - rest_start
        if dur >= min_rest_s:
            boundaries.append((rest_start, total_t))

    # ── Fallback: mathematical division ──────────────────────────────────────
    n = int(num_rounds or 0)
    if n > 0 and len(boundaries) < max(1, n - 1):
        # Heuristic: assume equal-length rounds with 1-min rest between them
        # Estimate fight time per round: total_t / n (rough)
        fight_per_round = total_t / n
        rest_est = min(60.0, fight_per_round * 0.2)
        cycle = fight_per_round + rest_est
        boundaries = []
        for i in range(1, n):
            r_start = i * cycle - rest_est
            r_end   = i * cycle
            if r_start < total_t:
                boundaries.append((max(0.0, r_start), min(total_t, r_end)))

    # ── Build segments from boundaries ───────────────────────────────────────
    def _frames_in(t0: float, t1: float) -> list:
        return [f for f in duel_frames if t0 <= f["t"] < t1]

    def _seg_metrics(frames: list) -> dict:
        if not frames:
            return {}
        moves_r = [float(f.get("red_move", 0) or 0) for f in frames]
        moves_b = [float(f.get("blue_move", 0) or 0) for f in frames]
        dists   = [float(f.get("distance", 0) or 0) for f in frames]
        ang_r   = [float(f.get("red_ang_vel_max", 0) or 0) for f in frames]
        ang_b   = [float(f.get("blue_ang_vel_max", 0) or 0) for f in frames]
        return {
            "exchange_count":    sum(1 for f in frames if f.get("exchange")),
            "avg_red_move":      round(float(np.mean(moves_r)), 4),
            "avg_blue_move":     round(float(np.mean(moves_b)), 4),
            "avg_distance":      round(float(np.mean(dists)), 3),
            "red_peak_ang_vel":  round(max(ang_r, default=0.0), 1),
            "blue_peak_ang_vel": round(max(ang_b, default=0.0), 1),
        }

    segments = []
    fight_num = 0
    cursor = 0.0
    for rest_t0, rest_t1 in sorted(boundaries):
        # Fight segment before this rest
        fight_frames = _frames_in(cursor, rest_t0)
        if fight_frames:
            fight_num += 1
            segments.append({
                "round": fight_num,
                "t_start": round(cursor, 2),
                "t_end": round(rest_t0, 2),
                "phase": "fight",
                **_seg_metrics(fight_frames),
            })
        # Rest segment
        rest_frames = _frames_in(rest_t0, rest_t1)
        segments.append({
            "round": fight_num,
            "t_start": round(rest_t0, 2),
            "t_end": round(rest_t1, 2),
            "phase": "rest",
            **_seg_metrics(rest_frames),
        })
        cursor = rest_t1

    # Final fight segment (after last rest or if no boundaries at all)
    if cursor < total_t:
        fight_frames = _frames_in(cursor, total_t + 1.0)
        if fight_frames:
            fight_num += 1
            segments.append({
                "round": fight_num,
                "t_start": round(cursor, 2),
                "t_end": round(total_t, 2),
                "phase": "fight",
                **_seg_metrics(fight_frames),
            })

    return segments


def _analyze_video_duel(
    video_path: str,
    sample_every: int,
    max_frames: int,
    max_seconds: float,
    sport: str | None,
    num_poses: int,
    num_rounds: int | None = None,
) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"error": "No se pudo abrir el video. Formato no soportado."}

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    options = mp_vision.PoseLandmarkerOptions(
        base_options=base_opts.BaseOptions(model_asset_path=_MODEL_PATH),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=max(2, num_poses),
        min_pose_detection_confidence=0.30,
        min_pose_presence_confidence=0.35,
        min_tracking_confidence=0.50,
    )

    target_frames = {"red": [], "blue": []}
    duel_frames = []
    tracks = {"red": None, "blue": None}
    processed_counts = {"red": 0, "blue": 0}
    misses = {"red": 0, "blue": 0}
    confidences = {"red": [], "blue": []}
    continuity = {"red": [], "blue": []}
    track_rejections = {"red": 0, "blue": 0}
    rejection_reasons = {"red": {}, "blue": {}}
    candidates_seen = 0
    frame_idx = 0
    sampled = 0
    time_limited = False
    t_start = time.perf_counter()
    best_frame_rgb = None
    best_score = -1.0
    best_frame_is_clean = False
    prev_pair = None
    prev_angles: dict[str, dict] = {"red": {}, "blue": {}}
    prev_frame_idx: dict[str, int] = {"red": -1, "blue": -1}
    all_key_frames: list[tuple[float, float, bytes]] = []  # (score, t_s, jpeg_bytes)

    try:
        with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
            while sampled < max_frames:
                if time.perf_counter() - t_start > max_seconds:
                    time_limited = True
                    break

                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % sample_every != 0:
                    frame_idx += 1
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_img)
                sampled += 1
                frame_targets = {}

                if result.pose_landmarks:
                    candidates = [
                        _describe_pose(rgb, lms, w, h, idx)
                        for idx, lms in enumerate(result.pose_landmarks)
                    ]
                    candidates_seen += len(candidates)
                    picked = _select_duel_poses(candidates, tracks)

                    for key in ("red", "blue"):
                        item = picked.get(key) or {}
                        lms = item.get("landmarks")
                        selected = item.get("selected") or {}
                        if lms is None:
                            misses[key] += 1
                            reason = selected.get("rejection_reason")
                            if reason:
                                track_rejections[key] += 1
                                rejection_reasons[key][reason] = rejection_reasons[key].get(reason, 0) + 1
                            continue

                        tracks[key] = _update_track_state(tracks[key], selected, key, frame_idx)
                        validity, validity_notes = _landmark_validity(rgb, lms, w, h, key)
                        identity_quality = float(selected.get("identity_quality", selected.get(f"{key}_identity_quality", 1.0)) or 0.0)
                        identity_notes = list(selected.get("identity_notes", selected.get(f"{key}_identity_notes", [])) or [])
                        if identity_quality < 0.75:
                            validity_notes = list(validity_notes) + ["pose_contaminada"] + identity_notes[:3]
                        edge_margin = _bbox_edge_margin(selected, w, h)
                        if edge_margin < 0.012:
                            validity_notes = list(validity_notes) + ["cuerpo_recortado"]
                        pose_quality = float(np.mean([1.0 if validity.get(name, True) else 0.0 for name in _L]))
                        pose_quality = min(pose_quality, max(0.25, identity_quality))
                        angles = _extract_angles(lms, w, h, validity=validity)
                        # Angular velocity: degrees per second between consecutive sampled frames
                        _prev_fidx = prev_frame_idx.get(key, -1)
                        _dt = (frame_idx - _prev_fidx) / fps if _prev_fidx >= 0 else sample_every / fps
                        prev_frame_idx[key] = frame_idx
                        _prev_ang = prev_angles[key]
                        _ang_vels = [
                            abs(angles[k] - _prev_ang[k]) / _dt
                            for k in angles
                            if angles.get(k) is not None and _prev_ang.get(k) is not None and _dt > 0
                        ]
                        ang_vel_max = round(max(_ang_vels), 1) if _ang_vels else 0.0
                        prev_angles[key] = {k: v for k, v in angles.items() if v is not None}
                        vis = float(selected.get("visibility") or _pose_visibility(lms))
                        confidence = float(selected.get("selection_confidence", 0.0) or 0.0)
                        affinity = float(selected.get("track_affinity", 1.0) or 0.0)
                        confidences[key].append(confidence)
                        continuity[key].append(affinity)
                        processed_counts[key] += 1

                        frame_record = {
                            "t": round(frame_idx / fps, 2),
                            "frame": frame_idx,
                            "target": key,
                            "track_id": tracks[key].get("track_id"),
                            "visibility": round(vis, 2),
                            "pose_quality": round(pose_quality, 2),
                            "landmark_warnings": validity_notes,
                            "track_continuity": round(affinity, 3),
                            "selection_confidence": round(confidence, 3),
                            "identity_quality": round(identity_quality, 3),
                            "identity_warnings": identity_notes,
                            "body_overlap": round(float(selected.get("body_overlap", 0.0) or 0.0), 3),
                            "edge_margin": edge_margin,
                            "red_score": selected.get("red_score", 0.0),
                            "blue_score": selected.get("blue_score", 0.0),
                            "cx": selected.get("cx"),
                            "cy": selected.get("cy"),
                            "area": selected.get("area"),
                            "ang_vel_max": ang_vel_max,
                            **angles,
                        }
                        target_frames[key].append(frame_record)
                        frame_targets[key] = {
                            "landmarks": lms,
                            "selected": selected,
                            "validity": validity,
                            "record": frame_record,
                        }

                    if "red" in frame_targets and "blue" in frame_targets:
                        red_sel = frame_targets["red"]["selected"]
                        blue_sel = frame_targets["blue"]["selected"]
                        red_cx, red_cy = float(red_sel.get("cx", 0.0)), float(red_sel.get("cy", 0.0))
                        blue_cx, blue_cy = float(blue_sel.get("cx", 0.0)), float(blue_sel.get("cy", 0.0))
                        distance = float(np.sqrt((red_cx - blue_cx) ** 2 + (red_cy - blue_cy) ** 2))
                        red_move = blue_move = red_toward = blue_toward = distance_delta = 0.0
                        if prev_pair:
                            red_move = float(np.sqrt((red_cx - prev_pair["red_cx"]) ** 2 + (red_cy - prev_pair["red_cy"]) ** 2))
                            blue_move = float(np.sqrt((blue_cx - prev_pair["blue_cx"]) ** 2 + (blue_cy - prev_pair["blue_cy"]) ** 2))
                            direction = 1.0 if prev_pair["blue_cx"] >= prev_pair["red_cx"] else -1.0
                            red_toward = (red_cx - prev_pair["red_cx"]) * direction
                            blue_toward = (prev_pair["blue_cx"] - blue_cx) * direction
                            distance_delta = distance - float(prev_pair["distance"])

                        exchange = bool(distance <= 0.30 and (red_move + blue_move) >= 0.018)
                        duel_frame = {
                            "t": round(frame_idx / fps, 2),
                            "frame": frame_idx,
                            "distance": round(distance, 3),
                            "distance_delta": round(distance_delta, 3),
                            "exchange": exchange,
                            "red_cx": round(red_cx, 4),
                            "blue_cx": round(blue_cx, 4),
                            "red_move": round(red_move, 4),
                            "blue_move": round(blue_move, 4),
                            "red_toward": round(red_toward, 4),
                            "blue_toward": round(blue_toward, 4),
                            "red_confidence_raw": frame_targets["red"]["record"].get("selection_confidence", 0.0),
                            "blue_confidence_raw": frame_targets["blue"]["record"].get("selection_confidence", 0.0),
                            "red_confidence": round(
                                float(frame_targets["red"]["record"].get("selection_confidence", 0.0) or 0.0)
                                * float(frame_targets["red"]["record"].get("pose_quality", 0.0) or 0.0),
                                3,
                            ),
                            "blue_confidence": round(
                                float(frame_targets["blue"]["record"].get("selection_confidence", 0.0) or 0.0)
                                * float(frame_targets["blue"]["record"].get("pose_quality", 0.0) or 0.0),
                                3,
                            ),
                            "red_pose_quality": frame_targets["red"]["record"].get("pose_quality", 0.0),
                            "blue_pose_quality": frame_targets["blue"]["record"].get("pose_quality", 0.0),
                            "red_identity_quality": frame_targets["red"]["record"].get("identity_quality", 0.0),
                            "blue_identity_quality": frame_targets["blue"]["record"].get("identity_quality", 0.0),
                            "red_body_overlap": frame_targets["red"]["record"].get("body_overlap", 0.0),
                            "blue_body_overlap": frame_targets["blue"]["record"].get("body_overlap", 0.0),
                            "red_edge_margin": frame_targets["red"]["record"].get("edge_margin", 0.0),
                            "blue_edge_margin": frame_targets["blue"]["record"].get("edge_margin", 0.0),
                            "red_ang_vel_max": frame_targets["red"]["record"].get("ang_vel_max", 0.0),
                            "blue_ang_vel_max": frame_targets["blue"]["record"].get("ang_vel_max", 0.0),
                        }
                        duel_frames.append(duel_frame)
                        prev_pair = {
                            "red_cx": red_cx,
                            "red_cy": red_cy,
                            "blue_cx": blue_cx,
                            "blue_cy": blue_cy,
                            "distance": distance,
                        }

                        frame_score = (
                            float(duel_frame["red_confidence"]) + float(duel_frame["blue_confidence"])
                            + float(duel_frame["red_pose_quality"]) * 0.4
                            + float(duel_frame["blue_pose_quality"]) * 0.4
                            + (0.25 if exchange else 0.0)
                        )
                        critical_notes = {
                            "pose_contaminada",
                            "cuerpo_cruzado",
                            "oclusion_parcial",
                            "esqueleto_colapsado",
                            "casco_sin_peto_coherente",
                            "cuerpo_recortado",
                            "color_contrario_en_pose",
                            "casco_contrario",
                        }
                        red_bad = any(note in critical_notes for note in frame_targets["red"]["record"].get("landmark_warnings", []))
                        blue_bad = any(note in critical_notes for note in frame_targets["blue"]["record"].get("landmark_warnings", []))
                        if red_bad:
                            frame_score -= 0.55
                        if blue_bad:
                            frame_score -= 0.55
                        max_body_overlap = max(
                            float(duel_frame.get("red_body_overlap", 0.0) or 0.0),
                            float(duel_frame.get("blue_body_overlap", 0.0) or 0.0),
                        )
                        frame_score -= max_body_overlap * 0.45
                        min_frame_conf = min(
                            float(duel_frame.get("red_confidence", 0.0) or 0.0),
                            float(duel_frame.get("blue_confidence", 0.0) or 0.0),
                        )
                        min_keyframe_torso = min(
                            float(frame_targets["red"]["record"].get("red_score", 0.0) or 0.0),
                            float(frame_targets["blue"]["record"].get("blue_score", 0.0) or 0.0),
                        )
                        frame_is_clean = (
                            min_frame_conf >= 0.62
                            and not red_bad
                            and not blue_bad
                            and max_body_overlap < 0.58
                            and min_keyframe_torso >= 0.085
                            and min(
                                float(duel_frame.get("red_edge_margin", 0.0) or 0.0),
                                float(duel_frame.get("blue_edge_margin", 0.0) or 0.0),
                            ) >= 0.012
                        )
                        if (
                            (frame_is_clean and (not best_frame_is_clean or frame_score > best_score))
                            or (not best_frame_is_clean and best_frame_rgb is None and frame_score > best_score)
                        ):
                            best_score = frame_score
                            best_frame_is_clean = frame_is_clean
                            best_frame_rgb = _draw_duel_skeleton(
                                rgb,
                                frame_targets["red"]["landmarks"],
                                frame_targets["blue"]["landmarks"],
                                w,
                                h,
                                frame_targets["red"]["validity"],
                                frame_targets["blue"]["validity"],
                                red_ang_vel=float(duel_frame.get("red_ang_vel_max") or 0.0),
                                blue_ang_vel=float(duel_frame.get("blue_ang_vel_max") or 0.0),
                            )

                        # Capture annotated key frame (for gallery)
                        try:
                            if frame_is_clean:
                                # Keep the biomechanical analysis intact, but avoid encoding
                                # hundreds of JPEGs when only a handful can be displayed.
                                sep_bonus = float(duel_frame.get("distance", 0.0)) * 2.0
                                kf_score = frame_score + sep_bonus - (0.4 if exchange else 0.0)
                                should_keep_kf = (
                                    len(all_key_frames) < _DUEL_KEYFRAME_CANDIDATES
                                    or kf_score > min(item[0] for item in all_key_frames)
                                )
                                if should_keep_kf:
                                    kf_img = _draw_duel_annotated_frame(
                                        rgb,
                                        frame_targets["red"]["landmarks"],
                                        frame_targets["blue"]["landmarks"],
                                        w, h,
                                        frame_targets["red"]["validity"],
                                        frame_targets["blue"]["validity"],
                                        t=round(frame_idx / fps, 1),
                                        distance=distance,
                                        exchange=exchange,
                                        red_conf=float(duel_frame["red_confidence"]),
                                        blue_conf=float(duel_frame["blue_confidence"]),
                                        red_ang_vel=float(duel_frame.get("red_ang_vel_max") or 0.0),
                                        blue_ang_vel=float(duel_frame.get("blue_ang_vel_max") or 0.0),
                                    )
                                    bgr_kf = cv2.cvtColor(kf_img, cv2.COLOR_RGB2BGR)
                                    ok_kf, buf_kf = cv2.imencode(".jpg", bgr_kf, [cv2.IMWRITE_JPEG_QUALITY, 80])
                                    if ok_kf:
                                        all_key_frames.append((kf_score, round(frame_idx / fps, 1), buf_kf.tobytes()))
                                        if len(all_key_frames) > _DUEL_KEYFRAME_CANDIDATES:
                                            all_key_frames.sort(key=lambda item: -item[0])
                                            del all_key_frames[_DUEL_KEYFRAME_CANDIDATES:]
                        except Exception as exc:
                            _logger.debug("No se pudo preparar frame clave dual: %s", exc)
                else:
                    misses["red"] += 1
                    misses["blue"] += 1

                frame_idx += 1
    finally:
        cap.release()

    if not duel_frames:
        return {
            "error": (
                "No se pudo aislar simultáneamente peto rojo y peto azul. "
                "Prueba con mejor encuadre, más luz o analiza primero cada peto por separado."
            ),
            "fps": fps,
            "total_frames": total_frames,
            "frames_analyzed": sampled,
            "time_limited": time_limited,
        }

    red_summary = _summarize_target_frames(
        target_frames["red"], sampled, processed_counts["red"], "red", time_limited,
        candidates_seen, misses["red"], confidences["red"], continuity["red"],
        track_rejections["red"], rejection_reasons["red"],
    )
    blue_summary = _summarize_target_frames(
        target_frames["blue"], sampled, processed_counts["blue"], "blue", time_limited,
        candidates_seen, misses["blue"], confidences["blue"], continuity["blue"],
        track_rejections["blue"], rejection_reasons["blue"],
    )
    # TKD-only: ángulo de cámara de pateo para cada peleador
    if _sport_key(sport) == "taekwondo":
        if target_frames["red"]:
            red_summary.update(_detect_chamber_angles(target_frames["red"], fps))
        if target_frames["blue"]:
            blue_summary.update(_detect_chamber_angles(target_frames["blue"], fps))
    red_target = _target_info_from_summary("red", red_summary, processed_counts["red"], candidates_seen, misses["red"], tracks["red"])
    blue_target = _target_info_from_summary("blue", blue_summary, processed_counts["blue"], candidates_seen, misses["blue"], tracks["blue"])
    red_biomech = _build_biomech_reading(red_summary, target_frames["red"], sport=sport)
    blue_biomech = _build_biomech_reading(blue_summary, target_frames["blue"], sport=sport)
    red_biomech["target"] = red_target
    blue_biomech["target"] = blue_target

    duel_reading = _build_duel_reading(duel_frames, red_summary, blue_summary, red_target, blue_target, sampled, sport=sport)
    target_confidence = round(float(np.mean([red_target.get("confidence", 0.0), blue_target.get("confidence", 0.0)])), 3)
    target_continuity = round(float(np.mean([red_target.get("continuity", 0.0), blue_target.get("continuity", 0.0)])), 3)
    target_coverage = round(len(duel_frames) / max(sampled, 1), 3)
    target_info = {
        "key": "duel",
        "label": _TARGET_LABELS["duel"],
        "candidates_seen": candidates_seen,
        "selected_frames": len(duel_frames),
        "coverage": target_coverage,
        "misses": misses["red"] + misses["blue"],
        "confidence": target_confidence,
        "track_id": "red-blue-dual",
        "continuity": target_continuity,
        "track_rejections": track_rejections["red"] + track_rejections["blue"],
        "rejection_reasons": {
            "red": rejection_reasons["red"],
            "blue": rejection_reasons["blue"],
        },
    }
    summary = {
        "mode": "red_vs_blue",
        "frames_analyzed": sampled,
        "poses_detected": len(duel_frames),
        "paired_frames": len(duel_frames),
        "red_selected_frames": processed_counts["red"],
        "blue_selected_frames": processed_counts["blue"],
        "duration_s": round(duel_frames[-1]["t"], 1) if duel_frames else 0,
        "time_limited": time_limited,
        "target": "duel",
        "pose_candidates": candidates_seen,
        "target_misses": target_info["misses"],
        "target_coverage": target_coverage,
        "track_rejections": target_info["track_rejections"],
        "track_continuity": target_continuity,
    }
    biomech = {
        "sport": _sport_key(sport),
        "quality_ratio": round(len(duel_frames) / max(sampled, 1), 2),
        "target": target_info,
        "metrics": duel_reading["metrics"],
        "insights": duel_reading["insights"],
        "focus": duel_reading["coaching"].get("actions", [])[:3],
        "coaching": duel_reading["coaching"],
        "duel": duel_reading,
    }
    rounds = _detect_rounds(duel_frames, num_rounds=num_rounds)
    duel = {
        **duel_reading,
        "frames": duel_frames,
        "rounds": rounds,
        "red": {
            "summary": red_summary,
            "target": red_target,
            "biomech": red_biomech,
            "frames": target_frames["red"],
        },
        "blue": {
            "summary": blue_summary,
            "target": blue_target,
            "biomech": blue_biomech,
            "frames": target_frames["blue"],
        },
    }

    annotated_b64 = None
    if best_frame_rgb is not None:
        bgr = cv2.cvtColor(best_frame_rgb, cv2.COLOR_RGB2BGR)
        ok_frame, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok_frame:
            annotated_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

    # ── Key-frame gallery: up to 6 temporally diverse frames ─────────────────
    _KF_MAX     = 6
    _KF_GAP_S   = 6.0   # minimum seconds between selected frames
    all_key_frames.sort(key=lambda x: -x[0])   # sort by score desc
    selected_kf: list[tuple[float, float, bytes]] = []
    for score, t_s, jpeg in all_key_frames:
        if not any(abs(t_s - t_sel) < _KF_GAP_S for _, t_sel, _ in selected_kf):
            selected_kf.append((score, t_s, jpeg))
        if len(selected_kf) >= _KF_MAX:
            break
    selected_kf.sort(key=lambda x: x[1])  # chronological order for display
    annotated_frames = [
        "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        for _, _, jpeg in selected_kf
    ]
    annotated_frames_meta = [
        {"t": t_s, "score": round(float(score), 3)}
        for score, t_s, _ in selected_kf
    ]

    return {
        "error": None,
        "fps": round(fps, 1),
        "total_frames": total_frames,
        "frames": duel_frames,
        "summary": summary,
        "biomech": biomech,
        "target": target_info,
        "duel": duel,
        "annotated_frame": annotated_b64,
        "annotated_frames": annotated_frames,
        "annotated_frames_meta": annotated_frames_meta,
        "time_limited": time_limited,
        "processing_s": round(time.perf_counter() - t_start, 2),
        "analyzer_version": _ANALYZER_VERSION,
    }


def analyze_video(
    video_path: str,
    sample_every: int = 10,
    max_frames: int = 220,
    max_seconds: float | None = None,
    sport: str | None = None,
    target: str | None = None,
    num_rounds: int | None = None,
) -> dict:
    """
    Analiza la postura en un video y devuelve ángulos articulares por frame.

    Args:
        video_path:   Ruta al archivo de video.
        sample_every: Analiza 1 de cada N frames (10 → ~3 fps desde 30 fps).
        max_frames:   Máximo de frames a analizar.
        max_seconds:  Presupuesto máximo de procesamiento para evitar bloqueos largos.
        sport:        Deporte de referencia para traducir la lectura a focos útiles.

    Returns dict:
        error           str|None
        fps             float
        total_frames    int
        frames          list[dict]  — t, frame, ángulos, visibility
        summary         dict        — avg/max/min por articulación
        annotated_frame str|None    — data-URI JPEG con skeleton
    """
    requested_max_frames = max_frames
    requested_max_seconds = max_seconds
    sample_every = max(1, int(sample_every or 1))
    max_frames = max(1, int(max_frames or 220))
    if max_seconds is None:
        try:
            max_seconds = float(os.getenv("COMBATIQ_POSE_MAX_SECONDS", "25") or 25)
        except (TypeError, ValueError):
            max_seconds = 25.0
    max_seconds = max(1.0, float(max_seconds or 25.0))
    target_key = _normalize_target(target)
    target_label = _TARGET_LABELS.get(target_key, _TARGET_LABELS["auto"])
    try:
        num_poses = int(os.getenv("COMBATIQ_POSE_NUM_POSES", "4") or 4)
    except (TypeError, ValueError):
        num_poses = 4
    num_poses = max(1, min(6, num_poses))

    if not os.path.exists(_MODEL_PATH):
        return {"error": f"Modelo no encontrado: {_MODEL_PATH}. Ejecuta el script de descarga."}

    if not os.path.exists(video_path):
        return {"error": f"Video no encontrado: {video_path}"}

    if target_key == "duel":
        # Duel mode needs more frames to cover a full combat (3-6 minutes).
        # Auto-scale only when the caller kept the single-mode defaults; explicit
        # limits must be respected for previews/tests and to avoid UI freezes.
        try:
            default_single_frames = int(requested_max_frames or 220) == 220
        except (TypeError, ValueError):
            default_single_frames = True
        try:
            duel_env_frames = int(os.getenv("COMBATIQ_DUEL_MAX_FRAMES", "1200") or 1200)
        except (TypeError, ValueError):
            duel_env_frames = 1200
        try:
            duel_env_seconds = float(os.getenv("COMBATIQ_DUEL_MAX_SECONDS", "400") or 400)
        except (TypeError, ValueError):
            duel_env_seconds = 400.0
        if default_single_frames:
            duel_max_frames = max(max_frames, duel_env_frames)
        else:
            duel_max_frames = max_frames
        if requested_max_seconds is None:
            duel_max_seconds = max(max_seconds, duel_env_seconds)
        else:
            duel_max_seconds = max_seconds
        return _analyze_video_duel(
            video_path,
            sample_every=sample_every,
            max_frames=duel_max_frames,
            max_seconds=duel_max_seconds,
            sport=sport,
            num_poses=num_poses,
            num_rounds=num_rounds,
        )

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"error": "No se pudo abrir el video. Formato no soportado."}

    fps          = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w            = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h            = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Construir el landmarker
    options = mp_vision.PoseLandmarkerOptions(
        base_options=base_opts.BaseOptions(model_asset_path=_MODEL_PATH),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=num_poses,
        min_pose_detection_confidence=0.30,
        min_pose_presence_confidence=0.35,
        min_tracking_confidence=0.50,
    )

    frames_data     = []
    best_frame_rgb  = None
    best_visibility = -1.0
    best_score      = -1.0
    frame_idx       = 0
    processed       = 0
    sampled         = 0
    time_limited    = False
    t_start         = time.perf_counter()
    track_state     = None
    candidates_seen = 0
    target_misses   = 0
    confidences     = []
    continuity_scores = []
    track_rejections = 0
    rejection_reasons: dict[str, int] = {}

    try:
        with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
            while sampled < max_frames:
                if time.perf_counter() - t_start > max_seconds:
                    time_limited = True
                    break

                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % sample_every != 0:
                    frame_idx += 1
                    continue

                rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_img)
                sampled += 1

                if result.pose_landmarks:
                    candidates = [
                        _describe_pose(rgb, lms, w, h, idx)
                        for idx, lms in enumerate(result.pose_landmarks)
                    ]
                    candidates_seen += len(candidates)
                    lms, selected = _select_pose(candidates, target_key, track_state)
                    if lms is None:
                        target_misses += 1
                        reason = (selected or {}).get("rejection_reason")
                        if reason:
                            track_rejections += 1
                            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                        frame_idx += 1
                        continue

                    track_state = _update_track_state(track_state, selected, target_key, frame_idx)
                    vis = float(selected.get("visibility") or _pose_visibility(lms))
                    confidences.append(float(selected.get("selection_confidence", 0.0) or 0.0))
                    continuity_scores.append(float(selected.get("track_affinity", 1.0) or 0.0))
                    validity, validity_notes = _landmark_validity(rgb, lms, w, h, target_key)
                    identity_quality = float(selected.get("identity_quality", selected.get(f"{target_key}_identity_quality", 1.0)) or 0.0)
                    identity_notes = list(selected.get("identity_notes", selected.get(f"{target_key}_identity_notes", [])) or [])
                    if target_key in ("red", "blue") and identity_quality < 0.75:
                        validity_notes = list(validity_notes) + ["pose_contaminada"] + identity_notes[:3]
                    edge_margin = _bbox_edge_margin(selected, w, h)
                    if target_key in ("red", "blue") and edge_margin < 0.012:
                        validity_notes = list(validity_notes) + ["cuerpo_recortado"]
                    pose_quality = float(np.mean([1.0 if validity.get(name, True) else 0.0 for name in _L]))
                    if target_key in ("red", "blue"):
                        pose_quality = min(pose_quality, max(0.25, identity_quality))
                    angs = _extract_angles(lms, w, h, validity=validity)

                    frames_data.append({
                        "t":          round(frame_idx / fps, 2),
                        "frame":      frame_idx,
                        "visibility": round(vis, 2),
                        "pose_quality": round(pose_quality, 2),
                        "landmark_warnings": validity_notes,
                        "target":     target_key,
                        "track_id":   track_state.get("track_id"),
                        "track_continuity": round(float(selected.get("track_affinity", 1.0) or 0.0), 3),
                        "red_score":  selected.get("red_score", 0.0),
                        "blue_score": selected.get("blue_score", 0.0),
                        "selection_confidence": selected.get("selection_confidence", 0.0),
                        "identity_quality": round(identity_quality, 3),
                        "identity_warnings": identity_notes,
                        "body_overlap": round(float(selected.get("body_overlap", 0.0) or 0.0), 3),
                        "edge_margin": edge_margin,
                        **angs,
                    })

                    frame_score = (
                        vis
                        + float(selected.get("selection_confidence", 0.0) or 0.0) * 0.35
                        + pose_quality * 0.30
                    )
                    if frame_score > best_score:
                        best_score = frame_score
                        best_visibility = vis
                        best_frame_rgb  = _draw_skeleton(rgb, lms, w, h, label=target_label, validity=validity)

                    processed += 1
                else:
                    target_misses += 1

                frame_idx += 1
    finally:
        cap.release()

    if not frames_data:
        if target_key in ("red", "blue"):
            return {
                "error": (
                    f"No se pudo aislar {target_label.lower()} en el video. "
                    "Prueba con mejor luz/encuadre o usa izquierda/derecha como respaldo."
                ),
                "fps": fps,
                "total_frames": total_frames,
                "frames_analyzed": sampled,
                "time_limited": time_limited,
            }
        return {
            "error": "No se detectó ninguna persona en el video. Verifica que haya un deportista visible.",
            "fps": fps,
            "total_frames": total_frames,
            "frames_analyzed": sampled,
            "time_limited": time_limited,
        }

    # ── Summary stats ────────────────────────────────────────────────────────
    def _stats(key: str) -> dict:
        vals = [f.get(key) for f in frames_data if isinstance(f.get(key), (int, float))]
        if not vals:
            return {"avg": "-", "max": "-", "min": "-"}
        return {
            "avg": round(float(np.mean(vals)), 1),
            "max": round(float(np.max(vals)), 1),
            "min": round(float(np.min(vals)), 1),
        }

    summary = {
        k: _stats(k)
        for k in ("knee_l", "knee_r", "elbow_l", "elbow_r",
                  "hip_l",  "hip_r",  "shoulder_l", "shoulder_r")
    }
    summary["frames_analyzed"] = sampled
    summary["poses_detected"]  = processed
    summary["duration_s"]      = round(frames_data[-1]["t"], 1) if frames_data else 0
    summary["time_limited"]    = time_limited
    summary["target"]          = target_key
    summary["pose_candidates"] = candidates_seen
    summary["target_misses"]   = target_misses
    summary["target_coverage"] = round(processed / max(sampled, 1), 3)
    summary["track_rejections"] = track_rejections
    summary["track_continuity"] = round(float(np.mean(continuity_scores)), 3) if continuity_scores else 0.0
    summary["track_rejection_reasons"] = rejection_reasons
    pose_qualities = [f.get("pose_quality") for f in frames_data if isinstance(f.get("pose_quality"), (int, float))]
    summary["pose_quality_avg"] = round(float(np.mean(pose_qualities)), 2) if pose_qualities else 1.0
    summary["landmark_warning_frames"] = sum(1 for f in frames_data if f.get("landmark_warnings"))
    _wb: dict = {}
    for _f in frames_data:
        for _note in (_f.get("landmark_warnings") or []):
            _wb[_note] = _wb.get(_note, 0) + 1
    summary["warning_breakdown"] = _wb
    # ── TKD-only: ángulo de cámara de pateo ──────────────────────────────────
    if _sport_key(sport) == "taekwondo" and frames_data:
        summary.update(_detect_chamber_angles(frames_data, fps))
    _raw_confidence = round(float(np.mean(confidences)), 3) if confidences else 0.0
    _coverage = round(processed / max(sampled, 1), 3)
    _visible_confidence = round(_raw_confidence * (0.45 + 0.55 * _coverage), 3)
    target_info = {
        "key": target_key,
        "label": target_label,
        "candidates_seen": candidates_seen,
        "selected_frames": processed,
        "coverage": _coverage,
        "misses": target_misses,
        "confidence": _visible_confidence,
        "selection_confidence_raw": _raw_confidence,
        "track_id": (track_state or {}).get("track_id"),
        "continuity": summary["track_continuity"],
        "track_rejections": track_rejections,
        "rejection_reasons": rejection_reasons,
    }
    biomech = _build_biomech_reading(summary, frames_data, sport=sport)
    biomech["target"] = target_info
    biomech["coaching"] = _build_biomech_coaching(summary, frames_data, biomech, target_info)

    # ── Skeleton frame → base64 JPEG ─────────────────────────────────────────
    annotated_b64 = None
    if best_frame_rgb is not None:
        bgr = cv2.cvtColor(best_frame_rgb, cv2.COLOR_RGB2BGR)
        ok_frame, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok_frame:
            annotated_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

    return {
        "error":           None,
        "fps":             round(fps, 1),
        "total_frames":    total_frames,
        "frames":          frames_data,
        "summary":         summary,
        "biomech":         biomech,
        "target":          target_info,
        "annotated_frame": annotated_b64,
        "time_limited":    time_limited,
        "processing_s":    round(time.perf_counter() - t_start, 2),
        "analyzer_version": _ANALYZER_VERSION,
    }
