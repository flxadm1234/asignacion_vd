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

SEAAP_REPORT_URL = "https://visitasdomiciliarias.minsa.gob.pe/odoo/action-339/228"
SEAAP_LOGOUT_URL = "https://visitasdomiciliarias.minsa.gob.pe/web/session/logout"
WHADOX_LOGIN_URL = "https://sinanemia.site/login1.php"
WHADOX_MANT_URL = "https://sinanemia.site/appc/#/Mantenimiento"

def _sanitize_url(u: str) -> str:
    return (u or "").strip().strip('"').strip("'").replace("`", "").strip()

def _seaap_logout(page, log):
    try:
        page.goto(_sanitize_url(SEAAP_LOGOUT_URL), wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_timeout(800)
        except Exception:
            pass
    except Exception:
        pass

    try:
        btn_user = page.locator(
            "button.o-dropdown.dropdown-toggle:has(img.o_user_avatar), "
            "button.o-dropdown.dropdown-toggle:has(.oe_topbar_name), "
            "button.o-dropdown.dropdown-toggle"
        )
        if btn_user.count():
            btn_user.first.click(force=True)
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass
            link = page.locator("a[data-menu='logout'], a[href*='/web/session/logout']")
            if link.count():
                link.first.click(force=True)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                return True
    except Exception:
        pass

    try:
        page.goto(_sanitize_url(SEAAP_LOGOUT_URL), wait_until="domcontentloaded", timeout=60_000)
        return True
    except Exception:
        return False

def _seaap_login_if_needed(page, user: str, pwd: str, log):
    def _login_container():
        form = page.locator("form.oe_login_form, form[action='/web/login'], form:has(#login):has(#password)")
        if form.count():
            return form.first
        return page

    def _pwd_locator(container=None):
        c = container or page
        return c.locator("input[name='password'], #password, input[type='password']")

    def _user_locator(container=None):
        c = container or page
        return c.locator("input[name='login'], #login")

    def has_login_form() -> bool:
        try:
            cont = _login_container()
            if _pwd_locator(cont).count() == 0 or _user_locator(cont).count() == 0:
                return False
            try:
                return _pwd_locator(cont).first.is_visible()
            except Exception:
                return True
        except Exception:
            return False

    if not has_login_form():
        return True

    for intento in range(1, 4):
        log(f"[SEAAP] Login requerido. Intento {intento}/3…")
        cont = _login_container()

        user_inp = _user_locator(cont)
        pwd_inp = _pwd_locator(cont)
        if user_inp.count() == 0 or pwd_inp.count() == 0:
            log("[SEAAP][WARN] Formulario login no está completo (faltan inputs). Reintentando…")
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            continue

        try:
            user_inp.first.click(force=True)
            user_inp.first.fill(user)
        except Exception:
            pass
        try:
            pwd_inp.first.click(force=True)
            pwd_inp.first.fill(pwd)
        except Exception:
            pass

        btn = cont.locator(
            "button:has-text('Ingresar'), button:has-text('Iniciar sesión'), button[type='submit'], input[type='submit']"
        )
        if btn.count() == 0:
            btn = page.locator(
                "button:has-text('Ingresar'), button:has-text('Iniciar sesión'), button[type='submit'], input[type='submit']"
            )

        if btn.count():
            try:
                btn.first.click()
            except Exception:
                try:
                    btn.first.click(force=True)
                except Exception:
                    pass

        try:
            page.wait_for_load_state("domcontentloaded", timeout=60_000)
        except Exception:
            pass

        try:
            page.wait_for_url("**/odoo**", timeout=30_000)
        except Exception:
            pass

        try:
            if page.locator(".o_main_navbar, nav.o_main_navbar").count():
                log("[SEAAP] Login exitoso (navbar detectada).")
                return True
        except Exception:
            pass

        try:
            if not has_login_form():
                log("[SEAAP] Login exitoso (formulario ya no visible).")
                return True
        except Exception:
            pass

        if not has_login_form():
            log("[SEAAP] Login exitoso (formulario ya no presente).")
            return True

        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass

    log("[SEAAP][ERROR] No se pudo completar login (formulario sigue presente).")
    return False

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

    raw = (periodo_bd or "").strip()
    month_label = None
    year_label = None
    month_num = None
    try:
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            year_label = raw[0:4]
            month_num = int(raw[5:7])
            labels = {
                1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
                7: "Jul", 8: "Ago", 9: "Set", 10: "Oct", 11: "Nov", 12: "Dic",
            }
            month_label = labels.get(month_num)
    except Exception:
        month_label = None
        year_label = None
        month_num = None

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
                user = acc.get("seaap_user") or ""
                pwd = acc.get("seaap_password") or ""

                _default_log(f"[SEAAP] [{acc.get('name')}] Cerrando sesión previa…")
                _seaap_logout(page, _default_log)

                report_ok = False
                for intento in range(1, 4):
                    try:
                        _default_log(f"[SEAAP] [{acc.get('name')}] Cargando reportes… intento {intento}/3")
                        page.goto(SEAAP_REPORT_URL, wait_until="domcontentloaded", timeout=180_000)
                    except Exception:
                        try:
                            page.goto(SEAAP_REPORT_URL, wait_until="networkidle", timeout=180_000)
                        except Exception as e_goto:
                            _default_log(f"[SEAAP] [{acc.get('name')}] Falló goto reportes: {e_goto}")

                    if not _seaap_login_if_needed(page, user, pwd, _default_log):
                        continue

                    try:
                        page.goto(SEAAP_REPORT_URL, wait_until="domcontentloaded", timeout=180_000)
                    except Exception:
                        pass

                    try:
                        page.wait_for_timeout(600)
                    except Exception:
                        pass

                    export_btn = page.locator(
                        "button[name='do_report_2']:has-text('Generar Excel'), "
                        "button.btn.btn-primary[name='do_report_2'], "
                        "button:has-text('Generar Excel')"
                    ).first
                    month_sel = page.locator("select#month_0, div[name='month'] select.o_input, div[name='month'] select").first
                    if export_btn.count() or month_sel.count():
                        report_ok = True
                        break

                    _default_log(f"[SEAAP] [{acc.get('name')}] Reportes no listos (no aparece botón/mes). Reintentando…")
                    try:
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass

                if not report_ok:
                    try:
                        _default_log(f"[SEAAP] [{acc.get('name')}] No se pudo acceder a Reportes tras reintentos. URL={page.url}. Saltando cuenta.")
                    except Exception:
                        _default_log(f"[SEAAP] [{acc.get('name')}] No se pudo acceder a Reportes tras reintentos. Saltando cuenta.")
                    continue

                page.wait_for_timeout(400)

                if month_label:
                    month_sel = page.locator("select#month_0, div[name='month'] select.o_input, div[name='month'] select").first
                    if month_sel.count():
                        try:
                            month_sel.select_option(label=month_label)
                        except Exception:
                            try:
                                month_sel.select_option(value=str(month_num))
                            except Exception:
                                try:
                                    month_sel.select_option(value=f"\"{month_num}\"")
                                except Exception:
                                    pass
                        page.wait_for_timeout(700)

                if year_label:
                    year_sel = page.locator("select#year_0, div[name='year'] select.o_input, div[name='year'] select").first
                    if year_sel.count():
                        try:
                            year_sel.select_option(label=year_label)
                            page.wait_for_timeout(700)
                        except Exception:
                            pass
                    else:
                        year_inp = page.locator("div[name='year'] input.o_input, div[name='year'] input").first
                        if year_inp.count():
                            try:
                                year_inp.fill(year_label)
                                page.wait_for_timeout(700)
                            except Exception:
                                pass

                export_btn = page.locator(
                    "button[name='do_report_2']:has-text('Generar Excel'), "
                    "button.btn.btn-primary[name='do_report_2'], "
                    "button:has-text('Generar Excel')"
                ).first
                if export_btn.count() == 0:
                    try:
                        _default_log(f"[SEAAP] [{acc.get('name')}] No se encontró botón 'Generar Excel'. URL={page.url}")
                    except Exception:
                        _default_log(f"[SEAAP] [{acc.get('name')}] No se encontró botón 'Generar Excel'.")
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
                page.goto(_sanitize_url(WHADOX_LOGIN_URL), wait_until="domcontentloaded", timeout=120_000)
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
                        page.goto(_sanitize_url(WHADOX_MANT_URL), wait_until="domcontentloaded", timeout=90_000)
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
                try:
                    page.wait_for_function(
                        "() => { const i=document.querySelector('#archivo5'); return i && i.files && i.files.length > 0; }",
                        timeout=10_000,
                    )
                except Exception:
                    pass
                etapa_val = str(periodo_bd or "").strip()
                try:
                    inp_etapa = cont.locator("#etapa3, input[name='etapa3'], input[type='date']")
                    if inp_etapa.count():
                        if etapa_val:
                            inp_etapa.first.fill(etapa_val)
                            page.wait_for_timeout(300)
                            try:
                                page.evaluate(
                                    "v => { const i=document.querySelector('#etapa3, input[name=\"etapa3\"], input[type=\"date\"]'); if(i){ i.value=v; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true})); } }",
                                    etapa_val,
                                )
                            except Exception:
                                pass
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
                        json_ok = None
                        try:
                            j = resp.json()
                            if isinstance(j, dict):
                                rows_cnt = j.get("rows")
                                json_ok = j.get("ok")
                                _default_log(f"[WHADOX] [{acc.get('name')}] JSON ok={json_ok} rows={j.get('rows')} message={j.get('message')}")
                                msg = str(j.get("message") or "")
                                if (json_ok is False) and ("etapa" in msg.lower()):
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
                        if json_ok is True and (rows_cnt == 0 or rows_cnt == "0"):
                            try:
                                ubig2 = str(acc.get("name") or acc.get("ubigeo") or "").strip()
                                etapa2 = etapa_val
                                mime = "application/vnd.ms-excel"
                                try:
                                    import mimetypes
                                    mt = mimetypes.guess_type(str(final_path))[0]
                                    if mt:
                                        mime = mt
                                except Exception:
                                    pass
                                with open(final_path, "rb") as fh:
                                    form = {"archivo5": (Path(final_path).name, fh.read(), mime)}
                                r3 = page.request.post(
                                    "https://sinanemia.site/appc/archivos/cargardataseaap2.php",
                                    params={"ubigeo": ubig2, "etapa": etapa2},
                                    multipart=form,
                                    timeout=600000,
                                )
                                try:
                                    j3 = r3.json()
                                except Exception:
                                    j3 = None
                                if isinstance(j3, dict):
                                    _default_log(f"[WHADOX] [{acc.get('name')}] Reintento POST por filas=0 ok={j3.get('ok')} rows={j3.get('rows')} message={j3.get('message')}")
                                    rows_cnt = j3.get("rows", rows_cnt)
                            except Exception as epost0:
                                _default_log(f"[WHADOX] [{acc.get('name')}] Reintento POST por filas=0 falló: {epost0}")
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
