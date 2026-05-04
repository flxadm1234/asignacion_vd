# browser_seaap.py
"""
Automatización SEAAP – Padrón Nominal / Buscar DNI del Niño
Versión optimizada y robusta.
"""

import sys
import subprocess
import os
import re
from playwright.sync_api import sync_playwright
from db_utils import marcar_registro_consistente
from pathlib import Path


from config import (
    BROWSERS_DIR,
    PROFILE_DIR,
)

SEAAP_BASE_URL = "https://visitasdomiciliarias.minsa.gob.pe"
SEAAP_LOGIN_URL = f"{SEAAP_BASE_URL}/es_419/web/login"
PADRON_URL = f"{SEAAP_BASE_URL}/odoo/action-314/1/action-317"



# ============================================================
# INSTALAR CHROMIUM SI FALTA
# ============================================================
# ============================================================
# INSTALAR CHROMIUM SOLO SI FALTA (sin rutas fijas)
# ============================================================
def ensure_playwright_browsers(log):
    import os
    import sys
    import subprocess
    from playwright.sync_api import sync_playwright

    # Intentar ejecutar la verificación en un subproceso para evitar conflictos con asyncio loops
    # Si ya estamos en un subproceso (evitar recursión infinita si usáramos la misma función como entrypoint)
    # Pero aquí simplemente usaremos el CLI de playwright o un script pequeño.
    
    # 1) Verificación rápida en proceso actual (si no hay conflicto de loop)
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.exists(exe):
                log(f"Playwright: Chromium ya está instalado. OK → {exe}")
                return True
    except Exception as e:
        log(f"[WARN] Verificación directa de Playwright falló ({e}). Intentando vía subproceso...")

    # 2) Verificación/Instalación vía subproceso (robusto ante asyncio loops)
    try:
        # Script para verificar/instalar
        script = """
import sys
import os
from playwright.sync_api import sync_playwright

def check():
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.exists(exe):
                print(f"OK:{exe}")
                return
    except Exception:
        pass
    
    # Instalar
    from playwright.__main__ import main
    sys.argv = ["playwright", "install", "chromium"]
    try:
        main()
    except SystemExit:
        pass
        
    # Verificar de nuevo
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.exists(exe):
                print(f"OK:{exe}")
            else:
                print("FAIL")
    except Exception as e:
        print(f"FAIL:{e}")

if __name__ == "__main__":
    check()
"""
        # Ejecutar script
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )
        
        output = res.stdout.strip()
        if output.startswith("OK:"):
            exe = output.split(":", 1)[1]
            log(f"Playwright: Chromium verificado/instalado externo. OK → {exe}")
            return True
        else:
            log(f"[ERROR] Subproceso Playwright falló: {output} / {res.stderr}")
            raise RuntimeError("No se pudo asegurar Chromium (subproceso falló).")

    except Exception as e:
        log(f"[ERROR] No se pudo asegurar Chromium: {e}")
        raise

# ============================================================
# ABRIR NAVEGADOR PERSISTENTE
# ============================================================
def open_browser(headless, log):

    log(f"[NAVEGADOR] open_browser: solicitado headless={headless}")
    no_display = (sys.platform != "win32") and (not os.environ.get("DISPLAY"))
    final_headless = bool(headless) or no_display
    if no_display and not headless:
        log("[NAVEGADOR] No se detectó DISPLAY. Forzando modo headless.")
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=final_headless,
        args=[
            "--start-maximized",
            "--window-position=0,0",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    if final_headless:
        log(f"[NAVEGADOR] Chromium lanzado (persistente, headless).")
    else:
        log(f"[NAVEGADOR] Chromium lanzado (persistente, visible).")
    return p, browser, ctx, page

# ============================================================
# LOGOUT
# ============================================================
def logout_seaap(page, log):
    log("[SEAAP] Cerrando sesión (Logout)…")
    try:
        logout_urls = [
            f"{SEAAP_BASE_URL}/web/session/logout",
            f"{SEAAP_BASE_URL}/es_419/web/session/logout",
            f"{SEAAP_BASE_URL}/odoo/web/session/logout",
            f"{SEAAP_BASE_URL}/es_419/odoo/web/session/logout",
        ]
        for u in logout_urls:
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                if page.locator("input[type=password]").count() > 0:
                    log("[SEAAP] Sesión cerrada correctamente.")
                    return True
            except Exception:
                pass

        # Si no funcionó, intentar vía menú (fallback)
        log("[SEAAP] Logout directo no detectado, intentando menú usuario…")
        user_menu = page.locator(".o_user_menu .dropdown-toggle, .o_user_menu > a, li.o_user_menu")
        if user_menu.count() > 0:
            user_menu.first.click()
            page.wait_for_timeout(800)
            logout_btn = page.locator("a[data-menu='logout'], a[href='/web/session/logout']")
            if logout_btn.count() > 0:
                logout_btn.first.click()
                page.wait_for_timeout(3000)
            else:
                log("[SEAAP][WARN] No se encontró botón logout en el menú.")
        
        # Verificar de nuevo
        if page.locator("input[type=password]").count() > 0:
            log("[SEAAP] Sesión cerrada (vía menú).")
            return True

        # Fallback final: Borrar cookies
        log("[SEAAP][WARN] No se pudo cerrar sesión. Borrando cookies forzosamente.")
        page.context.clear_cookies()
        page.goto(SEAAP_LOGIN_URL, wait_until="domcontentloaded")
        return True

    except Exception as e:
        log(f"[SEAAP][ERROR] Fallo crítico al cerrar sesión: {e}")
        return False


# ============================================================
# LOGIN + IR A PADRÓN NOMINAL
# ============================================================
def login_seaap(page, user, pwd, log):
    log("Ingresando al Padrón Nominal…")
    try:
        page.goto(PADRON_URL, wait_until="domcontentloaded", timeout=180_000)
        page.wait_for_timeout(2000)
    except Exception:
        page.goto(SEAAP_LOGIN_URL, wait_until="domcontentloaded", timeout=180_000)
        page.wait_for_timeout(2000)

    # 0. VERIFICAR SI HAY SESIÓN ACTIVA DE OTRA CUENTA
    if page.locator("input[type=password]").count() == 0:
        log("[SEAAP] Se detectó una sesión activa. Cerrando para asegurar cuenta correcta…")
        logout_seaap(page, log)
        page.goto(SEAAP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

    # pantalla de login
    if page.locator("input[type=password]").count() > 0:
        log("Login detectado, ingresando credenciales…")

        user_in = page.locator("#login, input[name=login]").first
        pwd_in = page.locator("#password, input[name=password]").first

        user_in.fill(str(user))
        pwd_in.fill(str(pwd))

        # ✅ botón REAL: "Iniciar Sesión"
        btn = page.locator("button[type=submit].btn.btn-primary").first

        # fallback por texto (por si cambian clases)
        if btn.count() == 0:
            btn = page.locator("button[type=submit]:has-text('Iniciar Sesión')").first

        # último fallback (por si el texto cambia)
        if btn.count() == 0:
            btn = page.locator("button[type=submit], input[type=submit]").first

        # Asegurar que esté visible y habilitado
        btn.wait_for(state="visible", timeout=60_000)

        # ✅ click sin esperar navegación (evita el freeze de 30s)
        btn.click(timeout=60_000, force=True, no_wait_after=True)

        # Esperar a que la URL cambie (éxito de login) o aparezca error
        log("Esperando validación de ingreso…")
        try:
             page.wait_for_url(lambda u: ("/web/login" not in u) and ("/es_419/web/login" not in u), timeout=15000)
             log("[SEAAP] Login exitoso detectado por cambio de URL.")
        except:
             log("[SEAAP][WARN] URL no cambió rápido. Verificando estado…")

        # Si seguimos en login, intentar un clic más
        if page.locator("input[type=password]").count() > 0:
             log("[SEAAP][WARN] Parece que seguimos en Login. Reintentando clic…")
             btn.click(force=True, no_wait_after=True)
             page.wait_for_timeout(3000)
        
        # ✅ volver al padrón sí o sí, PERO con espera de red
        log("Navegando al Padrón Nominal post-login…")
        try:
            page.goto(PADRON_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            
            # Verificar si cargó la lista
            if page.locator(".o_list_view").count() == 0:
                log("[SEAAP][WARN] No parece haber cargado la lista. Reintentando navegación…")
                page.goto(PADRON_URL, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2000)

        except Exception as e:
            log(f"[SEAAP][WARN] Timeout navegando al Padrón: {e}")

    log("Página cargada.")



# ============================================================
# LIMPIAR ETIQUETAS DE BÚSQUEDA
# ============================================================
def clear_search_facets(page, log):
    for _ in range(8):
        rm = page.locator(".o_facet_remove")
        if rm.count():
            rm.first.click()
            page.wait_for_timeout(200)
        else:
            break
        
        
        
def _locator_autocomplete_input(page, placeholder):
    loc = page.locator(
        f"input.o-autocomplete--input[placeholder='{placeholder}'], "
        f"input.o_form_input.ui-autocomplete-input[placeholder='{placeholder}'], "
        f"input.o_form_input[placeholder='{placeholder}'], "
        f"input[placeholder='{placeholder}']"
    )
    if loc.count() == 0:
        return None
    return loc.first


def _locator_autocomplete_options(page):
    return page.locator(
        "ul.ui-autocomplete li a, "
        ".o-autocomplete.dropdown .dropdown-menu.show a, "
        ".o-autocomplete.dropdown .dropdown-menu.show .dropdown-item, "
        ".dropdown-menu.show a.dropdown-item, "
        ".dropdown-menu.show .dropdown-item, "
        "[role='listbox'] [role='option'], "
        "ul[role='listbox'] li, "
        "div[role='listbox'] [role='option']"
    )


def _click_autocomplete_option(page, valor):
    valor = str(valor).strip()
    options = _locator_autocomplete_options(page)
    if options.count() == 0:
        return False
    try:
        texts = [t.strip() for t in options.all_inner_texts()]
    except Exception:
        try:
            texts = [t.strip() for t in options.all_text_contents()]
        except Exception:
            texts = []
    if texts:
        for i, t in enumerate(texts):
            if t.lower() == valor.lower():
                try:
                    options.nth(i).click(force=True)
                    return True
                except Exception:
                    break
    opcion = options.filter(has_text=valor)
    if opcion.count() > 0:
        try:
            opcion.first.click(force=True)
            return True
        except Exception:
            return False
    if texts and len(texts) == 1 and texts[0].lower() == valor.lower():
        try:
            options.first.click(force=True)
            return True
        except Exception:
            return False
    return False


# ============================================================
# SELECCIONAR TIPO DE CENTRO POBLADO (URBANO / RURAL)
# ============================================================
def seleccionar_tipo_centro_poblado(page, tipo, log):
    """
    Selección ultra robusta de Rural/Urbano.
    - Hace clic
    - Espera recarga (2–3 segundos)
    - Verifica que 'Centro poblado' esté listo
    - Reintenta si la UI no cargó
    """

    if not tipo:
        log("[SEAAP] No se recibió 'tipo_centro_poblado'.")
        return False

    tipo_norm = tipo.strip().lower()  # urbano / rural

    log(f"[SEAAP] Seleccionando Tipo Centro Poblado: {tipo_norm}")

    radio = page.locator(
        f"div[name='tipo_centropoblado'] input.o_radio_input[data-value='{tipo_norm}'], "
        f"div[name='tipo_centropoblado'] input[type='radio'][data-value='{tipo_norm}'], "
        f"input.o_radio_input[data-value='{tipo_norm}'], "
        f"input[type='radio'][data-value='{tipo_norm}'], "
        f"input#radio_field_1_{tipo_norm}"
    )

    if radio.count() == 0:
        log(f"[SEAAP][ERROR] No existe el radio button '{tipo_norm}'.")
        return False

    loading = page.locator(".o_loading, .o_view_loading, .o_spinner, .o_list_view_loading")

    # Intentaremos hasta 3 veces por fallos de carga de Odoo
    for intento in range(1, 4):
        try:
            log(f"[SEAAP] Intento {intento}/3 → seleccionar '{tipo_norm}'")

            radio.first.click(force=True)
            page.wait_for_timeout(150)

            # Cerrar modales o overlays
            cerrar_todos_los_modales(page, log)
            watchdog_recovery(page, log)

            cp = None
            max_loops = 80 if tipo_norm == "rural" else 40
            for _ in range(max_loops):  # rural suele refrescar más lento
                try:
                    if loading.count() > 0:
                        page.wait_for_timeout(150)
                        continue
                except Exception:
                    pass

                cp = _locator_autocomplete_input(page, "Centro poblado")
                if cp is None and page.locator("input#centropoblado_id_0").count() > 0:
                    cp = page.locator("input#centropoblado_id_0").first

                if cp is not None:
                    try:
                        _ = cp.input_value()
                        try:
                            if hasattr(cp, "is_enabled") and (not cp.is_enabled()):
                                cp = None
                                page.wait_for_timeout(150)
                                continue
                        except Exception:
                            pass
                        break
                    except Exception:
                        cp = None

                page.wait_for_timeout(150)
            if cp is None:
                log("[SEAAP][WARN] El campo 'Centro poblado' aún no está disponible. Reintentando…")
                continue

            log(f"[SEAAP] Tipo Centro Poblado '{tipo_norm}' seleccionado exitosamente.")
            return True

        except Exception as e:
            log(f"[SEAAP][WARN] Error en intento {intento}: {e}")
            cerrar_todos_los_modales(page, log)
            watchdog_recovery(page, log)

    log("[SEAAP][ERROR] No se pudo seleccionar el tipo de centro poblado tras 3 intentos.")
    return False




def seleccionar_autocomplete(page, selector_input, texto, log):

    log(f"[SEAAP] Seleccionando '{texto}' en {selector_input}…")

    input_box = page.locator(selector_input)

    if input_box.count() == 0:
        raise RuntimeError(f"No se encontró input {selector_input}")

    input_box.first.click()
    input_box.first.fill("")
    page.wait_for_timeout(300)

    for ch in texto:
        input_box.first.type(ch, delay=80)
    page.wait_for_timeout(400)

    # menú jQuery UI
    menu = page.locator("ul.ui-autocomplete li a")

    for _ in range(40):
        if menu.count() > 0:
            break
        page.wait_for_timeout(120)

    if menu.count() == 0:
        raise RuntimeError(f"No apareció menú autocomplete para '{texto}'")

    # buscar opción exacta
    opcion = menu.filter(has_text=texto)
    if opcion.count() == 0:
        raise RuntimeError(f"No existe opción '{texto}' en el autocomplete.")

    opcion.first.click()
    log(f"[SEAAP] Seleccionado: {texto}")
    page.wait_for_timeout(300)


def seleccionar_autocomplete_por_placeholder(page, placeholder, valor, log, max_retries=3, allow_recovery=True):
    """
    Autocompletado robusto excepto para SECTOR,
    el cual se maneja exclusivamente con la función especializada
    seleccionar_sector_rural().
    """

    valor = str(valor).strip()
    log(f"[SEAAP] Seleccionando '{valor}' en placeholder '{placeholder}'…")


    # ============================================================
    # LOCALIZAR INPUT
    # ============================================================
    inp = _locator_autocomplete_input(page, placeholder)
    if inp is None:
        log(f"[SEAAP][ERROR] No se encontró input con placeholder '{placeholder}'.")
        return False

    # ============================================================
    # 0. VERIFICACIÓN INTELIGENTE
    # ============================================================
    try:
        val_actual = inp.input_value().strip()
        # Coincidencia exacta o parcial segura
        if val_actual and (val_actual.lower() == valor.lower() or (len(valor) > 3 and valor.lower() in val_actual.lower())):
             log(f"[SMART] '{placeholder}' ya tiene valor '{val_actual}'. Saltando.")
             return True
    except:
        pass

    # ============================================================
    # REINTENTOS NORMALES
    # ============================================================
    for intento in range(1, max_retries + 1):

        log(f"[SEAAP] Intento {intento}/{max_retries} → {placeholder}='{valor}'")

        watchdog_recovery(page, log)
        cerrar_todos_los_modales(page, log)

        try:
            # limpiar input
            inp.click()
            inp.fill("")
            page.wait_for_timeout(250)

            # escribir texto
            for ch in valor:
                inp.type(ch, delay=70)

            page.wait_for_timeout(450)
            watchdog_recovery(page, log)

            # abrir menú con ArrowDown
            try:
                inp.press("ArrowDown")
            except Exception:
                pass
            page.wait_for_timeout(300)

            menu_items = _locator_autocomplete_options(page)

            # esperar menú
            for _ in range(80):
                if menu_items.count() > 0:
                    break
                page.wait_for_timeout(120)

            if menu_items.count() == 0:
                log(f"[SEAAP][WARN] No apareció menú autocomplete para '{valor}'.")
                continue

            # SELECCIÓN ROBUSTA (Exacta > Parcial)
            try:
                texts = menu_items.all_inner_texts()
            except Exception:
                texts = menu_items.all_text_contents()
            idx_exact = -1
            for idx, t in enumerate(texts):
                if t.strip() == valor:
                    idx_exact = idx
                    break

            if idx_exact >= 0:
                try:
                    menu_items.nth(idx_exact).click(force=True)
                    page.wait_for_timeout(600)
                    log(f"[SEAAP] '{valor}' seleccionado (EXACTO) en '{placeholder}'.")
                    return True
                except:
                    pass

            # Si no hay exacta, intentamos parcial
            opcion = menu_items.filter(has_text=valor)

            if opcion.count() > 0:
                try:
                    opcion.first.click(force=True)
                    page.wait_for_timeout(600)
                    log(f"[SEAAP] '{valor}' seleccionado (PARCIAL) en '{placeholder}'.")
                    return True
                except:
                    pass

            # fallback: única opción
            opciones = menu_items.all_inner_texts()
            if len(opciones) == 1 and opciones[0].strip() == valor:
                menu_items.first.click(force=True)
                page.wait_for_timeout(300)
                return True

            # Si llegamos aquí y el valor está escrito correctamente, aceptarlo
            val_escrito = inp.input_value().strip()
            if val_escrito == valor:
                log(f"[SEAAP] Selección menú falló, pero valor '{val_escrito}' es correcto. Aceptando.")
                inp.press("Enter")
                page.wait_for_timeout(300)
                return True

            # si no seleccionó nada, reintentar
            page.keyboard.press("Tab")
            page.wait_for_timeout(250)
            inp.click()

        except Exception as e:
            log(f"[SEAAP][WARN] Error en intento {intento}: {e}")
            watchdog_recovery(page, log)
            page.wait_for_timeout(500)

    # ============================================================
    # RECUPERACIÓN ESPECIAL PARA MANZANA
    # ============================================================
    if placeholder.lower() == "manzana":
        log("[SEAAP][RECOVERY] Reintentando Zona y luego Manzana…")

        try:
            zona_inp = _locator_autocomplete_input(page, "Zona")
            if zona_inp is not None:
                valor_zona = zona_inp.input_value().strip()
                if valor_zona:
                    if seleccionar_autocomplete_por_placeholder(
                        page, "Zona", valor_zona, log, max_retries=2
                    ):
                        return seleccionar_autocomplete_por_placeholder(
                            page, "Manzana", valor, log, max_retries=1
                        )
        except:
            pass

    log(f"[SEAAP][ERROR] No se pudo seleccionar '{valor}' en '{placeholder}'.")
    return False



def cerrar_todos_los_modales(page, log, max_loops=10):
    """
    Odoo a veces apila múltiples modales (error + advertencia + advertencia + bloqueo).
    Esta función los cierra TODOS en cascada hasta que no quede ninguno.
    """

    try:
        for ciclo in range(1, max_loops + 1):

            modales = page.locator("div.modal.in[role='dialog'], div.modal[role='dialog'], .o_dialog")
            count = modales.count()

            if count == 0:
                return False  # no había modales

            log(f"[SEAAP][MODAL] {count} modales detectados. Cerrando ciclo {ciclo}…")

            # Siempre trabajar con el modal más arriba (último)
            modal = modales.nth(count - 1)

            # Intentar botón Aceptar
            btn_aceptar = modal.locator("button:has-text('Aceptar'), .o_dialog_button_ok, .modal-footer .btn-primary, button:has-text('OK')")
            if btn_aceptar.count():
                btn_aceptar.first.click(force=True)
                page.wait_for_timeout(500)
                continue

            # Intentar botón Cerrar (X)
            btn_close = modal.locator("button.close")
            if btn_close.count():
                btn_close.first.click(force=True)
                page.wait_for_timeout(500)
                continue

            # Último recurso: Escape
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        return True

    except Exception as e:
        log(f"[SEAAP][MODAL-ERROR] Error cerrando modales: {e}")
        return False


def _abrir_formulario_fila(page, row, log, intentos=3):
    """
    Intenta abrir el formulario de la fila dada con distintas estrategias y
    maneja modales de Odoo si aparecen.
    """
    targets = [
        "td[name='periodo_carga'], td[data-field='periodo_carga']",
        "td[name='documento_numero'], td[data-field='documento_numero']",
        "td[name='documento_tipo'], td[data-field='documento_tipo']",
        "td[name='name'], td[data-field='name']",
        "td[name='nombres'], td[data-field='nombres']",
        "td.o_data_cell",
    ]
    for intento in range(1, intentos + 1):
        cerrar_todos_los_modales(page, log)
        watchdog_recovery(page, log)
        for sel in targets:
            cell = row.locator(sel)
            if cell.count():
                try:
                    c = cell.first
                    try:
                        c.scroll_into_view_if_needed(timeout=5000)
                    except Exception:
                        pass

                    c.click(force=True, timeout=60_000)
                    page.wait_for_timeout(800)
                    cerrar_todos_los_modales(page, log)
                    watchdog_recovery(page, log)

                    if page.locator(".o_form_view").count() or page.locator("button.o_form_button_edit").count():
                        log(f"[SEAAP] Fila abierta en formulario (clic en {sel}).")
                        return True

                    if "periodo_carga" in sel:
                        try:
                            page.keyboard.press("Enter")
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                        cerrar_todos_los_modales(page, log)
                        watchdog_recovery(page, log)
                        if page.locator(".o_form_view").count() or page.locator("button.o_form_button_edit").count():
                            log("[SEAAP] Fila abierta en formulario (clic en periodo_carga + Enter).")
                            return True

                    try:
                        c.dblclick(timeout=60_000)
                    except Exception:
                        row.dblclick(timeout=60_000)
                    page.wait_for_timeout(900)
                    cerrar_todos_los_modales(page, log)
                    watchdog_recovery(page, log)
                    if page.locator(".o_form_view").count() or page.locator("button.o_form_button_edit").count():
                        log(f"[SEAAP] Fila abierta en formulario (doble clic en {sel}).")
                        return True
                except Exception:
                    cerrar_todos_los_modales(page, log)
                    watchdog_recovery(page, log)
        # Doble clic
        try:
            row.dblclick()
            page.wait_for_timeout(1200)
            cerrar_todos_los_modales(page, log)
            watchdog_recovery(page, log)
            if page.locator(".o_form_view").count() or page.locator("button.o_form_button_edit").count():
                log("[SEAAP] Fila abierta en formulario (doble clic).")
                return True
        except Exception:
            pass
        cerrar_todos_los_modales(page, log)
        page.wait_for_timeout(500)
    return False



def seleccionar_actor_social(page, nombre, log):
    seleccionar_autocomplete(
        page,
        "input[name='promsa_responsable_nombres']",
        nombre,
        log
    )

def seleccionar_many2one(page, log, label_text, valor_a_escribir):

    valor = str(valor_a_escribir).strip()
    log(f"[SEAAP] Seleccionando {label_text}: {valor}")

    # 1. Encontrar el label correspondiente
    label = page.locator(f"label:has-text('{label_text}')")
    if label.count() == 0:
        raise RuntimeError(f"No se encontró label '{label_text}'")

    # 2. Subir a contenedor del campo (Odoo estructura: div > div > label / input)
    container = label.locator("xpath=../../..")

    # 3. Localizar el input many2one real
    inp = container.locator("input.o-autocomplete--input, input.o_form_input.ui-autocomplete-input, input.o_form_input, input").first
    if inp.count() == 0:
        raise RuntimeError(f"No se encontró input many2one para '{label_text}'.")

    # 4. Limpiar y escribir
    inp.click()
    inp.press("Control+A")
    inp.press("Backspace")
    page.wait_for_timeout(200)

    for ch in valor:
        inp.type(ch, delay=80)

    page.wait_for_timeout(200)

    # 5. Menú autocomplete
    menu = _locator_autocomplete_options(page)

    for _ in range(40):
        if menu.count() > 0:
            break
        page.wait_for_timeout(150)

    if menu.count() == 0:
        raise RuntimeError(f"No apareció autocomplete para '{label_text}'.")

    # 6. Selección exacta
    opcion = menu.filter(has_text=valor)

    if opcion.count() == 0:
        raise RuntimeError(f"No existe la opción '{valor}' en {label_text}.")

    opcion.first.click()
    page.wait_for_timeout(300)

    log(f"[SEAAP] {label_text} seleccionado correctamente.")


def verificar_campo_autocomplete(page, placeholder, valor_esperado, log):
    """
    Devuelve True si el campo contiene EXACTAMENTE el valor seleccionado.
    """
    valor_esperado = str(valor_esperado).strip()

    inp = _locator_autocomplete_input(page, placeholder)
    if inp is None:
        log(f"[SEAAP][ERROR] No se encontró input para verificar '{placeholder}'.")
        return False
    valor_actual = inp.input_value().strip()

    # Verificación exacta
    if valor_actual.lower() == valor_esperado.lower():
        log(f"[SEAAP] Verificado OK → {placeholder} = '{valor_actual}'")
        return True

    # Verificación flexible
    if len(valor_esperado) > 3 and valor_esperado.lower() in valor_actual.lower():
        log(f"[SEAAP] Verificado OK (Flexible) → {placeholder} = '{valor_actual}'")
        return True

    log(f"[SEAAP][ERROR] Verificación falló → {placeholder}='{valor_actual}' "
        f"(esperado '{valor_esperado}')")
    return False

def llenar_formulario_asignacion(page, datos, log):

    tipo_cp = (datos["tipo_centro_poblado"] or "").strip().lower()
    log(f"[SEAAP] Llenando formulario según tipo CP: {tipo_cp}")

    # ===============================================================
    # 1. Seleccionar Tipo Centro Poblado
    # ===============================================================
    if not seleccionar_tipo_centro_poblado(page, datos["tipo_centro_poblado"], log):
        log("[SEAAP][ERROR] Falló Tipo Centro Poblado → Saltando registro.")
        return False

    # ===============================================================
    # 2. Centro Poblado (siempre obligatorio)
    # ===============================================================
    if not seleccionar_autocomplete_robusto(page, "Centro poblado", datos["centro_poblado"], log):
        log("[SEAAP][ERROR] Falló Centro Poblado → Saltando registro.")
        return False

    if not verificar_campo_autocomplete(page, "Centro poblado", datos["centro_poblado"], log):
        return False

    # ===============================================================
    # 3. FLUJO ESPECIAL PARA REGISTROS RURALES
    # ===============================================================
    if tipo_cp == "rural":
        log("[SEAAP] Registro es RURAL → Saltando Zona y Manzana.")

        # SECTOR en modo RURAL (usa función especializada)
        # Rural también usa robusto (NO usar seleccionar_autocomplete_por_placeholder)
        if not seleccionar_autocomplete_robusto(page, "Sector", datos["sector"], log):
            log("[SEAAP][ERROR] Falló Sector (RURAL) → Saltando registro.")
            return False


        if not verificar_campo_autocomplete(page, "Sector", datos["sector"], log):
            return False

        # Actor Social igual que siempre
        if not seleccionar_autocomplete_por_placeholder(page, "Actor Social", datos["nombre"], log):
            log("[SEAAP][ERROR] Falló Actor Social → Saltando registro.")
            return False

        if not verificar_campo_autocomplete(page, "Actor Social", datos["nombre"], log):
            return False

        log("[SEAAP] Formulario RURAL completado correctamente.")
        return True

    # ===============================================================
    # 4. FLUJO COMPLETO PARA URBANO
    # ===============================================================

    # Zona
    if not seleccionar_autocomplete_por_placeholder(page, "Zona", datos["zona"], log):
        log("[SEAAP][ERROR] Falló Zona → Saltando registro.")
        return False

    if not verificar_campo_autocomplete(page, "Zona", datos["zona"], log):
        return False

    # Manzana
    # Aseguramos que Zona esté bien asentada
    if not seleccionar_autocomplete_por_placeholder(page, "Manzana", datos["mz"], log):
        # Intento de recuperación in-situ: borrar y reintentar escribiendo más lento
        log("[SEAAP][RETRY] Falló Manzana. Reintentando con escritura lenta…")
        page.wait_for_timeout(1000)
        if not seleccionar_autocomplete_robusto(page, "Manzana", datos["mz"], log, intentos=2, espera_inicial=1000):
             log("[SEAAP][ERROR] Falló Manzana (Definitivo) → Saltando registro.")
             return False

    if not verificar_campo_autocomplete(page, "Manzana", datos["mz"], log):
        return False

    # Sector debe usarse SIEMPRE con seleccionar_autocomplete_robusto
    if not seleccionar_autocomplete_robusto(page, "Sector", datos["sector"], log):
        log("[SEAAP][ERROR] Falló Sector (URBANO) → Saltando registro.")
        return False



    # Actor social
    if not seleccionar_autocomplete_por_placeholder(page, "Actor Social", datos["nombre"], log):
        log("[SEAAP][ERROR] Falló Actor Social → Saltando registro.")
        return False

    if not verificar_campo_autocomplete(page, "Actor Social", datos["nombre"], log):
        return False

    log("[SEAAP] Formulario URBANO completado y verificado correctamente.")
    return True


def presionar_guardar(page, log):
    btn = page.locator("button.o_form_button_save:has-text('Guardar'), button.o_form_button_save")

    for _ in range(20):
        if btn.count():
            b = btn.first
            try:
                b.wait_for(state="visible", timeout=5000)
            except Exception:
                pass
            try:
                if hasattr(b, "is_enabled") and (not b.is_enabled()):
                    page.wait_for_timeout(250)
                    continue
            except Exception:
                pass
            b.click(timeout=60_000, force=True, no_wait_after=True)
            log("[SEAAP] Guardado solicitado.")
            page.wait_for_timeout(1500)
            return True
        page.wait_for_timeout(200)

    try:
        if page.locator(".o_form_view").count() == 0:
            log("[SEAAP][ERROR] No estamos en formulario (o_form_view no detectado). No se puede guardar.")
    except Exception:
        pass
    try:
        log(f"[SEAAP][DEBUG] URL actual: {page.url}")
    except Exception:
        pass
    raise RuntimeError("No se encontró botón Guardar.")


def esperar_guardado_ok(page, log, timeout_ms=20000):
    btn = page.locator("button.o_form_button_save:has-text('Guardar'), button.o_form_button_save")
    loading = page.locator(".o_loading, .o_view_loading, .o_spinner, .o_list_view_loading")
    notif = page.locator(".o_notification, .o_toaster, .toast, .o-notification")

    step = 250
    for _ in range(max(1, timeout_ms // step)):
        try:
            if loading.count() > 0:
                page.wait_for_timeout(step)
                continue
        except Exception:
            pass

        try:
            if notif.count() > 0:
                try:
                    txt = " ".join([(t or "").strip() for t in notif.all_inner_texts()])
                except Exception:
                    try:
                        txt = " ".join([(t or "").strip() for t in notif.all_text_contents()])
                    except Exception:
                        txt = ""
                if txt and ("guardad" in txt.lower() or "saved" in txt.lower()):
                    log("[SEAAP] Guardado confirmado por notificación.")
                    return True
        except Exception:
            pass

        try:
            if btn.count() == 0:
                log("[SEAAP] Guardado confirmado (botón Guardar no visible).")
                return True
        except Exception:
            pass

        try:
            b = btn.first
            try:
                if hasattr(b, "is_enabled") and (not b.is_enabled()):
                    log("[SEAAP] Guardado confirmado (botón Guardar deshabilitado).")
                    return True
            except Exception:
                pass
        except Exception:
            pass

        page.wait_for_timeout(step)

    return False


def _is_rural_selected(page):
    try:
        return page.locator(
            "div[name='tipo_centropoblado'] input.o_radio_input[data-value='rural']:checked, "
            "div[name='tipo_centropoblado'] input[type='radio'][data-value='rural']:checked, "
            "input.o_radio_input[data-value='rural']:checked, "
            "input[type='radio'][data-value='rural']:checked"
        ).count() > 0
    except Exception:
        return False


# ============================================================
# ESPERA DEFINITIVA: TABLA REAL (NO LA DE 50 FILAS)
# ============================================================
def wait_for_real_child_table(page, log, timeout_ms=20000):
    rows = page.locator(
        "tbody.ui-sortable tr.o_data_row[data-id], "
        "table.o_list_table tbody tr.o_data_row[data-id], "
        "table.o_list_view tbody tr.o_data_row[data-id], "
        "table.o_list_table tbody tr[data-id], "
        "table.o_list_view tbody tr[data-id]"
    )
    loading = page.locator(".o_loading, .o_view_loading, .o_list_view_loading, .o_spinner")
    no_rows = page.locator(".o_view_nocontent, .o_nocontent_help, .o_list_nocontent, .o_empty")

    log("[SEAAP] Esperando la tabla de resultados…")

    step_ms = 200
    for _ in range(max(1, timeout_ms // step_ms)):
        try:
            if loading.count() > 0:
                page.wait_for_timeout(step_ms)
                continue
        except Exception:
            pass

        try:
            c = rows.count()
        except Exception:
            c = 0

        if c > 0:
            break

        try:
            if no_rows.count() > 0:
                log("[SEAAP] Vista sin resultados detectada.")
                return False
        except Exception:
            pass

        page.wait_for_timeout(step_ms)

    try:
        c0 = rows.count()
    except Exception:
        c0 = 0

    if c0 <= 0:
        log("[ERROR SEAAP] No se detectaron filas en la tabla dentro del tiempo de espera.")
        try:
            log(f"[SEAAP][DEBUG] URL actual: {page.url}")
        except Exception:
            pass
        return False

    last = None
    estable = 0
    for _ in range(max(1, timeout_ms // step_ms)):
        try:
            c = rows.count()
        except Exception:
            c = None
        if c == last and c is not None:
            estable += 1
        else:
            estable = 0
        last = c
        if estable >= 3:
            log(f"[SEAAP] Tabla estable con {c} fila(s).")
            return True
        page.wait_for_timeout(step_ms)

    try:
        c_final = rows.count()
    except Exception:
        c_final = -1
    log(f"[WARN SEAAP] Tabla no estabilizó completamente, continuando (filas={c_final}).")
    return True



# ============================================================
# ESCRIBIR DNI → AUTOCOMPLETE → “Buscar DNI del Niño”
# ============================================================
def buscar_dni_nino(page, dni, log):

    dni = str(dni).strip()
    log(f"[SEAAP] Buscando DNI del Niño: {dni}")

    # esperar input
    input_box = None
    for _ in range(30):
        cand = page.locator("input.o_searchview_input")
        if cand.count():
            input_box = cand.first
            break
        # Fallbacks adicionales
        cand2 = page.locator(".o_searchview input")
        if cand2.count():
            input_box = cand2.first
            break
        page.wait_for_timeout(300)

    if input_box is None:
        # Reintentar re-navegando al listado del padrón
        try:
            log("[SEAAP][WARN] Input de búsqueda no encontrado. Reingresando al Padrón Nominal…")
            page.goto(PADRON_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            cand = page.locator("input.o_searchview_input")
            if cand.count():
                input_box = cand.first
            else:
                cand2 = page.locator(".o_searchview input")
                if cand2.count():
                    input_box = cand2.first
        except Exception:
            pass
        if input_box is None:
            # Último intento: atajo para foco
            try:
                page.keyboard.press("/")
                page.wait_for_timeout(500)
                cand3 = page.locator("input.o_searchview_input")
                if cand3.count():
                    input_box = cand3.first
            except Exception:
                pass
        if input_box is None:
            raise RuntimeError("No se encontró input de búsqueda.")

    # limpiar facets
    clear_search_facets(page, log)

    # limpiar input
    input_box.click()
    input_box.press("Control+A")
    input_box.press("Backspace")
    page.wait_for_timeout(200)

    # teclear
    for ch in dni:
        input_box.press(ch)
        page.wait_for_timeout(100)

    # esperar autocomplete
    ul = page.locator("ul.o_searchview_autocomplete")
    ok = False

    for _ in range(30):
        if ul.count():
            visible = ul.first.evaluate(
                "e => getComputedStyle(e).display !== 'none'"
            )
            if visible:
                ok = True
                break
        page.wait_for_timeout(200)

    if not ok:
        log("[SEAAP] No apareció autocomplete, usando Enter.")
        input_box.press("Enter")
        page.wait_for_timeout(1500)
        return

    # seleccionar opción
    items = page.locator("ul.o_searchview_autocomplete li")
    prefer = (
        items
        .filter(has_text=re.compile(r"(Búsqueda\s+Niñ|Busqueda\s+Niñ|DNI\s+del\s+Niñ)", re.IGNORECASE))
        .filter(has_text=dni)
    )
    if prefer.count() == 0:
        prefer = items.filter(has_text=re.compile(r"(Búsqueda\s+Niñ|Busqueda\s+Niñ|DNI\s+del\s+Niñ)", re.IGNORECASE))

    if prefer.count() > 0:
        prefer.first.click(force=True)
        log("SEAAP: opción de búsqueda de Niño seleccionada.")
    else:
        log("[SEAAP][WARN] No se encontró opción explícita de Niño. Intentando con teclado…")
        selected = False
        for _ in range(6):
            input_box.press("ArrowDown")
            page.wait_for_timeout(120)
            focus = items.filter(has_text=re.compile(r"(Niñ|Nin)", re.IGNORECASE)).first
            if focus.count():
                input_box.press("Enter")
                selected = True
                break
        if not selected:
            log("[SEAAP][WARN] No se pudo seleccionar Niño por teclado. Usando Enter.")
            input_box.press("Enter")
        page.wait_for_timeout(250)

    # esperar tabla real
    wait_for_real_child_table(page, log)



# ============================================================
# SELECCIONAR FILA CON CHECKED (PERIODO ACTUAL)
# ============================================================
def seleccionar_periodo(page, log):
    # SOLO PARA PERIODO ACTUAL
    rows = page.locator("table.o_list_view tbody tr[data-id]")
    for i in range(rows.count()):
        row = rows.nth(i)
        chk = row.locator("td[data-field='periodo_actual'] input[checked]")
        if chk.count():
            if _abrir_formulario_fila(page, row, log, intentos=3):
                return True
            log("[SEAAP][WARN] Fila encontrada (periodo actual) pero no se abrió el formulario.")
            return False
    return False


# ============================================================
# HACER CLICK EN EDITAR
# ============================================================
def presionar_editar(page, log):
    watchdog_recovery(page, log)
    cerrar_todos_los_modales(page, log)

    btn = page.locator("button.o_form_button_edit")

    for _ in range(25):
        watchdog_recovery(page, log)
        cerrar_todos_los_modales(page, log)

        if btn.count():
            try:
                btn.first.click()
                page.wait_for_timeout(800)
                cerrar_todos_los_modales(page, log)
                watchdog_recovery(page, log)

                log("[SEAAP] Botón EDITAR presionado.")
                return True
            except:
                pass

        page.wait_for_timeout(200)

    raise RuntimeError("No se encontró el botón Editar.")

def seleccionar_autocomplete_robusto(page, placeholder, valor, log,
                                     intentos=3, espera_inicial=350):
    """
    Selector definitivo y robusto para Odoo + jQuery UI.
    Incluye manejo especial para:
      - Sector (urbano)
      - Campos rurales (Centro poblado / Sector)
    """

    valor = str(valor).strip()
    log(f"[ROBUST] Seleccionando '{valor}' en '{placeholder}'…")

    is_sector = (placeholder.lower() == "sector")
    is_cp = (placeholder.lower() == "centro poblado")
    is_rural_ctx = _is_rural_selected(page)

    def intento_unico():
        inp = _locator_autocomplete_input(page, placeholder)
        if inp is None:
            log(f"[ROBUST][ERROR] Input '{placeholder}' no encontrado.")
            return False

        # si ya contiene el valor esperado → éxito instantáneo
        try:
            val_actual = inp.input_value().strip()
            if (not is_rural_ctx) and val_actual.lower() == valor.lower():
                log(f"[ROBUST] '{placeholder}' ya tenía '{valor}' ✓")
                return True
        except Exception:
            pass

        # limpiar
        inp.click(force=True)
        inp.press("Control+A")
        inp.press("Backspace")
        page.wait_for_timeout(80)

        # escribir letra por letra
        for ch in valor:
            inp.type(ch, delay=40)

        try:
            inp.press("ArrowDown")
        except Exception:
            pass

        if is_rural_ctx and (is_cp or is_sector):
            page.wait_for_timeout(1000)

        menu = _locator_autocomplete_options(page)
        max_wait_ms = 4500 if (is_rural_ctx and (is_cp or is_sector)) else (2500 if is_cp else 2000)
        for _ in range(max(1, max_wait_ms // 80)):
            if menu.count() > 0:
                break
            page.wait_for_timeout(80)

        # si NO aparece menú:
        if menu.count() == 0:
            # si el campo quedó correcto, aun sin menú → éxito
            final = inp.input_value().strip()
            if final.lower() == valor.lower() and (not (is_rural_ctx and (is_cp or is_sector))):
                log(f"[ROBUST] '{placeholder}' = '{final}' (sin menú) ✓")
                return True

            return False

        # seleccionar opción por texto (exacta case-insensitive)
        if not _click_autocomplete_option(page, valor):
            # fallback: si solo hay 1 opción y coincide, click
            try:
                opciones = [t.strip() for t in menu.all_inner_texts()]
            except Exception:
                try:
                    opciones = [t.strip() for t in menu.all_text_contents()]
                except Exception:
                    opciones = []
            opciones_validas = [t for t in opciones if t and "buscar más" not in t.lower()]
            if len(opciones_validas) == 1 and opciones_validas[0].lower() == valor.lower():
                try:
                    menu.first.click(force=True)
                except Exception:
                    return False
            else:
                return False

        page.wait_for_timeout(250)

        # verificación
        inp2 = _locator_autocomplete_input(page, placeholder)
        if inp2 is None:
            return False
        try:
            inp2.press("Tab")
        except Exception:
            pass
        page.wait_for_timeout(150)
        final = inp2.input_value().strip()
        if final.lower() == valor.lower():
            log(f"[ROBUST] '{placeholder}' = '{final}' ✓")
            return True

        # segundo chequeo en flujos rurales
        if is_cp or (is_sector and is_rural_ctx):
            page.wait_for_timeout(900 if is_rural_ctx else 500)
            inp3 = _locator_autocomplete_input(page, placeholder)
            if inp3 is None:
                return False
            final2 = inp3.input_value().strip()
            if final2.lower() == valor.lower():
                log(f"[ROBUST][RURAL] '{placeholder}' estable en '{final2}' ✓")
                return True

        return False

    # ============================================================
    # BUCLE DE INTENTOS
    # ============================================================
    for i in range(1, intentos + 1):
        log(f"[ROBUST] Intento {i}/{intentos} → {placeholder}='{valor}'")

        # para rural NO matar modales ni overlays bruscamente
        if not is_cp and not is_sector:
            watchdog_recovery(page, log)
            cerrar_todos_los_modales(page, log)

        if intento_unico():
            return True

        # descanso entre intentos
        if is_rural_ctx and (is_cp or is_sector):
            page.wait_for_timeout(900)
        elif is_cp:
            page.wait_for_timeout(400)
        else:
            page.wait_for_timeout(espera_inicial + (i * 150))

    # ============================================================
    # RECUPERACIÓN FINAL
    # ============================================================
    inp_fin = _locator_autocomplete_input(page, placeholder)
    if inp_fin is None:
        log(f"[ROBUST][ERROR] Input '{placeholder}' no encontrado al final.")
        return False
    val_fin = inp_fin.input_value().strip()
    
    # Validación exacta
    if val_fin.lower() == valor.lower():
        log(f"[ROBUST] Valor final OK '{val_fin}' ✓")
        return True
    
    # Validación flexible (si contiene el valor)
    if len(valor) > 3 and valor.lower() in val_fin.lower():
         log(f"[ROBUST] Valor final OK (Flexible) '{val_fin}' contiene '{valor}' ✓")
         return True

    log(f"[ROBUST][ERROR] Falló '{placeholder}'. Valor final: '{val_fin}'")
    return False


def normalizar_periodo_seaap(periodo_raw):
    # Convierte '2025-12-01' → '2025-Dic'
    meses = {
        "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
        "09": "Set", "10": "Oct", "11": "Nov", "12": "Dic"
    }
    try:
        y, m, _ = periodo_raw.split("-")
        return f"{y}-{meses[m]}"
    except:
        return periodo_raw


def seleccionar_fila_periodo_manual(page, periodo_manual, log):
    periodo_seaap = normalizar_periodo_seaap(periodo_manual)
    log(f"[SEAAP] Buscando fila con periodo: {periodo_seaap}")

    rows = page.locator(
        "tbody.ui-sortable tr.o_data_row[data-id], "
        "table.o_list_table tbody tr.o_data_row[data-id], "
        "table.o_list_view tbody tr.o_data_row[data-id], "
        "table.o_list_table tbody tr[data-id], "
        "table.o_list_view tbody tr[data-id]"
    )
    total = rows.count()
    for _ in range(60):
        if total > 0:
            break
        page.wait_for_timeout(250)
        total = rows.count()

    if total == 0:
        log("[SEAAP][ERROR] No hay filas en la tabla.")
        return False

    target_norm = str(periodo_seaap or "").strip().lower()

    for i in range(total):
        row = rows.nth(i)
        cell = row.locator("td[name='periodo_carga'], td[data-field='periodo_carga']")
        if cell.count() == 0:
            try:
                row_text = (row.inner_text() or "").strip()
            except Exception:
                row_text = ""
            if target_norm and (target_norm in row_text.lower()):
                if _abrir_formulario_fila(page, row, log, intentos=3):
                    log("[SEAAP] Fila seleccionada y abierta en formulario (match por texto).")
                    return True
                log("[SEAAP][WARN] Fila encontrada (match por texto) pero no se abrió el formulario.")
                return False
            continue
        try:
            periodo = (cell.first.inner_text() or "").strip()
        except Exception:
            try:
                periodo = (cell.first.text_content() or "").strip()
            except Exception:
                continue

        if periodo.strip().lower() == target_norm:
            if _abrir_formulario_fila(page, row, log, intentos=3):
                log("[SEAAP] Fila seleccionada y abierta en formulario.")
                return True
            log("[SEAAP][WARN] Fila encontrada pero no se abrió el formulario.")
            return False

    log(f"[SEAAP] No se encontró fila con periodo '{periodo_seaap}'.")
    return False


def watchdog_recovery(page, log, timeout_ms=15000):
    """
    Watchdog anticongelamiento.
    Si la página no responde durante timeout_ms, intenta recuperar:
    - Cierra modales
    - Elimina overlays
    - Refresca página
    - Si no revive, fuerza un reload completo
    """
    try:
        # Verificar modales primero
        cerrar_todos_los_modales(page, log)

        # Si hay overlays blockUI → eliminarlos
        block_ui = page.locator("div.blockUI.blockOverlay")
        if block_ui.count() > 0:
            log("[WATCHDOG] blockUI detectado. Eliminando…")
            page.evaluate("""
                var b = document.querySelector('div.blockUI.blockOverlay');
                if (b) b.remove();
            """)
            page.wait_for_timeout(300)
            return True

        # Probar pequeña acción: leer título
        start = page.evaluate("Date.now()")
        _ = page.title()
        end = page.evaluate("Date.now()")

        if (end - start) < 2000:
            return True  # la página responde

        # Si el título demora → página congelada
        log("[WATCHDOG] Página detectada como lenta o congelada. Intentando recuperación rápida…")

        cerrar_todos_los_modales(page, log)
        page.wait_for_timeout(800)

        # Intento 2: refresh suave
        log("[WATCHDOG] Intento 2 → refresh suave.")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        cerrar_todos_los_modales(page, log)

        # Probar nuevamente interacción
        test = page.evaluate("Date.now()")
        if test:
            return True

        # Último recurso: reload total
        log("[WATCHDOG] Intento 3 → Fuerza reload completo del PADRÓN.")
        page.goto(PADRON_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        cerrar_todos_los_modales(page, log)

        return True

    except Exception as e:
        log(f"[WATCHDOG][ERROR] Fallo en watchdog: {e}")
        return False


def limpiar_formulario(page, log):
    """
    Limpia el formulario completo de forma robusta:
    - Clic en RURAL → espera larga a que refresque datos
    - Clic en URBANO → espera larga
    - Reintenta si algún campo quedó con valor
    """

    def esperar_refresco_campo(placeholder, max_wait_ms=4000):
        """
        Espera hasta que el campo quede vacío.
        """
        campo = page.locator(
            f"input.o-autocomplete--input[placeholder='{placeholder}'], "
            f"input.o_form_input.ui-autocomplete-input[placeholder='{placeholder}'], "
            f"input[placeholder='{placeholder}']"
        )

        for _ in range(max_wait_ms // 300):
            if campo.count() == 0:
                return True  # campo no existe, seguimos

            val = campo.input_value().strip()
            if val == "":
                return True  # ya está vacío
            page.wait_for_timeout(300)

        return False

    try:
        log("[SEAAP] Limpieza del formulario iniciada…")

        campos = ["Centro poblado", "Zona", "Manzana", "Sector", "Actor Social"]

        def clear_fast(placeholder):
            inp = _locator_autocomplete_input(page, placeholder)
            if inp is None:
                return True
            try:
                inp.click(force=True)
                inp.press("Control+A")
                inp.press("Backspace")
                page.wait_for_timeout(60)
                try:
                    inp.press("Tab")
                except Exception:
                    pass
                return True
            except Exception:
                return False

        cleared_any = False
        for p in campos:
            try:
                val0 = ""
                inp0 = _locator_autocomplete_input(page, p)
                if inp0 is not None:
                    val0 = (inp0.input_value() or "").strip()
                if val0:
                    if clear_fast(p):
                        cleared_any = True
            except Exception:
                pass

        if cleared_any:
            page.wait_for_timeout(120)

        todo_ok = True
        for placeholder in campos:
            ok = esperar_refresco_campo(placeholder, max_wait_ms=1200)
            if not ok:
                todo_ok = False
                break
        if todo_ok:
            log("[SEAAP] Formulario limpiado exitosamente.")
            return True

        # =======================================================
        # 1. CLIC EN RURAL (si existe)
        # =======================================================
        rural = page.locator(
            "div[name='tipo_centropoblado'] input.o_radio_input[data-value='rural'], "
            "input.o_radio_input[data-value='rural'], "
            "input#radio_field_1_rural"
        )
        if rural.count():
            try:
                rural.first.click(force=True)
                log("[SEAAP] Marcado 'Rural'.")
                page.wait_for_timeout(200)
            except Exception:
                log("[SEAAP][WARN] No se pudo clickear 'Rural'.")
        else:
            log("[SEAAP][WARN] No existe radio 'rural'.")

        cerrar_todos_los_modales(page, log)
        watchdog_recovery(page, log)

        # Esperar refresco profundo
        log("[SEAAP] Esperando refresco tras seleccionar 'Rural'…")
        page.wait_for_timeout(250)

        # =======================================================
        # 2. CLIC EN URBANO (si existe)
        # =======================================================
        urbano = page.locator(
            "div[name='tipo_centropoblado'] input.o_radio_input[data-value='urbano'], "
            "input.o_radio_input[data-value='urbano'], "
            "input#radio_field_1_urbano"
        )
        if urbano.count():
            try:
                urbano.first.click(force=True)
                log("[SEAAP] Marcado 'Urbano'.")
                page.wait_for_timeout(200)
            except Exception:
                log("[SEAAP][WARN] No se pudo clickear 'Urbano'.")
        else:
            log("[SEAAP][WARN] No existe radio 'urbano'.")

        cerrar_todos_los_modales(page, log)
        watchdog_recovery(page, log)

        log("[SEAAP] Esperando refresco tras volver a 'Urbano'…")
        page.wait_for_timeout(250)

        # =======================================================
        # 3. VERIFICAR Y REINTENTAR SI ES NECESARIO
        # =======================================================
        todo_ok = True

        for placeholder in campos:
            ok = esperar_refresco_campo(placeholder)
            if not ok:
                todo_ok = False
                log(f"[SEAAP][WARN] El campo '{placeholder}' NO se vació a tiempo.")

        if not todo_ok:
            log("[SEAAP][INFO] Reintentando limpieza del formulario una vez más…")

            page.wait_for_timeout(400)
            cerrar_todos_los_modales(page, log)
            watchdog_recovery(page, log)

            # SEGUNDO INTENTO
            if urbano.count():
                try:
                    urbano.first.click(force=True)
                    log("[SEAAP] Segundo toque a 'Urbano'.")
                    page.wait_for_timeout(500)
                except Exception:
                    pass

            for placeholder in campos:
                esperar_refresco_campo(placeholder)

        # =======================================================
        # Verificación final de seguridad
        # =======================================================
        for placeholder in campos:
            campo = page.locator(
                f"input.o-autocomplete--input[placeholder='{placeholder}'], "
                f"input.o_form_input.ui-autocomplete-input[placeholder='{placeholder}'], "
                f"input[placeholder='{placeholder}']"
            )
            if campo.count():
                val = campo.input_value().strip()
                if val:
                    log(f"[SEAAP][ERROR] {placeholder} aún tiene valor: '{val}'")
                else:
                    log(f"[SEAAP] {placeholder} → vacío ✓")

        log("[SEAAP] Formulario limpiado exitosamente.")
        return True

    except Exception as e:
        log(f"[SEAAP][ERROR] Fallo al limpiar formulario: {e}")
        return False


# ============================================================
# FLUJO POR CUENTA
# ============================================================
def run_seaap_flow_for_account(
        account, registros, log,
        periodo_manual=None, headless=False,
        lista_ok=None, lista_fail=None,
        etapa=None, db_conn=None,
        progress_callback=None
    ):
    
    user = account["seaap_user"]
    pwd = account["seaap_password"]
    ubigeo = account["name"]

    # Conexión a BD
    conn = account["db_conn"]

    registros_exitosos = []
    registros_fallidos = []
    errores_count = 0

    log(f"[SEAAP] Iniciando flujo por cuenta (ubigeo={ubigeo}) con headless={headless} → usando {'headless' if headless else 'visible'}.")
    p, browser, ctx, page = open_browser(headless, log)

    try:
        login_seaap(page, user, pwd, log)

        for idx, r in enumerate(registros, start=1):
            dni = r["dni"]
            log(f"[{ubigeo}] Registro {idx}/{len(registros)} – DNI {dni}")
            
            if progress_callback:
                progress_callback(idx, len(registros), f"UBIGEO {ubigeo} - DNI {dni}")

            try:
                # =====================================================
                # 1) BUSCAR DNI
                # =====================================================
                buscar_dni_nino(page, dni, log)
                watchdog_recovery(page, log)
                cerrar_todos_los_modales(page, log)

                # =====================================================
                # 2) SELECCIONAR FILA DEL PERIODO
                # =====================================================
                if periodo_manual:
                    if not seleccionar_fila_periodo_manual(page, periodo_manual, log):
                        registros_fallidos.append({"dni": dni, "motivo": "Periodo no encontrado"})

                        # 🔥 VOLVER A PADRÓN
                        page.goto(PADRON_URL, wait_until="domcontentloaded")
                        cerrar_todos_los_modales(page, log)
                        watchdog_recovery(page, log)
                        page.wait_for_timeout(1500)
                        continue

                else:
                    if not seleccionar_periodo(page, log):
                        registros_fallidos.append({"dni": dni, "motivo": "Periodo no encontrado"})

                        # 🔥 VOLVER A PADRÓN
                        page.goto(PADRON_URL, wait_until="domcontentloaded")
                        cerrar_todos_los_modales(page, log)
                        watchdog_recovery(page, log)
                        page.wait_for_timeout(1500)
                        continue

                watchdog_recovery(page, log)
                cerrar_todos_los_modales(page, log)
                page.wait_for_timeout(600)

                registro_ok = False
                motivo_fallo = ""

                for intento_reg in range(1, 3):
                    log(f"[SEAAP] Intento {intento_reg}/2 → completar y guardar registro…")

                    # =====================================================
                    # 3) LIMPIAR FORMULARIO
                    # =====================================================
                    log("[SEAAP] Limpiando formulario antes de continuar…")
                    if not limpiar_formulario(page, log):
                        motivo_fallo = "No se pudo limpiar"
                        continue

                    page.wait_for_timeout(500)

                    # =====================================================
                    # 5) actorsocial = 0
                    # =====================================================
                    if str(r["actorsocial"]) == "0":
                        log("[SEAAP] Caso especial: actorsocial = 0 → Guardando…")

                        presionar_guardar(page, log)
                        cerrar_todos_los_modales(page, log)
                        if not esperar_guardado_ok(page, log, timeout_ms=25000):
                            motivo_fallo = "No se confirmó guardado (actorsocial=0)"
                            continue

                        marcar_registro_consistente(conn, dni, etapa, log)
                        registros_exitosos.append({"dni": dni})
                        registro_ok = True
                        break

                    # =====================================================
                    # 6) CASO NORMAL
                    # =====================================================
                    datos = {
                        "tipo_centro_poblado": r["tipo_centro_poblado"],
                        "centro_poblado": r["centro_poblado"],
                        "zona": r["zona"],
                        "mz": r["mz"],
                        "sector": r["sector"],
                        "nombre": r["nombre"]
                    }

                    if not llenar_formulario_asignacion(page, datos, log):
                        motivo_fallo = "Formulario no llenado"
                        continue

                    # =====================================================
                    # 7) GUARDAR + VERIFICAR
                    # =====================================================
                    presionar_guardar(page, log)
                    cerrar_todos_los_modales(page, log)
                    if not esperar_guardado_ok(page, log, timeout_ms=25000):
                        motivo_fallo = "No se confirmó guardado"
                        continue

                    marcar_registro_consistente(conn, dni, etapa, log)
                    registros_exitosos.append({"dni": dni})
                    registro_ok = True
                    break

                if not registro_ok:
                    registros_fallidos.append({"dni": dni, "motivo": motivo_fallo or "Fallo no especificado"})

                # 🔥 VOLVER A PADRÓN SIEMPRE
                page.goto(PADRON_URL, wait_until="domcontentloaded")
                cerrar_todos_los_modales(page, log)
                watchdog_recovery(page, log)
                page.wait_for_timeout(1500)
                continue

            except Exception as e:
                registros_fallidos.append({"dni": dni, "motivo": str(e)})
                cerrar_todos_los_modales(page, log)
                watchdog_recovery(page, log)
                try:
                    errores_count += 1
                except Exception:
                    pass

                # 🔥 VOLVER A PADRÓN
                page.goto(PADRON_URL, wait_until="domcontentloaded")
                cerrar_todos_los_modales(page, log)
                watchdog_recovery(page, log)
                page.wait_for_timeout(1500)

        # =====================================================
        # REPORTE FINAL
        # =====================================================
        log("===== REPORTE FINAL =====")

        log("\nRegistros exitosos:")
        for r in registros_exitosos:
            log(f"\033[92m✓ {r['dni']}\033[0m")

        log("\nRegistros fallidos:")
        for f in registros_fallidos:
            log(f"\033[91m✗ {f['dni']} - {f['motivo']}\033[0m")

        # Los reintentos se manejan desde la capa superior (AutomationWorker.segunda_corrida_fallidos)
        result = {
            "ok": len(registros_exitosos),
            "fallidos": len(registros_fallidos),
            "errores": errores_count,
            "procesados": len(registros_exitosos) + len(registros_fallidos),
        }

    finally:
        # Intentar Logout limpio antes de cerrar
        try:
            if page and not page.is_closed():
                logout_seaap(page, log)
        except:
            pass

        try:
            ctx.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return result
