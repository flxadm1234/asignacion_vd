import json
import os
import sys
import threading
from pathlib import Path
from datetime import datetime
import importlib.util
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from config import DEFAULT_ACCOUNTS_JSON, log_to_file, DATA_DIR
from automation import AutomationWorker
from automation import load_accounts_from_json


BASE_DIR = Path(__file__).resolve().parent
PROJ2_DIR = BASE_DIR / "proyecto 2"

SEAAP_WIZARD_URL = (
    "https://visitasdomiciliarias.minsa.gob.pe/web"
    "#view_type=form&model=actividades.reporte.visitas.ninos.pivot.wizard&menu_id=353&action=1261"
)
WHADOX_LOGIN_URL = "https://sinanemia.site/login1.php"
WHADOX_MANT_URL = "https://sinanemia.site/appc/#/Mantenimiento"

def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _default_log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_to_file(line)


def run_seaap_whadox_pipeline(headless: bool = False, periodo_bd: str = "", ubigeo: str | None = None):
    accounts_path = str((BASE_DIR / "accounts.json").resolve())
    if not Path(accounts_path).exists():
        _default_log("[PIPELINE] No se encontró accounts.json en carpeta principal. Saltando pipeline SEAAP→Whadox.")
        return
    accounts = load_accounts_from_json(accounts_path, _default_log)
    if not accounts:
        _default_log("[PIPELINE] accounts.json sin cuentas. Saltando pipeline SEAAP→Whadox.")
        return
    if ubigeo:
        accounts = [a for a in accounts if str(a.get("name")) == str(ubigeo)]
    _default_log(f"[PIPELINE] Ejecutando SEAAP→Whadox para {len(accounts)} cuenta(s).")
    with sync_playwright() as p:
        # Lanzar navegador respetando 'headless' y con fallback si no hay DISPLAY
        browser_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        if sys.platform != "win32":
            browser_args.append("--disable-gpu")
            browser_args.append("--disable-software-rasterizer")
        no_display = (sys.platform != "win32") and (not os.environ.get("DISPLAY"))
        headless_launch = bool(headless) or no_display
        if no_display and not headless:
            _default_log("[PIPELINE] No se detectó DISPLAY. Forzando modo headless para Playwright.")
        browser = None
        try:
            browser = p.chromium.launch(headless=headless_launch, args=browser_args)
        except Exception as e_launch:
            if not headless_launch:
                _default_log(f"[PIPELINE] Lanzamiento con ventana falló. Reintentando en headless. Error: {e_launch}")
                browser = p.chromium.launch(headless=True, args=browser_args)
            else:
                raise
        ctx = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.bring_to_front()
        except Exception:
            pass
        for acc in accounts:
            try:
                _default_log(f"[SEAAP] [{acc.get('name')}] Abriendo asistente de exportación…")
                page.goto(SEAAP_WIZARD_URL, wait_until="domcontentloaded", timeout=180_000)
                # Login si aparece
                if page.locator("input[type=password]").count():
                    user = acc.get("seaap_user") or ""
                    pwd = acc.get("seaap_password") or ""
                    page.locator("#login, input[name='login'], input[type='text']").first.fill(user)
                    page.locator("#password, input[name='password'], input[type='password']").first.fill(pwd)
                    btn = page.locator(
                        "button:has-text('Ingresar'), button:has-text('Iniciar sesión'), button[type='submit'], input[type='submit']"
                    )
                    if btn.count():
                        btn.first.click()
                        page.wait_for_timeout(2500)
                # Buscar botón exportar
                EXPORT = [
                    'button:has-text("Generar Excel")',
                    'button.btn-sm.oe_highlight:has-text("Generar Excel")',
                    'span:has-text("Generar Excel")',
                    'button:has-text("Excel")',
                    'button:has-text("Exportar")',
                    'button:has-text("Descargar")',
                    "button.btn-primary",
                    ".o_form_button_save",
                ]
                export_btn = None
                for intento in range(8):
                    for sel in EXPORT:
                        if page.locator(sel).count():
                            export_btn = page.locator(sel).first
                            break
                    if export_btn:
                        break
                    page.wait_for_timeout(2000)
                if not export_btn:
                    _default_log(f"[SEAAP] [{acc.get('name')}] No se encontró botón de exportación.")
                    continue
                _default_log(f"[SEAAP] [{acc.get('name')}] Descargando Excel…")
                with page.expect_download(timeout=180_000) as dl_info:
                    export_btn.click()
                dl = dl_info.value
                suggested = dl.suggested_filename or "reporte.xls"
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                final_path = DATA_DIR / f"seaap_{acc.get('seaap_user')}_{stamp}_{suggested}"
                dl.save_as(str(final_path))
                _default_log(f"[SEAAP] [{acc.get('name')}] Archivo guardado: {final_path}")
                # Subir a Whadox
                _default_log(f"[WHADOX] [{acc.get('name')}] Ingresando…")
                page.goto(WHADOX_LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)
                page.fill("#dni", str(acc.get("whadox_dni") or ""))
                page.fill("#pass", str(acc.get("whadox_password") or ""))
                btn_login = None
                for sel in ["button.login-form-btn", "button:has-text('CONECTAR')", "button[type='submit']"]:
                    if page.locator(sel).count():
                        btn_login = page.locator(sel).first
                        break
                if btn_login:
                    btn_login.click()
                    page.wait_for_timeout(1500)
                # Navegación robusta a Mantenimiento (SPA) con reintentos
                cont = None
                for intento in range(1, 7):
                    try:
                        _default_log(f"[WHADOX] [{acc.get('name')}] Cargando Mantenimiento… intento {intento}/6")
                        page.goto(WHADOX_MANT_URL, wait_until="domcontentloaded", timeout=90_000)
                    except Exception as e:
                        _default_log(f"[WHADOX] [{acc.get('name')}] goto abortado ({e}). Probando ruta base…")
                        try:
                            page.goto("https://sinanemia.site/appc/", wait_until="domcontentloaded", timeout=90_000)
                            page.wait_for_timeout(1500)
                            try:
                                page.evaluate("() => { try { location.hash = '#/Mantenimiento' } catch(e) {} }")
                            except Exception:
                                pass
                            page.wait_for_timeout(1500)
                        except Exception as e2:
                            _default_log(f"[WHADOX] [{acc.get('name')}] Ruta base también falló: {e2}")
                    # Validar sección
                    for _ in range(20):
                        loc = page.locator("div.card:has(h2:has-text('Verificar Asignación SEAAP'))")
                        if loc.count():
                            cont = loc.first
                            break
                        page.wait_for_timeout(500)
                    if cont:
                        break
                    page.wait_for_timeout(2000)
                if not cont:
                    _default_log(f"[WHADOX] [{acc.get('name')}] No se encontró sección 'Verificar Asignación SEAAP' tras reintentos.")
                    continue
                # Localizar input file con espera
                file_input = None
                for _ in range(20):
                    loc_inp = cont.locator('#archivo5, input[type="file"]')
                    if loc_inp.count():
                        file_input = loc_inp.first
                        break
                    page.wait_for_timeout(500)
                if not file_input:
                    _default_log(f"[WHADOX] [{acc.get('name')}] Input file no encontrado tras espera.")
                    continue
                file_input.set_input_files(str(final_path))
                etapa_val = str(periodo_bd or "").strip()
                try:
                    inp_etapa = cont.locator("#etapa3, input[name='etapa3'], input[type='date']")
                    if inp_etapa.count():
                        if etapa_val:
                            inp_etapa.first.fill(etapa_val)
                            page.wait_for_timeout(300)
                except Exception:
                    pass
                subir_btn = cont.locator('button[onclick*="subirArchivos5"], button:has-text("SUBIR ARCHIVO"), button:has-text("Subir"), button.btn-success')
                if subir_btn.count():
                    _default_log(f"[WHADOX] [{acc.get('name')}] Subiendo archivo…")
                    try:
                        with page.expect_response(lambda r: ("archivos/cargardataseaap2.php" in r.url), timeout=600_000) as resp_info:
                            try:
                                ubig = str(acc.get("name") or acc.get("ubigeo") or "").strip()
                                try:
                                    page.evaluate("u => { try { subirArchivos5(u); } catch(e) {} }", ubig)
                                except Exception:
                                    page.evaluate("() => { try { subirArchivos5(); } catch(e) {} }")
                            except Exception:
                                subir_btn.first.click()
                        resp = resp_info.value
                        try:
                            _default_log(f"[WHADOX] [{acc.get('name')}] Respuesta HTTP: {resp.status}")
                        except Exception:
                            pass
                        txt = ""
                        try:
                            txt = resp.text()
                        except Exception:
                            txt = ""
                        rows_cnt = None
                        try:
                            j = resp.json()
                            if isinstance(j, dict):
                                rows_cnt = j.get("rows")
                                _default_log(f"[WHADOX] [{acc.get('name')}] JSON ok={j.get('ok')} rows={j.get('rows')} message={j.get('message')}")
                                msg = str(j.get("message") or "")
                                ok = j.get("ok")
                                if (ok is False) and ("etapa" in msg.lower()):
                                    try:
                                        ubig2 = str(acc.get("name") or acc.get("ubigeo") or "").strip()
                                        etapa2 = etapa_val
                                        with open(final_path, "rb") as fh:
                                            form = {"archivo5": ("file.xls", fh.read(), "application/vnd.ms-excel")}
                                        r2 = page.request.post(
                                            "https://sinanemia.site/appc/archivos/cargardataseaap2.php",
                                            params={"ubigeo": ubig2, "etapa": etapa2},
                                            multipart=form,
                                            timeout=600000,
                                        )
                                        try:
                                            j2 = r2.json()
                                        except Exception:
                                            j2 = None
                                        if isinstance(j2, dict):
                                            rows_cnt = j2.get("rows", rows_cnt)
                                            _default_log(f"[WHADOX] [{acc.get('name')}] Reintento POST ok={j2.get('ok')} rows={j2.get('rows')} message={j2.get('message')}")
                                    except Exception as epost:
                                        _default_log(f"[WHADOX] [{acc.get('name')}] Reintento POST falló: {epost}")
                        except Exception:
                            pass
                        if rows_cnt is None and txt:
                            import re
                            m = re.search(r"Se han cargado\s+(\d+)\s+datos", txt, flags=re.IGNORECASE)
                            if m:
                                rows_cnt = int(m.group(1))
                        try:
                            page.wait_for_selector("div.swal2-popup.swal2-modal", state="visible", timeout=120_000)
                            title_text = page.locator("#swal2-title").first.inner_text() if page.locator("#swal2-title").count() else ""
                            html_text = page.locator("#swal2-html-container").first.inner_text() if page.locator("#swal2-html-container").count() else ""
                            import re
                            m2 = re.search(r"cargado(?:s)?\s+(\d+)\s+dato", html_text, flags=re.IGNORECASE)
                            if m2:
                                rows_cnt = int(m2.group(1))
                            if page.locator(".swal2-confirm").count():
                                page.locator(".swal2-confirm").first.click()
                            _default_log(f"[WHADOX] [{acc.get('name')}] Carga confirmada. Filas={rows_cnt if rows_cnt is not None else 'N/D'}")
                        except Exception:
                            _default_log(f"[WHADOX] [{acc.get('name')}] Sin modal de confirmación. Filas={rows_cnt if rows_cnt is not None else 'N/D'}")
                    except PWTimeout:
                        _default_log(f"[WHADOX] [{acc.get('name')}] No se recibió respuesta AJAX en el tiempo esperado.")
                else:
                    _default_log(f"[WHADOX] [{acc.get('name')}] Botón SUBIR no encontrado.")
            except Exception as e:
                _default_log(f"[PIPELINE][ERROR] Cuenta {acc.get('name')}: {e}")
        ctx.close()
        browser.close()


def _read_db_config_from_env():
    host = os.getenv("SEAAP_DB_HOST", "31.220.84.86")
    user = os.getenv("SEAAP_DB_USER", "felix")
    password = os.getenv("SEAAP_DB_PASSWORD", "flxadm1234abc")
    database = os.getenv("SEAAP_DB_NAME", "compromiso_uno")
    port = int(os.getenv("SEAAP_DB_PORT", "3306"))
    return {"host": host, "user": user, "password": password, "database": database, "port": port}


def run_main_automation(headless: bool = False, periodo_bd: str = "", periodo_manual: str = "", ubigeo: str | None = None, request_id: int | None = None):
    accounts_path = str((BASE_DIR / "accounts.json").resolve())
    if not Path(accounts_path).exists():
        accounts_path = str(DEFAULT_ACCOUNTS_JSON)
    _default_log(f"[PIPELINE] Usando cuentas PRINCIPALES: {accounts_path}")
    _default_log(f"[PIPELINE] HEADLESS para automatización solicitado={headless} (forzaremos visible).")
    # Cargar configuración de BD desde config.json en carpeta principal si existe
    db_config = None
    # Intento secundario: usar config.json del proyecto si existe
    if not db_config:
        try:
            local_cfg = BASE_DIR / "config.json"
            if local_cfg.exists():
                data = json.loads(local_cfg.read_text(encoding="utf-8") or "{}")
                db_node = data.get("db") or data
                host = db_node.get("host")
                user = db_node.get("user")
                password = db_node.get("password")
                database = db_node.get("database")
                port = int(db_node.get("port")) if db_node.get("port") else None
                if host and user and database:
                    db_config = {
                        "host": host,
                        "user": user,
                        "password": password or "",
                        "database": database,
                        "port": port or 3306,
                    }
                    _default_log("[PIPELINE] Configuración BD tomada de config.json en carpeta principal.")
        except Exception as e:
            _default_log(f"[PIPELINE] Falló lectura de config.json (principal): {e}")
    if not db_config:
        db_config = _read_db_config_from_env()
        _default_log(f"[PIPELINE] Configuración BD por defecto aplicada: host={db_config['host']} user={db_config['user']} db={db_config['database']}")

    def log(msg: str):
        _default_log(msg)


    worker = AutomationWorker(
        db_config=db_config,
        accounts_path=accounts_path,
        periodo_bd=periodo_bd or "",
        periodo_manual=periodo_manual or "",
        log_callback=log,
        progress_callback=None,
        headless=headless,
        target_ubigeo=(ubigeo or None),
        request_id=request_id,
    )
    worker.start()
    return worker


def orchestrate_full_run(headless: bool = False, periodo_bd: str = "", periodo_manual: str = "", ubigeo: str | None = None, request_id: int | None = None):
    run_seaap_whadox_pipeline(headless=headless, periodo_bd=periodo_bd, ubigeo=ubigeo)
    worker = run_main_automation(headless=headless, periodo_bd=periodo_bd, periodo_manual=periodo_manual, ubigeo=ubigeo, request_id=request_id)
    return worker
