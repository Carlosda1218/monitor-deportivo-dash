# test_claude.py — Prueba rapida de la integracion Claude en CombatIQ.
# Ejecutar: .venv/Scripts/python.exe test_claude.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_insights import generate_coaching_note, analyze_combat_session, _load_api_key

print("=" * 60)
print("  TEST: Integración Claude — CombatIQ")
print("=" * 60)

# Verificar API key
key = _load_api_key()
if not key:
    print("\n[FAIL] ANTHROPIC_API_KEY no encontrada en .env")
    sys.exit(1)
print(f"\n[OK]  API key cargada: {key[:18]}...{key[-4:]}")

# ── Test 1: Coaching note (Sonnet + adaptive thinking) ────────────────────────
print("\n[1] generate_coaching_note (Sonnet 4.6 + adaptive thinking)")
print("    Generando... puede tardar 5-15s")

report = {
    "acwr":    {"ratio": 1.2, "acute_load": 1200, "chronic_load": 1000,
                "zone": "optimal", "trend": "rising"},
    "hrv":     {"today_rmssd": 31, "baseline_rmssd": 45,
                "delta_pct": -31, "zone": "fatiga_leve"},
    "wellness":{"latest_score": 65, "avg_score": 70, "trend": "stable", "low_days": 1},
    "imu":     {"total_hits": 20, "avg_intensity": 3.17,
                "peak_intensity": 5.5, "trend": "stable"},
    "alerts":  [{"level": "warning", "title": "HRV bajo",
                 "message": "RMSSD 31ms — 31% bajo baseline"}],
    "generated_at": "2026-05-18T10:00:00",
}
note = generate_coaching_note(report, athlete_name="García", sport="taekwondo")
print("\n" + note)

# ── Test 2: Análisis estructurado con tool use (Opus 4.7) ─────────────────────
print("\n" + "=" * 60)
print("[2] analyze_combat_session (Opus 4.7 + tool use)")
print("    Generando... puede tardar 15-30s")

session = {
    "session_id": 30,
    "athlete_name": "García",
    "age": 21,
    "weight_category": "-63kg",
    "experience_years": 3,
    "days_to_competition": 18,
    "ecg": {
        "bpm": 163, "sdnn": 42, "rmssd": 31,
        "by_round": {1: {"bpm": 142}, 2: {"bpm": 168}, 3: {"bpm": 181}},
    },
    "imu": {
        "total_dado": 8, "total_recibido": 12,
        "avg_intensity": 3.17, "peak_intensity": 5.5,
        "by_round": {
            1: {"dado": 4, "dado_g": 3.8, "recibido": 2, "recibido_g": 4.1},
            2: {"dado": 3, "dado_g": 4.2, "recibido": 3, "recibido_g": 3.9},
            3: {"dado": 1, "dado_g": 3.2, "recibido": 7, "recibido_g": 4.8},
        },
    },
}
result = analyze_combat_session(session, sport="taekwondo")

if result.get("error"):
    print(f"\n[FAIL] Error: {result['error']}")
    sys.exit(1)

print(f"\nModelo usado: {result['model_used']}")

print(f"\n--- HALLAZGOS ({len(result['findings'])}) ---")
for f in result["findings"]:
    icon = {"positivo": "✓", "observar": "→", "corregir": "!", "urgente": "!!"}.get(f["severity"], "·")
    print(f"  [{f['severity'].upper():8}] {icon} {f['finding']}")
    print(f"              Evidencia: {f['evidence']}")
    if f.get("drill"):
        print(f"              Ejercicio: {f['drill']}")

print(f"\n--- RIESGOS ({len(result['risks'])}) ---")
for r in result["risks"]:
    sev = r.get("severity", "?").upper()
    print(f"  [{sev:5}] {r.get('risk_type','?')}: {r.get('value','?')}")
    print(f"          -> {r.get('recommendation','?')}")

print(f"\n--- RECOMENDACIONES ({len(result['recommendations'])}) ---")
for r in result["recommendations"]:
    sets = f" ({r['sets_reps']})" if r.get("sets_reps") else ""
    tf   = r.get("timeframe", "?")
    pri  = r.get("priority", "?")
    drill = r.get("drill", "?")
    rat   = r.get("rationale", "?")
    print(f"  P{pri} [{tf:6}] {drill}{sets}")
    print(f"          {rat}")

print(f"\n--- NARRATIVA ---")
print(result["narrative"])

print("\n" + "=" * 60)
print("  Integración Claude: OK")
print("=" * 60)
