"""
Genera un token de la GitHub App y hace push manual del commit pendiente.
Uso: python push_manual.py
"""

import json
import time
import subprocess
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
url_push = f"https://x-access-token:{token}@cantabrialabs.ghe.com/{owner}/{repo}.git"

print("Empujando cambios...")
resultado = subprocess.run(['git', 'push', url_push, 'main'], capture_output=True, text=True)
print(f"Código: {resultado.returncode}")
print(f"stdout: {resultado.stdout}")
print(f"stderr: {resultado.stderr}")
