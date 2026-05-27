"""
test_replay_session.py -- Verifica que la sesion #30 este correctamente
configurada para funcionar en Replay de combate.
"""
import sys, os, json, csv, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ECG_DIR  = os.path.join(BASE_DIR, "data", "ecg")
DB_PATH  = os.path.join(BASE_DIR, "data", "users.db")
SESSION_ID = 30

PASS = "[OK]"
FAIL = "[FAIL]"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status}  {name}" + (f"  -> {detail}" if detail else ""))
    return condition


print("=" * 65)
print("  TEST: Sesion WT #30 para Replay de Combate")
print("=" * 65)

# 1. Archivos en disco
print("\n[1] Archivos en disco")
ecg_path = os.path.join(ECG_DIR, "combat_12_wt_videoplayback.csv")
imu_path = os.path.join(ECG_DIR, "combat_12_wt_videoplayback_imu.json")
check("ECG file existe", os.path.exists(ecg_path), ecg_path)
check("IMU file existe", os.path.exists(imu_path), imu_path)

video_path = os.path.join(BASE_DIR, "data", "uploads", "videoplayback.mp4")
check("Video existe", os.path.exists(video_path), video_path)

# 2. Formato del ECG
print("\n[2] Formato ECG")
ecg_rows = []
with open(ecg_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    ecg_rows = list(reader)

check("ECG tiene filas", len(ecg_rows) > 100, f"{len(ecg_rows)} filas")
check("ECG tiene columna time", "time" in ecg_rows[0] if ecg_rows else False)
check("ECG tiene columna ecg",  "ecg"  in ecg_rows[0] if ecg_rows else False)

if ecg_rows:
    times = [float(r["time"]) for r in ecg_rows]
    vals  = [float(r["ecg"])  for r in ecg_rows]
    check("ECG cubre 370+ segundos", max(times) >= 370, f"max_t={max(times):.1f}s")
    check("ECG empieza en t=0",      min(times) < 0.5,   f"min_t={min(times):.2f}s")
    check("ECG tiene valores positivos (picos QRS)", max(vals) > 0.5, f"max_val={max(vals):.3f}")
    check("ECG tiene valores cerca de 0 (baseline)", min(vals) < 0.2, f"min_val={min(vals):.3f}")
    # Verificar que hay variacion (no es una linea plana)
    import statistics
    stdev = statistics.stdev(vals[:200])
    check("ECG tiene variacion (no plano)", stdev > 0.05, f"stdev={stdev:.4f}")

# 3. Formato del IMU
print("\n[3] Formato IMU")
with open(imu_path, encoding="utf-8") as f:
    imu_events = json.load(f)

check("IMU es lista", isinstance(imu_events, list), f"{len(imu_events)} eventos")
check("IMU tiene 40+ eventos", len(imu_events) >= 40)

if imu_events:
    req_fields = {"t", "intensity", "type", "round"}
    has_fields = all(req_fields.issubset(e.keys()) for e in imu_events)
    check("IMU tiene campos t/intensity/type/round", has_fields)

    types = set(e["type"] for e in imu_events)
    check("IMU tiene tipo 'dado'",     "dado"     in types)
    check("IMU tiene tipo 'recibido'", "recibido" in types)
    check("IMU tiene tipo 'ruido'",    "ruido"    in types)

    n_dado     = sum(1 for e in imu_events if e["type"] == "dado")
    n_recibido = sum(1 for e in imu_events if e["type"] == "recibido")
    n_ruido    = sum(1 for e in imu_events if e["type"] == "ruido")
    check("Impactos balance razonable (dado 5-20, recibido 5-20)",
          5 <= n_dado <= 20 and 5 <= n_recibido <= 20,
          f"dado={n_dado} recibido={n_recibido}")

    intensities = [e["intensity"] for e in imu_events if e["type"] != "ruido"]
    if intensities:
        check("Intensidades con variacion (no todas iguales)",
              max(intensities) - min(intensities) > 1.0,
              f"rango {min(intensities):.1f}-{max(intensities):.1f}g")
        check("Sin duplicados de timestamp exacto",
              len(imu_events) == len(set(e["t"] for e in imu_events)),
              f"{len(imu_events)} eventos, {len(set(e['t'] for e in imu_events))} timestamps unicos")

    # Verificar que los impactos estan en periodos de lucha
    WT_FIGHT = [(0, 120), (180, 300), (360, 375.4)]
    impacts_in_fight = all(
        any(t0 <= e["t"] <= t1 for t0, t1 in WT_FIGHT)
        for e in imu_events if e["type"] != "ruido"
    )
    check("Todos los impactos en periodos de lucha WT", impacts_in_fight)

    # Impactos distribuidos en los 3 rounds
    rounds_used = set(e["round"] for e in imu_events if e["type"] != "ruido")
    check("Impactos en los 3 rounds (1, 2, 3)", {1, 2, 3} == rounds_used, str(rounds_used))

# 4. Base de datos
print("\n[4] Base de datos")
con = sqlite3.connect(DB_PATH)
row = con.execute("SELECT id, athlete_id, notes, sport FROM sessions WHERE id=?",
                  (SESSION_ID,)).fetchone()
check("Sesion #30 existe en DB", row is not None)

if row:
    notes = row[2] or ""
    check("Notes empieza con 'Combat Monitor'",
          notes.startswith("Combat Monitor"),
          notes[:60])
    check("Notes contiene 'rounds'",  "rounds" in notes)
    check("Notes contiene 'BPM'",     "BPM"    in notes)
    check("Notes contiene 'impactos'","impactos" in notes)
    check("Deporte es Taekwondo",
          (row[3] or "").lower() in ("taekwondo", "tkd"),
          f"sport={row[3]}")

ecg_file = con.execute(
    "SELECT id, filename, fs, user_id FROM ecg_files WHERE session_id=?",
    (SESSION_ID,)).fetchone()
check("ECG file registrado en DB", ecg_file is not None,
      ecg_file[1] if ecg_file else "no encontrado")
if ecg_file:
    check("ECG filename correcto",
          "combat_12_wt_videoplayback" in (ecg_file[1] or ""))
    check("ECG fs=4 Hz", ecg_file[2] == 4, f"fs={ecg_file[2]}")

imu_row = con.execute(
    "SELECT user_id, filename, n_hits FROM imu_metrics WHERE session_id=?",
    (SESSION_ID,)).fetchone()
check("IMU metrics registrado en DB", imu_row is not None)
if imu_row:
    stem = imu_row[1] or ""
    check("IMU stem correcto (termina en _imu)",
          stem.endswith("_imu"),
          f"stem='{stem}'")
    check("IMU n_hits > 0", (imu_row[2] or 0) > 0, f"n_hits={imu_row[2]}")

user_row = con.execute(
    "SELECT id, email, role FROM users WHERE id=?",
    (con.execute("SELECT athlete_id FROM sessions WHERE id=?",
                 (SESSION_ID,)).fetchone()[0],)).fetchone()
check("Atleta asignado existe",  user_row is not None)
if user_row:
    check("Atleta tiene rol deportista", user_row[2] == "deportista", f"email={user_row[1]}")

con.close()

# 5. Codigo -- fix del cursor
print("\n[5] Codigo: fix del cursor en render_sensor_charts")
signals_path = os.path.join(BASE_DIR, "views", "signals_view.py")
with open(signals_path, encoding="utf-8") as f:
    code = f.read()

check("Placeholder cursor en render_sensor_charts",
      "_cursor_placeholder" in code and
      "band_shapes = [_cursor_placeholder]" in code,
      "shapes[0] siempre reservado")

check("Clientside cursor usa shapes[0]",
      "'shapes[0]'" in code or '"shapes[0]"' in code)

check("replay-time-poll interval presente",
      "replay-time-poll" in code)

check("Poll interval <= 1000ms (cursor fluido en demo)",
      "interval=500" in code or "interval=250" in code or "interval=333" in code,
      "500ms = 2 actualizaciones/s minimo")

# 6. Sincronizacion video / ECG / IMU
print("\n[6] Sincronizacion video-datos")
try:
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps_vid = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    vid_dur = total_f / fps_vid
    check("Video cargable con cv2", vid_dur > 10, f"duracion={vid_dur:.1f}s")
except ImportError:
    vid_dur = 375.4   # fallback del analisis previo
    check("Video cargable con cv2", False, "cv2 no disponible")

ecg_max_t = max(float(r["time"]) for r in ecg_rows)
check("ECG duracion aprox igual a video (diferencia < 3s)",
      abs(ecg_max_t - vid_dur) < 3.0,
      f"ecg={ecg_max_t:.1f}s  video={vid_dur:.1f}s  dif={abs(ecg_max_t - vid_dur):.1f}s")

imu_max_t = max(e["t"] for e in imu_events)
check("Ultimo evento IMU dentro del video",
      imu_max_t <= vid_dur + 1.0,
      f"imu_max={imu_max_t:.1f}s  video={vid_dur:.1f}s")

impacts_all = [e for e in imu_events if e["type"] != "ruido"]
out_range   = [e for e in impacts_all if not (0 < e["t"] <= vid_dur + 1.0)]
check("Todos los impactos tienen timestamp seekable (0 < t <= duracion)",
      len(out_range) == 0,
      f"{len(out_range)} impactos fuera de rango de {len(impacts_all)} total")

ts_list = [e["t"] for e in imu_events]
check("IMU ordenado cronologicamente (UI muestra eventos en orden)",
      ts_list == sorted(ts_list))

# 7. Clasificacion spot-checks (verificados contra el video)
print("\n[7] Clasificacion spot-checks")
impacts_all = [e for e in imu_events if e["type"] != "ruido"]

ev_57 = next((e for e in impacts_all if abs(e["t"] - 57.0) < 2.0), None)
check("t~57s es 'dado' (Garcia Martinez pateando, verificado en captura)",
      ev_57 is not None and ev_57["type"] == "dado",
      f"tipo={ev_57['type'] if ev_57 else 'no encontrado'}  "
      f"t={ev_57['t'] if ev_57 else '?'}")

r1_dado = sum(1 for e in impacts_all if e["round"] == 1 and e["type"] == "dado")
r1_rec  = sum(1 for e in impacts_all if e["round"] == 1 and e["type"] == "recibido")
check("R1 bidireccional: hay dado Y recibido",
      r1_dado >= 1 and r1_rec >= 1,
      f"R1 dado={r1_dado}  recibido={r1_rec}")

r3_dado = sum(1 for e in impacts_all if e["round"] == 3 and e["type"] == "dado")
r3_rec  = sum(1 for e in impacts_all if e["round"] == 3 and e["type"] == "recibido")
check("R3 bidireccional: ambos luchadores activos (dado y recibido presentes)",
      r3_dado >= 1 and r3_rec >= 1,
      f"R3 dado={r3_dado}  recibido={r3_rec}  [optical flow: fb=7.2 fr=5.6 en t=360s]")

max_g_val = max(e["intensity"] for e in impacts_all)
check("Impacto mas fuerte >= 4g (visible en grafica IMU)",
      max_g_val >= 4.0,
      f"max={max_g_val:.1f}g")

# 8. Pipeline click-to-seek
print("\n[8] Pipeline click-to-seek (codigo + datos)")
check("Store replay-seek-target en layout",
      '"replay-seek-target"' in code or "'replay-seek-target'" in code)

check("Clientside seek: vid.currentTime = seek_t",
      "vid.currentTime = seek_t" in code)

check("seek_to_annotation callback devuelve ann['time']",
      "anns[idx][\"time\"]" in code or "anns[idx]['time']" in code)

check("_generate_auto_annotations existe en signals_view",
      "_generate_auto_annotations" in code)

check("IMU dado -> anotacion tipo 'attack'",
      '"attack"' in code and 'hit_type == "dado"' in code)

check("IMU recibido -> anotacion tipo 'defense'",
      '"defense"' in code and '"recibido"' in code)

MIN_G_THRESHOLD = 3.0
visible_dado = [e for e in impacts_all
                if e["type"] == "dado" and e["intensity"] >= MIN_G_THRESHOLD]
visible_rec  = [e for e in impacts_all
                if e["type"] == "recibido" and e["intensity"] >= MIN_G_THRESHOLD]
check("Hay eventos 'attack' visibles en UI (dado >= 3g)",
      len(visible_dado) > 0,
      f"{len(visible_dado)} ataques sobre umbral {MIN_G_THRESHOLD}g")
check("Hay eventos 'defense' visibles en UI (recibido >= 3g)",
      len(visible_rec) > 0,
      f"{len(visible_rec)} defensas sobre umbral {MIN_G_THRESHOLD}g")

all_seekable = all(0 < e["t"] <= vid_dur + 1.0
                   for e in visible_dado + visible_rec)
check("Todos los eventos visibles tienen posicion seekable en el video",
      all_seekable,
      f"{len(visible_dado + visible_rec)} eventos verificados")

# 9. Resumen
print("\n" + "=" * 65)
total  = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = total - passed
print(f"  RESULTADO: {passed}/{total} pruebas pasaron  ({failed} fallaron)")
if failed == 0:
    print("  Todo listo para la demo.")
else:
    print("  Pruebas fallidas:")
    for r in results:
        if r[0] == FAIL:
            print(f"    - {r[1]}: {r[2]}")
print("=" * 65)

sys.exit(0 if failed == 0 else 1)
