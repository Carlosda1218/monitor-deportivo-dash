"""
hub/config.py — Configuración central del BLE hub.
Ajusta las UUIDs y umbrales según tu firmware ESP32.
"""
import os

# ── API ───────────────────────────────────────────────────────────────────────
API_BASE        = os.environ.get("COMBATIQ_API", "http://127.0.0.1:8051")
API_TOKEN       = os.environ.get("COMBATIQ_SENSOR_API_TOKEN", "")
REPORT_INTERVAL = int(os.environ.get("REPORT_INTERVAL", "10"))   # segundos entre envíos

# ── BLE (Nordic UART Service) ─────────────────────────────────────────────────
NUS_SERVICE     = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX          = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # ESP32 → PC
NUS_RX          = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # PC → ESP32 (no usado aún)

# ── IMU ───────────────────────────────────────────────────────────────────────
ACCEL_SCALE     = 16384.0   # LSB/g para MPU-6050 a ±2g
GYRO_SCALE      = 131.0     # LSB/(°/s) para MPU-6050 a ±250°/s

# Umbrales de impacto (en g) por posición del sensor
# Ajusta según la sensibilidad real de tu hardware
THRESHOLDS = {
    "IMU_GLOVE":  2.5,   # guante boxeo (muñeca, sensor en guante)
    "IMU_WRIST":  2.5,   # muñeca genérica
    "IMU_FOOT":   3.5,   # tobillera TKD (kick)
    "IMU_ANKLE":  3.5,   # tobillera genérica
    "IMU_HEAD":   1.5,   # casco (impacto recibido, umbral más bajo)
}

# Etiqueta legible de la acción por posición (para logs y export)
HIT_LABEL = {
    "IMU_GLOVE":  "golpe",
    "IMU_WRIST":  "golpe",
    "IMU_FOOT":   "patada",
    "IMU_ANKLE":  "patada",
    "IMU_HEAD":   "impacto recibido",
}

DEFAULT_THRESHOLD = 2.5

# Nombre BLE prefix del ESP32 (el firmware anuncia "CombatIQ-*")
BLE_NAME_PREFIX = "CombatIQ"
BLE_SCAN_TIMEOUT = 10.0   # segundos
