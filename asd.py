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
    format='%(asctime)s  %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('tableau_sync.log', encoding='utf-8'),
        logging.StreamHandler()
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
        mb = Path(ruta).stat().st_size / (1024 * 1024)
        return f"{mb:.1f} MB"
    except Exception:
        return "?"


def duracion_legible(segundos):
    """Convierte segundos en '12m 5s'."""
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
        log.error("No se encuentra %s en %s", fichero, os.getcwd())
        log.error("Comprueba que la tarea programada tiene el campo 'Iniciar en' relleno")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("El fichero %s tiene un error de sintaxis: %s", fichero, e)
        sys.exit(1)

    # Rellenar opcionales antes de validar: github_enabled puede no venir
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
    """
    Lanza ConexionOracle.bat, que hace login en Oracle y ejecuta Descarga.sql.

    shell=True porque el comando es un .bat de Windows.
    """
    try:
        resultado = subprocess.run(
            comando, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log.error("Oracle no respondio en %d segundos", timeout)
        log.error("Sube 'timeout_sqlplus' en config.json si la consulta es lenta")
        return False
    except Exception as e:
        log.error("No se pudo lanzar el comando de Oracle: %s", e)
        return False

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
    if ruta.stat().st_size == 0:
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
            skipinitialspace=True,
        )
    except Exception as e:
        log.error("El fichero no tiene formato CSV valido: %s", e)
        return None

    df.columns = [str(c).strip().upper() for c in df.columns]
    for columna in df.columns:
        df[columna] = df[columna].astype(str).str.strip()

    faltan = {"WORKBOOK_LUID", "WORKBOOK"} - set(df.columns)
    if faltan:
        log.error("La vista de Oracle no devuelve las columnas: %s", ", ".join(faltan))
        log.error("Columnas recibidas: %s", ", ".join(df.columns))
        return None

    if "RUTA_PROYECTO" not in df.columns:
        df["RUTA_PROYECTO"] = "default"

    # Las filas sin LUID son las de 'carpeta intermedia' de la vista, que
    # existen solo como control visual al revisar la consulta en Oracle.
    df = df[(df["WORKBOOK_LUID"] != "") & (df["WORKBOOK"] != "")]
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
    ahora = int(time.time())
    payload = {
        'iat': ahora - 60,   # 60s de margen por si el reloj va adelantado
        'exp': ahora + 600,
        'iss': config['github_client_id'],
    }

    with open(config['github_private_key_path'], 'rb') as f:
        llave = f.read()

    jwt_token = pyjwt.encode(payload, llave, algorithm='RS256')
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode('utf-8')

    url = f"{GITHUB_API}/app/installations/{config['github_installation_id']}/access_tokens"
    cabeceras = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }

    respuesta = requests.post(url, headers=cabeceras, timeout=15)

    if respuesta.status_code != 201:
        log.error("GitHub rechazo la autenticacion (codigo %d)", respuesta.status_code)
        log.error("Respuesta: %s", respuesta.text[:200])
        log.error("Ejecuta 'python diagnostico_github.py' para localizar la causa")
        return None

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
    for secreto in secretos or []:
        if secreto:
            texto = texto.replace(secreto, "***")
    return texto


def git(comando, secretos=None, mostrar=True):
    """
    Ejecuta un comando git mostrando su salida en tiempo real.

    En vez de capturar la salida y mostrarla al final, se va imprimiendo
    linea a linea: con archivos de cientos de MB, git tarda minutos
    comprimiendo y sin esto la consola se queda en blanco, dando la
    impresion de estar colgado.

    encoding='utf-8' es necesario porque git escribe en UTF-8 y Python
    usaria por defecto la codificacion de Windows en espanol (cp1252),
    que se atasca con algunos caracteres.

    Devuelve (codigo_de_salida, salida_completa_ya_censurada).
    """
    proceso = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )

    lineas = []
    for linea in proceso.stdout:
        linea = ocultar_secretos(linea.rstrip(), secretos)
        if linea:
            lineas.append(linea)
            if mostrar:
                log.info("        %s", linea)

    proceso.wait()
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
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)

    git(['git', 'merge', '--abort'], [token], mostrar=False)  # por si quedo algo a medias

    codigo, salida = git(['git', '-c', cabecera, 'fetch', url, 'main'], [token], mostrar=False)
    if codigo != 0:
        log.error("No se pudo consultar el repositorio remoto")
        log.error("%s", salida)
        return False

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


def vaciar_directorio(directorio):
    """Borra el contenido de la carpeta de descargas, salvo lo protegido."""
    ruta = Path(directorio)

    if ruta.exists():
        for elemento in ruta.iterdir():
            if elemento.name in PROTEGIDOS:
                continue
            try:
                shutil.rmtree(elemento) if elemento.is_dir() else elemento.unlink()
            except Exception as e:
                log.warning("No se pudo borrar %s: %s", elemento.name, e)

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
        destino.parent.mkdir(parents=True, exist_ok=True)

        # La libreria crea a veces una carpeta con el nombre del workbook y
        # mete el fichero dentro, asi que se descarga a una ruta temporal.
        temporal = str(destino.parent / destino.stem)
        servidor.workbooks.download(luid, filepath=temporal)

        carpeta = Path(temporal)
        if carpeta.is_dir():
            encontrados = list(carpeta.glob('*.twbx')) + list(carpeta.glob('*.twb'))
            if not encontrados:
                log.error("        Tableau no devolvio ningun fichero")
                return None
            final = destino.with_suffix(encontrados[0].suffix)
            shutil.move(str(encontrados[0]), str(final))
            shutil.rmtree(carpeta, ignore_errors=True)
            return final

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

    # Margen amplio para transferencias grandes, y sin corte por lentitud:
    # por defecto git aborta si la velocidad baja de 1 KB/s durante 10s.
    for ajuste in [('http.postBuffer', '2147483648'),
                   ('http.lowSpeedLimit', '0'),
                   ('http.lowSpeedTime', '999999')]:
        git(['git', 'config', *ajuste], mostrar=False)

    # rm --cached + add (y NO 'git add --renormalize'): --renormalize implica
    # -u, que solo actualiza ficheros ya rastreados y nunca anade nuevos. Como
    # la carpeta se vacia en cada ejecucion, casi todos los ficheros son
    # nuevos y --renormalize los ignoraria en silencio, sin comitear nada.
    # Sacarlos del indice y volver a anadirlos fuerza ademas el filtro de LFS.
    git(['git', 'rm', '-r', '--cached', '--ignore-unmatch', '.'], [token], mostrar=False)

    codigo, salida = git(['git', 'add', '.'], [token], mostrar=False)
    if codigo != 0:
        log.error("        No se pudieron preparar los ficheros")
        log.error("        %s", salida)
        return False

    codigo, salida = git(['git', 'commit', '-m', mensaje], [token], mostrar=False)
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
    git(['git', 'merge', '--abort'], [token], mostrar=False)
    codigo, salida = git(
        ['git', '-c', cabecera, 'pull', '--no-edit', '-X', 'ours', url, 'main'],
        [token], mostrar=False
    )
    if codigo != 0:
        log.error("        No se pudo sincronizar antes de subir")
        log.error("        %s", salida)
        return False

    codigo, salida = git(['git', '-c', cabecera, 'push', url, 'main'], [token])

    # Un rechazo por 'fetch first' significa que el remoto avanzo entre el
    # pull y el push. Se reintenta una vez antes de darlo por fallido.
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

    for numero, (_, fila) in enumerate(df.iterrows(), start=1):
        luid = fila['WORKBOOK_LUID']
        nombre = fila['WORKBOOK']
        proyecto = fila.get('RUTA_PROYECTO', 'default')

        log.info("  [%d/%d] %s", numero, stats['total'], nombre)
        log.info("        Proyecto: %s", proyecto)

        destino = Path(directorio) / proyecto / f"{nombre}.twbx"
        fichero = descargar_workbook(servidor, luid, destino)

        if fichero:
            stats['ok'] += 1
            log.info("        Descargado (%s)", tamano_legible(fichero))
        else:
            stats['error'] += 1
            log.info("        LUID: %s", luid)   # para poder buscarlo en Tableau

        es_ultimo = (numero == stats['total'])
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
    # fichero, el paso 3 se encontraria con que no existe y abortaria, en
    # vez de seguir trabajando en silencio con los datos de ayer.
    log.info("[1/8] Borrando la lista anterior")
    if lista.exists():
        try:
            lista.unlink()
            log.info("      Eliminada")
        except OSError as e:
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

    try:
        servidor.auth.sign_out()
    except Exception:
        pass

    # --- Paso 8: resumen ---------------------------------------------------
    log.info("[8/8] Resumen")
    mostrar_resumen(stats, (datetime.now() - inicio).total_seconds())


if __name__ == '__main__':
    main()
