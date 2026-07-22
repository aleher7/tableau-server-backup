"""
Prueba de sanidad: intenta subir SOLO el último commit (uno mínimo, sin
workbooks), para comprobar si el problema es de tamaño o algo más de fondo
con el servidor/conexión en general.
Uso: python prueba_push_minimo.py
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

ahora = int(time.time())
payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
jwt_token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(jwt_token, bytes):
    jwt_token = jwt_token.decode('utf-8')

url_token = f"{API}/app/installations/{config['github_installation_id']}/access_tokens"
headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
token = requests.post(url_token, headers=headers, timeout=15).json()['token']

url = f"https://cantabrialabs.ghe.com/{owner}/{repo}.git"
credencial_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
extra_header = f"http.extraHeader=Authorization: Basic {credencial_b64}"

print("Empujando SOLO el último commit (mínimo, sin workbooks)...")
r = subprocess.run(['git', '-c', extra_header, 'push', url, 'main'], capture_output=True, text=True)
print(f"Código: {r.returncode}")
print(r.stdout.replace(token, "***"))
print(r.stderr.replace(token, "***"))
