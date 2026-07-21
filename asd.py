"""
Deshace el commit gigante atascado (2.3 GB de una vez) y lo vuelve a subir
en trozos pequeños, evitando el timeout del servidor.
SOLO hace falta ejecutar esto UNA VEZ para desatascar el estado actual.
Después de esto, el script principal (que ya sube por lotes de 25) no
debería volver a toparse con este problema.

Uso: python desatascar_push_gigante.py
"""

import json
import time
import subprocess
import jwt
import requests

TAMANO_LOTE = 15  # archivos por push -- bajo a propósito, para ir sobre seguro

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


def ejecutar(cmd):
    print(f"$ {' '.join(cmd[:2])} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    return r.returncode


# PASO 1: deshacer el commit gigante, dejando los archivos como "modificados"
# sin comitear (--mixed: mueve HEAD a origin/main, pero deja los archivos
# en el disco tal cual, solo los "desetapa" del commit)
print("=== Deshaciendo el commit gigante (los archivos NO se borran del disco) ===")
ejecutar(['git', 'reset', '--mixed', 'origin/main'])

# PASO 2: listar todos los archivos pendientes de subir
resultado = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
lineas = [l for l in resultado.stdout.splitlines() if l.strip()]
archivos = [l[3:].strip().strip('"') for l in lineas]
print(f"\n=== {len(archivos)} archivos pendientes de subir, en lotes de {TAMANO_LOTE} ===\n")

if not archivos:
    print("No hay nada pendiente. Nada que hacer.")
    exit()

token = obtener_token()
url_push = f"https://x-access-token:{token}@cantabrialabs.ghe.com/{owner}/{repo}.git"

# PASO 3: subir en trozos pequeños
for i in range(0, len(archivos), TAMANO_LOTE):
    lote = archivos[i:i + TAMANO_LOTE]
    numero_lote = i // TAMANO_LOTE + 1
    print(f"\n--- Lote {numero_lote}: {len(lote)} archivos ---")

    ejecutar(['git', 'add'] + lote)
    mensaje = f"Tableau Backup - lote {numero_lote} (desatasco) - {time.strftime('%Y-%m-%d %H:%M:%S')}"
    codigo = ejecutar(['git', 'commit', '-m', mensaje])
    if codigo != 0:
        print("Nada que comitear en este lote, se salta.")
        continue

    codigo = ejecutar(['git', 'push', url_push, 'main'])
    if codigo != 0:
        print(f"❌ Lote {numero_lote} falló. Deteniendo aquí -- vuelve a ejecutar el script para reintentar desde donde quedó.")
        exit(1)
    print(f"✅ Lote {numero_lote} subido correctamente")

print("\n🎉 TODO SUBIDO -- ya puedes ejecutar el script principal con normalidad")
