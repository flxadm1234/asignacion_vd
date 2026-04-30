# main.py
from gui_app import SeaapAutomationApp
from orchestrator_api import start_server


if __name__ == "__main__":
    try:
        start_server()
    except OSError as e:
        if "Address already in use" in str(e):
            print("[API] Puerto 8787 en uso, iniciando GUI sin API.")
        else:
            print(f"[API] Error al iniciar servidor: {e}")
    app = SeaapAutomationApp()
    app.mainloop()
