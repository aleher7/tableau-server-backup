"""
Diagnóstico del error 401 al generar el token de la GitHub App.

Comprueba, en orden:
  1. Si el .pem tiene formato correcto (cabecera/pie PEM válidos)
  2. Si el reloj de este PC está desincronizado respecto al de GitHub
     (causa nº1, con diferencia, de un 401 "raro")
  3. Muestra el JWT decodificado (sin verificar) para que puedas comprobar
     tú mismo que el App ID (claim 'iss') es el que esperas
  4. Muestra la respuesta COMPLETA de error de GitHub (antes se recortaba
     a 300 caracteres; el mensaje completo suele decir la causa exacta)

USO:
    python diagnostico_401_github_app.py
"""

import json
import time
from datetime import datetime, timezone
import jwt as pyjwt
import requests


def main():
    config = json.load(open('config.json'))
    ruta_pem = config['github_private_key_path']
    app_id = config['github_app_id']

    print("="*70)
    print("1. Comprobando el archivo .pem")
    print("="*70)
    with open(ruta_pem, 'r') as f:
        contenido = f.read()

    primera_linea = contenido.strip().splitlines()[0] if contenido.strip() else ""
    ultima_linea = contenido.strip().splitlines()[-1] if contenido.strip() else ""

    print(f"Primera línea del archivo: {primera_linea!r}")
    print(f"Última línea del archivo : {ultima_linea!r}")

    if "BEGIN RSA PRIVATE KEY" in contenido or "BEGIN PRIVATE KEY" in contenido:
        print("✅ El archivo tiene cabecera PEM válida")
    else:
        print("❌ El archivo NO parece un .pem válido")
        print("   (debería empezar por '-----BEGIN RSA PRIVATE KEY-----' o similar)")
        return

    print()
    print("="*70)
    print("2. Comprobando la hora de este PC frente a la hora real de GitHub")
    print("="*70)
    hora_local = datetime.now(timezone.utc)
    print(f"Hora de este PC (UTC)      : {hora_local}")

    try:
        resp = requests.get("https://api.github.com", timeout=10)
        hora_github = datetime.strptime(
            resp.headers['Date'], '%a, %d %b %Y %H:%M:%S %Z'
        ).replace(tzinfo=timezone.utc)
        print(f"Hora real de GitHub (UTC)  : {hora_github}")

        diferencia = abs((hora_local - hora_github).total_seconds())
        print(f"Diferencia                 : {diferencia:.0f} segundos")

        if diferencia > 60:
            print()
            print("❌ ¡AQUÍ ESTÁ EL PROBLEMA! El reloj de este PC está desincronizado")
            print("   más de 60 segundos respecto a la hora real.")
            print("   Solución en Windows:")
            print("   Configuración > Hora e idioma > Fecha y hora > 'Sincronizar ahora'")
            print("   (o revisa que la zona horaria configurada sea la correcta)")
        else:
            print("✅ El reloj está sincronizado correctamente (no es la causa)")
    except Exception as e:
        print(f"⚠️  No se pudo comprobar la hora de GitHub: {e}")

    print()
    print("="*70)
    print("2.5 Comprobando qué librería 'jwt' está realmente instalada")
    print("="*70)
    print(f"Módulo cargado desde: {pyjwt.__file__}")
    print(f"Versión (si aplica) : {getattr(pyjwt, '__version__', 'NO TIENE __version__ -> sospechoso')}")
    print("PyJWT (la correcta) siempre tiene __version__ y expone jwt.encode()/jwt.decode().")
    print("Si el archivo cargado NO está dentro de una carpeta 'PyJWT' o similar,")
    print("es probable que tengas instalado el paquete 'jwt' incorrecto (no PyJWT).")
    print()
    print("="*70)
    print("3. Generando el JWT y mostrando su contenido (sin verificar firma)")
    print("="*70)
    ahora = int(time.time())
    payload = {
        'iat': ahora - 60,
        'exp': ahora + (10 * 60),
        'iss': app_id
    }
    token = pyjwt.encode(payload, contenido, algorithm='RS256')

    print(f"Tipo de dato del token generado: {type(token)}")
    if isinstance(token, bytes):
        print("⚠️  El token es 'bytes', no 'str'. Con versiones antiguas de PyJWT")
        print("   (anteriores a la 2.0) esto es normal, PERO hay que decodificarlo")
        print("   a texto antes de usarlo en la cabecera Authorization, si no,")
        print("   se envía literalmente como \"b'eyJhbGci...'\" y GitHub no lo puede leer.")
        token = token.decode('utf-8')
        print("   -> Se ha corregido automáticamente aquí para continuar la prueba.")
    print(f"Primeros 20 caracteres del token: {token[:20]!r}")

    # Decodificamos el propio JWT que acabamos de generar, SOLO para
    # mostrar su contenido -- no verifica nada, es solo lectura del payload
    decodificado = pyjwt.decode(token, options={"verify_signature": False})
    print(f"Payload del JWT enviado a GitHub: {decodificado}")
    print(f"  -> 'iss' (App ID usado)        : {decodificado['iss']!r}")
    print("  Confirma que este es EXACTAMENTE tu App ID (revisa que no tenga")
    print("  comillas de más, espacios, o que sea el Client ID por error)")

    print()
    print("="*70)
    print("4. Enviando el JWT a GitHub y mostrando la respuesta COMPLETA")
    print("="*70)
    url = "https://api.github.com/app/installations"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    respuesta = requests.get(url, headers=headers, timeout=15)
    print(f"Código: {respuesta.status_code}")
    print(f"Respuesta completa de GitHub:")
    print(json.dumps(respuesta.json(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
