"""
Genera sesión de ECG + IMU sincronizada con videoplayback.mp4 (6:15, 375.4s)
y la carga en la DB para el atleta demo (demo@combatiq.app / id=12).

Estructura de combate WT (3 rounds × 2 min, descanso 1 min):
  Round 1:  t=0     – t=120   (fight)
  Descanso: t=120   – t=180   (rest)
  Round 2:  t=180   – t=300   (fight)
  Descanso: t=300   – t=360   (rest)
  Round 3:  t=360   – t=375.4 (fight, truncado — video termina aquí)
"""
import sys, os, csv, json, random, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

random.seed(42)

# ── Configuración ─────────────────────────────────────────────────────────────
VIDEO_DURATION   = 375.4          # segundos exactos del video
ECG_SAMPLE_DT    = 0.25           # 4 Hz — igual que los archivos existentes
FS               = 4              # Hz
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ECG_DIR          = os.path.join(BASE_DIR, "data", "ecg")

STEM             = "combat_12_wt_videoplayback"
ECG_FILENAME     = f"{STEM}.csv"
IMU_FILENAME     = f"{STEM}_imu.json"

# WT 3 rounds
ROUNDS = [
    {"round": 1, "t_start":   0.0, "t_end": 120.0, "phase": "fight"},
    {"round": 0, "t_start": 120.0, "t_end": 180.0, "phase": "rest"},
    {"round": 2, "t_start": 180.0, "t_end": 300.0, "phase": "fight"},
    {"round": 0, "t_start": 300.0, "t_end": 360.0, "phase": "rest"},
    {"round": 3, "t_start": 360.0, "t_end": 375.4, "phase": "fight"},
]

def _in_fight(t):
    for seg in ROUNDS:
        if seg["phase"] == "fight" and seg["t_start"] <= t < seg["t_end"]:
            return seg["round"]
    return 0   # 0 = rest

def _in_rest(t):
    return _in_fight(t) == 0

# ── ECG ───────────────────────────────────────────────────────────────────────
# HR model: lucha = 175-195 bpm rampa suave; descanso = recuperación exponencial
# Waveform: picos QRS gaussianos al ritmo de la FC instantánea

def _make_ecg():
    rows = []
    HR_FIGHT_TARGET = 188.0   # bpm pico lucha
    HR_FIGHT_BASE   = 170.0   # bpm inicio lucha
    HR_REST         = 130.0   # bpm recuperación mínima en descanso WT
    REST_DECAY      = math.exp(-1.0 / 35.0)  # tau=35s (WT — no bajan tanto en 1 min)
    FIGHT_ALPHA     = 0.08    # velocidad de subida

    hr = 155.0          # FC inicial (ya está calentando)
    last_peak_t = -999.0
    noise_amp   = 0.012

    t = 0.0
    while t <= VIDEO_DURATION:
        rnd = _in_fight(t)
        if rnd:   # fight
            target = HR_FIGHT_BASE + (HR_FIGHT_TARGET - HR_FIGHT_BASE) * min(1.0, (t - _get_round_start(rnd)) / 60.0)
            hr = hr + FIGHT_ALPHA * (target - hr) + random.gauss(0, 0.4)
            hr = max(160.0, min(200.0, hr))
        else:     # rest
            hr = HR_REST + (hr - HR_REST) * REST_DECAY + random.gauss(0, 0.3)
            hr = max(HR_REST, min(175.0, hr))

        # ECG waveform: ruido base + pico QRS gaussiano
        rr = 60.0 / hr           # inter-beat interval en segundos
        ecg_val = random.gauss(0, noise_amp)
        # Check if a QRS should fire at time t
        if t - last_peak_t >= rr:
            last_peak_t = t
            # QRS en las próximas muestras (ancho ~0.06s → 3 muestras a 4Hz)
            ecg_val += 1.4 * math.exp(-((t - last_peak_t) ** 2) / (2 * 0.008))
        # T-wave smaller ripple ~200ms after R
        t_wave_delay = last_peak_t + 0.22
        ecg_val += 0.18 * math.exp(-((t - t_wave_delay) ** 2) / (2 * 0.015))

        rows.append((round(t, 3), round(ecg_val, 5)))
        t += ECG_SAMPLE_DT

    return rows

def _get_round_start(rnd_num):
    for seg in ROUNDS:
        if seg.get("round") == rnd_num and seg["phase"] == "fight":
            return seg["t_start"]
    return 0.0

# ── IMU ───────────────────────────────────────────────────────────────────────
# Eventos: "ruido" cada ~2s durante lucha (movimiento continuo),
#          "dado" y "recibido" en momentos de técnica
# En descanso: sólo ruido bajo ocasional

def _make_imu():
    events = []
    t = 0.0
    while t < VIDEO_DURATION:
        rnd = _in_fight(t)
        if rnd:
            # Movimiento continuo cada 2s aprox
            noise_interval = random.uniform(1.6, 2.4)
            intensity = random.uniform(0.28, 0.92)
            events.append({"t": round(t, 2), "intensity": round(intensity, 2),
                           "type": "ruido", "round": rnd})
            # Técnicas: cada ~12-18s en promedio durante el round
            if random.random() < 0.12:  # ~6% por evento de ruido ≈ 1 técnica / 16s
                tech_t = round(t + random.uniform(0.2, 1.5), 2)
                if tech_t < VIDEO_DURATION and _in_fight(tech_t):
                    tech_type = "dado" if random.random() < 0.60 else "recibido"
                    tech_int  = round(random.uniform(1.8, 5.2), 2)
                    events.append({"t": tech_t, "intensity": tech_int,
                                   "type": tech_type, "round": rnd})
            t += noise_interval
        else:
            # Descanso: actividad mínima (pasos al córner, estiramiento)
            t += random.uniform(4.0, 8.0)
            if t < VIDEO_DURATION and _in_rest(t):
                events.append({"t": round(t, 2), "intensity": round(random.uniform(0.12, 0.35), 2),
                               "type": "ruido", "round": 0})

    # Ordenar por tiempo
    events.sort(key=lambda e: e["t"])
    return events

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # 1. Generar datos
    print("Generando ECG (4 Hz, 375.4s)...")
    ecg_rows = _make_ecg()
    print(f"  → {len(ecg_rows)} muestras ECG")

    print("Generando IMU (eventos de técnica)...")
    imu_events = _make_imu()
    n_dado     = sum(1 for e in imu_events if e["type"] == "dado")
    n_recibido = sum(1 for e in imu_events if e["type"] == "recibido")
    n_ruido    = sum(1 for e in imu_events if e["type"] == "ruido")
    print(f"  → {len(imu_events)} eventos: {n_dado} dado, {n_recibido} recibido, {n_ruido} ruido")

    # 2. Guardar archivos
    ecg_path = os.path.join(ECG_DIR, ECG_FILENAME)
    with open(ecg_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "ecg"])
        w.writerows(ecg_rows)
    print(f"  ECG guardado: {ecg_path}")

    imu_path = os.path.join(ECG_DIR, IMU_FILENAME)
    with open(imu_path, "w", encoding="utf-8") as f:
        json.dump(imu_events, f)
    print(f"  IMU guardado: {imu_path}")

    # 3. Encontrar el atleta demo — probar emails conocidos en orden
    DEMO_EMAILS = [
        "carlos.tkd@demo.combatiq",   # cuenta con login real (demo123)
        "demo@combatiq.app",          # cuenta original (demo_no_auth)
    ]
    demo = None
    for email in DEMO_EMAILS:
        demo = db.get_user_by_email(email)
        if demo:
            break
    if not demo:
        # Fallback: primer atleta TKD activo
        users = db.list_users()
        demo = next((u for u in users if u.get("role") == "deportista"
                     and (u.get("sport") or "").lower() in ("taekwondo", "tkd")), None)

    if not demo:
        print("ERROR: No se encontró atleta demo. Usuarios disponibles:")
        for u in users:
            print(f"  id={u['id']}  {u['email']}  role={u['role']}  sport={u.get('sport')}")
        sys.exit(1)

    uid   = demo["id"]
    uname = demo.get("name") or demo.get("email")
    print(f"\nAtleta demo: {uname} (id={uid})")

    # 4. Crear sesión ligada al atleta
    session_notes = "WT Videoplayback — 3 rounds (6:15) — sesión de sincronización"
    sid = db.create_session(
        athlete_id=uid,
        created_by=uid,
        sport=demo.get("sport") or "taekwondo",
        notes=session_notes,
    )
    print(f"  Sesión creada: id={sid}")

    # 5. Registrar ECG en la DB (ligado a sesión)
    ecg_id = db.add_ecg_file(uid, ECG_FILENAME, FS, session_id=sid)
    print(f"  ECG file registrado: id={ecg_id}  filename={ECG_FILENAME}")

    # 6. Registrar IMU metrics en la DB (ligado a sesión)
    db.save_imu_metrics(
        user_id    = uid,
        filename   = STEM,           # sin extensión — el loader agrega _imu.json
        n_hits     = n_dado + n_recibido,
        hits_per_min = round((n_dado + n_recibido) / (VIDEO_DURATION / 60), 2),
        mean_int_g = round(sum(e["intensity"] for e in imu_events if e["type"] != "ruido")
                          / max(1, n_dado + n_recibido), 3),
        max_int_g  = round(max((e["intensity"] for e in imu_events if e["type"] != "ruido"),
                               default=0.0), 3),
        session_id = sid,
        sensor_type = "IMU-BLE",
        mean_ang_vel = 185.0,   # estimado WT
        max_ang_vel  = 410.0,
    )
    print(f"  IMU metrics registrado para sesión {sid}")

    print(f"""
════════════════════════════════════════════════════
  SESIÓN LISTA
  Atleta   : {uname} (id={uid})
  Sesión   : {sid}  ("{session_notes}")
  ECG      : {ECG_FILENAME}  ({len(ecg_rows)} pts, 4 Hz)
  IMU      : {IMU_FILENAME}  ({len(imu_events)} eventos)
  Video    : videoplayback.mp4  (375.4 s)

  Para probar en la app:
  1. Login como demo@combatiq.app
  2. Ir a "Replay de combate"
  3. En "Vincular sesión", seleccionar sesión id={sid}
  4. Cargar videoplayback.mp4
  5. Reproducir — las gráficas deben moverse sincronizadas
════════════════════════════════════════════════════""")

if __name__ == "__main__":
    main()
