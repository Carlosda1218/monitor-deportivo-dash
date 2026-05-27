import sqlite3, os

con = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "users.db"))

# 1. Formato "Combat Monitor" para que aparezca en Replay de combate
notes = "Combat Monitor — Taekwondo (WT) · 3 rounds · Peak BPM 188 · 23 impactos"
con.execute("UPDATE sessions SET notes=? WHERE id=30", (notes,))

# 2. Corregir stem IMU: el loader agrega .json, necesita incluir _imu
con.execute("UPDATE imu_metrics SET filename='combat_12_wt_videoplayback_imu' WHERE session_id=30")

con.commit()

row = con.execute("SELECT id, notes FROM sessions WHERE id=30").fetchone()
print(f"Session {row[0]}: {row[1]}")
imu = con.execute("SELECT filename FROM imu_metrics WHERE session_id=30").fetchone()
print(f"IMU stem: {imu[0]}")

# 3. Verificar que los archivos existen
ecg_path = os.path.join(os.path.dirname(__file__), "data", "ecg", "combat_12_wt_videoplayback.csv")
imu_path = os.path.join(os.path.dirname(__file__), "data", "ecg", "combat_12_wt_videoplayback_imu.json")
print(f"ECG file exists: {os.path.exists(ecg_path)}")
print(f"IMU file exists: {os.path.exists(imu_path)}")

con.close()
print("Listo.")
