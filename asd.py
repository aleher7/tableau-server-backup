#!/usr/bin/env python3
"""
Sincroniza la carpeta local de workbooks de Tableau con GitHub.

CAMBIOS RESPECTO A LA VERSION ANTERIOR
--------------------------------------
1. Ya NO es un monitor infinito (while True): se ejecuta UNA vez y termina.
   Asi se puede encadenar tras la descarga diaria en el Programador de
   tareas:  descargar_workbooks.py  ->  subir_github.py
2. Detecta archivos NUEVOS y tambien MODIFICADOS (antes solo nuevos,
   porque usaba un set de rutas ya procesadas en memoria).
3. Hace UN solo commit con todos los cambios del dia (mas limpio que un
   commit por archivo) y un unico push.
4. MODO PRUEBA: con --test "ruta/al/archivo.twbx" sube UN solo archivo,
   para validar el flujo antes de subirlo todo (datos confidenciales).

USO
---
    # Prueba con un unico archivo (recomendado la primera vez):
    python subir_github.py --test "Production\\Supply Chain\\Servicio.twbx"

    # Sincronizacion completa (uso diario):
    python subir_github.py
"""

import os
import sys
import shutil
import logging
import argparse
from datetime import datetime
from pathlib import Path

import git

# ============================================================
# CONFIGURACION
# ============================================================

# Carpeta donde descarga los workbooks descargar_workbooks.py
SOURCE_PATH = r"C:\Users\alejandro.romaguera\Documents\Tableau Workbooks"

# Carpeta del repositorio Git (por defecto, la carpeta actual)
REPO_PATH = os.getcwd()

# Subcarpeta del repositorio donde se guardan los workbooks
DEST_SUBFOLDER = "workbooks"

# Extensiones a sincronizar
EXTENSIONES = (".twbx", ".twb")

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# FUNCIONES
# ============================================================

def copiar_al_repo(origen: Path, source_root: Path, repo_root: Path) -> str:
    """
    Copia un archivo al repositorio conservando las subcarpetas y
    devuelve su ruta relativa dentro del repo (con barras de Git).
    """
    rel = origen.relative_to(source_root)
    destino = repo_root / DEST_SUBFOLDER / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origen, destino)
    return str(Path(DEST_SUBFOLDER) / rel).replace("\\", "/")


def archivo_cambiado(origen: Path, source_root: Path, repo_root: Path) -> bool:
    """True si el archivo no existe en el repo o su contenido difiere
    (comparacion rapida por tamano y fecha de modificacion)."""
    destino = repo_root / DEST_SUBFOLDER / origen.relative_to(source_root)
    if not destino.exists():
        return True
    o, d = origen.stat(), destino.stat()
    return o.st_size != d.st_size or int(o.st_mtime) > int(d.st_mtime)


def main():
    parser = argparse.ArgumentParser(
        description="Sincroniza workbooks de Tableau con GitHub")
    parser.add_argument(
        "--test", metavar="ARCHIVO",
        help="Modo prueba: sube UN solo archivo (ruta relativa a la "
             "carpeta de workbooks, p. ej. 'Production\\Supply Chain\\Servicio.twbx')")
    parser.add_argument(
        "--no-push", action="store_true",
        help="Hace el commit pero NO hace push (para revisar antes)")
    args = parser.parse_args()

    source_root = Path(SOURCE_PATH)
    repo_root = Path(REPO_PATH)

    logger.info("=" * 60)
    logger.info("Sincronizacion Tableau -> GitHub  (%s)",
                "MODO PRUEBA: 1 archivo" if args.test else "completa")
    logger.info("Origen:      %s", source_root)
    logger.info("Repositorio: %s", repo_root)
    logger.info("=" * 60)

    # --- Validaciones ---
    try:
        repo = git.Repo(repo_root)
    except Exception as e:
        logger.error("No es un repositorio Git valido: %s", e)
        sys.exit(1)

    if not source_root.exists():
        logger.error("La carpeta de origen NO existe: %s", source_root)
        sys.exit(1)

    # --- Seleccionar archivos a subir ---
    if args.test:
        candidato = source_root / args.test
        if not candidato.exists():
            logger.error("El archivo de prueba no existe: %s", candidato)
            sys.exit(1)
        archivos = [candidato]
    else:
        archivos = [
            p for p in source_root.rglob("*")
            if p.suffix.lower() in EXTENSIONES
            and archivo_cambiado(p, source_root, repo_root)
        ]

    if not archivos:
        logger.info("No hay cambios que subir. Fin.")
        return

    logger.info("Archivos a sincronizar: %d", len(archivos))

    # --- Copiar y anadir a Git ---
    rutas_git = []
    for f in archivos:
        try:
            ruta = copiar_al_repo(f, source_root, repo_root)
            rutas_git.append(ruta)
            logger.info("  + %s", ruta)
        except Exception as e:
            logger.error("Error copiando %s: %s", f, e)

    if not rutas_git:
        logger.error("Ningun archivo se pudo copiar. Fin.")
        sys.exit(1)

    repo.index.add(rutas_git)

    if not repo.index.diff("HEAD") and not repo.untracked_files:
        logger.info("Los archivos son identicos a los del repositorio. "
                    "No se crea commit.")
        return

    # --- Commit ---
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if args.test:
        msg = f"[PRUEBA] {rutas_git[0]} - {timestamp}"
    else:
        msg = f"[SYNC] {len(rutas_git)} workbook(s) actualizados - {timestamp}"
    repo.index.commit(msg)
    logger.info("Commit creado: %s", msg)

    # --- Push ---
    if args.no_push:
        logger.info("--no-push activado: revisa el commit y haz "
                    "'git push' manualmente cuando quieras.")
        return

    try:
        repo.remote("origin").push()
        logger.info("Push a GitHub correcto")
    except Exception as e:
        logger.error("Error en el push: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
