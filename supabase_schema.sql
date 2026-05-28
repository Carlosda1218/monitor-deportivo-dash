-- ============================================================
-- CombatIQ — Schema PostgreSQL para Supabase
-- Generado desde db.py (SQLite) — 2026-05-28
-- Ejecutar en: Supabase → SQL Editor → New query → Run
-- ============================================================

-- Extensión para UUIDs (opcional, útil en futuro)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. schema_migrations  (control de versiones)
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT
);

-- ============================================================
-- 2. users  (tabla central — todos los roles)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id                      SERIAL PRIMARY KEY,
    name                    TEXT,
    email                   TEXT UNIQUE,
    role                    TEXT,
    sport                   TEXT,
    password_hash           BYTEA,
    created_at              TEXT,
    coach_id                INTEGER,
    athlete_profile_json    TEXT,
    onboarding_done         INTEGER DEFAULT 0,
    avatar_url              TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================================
-- 3. user_sensors  (sensores activos por usuario)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_sensors (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sensor_code TEXT NOT NULL,
    PRIMARY KEY (user_id, sensor_code)
);

-- ============================================================
-- 4. sensor_devices  (dispositivos físicos emparejados)
-- ============================================================
CREATE TABLE IF NOT EXISTS sensor_devices (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sensor_code      TEXT NOT NULL,
    device_id        TEXT NOT NULL,
    device_label     TEXT,
    status           TEXT DEFAULT 'paired',
    last_seen        TEXT,
    firmware_version TEXT,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (user_id, device_id)
);

-- ============================================================
-- 5. ecg_files  (archivos ECG subidos)
-- ============================================================
CREATE TABLE IF NOT EXISTS ecg_files (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    filename   TEXT,
    fs         INTEGER,
    created_at TEXT,
    session_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ecg_files_user ON ecg_files(user_id);
CREATE INDEX IF NOT EXISTS idx_ecg_files_session_id_desc ON ecg_files(session_id, id DESC);

-- ============================================================
-- 6. ecg_metrics  (métricas calculadas de ECG)
-- ============================================================
CREATE TABLE IF NOT EXISTS ecg_metrics (
    id          SERIAL PRIMARY KEY,
    ecg_file_id INTEGER NOT NULL REFERENCES ecg_files(id) ON DELETE CASCADE,
    bpm         REAL,
    sdnn        REAL,
    rmssd       REAL,
    n_peaks     INTEGER,
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ecg_metrics_file_id_desc ON ecg_metrics(ecg_file_id, id DESC);

-- ============================================================
-- 7. questionnaires  (check-ins de bienestar del atleta)
-- ============================================================
CREATE TABLE IF NOT EXISTS questionnaires (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    ts              TEXT,
    answers_json    TEXT,
    wellness_score  REAL,
    rpe             REAL,
    duration_min    REAL,
    session_id      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_questionnaires_user ON questionnaires(user_id);
CREATE INDEX IF NOT EXISTS idx_questionnaires_ts ON questionnaires(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_questionnaires_user_id_desc ON questionnaires(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_questionnaires_session ON questionnaires(session_id);

-- ============================================================
-- 8. imu_metrics  (datos de acelerómetro/giroscopio)
-- ============================================================
CREATE TABLE IF NOT EXISTS imu_metrics (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename     TEXT,
    ts           TEXT DEFAULT NOW()::TEXT,
    n_hits       INTEGER,
    hits_per_min REAL,
    mean_int_g   REAL,
    max_int_g    REAL,
    session_id   INTEGER,
    sensor_type  TEXT,
    mean_ang_vel REAL,
    max_ang_vel  REAL
);

CREATE INDEX IF NOT EXISTS idx_imu_metrics_session_ts ON imu_metrics(session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_imu_metrics_user_ts ON imu_metrics(user_id, ts DESC);

-- ============================================================
-- 9. emg_metrics  (electromiografía)
-- ============================================================
CREATE TABLE IF NOT EXISTS emg_metrics (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT,
    ts          TEXT DEFAULT NOW()::TEXT,
    rms         REAL,
    peak        REAL,
    fatigue     REAL,
    session_id  INTEGER
);

-- ============================================================
-- 10. resp_metrics  (banda respiratoria)
-- ============================================================
CREATE TABLE IF NOT EXISTS resp_metrics (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT,
    ts          TEXT DEFAULT NOW()::TEXT,
    n_breaths   INTEGER,
    br_min      REAL,
    mean_period REAL,
    session_id  INTEGER
);

-- ============================================================
-- 11. sessions  (sesiones de entrenamiento/combate)
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    athlete_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ts_start    TEXT,
    ts_end      TEXT,
    sport       TEXT,
    notes       TEXT,
    status      TEXT,
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_athlete ON sessions(athlete_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(athlete_id, ts_start);
CREATE INDEX IF NOT EXISTS idx_sessions_athlete_ts ON sessions(athlete_id, ts_start);

-- ============================================================
-- 12. coach_athletes  (roster: relación coach ↔ atleta)
-- ============================================================
CREATE TABLE IF NOT EXISTS coach_athletes (
    coach_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    athlete_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT,
    PRIMARY KEY (coach_id, athlete_id)
);

CREATE INDEX IF NOT EXISTS idx_coach_athletes_coach ON coach_athletes(coach_id);

-- ============================================================
-- 13. teams
-- ============================================================
CREATE TABLE IF NOT EXISTS teams (
    id         SERIAL PRIMARY KEY,
    coach_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sport      TEXT,
    created_at TEXT
);

-- ============================================================
-- 14. team_members
-- ============================================================
CREATE TABLE IF NOT EXISTS team_members (
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    athlete_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_label  TEXT,
    created_at  TEXT,
    PRIMARY KEY (team_id, athlete_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);

-- ============================================================
-- 15. weights  (registros de peso corporal)
-- ============================================================
CREATE TABLE IF NOT EXISTS weights (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    weight_kg  REAL NOT NULL,
    target_kg  REAL,
    note       TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_weights_user_date ON weights(user_id, date);
CREATE INDEX IF NOT EXISTS idx_weights_user_date_desc ON weights(user_id, date DESC);

-- ============================================================
-- 16. nutrition_logs  (registros de nutrición)
-- ============================================================
CREATE TABLE IF NOT EXISTS nutrition_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    adherence_pct   REAL NOT NULL,
    kcal            REAL,
    note            TEXT,
    created_at      TEXT,
    protein_g       REAL,
    carbs_g         REAL,
    fats_g          REAL,
    water_ml        REAL
);

CREATE INDEX IF NOT EXISTS idx_nutrition_user_date ON nutrition_logs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_nutrition_user_date_desc ON nutrition_logs(user_id, date DESC);

-- ============================================================
-- 17. announcements  (comunicados del coach)
-- ============================================================
CREATE TABLE IF NOT EXISTS announcements (
    id         SERIAL PRIMARY KEY,
    coach_id   INTEGER NOT NULL,
    sport      TEXT,
    title      TEXT NOT NULL,
    body       TEXT,
    pinned     INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- ============================================================
-- 18. notification_prefs
-- ============================================================
CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id              INTEGER PRIMARY KEY,
    low_wellness_alert   INTEGER DEFAULT 1,
    announcement_notify  INTEGER DEFAULT 1,
    checkin_reminder     INTEGER DEFAULT 0,
    email_override       TEXT,
    updated_at           TEXT
);

-- ============================================================
-- 19. competition_events
-- ============================================================
CREATE TABLE IF NOT EXISTS competition_events (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    name          TEXT NOT NULL,
    event_date    TEXT NOT NULL,
    sport         TEXT,
    target_weight REAL,
    location      TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL
);

-- ============================================================
-- 20. competition_results
-- ============================================================
CREATE TABLE IF NOT EXISTS competition_results (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    event_date TEXT NOT NULL,
    medal      TEXT DEFAULT 'participant',
    category   TEXT,
    location   TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL
);

-- ============================================================
-- 21. session_notes  (notas del coach por atleta)
-- ============================================================
CREATE TABLE IF NOT EXISTS session_notes (
    id         SERIAL PRIMARY KEY,
    coach_id   INTEGER NOT NULL,
    athlete_id INTEGER NOT NULL,
    note       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- ============================================================
-- 22. messages  (chat interno coach ↔ atleta)
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    sender_id   INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    body        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    read_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, receiver_id);
CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id, read_at);
CREATE INDEX IF NOT EXISTS idx_messages_pair_ts ON messages(sender_id, receiver_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_receiver_sender_read ON messages(receiver_id, sender_id, read_at);

-- ============================================================
-- 23. sensor_sessions  (sesiones de captura de sensores)
-- ============================================================
CREATE TABLE IF NOT EXISTS sensor_sessions (
    id           SERIAL PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    device_id    INTEGER REFERENCES sensor_devices(id) ON DELETE SET NULL,
    sensor_code  TEXT NOT NULL,
    ts_start     TEXT NOT NULL DEFAULT (NOW()::TEXT),
    ts_end       TEXT,
    status       TEXT NOT NULL DEFAULT 'collecting',
    sample_count INTEGER NOT NULL DEFAULT 0,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (NOW()::TEXT)
);

CREATE INDEX IF NOT EXISTS idx_sensor_sessions_session ON sensor_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_sensor_sessions_device ON sensor_sessions(device_id);

-- ============================================================
-- 24. nutrition_coach_feedback
-- ============================================================
CREATE TABLE IF NOT EXISTS nutrition_coach_feedback (
    id           SERIAL PRIMARY KEY,
    athlete_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    coach_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start   TEXT NOT NULL,
    note         TEXT,
    validated_at TEXT,
    created_at   TEXT,
    UNIQUE (athlete_id, coach_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_nutri_feedback_athlete ON nutrition_coach_feedback(athlete_id);

-- ============================================================
-- 25. password_reset_tokens
-- ============================================================
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT,
    request_ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_password_reset_hash ON password_reset_tokens(token_hash);

-- ============================================================
-- Marcar todas las migraciones como aplicadas
-- ============================================================
INSERT INTO schema_migrations(version, applied_at)
VALUES
    (10,  NOW()::TEXT),
    (20,  NOW()::TEXT),
    (30,  NOW()::TEXT),
    (40,  NOW()::TEXT),
    (50,  NOW()::TEXT),
    (60,  NOW()::TEXT),
    (70,  NOW()::TEXT),
    (80,  NOW()::TEXT),
    (90,  NOW()::TEXT),
    (100, NOW()::TEXT),
    (110, NOW()::TEXT),
    (120, NOW()::TEXT),
    (130, NOW()::TEXT),
    (140, NOW()::TEXT),
    (150, NOW()::TEXT),
    (160, NOW()::TEXT),
    (170, NOW()::TEXT),
    (180, NOW()::TEXT),
    (190, NOW()::TEXT),
    (200, NOW()::TEXT),
    (210, NOW()::TEXT)
ON CONFLICT (version) DO NOTHING;

-- ============================================================
-- FIN DEL SCHEMA
-- 25 tablas · 28 índices · migraciones marcadas
-- ============================================================
