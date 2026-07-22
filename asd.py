"""
Sube el backlog pendiente a GitHub, separando los archivos GRANDES (que van
uno por uno, para no acumular varios binarios pesados en el mismo push) de
los pequeños (que sí se agrupan en lotes, más eficiente).

SEGURIDAD: token siempre por cabecera HTTP, nunca en la URL ni impreso.

Uso: python sincronizar_backlog_por_lotes.py
"""

import json
import time
import base64
import subprocess
import jwt
import requests
from pathlib import Path

TAMANO_LOTE = 10
UMBRAL_GRANDE_MB = 50  # archivos por encima de esto van SOLOS, uno por push

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
    salida = (redactar(r.stdout.strip(), secreto) + "\n" + redactar(r.stderr.strip(), secreto)).strip()
    if salida:
        print(salida[-2000:])
    return r.returncode


def subir(lote, numero, extra_header, url, token):
    print(f"\n--- Lote {numero}: {len(lote)} archivo(s) ---")
    for f in lote:
        print(f"    {f}")
    ejecutar(['git', 'add', '--'] + lote, token)
    mensaje = f"Tableau Backup - lote {numero} - {time.strftime('%Y-%m-%d %H:%M:%S')}"
    codigo = ejecutar(['git', 'commit', '-m', mensaje], token)
    if codigo != 0:
        print("Nada que comitear en este lote, se salta.")
        return True
    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo != 0:
        print(f"❌ Lote {numero} falló. Vuelve a ejecutar el script para reintentar desde aquí.")
        return False
    print(f"✅ Lote {numero} subido correctamente")
    return True


token = obtener_token()
url = f"https://cantabrialabs.ghe.com/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.extraHeader=Authorization: Basic {credencial_b64}"

print("=== 0. Configurando git para archivos grandes ===")
ejecutar(['git', 'config', 'http.postBuffer', '2147483648'])   # 2 GB
ejecutar(['git', 'config', 'http.maxRequestBuffer', '2147483648'])
ejecutar(['git', 'config', 'http.lowSpeedLimit', '0'])
ejecutar(['git', 'config', 'http.lowSpeedTime', '999999'])

print("\n=== 1. Alineando con el remoto (los archivos NO se borran) ===")
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)

resultado = subprocess.run(
    ['git', 'status', '--porcelain', '--untracked-files=all', '--', '.'],
    capture_output=True, text=True
)
lineas = [l for l in resultado.stdout.splitlines() if l.strip()]
archivos = [l[3:].strip().strip('"') for l in lineas]

# Separar grandes de pequeños según el tamaño REAL en disco ahora mismo
grandes, pequenos = [], []
for f in archivos:
    ruta = Path(f)
    tam_mb = ruta.stat().st_size / (1024 * 1024) if ruta.exists() else 0
    (grandes if tam_mb > UMBRAL_GRANDE_MB else pequenos).append(f)

print(f"\n=== {len(archivos)} archivos pendientes: {len(grandes)} grandes (>{UMBRAL_GRANDE_MB}MB, uno por lote) + {len(pequenos)} pequeños (lotes de {TAMANO_LOTE}) ===")

numero_lote = 1

# Primero los grandes, cada uno en SU PROPIO push
for f in grandes:
    if not subir([f], numero_lote, extra_header, url, token):
        exit(1)
    numero_lote += 1

# Luego los pequeños, en lotes normales
for i in range(0, len(pequenos), TAMANO_LOTE):
    lote = pequenos[i:i + TAMANO_LOTE]
    if not subir(lote, numero_lote, extra_header, url, token):
        exit(1)
    numero_lote += 1

print("\n🎉 TODO SUBIDO")
