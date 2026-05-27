"""
analyze_wt_video.py -- Analiza videoplayback.mp4 con MediaPipe (duel mode),
extrae los eventos reales del peleador AZUL (Garcia Martinez, ESP) y
regenera los archivos ECG + IMU de la sesion #30.

Uso:
    .venv\\Scripts\\python.exe analyze_wt_video.py
"""
import sys, os, csv, json, math, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("COMBATIQ_DUEL_MAX_FRAMES", "4000")
os.environ.setdefault("COMBATIQ_DUEL_MAX_SECONDS", "900")

import numpy as np
import db
from pose_analyzer import analyze_video, simulate_duel_ecg_imu

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
VIDEO_PATH = os.path.join(BASE_DIR, "data", "uploads", "videoplayback.mp4")
ECG_DIR    = os.path.join(BASE_DIR, "data", "ecg")
CACHE_PATH = os.path.join(BASE_DIR, "data", "ecg", "wt_analysis_cache.json")
STEM       = "combat_12_wt_videoplayback"
ECG_FILE   = f"{STEM}.csv"
IMU_FILE   = f"{STEM}_imu.json"
SESSION_ID = 30
TARGET     = "blue"   # Garcia Martinez, ESP
FS         = 4        # Hz del ECG output

# Estructura WT estandar -- se usa cuando el analisis no detecta 3 rounds
# El video tiene segmentos NO combate (saludo, prueba de peto, encuadre final)
# que se tratan como descanso al quedar fuera de estos ranges.
WT_ROUNDS = [
    {"round": 1, "t_start":   0.0, "t_end": 120.0, "phase": "fight"},
    {"round": 0, "t_start": 120.0, "t_end": 180.0, "phase": "rest"},
    {"round": 2, "t_start": 180.0, "t_end": 300.0, "phase": "fight"},
    {"round": 0, "t_start": 300.0, "t_end": 360.0, "phase": "rest"},
    {"round": 3, "t_start": 360.0, "t_end": 375.4, "phase": "fight"},
]

# Objetivo: ~25-45 impactos para un combate WT de 3 rounds (realista)
TARGET_IMPACTS = 35
MIN_GAP_SECONDS = 2.5   # minimo segundos entre dos impactos consecutivos


def _get_round(t, rounds):
    for seg in rounds:
        if seg["phase"] == "fight" and seg["t_start"] <= t < seg["t_end"]:
            return seg.get("round", 1)
    return 0


def _in_rest(t, rounds):
    for seg in rounds:
        if seg["phase"] == "rest" and seg["t_start"] <= t <= seg["t_end"]:
            return True
    return False


def _hr_series_to_ecg_waveform(ecg_output, video_duration):
    """Convierte HR series (1 Hz) a ECG waveform (4 Hz) con picos QRS."""
    if not ecg_output:
        return []
    hr_times = np.array([e["t"] for e in ecg_output], dtype=float)
    hr_vals  = np.array([e["hr"] for e in ecg_output], dtype=float)

    def _hr_at(t):
        return float(np.interp(t, hr_times, hr_vals))

    rows = []
    last_peak_t = -999.0
    noise_amp   = 0.010
    rng         = np.random.default_rng(42)
    dt          = 1.0 / FS

    t = 0.0
    while t <= video_duration:
        hr  = _hr_at(t)
        rr  = 60.0 / max(hr, 30.0)
        ecg_val = float(rng.normal(0, noise_amp))
        if t - last_peak_t >= rr:
            last_peak_t = t
        ecg_val += 1.2 * math.exp(-((t - last_peak_t) ** 2) / (2 * 0.006))
        t_wave = last_peak_t + 0.22
        ecg_val += 0.15 * math.exp(-((t - t_wave) ** 2) / (2 * 0.012))
        rows.append((round(t, 3), round(ecg_val, 5)))
        t += dt
    return rows


def _convert_and_filter_imu(imu_raw, frames, rounds):
    """
    Convierte {t, g, event} -> {t, intensity, type, round} y filtra
    para obtener un numero realista de impactos (~TARGET_IMPACTS).
    """
    # Lookup de movimiento azul/rojo por segundo
    move_by_sec = {}
    for f in frames:
        ti = int(f.get("t", 0))
        bm = float(f.get("blue_move", 0.0) or 0.0)
        rm = float(f.get("red_move",  0.0) or 0.0)
        if ti not in move_by_sec:
            move_by_sec[ti] = {"blue": 0.0, "red": 0.0, "n": 0}
        move_by_sec[ti]["blue"] += bm
        move_by_sec[ti]["red"]  += rm
        move_by_sec[ti]["n"]    += 1

    # Separar impactos de ruido
    impactos = []
    ruido    = []
    for ev in imu_raw:
        t   = float(ev["t"])
        g   = float(ev["g"])
        evt = ev.get("event", "movimiento")
        rnd = _get_round(t, rounds)
        if _in_rest(t, rounds):
            continue   # sin eventos durante descanso

        if evt == "impacto":
            ti   = int(t)
            info = move_by_sec.get(ti, {})
            n    = max(1, info.get("n", 1))
            b_avg = info.get("blue", 0.0) / n
            r_avg = info.get("red",  0.0) / n
            hit_type = "dado" if b_avg >= r_avg else "recibido"
            impactos.append({"t": round(t, 2), "intensity": round(g, 2),
                             "type": hit_type, "round": rnd})
        else:
            ruido.append({"t": round(t, 2), "intensity": round(g, 2),
                          "type": "ruido", "round": rnd})

    # Filtrar impactos: primero por intensidad (top por percentil),
    # luego deduplicar por ventana minima de MIN_GAP_SECONDS
    impactos.sort(key=lambda e: e["intensity"], reverse=True)
    # Tomar los mejores hasta 3x TARGET_IMPACTS para luego deduplicar
    candidates = impactos[:TARGET_IMPACTS * 3]
    candidates.sort(key=lambda e: e["t"])

    filtered = []
    last_t = -999.0
    for ev in candidates:
        if ev["t"] - last_t >= MIN_GAP_SECONDS:
            filtered.append(ev)
            last_t = ev["t"]
        if len(filtered) >= TARGET_IMPACTS:
            break

    # Ruido: submuestrear -- 1 cada ~4s durante pelea
    ruido_filtered = []
    last_ruido_t = -999.0
    for ev in sorted(ruido, key=lambda e: e["t"]):
        if ev["t"] - last_ruido_t >= 4.0:
            ruido_filtered.append(ev)
            last_ruido_t = ev["t"]

    result = sorted(filtered + ruido_filtered, key=lambda e: e["t"])
    return result


def main():
    print(f"Video: {VIDEO_PATH}")
    if not os.path.exists(VIDEO_PATH):
        print("ERROR: No se encontro videoplayback.mp4")
        sys.exit(1)

    # 1. Analisis MediaPipe (o cargar cache si existe)
    if os.path.exists(CACHE_PATH):
        print(f"\nCargando resultado cacheado de: {CACHE_PATH}")
        with open(CACHE_PATH, encoding="utf-8") as f:
            result = json.load(f)
        print("  Cache cargado OK")
    else:
        print("\nEjecutando analisis MediaPipe (modo duel, peto azul)...")
        print("  Puede tardar varios minutos...\n")
        result = analyze_video(
            VIDEO_PATH,
            target       = "duel",
            sample_every = 3,
            sport        = "taekwondo",
        )
        if result.get("error"):
            print(f"ERROR del analisis: {result['error']}")
            sys.exit(1)
        # Cachear para no repetir
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f)
        print(f"  Resultado cacheado en: {CACHE_PATH}")

    frames = result.get("frames", [])
    print(f"\n  Frames procesados  : {len(frames)}")
    if frames:
        print(f"  Duracion detectada : {max(f['t'] for f in frames):.1f}s")

    # 2. Rounds: usar estructura WT estandar si solo se detecto 1
    duel_info       = result.get("duel", {}) or {}
    detected_rounds = duel_info.get("rounds", [])
    fight_rounds    = [r for r in detected_rounds if r.get("phase") == "fight"]
    if len(fight_rounds) >= 3:
        rounds = detected_rounds
        print(f"  Rounds detectados  : {len(fight_rounds)} rounds de lucha")
    else:
        rounds = WT_ROUNDS
        print(f"  Rounds: usando estructura WT estandar (detecto {len(fight_rounds)}, necesita 3)")

    # 3. Simular ECG e IMU desde datos reales del analisis
    print("\nGenerando ECG e IMU para peto AZUL...")
    sim = simulate_duel_ecg_imu(result, for_target=TARGET)

    ecg_hr_series = sim.get("ecg", [])
    imu_raw       = sim.get("imu", [])
    max_hr        = sim.get("max_hr", 0)
    avg_hr        = sim.get("avg_hr", 0)
    n_raw_impacts = sim.get("impacts", 0)

    print(f"  HR serie: {len(ecg_hr_series)} puntos, max={max_hr:.0f} bpm, avg={avg_hr:.0f} bpm")
    print(f"  IMU raw: {len(imu_raw)} eventos, {n_raw_impacts} impactos crudos")

    # 4. ECG waveform a 4 Hz
    video_duration = max(f["t"] for f in frames) if frames else 375.4
    print(f"\nConvirtiendo HR -> ECG waveform a {FS} Hz ({video_duration:.1f}s)...")
    ecg_rows = _hr_series_to_ecg_waveform(ecg_hr_series, video_duration)
    print(f"  ECG: {len(ecg_rows)} muestras")

    # 5. IMU filtrado y convertido
    print("Filtrando y convirtiendo IMU...")
    imu_events = _convert_and_filter_imu(imu_raw, frames, rounds)
    n_dado     = sum(1 for e in imu_events if e["type"] == "dado")
    n_recibido = sum(1 for e in imu_events if e["type"] == "recibido")
    n_ruido    = sum(1 for e in imu_events if e["type"] == "ruido")
    print(f"  IMU: {len(imu_events)} eventos ({n_dado} dado / {n_recibido} recibido / {n_ruido} ruido)")

    # 6. Guardar archivos
    ecg_path = os.path.join(ECG_DIR, ECG_FILE)
    with open(ecg_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "ecg"])
        w.writerows(ecg_rows)
    print(f"\n  ECG guardado: {ecg_path}")

    imu_path = os.path.join(ECG_DIR, IMU_FILE)
    with open(imu_path, "w", encoding="utf-8") as f:
        json.dump(imu_events, f)
    print(f"  IMU guardado: {imu_path}")

    # 7. Actualizar sesion en DB
    import sqlite3
    con = sqlite3.connect(os.path.join(BASE_DIR, "data", "users.db"))
    peak_bpm   = int(round(max_hr))
    n_impactos = n_dado + n_recibido
    notes = (
        f"Combat Monitor - Taekwondo (WT) . 3 rounds . Peak BPM {peak_bpm} . {n_impactos} impactos"
        .replace(" . ", " \xb7 ")
        .replace("Combat Monitor -", "Combat Monitor —")
    )
    con.execute("UPDATE sessions SET notes=? WHERE id=?", (notes, SESSION_ID))
    n_hit_total = n_dado + n_recibido
    mean_g = (sum(e["intensity"] for e in imu_events if e["type"] != "ruido")
              / max(1, n_hit_total))
    max_g  = max((e["intensity"] for e in imu_events if e["type"] != "ruido"), default=0.0)
    con.execute(
        "UPDATE imu_metrics SET n_hits=?, hits_per_min=?, mean_int_g=?, max_int_g=? WHERE session_id=?",
        (n_hit_total, round(n_hit_total / (video_duration / 60), 2),
         round(mean_g, 3), round(max_g, 3), SESSION_ID),
    )
    con.commit()
    con.close()
    print(f"  DB actualizada: sesion {SESSION_ID}, BPM pico={peak_bpm}, impactos={n_impactos}")

    print("")
    print("=" * 60)
    print("  ANALISIS COMPLETO")
    print(f"  Peleador   : AZUL - Garcia Martinez (ESP)")
    print(f"  Video      : videoplayback.mp4 ({video_duration:.1f}s)")
    print(f"  ECG        : {len(ecg_rows)} muestras a {FS} Hz")
    print(f"  IMU        : {n_dado} dado / {n_recibido} recibido / {n_ruido} ruido")
    print(f"  BPM pico   : {peak_bpm}   BPM promedio: {avg_hr:.0f}")
    print("")
    print("  Ahora en la app:")
    print("  1. Recarga la pagina (F5)")
    print(f"  2. Replay de combate -> sesion BPM {peak_bpm}")
    print("  3. Cargar videoplayback.mp4 -> Play")
    print("  4. Cursor avanza sincronizado, eventos = impactos reales")
    print("=" * 60)


if __name__ == "__main__":
    main()
