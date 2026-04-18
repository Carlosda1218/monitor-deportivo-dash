# sensors.py
"""
Catálogo de sensores para CombatIQ.

Sensores funcionales para deportes de combate (Taekwondo y Boxeo):
- ECG           Banda torácica ECG/HRV
- IMU_WRIST     IMU muñeca / bajo vendas  (golpes)
- IMU_FOOT      IMU tobillo / pie         (patadas)
- IMU_HEAD      IMU casco / cabeza        (impactos recibidos)
- HR_WRIST      FC desde reloj/pulsera    (Garmin, Polar, Apple Watch)
"""

from typing import Dict, List


SENSOR_CATALOG: Dict[str, Dict] = {
    "ECG": {
        "name": "Banda torácica ECG / HRV",
        "short": "ECG / HRV",
        "description": (
            "Banda torácica que registra la señal ECG e intervalos R–R. "
            "CombatIQ calcula frecuencia cardiaca, variabilidad (HRV) y "
            "carga interna para monitorizar recuperación y estado de forma. "
            "Compatible con Polar H10 y sensores BLE similares."
        ),
        "signals": ["ecg"],
        "metrics": ["BPM", "SDNN", "RMSSD", "latidos detectados"],
        "sport_relevance": ["taekwondo", "boxeo"],
        "hardware_examples": ["Polar H10", "Custom BLE ECG"],
    },
    "IMU_WRIST": {
        "name": "IMU muñeca / guante",
        "short": "IMU muñeca",
        "description": (
            "Unidad inercial en muñeca o bajo las vendas. Detecta golpes de brazo, "
            "estima volumen (nº de golpes), ritmo (golpes/min) e intensidad (g de impacto). "
            "Para boxeo analiza combinaciones en saco o sparring. "
            "Para taekwondo registra golpes de brazo y técnicas de mano."
        ),
        "signals": ["imu_hits"],
        "metrics": ["golpes totales", "golpes/min", "intensidad media (g)", "intensidad máxima (g)"],
        "sport_relevance": ["boxeo", "taekwondo"],
        "hardware_examples": ["Custom BLE (ESP32 + MPU-6050)", "Hykso sensor"],
    },
    "IMU_FOOT": {
        "name": "IMU tobillo / pie",
        "short": "IMU pie",
        "description": (
            "Unidad inercial en tobillo o empeine. Detecta patadas, "
            "estima volumen (nº de patadas), frecuencia (patadas/min), "
            "potencia (g de impacto) y simetría entre pierna dominante y no dominante. "
            "Clave para analizar resistencia específica en taekwondo y capoeira."
        ),
        "signals": ["imu_kicks"],
        "metrics": ["patadas totales", "patadas/min", "potencia media (g)", "potencia máxima (g)", "ratio dominante/no-dominante"],
        "sport_relevance": ["taekwondo"],
        "hardware_examples": ["Custom BLE (ESP32 + MPU-6050)"],
    },
    "IMU_HEAD": {
        "name": "IMU casco / cabeza",
        "short": "IMU cabeza",
        "description": (
            "IMU integrada en el casco o cabezal. Registra impactos recibidos, "
            "intensidad de cada impacto (pico de aceleración en g) y acumulación de carga "
            "sobre la cabeza. Fundamental para vigilar la seguridad del deportista "
            "y controlar el volumen de contacto por sesión."
        ),
        "signals": ["imu_head"],
        "metrics": ["impactos totales", "pico de g por impacto", "impactos >3g", "impactos >6g"],
        "sport_relevance": ["boxeo", "taekwondo"],
        "hardware_examples": ["Custom BLE (ESP32 + MPU-6050)", "FitGuard"],
    },
    "HR_WRIST": {
        "name": "FC desde reloj / pulsera",
        "short": "FC reloj",
        "description": (
            "Frecuencia cardiaca obtenida desde un reloj inteligente o pulsera de actividad. "
            "Menos preciso que el ECG pero más accesible para deportistas que ya usan "
            "Garmin, Polar Vantage, Apple Watch o similar. "
            "Aporta FC media, FC máxima y zonas de entrenamiento por sesión."
        ),
        "signals": ["hr"],
        "metrics": ["FC media", "FC máxima", "tiempo por zona", "calorías estimadas"],
        "sport_relevance": ["taekwondo", "boxeo"],
        "hardware_examples": ["Garmin Forerunner", "Polar Vantage", "Apple Watch", "Fitbit"],
    },
}


# === Helpers públicos ===

def catalog() -> Dict[str, Dict]:
    return SENSOR_CATALOG


def labels_for_checklist() -> List[Dict]:
    """Opciones para dcc.Checklist (asignación de sensores)."""
    return [
        {"label": data.get("short") or data.get("name") or code, "value": code}
        for code, data in SENSOR_CATALOG.items()
    ]


def description(code: str) -> str:
    info = SENSOR_CATALOG.get(code)
    if not info:
        return "Sensor no reconocido."
    return info.get("description", info.get("name", code))


def metrics_for(code: str) -> List[str]:
    return list(SENSOR_CATALOG.get(code, {}).get("metrics", []))


def signals_for(code: str) -> List[str]:
    return list(SENSOR_CATALOG.get(code, {}).get("signals", []))


def pretty_signals_for(code: str) -> str:
    mapping = {
        "ecg":       "ECG / HRV",
        "imu_hits":  "Golpes (IMU muñeca)",
        "imu_kicks": "Patadas (IMU pie)",
        "imu_head":  "Impactos cabeza (IMU casco)",
        "hr":        "Frecuencia cardiaca",
    }
    signals = signals_for(code)
    if not signals:
        return "—"
    return ", ".join(mapping.get(s, s) for s in signals)


def sensors_for_sport(sport: str) -> List[str]:
    """Códigos de sensores recomendados para un deporte concreto."""
    sport = (sport or "").strip().lower()
    return [
        code for code, data in SENSOR_CATALOG.items()
        if sport in [s.lower() for s in data.get("sport_relevance", [])]
    ]
