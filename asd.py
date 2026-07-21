"""
Lista las instalaciones reales de la GitHub App (para saber el Installation ID correcto).
Uso: python listar_installations.py
"""

import json
import time
import jwt
import requests

config = json.load(open('config.json'))
llave = open(config['github_private_key_path'], 'rb').read()

ahora = int(time.time())
payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': config['github_client_id']}
token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(token, bytes):
    token = token.decode('utf-8')

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10"
}
respuesta = requests.get("https://api.cantabrialabs.ghe.com/app/installations", headers=headers, timeout=15)

print(f"Código: {respuesta.status_code}\n")

if respuesta.status_code == 401:
    print("❌ El token no fue aceptado -> revisar Client ID / .pem (ver diagnostico_github_app.py)")
    exit()

instalaciones = respuesta.json()

if not instalaciones:
    print("⚠️  La App es válida pero no está instalada en ningún sitio todavía.")
else:
    print(f"✅ {len(instalaciones)} instalación/es encontradas:\n")
    for inst in instalaciones:
        print(f"   Installation ID : {inst['id']}")
        print(f"   Cuenta          : {inst['account']['login']}")
        print(f"   Permisos        : {inst.get('permissions', {})}")
        print("   " + "-"*40)
