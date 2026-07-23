"""
Escribe la version ampliada de .gitignore en la raiz del repo, la comitea
y la sube a GitHub -- para que no se "revierta" cada vez que el script
principal hace su sincronizacion inicial (git reset --hard FETCH_HEAD).

Uso: python actualizar_gitignore.py
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
owner, repo = config['github_owner'], config['github_repo_name']

CONTENIDO_GITIGNORE = """# Credenciales y secretos -- NUNCA deben subirse a GitHub
config.json
*.pem
*.key

# Scripts y herramientas de mantenimiento -- no forman parte del backup.
# Seguro para .twbx/.twb (nunca coinciden con estas extensiones).
*.py
*.bat
*.sql
*.txt

# Logs y archivos temporales generados por el script
*.log
lista_workbooks.csv
__pycache__/
*.pyc
"""


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
    return r.returncode


r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                    cwd=config['directorio_descarga'], capture_output=True, text=True)
raiz_repo = r.stdout.strip().replace('/', os.sep)
os.chdir(raiz_repo)
print(f"Raiz del repo: {raiz_repo}\n")

with open(".gitignore", "w", encoding="utf-8") as f:
    f.write(CONTENIDO_GITIGNORE)
print("gitignore reescrito con la version ampliada\n")

token = obtener_token()
url = "https://cantabrialabs.ghe.com/" + owner + "/" + repo + ".git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.https://cantabrialabs.ghe.com.extraHeader=Authorization: Basic {credencial_b64}"

ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
ejecutar(['git', 'reset', '--mixed', 'FETCH_HEAD'], token)
ejecutar(['git', 'add', '.gitignore'], token)
codigo = ejecutar(['git', 'commit', '-m', 'Ampliar .gitignore (scripts sueltos)'], token)

if codigo != 0:
    print("\nNada que comitear -- el .gitignore de GitHub ya coincide con este.")
else:
    codigo = ejecutar(['git', '-c', extra_header, 'push', url, 'main'], token)
    if codigo == 0:
        ejecutar(['git', '-c', extra_header, 'fetch', url, 'main'], token)
        ejecutar(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], token)
        print("\ngitignore actualizado y subido a GitHub. No deberia revertirse mas.")
    else:
        print("\nFallo el push -- revisa el mensaje de arriba")
