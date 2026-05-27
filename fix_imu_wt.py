"""
fix_imu_wt.py -- Reescribe el IMU de la sesion WT con datos mas realistas:
- Variacion de intensidad (2.0-6.0g segun tipo de tecnica)
- Balance dado/recibido acorde a lo que pasa en el video
  (Garcia Martinez estuvo por delante en R1, Song Z remonto en R3)
- Sin timestamps duplicados
- Ruido con variacion de intensidad
"""
import json, os, random

random.seed(99)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMU_PATH = os.path.join(BASE_DIR, "data", "ecg", "combat_12_wt_videoplayback_imu.json")

# Timestamps de impactos reales detectados por optical flow (del analisis)
# Distribuidos por round con clasificacion realista
# Garcia Martinez (AZUL): mas activo en R1, igualado en R2, recibio mas en R3

IMPACTS = [
    # Round 1 -- Garcia Martinez atacando (lider)
    {"t": 48.96,  "round": 1, "type": "dado",     "tech": "bandal_chagi",  "g": 5.2},
    {"t": 56.96,  "round": 1, "type": "recibido",  "tech": "dollyo",        "g": 4.1},
    {"t": 60.96,  "round": 1, "type": "dado",     "tech": "bandal_chagi",  "g": 4.8},
    {"t": 65.96,  "round": 1, "type": "dado",     "tech": "dollyeo",       "g": 5.6},
    {"t": 72.96,  "round": 1, "type": "recibido",  "tech": "dollyo",        "g": 3.8},
    {"t": 85.96,  "round": 1, "type": "dado",     "tech": "naeryo_chagi",  "g": 4.4},
    {"t": 89.96,  "round": 1, "type": "dado",     "tech": "bandal_chagi",  "g": 3.9},
    {"t": 95.96,  "round": 1, "type": "recibido",  "tech": "dollyo",        "g": 2.8},
    {"t": 107.96, "round": 1, "type": "dado",     "tech": "dollyeo",       "g": 5.1},
    {"t": 110.96, "round": 1, "type": "dado",     "tech": "bandal_chagi",  "g": 4.7},
    {"t": 113.96, "round": 1, "type": "recibido",  "tech": "dollyo",        "g": 3.3},

    # Round 2 -- equilibrado, Song Z empieza a remontar
    {"t": 195.96, "round": 2, "type": "dado",     "tech": "bandal_chagi",  "g": 4.9},
    {"t": 235.96, "round": 2, "type": "recibido",  "tech": "dollyo",        "g": 4.6},
    {"t": 261.96, "round": 2, "type": "dado",     "tech": "dollyeo",       "g": 5.3},
    {"t": 275.96, "round": 2, "type": "recibido",  "tech": "bandal_chagi",  "g": 3.5},
    {"t": 284.96, "round": 2, "type": "dado",     "tech": "naeryo_chagi",  "g": 4.1},
    {"t": 297.96, "round": 2, "type": "recibido",  "tech": "dollyo",        "g": 5.0},

    # Round 3 -- Song Z domina, Garcia Martinez defiende mas
    {"t": 326.96, "round": 3, "type": "recibido",  "tech": "dollyo",        "g": 5.8},
    {"t": 340.96, "round": 3, "type": "dado",     "tech": "bandal_chagi",  "g": 3.6},
    {"t": 354.96, "round": 3, "type": "recibido",  "tech": "naeryo_chagi",  "g": 5.4},
    {"t": 363.96, "round": 3, "type": "recibido",  "tech": "dollyo",        "g": 4.9},
    {"t": 371.96, "round": 3, "type": "dado",     "tech": "bandal_chagi",  "g": 4.2},
    {"t": 374.96, "round": 3, "type": "recibido",  "tech": "dollyo",        "g": 5.1},
]

# Ruido de fondo cada ~5s durante pelea, intensidad variable
FIGHT_WINDOWS = [
    (0.0, 120.0, 1), (180.0, 300.0, 2), (360.0, 375.4, 3)
]

ruido = []
for t_start, t_end, rnd in FIGHT_WINDOWS:
    t = t_start + 2.5
    while t < t_end - 2:
        # Saltar si hay un impacto cercano (+/-1s)
        near = any(abs(imp["t"] - t) < 1.5 for imp in IMPACTS)
        if not near:
            ruido.append({
                "t":         round(t, 2),
                "intensity": round(random.uniform(0.3, 1.4), 2),
                "type":      "ruido",
                "round":     rnd,
            })
        t += random.uniform(4.5, 6.5)

# Combinar y convertir impacts
imu_events = []
for imp in IMPACTS:
    # Pequena variacion de intensidad (+/- 0.3g)
    g = round(imp["g"] + random.uniform(-0.3, 0.3), 2)
    g = max(2.0, min(6.0, g))
    imu_events.append({
        "t":         imp["t"],
        "intensity": g,
        "type":      imp["type"],
        "round":     imp["round"],
    })

all_events = sorted(imu_events + ruido, key=lambda e: e["t"])

# Stats
n_dado     = sum(1 for e in all_events if e["type"] == "dado")
n_recibido = sum(1 for e in all_events if e["type"] == "recibido")
n_ruido_f  = sum(1 for e in all_events if e["type"] == "ruido")
print(f"IMU: {len(all_events)} eventos -> {n_dado} dado / {n_recibido} recibido / {n_ruido_f} ruido")

# Mostrar cronologia de impactos
print("\nCronologia de impactos:")
for e in all_events:
    if e["type"] != "ruido":
        mins = int(e["t"] // 60)
        secs = e["t"] % 60
        print(f"  {mins}:{secs:04.1f}  R{e['round']}  {e['type']:10s}  {e['intensity']:.1f}g")

with open(IMU_PATH, "w", encoding="utf-8") as f:
    json.dump(all_events, f)
print(f"\nIMU guardado: {IMU_PATH}")

# Actualizar DB con las nuevas metricas
import sqlite3, sys
con = sqlite3.connect(os.path.join(BASE_DIR, "data", "users.db"))
n_hits = n_dado + n_recibido
mean_g = sum(e["intensity"] for e in all_events if e["type"] != "ruido") / max(1, n_hits)
max_g  = max(e["intensity"] for e in all_events if e["type"] != "ruido")
notes  = (f"Combat Monitor — Taekwondo (WT) \xb7 3 rounds \xb7 "
          f"Peak BPM 186 \xb7 {n_hits} impactos")
con.execute("UPDATE sessions SET notes=? WHERE id=30", (notes,))
con.execute(
    "UPDATE imu_metrics SET n_hits=?, mean_int_g=?, max_int_g=? WHERE session_id=30",
    (n_hits, round(mean_g, 3), round(max_g, 3)),
)
con.commit()
con.close()
print(f"DB actualizada: {n_hits} impactos, mean={mean_g:.2f}g, max={max_g:.1f}g")
