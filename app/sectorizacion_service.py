# sectorizacion_service.py
"""
Servicios para la tabla sectorizacion_actor:
- Crear tabla en MySQL
- Cargar / actualizar registros desde Excel
- Registrar / actualizar un registro manualmente
"""

import pandas as pd
import mysql.connector
from mysql.connector import Error


# ----------------------------------------------------------
# FUNCIÓN DE CONEXIÓN (ajústala a tu realidad)
# ----------------------------------------------------------
def get_db_connection(
    host: str,
    user: str,
    password: str,
    database: str,
    port: int = 3306,
):
    """
    Crea y retorna una conexión a MySQL.
    Maneja errores de forma explícita.
    """
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port,
        )
        if conn.is_connected():
            return conn
    except Error as e:
        raise RuntimeError(f"Error conectando a MySQL: {e}")


# ----------------------------------------------------------
# CREAR TABLA
# ----------------------------------------------------------
def create_sectorizacion_table(conn, log=print):
    """
    Crea la tabla sectorizacion_actor si no existe.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS sectorizacion_actor (
        id_sectorizacion_actor BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        dni_actor_social       VARCHAR(20) NOT NULL,
        tipo_centro_poblado    VARCHAR(100) NULL,
        centro_poblado         VARCHAR(255) NULL,
        zona                   VARCHAR(50) NULL,
        mz                     VARCHAR(50) NULL,
        sector                 VARCHAR(50) NULL,
        PRIMARY KEY (id_sectorizacion_actor),
        UNIQUE KEY uq_dni_actor_social (dni_actor_social)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
    cur = conn.cursor()
    cur.execute(ddl)
    conn.commit()
    cur.close()
    log("[DB] Tabla sectorizacion_actor verificada/creada correctamente.")


# ----------------------------------------------------------
# UPSERT MANUAL
# ----------------------------------------------------------
def normalize_dni(dni: str) -> str:
    """
    Normaliza un DNI para que tenga exactamente 8 dígitos,
    rellenando con ceros a la izquierda cuando sea necesario.
    """
    dni = (dni or "").strip()
    dni = "".join(filter(str.isdigit, dni))  # dejar solo números
    return dni.zfill(8)

# ----------------------------------------------------------
# UPSERT MANUAL
# ----------------------------------------------------------
def upsert_sectorizacion_manual(
    conn,
    dni_actor_social: str,
    tipo_centro_poblado: str,
    centro_poblado: str,
    zona: str,
    mz: str,
    sector: str,
    log=print,
):
    dni_actor_social = normalize_dni(dni_actor_social)

    if not dni_actor_social:
        raise ValueError("El DNI del Actor Social no puede estar vacío.")

    sql = """
    INSERT INTO sectorizacion_actor
        (dni_actor_social, tipo_centro_poblado, centro_poblado, zona, mz, sector)
    VALUES
        (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        tipo_centro_poblado = VALUES(tipo_centro_poblado),
        centro_poblado      = VALUES(centro_poblado),
        zona                = VALUES(zona),
        mz                  = VALUES(mz),
        sector              = VALUES(sector);
    """

    params = (
        dni_actor_social,
        (tipo_centro_poblado or "").strip(),
        (centro_poblado or "").strip(),
        (zona or "").strip(),
        (mz or "").strip(),
        (sector or "").strip(),
    )

    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    cur.close()

    log(f"[DB] UPSERT manual OK para DNI {dni_actor_social}.")


# ----------------------------------------------------------
# CARGAR DESDE EXCEL (EVITANDO DUPLICADOS POR DNI)
# ----------------------------------------------------------

def upsert_sectorizacion_from_excel(conn, excel_path: str, log=print):
    log(f"[EXCEL] Leyendo archivo: {excel_path}")

    try:
        # IMPORTANTE: leer todo como TEXTO
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as e:
        raise RuntimeError(f"No se pudo leer el Excel: {e}")

    expected_cols = {
        "DNI del Actor Social": "dni_actor_social",
        "Tipo Centro Poblado": "tipo_centro_poblado",
        "Centro Poblado": "centro_poblado",
        "Zona": "zona",
        "Manzana": "mz",
        "Sector": "sector",
    }

    missing = [c for c in expected_cols.keys() if c not in df.columns]
    if missing:
        raise ValueError("El Excel no contiene las columnas esperadas. Faltan: "
                         + ", ".join(missing))

    df = df.rename(columns=expected_cols)
    df = df[list(expected_cols.values())]

    # limpieza
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace({"nan": ""})

    sql = """
    INSERT INTO sectorizacion_actor
        (dni_actor_social, tipo_centro_poblado, centro_poblado, zona, mz, sector)
    VALUES
        (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        tipo_centro_poblado = VALUES(tipo_centro_poblado),
        centro_poblado      = VALUES(centro_poblado),
        zona                = VALUES(zona),
        mz                  = VALUES(mz),
        sector              = VALUES(sector);
    """

    cur = conn.cursor()
    total = 0

    for _, row in df.iterrows():
        dni_raw = str(row["dni_actor_social"])
        dni = normalize_dni(dni_raw)

        if not dni:
            continue

        params = (
            dni,
            row["tipo_centro_poblado"],
            row["centro_poblado"],
            row["zona"],
            row["mz"],
            row["sector"],
        )
        cur.execute(sql, params)
        total += 1

    conn.commit()
    cur.close()

    log(f"[DB] Importación/actualización desde Excel finalizada. Registros procesados: {total}")
    return total
