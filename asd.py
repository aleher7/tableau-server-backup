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
import os
from pathlib import Path

TAMANO_LOTE = 10
UMBRAL_GRANDE_MB = 50  # archivos por encima de esto van SOLOS, uno por push

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"
owner, repo = config['github_owner'], config['github_repo_name']

# Averiguamos la raíz REAL del repositorio (donde vive .git) preguntándole
# a git directamente, en vez de asumirla -- así evitamos el bug de rutas
# duplicadas ("Tableau Workbooks/Tableau Workbooks/..."): git status
# reporta las rutas relativas a la raíz del repo, así que hay que trabajar
# SIEMPRE desde esa raíz para que "git add" interprete esas mismas rutas
# de la misma forma (si no, git las interpreta relativas a la carpeta
# actual, y con un os.chdir a una subcarpeta, la ruta se duplica).
r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                    cwd=config['directorio_descarga'], capture_output=True, text=True)
raiz_repo = r.stdout.strip().replace('/', os.sep)
os.chdir(raiz_repo)

# Ruta de la carpeta de descargas, relativa a la raíz del repo
# (ej: "Tableau Workbooks") -- se usa para restringir el "git status" a
# SOLO esa carpeta, sin ver config.json/.pem/scripts sueltos de al lado.
carpeta_relativa = os.path.relpath(config['directorio_descarga'], raiz_repo)
print(f"Raíz del repo: {raiz_repo}")
print(f"Carpeta de trabajo (relativa): {carpeta_relativa}")


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
    # --renormalize: fuerza a git a re-aplicar el .gitattributes (y por
    # tanto el filtro de LFS) aunque ya exista en el repositorio un objeto
    # con el mismo contenido creado ANTES de activar LFS -- sin esto, git
    # reutiliza silenciosamente ese blob antiguo (sin pasar por LFS) y el
    # archivo se cuela como blob normal, chocando otra vez con el límite
    # de 100 MB de GitHub.
    # NOTA: NO usar --renormalize aquí -- implica -u/--update, que solo
    # afecta a archivos YA rastreados y NUNCA añade archivos nuevos. Como
    # la mayoría de estos .twbx son "nuevos" en cada ejecución, --renormalize
    # los ignoraba en silencio (sin error), y no se comiteaba nada.
    #
    # En su lugar: "git rm --cached" (si el archivo ya estaba rastreado de
    # antes, por ejemplo como blob normal sin pasar por LFS) lo saca del
    # índice, y el "git add" siguiente lo vuelve a añadir de cero, forzando
    # que el filtro de LFS se aplique sí o sí. Si el archivo es nuevo,
    # "--ignore-unmatch" evita que el rm falle por "no encontrado".
    ejecutar(['git', 'rm', '-r', '--cached', '--ignore-unmatch', '--'] + lote, token)
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
# IMPORTANTE: restringido con "http.<URL>.extraHeader" (no el genérico
# "http.extraHeader") -- si no, esta cabecera se envía también al almacén
# de objetos de LFS (otro dominio: objects-origin.cantabrialabs.ghe.com),
# que usa sus propias URLs firmadas, y choca dando "Authentication required".
extra_header = f"http.https://cantabrialabs.ghe.com.extraHeader=Authorization: Basic {credencial_b64}"

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
    # "-z": las rutas se separan por bytes NUL (\0) en vez de saltos de
    # línea, y SIN escapar caracteres especiales (nada de "\303\261" para
    # la ñ, ni comillas envolviendo la ruta). Esto es imprescindible: sin
    # "-z", una ruta con tildes/espacios especiales llega escapada en
    # octal, y "git add" no la reconoce (no encuentra ningún archivo con
    # ese nombre "literal"), así que esos archivos se saltaban en silencio.
    ['git', 'status', '--porcelain', '-z', '--untracked-files=all', '--', carpeta_relativa],
    capture_output=True, text=True, encoding='utf-8'
)
# Con -z, cada entrada es "XY ruta\0" (sin salto de línea); se separan por \0
entradas = [e for e in resultado.stdout.split('\0') if e.strip()]
archivos = [e[3:] for e in entradas]  # quita el prefijo de 2 letras de estado + espacio

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

# Refrescamos la "memoria" local de git sobre dónde está origin/main de
# verdad -- sin esto, un "git push <URL directa> main" (como hacemos aquí)
# SUBE los datos correctamente, pero NO actualiza el puntero local
# "origin/main" (eso solo pasa automático al usar el nombre "origin", no
# una URL directa). Sin este paso, un "git status" posterior mostraría
# "ahead of origin/main by N commits" aunque en realidad YA estén todos
# subidos -- exactamente la confusión de "commits fantasma" de hoy.
print("\n=== Actualizando la referencia local de origin/main ===")
ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
print("Listo -- 'git status' ahora reflejará el estado real.")
