#!/usr/bin/env python3
"""
Descarga TODOS los workbooks de Tableau Cloud replicando en local la
estructura de carpetas (proyectos) que existe en el servidor.
 
    Tableau:  Production / Explorers / International / Mi Workbook
    Local:    <CARPETA_DESTINO>/Production/Explorers/International/Mi Workbook.twbx
 
MODOS DE FUNCIONAMIENTO
-----------------------
1) MODO API (recomendado, por defecto): usa la API REST de Tableau mediante
   la libreria 'tableauserverclient'. Descarga por LUID, obtiene la jerarquia
   de proyectos directamente del servidor (siempre actualizada) y permite
   descargar SIN extracto de datos (importante por confidencialidad).
 
2) MODO TABCMD (opcional, MODO = "tabcmd"): genera y ejecuta comandos
   'tabcmd get /workbooks/<content_url>.twbx'. Necesita el content_url de
   cada workbook, que el modo API obtiene automaticamente.
 
REQUISITOS
----------
    pip install tableauserverclient
 
AUTENTICACION
-------------
Se usa un Personal Access Token (PAT). Se crea en Tableau Cloud:
    Mi cuenta > Configuracion > Tokens de acceso personal
Guardar el token en variables de entorno (recomendado) o en CONFIG.
 
PROGRAMACION DIARIA
-------------------
Ver README.md (Programador de tareas de Windows + ejecutar_backup_diario.bat)
"""
 
import os
import re
import sys
import logging
import subprocess
from pathlib import Path
from datetime import datetime
 
# ============================================================
# CONFIGURACION
# ============================================================
 
CONFIG = {
    # --- Servidor Tableau Cloud ---
    "SERVER_URL": "https://dub01.online.tableau.com",
    "SITE_ID": "cantabrialabscorporatebi",          # nombre del site en la URL
 
    # --- Autenticacion con Personal Access Token ---
    # Recomendado: definir variables de entorno TABLEAU_PAT_NAME y TABLEAU_PAT_SECRET
    "PAT_NAME": os.environ.get("TABLEAU_PAT_NAME", "PON_AQUI_EL_NOMBRE_DEL_TOKEN"),
    "PAT_SECRET": os.environ.get("TABLEAU_PAT_SECRET", "PON_AQUI_EL_SECRETO"),
 
    # --- Carpeta local destino (la que luego se sube a GitHub) ---
    "CARPETA_DESTINO": r"C:\Users\alejandro.romaguera\Documents\Tableau Workbooks",
 
    # --- Descargar con o sin datos ---
    # False = descarga el workbook SIN el extracto de datos (recomendado si el
    #         destino final es GitHub y los datos son confidenciales).
    # True  = descarga el .twbx completo con datos.
    "INCLUIR_EXTRACTO": False,
 
    # --- Filtros opcionales ---
    # Lista de proyectos raiz a incluir; vacia = todos.
    # Ejemplo: ["Production", "Control Interno"]
    "PROYECTOS_RAIZ": [],
 
    # Proyectos a excluir por nombre exacto (en cualquier nivel).
    # Util para saltarse "Admin Insights", papeleras, etc.
    "PROYECTOS_EXCLUIDOS": ["Admin Insights"],
 
    # --- Modo: "api" (recomendado) o "tabcmd" ---
    "MODO": "api",
 
    # --- Solo para MODO tabcmd ---
    "TABCMD_EXE": r"C:\Program Files\Tableau\Tableau Server\packages\bin\tabcmd.exe",
}
 
# ============================================================
# LOGGING
# ============================================================
 
log_file = Path(CONFIG["CARPETA_DESTINO"]).parent / "descarga_workbooks.log"
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
# UTILIDADES
# ============================================================
 
def limpiar_nombre(nombre: str) -> str:
    """
    Convierte un nombre de proyecto/workbook en un nombre valido de carpeta
    o archivo en Windows: elimina caracteres prohibidos, espacios y puntos
    finales, y recorta espacios sobrantes.
    """
    nombre = nombre.strip()
    nombre = re.sub(r'[<>:"/\\|?*]', "_", nombre)   # caracteres prohibidos
    nombre = re.sub(r"\s+", " ", nombre)            # espacios multiples
    nombre = nombre.rstrip(". ")                    # Windows no admite punto/espacio final
    return nombre or "_sin_nombre_"
 
 
# ============================================================
# MODO API (tableauserverclient)
# ============================================================
 
def descargar_via_api():
    import tableauserverclient as TSC
 
    auth = TSC.PersonalAccessTokenAuth(
        token_name=CONFIG["PAT_NAME"],
        personal_access_token=CONFIG["PAT_SECRET"],
        site_id=CONFIG["SITE_ID"],
    )
    server = TSC.Server(CONFIG["SERVER_URL"], use_server_version=True)
 
    destino_raiz = Path(CONFIG["CARPETA_DESTINO"])
    destino_raiz.mkdir(parents=True, exist_ok=True)
 
    ok, errores, saltados = 0, 0, 0
 
    with server.auth.sign_in(auth):
        logger.info("Sesion iniciada en %s (site: %s)",
                    CONFIG["SERVER_URL"], CONFIG["SITE_ID"])
 
        # ----------------------------------------------------
        # 1. Descargar la jerarquia completa de proyectos
        # ----------------------------------------------------
        proyectos = list(TSC.Pager(server.projects))
        por_id = {p.id: p for p in proyectos}
        logger.info("Proyectos encontrados: %d", len(proyectos))
 
        def ruta_proyecto(project_id: str) -> list[str]:
            """Devuelve la ruta completa de un proyecto como lista de nombres,
            desde la raiz hasta el propio proyecto."""
            partes = []
            actual = por_id.get(project_id)
            while actual is not None:
                partes.append(limpiar_nombre(actual.name))
                actual = por_id.get(actual.parent_id) if actual.parent_id else None
            return list(reversed(partes))
 
        # ----------------------------------------------------
        # 2. Recorrer todos los workbooks y descargarlos
        # ----------------------------------------------------
        for wb in TSC.Pager(server.workbooks):
            partes_ruta = ruta_proyecto(wb.project_id)
 
            # Filtro de proyectos excluidos
            if any(p in CONFIG["PROYECTOS_EXCLUIDOS"] for p in partes_ruta):
                saltados += 1
                continue
 
            # Filtro de proyectos raiz
            if CONFIG["PROYECTOS_RAIZ"] and (
                not partes_ruta or partes_ruta[0] not in CONFIG["PROYECTOS_RAIZ"]
            ):
                saltados += 1
                continue
 
            carpeta = destino_raiz.joinpath(*partes_ruta)
            carpeta.mkdir(parents=True, exist_ok=True)
 
            nombre_archivo = limpiar_nombre(wb.name)
            ruta_final = carpeta / nombre_archivo  # TSC anade la extension
 
            try:
                fichero = server.workbooks.download(
                    wb.id,
                    filepath=str(ruta_final),
                    include_extract=CONFIG["INCLUIR_EXTRACTO"],
                )
                ok += 1
                logger.info("OK  [%s] -> %s", wb.name,
                            Path(fichero).relative_to(destino_raiz))
            except Exception as e:
                errores += 1
                logger.error("ERROR descargando '%s' (%s): %s", wb.name, wb.id, e)
 
    logger.info("=" * 60)
    logger.info("Descarga finalizada: %d correctos, %d errores, %d saltados",
                ok, errores, saltados)
    return errores == 0
 
 
# ============================================================
# MODO TABCMD (alternativo)
# ============================================================
 
def descargar_via_tabcmd():
    """
    Igual que el modo API pero la descarga fisica la hace tabcmd.
    Se usa la API SOLO para obtener la lista de workbooks, su content_url
    (imprescindible para 'tabcmd get') y la jerarquia de proyectos.
    """
    import tableauserverclient as TSC
 
    tabcmd = CONFIG["TABCMD_EXE"]
    if not Path(tabcmd).exists():
        logger.error("No se encuentra tabcmd en: %s", tabcmd)
        return False
 
    # Login de tabcmd con el mismo PAT
    login = subprocess.run(
        [tabcmd, "login",
         "-s", CONFIG["SERVER_URL"],
         "-t", CONFIG["SITE_ID"],
         "--token-name", CONFIG["PAT_NAME"],
         "--token-value", CONFIG["PAT_SECRET"]],
        capture_output=True, text=True,
    )
    if login.returncode != 0:
        logger.error("Fallo el login de tabcmd:\n%s", login.stderr)
        return False
    logger.info("Login de tabcmd correcto")
 
    auth = TSC.PersonalAccessTokenAuth(
        CONFIG["PAT_NAME"], CONFIG["PAT_SECRET"], site_id=CONFIG["SITE_ID"])
    server = TSC.Server(CONFIG["SERVER_URL"], use_server_version=True)
 
    destino_raiz = Path(CONFIG["CARPETA_DESTINO"])
    destino_raiz.mkdir(parents=True, exist_ok=True)
    ok, errores = 0, 0
 
    with server.auth.sign_in(auth):
        proyectos = list(TSC.Pager(server.projects))
        por_id = {p.id: p for p in proyectos}
 
        def ruta_proyecto(pid):
            partes, actual = [], por_id.get(pid)
            while actual:
                partes.append(limpiar_nombre(actual.name))
                actual = por_id.get(actual.parent_id) if actual.parent_id else None
            return list(reversed(partes))
 
        for wb in TSC.Pager(server.workbooks):
            partes = ruta_proyecto(wb.project_id)
            if any(p in CONFIG["PROYECTOS_EXCLUIDOS"] for p in partes):
                continue
 
            carpeta = destino_raiz.joinpath(*partes)
            carpeta.mkdir(parents=True, exist_ok=True)
            salida = carpeta / f"{limpiar_nombre(wb.name)}.twbx"
 
            cmd = [tabcmd, "get",
                   f"/workbooks/{wb.content_url}.twbx",
                   "-f", str(salida)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                ok += 1
                logger.info("OK  [tabcmd] %s", salida.relative_to(destino_raiz))
            else:
                errores += 1
                logger.error("ERROR [tabcmd] %s:\n%s", wb.name, res.stderr)
 
    subprocess.run([tabcmd, "logout"], capture_output=True)
    logger.info("tabcmd: %d correctos, %d errores", ok, errores)
    return errores == 0
 
 
# ============================================================
# MAIN
# ============================================================
 
def main():
    logger.info("=" * 60)
    logger.info("DESCARGA DE WORKBOOKS DE TABLEAU - %s",
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    logger.info("Modo: %s | Extracto de datos: %s",
                CONFIG["MODO"], CONFIG["INCLUIR_EXTRACTO"])
    logger.info("=" * 60)
 
    if "PON_AQUI" in CONFIG["PAT_NAME"] or "PON_AQUI" in CONFIG["PAT_SECRET"]:
        logger.error("Falta configurar el Personal Access Token "
                     "(variables TABLEAU_PAT_NAME / TABLEAU_PAT_SECRET).")
        sys.exit(1)
 
    exito = descargar_via_api() if CONFIG["MODO"] == "api" else descargar_via_tabcmd()
    sys.exit(0 if exito else 1)
 
 
if __name__ == "__main__":
    main()
 
