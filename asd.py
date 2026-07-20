"""
Diagnóstico: lista todas las instalaciones REALES de tu GitHub App.

Sirve para averiguar el Installation ID correcto cuando el que se está
usando da error 404. Solo necesita el App ID y el .pem -- NO necesita
el installation_id (es precisamente lo que vamos a descubrir).

USO:
    python listar_installations_github_app.py
"""

import json
import time
import jwt as pyjwt
import requests


def generar_jwt_github_app(app_id, ruta_llave_privada):
    ahora = int(time.time())
    payload = {
        'iat': ahora - 60,
        'exp': ahora + (10 * 60),
        'iss': app_id
    }
    with open(ruta_llave_privada, 'r') as f:
        llave_privada = f.read()
    return pyjwt.encode(payload, llave_privada, algorithm='RS256')


def main():
    config = json.load(open('config.json'))

    jwt_token = generar_jwt_github_app(
        config['github_app_id'],
        config['github_private_key_path']
    )

    # Este endpoint devuelve TODAS las instalaciones de la App identificada
    # por el JWT (es decir, por el App ID + .pem) -- no requiere adivinar
    # ningún installation_id de antemano.
    url = "https://api.github.com/app/installations"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    respuesta = requests.get(url, headers=headers, timeout=15)

    print(f"Código de respuesta: {respuesta.status_code}")
    print()

    if respuesta.status_code == 401:
        print("❌ 401 Unauthorized: el JWT fue RECHAZADO.")
        print("   Esto significa que el problema está en github_app_id o en el .pem,")
        print("   no en el installation_id. Revisa que:")
        print("   - github_app_id sea EXACTAMENTE el App ID (un número), no el Client ID")
        print("   - El .pem sea el correcto para esa App (si tienes varias llaves, prueba otra)")
        return

    if respuesta.status_code != 200:
        print(f"❌ Error inesperado: {respuesta.text[:400]}")
        return

    instalaciones = respuesta.json()

    if not instalaciones:
        print("⚠️  El JWT es válido (App ID y .pem correctos), pero esta App")
        print("   no está instalada en NINGÚN sitio todavía.")
        print("   -> Pide que instalen la App en el repositorio/organización.")
        return

    print(f"✅ Se encontraron {len(instalaciones)} instalación/es de tu App:")
    print()
    for inst in instalaciones:
        print(f"   Installation ID : {inst['id']}")
        print(f"   Instalada en    : {inst['account']['login']}")
        print(f"   Tipo de cuenta  : {inst['account']['type']}")
        print(f"   Permisos        : {inst.get('permissions', {})}")
        print("   " + "-"*50)

    print()
    print("Copia el 'Installation ID' correcto (el que corresponde a tu")
    print("organización/repo) dentro de github_installation_id en config.json")


if __name__ == '__main__':
    main()
