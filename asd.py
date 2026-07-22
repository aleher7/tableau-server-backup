"""
Deshace el commit gigante atascado y lo vuelve a subir en trozos pequeños.

CORRECCIONES v2:
- --untracked-files=all: evita que git "colapse" carpetas nuevas enteras en
  una sola línea de status -- así el conteo de archivos por lote es real.
- Todo restringido con pathspec "--" al directorio actual: NUNCA toca nada
  fuera de "Tableau Workbooks" (ni config.json, ni scripts, ni el .pem).

Uso: python desatascar_push_gigante.py
"""

import json
import time
import subprocess
import jwt
import requests

TAMANO_LOTE = 15  # archivos individuales reales por push (ahora sí exacto)

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


# PASO 1: deshacer cualquier commit sin subir, dejando los archivos en disco
print("=== Deshaciendo commits locales sin subir (los archivos NO se borran) ===")
ejecutar(['git', 'reset', '--mixed', 'origin/main'])

# PASO 2: listar TODOS los archivos individuales pendientes, SIN colapsar
# carpetas nuevas en una línea, y SIN salir de esta carpeta ("--" + ".")
resultado = subprocess.run(
    ['git', 'status', '--porcelain', '--untracked-files=all', '--', '.'],
    capture_output=True, text=True
)
lineas = [l for l in resultado.stdout.splitlines() if l.strip()]
archivos = [l[3:].strip().strip('"') for l in lineas]

print(f"\n=== {len(archivos)} archivos individuales pendientes, en lotes de {TAMANO_LOTE} ===")
print("(restringido a esta carpeta -- nunca toca config.json, .pem ni scripts)\n")

if not archivos:
    print("No hay nada pendiente. Nada que hacer.")
    exit()

token = obtener_token()
url_push = f"https://x-access-token:{token}@cantabrialabs.ghe.com/{owner}/{repo}.git"

# PASO 3: subir en trozos pequeños, SIEMPRE restringido a "." (esta carpeta)
for i in range(0, len(archivos), TAMANO_LOTE):
    lote = archivos[i:i + TAMANO_LOTE]
    numero_lote = i // TAMANO_LOTE + 1
    print(f"\n--- Lote {numero_lote}: {len(lote)} archivos ---")
    for f in lote:
        print(f"    {f}")

    ejecutar(['git', 'add', '--'] + lote)
    mensaje = f"Tableau Backup - lote {numero_lote} (desatasco) - {time.strftime('%Y-%m-%d %H:%M:%S')}"
    codigo = ejecutar(['git', 'commit', '-m', mensaje])
    if codigo != 0:
        print("Nada que comitear en este lote, se salta.")
        continue

    ejecutar(['git', 'merge', '--abort'])  # por si quedó un conflicto sin resolver de antes
    codigo = ejecutar(['git', 'pull', '--no-edit', '-X', 'ours', url_push, 'main'])
    if codigo != 0:
        print(f"❌ git pull falló en el lote {numero_lote}. Revisar manualmente.")
        exit(1)

    codigo = ejecutar(['git', 'push', url_push, 'main'])
    if codigo != 0:
        print(f"❌ Lote {numero_lote} falló al subir. Vuelve a ejecutar el script para reintentar.")
        exit(1)
    print(f"✅ Lote {numero_lote} subido correctamente")

print("\n🎉 TODO SUBIDO -- ya puedes ejecutar el script principal con normalidad")
