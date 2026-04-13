import os, json, sqlite3, hashlib
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.join("data", "users.db")


# ======================
# Conexión / utilidades
# ======================

def _conn():
    """
    Devuelve una conexión SQLite con:
    - carpeta data/ creada si no existe
    - timeout ampliado para reducir "database is locked"
    - check_same_thread=False para uso con Dash
    - PRAGMAs para mejorar concurrencia (WAL) en despliegue web
    """
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)

    try:
        con.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass

    # Concurrencia más estable (recomendado en apps web con SQLite)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout = 5000;")  # 5s
    except Exception:
        pass

    return con


@contextmanager
def _get_conn():
    """
    Context manager que garantiza:
    - commit automático si todo va bien
    - cierre de la conexión SIEMPRE (también si hay excepción)
    """
    con = _conn()
    try:
        yield con
        con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass


def _dicts(cur):
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        yield {k: v for k, v in zip(cols, row)}


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cur = con.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]  # (cid, name, type, notnull, dflt_value, pk)
        return column in cols
    except Exception:
        return False


# ======================
# Migraciones versionadas
# ======================

def _ensure_schema_migrations(con: sqlite3.Connection):
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY,
            applied_at TEXT
        )"""
    )


def _get_db_version(con: sqlite3.Connection) -> int:
    try:
        cur = con.cursor()
        cur.execute("SELECT MAX(version) FROM schema_migrations")
        v = cur.fetchone()[0]
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _set_db_version(con: sqlite3.Connection, version: int):
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
        (int(version), datetime.utcnow().isoformat()),
    )


def migrate_db():
    """
    Ejecuta migraciones incrementales para poder actualizar en producción sin romper la DB.

    Nota: el proyecto ya tenía 'ALTER TABLE ...' sueltos. Aquí lo volvemos más robusto:
    - Cada migración se ejecuta una sola vez.
    - Aun así, cada paso chequea columnas antes de hacer ALTER.
    """
    with _get_conn() as con:
        _ensure_schema_migrations(con)
        current = _get_db_version(con)

        # --------------------------
        # Migración 10: columnas legacy
        # --------------------------
        if current < 10:
            cur = con.cursor()
            # users.coach_id (legacy)
            if not _has_column(con, "users", "coach_id"):
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN coach_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # ecg_files.created_at (ya existía como migración suave)
            if not _has_column(con, "ecg_files", "created_at"):
                try:
                    cur.execute("ALTER TABLE ecg_files ADD COLUMN created_at TEXT")
                except sqlite3.OperationalError:
                    pass

            _set_db_version(con, 10)

        # --------------------------
        # Migración 20: sesiones + session_id en tablas existentes
        # --------------------------
        if current < 20:
            cur = con.cursor()

            # Tabla sessions (nueva)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    created_by INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    sport TEXT,
                    notes TEXT,
                    status TEXT,
                    created_at TEXT,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
                )"""
            )

            # session_id en ecg_files
            if not _has_column(con, "ecg_files", "session_id"):
                try:
                    cur.execute("ALTER TABLE ecg_files ADD COLUMN session_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # session_id en questionnaires
            if not _has_column(con, "questionnaires", "session_id"):
                try:
                    cur.execute("ALTER TABLE questionnaires ADD COLUMN session_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # session_id en métricas IMU/EMG/RESP (recomendado para informes por sesión)
            for table in ("imu_metrics", "emg_metrics", "resp_metrics"):
                if not _has_column(con, table, "session_id"):
                    try:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN session_id INTEGER")
                    except sqlite3.OperationalError:
                        pass

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_athlete ON sessions(athlete_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_ecg_files_user ON ecg_files(user_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_questionnaires_user ON questionnaires(user_id)")
            except Exception:
                pass

            _set_db_version(con, 20)

        # --------------------------
        # Migración 30: adopción + equipos
        # --------------------------
        if current < 30:
            cur = con.cursor()

            # Coach adopta deportistas (roster)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS coach_athletes(
                    coach_id INTEGER NOT NULL,
                    athlete_id INTEGER NOT NULL,
                    created_at TEXT,
                    PRIMARY KEY (coach_id, athlete_id),
                    FOREIGN KEY (coach_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # Equipos
            cur.execute(
                """CREATE TABLE IF NOT EXISTS teams(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coach_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sport TEXT,
                    created_at TEXT,
                    FOREIGN KEY (coach_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS team_members(
                    team_id INTEGER NOT NULL,
                    athlete_id INTEGER NOT NULL,
                    role_label TEXT,
                    created_at TEXT,
                    PRIMARY KEY (team_id, athlete_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_coach_athletes_coach ON coach_athletes(coach_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id)")
            except Exception:
                pass

            _set_db_version(con, 30)

        # --------------------------
        # Migración 40: peso + nutrición (persistencia)
        # --------------------------
        if current < 40:
            cur = con.cursor()

            # Registros de peso (por usuario)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS weights(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    target_kg REAL,
                    note TEXT,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # Registros de nutrición (por usuario)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS nutrition_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    adherence_pct REAL NOT NULL,
                    kcal REAL,
                    note TEXT,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_weights_user_date ON weights(user_id, date)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_user_date ON nutrition_logs(user_id, date)")
            except Exception:
                pass

            _set_db_version(con, 40)



        # --------------------------
        # Migración 50: perfil deportivo extendido (JSON)
        # --------------------------
        if current < 50:
            cur = con.cursor()
            if not _has_column(con, "users", "athlete_profile_json"):
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN athlete_profile_json TEXT")
                except sqlite3.OperationalError:
                    pass
            _set_db_version(con, 50)

# ======================
# Inicialización DB
# ======================

def init_db():
    """
    Inicializa la base de datos con las tablas necesarias.
    No borra nada si ya existen: solo crea lo que falta.

    Importante:
    - Mantiene compatibilidad con el código actual.
    - Además ejecuta migrate_db() para nuevas features (sesiones/equipos/adopción).
    """
    with _get_conn() as con:
        cur = con.cursor()

        # ---------- Usuarios ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            role TEXT,
            sport TEXT,
            password_hash BLOB,
            created_at TEXT
        )"""
        )
        # Aseguramos que exista la columna coach_id (relación coach -> deportistas) (legacy)
        if not _has_column(con, "users", "coach_id"):
            try:
                cur.execute("ALTER TABLE users ADD COLUMN coach_id INTEGER")
            except sqlite3.OperationalError:
                pass
        if not _has_column(con, "users", "athlete_profile_json"):
            try:
                cur.execute("ALTER TABLE users ADD COLUMN athlete_profile_json TEXT")
            except sqlite3.OperationalError:
                pass

        # ---------- Sensores por usuario ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS user_sensors(
            user_id INTEGER,
            sensor_code TEXT,
            PRIMARY KEY(user_id, sensor_code)
        )"""
        )

        # ---------- Archivos ECG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ecg_files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            fs INTEGER
        )"""
        )
        # Migración suave: añadir created_at si no existe
        if not _has_column(con, "ecg_files", "created_at"):
            try:
                cur.execute("ALTER TABLE ecg_files ADD COLUMN created_at TEXT")
            except sqlite3.OperationalError:
                pass

        # ---------- Métricas ECG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ecg_metrics(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ecg_file_id INTEGER NOT NULL,
            bpm REAL,
            sdnn REAL,
            rmssd REAL,
            n_peaks INTEGER,
            created_at TEXT
        )"""
        )

        # ---------- Cuestionarios ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS questionnaires(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ts TEXT,
            answers_json TEXT,
            wellness_score REAL,
            rpe REAL,
            duration_min REAL
        )"""
        )

        # ---------- Métricas IMU (golpes) ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS imu_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            n_hits INTEGER,
            hits_per_min REAL,
            mean_int_g REAL,
            max_int_g REAL
        )"""
        )

        # ---------- Métricas EMG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS emg_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            rms REAL,
            peak REAL,
            fatigue REAL
        )"""
        )

        # ---------- Métricas banda respiratoria ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS resp_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            n_breaths INTEGER,
            br_min REAL,
            mean_period REAL
        )"""
        )

    # Ejecuta migraciones nuevas (idempotente y versionado)
    try:
        migrate_db()
    except Exception:
        # No matamos la app si una migración falla; pero en dev lo verás en consola.
        pass


# ======================
# Users / Auth
# ======================

def _hash_pw(pw: str):
    """
    Hash de password. Intenta usar bcrypt y si no, SHA256 como fallback.
    """
    try:
        import bcrypt
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    except Exception:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8")


def _check_pw(pw: str, hashed: bytes):
    try:
        import bcrypt
        return bcrypt.checkpw(pw.encode("utf-8"), hashed)
    except Exception:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8") == hashed


def create_user(name, email, pw, role, sport, coach_id=None):
    """
    Crea usuario completo con email y password hasheado.
    Pensado para login real (deportistas y coaches).
    """
    with _get_conn() as con:
        cur = con.cursor()
        hashed = _hash_pw(pw)
        if coach_id is None:
            cur.execute(
                "INSERT INTO users(name,email,role,sport,password_hash,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (name, email, role, sport, hashed, datetime.utcnow().isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO users(name,email,role,sport,password_hash,created_at,coach_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (name, email, role, sport, hashed, datetime.utcnow().isoformat(), coach_id),
            )
        return cur.lastrowid


def get_user_by_email(email: str):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id,athlete_profile_json "
            "FROM users WHERE email=?",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id", "athlete_profile_json"]
    return {k: v for k, v in zip(cols, row)}


def get_user_by_id(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id,athlete_profile_json "
            "FROM users WHERE id=?",
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id", "athlete_profile_json"]
    return {k: v for k, v in zip(cols, row)}


def list_users():
    """
    Lista TODOS los usuarios (coaches y deportistas).
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT id,name,role,sport,created_at,coach_id FROM users ORDER BY id DESC")
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "sport": r[3],
                "created_at": r[4],
                "coach_id": r[5],
            }
        )
    return out


def list_coaches():
    """
    Lista sólo usuarios con rol 'coach'.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,created_at "
            "FROM users WHERE role='coach' ORDER BY id DESC"
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "email": r[2],
                "role": r[3],
                "sport": r[4],
                "created_at": r[5],
            }
        )
    return out


def list_athletes_for_coach(coach_id: int):
    """
    LEGACY: Lista deportistas asignados a un coach concreto vía users.coach_id.
    (Se mantiene para no romper la app mientras migras a adopción/equipos.)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,role,sport,created_at,coach_id "
            "FROM users WHERE role='deportista' AND coach_id=? ORDER BY id DESC",
            (coach_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "sport": r[3],
                "created_at": r[4],
                "coach_id": r[5],
            }
        )
    return out


def get_user_coach(user_id: int):
    """
    Devuelve los datos del coach asociado a un deportista (o None si no tiene).
    (Legacy, depende de users.coach_id)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """SELECT c.id, c.name, c.email, c.role, c.sport, c.created_at
               FROM users u
               JOIN users c ON u.coach_id = c.id
               WHERE u.id=?""",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "created_at"]
    return {k: v for k, v in zip(cols, row)}


def add_user(name, sport=None, role="deportista", coach_id=None):
    """
    LEGACY: alta rápida de usuario SIN email ni password.
    Se mantiene por compatibilidad con código antiguo.
    """
    with _get_conn() as con:
        cur = con.cursor()
        if coach_id is None:
            cur.execute(
                "INSERT INTO users(name,role,sport,created_at) VALUES(?,?,?,?)",
                (name, role or "deportista", sport, datetime.utcnow().isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO users(name,role,sport,created_at,coach_id) VALUES(?,?,?,?,?)",
                (name, role or "deportista", sport, datetime.utcnow().isoformat(), coach_id),
            )


def coach_create_athlete_with_login(coach_id: int, name: str, email: str, pw: str, sport: str):
    """
    LEGACY: Utilidad para panel del coach (crea deportista con login y lo asigna por coach_id).
    Se mantiene por compatibilidad, pero se recomienda migrar a adopción.
    """
    return create_user(
        name=name,
        email=email,
        pw=pw,
        role="deportista",
        sport=sport,
        coach_id=coach_id,
    )


def delete_user(uid: int):
    """
    Borra usuario y todo lo asociado (sensores, ECG, métricas, cuestionarios).
    """
    with _get_conn() as con:
        cur = con.cursor()

        cur.execute(
            "DELETE FROM ecg_metrics WHERE ecg_file_id IN "
            "(SELECT id FROM ecg_files WHERE user_id=?)",
            (uid,),
        )
        cur.execute("DELETE FROM ecg_files WHERE user_id=?", (uid,))

        cur.execute("DELETE FROM imu_metrics WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM emg_metrics WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM resp_metrics WHERE user_id=?", (uid,))

        cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM questionnaires WHERE user_id=?", (uid,))

        # sesiones (si existen)
        try:
            cur.execute("DELETE FROM sessions WHERE athlete_id=?", (uid,))
        except Exception:
            pass

        # peso/nutrición (si existen; también están en FK CASCADE)
        try:
            cur.execute("DELETE FROM weights WHERE user_id=?", (uid,))
        except Exception:
            pass
        try:
            cur.execute("DELETE FROM nutrition_logs WHERE user_id=?", (uid,))
        except Exception:
            pass



        cur.execute("DELETE FROM users WHERE id=?", (uid,))


def delete_user_as_coach(coach_id: int, uid: int):
    """
    BORRADO SEGURO para coaches:
    - Solo permite borrar si el usuario existe
    - y si es deportista
    - y si su coach_id coincide con el coach que lo solicita.
    Devuelve True si borró, False si no estaba permitido.
    """
    u = get_user_by_id(int(uid))
    if not u:
        return False
    if (u.get("role") or "") != "deportista":
        return False
    try:
        if int(u.get("coach_id") or -1) != int(coach_id):
            return False
    except Exception:
        return False

    delete_user(int(uid))
    return True


# ======================
# Adopción coach <-> deportistas (nuevo)
# ======================

def search_athletes(text: str = "", sport: str = None, limit: int = 50):
    """
    Busca deportistas por:
    - nombre (LIKE) opcional
    - deporte exacto opcional

    Cambio mínimo (compatibilidad con app_updated.py):
    - Antes requería 'text' obligatorio.
    - Ahora permite buscar SOLO por deporte (text vacío), para que el coach filtre por deporte.
    - Si no hay ni text ni sport, devuelve [] (para no listar toda la BD sin querer).
    """
    text = (text or "").strip()
    sport = (sport or "").strip() if sport is not None else None
    sport = sport if sport else None

    if not text and not sport:
        return []

    like = f"%{text}%" if text else None

    with _get_conn() as con:
        cur = con.cursor()
        if text and sport:
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (name LIKE ?) AND (sport = ?)
                ORDER BY name
                LIMIT ?
                """,
                (like, sport, int(limit)),
            )
        elif text and not sport:
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (name LIKE ?)
                ORDER BY name
                LIMIT ?
                """,
                (like, int(limit)),
            )
        else:
            # sport y sin text
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (sport = ?)
                ORDER BY name
                LIMIT ?
                """,
                (sport, int(limit)),
            )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "sport": r[3],
            "created_at": r[4],
            "coach_id": r[5],
        }
        for r in rows
    ]


def adopt_athlete(coach_id: int, athlete_id: int):
    """
    Añade (coach, atleta) a coach_athletes. No altera users.coach_id por defecto.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES(?,?,?)",
            (int(coach_id), int(athlete_id), datetime.utcnow().isoformat()),
        )


def adopt_athlete_set_primary_if_empty(coach_id: int, athlete_id: int):
    """
    Adopta al atleta y, si NO tiene coach_id (legacy), lo setea para que:
    - 'Contactar a mi coach' siga teniendo sentido para el deportista.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES(?,?,?)",
            (int(coach_id), int(athlete_id), datetime.utcnow().isoformat()),
        )
        # si coach_id está vacío, lo seteamos
        try:
            cur.execute(
                """
                UPDATE users
                SET coach_id=?
                WHERE id=? AND (coach_id IS NULL OR coach_id='')
                """,
                (int(coach_id), int(athlete_id)),
            )
        except Exception:
            pass


def remove_adopted_athlete(coach_id: int, athlete_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM coach_athletes WHERE coach_id=? AND athlete_id=?",
            (int(coach_id), int(athlete_id)),
        )


def list_my_athletes(coach_id: int):
    """
    Devuelve deportistas adoptados por un coach (JOIN coach_athletes + users).
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT u.id, u.name, u.role, u.sport, u.created_at, u.coach_id
            FROM coach_athletes ca
            JOIN users u ON u.id = ca.athlete_id
            WHERE ca.coach_id = ?
            ORDER BY u.name
            """,
            (int(coach_id),),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "sport": r[3],
            "created_at": r[4],
            "coach_id": r[5],
        }
        for r in rows
    ]


def list_roster_for_coach(coach_id: int):
    """
    Roster unificado para app_updated.py.
    Une:
      - adopción (coach_athletes -> list_my_athletes)
      - legacy (users.coach_id -> list_athletes_for_coach)
    sin duplicados.
    """
    out = []
    seen = set()

    try:
        a1 = list_my_athletes(int(coach_id)) or []
    except Exception:
        a1 = []

    try:
        a2 = list_athletes_for_coach(int(coach_id)) or []
    except Exception:
        a2 = []

    for a in (a1 + a2):
        aid = a.get("id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        out.append(a)

    return out


# ======================
# Equipos (nuevo)
# ======================

def create_team(coach_id: int, name: str, sport: str = None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Nombre de equipo requerido")
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO teams(coach_id, name, sport, created_at) VALUES(?,?,?,?)",
            (int(coach_id), name, sport, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_teams(coach_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id, coach_id, name, sport, created_at FROM teams WHERE coach_id=? ORDER BY id DESC",
            (int(coach_id),),
        )
        rows = cur.fetchall()
    return [
        {"id": r[0], "coach_id": r[1], "name": r[2], "sport": r[3], "created_at": r[4]}
        for r in rows
    ]


def add_team_member(team_id: int, athlete_id: int, role_label: str = None):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO team_members(team_id, athlete_id, role_label, created_at)
            VALUES(?,?,?,?)
            """,
            (int(team_id), int(athlete_id), (role_label or "").strip() or None, datetime.utcnow().isoformat()),
        )


def remove_team_member(team_id: int, athlete_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM team_members WHERE team_id=? AND athlete_id=?",
            (int(team_id), int(athlete_id)),
        )


def list_team_members(team_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT u.id, u.name, u.sport, tm.role_label, tm.created_at
            FROM team_members tm
            JOIN users u ON u.id = tm.athlete_id
            WHERE tm.team_id = ?
            ORDER BY u.name
            """,
            (int(team_id),),
        )
        rows = cur.fetchall()
    return [
        {"athlete_id": r[0], "name": r[1], "sport": r[2], "role_label": r[3], "added_at": r[4]}
        for r in rows
    ]


# ======================
# Sesiones (nuevo)
# ======================

def create_session(athlete_id: int, created_by: int = None, sport: str = None, notes: str = None):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO sessions(athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                int(athlete_id),
                int(created_by) if created_by is not None else None,
                datetime.utcnow().isoformat(),
                None,
                sport,
                (notes or "").strip() or None,
                "open",
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid




def ensure_open_session(athlete_id: int, created_by: int = None, sport: str = None, notes: str = None):
    """
    Devuelve una sesión abierta existente para el atleta o crea una nueva.
    Ayuda a que la carga/simulación siempre quede asociada a una sesión válida.
    """
    try:
        sessions = list_sessions(int(athlete_id), limit=20) or []
        open_s = next((s for s in sessions if (s.get("status") == "open")), None)
        if open_s and open_s.get("id"):
            return int(open_s.get("id"))
    except Exception:
        pass
    return create_session(athlete_id=int(athlete_id), created_by=created_by, sport=sport, notes=notes)

def close_session(session_id: int, ts_end: str = None):
    end = ts_end or datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE sessions SET ts_end=?, status='closed' WHERE id=?",
            (end, int(session_id)),
        )


def get_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE id=?
            """,
            (int(session_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return {k: v for k, v in zip(cols, row)}


def list_sessions(athlete_id: int, limit: int = 50):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE athlete_id=?
            ORDER BY datetime(ts_start) DESC
            LIMIT ?
            """,
            (int(athlete_id), int(limit)),
        )
        rows = cur.fetchall()
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return [{k: v for k, v in zip(cols, r)} for r in rows]


def get_previous_session(athlete_id: int, session_id: int):
    """
    Devuelve la sesión inmediatamente anterior a 'session_id' (por ts_start).
    Útil para comparativas en informes.
    """
    s = get_session(session_id)
    if not s:
        return None
    ts = s.get("ts_start")
    if not ts:
        return None

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE athlete_id=? AND datetime(ts_start) < datetime(?)
            ORDER BY datetime(ts_start) DESC
            LIMIT 1
            """,
            (int(athlete_id), ts),
        )
        row = cur.fetchone()

    if not row:
        return None
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return {k: v for k, v in zip(cols, row)}


# ======================
# Asignación coach <-> deportistas (legacy)
# ======================

def list_unassigned_athletes():
    """
    LEGACY: Devuelve deportistas que todavía NO tienen coach_id asignado.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, name, sport, created_at
            FROM users
            WHERE role='deportista'
              AND (coach_id IS NULL OR coach_id = '')
            ORDER BY name
            """
        )
        rows = cur.fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "name": r[1],
            "sport": r[2],
            "created_at": r[3],
        })
    return out


def assign_athlete_to_coach(user_id: int, coach_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET coach_id=? WHERE id=?",
            (int(coach_id), int(user_id))
        )


def remove_athlete_from_coach(user_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET coach_id=NULL WHERE id=?",
            (int(user_id),)
        )


def set_athlete_coach(athlete_id: int, coach_id: int):
    assign_athlete_to_coach(athlete_id, coach_id)



# ======================
# Perfil deportivo (nuevo)
# ======================

def _default_athlete_profile():
    return {
        "competitive_level": None,
        "weight_category": None,
        "dominant_side": None,
        "current_status": None,
        "watch_zone": None,
        "competition_proximity": None,
        "profile_note": None,
    }


def get_athlete_profile(uid: int):
    user = get_user_by_id(int(uid))
    base = _default_athlete_profile()
    if not user:
        return base

    raw = user.get("athlete_profile_json")
    if not raw:
        return base

    try:
        data = json.loads(raw)
    except Exception:
        return base

    if not isinstance(data, dict):
        return base

    out = base.copy()
    for k in out.keys():
        v = data.get(k)
        out[k] = v if v not in ("", []) else None
    return out


def save_athlete_profile(uid: int, profile: dict):
    base = _default_athlete_profile()
    clean = {}
    profile = profile or {}
    for k in base.keys():
        v = profile.get(k)
        if isinstance(v, str):
            v = v.strip() or None
        clean[k] = v

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET athlete_profile_json=? WHERE id=?",
            (json.dumps(clean, ensure_ascii=False), int(uid)),
        )
    return clean


# ======================
# User sensors
# ======================

def get_user_sensors(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT sensor_code FROM user_sensors WHERE user_id=?", (uid,))
        rows = cur.fetchall()
    return [r[0] for r in rows]


def set_user_sensors(uid: int, codes):
    codes = codes or []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
        for c in codes:
            cur.execute(
                "INSERT INTO user_sensors(user_id,sensor_code) VALUES(?,?)",
                (uid, c),
            )


# ======================
# ECG files
# ======================

def add_ecg_file(uid: int, filename: str, fs: int, session_id: int = None):
    """
    Añade archivo ECG. Mantiene compatibilidad (session_id opcional).
    """
    with _get_conn() as con:
        cur = con.cursor()
        # si la columna session_id existe, la usamos
        if _has_column(con, "ecg_files", "session_id"):
            cur.execute(
                "INSERT INTO ecg_files(user_id,filename,fs,created_at,session_id) "
                "VALUES(?,?,?,?,?)",
                (uid, filename, fs, datetime.utcnow().isoformat(), session_id),
            )
        else:
            cur.execute(
                "INSERT INTO ecg_files(user_id,filename,fs,created_at) "
                "VALUES(?,?,?,?)",
                (uid, filename, fs, datetime.utcnow().isoformat()),
            )
        return cur.lastrowid


def list_ecg_files(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "ecg_files", "session_id"):
            cur.execute(
                "SELECT id,user_id,filename,fs,created_at,session_id "
                "FROM ecg_files WHERE user_id=? ORDER BY id DESC",
                (uid,),
            )
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "filename": r[2],
                        "fs": r[3],
                        "created_at": r[4],
                        "session_id": r[5],
                    }
                )
            return out
        else:
            cur.execute(
                "SELECT id,user_id,filename,fs,created_at "
                "FROM ecg_files WHERE user_id=? ORDER BY id DESC",
                (uid,),
            )
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "filename": r[2],
                        "fs": r[3],
                        "created_at": r[4],
                    }
                )
            return out


def list_ecg_files_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "ecg_files", "session_id"):
            return []
        cur.execute(
            "SELECT id,user_id,filename,fs,created_at,session_id FROM ecg_files WHERE session_id=? ORDER BY id DESC",
            (int(session_id),),
        )
        return list(_dicts(cur))


def delete_ecg_file(file_id: int):
    """
    Elimina un archivo ECG de forma controlada:
    - borra métricas asociadas
    - borra el registro en ecg_files
    - intenta borrar el archivo físico en data/ecg
    Devuelve True si el registro existía y se eliminó.
    """
    if not file_id:
        return False

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT filename FROM ecg_files WHERE id=?", (int(file_id),))
        row = cur.fetchone()
        if not row:
            return False

        filename = row[0]
        cur.execute("DELETE FROM ecg_metrics WHERE ecg_file_id=?", (int(file_id),))
        cur.execute("DELETE FROM ecg_files WHERE id=?", (int(file_id),))

    try:
        safe_name = os.path.basename(filename or "")
        path = os.path.join("data", "ecg", safe_name)
        if safe_name and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

    return True


# ======================
# ECG metrics (HRV)
# ======================

def save_ecg_metrics(ecg_id: int, bpm: float, sdnn: float, rmssd: float, peaks_count: int):
    """
    Guarda una fila de métricas. Se mantiene igual para no romper histórico existente.
    (Si luego quieres evitar spam por sliders, usa save_ecg_metrics_latest().)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO ecg_metrics(ecg_file_id,bpm,sdnn,rmssd,n_peaks,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (ecg_id, bpm, sdnn, rmssd, peaks_count, datetime.utcnow().isoformat()),
        )


def save_ecg_metrics_latest(ecg_id: int, bpm: float, sdnn: float, rmssd: float, peaks_count: int):
    """
    Variante para UI (sliders): mantiene SOLO la última métrica por archivo ECG.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM ecg_metrics WHERE ecg_file_id=?", (int(ecg_id),))
        cur.execute(
            "INSERT INTO ecg_metrics(ecg_file_id,bpm,sdnn,rmssd,n_peaks,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (ecg_id, bpm, sdnn, rmssd, peaks_count, datetime.utcnow().isoformat()),
        )


def get_last_ecg_metrics(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """SELECT m.bpm,m.sdnn,m.rmssd
               FROM ecg_metrics m
               JOIN ecg_files f ON f.id=m.ecg_file_id
               WHERE f.user_id=?
               ORDER BY m.id DESC LIMIT 1""",
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"bpm": row[0], "sdnn": row[1], "rmssd": row[2]}


# ======================
# Questionnaires
# ======================

def save_questionnaire(uid: int, answers: dict,
                       wellness: float, rpe: float = None, duration: float = None,
                       session_id: int = None):
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "questionnaires", "session_id"):
            cur.execute(
                "INSERT INTO questionnaires(user_id,ts,answers_json,wellness_score,rpe,duration_min,session_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (uid, datetime.utcnow().isoformat(), json.dumps(answers),
                 wellness, rpe, duration, session_id),
            )
        else:
            cur.execute(
                "INSERT INTO questionnaires(user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                "VALUES(?,?,?,?,?,?)",
                (uid, datetime.utcnow().isoformat(), json.dumps(answers),
                 wellness, rpe, duration),
            )


def list_questionnaires(uid: int):
    # ✅ FIX: fetchall dentro del with
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min "
            "FROM questionnaires WHERE user_id=? ORDER BY id DESC",
            (uid,),
        )
        rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "user_id": r[1],
                "ts": r[2],
                "answers_json": r[3],
                "wellness_score": r[4],
                "rpe": r[5],
                "duration_min": r[6],
            }
        )
    return out


def list_questionnaires_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "questionnaires", "session_id"):
            return []
        cur.execute(
            """
            SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min,session_id
            FROM questionnaires
            WHERE session_id=?
            ORDER BY id DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


# ======================
# Peso / Nutrición (nuevo)
# ======================

def add_weight_entry(user_id: int, date: str, weight_kg: float, target_kg: float = None, note: str = None):
    """
    Guarda un registro de peso persistente (DB).
    - date: "YYYY-MM-DD"
    """
    if not user_id:
        raise ValueError("user_id requerido")
    if date is None:
        date = datetime.utcnow().date().isoformat()

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO weights(user_id, date, weight_kg, target_kg, note, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(user_id),
                str(date),
                float(weight_kg),
                float(target_kg) if target_kg is not None else None,
                (note or "").strip() or None,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_weight_entries(user_id: int, limit: int = 200):
    """
    Devuelve registros de peso del usuario (más recientes primero por fecha/id).
    """
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, user_id, date, weight_kg, target_kg, note, created_at
            FROM weights
            WHERE user_id=?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "user_id": r[1],
            "date": r[2],
            "weight": r[3],
            "target": r[4],
            "note": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_latest_weight_entry(user_id: int):
    rows = list_weight_entries(int(user_id), limit=1)
    return rows[0] if rows else None


def delete_weight_entry(entry_id: int, user_id: int = None):
    """
    Elimina un registro de peso (opcionalmente validando user_id).
    """
    if not entry_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if user_id is None:
            cur.execute("DELETE FROM weights WHERE id=?", (int(entry_id),))
        else:
            cur.execute("DELETE FROM weights WHERE id=? AND user_id=?", (int(entry_id), int(user_id)))


def add_nutrition_entry(user_id: int, date: str, adherence_pct: float, kcal: float = None, note: str = None):
    """
    Guarda un registro de nutrición persistente (DB).
    - adherence_pct: 0..100
    - date: "YYYY-MM-DD"
    """
    if not user_id:
        raise ValueError("user_id requerido")
    if date is None:
        date = datetime.utcnow().date().isoformat()

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO nutrition_logs(user_id, date, adherence_pct, kcal, note, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(user_id),
                str(date),
                float(adherence_pct),
                float(kcal) if kcal is not None else None,
                (note or "").strip() or None,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_nutrition_entries(user_id: int, limit: int = 200):
    """
    Devuelve registros de nutrición del usuario (más recientes primero por fecha/id).
    """
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, user_id, date, adherence_pct, kcal, note, created_at
            FROM nutrition_logs
            WHERE user_id=?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "user_id": r[1],
            "date": r[2],
            "adherence": r[3],
            "kcal": r[4],
            "note": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_latest_nutrition_entry(user_id: int):
    rows = list_nutrition_entries(int(user_id), limit=1)
    return rows[0] if rows else None


def delete_nutrition_entry(entry_id: int, user_id: int = None):
    """
    Elimina un registro de nutrición (opcionalmente validando user_id).
    """
    if not entry_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if user_id is None:
            cur.execute("DELETE FROM nutrition_logs WHERE id=?", (int(entry_id),))
        else:
            cur.execute("DELETE FROM nutrition_logs WHERE id=? AND user_id=?", (int(entry_id), int(user_id)))


# ======================
# Métricas IMU / EMG / RESP
# ======================

def save_imu_metrics(user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "imu_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO imu_metrics (user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_hits),
                 float(hits_per_min), float(mean_int_g), float(max_int_g), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO imu_metrics (user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_hits),
                 float(hits_per_min), float(mean_int_g), float(max_int_g)),
            )


def list_imu_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g
            FROM imu_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "n_hits": r[3],
            "hits_per_min": r[4],
            "mean_int_g": r[5],
            "max_int_g": r[6],
        }
        for r in rows
    ]


def list_imu_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "imu_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g, session_id
            FROM imu_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


def save_emg_metrics(user_id, filename, rms, peak, fatigue, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "emg_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO emg_metrics (user_id, filename, rms, peak, fatigue, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, float(rms), float(peak), float(fatigue), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO emg_metrics (user_id, filename, rms, peak, fatigue)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, float(rms), float(peak), float(fatigue)),
            )


def list_emg_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, rms, peak, fatigue
            FROM emg_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "rms": r[3],
            "peak": r[4],
            "fatigue": r[5],
        }
        for r in rows
    ]


def list_emg_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "emg_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, rms, peak, fatigue, session_id
            FROM emg_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


def save_resp_metrics(user_id, filename, n_breaths, br_min, mean_period, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "resp_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO resp_metrics (user_id, filename, n_breaths, br_min, mean_period, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_breaths),
                 float(br_min), float(mean_period), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO resp_metrics (user_id, filename, n_breaths, br_min, mean_period)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_breaths),
                 float(br_min), float(mean_period)),
            )


def list_resp_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, n_breaths, br_min, mean_period
            FROM resp_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "n_breaths": r[3],
            "br_min": r[4],
            "mean_period": r[5],
        }
        for r in rows
    ]


def list_resp_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "resp_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, n_breaths, br_min, mean_period, session_id
            FROM resp_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


# ---------------------------------------------------------------------------
# CS-016 — Carga Acumulada Semanal
# ---------------------------------------------------------------------------

def get_weekly_load_summary(uid: int, days: int = 7):
    """Carga acumulada de los últimos `days` días usando sRPE Foster (RPE × min).
    Devuelve n_sessions, load_units, wellness_avg, trend y flag de estado."""
    import datetime
    now = datetime.datetime.utcnow()
    cutoff_cur = (now - datetime.timedelta(days=days)).isoformat()
    cutoff_prev = (now - datetime.timedelta(days=days * 2)).isoformat()

    with _get_conn() as con:
        cur = con.cursor()

        cur.execute(
            "SELECT wellness_score, rpe, duration_min FROM questionnaires "
            "WHERE user_id=? AND datetime(ts) >= datetime(?) ORDER BY ts DESC",
            (int(uid), cutoff_cur),
        )
        rows_cur = list(_dicts(cur))

        cur.execute(
            "SELECT rpe, duration_min FROM questionnaires "
            "WHERE user_id=? AND datetime(ts) >= datetime(?) AND datetime(ts) < datetime(?)",
            (int(uid), cutoff_prev, cutoff_cur),
        )
        rows_prev = list(_dicts(cur))

        cur.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE athlete_id=? AND datetime(ts_start) >= datetime(?)",
            (int(uid), cutoff_cur),
        )
        row = cur.fetchone()
        n_sessions = row[0] if row else 0

    wellness_vals = [r["wellness_score"] for r in rows_cur if r.get("wellness_score") is not None]
    wellness_avg = round(sum(wellness_vals) / len(wellness_vals), 1) if wellness_vals else None

    def _load(rows):
        return sum((r.get("rpe") or 0) * (r.get("duration_min") or 0) for r in rows)

    load_cur = _load(rows_cur)
    load_prev = _load(rows_prev)
    has_load = any((r.get("rpe") and r.get("duration_min")) for r in rows_cur)

    if load_prev == 0 and load_cur == 0:
        trend = "stable"
    elif load_prev == 0:
        trend = "up"
    elif load_cur == 0:
        trend = "down"
    else:
        pct = (load_cur - load_prev) / load_prev
        trend = "up" if pct > 0.30 else ("down" if pct < -0.20 else "stable")

    if wellness_avg is None:
        flag = "gray"
    elif wellness_avg < 50 or (trend == "up" and load_cur > 400):
        flag = "red"
    elif wellness_avg < 70 or trend == "up":
        flag = "yellow"
    else:
        flag = "green"

    return {
        "n_sessions": n_sessions,
        "load_units": round(load_cur) if has_load else None,
        "load_prev_units": round(load_prev) if any((r.get("rpe") and r.get("duration_min")) for r in rows_prev) else None,
        "wellness_avg": wellness_avg,
        "trend": trend,
        "flag": flag,
        "has_data": bool(rows_cur or n_sessions > 0),
    }


def get_team_weekly_summary(coach_id: int):
    """Devuelve lista de {atleta + resumen semanal} para el roster del coach,
    filtrado por el mismo deporte que el coach."""
    coach = get_user_by_id(int(coach_id))
    coach_sport = (coach.get("sport") or "").strip().lower() if coach else ""

    athletes = list_roster_for_coach(coach_id)
    result = []
    for a in athletes:
        aid = a.get("id")
        if not aid:
            continue
        # Filtro por deporte: solo atletas del mismo deporte que el coach
        if coach_sport:
            athlete_sport = (a.get("sport") or "").strip().lower()
            if athlete_sport != coach_sport:
                continue
        summary = get_weekly_load_summary(int(aid))
        result.append({**a, "weekly": summary})
    return result


def get_load_history(uid: int, weeks: int = 4):
    """Devuelve lista de buckets semanales (lunes–domingo) de los últimos `weeks` semanas.
    Cada bucket: {label, load_units, wellness_avg, n_sessions}."""
    import datetime
    today = datetime.date.today()
    this_monday = today - datetime.timedelta(days=today.weekday())

    buckets = []
    for i in range(weeks - 1, -1, -1):
        w_start = this_monday - datetime.timedelta(weeks=i)
        w_end = w_start + datetime.timedelta(days=7)
        label = "Esta sem" if i == 0 else f"Sem -{i}"
        buckets.append({"label": label, "start": w_start.isoformat(), "end": w_end.isoformat()})

    with _get_conn() as con:
        cur = con.cursor()
        result = []
        for b in buckets:
            cur.execute(
                """
                SELECT
                    AVG(wellness_score) AS wa,
                    SUM(CASE WHEN rpe IS NOT NULL AND duration_min IS NOT NULL
                             THEN rpe * duration_min ELSE 0 END) AS lu
                FROM questionnaires
                WHERE user_id = ?
                  AND date(ts) >= ? AND date(ts) < ?
                """,
                (int(uid), b["start"], b["end"]),
            )
            row = cur.fetchone()
            wellness_avg = round(row[0], 1) if row and row[0] is not None else None
            load_raw = row[1] if row and row[1] else 0
            has_load = any(
                row and row[1]
                for _ in [None]
            )
            # re-query to detect if there was actual rpe*duration data
            cur.execute(
                """
                SELECT COUNT(*) FROM questionnaires
                WHERE user_id = ? AND date(ts) >= ? AND date(ts) < ?
                  AND rpe IS NOT NULL AND duration_min IS NOT NULL
                """,
                (int(uid), b["start"], b["end"]),
            )
            cnt = cur.fetchone()
            has_load = (cnt[0] > 0) if cnt else False

            cur.execute(
                "SELECT COUNT(*) FROM sessions WHERE athlete_id = ? AND date(ts_start) >= ? AND date(ts_start) < ?",
                (int(uid), b["start"], b["end"]),
            )
            sess = cur.fetchone()
            n_sessions = sess[0] if sess else 0

            result.append({
                "label": b["label"],
                "load_units": round(load_raw) if has_load else None,
                "wellness_avg": wellness_avg,
                "n_sessions": n_sessions,
            })
    return result


def ensure_demo_user() -> int:
    """Garantiza que existe un usuario demo con datos sembrados. Devuelve su user_id."""
    import datetime as _dt, json as _json

    DEMO_EMAIL = "demo@combatiq.app"
    DEMO_NAME  = "Demo Atleta"
    DEMO_SPORT = "Taekwondo"

    with _get_conn() as con:
        cur = con.cursor()

        cur.execute("SELECT id FROM users WHERE email=?", (DEMO_EMAIL,))
        row = cur.fetchone()
        if row:
            uid = row[0]
        else:
            cur.execute(
                "INSERT INTO users (name, email, role, sport, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                (DEMO_NAME, DEMO_EMAIL, "deportista", DEMO_SPORT,
                 b"demo_no_auth", _dt.datetime.utcnow().isoformat()),
            )
            uid = cur.lastrowid

        # Perfil deportivo
        cur.execute("SELECT athlete_profile_json FROM users WHERE id=?", (uid,))
        prof_row = cur.fetchone()
        if not prof_row or not prof_row[0]:
            profile = _json.dumps({
                "competitive_level":    "Competitivo",
                "weight_category":      "-68 kg",
                "dominant_side":        "Derecho",
                "current_status":       "Listo con control",
                "watch_zone":           "rodilla izquierda",
                "competition_proximity":"Próximas 3-4 semanas",
                "profile_note":         "Priorizar guardia y pierna de apoyo. Torneo regional en 3 semanas.",
            })
            cur.execute("UPDATE users SET athlete_profile_json=? WHERE id=?", (profile, uid))

        # Cuestionarios sembrados (una entrada por semana, 4 semanas)
        cur.execute("SELECT COUNT(*) FROM questionnaires WHERE user_id=?", (uid,))
        if (cur.fetchone() or [0])[0] < 4:
            seeds = [
                {"d": 22, "w": 72.0, "rpe": 7, "dur": 75,
                 "a": {"energia":4,"recuperacion":4,"sueno_calidad":3,"sueno_horas":7,
                       "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                       "tkd_explosividad":4,"tkd_agilidad":3,"tkd_ritmo":4,"tkd_molestia_inferior":2}},
                {"d": 15, "w": 65.0, "rpe": 8, "dur": 90,
                 "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":6,
                       "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                       "tkd_explosividad":3,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
                {"d": 8,  "w": 58.0, "rpe": 9, "dur":100,
                 "a": {"energia":3,"recuperacion":2,"sueno_calidad":2,"sueno_horas":5,
                       "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":4,
                       "tkd_explosividad":3,"tkd_agilidad":2,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
                {"d": 1,  "w": 74.0, "rpe": 6, "dur": 70,
                 "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,
                       "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                       "tkd_explosividad":4,"tkd_agilidad":4,"tkd_ritmo":4,"tkd_molestia_inferior":2}},
            ]
            for s in seeds:
                ts = (_dt.datetime.utcnow() - _dt.timedelta(days=s["d"])).isoformat()
                cur.execute(
                    "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                    "VALUES (?,?,?,?,?,?)",
                    (uid, ts, _json.dumps(s["a"]), s["w"], s["rpe"], s["dur"]),
                )
    return uid


def ensure_demo_coach() -> int:
    """Garantiza que existe un coach demo con 3 atletas y datos sembrados. Devuelve coach user_id."""
    import datetime as _dt, json as _json

    COACH_EMAIL = "demo-coach@combatiq.app"
    COACH_NAME  = "Demo Coach"

    # Atletas: (email, nombre, sport, nivel, weight_cat, watch_zone, comp_prox)
    ATHLETES = [
        ("demo-ana@combatiq.app",    "Ana Torres",  "Taekwondo", "Competitivo",      "-57 kg",  "tobillo derecho",   "Próximas 3-4 semanas"),
        ("demo-carlos@combatiq.app", "Carlos Ruiz", "Taekwondo", "Alto rendimiento", "-80 kg",  "hombro izquierdo",  "Próximas 1-2 semanas"),
        ("demo-mia@combatiq.app",    "Mia Soto",    "Taekwondo", "Recreativo",       "-53 kg",  "muñeca derecha",    "Sin competencia cercana"),
    ]

    # Semillas por atleta: (dias_atras, wellness, rpe, duracion, answers)
    SEEDS = [
        # Ana — balanceada, semáforo verde
        [
            {"d": 22, "w": 76.0, "rpe": 6, "dur": 70,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":7,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                   "tkd_explosividad":4,"tkd_agilidad":4,"tkd_ritmo":4,"tkd_molestia_inferior":2}},
            {"d": 15, "w": 71.0, "rpe": 6, "dur": 65,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":3,"sueno_horas":7,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                   "tkd_explosividad":4,"tkd_agilidad":3,"tkd_ritmo":4,"tkd_molestia_inferior":2}},
            {"d": 8,  "w": 69.0, "rpe": 7, "dur": 75,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,
                   "listo_rendir":4,"fatiga_general":3,"cuerpo_pesado":2,
                   "tkd_explosividad":4,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":2}},
            {"d": 1,  "w": 73.0, "rpe": 6, "dur": 70,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                   "tkd_explosividad":4,"tkd_agilidad":4,"tkd_ritmo":4,"tkd_molestia_inferior":2}},
        ],
        # Carlos — RPE alto, fatiga acumulada, semáforo amarillo
        [
            {"d": 22, "w": 63.0, "rpe": 8, "dur": 95,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                   "tkd_explosividad":3,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
            {"d": 15, "w": 60.0, "rpe": 9, "dur":100,
             "a": {"energia":3,"recuperacion":2,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":3,
                   "tkd_explosividad":3,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
            {"d": 8,  "w": 55.0, "rpe": 9, "dur":110,
             "a": {"energia":2,"recuperacion":2,"sueno_calidad":2,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":4,
                   "tkd_explosividad":3,"tkd_agilidad":2,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
            {"d": 1,  "w": 58.0, "rpe": 8, "dur": 90,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                   "tkd_explosividad":3,"tkd_agilidad":3,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
        ],
        # Mia — sobrecarga, wellness bajo, semáforo rojo
        [
            {"d": 22, "w": 52.0, "rpe": 8, "dur": 80,
             "a": {"energia":3,"recuperacion":2,"sueno_calidad":2,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":3,
                   "tkd_explosividad":3,"tkd_agilidad":2,"tkd_ritmo":3,"tkd_molestia_inferior":3}},
            {"d": 15, "w": 48.0, "rpe": 9, "dur": 90,
             "a": {"energia":2,"recuperacion":2,"sueno_calidad":2,"sueno_horas":5,
                   "listo_rendir":2,"fatiga_general":4,"cuerpo_pesado":4,
                   "tkd_explosividad":2,"tkd_agilidad":2,"tkd_ritmo":2,"tkd_molestia_inferior":4}},
            {"d": 8,  "w": 44.0, "rpe":10, "dur":105,
             "a": {"energia":2,"recuperacion":1,"sueno_calidad":2,"sueno_horas":5,
                   "listo_rendir":2,"fatiga_general":5,"cuerpo_pesado":5,
                   "tkd_explosividad":2,"tkd_agilidad":2,"tkd_ritmo":2,"tkd_molestia_inferior":4}},
            {"d": 1,  "w": 41.0, "rpe":10, "dur":100,
             "a": {"energia":2,"recuperacion":1,"sueno_calidad":1,"sueno_horas":5,
                   "listo_rendir":2,"fatiga_general":5,"cuerpo_pesado":5,
                   "tkd_explosividad":2,"tkd_agilidad":1,"tkd_ritmo":2,"tkd_molestia_inferior":5}},
        ],
    ]

    with _get_conn() as con:
        cur = con.cursor()

        # --- Coach ---
        cur.execute("SELECT id FROM users WHERE email=?", (COACH_EMAIL,))
        row = cur.fetchone()
        if row:
            coach_id = row[0]
        else:
            cur.execute(
                "INSERT INTO users (name, email, role, sport, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                (COACH_NAME, COACH_EMAIL, "coach", "Taekwondo",
                 b"demo_no_auth", _dt.datetime.utcnow().isoformat()),
            )
            coach_id = cur.lastrowid

        # --- Atletas ---
        for idx, (email, name, sport, level, weight, watch, comp) in enumerate(ATHLETES):
            cur.execute("SELECT id FROM users WHERE email=?", (email,))
            row = cur.fetchone()
            if row:
                aid = row[0]
            else:
                cur.execute(
                    "INSERT INTO users (name, email, role, sport, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                    (name, email, "deportista", sport,
                     b"demo_no_auth", _dt.datetime.utcnow().isoformat()),
                )
                aid = cur.lastrowid

            # Perfil deportivo
            cur.execute("SELECT athlete_profile_json FROM users WHERE id=?", (aid,))
            prof_row = cur.fetchone()
            if not prof_row or not prof_row[0]:
                profile = _json.dumps({
                    "competitive_level":     level,
                    "weight_category":       weight,
                    "dominant_side":         "Derecho",
                    "current_status":        "Listo con control",
                    "watch_zone":            watch,
                    "competition_proximity": comp,
                })
                cur.execute("UPDATE users SET athlete_profile_json=? WHERE id=?", (profile, aid))

            # Vincular al coach
            cur.execute(
                "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES (?,?,?)",
                (int(coach_id), int(aid), _dt.datetime.utcnow().isoformat()),
            )
            # Legacy coach_id column
            cur.execute(
                "UPDATE users SET coach_id=? WHERE id=? AND (coach_id IS NULL OR coach_id='')",
                (int(coach_id), int(aid)),
            )

            # Cuestionarios sembrados
            cur.execute("SELECT COUNT(*) FROM questionnaires WHERE user_id=?", (aid,))
            if (cur.fetchone() or [0])[0] < 4:
                for s in SEEDS[idx]:
                    ts = (_dt.datetime.utcnow() - _dt.timedelta(days=s["d"])).isoformat()
                    cur.execute(
                        "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                        "VALUES (?,?,?,?,?,?)",
                        (aid, ts, _json.dumps(s["a"]), s["w"], s["rpe"], s["dur"]),
                    )

    return coach_id


def ensure_demo_coach_boxeo() -> int:
    """Garantiza que existe un coach demo de Boxeo con 3 atletas y datos sembrados. Devuelve coach user_id."""
    import datetime as _dt, json as _json

    COACH_EMAIL = "demo-coach-boxeo@combatiq.app"
    COACH_NAME  = "Demo Coach Boxeo"

    # Atletas: (email, nombre, sport, nivel, weight_cat, watch_zone, comp_prox)
    ATHLETES = [
        ("demo-box-luis@combatiq.app",   "Luis Peña",    "Boxeo", "Competitivo",      "-69 kg",  "hombro derecho",   "Próximas 3-4 semanas"),
        ("demo-box-sofia@combatiq.app",  "Sofía Vega",   "Boxeo", "Alto rendimiento", "-60 kg",  "muñeca izquierda", "Próximas 1-2 semanas"),
        ("demo-box-marco@combatiq.app",  "Marco Díaz",   "Boxeo", "Iniciación",       "-75 kg",  "Sin zona crítica", "Sin competencia cercana"),
    ]

    SEEDS = [
        # Luis — carga alta pero sostenida, semáforo amarillo
        [
            {"d": 22, "w": 64.0, "rpe": 8, "dur": 85,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,
                   "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                   "box_potencia":4,"box_velocidad":3,"box_guard":3,"box_molestia_sup":2}},
            {"d": 15, "w": 61.0, "rpe": 8, "dur": 90,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":3}},
            {"d": 8,  "w": 59.0, "rpe": 9, "dur": 95,
             "a": {"energia":3,"recuperacion":2,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":3,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":3}},
            {"d": 1,  "w": 60.0, "rpe": 8, "dur": 85,
             "a": {"energia":3,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,
                   "listo_rendir":3,"fatiga_general":3,"cuerpo_pesado":3,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":3}},
        ],
        # Sofía — pico de carga precompetitiva, semáforo rojo
        [
            {"d": 22, "w": 70.0, "rpe": 7, "dur": 75,
             "a": {"energia":4,"recuperacion":3,"sueno_calidad":3,"sueno_horas":7,
                   "listo_rendir":4,"fatiga_general":3,"cuerpo_pesado":2,
                   "box_potencia":4,"box_velocidad":4,"box_guard":4,"box_molestia_sup":2}},
            {"d": 15, "w": 58.0, "rpe": 9, "dur": 95,
             "a": {"energia":3,"recuperacion":2,"sueno_calidad":3,"sueno_horas":6,
                   "listo_rendir":3,"fatiga_general":4,"cuerpo_pesado":3,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":3}},
            {"d": 8,  "w": 46.0, "rpe":10, "dur":105,
             "a": {"energia":2,"recuperacion":2,"sueno_calidad":2,"sueno_horas":5,
                   "listo_rendir":2,"fatiga_general":5,"cuerpo_pesado":4,
                   "box_potencia":2,"box_velocidad":3,"box_guard":2,"box_molestia_sup":4}},
            {"d": 1,  "w": 43.0, "rpe":10, "dur":100,
             "a": {"energia":2,"recuperacion":1,"sueno_calidad":2,"sueno_horas":5,
                   "listo_rendir":2,"fatiga_general":5,"cuerpo_pesado":5,
                   "box_potencia":2,"box_velocidad":2,"box_guard":2,"box_molestia_sup":5}},
        ],
        # Marco — carga ligera, buena recuperación, semáforo verde
        [
            {"d": 22, "w": 78.0, "rpe": 5, "dur": 60,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":1,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":1}},
            {"d": 15, "w": 75.0, "rpe": 5, "dur": 55,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":8,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                   "box_potencia":3,"box_velocidad":3,"box_guard":3,"box_molestia_sup":1}},
            {"d": 8,  "w": 77.0, "rpe": 6, "dur": 65,
             "a": {"energia":4,"recuperacion":4,"sueno_calidad":4,"sueno_horas":7,
                   "listo_rendir":4,"fatiga_general":2,"cuerpo_pesado":2,
                   "box_potencia":3,"box_velocidad":4,"box_guard":4,"box_molestia_sup":1}},
            {"d": 1,  "w": 79.0, "rpe": 5, "dur": 60,
             "a": {"energia":5,"recuperacion":5,"sueno_calidad":5,"sueno_horas":8,
                   "listo_rendir":5,"fatiga_general":1,"cuerpo_pesado":1,
                   "box_potencia":4,"box_velocidad":4,"box_guard":4,"box_molestia_sup":1}},
        ],
    ]

    with _get_conn() as con:
        cur = con.cursor()

        # --- Coach ---
        cur.execute("SELECT id FROM users WHERE email=?", (COACH_EMAIL,))
        row = cur.fetchone()
        if row:
            coach_id = row[0]
        else:
            cur.execute(
                "INSERT INTO users (name, email, role, sport, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                (COACH_NAME, COACH_EMAIL, "coach", "Boxeo",
                 b"demo_no_auth", _dt.datetime.utcnow().isoformat()),
            )
            coach_id = cur.lastrowid

        # --- Atletas ---
        for idx, (email, name, sport, level, weight, watch, comp) in enumerate(ATHLETES):
            cur.execute("SELECT id FROM users WHERE email=?", (email,))
            row = cur.fetchone()
            if row:
                aid = row[0]
            else:
                cur.execute(
                    "INSERT INTO users (name, email, role, sport, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                    (name, email, "deportista", sport,
                     b"demo_no_auth", _dt.datetime.utcnow().isoformat()),
                )
                aid = cur.lastrowid

            # Perfil deportivo
            cur.execute("SELECT athlete_profile_json FROM users WHERE id=?", (aid,))
            prof_row = cur.fetchone()
            if not prof_row or not prof_row[0]:
                profile = _json.dumps({
                    "competitive_level":     level,
                    "weight_category":       weight,
                    "dominant_side":         "Derecho",
                    "current_status":        "Listo con control",
                    "watch_zone":            watch,
                    "competition_proximity": comp,
                })
                cur.execute("UPDATE users SET athlete_profile_json=? WHERE id=?", (profile, aid))

            # Vincular al coach
            cur.execute(
                "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES (?,?,?)",
                (int(coach_id), int(aid), _dt.datetime.utcnow().isoformat()),
            )
            cur.execute(
                "UPDATE users SET coach_id=? WHERE id=? AND (coach_id IS NULL OR coach_id='')",
                (int(coach_id), int(aid)),
            )

            # Cuestionarios sembrados
            cur.execute("SELECT COUNT(*) FROM questionnaires WHERE user_id=?", (aid,))
            if (cur.fetchone() or [0])[0] < 4:
                for s in SEEDS[idx]:
                    ts = (_dt.datetime.utcnow() - _dt.timedelta(days=s["d"])).isoformat()
                    cur.execute(
                        "INSERT INTO questionnaires (user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                        "VALUES (?,?,?,?,?,?)",
                        (aid, ts, _json.dumps(s["a"]), s["w"], s["rpe"], s["dur"]),
                    )

    return coach_id


def save_rpe_entry(uid: int, rpe: float, duration_min: float = 0, session_id: int = None):
    """Guarda un registro rápido de esfuerzo post-sesión (RPE + duración).
    No reemplaza el cuestionario de wellbeing — alimenta directamente el cálculo de carga."""
    import datetime
    import json
    ts = datetime.datetime.utcnow().isoformat()
    answers = json.dumps({"quick_rpe": True})
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO questionnaires (user_id, ts, answers_json, wellness_score, rpe, duration_min, session_id)
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            """,
            (int(uid), ts, answers, float(rpe), float(duration_min or 0), session_id),
        )
    return True