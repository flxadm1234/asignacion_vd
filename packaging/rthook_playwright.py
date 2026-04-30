import os
import sys
from pathlib import Path

base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
mp = base / "ms-playwright"
if mp.exists():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(mp)
os.environ.setdefault("PLAYWRIGHT_SYNC_API", "1")
os.environ.setdefault("PWDEBUG", "0")
os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
