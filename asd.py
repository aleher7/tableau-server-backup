"""
Script de PRUEBA aislado, solo para verificar que la GitHub App funciona
correctamente, SIN tocar Oracle ni Tableau.

Se ejecuta por separado del script principal, y hace 4 comprobaciones
progresivas: si una falla, no tiene sentido seguir a la siguiente.

USO:
    python test_github_app.py
"""

import json
import sys
import time
import jwt as pyjwt
import requests


def cargar_config(config_file="config.json"):
    with open(config_file, 'r') as f:
        return json.load(f)


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
    print("="*70)
    print("PRUEBA 1: Leer config.json")
    print("="*70)
    try:
        config = cargar_config()
        claves_necesarias = [
            'github_app_id', 'github_installation_id',
            'github_private_key_path', 'github_owner', 'github_repo_name'
        ]
        faltantes = [c for c in claves_necesarias if c not in config]
        if faltantes:
            print(f"❌ Faltan claves en config.json: {faltantes}")
            sys.exit(1)
        print("✅ config.json tiene todas las claves necesarias")
    except Exception as e:
        print(f"❌ No se pudo leer config.json: {e}")
        sys.exit(1)

    print()
    print("="*70)
    print("PRUEBA 2: Leer la llave privada (.pem) y generar el JWT")
    print("="*70)
    try:
        jwt_token = generar_jwt_github_app(
            config['github_app_id'],
            config['github_private_key_path']
        )
        print("✅ JWT generado correctamente (primeros 40 caracteres):")
        print(f"   {jwt_token[:40]}...")
    except FileNotFoundError:
        print(f"❌ No se encuentra el archivo .pem en: {config['github_private_key_path']}")
        print("   Revisa la ruta en config.json")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error generando el JWT: {e}")
        print("   Puede que el .pem esté corrupto o mal copiado (revisa saltos de línea)")
        sys.exit(1)

    print()
    print("="*70)
    print("PRUEBA 3: Canjear el JWT por un token de instalación (llamada real a GitHub)")
    print("="*70)
    url = f"https://api.github.com/app/installations/{config['github_installation_id']}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    try:
        respuesta = requests.post(url, headers=headers, timeout=15)
    except Exception as e:
        print(f"❌ No se pudo conectar a GitHub: {e}")
        print("   Revisa tu conexión a internet / proxy de la empresa")
        sys.exit(1)

    if respuesta.status_code != 201:
        print(f"❌ GitHub rechazó la solicitud (código {respuesta.status_code})")
        print(f"   Respuesta: {respuesta.text[:400]}")
        print()
        print("   Causas más probables:")
        print("   - github_app_id o github_installation_id incorrectos")
        print("   - El .pem no corresponde a esta App")
        print("   - La App fue desinstalada del repositorio")
        sys.exit(1)

    token = respuesta.json()['token']
    print("✅ Token de instalación obtenido correctamente (válido ~1 hora)")

    print()
    print("="*70)
    print("PRUEBA 4: Listar el contenido del repositorio (solo LECTURA)")
    print("="*70)
    owner = config['github_owner']
    repo = config['github_repo_name']
    url_contenido = f"https://api.github.com/repos/{owner}/{repo}/contents/"
    headers_contenido = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    respuesta2 = requests.get(url_contenido, headers=headers_contenido, timeout=15)

    if respuesta2.status_code != 200:
        print(f"❌ No se pudo leer el repositorio (código {respuesta2.status_code})")
        print(f"   Respuesta: {respuesta2.text[:400]}")
        print()
        print("   Causas más probables:")
        print("   - github_owner o github_repo_name incorrectos (revisa mayúsculas/guiones)")
        print("   - La App no tiene permiso de lectura sobre ESTE repo concreto")
        sys.exit(1)

    elementos = respuesta2.json()
    print(f"✅ Conexión y permisos OK. Contenido de la raíz del repo ({owner}/{repo}):")
    if not elementos:
        print("   (el repositorio está vacío)")
    for el in elementos:
        tipo = "📁" if el['type'] == 'dir' else "📄"
        print(f"   {tipo} {el['path']}")

    print()
    print("="*70)
    print("🎉 TODO CORRECTO — la GitHub App está bien configurada")
    print("="*70)
    print()
    print("Siguiente paso: probar la escritura (git push) con un commit de prueba.")
    print("Ver instrucciones en el chat para hacerlo de forma segura.")


if __name__ == '__main__':
    main()
