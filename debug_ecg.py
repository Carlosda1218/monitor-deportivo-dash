import sqlite3, sys
con = sqlite3.connect("data/users.db")
con.row_factory = sqlite3.Row

print("=== Sessions athlete 21 ===")
for r in con.execute("SELECT id, ts_start, notes FROM sessions WHERE athlete_id=21 ORDER BY ts_start").fetchall():
    print(f"  Session {r['id']}: {r['ts_start']} | {r['notes'][:60]}")

print("\n=== ECG files athlete 21 ===")
for r in con.execute("SELECT id, user_id, filename, fs, session_id FROM ecg_files WHERE user_id=21 ORDER BY id").fetchall():
    print(f"  ECG {r['id']}: file={r['filename']}, fs={r['fs']}, session_id={r['session_id']}")

con.close()
