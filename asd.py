"""
BACKUP AUTOMATICO DE WORKBOOKS DE TABLEAU A GITHUB
==================================================

Flujo completo:
    Oracle (vista DESCARGA_WORKBOOKS)
      -> ConexionOracle.bat ejecuta Descarga.sql
      -> genera lista_workbooks.csv
      -> este script descarga cada workbook de Tableau
      -> los sube a GitHub por lotes, con Git LFS para los grandes

Uso:
    python descargar_workbooks.py                  # proceso completo
    python descargar_workbooks.py --sin-github     # solo descargar
    python descargar_workbooks.py --config x.json  # otra configuracion

Documentacion completa: MANUAL_Backup_Tableau_GitHub.docx
"""

import os
import sys
import json
import time
import base64
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

import pandas as pd
import jwt as pyjwt
import requests

# La libreria de Tableau se importa dentro de un try para poder dar un mensaje
# entendible si falta, en vez de un traceback de Python.
try:
    import tableauserverclient as TSC
except ImportError:
    print("ERROR: falta la libreria tableauserverclient")
    print("Instala las dependencias con: pip install -r requirements.txt")
    sys.exit(1)


# ============================================================================
# CONSTANTES DEL ENTORNO
# ============================================================================
# Este entorno es GitHub Enterprise Cloud con residencia de datos, NO
# github.com publico. Son tres dominios distintos y no intercambiables:
#   - API REST      -> lleva el prefijo "api."
#   - Git (push)    -> sin prefijo
#   - Almacen LFS   -> lo gestiona Git LFS solo, no hay que tocarlo
GITHUB_DOMINIO = "cantabrialabs.ghe.com"
GITHUB_API = "https://api.cantabrialabs.ghe.com"
GITHUB_API_VERSION = "2026-03-10"

# Workbooks que se descargan antes de hacer cada commit + push.
# No subir todo de golpe: un push de varios GB falla por timeout.
TAMANO_LOTE = 8


# ============================================================================
# LOG
# ============================================================================
# Se escribe a la vez en el fichero y en pantalla.
# IMPORTANTE: nunca usar emojis en los mensajes. La consola del servidor no
# siempre esta en UTF-8 y un caracter no ASCII aborta la ejecucion entera.
logging.basicConfig(
    level=logging.INFO,
    # %(levelname)-5s reserva 5 huecos para el nivel, asi los mensajes quedan
    # alineados en columna aunque unos pongan INFO y otros ERROR.
    format='%(asctime)s  %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('tableau_sync.log', encoding='utf-8'),  # al fichero
        logging.StreamHandler()                                     # a la pantalla
    ]
)
log = logging.getLogger(__name__)


def separador(titulo=""):
    """Linea divisoria en el log, para separar visualmente las fases."""
    log.info("=" * 60)
    if titulo:
        log.info(titulo)
        log.info("=" * 60)


def tamano_legible(ruta):
    """Devuelve el tamano de un fichero como texto ('12.4 MB')."""
    try:
        # .stat() da la informacion del fichero; .st_size es el tamano en bytes
        mb = Path(ruta).stat().st_size / (1024 * 1024)
        return f"{mb:.1f} MB"
    except Exception:
        return "?"


def duracion_legible(segundos):
    """Convierte segundos en '12m 5s'."""
    # divmod devuelve de una vez el cociente y el resto de la division
    minutos, seg = divmod(int(segundos), 60)
    return f"{minutos}m {seg}s" if minutos else f"{seg}s"


# ============================================================================
# CONFIGURACION
# ============================================================================

CLAVES_ORACLE = ['sqlplus_comando', 'archivo_lista_workbooks']
CLAVES_TABLEAU = ['tableau_server', 'tableau_token_name', 'tableau_token', 'tableau_site']
CLAVES_GITHUB = ['github_client_id', 'github_installation_id',
                 'github_private_key_path', 'github_owner', 'github_repo_name']
CLAVES_OPCIONALES = {
    'directorio_descarga': './tableau_workbooks',
    'timeout_sqlplus': 15,
    'github_enabled': True,
}


def cargar_config(fichero="config.json"):
    """
    Carga config.json y comprueba que estan todas las claves necesarias.

    La validacion se hace ANTES de tocar Oracle, Tableau o GitHub: si falta
    algo, el script para aqui con un mensaje claro en vez de fallar a los
    diez minutos con un error criptico a mitad de la descarga.
    """
    try:
        with open(fichero, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        # os.getcwd() = carpeta desde la que se esta ejecutando el script.
        # Se muestra porque la causa habitual es justamente esa: la tarea
        # programada arranca desde otra carpeta y no encuentra el fichero.
        log.error("No se encuentra %s en %s", fichero, os.getcwd())
        log.error("Comprueba que la tarea programada tiene el campo 'Iniciar en' relleno")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("El fichero %s tiene un error de sintaxis: %s", fichero, e)
        sys.exit(1)

    # setdefault anade la clave SOLO si no existe; si ya viene en el fichero,
    # respeta el valor del usuario. Se hace antes de validar porque
    # github_enabled decide si las claves de GitHub son obligatorias o no.
    for clave, valor in CLAVES_OPCIONALES.items():
        config.setdefault(clave, valor)

    obligatorias = CLAVES_ORACLE + CLAVES_TABLEAU
    if config['github_enabled']:
        obligatorias += CLAVES_GITHUB

    faltan = [c for c in obligatorias if c not in config]
    if faltan:
        log.error("Faltan claves obligatorias en %s: %s", fichero, ", ".join(faltan))
        sys.exit(1)

    log.info("Configuracion cargada y validada")
    return config


# ============================================================================
# PASO 2 - ORACLE
# ============================================================================

def ejecutar_sqlplus(comando, timeout):
    """Lanza ConexionOracle.bat, que hace login en Oracle y ejecuta Descarga.sql."""
    try:
        resultado = subprocess.run(
            comando,
            shell=True,             # necesario para ejecutar un .bat de Windows
            capture_output=True,    # guarda la salida en vez de mostrarla
            text=True,              # devuelve texto, no bytes
            timeout=timeout,        # si tarda mas, lanza TimeoutExpired
        )
    except subprocess.TimeoutExpired:
        log.error("Oracle no respondio en %d segundos", timeout)
        log.error("Sube 'timeout_sqlplus' en config.json si la consulta es lenta")
        return False
    except Exception as e:
        log.error("No se pudo lanzar el comando de Oracle: %s", e)
        return False

    # Convencion universal: codigo de salida 0 = correcto, cualquier otro = error.
    # Aqui se puede confiar en el porque Descarga.sql empieza con los dos
    # WHENEVER que obligan a SQL*Plus a abortar ante cualquier fallo.
    if resultado.returncode != 0:
        log.error("Oracle devolvio un error (codigo %d)", resultado.returncode)
        log.error("Abre lista_workbooks.csv: el mensaje de Oracle esta dentro")
        return False

    return True


# ============================================================================
# PASO 3 - LEER LA LISTA
# ============================================================================

def leer_lista_workbooks(ruta, separador_csv=','):
    """
    Convierte lista_workbooks.csv en una tabla de trabajo (DataFrame).

    Devuelve None si el fichero no sirve, para que main() pueda abortar.
    """
    ruta = Path(ruta)

    if not ruta.is_file():
        log.error("No se genero el fichero %s", ruta)
        return None
    if ruta.stat().st_size == 0:       # st_size en bytes: 0 = fichero vacio
        log.error("El fichero %s esta vacio", ruta)
        return None

    try:
        df = pd.read_csv(
            ruta,
            sep=separador_csv,
            dtype=str,              # un LUID no es un numero: todo como texto
            encoding='utf-8',
            quotechar='"',          # Descarga.sql usa QUOTE ON: los campos vienen
                                    # entrecomillados, y sin esto una coma dentro
                                    # de un nombre partiria la fila
            keep_default_na=False,  # los campos vacios se quedan vacios, no NaN
            skipinitialspace=True,  # ignora los espacios que siguen a cada coma
        )
    except Exception as e:
        log.error("El fichero no tiene formato CSV valido: %s", e)
        return None

    # Cabeceras a mayusculas y sin espacios, para que las comprobaciones de
    # mas abajo funcionen aunque Oracle las devuelva de otra forma.
    df.columns = [str(c).strip().upper() for c in df.columns]

    # .str aplica una operacion de texto a TODOS los valores de la columna de
    # una vez, sin recorrerla. Aqui quita espacios sobrantes: un LUID con un
    # espacio invisible al final no coincide con el real y produce un
    # "workbook no encontrado" imposible de diagnosticar a simple vista.
    for columna in df.columns:
        df[columna] = df[columna].astype(str).str.strip()

    # Resta de conjuntos: lo que hace falta menos lo que hay = lo que falta
    faltan = {"WORKBOOK_LUID", "WORKBOOK"} - set(df.columns)
    if faltan:
        log.error("La vista de Oracle no devuelve las columnas: %s", ", ".join(faltan))
        log.error("Columnas recibidas: %s", ", ".join(df.columns))
        return None

    if "RUTA_PROYECTO" not in df.columns:
        df["RUTA_PROYECTO"] = "default"

    # df[condicion] devuelve solo las filas que cumplen la condicion.
    # Las filas sin LUID son las de 'carpeta intermedia' de la vista, que
    # existen solo como control visual al revisar la consulta en Oracle.
    df = df[(df["WORKBOOK_LUID"] != "") & (df["WORKBOOK"] != "")]

    # drop_duplicates(subset=...) mira solo esa columna para decidir que es
    # duplicado; keep='last' se queda con la ultima aparicion.
    # reset_index(drop=True) renumera las filas de 0 en adelante y descarta la
    # numeracion antigua, que quedo con huecos tras el filtrado anterior.
    df = df.drop_duplicates(subset=["WORKBOOK_LUID"], keep="last").reset_index(drop=True)

    return df


# ============================================================================
# AUTENTICACION CON GITHUB APP
# ============================================================================

def obtener_token_github(config):
    """
    Consigue un token de instalacion valido durante una hora.

    Son dos pasos: se firma un JWT con la clave privada (.pem) y se canjea
    por el token real. El JWT dura solo 10 minutos y no sirve para nada mas.

    OJO: el emisor (iss) del JWT es el CLIENT ID, no el App ID. GitHub
    documenta ambos como validos, pero con el App ID devuelve un
    "401 - A JSON web token could not be decoded" que no dice nada util.
    """
    # time.time() da los segundos transcurridos desde 1970, que es el formato
    # de fecha que exige el estandar JWT.
    ahora = int(time.time())
    payload = {
        'iat': ahora - 60,   # emitido: 60s de margen por si el reloj va adelantado
        'exp': ahora + 600,  # caduca a los 10 minutos (maximo que admite GitHub)
        'iss': config['github_client_id'],
    }

    # Modo binario ('rb'): pyjwt espera los bytes de la clave, no texto.
    with open(config['github_private_key_path'], 'rb') as f:
        llave = f.read()

    # RS256 = firma asimetrica con RSA. Se firma con la clave privada que
    # tenemos aqui, y GitHub lo verifica con la publica que guarda de la App.
    jwt_token = pyjwt.encode(payload, llave, algorithm='RS256')

    # Las versiones de PyJWT anteriores a la 2.0 devuelven bytes en vez de
    # texto. Si se enviaran tal cual, la cabecera saldria como "b'eyJ...'".
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode('utf-8')

    url = f"{GITHUB_API}/app/installations/{config['github_installation_id']}/access_tokens"
    cabeceras = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }

    # POST y no GET porque esta llamada CREA algo nuevo: un token que antes no
    # existia. Cada vez que se llama, GitHub genera uno distinto.
    respuesta = requests.post(url, headers=cabeceras, timeout=15)

    # 201 = "creado". Esta llamada no devuelve 200.
    if respuesta.status_code != 201:
        log.error("GitHub rechazo la autenticacion (codigo %d)", respuesta.status_code)
        log.error("Respuesta: %s", respuesta.text[:200])
        log.error("Ejecuta 'python diagnostico_github.py' para localizar la causa")
        return None

    # .json() convierte la respuesta (texto en formato JSON) en un diccionario
    return respuesta.json()['token']


def cabecera_git(token):
    """
    Prepara la autenticacion de git para pasarsela con 'git -c ...'.

    El token va en una cabecera HTTP, NO dentro de la URL, por dos motivos:

      1. Con el token en la URL, es el propio git quien lo imprime en sus
         mensajes de aviso, y acaba visible en pantalla y en el log.
      2. La cabecera se restringe a este dominio concreto. Si se declarara
         de forma generica (http.extraHeader), git la enviaria tambien al
         almacen de objetos de Git LFS, que vive en otro dominio y usa sus
         propias URLs firmadas: chocan y la subida de LFS falla.
    """
    # Tres conversiones encadenadas, en este orden:
    #   .encode()    texto -> bytes (lo que b64encode necesita)
    #   b64encode()  bytes -> bytes codificados en base64
    #   .decode()    bytes -> texto otra vez, para meterlo en la cabecera
    # Base64 no cifra nada: es el formato que exige la autenticacion HTTP Basic.
    credencial = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"http.https://{GITHUB_DOMINIO}.extraHeader=Authorization: Basic {credencial}"


def url_repo(config):
    """URL del repositorio, sin credenciales."""
    return f"https://{GITHUB_DOMINIO}/{config['github_owner']}/{config['github_repo_name']}.git"


# ============================================================================
# EJECUCION DE COMANDOS GIT
# ============================================================================

def ocultar_secretos(texto, secretos):
    """Sustituye cualquier token por *** antes de imprimir o guardar nada."""
    for secreto in secretos or []:   # 'secretos or []' evita fallar si llega None
        if secreto:
            texto = texto.replace(secreto, "***")
    return texto


def git(comando, secretos=None, mostrar=True):
    """
    Ejecuta un comando git mostrando su salida en tiempo real.

    Se usa Popen en vez de subprocess.run porque run espera a que el comando
    TERMINE para devolver la salida. Con ficheros de cientos de MB, git tarda
    minutos comprimiendo y la consola se quedaria en blanco todo ese rato,
    dando la impresion de estar colgado.

    Devuelve (codigo_de_salida, salida_completa_ya_censurada).
    """
    proceso = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,      # capturamos la salida normal
        stderr=subprocess.STDOUT,    # y mezclamos los errores en el mismo flujo,
                                     # porque git escribe el progreso en stderr
        text=True,
        encoding='utf-8',            # git escribe en UTF-8; sin forzarlo, Python
                                     # usaria cp1252 (Windows en espanol) y se
                                     # atasca al leer ciertos caracteres
        errors='replace',            # si aun asi llega un byte raro, lo sustituye
                                     # en vez de romper la lectura
        bufsize=1,                   # entrega cada linea en cuanto aparece, sin
                                     # esperar a llenar un bloque de memoria
    )

    lineas = []
    # Este bucle NO espera a que git termine: lee segun van llegando las lineas
    for linea in proceso.stdout:
        linea = ocultar_secretos(linea.rstrip(), secretos)
        if linea:
            lineas.append(linea)
            if mostrar:
                log.info("        %s", linea)

    proceso.wait()   # ya se leyo toda la salida; esperamos a que cierre el proceso
    return proceso.returncode, "\n".join(lineas)


# ============================================================================
# PASO 4 - SINCRONIZAR CON GITHUB (antes de descargar)
# ============================================================================

def sincronizar_con_remoto(directorio, config, token):
    """
    Deja la carpeta local exactamente igual que el repositorio remoto.

    Se hace ANTES de descargar nada. Asi el commit de esta ejecucion es
    simplemente el siguiente de la historia y no hay que fusionar nada.

    Sin esto habria que fusionar ficheros binarios: cada exportacion de un
    mismo workbook produce bytes distintos aunque el contenido no cambie
    (Tableau mete metadatos internos), y git no sabe resolver eso solo.

    Usa --hard, que si sobrescribe ficheros locales. No hay riesgo: el paso
    siguiente vacia la carpeta igualmente y todo se vuelve a descargar.
    """
    os.chdir(directorio)   # git actua sobre la carpeta actual: hay que situarse dentro
    cabecera = cabecera_git(token)
    url = url_repo(config)

    # Por si una ejecucion anterior se corto en mitad de una fusion. Si no
    # habia ninguna, git devuelve error y no pasa nada: se ignora.
    git(['git', 'merge', '--abort'], [token], mostrar=False)

    # 'git -c clave=valor' aplica un ajuste SOLO a este comando, sin dejarlo
    # guardado en la configuracion del repositorio. Asi el token no persiste
    # en ningun fichero del disco.
    codigo, salida = git(['git', '-c', cabecera, 'fetch', url, 'main'], [token], mostrar=False)
    if codigo != 0:
        log.error("No se pudo consultar el repositorio remoto")
        log.error("%s", salida)
        return False

    # FETCH_HEAD es la referencia temporal que acaba de dejar el fetch: apunta
    # a lo ultimo que hay en el remoto. --hard alinea con ello tanto el
    # historial como los ficheros del disco.
    codigo, salida = git(['git', 'reset', '--hard', 'FETCH_HEAD'], [token], mostrar=False)
    if codigo != 0:
        log.error("No se pudo alinear la carpeta local con el repositorio")
        log.error("%s", salida)
        return False

    return True


# ============================================================================
# PASO 5 - VACIAR LA CARPETA DE DESCARGAS
# ============================================================================

# Ficheros que NO se borran nunca al vaciar la carpeta:
#   .git           -> es el repositorio
#   .gitattributes -> es la configuracion de Git LFS. Si desaparece, los
#                     .twbx grandes se intentan subir como ficheros normales
#                     y GitHub los rechaza por superar los 100 MB. El fallo
#                     no avisa: simplemente el push deja de funcionar.
PROTEGIDOS = {".git", ".gitattributes"}

# Patron SIN barra: coincide con cualquier .twbx a cualquier profundidad
# dentro de esta carpeta. Un patron con ruta (ej. "Tableau Workbooks/*.twbx")
# solo alcanzaria a los .twbx que esten DIRECTAMENTE en esa carpeta, dejando
# fuera los de subcarpetas de proyecto -- que son casi todos.
CONTENIDO_GITATTRIBUTES = "*.twbx filter=lfs diff=lfs merge=lfs -text\n"


def asegurar_gitattributes(directorio):
    """
    Garantiza que .gitattributes existe y tiene el contenido correcto,
    ESCRIBIENDOLO SIEMPRE en cada ejecucion, sin depender de que haya
    sobrevivido a una sincronizacion anterior con GitHub.

    Por que esto y no solo confiar en que este bien en el remoto: si en
    algun momento se pierde la version correcta en GitHub (por ejemplo un
    push que no llego a completarse del todo), la sincronizacion del paso 4
    seguiria trayendo una version mala o inexistente una y otra vez, sin
    que protegerlo al vaciar la carpeta sirviera de nada. Escribirlo aqui,
    siempre, hace que el propio script sea la fuente de verdad de este
    archivo en vez de depender de lo que haya en el remoto.

    Si el contenido ya es el correcto, escribir encima no genera ningun
    cambio real (git compara por contenido, no por fecha de modificacion).
    """
    ruta = Path(directorio) / ".gitattributes"
    ruta.write_text(CONTENIDO_GITATTRIBUTES, encoding='utf-8', newline='\n')


def vaciar_directorio(directorio):
    """Borra el contenido de la carpeta de descargas, salvo lo protegido."""
    ruta = Path(directorio)

    if ruta.exists():
        # .iterdir() lista lo que hay dentro de la carpeta, un solo nivel
        # (no entra en las subcarpetas).
        for elemento in ruta.iterdir():
            if elemento.name in PROTEGIDOS:
                continue
            try:
                if elemento.is_dir():
                    # rmtree borra la carpeta Y todo su contenido, recursivamente.
                    # Es la unica forma: os.rmdir solo admite carpetas vacias.
                    shutil.rmtree(elemento)
                else:
                    # unlink borra un fichero suelto (equivale a os.remove,
                    # pero en la sintaxis de pathlib).
                    elemento.unlink()
            except Exception as e:
                log.warning("No se pudo borrar %s: %s", elemento.name, e)

    # parents=True crea tambien las carpetas intermedias que falten;
    # exist_ok=True evita el error si la carpeta ya existe.
    ruta.mkdir(parents=True, exist_ok=True)


# ============================================================================
# PASO 6 - TABLEAU
# ============================================================================

def conectar_tableau(config):
    """Inicia sesion en Tableau Cloud con el token de acceso personal (PAT)."""
    try:
        auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site'],   # es el content URL del sitio,
                                              # no su nombre visible
        )
        servidor = TSC.Server(config['tableau_server'])
        servidor.auth.sign_in(auth)
        return servidor
    except Exception as e:
        log.error("No se pudo conectar con Tableau: %s", e)
        log.error("Si el error es 401, el PAT ha caducado: renuevalo en Tableau Cloud")
        sys.exit(1)


def descargar_workbook(servidor, luid, destino):
    """
    Descarga un workbook y lo deja en su ruta final.

    Tableau devuelve dos formatos segun el workbook:
      .twbx -> empaquetado, con los datos o el extracto dentro
      .twb  -> sin empaquetar, solo la definicion (conexion en vivo)
    Se aceptan ambos y se conserva la extension real. Forzar .twbx sobre un
    fichero que en realidad es .twb da un archivo que Tableau no abre bien.
    """
    try:
        destino = Path(destino)
        # .parent es la carpeta que contiene el fichero. Hay que crearla antes,
        # porque Tableau no reproduce la jerarquia de proyectos por su cuenta.
        destino.parent.mkdir(parents=True, exist_ok=True)

        # .stem es el nombre del fichero SIN extension. Se descarga a esa ruta
        # porque la libreria decide ella la extension final, y ademas a veces
        # crea una carpeta con ese nombre y mete el fichero dentro.
        temporal = str(destino.parent / destino.stem)
        servidor.workbooks.download(luid, filepath=temporal)

        carpeta = Path(temporal)
        if carpeta.is_dir():
            # .glob() busca por patron dentro de la carpeta. Se concatenan las
            # dos listas para aceptar cualquiera de los dos formatos.
            encontrados = list(carpeta.glob('*.twbx')) + list(carpeta.glob('*.twb'))
            if not encontrados:
                log.error("        Tableau no devolvio ningun fichero")
                return None

            # .with_suffix() devuelve la misma ruta cambiando la extension. Asi
            # el fichero final conserva la que Tableau haya usado de verdad.
            final = destino.with_suffix(encontrados[0].suffix)
            shutil.move(str(encontrados[0]), str(final))   # mover, no copiar

            # ignore_errors=True: si la carpeta temporal no se puede borrar no
            # importa, el fichero bueno ya esta en su sitio.
            shutil.rmtree(carpeta, ignore_errors=True)
            return final

        # Algunas versiones de la libreria dejan el fichero suelto, sin carpeta.
        for extension in ('.twbx', '.twb'):
            candidato = destino.with_suffix(extension)
            if candidato.exists():
                return candidato

        log.error("        No se encontro el fichero descargado")
        return None

    except Exception as e:
        log.error("        Error al descargar: %s", e)
        return None


# ============================================================================
# PASO 7 - SUBIR A GITHUB
# ============================================================================

def subir_a_github(directorio, config, token, mensaje):
    """
    Hace commit y push de lo que haya en la carpeta de descargas.

    Devuelve True si se subio (o si no habia nada que subir).
    """
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)

    # Margen amplio para transferencias grandes, y sin corte por lentitud: por
    # defecto git aborta si la velocidad baja de 1 KB/s durante 10 segundos.
    # Estos ajustes SI quedan guardados en el repositorio (no llevan '-c'),
    # asi que basta con aplicarlos; repetirlos cada vez no cuesta nada.
    for ajuste in [('http.postBuffer', '2147483648'),
                   ('http.lowSpeedLimit', '0'),
                   ('http.lowSpeedTime', '999999')]:
        # El * desempaqueta la tupla en argumentos sueltos:
        # ('http.postBuffer', '2147483648') pasa a ser dos elementos de la lista
        git(['git', 'config', *ajuste], mostrar=False)

    # rm --cached + add (y NO 'git add --renormalize'): --renormalize implica
    # -u, que solo actualiza ficheros ya rastreados y nunca anade nuevos. Como
    # la carpeta se vacia en cada ejecucion, casi todos los ficheros son
    # nuevos y --renormalize los ignoraria en silencio, sin comitear nada.
    #
    #   --cached          -> saca del indice de git, pero NO borra del disco
    #   --ignore-unmatch  -> no falla si el fichero no estaba rastreado
    # Sacarlos y volver a anadirlos fuerza ademas que se aplique el filtro de LFS.
    git(['git', 'rm', '-r', '--cached', '--ignore-unmatch', '.'], [token], mostrar=False)

    codigo, salida = git(['git', 'add', '.'], [token], mostrar=False)
    if codigo != 0:
        log.error("        No se pudieron preparar los ficheros")
        log.error("        %s", salida)
        return False

    codigo, salida = git(['git', 'commit', '-m', mensaje], [token], mostrar=False)
    # git devuelve codigo de error cuando no hay nada que comitear, pero eso no
    # es un fallo real: hay que distinguirlo mirando el texto de la salida.
    if "nothing to commit" in salida.lower():
        log.info("        Sin cambios que subir")
        return True
    if codigo != 0:
        log.error("        No se pudo crear el commit")
        log.error("        %s", salida)
        return False

    # -X ours: si hay conflicto, gana la version recien descargada. Es lo
    # correcto en un backup, y evita que git se pare intentando fusionar
    # ficheros binarios que cambian de bytes en cada exportacion.
    # --no-edit: no abre el editor de texto para el mensaje de la fusion.
    git(['git', 'merge', '--abort'], [token], mostrar=False)
    codigo, salida = git(
        ['git', '-c', cabecera, 'pull', '--no-edit', '-X', 'ours', url, 'main'],
        [token], mostrar=False
    )
    if codigo != 0:
        log.error("        No se pudo sincronizar antes de subir")
        log.error("        %s", salida)
        return False

    # Este si se muestra en pantalla: es donde aparece el progreso de Git LFS
    codigo, salida = git(['git', '-c', cabecera, 'push', url, 'main'], [token])

    # Un rechazo por 'fetch first' significa que el remoto avanzo entre el pull
    # y el push. Se reintenta una vez antes de darlo por fallido.
    if codigo != 0 and ("fetch first" in salida.lower() or "non-fast-forward" in salida.lower()):
        log.info("        El repositorio avanzo mientras subiamos, reintentando")
        git(['git', '-c', cabecera, 'pull', '--no-edit', '-X', 'ours', url, 'main'],
            [token], mostrar=False)
        codigo, salida = git(['git', '-c', cabecera, 'push', url, 'main'], [token])

    if codigo != 0:
        log.error("        Fallo la subida a GitHub")
        if "exceeds GitHub's file size limit" in salida:
            log.error("        Hay un fichero de mas de 100 MB que no esta pasando por Git LFS")
            log.error("        Comprueba que existe 'Tableau Workbooks\\.gitattributes'")
        return False

    return True


def actualizar_referencia_remota(directorio, config, token):
    """
    Pone al dia la referencia local de origin/main.

    Hace falta porque el push se hace contra una URL directa, no contra el
    remoto 'origin'. En ese caso git sube los datos correctamente pero no
    actualiza su propia referencia, y un 'git status' posterior diria que
    hay commits pendientes cuando en realidad ya estan todos subidos.
    """
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)
    git(['git', '-c', cabecera, 'fetch', url, 'main'], [token], mostrar=False)
    # update-ref mueve a mano un puntero interno de git. Aqui apunta
    # origin/main a lo que acaba de traer el fetch.
    git(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], [token], mostrar=False)


# ============================================================================
# BUCLE PRINCIPAL DE DESCARGA
# ============================================================================

def descargar_y_subir(servidor, df, directorio, config, subir):
    """
    Descarga todos los workbooks y, cada TAMANO_LOTE, los sube a GitHub.

    El token se pide una sola vez y se reutiliza: dura una hora, tiempo de
    sobra para una ejecucion completa.

    Que un workbook falle no detiene el proceso; se anota y se sigue.
    """
    stats = {'total': len(df), 'ok': 0, 'error': 0,
             'lotes_ok': 0, 'lotes_error': 0}

    token = None
    if subir:
        token = obtener_token_github(config)
        if token is None:
            log.warning("Se descargara todo, pero no se subira nada a GitHub")
            subir = False

    # .iterrows() recorre el DataFrame fila a fila y devuelve pares
    # (indice, fila); el indice no se usa, de ahi el guion bajo.
    # enumerate(..., start=1) anade el contador que se ve en el log.
    for numero, (_, fila) in enumerate(df.iterrows(), start=1):
        luid = fila['WORKBOOK_LUID']
        nombre = fila['WORKBOOK']
        proyecto = fila.get('RUTA_PROYECTO', 'default')

        log.info("  [%d/%d] %s", numero, stats['total'], nombre)
        log.info("        Proyecto: %s", proyecto)

        # El operador / entre objetos Path une rutas con el separador correcto
        # del sistema (\ en Windows), sin tener que escribirlo a mano.
        destino = Path(directorio) / proyecto / f"{nombre}.twbx"
        fichero = descargar_workbook(servidor, luid, destino)

        if fichero:
            stats['ok'] += 1
            log.info("        Descargado (%s)", tamano_legible(fichero))
        else:
            stats['error'] += 1
            log.info("        LUID: %s", luid)   # para poder buscarlo en Tableau

        es_ultimo = (numero == stats['total'])
        # % es el resto de la division: vale 0 cada TAMANO_LOTE vueltas.
        # El 'or es_ultimo' asegura que los que sobren del ultimo lote suban tambien.
        if subir and (numero % TAMANO_LOTE == 0 or es_ultimo):
            log.info("  --- Subiendo lote (%d/%d workbooks procesados) ---",
                     numero, stats['total'])
            mensaje = f"Tableau Backup - lote hasta {numero}/{stats['total']}"
            if subir_a_github(directorio, config, token, mensaje):
                stats['lotes_ok'] += 1
                log.info("        Lote subido")
            else:
                stats['lotes_error'] += 1
                log.warning("        Lote fallido: sus ficheros iran en el siguiente")

    if subir and token:
        actualizar_referencia_remota(directorio, config, token)

    return stats


# ============================================================================
# RESUMEN FINAL
# ============================================================================

def mostrar_resumen(stats, segundos):
    """Bloque final del log. Es lo unico que hay que mirar cada manana."""
    separador("RESUMEN DE LA EJECUCION")
    log.info("Workbooks en la lista ....... %d", stats['total'])
    log.info("Descargados correctamente ... %d", stats['ok'])
    log.info("Con error ................... %d", stats['error'])
    log.info("Lotes subidos a GitHub ...... %d", stats['lotes_ok'])
    log.info("Lotes fallidos .............. %d", stats['lotes_error'])
    log.info("Tiempo total ................ %s", duracion_legible(segundos))
    log.info("=" * 60)

    if stats['error'] == 0 and stats['lotes_error'] == 0:
        log.info("BACKUP COMPLETADO SIN ERRORES")
    else:
        log.warning("BACKUP COMPLETADO CON INCIDENCIAS - revisa el log")
    log.info("=" * 60)


# ============================================================================
# PROGRAMA PRINCIPAL
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Backup de workbooks de Tableau a GitHub")
    parser.add_argument('--config', default='config.json', help="fichero de configuracion")
    # action='store_true': la opcion no lleva valor. Si aparece vale True.
    parser.add_argument('--sin-github', action='store_true', help="descargar sin subir a GitHub")
    parser.add_argument('--separador', default=',', help="separador del CSV")
    args = parser.parse_args()

    inicio = datetime.now()
    separador("BACKUP TABLEAU -> GITHUB")

    config = cargar_config(args.config)
    directorio = config['directorio_descarga']
    lista = Path(config['archivo_lista_workbooks'])
    subir = config['github_enabled'] and not args.sin_github

    # --- Paso 1: borrar la lista anterior ---------------------------------
    # Se borra ANTES de pedir la nueva. Si Oracle fallara y no regenerase el
    # fichero, el paso 3 se encontraria con que no existe y abortaria, en vez
    # de seguir trabajando en silencio con los datos de ayer.
    log.info("[1/8] Borrando la lista anterior")
    if lista.exists():
        try:
            lista.unlink()
            log.info("      Eliminada")
        except OSError as e:
            # Pasa si el fichero esta abierto en otro programa (por ejemplo,
            # alguien lo dejo abierto en Excel o en el Bloc de notas).
            log.warning("      No se pudo borrar (%s), se intentara sobrescribir", e)
    else:
        log.info("      No habia lista anterior")

    # --- Paso 2: consultar Oracle -----------------------------------------
    log.info("[2/8] Consultando Oracle")
    if not ejecutar_sqlplus(config['sqlplus_comando'], config['timeout_sqlplus']):
        log.error("Proceso abortado: sin lista de workbooks no hay nada que descargar")
        sys.exit(1)
    log.info("      Lista generada")

    # --- Paso 3: leer la lista --------------------------------------------
    log.info("[3/8] Leyendo la lista de workbooks")
    df = leer_lista_workbooks(lista, args.separador)
    if df is None or df.empty:
        log.error("Proceso abortado: la lista no contiene workbooks validos")
        sys.exit(1)
    log.info("      %d workbooks encontrados", len(df))

    # --- Paso 4: sincronizar con GitHub -----------------------------------
    if subir:
        log.info("[4/8] Sincronizando con GitHub")
        token = obtener_token_github(config)
        if token is None or not sincronizar_con_remoto(directorio, config, token):
            log.error("Proceso abortado: sin sincronizar antes, la subida daria conflictos")
            sys.exit(1)
        log.info("      Carpeta local alineada con el repositorio")
    else:
        log.info("[4/8] Sincronizacion omitida (modo sin GitHub)")

    # --- Paso 5: vaciar la carpeta ----------------------------------------
    log.info("[5/8] Vaciando la carpeta de descargas")
    vaciar_directorio(directorio)
    if subir:
        asegurar_gitattributes(directorio)   # LFS activo, pase lo que pase
    log.info("      Lista para recibir la descarga")

    # --- Paso 6: conectar con Tableau -------------------------------------
    log.info("[6/8] Conectando con Tableau")
    servidor = conectar_tableau(config)
    log.info("      Conectado")

    # --- Paso 7: descargar y subir ----------------------------------------
    if subir:
        log.info("[7/8] Descargando y subiendo en lotes de %d", TAMANO_LOTE)
    else:
        log.info("[7/8] Descargando (sin subir a GitHub)")
    stats = descargar_y_subir(servidor, df, directorio, config, subir)

    # Cerrar sesion en Tableau para no dejarlas acumuladas en el servidor.
    # Si falla no importa: caducan solas.
    try:
        servidor.auth.sign_out()
    except Exception:
        pass

    # --- Paso 8: resumen ---------------------------------------------------
    log.info("[8/8] Resumen")
    mostrar_resumen(stats, (datetime.now() - inicio).total_seconds())


# Solo se ejecuta main() si el fichero se lanza directamente, no si algun dia
# se importa desde otro script para reutilizar sus funciones.
if __name__ == '__main__':
    main()
