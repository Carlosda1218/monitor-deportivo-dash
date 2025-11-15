import os, json, sqlite3, hashlib
from datetime import datetime

DB_PATH = os.path.join("data", "users.db")

def _conn():
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def _dicts(cur):
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        yield {k: v for k, v in zip(cols, row)}

def init_db():
    con = _conn(); cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        role TEXT DEFAULT 'deportista',
        sport TEXT,
        password_hash BLOB,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_sensors(
        user_id INTEGER, sensor_code TEXT, PRIMARY KEY(user_id, sensor_code)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ecg_files(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        filename TEXT, fs INTEGER, created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ecg_metrics(
        ecg_id INTEGER PRIMARY KEY, bpm REAL, sdnn REAL, rmssd REAL, peaks_count INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS questionnaires(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ts TEXT,
        answers_json TEXT, wellness_score REAL, rpe REAL, duration_min REAL
    )""")
    con.commit(); con.close()

# ---------- Users ----------
def _hash_pw(pw:str):
    try:
        import bcrypt
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    except Exception:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8")

def create_user(name, email, pw, role, sport):
    con = _conn(); cur = con.cursor()
    cur.execute(
        "INSERT INTO users(name,email,role,sport,password_hash,created_at) VALUES(?,?,?,?,?,?)",
        (name, email, role or "deportista", sport, _hash_pw(pw), datetime.utcnow().isoformat())
    )
    con.commit(); con.close()

def get_user_by_email(email):
    con = _conn(); cur = con.cursor()
    # ¡Orden explícito de columnas!
    cur.execute("SELECT id,name,email,role,sport,password_hash,created_at FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    con.close()
    if not row: return None
    cols = ["id","name","email","role","sport","password_hash","created_at"]
    return {k:v for k,v in zip(cols,row)}

def list_users():
    con = _conn(); cur = con.cursor()
    cur.execute("SELECT id,name,role,sport,created_at FROM users ORDER BY id DESC")
    data = list(_dicts(cur)); con.close()
    return data

def add_user(name, sport=None, role="deportista"):
    con = _conn(); cur = con.cursor()
    cur.execute("INSERT INTO users(name,role,sport,created_at) VALUES(?,?,?,?)",
                (name, role or "deportista", sport, datetime.utcnow().isoformat()))
    con.commit(); con.close()

def delete_user(uid:int):
    con = _conn(); cur = con.cursor()
    cur.execute("DELETE FROM users WHERE id=?", (uid,))
    cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
    cur.execute("DELETE FROM ecg_files WHERE user_id=?", (uid,))
    cur.execute("DELETE FROM questionnaires WHERE user_id=?", (uid,))
    con.commit(); con.close()

# ---------- Sensors ----------
def get_user_sensors(uid:int):
    con = _conn(); cur = con.cursor()
    cur.execute("SELECT sensor_code FROM user_sensors WHERE user_id=?", (uid,))
    values = [r[0] for r in cur.fetchall()]
    con.close()
    return values

def set_user_sensors(uid:int, codes):
    con = _conn(); cur = con.cursor()
    cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
    for c in (codes or []):
        cur.execute("INSERT OR IGNORE INTO user_sensors(user_id,sensor_code) VALUES(?,?)", (uid, c))
    con.commit(); con.close()

# ---------- ECG ----------
def add_ecg_file(uid:int, filename:str, fs:int):
    con = _conn(); cur = con.cursor()
    cur.execute("INSERT INTO ecg_files(user_id,filename,fs,created_at) VALUES(?,?,?,?)",
                (uid, filename, fs, datetime.utcnow().isoformat()))
    ecg_id = cur.lastrowid
    con.commit(); con.close()
    return ecg_id

def list_ecg_files(uid:int):
    con = _conn(); cur = con.cursor()
    cur.execute("SELECT id,user_id,filename,fs,created_at FROM ecg_files WHERE user_id=? ORDER BY id DESC", (uid,))
    data = list(_dicts(cur)); con.close()
    return data

def save_ecg_metrics(ecg_id:int, bpm:float, sdnn:float, rmssd:float, peaks_count:int):
    con = _conn(); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO ecg_metrics(ecg_id,bpm,sdnn,rmssd,peaks_count) VALUES(?,?,?,?,?)",
                (ecg_id, bpm, sdnn, rmssd, peaks_count))
    con.commit(); con.close()

def get_last_ecg_metrics(uid:int):
    con = _conn(); cur = con.cursor()
    cur.execute("""SELECT m.bpm,m.sdnn,m.rmssd
                   FROM ecg_metrics m
                   JOIN ecg_files f ON f.id=m.ecg_id
                   WHERE f.user_id=?
                   ORDER BY f.id DESC LIMIT 1""", (uid,))
    row = cur.fetchone(); con.close()
    if not row: return None
    return {"bpm":row[0], "sdnn":row[1], "rmssd":row[2]}

# ---------- Questionnaire ----------
def save_questionnaire(uid:int, answers:dict, wellness:float, rpe:float=None, duration:float=None):
    con = _conn(); cur = con.cursor()
    cur.execute("INSERT INTO questionnaires(user_id,ts,answers_json,wellness_score,rpe,duration_min) VALUES(?,?,?,?,?,?)",
                (uid, datetime.utcnow().isoformat(), json.dumps(answers), wellness, rpe, duration))
    con.commit(); con.close()

def list_questionnaires(uid:int):
    con = _conn(); cur = con.cursor()
    cur.execute("SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min FROM questionnaires WHERE user_id=? ORDER BY id DESC", (uid,))
    data = list(_dicts(cur)); con.close()
    return data
