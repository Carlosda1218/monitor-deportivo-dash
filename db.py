import os, json, sqlite3, hashlib, hmac, logging, secrets
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.path.join("data", "users.db")
_WAL_CONFIGURED_PATHS = set()

# ── Detección de backend ──────────────────────────────────────────────────────
# Si las variables SUPABASE_* están presentes usamos PostgreSQL (psycopg2).
# En caso contrario, SQLite local (desarrollo / tests).
_SUPABASE_HOST = os.getenv("SUPABASE_HOST")
_USE_POSTGRES   = bool(_SUPABASE_HOST)


# ── Cursor wrapper ────────────────────────────────────────────────────────────
# Convierte placeholders SQLite (?) a PostgreSQL (%s) y expone lastrowid
# usando SELECT lastval() para mantener compatibilidad con el código existente.
class _PGCursor:
    def __init__(self, cur):
        self._c = cur

    @staticmethod
    def _adapt(sql: str) -> str:
        import re
        s = sql.upper()

        # INSERT OR IGNORE → INSERT INTO ... ON CONFLICT DO NOTHING
        if 'INSERT OR IGNORE' in s:
            sql = re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT INTO',
                         sql, flags=re.IGNORECASE)
            sql = sql.rstrip() + ' ON CONFLICT DO NOTHING'
            return sql.replace("?", "%s")

        # INSERT OR REPLACE INTO schema_migrations → upsert on (version)
        if 'INSERT OR REPLACE' in s and 'SCHEMA_MIGRATIONS' in s:
            sql = re.sub(r'\bINSERT\s+OR\s+REPLACE\s+INTO\b', 'INSERT INTO',
                         sql, flags=re.IGNORECASE)
            sql = sql.rstrip() + ' ON CONFLICT (version) DO UPDATE SET applied_at = EXCLUDED.applied_at'
            return sql.replace("?", "%s")

        # INSERT OR REPLACE INTO team_members → upsert on (team_id, athlete_id)
        if 'INSERT OR REPLACE' in s and 'TEAM_MEMBERS' in s:
            sql = re.sub(r'\bINSERT\s+OR\s+REPLACE\s+INTO\b', 'INSERT INTO',
                         sql, flags=re.IGNORECASE)
            sql = sql.rstrip() + ' ON CONFLICT (team_id, athlete_id) DO UPDATE SET role_label = EXCLUDED.role_label, created_at = EXCLUDED.created_at'
            return sql.replace("?", "%s")

        # Remaining INSERT OR REPLACE → plain INSERT (constraint error = correct behavior)
        if 'INSERT OR REPLACE' in s:
            sql = re.sub(r'\bINSERT\s+OR\s+REPLACE\s+INTO\b', 'INSERT INTO',
                         sql, flags=re.IGNORECASE)

        # ── DDL: SQLite → PostgreSQL syntax ──────────────────────────────────
        # AUTOINCREMENT → SERIAL (must replace entire "INTEGER PRIMARY KEY AUTOINCREMENT")
        sql = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
                     'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
        # BLOB → BYTEA (password hashes)
        sql = re.sub(r'\bBLOB\b', 'BYTEA', sql, flags=re.IGNORECASE)
        # SQLite datetime defaults → PostgreSQL
        sql = re.sub(r"DEFAULT\s+\(datetime\('now'\)\)", "DEFAULT (NOW()::TEXT)",
                     sql, flags=re.IGNORECASE)
        sql = re.sub(r"DEFAULT\s+\(strftime\('[^']+','now'\)\)",
                     "DEFAULT (NOW()::TEXT)", sql, flags=re.IGNORECASE)
        # PRAGMA statements → no-op comment (PostgreSQL ignores them)
        if re.match(r'^\s*PRAGMA\b', sql, re.IGNORECASE):
            return "SELECT 1 -- pragma skipped"

        # SQLite datetime() wrapper → bare column/value (ISO strings sort correctly in PG)
        # datetime(col) → col  |  datetime(?) → ?
        sql = re.sub(r'\bdatetime\((\?|[a-zA-Z_][\w.]*)\)', r'\1', sql, flags=re.IGNORECASE)

        # SQLite empty-string integer comparisons → IS NULL
        sql = re.sub(r"(\w+)\s*=\s*''", r"\1 IS NULL", sql)
        sql = re.sub(r'(\w+)\s*=\s*""', r"\1 IS NULL", sql)

        return sql.replace("?", "%s")

    def execute(self, sql: str, params=None):
        sql = self._adapt(sql)
        self._c.execute(sql, params) if params is not None else self._c.execute(sql)
        return self

    def executemany(self, sql: str, seq):
        sql = sql.replace("?", "%s")
        self._c.executemany(sql, seq)
        return self

    def fetchone(self):  return self._c.fetchone()
    def fetchall(self):  return self._c.fetchall()

    @property
    def description(self): return self._c.description
    @property
    def rowcount(self):    return self._c.rowcount

    @property
    def lastrowid(self):
        try:
            self._c.execute("SELECT lastval()")
            row = self._c.fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def __iter__(self): return iter(self._c)


# ── Conexión PostgreSQL ───────────────────────────────────────────────────────
class _PGConn:
    """Thin wrapper sobre psycopg2 connection que imita la interfaz sqlite3."""

    def __init__(self):
        import psycopg2
        self._con = psycopg2.connect(
            host=os.getenv("SUPABASE_HOST"),
            port=int(os.getenv("SUPABASE_PORT", "5432")),
            dbname=os.getenv("SUPABASE_DB", "postgres"),
            user=os.getenv("SUPABASE_USER", "postgres"),
            password=os.getenv("SUPABASE_PASSWORD", ""),
            sslmode="require",
            connect_timeout=10,
        )
        self._con.autocommit = False

    def cursor(self):
        return _PGCursor(self._con.cursor())

    def execute(self, sql: str, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):   self._con.commit()
    def rollback(self): self._con.rollback()
    def close(self):    self._con.close()


# ======================
# Conexión / utilidades
# ======================

def _conn():
    if _USE_POSTGRES:
        return _PGConn()
    # SQLite local
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass
    try:
        db_key = os.path.abspath(DB_PATH)
        if db_key not in _WAL_CONFIGURED_PATHS:
            con.execute("PRAGMA journal_mode = WAL;")
            _WAL_CONFIGURED_PATHS.add(db_key)
    except Exception:
        pass
    try:
        con.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout = 5000;")
    except Exception:
        pass
    return con


@contextmanager
def _get_conn():
    con = _conn()
    try:
        yield con
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass


def _dicts(cur):
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        yield {k: v for k, v in zip(cols, row)}


def _has_column(con, table: str, column: str) -> bool:
    try:
        cur = con.cursor()
        if _USE_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            )
            return cur.fetchone() is not None
        else:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
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

        # --------------------------
        # Migración 60: macros en nutrition_logs
        # --------------------------
        if current < 60:
            cur = con.cursor()
            for col in ("protein_g", "carbs_g", "fats_g", "water_ml"):
                if not _has_column(con, "nutrition_logs", col):
                    try:
                        cur.execute(f"ALTER TABLE nutrition_logs ADD COLUMN {col} REAL")
                    except sqlite3.OperationalError:
                        pass
            _set_db_version(con, 60)

        # Migración 70: tabla announcements (comunicados in-app del coach)
        # --------------------------
        if current < 70:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS announcements(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coach_id INTEGER NOT NULL,
                    sport TEXT,
                    title TEXT NOT NULL,
                    body TEXT,
                    pinned INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )"""
            )
            _set_db_version(con, 70)

        # Migración 80: preferencias de notificación por usuario
        # --------------------------
        if current < 80:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS notification_prefs(
                    user_id INTEGER PRIMARY KEY,
                    low_wellness_alert INTEGER DEFAULT 1,
                    announcement_notify INTEGER DEFAULT 1,
                    checkin_reminder INTEGER DEFAULT 0,
                    email_override TEXT,
                    updated_at TEXT
                )"""
            )
            _set_db_version(con, 80)

        # Migración 90: columna onboarding_done en users
        # --------------------------
        if current < 90:
            cur = con.cursor()
            if not _has_column(con, "users", "onboarding_done"):
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN onboarding_done INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
            _set_db_version(con, 90)

        # Migración 100: tabla competition_events (planificación de competencia)
        # --------------------------
        if current < 100:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS competition_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    sport TEXT,
                    target_weight REAL,
                    location TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            _set_db_version(con, 100)

        # Migración 110: tabla competition_results (logros / trofeos del atleta)
        if current < 110:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS competition_results(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    medal TEXT DEFAULT 'participant',
                    category TEXT,
                    location TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            _set_db_version(con, 110)

        # Migración 120: notas de sesión del coach por atleta
        if current < 120:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS session_notes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coach_id INTEGER NOT NULL,
                    athlete_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            _set_db_version(con, 120)

        # Migración 130: tabla messages (chat interno coach-atleta)
        if current < 130:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    read_at TEXT
                )"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, receiver_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id, read_at)")
            _set_db_version(con, 130)

        # Migración 140: índices de rendimiento faltantes
        # --------------------------
        if current < 140:
            cur = con.cursor()
            for _sql in [
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
                "CREATE INDEX IF NOT EXISTS idx_questionnaires_ts ON questionnaires(user_id, ts)",
                "CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(athlete_id, ts_start)",
            ]:
                try:
                    cur.execute(_sql)
                except Exception:
                    pass
            _set_db_version(con, 140)

        # Migración 150: foto de perfil
        # --------------------------
        if current < 150:
            cur = con.cursor()
            if not _has_column(con, "users", "avatar_url"):
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
                except sqlite3.OperationalError:
                    pass
            _set_db_version(con, 150)

        # Migración 160: sensor_type + métricas giroscopio en imu_metrics
        # --------------------------
        if current < 160:
            cur = con.cursor()
            for col, typedef in [
                ("sensor_type",   "TEXT"),
                ("mean_ang_vel",  "REAL"),
                ("max_ang_vel",   "REAL"),
            ]:
                if not _has_column(con, "imu_metrics", col):
                    try:
                        cur.execute(f"ALTER TABLE imu_metrics ADD COLUMN {col} {typedef}")
                    except sqlite3.OperationalError:
                        pass
            _set_db_version(con, 160)

        # Migración 170: índice correcto para ordenar sesiones por fecha de inicio.
        # La versión 140 intentaba indexar una columna "date" que no existe en sessions.
        if current < 170:
            cur = con.cursor()
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_athlete_ts ON sessions(athlete_id, ts_start)")
            _set_db_version(con, 170)

        # Migración 180: sensor_sessions — entidad de sesión sensorial.
        # Enlaza sessions.id con sensor_devices.id; registra qué sensores
        # estuvieron activos, cuándo y cuántos paquetes entregaron.
        # Diseñada para ser compatible con Postgres sin cambios (ISO timestamps, sin
        # funciones SQLite en queries de lectura, índices explícitos en FK columns).
        if current < 180:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sensor_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   INTEGER NOT NULL,
                    device_id    INTEGER,
                    sensor_code  TEXT    NOT NULL,
                    ts_start     TEXT    NOT NULL
                                 DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    ts_end       TEXT,
                    status       TEXT    NOT NULL DEFAULT 'collecting',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    notes        TEXT,
                    created_at   TEXT    NOT NULL
                                 DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sensor_sessions_session "
                "ON sensor_sessions(session_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sensor_sessions_device "
                "ON sensor_sessions(device_id)"
            )
            _set_db_version(con, 180)

        # --------------------------
        # Migración 190: feedback nutricional del coach
        # --------------------------
        if current < 190:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS nutrition_coach_feedback(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    coach_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    note TEXT,
                    validated_at TEXT,
                    created_at TEXT,
                    UNIQUE(athlete_id, coach_id, week_start),
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (coach_id)   REFERENCES users(id) ON DELETE CASCADE
                )"""
            )
            try:
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_nutri_feedback_athlete "
                    "ON nutrition_coach_feedback(athlete_id)"
                )
            except Exception:
                pass
            _set_db_version(con, 190)

        # --------------------------
        # Migracion 200: indices de lectura para dashboards con historicos
        # --------------------------
        if current < 200:
            cur = con.cursor()
            for _sql in [
                "CREATE INDEX IF NOT EXISTS idx_questionnaires_user_id_desc "
                "ON questionnaires(user_id, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_questionnaires_session "
                "ON questionnaires(session_id)",
                "CREATE INDEX IF NOT EXISTS idx_ecg_files_session_id_desc "
                "ON ecg_files(session_id, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_ecg_metrics_file_id_desc "
                "ON ecg_metrics(ecg_file_id, id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_imu_metrics_session_ts "
                "ON imu_metrics(session_id, ts DESC)",
                "CREATE INDEX IF NOT EXISTS idx_imu_metrics_user_ts "
                "ON imu_metrics(user_id, ts DESC)",
                "CREATE INDEX IF NOT EXISTS idx_messages_pair_ts "
                "ON messages(sender_id, receiver_id, ts DESC)",
                "CREATE INDEX IF NOT EXISTS idx_messages_receiver_sender_read "
                "ON messages(receiver_id, sender_id, read_at)",
                "CREATE INDEX IF NOT EXISTS idx_weights_user_date_desc "
                "ON weights(user_id, date DESC)",
                "CREATE INDEX IF NOT EXISTS idx_nutrition_user_date_desc "
                "ON nutrition_logs(user_id, date DESC)",
            ]:
                try:
                    cur.execute(_sql)
                except Exception:
                    pass
            _set_db_version(con, 200)

        # --------------------------
        # Migracion 210: recuperacion de contraseña
        # --------------------------
        if current < 210:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS password_reset_tokens(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    request_ip TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )
            for _sql in [
                "CREATE INDEX IF NOT EXISTS idx_password_reset_user "
                "ON password_reset_tokens(user_id, expires_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_password_reset_hash "
                "ON password_reset_tokens(token_hash)",
            ]:
                try:
                    cur.execute(_sql)
                except Exception:
                    pass
            _set_db_version(con, 210)

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

        # ---------- Dispositivos físicos de sensores ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS sensor_devices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sensor_code TEXT NOT NULL,
            device_id TEXT NOT NULL,
            device_label TEXT,
            status TEXT DEFAULT 'paired',
            last_seen TEXT,
            firmware_version TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, device_id)
        )"""
        )
        # Migraciones suaves por si la tabla ya existía sin alguna columna
        for _col, _def in [
            ("device_label", "TEXT"),
            ("status", "TEXT DEFAULT 'paired'"),
            ("last_seen", "TEXT"),
            ("firmware_version", "TEXT"),
            ("notes", "TEXT"),
        ]:
            if not _has_column(con, "sensor_devices", _col):
                try:
                    cur.execute(f"ALTER TABLE sensor_devices ADD COLUMN {_col} {_def}")
                except sqlite3.OperationalError:
                    pass

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
    except Exception as exc:
        logging.getLogger("combatiq.db").exception("DB migration failed during init_db: %s", exc)


# ======================
# Users / Auth
# ======================

def _hash_pw(pw: str) -> bytes:
    try:
        import bcrypt
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    except ImportError:
        # bcrypt no disponible — usar PBKDF2-HMAC (seguro, stdlib)
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 260_000)
        return b"pbkdf2:" + salt.hex().encode() + b":" + dk.hex().encode()


def _check_pw(pw: str, hashed) -> bool:
    # PostgreSQL BYTEA llega como memoryview; SQLite BLOB llega como bytes.
    # Normalizar a bytes ANTES de pasar a bcrypt o startswith.
    if hashed is None:
        return False
    if isinstance(hashed, memoryview):
        hashed = bytes(hashed)
    elif isinstance(hashed, bytearray):
        hashed = bytes(hashed)
    elif isinstance(hashed, str):
        hashed = hashed.encode("utf-8")
    # Hashes PBKDF2 (fallback moderno)
    if hashed.startswith(b"pbkdf2:"):
        try:
            _, salt_hex, dk_hex = hashed.split(b":")
            salt = bytes.fromhex(salt_hex.decode())
            dk   = bytes.fromhex(dk_hex.decode())
            candidate = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 260_000)
            return hmac.compare_digest(candidate, dk)
        except Exception:
            return False
    # Hashes bcrypt (primario)
    try:
        import bcrypt
        return bcrypt.checkpw(pw.encode("utf-8"), hashed)
    except Exception:
        pass
    # Hashes SHA256 legacy (solo verificación, no se crean nuevos)
    legacy = hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8")
    return hmac.compare_digest(legacy, hashed)


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
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id,athlete_profile_json,avatar_url,onboarding_done "
            "FROM users WHERE email=?",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id", "athlete_profile_json", "avatar_url", "onboarding_done"]
    return {k: v for k, v in zip(cols, row)}


def get_user_by_id(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id,athlete_profile_json,avatar_url,onboarding_done "
            "FROM users WHERE id=?",
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id", "athlete_profile_json", "avatar_url", "onboarding_done"]
    return {k: v for k, v in zip(cols, row)}


def _find_user_by_email_casefold(con: sqlite3.Connection, email: str):
    email_clean = (email or "").strip()
    if not email_clean:
        return None
    cur = con.cursor()
    cur.execute(
        "SELECT id,name,email,role,sport FROM users WHERE LOWER(email)=LOWER(?)",
        (email_clean,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport"]
    return {k: v for k, v in zip(cols, row)}


def update_user_password(uid: int, new_password: str) -> bool:
    """Actualiza la contraseña de un usuario con el hash vigente del proyecto."""
    if not uid or not new_password:
        return False
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (_hash_pw(str(new_password)), int(uid)),
        )
        return cur.rowcount > 0


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def create_password_reset_token(email: str, request_ip: str = None,
                                ttl_minutes: int = 30) -> dict:
    """
    Crea un token temporal de recuperacion si el correo existe.

    La UI debe mostrar siempre un mensaje generico para no revelar si el correo
    esta registrado. En local/demo puede mostrar `token` para probar el flujo.
    """
    email_clean = (email or "").strip()
    now = datetime.utcnow()
    expires = now + timedelta(minutes=max(5, int(ttl_minutes or 30)))
    token = secrets.token_urlsafe(18)
    token_hash = _hash_reset_token(token)

    with _get_conn() as con:
        user = _find_user_by_email_casefold(con, email_clean)
        if not user:
            return {"created": False, "token": None, "expires_at": expires.isoformat()}

        cur = con.cursor()
        # Invalida tokens anteriores sin borrar auditoria.
        cur.execute(
            "UPDATE password_reset_tokens SET used_at=? "
            "WHERE user_id=? AND used_at IS NULL",
            (now.isoformat(), int(user["id"])),
        )
        cur.execute(
            """INSERT INTO password_reset_tokens
               (user_id, token_hash, created_at, expires_at, request_ip)
               VALUES(?,?,?,?,?)""",
            (
                int(user["id"]),
                token_hash,
                now.isoformat(),
                expires.isoformat(),
                (request_ip or "")[:64] or None,
            ),
        )
    return {
        "created": True,
        "token": token,
        "expires_at": expires.isoformat(),
        "user_id": user["id"],
    }


def reset_password_with_token(email: str, token: str, new_password: str) -> bool:
    """Valida token temporal y cambia la contraseña en una sola transaccion."""
    email_clean = (email or "").strip()
    token_clean = (token or "").strip()
    if not email_clean or not token_clean or not new_password:
        return False

    now = datetime.utcnow().isoformat()
    token_hash = _hash_reset_token(token_clean)
    with _get_conn() as con:
        user = _find_user_by_email_casefold(con, email_clean)
        if not user:
            return False

        cur = con.cursor()
        cur.execute(
            """SELECT id FROM password_reset_tokens
               WHERE user_id=? AND token_hash=? AND used_at IS NULL
                 AND expires_at >= ?
               ORDER BY id DESC LIMIT 1""",
            (int(user["id"]), token_hash, now),
        )
        row = cur.fetchone()
        if not row:
            return False

        cur.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (_hash_pw(str(new_password)), int(user["id"])),
        )
        cur.execute(
            "UPDATE password_reset_tokens SET used_at=? WHERE id=?",
            (now, int(row[0])),
        )
    return True


def save_avatar_url(uid: int, data_url: str) -> None:
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET avatar_url=? WHERE id=?", (data_url, uid))


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


def list_athletes_for_coach(coach_id: int, sport: str = None):
    """
    Lista deportistas asignados a un coach concreto vía users.coach_id.
    Si se pasa `sport`, solo devuelve deportistas de ese deporte.
    """
    with _get_conn() as con:
        cur = con.cursor()
        if sport:
            cur.execute(
                "SELECT id,name,role,sport,created_at,coach_id,avatar_url "
                "FROM users WHERE role='deportista' AND coach_id=? AND sport=? ORDER BY id DESC",
                (coach_id, sport),
            )
        else:
            cur.execute(
                "SELECT id,name,role,sport,created_at,coach_id,avatar_url "
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
                "avatar_url": r[6] if len(r) > 6 else None,
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
        return cur.lastrowid


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


def list_my_athletes(coach_id: int, sport: str = None):
    """
    Devuelve deportistas adoptados por un coach (JOIN coach_athletes + users).
    Si se pasa sport, filtra por deporte en la propia query.
    """
    with _get_conn() as con:
        cur = con.cursor()
        if sport:
            cur.execute(
                """
                SELECT u.id, u.name, u.role, u.sport, u.created_at, u.coach_id, u.avatar_url
                FROM coach_athletes ca
                JOIN users u ON u.id = ca.athlete_id
                WHERE ca.coach_id = ? AND u.sport = ?
                ORDER BY u.name
                """,
                (int(coach_id), sport),
            )
        else:
            cur.execute(
                """
                SELECT u.id, u.name, u.role, u.sport, u.created_at, u.coach_id, u.avatar_url
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
            "avatar_url": r[6] if len(r) > 6 else None,
        }
        for r in rows
    ]


def list_roster_for_coach(coach_id: int, sport: str = None):
    """
    Roster unificado.
    Une adopción (coach_athletes) + legacy (users.coach_id) sin duplicados.
    Si se pasa sport, filtra en DB en ambas fuentes.
    """
    out = []
    seen = set()

    try:
        a1 = list_my_athletes(int(coach_id), sport=sport) or []
    except Exception:
        a1 = []

    try:
        a2 = list_athletes_for_coach(int(coach_id), sport=sport) or []
    except Exception:
        a2 = []

    for a in (a1 + a2):
        aid = a.get("id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        out.append(a)

    return out


def coach_has_athlete(coach_id: int, athlete_id: int, sport: str = None) -> bool:
    """
    Valida pertenencia coach-atleta usando roster unificado:
    adopcion en coach_athletes y relacion legacy users.coach_id.
    """
    if not coach_id or not athlete_id:
        return False
    roster = list_roster_for_coach(int(coach_id), sport=sport)
    return any(int(a.get("id")) == int(athlete_id) for a in roster if a.get("id") is not None)


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


def team_belongs_to_coach(team_id: int, coach_id: int) -> bool:
    if not team_id or not coach_id:
        return False
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT 1 FROM teams WHERE id=? AND coach_id=? LIMIT 1",
            (int(team_id), int(coach_id)),
        )
        return cur.fetchone() is not None


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


def rename_session(session_id: int, new_name: str) -> bool:
    """Update the notes/name field of a session. Returns True if found and updated."""
    name = str(new_name or "").strip()[:200]
    if not name:
        return False
    with _get_conn() as con:
        cur = con.execute(
            "UPDATE sessions SET notes = ? WHERE id = ?",
            (name, int(session_id)),
        )
        return cur.rowcount > 0


def delete_session(session_id: int) -> bool:
    """Delete a session and its associated ECG files + IMU metrics records.
    Physical files on disk are also removed. Returns True if the session existed."""
    import os as _os
    sid = int(session_id)
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT id FROM sessions WHERE id=?", (sid,))
        if not cur.fetchone():
            return False
        # Remove ECG physical files
        if _has_column(con, "ecg_files", "session_id"):
            cur.execute(
                "DELETE FROM ecg_metrics WHERE ecg_file_id IN "
                "(SELECT id FROM ecg_files WHERE session_id=?)",
                (sid,),
            )
            cur.execute("SELECT filename FROM ecg_files WHERE session_id=?", (sid,))
            for (fname,) in cur.fetchall():
                for base in ["data/ecg", _os.path.join(_os.path.dirname(__file__), "data", "ecg")]:
                    fpath = _os.path.join(base, fname)
                    if _os.path.exists(fpath):
                        try:
                            _os.remove(fpath)
                        except Exception:
                            pass
            cur.execute("DELETE FROM ecg_files WHERE session_id=?", (sid,))
        # Remove IMU sidecar + metrics
        try:
            cur.execute("SELECT filename FROM imu_metrics WHERE session_id=?", (sid,))
            for (stem,) in cur.fetchall():
                for base in ["data/ecg", _os.path.join(_os.path.dirname(__file__), "data", "ecg")]:
                    jpath = _os.path.join(base, f"{stem}.json")
                    if _os.path.exists(jpath):
                        try:
                            _os.remove(jpath)
                        except Exception:
                            pass
            cur.execute("DELETE FROM imu_metrics WHERE session_id=?", (sid,))
        except Exception:
            pass
        cur.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return True


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


def list_sessions_for_team(athlete_ids: list, limit: int = 200) -> list:
    """Devuelve sesiones de varios atletas en una sola query. Reemplaza el loop N+1."""
    if not athlete_ids:
        return []
    placeholders = ",".join("?" * len(athlete_ids))
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            f"SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at "
            f"FROM sessions WHERE athlete_id IN ({placeholders}) "
            f"ORDER BY datetime(ts_start) DESC LIMIT ?",
            [*athlete_ids, int(limit)],
        )
        rows = cur.fetchall()
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return [{k: v for k, v in zip(cols, r)} for r in rows]


def get_athlete_profiles_bulk(athlete_ids: list) -> dict:
    """Devuelve {athlete_id: profile_dict} para una lista de IDs. 1 query en lugar de N."""
    if not athlete_ids:
        return {}
    placeholders = ",".join("?" * len(athlete_ids))
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            f"SELECT id, athlete_profile_json FROM users WHERE id IN ({placeholders})",
            athlete_ids,
        )
        rows = cur.fetchall()
    result = {}
    base = _default_athlete_profile()
    for uid, raw in rows:
        profile = base.copy()
        try:
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                for k in profile.keys():
                    v = data.get(k)
                    profile[k] = v if v not in ("", [], None) else None
        except Exception:
            pass
        result[uid] = profile
    return result


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


def get_user_sensors_bulk(user_ids: list) -> dict:
    ids = []
    seen = set()
    for raw in user_ids or []:
        try:
            uid = int(raw)
        except Exception:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        ids.append(uid)
    if not ids:
        return {}

    out = {uid: [] for uid in ids}
    with _get_conn() as con:
        cur = con.cursor()
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""SELECT user_id, sensor_code
                    FROM user_sensors
                    WHERE user_id IN ({placeholders})
                    ORDER BY user_id, sensor_code""",
                chunk,
            )
            for uid, sensor_code in cur.fetchall():
                out.setdefault(int(uid), []).append(sensor_code)
    return out


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


def add_user_sensor(uid: int, code: str) -> bool:
    """Asigna un sensor a un usuario sin borrar los sensores existentes."""
    clean = str(code or "").strip().upper()
    if not uid or not clean:
        return False
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO user_sensors(user_id,sensor_code) VALUES(?,?)",
            (int(uid), clean),
        )
        return cur.rowcount > 0


# ======================
# Dispositivos físicos
# ======================

def register_device(user_id: int, sensor_code: str, device_id: str,
                    device_label: str = None, firmware_version: str = None) -> int:
    """
    Registra (o actualiza) un dispositivo físico para un usuario.
    Devuelve el id de la fila en sensor_devices.
    """
    now = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO sensor_devices
               (user_id, sensor_code, device_id, device_label, status, firmware_version, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, device_id) DO UPDATE SET
                 sensor_code      = excluded.sensor_code,
                 device_label     = COALESCE(excluded.device_label, sensor_devices.device_label),
                 status           = 'paired',
                 firmware_version = COALESCE(excluded.firmware_version, sensor_devices.firmware_version)
            """,
            (user_id, sensor_code, device_id, device_label, "paired", firmware_version, now),
        )
        cur.execute(
            "SELECT id FROM sensor_devices WHERE user_id=? AND device_id=?",
            (user_id, device_id),
        )
        row = cur.fetchone()
        return row[0] if row else -1


def update_device_last_seen(device_id: str, user_id: int) -> bool:
    """
    Actualiza last_seen y marca status='connected'.
    Llamado por el endpoint /api/sensor-ping.
    Devuelve True si encontró el dispositivo.
    """
    now = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """UPDATE sensor_devices SET last_seen=?, status='connected'
               WHERE device_id=? AND user_id=?""",
            (now, device_id, user_id),
        )
        return cur.rowcount > 0


def get_user_devices(user_id: int) -> list:
    """
    Devuelve todos los dispositivos registrados para un usuario,
    con status calculado según last_seen:
      - 'connected'     si last_seen < 5 min
      - 'idle'          si last_seen entre 5 min y 1 h
      - 'offline'       si last_seen > 1 h
      - 'paired'        si nunca ha pingado (last_seen IS NULL)
    """
    from datetime import timezone
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """SELECT id, user_id, sensor_code, device_id, device_label,
                      status, last_seen, firmware_version, notes, created_at
               FROM sensor_devices WHERE user_id=? ORDER BY sensor_code, created_at""",
            (user_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    now = datetime.utcnow()
    for r in rows:
        ls = r.get("last_seen")
        if not ls:
            r["computed_status"] = "paired"
        else:
            try:
                delta = (now - datetime.fromisoformat(ls)).total_seconds()
                if delta < 300:
                    r["computed_status"] = "connected"
                elif delta < 3600:
                    r["computed_status"] = "idle"
                else:
                    r["computed_status"] = "offline"
            except Exception:
                r["computed_status"] = "paired"
    return rows


def get_user_devices_bulk(user_ids: list) -> dict:
    ids = []
    seen = set()
    for raw in user_ids or []:
        try:
            uid = int(raw)
        except Exception:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        ids.append(uid)
    if not ids:
        return {}

    out = {uid: [] for uid in ids}
    now = datetime.utcnow()
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""SELECT id, user_id, sensor_code, device_id, device_label,
                           status, last_seen, firmware_version, notes, created_at
                    FROM sensor_devices
                    WHERE user_id IN ({placeholders})
                    ORDER BY user_id, sensor_code, created_at""",
                chunk,
            )
            for row in cur.fetchall():
                item = dict(row)
                ls = item.get("last_seen")
                if not ls:
                    item["computed_status"] = "paired"
                else:
                    try:
                        delta = (now - datetime.fromisoformat(ls)).total_seconds()
                        if delta < 300:
                            item["computed_status"] = "connected"
                        elif delta < 3600:
                            item["computed_status"] = "idle"
                        else:
                            item["computed_status"] = "offline"
                    except Exception:
                        item["computed_status"] = "paired"
                out.setdefault(int(item["user_id"]), []).append(item)
    return out


def get_device_by_id(device_id: str, user_id: int) -> dict:
    """Devuelve info del dispositivo o None si no existe."""
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM sensor_devices WHERE device_id=? AND user_id=?",
            (device_id, user_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_device(device_id: str, user_id: int) -> bool:
    """Elimina el emparejamiento de un dispositivo. Devuelve True si lo encontró."""
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM sensor_devices WHERE device_id=? AND user_id=?",
            (device_id, user_id),
        )
        return cur.rowcount > 0


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


def get_last_ecg_metrics_bulk(user_ids: list) -> dict:
    ids = []
    seen = set()
    for raw in user_ids or []:
        try:
            uid = int(raw)
        except Exception:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        ids.append(uid)
    if not ids:
        return {}

    out = {}
    with _get_conn() as con:
        cur = con.cursor()
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""
                SELECT f.user_id, m.bpm, m.sdnn, m.rmssd
                FROM ecg_metrics m
                JOIN ecg_files f ON f.id = m.ecg_file_id
                JOIN (
                    SELECT f2.user_id, MAX(m2.id) AS max_id
                    FROM ecg_metrics m2
                    JOIN ecg_files f2 ON f2.id = m2.ecg_file_id
                    WHERE f2.user_id IN ({placeholders})
                    GROUP BY f2.user_id
                ) latest ON latest.max_id = m.id
                """,
                chunk,
            )
            for row in cur.fetchall():
                out[int(row[0])] = {"bpm": row[1], "sdnn": row[2], "rmssd": row[3]}
    return out


def get_latest_ecg_metrics_for_file(ecg_file_id: int):
    if not ecg_file_id:
        return None
    metrics = list_latest_ecg_metrics_for_files([int(ecg_file_id)])
    return metrics.get(int(ecg_file_id))


def list_latest_ecg_metrics_for_files(file_ids: list) -> dict:
    """
    Devuelve la ultima metrica guardada por archivo ECG en una sola pasada.
    Evita N queries y recalculos pesados de CSV en vistas de comparacion/reportes.
    """
    ids = []
    seen = set()
    for raw in file_ids or []:
        try:
            fid = int(raw)
        except Exception:
            continue
        if fid in seen:
            continue
        seen.add(fid)
        ids.append(fid)
    if not ids:
        return {}

    out = {}
    with _get_conn() as con:
        cur = con.cursor()
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""
                SELECT m.ecg_file_id, m.bpm, m.sdnn, m.rmssd, m.n_peaks, m.created_at
                FROM ecg_metrics m
                JOIN (
                    SELECT ecg_file_id, MAX(id) AS max_id
                    FROM ecg_metrics
                    WHERE ecg_file_id IN ({placeholders})
                    GROUP BY ecg_file_id
                ) latest ON latest.max_id = m.id
                """,
                chunk,
            )
            for row in cur.fetchall():
                out[int(row[0])] = {
                    "bpm": row[1],
                    "sdnn": row[2],
                    "rmssd": row[3],
                    "n_peaks": row[4],
                    "created_at": row[5],
                }
    return out


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


def list_questionnaires(uid: int, limit: int | None = None):
    # ✅ FIX: fetchall dentro del with
    with _get_conn() as con:
        cur = con.cursor()
        sql = (
            "SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min "
            "FROM questionnaires WHERE user_id=? ORDER BY id DESC"
        )
        params = (uid,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (uid, int(limit))
        cur.execute(sql, params)
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


def list_questionnaires_bulk(user_ids: list) -> dict:
    """Single query to fetch questionnaires for multiple users. Returns {user_id: [rows]}."""
    if not user_ids:
        return {}
    ids = [int(uid) for uid in user_ids]
    placeholders = ",".join("?" * len(ids))
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            f"SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min "
            f"FROM questionnaires WHERE user_id IN ({placeholders}) ORDER BY id DESC",
            ids,
        )
        rows = cur.fetchall()
    out: dict = {}
    for r in rows:
        uid = r[1]
        if uid not in out:
            out[uid] = []
        out[uid].append({
            "id": r[0], "user_id": r[1], "ts": r[2], "answers_json": r[3],
            "wellness_score": r[4], "rpe": r[5], "duration_min": r[6],
        })
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


def add_nutrition_entry(user_id: int, date: str, adherence_pct: float, kcal: float = None,
                        note: str = None, protein_g: float = None, carbs_g: float = None,
                        fats_g: float = None, water_ml: float = None):
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
            INSERT INTO nutrition_logs(user_id, date, adherence_pct, kcal, note, created_at,
                                       protein_g, carbs_g, fats_g, water_ml)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(user_id),
                str(date),
                float(adherence_pct),
                float(kcal) if kcal is not None else None,
                (note or "").strip() or None,
                datetime.utcnow().isoformat(),
                float(protein_g) if protein_g is not None else None,
                float(carbs_g)   if carbs_g   is not None else None,
                float(fats_g)    if fats_g     is not None else None,
                float(water_ml)  if water_ml   is not None else None,
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
            SELECT id, user_id, date, adherence_pct, kcal, note, created_at,
                   protein_g, carbs_g, fats_g, water_ml
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
            "protein_g": r[7] if len(r) > 7 else None,
            "carbs_g":   r[8] if len(r) > 8 else None,
            "fats_g":    r[9] if len(r) > 9 else None,
            "water_ml":  r[10] if len(r) > 10 else None,
            "created_at": r[6],
        }
        for r in rows
    ]


def get_latest_nutrition_entry(user_id: int):
    rows = list_nutrition_entries(int(user_id), limit=1)
    return rows[0] if rows else None


def upsert_nutrition_feedback(athlete_id: int, coach_id: int, week_start: str, note: str = None):
    """Guarda o actualiza la validación semanal de dieta de un coach sobre un atleta."""
    now = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO nutrition_coach_feedback
                (athlete_id, coach_id, week_start, note, validated_at, created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(athlete_id, coach_id, week_start)
            DO UPDATE SET note=excluded.note, validated_at=excluded.validated_at
            """,
            (int(athlete_id), int(coach_id), str(week_start),
             (note or "").strip() or None, now, now),
        )
        return cur.lastrowid


def get_latest_nutrition_feedback(athlete_id: int, coach_id: int = None):
    """Devuelve la validación más reciente para un atleta (opcionalmente filtrada por coach)."""
    with _get_conn() as con:
        cur = con.cursor()
        if coach_id:
            cur.execute(
                """
                SELECT f.id, f.athlete_id, f.coach_id, f.week_start,
                       f.note, f.validated_at, f.created_at, u.name AS coach_name
                FROM nutrition_coach_feedback f
                JOIN users u ON u.id = f.coach_id
                WHERE f.athlete_id=? AND f.coach_id=?
                ORDER BY f.week_start DESC LIMIT 1
                """,
                (int(athlete_id), int(coach_id)),
            )
        else:
            cur.execute(
                """
                SELECT f.id, f.athlete_id, f.coach_id, f.week_start,
                       f.note, f.validated_at, f.created_at, u.name AS coach_name
                FROM nutrition_coach_feedback f
                JOIN users u ON u.id = f.coach_id
                WHERE f.athlete_id=?
                ORDER BY f.week_start DESC LIMIT 1
                """,
                (int(athlete_id),),
            )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


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

def save_imu_metrics(user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g,
                     session_id: int = None, sensor_type: str = None,
                     mean_ang_vel: float = None, max_ang_vel: float = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        cols = ["user_id", "filename", "n_hits", "hits_per_min", "mean_int_g", "max_int_g"]
        vals = [int(user_id), filename, int(n_hits),
                float(hits_per_min), float(mean_int_g), float(max_int_g)]
        if _has_column(con, "imu_metrics", "session_id"):
            cols.append("session_id"); vals.append(session_id)
        if _has_column(con, "imu_metrics", "sensor_type"):
            cols.append("sensor_type"); vals.append(sensor_type)
        if _has_column(con, "imu_metrics", "mean_ang_vel") and mean_ang_vel is not None:
            cols.append("mean_ang_vel"); vals.append(float(mean_ang_vel))
        if _has_column(con, "imu_metrics", "max_ang_vel") and max_ang_vel is not None:
            cols.append("max_ang_vel"); vals.append(float(max_ang_vel))
        placeholders = ", ".join("?" * len(vals))
        cur.execute(
            f"INSERT INTO imu_metrics ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )


def list_imu_metrics(user_id, sensor_type: str = None):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        extra_cols = ""
        for col in ("session_id", "sensor_type", "mean_ang_vel", "max_ang_vel"):
            if _has_column(con, "imu_metrics", col):
                extra_cols += f", {col}"
        where = "WHERE user_id = ?"
        params = [int(user_id)]
        if sensor_type and _has_column(con, "imu_metrics", "sensor_type"):
            where += " AND sensor_type = ?"
            params.append(sensor_type)
        cur.execute(
            f"SELECT id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g{extra_cols} "
            f"FROM imu_metrics {where} ORDER BY datetime(ts) DESC",
            params,
        )
        return list(_dicts(cur))


def list_imu_metrics_by_session(session_id: int, sensor_type: str = None):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "imu_metrics", "session_id"):
            return []
        extra_cols = ""
        for col in ("sensor_type", "mean_ang_vel", "max_ang_vel"):
            if _has_column(con, "imu_metrics", col):
                extra_cols += f", {col}"
        where = "WHERE session_id = ?"
        params = [int(session_id)]
        if sensor_type and _has_column(con, "imu_metrics", "sensor_type"):
            where += " AND sensor_type = ?"
            params.append(sensor_type)
        cur.execute(
            f"SELECT id, user_id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g, session_id{extra_cols} "
            f"FROM imu_metrics {where} ORDER BY datetime(ts) DESC",
            params,
        )
        return list(_dicts(cur))


# ── Sensor Sessions ────────────────────────────────────────────────────────────

def open_sensor_session(session_id: int, sensor_code: str,
                        device_id: int | None = None, ts_start: str | None = None) -> int:
    """Abre una nueva sensor_session y devuelve su id."""
    from datetime import datetime as _dt
    ts = ts_start or _dt.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.execute(
            "INSERT INTO sensor_sessions (session_id, sensor_code, device_id, ts_start) VALUES (?, ?, ?, ?)",
            (int(session_id), sensor_code, device_id, ts),
        )
        return cur.lastrowid


def close_sensor_session(sensor_session_id: int,
                         sample_count: int = 0, status: str = "complete") -> None:
    """Cierra una sensor_session con ts_end=ahora y estado final."""
    from datetime import datetime as _dt
    with _get_conn() as con:
        con.execute(
            "UPDATE sensor_sessions SET ts_end=?, status=?, sample_count=? WHERE id=?",
            (_dt.utcnow().isoformat(), status, int(sample_count), int(sensor_session_id)),
        )


def record_sensor_sample(session_id: int, sensor_code: str,
                         device_id: int | None = None) -> None:
    """Registra un paquete recibido: crea la sensor_session si no existe, o incrementa su contador."""
    with _get_conn() as con:
        row = con.execute(
            "SELECT id FROM sensor_sessions "
            "WHERE session_id=? AND sensor_code=? AND status='collecting' "
            "ORDER BY id DESC LIMIT 1",
            (int(session_id), sensor_code),
        ).fetchone()
        if row:
            con.execute(
                "UPDATE sensor_sessions SET sample_count = sample_count + 1 WHERE id=?",
                (row[0],),
            )
        else:
            from datetime import datetime as _dt
            con.execute(
                "INSERT INTO sensor_sessions (session_id, sensor_code, device_id, sample_count) VALUES (?, ?, ?, 1)",
                (int(session_id), sensor_code, device_id),
            )


def list_sensor_sessions(session_id: int) -> list:
    """Devuelve todas las sensor_sessions de una sesión de entrenamiento."""
    with _get_conn() as con:
        cur = con.execute(
            "SELECT id, session_id, device_id, sensor_code, ts_start, ts_end, "
            "status, sample_count, notes, created_at "
            "FROM sensor_sessions WHERE session_id=? ORDER BY ts_start",
            (int(session_id),),
        )
        return list(_dicts(cur))


def get_sensor_data_summary(session_id: int) -> dict:
    """Devuelve un dict por sensor_code con totales acumulados de todas sus sensor_sessions."""
    summary: dict = {}
    for r in list_sensor_sessions(session_id):
        code = r["sensor_code"]
        if code not in summary:
            summary[code] = {
                "sample_count": r.get("sample_count") or 0,
                "ts_start":     r["ts_start"],
                "ts_end":       r["ts_end"],
                "status":       r["status"],
            }
        else:
            summary[code]["sample_count"] += r.get("sample_count") or 0
            if r["ts_start"] < summary[code]["ts_start"]:
                summary[code]["ts_start"] = r["ts_start"]
            if r["ts_end"] and (
                not summary[code]["ts_end"]
                or r["ts_end"] > summary[code]["ts_end"]
            ):
                summary[code]["ts_end"] = r["ts_end"]
            if r["status"] == "collecting":
                summary[code]["status"] = "collecting"
    return summary


# ─── fin sensor sessions ────────────────────────────────────────────────────


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

    return _weekly_load_summary_from_rows(rows_cur, rows_prev, n_sessions)


def _weekly_load_summary_from_rows(rows_cur: list, rows_prev: list, n_sessions: int) -> dict:
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


def get_weekly_load_summary_bulk(user_ids: list, days: int = 7) -> dict:
    """Carga semanal para multiples atletas sin N+1 queries."""
    import datetime
    ids = []
    seen = set()
    for raw in user_ids or []:
        try:
            uid = int(raw)
        except Exception:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        ids.append(uid)
    if not ids:
        return {}

    now = datetime.datetime.utcnow()
    cutoff_cur = (now - datetime.timedelta(days=days)).isoformat()
    cutoff_prev = (now - datetime.timedelta(days=days * 2)).isoformat()
    placeholders = ",".join("?" * len(ids))

    rows_cur_by_uid = {uid: [] for uid in ids}
    rows_prev_by_uid = {uid: [] for uid in ids}
    sessions_by_uid = {uid: 0 for uid in ids}

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            f"""
            SELECT user_id, wellness_score, rpe, duration_min, ts
            FROM questionnaires
            WHERE user_id IN ({placeholders})
              AND datetime(ts) >= datetime(?)
            ORDER BY user_id, ts DESC
            """,
            [*ids, cutoff_prev],
        )
        for row in cur.fetchall():
            item = {
                "wellness_score": row[1],
                "rpe": row[2],
                "duration_min": row[3],
            }
            if row[4] and row[4] >= cutoff_cur:
                rows_cur_by_uid.setdefault(int(row[0]), []).append(item)
            else:
                rows_prev_by_uid.setdefault(int(row[0]), []).append(item)

        cur.execute(
            f"""
            SELECT athlete_id, COUNT(*) AS n
            FROM sessions
            WHERE athlete_id IN ({placeholders})
              AND datetime(ts_start) >= datetime(?)
            GROUP BY athlete_id
            """,
            [*ids, cutoff_cur],
        )
        for row in cur.fetchall():
            sessions_by_uid[int(row[0])] = row[1]

    return {
        uid: _weekly_load_summary_from_rows(
            rows_cur_by_uid.get(uid, []),
            rows_prev_by_uid.get(uid, []),
            sessions_by_uid.get(uid, 0),
        )
        for uid in ids
    }


def get_team_weekly_summary(coach_id: int):
    """Devuelve lista de {atleta + resumen semanal} para el roster del coach,
    filtrado por el mismo deporte que el coach."""
    coach = get_user_by_id(int(coach_id))
    coach_sport = (coach.get("sport") or "").strip() if coach else ""

    athletes = list_roster_for_coach(coach_id, sport=coach_sport or None)
    summaries = get_weekly_load_summary_bulk([a.get("id") for a in athletes])
    result = []
    for a in athletes:
        aid = a.get("id")
        if not aid:
            continue
        summary = summaries.get(int(aid)) or get_weekly_load_summary(int(aid))
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
        ("demo-box-luis@combatiq.app",   "Luis Peña",    "Box", "Competitivo",      "-69 kg",  "hombro derecho",   "Próximas 3-4 semanas"),
        ("demo-box-sofia@combatiq.app",  "Sofía Vega",   "Box", "Alto rendimiento", "-60 kg",  "muñeca izquierda", "Próximas 1-2 semanas"),
        ("demo-box-marco@combatiq.app",  "Marco Díaz",   "Box", "Iniciación",       "-75 kg",  "Sin zona crítica", "Sin competencia cercana"),
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
                (COACH_NAME, COACH_EMAIL, "coach", "Box",
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


# ======================
# Announcements (comunicados in-app)
# ======================

def add_announcement(coach_id: int, sport: str, title: str, body: str = "", pinned: bool = False) -> int:
    """Crea un comunicado del coach. Devuelve el id insertado."""
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO announcements(coach_id, sport, title, body, pinned, created_at) VALUES(?,?,?,?,?,?)",
            (int(coach_id), sport or "", title, body or "", 1 if pinned else 0, ts),
        )
        return cur.lastrowid


def list_announcements_for_coach(coach_id: int, limit: int = 30) -> list:
    """Listado de comunicados enviados por este coach (más recientes primero)."""
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM announcements WHERE coach_id=? ORDER BY pinned DESC, created_at DESC LIMIT ?",
            (int(coach_id), limit),
        )
        return [dict(r) for r in cur.fetchall()]


def list_announcements_for_sport(sport: str, limit: int = 20) -> list:
    """Comunicados visibles para deportistas de un deporte (más recientes primero)."""
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT a.*, u.name AS coach_name FROM announcements a "
            "LEFT JOIN users u ON u.id = a.coach_id "
            "WHERE a.sport=? ORDER BY a.pinned DESC, a.created_at DESC LIMIT ?",
            (sport or "", limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ======================
# Notification preferences
# ======================

# ======================
# Competition events
# ======================

def add_competition_event(user_id: int, name: str, event_date: str, sport: str = None,
                          target_weight: float = None, location: str = None,
                          notes: str = None) -> int:
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO competition_events
               (user_id, name, event_date, sport, target_weight, location, notes, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (int(user_id), name, event_date, sport or "", target_weight, location or "", notes or "", ts),
        )
        return cur.lastrowid


def list_competition_events(user_id: int, upcoming_only: bool = False, limit: int = 20) -> list:
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        if upcoming_only:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            cur.execute(
                "SELECT * FROM competition_events WHERE user_id=? AND event_date >= ? "
                "ORDER BY event_date ASC LIMIT ?",
                (int(user_id), today, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM competition_events WHERE user_id=? ORDER BY event_date DESC LIMIT ?",
                (int(user_id), limit),
            )
        return [dict(r) for r in cur.fetchall()]


def get_next_competition(user_id: int) -> dict | None:
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cur.execute(
            "SELECT * FROM competition_events WHERE user_id=? AND event_date >= ? "
            "ORDER BY event_date ASC LIMIT 1",
            (int(user_id), today),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_competition_event(event_id: int, user_id: int) -> bool:
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM competition_events WHERE id=? AND user_id=?",
            (int(event_id), int(user_id)),
        )
        return cur.rowcount > 0


# ── Competition results (logros / trofeos) ──────────────────────────────────

def add_competition_result(user_id: int, name: str, event_date: str,
                           medal: str = "participant", category: str = None,
                           location: str = None, notes: str = None) -> int:
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO competition_results
               (user_id, name, event_date, medal, category, location, notes, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (int(user_id), name, event_date,
             medal or "participant", category or "", location or "", notes or "", ts),
        )
        return cur.lastrowid


def list_competition_results(user_id: int) -> list:
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM competition_results WHERE user_id=? ORDER BY event_date DESC",
            (int(user_id),),
        )
        return [dict(r) for r in cur.fetchall()]


def delete_competition_result(result_id: int, user_id: int) -> bool:
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM competition_results WHERE id=? AND user_id=?",
            (int(result_id), int(user_id)),
        )
        return cur.rowcount > 0


def add_session_note(coach_id: int, athlete_id: int, note: str) -> int:
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO session_notes (coach_id, athlete_id, note, created_at) VALUES (?,?,?,?)",
            (int(coach_id), int(athlete_id), note.strip(), ts),
        )
        return cur.lastrowid


def list_session_notes(athlete_id: int, coach_id: int = None, limit: int = 10) -> list:
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        if coach_id:
            cur.execute(
                "SELECT * FROM session_notes WHERE athlete_id=? AND coach_id=? ORDER BY created_at DESC LIMIT ?",
                (int(athlete_id), int(coach_id), limit),
            )
        else:
            cur.execute(
                "SELECT * FROM session_notes WHERE athlete_id=? ORDER BY created_at DESC LIMIT ?",
                (int(athlete_id), limit),
            )
        return [dict(r) for r in cur.fetchall()]


def get_team_wellness_7d(coach_id: int, sport: str = None) -> list:
    """Devuelve [{name, dates:[str], scores:[float]}] para los últimos 7 días."""
    from datetime import timedelta, date as _date
    today = _date.today()
    cutoff = (today - timedelta(days=6)).isoformat()

    athletes = list_roster_for_coach(int(coach_id), sport=sport) or []
    athlete_ids = [int(a["id"]) for a in athletes if a.get("id") is not None]
    if not athlete_ids:
        return []

    placeholders = ",".join("?" * len(athlete_ids))
    rows_by_uid = {aid: [] for aid in athlete_ids}
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            f"""SELECT user_id, DATE(ts) as day, AVG(wellness_score) as avg_w
               FROM questionnaires
               WHERE user_id IN ({placeholders})
                 AND DATE(ts) >= ?
                 AND wellness_score IS NOT NULL
               GROUP BY user_id, DATE(ts)
               ORDER BY user_id, day ASC""",
            [*athlete_ids, cutoff],
        )
        for row in cur.fetchall():
            rows_by_uid.setdefault(int(row["user_id"]), []).append(row)

    result = []
    for a in athletes:
        rows = rows_by_uid.get(int(a["id"]), [])
        if rows:
            result.append({
                "name":   a["name"],
                "dates":  [r["day"] for r in rows],
                "scores": [round(float(r["avg_w"]), 1) for r in rows],
            })
    return result


def get_red_streak_athletes(coach_id: int, days: int = 3, threshold: float = 50.0,
                             sport: str = None) -> list:
    """Devuelve atletas del coach con wellness < threshold en los últimos `days` días consecutivos."""
    from datetime import timedelta
    today = datetime.utcnow().date()
    cutoff = (today - timedelta(days=days - 1)).isoformat()

    athletes = list_roster_for_coach(int(coach_id), sport=sport) or []
    athlete_ids = [int(a["id"]) for a in athletes if a.get("id") is not None]
    if not athlete_ids:
        return []

    placeholders = ",".join("?" * len(athlete_ids))
    rows_by_uid = {aid: [] for aid in athlete_ids}
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            f"""SELECT user_id, DATE(ts) as day, MIN(wellness_score) as min_w
               FROM questionnaires
               WHERE user_id IN ({placeholders}) AND DATE(ts) >= ?
               GROUP BY user_id, DATE(ts)
               ORDER BY user_id, day DESC""",
            [*athlete_ids, cutoff],
        )
        for row in cur.fetchall():
            rows_by_uid.setdefault(int(row["user_id"]), []).append(row)

    red_athletes = []
    for a in athletes:
        rows = rows_by_uid.get(int(a["id"]), [])
        if len(rows) >= days and all(float(r["min_w"] or 100) < threshold for r in rows[:days]):
            a["consecutive_red"] = days
            a["last_score"] = float(rows[0]["min_w"] or 0)
            red_athletes.append(a)
    return red_athletes


def mark_onboarding_done(user_id: int) -> None:
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET onboarding_done=1 WHERE id=?", (int(user_id),))


def get_notification_prefs(user_id: int) -> dict:
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM notification_prefs WHERE user_id=?", (int(user_id),))
        row = cur.fetchone()
    if row:
        return dict(row)
    return {
        "user_id": user_id,
        "low_wellness_alert": 1,
        "announcement_notify": 1,
        "checkin_reminder": 0,
        "email_override": None,
    }


def save_notification_prefs(user_id: int, low_wellness_alert: bool,
                             announcement_notify: bool, checkin_reminder: bool,
                             email_override: str = None) -> None:
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO notification_prefs
               (user_id, low_wellness_alert, announcement_notify, checkin_reminder, email_override, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 low_wellness_alert=excluded.low_wellness_alert,
                 announcement_notify=excluded.announcement_notify,
                 checkin_reminder=excluded.checkin_reminder,
                 email_override=excluded.email_override,
                 updated_at=excluded.updated_at""",
            (int(user_id),
             1 if low_wellness_alert else 0,
             1 if announcement_notify else 0,
             1 if checkin_reminder else 0,
             email_override or None,
             ts),
        )


def delete_announcement(ann_id: int, coach_id: int) -> bool:
    """Elimina un comunicado si pertenece a este coach."""
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM announcements WHERE id=? AND coach_id=?",
            (int(ann_id), int(coach_id)),
        )
        return cur.rowcount > 0


def get_readiness_score(uid: int) -> dict:
    """
    Competition readiness score 0-100 for an athlete.

    Components:
      Wellness average 7d  → 40 pts
      Wellness trend       → 20 pts  (improving / stable / declining)
      Load manageability   → 20 pts  (green/yellow/red flag)
      Competition timing   → 20 pts  (days to next event)

    Returns dict: score, label, color, breakdown, days_to_comp, next_event_name
    """
    from datetime import timedelta

    summary = get_weekly_load_summary(uid, days=7)
    wellness_avg  = summary.get("wellness_avg")     # None or 0-100
    trend         = summary.get("trend", "stable")  # up/down/stable
    flag          = summary.get("flag", "gray")

    # ── Prior week wellness for trend comparison ──────────────────────────
    now       = datetime.utcnow()
    cutoff_7  = (now - timedelta(days=7)).isoformat()
    cutoff_14 = (now - timedelta(days=14)).isoformat()
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT AVG(wellness_score) as wa FROM questionnaires "
            "WHERE user_id=? AND datetime(ts) >= datetime(?) AND datetime(ts) < datetime(?)",
            (int(uid), cutoff_14, cutoff_7),
        )
        row = cur.fetchone()
    wellness_prev = float(row["wa"]) if row and row["wa"] is not None else None

    if wellness_avg is not None and wellness_prev is not None:
        w_delta = wellness_avg - wellness_prev
        w_trend_pts = 20 if w_delta > 5 else (10 if w_delta >= -5 else 0)
    elif wellness_avg is not None:
        w_trend_pts = 10   # no prior data — neutral
    else:
        w_trend_pts = 0

    # ── Wellness average component ────────────────────────────────────────
    w_avg_pts = round((wellness_avg / 100) * 40) if wellness_avg is not None else 10

    # ── Load manageability ────────────────────────────────────────────────
    load_pts = {"green": 20, "yellow": 10, "red": 0, "gray": 10}.get(flag, 10)

    # ── Competition timing ────────────────────────────────────────────────
    next_ev = get_next_competition(uid)
    days_to_comp   = None
    next_ev_name   = None
    comp_pts       = 10  # neutral when no competition
    if next_ev:
        next_ev_name = next_ev.get("name", "Competencia")
        try:
            ev_date     = datetime.strptime(next_ev["event_date"][:10], "%Y-%m-%d").date()
            days_to_comp = (ev_date - now.date()).days
            if days_to_comp < 0:
                comp_pts = 10   # passed — neutral
            elif days_to_comp == 0:
                comp_pts = 15   # competition day
            elif days_to_comp <= 2:
                comp_pts = 5    # final taper — rest, not push
            elif days_to_comp <= 6:
                comp_pts = 10   # immediate pre-comp
            elif days_to_comp <= 13:
                comp_pts = 15   # taper week
            elif days_to_comp <= 21:
                comp_pts = 20   # peak prep window
            else:
                comp_pts = 12   # general training cycle
        except Exception:
            comp_pts = 10

    score = min(100, w_avg_pts + w_trend_pts + load_pts + comp_pts)

    if score >= 85:
        label, color = "Listo para competir", "#2fb7c4"
    elif score >= 70:
        label, color = "Buen estado", "#2fb7c4"
    elif score >= 55:
        label, color = "Forma moderada", "#f0a832"
    elif score >= 40:
        label, color = "Revisar carga", "#f0a832"
    else:
        label, color = "Recuperación activa", "#e45a5a"

    return {
        "score":          score,
        "label":          label,
        "color":          color,
        "days_to_comp":   days_to_comp,
        "next_event":     next_ev_name,
        "breakdown": {
            "wellness_avg_pts":  w_avg_pts,
            "wellness_trend_pts": w_trend_pts,
            "load_pts":          load_pts,
            "comp_timing_pts":   comp_pts,
        },
    }


def get_platform_metrics() -> dict:
    """Métricas de uso de la plataforma para el panel interno/admin."""
    from datetime import timedelta
    now = datetime.utcnow()
    iso24h = (now - timedelta(hours=24)).isoformat()
    iso7d  = (now - timedelta(days=7)).isoformat()
    iso30d = (now - timedelta(days=30)).isoformat()

    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("SELECT role, COUNT(*) as n FROM users GROUP BY role")
        role_counts = {r["role"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            "SELECT COUNT(DISTINCT user_id) as n FROM questionnaires WHERE ts >= ?",
            (iso24h,),
        )
        dau = cur.fetchone()["n"]

        cur.execute(
            "SELECT COUNT(DISTINCT user_id) as n FROM questionnaires WHERE ts >= ?",
            (iso7d,),
        )
        wau = cur.fetchone()["n"]

        cur.execute(
            "SELECT COUNT(*) as n FROM questionnaires WHERE ts >= ?",
            (iso7d,),
        )
        checkins_7d = cur.fetchone()["n"]

        cur.execute(
            """SELECT DATE(ts) as day, COUNT(*) as n
               FROM questionnaires WHERE ts >= ?
               GROUP BY DATE(ts) ORDER BY day ASC""",
            (iso30d,),
        )
        checkins_daily = [{"day": r["day"], "n": r["n"]} for r in cur.fetchall()]

        cur.execute(
            """SELECT COUNT(DISTINCT q.user_id) as n
               FROM questionnaires q
               JOIN users u ON u.id = q.user_id
               WHERE q.ts >= ? AND u.role = 'deportista'""",
            (iso7d,),
        )
        active_athletes_7d = cur.fetchone()["n"]

        cur.execute(
            "SELECT AVG(wellness_score) as avg FROM questionnaires "
            "WHERE ts >= ? AND wellness_score IS NOT NULL",
            (iso7d,),
        )
        row = cur.fetchone()
        avg_wellness_7d = round(float(row["avg"]), 1) if row and row["avg"] is not None else None

        cur.execute(
            "SELECT COUNT(*) as n FROM users WHERE created_at >= ?",
            (iso30d,),
        )
        new_users_30d = cur.fetchone()["n"]

        cur.execute(
            "SELECT sport, COUNT(*) as n FROM users "
            "WHERE role='deportista' GROUP BY sport ORDER BY n DESC"
        )
        sport_dist = [{"sport": r["sport"] or "Sin definir", "n": r["n"]}
                      for r in cur.fetchall()]

        sessions_7d = 0
        try:
            cur.execute(
                "SELECT COUNT(*) as n FROM sessions WHERE ts_start >= ?",
                (iso7d,),
            )
            sessions_7d = cur.fetchone()["n"]
        except Exception:
            pass

        cur.execute(
            "SELECT COUNT(*) as n FROM questionnaires WHERE ts >= ?",
            (iso30d,),
        )
        checkins_30d = cur.fetchone()["n"]

        # WAU trend — 4 semanas completas (lunes-domingo)
        from datetime import timedelta
        wau_trend = []
        for w in range(3, -1, -1):
            week_end   = now - timedelta(days=w * 7)
            week_start = week_end - timedelta(days=6)
            cur.execute(
                "SELECT COUNT(DISTINCT user_id) as n FROM questionnaires "
                "WHERE ts >= ? AND ts < ?",
                (week_start.isoformat(), (week_end + timedelta(days=1)).isoformat()),
            )
            wau_trend.append({
                "label": f"S-{3-w}" if w > 0 else "Esta sem.",
                "n": cur.fetchone()["n"],
            })

    return {
        "total_athletes":    role_counts.get("deportista", 0),
        "total_coaches":     role_counts.get("coach", 0),
        "total_users":       sum(role_counts.values()),
        "dau":               dau,
        "wau":               wau,
        "checkins_7d":       checkins_7d,
        "checkins_30d":      checkins_30d,
        "checkins_daily":    checkins_daily,
        "active_athletes_7d": active_athletes_7d,
        "avg_wellness_7d":   avg_wellness_7d,
        "new_users_30d":     new_users_30d,
        "sport_dist":        sport_dist,
        "sessions_7d":       sessions_7d,
        "wau_trend":         wau_trend,
    }


# ======================
# Mensajes (chat interno)
# ======================

def send_message(sender_id: int, receiver_id: int, body: str) -> int:
    """Guarda un mensaje y devuelve su ID."""
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO messages(sender_id, receiver_id, body, ts) VALUES(?,?,?,?)",
            (int(sender_id), int(receiver_id), (body or "").strip(), ts),
        )
        return cur.lastrowid


def list_conversation(user_a: int, user_b: int, limit: int = 60) -> list:
    """Devuelve los últimos `limit` mensajes entre dos usuarios, orden cronológico."""
    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """SELECT * FROM messages
               WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
               ORDER BY ts DESC LIMIT ?""",
            (int(user_a), int(user_b), int(user_b), int(user_a), limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return list(reversed(rows))


def list_conversations_for_coach(coach_id: int, sport: str = None) -> list:
    """Lista los atletas que han intercambiado mensajes con el coach,
    con el último mensaje y el conteo de no leídos.
    Si se pasa `sport`, excluye conversaciones con atletas de otro deporte."""
    sport_val = (sport or "").strip()
    try:
        with _get_conn() as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute(
                """
                WITH coach_msgs AS (
                    SELECT
                        id,
                        CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END AS other_id,
                        body,
                        ts
                    FROM messages
                    WHERE sender_id=? OR receiver_id=?
                ),
                latest_ids AS (
                    SELECT other_id, MAX(id) AS last_id
                    FROM coach_msgs
                    GROUP BY other_id
                ),
                unread AS (
                    SELECT sender_id AS other_id, COUNT(*) AS unread
                    FROM messages
                    WHERE receiver_id=? AND read_at IS NULL
                    GROUP BY sender_id
                )
                SELECT
                    cm.other_id AS user_id,
                    COALESCE(u.name, '?') AS name,
                    COALESCE(u.sport, '') AS sport,
                    u.avatar_url AS avatar_url,
                    cm.body AS last_msg,
                    cm.ts AS last_ts,
                    COALESCE(unread.unread, 0) AS unread
                FROM latest_ids li
                JOIN coach_msgs cm ON cm.id = li.last_id
                LEFT JOIN users u ON u.id = cm.other_id
                LEFT JOIN unread ON unread.other_id = cm.other_id
                WHERE (? = '' OR COALESCE(u.sport, '') = '' OR u.sport = ?)
                ORDER BY cm.ts DESC, cm.id DESC
                """,
                (
                    int(coach_id), int(coach_id), int(coach_id), int(coach_id),
                    sport_val, sport_val,
                ),
            )
            rows = cur.fetchall()
        return [
            {
                "user_id":    r["user_id"],
                "name":       r["name"] or "?",
                "sport":      r["sport"] or "",
                "avatar_url": r["avatar_url"],
                "last_msg":   (r["last_msg"] or "")[:60],
                "last_ts":    r["last_ts"] or "",
                "unread":     int(r["unread"] or 0),
            }
            for r in rows
        ]
    except Exception:
        pass

    with _get_conn() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """SELECT DISTINCT
                   CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END AS other_id
               FROM messages WHERE sender_id=? OR receiver_id=?""",
            (int(coach_id), int(coach_id), int(coach_id)),
        )
        other_ids = [r["other_id"] for r in cur.fetchall()]

    result = []
    for oid in other_ids:
        user = get_user_by_id(int(oid)) or {}
        if sport_val and user.get("sport") and user["sport"] != sport_val:
            continue
        msgs = list_conversation(int(coach_id), int(oid), limit=1)
        unread = _count_unread_from(int(oid), int(coach_id))
        result.append({
            "user_id":    oid,
            "name":       user.get("name", "—"),
            "sport":      user.get("sport", ""),
            "avatar_url": user.get("avatar_url"),
            "last_msg":   msgs[-1]["body"][:60] if msgs else "",
            "last_ts":    msgs[-1]["ts"] if msgs else "",
            "unread":     unread,
        })
    return sorted(result, key=lambda x: x["last_ts"], reverse=True)


def mark_messages_read(reader_id: int, sender_id: int) -> None:
    """Marca como leídos todos los mensajes de sender_id hacia reader_id."""
    ts = datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE messages SET read_at=? WHERE sender_id=? AND receiver_id=? AND read_at IS NULL",
            (ts, int(sender_id), int(reader_id)),
        )


def _count_unread_from(sender_id: int, receiver_id: int) -> int:
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_id=? AND receiver_id=? AND read_at IS NULL",
            (int(sender_id), int(receiver_id)),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def get_unread_count(user_id: int) -> int:
    """Total de mensajes no leídos dirigidos a este usuario."""
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE receiver_id=? AND read_at IS NULL",
            (int(user_id),),
        )
        row = cur.fetchone()
    return row[0] if row else 0
