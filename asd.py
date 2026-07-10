#!/usr/bin/env python3
"""
DESCARGA DE WORKBOOKS VIA TABCMD (orquestado por SQL + Python)

Arquitectura:
    Oracle (Admin Insights)
        ↓
    consulta_rutas_oracle_mejorada.sql
        ↓
    Python (este script)
        ├─ Lee SQL
        ├─ Obtiene: LUID, nombre, ruta_proyecto, tipo_item
        ├─ Crea carpetas locales
        └─ Ejecuta: tabcmd get <LUID> -f "ruta/local/archivo.twbx"
        ↓
    C:\Users\...\Tableau Workbooks (estructura replicada)

REQUISITOS
----------
    pip install oracledb gitpython

TABCMD
------
    Debe estar instalado y en PATH o en ruta específica.
    Aceptamos tanto LUID como content_url:
        tabcmd get <luid> -f "ruta/archivo.twbx"
        tabcmd get /workbooks/<content_url>.twbx -f "ruta/archivo.twbx"

CONEXION ORACLE
---------------
    Formato: user/pass@host:port/service_name
    Ejemplo: admin/password@localhost:1521/orcl
"""

import os
import sys
import logging
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

try:
    import oracledb
except ImportError:
    print("ERROR: pip install oracledb")
    sys.exit(1)

# ============================================================
# CONFIGURACION
# ============================================================

CONFIG = {
    # Oracle: conexion y tabla
    "ORACLE_CONN_STR": os.environ.get(
        "TABLEAU_ORACLE_CONN",
        "admin/password@localhost:1521/orcl"
    ),
    "TABLA_ITEMS": "tableau_items",

    # Carpeta local destino
    "CARPETA_DESTINO": r"C:\Users\alejandro.romaguera\Documents\Tableau Workbooks",

    # tabcmd
    "TABCMD_EXE": r"C:\Program Files\Tableau\Tableau Server\packages\bin\tabcmd.exe",
    "TABCMD_SERVER": "https://dub01.online.tableau.com",
    "TABCMD_SITE": "cantabrialabscorporatebi",

    # Autenticacion Tableau
    "TABLEAU_USER": os.environ.get("TABLEAU_USER", "usuario@empresa.com"),
    "TABLEAU_PASS": os.environ.get("TABLEAU_PASS", "password"),
    # O usar PAT (recomendado):
    "TABLEAU_PAT_NAME": os.environ.get("TABLEAU_PAT_NAME", ""),
    "TABLEAU_PAT_SECRET": os.environ.get("TABLEAU_PAT_SECRET", ""),
}

# ============================================================
# LOGGING
# ============================================================

log_file = Path(CONFIG["CARPETA_DESTINO"]).parent / "descarga_tabcmd.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================
# LEER DATOS DE ORACLE
# ============================================================

def obtener_workbooks_desde_sql(conn_str: str, tabla: str) -> list:
    """
    Conecta a Oracle y ejecuta la consulta jerárquica.
    Devuelve lista de workbooks (dicts con: luid, nombre, ruta, tipo).
    """
    try:
        user_pass, host_sid = conn_str.split("@")
        user, password = user_pass.split("/")
        host, port_sid = host_sid.split(":")
        port, sid = port_sid.split("/")
        port = int(port)
    except Exception as e:
        logger.error("Formato conexion invalido (usa user/pass@host:port/sid): %s", e)
        return []

    logger.info("Conectando a Oracle: %s@%s:%d/%s", user, host, port, sid)

    try:
        con = oracledb.connect(
            user=user, password=password,
            dsn=oracledb.make_dsn(host=host, port=port, service_name=sid)
        )
    except Exception as e:
        logger.error("Fallo conexion Oracle: %s", e)
        return []

    # Consulta: jerarquía completa
    sql = f"""
    WITH proyectos AS (
        SELECT item_id, item_name, item_parent_project_id AS parent_id
        FROM {tabla}
        WHERE item_type = 'Project'
    ),
    rutas_jerarquicas AS (
        SELECT
            item_id, item_name, parent_id,
            LTRIM(SYS_CONNECT_BY_PATH(item_name, '/'), '/') AS ruta_completa,
            LEVEL AS profundidad
        FROM proyectos
        START WITH parent_id IS NULL
        CONNECT BY PRIOR item_id = parent_id
    )
    SELECT
        w.item_luid, w.item_name, rj.ruta_completa,
        'WORKBOOK' AS tipo
    FROM {tabla} w
    JOIN rutas_jerarquicas rj ON rj.item_id = w.item_parent_project_id
    WHERE w.item_type = 'Workbook'
    ORDER BY rj.ruta_completa, w.item_name
    """

    cur = con.cursor()
    cur.execute(sql)

    workbooks = []
    for luid, nombre, ruta, tipo in cur.fetchall():
        workbooks.append({
            "luid": luid,
            "nombre": nombre,
            "ruta_proyecto": ruta,
            "ruta_local_destino": f"{ruta}/{nombre}",  # Sera nombre.twbx al guardar
        })

    con.close()
    logger.info("Obtenidos %d workbooks de Oracle", len(workbooks))
    return workbooks


# ============================================================
# GESTIONAR TABCMD
# ============================================================

def login_tabcmd() -> bool:
    """Login en tabcmd. Devuelve True si exitoso."""
    cmd = [CONFIG["TABCMD_EXE"], "login"]
    cmd.extend(["-s", CONFIG["TABCMD_SERVER"]])
    cmd.extend(["-t", CONFIG["TABCMD_SITE"]])

    # Prioridad: PAT > usuario/contraseña
    if CONFIG["TABLEAU_PAT_NAME"] and CONFIG["TABLEAU_PAT_SECRET"]:
        cmd.extend(["--token-name", CONFIG["TABLEAU_PAT_NAME"]])
        cmd.extend(["--token-value", CONFIG["TABLEAU_PAT_SECRET"]])
        logger.info("Login tabcmd con PAT")
    else:
        cmd.extend(["-u", CONFIG["TABLEAU_USER"]])
        cmd.extend(["-p", CONFIG["TABLEAU_PASS"]])
        logger.info("Login tabcmd con usuario/contraseña")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Fallo login tabcmd:\n%s", result.stderr)
        return False

    logger.info("Login OK")
    return True


def logout_tabcmd() -> bool:
    """Logout de tabcmd."""
    cmd = [CONFIG["TABCMD_EXE"], "logout"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("Logout tabcmd: %s", result.stderr)
        return False
    logger.info("Logout OK")
    return True


def descargar_workbook_tabcmd(nombre: str, ruta_local: Path) -> bool:
    """
    Ejecuta: tabcmd get "/workbooks/{nombre}.twbx" -f "ruta_local/nombre.twbx"
    
    NOTA: Usa content_url (nombre del workbook) en lugar de LUID.
    Esto es lo que el usuario probó y funciona.
    
    Devuelve True si exitoso.
    """
    # Asegurar extension .twbx
    if not nombre.lower().endswith(".twbx"):
        nombre_archivo = f"{nombre}.twbx"
    else:
        nombre_archivo = nombre

    ruta_archivo = ruta_local / nombre_archivo
    
    # Formato que el usuario probó y funciona:
    # tabcmd get "/workbooks/{nombre}.twbx" -f "ruta_local/nombre.twbx"
    content_url = f"/workbooks/{nombre_archivo}"

    cmd = [CONFIG["TABCMD_EXE"], "get", content_url, "-f", str(ruta_archivo)]

    logger.info("  Descargando [tabcmd get %s] -> %s", content_url, nombre_archivo)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error("    ERROR: %s", result.stderr.strip() if result.stderr else "Codigo error")
        return False

    logger.info("    OK")
    return True


# ============================================================
# DESCARGA PRINCIPAL
# ============================================================

def descargar_todos():
    """
    Flujo principal:
    1. Lee SQL → lista de workbooks
    2. Login tabcmd
    3. Para cada workbook: crea carpeta + descarga
    4. Logout
    """
    destino_raiz = Path(CONFIG["CARPETA_DESTINO"])
    destino_raiz.mkdir(parents=True, exist_ok=True)

    # Leer SQL
    workbooks = obtener_workbooks_desde_sql(
        CONFIG["ORACLE_CONN_STR"],
        CONFIG["TABLA_ITEMS"]
    )

    if not workbooks:
        logger.error("No se obtuvieron workbooks de SQL")
        return False

    # Verificar tabcmd
    if not Path(CONFIG["TABCMD_EXE"]).exists():
        logger.error("tabcmd no encontrado: %s", CONFIG["TABCMD_EXE"])
        return False

    # Login
    if not login_tabcmd():
        return False

    ok, errores = 0, 0

    try:
        for wb in workbooks:
            nombre = wb["nombre"]
            ruta_proyecto = wb["ruta_proyecto"]

            # Crear carpeta local
            carpeta = destino_raiz / ruta_proyecto.replace("/", "\\")
            carpeta.mkdir(parents=True, exist_ok=True)

            # Descargar con nombre de archivo
            # (tabcmd usa el nombre del workbook como content_url)
            if descargar_workbook_tabcmd(nombre, carpeta):
                ok += 1
            else:
                errores += 1

    finally:
        logout_tabcmd()

    logger.info("=" * 60)
    logger.info("Descarga finalizada: %d OK, %d errores", ok, errores)
    logger.info("Archivos en: %s", destino_raiz)

    return errores == 0


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Descarga workbooks de Tableau via tabcmd (SQL + Python)"
    )
    parser.add_argument(
        "--oracle", default=CONFIG["ORACLE_CONN_STR"],
        help="Conexion Oracle (user/pass@host:port/sid)"
    )
    parser.add_argument(
        "--tabcmd", default=CONFIG["TABCMD_EXE"],
        help="Ruta a tabcmd.exe"
    )
    parser.add_argument(
        "--destino", default=CONFIG["CARPETA_DESTINO"],
        help="Carpeta destino para workbooks"
    )
    args = parser.parse_args()

    # Actualizar config
    CONFIG["ORACLE_CONN_STR"] = args.oracle
    CONFIG["TABCMD_EXE"] = args.tabcmd
    CONFIG["CARPETA_DESTINO"] = args.destino

    logger.info("=" * 60)
    logger.info("DESCARGA VIA TABCMD (SQL + Python)")
    logger.info("Fecha: %s", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    logger.info("=" * 60)

    exito = descargar_todos()
    sys.exit(0 if exito else 1)


if __name__ == "__main__":
    main()
