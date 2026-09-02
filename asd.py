"""
Vacia TODO el contenido de "Tableau Workbooks/" en GitHub (Versiones/ y
todas las carpetas de proyecto), CONSERVANDO el historial de commits.
NO toca las carpetas legado de la raiz del repositorio (Admin, Clientes,
Commercial Performance, Industrial, MDM, Performance Sell In, Supply Chain).

Por seguridad, funciona en dos pasos:
  1. Sin --aplicar: solo CUENTA y MUESTRA cuantos archivos se borrarian,
     sin tocar nada.
  2. Con --aplicar: borra de verdad, comitea y sube a GitHub.

Uso:
    python vaciar_tableau_workbooks.py              # solo mostrar, no borra
    python vaciar_tableau_workbooks.py --aplicar     # borra de verdad
"""

import sys
import json
import time
import base64
import subprocess
import shutil
import jwt
import requests
from pathlib import Path

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
DOMINIO = "cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']
directorio = Path(config['directorio_descarga'])  # ".../Tableau Workbooks"

aplicar = '--aplicar' in sys.argv


def obtener_token():
    ahora = int(time.time())
    payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
    jwt_token = jwt.encode(payload, llave, algorithm='RS256')
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode('utf-8')
    url_token = f"{API}/app/installations/{config['github_installation_id']}/access_tokens"
    headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    return requests.post(url_token, headers=headers, timeout=15).json()['token']


def redactar(texto, secreto):
    return texto.replace(secreto, "***") if secreto else texto


def ejecutar(cmd, secreto=None):
    cmd_seguro = [redactar(str(c), secreto) for c in cmd]
    print(f"$ {' '.join(cmd_seguro)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    salida = (redactar(r.stdout.strip(), secreto) + "\n" + redactar(r.stderr.strip(), secreto)).strip()
    if salida:
        print(salida[-1500:])
    return r.returncode


token = obtener_token()
url = f"https://{DOMINIO}/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.https://{DOMINIO}.extraHeader=Authorization: Basic {credencial_b64}"

r = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=directorio, capture_output=True, text=True)
raiz_repo = r.stdout.strip()

import os
os.chdir(raiz_repo)

print("=== 1. Alineando el disco EXACTAMENTE con GitHub (git reset --hard) ===")
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--hard', 'FETCH_HEAD'], token)

print("\n=== 2. Contando lo que hay en Tableau Workbooks/ ===")
elementos = list(directorio.iterdir())
archivos = [e for e in directorio.rglob('*') if e.is_file()]
print(f"Elementos de primer nivel: {[e.name for e in elementos]}")
print(f"Total de archivos (incluye Versiones/ y todas las carpetas de proyecto): {len(archivos)}")

if not archivos:
    print("\nYa esta vacio. Nada que hacer.")
    sys.exit(0)

if not aplicar:
    print("\nEsto es solo un RECUENTO -- no se ha borrado nada.")
    print("Para borrar de verdad y subir el cambio a GitHub:")
    print("    python vaciar_tableau_workbooks.py --aplicar")
    sys.exit(0)

print(f"\n=== 3. Borrando los {len(archivos)} archivos de Tableau Workbooks/ ===")
for elemento in elementos:
    if elemento.name in ('.git', '.gitattributes'):
        continue
    if elemento.is_dir():
        shutil.rmtree(elemento)
    else:
        elemento.unlink()
print("Contenido borrado del disco.")

print("\n=== 4. Comiteando y subiendo la eliminacion (el historial se conserva) ===")
ejecutar(['git', 'add', '-A', '--', 'Tableau Workbooks'], token)
codigo = ejecutar(['git', 'commit', '-m', f'Vaciar Tableau Workbooks/ para reiniciar el versionado desde cero ({len(archivos)} archivos retirados)'], token)
if codigo != 0:
    print("Nada que comitear.")
else:
    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo == 0:
        ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
        ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
        print("\nTableau Workbooks/ vaciado y confirmado en GitHub. El historial sigue disponible.")
        print("Las carpetas legado de la raiz NO se han tocado.")
    else:
        print("\nFallo el push -- revisa el mensaje de arriba")
