import urllib.request, json, time, sys
from pathlib import Path

def health_ok():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False

if not health_ok():
    print("API no está corriendo. Abre main.py para iniciar la GUI y el servidor API.")
    sys.exit(1)

req = urllib.request.Request(
    "http://127.0.0.1:8787/run",
    data=json.dumps({"headless": True, "periodo_bd": "2026-02-01"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
print(urllib.request.urlopen(req).read().decode())
