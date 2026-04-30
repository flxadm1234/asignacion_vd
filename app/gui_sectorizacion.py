# gui_sectorizacion.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from sectorizacion_service import (
    get_db_connection,
    create_sectorizacion_table,
    upsert_sectorizacion_from_excel,
    upsert_sectorizacion_manual,
    normalize_dni,   # ← AGRÉGALO
)


class SectorizacionFrame(ttk.LabelFrame):
    """
    Frame de Tkinter para:
    - Subir Excel y cargar/actualizar sectorización en BD
    - Registrar/actualizar un registro manualmente
    """

    def __init__(self, master, db_config: dict, log_callback=print, **kwargs):
        super().__init__(master, text="Sectorización Actor Social", **kwargs)

        self.db_config = db_config
        self.log = log_callback

        # conexión se abre bajo demanda
        self.conn = None

        # ----------------- UI: CARGA DESDE EXCEL -----------------
        frm_excel = ttk.LabelFrame(self, text="Carga desde Excel")
        frm_excel.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        self.var_excel_path = tk.StringVar()

        ttk.Label(frm_excel, text="Archivo Excel:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_excel, textvariable=self.var_excel_path, width=50).grid(
            row=0, column=1, padx=5, pady=5, sticky="ew"
        )

        ttk.Button(
            frm_excel,
            text="Seleccionar…",
            command=self._seleccionar_excel,
        ).grid(row=0, column=2, padx=5, pady=5)

        ttk.Button(
            frm_excel,
            text="Cargar Excel en BD",
            command=self._cargar_excel,
        ).grid(row=1, column=1, columnspan=2, pady=5, sticky="e")

        frm_excel.columnconfigure(1, weight=1)

        # ----------------- UI: REGISTRO MANUAL -----------------
        frm_manual = ttk.LabelFrame(self, text="Registro / Actualización manual")
        frm_manual.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        self.var_dni = tk.StringVar()
        self.var_tipo_cp = tk.StringVar()
        self.var_cp = tk.StringVar()
        self.var_zona = tk.StringVar()
        self.var_mz = tk.StringVar()
        self.var_sector = tk.StringVar()

        fila = 0
        ttk.Label(frm_manual, text="DNI Actor Social *").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_dni, width=20).grid(
            row=fila, column=1, sticky="w", padx=5, pady=2
        )

        fila += 1
        ttk.Label(frm_manual, text="Tipo Centro Poblado").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_tipo_cp, width=30).grid(
            row=fila, column=1, sticky="ew", padx=5, pady=2
        )

        fila += 1
        ttk.Label(frm_manual, text="Centro Poblado").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_cp, width=40).grid(
            row=fila, column=1, sticky="ew", padx=5, pady=2
        )

        fila += 1
        ttk.Label(frm_manual, text="Zona").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_zona, width=15).grid(
            row=fila, column=1, sticky="w", padx=5, pady=2
        )

        fila += 1
        ttk.Label(frm_manual, text="Manzana (Mz)").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_mz, width=15).grid(
            row=fila, column=1, sticky="w", padx=5, pady=2
        )

        fila += 1
        ttk.Label(frm_manual, text="Sector").grid(row=fila, column=0, sticky="w")
        ttk.Entry(frm_manual, textvariable=self.var_sector, width=15).grid(
            row=fila, column=1, sticky="w", padx=5, pady=2
        )

        fila += 1
        ttk.Button(
            frm_manual,
            text="Guardar / Actualizar",
            command=self._guardar_manual,
        ).grid(row=fila, column=1, pady=8, sticky="e")

        frm_manual.columnconfigure(1, weight=1)

    # ------------------- Helpers internos -------------------

    def _ensure_connection(self):
        if self.conn is None or not self.conn.is_connected():
            self.conn = get_db_connection(**self.db_config)
            create_sectorizacion_table(self.conn, log=self.log)

    def _seleccionar_excel(self):
        path = filedialog.askopenfilename(
            title="Seleccionar archivo Excel",
            filetypes=[("Archivos Excel", "*.xlsx *.xls")],
        )
        if path:
            self.var_excel_path.set(path)

    def _cargar_excel(self):
        path = self.var_excel_path.get().strip()
        if not path:
            messagebox.showwarning("Advertencia", "Seleccione un archivo Excel primero.")
            return

        try:
            self._ensure_connection()
            total = upsert_sectorizacion_from_excel(self.conn, path, log=self.log)
            messagebox.showinfo(
                "Carga completada",
                f"Se procesaron {total} registros (insertados/actualizados).",
            )
        except Exception as e:
            self.log(f"[ERROR EXCEL] {e}")
            messagebox.showerror("Error", f"No se pudo cargar el Excel:\n{e}")

    def _guardar_manual(self):
        dni_raw = self.var_dni.get().strip()
        if not dni_raw:
            messagebox.showwarning("Advertencia", "El DNI del Actor Social es obligatorio.")
            return

        # Normalizar aquí también
        dni = normalize_dni(dni_raw)

        try:
            self._ensure_connection()
            upsert_sectorizacion_manual(
                self.conn,
                dni_actor_social=dni,  # ← Enviamos el DNI ya corregido
                tipo_centro_poblado=self.var_tipo_cp.get(),
                centro_poblado=self.var_cp.get(),
                zona=self.var_zona.get(),
                mz=self.var_mz.get(),
                sector=self.var_sector.get(),
                log=self.log,
            )

            # MOSTRAR el DNI REAL que quedó almacenado
            messagebox.showinfo(
                "Guardado",
                f"Registro para DNI {dni} insertado/actualizado correctamente.",
            )

            # OPCIONAL: actualizar el campo del GUI con el DNI corregido
            self.var_dni.set(dni)

        except Exception as e:
            self.log(f"[ERROR MANUAL] {e}")
            messagebox.showerror("Error", f"No se pudo guardar el registro:\n{e}")
