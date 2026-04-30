# db_utils.py
import mysql.connector
from mysql.connector import Error


def create_db_connection(config: dict, log):
    """
    Crea conexión a MySQL.

    config = {
        "host": ...,
        "user": ...,
        "password": ...,
        "database": ...,
        "port": 3306
    }
    """
    try:
        conn = mysql.connector.connect(
            host=config["host"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            port=config["port"],
        )
        if conn.is_connected():
            log("[BD] Conexión exitosa a la base de datos.")
            return conn
        else:
            log("[BD] No se pudo conectar a la base de datos.")
            return None
    except Error as e:
        log(f"[BD][ERROR] {e}")
        return None

def fetch_padron_for_ubigeo(conn, ubigeo: str, etapa_ingresada: str, log):
    """
    Obtiene:
     - actorsocial
     - dni
     - tipo_centro_poblado
     - centro_poblado
     - zona
     - mz
     - sector
     - nombre del actor social

    Con filtrado por etapa ingresada manualmente.
    """

    query = """           
        WITH cte AS (
        SELECT 
            pn.actorsocial,
            pn.dni,
            sa.tipo_centro_poblado,
            sa.centro_poblado,
            sa.zona,
            sa.mz,
            sa.sector,
            CONCAT(p.apellidos,' ',p.nombrecompleto) AS nombre,
            ROW_NUMBER() OVER (
                PARTITION BY pn.dni
                ORDER BY 
                    (pn.actorsocial NOT LIKE '0%') ASC,
                    CAST(pn.actorsocial AS UNSIGNED) ASC
            ) AS rn
        FROM padronnominal AS pn
        LEFT JOIN sectorizacion_actor sa 
            ON sa.dni_actor_social = pn.actorsocial
        LEFT JOIN persona p 
            ON p.dni = pn.actorsocial
        AND p.tipo = 'ACTOR SOCIAL'
        WHERE pn.ubigeo = %s
        AND pn.asignacion = 'PENDIENTE'
        AND pn.etapa = %s
        )
        SELECT 
            actorsocial,
            dni,
            tipo_centro_poblado,
            centro_poblado,
            zona,
            mz,
            sector,
            nombre
        FROM cte
        WHERE rn = 1
        ORDER BY 
            (actorsocial NOT LIKE '0%') ASC,
            CAST(actorsocial AS UNSIGNED) ASC;
        """

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, (ubigeo, etapa_ingresada))
        rows = cursor.fetchall()
        cursor.close()

        log(f"[BD] Ubigeo {ubigeo}: {len(rows)} registros encontrados para etapa {etapa_ingresada}.")

        return rows

    except Error as e:
        log(f"[BD][ERROR] Error consultando ubigeo {ubigeo}: {e}")
        return []

# db_utils.py

def marcar_registro_consistente(conn, dni, etapa, log):
    """
    Marca un registro como CONSISTENTE en la tabla padronnominal
    usando el campo 'etapa' (formato YYYY-MM-DD, como viene del GUI).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE padronnominal
                SET asignacion = 'CONSISTENTE'
                WHERE dni = %s
                  AND etapa = %s
                """,
                (dni, etapa),
            )
        conn.commit()
        log(f"[BD] Marcado CONSISTENTE → DNI {dni} / etapa {etapa}")
    except Exception as e:
        conn.rollback()
        log(f"[BD][ERROR] No se pudo marcar CONSISTENTE para DNI {dni}: {e}")


def ensure_requests_table(conn, log):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_requests (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    origen VARCHAR(50) DEFAULT 'api',
                    periodo_bd VARCHAR(20),
                    periodo_manual VARCHAR(20),
                    ubigeo VARCHAR(20),
                    total INT DEFAULT 0,
                    ok INT DEFAULT 0,
                    fallidos INT DEFAULT 0,
                    errores INT DEFAULT 0,
                    estado VARCHAR(20),
                    estado_desc VARCHAR(255)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
        conn.commit()
        log("[BD] Tabla automation_requests verificada/creada.")
    except Exception as e:
        conn.rollback()
        log(f"[BD][ERROR] No se pudo crear/verificar automation_requests: {e}")


def insert_automation_request(conn, data: dict, log):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO automation_requests (origen, periodo_bd, periodo_manual, ubigeo, total, ok, fallidos, errores, estado, estado_desc)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    data.get("origen", "api"),
                    data.get("periodo_bd", ""),
                    data.get("periodo_manual", ""),
                    data.get("ubigeo", ""),
                    int(data.get("total", 0)),
                    int(data.get("ok", 0)),
                    int(data.get("fallidos", 0)),
                    int(data.get("errores", 0)),
                    data.get("estado", "en_proceso"),
                    data.get("estado_desc", "Solicitud recibida"),
                ),
            )
            req_id = cur.lastrowid
        conn.commit()
        log(f"[BD] Solicitud registrada en automation_requests id={req_id}.")
        return req_id
    except Exception as e:
        conn.rollback()
        log(f"[BD][ERROR] No se pudo insertar solicitud: {e}")
        return None


def update_automation_request_status(conn, request_id: int, estado: str, estado_desc: str, totals: dict | None, log):
    try:
        sets = ["estado=%s", "estado_desc=%s"]
        params = [estado, estado_desc]
        if totals:
            if "total" in totals:
                sets.append("total=%s")
                params.append(int(totals["total"]))
            if "ok" in totals:
                sets.append("ok=%s")
                params.append(int(totals["ok"]))
            if "fallidos" in totals:
                sets.append("fallidos=%s")
                params.append(int(totals["fallidos"]))
            if "errores" in totals:
                sets.append("errores=%s")
                params.append(int(totals["errores"]))
        sql = f"UPDATE automation_requests SET {', '.join(sets)} WHERE id=%s"
        params.append(int(request_id))
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
        conn.commit()
        log(f"[BD] Estado actualizado para solicitud id={request_id} → {estado} / {estado_desc}")
        return True
    except Exception as e:
        conn.rollback()
        log(f"[BD][ERROR] No se pudo actualizar estado de solicitud id={request_id}: {e}")
        return False
