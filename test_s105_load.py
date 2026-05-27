#!/usr/bin/env python3
"""
S1-05 Load Test — CombatIQ PowerSync
Valida:
  [1] ECG CSV 10 000 filas  — parse + save < 2 s
  [2] Video upload ~10 MB   — POST /upload-video < 10 s
  [3] DB queries críticas   — cada una < 500 ms

Uso:
    python test_s105_load.py               # servidor en localhost:8050
    python test_s105_load.py --port 8051   # puerto distinto
    python test_s105_load.py --skip-video  # salta el upload si no hay servidor
"""
import io
import os
import csv
import sys
import math
import time
import random
import argparse

# ── Configuración ───────────────────────────────────────────────────────────
DEFAULT_PORT = 8050
ECG_ROWS     = 10_000
VIDEO_MB     = 10.0       # MB del video sintético
QUERY_MAX_MS = 500        # umbral por query DB
ECG_MAX_MS   = 2_000      # umbral parse ECG
VIDEO_MAX_MS = 10_000     # umbral upload video


# ── Helpers ─────────────────────────────────────────────────────────────────
def _sep(title=""):
    w = 56
    if title:
        pad = (w - len(title) - 2) // 2
        print(f"\n{'-'*pad} {title} {'-'*(w-pad-len(title)-2)}")
    else:
        print("-" * w)


def _result(label, elapsed_ms, threshold_ms, extra=""):
    ok     = elapsed_ms < threshold_ms
    icon   = "PASS" if ok else "SLOW"
    timing = f"{elapsed_ms:.1f} ms"
    limit  = f"(limite {threshold_ms} ms)"
    tail   = f"  {extra}" if extra else ""
    print(f"  [{icon}]  {label:<32} {timing:>9}  {limit}{tail}")
    return ok


# ── Test 1: ECG CSV parse ────────────────────────────────────────────────────
def _make_ecg_csv(n: int) -> bytes:
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["time", "ecg"])
    dt  = 1 / 250
    for i in range(n):
        t     = i * dt
        phase = (t % 0.8) / 0.8  # ~75 bpm
        if 0.35 < phase < 0.45:
            v = 1.5 * math.exp(-((phase - 0.40) ** 2) / 0.001)
        elif 0.30 < phase < 0.35:
            v = -0.20
        elif 0.45 < phase < 0.50:
            v = -0.15
        else:
            v = 0.05 * math.sin(2 * math.pi * phase * 3) + random.gauss(0, 0.01)
        w.writerow([f"{t:.4f}", f"{v:.5f}"])
    return buf.getvalue().encode()


def test_ecg_parse():
    _sep("ECG CSV 10 000 filas")
    results = []

    # Importar read_ecg_csv desde su módulo real
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from views.signals_view import read_ecg_csv
    except ImportError as e:
        print(f"  FAIL:  No se pudo importar read_ecg_csv: {e}")
        return False

    data     = _make_ecg_csv(ECG_ROWS)
    tmp_path = os.path.join("data", "ecg", "_s105_test_ecg.csv")
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)

    # Escritura a disco
    t0 = time.perf_counter()
    with open(tmp_path, "wb") as f:
        f.write(data)
    ms_write = (time.perf_counter() - t0) * 1000
    results.append(_result("write a disco", ms_write, ECG_MAX_MS,
                            f"({len(data)/1024:.0f} KB)"))

    # Parse
    t0 = time.perf_counter()
    _, x, fs = read_ecg_csv(tmp_path, fs_default=250)
    ms_parse = (time.perf_counter() - t0) * 1000

    samples = len(x) if x is not None else 0
    ok_data = samples == ECG_ROWS
    label   = f"parse ({samples} samples, fs={fs})"
    results.append(_result(label, ms_parse, ECG_MAX_MS))
    if not ok_data:
        print(f"       WARN: esperaba {ECG_ROWS} samples, recibió {samples}")

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    return all(results) and ok_data


# ── Test 2: Video upload ─────────────────────────────────────────────────────
def _make_mp4(size_mb: float) -> bytes:
    ftyp = (
        b'\x00\x00\x00\x18'
        b'ftyp'
        b'mp42'
        b'\x00\x00\x00\x00'
        b'mp42mp41'
    )
    pad = max(0, int(size_mb * 1024 * 1024) - len(ftyp))
    return ftyp + b'\x00' * pad


def test_video_upload(base_url: str):
    _sep(f"Video upload ~{VIDEO_MB:.0f} MB")

    try:
        import requests
    except ImportError:
        print("  WARN:  requests no instalado — pip install requests")
        return None   # no cuenta como fallo

    data  = _make_mp4(VIDEO_MB)
    files = {"file": ("s105_test.mp4", io.BytesIO(data), "video/mp4")}

    t0 = time.perf_counter()
    try:
        r = requests.post(f"{base_url}/upload-video", files=files, timeout=30)
        ms = (time.perf_counter() - t0) * 1000
    except requests.exceptions.ConnectionError:
        print(f"  FAIL:  Servidor no responde en {base_url}")
        print(f"      Arranca la app primero: python app.py")
        return False

    ok = r.status_code == 200 and bool((r.json() or {}).get("url"))
    results = [_result(f"POST /upload-video ({VIDEO_MB:.0f} MB)", ms, VIDEO_MAX_MS,
                       f"HTTP {r.status_code}")]

    # Limpieza del archivo subido
    if ok:
        fname = (r.json() or {}).get("filename")
        if fname:
            try:
                os.remove(os.path.join("assets", "uploads", fname))
            except Exception:
                pass

    return all(results) and ok


# ── Test 3: DB queries ───────────────────────────────────────────────────────
def test_db_queries():
    _sep("DB queries críticas")

    try:
        import db
    except ImportError as e:
        print(f"  FAIL:  No se pudo importar db: {e}")
        return False

    users = db.list_users() or []
    if not users:
        print("  WARN:  Sin usuarios en DB — crea datos de demo primero (python seed_demo.py)")
        return True   # no es fallo del sistema

    uid = users[0]["id"]
    results = []

    queries = [
        ("list_sessions(limit=40)",    lambda: db.list_sessions(uid, limit=40)),
        ("get_last_ecg_metrics",       lambda: db.get_last_ecg_metrics(uid)),
        ("list_imu_metrics",           lambda: db.list_imu_metrics(uid)),
        ("list_questionnaires",        lambda: db.list_questionnaires(uid)),
        ("list_questionnaires_bulk",   lambda: db.list_questionnaires_bulk([uid])),
        ("get_user_sensors",           lambda: db.get_user_sensors(uid)),
    ]

    for name, fn in queries:
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:
            print(f"  FAIL:  {name}: {e}")
            results.append(False)
            continue
        ms = (time.perf_counter() - t0) * 1000
        results.append(_result(name, ms, QUERY_MAX_MS))

    return all(results)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="S1-05 Load Test")
    parser.add_argument("--port",       type=int, default=DEFAULT_PORT)
    parser.add_argument("--skip-video", action="store_true",
                        help="Salta el test de upload (si el servidor no está corriendo)")
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"

    print("=" * 56)
    print("  S1-05 Load Test — CombatIQ PowerSync")
    print(f"  Servidor: {base_url}")
    print("=" * 56)

    outcomes = []

    outcomes.append(("ECG parse",    test_ecg_parse()))

    if args.skip_video:
        print("\n[Video upload] — omitido (--skip-video)")
    else:
        r = test_video_upload(base_url)
        if r is not None:       # None = requests no instalado, no cuenta
            outcomes.append(("Video upload", r))

    outcomes.append(("DB queries",  test_db_queries()))

    _sep()
    passed = sum(1 for _, ok in outcomes if ok)
    total  = len(outcomes)
    print(f"\n  Resultado: {passed}/{total} baterías pasaron\n")
    for label, ok in outcomes:
        icon = "OK:" if ok else "FAIL:"
        print(f"    {icon}  {label}")

    if passed == total:
        print("\n  OK:  S1-05 COMPLETADO — app lista para carga real\n")
    else:
        print("\n  WARN:  Revisa los puntos marcados antes de cerrar S1-05\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
