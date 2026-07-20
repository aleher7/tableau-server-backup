"""
Script para descargar workbooks de Tableau usando:
1. SQL PLUS (vía ConexionOracle.bat) -> genera lista_workbooks.csv
2. Python -> parsea el CSV, descarga los workbooks desde Tableau Server
   y sube el resultado a GitHub.

CÓMO ENCAJAN LAS PIEZAS (resumen del flujo completo):
  ConexionOracle.bat
      -> hace login en Oracle y ejecuta Descarga.sql
  Descarga.sql
      -> usa "SET MARKUP CSV ON DELIMITER ',' QUOTE ON" + SPOOL
      -> genera C:\\tabcmd\\TableauGitHub\\lista_workbooks.csv (CSV real, con comillas)
  Este script Python
      -> ejecuta el .bat (PASO 2)
      -> lee el CSV generado (PASO 3)
      -> descarga cada workbook desde Tableau Server (PASO 6)
      -> hace commit y push del directorio de descarga a GitHub (PASO 7)

NOTA SOBRE EL FORMATO CSV:
  Como Descarga.sql ahora usa "SET MARKUP CSV ON ... QUOTE ON", cada campo de
  texto sale entre comillas dobles ("Admin Insights Starter"), y las comillas
  internas se escapan duplicándolas (""). Por eso en pandas usamos
  quotechar='"' — si no, un valor con coma dentro se rompería en columnas de más.
"""

import os
import sys
import json
import logging
import subprocess
import argparse
import shutil
import time
from pathlib import Path
from datetime import datetime
import pandas as pd
import jwt as pyjwt      # PyJWT: firma el token de autenticación de la GitHub App
import requests          # Para llamar a la API REST de GitHub y pedir el token de instalación
import re  # NOTA: actualmente no se usa 're' en ningún sitio del script.
           # Se puede eliminar este import sin que nada se rompa.

try:
    import tableauserverclient as TSC
except ImportError:
    print("ERROR: tableauserverclient no está instalado")
    print("Instala con: pip install tableauserverclient")
    sys.exit(1)

# ============================================================================
# CONFIGURACIÓN DE LOGGING
# ============================================================================
# Todo lo que se registre con logger.info/warning/error se escribe A LA VEZ en:
#   - el archivo tableau_sync.log (queda guardado, útil para revisar después)
#   - la consola (lo que ves mientras corre el script)
# Esto es clave para diagnosticar por qué un workbook concreto "no se encuentra":
# revisando tableau_sync.log puedes ver el LUID exacto que se intentó descargar.

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tableau_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# FUNCIONES DE VALIDACIÓN
# ============================================================================

def validar_config(config):
    """
    Comprueba que config.json trae todas las claves necesarias ANTES de
    empezar a hacer nada (SQL PLUS, Tableau, GitHub...).

    Objetivo: si falta algo, el script debe fallar YA, con un mensaje claro
    de qué clave falta — en vez de fallar 2 minutos después con un
    KeyError críptico en mitad de la descarga.
    """

    logger.info("="*60)
    logger.info("VALIDANDO CONFIGURACIÓN")
    logger.info("="*60)

    # Claves SIN las cuales no se puede ni ejecutar SQL PLUS ni encontrar el CSV
    claves_sqlplus_requeridas = [
        'sqlplus_comando',
        'archivo_lista_workbooks'
    ]

    # Claves SIN las cuales no se puede autenticar en Tableau Server
    claves_tableau_requeridas = [
        'tableau_server',
        'tableau_token_name',
        'tableau_token',
        'tableau_site'
    ]

    # Claves opcionales con valores por defecto.
    # (github_enabled se valida aparte más abajo porque, si es true,
    #  deja de ser "opcional": exige las 5 claves de GitHub App)
    claves_opcionales = {
        'directorio_descarga': './tableau_workbooks',
        'timeout_sqlplus': 15,
        'github_enabled': True
    }

    # Claves SIN las cuales no se puede autenticar la GitHub App
    # (solo se exigen si github_enabled es true; si está en false, se
    #  ignoran por completo porque no se va a intentar subir nada)
    claves_github_requeridas = [
        'github_app_id',
        'github_installation_id',
        'github_private_key_path',
        'github_owner',
        'github_repo_name'
    ]

    # --- Validar claves de SQL PLUS ---
    logger.info("[VERIFICANDO] Claves SQL PLUS...")
    for clave in claves_sqlplus_requeridas:
        if clave not in config:
            logger.error("[ERROR] Clave REQUERIDA no encontrada: %s", clave)
            logger.error("[ERROR] Claves disponibles: %s", ", ".join(config.keys()))
            logger.error("")
            logger.error("Por favor, agrega estas claves a tu config.json:")
            for c in claves_sqlplus_requeridas:
                if c not in config:
                    logger.error('  "%s": "...",', c)
            sys.exit(1)  # Corta la ejecución: sin esto no tiene sentido seguir
        else:
            logger.info(" %s encontrada", clave)

    # --- Validar claves de Tableau ---
    logger.info("[VERIFICANDO] Claves Tableau...")
    for clave in claves_tableau_requeridas:
        if clave not in config:
            logger.error("[ERROR] Clave REQUERIDA no encontrada: %s", clave)
            logger.error("[ERROR] Claves disponibles: %s", ", ".join(config.keys()))
            logger.error("")
            logger.error("Por favor, agrega estas claves a tu config.json:")
            for c in claves_tableau_requeridas:
                if c not in config:
                    logger.error('  "%s": "...",', c)
            sys.exit(1)
        else:
            logger.info(" %s encontrada", clave)

    # --- Rellenar claves opcionales con su valor por defecto si faltan ---
    logger.info("[VERIFICANDO] Claves opcionales...")
    for clave, valor_default in claves_opcionales.items():
        if clave not in config:
            logger.info("  %s no encontrada, usando default: %s", clave, valor_default)
            config[clave] = valor_default
        else:
            logger.info(" %s encontrada", clave)

    # --- Validar claves de GitHub App (solo si github_enabled == True) ---
    # Esto se comprueba DESPUÉS de rellenar los defaults, porque
    # 'github_enabled' podría no venir en config.json y haberse rellenado
    # con su valor por defecto (True) justo en el bucle de arriba.
    if config.get('github_enabled'):
        logger.info("[VERIFICANDO] Claves GitHub App (github_enabled=true)...")
        for clave in claves_github_requeridas:
            if clave not in config:
                logger.error("[ERROR] Clave REQUERIDA no encontrada: %s", clave)
                logger.error("")
                logger.error("Por favor, agrega estas claves a tu config.json")
                logger.error("(o pon \"github_enabled\": false si no quieres subir a GitHub):")
                for c in claves_github_requeridas:
                    if c not in config:
                        logger.error('  "%s": "...",', c)
                sys.exit(1)
            else:
                logger.info(" %s encontrada", clave)

    logger.info("[OK] Configuración validada correctamente")
    logger.info("")
    return config


# ============================================================================
# FUNCIONES PRINCIPALES
# ============================================================================

def cargar_config(config_file="config.json"):
    """
    Abre config.json, lo convierte a diccionario Python y lo valida.

    Si el archivo no existe o el JSON está mal escrito (una coma de más,
    comillas sin cerrar, etc.), se avisa con un mensaje claro en vez de
    dejar que Python lance un traceback confuso.
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logger.info("[OK] Archivo %s cargado", config_file)

        # Aquí es donde se comprueba que no falte ninguna clave obligatoria
        config = validar_config(config)

        return config
    except FileNotFoundError:
        logger.error("[ERROR] Archivo %s no encontrado", config_file)
        logger.error("")
        logger.error("Debes crear un archivo config.json con este contenido:")
        logger.error("")
        logger.error("""{
  "tableau_server": "https://tu_tableau_server.com",
  "tableau_token_name": "tu_nombre_del_token",
  "tableau_token": "tu_token_aqui",
  "tableau_site": "default",

  "directorio_descarga": "./tableau_workbooks",
  "github_enabled": true,

  "sqlplus_comando": "cd C:\\\\oracle\\\\instantclient_23_0 && sqlplus -S usuario/password@servidor:1521/SID @C:\\\\tabcmd\\\\TableauGitHub\\\\Descarga.sql",
  "archivo_lista_workbooks": "C:\\\\tabcmd\\\\TableauGitHub\\\\lista_workbooks.txt",
  "timeout_sqlplus": 15
}""")
        sys.exit(1)
    except json.JSONDecodeError:
        # Esto salta si el JSON tiene un error de sintaxis (falta una coma,
        # comillas sin cerrar, etc.). No dice la línea exacta a propósito
        # para mantenerlo simple, pero conviene validar el JSON en un editor.
        logger.error("[ERROR] Error al parsear %s (JSON inválido)", config_file)
        sys.exit(1)


def ejecutar_sqlplus(comando_sqlplus, timeout=15):
    """
    Lanza el .bat (ConexionOracle.bat) que a su vez ejecuta SQL PLUS.

    subprocess.run(..., shell=True) es equivalente a escribir el comando
    a mano en la consola de Windows (cmd.exe). Esto es necesario aquí
    porque comando_sqlplus puede ser una ruta a un .bat, o (en versiones
    anteriores) un comando con "&&" encadenado, y shell=True es lo que
    permite que Windows lo interprete igual que en una consola normal.
    """
    try:
        logger.info("[SQLPLUS] Ejecutando comando...")
        logger.info("[SQLPLUS] Timeout: %d segundos", timeout)

        resultado = subprocess.run(
            comando_sqlplus,
            shell=True,           # Permite ejecutar .bat / comandos con && igual que en cmd.exe
            capture_output=True,  # Guarda todo lo que el .bat escribe (stdout/stderr) en 'resultado'
            text=True,            # Decodifica esa salida como texto en vez de bytes
            timeout=timeout       # Si tarda más de 'timeout' segundos, lo cancela solo
        )

        # returncode == 0 significa "todo OK" en la convención de Windows/Linux.
        # Cualquier otro valor indica que el .bat o SQL PLUS devolvió un error.
        if resultado.returncode != 0:
            logger.error("[ERROR] SQL PLUS error (código %d)", resultado.returncode)
            logger.error("[ERROR] stderr: %s", resultado.stderr[:500])  # Solo los primeros 500 caracteres, para no saturar el log
            return False

        logger.info("[OK] Comando ejecutado correctamente")
        return True

    except subprocess.TimeoutExpired:
        # Se dispara si SQL PLUS/Oracle tarda más de 'timeout' segundos en responder
        # (servidor lento, credenciales que se quedan "colgadas" pidiendo password, etc.)
        logger.error("[ERROR] SQL PLUS timeout (>%d segundos)", timeout)
        logger.error("[ERROR] El comando tardó demasiado. Aumenta timeout_sqlplus en config.json")
        return False
    except Exception as e:
        # Red de seguridad genérica por si pasa algo no previsto
        # (por ejemplo que el .bat no exista en esa ruta)
        logger.error("[ERROR] Error ejecutando SQL PLUS: %s", e)
        return False


def parsear_lista_workbooks(ruta_archivo, separador=','):
    """
    Lee lista_workbooks.csv (generado por Descarga.sql con SET MARKUP CSV ON)
    y lo convierte en un DataFrame de pandas, limpio y validado.

    Esta es probablemente la función más importante para diagnosticar tu
    problema de "no encuentra los workbooks": aquí es donde se decide qué
    LUID exacto se le pasará después a Tableau para descargar.
    """

    ruta = Path(ruta_archivo)

    try:
        logger.info("[PARSEANDO] Archivo: %s", ruta)
        logger.info("[PARSEANDO] Separador: %r", separador)

        # Comprobaciones básicas antes de intentar leer el archivo:
        if not ruta.is_file():
            logger.error("[ERROR] El archivo no existe: %s", ruta)
            return None

        if ruta.stat().st_size == 0:
            # Puede pasar si SQL PLUS falló silenciosamente o si el SPOOL
            # no llegó a escribir nada (por ejemplo la query no devolvió filas)
            logger.error("[ERROR] El archivo está vacío: %s", ruta)
            return None

        # pd.read_csv hace todo el trabajo de parsing:
        #   sep=separador      -> normalmente ',' porque Descarga.sql usa DELIMITER ','
        #   dtype=str          -> TODO se lee como texto (evita que pandas "adivine"
        #                         tipos raros, p.ej. que interprete un LUID como número)
        #   quotechar='"'      -> IMPRESCINDIBLE porque Descarga.sql usa QUOTE ON:
        #                         cada campo viene entre comillas dobles, así una coma
        #                         DENTRO de un nombre de workbook no rompe las columnas
        #   keep_default_na=False -> evita que pandas convierta campos vacíos o la
        #                         palabra "NA" en NaN; se quedan como texto vacío
        #   skipinitialspace=True -> ignora espacios justo después de cada coma
        df = pd.read_csv(
            ruta, sep=separador, dtype=str, encoding='utf-8',
            quotechar='"', keep_default_na=False, skipinitialspace=True
        )

        # Normalizar encabezados: quita espacios sobrantes y los pasa a MAYÚSCULAS,
        # así "Workbook_Luid" o " WORKBOOK_LUID " se convierten en "WORKBOOK_LUID"
        # y las comparaciones de más abajo funcionan sí o sí.
        df.columns = [str(columna).strip().upper() for columna in df.columns]

        # Limpiar espacios sobrantes en TODOS los valores de TODAS las columnas.
        # Esto es clave: un LUID con un espacio al final (" cede88a2...")
        # NO coincidiría con el LUID real en Tableau, y eso da justo el error
        # de "workbook no encontrado" que comentas.
        for columna in df.columns:
            df[columna] = (df[columna].astype(str).str.strip())

        # Columnas sin las cuales no se puede identificar ni descargar un workbook
        columnas_requeridas = {"WORKBOOK_LUID", "WORKBOOK"}
        columnas_faltantes = (columnas_requeridas - set(df.columns))

        if columnas_faltantes:
            logger.error(
                "[ERROR] Faltan columnas requeridas: %s",
                ", ".join(sorted(columnas_faltantes))
            )
            logger.error("[INFO] Columnas disponibles: %s", ", ".join(df.columns))
            return None

        # RUTA_PROYECTO es opcional: si no viene, todos los workbooks se
        # guardan en una carpeta "default" en vez de organizarlos por proyecto.
        if "RUTA_PROYECTO" not in df.columns:
            logger.warning("[AVISO] RUTA_PROYECTO no encontrada. Se utilizará 'default'")
            df["RUTA_PROYECTO"] = "default"

        if "RUTA_LOCAL_DESTINO" not in df.columns:
            logger.warning("[AVISO] RUTA_LOCAL_DESTINO no encontrada")
            df["RUTA_LOCAL_DESTINO"] = ""

        # Descarta filas "vacías" o corruptas: sin LUID o sin nombre no hay
        # forma de descargar ese workbook, así que mejor quitarlas ahora
        # que fallar más adelante a mitad de la descarga.
        df = df[
            (df["WORKBOOK_LUID"] != "")
            & (df["WORKBOOK"] != "")
        ]

        # Si la consulta de Oracle trae el mismo workbook repetido varias veces
        # (por ejemplo un JOIN que duplica filas), aquí nos quedamos solo con
        # la ÚLTIMA aparición de cada LUID, para no descargarlo dos veces.
        df = df.drop_duplicates(subset=["WORKBOOK_LUID"], keep="last")

        # Reindexar 0,1,2,3... después de haber borrado filas arriba,
        # para que procesar_descargas() itere sin huecos raros en el índice.
        df = df.reset_index(drop=True)

        logger.info("[OK] Workbooks válidos: %d", len(df))
        logger.info("[COLUMNAS] %s", ", ".join(df.columns))

        return df

    except UnicodeDecodeError:
        # El archivo no está en UTF-8 (por ejemplo Oracle lo generó en
        # Windows-1252/Latin1 por el NLS_LANG del .bat). Si te salta esto,
        # revisa el "set NLS_LANG=..." de ConexionOracle.bat.
        logger.exception("[ERROR] La codificación del archivo no coincide con encoding_lista")
        return None

    except pd.errors.ParserError:
        # El CSV está mal formado: número de columnas distinto entre filas,
        # comillas sin cerrar, etc. Suele pasar si Descarga.sql se modificó
        # y algún SELECT ya no encaja con "SET MARKUP CSV ON ... QUOTE ON".
        logger.exception("[ERROR] El archivo no tiene un formato CSV válido")
        return None

    except Exception:
        # Cualquier otro fallo no previsto: logger.exception imprime también
        # el traceback completo en tableau_sync.log, muy útil para depurar.
        logger.exception("[ERROR] No se pudo parsear el archivo")
        return None


def limpiar_directorio(directorio_base):
    """
    Borra por completo el directorio de descargas y lo vuelve a crear vacío.

    Esto asegura que cada ejecución del script parte de cero: si un workbook
    se eliminó de Tableau, su .twbx viejo también desaparece del backup local
    (en vez de quedarse ahí para siempre como basura).
    """
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)

    ruta = Path(directorio_base)

    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)  # Borra la carpeta Y todo su contenido
            logger.info("[OK] Directorio eliminado")
        except Exception as e:
            # No se corta el programa aquí: si falla el borrado (por ejemplo
            # un archivo bloqueado por otro proceso), se intenta seguir igualmente.
            logger.error("[ERROR] Error al eliminar: %s", e)

    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        # Si ni siquiera se puede CREAR el directorio (permisos, ruta inválida...)
        # no tiene sentido continuar: no habría dónde guardar nada.
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def autenticar_tableau(config):
    """
    Inicia sesión en Tableau Server usando un Personal Access Token (PAT).

    Causas típicas de fallo aquí (y que luego se "disfrazan" de
    "no encuentra el workbook" más adelante):
      - El PAT caducó (los PAT de Tableau expiran si llevan tiempo sin usarse,
        o tienen fecha de caducidad fija según la config del servidor).
      - tableau_site no coincide con el site real: si tus workbooks están en
        un site llamado "ventas" y pones tableau_site="default", el login
        funciona pero luego NINGÚN LUID se va a encontrar, porque estás
        buscando en el site equivocado.
    """
    try:
        logger.info("[AUTENTICANDO] Tableau...")

        tableau_auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site']  # OJO: site_id es el "content URL" del site, no su nombre visible
        )

        server = TSC.Server(config['tableau_server'])
        server.auth.sign_in(tableau_auth)

        logger.info("[OK] Autenticado en Tableau")
        return server

    except Exception as e:
        logger.error("[ERROR] Error al autenticar: %s", e)
        logger.error("")
        logger.error("Verifica en config.json:")
        logger.error("- tableau_server: %s", config.get('tableau_server', 'NO DEFINIDO'))
        logger.error("- tableau_token_name: %s", config.get('tableau_token_name', 'NO DEFINIDO'))
        logger.error("- tableau_token: [oculto]")
        logger.error("- tableau_site: %s", config.get('tableau_site', 'NO DEFINIDO'))
        sys.exit(1)  # Sin sesión válida no tiene sentido seguir


def descargar_workbook(server, workbook_luid, ruta_destino):
    """
    Descarga UN workbook concreto por su LUID.

    Si esta función falla con algo como "workbook not found" o un error
    de la librería TSC, casi siempre es una de estas 3 causas:
      1) El LUID en el CSV tiene caracteres invisibles/espacios (por eso
         se hace .strip() en parsear_lista_workbooks, pero si Oracle
         devuelve saltos de línea dentro del campo, .strip() no los quita).
      2) El workbook fue borrado o movido de site después de que Oracle
         generara la lista (la tabla DESCARGA_WORKBOOKS está desactualizada).
      3) El PAT usado no tiene permisos de "Ver"/"Descargar" sobre ese
         proyecto/workbook concreto en Tableau.
    """
    try:
        ruta_destino = Path(ruta_destino)
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)

        logger.info("[DESCARGANDO] %s", workbook_luid)

        # TSC.workbooks.download() por defecto crea una CARPETA con el
        # nombre del workbook y mete el .twbx dentro (comportamiento raro
        # de la librería). Por eso se descarga primero a una ruta temporal...
        #
        # Desglose de la línea siguiente:
        #   ruta_destino.parent -> la carpeta donde debe quedar el archivo
        #                          (ej: tableau_workbooks/Finance)
        #   ruta_destino.stem   -> el nombre del archivo SIN extensión
        #                          (ej: "Admin Insights Starter", sin el ".twbx")
        #   parent / stem       -> el operador "/" en un objeto Path junta
        #                          carpeta + nombre (equivale a unir con "\" en Windows)
        #   str(...)            -> TSC.workbooks.download() espera un string, no un Path
        # Resultado: "tableau_workbooks/Finance/Admin Insights Starter"
        # (todavía SIN ".twbx", porque esa ruta se usará como si fuera una carpeta)
        ruta_temporal = str(ruta_destino.parent / ruta_destino.stem)
        server.workbooks.download(workbook_luid, filepath=ruta_temporal)

        carpeta_descargada = Path(ruta_temporal)

        if carpeta_descargada.is_dir():
            # ...y luego se saca el .twbx de dentro de esa carpeta y se
            # mueve al nombre de archivo final que queremos (ruta_destino).
            archivos_twbx = list(carpeta_descargada.glob('*.twbx'))

            if archivos_twbx:
                shutil.move(str(archivos_twbx[0]), str(ruta_destino))
                try:
                    shutil.rmtree(carpeta_descargada)  # Limpia la carpeta temporal, ya vacía de .twbx
                    logger.info("[OK] Descargado: %s", ruta_destino.name)
                except:
                    # No es grave si falla el borrado de la carpeta temporal:
                    # el .twbx ya se movió correctamente a su sitio.
                    pass
                return True
            else:
                logger.error("[ERROR] No se encontró .twbx")
                return False
        else:
            # Algunos workbooks se descargan directamente como archivo
            # (sin crear carpeta intermedia), dependiendo de la versión de TSC.
            if ruta_destino.exists():
                logger.info("[OK] Descargado: %s", ruta_destino.name)
                return True
            else:
                logger.error("[ERROR] Archivo no encontrado")
                return False

    except Exception as e:
        # Aquí es donde probablemente están cayendo tus "excepciones que no
        # entiendes": el mensaje 'e' trae el motivo exacto que da la API de
        # Tableau (permisos, LUID inexistente, etc.). Revisa tableau_sync.log
        # línea por línea de cada "[ERROR] Error descargando: ..." para ver
        # el detalle real que devuelve el servidor.
        logger.error("[ERROR] Error descargando: %s", e)
        return False


def procesar_descargas(server, df, directorio_base):
    """
    Recorre el DataFrame fila a fila y descarga cada workbook,
    llevando la cuenta de cuántos salieron bien y cuántos con error.
    """

    estadisticas = {
        'total': len(df),
        'descargados': 0,
        'errores': 0,
        'tiempos': {}
    }

    logger.info("="*60)
    logger.info("DESCARGANDO WORKBOOKS")
    logger.info("="*60)

    # df.iterrows() recorre el DataFrame fila por fila.
    # enumerate(..., 1) añade un contador empezando en 1 (solo para el log "[3/15]")
    for contador, (idx, fila) in enumerate(df.iterrows(), 1):
        workbook_luid = str(fila['WORKBOOK_LUID']).strip()
        workbook_nombre = str(fila['WORKBOOK']).strip()
        ruta_proyecto = str(fila.get('RUTA_PROYECTO', 'default')).strip()

        # El .twbx final queda en: directorio_base / RUTA_PROYECTO / NombreWorkbook.twbx
        ruta_local = Path(directorio_base) / ruta_proyecto / f"{workbook_nombre}.twbx"

        logger.info("\n[%d/%d] %s", contador, len(df), workbook_nombre)
        logger.info("       Proyecto: %s", ruta_proyecto)
        logger.info("       LUID: %s", workbook_luid)

        inicio = datetime.now()

        if descargar_workbook(server, workbook_luid, ruta_local):
            estadisticas['descargados'] += 1
            tiempo = (datetime.now() - inicio).total_seconds()
            estadisticas['tiempos'][workbook_nombre] = tiempo
        else:
            estadisticas['errores'] += 1
            # NOTA: aquí NO se detiene el script si un workbook falla.
            # Se registra el error y se sigue con el siguiente, para que
            # un solo workbook problemático no bloquee todo el backup.

    return estadisticas


def generar_jwt_github_app(app_id, ruta_llave_privada):
    """
    Crea un JWT (JSON Web Token) firmado con la llave privada de la GitHub App.

    Este JWT es como una "credencial de la app en general" — sirve para
    demostrarle a GitHub "soy la App con este App ID", pero TODAVÍA no da
    permiso para tocar ningún repositorio concreto. Solo es el paso
    intermedio para pedir el token de instalación (ver función siguiente).

    Dura muy poco a propósito (10 minutos): así, si alguien lo intercepta,
    deja de servir casi enseguida. Por eso se genera uno nuevo cada vez que
    se ejecuta el script, en vez de guardarlo.
    """
    ahora = int(time.time())

    payload = {
        'iat': ahora - 60,       # "issued at": se resta 1 minuto por si el
                                  # reloj del servidor de GitHub va un poco
                                  # adelantado respecto al de esta máquina
        'exp': ahora + (10 * 60),  # "expira en": 10 minutos desde ahora (máximo permitido por GitHub)
        'iss': app_id             # "issuer": el App ID, identifica QUÉ app está pidiendo esto
    }

    with open(ruta_llave_privada, 'r') as f:
        llave_privada = f.read()

    # algorithm='RS256' -> la llave privada de una GitHub App siempre es RSA,
    # por eso se firma con este algoritmo (no vale HS256, que es de llave simétrica)
    token = pyjwt.encode(payload, llave_privada, algorithm='RS256')
    return token


def obtener_installation_token(app_id, installation_id, ruta_llave_privada):
    """
    Cambia el JWT "genérico de la app" (función anterior) por un token de
    instalación válido para el repositorio concreto donde se instaló la app.

    Este SÍ es el token que se usa para hacer git push, equivalente en la
    práctica a un Personal Access Token, pero con dos ventajas:
      - Expira solo en ~1 hora (mucho más seguro que un PAT que dura meses)
      - Solo tiene los permisos que el administrador le dio a la app al
        instalarla (normalmente: leer/escribir contenido de ese repo, nada más)
    """
    jwt_token = generar_jwt_github_app(app_id, ruta_llave_privada)

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    logger.info("[GITHUB APP] Solicitando token de instalación...")
    respuesta = requests.post(url, headers=headers, timeout=15)

    if respuesta.status_code != 201:
        # Causas típicas: App ID o Installation ID incorrectos, la llave
        # privada no corresponde a esa app, o la app fue desinstalada del repo
        logger.error(
            "[ERROR] No se pudo obtener el token de instalación (código %d): %s",
            respuesta.status_code, respuesta.text[:300]
        )
        return None

    token = respuesta.json()['token']
    logger.info("[OK] Token de instalación obtenido (válido ~1 hora)")
    return token


def listar_contenido_github(config, ruta_en_repo=""):
    """
    Consulta, vía la API REST de GitHub, qué archivos hay en el repositorio
    (o en una subcarpeta concreta) SIN necesidad de clonarlo ni de que haya
    ninguna sesión de usuario iniciada. Usa el mismo token de instalación
    que subir_github(), reutilizando la misma GitHub App.

    Parámetros:
      ruta_en_repo -> subcarpeta del repo a listar. Con "" (vacío) lista
                       la raíz del repositorio.

    Devuelve: una lista de nombres de archivo/carpeta, o None si algo falla.
    """
    token = obtener_installation_token(
        config['github_app_id'],
        config['github_installation_id'],
        config['github_private_key_path']
    )

    if token is None:
        logger.error("[ERROR] No se pudo autenticar con GitHub App para listar contenido")
        return None

    owner = config['github_owner']
    repo = config['github_repo_name']

    # Endpoint "Contents API": devuelve lo que hay dentro de una ruta del repo,
    # tal y como está en la rama por defecto (normalmente 'main')
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{ruta_en_repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    respuesta = requests.get(url, headers=headers, timeout=15)

    if respuesta.status_code != 200:
        logger.error(
            "[ERROR] No se pudo listar el contenido (código %d): %s",
            respuesta.status_code, respuesta.text[:300]
        )
        return None

    elementos = respuesta.json()  # lista de dicts: uno por archivo/carpeta

    for elemento in elementos:
        tipo = "carpeta" if elemento['type'] == 'dir' else "archivo"
        logger.info("  [%s] %s", tipo, elemento['path'])

    # Devuelve solo los nombres/rutas, por si se quieren usar en código
    return [elemento['path'] for elemento in elementos]


def subir_github(directorio_base, config):
    """
    Hace commit y push del contenido de directorio_base a GitHub, autenticando
    con una GitHub App (App ID + Installation ID + llave privada .pem) en vez
    de depender de credenciales git ya guardadas en Windows.

    El token de instalación se pide DE NUEVO en cada ejecución del script
    (dura solo ~1 hora, así que no tiene sentido guardarlo) y se usa
    ÚNICAMENTE en la URL del push — nunca se guarda en el remote "origin"
    del repositorio, para que no quede el token en texto plano dentro de
    .git/config.
    """

    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)

        token = obtener_installation_token(
            config['github_app_id'],
            config['github_installation_id'],
            config['github_private_key_path']
        )

        if token is None:
            logger.error("[ERROR] No se pudo autenticar con GitHub App, se aborta la subida")
            return

        os.chdir(directorio_base)  # git necesita ejecutarse DENTRO del repositorio

        # Comandos simples (add, commit) no necesitan credenciales -> igual que antes
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"

        logger.info("[GIT] git commit")
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )

        if "nothing to commit" in resultado.stdout.lower():
            logger.info("[AVISO] No hay cambios")
            return

        # Aquí SÍ hace falta el token: se construye la URL de push con el
        # token embebido como "usuario" (x-access-token es un usuario fijo
        # que GitHub reconoce especialmente para tokens de GitHub App).
        # No se toca el remote "origin" guardado en el repo -> el token no
        # queda persistido en ningún archivo, solo se usa en esta llamada.
        owner = config['github_owner']
        repo = config['github_repo_name']
        url_con_token = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        logger.info("[GIT] git push (autenticado con GitHub App)")
        subprocess.run(
            ['git', 'push', url_con_token, 'main'],
            check=True,
            capture_output=True
        )

        logger.info("[OK] Subido a GitHub")

    except Exception as e:
        # Un fallo de GitHub (sin conexión, token caducado, conflicto de
        # push, etc.) se registra pero NO detiene el script: los workbooks
        # ya se descargaron localmente igualmente.
        logger.error("[ERROR] Error en GitHub: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """Imprime el resumen final: cuántos workbooks salieron bien, cuántos
    con error, tiempo total y tiempo medio por workbook."""

    logger.info("="*60)
    logger.info("REPORTE FINAL")
    logger.info("="*60)

    logger.info("Total de workbooks:    %d", estadisticas['total'])
    logger.info("Descargados:           %d [OK]", estadisticas['descargados'])
    logger.info("Errores:               %d [ERROR]", estadisticas['errores'])

    if estadisticas['total'] > 0:
        tasa = (estadisticas['descargados'] / estadisticas['total'] * 100)
        logger.info("Tasa de exito:         %.1f%%", tasa)

    logger.info("Tiempo total:          %.2fs", tiempo_total)

    if estadisticas['tiempos']:
        promedio = sum(estadisticas['tiempos'].values()) / len(estadisticas['tiempos'])
        logger.info("Tiempo promedio/wb:    %.2fs", promedio)

    logger.info("="*60)


# ============================================================================
# FUNCIÓN PRINCIPAL — ORQUESTA TODOS LOS PASOS EN ORDEN
# ============================================================================

def main():
    """
    Punto de entrada del script. Ejecuta, EN ORDEN, todos los pasos:

      1. Borrar el CSV de la ejecución anterior (para no leer datos viejos
         por error si el PASO 2 falla silenciosamente y no regenera el archivo)
      2. Ejecutar SQL PLUS (vía el .bat) para regenerar el CSV
      3. Parsear ese CSV a un DataFrame de pandas
      4. Vaciar y recrear el directorio local de descargas
      5. Autenticarse en Tableau Server
      6. Descargar cada workbook del DataFrame
      7. Subir los cambios a GitHub
      8. Mostrar un reporte final con estadísticas
    """

    # --- Argumentos de línea de comandos ---
    parser = argparse.ArgumentParser(
        description='Descarga workbooks Tableau usando SQL PLUS + Python (v2 CORREGIDO)'
    )
    parser.add_argument(
        '--config',
        default='config.json',
        help='Archivo de configuración (default: config.json)'
    )
    parser.add_argument(
        '--sin-github',
        action='store_true',  # Si se pasa este flag, sin_github=True; si no, False
        help='Solo descargar, sin subir a GitHub'
    )
    parser.add_argument(
        '--separador',
        default=',',
        help='Separador CSV (default: ,) - usar "\\t" para TSV o "|" para PIPE'
    )

    args = parser.parse_args()

    inicio_total = datetime.now()  # Para calcular la duración total al final

    # --- Cargar y validar configuración ---
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    comando = config['sqlplus_comando']              # Ruta al .bat (ConexionOracle.bat)
    timeout = config.get('timeout_sqlplus', 15)
    archivo_lista = Path(config['archivo_lista_workbooks'])  # Ruta al CSV generado por SQL PLUS

    directorio_base = config.get('directorio_descarga', './tableau_workbooks')

    # ========================================================================
    # PASO 1: ELIMINAR LISTA ANTERIOR
    # ========================================================================
    # ¿Por qué borrar el CSV antes de volver a generarlo?
    # Porque si el PASO 2 (ejecutar SQL PLUS) falla a medias, o Oracle no
    # devuelve ninguna fila, el .bat podría NO sobrescribir el archivo viejo.
    # Sin este borrado previo, el script seguiría leyendo alegremente el CSV
    # de la ejecución ANTERIOR sin darse cuenta de que los datos están
    # desactualizados. Borrándolo primero, si el PASO 2 falla, el PASO 3
    # (parsear) se encuentra con que el archivo no existe y corta la
    # ejecución con un error claro, en vez de seguir con datos viejos.
    logger.info("="*60)
    logger.info("PASO 1: ELIMINAR LISTA ANTERIOR")
    logger.info("="*60)

    try:
        if archivo_lista.exists():
            archivo_lista.unlink()  # Borra el archivo (equivalente a "delete")
            logger.info("[OK] Archivo anterior eliminado :%s", archivo_lista)
        else:
            logger.info("[INFO] No había un archivo anterior que eliminar")
    except OSError as error:
        # Por ejemplo si el archivo está abierto en Notepad y Windows lo
        # bloquea para borrado. Se registra el error pero NO se corta el
        # script aquí (podría seguir funcionando si el PASO 2 lo sobrescribe igual).
        logger.error("[FATAL] No se pudo eliminar el archivo anterior %s: %s", archivo_lista, error)

    # ========================================================================
    # PASO 2: EJECUTAR SQL PLUS
    # ========================================================================
    # Lanza ConexionOracle.bat, que a su vez hace login en Oracle y ejecuta
    # Descarga.sql, generando de nuevo lista_workbooks.csv.
    logger.info("="*60)
    logger.info("PASO 2: EJECUTAR SQL PLUS")
    logger.info("="*60)

    if not ejecutar_sqlplus(comando, timeout):
        logger.error("[FATAL] No se pudo ejecutar SQL PLUS")
        sys.exit(1)  # Sin el CSV actualizado, no tiene sentido continuar

    # ========================================================================
    # PASO 3: PARSEAR ARCHIVO (CSV/TSV/PIPE)
    # ========================================================================
    logger.info("="*60)
    logger.info("PASO 3: PARSEAR ARCHIVO (CSV/TSV/PIPE)")
    logger.info("="*60)

    df = parsear_lista_workbooks(archivo_lista, args.separador)

    if df is None or len(df) == 0:
        logger.error("[FATAL] No se pudo parsear el archivo")
        sys.exit(1)

    # ========================================================================
    # PASO 4: LIMPIAR DIRECTORIO LOCAL DE DESCARGAS
    # ========================================================================
    limpiar_directorio(directorio_base)

    # ========================================================================
    # PASO 5: AUTENTICAR EN TABLEAU
    # ========================================================================
    logger.info("="*60)
    logger.info("PASO 5: AUTENTICAR TABLEAU")
    logger.info("="*60)

    server = autenticar_tableau(config)

    # ========================================================================
    # PASO 6: DESCARGAR TODOS LOS WORKBOOKS DEL DATAFRAME
    # ========================================================================
    estadisticas = procesar_descargas(server, df, directorio_base)

    # ========================================================================
    # PASO 7: SUBIR A GITHUB (si está habilitado)
    # ========================================================================
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado")

    # Cerrar sesión en Tableau al terminar, buena práctica para no dejar
    # sesiones abiertas acumulándose en el servidor.
    server.auth.sign_out()

    # ========================================================================
    # PASO 8: REPORTE FINAL
    # ========================================================================
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


if __name__ == '__main__':
    # Esto asegura que main() solo se ejecuta si el archivo se corre
    # directamente (python descargar_workbooks_sqlplus_v3_COMENTADO.py),
    # y NO si en algún momento decides importar este archivo desde otro
    # script Python (import descargar_workbooks_sqlplus_v3_COMENTADO).
    main()
