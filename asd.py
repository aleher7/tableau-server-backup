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
jwt_token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(jwt_token, bytes):
    jwt_token = jwt_token.decode('utf-8')

headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10"
}
respuesta = requests.get("https://api.cantabrialabs.ghe.com/app/installations", headers=headers, timeout=15)

print(f"Codigo de respuesta: {respuesta.status_code}\n")

if respuesta.status_code == 401:
    print("No se acepto el token. Revisar Client ID / .pem (ver diagnostico_github_app.py)")
    exit()

instalaciones = respuesta.json()

if not instalaciones:
    print("La App es valida pero no esta instalada en ningun sitio todavia.")
else:
    print(f"{len(instalaciones)} instalacion(es) encontradas:\n")
    for inst in instalaciones:
        print(f"  Installation ID : {inst['id']}")
        print(f"  Cuenta          : {inst['account']['login']}")
        print(f"  Permisos        : {inst.get('permissions', {})}")
        print("  " + "-" * 40)
