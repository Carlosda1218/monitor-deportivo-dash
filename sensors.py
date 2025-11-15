def catalog():
    return {
        "ECG": {"name":"ECG / HRV"},
        "IMU_HEAD": {"name":"IMU Cabeza/pecho"},
        "EMG": {"name":"EMG"},
        "ACC_TIBIA": {"name":"Acelerómetro Tibia/Empeine"},
        "ACC_GLOVE": {"name":"Acelerómetro Guante"},
    }

def labels_for_checklist():
    return [{"label":v["name"], "value":k} for k,v in catalog().items()]

def description(code:str):
    desc = {
        "ECG": "Frecuencia cardiaca y variabilidad (SDNN, RMSSD). Indica readiness y fatiga.",
        "IMU_HEAD": "Conteo de impactos, pico de g y exposición total. Útil en deportes de contacto.",
        "EMG": "RMS y tiempo bajo tensión; asimetrías L/R.",
        "ACC_TIBIA": "Potencia/impacto de patadas (pico g × duración).",
        "ACC_GLOVE": "Conteo e intensidad de golpes.",
    }
    return desc.get(code, "Sensor no documentado.")
