"""
BACKUP AUTOMATICO DE FUENTES DE DATOS DE TABLEAU A GITHUB
==========================================================

Flujo completo:
    Oracle (MDM_TABLEAU_GIT_CONTENT_DATASOURCES: registro, sin versionado)
      -> EjecutarProcesoCompletoDatasources.bat encadena en serie:
           1. ActualizarGitContentDatasources.bat  (registra fuentes de
              datos nuevas o con version distinta a la ya subida)
           2. ConexionOracleDatasources.bat        (genera
              lista_datasources.csv, ya filtrado: solo lo pendiente)
      -> este script descarga cada fuente de datos pendiente de Tableau
      -> sube todo a GitHub por lotes, con Git LFS para los grandes

A diferencia de descargar_workbooks.py, aqui NO se conservan versiones
historicas: una fuente de datos = un archivo, con nombre FIJO, que se
sobrescribe cada vez que hay una version nueva. Si una fuente de datos se
borra de Tableau, su archivo se queda intacto en GitHub (no se retira).

Estructura en disco y en GitHub:

    Tableau Datasources/
    |-- Development/.../NombreFuente          <- si la carpeta de proyecto
    |                                            tiene subcarpetas, va suelta
    `-- Production/.../Carpeta/DataSources/NombreFuente
                                               <- si la carpeta de proyecto
                                                  es "hoja" (sin subcarpetas),
                                                  va dentro de "DataSources"
    (la ruta exacta ya viene calculada por Oracle en RUTA_DESTINO)

Uso:
    python descargar_datasources.py                  # proceso completo
    python descargar_datasources.py --sin-github     # solo descargar
    python descargar_datasources.py --config x.json  # otra configuracion

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
GITHUB_DOMINIO = "cantabrialabs.ghe.com"
GITHUB_API = "https://api.cantabrialabs.ghe.com"
GITHUB_API_VERSION = "2026-03-10"

# Fuentes de datos que se descargan antes de hacer cada commit + push.
# Mas bajo que en workbooks (8): las fuentes de datos con extracto pueden
# pesar bastante mas, asi que se prefieren lotes mas pequenos.
TAMANO_LOTE = 4

CONTENIDO_GITATTRIBUTES_LINEAS = [
    "*.tdsx filter=lfs diff=lfs merge=lfs -text\n",
    "*.hyper filter=lfs diff=lfs merge=lfs -text\n",
]


# ============================================================================
# LOG
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('tableau_sync_datasources.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def separador(titulo=""):
    """
    Linea divisoria en el log, para separar visualmente las fases.

    Args:
        titulo: texto a mostrar entre las dos lineas divisorias. Si se deja
            vacio, solo se imprime una linea suelta.

    Returns:
        No devuelve nada (imprime directamente en el log).
    """
    log.info("=" * 60)
    if titulo:
        log.info(titulo)
        log.info("=" * 60)


def tamano_legible(ruta):
    """
    Devuelve el tamano de un fichero como texto ('12.4 MB').

    Args:
        ruta: ruta (str o Path) del fichero a medir.

    Returns:
        Texto con el tamano en MB, o '?' si el fichero no se puede leer.
    """
    try:
        mb = Path(ruta).stat().st_size / (1024 * 1024)
        return f"{mb:.1f} MB"
    except Exception:
        return "?"


def duracion_legible(segundos):
    """
    Convierte segundos en '12m 5s'.

    Args:
        segundos: duracion en segundos (int o float).

    Returns:
        Texto con la duracion en minutos y segundos, o solo segundos si
        dura menos de un minuto.
    """
    minutos, seg = divmod(int(segundos), 60)
    return f"{minutos}m {seg}s" if minutos else f"{seg}s"


# ============================================================================
# CONFIGURACION
# ============================================================================

CLAVES_ORACLE = ['sqlplus_comando_datasources', 'sqlplus_marcar_comando', 'archivo_lista_datasources']
CLAVES_TABLEAU = ['tableau_server', 'tableau_token_name', 'tableau_token', 'tableau_site']
CLAVES_GITHUB = ['github_client_id', 'github_installation_id',
                 'github_private_key_path', 'github_owner', 'github_repo_name']
CLAVES_OPCIONALES = {
    'directorio_descarga_datasources': './tableau_datasources',
    'timeout_sqlplus': 15,
    'github_enabled': True,
}


def cargar_config(fichero="config.json"):
    """
    Carga config.json y comprueba que estan todas las claves necesarias
    para el backup de fuentes de datos.

    Args:
        fichero: ruta del fichero de configuracion a cargar.

    Returns:
        Diccionario con la configuracion ya validada. Si falta el fichero,
        tiene un error de sintaxis, o le falta alguna clave obligatoria,
        el programa termina aqui (sys.exit) en vez de devolver nada.
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
# ORACLE
# ============================================================================

def ejecutar_sqlplus(comando, timeout):
    """
    Lanza EjecutarProcesoCompletoDatasources.bat, que registra las fuentes
    de datos pendientes en Oracle y genera lista_datasources.csv.

    Args:
        comando: ruta del .bat a ejecutar (config['sqlplus_comando_datasources']).
        timeout: segundos maximos de espera antes de darlo por colgado.

    Returns:
        True si el comando termino con codigo de salida 0. False si tardo
        mas del timeout, no se pudo lanzar, o devolvio un codigo de error.
    """
    try:
        resultado = subprocess.run(
            comando, shell=True, capture_output=True, text=True, timeout=timeout,
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
        log.error("Abre lista_datasources.csv: el mensaje de Oracle esta dentro")
        return False

    return True


# ============================================================================
# LEER LA LISTA
# ============================================================================

def leer_lista_datasources(ruta, separador_csv=','):
    """
    Convierte lista_datasources.csv en una tabla de trabajo (DataFrame).

    Un fichero vacio (0 bytes, o con cabecera pero sin filas) NO es un
    error: significa que ninguna fuente de datos tiene una version nueva
    desde la ultima ejecucion -- el caso mas habitual en el dia a dia,
    igual que en descargar_workbooks.py. Solo se considera error si el
    fichero directamente no existe, o si su contenido no se puede
    interpretar como CSV.

    Args:
        ruta: ruta del CSV a leer (lista_datasources.csv).
        separador_csv: caracter separador de columnas del CSV.

    Returns:
        DataFrame de pandas con las fuentes de datos pendientes (sin
        duplicados, con DATASOURCE_LUID y DATASOURCE no vacios). Puede
        estar vacio (0 filas) si no hay nada pendiente hoy. None solo si
        el fichero no existe o su formato no es valido.
    """
    ruta = Path(ruta)
    columnas_esperadas = ["DATASOURCE_LUID", "DATASOURCE", "RUTA_DESTINO", "VERSION_ACTUAL"]

    if not ruta.is_file():
        log.error("No se genero el fichero %s", ruta)
        return None
    if ruta.stat().st_size == 0:
        log.info("      %s esta vacio: no hay fuentes de datos pendientes hoy", ruta.name)
        return pd.DataFrame(columns=columnas_esperadas)

    try:
        df = pd.read_csv(
            ruta, sep=separador_csv, dtype=str, encoding='utf-8',
            quotechar='"', keep_default_na=False, skipinitialspace=True,
        )
    except pd.errors.EmptyDataError:
        # Fichero con contenido (por ejemplo, alguna linea en blanco) pero
        # sin datos interpretables como tabla -- mismo caso que "vacio".
        log.info("      %s no tiene filas: no hay fuentes de datos pendientes hoy", ruta.name)
        return pd.DataFrame(columns=columnas_esperadas)
    except Exception as e:
        log.error("El fichero no tiene formato CSV valido: %s", e)
        return None

    df.columns = [str(c).strip().upper() for c in df.columns]
    for columna in df.columns:
        df[columna] = df[columna].astype(str).str.strip()

    faltan = {"DATASOURCE_LUID", "DATASOURCE", "RUTA_DESTINO", "VERSION_ACTUAL"} - set(df.columns)
    if faltan:
        log.error("La vista de Oracle no devuelve las columnas: %s", ", ".join(faltan))
        log.error("Columnas recibidas: %s", ", ".join(df.columns))
        return None

    df = df[(df["DATASOURCE_LUID"] != "") & (df["DATASOURCE"] != "")]
    df = df.drop_duplicates(subset=["DATASOURCE_LUID"], keep="last").reset_index(drop=True)

    return df


# ============================================================================
# AUTENTICACION CON GITHUB APP
# ============================================================================
# Identicas a descargar_workbooks.py -- se repiten aqui para que este
# script sea independiente y no dependa de importar el otro fichero.

def obtener_token_github(config):
    """
    Consigue un token de instalacion valido durante una hora.

    Args:
        config: diccionario de configuracion, con 'github_client_id',
            'github_private_key_path' y 'github_installation_id'.

    Returns:
        Texto con el token de instalacion, o None si GitHub rechaza la
        autenticacion.
    """
    ahora = int(time.time())
    payload = {
        'iat': ahora - 60,
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
        log.error("Ejecuta 'python diagnostico_github_app.py' para localizar la causa")
        return None

    return respuesta.json()['token']


def cabecera_git(token):
    """
    Prepara la autenticacion de git para pasarsela con 'git -c ...'.

    Args:
        token: token de instalacion de la GitHub App (de obtener_token_github).

    Returns:
        Texto con el parametro completo listo para 'git -c <esto>'.
    """
    credencial = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"http.https://{GITHUB_DOMINIO}.extraHeader=Authorization: Basic {credencial}"


def url_repo(config):
    """
    URL del repositorio, sin credenciales.

    Args:
        config: diccionario de configuracion, con 'github_owner' y
            'github_repo_name'.

    Returns:
        Texto con la URL completa del repositorio (formato .git).
    """
    return f"https://{GITHUB_DOMINIO}/{config['github_owner']}/{config['github_repo_name']}.git"


# ============================================================================
# EJECUCION DE COMANDOS GIT
# ============================================================================

def ocultar_secretos(texto, secretos):
    """
    Sustituye cualquier token por *** antes de imprimir o guardar nada.

    Args:
        texto: texto sobre el que buscar y censurar los secretos.
        secretos: lista de textos a ocultar. Puede ser None.

    Returns:
        El mismo texto, con cada aparicion de cada secreto sustituida por '***'.
    """
    for secreto in secretos or []:
        if secreto:
            texto = texto.replace(secreto, "***")
    return texto


def git(comando, secretos=None, mostrar=True):
    """
    Ejecuta un comando git mostrando su salida en tiempo real.

    Args:
        comando: lista con el comando y sus argumentos (formato subprocess).
        secretos: lista de textos a censurar en la salida. Por defecto no
            censura nada.
        mostrar: si es False, no imprime la salida linea a linea en el log.

    Returns:
        Tupla (codigo_de_salida, salida_completa_ya_censurada).
    """
    proceso = subprocess.Popen(
        comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
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
# SINCRONIZAR CON GITHUB
# ============================================================================

def sincronizar_con_remoto(directorio, config, token):
    """
    Deja la carpeta local exactamente igual que el repositorio remoto.

    Se hace ANTES de descargar nada, para partir de un estado identico y no
    tener que fusionar nada.

    Args:
        directorio: ruta de la carpeta de descargas (Tableau Datasources).
        config: diccionario de configuracion, usado por url_repo().
        token: token de instalacion de la GitHub App.

    Returns:
        True si el fetch y el reset se completaron correctamente. False si
        alguno de los dos comandos git fallo.
    """
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)

    git(['git', 'merge', '--abort'], [token], mostrar=False)

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
# CARPETA DE DESCARGAS Y GIT LFS
# ============================================================================

def asegurar_gitattributes(directorio):
    """
    Garantiza que .gitattributes existe y tiene el contenido correcto para
    fuentes de datos (.tdsx y .hyper por LFS), escribiendolo siempre en
    cada ejecucion.

    Args:
        directorio: ruta de la carpeta de descargas (Tableau Datasources),
            donde debe vivir el .gitattributes.

    Returns:
        No devuelve nada.
    """
    ruta = Path(directorio) / ".gitattributes"
    ruta.write_text("".join(CONTENIDO_GITATTRIBUTES_LINEAS), encoding='utf-8', newline='\n')


def preparar_directorio(directorio):
    """
    Crea la carpeta de descargas si no existe. NO la vacia: los archivos ya
    descargados de fuentes de datos que no hayan cambiado se conservan tal
    cual, para no tener que volver a pedirlos a Tableau sin necesidad.

    Args:
        directorio: ruta de la carpeta de descargas a crear si falta.

    Returns:
        No devuelve nada.
    """
    Path(directorio).mkdir(parents=True, exist_ok=True)


# ============================================================================
# TABLEAU
# ============================================================================

def conectar_tableau(config):
    """
    Inicia sesion en Tableau Cloud con el token de acceso personal (PAT).

    Args:
        config: diccionario de configuracion, con 'tableau_token_name',
            'tableau_token', 'tableau_site' y 'tableau_server'.

    Returns:
        Objeto Server de tableauserverclient, ya autenticado. Si la
        conexion falla, el programa termina aqui (sys.exit).
    """
    try:
        auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site'],
        )
        servidor = TSC.Server(config['tableau_server'])
        servidor.auth.sign_in(auth)
        return servidor
    except Exception as e:
        log.error("No se pudo conectar con Tableau: %s", e)
        log.error("Si el error es 401, el PAT ha caducado: renuevalo en Tableau Cloud")
        sys.exit(1)


def descargar_datasource(servidor, luid, destino):
    """
    Descarga una fuente de datos y la deja en su ruta final, con nombre fijo.

    Tableau devuelve dos formatos, igual que con los workbooks:
      .tdsx -> empaquetada, con el extracto dentro
      .tds  -> sin empaquetar, solo la definicion (conexion en vivo)
    Se aceptan ambos y se conserva la extension real. No se fuerza ningun
    parametro de extracto: se descarga tal como Tableau la entregue.

    Args:
        servidor: objeto Server de tableauserverclient, ya autenticado.
        luid: identificador de la fuente de datos en Tableau.
        destino: ruta completa donde debe guardarse, con nombre fijo (sin
            sufijo de version).

    Returns:
        Ruta (Path) del fichero descargado, con su extension real. None si
        la descarga falla o Tableau no devuelve ningun fichero.
    """
    try:
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)

        # Se retiran ambas extensiones antes de descargar: como el nombre
        # es fijo, si la version anterior tenia la OTRA extension, hay que
        # quitarla para no dejar dos archivos con nombres distintos
        # representando la misma fuente de datos.
        destino.with_suffix('.tdsx').unlink(missing_ok=True)
        destino.with_suffix('.tds').unlink(missing_ok=True)

        temporal = str(destino.parent / destino.stem)
        servidor.datasources.download(luid, filepath=temporal)

        carpeta = Path(temporal)
        if carpeta.is_dir():
            encontrados = list(carpeta.glob('*.tdsx')) + list(carpeta.glob('*.tds'))
            if not encontrados:
                log.error("        Tableau no devolvio ningun fichero")
                return None

            final = destino.with_suffix(encontrados[0].suffix)
            shutil.move(str(encontrados[0]), str(final))
            shutil.rmtree(carpeta, ignore_errors=True)
            return final

        for extension in ('.tdsx', '.tds'):
            candidato = destino.with_suffix(extension)
            if candidato.exists():
                return candidato

        log.error("        No se encontro el fichero descargado")
        return None

    except Exception as e:
        log.error("        Error al descargar: %s", e)
        return None


# ============================================================================
# SUBIR A GITHUB
# ============================================================================

def marcar_subido_en_oracle(config, pares_luid_version):
    """
    Pone FLG_SUBIDO_GITHUB=1 en Oracle para las fuentes de datos de ESTE
    lote que se acaban de subir a GitHub con exito.

    Args:
        config: diccionario de configuracion, con 'sqlplus_marcar_comando'.
        pares_luid_version: lista de tuplas (luid, version) de las fuentes
            de datos de este lote que ya estan subidas.

    Returns:
        True si Oracle confirmo el UPDATE. False si fallo (en ese caso, se
        reintentaran en la siguiente ejecucion). True tambien si la lista
        de pares esta vacia.
    """
    if not pares_luid_version:
        return True

    valores = ",\n        ".join(
        f"('{luid.replace(chr(39), chr(39)*2)}', {version})"
        for luid, version in pares_luid_version
    )

    sql = f"""WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
UPDATE MDM_TABLEAU_GIT_CONTENT_DATASOURCES
SET FLG_SUBIDO_GITHUB = 1
WHERE (DATASOURCE_LUID, VERSION) IN (
        {valores}
      );
COMMIT;
EXIT;
"""

    ruta_temporal = Path("marcar_subido_datasources_temp.sql")
    ruta_temporal.write_text(sql, encoding='utf-8')

    try:
        resultado = subprocess.run(
            [config['sqlplus_marcar_comando'], str(ruta_temporal.resolve())],
            shell=True, capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        log.error("        No se pudo marcar en Oracle: %s", e)
        return False
    finally:
        ruta_temporal.unlink(missing_ok=True)

    if resultado.returncode != 0:
        log.error("        Oracle no confirmo el marcado de subida (codigo %d)", resultado.returncode)
        log.error("        Estas fuentes de datos se reintentaran en la proxima ejecucion")
        return False

    return True


def subir_a_github(directorio, config, token, mensaje):
    """
    Hace commit y push de lo que haya en la carpeta de descargas.

    Args:
        directorio: ruta de la carpeta de descargas (Tableau Datasources).
        config: diccionario de configuracion, usado por url_repo().
        token: token de instalacion de la GitHub App.
        mensaje: texto del commit.

    Returns:
        True si el commit y el push se completaron (o no habia cambios
        pendientes). False si algun paso fallo de verdad.
    """
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)

    # Se limpia cualquier conflicto sin resolver ANTES de tocar nada: un
    # commit no puede completarse con archivos sin fusionar, y hacerlo
    # despues dejaria bloqueados tambien todos los lotes siguientes.
    git(['git', 'merge', '--abort'], [token], mostrar=False)

    for ajuste in [('http.postBuffer', '2147483648'),
                   ('http.lowSpeedLimit', '0'),
                   ('http.lowSpeedTime', '999999')]:
        git(['git', 'config', *ajuste], mostrar=False)

    codigo, salida = git(['git', 'add', '-A', '.'], [token], mostrar=False)
    if codigo != 0:
        log.error("        No se pudieron preparar los ficheros")
        log.error("        %s", salida)
        return False

    codigo, salida = git(['git', 'commit', '-m', mensaje], [token], mostrar=False)
    sin_cambios = "nothing to commit" in salida.lower() or "nothing added to commit" in salida.lower()
    if sin_cambios:
        log.info("        Sin cambios que subir")
        return True
    if codigo != 0:
        log.error("        No se pudo crear el commit")
        log.error("        %s", salida)
        return False

    codigo, salida = git(
        ['git', '-c', cabecera, 'pull', '--no-edit', '-X', 'ours', url, 'main'],
        [token], mostrar=False
    )
    if codigo != 0:
        log.error("        No se pudo sincronizar antes de subir")
        log.error("        %s", salida)
        return False

    codigo, salida = git(['git', '-c', cabecera, 'push', url, 'main'], [token])

    if codigo != 0 and ("fetch first" in salida.lower() or "non-fast-forward" in salida.lower()):
        log.info("        El repositorio avanzo mientras subiamos, reintentando")
        git(['git', '-c', cabecera, 'pull', '--no-edit', '-X', 'ours', url, 'main'],
            [token], mostrar=False)
        codigo, salida = git(['git', '-c', cabecera, 'push', url, 'main'], [token])

    if codigo != 0:
        log.error("        Fallo la subida a GitHub")
        if "exceeds GitHub's file size limit" in salida:
            log.error("        Hay un fichero de mas de 100 MB que no esta pasando por Git LFS")
            log.error("        Comprueba que existe 'Tableau Datasources\\.gitattributes'")
        return False

    return True


def actualizar_referencia_remota(directorio, config, token):
    """
    Pone al dia la referencia local de origin/main.

    Args:
        directorio: ruta de la carpeta de descargas (Tableau Datasources).
        config: diccionario de configuracion, usado por url_repo().
        token: token de instalacion de la GitHub App.

    Returns:
        No devuelve nada.
    """
    os.chdir(directorio)
    cabecera = cabecera_git(token)
    url = url_repo(config)
    git(['git', '-c', cabecera, 'fetch', url, 'main'], [token], mostrar=False)
    git(['git', 'update-ref', 'refs/remotes/origin/main', 'FETCH_HEAD'], [token], mostrar=False)


# ============================================================================
# BUCLE PRINCIPAL DE DESCARGA
# ============================================================================

# Caracteres que Windows prohibe en nombres de archivo. Algunas fuentes de
# datos de Tableau los llevan en el propio nombre (ej. "Nombre | Project :
# Carpeta"), lo que hacia fallar la creacion del fichero con
# "[WinError 123] The filename... syntax is incorrect".
CARACTERES_INVALIDOS_WINDOWS = str.maketrans('', '', '<>:"/\\|?*')


def sanear_nombre_archivo(nombre):
    """
    Quita los caracteres que Windows no permite en nombres de archivo.

    Args:
        nombre: nombre original de la fuente de datos, tal como lo
            devuelve Tableau.

    Returns:
        El mismo nombre, sin los caracteres invalidos y sin espacios
        sobrantes al principio o al final.
    """
    return nombre.translate(CARACTERES_INVALIDOS_WINDOWS).strip()


def descargar_y_subir(servidor, df, directorio, config, subir, token):
    """
    Descarga todas las fuentes de datos y, cada TAMANO_LOTE, las sube a GitHub.

    Args:
        servidor: objeto Server de tableauserverclient, ya autenticado.
        df: DataFrame de leer_lista_datasources(), con las fuentes de datos
            a procesar.
        directorio: ruta de la carpeta de descargas (Tableau Datasources).
        config: diccionario de configuracion.
        subir: si es False, descarga pero no sube nada a GitHub.
        token: token de instalacion de la GitHub App, obtenido una vez y
            reutilizado en todos los lotes de la ejecucion.

    Returns:
        Diccionario con las estadisticas de la ejecucion: 'total', 'ok',
        'error', 'lotes_ok' y 'lotes_error'.
    """
    stats = {'total': len(df), 'ok': 0, 'error': 0, 'lotes_ok': 0, 'lotes_error': 0}
    lote_pendiente_marcar = []

    for numero, (_, fila) in enumerate(df.iterrows(), start=1):
        luid = fila['DATASOURCE_LUID']
        nombre = sanear_nombre_archivo(fila['DATASOURCE'])
        ruta_destino = fila['RUTA_DESTINO']
        version = fila['VERSION_ACTUAL']

        log.info("  [%d/%d] %s (v%s)", numero, stats['total'], nombre, version)
        log.info("        Destino: %s", ruta_destino)

        destino = Path(directorio) / ruta_destino / nombre
        fichero = descargar_datasource(servidor, luid, destino)

        if fichero:
            stats['ok'] += 1
            log.info("        Descargado (%s)", tamano_legible(fichero))
            lote_pendiente_marcar.append((luid, version))
        else:
            stats['error'] += 1
            log.info("        LUID: %s", luid)

        es_ultimo = (numero == stats['total'])
        if subir and (numero % TAMANO_LOTE == 0 or es_ultimo):
            log.info("  --- Subiendo lote (%d/%d fuentes de datos procesadas) ---",
                     numero, stats['total'])
            mensaje = f"Tableau Datasources Backup - lote hasta {numero}/{stats['total']}"
            if subir_a_github(directorio, config, token, mensaje):
                stats['lotes_ok'] += 1
                log.info("        Lote subido")
                if lote_pendiente_marcar:
                    if marcar_subido_en_oracle(config, lote_pendiente_marcar):
                        log.info("        %d fuente(s) de datos marcadas como subidas en Oracle",
                                  len(lote_pendiente_marcar))
                    else:
                        log.warning("        No se pudo confirmar en Oracle (se reintentara)")
            else:
                stats['lotes_error'] += 1
                log.warning("        Lote fallido: sus ficheros iran en el siguiente")
            lote_pendiente_marcar = []

    return stats


# ============================================================================
# RESUMEN FINAL
# ============================================================================

def mostrar_resumen(stats, segundos):
    """
    Bloque final del log. Es lo unico que hay que mirar cada manana.

    Args:
        stats: diccionario de estadisticas (de descargar_y_subir).
        segundos: duracion total de la ejecucion, en segundos.

    Returns:
        No devuelve nada.
    """
    separador("RESUMEN DE LA EJECUCION")
    log.info("Fuentes de datos con version nueva ... %d", stats['total'])
    log.info("Descargadas correctamente ............ %d", stats['ok'])
    log.info("Con error ............................. %d", stats['error'])
    log.info("Lotes subidos a GitHub ................ %d", stats['lotes_ok'])
    log.info("Lotes fallidos ......................... %d", stats['lotes_error'])
    log.info("Tiempo total ........................... %s", duracion_legible(segundos))
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
    """
    Orquesta las 7 fases del proceso completo, de principio a fin.

    Lee los argumentos de linea de comandos (--config, --sin-github,
    --separador) y ejecuta en orden: consultar Oracle, leer la lista,
    sincronizar con GitHub, preparar la carpeta, conectar con Tableau,
    descargar y subir, y mostrar el resumen final.

    Args:
        No recibe argumentos de Python (los toma de sys.argv via argparse).

    Returns:
        No devuelve nada. Termina el programa (sys.exit) si algun paso
        obligatorio falla.
    """
    parser = argparse.ArgumentParser(description="Backup de fuentes de datos de Tableau a GitHub")
    parser.add_argument('--config', default='config.json', help="fichero de configuracion")
    parser.add_argument('--sin-github', action='store_true', help="descargar sin subir a GitHub")
    parser.add_argument('--separador', default=',', help="separador del CSV")
    args = parser.parse_args()

    inicio = datetime.now()
    separador("BACKUP DATASOURCES TABLEAU -> GITHUB")

    config = cargar_config(args.config)
    directorio = config['directorio_descarga_datasources']
    lista = Path(config['archivo_lista_datasources'])
    subir = config['github_enabled'] and not args.sin_github

    # --- Paso 1: borrar la lista anterior -----------------------------------
    log.info("[1/7] Borrando la lista anterior")
    if lista.exists():
        try:
            lista.unlink()
        except OSError as e:
            log.warning("      No se pudo borrar %s (%s), se intentara sobrescribir", lista.name, e)
    log.info("      Listo")

    # --- Paso 2: consultar Oracle --------------------------------------------
    log.info("[2/7] Consultando Oracle (registro + lista de descarga)")
    if not ejecutar_sqlplus(config['sqlplus_comando_datasources'], config['timeout_sqlplus']):
        log.error("Proceso abortado: sin lista de fuentes de datos no hay nada que descargar")
        sys.exit(1)
    log.info("      Lista generada")

    # --- Paso 3: leer la lista ------------------------------------------------
    log.info("[3/7] Leyendo la lista")
    df = leer_lista_datasources(lista, args.separador)
    if df is None:
        log.error("Proceso abortado: la lista de descarga no es valida")
        sys.exit(1)
    log.info("      %d fuentes de datos con version nueva que descargar", len(df))

    # --- Paso 4: sincronizar con GitHub y obtener el token -------------------
    token = None
    if subir:
        log.info("[4/7] Sincronizando con GitHub")
        token = obtener_token_github(config)
        if token is None or not sincronizar_con_remoto(directorio, config, token):
            log.error("Proceso abortado: sin sincronizar antes, la subida daria conflictos")
            sys.exit(1)
        log.info("      Carpeta local alineada con el repositorio")
    else:
        log.info("[4/7] Sincronizacion omitida (modo sin GitHub)")

    # --- Paso 5: preparar la carpeta ------------------------------------------
    log.info("[5/7] Preparando la carpeta de descargas")
    preparar_directorio(directorio)
    if subir:
        asegurar_gitattributes(directorio)
    log.info("      Lista para recibir la descarga")

    # --- Paso 6: conectar con Tableau y descargar/subir -----------------------
    log.info("[6/7] Conectando con Tableau")
    servidor = conectar_tableau(config)
    log.info("      Conectado")

    if df.empty:
        log.info("      Sin fuentes de datos nuevas que descargar")
        stats = {'total': 0, 'ok': 0, 'error': 0, 'lotes_ok': 0, 'lotes_error': 0}
    else:
        if subir:
            log.info("      Descargando y subiendo en lotes de %d", TAMANO_LOTE)
        else:
            log.info("      Descargando (sin subir a GitHub)")
        stats = descargar_y_subir(servidor, df, directorio, config, subir, token)

    try:
        servidor.auth.sign_out()
    except Exception:
        pass

    if subir and token:
        actualizar_referencia_remota(directorio, config, token)

    # --- Paso 7: resumen ---------------------------------------------------------
    log.info("[7/7] Resumen")
    mostrar_resumen(stats, (datetime.now() - inicio).total_seconds())


if __name__ == '__main__':
    main()
