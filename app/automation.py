# automation.py
import json
import threading
import time
from datetime import datetime

from config import log_to_file, get_current_etapa_date
from db_utils import create_db_connection, fetch_padron_for_ubigeo, ensure_requests_table, update_automation_request_status
from browser_seaap import run_seaap_flow_for_account


# ============================
# COLORES PARA REPORTE FINAL
# ============================
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def load_accounts_from_json(path: str, log):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Admite múltiples esquemas:
        # 1) {"accounts": [ {...} ]}
        # 2) [ {...}, {...} ]
        # 3) {"ubigeo1": {...}, "ubigeo2": {...}}
        if isinstance(data, dict) and "accounts" in data:
            raw_items = data.get("accounts", [])
        elif isinstance(data, dict) and "cuentas" in data:
            raw_items = data.get("cuentas", [])
        elif isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = []
            for key, val in data.items():
                if isinstance(val, dict):
                    v = val.copy()
                    v.setdefault("name", key)
                    v.setdefault("ubigeo", key)
                    raw_items.append(v)
        else:
            raw_items = []
        normalized = []
        for a in raw_items:
            if not isinstance(a, dict):
                continue
            ubigeo = str(a.get("ubigeo") or a.get("name") or a.get("codigo") or a.get("id") or "").strip()
            name_raw = str(a.get("name") or "").strip()
            if ubigeo and ubigeo.isdigit() and len(ubigeo) == 6:
                name = ubigeo
            else:
                name = str(name_raw or ubigeo or (a.get("seaap_user") or "")).strip()

            seaap_user = (
                a.get("seaap_user")
                or a.get("usuario")
                or a.get("user")
                or (a.get("seaap") or {}).get("user")
                or (a.get("seaap") or {}).get("usuario")
            )
            seaap_password = (
                a.get("seaap_password")
                or a.get("contraseña")
                or a.get("contrasena")
                or a.get("password")
                or a.get("clave")
                or (a.get("seaap") or {}).get("password")
                or (a.get("seaap") or {}).get("clave")
            )
            whadox_dni = a.get("whadox_dni") or (a.get("whadox") or {}).get("dni") or (a.get("whadox") or {}).get("user")
            whadox_password = a.get("whadox_password") or (a.get("whadox") or {}).get("password") or (a.get("whadox") or {}).get("clave")
            item = {
                "name": name or "",
                "ubigeo": ubigeo or "",
                "seaap_user": str(seaap_user or ""),
                "seaap_password": str(seaap_password or ""),
                "whadox_dni": str(whadox_dni or ""),
                "whadox_password": str(whadox_password or ""),
            }
            # Solo aceptar si al menos tiene credenciales SEAAP o Whadox
            if item["seaap_user"] or item["whadox_dni"]:
                normalized.append(item)
        log(f"[CONFIG] {len(normalized)} cuentas cargadas desde {path}")
        return normalized
    except Exception as e:
        log(f"[CONFIG][ERROR] No se pudo cargar el JSON {path}: {e}")
        return []


def _to_periodo_manual(etapa_ymd: str) -> str:
    try:
        y, m, _ = etapa_ymd.split("-")
        meses = {
            "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
            "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
            "09": "Set", "10": "Oct", "11": "Nov", "12": "Dic",
        }
        return f"{y}-{meses.get(m, m)}"
    except Exception:
        return etapa_ymd


class AutomationWorker(threading.Thread):
    """
    Ejecuta una corrida completa:
    - Conecta a BD
    - Para cada cuenta:
        - Obtiene registros
        - Ejecuta automatización SEAAP
        - Marca en BD los registros correctos
        - Prepara repetición con fallidos
    """

    def __init__(self, db_config: dict, accounts_path: str,
                 periodo_bd: str, periodo_manual: str, log_callback,
                 progress_callback=None, headless=False, target_ubigeo: str | None = None,
                 request_id: int | None = None):
        super().__init__(daemon=True)
        self.db_config = db_config
        self.accounts_path = accounts_path
        self.periodo_bd = (periodo_bd or "").strip() or get_current_etapa_date()
        self.periodo_manual = (periodo_manual.strip() or _to_periodo_manual(self.periodo_bd))
        self.log = log_callback
        self.progress_callback = progress_callback
        self.headless = headless
        self._stop_event = threading.Event()
        self.target_ubigeo = (target_ubigeo.strip() if isinstance(target_ubigeo, str) else None)

        # listas para reporte final
        self.registros_ok = []
        self.registros_fail = []
        self.count_ok = 0
        self.count_fail = 0
        self.count_err = 0
        self.request_id = request_id

    def stop(self):
        self._stop_event.set()

    def marcar_registro_ok(self, conn, dni, etapa):
        """
        UPDATE padronnominal set asignacion='CONSISTENTE' where dni='xxxx' and etapa='yyyy-mm-dd'
        """
        try:
            cursor = conn.cursor()
            query = """
                UPDATE padronnominal
                SET asignacion = 'CONSISTENTE'
                WHERE dni = %s AND etapa = %s
            """
            cursor.execute(query, (dni, etapa))
            conn.commit()
            cursor.close()

            self.log(f"[BD] UPDATE aplicado para DNI {dni} (CONSISTENTE).")

        except Exception as e:
            self.log(f"[BD][ERROR] No se pudo actualizar asignacion para {dni}: {e}")

    def reporte_final(self):
        self.log("\n===== REPORTE FINAL DEL PROCESO =====")

        self.log("\nRegistros completados correctamente:")
        for dni in self.registros_ok:
            self.log(f"{GREEN}✔ {dni}{RESET}")

        self.log("\nRegistros fallidos:")
        for dni in self.registros_fail:
            self.log(f"{RED}✘ {dni}{RESET}")

        total = self.count_ok + self.count_fail
        self.log(f"\nResumen: procesados={total}, ok={self.count_ok}, fallidos={self.count_fail}, errores={self.count_err}")
        self.log("\n=======================================\n")

    def segunda_corrida_fallidos(self, account, conn, etapa):
        """
        Ejecutar nuevamente solo con registros fallidos.
        """
        if not self.registros_fail:
            self.log("[REINTENTO] No hay registros fallidos para reintentar.")
            return

        self.log("[REINTENTO] Ejecutando segunda corrida SOLO con fallidos…")

        # generar lista con formato compatible (diccionarios)
        registros_retry = []

        for dni in self.registros_fail:
            for r in self.registros_cache:
                if r["dni"] == dni:
                    registros_retry.append(r)
                    break

        res = run_seaap_flow_for_account(
            account=account,
            registros=registros_retry,
            log=self.log,
            headless=self.headless,
            periodo_manual=self.periodo_manual,
            lista_ok=self.registros_ok,
            lista_fail=self.registros_fail,
            etapa=self.periodo_bd,
            db_conn=conn,
            progress_callback=self.progress_callback
        )
        try:
            self.count_ok += int(res.get("ok", 0))
            self.count_fail += int(res.get("fallidos", 0))
            self.count_err += int(res.get("errores", 0))
        except Exception:
            pass

        self.log("[REINTENTO] Reintento finalizado.")

    def run(self):
        # Asegurar que no haya loop de asyncio en este hilo para evitar conflictos con Playwright Sync
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # No corremos el loop, solo lo definimos para que esté limpio, 
            # pero Playwright Sync requiere que NO haya un loop corriendo.
            # Si hay uno corriendo, sync_playwright falla.
            # Verificamos si hay uno corriendo:
            try:
                running = asyncio.get_running_loop()
                if running:
                    self.log("[WARN] Se detectó un loop de asyncio activo. Playwright Sync podría fallar.")
            except RuntimeError:
                # No running loop, perfecto.
                pass
        except Exception:
            pass

        self.log("===== INICIANDO PROCESO DE AUTOMATIZACIÓN =====")
        log_to_file("PROCESO DE AUTOMATIZACIÓN INICIADO")

        etapa = self.periodo_bd
        self.log(f"[INFO] Etapa indicada desde GUI: {etapa}")

        accounts = load_accounts_from_json(self.accounts_path, self.log)
        if not accounts:
            self.log("[AUTOMATIZACIÓN] No hay cuentas configuradas. Abortando.")
            return
        if self.target_ubigeo:
            accounts = [a for a in accounts if str(a.get("name")).strip() == str(self.target_ubigeo)]
            self.log(f"[CONFIG] Filtrado por ubigeo={self.target_ubigeo}: {len(accounts)} cuenta(s).")

        conn = create_db_connection(self.db_config, self.log)
        if not conn:
            self.log("[AUTOMATIZACIÓN] Abortando por error de conexión a BD.")
            return

        try:
            # calcular cantidad total a procesar
            total_to_process = 0
            try:
                for acc in accounts:
                    rows_preview = fetch_padron_for_ubigeo(conn, str(acc.get("name")), etapa, self.log)
                    total_to_process += len(rows_preview)
            except Exception:
                pass

            # estado → procesando
            try:
                ensure_requests_table(conn, self.log)
                if self.request_id:
                    if total_to_process > 0:
                        update_automation_request_status(
                            conn,
                            self.request_id,
                            "procesando",
                            f"Procesando {total_to_process} registros",
                            {"total": total_to_process},
                            self.log
                        )
                    else:
                        update_automation_request_status(
                            conn,
                            self.request_id,
                            "finalizado",
                            "Se ha procesado 0 registros",
                            {"total": 0, "ok": 0, "fallidos": 0, "errores": 0},
                            self.log
                        )
                        # No hay nada que procesar; finalizar temprano
                        return
            except Exception:
                pass

            for account in accounts:
                if self._stop_event.is_set():
                    self.log("[AUTOMATIZACIÓN] Detenido por el usuario.")
                    break

                # inyectar conexión de BD en el account
                account["db_conn"] = conn

                ubigeo = account.get("name")
                if not ubigeo:
                    self.log("[WARN] Cuenta sin 'name' (ubigeo). Se omite.")
                    continue

                self.log(f"----- Procesando cuenta SEAAP (ubigeo {ubigeo}) -----")

                # obtener registros
                registros = fetch_padron_for_ubigeo(conn, ubigeo, etapa, self.log)
                self.registros_cache = registros  # importante para reintentos

                if not registros:
                    self.log(f"[INFO] Ubigeo {ubigeo} sin registros para procesar.")
                else:
                    res = run_seaap_flow_for_account(
                        account=account,
                        registros=registros,
                        log=self.log,
                        headless=self.headless,
                        periodo_manual=self.periodo_manual,
                        lista_ok=self.registros_ok,
                        lista_fail=self.registros_fail,
                        etapa=self.periodo_bd,
                        db_conn=conn,
                        progress_callback=self.progress_callback
                    )

                    # Segunda corrida automáticamente
                    self.segunda_corrida_fallidos(account, conn, etapa)
                    try:
                        self.count_ok += int(res.get("ok", 0))
                        self.count_fail += int(res.get("fallidos", 0))
                        self.count_err += int(res.get("errores", 0))
                    except Exception:
                        pass

                self.log(f"----- Cuenta ubigeo {ubigeo} finalizada -----")

        finally:
            # reporte final al terminar todas las cuentas (antes de cerrar conexión para poder actualizar estado)
            self.reporte_final()
            try:
                if self.request_id:
                    total = self.count_ok + self.count_fail
                    # si la conexión se cerró accidentalmente, reabrir para actualizar estado
                    try:
                        if not conn or not conn.is_connected():
                            conn = create_db_connection(self.db_config, self.log)
                    except Exception:
                        conn = create_db_connection(self.db_config, self.log)
                    if conn:
                        update_automation_request_status(
                            conn,
                            self.request_id,
                            "finalizado",
                            f"Se ha procesado {total} registros",
                            {"total": total, "ok": self.count_ok, "fallidos": self.count_fail, "errores": self.count_err},
                            self.log
                        )
            except Exception:
                pass

            try:
                if conn.is_connected():
                    conn.close()
                    self.log("[BD] Conexión cerrada.")
            except Exception:
                pass

            self.log("===== PROCESO DE AUTOMATIZACIÓN FINALIZADO =====")
            log_to_file("PROCESO DE AUTOMATIZACIÓN FINALIZADO")


class SchedulerThread(threading.Thread):
    """
    Hilo para programar ejecuciones por horas específicas del día.
    """

    def __init__(self, times: list[tuple[int, int]], start_automation_callback, log_callback):
        super().__init__(daemon=True)
        self.times = times
        self.start_automation = start_automation_callback
        self.log = log_callback
        self._stop_event = threading.Event()
        self._last_run = {}

    def stop(self):
        self._stop_event.set()

    def run(self):
        times_str = ", ".join(f"{h:02d}:{m:02d}" for h, m in self.times)
        self.log(f"[SCHEDULER] Iniciado. Horas programadas: {times_str}")

        while not self._stop_event.is_set():
            now = datetime.now()

            for (h, m) in self.times:
                if now.hour == h and now.minute == m:
                    if self._last_run.get((h, m)) != now.date():
                        self.log(f"[SCHEDULER] Hora alcanzada ({h:02d}:{m:02d}). Lanzando automatización…")
                        self._last_run[(h, m)] = now.date()
                        self.start_automation()

            time.sleep(10)

        self.log("[SCHEDULER] Detenido.")
