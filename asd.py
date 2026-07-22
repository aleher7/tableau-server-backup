"""
Drena el backlog de commits sin subir, comiteando y subiendo el estado
ACTUAL de los archivos en un solo commit limpio (con LFS ya activo para
los .twbx, así que los archivos grandes no chocan con el límite de 100 MB).

SEGURIDAD: el token de la GitHub App se pasa por cabecera HTTP
(-c http.extraHeader=...), nunca dentro de la URL -- así ni git ni la
consola llegan a mostrarlo en ningún mensaje. Además, cualquier línea
impresa se redacta por si el token apareciera igualmente.

Uso: python sincronizar_backlog.py
"""

import json
import time
import base64
import subprocess
import jwt
import requests

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']


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
    salida = redactar(r.stdout.strip(), secreto) + "\n" + redactar(r.stderr.strip(), secreto)
    salida = salida.strip()
    if salida:
        print(salida[-1500:])  # últimas líneas, para no saturar
    return r.returncode


token = obtener_token()
url = f"https://cantabrialabs.ghe.com/{owner}/{repo}.git"  # URL SIN credenciales
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.extraHeader=Authorization: Basic {credencial_b64}"

print("=== 1. Descartando commits acumulados sin subir (los archivos NO se borran) ===")
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)

print("\n=== 2. Comiteando el estado actual en UN solo commit ===")
ejecutar(['git', 'add', '-A'], token)
codigo = ejecutar(['git', 'commit', '-m', f'Tableau Backup - sync {time.strftime("%Y-%m-%d %H:%M:%S")}'], token)

if codigo != 0:
    print("Nada nuevo que subir. Repositorio ya al día.")
    exit()

print("\n=== 3. Subiendo (LFS gestiona automáticamente los .twbx grandes) ===")
codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)

if codigo == 0:
    print("\n🎉 SINCRONIZADO CORRECTAMENTE")
else:
    print("\n❌ Falló -- revisa el mensaje de arriba (puede necesitar otro intento si fue un corte puntual de red)")
