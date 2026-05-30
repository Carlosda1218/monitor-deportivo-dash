"""
Genera los 5 CSV de ECG semanales de Carlos (lecturas de HRV en reposo) que la
DB de produccion ya referencia pero cuyos archivos nunca se commitearon.

Cada onda PQRST se genera a 360 Hz con intervalos R-R modelados para que, al
recomputar con la deteccion real de la app (detect_r_peaks + ecg_metrics_from_peaks),
el bpm/SDNN/RMSSD coincidan con las metricas almacenadas en la DB.

Modelo R-R:  RR_i = RR_mean + LF_i + HF_i
  - HF_i ~ N(0, sigma_hf)  con sigma_hf = RMSSD/sqrt(2)   -> domina RMSSD
  - LF_i = oscilacion suave (resp/Mayer)                  -> aporta a SDNN, no a RMSSD
  - var(LF) = SDNN^2 - sigma_hf^2

Ejecutar:  python _gen_carlos_weekly_ecg.py
"""
import os, math, random

ECG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ecg")
FS = 360

# (filename, bpm, sdnn_ms, rmssd_ms, n_peaks)  -- copiado de seed_demo.seed_carlos_tkd
WEEKLY = [
    ("ecg_carlos_w1_lunes.csv",     54.0, 68.2, 52.1, 178),
    ("ecg_carlos_w1_miercoles.csv", 58.0, 61.5, 46.3, 182),
    ("ecg_carlos_w1_viernes.csv",   62.0, 55.3, 41.8, 186),
    ("ecg_carlos_w2_lunes.csv",     52.0, 72.1, 58.7, 174),
    ("ecg_carlos_w2_miercoles.csv", 56.0, 65.8, 50.4, 179),
]


def _pqrst(t_beat: float) -> float:
    """Aproximacion PQRST en [0,1] -> voltaje relativo (igual que seed_demo)."""
    y = 0.0
    if 0.08 < t_beat < 0.22:
        y += 0.08 * math.sin(math.pi * (t_beat - 0.08) / 0.14)
    if 0.28 < t_beat < 0.30:
        y -= 0.04 * math.sin(math.pi * (t_beat - 0.28) / 0.02)
    if 0.30 < t_beat < 0.36:
        y += 0.80 * math.sin(math.pi * (t_beat - 0.30) / 0.06)
    if 0.36 < t_beat < 0.40:
        y -= 0.12 * math.sin(math.pi * (t_beat - 0.36) / 0.04)
    if 0.50 < t_beat < 0.70:
        y += 0.15 * math.sin(math.pi * (t_beat - 0.50) / 0.20)
    return y


# Compensa la perdida de varianza por el suavizado de detect_r_peaks (80 ms),
# para que el SDNN/RMSSD recomputado por la app coincida con el almacenado en DB.
_COMP_SDNN  = 1.06
_COMP_RMSSD = 1.20


def _rr_series(bpm, sdnn_ms, rmssd_ms, n, rng):
    sdnn_ms  = sdnn_ms * _COMP_SDNN
    rmssd_ms = rmssd_ms * _COMP_RMSSD
    rr_mean  = 60.0 / bpm                       # s
    sigma_hf = (rmssd_ms / math.sqrt(2.0)) / 1000.0
    var_lf   = max(0.0, (sdnn_ms / 1000.0) ** 2 - sigma_hf ** 2)
    amp_lf   = math.sqrt(var_lf) * math.sqrt(2.0)   # std de un seno = amp/sqrt(2)
    period   = rng.uniform(9, 13)               # beats por ciclo LF
    phase    = rng.uniform(0, 2 * math.pi)
    rrs = []
    for i in range(n):
        lf = amp_lf * math.sin(2 * math.pi * i / period + phase)
        hf = rng.gauss(0, sigma_hf)
        rrs.append(max(0.33, rr_mean + lf + hf))
    return rrs


def generate(fname, bpm, sdnn_ms, rmssd_ms, n_peaks, seed):
    rng = random.Random(seed)
    rrs = _rr_series(bpm, sdnn_ms, rmssd_ms, n_peaks, rng)
    # limites de cada latido
    starts, acc = [], 0.0
    for rr in rrs:
        starts.append((acc, rr)); acc += rr
    total = acc
    n_samples = int(total * FS)
    path = os.path.join(ECG_DIR, fname)
    bi = 0
    with open(path, "w", newline="") as fh:
        fh.write("time,ecg\n")
        for s in range(n_samples):
            tt = s / FS
            while bi + 1 < len(starts) and tt >= starts[bi][0] + starts[bi][1]:
                bi += 1
            t0, rr = starts[bi]
            ph = (tt - t0) / rr if rr > 0 else 0.0
            y = _pqrst(ph) + rng.gauss(0, 0.010)
            fh.write(f"{round(tt, 4)},{round(y, 4)}\n")
    return path, n_samples, total


def validate(path, fs):
    """Corre la deteccion REAL de la app sobre el archivo generado."""
    from views.signals_view import read_ecg_csv, detect_r_peaks, ecg_metrics_from_peaks
    t, x, fsr = read_ecg_csv(path, fs_default=fs)
    peaks = detect_r_peaks(x, fsr, 0.6)
    bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fsr)
    return len(peaks), bpm, sdnn, rmssd


if __name__ == "__main__":
    os.makedirs(ECG_DIR, exist_ok=True)
    print(f"{'archivo':<28} {'target bpm/sdnn/rmssd':>24}   ->   recomputado (picos, bpm, sdnn, rmssd)")
    print("-" * 110)
    for idx, (fn, bpm, sd, rm, npk) in enumerate(WEEKLY):
        p, n, tot = generate(fn, bpm, sd, rm, npk, seed=4100 + idx)
        try:
            pk, rbpm, rsd, rrm = validate(p, FS)
            print(f"{fn:<28} {bpm:>5.0f}/{sd:>5.1f}/{rm:>5.1f}   "
                  f"->   picos={pk:>3}  bpm={rbpm:5.1f}  sdnn={rsd:5.1f}  rmssd={rrm:5.1f}   "
                  f"({tot:.0f}s, {n} muestras)")
        except Exception as e:
            print(f"{fn:<28} GENERADO ({tot:.0f}s) — validacion fallo: {e}")
