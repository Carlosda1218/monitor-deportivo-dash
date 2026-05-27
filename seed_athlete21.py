"""
seed_athlete21.py — Crea sesiones demo para el atleta #21 (Carlos Ríos).

Reutiliza los archivos ECG/IMU ya existentes en data/ecg/.
Idempotente: no duplica si las sesiones ya existen.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db

ATHLETE_ID = 21   # carlos.tkd@demo.combatiq / Carlos Ríos

# ── Verificar que el atleta existe ────────────────────────────────────────────
athlete = db.get_user_by_id(ATHLETE_ID)
if not athlete:
    print(f"ERROR: atleta id={ATHLETE_ID} no existe en DB. Abortando.")
    sys.exit(1)
print(f"Atleta encontrado: {athlete['name']} ({athlete['email']})")

# ── Verificar sesiones existentes ─────────────────────────────────────────────
existing = db.list_sessions(ATHLETE_ID)
if existing:
    print(f"Atleta ya tiene {len(existing)} sesiones. Abortando para no duplicar.")
    for s in existing:
        print(f"  sesión {s['id']}: {s['ts_start']} — {s['notes']}")
    sys.exit(0)

# ── Definir sesiones ──────────────────────────────────────────────────────────
sessions_to_create = [
    {
        "ts_start":   "2026-04-29T17:01:00",
        "notes":      "Combat Monitor — Taekwondo (WT) — 3 rounds — Peak BPM 165 — 28 impactos",
        "sport":      "Taekwondo (WT)",
        "ecg_file":   "combat_12_20260429_170100.csv",
        "imu_file":   "combat_12_20260429_170100_imu.json",
        "imu_metrics": {"n_hits": 28, "hits_per_min": 3.54, "mean_int_g": 3.24, "max_int_g": 4.65},
        "ecg_bpm":    165, "ecg_sdnn": 41, "ecg_rmssd": 28, "ecg_peaks": 812,
        "wellness":   {"energia": 3, "recuperacion": 3, "sueno_calidad": 4, "sueno_horas": 8,
                       "listo_rendir": 4, "fatiga_general": 2, "cuerpo_pesado": 2,
                       "tkd_explosividad": 4, "tkd_agilidad": 3, "tkd_ritmo": 3,
                       "tkd_molestia_inferior": 1,
                       "_ctx": {"sport": "taekwondo", "competition": False, "weight": False, "injury": False}},
        "wellness_score": 71.2,
    },
    {
        "ts_start":   "2026-05-04T13:31:00",
        "notes":      "Combat Monitor — Taekwondo (WT) — 3 rounds — Peak BPM 171",
        "sport":      "Taekwondo (WT)",
        "ecg_file":   "combat_21_20260504_133145.csv",
        "imu_file":   None,
        "imu_metrics": None,
        "ecg_bpm":    171, "ecg_sdnn": 38, "ecg_rmssd": 25, "ecg_peaks": 870,
        "wellness":   {"energia": 4, "recuperacion": 4, "sueno_calidad": 3, "sueno_horas": 7,
                       "listo_rendir": 4, "fatiga_general": 2, "cuerpo_pesado": 1,
                       "tkd_explosividad": 4, "tkd_agilidad": 4, "tkd_ritmo": 4,
                       "tkd_molestia_inferior": 1,
                       "_ctx": {"sport": "taekwondo", "competition": False, "weight": False, "injury": False}},
        "wellness_score": 76.8,
    },
    {
        "ts_start":   "2026-05-06T14:57:00",
        "notes":      "Combat Monitor — Taekwondo (WT) — 3 rounds — Peak BPM 176 — 38 impactos",
        "sport":      "Taekwondo (WT)",
        "ecg_file":   "combat_12_20260506_145700.csv",
        "imu_file":   "combat_12_20260506_145700_imu.json",
        "imu_metrics": {"n_hits": 38, "hits_per_min": 4.81, "mean_int_g": 2.93, "max_int_g": 4.71},
        "ecg_bpm":    176, "ecg_sdnn": 35, "ecg_rmssd": 22, "ecg_peaks": 891,
        "wellness":   {"energia": 3, "recuperacion": 2, "sueno_calidad": 3, "sueno_horas": 7,
                       "listo_rendir": 3, "fatiga_general": 3, "cuerpo_pesado": 3,
                       "tkd_explosividad": 3, "tkd_agilidad": 3, "tkd_ritmo": 3,
                       "tkd_molestia_inferior": 1,
                       "_ctx": {"sport": "taekwondo", "competition": False, "weight": False, "injury": False}},
        "wellness_score": 58.4,
    },
    {
        "ts_start":   "2026-05-14T10:25:00",
        "notes":      "Combat Monitor — Taekwondo (WT) — 3 rounds — Peak BPM 174 — 24 impactos",
        "sport":      "Taekwondo (WT)",
        "ecg_file":   "combat_12_wt_videoplayback.csv",
        "imu_file":   "combat_12_wt_videoplayback_imu.json",
        "imu_metrics": {"n_hits": 24, "hits_per_min": 3.98, "mean_int_g": 6.0, "max_int_g": 6.0},
        "ecg_bpm":    174, "ecg_sdnn": 39, "ecg_rmssd": 26, "ecg_peaks": 850,
        "wellness":   {"energia": 4, "recuperacion": 3, "sueno_calidad": 4, "sueno_horas": 8,
                       "listo_rendir": 4, "fatiga_general": 2, "cuerpo_pesado": 2,
                       "tkd_explosividad": 4, "tkd_agilidad": 4, "tkd_ritmo": 4,
                       "tkd_molestia_inferior": 1,
                       "_ctx": {"sport": "taekwondo", "competition": False, "weight": False, "injury": False}},
        "wellness_score": 73.5,
    },
]

import sqlite3, json as _json
from datetime import datetime

conn = sqlite3.connect("data/users.db")
conn.row_factory = sqlite3.Row
created_sessions = []

for sdef in sessions_to_create:
    # 1. Crear sesión
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO sessions (athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, 'closed', ?)",
        (ATHLETE_ID, ATHLETE_ID, sdef["ts_start"], sdef["sport"], sdef["notes"], now),
    )
    session_id = cur.lastrowid
    created_sessions.append(session_id)
    print(f"  OK sesion {session_id}: {sdef['ts_start']}")

    # 2. Registrar ECG file
    cur2 = conn.execute(
        "INSERT INTO ecg_files (user_id, filename, fs, created_at, session_id) VALUES (?, ?, 4, ?, ?)",
        (ATHLETE_ID, sdef["ecg_file"], sdef["ts_start"], session_id),
    )
    ecg_id = cur2.lastrowid

    # 3. ECG metrics
    conn.execute(
        "INSERT INTO ecg_metrics (ecg_file_id, bpm, sdnn, rmssd, n_peaks, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ecg_id, sdef["ecg_bpm"], sdef["ecg_sdnn"], sdef["ecg_rmssd"], sdef["ecg_peaks"], now),
    )

    # 4. IMU metrics (si hay)
    if sdef["imu_metrics"] and sdef["imu_file"]:
        m = sdef["imu_metrics"]
        imu_fname = sdef["imu_file"].replace(".json", "")
        conn.execute(
            "INSERT INTO imu_metrics (user_id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ATHLETE_ID, imu_fname, sdef["ts_start"], m["n_hits"], m["hits_per_min"],
             m["mean_int_g"], m["max_int_g"], session_id),
        )

    # 5. Wellbeing (questionnaire)
    conn.execute(
        "INSERT INTO questionnaires (user_id, ts, answers_json, wellness_score, rpe, duration_min, session_id) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
        (ATHLETE_ID, sdef["ts_start"], _json.dumps(sdef["wellness"]), sdef["wellness_score"], session_id),
    )

conn.commit()
conn.close()

print(f"\nSeed completado. Sesiones creadas para atleta #{ATHLETE_ID}: {created_sessions}")

# ── Verificar ─────────────────────────────────────────────────────────────────
check = db.list_sessions(ATHLETE_ID)
print(f"Verificación — {len(check)} sesiones en DB:")
for s in check:
    print(f"  [{s['id']}] {s['ts_start']} | {s['notes'][:60]}")
