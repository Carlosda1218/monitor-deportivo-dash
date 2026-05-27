"""
test_sensor_hw.py — Simulador de hardware para probar los endpoints de sensores.

USO:
    1. Arranca la app:   python app.py
    2. Abre otro terminal y ejecuta:

    ECG (por defecto):
        python test_sensor_hw.py

    IMU — guante de boxeo:
        python test_sensor_hw.py --sensor IMU_GLOVE

    IMU — tobillera TKD:
        python test_sensor_hw.py --sensor IMU_FOOT

El script hace todo solo:
  - Busca un user_id válido en la DB (o usa el que pases con --user)
  - Registra un dispositivo simulado vía db.register_device()
  - Envía pings y datos a la API REST
  - Consulta el estado y lo imprime
  - Limpia el dispositivo de prueba al terminar

Opciones:
    --user   5               User ID destino (por defecto busca el primero disponible)
    --host   http://127.0.0.1:8051
    --loops  3               Cuántos ciclos de ping+data enviar
    --sensor ECG|IMU_GLOVE|IMU_FOOT|IMU_HEAD   Tipo de sensor a simular
    --keep                   No elimina el dispositivo al terminar
"""

import random
import sys
import time
import argparse
import requests

# -- Intentar importar db directamente (sin arrancar Dash) ---------------------
try:
    import db as _db
    DB_AVAILABLE = True
except Exception as e:
    DB_AVAILABLE = False
    print(f"[WARN] db.py no disponible directamente: {e}")

# -- Config --------------------------------------------------------------------
FAKE_DEVICE_ID = "TEST:AA:BB:CC:DD:00"
FAKE_FIRMWARE  = "0.0.1-test"

# Perfiles de simulación por sensor
_SENSOR_PROFILES = {
    "ECG": {
        "label":   "Simulador ECG (test)",
        "code":    "ECG",
    },
    "IMU_GLOVE": {
        "label":   "Simulador IMU Guante Boxeo (test)",
        "code":    "IMU_GLOVE",
    },
    "IMU_FOOT": {
        "label":   "Simulador IMU Tobillera TKD (test)",
        "code":    "IMU_FOOT",
    },
    "IMU_HEAD": {
        "label":   "Simulador IMU Casco (test)",
        "code":    "IMU_HEAD",
    },
}

SEP = "-" * 60


def _ok(msg):   print(f"  OK  {msg}")
def _fail(msg): print(f"  FAIL  {msg}")
def _info(msg): print(f"  *  {msg}")


def find_user_id():
    """Busca el primer user_id con rol 'deportista' en la DB."""
    if not DB_AVAILABLE:
        return None
    try:
        users = _db.list_users() or []
        for u in users:
            if u.get("role") == "deportista":
                return int(u["id"])
        # Si no hay deportistas, usa el primer usuario
        if users:
            return int(users[0]["id"])
    except Exception as e:
        print(f"[WARN] No se pudo listar usuarios: {e}")
    return None


def step_register(user_id: int, sensor_code: str, label: str):
    """Registra el dispositivo simulado directamente en DB."""
    print(f"\n{SEP}")
    print("PASO 1 — Registrar dispositivo simulado en DB")
    print(SEP)
    if not DB_AVAILABLE:
        _fail("db.py no disponible — omitiendo registro directo.")
        return False
    try:
        _db.register_device(
            user_id,
            sensor_code,
            FAKE_DEVICE_ID,
            device_label=label,
            firmware_version=FAKE_FIRMWARE,
        )
        _ok(f"Dispositivo '{FAKE_DEVICE_ID}' ({sensor_code}) registrado para user_id={user_id}")
        return True
    except Exception as e:
        _fail(f"Error: {e}")
        return False


def step_ping(host: str, user_id: int, sensor_code: str):
    """Envía POST /api/sensor-ping."""
    print(f"\n{SEP}")
    print("PASO 2 — Enviar ping (simula heartbeat del hardware)")
    print(SEP)
    url  = f"{host}/api/sensor-ping"
    body = {"device_id": FAKE_DEVICE_ID, "user_id": user_id, "sensor_code": sensor_code}
    try:
        r = requests.post(url, json=body, timeout=5)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            _ok(f"Ping aceptado  →  found={data.get('found')}  status={r.status_code}")
        else:
            _fail(f"Respuesta inesperada: {r.status_code} {data}")
        return r.status_code == 200
    except requests.exceptions.ConnectionError:
        _fail("No se pudo conectar. ¿Está corriendo la app en " + host + "?")
        return False
    except Exception as e:
        _fail(f"Error: {e}")
        return False


def step_send_ecg(host: str, user_id: int, cycle: int):
    """Envía POST /api/sensor-data con lecturas ECG simuladas."""
    print(f"\n{SEP}")
    print(f"PASO 3 (ciclo {cycle}) — Enviar datos ECG simulados")
    print(SEP)
    url  = f"{host}/api/sensor-data"
    body = {
        "device_id":   FAKE_DEVICE_ID,
        "user_id":     user_id,
        "sensor_code": "ECG",
        "filename":    f"ecg_test_ciclo{cycle}.csv",
        "fs":          250,
        "bpm":         round(60 + cycle * 3 + 0.5, 1),
        "sdnn":        round(45.0 - cycle * 1.5, 1),
        "rmssd":       round(38.0 - cycle * 1.0, 1),
        "peaks_count": 180 + cycle * 5,
    }
    _info(f"Payload: BPM={body['bpm']}  SDNN={body['sdnn']}  RMSSD={body['rmssd']}")
    try:
        r = requests.post(url, json=body, timeout=5)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            _ok(f"Datos guardados  →  sensor_code={data.get('sensor_code')}  status={r.status_code}")
        else:
            _fail(f"Respuesta inesperada: {r.status_code} {data}")
        return r.status_code == 200
    except requests.exceptions.ConnectionError:
        _fail("No se pudo conectar.")
        return False
    except Exception as e:
        _fail(f"Error: {e}")
        return False


# Umbrales de detección por tipo de sensor (g)
_IMU_THRESHOLDS = {
    "IMU_GLOVE":  2.5,   # guante boxeo — golpe
    "IMU_WRIST":  2.5,
    "IMU_FOOT":   3.5,   # tobillera TKD — patada
    "IMU_ANKLE":  3.5,
    "IMU_HEAD":   1.5,   # casco — impacto recibido
}
_HIT_LABEL = {
    "IMU_GLOVE": "golpes",  "IMU_WRIST": "golpes",
    "IMU_FOOT":  "patadas", "IMU_ANKLE": "patadas",
    "IMU_HEAD":  "impactos recibidos",
}


def _sim_imu_round(sensor_code: str, duration_s: int = 120) -> dict:
    """
    Simula un round completo (duration_s seg.) y devuelve métricas IMU.
    Modela ráfagas de actividad con picos de impacto realistas.
    """
    threshold = _IMU_THRESHOLDS.get(sensor_code, 2.5)
    hits      = 0
    sum_g     = 0.0
    max_g     = 0.0
    samples   = duration_s * 50   # 50 Hz simulado

    above = False
    for _ in range(samples):
        # ~4 % de muestras superan el umbral (ráfaga de actividad)
        if random.random() < 0.04:
            mag = threshold * random.uniform(1.05, 2.2)
        else:
            mag = random.uniform(0.3, threshold * 0.85)

        sum_g += mag
        if mag > max_g:
            max_g = mag

        if mag >= threshold:
            if not above:
                above = True
                hits += 1
        else:
            above = False

    mean_g  = round(sum_g / samples, 3)
    hpm     = round(hits / (duration_s / 60), 1)
    return {
        "n_hits":       hits,
        "hits_per_min": hpm,
        "mean_int_g":   mean_g,
        "max_int_g":    round(max_g, 3),
    }


def step_send_imu(host: str, user_id: int, sensor_code: str, cycle: int, round_s: int = 120):
    """Envía POST /api/sensor-data con métricas IMU simuladas de un round."""
    print(f"\n{SEP}")
    print(f"PASO 3 (ciclo {cycle}) — Enviar datos IMU simulados ({sensor_code})")
    print(SEP)
    metrics = _sim_imu_round(sensor_code, duration_s=round_s)
    url  = f"{host}/api/sensor-data"
    body = {
        "device_id":   FAKE_DEVICE_ID,
        "user_id":     user_id,
        "sensor_code": sensor_code,
        "filename":    f"imu_{sensor_code.lower()}_ciclo{cycle}",
        **metrics,
    }
    label = _HIT_LABEL.get(sensor_code, "impactos")
    _info(
        f"{label}: {metrics['n_hits']}  "
        f"({metrics['hits_per_min']}/min)  "
        f"intensidad media: {metrics['mean_int_g']:.2f}g  "
        f"pico: {metrics['max_int_g']:.2f}g"
    )
    try:
        r = requests.post(url, json=body, timeout=5)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            _ok(f"Métricas IMU guardadas  →  sensor_code={data.get('sensor_code')}  status={r.status_code}")
        else:
            _fail(f"Respuesta inesperada: {r.status_code} {data}")
        return r.status_code == 200
    except requests.exceptions.ConnectionError:
        _fail("No se pudo conectar.")
        return False
    except Exception as e:
        _fail(f"Error: {e}")
        return False


def step_check_status(host: str, user_id: int):
    """Consulta GET /api/sensor-status/<user_id> y muestra el estado."""
    print(f"\n{SEP}")
    print("PASO 4 — Consultar estado de dispositivos")
    print(SEP)
    url = f"{host}/api/sensor-status/{user_id}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        devices = data.get("devices", [])
        if not devices:
            _info("No hay dispositivos registrados para este usuario.")
            return
        for dev in devices:
            status = dev.get("computed_status", "?")
            symbol = {"connected": "[verde]", "idle": "[amarillo]", "offline": "[rojo]", "paired": "[gris]"}.get(status, "[?]")
            print(f"\n  {symbol}  [{status.upper()}]  {dev.get('device_id')}")
            print(f"      Sensor:    {dev.get('sensor_code')}")
            print(f"      Etiqueta:  {dev.get('device_label') or '—'}")
            print(f"      Firmware:  {dev.get('firmware_version') or '—'}")
            print(f"      Último ping: {dev.get('last_seen') or 'nunca'}")
    except requests.exceptions.ConnectionError:
        _fail("No se pudo conectar.")
    except Exception as e:
        _fail(f"Error: {e}")


def step_check_saved(user_id: int, sensor_code: str):
    """Verifica directamente en DB que los datos se guardaron correctamente."""
    print(f"\n{SEP}")
    print("PASO 5 — Verificar que los datos quedaron en la DB")
    print(SEP)
    if not DB_AVAILABLE:
        _info("db.py no disponible — omitiendo verificación directa.")
        return
    try:
        if sensor_code == "ECG":
            metrics = _db.get_last_ecg_metrics(user_id)
            if metrics:
                _ok("Última métrica ECG guardada:")
                print(f"      BPM:   {metrics.get('bpm')}")
                print(f"      SDNN:  {metrics.get('sdnn')}")
                print(f"      RMSSD: {metrics.get('rmssd')}")
            else:
                _fail("No se encontraron métricas ECG.")
        else:
            rows = _db.list_imu_metrics(user_id, limit=1) if hasattr(_db, "list_imu_metrics") else []
            if rows:
                m = rows[0]
                _ok("Última métrica IMU guardada:")
                print(f"      Hits:      {m.get('n_hits') or m.get('hits')}")
                print(f"      HPM:       {m.get('hits_per_min') or m.get('hpm')}")
                print(f"      Media (g): {m.get('mean_int_g') or m.get('mean_g')}")
                print(f"      Pico (g):  {m.get('max_int_g')  or m.get('max_g')}")
            else:
                _fail(f"No se encontraron métricas IMU para este usuario.")
    except Exception as e:
        _fail(f"Error consultando DB: {e}")


def step_cleanup(user_id: int):
    """Elimina el dispositivo de prueba de la DB."""
    print(f"\n{SEP}")
    print("PASO 6 — Limpieza (eliminar dispositivo de prueba)")
    print(SEP)
    if not DB_AVAILABLE:
        _info("db.py no disponible — nada que limpiar.")
        return
    try:
        deleted = _db.delete_device(FAKE_DEVICE_ID, user_id)
        if deleted:
            _ok(f"Dispositivo '{FAKE_DEVICE_ID}' eliminado.")
        else:
            _info("El dispositivo ya no existía (ya fue limpiado).")
    except Exception as e:
        _fail(f"Error limpiando: {e}")


# -- Entry point ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Simulador de hardware para CombatIQ Sensores")
    parser.add_argument("--user",   type=int, default=None, help="User ID destino")
    parser.add_argument("--host",   type=str, default="http://127.0.0.1:8051")
    parser.add_argument("--loops",  type=int, default=3,   help="Ciclos de ping+data")
    parser.add_argument("--sensor", type=str, default="ECG",
                        choices=list(_SENSOR_PROFILES.keys()),
                        help="Tipo de sensor a simular (default: ECG)")
    parser.add_argument("--round",  type=int, default=120, dest="round_s",
                        help="Duración del round IMU simulado en segundos (default: 120)")
    parser.add_argument("--keep",   action="store_true",   help="No eliminar dispositivo al terminar")
    args = parser.parse_args()

    profile      = _SENSOR_PROFILES[args.sensor]
    sensor_code  = profile["code"]
    sensor_label = profile["label"]
    is_imu       = sensor_code.startswith("IMU")

    print(f"\n{'='*60}")
    print(" CombatIQ — Test de hardware de sensores")
    print(f"{'='*60}")
    print(f"  Sensor: {sensor_code}  ({sensor_label})")
    print(f"  Host:   {args.host}")
    print(f"  Loops:  {args.loops}")
    if is_imu:
        print(f"  Round:  {args.round_s}s simulados por ciclo")
    print(f"  Keep:   {args.keep}")

    # Resolver user_id
    user_id = args.user
    if not user_id:
        user_id = find_user_id()
    if not user_id:
        print("\n[ERROR] No se encontró ningún user_id. Usa --user <id>.")
        sys.exit(1)
    print(f"  User:   {user_id}\n")

    # Paso 1: registrar en DB
    step_register(user_id, sensor_code, sensor_label)

    # Paso 2: primer ping
    ok = step_ping(args.host, user_id, sensor_code)
    if not ok:
        print("\n[ABORT] La app no responde. Asegúrate de que esté corriendo.")
        sys.exit(1)

    # Paso 3: ciclos de datos
    for i in range(1, args.loops + 1):
        if is_imu:
            step_send_imu(args.host, user_id, sensor_code, cycle=i, round_s=args.round_s)
        else:
            step_send_ecg(args.host, user_id, cycle=i)
        if i < args.loops:
            print(f"\n  [espera 1 s...]")
            time.sleep(1)

    # Paso 4: consulta estado final
    step_check_status(args.host, user_id)

    # Paso 5: verifica en DB
    step_check_saved(user_id, sensor_code)

    # Paso 6: limpieza
    if not args.keep:
        step_cleanup(user_id)
    else:
        print(f"\n{SEP}")
        print("  --keep activo: el dispositivo QUEDA en la DB.")
        print("  Ve a /sensores en la app para ver el dot verde en la sensor card.")
        print(SEP)

    print(f"\n{'='*60}")
    print(" Test completado.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
