"""
Quita del repositorio (no del disco) cualquier script/herramienta suelta que
se haya quedado rastreada por git de antes de que el .gitignore se corrigiera.

El .gitignore solo bloquea que se ANADAN archivos nuevos; no retira los que
ya estuvieran dentro. Este script hace ese "retiro" una sola vez.

NO toca: Tableau Workbooks/, las carpetas legado de la raiz (Admin, Ad hoc
Reports, etc.), ni config.json/.pem (esos nunca deberian estar, se
comprueban aparte).

Uso: python limpiar_scripts_del_repo.py
"""

import json
import time
import base64
import subprocess
import jwt
import requests
import os

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
DOMINIO = "cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']

# Extensiones que nunca deberian estar rastreadas en la raiz del repo
EXTENSIONES_A_QUITAR = ('.py', '.bat', '.sql', '.txt', '.log', '.pyc')
# Nombres que, si aparecen, son una alarma de seguridad (no solo limpieza)
SENSIBLES = ('config.json', '.pem', '.key')


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
        print(salida)
    return r.returncode, r.stdout


r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                    cwd=config['directorio_descarga'], capture_output=True, text=True)
raiz_repo = r.stdout.strip().replace('/', os.sep)
os.chdir(raiz_repo)
carpeta_relativa = os.path.relpath(config['directorio_descarga'], raiz_repo)
print(f"Raiz del repo: {raiz_repo}\n")

token = obtener_token()
url = f"https://{DOMINIO}/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.https://{DOMINIO}.extraHeader=Authorization: Basic {credencial_b64}"

print("=== 1. Sincronizando con el remoto ===")
ejecutar(['git', 'merge', '--abort'], token)
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)

print("\n=== 2. Buscando lo que esta rastreado en la raiz ===")
_, salida = ejecutar(['git', 'ls-files'])
todos = [l for l in salida.splitlines() if l.strip()]

# Solo lo que esta DIRECTAMENTE en la raiz (sin '/'), o dentro de una
# carpeta que no sea Tableau Workbooks ni empiece por ella.
en_raiz = [f for f in todos if '/' not in f and '\\' not in f]

a_quitar = [f for f in en_raiz if f.lower().endswith(EXTENSIONES_A_QUITAR)]
alarmas = [f for f in en_raiz if any(s in f for s in SENSIBLES)]

if alarmas:
    print("\n*** ATENCION: archivos sensibles encontrados en el repositorio ***")
    for f in alarmas:
        print(f"    {f}")
    print("Esto requiere revocar la credencial expuesta, no solo borrarla del repo.")
    print("Parar aqui y avisar antes de continuar.\n")
    raise SystemExit(1)

if not a_quitar:
    print("No hay scripts sueltos rastreados en la raiz. Nada que hacer.")
    raise SystemExit(0)

print(f"\nSe van a quitar del repositorio (quedan intactos en el disco):")
for f in a_quitar:
    print(f"    {f}")

print("\n=== 3. Quitandolos del indice (no se borran del disco) ===")
ejecutar(['git', 'rm', '--cached'] + a_quitar, token)

print("\n=== 4. Comiteando ===")
codigo, _ = ejecutar(['git', 'commit', '-m', 'Retirar del repositorio scripts sueltos que no deberian estar'], token)

if codigo != 0:
    print("\nNada que comitear.")
else:
    print("\n=== 5. Subiendo ===")
    codigo, _ = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo == 0:
        ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
        ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
        print("\nRepositorio limpiado y confirmado en GitHub.")
    else:
        print("\nFallo el push -- revisa el mensaje de arriba")
