import json
with open("data/ecg/combat_12_wt_videoplayback_imu.json") as f:
    events = json.load(f)
print("Total eventos:", len(events))
print()
print("Impactos (no ruido):")
for e in events:
    if e["type"] != "ruido":
        mins = int(e["t"] // 60)
        secs = e["t"] % 60
        print(f"  {mins}:{secs:04.1f}  R{e['round']}  {e['type']:10s}  {e['intensity']:.1f}g")
print()
n_dado     = sum(1 for e in events if e["type"] == "dado")
n_recibido = sum(1 for e in events if e["type"] == "recibido")
n_ruido    = sum(1 for e in events if e["type"] == "ruido")
print(f"Conteo: dado={n_dado}  recibido={n_recibido}  ruido={n_ruido}")
