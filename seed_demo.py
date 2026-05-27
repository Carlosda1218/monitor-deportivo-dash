"""
seed_demo.py — Datos de demostración completos para CombatIQ.

Crea / actualiza 4 cuentas con login real:
  TKD Atleta  : carlos.tkd@demo.combatiq   / demo123
  Box Atleta  : marco.box@demo.combatiq    / demo123
  Coach TKD   : ana.coach.tkd@demo.combatiq / demo123
  Coach Box   : rafael.coach.box@demo.combatiq / demo123

Es IDEMPOTENTE: detecta si ya existe el usuario por email y solo
agrega datos que falten (questionnaires, ECG, IMU, peso, nutrición).
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json
import datetime as _dt
import random
import db
from questionnaires import score_breakdown, norm_sport

random.seed(42)  # reproducible


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _ts(days_ago: float, hour: int = 8) -> str:
    return (_dt.datetime.utcnow() - _dt.timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat()


def _get_or_create_user(name, email, pw, role, sport) -> int:
    existing = db.get_user_by_email(email)
    if existing:
        return existing["id"]
    return db.create_user(name=name, email=email, pw=pw, role=role, sport=sport)


def _wipe_questionnaires(uid: int):
    """Borra todos los cuestionarios del usuario para resembrar limpios."""
    with db._get_conn() as con:
        con.execute("DELETE FROM questionnaires WHERE user_id=?", (uid,))


def _wipe_ecg(uid: int):
    with db._get_conn() as con:
        file_ids = [r[0] for r in con.execute(
            "SELECT id FROM ecg_files WHERE user_id=?", (uid,)).fetchall()]
        for fid in file_ids:
            con.execute("DELETE FROM ecg_metrics WHERE ecg_file_id=?", (fid,))
        con.execute("DELETE FROM ecg_files WHERE user_id=?", (uid,))


def _wipe_imu(uid: int):
    with db._get_conn() as con:
        con.execute("DELETE FROM imu_metrics WHERE user_id=?", (uid,))


def _wipe_emg(uid: int):
    with db._get_conn() as con:
        con.execute("DELETE FROM emg_metrics WHERE user_id=?", (uid,))


def _wipe_weight(uid: int):
    with db._get_conn() as con:
        con.execute("DELETE FROM weights WHERE user_id=?", (uid,))


def _wipe_nutrition(uid: int):
    with db._get_conn() as con:
        con.execute("DELETE FROM nutrition_logs WHERE user_id=?", (uid,))


def _count_q(uid: int) -> int:
    with db._get_conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM questionnaires WHERE user_id=?", (uid,)
        ).fetchone()[0]


def _count_ecg(uid: int) -> int:
    with db._get_conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM ecg_files WHERE user_id=?", (uid,)
        ).fetchone()[0]


def _count_imu(uid: int) -> int:
    with db._get_conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM imu_metrics WHERE user_id=?", (uid,)
        ).fetchone()[0]


def _insert_questionnaire(uid: int, ts: str, answers: dict, sport: str,
                           competition: bool, weight: bool, injury: bool,
                           rpe: int, duration: int):
    bd = score_breakdown(answers, sport=norm_sport(sport),
                         competition=competition, weight=weight, injury=injury)
    wellness = round(bd["score"], 1)
    with db._get_conn() as con:
        con.execute(
            "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
            "VALUES (?,?,?,?,?,?)",
            (uid, ts, json.dumps(answers), wellness, rpe, duration),
        )
    return wellness


def _insert_ecg(uid: int, ts: str, bpm: float, sdnn: float, rmssd: float,
                peaks: int, filename: str):
    fid = db.add_ecg_file(uid, filename, fs=360)
    with db._get_conn() as con:
        con.execute(
            "UPDATE ecg_files SET created_at=? WHERE id=?", (ts, fid)
        )
    db.save_ecg_metrics(fid, bpm, sdnn, rmssd, peaks)


def _insert_imu(uid: int, ts: str, n_hits: int, hpm: float,
                mean_g: float, max_g: float, filename: str):
    with db._get_conn() as con:
        con.execute(
            "INSERT INTO imu_metrics (user_id,filename,ts,n_hits,hits_per_min,mean_int_g,max_int_g) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, filename, ts, n_hits, hpm, mean_g, max_g),
        )


def _insert_emg(uid: int, ts: str, rms: float, peak: float, fatigue: float, filename: str):
    with db._get_conn() as con:
        con.execute(
            "INSERT INTO emg_metrics (user_id,filename,ts,rms,peak,fatigue) "
            "VALUES (?,?,?,?,?,?)",
            (uid, filename, ts, rms, peak, fatigue),
        )


def _set_profile(uid: int, level: str, weight_cat: str, dominant: str,
                 status: str, watch: str, comp: str, note: str = ""):
    db.save_athlete_profile(uid, {
        "competitive_level":    level,
        "weight_category":      weight_cat,
        "dominant_side":        dominant,
        "current_status":       status,
        "watch_zone":           watch,
        "competition_proximity": comp,
        "profile_note":         note,
    })


def _adopt(coach_id: int, athlete_id: int):
    db.adopt_athlete(coach_id, athlete_id)
    with db._get_conn() as con:
        con.execute(
            "UPDATE users SET coach_id=? WHERE id=? AND (coach_id IS NULL OR coach_id=0 OR coach_id='')",
            (coach_id, athlete_id),
        )


def _insert_message(sender_id: int, receiver_id: int, body: str, days_ago: float = 0):
    ts = (_dt.datetime.utcnow() - _dt.timedelta(days=days_ago)).isoformat()
    with db._get_conn() as con:
        con.execute(
            "INSERT INTO messages(sender_id, receiver_id, body, ts) VALUES(?,?,?,?)",
            (int(sender_id), int(receiver_id), body.strip(), ts),
        )


def _has_messages(uid1: int, uid2: int) -> bool:
    with db._get_conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)",
            (uid1, uid2, uid2, uid1),
        ).fetchone()[0] > 0


# ─────────────────────────────────────────────
# Perfiles de cuestionario
# ─────────────────────────────────────────────

def _tkd_answers(day_pattern: str) -> dict:
    """
    day_pattern:
      'fresh'   — inicio de semana, bien descansado
      'loaded'  — acumulación media-semana
      'peak'    — máxima carga, fin de semana
      'recover' — día de recuperación
    """
    base = {
        "fresh":   {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,"listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2},
        "loaded":  {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,"listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3},
        "peak":    {"energia":3,"recuperacion":2,"sueno_calidad":2,"sueno_horas":6,"listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":4},
        "recover": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,"listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":1},
    }
    tkd_ext = {
        "fresh":   {"tkd_explosividad":4,"tkd_agilidad":4,"tkd_ritmo":4,"tkd_molestia_inferior":1},
        "loaded":  {"tkd_explosividad":3,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":2},
        "peak":    {"tkd_explosividad":3,"tkd_agilidad":2,"tkd_ritmo":3,"tkd_molestia_inferior":3},
        "recover": {"tkd_explosividad":4,"tkd_agilidad":4,"tkd_ritmo":4,"tkd_molestia_inferior":1},
    }
    return {**base[day_pattern], **tkd_ext[day_pattern]}


def _box_answers(day_pattern: str) -> dict:
    base = {
        "fresh":   {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,"listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2},
        "loaded":  {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,"listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3},
        "peak":    {"energia":3,"recuperacion":2,"sueno_calidad":2,"sueno_horas":6,"listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":4},
        "recover": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,"listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":1},
    }
    box_ext = {
        "fresh":   {"box_ritmo":4,"box_rapidez":4,"box_precision":4,"box_molestia_superior":1},
        "loaded":  {"box_ritmo":3,"box_rapidez":3,"box_precision":3,"box_molestia_superior":2},
        "peak":    {"box_ritmo":3,"box_rapidez":3,"box_precision":3,"box_molestia_superior":3},
        "recover": {"box_ritmo":4,"box_rapidez":4,"box_precision":4,"box_molestia_superior":1},
    }
    return {**base[day_pattern], **box_ext[day_pattern]}


# Ciclo de 7 días repetido: L-M-X-J-V-S-D
_TKD_CYCLE = ["fresh","loaded","loaded","peak","peak","recover","recover"]
_BOX_CYCLE  = ["fresh","loaded","loaded","peak","loaded","recover","recover"]

# RPE y duración por patrón
_RPE = {"fresh":6, "loaded":7, "peak":8, "recover":5}
_DUR = {"fresh":70,"loaded":80,"peak":90,"recover":60}


# ─────────────────────────────────────────────
# TKD ATLETA — Carlos Ríos
# ─────────────────────────────────────────────

def seed_carlos_tkd(force: bool = False) -> int:
    uid = _get_or_create_user(
        "Carlos Ríos", "carlos.tkd@demo.combatiq", "demo123", "deportista", "Taekwondo"
    )
    _set_profile(uid,
        level="Competitivo", weight_cat="-68 kg", dominant="Derecho",
        status="Listo con control", watch="rodilla izquierda",
        comp="Próximas 3-4 semanas",
        note="Torneo regional en 3 semanas. Priorizar entrada limpia y pierna de apoyo."
    )

    # ── Cuestionarios — 14 días diarios ──
    if force or _count_q(uid) < 14:
        if force:
            _wipe_questionnaires(uid)
        for day in range(14, 0, -1):
            pattern = _TKD_CYCLE[(14 - day) % 7]
            ans = _tkd_answers(pattern)
            _insert_questionnaire(uid, _ts(day), ans, "Taekwondo",
                                  competition=True, weight=True, injury=True,
                                  rpe=_RPE[pattern], duration=_DUR[pattern])

    # ── ECG — 5 sesiones ──
    if force or _count_ecg(uid) < 5:
        if force:
            _wipe_ecg(uid)
        ecg_data = [
            (13, 54.0, 68.2, 52.1, 178, "ecg_carlos_w1_lunes.csv"),
            (11, 58.0, 61.5, 46.3, 182, "ecg_carlos_w1_miercoles.csv"),
            (9,  62.0, 55.3, 41.8, 186, "ecg_carlos_w1_viernes.csv"),
            (6,  52.0, 72.1, 58.7, 174, "ecg_carlos_w2_lunes.csv"),
            (4,  56.0, 65.8, 50.4, 179, "ecg_carlos_w2_miercoles.csv"),
        ]
        for (d, bpm, sdnn, rmssd, peaks, fname) in ecg_data:
            _insert_ecg(uid, _ts(d), bpm, sdnn, rmssd, peaks, fname)

    # ── IMU (patadas) — 5 sesiones ──
    if force or _count_imu(uid) < 5:
        if force:
            _wipe_imu(uid)
        imu_data = [
            (13, 112, 32.4, 2.1, 5.8, "imu_carlos_w1_lunes.csv"),
            (11, 135, 38.7, 2.4, 6.5, "imu_carlos_w1_miercoles.csv"),
            (9,  148, 41.2, 2.7, 7.1, "imu_carlos_w1_viernes.csv"),
            (6,  105, 30.1, 1.9, 5.2, "imu_carlos_w2_lunes.csv"),
            (4,  122, 35.6, 2.2, 6.0, "imu_carlos_w2_miercoles.csv"),
        ]
        for (d, hits, hpm, mg, maxg, fname) in imu_data:
            _insert_imu(uid, _ts(d), hits, hpm, mg, maxg, fname)

    # ── EMG (pierna) — 3 sesiones ──
    if force or True:  # always seed if fresh user
        _wipe_emg(uid)
        for d, rms, peak, fat in [(13, 0.41, 1.82, 0.12), (9, 0.48, 2.10, 0.19), (4, 0.38, 1.75, 0.10)]:
            _insert_emg(uid, _ts(d), rms, peak, fat, f"emg_carlos_pierna_{d}d.csv")

    # ── Peso (14 días) ──
    with db._get_conn() as con:
        if con.execute("SELECT COUNT(*) FROM weights WHERE user_id=?", (uid,)).fetchone()[0] < 14:
            weights_kg = [68.4,68.1,67.9,68.2,68.0,67.8,67.6,67.8,67.5,67.7,67.6,67.4,67.5,67.3]
            for i, w in enumerate(weights_kg):
                db.add_weight_entry(uid, (_dt.datetime.utcnow() - _dt.timedelta(days=14-i)).date().isoformat(),
                                    w, target_kg=67.0)

    # ── Nutrición (14 días) ──
    with db._get_conn() as con:
        if con.execute("SELECT COUNT(*) FROM nutrition_logs WHERE user_id=?", (uid,)).fetchone()[0] < 14:
            adhs = [88,90,82,78,85,92,88,80,75,84,89,91,86,93]
            kcals = [2600,2650,2500,2450,2550,2700,2620,2480,2400,2560,2630,2690,2580,2710]
            for i, (adh, kcal) in enumerate(zip(adhs, kcals)):
                db.add_nutrition_entry(uid,
                    (_dt.datetime.utcnow() - _dt.timedelta(days=14-i)).date().isoformat(),
                    adh, kcal)

    db.mark_onboarding_done(uid)
    print(f"  OK Carlos Ríos (TKD atleta) — id={uid}")
    return uid


# ─────────────────────────────────────────────
# BOX ATLETA — Marco Silva
# ─────────────────────────────────────────────

def seed_marco_box(force: bool = False) -> int:
    uid = _get_or_create_user(
        "Marco Silva", "marco.box@demo.combatiq", "demo123", "deportista", "Box"
    )
    _set_profile(uid,
        level="Competitivo", weight_cat="-69 kg", dominant="Derecho",
        status="Recuperación activa", watch="muñeca izquierda",
        comp="Sin competencia cercana",
        note="Recuperando muñeca izquierda. Evitar trabajo de saco con mano izquierda hasta que cierre el ciclo."
    )

    # ── Cuestionarios — 14 días diarios ──
    if force or _count_q(uid) < 14:
        if force:
            _wipe_questionnaires(uid)
        # Marco mejora progresivamente (muñeca en recuperación → mejora)
        mol_sup = [3,3,2,3,2,2,2,2,1,1,1,1,1,1]  # mejora gradual
        for day in range(14, 0, -1):
            pattern = _BOX_CYCLE[(14 - day) % 7]
            ans = _box_answers(pattern)
            ans["box_molestia_superior"] = mol_sup[14 - day]
            _insert_questionnaire(uid, _ts(day), ans, "Box",
                                  competition=False, weight=True, injury=True,
                                  rpe=_RPE[pattern], duration=_DUR[pattern])

    # ── ECG — 5 sesiones ──
    if force or _count_ecg(uid) < 5:
        if force:
            _wipe_ecg(uid)
        ecg_data = [
            (13, 56.0, 65.4, 49.8, 176, "ecg_marco_w1_lunes.csv"),
            (11, 60.0, 59.2, 44.1, 181, "ecg_marco_w1_miercoles.csv"),
            (9,  64.0, 54.7, 40.3, 188, "ecg_marco_w1_viernes.csv"),
            (6,  54.0, 70.3, 55.2, 172, "ecg_marco_w2_lunes.csv"),
            (4,  58.0, 63.1, 47.9, 178, "ecg_marco_w2_miercoles.csv"),
        ]
        for (d, bpm, sdnn, rmssd, peaks, fname) in ecg_data:
            _insert_ecg(uid, _ts(d), bpm, sdnn, rmssd, peaks, fname)

    # ── IMU (golpes) — 5 sesiones ──
    if force or _count_imu(uid) < 5:
        if force:
            _wipe_imu(uid)
        imu_data = [
            (13, 145, 44.2, 2.6, 7.3, "imu_marco_w1_lunes.csv"),
            (11, 168, 50.1, 2.9, 8.2, "imu_marco_w1_miercoles.csv"),
            (9,  152, 46.8, 2.7, 7.8, "imu_marco_w1_viernes.csv"),
            (6,  138, 42.0, 2.4, 7.0, "imu_marco_w2_lunes.csv"),
            (4,  160, 48.5, 2.8, 8.0, "imu_marco_w2_miercoles.csv"),
        ]
        for (d, hits, hpm, mg, maxg, fname) in imu_data:
            _insert_imu(uid, _ts(d), hits, hpm, mg, maxg, fname)

    # ── EMG (brazo) — 3 sesiones ──
    _wipe_emg(uid)
    for d, rms, peak, fat in [(13, 0.52, 2.31, 0.22), (9, 0.48, 2.18, 0.18), (4, 0.44, 2.05, 0.14)]:
        _insert_emg(uid, _ts(d), rms, peak, fat, f"emg_marco_brazo_{d}d.csv")

    # ── Peso (14 días) ──
    with db._get_conn() as con:
        if con.execute("SELECT COUNT(*) FROM weights WHERE user_id=?", (uid,)).fetchone()[0] < 14:
            weights_kg = [69.8,69.5,69.2,69.4,69.1,68.9,68.7,68.9,68.6,68.8,68.7,68.5,68.6,68.4]
            for i, w in enumerate(weights_kg):
                db.add_weight_entry(uid, (_dt.datetime.utcnow() - _dt.timedelta(days=14-i)).date().isoformat(),
                                    w, target_kg=69.0)

    # ── Nutrición (14 días) ──
    with db._get_conn() as con:
        if con.execute("SELECT COUNT(*) FROM nutrition_logs WHERE user_id=?", (uid,)).fetchone()[0] < 14:
            adhs = [80,83,78,85,88,90,82,76,80,85,88,86,89,91]
            kcals = [2700,2720,2680,2750,2800,2820,2700,2650,2700,2760,2810,2790,2830,2850]
            for i, (adh, kcal) in enumerate(zip(adhs, kcals)):
                db.add_nutrition_entry(uid,
                    (_dt.datetime.utcnow() - _dt.timedelta(days=14-i)).date().isoformat(),
                    adh, kcal)

    db.mark_onboarding_done(uid)
    print(f"  OK Marco Silva (Box atleta) — id={uid}")
    return uid


# ─────────────────────────────────────────────
# COACH TKD — Ana Morales (3 atletas TKD)
# ─────────────────────────────────────────────

def seed_coach_tkd(uid_carlos: int, force: bool = False) -> int:
    coach_id = _get_or_create_user(
        "Ana Morales", "ana.coach.tkd@demo.combatiq", "demo123", "coach", "Taekwondo"
    )

    # Atletas adicionales del equipo TKD
    _TKD_TEAM = [
        ("demo-ana-tkd@combatiq.app",    "Ana Torres",  "Taekwondo", "Competitivo",      "-57 kg",  "tobillo derecho",  "Próximas 3-4 semanas"),
        ("demo-sofia-tkd@combatiq.app",  "Sofía Mendez","Taekwondo", "Alto rendimiento", "-49 kg",  "rodilla derecha",  "Semana competitiva"),
    ]
    team_ids = [uid_carlos]
    for (email, name, sport, level, wcat, watch, comp) in _TKD_TEAM:
        aid = _get_or_create_user(name, email, "demo_no_auth", "deportista", sport)
        _set_profile(aid, level=level, weight_cat=wcat, dominant="Derecho",
                     status="Listo con control", watch=watch, comp=comp)
        db.mark_onboarding_done(aid)
        team_ids.append(aid)

    # Vincular todos al coach
    for aid in team_ids:
        _adopt(coach_id, aid)

    # Cuestionarios del equipo TKD
    for i, (aid, cycle, q_count, pattern_weights) in enumerate([
        (uid_carlos,   _TKD_CYCLE, 14, None),
        (team_ids[1],  _TKD_CYCLE, 10, {"fresh":4,"loaded":3,"peak":3,"recover":4}),  # Ana: buen estado
        (team_ids[2],  _TKD_CYCLE, 12, {"fresh":3,"loaded":3,"peak":2,"recover":3}),  # Sofía: carga alta
    ]):
        if force or _count_q(aid) < q_count:
            if force and aid != uid_carlos:
                _wipe_questionnaires(aid)
            count_needed = q_count - _count_q(aid)
            if count_needed <= 0:
                continue
            mod = {
                "fresh":   _tkd_answers("fresh"),
                "loaded":  _tkd_answers("loaded"),
                "peak":    _tkd_answers("peak"),
                "recover": _tkd_answers("recover"),
            }
            if pattern_weights:
                # Apply overrides
                for p, score_base in pattern_weights.items():
                    mod[p]["energia"] = score_base
                    mod[p]["recuperacion"] = score_base
                    mod[p]["listo_rendir"] = min(5, score_base)
            for day in range(count_needed, 0, -1):
                pattern = cycle[(count_needed - day) % 7]
                ans = mod[pattern].copy()
                _insert_questionnaire(aid, _ts(day + (i * 2)), ans, "Taekwondo",
                                      competition=(i == 2), weight=False, injury=False,
                                      rpe=_RPE[pattern], duration=_DUR[pattern])

    # ECG para los atletas nuevos del equipo
    for aid, d_offset in [(team_ids[1], 1), (team_ids[2], 2)]:
        if _count_ecg(aid) < 3:
            _wipe_ecg(aid)
            for (d, bpm, sdnn, rmssd, peaks) in [
                (12+d_offset, 55.0+d_offset, 66.0, 50.5, 177),
                (8+d_offset,  57.0+d_offset, 60.2, 45.8, 180),
                (4+d_offset,  53.0+d_offset, 71.0, 56.1, 174),
            ]:
                _insert_ecg(aid, _ts(d), bpm, sdnn, rmssd, peaks,
                            f"ecg_tkdteam_{aid}_{d}d.csv")

    # ── Coach check-in (cuestionario del coach como observación del equipo) ──
    if force or _count_q(coach_id) < 7:
        if force:
            _wipe_questionnaires(coach_id)
        coach_seeds = [
            (14, {"cq_energia_equipo":4,"cq_motivacion":4,"cq_cohesion":4,
                  "cq_carga_acumulada":2,"cq_molestias_equipo":1,
                  "cq_intensidad_sesion":3,"cq_complejidad_tecnica":3}, 68.0),
            (11, {"cq_energia_equipo":3,"cq_motivacion":4,"cq_cohesion":3,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":4}, 61.0),
            (9,  {"cq_energia_equipo":3,"cq_motivacion":3,"cq_cohesion":3,
                  "cq_carga_acumulada":4,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":3}, 55.0),
            (7,  {"cq_energia_equipo":4,"cq_motivacion":4,"cq_cohesion":4,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":1,
                  "cq_intensidad_sesion":3,"cq_complejidad_tecnica":3}, 64.0),
            (5,  {"cq_energia_equipo":3,"cq_motivacion":3,"cq_cohesion":4,
                  "cq_carga_acumulada":4,"cq_molestias_equipo":3,
                  "cq_intensidad_sesion":5,"cq_complejidad_tecnica":4}, 50.0),
            (3,  {"cq_energia_equipo":4,"cq_motivacion":4,"cq_cohesion":4,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":4}, 62.0),
            (1,  {"cq_energia_equipo":5,"cq_motivacion":5,"cq_cohesion":5,
                  "cq_carga_acumulada":2,"cq_molestias_equipo":1,
                  "cq_intensidad_sesion":3,"cq_complejidad_tecnica":3}, 74.0),
        ]
        with db._get_conn() as con:
            for (d, ans, w) in coach_seeds:
                con.execute(
                    "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                    "VALUES (?,?,?,?,?,?)",
                    (coach_id, _ts(d), json.dumps(ans), w, None, None),
                )

    db.mark_onboarding_done(coach_id)
    print(f"  OK Ana Morales (Coach TKD) — id={coach_id}, equipo={team_ids}")
    return coach_id


# ─────────────────────────────────────────────
# COACH BOX — Rafael Guzmán (3 atletas Box)
# ─────────────────────────────────────────────

def seed_coach_box(uid_marco: int, force: bool = False) -> int:
    coach_id = _get_or_create_user(
        "Rafael Guzmán", "rafael.coach.box@demo.combatiq", "demo123", "coach", "Box"
    )

    _BOX_TEAM = [
        ("demo-luis-box@combatiq.app",   "Luis Peña",   "Box", "Competitivo",      "-69 kg",  "hombro derecho",   "Próximas 3-4 semanas"),
        ("demo-sofia-box@combatiq.app",  "Sofía Vega",  "Box", "Alto rendimiento", "-60 kg",  "muñeca izquierda", "Próximas 1-2 semanas"),
    ]
    team_ids = [uid_marco]
    for (email, name, sport, level, wcat, watch, comp) in _BOX_TEAM:
        aid = _get_or_create_user(name, email, "demo_no_auth", "deportista", sport)
        _set_profile(aid, level=level, weight_cat=wcat, dominant="Derecho",
                     status="Listo con control", watch=watch, comp=comp)
        db.mark_onboarding_done(aid)
        team_ids.append(aid)

    for aid in team_ids:
        _adopt(coach_id, aid)

    # Cuestionarios box (con claves correctas)
    for i, (aid, q_count) in enumerate([
        (uid_marco,   14),
        (team_ids[1], 10),  # Luis: carga media-alta
        (team_ids[2], 12),  # Sofía: precompetitiva
    ]):
        if force or _count_q(aid) < q_count:
            if force and aid != uid_marco:
                _wipe_questionnaires(aid)
            count_needed = q_count - _count_q(aid)
            if count_needed <= 0:
                continue
            fatigue_boost = {0: 0, 1: 1, 2: 2}[i]
            for day in range(count_needed, 0, -1):
                pattern = _BOX_CYCLE[(count_needed - day) % 7]
                ans = _box_answers(pattern)
                if fatigue_boost:
                    ans["fatiga_general"] = min(5, ans["fatiga_general"] + fatigue_boost)
                    ans["box_molestia_superior"] = min(5, ans["box_molestia_superior"] + fatigue_boost)
                _insert_questionnaire(aid, _ts(day + (i * 2)), ans, "Box",
                                      competition=(i == 2), weight=False, injury=(i > 0),
                                      rpe=_RPE[pattern] + min(2, fatigue_boost),
                                      duration=_DUR[pattern])

    # ECG para atletas del equipo box
    for aid, d_offset in [(team_ids[1], 1), (team_ids[2], 2)]:
        if _count_ecg(aid) < 3:
            _wipe_ecg(aid)
            for (d, bpm, sdnn, rmssd, peaks) in [
                (12+d_offset, 58.0+d_offset, 62.0, 47.5, 179),
                (8+d_offset,  62.0+d_offset, 57.3, 43.2, 184),
                (4+d_offset,  55.0+d_offset, 68.9, 53.6, 175),
            ]:
                _insert_ecg(aid, _ts(d), bpm, sdnn, rmssd, peaks,
                            f"ecg_boxteam_{aid}_{d}d.csv")

    # IMU para el equipo box
    for aid, d_offset in [(team_ids[1], 1), (team_ids[2], 2)]:
        if _count_imu(aid) < 3:
            _wipe_imu(aid)
            for (d, hits, hpm, mg, maxg) in [
                (13+d_offset, 150+d_offset*5, 46.0+d_offset, 2.7, 7.5),
                (9+d_offset,  138+d_offset*3, 42.0+d_offset, 2.5, 7.1),
                (5+d_offset,  160+d_offset*4, 48.0+d_offset, 2.9, 8.0),
            ]:
                _insert_imu(aid, _ts(d), hits, hpm, mg, maxg,
                            f"imu_boxteam_{aid}_{d}d.csv")

    # ── Coach check-in de boxeo ──
    if force or _count_q(coach_id) < 7:
        if force:
            _wipe_questionnaires(coach_id)
        coach_seeds = [
            (14, {"cq_energia_equipo":4,"cq_motivacion":3,"cq_cohesion":4,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":3}, 60.0),
            (10, {"cq_energia_equipo":3,"cq_motivacion":4,"cq_cohesion":3,
                  "cq_carga_acumulada":4,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":5,"cq_complejidad_tecnica":4}, 53.0),
            (8,  {"cq_energia_equipo":3,"cq_motivacion":3,"cq_cohesion":3,
                  "cq_carga_acumulada":4,"cq_molestias_equipo":3,
                  "cq_intensidad_sesion":5,"cq_complejidad_tecnica":5}, 49.0),
            (6,  {"cq_energia_equipo":4,"cq_motivacion":4,"cq_cohesion":4,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":3}, 61.0),
            (4,  {"cq_energia_equipo":3,"cq_motivacion":3,"cq_cohesion":3,
                  "cq_carga_acumulada":5,"cq_molestias_equipo":3,
                  "cq_intensidad_sesion":5,"cq_complejidad_tecnica":4}, 46.0),
            (2,  {"cq_energia_equipo":4,"cq_motivacion":4,"cq_cohesion":4,
                  "cq_carga_acumulada":3,"cq_molestias_equipo":2,
                  "cq_intensidad_sesion":4,"cq_complejidad_tecnica":3}, 62.0),
            (1,  {"cq_energia_equipo":4,"cq_motivacion":5,"cq_cohesion":4,
                  "cq_carga_acumulada":2,"cq_molestias_equipo":1,
                  "cq_intensidad_sesion":3,"cq_complejidad_tecnica":3}, 70.0),
        ]
        with db._get_conn() as con:
            for (d, ans, w) in coach_seeds:
                con.execute(
                    "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                    "VALUES (?,?,?,?,?,?)",
                    (coach_id, _ts(d), json.dumps(ans), w, None, None),
                )

    db.mark_onboarding_done(coach_id)
    print(f"  OK Rafael Guzmán (Coach Box) — id={coach_id}, equipo={team_ids}")
    return coach_id


# ─────────────────────────────────────────────
# FIX: corrige claves erróneas en datos box ya sembrados
# ─────────────────────────────────────────────

def _fix_boxeo_answer_keys():
    """Reemplaza claves obsoletas (box_potencia, box_velocidad, box_guard, box_molestia_sup)
    por las claves reales del cuestionario."""
    _KEY_MAP = {
        "box_potencia":     "box_ritmo",
        "box_velocidad":    "box_rapidez",
        "box_guard":        "box_precision",
        "box_molestia_sup": "box_molestia_superior",
    }
    with db._get_conn() as con:
        rows = con.execute(
            "SELECT id, answers_json FROM questionnaires WHERE answers_json LIKE '%box_potencia%' "
            "OR answers_json LIKE '%box_velocidad%' OR answers_json LIKE '%box_guard%' "
            "OR answers_json LIKE '%box_molestia_sup%'"
        ).fetchall()
        for (qid, raw) in rows:
            try:
                ans = json.loads(raw)
                updated = False
                for old, new in _KEY_MAP.items():
                    if old in ans:
                        ans[new] = ans.pop(old)
                        updated = True
                if updated:
                    con.execute("UPDATE questionnaires SET answers_json=? WHERE id=?",
                                (json.dumps(ans), qid))
            except Exception:
                pass
    print("  OK Claves obsoletas de boxeo corregidas en questionnaires.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def _seed_competitions(uid_carlos: int, uid_marco: int, force: bool = False):
    """Añade/actualiza eventos de competencia, siempre con fecha futura."""
    today = _dt.date.today()

    def _has_comp(uid: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM competition_events WHERE user_id=?", (uid,)
            ).fetchone()[0] > 0

    def _has_future_comp(uid: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM competition_events WHERE user_id=? AND event_date >= ?",
                (uid, today.isoformat()),
            ).fetchone()[0] > 0

    def _refresh_comp_date(uid: int, new_date: str):
        with db._get_conn() as con:
            con.execute(
                "UPDATE competition_events SET event_date=? WHERE user_id=?",
                (new_date, uid),
            )

    if not _has_comp(uid_carlos) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM competition_events WHERE user_id=?", (uid_carlos,))
        comp_tkd = today + _dt.timedelta(days=21)
        db.add_competition_event(uid_carlos, "Torneo Regional TKD 2026",
                                  comp_tkd.isoformat(), sport="Taekwondo",
                                  target_weight=67.0, location="Guadalajara, MX",
                                  notes="Clasificatorio para nacionales. Meta: -68 kg, entrada limpia.")
        print(f"  OK Competencia TKD (Carlos) -> {comp_tkd}")

    if not _has_comp(uid_marco) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM competition_events WHERE user_id=?", (uid_marco,))
        comp_box = today + _dt.timedelta(days=45)
        db.add_competition_event(uid_marco, "Campeonato Estatal Boxeo 2026",
                                  comp_box.isoformat(), sport="Box",
                                  target_weight=69.0, location="Monterrey, MX",
                                  notes="Muneca estabilizada. Ajustar taper segun evolucion.")
        print(f"  OK Competencia Box (Marco) -> {comp_box}")

    # Si ya existen pero la fecha pasó, actualizarlas a futuro
    if not force:
        if not _has_future_comp(uid_carlos):
            new_tkd = (today + _dt.timedelta(days=21)).isoformat()
            _refresh_comp_date(uid_carlos, new_tkd)
            print(f"  REFRESH Competencia TKD (Carlos) -> {new_tkd}")
        if not _has_future_comp(uid_marco):
            new_box = (today + _dt.timedelta(days=45)).isoformat()
            _refresh_comp_date(uid_marco, new_box)
            print(f"  REFRESH Competencia Box (Marco) -> {new_box}")


def _seed_trophies(uid_carlos: int, uid_marco: int, force: bool = False):
    """Siembra trofeos historicos para que la sala de logros no este vacia."""
    def _has_trophies(uid: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM competition_results WHERE user_id=?", (uid,)
            ).fetchone()[0] > 0

    if not _has_trophies(uid_carlos) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM competition_results WHERE user_id=?", (uid_carlos,))
        db.add_competition_result(uid_carlos, "Torneo Estatal Taekwondo 2025", "2025-11-15",
                                  medal="gold", category="-68 kg Senior", location="Guadalajara, MX",
                                  notes="Final reñida. Tres combates ganados por puntos.")
        db.add_competition_result(uid_carlos, "Open Nacional TKD 2025", "2025-08-02",
                                  medal="bronze", category="-68 kg Senior", location="CDMX",
                                  notes="Semifinal perdida por lesion de tobillo en ultimo round.")
        db.add_competition_result(uid_carlos, "Copa Regional TKD 2024", "2024-05-20",
                                  medal="silver", category="-68 kg Junior", location="Monterrey, MX")
        print(f"  OK Trofeos TKD (Carlos): Oro, Bronce, Plata")

    if not _has_trophies(uid_marco) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM competition_results WHERE user_id=?", (uid_marco,))
        db.add_competition_result(uid_marco, "Campeonato Estatal Boxeo 2025", "2025-10-08",
                                  medal="silver", category="-69 kg Elite", location="Monterrey, MX",
                                  notes="Final perdida en decision dividida. Buen torneo.")
        db.add_competition_result(uid_marco, "Torneo Invitacional Box 2025", "2025-06-14",
                                  medal="gold", category="-69 kg", location="Guadalajara, MX")
        db.add_competition_result(uid_marco, "Copa Zona Norte Box 2024", "2024-09-21",
                                  medal="bronze", category="-69 kg", location="Monterrey, MX")
        print(f"  OK Trofeos Box (Marco): Plata, Oro, Bronce")


def _seed_mock_hardware(uid_carlos: int, uid_marco: int, force: bool = False):
    """Registra dispositivos simulados Polar H10 + IMU para los atletas demo."""
    import datetime as _dt2

    def _has_device(uid: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM sensor_devices WHERE user_id=?", (uid,)
            ).fetchone()[0] > 0

    recent = (_dt2.datetime.utcnow() - _dt2.timedelta(minutes=8)).isoformat()

    if not _has_device(uid_carlos) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM sensor_devices WHERE user_id=?", (uid_carlos,))
        # Polar H10 (ECG) — conectado hace 8 min
        dev_id = db.register_device(uid_carlos, "ECG", "AA:BB:CC:11:22:33",
                                    device_label="Polar H10 — Carlos", firmware_version="3.1.1")
        with db._get_conn() as con:
            con.execute("UPDATE sensor_devices SET status='connected', last_seen=? WHERE id=?",
                        (recent, dev_id))
        # IMU tobillo (TKD)
        dev_id2 = db.register_device(uid_carlos, "IMU_FOOT", "AA:BB:CC:44:55:66",
                                     device_label="IMU Tobillo — Carlos", firmware_version="1.4.0")
        with db._get_conn() as con:
            con.execute("UPDATE sensor_devices SET status='connected', last_seen=? WHERE id=?",
                        (recent, dev_id2))
        db.set_user_sensors(uid_carlos, ["ECG", "IMU_FOOT"])
        print(f"  OK Hardware mock (Carlos TKD): Polar H10 + IMU Tobillo — connected")

    if not _has_device(uid_marco) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM sensor_devices WHERE user_id=?", (uid_marco,))
        # Polar H10 (ECG)
        dev_id = db.register_device(uid_marco, "ECG", "DD:EE:FF:11:22:33",
                                    device_label="Polar H10 — Marco", firmware_version="3.1.1")
        with db._get_conn() as con:
            con.execute("UPDATE sensor_devices SET status='connected', last_seen=? WHERE id=?",
                        (recent, dev_id))
        # IMU muñeca (Boxeo)
        dev_id2 = db.register_device(uid_marco, "IMU_WRIST", "DD:EE:FF:44:55:66",
                                     device_label="IMU Muneca — Marco", firmware_version="1.4.0")
        with db._get_conn() as con:
            con.execute("UPDATE sensor_devices SET status='idle', last_seen=? WHERE id=?",
                        (recent, dev_id2))
        db.set_user_sensors(uid_marco, ["ECG", "IMU_WRIST"])
        print(f"  OK Hardware mock (Marco Box): Polar H10 connected + IMU Muneca idle")

    # Siempre refrescar last_seen para que aparezcan conectados en la demo
    if not force:
        _now = _dt2.datetime.utcnow()
        _conn = (_now - _dt2.timedelta(minutes=8)).isoformat()
        _idle = (_now - _dt2.timedelta(minutes=15)).isoformat()
        with db._get_conn() as con:
            con.execute(
                "UPDATE sensor_devices SET last_seen=?, status='connected' WHERE user_id=?",
                (_conn, uid_carlos),
            )
            con.execute(
                "UPDATE sensor_devices SET last_seen=?, status='connected' WHERE user_id=? AND sensor_code='ECG'",
                (_conn, uid_marco),
            )
            con.execute(
                "UPDATE sensor_devices SET last_seen=?, status='idle' WHERE user_id=? AND sensor_code='IMU_WRIST'",
                (_idle, uid_marco),
            )
        print(f"  REFRESH last_seen: Carlos connected, Marco ECG connected / IMU idle")


def _seed_announcements(coach_tkd_id: int, coach_box_id: int, force: bool = False):
    """Añade anuncios de los coaches si no existen."""
    def _has_ann(coach_id: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM announcements WHERE coach_id=?", (coach_id,)
            ).fetchone()[0] > 0

    if not _has_ann(coach_tkd_id) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM announcements WHERE coach_id=?", (coach_tkd_id,))
        db.add_announcement(coach_tkd_id, "Taekwondo",
                            "Semana de taper — reducción de volumen",
                            "Esta semana bajamos el volumen un 30% y mantenemos intensidad alta en los últimos 10 min de cada sesión. Prioridad: entrada limpia y pierna de apoyo.",
                            pinned=True)
        db.add_announcement(coach_tkd_id, "Taekwondo",
                            "Control de peso — protocolo de hidratación",
                            "Revisamos peso cada mañana en ayuno. Si estás por encima de -68 kg ajustamos plan de hidratación. Cualquier duda, escríbeme antes de entrenar.")
        print(f"  OK Anuncios TKD (coach_id={coach_tkd_id})")

    if not _has_ann(coach_box_id) or force:
        with db._get_conn() as con:
            con.execute("DELETE FROM announcements WHERE coach_id=?", (coach_box_id,))
        db.add_announcement(coach_box_id, "Box",
                            "Protocolo muñeca — sin saco con mano izquierda",
                            "Hasta el martes próximo: nada de trabajo de saco con mano izquierda. Enfoque en técnica de esquiva y defensa de guardia derecha.",
                            pinned=True)
        db.add_announcement(coach_box_id, "Box",
                            "Sparring ligero este jueves",
                            "Sesión de sparring técnico el jueves. Intensidad máxima 6/10. Avísame si alguien no puede llegar.")
        print(f"  OK Anuncios Box (coach_id={coach_box_id})")


def _seed_chat(uid_carlos: int, coach_tkd_id: int,
               uid_marco: int, coach_box_id: int,
               force: bool = False):
    """Siembra conversaciones realistas coach-atleta para la demo."""
    # ── TKD: Ana <-> Carlos ───────────────────────────────────────────────────
    if force or not _has_messages(uid_carlos, coach_tkd_id):
        if force:
            with db._get_conn() as con:
                con.execute(
                    "DELETE FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)",
                    (uid_carlos, coach_tkd_id, coach_tkd_id, uid_carlos),
                )
        _insert_message(coach_tkd_id, uid_carlos,
            "Carlos, tu HRV de hoy bajo un 12% vs. tu baseline. Como te sientes? "
            "Considera bajar la intensidad de la sesion tarde.", days_ago=5)
        _insert_message(uid_carlos, coach_tkd_id,
            "Dormi mal anoche, molestia leve en la rodilla izquierda tambien. "
            "Bajamos la sesion?", days_ago=4.9)
        _insert_message(coach_tkd_id, uid_carlos,
            "Exacto. Hoy solo tecnica sin impacto y 20 min de recuperacion activa. "
            "El martes revisamos si podemos retomar sparring.", days_ago=4.8)
        _insert_message(uid_carlos, coach_tkd_id,
            "Perfecto. Ya hice el check-in, bienestar en 74. Rodilla bastante mejor.", days_ago=3)
        _insert_message(coach_tkd_id, uid_carlos,
            "Excelente racha de check-ins esta semana. Tu ACWR esta en zona optima. "
            "Seguimos con el plan de taper.", days_ago=1)
        print(f"  OK Chat TKD (Ana <-> Carlos): 5 mensajes")

    # ── Box: Rafael <-> Marco ─────────────────────────────────────────────────
    if force or not _has_messages(uid_marco, coach_box_id):
        if force:
            with db._get_conn() as con:
                con.execute(
                    "DELETE FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)",
                    (uid_marco, coach_box_id, coach_box_id, uid_marco),
                )
        _insert_message(coach_box_id, uid_marco,
            "Marco, recuerda: nada de trabajo de saco con mano izquierda hasta el martes. "
            "¿Cómo va la muñeca?", days_ago=6)
        _insert_message(uid_marco, coach_box_id,
            "Va mejorando, coach. Hice el check-in, molestia bajó a 1/5. "
            "¿Puedo hacer sombra hoy?", days_ago=5.9)
        _insert_message(coach_box_id, uid_marco,
            "Sombra sí, sin guantes. Y sube tus métricas IMU de la semana, "
            "quiero ver el volumen de golpes antes de decidir.", days_ago=5.8)
        _insert_message(uid_marco, coach_box_id,
            "Las subí. 160 golpes en la última sesión, intensidad 2.8g media. "
            "Muñeca al 100%.", days_ago=2)
        _insert_message(coach_box_id, uid_marco,
            "Bien. Jueves sparring técnico, intensidad 6/10 máximo. "
            "Avísame si algo cambia.", days_ago=1)
        print(f"  OK Chat Box (Rafael <-> Marco): 5 mensajes")


def _seed_combat_sessions(uid_carlos: int, uid_marco: int, force: bool = False):
    """Siembra sesiones de Combat Monitor con ECG CSV + IMU JSON sidecar realistas."""
    import csv   as _csv
    import math  as _math

    ECG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ecg")
    os.makedirs(ECG_DIR, exist_ok=True)

    def _has_combat(uid: int) -> bool:
        sessions = db.list_sessions(uid, limit=20) or []
        return any((s.get("notes") or "").startswith("Combat Monitor") for s in sessions)

    def _pqrst(t_beat: float) -> float:
        """Aproximación PQRST en [0,1] → voltaje relativo."""
        y = 0.0
        if 0.08 < t_beat < 0.22:
            y += 0.08 * _math.sin(_math.pi * (t_beat - 0.08) / 0.14)
        if 0.28 < t_beat < 0.30:
            y -= 0.04 * _math.sin(_math.pi * (t_beat - 0.28) / 0.02)
        if 0.30 < t_beat < 0.36:
            y += 0.80 * _math.sin(_math.pi * (t_beat - 0.30) / 0.06)
        if 0.36 < t_beat < 0.40:
            y -= 0.12 * _math.sin(_math.pi * (t_beat - 0.36) / 0.04)
        if 0.50 < t_beat < 0.70:
            y += 0.15 * _math.sin(_math.pi * (t_beat - 0.50) / 0.20)
        return y

    # (uid, deporte, rounds, round_min, label, bpm_fight, bpm_rest)
    ATHLETE_CFG = [
        (uid_carlos, "Taekwondo", 3, 2, "Taekwondo (WT)",      168, 72),
        (uid_marco,  "Box",       3, 3, "Box Elite Amateur",   174, 68),
    ]

    # Dos sesiones por atleta: una reciente, otra hace ~8 días
    SESSION_OFFSETS = [
        {"days_ago": 1.5, "dado": 18, "recibido": 11, "bpm_delta":  +6},
        {"days_ago": 8.0, "dado": 22, "recibido": 15, "bpm_delta":  -3},
    ]

    for (uid, sport, rounds, round_min, label, bpm_fight, bpm_rest) in ATHLETE_CFG:
        if not force and _has_combat(uid):
            print(f"  SKIP sesiones Combat Monitor (uid={uid}) — ya existen")
            continue
        if force:
            sessions = db.list_sessions(uid, limit=50) or []
            for s in sessions:
                if (s.get("notes") or "").startswith("Combat Monitor"):
                    try:
                        db.delete_session(s["id"])
                    except Exception:
                        pass

        rng = random.Random(uid * 999 + rounds)

        for cfg in SESSION_OFFSETS:
            days_ago   = cfg["days_ago"]
            n_dado     = cfg["dado"]
            n_recibido = cfg["recibido"]
            peak_bpm   = bpm_fight + cfg["bpm_delta"]
            n_impacts  = n_dado + n_recibido

            ts_start = (
                _dt.datetime.utcnow() - _dt.timedelta(days=days_ago)
            ).replace(hour=rng.randint(9, 18), minute=rng.randint(0, 59),
                      second=0, microsecond=0).isoformat()
            notes = (
                f"Combat Monitor — {label} · "
                f"{rounds} rounds · "
                f"Peak BPM {peak_bpm:.0f} · "
                f"{n_impacts} impactos"
            )

            sid = db.create_session(uid, created_by=uid, sport=sport, notes=notes)
            with db._get_conn() as con:
                con.execute(
                    "UPDATE sessions SET ts_start=?, status='closed' WHERE id=?",
                    (ts_start, sid),
                )

            # ── ECG CSV ────────────────────────────────────────────────────
            ts_tag    = (
                _dt.datetime.utcnow() - _dt.timedelta(days=days_ago)
            ).strftime("%Y%m%d_%H%M%S")
            ecg_fname = f"combat_{uid}_{ts_tag}.csv"
            ecg_path  = os.path.join(ECG_DIR, ecg_fname)

            # Combate: rounds × round_min + (rounds-1) descansos de 60s
            total_s   = rounds * round_min * 60 + (rounds - 1) * 60
            FS        = 4          # Hz — igual que Combat Monitor
            n_samples = int(total_s * FS)

            with open(ecg_path, "w", newline="") as fh:
                fh.write("time,ecg\n")
                for i in range(n_samples):
                    t          = i / FS
                    round_cycle = round_min * 60 + 60
                    is_fight   = (t % round_cycle) < (round_min * 60)
                    bpm        = (bpm_fight if is_fight else bpm_rest) + rng.gauss(0, 3)
                    bpm        = max(40, bpm)
                    beat_len   = 60.0 / bpm
                    t_in_beat  = (t % beat_len) / beat_len
                    y          = _pqrst(t_in_beat) + rng.gauss(0, 0.012)
                    fh.write(f"{round(t, 3)},{round(y, 4)}\n")

            fid = db.add_ecg_file(uid, ecg_fname, fs=FS, session_id=sid)
            db.save_ecg_metrics(fid, float(peak_bpm) * 0.88, 44.0, 36.0, n_samples // 2)

            # ── IMU JSON sidecar ────────────────────────────────────────────
            imu_stem  = f"combat_{uid}_{ts_tag}_imu"
            imu_path  = os.path.join(ECG_DIR, f"{imu_stem}.json")
            events    = []

            for r in range(1, rounds + 1):
                r_start = (r - 1) * (round_min * 60 + 60)
                r_end   = r_start + round_min * 60 - 5

                # Golpes dados
                per_r_dado = n_dado // rounds + (1 if r <= n_dado % rounds else 0)
                for _ in range(per_r_dado):
                    events.append({
                        "t":         round(rng.uniform(r_start + 5, r_end), 2),
                        "intensity": round(rng.uniform(2.2, 4.6), 2),
                        "type":      "dado",
                        "round":     r,
                    })

                # Golpes recibidos
                per_r_rec = n_recibido // rounds + (1 if r <= n_recibido % rounds else 0)
                for _ in range(per_r_rec):
                    events.append({
                        "t":         round(rng.uniform(r_start + 5, r_end), 2),
                        "intensity": round(rng.uniform(1.4, 3.0), 2),
                        "type":      "recibido",
                        "round":     r,
                    })

                # Ruido de fondo (cada 2s)
                for t_n in range(int(r_start), int(r_end), 2):
                    events.append({
                        "t":         round(t_n + rng.random() * 0.5, 2),
                        "intensity": round(rng.uniform(0.3, 0.9), 2),
                        "type":      "ruido",
                        "round":     r,
                    })

            events.sort(key=lambda e: e["t"])
            with open(imu_path, "w") as fh:
                json.dump(events, fh)

            hits_only  = [e for e in events if e["type"] in ("dado", "recibido")]
            intensities = [e["intensity"] for e in hits_only]
            n_hits     = len(hits_only)
            hpm        = round(n_hits / (total_s / 60), 1) if total_s > 0 else 0.0
            mean_g     = round(sum(intensities) / len(intensities), 2) if intensities else 0.0
            max_g      = round(max(intensities), 2) if intensities else 0.0
            db.save_imu_metrics(uid, imu_stem, n_hits, hpm, mean_g, max_g, session_id=sid)

            print(
                f"  OK Session #{sid} ({label}) uid={uid} "
                f"— {n_impacts} golpes, {n_samples} pts ECG, {len(events)} eventos IMU"
            )


def _seed_sensor_sessions(uid_carlos: int, uid_marco: int, force: bool = False):
    """Siembra sensor_sessions completadas para las sesiones Combat Monitor demo."""
    rng = random.Random(2026)

    # Sensores asignados a cada atleta con conteos realistas de paquetes
    _CFG = {
        uid_carlos: [("ECG", (40, 55)), ("IMU_FOOT",  (70, 120))],
        uid_marco:  [("ECG", (45, 60)), ("IMU_WRIST", (80, 130))],
    }

    def _has_ss(session_id: int) -> bool:
        with db._get_conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM sensor_sessions WHERE session_id=?", (session_id,)
            ).fetchone()[0] > 0

    for uid, sensors in _CFG.items():
        sessions = db.list_sessions(uid, limit=20) or []
        combat = [s for s in sessions if (s.get("notes") or "").startswith("Combat Monitor")]
        for s in combat:
            sid = s["id"]
            if _has_ss(sid) and not force:
                continue
            if force:
                with db._get_conn() as con:
                    con.execute("DELETE FROM sensor_sessions WHERE session_id=?", (sid,))

            ts_base_str = (s.get("ts_start") or "").rstrip("Z")
            try:
                ts_base = _dt.datetime.fromisoformat(ts_base_str)
            except (ValueError, TypeError):
                ts_base = _dt.datetime.utcnow() - _dt.timedelta(days=2)

            for sensor_code, (lo, hi) in sensors:
                sample_count = rng.randint(lo, hi)
                duration_min = rng.randint(18, 28)
                ts_start = ts_base.isoformat()
                ts_end   = (ts_base + _dt.timedelta(minutes=duration_min)).isoformat()
                ss_id = db.open_sensor_session(sid, sensor_code, ts_start=ts_start)
                with db._get_conn() as con:
                    con.execute(
                        "UPDATE sensor_sessions SET ts_end=?, status='complete', sample_count=? WHERE id=?",
                        (ts_end, sample_count, ss_id),
                    )
        if combat:
            print(f"  OK sensor_sessions uid={uid}: {len(combat)} sesiones × {len(sensors)} sensores")


def main(force: bool = False):
    print("\n=== CombatIQ — Seed de datos demo ===\n")

    # Corregir datos box existentes antes de sembrar nuevos
    _fix_boxeo_answer_keys()

    print("Sembrando atletas...")
    uid_carlos = seed_carlos_tkd(force=force)
    uid_marco  = seed_marco_box(force=force)

    print("\nSembrando coaches y equipos...")
    coach_tkd_id = seed_coach_tkd(uid_carlos, force=force)
    coach_box_id = seed_coach_box(uid_marco,  force=force)

    print("\nSembrando competencias, anuncios, hardware mock, trofeos y chat...")
    _seed_competitions(uid_carlos, uid_marco, force=force)
    _seed_announcements(coach_tkd_id, coach_box_id, force=force)
    _seed_mock_hardware(uid_carlos, uid_marco, force=force)
    _seed_trophies(uid_carlos, uid_marco, force=force)
    _seed_chat(uid_carlos, coach_tkd_id, uid_marco, coach_box_id, force=force)

    print("\nSembrando sesiones de Combat Monitor...")
    _seed_combat_sessions(uid_carlos, uid_marco, force=force)

    print("\nSembrando sensor_sessions...")
    _seed_sensor_sessions(uid_carlos, uid_marco, force=force)

    # Admin demo (idempotente)
    _admin = db.get_user_by_email("demo-admin@combatiq.app")
    if not _admin:
        _admin_id = db.create_user(
            name="Demo Admin",
            email="demo-admin@combatiq.app",
            pw="demo123",
            role="admin",
            sport="",
        )
        db.mark_onboarding_done(_admin_id)
        print(f"  OK Admin demo — id={_admin_id}")
    else:
        print(f"  OK Admin demo ya existe — id={_admin['id']}")

    print("\n=== Seed completado ===")
    print("\nCuentas de acceso:")
    print("  TKD Atleta   : carlos.tkd@demo.combatiq      / demo123")
    print("  Box Atleta   : marco.box@demo.combatiq       / demo123")
    print("  Coach TKD    : ana.coach.tkd@demo.combatiq   / demo123")
    print("  Coach Box    : rafael.coach.box@demo.combatiq / demo123")
    print("  Admin demo   : demo-admin@combatiq.app       / demo123")


if __name__ == "__main__":
    force = "--force" in sys.argv
    if force:
        print("WARN: Modo --force: borrando y resembrando todos los datos demo.")
    main(force=force)
