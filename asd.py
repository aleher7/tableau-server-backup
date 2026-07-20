"""
Diagnóstico de autenticación GitHub App.
Comprueba: .pem valido -> reloj -> libreria jwt -> token generado -> respuesta de GitHub
Uso: python diagnostico_github_app.py
"""

import json
import time
from datetime import datetime, timezone
import jwt
import requests

config = json.load(open('config.json'))
app_id = config['github_app_id']
ruta_pem = config['github_private_key_path']

print("1. Archivo .pem")
llave = open(ruta_pem, 'r').read()
if "PRIVATE KEY" in llave:
    print("   OK - formato PEM válido\n")
else:
    print("   ERROR - el archivo no parece un .pem\n")
    exit()

print("2. Reloj del PC vs hora real de GitHub")
hora_local = datetime.now(timezone.utc)
resp = requests.get("https://api.github.com", timeout=10)
hora_github = datetime.strptime(resp.headers['Date'], '%a, %d %b %Y %H:%M:%S %Z').replace(tzinfo=timezone.utc)
diferencia = abs((hora_local - hora_github).total_seconds())
print(f"   PC: {hora_local}  |  GitHub: {hora_github}  |  Diferencia: {diferencia:.0f}s")
print("   OK\n" if diferencia < 60 else "   ERROR - reloj desincronizado (sincronizar Windows)\n")

print("3. Librería jwt instalada")
print(f"   Módulo: {jwt.__file__}")
print(f"   Versión: {getattr(jwt, '__version__', 'SIN VERSION -> puede ser el paquete equivocado')}\n")

print("4. Generando el token (JWT)")
ahora = int(time.time())
payload = {'iat': ahora - 60, 'exp': ahora + 600, 'iss': app_id}
token = jwt.encode(payload, llave, algorithm='RS256')
if isinstance(token, bytes):
    token = token.decode('utf-8')
print(f"   Tipo: {type(token).__name__}  |  Empieza por: {token[:20]}")
print(f"   App ID usado (iss): {app_id}\n")

print("5. Enviando el token a GitHub")
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}
respuesta = requests.get("https://api.github.com/app/installations", headers=headers, timeout=15)
print(f"   Código: {respuesta.status_code}")
print(f"   Respuesta: {json.dumps(respuesta.json(), indent=2, ensure_ascii=False)}")

if respuesta.status_code == 200:
    print("\n✅ TODO CORRECTO")
else:
    print("\n❌ FALLO -> revisar App ID / .pem (probablemente no coinciden entre sí)")
