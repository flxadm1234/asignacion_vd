import os
import sys
from pathlib import Path
from datetime import datetime
import re
import mimetypes

# ---------------------------------------------------------
# ARREGLO ESENCIAL PARA PLAYWRIGHT + PYINSTALLER + TKINTER
# ---------------------------------------------------------

os.environ["PLAYWRIGHT_SYNC_API"] = "1"
os.environ["PYTHONASYNCIODEBUG"] = "0"
os.environ["PWDEBUG"] = "0"

# Detectar si estamos dentro del EXE de PyInstaller
# Ruta donde Playwright instalará Chromium
if sys.platform == "win32":
    PLAYWRIGHT_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ms-playwright"
else:
    base_runtime = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidate = base_runtime / "ms-playwright"
    if candidate.exists():
        PLAYWRIGHT_DIR = candidate
    else:
        PLAYWRIGHT_DIR = Path.home() / ".cache" / "ms-playwright"

PLAYWRIGHT_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_DIR))


from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from settings import (
    SEAAP_WIZARD_URL,
    WHADOX_LOGIN_URL,
    WHADOX_MANT_URL,
    DATA_DIR,
    Account,
    log,
    load_accounts,
    load_general_config,
)


# ================================
# 🔹 Verificar navegadores instalados
# ================================
def ensure_playwright_browsers():
    """
    Verifica si Chromium está instalado.
    Si NO está → lo instala automáticamente.
    Funciona en EXE y también en Python normal.
    """
    from playwright.__main__ import main as pw_cli

    try:
        # Verificar si Chromium ya funciona
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return  # Ya está instalado
    except Exception:
        print("Instalando navegadores Playwright...")

    # Instalar Chromium (silencioso)
    try:
        old_argv = sys.argv.copy()
        sys.argv = ["playwright", "install", "chromium"]
        pw_cli()
    finally:
        sys.argv = old_argv

    # Verificar instalación
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
    except Exception as e:
        raise RuntimeError(
            "Playwright no pudo instalar Chromium automáticamente.\n"
            "Instala manualmente con:\n\n"
            "   python -m playwright install chromium\n\n"
            f"Detalle: {e}"
        )



# ================================
# 🔹 Abrir navegador
# ================================
def open_browser(headless: bool = False):
    log(f"[NAVEGADOR] (flows) open_browser: solicitado headless={headless}")
    p = sync_playwright().start()
    
    browser_args = [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    # Optimización para Raspberry Pi (Linux)
    if sys.platform != "win32":
        browser_args.append("--disable-gpu")
        browser_args.append("--disable-software-rasterizer")

    final_headless = False
    browser = p.chromium.launch(
        headless=final_headless,
        args=browser_args,
    )
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    log(f"[NAVEGADOR] (flows) Chromium lanzado (visible).")
    return p, browser, ctx, page


# ================================
# 🔹 Espera inteligente
# ================================
def smart_wait(page, selector: str, timeout: int = 60_000):
    try:
        return page.wait_for_selector(selector, timeout=timeout)
    except Exception:
        page.wait_for_timeout(2_000)
        return None


# ================================
# 🔹 FLUJO SEAAP
# ================================
def flow_seaap(page, account: Account) -> Path:
    log(f"[{account.name}] STEP 1: Abriendo SEAAP…")
    page.goto(SEAAP_WIZARD_URL, wait_until="domcontentloaded", timeout=180_000)

    # Login
    try:
        if page.locator("input[type=password]").count():
            log(f"[{account.name}] SEAAP: pantalla de login, rellenando…")

            user = page.locator(
                "#login, input[name='login'], input[placeholder*='Usuario' i], input[type='text']"
            )
            pwd = page.locator(
                "#password, input[name='password'], input[type='password'], input[placeholder*='Contraseña' i]"
            )

            if user.count():
                user.first.fill(account.seaap_user)
            if pwd.count():
                pwd.first.fill(account.seaap_password)

            btn = page.locator(
                "button:has-text('Ingresar'), button:has-text('Iniciar sesión'), button[type='submit'], input[type='submit']"
            )
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(2_500)

    except Exception as e:
        log(f"[{account.name}] SEAAP: Error durante login (continuamos): {e}")

    # Esperar formulario
    try:
        page.wait_for_selector(".o_form_view, .o_view_controller, .o_content", timeout=120_000)
    except:
        page.wait_for_timeout(2_000)

    # Buscar botón de exportación
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
        log(f"[{account.name}] SEAAP: buscando botón de exportación… intento {intento+1}/8")

        for sel in EXPORT:
            if page.locator(sel).count():
                export_btn = page.locator(sel).first
                break

        if export_btn:
            break

        page.wait_for_timeout(2_000)

    if not export_btn:
        fname = f"seaap_no_btn_{account.seaap_user}"
        page.screenshot(path=str(DATA_DIR / f"{fname}.png"), full_page=True)
        (DATA_DIR / f"{fname}.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(f"[{account.name}] SEAAP: No se encontró botón de exportación.")

    log(f"[{account.name}] SEAAP: descargando archivo…")

    with page.expect_download(timeout=180_000) as dl_info:
        export_btn.click()

    dl = dl_info.value
    suggested = dl.suggested_filename or "reporte.xls"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = DATA_DIR / f"seaap_{account.seaap_user}_{stamp}_{suggested}"
    dl.save_as(str(final_path))

    try:
        size = final_path.stat().st_size
        with open(final_path, "rb") as f:
            head = f.read(8)
        sig = head.hex().upper()
        kind = "desconocido"
        if head.startswith(b"PK"):
            kind = "xlsx/zip"
        elif head.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
            kind = "xls/ole"
        elif head.startswith(b"\xFF\xFE") or head.startswith(b"\xEF\xBB\xBF") or b"," in head:
            kind = "csv/texto"
        log(f"[{account.name}] SEAAP: archivo guardado en {final_path} (tam={size} bytes, magic={sig}, tipo={kind})")
        if size < 1024:
            (DATA_DIR / f"seaap_small_{account.seaap_user}_{stamp}.html").write_text(page.content(), encoding="utf-8")
            raise RuntimeError(f"[{account.name}] SEAAP: descarga sospechosa (archivo <1KB). Se guardó HTML para diagnóstico.")
    except Exception as e:
        log(f"[{account.name}] SEAAP: diagnóstico del archivo falló: {e}")

    return final_path


# ================================
# 🔹 FLUJO WHADOX (VERSIÓN ROBUSTA)
# ================================
def flow_whadox(page, account: Account, excel_path: Path, etapa: str = "") -> None:
    if not excel_path.exists():
        raise RuntimeError(f"[{account.name}] ERROR: archivo no existe ({excel_path})")

    log(f"[{account.name}] STEP 2: Ingresando a Whadox…")
    page.goto(WHADOX_LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)

    # Login
    page.fill("#dni", account.whadox_dni)
    page.fill("#pass", account.whadox_password)

    btn_login = None
    for sel in ["button.login-form-btn", "button:has-text('CONECTAR')", "button[type='submit']"]:
        if page.locator(sel).count():
            btn_login = page.locator(sel).first
            break

    if not btn_login:
        base = f"whadox_no_login_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
        raise RuntimeError(f"[{account.name}] Whadox: No encontré botón login.")

    btn_login.click()
    page.wait_for_timeout(1_500)

    # Cargar mantenimiento con reintentos
    for intento in range(6):
        try:
            log(f"[{account.name}] Whadox: cargando Mantenimiento… intento {intento+1}/6")
            page.goto(WHADOX_MANT_URL, wait_until="domcontentloaded", timeout=90_000)

            if page.locator('input[type="file"]').count():
                break

            page.wait_for_timeout(2_000)
        except:
            page.wait_for_timeout(3_000)
    else:
        fname = f"whadox_mant_fail_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{fname}.png"), full_page=True)
        raise RuntimeError(f"[{account.name}] Whadox: No pudo cargar Mantenimiento.")

    cont = None
    for _ in range(30):
        locator = page.locator("div.card:has(h2:has-text('Verificar Asignación SEAAP'))")
        if locator.count():
            cont = locator.first
            break
        page.wait_for_timeout(800)
    if not cont:
        base = f"whadox_no_section_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
        (DATA_DIR / f"{base}.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(f"[{account.name}] Whadox: No se encontró sección 'Verificar Asignación SEAAP'.")
    log(f"[{account.name}] Whadox: sección 'Verificar Asignación SEAAP' localizada.")

    file_input = None
    for _ in range(30):
        if cont.locator('#archivo5, input[type="file"]').count():
            file_input = cont.locator('#archivo5, input[type="file"]').first
            break
        page.wait_for_timeout(800)

    if not file_input:
        fname = f"whadox_no_input_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{fname}.png"), full_page=True)
        raise RuntimeError(f"[{account.name}] Whadox: input file no encontrado.")

    log(f"[{account.name}] Whadox: adjuntando {excel_path.name}")
    file_input.set_input_files(str(excel_path))
    try:
        files_len = page.evaluate("""() => {
            const el = document.querySelector('#archivo5') || document.querySelector('input[type="file"]');
            return el && el.files ? el.files.length : 0;
        }""")
        log(f"[{account.name}] Whadox: input files adjuntos: {files_len}")
    except Exception:
        pass
    # Reintento si el input se re-renderiza y pierde el archivo
    try:
        for _ in range(5):
            cur = page.evaluate("""() => {
                const el = document.querySelector('#archivo5') || document.querySelector('input[type="file"]');
                return el && el.files ? el.files.length : 0;
            }""")
            if cur and int(cur) >= 1:
                break
            file_input.set_input_files(str(excel_path))
            page.wait_for_timeout(700)
            log(f"[{account.name}] Whadox: re-adjuntando archivo (reintento)…")
    except Exception:
        pass

    if etapa:
        try:
            if cont.locator("#etapa3, input[type='date'][name='etapa3']").count():
                cont.locator("#etapa3, input[type='date'][name='etapa3']").first.fill(etapa)
                try:
                    cur_etapa = cont.locator("#etapa3, input[type='date'][name='etapa3']").first.input_value()
                    log(f"[{account.name}] Whadox: etapa seleccionada: {cur_etapa}")
                except Exception:
                    pass
        except Exception:
            pass

    # Buscar botón subir
    subir_btn = None
    prefer_sel = 'button[onclick*="subirArchivos5"]'
    if cont.locator(prefer_sel).count():
        subir_btn = cont.locator(prefer_sel).first
    else:
        for sel in [
            'button:has-text("Subir Archivo")',
            'button:has-text("SUBIR ARCHIVO")',
            'button:has-text("SUBIR")',
            'button:has-text("Subir")',
            "button.btn-success",
            "button[type='submit']",
        ]:
            if cont.locator(sel).count():
                subir_btn = cont.locator(sel).first
                break

    if not subir_btn:
        fname = f"whadox_no_subir_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{fname}.png"), full_page=True)
        raise RuntimeError(f"[{account.name}] Whadox: No encontré botón SUBIR.")

    log(f"[{account.name}] Whadox: subiendo archivo…")
    # Capturar la respuesta del backend justo al disparar la subida
    try:
        with page.expect_response(lambda r: ("archivos/cargardataseaap2.php" in r.url), timeout=600_000) as resp_info:
            try:
                # Forzar llamada directa a la función con el ubigeo indicado
                ubig = int(re.sub(r"\\D+", "", account.name)) if re.search(r"\\d", account.name) else account.name
                page.evaluate("ub => { try { subirArchivos5(ub); } catch(e) { console.error(e); } }", ubig)
            except Exception:
                # Fallback: clic en el botón detectado
                subir_btn.click()
        resp = resp_info.value
        try:
            log(f"[{account.name}] Whadox: HTTP status AJAX: {resp.status}")
        except Exception:
            pass
        resp_text = resp.text()
        rows_from_json = None
        try:
            j = resp.json()
            if isinstance(j, dict) and j.get("ok"):
                rows_from_json = j.get("rows")
                log(f"[{account.name}] Whadox: JSON ok={j.get('ok')} rows={j.get('rows')} message={j.get('message')}")
        except Exception:
            pass
        log(f"[{account.name}] Whadox: Respuesta servidor (cargardataseaap2.php): {resp_text[:500]}")
        ok_by_text = bool(re.search(r"Se han cargado\\s+\\d+\\s+datos", resp_text, flags=re.IGNORECASE))
        err_by_text = bool(re.search(r"error", resp_text, flags=re.IGNORECASE))
        if rows_from_json or ok_by_text:
            title_sel = "#swal2-title"
            try:
                page.wait_for_selector(title_sel, state="visible", timeout=600_000)
                for _ in range(600):
                    t = page.locator(title_sel).first.inner_text()
                    log(f"[{account.name}] Whadox: título actual: {t}")
                    if "Verificando Asignación SEAAP" in t:
                        page.wait_for_timeout(1000)
                        continue
                    break
            except Exception:
                pass
            try:
                if page.locator(".swal2-confirm").count():
                    page.locator(".swal2-confirm").first.click()
                    log(f"[{account.name}] Whadox: confirmación modal clic")
            except Exception:
                pass
            log(f"[{account.name}] Whadox: Carga confirmada. Filas: {rows_from_json if rows_from_json is not None else 'N/D'}")
            base = f"whadox_post_upload_{account.whadox_dni}"
            page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
            return
        if err_by_text:
            log(f"[{account.name}] Whadox: respuesta indica error, se intentará POST directo: {resp_text[:200]}")
    except PWTimeout:
        log(f"[{account.name}] Whadox: No se recibió respuesta AJAX en el tiempo esperado (10 min). Se intentará validar por modal.")

    # ======================================================
    # 🟩 FALLBACK DURO: POST DIRECTO MULTIPART (manteniendo cookies de sesión)
    # ======================================================
    try:
        ubig = int(re.sub(r"\\D+", "", account.name)) if re.search(r"\\d", account.name) else account.name
        url = "https://sinanemia.site/appc/archivos/cargardataseaap2.php"
        mime = mimetypes.guess_type(str(excel_path))[0] or "application/octet-stream"
        with open(excel_path, "rb") as f:
            buf = f.read()
        resp2 = page.request.post(
            url,
            params={"ubigeo": str(ubig), "etapa": etapa or ""},
            multipart={
                "archivo5": {
                    "name": excel_path.name,
                    "mimeType": mime,
                    "buffer": buf,
                }
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json,*/*", "Origin": "https://sinanemia.site", "Referer": WHADOX_MANT_URL},
            timeout=300_000,
        )
        try:
            log(f"[{account.name}] Whadox: HTTP status POST directo: {resp2.status}")
        except Exception:
            pass
        txt2 = resp2.text()
        log(f"[{account.name}] Whadox: POST directo respuesta: {txt2[:500]}")
        # Intentar parseo JSON primero
        ok_flag = False
        rows_cnt = None
        try:
            j = resp2.json()
            ok_flag = bool(j.get("ok"))
            rows_cnt = j.get("rows")
            log(f"[{account.name}] Whadox: JSON POST ok={j.get('ok')} rows={j.get('rows')} message={j.get('message')}")
        except Exception:
            pass
        if not ok_flag:
            # Evaluar por texto plano
            m2 = re.search(r"Se han cargado\\s+(\\d+)\\s+datos", txt2, flags=re.IGNORECASE)
            if m2:
                ok_flag = True
                rows_cnt = int(m2.group(1))
        if ok_flag:
            try:
                page.wait_for_selector("div.swal2-popup.swal2-modal", state="visible", timeout=10_000)
                if page.locator(".swal2-confirm").count():
                    page.locator(".swal2-confirm").first.click()
                    log(f"[{account.name}] Whadox: confirmación modal clic (POST)")
            except Exception:
                pass
            log(f"[{account.name}] Whadox: ¡Carga confirmada por POST directo! ✔ Filas: {rows_cnt if rows_cnt is not None else 'N/D'}")
            base = f"whadox_post_upload_{account.whadox_dni}"
            page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
            return
        else:
            log(f"[{account.name}] Whadox: POST directo no confirmó éxito.")
    except Exception as e:
        log(f"[{account.name}] Whadox: Error en POST directo: {e}")

    # ======================================================
    # 🟩 FALLBACK FINAL: fetch() con FormData del propio input (emular jQuery)
    # ======================================================
    try:
        ubig = int(re.sub(r"\\D+", "", account.name)) if re.search(r"\\d", account.name) else account.name
        txt3 = page.evaluate(
            """async ([ub, et]) => {
                try {
                    const form = document.querySelector('#miFormulariodataseaap2');
                    const fd = new FormData(form);
                    const r = await fetch('archivos/cargardataseaap2.php?ubigeo=' + ub + '&etapa=' + et, {
                        method: 'POST',
                        body: fd,
                        headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json,*/*'}
                    });
                    const t = await r.text();
                    try { window.__ultimaCargaSeaap5 = JSON.parse(t); } catch(e) {
                        window.__ultimaCargaSeaap5 = { ok: /Se han cargado\\s+\\d+\\s+datos/i.test(t), rows: 0, message: t };
                    }
                    return t;
                } catch(e) {
                    return 'ERR:' + (e && e.message ? e.message : String(e));
                }
            }""",
            [str(ubig), str(etapa or "")],
        )
        log(f"[{account.name}] Whadox: fetch(FormData) respuesta: {txt3[:500]}")
        ok3 = False
        rows3 = None
        try:
            j3 = page.evaluate("() => window.__ultimaCargaSeaap5 || null")
            ok3 = bool(j3 and j3.ok)
            rows3 = j3 and j3.rows
        except Exception:
            pass
        if not ok3:
            m3 = re.search(r"Se han cargado\\s+(\\d+)\\s+datos", txt3, flags=re.IGNORECASE)
            if m3:
                ok3 = True
                rows3 = int(m3.group(1))
        if ok3:
            try:
                page.wait_for_selector("div.swal2-popup.swal2-modal", state="visible", timeout=30_000)
                if page.locator(".swal2-confirm").count():
                    page.locator(".swal2-confirm").first.click()
            except Exception:
                pass
            log(f"[{account.name}] Whadox: ¡Carga confirmada por fetch(FormData)! ✔ Filas: {rows3 if rows3 is not None else 'N/D'}")
            base = f"whadox_post_upload_{account.whadox_dni}"
            page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
            return
    except Exception as e:
        log(f"[{account.name}] Whadox: Error en fetch(FormData): {e}")

    # ======================================================
    # 🟩 SI NO HUBO RESPUESTA (versiones antiguas), DISPARO Y ESPERA POLLING
    # ======================================================
    try:
        # Dispara si nada se disparó
        if subir_btn:
            subir_btn.click()
    except Exception:
        pass

    # ======================================================
    # 🟩 ESPERAR BARRA DE PROGRESO AL 100% O DESAPARECER
    # ======================================================
    try:
        if page.locator("#progressbar5").count():
            for _ in range(600):
                val = page.locator("#progressbar5").evaluate("el => el.value")
                if val and float(val) >= 99.0:
                    break
                page.wait_for_timeout(1000)
    except Exception:
        pass

    # ======================================================
    # 🟩 ESPERAR MARCADOR JS window.__ultimaCargaSeaap5 (si el sitio lo expone)
    # ======================================================
    try:
        for _ in range(300):
            info = page.evaluate("() => window.__ultimaCargaSeaap5 || null")
            if info and isinstance(info, dict) and info.get("ok"):
                log(f"[{account.name}] Whadox: marcador JS ok, filas={info.get('rows')}")
                break
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # ======================================================
    # 🟩 DETECTAR MODAL SweetAlert2 "Proceso completado"
    # ======================================================
    log(f"[{account.name}] Whadox: esperando modal de resultado (SweetAlert2)…")
    try:
        modal_sel = "div.swal2-popup.swal2-modal"
        page.wait_for_selector(modal_sel, state="visible", timeout=600_000)

        # Esperar explícitamente ícono de éxito o error
        icon_success = page.locator(".swal2-icon-success")
        icon_error = page.locator(".swal2-icon-error")
        success = False
        error = False
        for _ in range(60):
            if icon_success.count():
                success = True
                break
            if icon_error.count():
                error = True
                break
            page.wait_for_timeout(1000)

        title_text = page.locator("#swal2-title").first.inner_text() if page.locator("#swal2-title").count() else ""
        html_text = page.locator("#swal2-html-container").first.inner_text() if page.locator("#swal2-html-container").count() else ""
        log(f"[{account.name}] Whadox: Modal título='{title_text}' detalle='{html_text}'")

        # Extraer número de filas cargadas si existe (ej. 'Se han cargado 740 datos al sistema.')
        m = re.search(r"cargado(?:s)?\\s+(\\d+)\\s+dato", html_text, flags=re.IGNORECASE)

        if error or "error" in (title_text + " " + html_text).lower():
            raise RuntimeError(f"[{account.name}] Whadox: resultado indica error: {html_text}")

        if not success and not m:
            # Si no detectamos éxito ni conteo, intentamos fallback más abajo
            raise PWTimeout("Modal sin indicador claro de éxito")

        # Cerrar modal
        try:
            if page.locator(".swal2-confirm").count():
                page.locator(".swal2-confirm").first.click()
        except:
            pass

        log(f"[{account.name}] Whadox: ¡Carga confirmada correctamente! ✔️ Filas: {(m.group(1) if m else 'N/D')}")
        base = f"whadox_post_upload_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)
        log(f"[{account.name}] Whadox: SUBIDA OK ✔")
        return
    except PWTimeout:
        log(f"[{account.name}] Whadox: Modal no concluyente o no apareció a tiempo. Intentando detección alternativa de texto de éxito…")

    SUCCESS_SELECTORS = [
        "text=Carga completada",
        "text=Subida completada",
        "h1:has-text('Carga completada')",
        "h2:has-text('Carga completada')",
        "div:has-text('Carga completada')",
        "span:has-text('Carga completada')",
        "p:has-text('Carga completada')",
        "pre:has-text('Carga completada')",
        "div.alert-success",
    ]

    success_found = False

    for sel in SUCCESS_SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=120_000, state="visible")
            success_found = True
            log(f"[{account.name}] Whadox: detectado mensaje de éxito con selector: {sel}")
            break
        except PWTimeout:
            continue

    if not success_found:
        fname = f"whadox_no_success_text_{account.whadox_dni}"
        page.screenshot(path=str(DATA_DIR / f"{fname}.png"), full_page=True)
        (DATA_DIR / f"{fname}.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(
            f"[{account.name}] ERROR: No pude detectar el mensaje 'Carga completada'. "
            f"Revisa {fname}.*"
        )

    log(f"[{account.name}] Whadox: ¡Carga confirmada correctamente! ✔️")


    # Evidencia final
    base = f"whadox_post_upload_{account.whadox_dni}"
    page.screenshot(path=str(DATA_DIR / f"{base}.png"), full_page=True)

    log(f"[{account.name}] Whadox: SUBIDA OK ✔")


# ================================
# 🔹 Ejecutar por cuenta
# ================================
def run_for_account(account: Account, headless: bool = False, etapa: str = "") -> None:
    log(f"========== INICIANDO PROCESO PARA {account.name} ({account.seaap_user}) ==========")

    p1, b1, c1, pg1 = open_browser(headless)
    try:
        excel_path = flow_seaap(pg1, account)
    finally:
        c1.close()
        b1.close()
        p1.stop()

    p2, b2, c2, pg2 = open_browser(headless)
    try:
        flow_whadox(pg2, account, excel_path, etapa=etapa)
    finally:
        c2.close()
        b2.close()
        p2.stop()

    log(f"========== PROCESO FINALIZADO PARA {account.name} ==========")


# ================================
# 🔹 Ejecutar todas las cuentas
# ================================
def run_all_accounts() -> None:
    ensure_playwright_browsers()
    cfg = load_general_config()
    headless = bool(cfg.get("headless", False))

    accounts = load_accounts()
    log(f"Se encontraron {len(accounts)} cuentas en accounts.json")

    for acc in accounts:
        try:
            run_for_account(acc, headless=headless)
        except Exception as e:
            log(f"[{acc.name}] ERROR en ejecución: {e}")
            continue

    log("TODAS LAS CUENTAS HAN SIDO PROCESADAS.")
