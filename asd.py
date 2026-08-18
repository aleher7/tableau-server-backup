"""
Elimina archivos con sufijo "_vX" que hayan quedado sueltos FUERA de la
carpeta Versiones/ (restos de antes de que existiera esa carpeta).

Con la estructura actual, ningun archivo de una carpeta de proyecto deberia
llevar "_vX" en el nombre -- solo los que viven dentro de Versiones/.

Uso: python limpiar_versiones_sueltas.py
     python limpiar_versiones_sueltas.py --aplicar   (para borrar de verdad)

Sin --aplicar, solo LISTA lo que encontraria, no borra nada.
"""

import re
import sys
import json
from pathlib import Path

PATRON_VERSION = re.compile(r'_v\d+\.(twbx|twb)$', re.IGNORECASE)

config = json.load(open('config.json'))
directorio = Path(config['directorio_descarga'])
carpeta_versiones = (directorio / "Versiones").resolve()

aplicar = '--aplicar' in sys.argv

encontrados = []
for archivo in directorio.rglob('*'):
    if not archivo.is_file():
        continue
    # Se salta todo lo que SI esta dentro de Versiones/ (ahi es correcto)
    if carpeta_versiones in archivo.resolve().parents:
        continue
    if PATRON_VERSION.search(archivo.name):
        encontrados.append(archivo)

print(f"Encontrados {len(encontrados)} archivo(s) con '_vX' fuera de Versiones/:\n")
for f in encontrados:
    print(f"  {f.relative_to(directorio)}")

if not encontrados:
    print("\nNada que limpiar.")
elif not aplicar:
    print(f"\nEsto es solo una LISTA (no se ha borrado nada).")
    print("Para borrarlos de verdad: python limpiar_versiones_sueltas.py --aplicar")
else:
    print("\nBorrando...")
    for f in encontrados:
        f.unlink()
    print(f"{len(encontrados)} archivo(s) eliminados.")
    print("Recuerda: el proximo 'python descargar_workbooks.py' subira estas")
    print("eliminaciones a GitHub en el siguiente commit (via 'git add -A').")
