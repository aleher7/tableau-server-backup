"""
Sube el backlog pendiente a GitHub, sin volver a descargar nada de Tableau.
Separa los archivos GRANDES (van uno por uno) de los pequeños (van en
lotes), para no acumular varios binarios pesados en el mismo push.

Uso: python sincronizar_backlog_por_lotes.py
"""

import json
import time
import base64
import subprocess
import jwt
import requests
import os
from pathlib import Path

TAMANO_LOTE = 10
UMBRAL_GRANDE_MB = 50  # archivos por encima de esto van solos, uno por push

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']

# Trabajar siempre desde la raiz REAL del repositorio (donde vive .git),
# preguntandosela a git en vez de asumirla. Esto evita que "git status"
# y "git add" usen sistemas de referencia de rutas distintos entre si
# (el bug de "Tableau Workbooks/Tableau Workbooks/..." duplicado).
r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                    cwd=config['directorio_descarga'], capture_output=True, text=True)
raiz_repo = r.stdout.strip().replace('/', os.sep)
os.chdir(raiz_repo)
carpeta_relativa = os.path.relpath(config['directorio_descarga'], raiz_repo)
print(f"Raiz del repo: {raiz_repo}")
print(f"Carpeta de trabajo: {carpeta_relativa}")


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
        print(salida[-2000:])
    return r.returncode


def subir(lote, numero, extra_header, url, token):
    print(f"\nLote {numero}: {len(lote)} archivo(s)")
    for f in lote:
        print(f"  {f}")
    # "git rm --cached" saca del indice cualquier version anterior mal
    # subida (por ejemplo como blob normal, sin pasar por LFS); el "git
    # add" siguiente la vuelve a añadir de cero, forzando el filtro LFS.
    ejecutar(['git', 'rm', '-r', '--cached', '--ignore-unmatch', '--'] + lote, token)
    ejecutar(['git', 'add', '--'] + lote, token)
    mensaje = f"Tableau Backup - lote {numero} - {time.strftime('%Y-%m-%d %H:%M:%S')}"
    codigo = ejecutar(['git', 'commit', '-m', mensaje], token)
    if codigo != 0:
        print("Nada que comitear en este lote, se salta.")
        return True
    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo != 0:
        print(f"Lote {numero} fallo al subir. Vuelve a ejecutar el script para reintentar.")
        return False
    print(f"Lote {numero} subido correctamente")
    return True


token = obtener_token()
url = f"https://cantabrialabs.ghe.com/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
# La cabecera se restringe a este dominio (http.<URL>.extraHeader), no al
# generico "http.extraHeader": si no, tambien se envia al almacen de
# objetos de LFS (otro dominio), y choca con su propia autenticacion.
extra_header = f"http.https://cantabrialabs.ghe.com.extraHeader=Authorization: Basic {credencial_b64}"

print("\nPaso 1: sincronizando con el remoto (los archivos locales no se borran)")
ejecutar(['git', 'config', 'http.postBuffer', '2147483648'])
ejecutar(['git', 'config', 'http.lowSpeedLimit', '0'])
ejecutar(['git', 'config', 'http.lowSpeedTime', '999999'])
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)

resultado = subprocess.run(
    # "-z": rutas separadas por byte nulo, sin escapar caracteres
    # especiales (tildes, ñ, etc.). Sin esto, una ruta con acentos llega
    # codificada en octal y "git add" no la reconoce.
    ['git', 'status', '--porcelain', '-z', '--untracked-files=all', '--', carpeta_relativa],
    capture_output=True, text=True, encoding='utf-8'
)
entradas = [e for e in resultado.stdout.split('\0') if e.strip()]
archivos = [e[3:] for e in entradas]

grandes, pequenos = [], []
for f in archivos:
    ruta = Path(f)
    tam_mb = ruta.stat().st_size / (1024 * 1024) if ruta.exists() else 0
    (grandes if tam_mb > UMBRAL_GRANDE_MB else pequenos).append(f)

print(f"\nPaso 2: {len(archivos)} archivos pendientes ({len(grandes)} grandes, {len(pequenos)} pequenos en lotes de {TAMANO_LOTE})")

if not archivos:
    print("No hay nada pendiente. El repositorio ya esta al dia.")
else:
    numero_lote = 1
    for f in grandes:
        if not subir([f], numero_lote, extra_header, url, token):
            exit(1)
        numero_lote += 1
    for i in range(0, len(pequenos), TAMANO_LOTE):
        lote = pequenos[i:i + TAMANO_LOTE]
        if not subir(lote, numero_lote, extra_header, url, token):
            exit(1)
        numero_lote += 1
    print("\nTodo subido correctamente.")

# Refrescar la referencia local de origin/main: un "git push a URL
# directa" sube los datos pero no actualiza esta referencia por si sola
# (eso solo pasa al empujar por el nombre "origin"). Sin este paso,
# "git status" mostraria commits "por delante" aunque ya esten subidos.
print("\nActualizando la referencia local de origin/main")
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
print("Listo. git status ahora reflejara el estado real.")
