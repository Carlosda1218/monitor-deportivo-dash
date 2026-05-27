"""
test_app_flow.py -- Verifica el flujo completo de la app para la demo:
  [1] App puede compilarse sin errores de sintaxis
  [2] Cadena de carga ECG: DB -> filename -> CSV -> puntos
  [3] Cadena de carga IMU: DB -> stem -> JSON -> eventos
  [4] Dropdown de Replay: la query real devuelve la sesion demo vigente
  [5] Figura Plotly: shapes[0] esta reservado para el cursor antes de renderizar
"""
import sys, os, csv, json, re, sqlite3, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "data", "users.db")
ECG_DIR    = os.path.join(BASE_DIR, "data", "ecg")
SESSION_ID = 34
ATHLETE_ID = 21   # carlos.tkd@demo.combatiq

PASS = "[OK]"
FAIL = "[FAIL]"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status}  {name}" + (f"  -> {detail}" if detail else ""))
    return condition


print("=" * 65)
print("  TEST: Flujo completo de la app para la demo")
print("=" * 65)

# -------------------------------------------------------------------
# [1] App.py: sintaxis y guard de main
# -------------------------------------------------------------------
print("\n[1] app.py: compilacion y guard de main")

app_path = os.path.join(BASE_DIR, "app.py")
result = subprocess.run(
    [sys.executable, "-m", "py_compile", app_path],
    capture_output=True, text=True,
)
check("app.py compila sin errores de sintaxis",
      result.returncode == 0,
      result.stderr[:120] if result.stderr else "OK")

with open(app_path, encoding="utf-8", errors="ignore") as f:
    app_src = f.read()

check("app.py tiene guard 'if __name__ == __main__' (no arranca al importar)",
      'if __name__ == "__main__":' in app_src or "if __name__ == '__main__':" in app_src)

check("db modulo importado en app.py",
      "import db" in app_src or "from db import" in app_src)

check("signals_view registrado en app.py",
      "signals_view" in app_src)

# -------------------------------------------------------------------
# [2] Cadena de carga ECG: DB -> filename -> CSV -> puntos
# -------------------------------------------------------------------
print("\n[2] Cadena de carga ECG (DB -> file -> datos)")

import db as db_module

ecg_files = db_module.list_ecg_files_by_session(SESSION_ID) or []
check(f"db.list_ecg_files_by_session({SESSION_ID}) devuelve resultado",
      len(ecg_files) > 0,
      f"{len(ecg_files)} archivo(s) registrado(s)")

if ecg_files:
    ecg_fname = os.path.basename(ecg_files[0].get("filename", ""))
    ecg_fpath = os.path.join(ECG_DIR, ecg_fname)
    check("Filename en DB no tiene ruta absoluta embebida (solo basename)",
          ecg_fname == ecg_files[0].get("filename", ""),
          f"fname='{ecg_fname}'")
    check("Archivo ECG existe en disco con ese filename",
          os.path.exists(ecg_fpath),
          ecg_fpath)

    ecg_pts = []
    if os.path.exists(ecg_fpath):
        with open(ecg_fpath, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    ecg_pts.append({
                        "t": float(row.get("time", row.get("t", 0))),
                        "y": float(row.get("ecg",  row.get("y", 0))),
                    })
                except (ValueError, TypeError):
                    pass

    check("ECG carga correctamente como lista de puntos {t, y}",
          len(ecg_pts) > 100,
          f"{len(ecg_pts)} puntos")
    check("Claves del punto ECG son 't' e 'y' (formato que espera el chart)",
          ecg_pts and "t" in ecg_pts[0] and "y" in ecg_pts[0])
    check("ECG tiene rango temporal completo (> 300s)",
          ecg_pts and max(p["t"] for p in ecg_pts) > 300,
          f"max_t={max(p['t'] for p in ecg_pts):.1f}s" if ecg_pts else "sin datos")

# -------------------------------------------------------------------
# [3] Cadena de carga IMU: DB -> stem -> JSON -> eventos
# -------------------------------------------------------------------
print("\n[3] Cadena de carga IMU (DB -> stem -> JSON -> eventos)")

imu_rows = db_module.list_imu_metrics_by_session(SESSION_ID) or []
check(f"db.list_imu_metrics_by_session({SESSION_ID}) devuelve resultado",
      len(imu_rows) > 0,
      f"{len(imu_rows)} fila(s)")

if imu_rows:
    stem = os.path.basename(imu_rows[0].get("filename", ""))
    imu_path = os.path.join(ECG_DIR, f"{stem}.json")
    check("Stem IMU termina en '_imu' (no agrega doble extension)",
          stem.endswith("_imu"),
          f"stem='{stem}'")
    check("Archivo IMU JSON existe con ruta stem + '.json'",
          os.path.exists(imu_path),
          imu_path)

    imu_events = []
    if os.path.exists(imu_path):
        with open(imu_path, encoding="utf-8") as fh:
            data = json.load(fh)
        imu_events = data if isinstance(data, list) else []

    check("IMU carga correctamente como lista de eventos",
          len(imu_events) > 0,
          f"{len(imu_events)} eventos")
    check("Eventos IMU tienen clave 't' (tiempo en segundos)",
          imu_events and "t" in imu_events[0])
    check("Eventos IMU tienen clave 'intensity' (g)",
          imu_events and "intensity" in imu_events[0])
    check("Eventos IMU tienen clave 'type' (dado/recibido/ruido)",
          imu_events and "type" in imu_events[0])

# -------------------------------------------------------------------
# [4] Dropdown de Replay: query real devuelve la sesion demo vigente
# -------------------------------------------------------------------
print(f"\n[4] Replay dropdown: query completa devuelve sesion #{SESSION_ID}")

# Simular exactamente lo que hace populate_replay_sessions para rol deportista
sessions_raw = db_module.list_sessions(ATHLETE_ID, limit=40) or []
check("db.list_sessions(21) devuelve sesiones",
      len(sessions_raw) > 0,
      f"{len(sessions_raw)} sesiones para athlete_id={ATHLETE_ID}")

# Filtro: solo notas que empiecen con "Combat Monitor"
combat_sessions = [s for s in sessions_raw
                   if (s.get("notes") or "").startswith("Combat Monitor")]
check("Hay sesiones con notas 'Combat Monitor' para el atleta",
      len(combat_sessions) > 0,
      f"{len(combat_sessions)} sesiones Combat Monitor")

target_session = next((s for s in sessions_raw if s.get("id") == SESSION_ID), None)
check(f"Sesion #{SESSION_ID} esta en las sesiones del atleta (id=21)",
      target_session is not None,
      f"id={target_session.get('id') if target_session else 'no encontrada'}")

if target_session:
    notes = target_session.get("notes") or ""
    check(f"Notes de sesion #{SESSION_ID} pasa el filtro 'startswith Combat Monitor'",
          notes.startswith("Combat Monitor"),
          notes[:60])

    rounds_m = re.search(r'(\d+) rounds?', notes)
    bpm_m    = re.search(r'Peak BPM (\d+)', notes)
    imp_m    = re.search(r'(\d+) impactos?', notes)
    check("Label del dropdown contiene rounds, BPM e impactos",
          bool(rounds_m and bpm_m and imp_m),
          f"rounds={rounds_m.group(1) if rounds_m else '?'}  "
          f"BPM={bpm_m.group(1) if bpm_m else '?'}  "
          f"imp={imp_m.group(1) if imp_m else '?'}")

# Verificar que el dropdown no filtra la sesion demo por error
opts_simulated = []
for s in sessions_raw[:40]:
    sid   = s.get("id")
    notes = (s.get("notes") or "")
    if sid is None or not notes.startswith("Combat Monitor"):
        continue
    opts_simulated.append(sid)
check(f"Sesion #{SESSION_ID} apareceria en el dropdown (pasa todos los filtros)",
      SESSION_ID in opts_simulated,
      f"opciones generadas: {opts_simulated}")

# -------------------------------------------------------------------
# [5] Figura Plotly: shapes[0] reservado para cursor
# -------------------------------------------------------------------
print("\n[5] Figura Plotly: integridad del placeholder shapes[0]")

with open(os.path.join(BASE_DIR, "views", "signals_view.py"),
          encoding="utf-8", errors="ignore") as f:
    sv_code = f.read()

# Verificar que el placeholder tiene opacity=0 y line width=0 (invisible)
check("Placeholder tiene opacity=0 (invisible al cargar)",
      '"opacity": 0' in sv_code or "'opacity': 0" in sv_code)
check("Placeholder tiene line width=0 (no ocupa espacio)",
      '"width": 0' in sv_code or "'width': 0" in sv_code)

# Verificar que las band_shapes NUNCA se insertan en posicion 0
# (eso pisaria el cursor)
check("rest_band_shapes se insertan DESPUES del placeholder ([placeholder] + bands)",
      "band_shapes = [_cursor_placeholder] + _rest_band_shapes" in sv_code or
      "band_shapes = [_placeholder] + _raw_bands" in sv_code)

# Verificar que la clientside callback actualiza shapes[0] y NO shapes[1] u otro
check("Clientside callback actualiza 'shapes[0]' (indice correcto)",
      "'shapes[0]'" in sv_code or '"shapes[0]"' in sv_code)

# Verificar que NO hay codigos que puedan pisar shapes[0] con algo distinto
# (buscamos si hay algun lugar donde shapes[0] se asigne a algo que no sea el cursor)
lines_shapes0 = [line.strip() for line in sv_code.splitlines()
                 if "shapes[0]" in line and "cursor" not in line.lower()
                 and "_cursor" not in line and "placeholder" not in line.lower()
                 and "#" not in line.split("shapes[0]")[0]]
check("No hay asignaciones espureas de shapes[0] (solo el cursor lo toca)",
      len(lines_shapes0) <= 2,   # la clientside callback usa shapes[0] 1-2 veces
      f"{len(lines_shapes0)} usos: {lines_shapes0[:2]}")

# -------------------------------------------------------------------
# Resumen
# -------------------------------------------------------------------
print("\n" + "=" * 65)
total  = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = total - passed
print(f"  RESULTADO: {passed}/{total} pruebas pasaron  ({failed} fallaron)")
if failed == 0:
    print("  Flujo de app verificado. Listo para checklist del browser.")
else:
    print("  Pruebas fallidas:")
    for r in results:
        if r[0] == FAIL:
            print(f"    - {r[1]}: {r[2]}")
print("=" * 65)

sys.exit(0 if failed == 0 else 1)
