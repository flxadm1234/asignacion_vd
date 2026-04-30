# config.py
import os
from pathlib import Path
from datetime import datetime
import sys

APP_NAME = "SEAAP-AS Scheduler"

# Carpeta base en AppData\Roaming\SeaapAS (Windows) o HOME/.SeaapAS (otros)
if sys.platform == "win32":
    APP_DIR = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "SeaapAS"
else:
    APP_DIR = Path.home() / ".local" / "share" / "SeaapAS"
APP_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = APP_DIR / "logs"
DATA_DIR = APP_DIR / "data"
PROFILE_DIR = APP_DIR / "chrome-profile"
for d in (LOG_DIR, DATA_DIR, PROFILE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Ruta por defecto del JSON de cuentas (puedes cambiarlo en la GUI)
DEFAULT_ACCOUNTS_JSON = APP_DIR / "accounts.json"

# Donde Playwright guardará sus navegadores
env_bp = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
if env_bp:
    BROWSERS_DIR = Path(env_bp)
else:
    linux_default = Path.home() / ".cache" / "ms-playwright"
    if linux_default.exists():
        BROWSERS_DIR = linux_default
    else:
        BROWSERS_DIR = APP_DIR / "ms-playwright"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)

# URL de la lista de actores sociales
SEAAP_ACTOR_LIST_URL = (
    "https://visitasdomiciliarias.minsa.gob.pe/web"
    "#min=1&limit=80&view_type=list&model=actividades.actor&menu_id=237&action=1189"
)

def get_current_etapa_date() -> str:
    """
    Devuelve la fecha de etapa como el primer día del mes actual en formato 'YYYY-MM-01'.
    Ejemplo: si hoy es 2025-11-24 -> '2025-11-01'
    """
    today = datetime.today()
    etapa = datetime(year=today.year, month=today.month, day=1)
    return etapa.strftime("%Y-%m-%d")


def log_to_file(message: str):
    """Graba también en archivo de log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt

    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    with open(LOG_DIR / "seaap_scheduler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
