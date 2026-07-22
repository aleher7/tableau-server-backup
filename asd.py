"""
Igual que sincronizar_backlog.py, pero sube en trozos pequeños (archivos
individuales reales, sin colapsar carpetas) en vez de un único commit
gigante. Útil si el push de un solo commit sigue dando RPC failed / HTTP 500
incluso con Git LFS ya activo.

SEGURIDAD: token siempre por cabecera HTTP, nunca en la URL ni impreso.

Uso: python sincronizar_backlog_por_lotes.py
"""

import json
import time
import base64
import subprocess
import jwt
import requests

TAMANO_LOTE = 10

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
        print(salida[-1500:])
    return r.returncode


token = obtener_token()
url = f"https://cantabrialabs.ghe.com/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.extraHeader=Authorization: Basic {credencial_b64}"

print("=== 1. Alineando con el remoto (los archivos NO se borran) ===")
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)

# --untracked-files=all: para que cada archivo salga en una línea propia,
# en vez de que git "colapse" carpetas enteras nuevas en una sola línea
# (eso fue lo que causó el commit de 221 archivos hace unos días).
resultado = subprocess.run(
    ['git', 'status', '--porcelain', '--untracked-files=all', '--', '.'],
    capture_output=True, text=True
)
lineas = [l for l in resultado.stdout.splitlines() if l.strip()]
archivos = [l[3:].strip().strip('"') for l in lineas]
print(f"\n=== {len(archivos)} archivos pendientes, en lotes de {TAMANO_LOTE} ===\n")

if not archivos:
    print("No hay nada pendiente.")
    exit()

for i in range(0, len(archivos), TAMANO_LOTE):
    lote = archivos[i:i + TAMANO_LOTE]
    numero_lote = i // TAMANO_LOTE + 1
    print(f"\n--- Lote {numero_lote}: {len(lote)} archivos ---")
    for f in lote:
        print(f"    {f}")

    ejecutar(['git', 'add', '--'] + lote, token)
    mensaje = f"Tableau Backup - lote {numero_lote} - {time.strftime('%Y-%m-%d %H:%M:%S')}"
    codigo = ejecutar(['git', 'commit', '-m', mensaje], token)
    if codigo != 0:
        print("Nada que comitear en este lote, se salta.")
        continue

    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo != 0:
        print(f"❌ Lote {numero_lote} falló. Vuelve a ejecutar el script para reintentar desde aquí.")
        exit(1)
    print(f"✅ Lote {numero_lote} subido correctamente")

print("\n🎉 TODO SUBIDO")
