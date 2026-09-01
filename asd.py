"""
Limpieza de archivos "_vX" sueltos fuera de Versiones/ -- esto puede pasar
con restos de antes de que existiera esta carpeta, o de pruebas anteriores.

El script:
  1. Alinea el disco local EXACTAMENTE con GitHub (git reset --hard), para
     partir de la realidad del remoto, no de lo que hubiera quedado local.
  2. Borra los "_vX" sueltos que encuentre fuera de Versiones/.
  3. Comitea y sube esa eliminacion a GitHub de verdad.

Asi, la proxima vez que corra "descargar_workbooks.py" (que tambien hace
"git reset --hard" en su PASO 4), no los va a volver a traer de vuelta,
porque ya no estaran en el historial que trae ese reset.

Uso: python limpiar_versiones_sueltas.py
"""

import re
import json
import time
import base64
import subprocess
import jwt
import requests
from pathlib import Path

PATRON_VERSION = re.compile(r'_v\d+\.(twbx|twb)$', re.IGNORECASE)

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
DOMINIO = "cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']


def obtener_token():
    """
    Consigue un token de instalacion de la GitHub App, valido ~1 hora.

    Args:
        No recibe argumentos (usa 'config' y 'llave', ya cargados
        globalmente al principio del script).

    Returns:
        Texto con el token de instalacion.
    """
    ahora = int(time.time())
    payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
    jwt_token = jwt.encode(payload, llave, algorithm='RS256')
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode('utf-8')
    url_token = f"{API}/app/installations/{config['github_installation_id']}/access_tokens"
    headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    return requests.post(url_token, headers=headers, timeout=15).json()['token']


def redactar(texto, secreto):
    """
    Sustituye una aparicion del secreto por *** en el texto dado.

    Args:
        texto: texto sobre el que buscar el secreto.
        secreto: token o contrasena a ocultar. Puede ser None o vacio.

    Returns:
        El mismo texto, con el secreto sustituido por '***' si aparecia.
    """
    return texto.replace(secreto, "***") if secreto else texto


def ejecutar(cmd, secreto=None):
    """
    Ejecuta un comando y muestra por pantalla el comando y su salida, ya
    censurados.

    Args:
        cmd: lista con el comando y sus argumentos (formato subprocess).
        secreto: texto a censurar en lo que se imprime (ver redactar).
            Por defecto no censura nada.

    Returns:
        Codigo de salida del comando (0 = exito).
    """
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

directorio = Path(config['directorio_descarga'])
carpeta_versiones = (directorio / "Versiones").resolve()

r = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=directorio, capture_output=True, text=True)
raiz_repo = r.stdout.strip()

print("=== 1. Alineando el disco EXACTAMENTE con GitHub (git reset --hard) ===")
import os
os.chdir(raiz_repo)
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--hard', 'FETCH_HEAD'], token)

print("\n=== 2. Buscando '_vX' sueltos fuera de Versiones/ ===")
encontrados = []
for archivo in directorio.rglob('*'):
    if not archivo.is_file():
        continue
    if carpeta_versiones in archivo.resolve().parents:
        continue
    if PATRON_VERSION.search(archivo.name):
        encontrados.append(archivo)

print(f"Encontrados: {len(encontrados)}")
for f in encontrados[:20]:
    print(f"  {f.relative_to(directorio)}")
if len(encontrados) > 20:
    print(f"  ... y {len(encontrados) - 20} mas")

if not encontrados:
    print("\nNada que limpiar. El remoto ya estaba correcto.")
    exit()

print("\n=== 3. Borrando del disco ===")
for f in encontrados:
    f.unlink()
print(f"{len(encontrados)} archivo(s) eliminados del disco.")

print("\n=== 4. Comiteando y subiendo la eliminacion ===")
ejecutar(['git', 'add', '-A', '.'], token)
codigo = ejecutar(['git', 'commit', '-m', f'Retirar {len(encontrados)} version(es) sueltas fuera de Versiones/'], token)
if codigo != 0:
    print("Nada que comitear (raro llegados a este punto).")
else:
    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo == 0:
        ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
        ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
        print("\nLimpieza subida a GitHub. Confirmado en el remoto de verdad.")
    else:
        print("\nFallo el push -- revisa el mensaje de arriba")
