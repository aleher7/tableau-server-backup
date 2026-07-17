#!/usr/bin/env python3

"""
Script dual: Lee estructura de workbooks desde SQL PLUS o ARCHIVO
Descarga workbooks de Tableau y los sube a GitHub
Versión MEJORADA: Limpia carpeta antes de descargar + SQL PLUS integrado

Este script automatiza el backup de workbooks de Tableau, ofreciendo dos formas
de obtener la lista de workbooks a descargar: directamente desde una tabla en 
Oracle (vía SQL PLUS) o desde un archivo local como fallback.
"""

import os
import sys
import json
import logging
import subprocess
import argparse
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import pandas as pd

try:
    import tableauserverclient as TSC
except ImportError:
    print("ERROR: tableauserverclient no está instalado")
    print("Instala con: pip install tableauserverclient")
    sys.exit(1)

# ============================================================================
# CONFIGURACIÓN DE LOGGING
# ============================================================================
# Aquí configuramos cómo y dónde se guardan los mensajes de ejecución.
# Usamos dos "handlers" (destinos) simultáneamente:
#   1. Archivo (tableau_sync.log) - para histórico completo
#   2. Pantalla (StreamHandler) - para ver en tiempo real
# Esto es importante para debugging porque puedes revisar el log incluso
# después de que el script terminó.

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
# FUNCIONES PRINCIPALES
# ============================================================================

def cargar_config(config_file="config.json"):
    """
    Carga la configuración desde un archivo JSON.
    
    Este archivo contiene TODAS las credenciales y parámetros que necesita
    el script para funcionar (URL de Tableau, credenciales Oracle, GitHub, etc.)
    
    Si el archivo no existe o está malformado, el script termina inmediatamente
    porque sin configuración no puede hacer nada útil.
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logger.info("[OK] Configuración cargada correctamente")
        return config
    except FileNotFoundError:
        logger.error("[ERROR] Archivo %s no encontrado", config_file)
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error("[ERROR] Error al parsear %s", config_file)
        sys.exit(1)


def limpiar_directorio(directorio_base):
    """
    Elimina COMPLETAMENTE la carpeta de descargas y la recrea vacía.
    
    ¿Por qué hace esto?
    - Garantiza que cada ejecución comienza con un estado limpio
    - Evita acumular archivos viejos o huérfanos
    - Asegura que solo descargues lo que está en la lista actual
    - Ideal para backups: quieres la "foto actual" de los workbooks
    
    IMPORTANTE: Intenta dos estrategias distintas:
    1. Eliminar la carpeta completa (más rápido)
    2. Si falla (permisos), al menos limpia el contenido (más lento pero seguro)
    
    Esto es resistente a errores porque los archivos a veces están bloqueados
    por el SO o por otro proceso.
    """
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    # INTENTO 1: Eliminar directorio completo (estrategia agresiva)
    # Esto es más rápido porque borra TODO de una vez
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado completamente")
        except PermissionError as e:
            # Si no podemos eliminar la carpeta entera (permisos del SO),
            # intentamos una estrategia alternativa: borrar archivo por archivo
            logger.warning("[AVISO] Permiso denegado, intentando limpiar contenido: %s", e)
            
            # INTENTO 2: Limpiar solo el contenido (estrategia alternativa)
            # Esto es más seguro pero más lento porque accede a cada archivo
            try:
                # rglob('*') = busca recursivamente TODOS los archivos y carpetas
                for archivo in ruta.rglob('*'):
                    try:
                        if archivo.is_file():
                            archivo.unlink()  # Borra archivo
                            logger.debug("[DEL] Archivo: %s", archivo.name)
                        elif archivo.is_dir() and archivo != ruta:
                            shutil.rmtree(archivo)  # Borra subcarpeta
                            logger.debug("[DEL] Carpeta: %s", archivo.name)
                    except Exception as ex:
                        logger.warning("[AVISO] No se pudo borrar: %s", ex)
                logger.info("[OK] Contenido del directorio limpiado")
            except Exception as ex:
                logger.error("[ERROR] No se pudo limpiar: %s", ex)
        except Exception as e:
            logger.error("[ERROR] Error al eliminar directorio: %s", e)
    
    # Una vez que borró todo, recreamos la carpeta VACÍA y lista para usar
    # exist_ok=True significa "si ya existe, no es un error"
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def ejecutar_sqlplus(usuario, contraseña, dsn, consulta_sql):
    """
    Ejecuta una consulta SQL PLUS y retorna los resultados como DataFrame.
    
    IMPORTANTE: SQL PLUS es una herramienta de línea de comandos de Oracle.
    No se conecta como Python, sino que ejecuta un PROCESO EXTERNO.
    
    ¿Por qué es complicado?
    - SQL PLUS espera entrada en stdin (entrada estándar)
    - SQL PLUS devuelve salida formateada (con espacios, caracteres especiales)
    - Necesitamos parsear esa salida de forma confiable
    
    Estrategia:
    1. Crear archivo SQL temporal con la consulta
    2. Ejecutar: sqlplus usuario/pass@dsn < archivo.sql
    3. Capturar la salida
    4. Parsear línea por línea (separadas por |)
    5. Convertir a DataFrame (tabla de pandas)
    """
    try:
        logger.info("[SQLPLUS] Conectando con SQL PLUS...")
        
        # ============================================================
        # PASO 1: Crear archivo SQL temporal
        # ============================================================
        # Creamos un archivo que SQL PLUS leerá. Es temporal porque lo
        # borramos después. Usamos tempfile para evitar conflictos de nombres.
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
            # Estos comandos configuran SQL PLUS para que la salida sea parseable
            f.write("SET FEEDBACK OFF\n")          # No mostrar "n rows selected"
            f.write("SET PAGESIZE 0\n")            # No dividir en páginas
            f.write("SET LINESIZE 1000\n")         # Líneas largas completas
            f.write("SET COLSEP |\n")              # Separador de columnas: |
            f.write("SET HEADING ON\n")            # Mostrar nombres de columnas
            f.write("WHENEVER SQLERROR EXIT SQL.SQLCODE\n")  # Parar si hay error
            
            # Agregamos la consulta que el usuario quiere ejecutar
            f.write(consulta_sql)
            f.write("\nEXIT;\n")  # Terminar sesión SQL PLUS
            
            archivo_sql = f.name
        
        # ============================================================
        # PASO 2: Ejecutar SQL PLUS como proceso externo
        # ============================================================
        # subprocess.run() ejecuta un programa externo (sqlplus en este caso)
        # capture_output=True captura la salida para poder procesarla en Python
        
        conexion_string = f"{usuario}/{contraseña}@{dsn}"
        
        logger.info("[SQLPLUS] Ejecutando consulta...")
        resultado = subprocess.run(
            ['sqlplus', '-S', conexion_string],  # -S = modo silencioso
            stdin=open(archivo_sql, encoding='utf-8'),
            capture_output=True,
            text=True,
            timeout=60  # Máximo 1 minuto, si tarda más hay problema
        )
        
        # Limpiar archivo temporal (ya no lo necesitamos)
        try:
            os.unlink(archivo_sql)
        except:
            pass  # Si falla, no es crítico
        
        # ============================================================
        # PASO 3: Verificar si la consulta tuvo éxito
        # ============================================================
        # returncode=0 significa que sqlplus terminó correctamente
        # Si es distinto de 0, hubo un error (credenciales, sintaxis SQL, etc.)
        
        if resultado.returncode != 0:
            logger.error("[ERROR] SQL PLUS error (código %d): %s", resultado.returncode, resultado.stderr)
            return None
        
        if not resultado.stdout.strip():
            logger.warning("[AVISO] SQL PLUS retornó sin datos")
            return None
        
        # ============================================================
        # PASO 4: Parsear la salida de SQL PLUS
        # ============================================================
        # SQL PLUS devuelve algo como:
        # WORKBOOK_LUID|WORKBOOK|RUTA_PROYECTO
        # a1b2c3d4|Dashboard_Ventas|Finance
        # b2c3d4e5|Dashboard_Budget|Finance
        #
        # Separamos por líneas y luego por |
        
        lineas = [l.strip() for l in resultado.stdout.strip().split('\n') if l.strip()]
        
        if len(lineas) < 2:
            logger.warning("[AVISO] No hay suficientes datos en la respuesta")
            return None
        
        # Primera línea = encabezados (nombres de columnas)
        encabezados = [col.strip() for col in lineas[0].split('|')]
        
        # Resto = datos reales (cada fila es un workbook)
        datos = []
        for linea in lineas[1:]:
            if linea.strip():
                valores = [val.strip() for val in linea.split('|')]
                datos.append(valores)
        
        if not datos:
            logger.warning("[AVISO] No hay datos en la tabla")
            return None
        
        # ============================================================
        # PASO 5: Convertir a DataFrame de pandas
        # ============================================================
        # DataFrame es como una tabla Excel en memoria. Es muy fácil de
        # manipular, filtrar y trabajar con ella en Python.
        
        df = pd.DataFrame(datos, columns=encabezados)
        
        logger.info("[OK] Datos obtenidos de SQL PLUS: %d filas", len(df))
        logger.debug("[COLUMNAS] %s", ", ".join(df.columns))
        
        return df
        
    except subprocess.TimeoutExpired:
        logger.error("[ERROR] SQL PLUS timeout (>60 segundos)")
        return None
    except FileNotFoundError:
        logger.error("[ERROR] SQL PLUS no encontrado. Verifica que esté instalado y en PATH")
        return None
    except Exception as e:
        logger.error("[ERROR] Error en SQL PLUS: %s", e)
        return None


def leer_archivo_datos(ruta_archivo):
    """
    Lee un archivo con la lista de workbooks (Excel o TXT o CSV).
    
    IMPORTANTE: Esta función es muy flexible porque intenta ADIVINAR
    el formato del archivo basándose en la extensión.
    
    ¿Por qué es complicado?
    - Los usuarios dan archivos en diferentes formatos (.txt, .xlsx, .csv)
    - Cada formato necesita un parser diferente
    - Además, algunos formatos pueden tener separadores distintos
    - Queremos que el script funcione sin que el usuario tenga que
      especificar el formato manualmente
    
    Estrategia:
    1. Detectar formato por extensión
    2. Leer con el parser apropriado
    3. Limpiar datos (espacios, comillas)
    4. Buscar columnas de forma flexible (tolera diferentes nombres)
    5. Renombrar a nombres estándar para el resto del script
    6. Filtrar solo lo que necesita descargarse
    """
    try:
        logger.info("[LEYENDO] Archivo: %s", ruta_archivo)
        
        # ============================================================
        # PASO 1: Detectar formato del archivo
        # ============================================================
        # Miramos la extensión: .xlsx, .csv, .txt, .dsv, etc.
        
        extension = Path(ruta_archivo).suffix.lower()
        
        # ============================================================
        # PASO 2: Leer el archivo con el parser correcto
        # ============================================================
        # Cada formato tiene sus peculiaridades:
        # - Excel (.xlsx): binario, necesita openpyxl
        # - CSV: texto con comas, puede tener comillas
        # - TXT: texto con tabulaciones (o espacios)
        # - DSV (Delimited Separated Values): similar a CSV pero más robusto
        
        if extension == '.dsv':
            df = pd.read_csv(
                ruta_archivo,
                sep=',',
                quotechar='"',
                doublequote=True,
                skipinitialspace=True,
                on_bad_lines='skip'  # Ignorar líneas malformadas
            )
        elif extension in ['.xlsx', '.xls']:
            df = pd.read_excel(ruta_archivo)
        elif extension == '.csv':
            df = pd.read_csv(
                ruta_archivo,
                sep=',',
                quotechar='"',
                doublequote=True,
                skipinitialspace=True,
                on_bad_lines='skip'
            )
        else:
            # Si no reconocemos el formato, asumir que es TXT con tabulación
            logger.warning("[AVISO] Extension no reconocida, intentando como TXT")
            df = pd.read_csv(ruta_archivo, sep='\t', on_bad_lines='skip')
        
        logger.info("[OK] Archivo cargado: %d filas", len(df))
        
        # ============================================================
        # PASO 3: Limpiar los datos
        # ============================================================
        # A veces los archivos tienen espacios o comillas extra que molestan:
        # "  WORKBOOK_LUID  " → debería ser WORKBOOK_LUID
        # ""Dashboard_Ventas"" → debería ser Dashboard_Ventas
        
        # Limpiar nombres de columnas
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        
        # Limpiar valores en las celdas (solo si son texto)
        for col in df.columns:
            if df[col].dtype == 'object':  # 'object' = texto en pandas
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        # ============================================================
        # PASO 4: Búsqueda flexible de columnas
        # ============================================================
        # Esto es CRUCIAL: los usuarios pueden nombrar las columnas de forma
        # distinta en cada empresa. Queremos que funcione con:
        #   WORKBOOK_LUID, workbook_luid, LUID, ID, etc.
        #
        # Estrategia en dos fases:
        # 1. Búsqueda EXACTA (WORKBOOK_LUID == WORKBOOK_LUID)
        # 2. Búsqueda PARCIAL (contiene la palabra clave)
        #
        # Ejemplo: Si la columna es "WORKBOOK_NAME" y buscamos "WORKBOOK",
        #          coincide parcialmente porque "WORKBOOK" está en "WORKBOOK_NAME"
        #
        # PERO: Si buscamos "WORKBOOK" no queremos que coincida con "WORKBOOK_LUID"
        #       Por eso tenemos validaciones especiales (los 'if' que ves)
        
        def buscar_columna(df, patrones):
            # Primero intenta coincidencia exacta (más preciso)
            for col in df.columns:
                col_limpio = col.strip().upper().replace('"', '')
                for patron in patrones:
                    if col_limpio == patron.upper():
                        logger.info("[MAPEO] Columna '%s' mapeada a '%s'", col, patron)
                        return col
            
            # Si no encontró exacta, intenta coincidencia parcial
            for col in df.columns:
                col_limpio = col.strip().upper()
                for patron in patrones:
                    if patron.upper() in col_limpio:
                        # Evitar falsas coincidencias:
                        # - Si buscamos "WORKBOOK", no queremos "WORKBOOK_LUID"
                        # - Si buscamos "RUTA", no queremos "RUTA_LOCAL"
                        if patron.upper() == 'WORKBOOK' and 'LUID' in col_limpio:
                            continue
                        if patron.upper() == 'RUTA' and 'LOCAL' in col_limpio:
                            continue
                        logger.info("[MAPEO] Columna '%s' mapeada parcialmente a '%s'", col, patron)
                        return col
            return None
        
        # Buscar cada columna que necesitamos
        col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID', 'ID'])
        col_nombre = buscar_columna(df, ['WORKBOOK', 'NOMBRE', 'NAME'])
        col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA', 'PROJECT'])
        col_descargar = buscar_columna(df, ['DESCARGAR', 'DOWNLOAD', 'ACTIVO', 'ACTIVE'])
        
        # ============================================================
        # PASO 5: Verificar que existen las columnas requeridas
        # ============================================================
        # Sin LUID, NOMBRE y RUTA no podemos hacer nada
        # Es mejor fallar aquí que después con errores confusos
        
        if not col_luid:
            logger.error("[ERROR] No se encontró columna WORKBOOK_LUID/LUID")
            logger.error("[INFO] Columnas disponibles: %s", ", ".join(df.columns))
            sys.exit(1)
        if not col_nombre:
            logger.error("[ERROR] No se encontró columna WORKBOOK/NOMBRE")
            sys.exit(1)
        if not col_ruta:
            logger.error("[ERROR] No se encontró columna RUTA_PROYECTO/PROYECTO")
            sys.exit(1)
        
        # ============================================================
        # PASO 6: Renombrar columnas a nombres estándar
        # ============================================================
        # Esto es importante: el resto del script ESPERA columnas llamadas
        # 'WORKBOOK_LUID', 'WORKBOOK', 'RUTA_PROYECTO'
        # Ahora renombramos las que encontramos a esos nombres estándar
        
        df = df.rename(columns={
            col_luid: 'WORKBOOK_LUID',
            col_nombre: 'WORKBOOK',
            col_ruta: 'RUTA_PROYECTO'
        })
        
        # ============================================================
        # PASO 7: Filtrar solo lo que debe descargarse
        # ============================================================
        # Si existe una columna "DESCARGAR", usarla para filtrar
        # Solo descargamos filas donde DESCARGAR sea TRUE, SI, 1, etc.
        
        if col_descargar:
            df = df.rename(columns={col_descargar: 'DESCARGAR'})
            # Mantener solo filas donde DESCARGAR es verdadero
            df = df[df['DESCARGAR'].astype(str).str.upper().isin(['SÍ', 'SI', '1', 'TRUE', 'Y', 'YES'])]
            logger.info("[FILTRADO] Workbooks para descargar: %d", len(df))
        
        return df
        
    except Exception as e:
        logger.error("[ERROR] Error al leer archivo: %s", e)
        sys.exit(1)


def obtener_datos_inteligente(config, forzar_excel=False):
    """
    Obtiene la lista de workbooks de la forma más inteligente posible.
    
    Esto usa una estrategia con FALLBACK:
    1. Primero intenta SQL PLUS (datos en tiempo real de la BD)
    2. Si falla, usa archivo como respaldo (datos estáticos)
    
    ¿Por qué es útil?
    - Robustez: Si Oracle falla, el script sigue funcionando
    - Flexibilidad: Desarrollador y producción pueden usar métodos distintos
    - Debugging: El fallback a archivo es útil para testing
    
    IMPORTANTE: El parámetro forzar_excel permite saltarse SQL PLUS
    directamente (útil para testing o emergencias)
    """
    logger.info("="*60)
    logger.info("OBTENER DATOS")
    logger.info("="*60)
    
    # ============================================================
    # Si el usuario pasó --forzar-excel, saltamos SQL PLUS
    # ============================================================
    # Esto es útil para testing o si SQL PLUS falla y queremos
    # debugging rápido sin esperar 60 segundos
    
    if forzar_excel:
        logger.info("[FORZANDO] Usando archivo de datos (--forzar-excel)")
        archivo_datos = config.get('archivo_datos', 'workbooks.txt')
        df = leer_archivo_datos(archivo_datos)
        return df, "ARCHIVO_DATOS"
    
    # ============================================================
    # Intentar SQL PLUS primero (datos en tiempo real)
    # ============================================================
    # Si la tabla en Oracle está actualizada, los datos estarán frescos
    # No necesita editar manualmente un archivo
    
    try:
        # Consulta por defecto si no se especifica en config
        consulta = config.get(
            'sqlplus_query',
            'SELECT * FROM DESCARGA_WORKBOOKS'
        )
        
        df = ejecutar_sqlplus(
            usuario=config['oracle_user'],
            contraseña=config['oracle_password'],
            dsn=config['oracle_dsn'],
            consulta_sql=consulta
        )
        
        if df is not None:
            logger.info("[EXITO] Datos obtenidos desde SQL PLUS")
            return df, "SQLPLUS"
        
    except KeyError as e:
        # KeyError = falta un parámetro en config (usuario, password, dsn)
        logger.warning("[AVISO] Config incompleta para SQL PLUS: %s", e)
    except Exception as e:
        logger.error("[ERROR] SQL PLUS falló: %s", e)
    
    # ============================================================
    # FALLBACK: Si SQL PLUS falló, usar archivo como respaldo
    # ============================================================
    # Es la red de seguridad: aunque Oracle esté caído,
    # el script puede seguir funcionando con datos del archivo
    
    logger.warning("[FALLBACK] Usando archivo de datos")
    archivo_datos = config.get('archivo_datos', 'workbooks.txt')
    df = leer_archivo_datos(archivo_datos)
    return df, "ARCHIVO_DATOS"


def autenticar_tableau(config):
    """
    Autentica en Tableau Server usando Personal Access Token (PAT).
    
    PAT es más seguro que usuario/contraseña porque:
    - No expone credenciales de usuario
    - Puedes revocar un token sin cambiar tu contraseña
    - Cada token puede tener permisos limitados
    
    Si esto falla, el script termina porque sin acceso a Tableau
    no puede descargar nada.
    """
    try:
        logger.info("[AUTENTICANDO] Tableau...")
        
        # Crear objeto de autenticación con el PAT token
        tableau_auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site']
        )
        
        # Crear conexión al servidor (pero aún no conectado)
        server = TSC.Server(config['tableau_server'])
        
        # Ahora sí, hacer el login
        server.auth.sign_in(tableau_auth)
        
        logger.info("[OK] Autenticado en Tableau")
        return server
        
    except Exception as e:
        logger.error("[ERROR] Error al autenticar: %s", e)
        sys.exit(1)


def descargar_workbook(server, workbook_luid, ruta_destino):
    """
    Descarga UN workbook de Tableau.
    
    IMPORTANTE: Esto resuelve un problema complicado.
    
    ¿Cuál es el problema?
    Cuando descargas un workbook llamado "Dashboard_Ventas" usando la
    extensión .twbx, Tableau crea esto:
    
    ANTES (incorrecto):
      Finance/
        └── Dashboard_Ventas.twbx/          ❌ CARPETA EXTRA (¡problema!)
            └── Dashboard_Ventas.twbx       ❌ archivo dentro
    
    DESPUÉS (correcto):
      Finance/
        └── Dashboard_Ventas.twbx           ✅ archivo directamente
    
    ¿Por qué pasaba?
    TSC interpreta "Dashboard_Ventas.twbx" como "quiero una carpeta
    con ese nombre" y descarga el contenido dentro.
    
    ¿Cómo lo resolvemos?
    Descargamos SIN la extensión, luego movemos el archivo al lugar correcto.
    
    Paso a paso:
    1. Descargar como "Dashboard_Ventas" (sin .twbx)
       → TSC crea: Dashboard_Ventas/ (carpeta)
       → Dentro: Dashboard_Ventas.twbx (archivo)
    2. Mover el archivo a la ubicación final
    3. Borrar la carpeta temporal
    """
    try:
        ruta_destino = Path(ruta_destino)
        
        # ============================================================
        # Paso 1: Crear la carpeta destino si no existe
        # ============================================================
        # parent.mkdir() crea todas las carpetas necesarias
        # Ejemplo: Si destino es Finance/Dashboard_Ventas.twbx
        #          Crea: Finance/ (si no existe)
        
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info("[DESCARGANDO] %s", workbook_luid)
        
        # ============================================================
        # Paso 2: El truco - descargar SIN la extensión
        # ============================================================
        # Esto es lo importante: ruta_destino.stem quita la extensión
        # Finance/Dashboard_Ventas.twbx → Finance/Dashboard_Ventas
        
        ruta_temporal = str(ruta_destino.parent / ruta_destino.stem)
        
        # Ahora sí, descargar usando TSC
        # Como no tiene .twbx, TSC crea una carpeta
        server.workbooks.download(workbook_luid, filepath=ruta_temporal)
        
        # ============================================================
        # Paso 3: Procesar el archivo descargado
        # ============================================================
        # Ahora tenemos:
        #   Finance/Dashboard_Ventas/              (carpeta que creó TSC)
        #     └── Dashboard_Ventas.twbx            (archivo dentro)
        #
        # Necesitamos:
        #   Finance/Dashboard_Ventas.twbx         (archivo directamente)
        
        carpeta_descargada = Path(ruta_temporal)
        
        if carpeta_descargada.is_dir():
            # Buscar el archivo .twbx dentro de la carpeta
            archivos_twbx = list(carpeta_descargada.glob('*.twbx'))
            
            if archivos_twbx:
                # ============================================================
                # Paso 4: Mover el archivo a la ubicación final
                # ============================================================
                # shutil.move() mueve el archivo DE un lugar A otro
                
                shutil.move(str(archivos_twbx[0]), str(ruta_destino))
                
                # ============================================================
                # Paso 5: Limpiar la carpeta temporal
                # ============================================================
                # Ya no necesitamos la carpeta, la eliminamos
                
                try:
                    shutil.rmtree(carpeta_descargada)
                    logger.info("[OK] Descargado: %s", ruta_destino.name)
                except Exception as e:
                    logger.warning("[AVISO] No se limpió carpeta temporal: %s", e)
                
                return True
            else:
                logger.error("[ERROR] No se encontró .twbx")
                return False
        else:
            # En versiones nuevas de TSC, a veces descarga directamente sin carpeta
            if ruta_destino.exists():
                logger.info("[OK] Descargado: %s", ruta_destino.name)
                return True
            else:
                logger.error("[ERROR] Archivo no encontrado")
                return False
        
    except Exception as e:
        logger.error("[ERROR] Error descargando: %s", e)
        return False


def procesar_descargas(server, df, directorio_base):
    """
    Descarga TODOS los workbooks del DataFrame.
    
    Esto es el "loop principal" - itera sobre cada fila (cada workbook)
    y lo descarga.
    
    IMPORTANTE: Además de descargar, registra estadísticas:
    - Cuántos se descargaron OK
    - Cuántos fallaron
    - Cuánto tiempo tardó cada uno
    
    Estas estadísticas son útiles para:
    - Detectar si hay workbooks problemáticos
    - Optimizar (si uno tarda mucho, investigar)
    - Reporting (si tarda mucho total, investigar por qué)
    """
    
    # Inicializar diccionario con las estadísticas
    # Esto es lo que retornaremos al final
    estadisticas = {
        'total': len(df),
        'descargados': 0,
        'errores': 0,
        'tiempos': {}
    }
    
    logger.info("="*60)
    logger.info("DESCARGANDO WORKBOOKS")
    logger.info("="*60)
    
    # ============================================================
    # Loop: Iterar sobre cada fila del DataFrame
    # ============================================================
    # enumerate() nos da (contador, datos_de_fila)
    # contador empieza en 1 (no en 0, por readabilidad)
    
    for contador, (idx, fila) in enumerate(df.iterrows(), 1):
        # Extraer valores de la fila actual
        # .strip() elimina espacios al principio/final
        workbook_luid = str(fila['WORKBOOK_LUID']).strip()
        workbook_nombre = str(fila['WORKBOOK']).strip()
        ruta_proyecto = str(fila['RUTA_PROYECTO']).strip()
        
        # Construir la ruta local completa
        # Ejemplo: ./tableau_workbooks/Finance/Dashboard_Ventas.twbx
        ruta_local = Path(directorio_base) / ruta_proyecto / f"{workbook_nombre}.twbx"
        
        # Mostrar información de lo que vamos a hacer
        logger.info("\n[%d/%d] %s", contador, len(df), workbook_nombre)
        logger.info("       Proyecto: %s", ruta_proyecto)
        logger.info("       LUID: %s", workbook_luid)
        
        # ============================================================
        # Medir el tiempo de descarga
        # ============================================================
        # Guardamos la hora de inicio para calcular cuánto tardó
        
        inicio = datetime.now()
        
        # ============================================================
        # Intentar descargar
        # ============================================================
        # Si descargar_workbook retorna True = OK
        # Si retorna False = falló
        
        if descargar_workbook(server, workbook_luid, ruta_local):
            estadisticas['descargados'] += 1
            # Calcular tiempo transcurrido en segundos
            tiempo = (datetime.now() - inicio).total_seconds()
            estadisticas['tiempos'][workbook_nombre] = tiempo
        else:
            estadisticas['errores'] += 1
    
    return estadisticas


def subir_github(directorio_base, config):
    """
    Sube los archivos descargados a GitHub usando Git.
    
    Esto ejecuta 3 comandos Git en secuencia:
    1. git add . → Agregar TODOS los cambios (archivos nuevos/modificados)
    2. git commit → Crear un "snapshot" con timestamp
    3. git push → Subir a GitHub
    
    IMPORTANTE: Git debe estar inicializado en esa carpeta de antemano.
    
    ¿Por qué es complicado?
    - Git es una herramienta externa (subprocess)
    - Necesita estar en el directorio correcto (os.chdir)
    - Cada comando puede fallar independientemente
    - Los errores pueden ser silenciosos
    
    Por eso:
    - Registramos logs de cada paso
    - Capturamos excepciones
    - Intentamos continuar aunque fallen pasos intermedios
    """
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        # ============================================================
        # Paso 1: Cambiar al directorio del proyecto
        # ============================================================
        # Git ejecuta comandos en el directorio actual
        # Por eso necesitamos estar en tableau_workbooks/
        
        os.chdir(directorio_base)
        
        # ============================================================
        # Paso 2: git add . (Agregar cambios)
        # ============================================================
        # El punto (.) significa "todos los archivos"
        # Git verá:
        #   - Archivos nuevos (los que descargamos)
        #   - Archivos modificados (los que actualizamos)
        #   - Archivos borrados (los que quitamos de la lista)
        
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        # ============================================================
        # Paso 3: git commit (Crear snapshot)
        # ============================================================
        # El commit es una "foto" de todos los cambios en un momento
        # El mensaje debe ser descriptivo
        # Usamos timestamp para que cada commit sea único
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"
        # Ejemplo: "Tableau Backup - 2025-01-17 10:30:45"
        
        logger.info("[GIT] git commit -m '%s'", mensaje)
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )
        
        # Verificar si había cambios para commitar
        # Si no hay cambios, Git avisa "nothing to commit" (no es error)
        if "nothing to commit" in resultado.stdout.lower():
            logger.info("[AVISO] No hay cambios que hacer commit")
            return  # Salir sin hacer push
        
        # ============================================================
        # Paso 4: git push (Subir a GitHub)
        # ============================================================
        # Envía los commits locales al repositorio remoto
        # 'origin' = nombre del servidor remoto
        # 'main' = rama en la que estamos
        
        logger.info("[GIT] git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        # CalledProcessError = el comando externo (git) devolvió error
        logger.error("[ERROR] Error en Git: %s", e)
    except Exception as e:
        logger.error("[ERROR] Error al subir: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """
    Muestra un resumen de la ejecución.
    
    Es importante para:
    - Validar que todo funcionó
    - Detectar problemas (si hay muchos errores)
    - Monitorear performance (si tarda mucho)
    - Reportes
    """
    
    logger.info("="*60)
    logger.info("REPORTE FINAL")
    logger.info("="*60)
    
    # Mostrar números simples
    logger.info("Total de workbooks:    %d", estadisticas['total'])
    logger.info("Descargados:           %d [OK]", estadisticas['descargados'])
    logger.info("Errores:               %d [ERROR]", estadisticas['errores'])
    
    # Calcular y mostrar porcentaje de éxito
    if estadisticas['total'] > 0:
        tasa = (estadisticas['descargados'] / estadisticas['total'] * 100)
        logger.info("Tasa de exito:         %.1f%%", tasa)
    
    # Mostrar tiempos
    logger.info("Tiempo total:          %.2fs", tiempo_total)
    
    # Calcular y mostrar tiempo promedio por workbook
    if estadisticas['tiempos']:
        promedio = sum(estadisticas['tiempos'].values()) / len(estadisticas['tiempos'])
        logger.info("Tiempo promedio/workbook: %.2fs", promedio)
    
    logger.info("="*60)


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def main():
    """
    Coordinador principal que orquesta TODO el proceso.
    
    Este es el "conductor de orquesta" que:
    1. Procesa argumentos de línea de comandos
    2. Carga configuración
    3. Limpia directorio
    4. Obtiene lista de workbooks
    5. Se conecta a Tableau
    6. Descarga workbooks
    7. Sube a GitHub
    8. Genera reporte
    
    El orden es IMPORTANTE porque cada paso depende de los anteriores.
    """
    
    # ============================================================
    # Paso 1: Procesar argumentos de línea de comandos
    # ============================================================
    # Permite al usuario personalizar el comportamiento sin editar el código
    
    parser = argparse.ArgumentParser(
        description='Descarga workbooks Tableau y sube a GitHub'
    )
    parser.add_argument(
        '--config',
        default='config.json',
        help='Archivo de configuracion (default: config.json)'
    )
    parser.add_argument(
        '--sin-github',
        action='store_true',
        help='Solo descargar, sin subir a GitHub'
    )
    parser.add_argument(
        '--forzar-excel',
        action='store_true',
        help='Forzar uso de Excel/TXT (sin SQL PLUS)'
    )
    
    args = parser.parse_args()
    
    # ============================================================
    # Paso 2: Cronometrar la ejecución TOTAL
    # ============================================================
    # Necesitamos saber cuánto tardó TODO el proceso
    
    inicio_total = datetime.now()
    
    # ============================================================
    # Paso 3: Cargar configuración
    # ============================================================
    # Sin esto, no sabemos URL de Tableau, credenciales, etc.
    
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    # ============================================================
    # Paso 4: Obtener directorio base
    # ============================================================
    # Es donde se descargarán todos los workbooks
    
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    
    # ============================================================
    # Paso 5: LIMPIAR DIRECTORIO (lo nuevo en esta versión)
    # ============================================================
    # Borra completamente tableau_workbooks y lo recrea vacío
    # Esto garantiza una "descarga limpia" cada vez
    
    limpiar_directorio(directorio_base)
    
    # ============================================================
    # Paso 6: Obtener datos (SQL PLUS o archivo)
    # ============================================================
    # Consigue la lista de workbooks a descargar
    
    df, fuente_datos = obtener_datos_inteligente(config, args.forzar_excel)
    
    # ============================================================
    # Paso 7: Autenticar en Tableau
    # ============================================================
    # Sin esto, no podemos descargar nada
    
    logger.info("="*60)
    logger.info("AUTENTICAR EN TABLEAU")
    logger.info("="*60)
    server = autenticar_tableau(config)
    
    # ============================================================
    # Paso 8: Mostrar información de la ejecución
    # ============================================================
    # Útil para debugging - confirma que todo está configurado bien
    
    logger.info("\n[DIRECTORIO] %s", directorio_base)
    logger.info("[FUENTE] %s", fuente_datos.upper())
    logger.info("[WORKBOOKS] %d para descargar", len(df))
    
    # ============================================================
    # Paso 9: Descargar todos los workbooks
    # ============================================================
    # Es el trabajo principal del script
    
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # ============================================================
    # Paso 10: Subir a GitHub (opcional, puede deshabilitarse)
    # ============================================================
    # Si el usuario no pasó --sin-github y está habilitado en config
    
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado o --sin-github especificado")
    
    # ============================================================
    # Paso 11: Cerrar sesión con Tableau
    # ============================================================
    # Buena práctica - libera recursos
    
    server.auth.sign_out()
    
    # ============================================================
    # Paso 12: Generar reporte final
    # ============================================================
    # Muestra resumen de lo que pasó
    
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


# ============================================================================
# PUNTO DE ENTRADA
# ============================================================================

if __name__ == '__main__':
    # Esto significa: "Si este archivo se ejecuta directamente (no se importa)"
    # Entonces ejecutar main()
    main()
