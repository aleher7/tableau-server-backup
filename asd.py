"""
Lista TODO el contenido del repositorio, de forma recursiva (todas las
carpetas y subcarpetas), para revisar qué hay antes de ejecutar el backup.
Uso: python ver_repositorio_completo.py
"""

import json
import time
import jwt
import requests

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()
API = "https://api.cantabrialabs.ghe.com"

ahora = int(time.time())
payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
jwt_token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(jwt_token, bytes):
 jwt_token = jwt_token.decode('utf-8')

url_token = f"{API}/app/installations/{config['github_installation_id']}/access_tokens"
headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
token = requests.post(url_token, headers=headers, timeout=15).json()['token']

owner, repo = config['github_owner'], config['github_repo_name']
headers2 = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}

# ?recursive=1 trae TODO el árbol de archivos de una sola vez (todas las subcarpetas)
url_tree = f"{API}/repos/{owner}/{repo}/git/trees/main?recursive=1"
resp = requests.get(url_tree, headers=headers2, timeout=20)

if resp.status_code != 200:
 print(f"ERROR: Código {resp.status_code}: {resp.text[:300]}")
 exit()

items = resp.json()['tree']
print(f"Total de elementos: {len(items)}\n")
for item in sorted(items, key=lambda x: x['path']):
 icono = "" if item['type'] == 'tree' else ""
 tam = f" ({item['size']} bytes)" if item['type'] == 'blob' else ""
 print(f"{icono} {item['path']}{tam}")
