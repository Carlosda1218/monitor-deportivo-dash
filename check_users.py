import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3

con = sqlite3.connect(os.path.join(os.path.dirname(__file__), "data", "users.db"))
con.row_factory = sqlite3.Row
rows = con.execute("SELECT id, email, name, role, sport, password_hash FROM users ORDER BY id").fetchall()
con.close()

print(f"{'id':>4}  {'email':35}  {'role':12}  {'sport':12}  {'name':20}  hash_prefix")
print("-" * 115)
for r in rows:
    print(f"{r['id']:>4}  {(r['email'] or ''):35}  {(r['role'] or ''):12}  {(r['sport'] or ''):12}  {(r['name'] or ''):20}  {(r['password_hash'] or '')[:30]}")
