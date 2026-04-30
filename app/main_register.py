# gui_main.py (ejemplo de integración)

import tkinter as tk
from tkinter import ttk
from gui_sectorizacion import SectorizacionFrame

# Supongamos que ya tienes una función log(msg) que escribe en tu textbox de log.
def log(msg: str):
    print(msg)  # aquí puedes redirigir a tu cuadro de texto


def main():
    root = tk.Tk()
    root.title("SEAAP AS – Automatización")

    # Config DB (ajústalo a tu entorno)
    db_config = {
        "host": "31.220.84.86",
        "user": "felix",
        "password": "flxadm1234abc",
        "database": "compromiso_uno",
        "port": 3306,
    }

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    # Aquí podrías tener otras pestañas: Automatización, Config, etc.
    # ...

    # Nueva pestaña para Sectorización
    frm_sector = SectorizacionFrame(notebook, db_config=db_config, log_callback=log)
    notebook.add(frm_sector, text="Sectorización")

    root.mainloop()


if __name__ == "__main__":
    main()
