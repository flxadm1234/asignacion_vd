import json
import os
import re
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from orchestrator import orchestrate_full_run, _default_log, _read_db_config_from_env
from config import get_current_etapa_date
from db_utils import create_db_connection, ensure_requests_table, insert_automation_request, update_automation_request_status

# Control de ejecución para evitar corridas concurrentes (Playwright + perfil)
_RUNNING = False
_LOCK = threading.Lock()


class OrchestratorHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(200, {"status": "ok"})
        elif path == "/":
            self._send_json(200, {
                "message": "SEAAP Autonomo API",
                "endpoints": {
                    "GET /health": "Estado del servidor",
                    "GET /run": "Dispara ejecución (query: headless=true|false, periodo_bd=YYYY-MM-01, periodo_manual=YYYY-MM-DD)",
                    "POST /run": "Dispara ejecución (JSON body con headless, periodo_bd, periodo_manual)"
                },
                "examples": {
                    "run_get": "/run?headless=true&periodo_bd=2026-02-01",
                    "run_post_body": {"headless": True, "periodo_bd": "2026-02-01", "periodo_manual": ""}
                }
            })
        elif path == "/run":
            qs = parse_qs(parsed.query or "")
            headless = str(qs.get("headless", ["false"])[0]).lower() in ("1", "true", "yes")
            def _clean(v): return str(v or "").strip()
            periodo_bd = _clean(qs.get("periodo_bd", [""])[0])
            periodo_manual = _clean(qs.get("periodo_manual", [""])[0])
            ubigeo = _clean(qs.get("ubigeo", [""])[0])
            if not periodo_bd or not re.match(r"^\d{4}-\d{2}-\d{2}$", periodo_bd):
                periodo_bd = get_current_etapa_date()

            with _LOCK:
                global _RUNNING
                if _RUNNING:
                    _default_log("[API] Solicitud ignorada: ya hay una ejecución en curso.")
                    self._send_json(202, {"status": "already_running", "message": "Solicitud en curso"})
                    return
                _RUNNING = True

            def _runner():
                try:
                    _default_log(f"[API] Orden GET /run recibida. headless={headless} periodo_bd={periodo_bd} periodo_manual={periodo_manual} ubigeo={ubigeo}")
                    db_cfg = _read_db_config_from_env()
                    conn = create_db_connection(db_cfg, _default_log)
                    req_id = None
                    if conn:
                        ensure_requests_table(conn, _default_log)
                        req_id = insert_automation_request(conn, {
                            "origen": "api",
                            "periodo_bd": periodo_bd,
                            "periodo_manual": periodo_manual,
                            "ubigeo": ubigeo or "",
                            "estado": "en_proceso",
                            "estado_desc": "Solicitud aceptada",
                        }, _default_log)
                    orchestrate_full_run(headless=headless, periodo_bd=periodo_bd, periodo_manual=periodo_manual, ubigeo=ubigeo or None, request_id=req_id)
                    _default_log("[API] Pipeline finalizado.")
                except Exception as e:
                    _default_log(f"[API][ERROR] {e}")
                finally:
                    with _LOCK:
                        global _RUNNING
                        _RUNNING = False

            import threading
            threading.Thread(target=_runner, daemon=True).start()
            self._send_json(202, {"status": "accepted", "message": "Ejecución iniciada"})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/run":
            headless = bool(data.get("headless", False))
            def _clean(v): return str(v or "").strip()
            periodo_bd = _clean(data.get("periodo_bd", ""))
            periodo_manual = _clean(data.get("periodo_manual", ""))
            ubigeo = _clean(data.get("ubigeo", ""))
            if not periodo_bd or not re.match(r"^\d{4}-\d{2}-\d{2}$", periodo_bd):
                periodo_bd = get_current_etapa_date()

            with _LOCK:
                global _RUNNING
                if _RUNNING:
                    _default_log("[API] Solicitud ignorada: ya hay una ejecución en curso.")
                    self._send_json(202, {"status": "already_running", "message": "Solicitud en curso"})
                    return
                _RUNNING = True

            def _runner():
                try:
                    _default_log(f"[API] Orden POST /run recibida. headless={headless} periodo_bd={periodo_bd} periodo_manual={periodo_manual} ubigeo={ubigeo}")
                    db_cfg = _read_db_config_from_env()
                    conn = create_db_connection(db_cfg, _default_log)
                    req_id = None
                    if conn:
                        ensure_requests_table(conn, _default_log)
                        req_id = insert_automation_request(conn, {
                            "origen": "api",
                            "periodo_bd": periodo_bd,
                            "periodo_manual": periodo_manual,
                            "ubigeo": ubigeo or "",
                            "estado": "en_proceso",
                            "estado_desc": "Solicitud aceptada",
                        }, _default_log)
                    orchestrate_full_run(headless=headless, periodo_bd=periodo_bd, periodo_manual=periodo_manual, ubigeo=ubigeo or None, request_id=req_id)
                    _default_log("[API] Pipeline finalizado.")
                except Exception as e:
                    _default_log(f"[API][ERROR] {e}")
                finally:
                    with _LOCK:
                        global _RUNNING
                        _RUNNING = False

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            self._send_json(202, {"status": "accepted", "message": "Ejecución iniciada"})
        else:
            self._send_json(404, {"error": "not_found"})


def start_server(host: str | None = None, port: int | None = None):
    host = host or os.getenv("API_HOST", "0.0.0.0")
    port = int(port or int(os.getenv("API_PORT", "8787")))
    server = ThreadingHTTPServer((host, port), OrchestratorHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _default_log(f"[API] Servidor iniciado en http://{host}:{port} (endpoints: GET /health, GET/POST /run)")
    return server

if __name__ == "__main__":
    start_server()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
