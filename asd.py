"""
Prueba funcional completa: config -> token -> lectura del repo.
Uso: python test_github_app.py
"""

import json
import time
import jwt
import requests

config = json.load(open('config.json'))
claves = ['github_client_id', 'github_installation_id', 'github_private_key_path', 'github_owner', 'github_repo_name']

print("1. config.json")
faltantes = [c for c in claves if c not in config]
if faltantes:
 print(f" ERROR: Faltan claves: {faltantes}")
 exit()
print(" OK: OK\n")

print("2. Generar token de la App (iss = Client ID)")
llave = open(config['github_private_key_path'], 'rb').read()
ahora = int(time.time())
payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
jwt_token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(jwt_token, bytes):
 jwt_token = jwt_token.decode('utf-8')
print(" OK: OK\n")

print("3. Canjear por token de instalación")
url = f"https://api.cantabrialabs.ghe.com/app/installations/{config['github_installation_id']}/access_tokens"
headers = {
 "Authorization": f"Bearer {jwt_token}",
 "Accept": "application/vnd.github+json",
 "X-GitHub-Api-Version": "2026-03-10"
}
resp = requests.post(url, headers=headers, timeout=15)
if resp.status_code != 201:
 print(f" ERROR: Código {resp.status_code}: {resp.text[:300]}")
 exit()
token = resp.json()['token']
print(" OK: OK (token válido ~1h)\n")

print("4. Leer contenido del repositorio")
owner, repo = config['github_owner'], config['github_repo_name']
url_repo = f"https://api.cantabrialabs.ghe.com/repos/{owner}/{repo}/contents/"
headers2 = {
 "Authorization": f"Bearer {token}",
 "Accept": "application/vnd.github+json",
 "X-GitHub-Api-Version": "2026-03-10"
}
resp2 = requests.get(url_repo, headers=headers2, timeout=15)
if resp2.status_code != 200:
 print(f" ERROR: Código {resp2.status_code}: {resp2.text[:300]}")
 exit()

print(f" OK: OK - contenido de {owner}/{repo}:")
for el in resp2.json():
 icono = "" if el['type'] == 'dir' else ""
 print(f" {icono} {el['path']}")

print("\n TODO FUNCIONA - ya se puede ejecutar el script principal")
