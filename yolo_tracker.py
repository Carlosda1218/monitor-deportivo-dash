"""
yolo_tracker.py — Tracking multi-atleta con YOLOv8 + OpenVINO (Intel Iris Xe).

Detecta y separa los dos atletas (rojo/azul) por color de peto,
calcula velocidad de pateo (tobillo) y velocidad de desplazamiento (cadera)
en m/s usando calibración automática por altura del atleta.

Uso:
    from yolo_tracker import analyze_duel_speeds
    result = analyze_duel_speeds("data/uploads/combate.mp4")
"""

from __future__ import annotations
import os
import cv2
import numpy as np
import logging
import warnings
import supervision as sv
# ByteTrack moved to sv.tracker in supervision ≥0.28 but sv.ByteTrack still works
warnings.filterwarnings("ignore", message=".*ByteTrack.*deprecated.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*deprecated.*ByteTrack.*", category=FutureWarning)

_logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "yolov8n-pose_openvino_model")
_MODEL_XML  = os.path.join(_MODEL_DIR, "yolov8n-pose.xml")

# YOLOv8-pose keypoint indices (COCO 17-point skeleton)
_KP = {
    "nose":         0,
    "L_shoulder":   5,  "R_shoulder":  6,
    "L_elbow":      7,  "R_elbow":     8,
    "L_wrist":      9,  "R_wrist":    10,
    "L_hip":       11,  "R_hip":      12,
    "L_knee":      13,  "R_knee":     14,
    "L_ankle":     15,  "R_ankle":    16,
}

_REAL_ATHLETE_HEIGHT_M = 1.75   # reference height for pixel calibration
_INPUT_SIZE            = 640    # YOLOv8 input resolution
_MAX_DETECTIONS_PER_FRAME = 6   # keep extra candidates so referees do not hide athletes


# ── Model loading ─────────────────────────────────────────────────────────────

_compiled_model = None

def _get_model():
    global _compiled_model
    if _compiled_model is not None:
        return _compiled_model
    if not os.path.isfile(_MODEL_XML):
        raise FileNotFoundError(
            f"YOLOv8 OpenVINO model not found: {_MODEL_XML}\n"
            "Run: python -c \"from ultralytics import YOLO; YOLO('yolov8n-pose.pt').export(format='openvino')\""
        )
    import openvino as ov
    core = ov.Core()
    model = core.read_model(_MODEL_XML)
    # Try GPU (Iris Xe) first, but keep CPU as a safe demo fallback.
    available = set(core.available_devices)
    candidate_devices = ["GPU", "CPU"] if "GPU" in available else ["CPU"]
    errors = []
    for device in candidate_devices:
        try:
            _compiled_model = core.compile_model(model, device)
            _logger.info("YOLOv8 OpenVINO loaded on %s", device)
            return _compiled_model
        except Exception as exc:
            errors.append(f"{device}: {exc}")
            _logger.warning("YOLOv8 OpenVINO failed on %s: %s", device, exc)
    raise RuntimeError("Could not compile YOLOv8 OpenVINO model. " + " | ".join(errors))


# ── Pre/post processing ───────────────────────────────────────────────────────

def _preprocess(frame: np.ndarray):
    """Returns (blob, scale_x, scale_y, pad_x, pad_y) for letterbox resize."""
    h, w = frame.shape[:2]
    scale = _INPUT_SIZE / max(h, w)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(frame, (new_w, new_h))
    # Letterbox padding to 640×640
    pad_x = (_INPUT_SIZE - new_w) // 2
    pad_y = (_INPUT_SIZE - new_h) // 2
    canvas = np.zeros((_INPUT_SIZE, _INPUT_SIZE, 3), dtype=np.uint8)
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    blob = canvas[:, :, ::-1].astype(np.float32) / 255.0   # BGR→RGB, /255
    blob = np.expand_dims(blob.transpose(2, 0, 1), 0)       # HWC→NCHW
    return blob, scale, pad_x, pad_y


def _postprocess(output: np.ndarray, scale: float, pad_x: int, pad_y: int,
                 orig_w: int, orig_h: int, conf_thr: float | None = None):
    """
    Parses YOLOv8-pose output (1, 56, N) → list of dicts:
    {bbox: [x1,y1,x2,y2], conf: float, keypoints: np.ndarray shape (17,3)}
    Coordinates are in original frame pixels.

    Filters applied (in order):
      1. conf < _CONF_THR_PERSON  → reject low-confidence detections
      2. bbox area < _MIN_BBOX_AREA_PX2 → reject distant/small ghosts (crowd, scoreboard)
      3. valid keypoints < _MIN_KP_COUNT → reject non-persons (objects, referees w/o body)
      4. rank by conf × area — prefers large close athletes over small distant referees
    """
    preds = output[0].T   # (N, 56): x,y,w,h,conf,kp×17×3
    if conf_thr is None:
        conf_thr = _CONF_THR_PERSON

    detections = []
    for pred in preds:
        conf = float(pred[4])
        if conf < conf_thr:
            continue
        cx, cy, bw, bh = pred[:4]
        # Undo letterbox
        x1 = (cx - bw / 2 - pad_x) / scale
        y1 = (cy - bh / 2 - pad_y) / scale
        x2 = (cx + bw / 2 - pad_x) / scale
        y2 = (cy + bh / 2 - pad_y) / scale
        x1 = float(np.clip(x1, 0, orig_w))
        y1 = float(np.clip(y1, 0, orig_h))
        x2 = float(np.clip(x2, 0, orig_w))
        y2 = float(np.clip(y2, 0, orig_h))

        # Filter 2: minimum bbox area — rejects crowd/scoreboard detections
        area = (x2 - x1) * (y2 - y1)
        if area < _MIN_BBOX_AREA_PX2:
            continue

        kps_raw = pred[5:].reshape(17, 3)   # (kp_x, kp_y, kp_conf)
        kps = np.zeros((17, 3), dtype=np.float32)
        kps[:, 0] = (kps_raw[:, 0] - pad_x) / scale
        kps[:, 1] = (kps_raw[:, 1] - pad_y) / scale
        kps[:, 2] = kps_raw[:, 2]

        # Filter 3: minimum valid keypoint count — rejects objects that aren't persons
        valid_kps = int(np.sum(kps[:, 2] >= 0.3))
        if valid_kps < _MIN_KP_COUNT:
            continue

        # Score = conf × area: prefers large close athletes over small distant referees
        detections.append({"bbox": [x1, y1, x2, y2], "conf": conf,
                           "keypoints": kps, "_score": conf * area})

    # Keep top-2 by conf×area score
    detections.sort(key=lambda d: d["_score"], reverse=True)
    top_detections = detections[:_MAX_DETECTIONS_PER_FRAME]
    for d in top_detections:
        del d["_score"]
    return top_detections


# ── Vest color classification ─────────────────────────────────────────────────

def _vest_color(frame: np.ndarray, bbox: list) -> str | None:
    """Returns 'rojo' | 'azul' | None from torso HSV analysis."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    th = int((y2 - y1) * 0.25)
    roi = frame[y1 + th: y2 - th, x1:x2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red  = (cv2.inRange(hsv, (0, 80, 60), (15, 255, 255)) +
            cv2.inRange(hsv, (160, 80, 60), (180, 255, 255)))
    blue = cv2.inRange(hsv, (95, 80, 60), (135, 255, 255))
    rp = int(red.sum() // 255)
    bp = int(blue.sum() // 255)
    area = max(1, roi.shape[0] * roi.shape[1])
    red_ratio = rp / area
    blue_ratio = bp / area
    if max(red_ratio, blue_ratio) < 0.02:
        return None
    if red_ratio > blue_ratio * 1.25 and red_ratio - blue_ratio > 0.01:
        return "rojo"
    if blue_ratio > red_ratio * 1.25 and blue_ratio - red_ratio > 0.01:
        return "azul"
    return None


# ── IoU matching helper (links ByteTrack output back to raw YOLO keypoints) ───

def _iou(b1, b2) -> float:
    x1, y1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    x2, y2 = min(b1[2], b2[2]), min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter == 0:
        return 0.0
    a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    return inter / (a1 + a2 - inter + 1e-6)


def _match_to_raw(tracked_bbox: np.ndarray, raw_dets: list) -> int:
    """Returns index of raw_dets entry with highest IoU, or -1 if < 0.3."""
    best_iou, best_idx = 0.0, -1
    for i, d in enumerate(raw_dets):
        iou = _iou(tracked_bbox.tolist(), d["bbox"])
        if iou > best_iou:
            best_iou, best_idx = iou, i
    return best_idx if best_iou >= 0.3 else -1


# ── Calibration ───────────────────────────────────────────────────────────────

_TORSO_TO_HEIGHT_RATIO = 0.30  # torso (shoulder-to-hip) ≈ 30% of full height

def _calibrate(detections_per_frame: list) -> float | None:
    """
    Estimates scale (m/px) from median torso length (shoulder-mid → hip-mid).
    More stable than bbox height because it's invariant to kicks and crouching.
    Falls back to bbox height only if keypoints are unavailable.
    """
    torso_lengths = []
    bbox_heights = []
    for dets in detections_per_frame:
        for d in dets:
            kps = d.get("keypoints")
            if kps is not None and kps.shape == (17, 3):
                ls, rs = kps[5, :2], kps[6, :2]   # L/R shoulder
                lh, rh = kps[11, :2], kps[12, :2]  # L/R hip
                confs = [kps[5, 2], kps[6, 2], kps[11, 2], kps[12, 2]]
                if min(confs) >= 0.35:
                    shoulder_mid = (ls + rs) / 2
                    hip_mid      = (lh + rh) / 2
                    torso_px = float(np.linalg.norm(shoulder_mid - hip_mid))
                    if torso_px > 15:
                        torso_lengths.append(torso_px)
            x1, y1, x2, y2 = d["bbox"]
            h_px = y2 - y1
            if h_px > 50:
                bbox_heights.append(h_px)

    if torso_lengths:
        real_torso_m = _REAL_ATHLETE_HEIGHT_M * _TORSO_TO_HEIGHT_RATIO
        return real_torso_m / float(np.median(torso_lengths))
    if bbox_heights:
        return _REAL_ATHLETE_HEIGHT_M / float(np.median(bbox_heights))
    return None


# ── Speed calculation ─────────────────────────────────────────────────────────

def _smooth(values: list, window: int = 3) -> list:
    """Simple moving average, ignoring None."""
    out = []
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        lo, hi = max(0, i - window // 2), min(len(values), i + window // 2 + 1)
        neighbours = [values[j] for j in range(lo, hi) if values[j] is not None]
        out.append(float(np.mean(neighbours)) if neighbours else v)
    return out


_MAX_KICK_SPEED_MS   = 17.0   # physiological cap: elite TKD ≈ 10-16 m/s (world record ~18)
_MAX_DISP_SPEED_MS   = 8.0    # running speed cap: sprint ~8 m/s
_KP_CONF_ANKLE       = 0.50   # higher threshold for extremity kps (noise-prone)
_KP_CONF_HIP         = 0.35   # hips are more stable

# ── Detection quality filters ─────────────────────────────────────────────────
_CONF_THR_PERSON   = 0.45    # raised from 0.35 — cuts crowd/scoreboard ghosts
_MIN_BBOX_AREA_PX2 = 3500    # ~59×59 px min — rejects distant small detections
_MIN_KP_COUNT      = 5       # at least 5 confident keypoints required (person check)
_MIN_TRACK_FRAMES  = 3       # ignore ByteTrack IDs seen in fewer frames (transient ghosts)


def _kp_speed(track: list, kp_idx: int, fps: float, scale: float,
              conf_thr: float = _KP_CONF_ANKLE,
              max_speed: float = _MAX_KICK_SPEED_MS) -> list:
    """
    track: list of (t, keypoints(17,3)) per frame
    Returns list of {t, speed_ms} for the given keypoint, capped at max_speed.
    """
    result = []
    for i in range(1, len(track)):
        t0, kp0 = track[i - 1]
        t1, kp1 = track[i]
        if kp0[kp_idx, 2] < conf_thr or kp1[kp_idx, 2] < conf_thr:
            continue   # low-confidence keypoint
        dx = (kp1[kp_idx, 0] - kp0[kp_idx, 0]) * scale
        dy = (kp1[kp_idx, 1] - kp0[kp_idx, 1]) * scale
        dt = t1 - t0
        if dt <= 0:
            continue
        speed = float(np.sqrt(dx ** 2 + dy ** 2) / dt)
        if speed > max_speed:
            continue   # keypoint noise / tracking artifact
        result.append({"t": round(t1, 2), "speed_ms": round(speed, 2)})
    return result


def _hip_center(kps: np.ndarray):
    """Returns (x, y) midpoint of both hips, or None if low confidence."""
    L, R = _KP["L_hip"], _KP["R_hip"]
    if kps[L, 2] < 0.3 and kps[R, 2] < 0.3:
        return None
    pts = [kps[i, :2] for i in (L, R) if kps[i, 2] >= 0.3]
    return np.mean(pts, axis=0)


def _displacement_speed(track: list, fps: float, scale: float) -> list:
    """Returns list of {t, speed_ms} for athlete displacement (hip center)."""
    result = []
    for i in range(1, len(track)):
        t0, kp0 = track[i - 1]
        t1, kp1 = track[i]
        c0, c1 = _hip_center(kp0), _hip_center(kp1)
        if c0 is None or c1 is None:
            continue
        dt = t1 - t0
        if dt <= 0:
            continue
        dx = (c1[0] - c0[0]) * scale
        dy = (c1[1] - c0[1]) * scale
        speed = float(np.sqrt(dx ** 2 + dy ** 2) / dt)
        if speed > _MAX_DISP_SPEED_MS:
            continue   # tracking artifact
        result.append({"t": round(t1, 2), "speed_ms": round(speed, 2)})
    return result


def _peak_kick_events(kick_speeds: list, window_s: float = 0.5,
                      min_speed: float = 3.0) -> list:
    """
    Finds local maxima in kick speed that exceed min_speed (m/s).
    Returns list of {t, speed_ms, label}.
    """
    if not kick_speeds:
        return []
    times  = [e["t"] for e in kick_speeds]
    speeds = [e["speed_ms"] for e in kick_speeds]
    events = []
    i = 0
    while i < len(speeds):
        if speeds[i] >= min_speed:
            # Find peak of this burst
            j = i
            while j < len(speeds) and (j == i or times[j] - times[j-1] < window_s):
                j += 1
            peak_idx = int(np.argmax(speeds[i:j])) + i
            events.append({
                "t":        times[peak_idx],
                "speed_ms": round(speeds[peak_idx], 2),
            })
            i = j
        else:
            i += 1
    return events


# ── Joint angle analysis (Phase 1: YOLO biomechanics) ────────────────────────

# (proximal, vertex, distal) — angle measured at vertex
_JOINT_ANGLES: dict[str, tuple[int, int, int]] = {
    "knee_l":  (_KP["L_hip"],      _KP["L_knee"],  _KP["L_ankle"]),
    "knee_r":  (_KP["R_hip"],      _KP["R_knee"],  _KP["R_ankle"]),
    "hip_l":   (_KP["L_shoulder"], _KP["L_hip"],   _KP["L_knee"]),
    "hip_r":   (_KP["R_shoulder"], _KP["R_hip"],   _KP["R_knee"]),
    "elbow_l": (_KP["L_shoulder"], _KP["L_elbow"], _KP["L_wrist"]),
    "elbow_r": (_KP["R_shoulder"], _KP["R_elbow"], _KP["R_wrist"]),
}


def _joint_angle(kps: np.ndarray, a: int, b: int, c: int,
                 conf_thr: float = 0.35) -> float | None:
    """Angle (degrees) at vertex b, vectors b→a and b→c. None if confidence too low."""
    if kps[a, 2] < conf_thr or kps[b, 2] < conf_thr or kps[c, 2] < conf_thr:
        return None
    ba = kps[a, :2] - kps[b, :2]
    bc = kps[c, :2] - kps[b, :2]
    denom = np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-9
    cos_a = float(np.clip(np.dot(ba, bc) / denom, -1.0, 1.0))
    return round(float(np.degrees(np.arccos(cos_a))), 1)


def _angular_velocity(series: list[tuple[float, float]],
                      max_deg_s: float = 2000.0) -> list[dict]:
    """
    series: [(t_s, angle_deg), ...]
    Returns [{t, ang_vel_degs}, ...] capped at max_deg_s (noise filter).
    """
    result = []
    for i in range(1, len(series)):
        t0, a0 = series[i - 1]
        t1, a1 = series[i]
        dt = t1 - t0
        if dt <= 0:
            continue
        av = abs(a1 - a0) / dt
        if av > max_deg_s:
            continue
        result.append({"t": round(t1, 2), "ang_vel_degs": round(av, 1)})
    return result


def _compute_duel_biomech(
    tracks: dict[str, list],
    fps: float,
    sample_every: int,
) -> dict:
    """
    Computes joint angles, ROM, bilateral asymmetry, and peak angular velocity
    for both athletes from YOLO keypoint tracks.

    tracks: {"azul": [(t_s, kps(17,3)), ...], "rojo": [...]}

    Returns:
    {
      "azul": {
          "angles":       {"knee_l": [(t, deg), ...], ...},
          "rom":          {"knee_l": float, ...},       # max - min per joint (deg)
          "peak_ang_vel": {"knee_l": float, ...},       # peak deg/s
          "asymmetry":    {"knee": float, "hip": float, "elbow": float},
          "n_frames":     int,
      },
      "rojo": { ... },
    }
    """
    out: dict = {}
    for color in ("azul", "rojo"):
        tr = tracks.get(color, [])
        if not tr:
            out[color] = {"error": "No track data"}
            continue

        angle_series: dict[str, list[tuple[float, float]]] = {j: [] for j in _JOINT_ANGLES}
        for t_s, kps in tr:
            for name, (a, b, c) in _JOINT_ANGLES.items():
                ang = _joint_angle(kps, a, b, c)
                if ang is not None:
                    angle_series[name].append((t_s, ang))

        rom: dict[str, float] = {}
        for name, series in angle_series.items():
            if len(series) >= 2:
                vals = [v for _, v in series]
                rom[name] = round(max(vals) - min(vals), 1)

        peak_ang_vel: dict[str, float] = {}
        for name, series in angle_series.items():
            if len(series) >= 2:
                avs = _angular_velocity(series)
                if avs:
                    peak_ang_vel[name] = round(max(e["ang_vel_degs"] for e in avs), 1)

        asymmetry: dict[str, float] = {}
        for group in ("knee", "hip", "elbow"):
            lrom = rom.get(f"{group}_l")
            rrom = rom.get(f"{group}_r")
            if lrom is not None and rrom is not None:
                asymmetry[group] = round(abs(lrom - rrom), 1)

        out[color] = {
            "angles":       {n: s for n, s in angle_series.items() if s},
            "rom":          rom,
            "peak_ang_vel": peak_ang_vel,
            "asymmetry":    asymmetry,
            "n_frames":     len(tr),
        }

    return out


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze_duel_speeds(
    video_path: str,
    sample_every: int = 3,
    max_duration_s: float = 600.0,
) -> dict:
    """
    Analyzes a combat video and returns speed data for both athletes.

    Returns:
    {
      "scale_m_per_px": float,
      "fps": float,
      "blue": {
          "kick_speeds":         [{t, speed_ms}, ...],   # ankle speed series
          "displacement_speeds": [{t, speed_ms}, ...],   # hip center speed
          "peak_kicks":          [{t, speed_ms}, ...],   # local maxima
          "max_kick_ms":         float,
          "avg_displacement_ms": float,
      },
      "red": { ... same ... },
      "error": str | None,
    }
    """
    result = {
        "scale_m_per_px": None, "fps": None,
        "azul": {}, "rojo": {}, "blue": {}, "red": {}, "error": None,
    }

    if not os.path.isfile(video_path):
        result["error"] = f"Video not found: {video_path}"
        return result

    try:
        model = _get_model()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        result["error"] = f"Could not open video: {video_path}"
        return result

    sample_every = max(1, int(sample_every or 1))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    orig_w       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_frames = int(max_duration_s * fps)
    max_frames   = min(total_frames, duration_frames) if total_frames > 0 else duration_frames

    result["fps"] = fps
    infer = model.create_infer_request()
    input_tensor = model.input(0)

    # ── ByteTrack setup — assigns persistent IDs across frames ───────────────
    byte_tracker = sv.ByteTrack()
    color_by_id:  dict[int, str] = {}   # tracker_id → "azul" | "rojo" (set once)
    id_obs_count: dict[int, int] = {}   # tracker_id → frames seen (stability filter)

    # ── Pass 1: detect + track all frames ────────────────────────────────────
    raw_frames: list[tuple[float, list]] = []   # (t_s, [tracked_det])
    calib_dets: list[list] = []

    fn = 0
    while fn < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        if fn % sample_every != 0:
            fn += 1
            continue
        t_s      = fn / fps
        blob, scale, pad_x, pad_y = _preprocess(frame)
        infer.infer({input_tensor: blob})
        out      = infer.get_output_tensor(0).data
        raw_dets = _postprocess(out, scale, pad_x, pad_y, orig_w, orig_h)

        frame_tracked: list[dict] = []
        frame_calib:   list[dict] = []

        if raw_dets:
            boxes   = np.array([d["bbox"] for d in raw_dets], dtype=float)
            confs   = np.array([d["conf"] for d in raw_dets], dtype=float)
            sv_dets = sv.Detections(xyxy=boxes, confidence=confs)
            tracked = byte_tracker.update_with_detections(sv_dets)

            if tracked.tracker_id is not None:
                # Resolve colors for IDs that don't have one yet
                taken: set[str] = {color_by_id[tid] for tid in tracked.tracker_id
                                   if tid in color_by_id}

                for i, tid in enumerate(tracked.tracker_id):
                    tid = int(tid)
                    id_obs_count[tid] = id_obs_count.get(tid, 0) + 1
                    t_bbox = tracked.xyxy[i]
                    raw_idx = _match_to_raw(t_bbox, raw_dets)
                    kps = raw_dets[raw_idx]["keypoints"] if raw_idx >= 0 else None

                    # Assign color once per tracker_id
                    if tid not in color_by_id:
                        color = _vest_color(frame, t_bbox)
                        if color and color not in taken:
                            color_by_id[tid] = color
                            taken.add(color)
                        # Position fallback handled after loop

                    td = {"bbox": t_bbox.tolist(), "keypoints": kps, "tracker_id": tid}
                    frame_tracked.append(td)
                    if kps is not None:
                        frame_calib.append({"bbox": t_bbox.tolist(), "keypoints": kps})

                # Position fallback is intentionally conservative:
                # use it only when neither athlete color was detected in this frame.
                uncolored = [d for d in frame_tracked if d["tracker_id"] not in color_by_id]
                if uncolored and not taken:
                    if len(uncolored) >= 2:
                        by_x = sorted(uncolored, key=lambda d: d["bbox"][0])
                        for det, col in zip(by_x, ["rojo", "azul"]):
                            color_by_id[det["tracker_id"]] = col

        raw_frames.append((t_s, frame_tracked))
        calib_dets.append(frame_calib)
        fn += 1

    cap.release()

    if not raw_frames:
        result["error"] = "No frames processed"
        return result

    # ── Calibration ───────────────────────────────────────────────────────────
    scale_mpp = _calibrate(calib_dets)
    if scale_mpp is None:
        scale_mpp = _REAL_ATHLETE_HEIGHT_M / 200.0   # fallback: 200px ≈ 1.75m
    result["scale_m_per_px"] = round(scale_mpp, 6)

    # ── Build per-color tracks using persistent ByteTrack IDs ────────────────
    # Only include IDs seen in ≥ _MIN_TRACK_FRAMES — filters transient ghost IDs
    # that ByteTrack assigns to referees/crowd when real athletes exit frame briefly
    stable_ids = {tid for tid, cnt in id_obs_count.items() if cnt >= _MIN_TRACK_FRAMES}

    tracks: dict[str, list] = {"azul": [], "rojo": []}

    for t_s, dets in raw_frames:
        for d in dets:
            if d["tracker_id"] not in stable_ids:
                continue   # ghost / transient referee detection
            color = color_by_id.get(d["tracker_id"])
            if color in ("azul", "rojo") and d.get("keypoints") is not None:
                tracks[color].append((t_s, d["keypoints"]))

    # ── Speed series ──────────────────────────────────────────────────────────
    for color in ("azul", "rojo"):
        tr = tracks[color]
        if not tr:
            result[color] = {"error": "No track data"}
            continue

        # Kick speed: faster ankle
        l_kick = _kp_speed(tr, _KP["L_ankle"], fps / sample_every, scale_mpp)
        r_kick = _kp_speed(tr, _KP["R_ankle"], fps / sample_every, scale_mpp)

        # Merge: keep the faster ankle per timestamp
        kick_map: dict[float, float] = {}
        for e in l_kick + r_kick:
            t, v = e["t"], e["speed_ms"]
            kick_map[t] = max(kick_map.get(t, 0), v)
        kick_series = [{"t": t, "speed_ms": v}
                       for t, v in sorted(kick_map.items())]

        disp_series = _displacement_speed(tr, fps / sample_every, scale_mpp)
        peaks       = _peak_kick_events(kick_series)

        max_kick  = max((e["speed_ms"] for e in kick_series), default=0.0)
        avg_disp  = (float(np.mean([e["speed_ms"] for e in disp_series]))
                     if disp_series else 0.0)

        result[color] = {
            "kick_speeds":         kick_series,
            "displacement_speeds": disp_series,
            "peak_kicks":          peaks,
            "max_kick_ms":         round(max_kick, 2),
            "avg_displacement_ms": round(avg_disp, 2),
        }

    # ── Phase 1: joint angles, ROM, asymmetry via YOLO keypoints ─────────────
    # Keep Spanish keys for the current UI and English aliases for API/docs compatibility.
    result["blue"] = result.get("azul", {})
    result["red"] = result.get("rojo", {})

    result["yolo_biomech"] = _compute_duel_biomech(tracks, fps, sample_every)

    return result
