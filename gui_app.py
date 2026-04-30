# gui_app.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import queue
import re
import sys
import os
from datetime import datetime
from pathlib import Path
import json

from config import DEFAULT_ACCOUNTS_JSON, LOG_DIR
from automation import AutomationWorker, SchedulerThread


class SeaapAutomationApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SEAAP-AS - Automatización por Ubigeo")
        self.geometry("1000x750")
        
        # Configurar icono si existe
        icon_path = "app_icon.ico"
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, "app_icon.ico")
        
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        # Estilo moderno
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Colores y configuración de estilo
        self.style.configure("TFrame", background="#f0f0f0")
        self.style.configure("TLabel", background="#f0f0f0", font=("Segoe UI", 10))
        self.style.configure("TButton", font=("Segoe UI", 9, "bold"))
        self.style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#333")
        self.style.configure("TCheckbutton", background="#f0f0f0", font=("Segoe UI", 10))
        self.style.configure("TLabelframe", background="#f0f0f0", font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabelframe.Label", background="#f0f0f0", foreground="#0056b3")
        
        self.configure(bg="#f0f0f0")

        # Cola de logs (thread-safe)
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.errors_count = 0
        self.api_log_pos = 0
        self.api_log_path = os.path.join(str(LOG_DIR), "seaap_scheduler.log")

        # Hilos
        self.automation_thread = None
        self.scheduler_thread = None

        # Variables de configuración
        self.json_path_var = tk.StringVar(value=str(DEFAULT_ACCOUNTS_JSON))
        self.headless_var = tk.BooleanVar(value=False)

        self.db_host_var = tk.StringVar(value="31.220.84.86")
        self.db_user_var = tk.StringVar(value="felix")
        self.db_pass_var = tk.StringVar(value="flxadm1234abc")
        self.db_name_var = tk.StringVar(value="compromiso_uno")
        self.db_port_var = tk.StringVar(value="3306")

        # PERIODOS NUEVOS
        self.periodo_bd_var = tk.StringVar(value="2026-02-01")
        self.periodo_manual_var = tk.StringVar(value="")  # opcional

        # Horas separadas por coma
        self.times_var = tk.StringVar(value="07:30")

        self._build_ui()

        # Bucle de logs y progreso
        self.after(100, self._process_queues)
        self.after(500, self._process_api_activity)

    # ========= UI =========

    def _build_ui(self):
        main_container = ttk.Frame(self, padding="15")
        main_container.pack(fill="both", expand=True)

        # Progreso de Carga
        progress_frame = ttk.LabelFrame(main_container, text=" Progreso de Carga ")
        progress_frame.pack(fill="x", pady=5)

        self.progress_label = ttk.Label(progress_frame, text="Esperando inicio...", font=("Segoe UI", 10))
        self.progress_label.pack(anchor="w", padx=10, pady=(8, 4))

        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate", length=500)
        self.progress_bar.pack(fill="x", padx=10, pady=(0, 10))

        # Registro de Actividad (abajo)
        log_frame = ttk.LabelFrame(main_container, text=" Registro de Actividad ")
        log_frame.pack(fill="both", expand=True, pady=5)

        self.log_text = ScrolledText(log_frame, wrap="word", height=20, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Configurar tags de colores
        self.log_text.tag_config("ERROR", foreground="#ff6b6b")  # Rojo claro
        self.log_text.tag_config("SUCCESS", foreground="#51cf66") # Verde claro
        self.log_text.tag_config("WARN", foreground="#fcc419")    # Amarillo
        self.log_text.tag_config("INFO", foreground="#339af0")    # Azul
        self.log_text.tag_config("DEFAULT", foreground="#d4d4d4")

    # ========= LOGS & PROGRESS =========

    def log(self, message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put((ts, message))

    def update_progress_ui(self, current, total, text):
        self.progress_queue.put((current, total, text))

    def _process_queues(self):
        # 1. Procesar Logs
        try:
            while True:
                ts, msg = self.log_queue.get_nowait()
                
                # Eliminar códigos ANSI (colores de terminal) para que no ensucien el log
                clean_msg = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', msg)
                
                full_msg = f"[{ts}] {clean_msg}\n"
                
                tag = "DEFAULT"
                if "[ERROR]" in clean_msg or "✘" in clean_msg or "fail" in clean_msg.lower():
                    tag = "ERROR"
                elif "✔" in clean_msg or "correctamente" in clean_msg.lower() or "éxito" in clean_msg.lower() or "ok" in clean_msg.lower():
                    tag = "SUCCESS"
                elif "[WARN]" in clean_msg:
                    tag = "WARN"
                elif "[INFO]" in clean_msg or "procesando" in clean_msg.lower():
                    tag = "INFO"

                self.log_text.insert("end", full_msg, tag)
                self.log_text.see("end")
                if tag == "ERROR":
                    self.errors_count += 1
        except queue.Empty:
            pass
            
        # 2. Procesar Progreso
        try:
            while True:
                current, total, text = self.progress_queue.get_nowait()
                self.progress_bar["value"] = current
                self.progress_bar["maximum"] = total
                percent = int((current / total) * 100) if total else 0
                faltan = max(total - current, 0)
                self.progress_label.config(text=f"{text} — {current}/{total} ({percent}%) · faltan {faltan} · errores {self.errors_count}")
        except queue.Empty:
            pass

        self.after(100, self._process_queues)

    def _process_api_activity(self):
        try:
            if os.path.exists(self.api_log_path):
                with open(self.api_log_path, "r", encoding="utf-8") as f:
                    f.seek(self.api_log_pos)
                    new_data = f.read()
                    self.api_log_pos = f.tell()
                if new_data:
                    for line in new_data.splitlines():
                        # Reinyectar al log con clasificación por colores
                        self.log(line)
        except Exception:
            pass
        finally:
            self.after(1000, self._process_api_activity)
    # ========= Helpers =========

    def _browse_json(self):
        path = filedialog.askopenfilename(
            title="Seleccionar archivo de cuentas JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.json_path_var.set(path)

    def _get_db_config(self):
        return {
            "host": "31.220.84.86",
            "user": "felix",
            "password": "flxadm1234abc",
            "database": "compromiso_uno",
            "port": 3306,
        }

    # ========= Automatización =========

    def run_automation_now(self):
        if self.automation_thread and self.automation_thread.is_alive():
            messagebox.showwarning("En ejecución", "La automatización ya está en curso.")
            return

        db_config = self._get_db_config()
        accounts_path = str(Path(__file__).resolve().parent / "accounts.json")
        
        # Reset progress
        self.update_progress_ui(0, 100, "Iniciando...")

        def _to_periodo_manual(etapa_ymd: str) -> str:
            try:
                y, m, _ = etapa_ymd.split("-")
                meses = {"01": "Ene","02": "Feb","03": "Mar","04": "Abr","05": "May","06": "Jun","07": "Jul","08": "Ago","09": "Sep","10": "Oct","11": "Nov","12": "Dic"}
                return f"{y}-{meses.get(m, m)}"
            except Exception:
                return etapa_ymd

        etapa = self.periodo_bd_var.get().strip() or datetime.now().strftime("%Y-%m-01")
        periodo_manual = _to_periodo_manual(etapa)

        self.automation_thread = AutomationWorker(
            db_config=db_config,
            accounts_path=accounts_path,
            periodo_bd=etapa,
            periodo_manual=periodo_manual,
            log_callback=self.log,
            progress_callback=self.update_progress_ui,
            headless=False
        )
        self.automation_thread.start()

    def start_scheduler(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            messagebox.showinfo("Scheduler", "El programador ya está en ejecución.")
            return

        times_text = self.times_var.get().strip()
        if not times_text:
            messagebox.showerror("Horas inválidas", "Ingresa al menos una hora en formato HH:MM.")
            return

        times_list = []
        for part in times_text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                hh, mm = part.split(":")
                hh = int(hh)
                mm = int(mm)
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    raise ValueError
                times_list.append((hh, mm))
            except ValueError:
                messagebox.showerror("Formato inválido", f"Hora inválida: '{part}'. Usa HH:MM.")
                return

        if not times_list:
            messagebox.showerror("Horas inválidas", "No se encontraron horas válidas.")
            return

        def start_automation_callback():
            # El scheduler solo dispara; la lógica de hilos ya está manejada en run_automation_now
            self.run_automation_now()

        self.scheduler_thread = SchedulerThread(
            times=times_list,
            start_automation_callback=start_automation_callback,
            log_callback=self.log
        )
        self.scheduler_thread.start()

    def stop_scheduler(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.stop()
            self.scheduler_thread = None
            messagebox.showinfo("Scheduler", "Programador detenido.")
        else:
            messagebox.showinfo("Scheduler", "No hay programador en ejecución.")
            
            
def main():
    app = SeaapAutomationApp()
    app.mainloop()


if __name__ == "__main__":
    main()
