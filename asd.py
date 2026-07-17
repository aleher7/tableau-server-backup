#!/usr/bin/env python3

"""
Script para descargar workbooks de Tableau desde SQL PLUS o archivo,
y subirlos automáticamente a GitHub.

Versión MEJORADA: Limpia carpeta + SQL PLUS integrado
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
# FUNCIONES
# ============================================================================

def cargar_config(config_file="config.json"):
    """Carga la configuración desde JSON"""
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
    Elimina completamente la carpeta de descargas y la recrea vacía.
    
    Estrategia:
    1. Intenta eliminar la carpeta completa (más rápido)
    2. Si falla por permisos, limpia archivo por archivo (fallback)
    3. Recrea la carpeta vacía
    """
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    # Intento 1: Eliminar carpeta completamente
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado completamente")
        except PermissionError as e:
            logger.warning("[AVISO] Permiso denegado, intentando limpiar contenido: %s", e)
            
            # Intento 2: Limpiar archivo por archivo (fallback)
            try:
                for archivo in ruta.rglob('*'):
                    try:
                        if archivo.is_file():
                            archivo.unlink()
                        elif archivo.is_dir() and archivo != ruta:
                            shutil.rmtree(archivo)
                    except Exception as ex:
                        logger.warning("[AVISO] No se pudo borrar: %s", ex)
                logger.info("[OK] Contenido del directorio limpiado")
            except Exception as ex:
                logger.error("[ERROR] No se pudo limpiar: %s", ex)
        except Exception as e:
            logger.error("[ERROR] Error al eliminar directorio: %s", e)
    
    # Recrear carpeta vacía
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def ejecutar_sqlplus(usuario, contraseña, dsn, consulta_sql):
    """
    Ejecuta una consulta SQL PLUS y retorna DataFrame con resultados.
    
    IMPORTANTE: SQL PLUS es una herramienta de línea de comandos de Oracle.
    Se ejecuta como un proceso externo, no como conexión nativa Python.
    
    Pasos:
    1. Crear archivo SQL temporal con la consulta y configuración SQL PLUS
    2. Ejecutar sqlplus como proceso externo (subprocess)
    3. Capturar salida estándar (stdout)
    4. Parsear salida línea por línea (separadas por |)
    5. Convertir a DataFrame de pandas
    
    Configuración SQL PLUS usada:
    - SET FEEDBACK OFF: No mostrar "n rows selected"
    - SET PAGESIZE 0: No dividir en páginas
    - SET LINESIZE 1000: Líneas completas sin truncar
    - SET COLSEP |: Usar | como separador de columnas
    - SET HEADING ON: Mostrar nombres de columnas
    """
    try:
        logger.info("[SQLPLUS] Conectando con SQL PLUS...")
        
        # ============================================================
        # PASO 1: Crear archivo SQL temporal con configuración
        # ============================================================
        # Necesitamos un archivo porque SQL PLUS lo lee de stdin
        # Lo hacemos temporal (se borra después) para no dejar archivos sueltos
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
            # Configuración SQL PLUS (afecta formato de salida)
            f.write("SET FEEDBACK OFF\n")
            f.write("SET PAGESIZE 0\n")
            f.write("SET LINESIZE 1000\n")
            f.write("SET COLSEP |\n")
            f.write("SET HEADING ON\n")
            f.write("WHENEVER SQLERROR EXIT SQL.SQLCODE\n")
            
            # La consulta que el usuario quiere ejecutar
            f.write(consulta_sql)
            f.write("\nEXIT;\n")  # Terminar sesión SQL PLUS
            
            archivo_sql = f.name
        
        # ============================================================
        # PASO 2: Ejecutar sqlplus como proceso externo
        # ============================================================
        # subprocess.run() ejecuta un programa externo
        # -S = modo silencioso (no muestra banner de Oracle)
        # stdin = archivo que SQL PLUS leerá
        # capture_output = True para capturar la salida
        # timeout = máximo 60 segundos (si tarda más, hay problema)
        
        conexion_string = f"{usuario}/{contraseña}@{dsn}"
        
        logger.info("[SQLPLUS] Ejecutando consulta...")
        resultado = subprocess.run(
            ['sqlplus', '-S', conexion_string],
            stdin=open(archivo_sql, encoding='utf-8'),
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Limpiar archivo temporal (ya no lo necesitamos)
        try:
            os.unlink(archivo_sql)
        except:
            pass
        
        # ============================================================
        # PASO 3: Verificar si la consulta tuvo éxito
        # ============================================================
        # returncode=0 significa que sqlplus terminó correctamente
        # Si es distinto de 0, hubo error (credenciales, sintaxis SQL, etc.)
        
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
        # Separamos por líneas, luego por |
        
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
        # DataFrame es como una tabla Excel en Python
        # Mucho más fácil de manipular que listas
        
        df = pd.DataFrame(datos, columns=encabezados)
        logger.info("[OK] Datos obtenidos de SQL PLUS: %d filas", len(df))
        
        return df
        
    except subprocess.TimeoutExpired:
        logger.error("[ERROR] SQL PLUS timeout (>60 segundos)")
        return None
    except FileNotFoundError:
        logger.error("[ERROR] SQL PLUS no encontrado. Verifica instalación y PATH")
        return None
    except Exception as e:
        logger.error("[ERROR] Error en SQL PLUS: %s", e)
        return None


def leer_archivo_datos(ruta_archivo):
    """
    Lee archivo de datos en múltiples formatos (Excel, CSV, TXT, DSV).
    
    Estos archivos pueden tener diferentes formatos y nombres de columnas.
    El objetivo es hacer que el script sea flexible sin que el usuario tenga
    que modificar su archivo.
    
    LÓGICA PRINCIPAL:
    1. Detectar formato por extensión (.xlsx, .csv, .txt, etc.)
    2. Leer con el parser aproppiado para ese formato
    3. Limpiar espacios y comillas (archivos frecuentemente tienen "suciedad")
    4. Buscar columnas FLEXIBLEMENTE
       - Intenta coincidencia exacta (WORKBOOK_LUID == WORKBOOK_LUID)
       - Si no, intenta parcial (contiene la palabra clave)
       - Evita falsas coincidencias (ej: no confundir WORKBOOK con WORKBOOK_LUID)
    5. Validar que existan LAS COLUMNAS REQUERIDAS
    6. Renombrar a NOMBRES ESTÁNDAR (el resto del script espera esto)
    7. Filtrar solo lo que debe descargarse
    
    RESULTADO: Siempre retorna DataFrame con columnas estándar:
    - WORKBOOK_LUID (ID único)
    - WORKBOOK (nombre)
    - RUTA_PROYECTO (carpeta destino)
    """
    try:
        logger.info("[LEYENDO] Archivo: %s", ruta_archivo)
        
        # ============================================================
        # PASO 1: Detectar formato por extensión
        # ============================================================
        extension = Path(ruta_archivo).suffix.lower()
        
        # ============================================================
        # PASO 2: Leer con el parser aproppiado
        # ============================================================
        # Cada formato tiene sus peculiaridades:
        # - Excel (.xlsx): binario, complejo
        # - CSV: texto con comas, puede tener comillas
        # - TXT: texto con tabulaciones
        # - DSV: similar a CSV pero más robusto
        
        if extension == '.dsv':
            df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', 
                           doublequote=True, skipinitialspace=True, on_bad_lines='skip')
        elif extension in ['.xlsx', '.xls']:
            df = pd.read_excel(ruta_archivo)
        elif extension == '.csv':
            df = pd.read_csv(ruta_archivo, sep=',', quotechar='"',
                           doublequote=True, skipinitialspace=True, on_bad_lines='skip')
        else:
            logger.warning("[AVISO] Extension no reconocida, intentando como TXT")
            df = pd.read_csv(ruta_archivo, sep='\t', on_bad_lines='skip')
        
        logger.info("[OK] Archivo cargado: %d filas", len(df))
        
        # ============================================================
        # PASO 3: Limpiar espacios y comillas
        # ============================================================
        # A veces los archivos tienen basura:
        # "  WORKBOOK_LUID  " → debería ser WORKBOOK_LUID
        # ""Dashboard"" → debería ser Dashboard
        
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        for col in df.columns:
            if df[col].dtype == 'object':  # 'object' = texto en pandas
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        # ============================================================
        # PASO 4: Buscar columnas flexiblemente
        # ============================================================
        # Los usuarios pueden nombrar las columnas de forma distinta.
        # Necesitamos que funcione con:
        #   WORKBOOK_LUID, workbook_luid, LUID, ID, etc.
        #
        # Estrategia de DOS FASES:
        # 1. Búsqueda EXACTA (más preciso)
        # 2. Búsqueda PARCIAL (si no encuentra exacta)
        #
        # IMPORTANTE: Tenemos validaciones para evitar falsas coincidencias.
        # Por ejemplo, si buscamos "WORKBOOK", no queremos "WORKBOOK_LUID".
        
        def buscar_columna(df, patrones):
            # FASE 1: Búsqueda exacta (más preciso)
            for col in df.columns:
                col_limpio = col.strip().upper().replace('"', '')
                for patron in patrones:
                    if col_limpio == patron.upper():
                        logger.info("[MAPEO] Columna '%s' mapeada a '%s'", col, patron)
                        return col
            
            # FASE 2: Búsqueda parcial (fallback)
            # Aquí buscamos si el nombre del patrón está DENTRO del nombre de la columna
            for col in df.columns:
                col_limpio = col.strip().upper()
                for patron in patrones:
                    if patron.upper() in col_limpio:
                        # VALIDACIONES para evitar falsas coincidencias
                        if patron.upper() == 'WORKBOOK' and 'LUID' in col_limpio:
                            continue  # No confundir WORKBOOK con WORKBOOK_LUID
                        if patron.upper() == 'RUTA' and 'LOCAL' in col_limpio:
                            continue  # No confundir RUTA con RUTA_LOCAL
                        logger.info("[MAPEO] Columna '%s' mapeada parcialmente a '%s'", col, patron)
                        return col
            return None
        
        # Buscar cada columna requerida
        col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID', 'ID'])
        col_nombre = buscar_columna(df, ['WORKBOOK', 'NOMBRE', 'NAME'])
        col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA', 'PROJECT'])
        col_descargar = buscar_columna(df, ['DESCARGAR', 'DOWNLOAD', 'ACTIVO', 'ACTIVE'])
        
        # ============================================================
        # PASO 5: Validar que existan TODAS las columnas requeridas
        # ============================================================
        # Sin estas, no podemos hacer nada útil
        # Es mejor fallar AQUÍ que después con errores confusos
        
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
        # PASO 6: Renombrar a NOMBRES ESTÁNDAR
        # ============================================================
        # El resto del script ESPERA columnas con estos nombres exactos.
        # Ahora renombramos las que encontramos a esos nombres estándar.
        
        df = df.rename(columns={
            col_luid: 'WORKBOOK_LUID',
            col_nombre: 'WORKBOOK',
            col_ruta: 'RUTA_PROYECTO'
        })
        
        # ============================================================
        # PASO 7: Filtrar solo lo que debe descargarse
        # ============================================================
        # Si existe una columna "DESCARGAR", usarla para filtrar.
        # Solo descargamos filas donde DESCARGAR sea TRUE, SI, 1, etc.
        
        if col_descargar:
            df = df.rename(columns={col_descargar: 'DESCARGAR'})
            df = df[df['DESCARGAR'].astype(str).str.upper().isin(['SÍ', 'SI', '1', 'TRUE', 'Y', 'YES'])]
            logger.info("[FILTRADO] Workbooks para descargar: %d", len(df))
        
        return df
        
    except Exception as e:
        logger.error("[ERROR] Error al leer archivo: %s", e)
        sys.exit(1)


def obtener_datos_inteligente(config, forzar_excel=False):
    """
    Obtiene lista de workbooks: intenta SQL PLUS, fallback a archivo.
    
    ESTRATEGIA CON FALLBACK:
    1. Intenta SQL PLUS (datos en tiempo real de la BD)
       - Si funciona → retorna datos frescos
       - Si falla → intenta archivo como respaldo
    2. Fallback a archivo (datos estáticos)
       - Más lento que BD, pero funciona aunque Oracle esté caído
    
    El parámetro --forzar-excel saltea SQL PLUS directamente
    (útil para testing o emergencias).
    """
    logger.info("="*60)
    logger.info("OBTENER DATOS")
    logger.info("="*60)
    
    # Si fuerza archivo, saltarse SQL PLUS directamente
    if forzar_excel:
        logger.info("[FORZANDO] Usando archivo de datos (--forzar-excel)")
        archivo_datos = config.get('archivo_datos', 'workbooks.txt')
        df = leer_archivo_datos(archivo_datos)
        return df, "ARCHIVO_DATOS"
    
    # Intentar SQL PLUS (datos en tiempo real)
    try:
        consulta = config.get('sqlplus_query', 'SELECT * FROM DESCARGA_WORKBOOKS')
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
        logger.warning("[AVISO] Config incompleta para SQL PLUS: %s", e)
    except Exception as e:
        logger.error("[ERROR] SQL PLUS falló: %s", e)
    
    # FALLBACK: Usar archivo como respaldo
    logger.warning("[FALLBACK] Usando archivo de datos")
    archivo_datos = config.get('archivo_datos', 'workbooks.txt')
    df = leer_archivo_datos(archivo_datos)
    return df, "ARCHIVO_DATOS"


def autenticar_tableau(config):
    """Autentica en Tableau Server usando PAT (Personal Access Token)"""
    try:
        logger.info("[AUTENTICANDO] Tableau...")
        
        tableau_auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site']
        )
        
        server = TSC.Server(config['tableau_server'])
        server.auth.sign_in(tableau_auth)
        
        logger.info("[OK] Autenticado en Tableau")
        return server
        
    except Exception as e:
        logger.error("[ERROR] Error al autenticar: %s", e)
        sys.exit(1)


def descargar_workbook(server, workbook_luid, ruta_destino):
    """
    Descarga UN workbook de Tableau Server.
    
    PROBLEMA SOLUCIONADO:
    Cuando descargas un workbook "Dashboard_Ventas.twbx", Tableau Server
    Client (TSC) crea una carpeta extra en lugar del archivo directo:
    
    ANTES (incorrecto):
      Finance/
        └── Dashboard_Ventas.twbx/    ❌ Es una CARPETA
            └── Dashboard_Ventas.twbx ❌ Archivo dentro
    
    DESPUÉS (correcto):
      Finance/
        └── Dashboard_Ventas.twbx     ✅ Archivo directo
    
    SOLUCIÓN:
    1. Descargar SIN la extensión .twbx
       → TSC crea carpeta (Dashboard_Ventas)
       → Dentro hay: Dashboard_Ventas.twbx
    2. Mover el archivo a la ubicación final
    3. Borrar la carpeta temporal
    """
    try:
        ruta_destino = Path(ruta_destino)
        
        # Crear carpeta destino si no existe
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[DESCARGANDO] %s", workbook_luid)
        
        # ============================================================
        # EL TRUCO: Descargar SIN la extensión
        # ============================================================
        # ruta_destino.stem quita la extensión
        # Finance/Dashboard_Ventas.twbx → Finance/Dashboard_Ventas
        
        ruta_temporal = str(ruta_destino.parent / ruta_destino.stem)
        
        # Como no tiene .twbx, TSC crea una carpeta
        server.workbooks.download(workbook_luid, filepath=ruta_temporal)
        
        # Procesar el archivo descargado
        carpeta_descargada = Path(ruta_temporal)
        
        if carpeta_descargada.is_dir():
            archivos_twbx = list(carpeta_descargada.glob('*.twbx'))
            
            if archivos_twbx:
                # Mover archivo a ubicación final
                shutil.move(str(archivos_twbx[0]), str(ruta_destino))
                
                # Limpiar carpeta temporal
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
            # Versiones nuevas de TSC descargan directamente sin carpeta
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
    Descarga todos los workbooks del DataFrame.
    
    IMPORTANTE:
    - Itera sobre cada fila del DataFrame (cada workbook)
    - Registra estadísticas: cuántos OK, cuántos fallaron, tiempos
    - Las estadísticas se usan después para el reporte
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
    
    # Loop: iterar sobre cada fila del DataFrame
    for contador, (idx, fila) in enumerate(df.iterrows(), 1):
        workbook_luid = str(fila['WORKBOOK_LUID']).strip()
        workbook_nombre = str(fila['WORKBOOK']).strip()
        ruta_proyecto = str(fila['RUTA_PROYECTO']).strip()
        
        # Construir ruta local completa
        ruta_local = Path(directorio_base) / ruta_proyecto / f"{workbook_nombre}.twbx"
        
        logger.info("\n[%d/%d] %s", contador, len(df), workbook_nombre)
        logger.info("       Proyecto: %s", ruta_proyecto)
        logger.info("       LUID: %s", workbook_luid)
        
        # Medir tiempo de descarga
        inicio = datetime.now()
        
        if descargar_workbook(server, workbook_luid, ruta_local):
            estadisticas['descargados'] += 1
            tiempo = (datetime.now() - inicio).total_seconds()
            estadisticas['tiempos'][workbook_nombre] = tiempo
        else:
            estadisticas['errores'] += 1
    
    return estadisticas


def subir_github(directorio_base, config):
    """
    Ejecuta los comandos Git: git add . → git commit → git push
    
    PASOS:
    1. Cambiar al directorio del proyecto
    2. git add . (agregar cambios)
    3. git commit (crear snapshot)
    4. git push (enviar a GitHub)
    """
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        os.chdir(directorio_base)
        
        # git add .
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        # git commit
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"
        
        logger.info("[GIT] git commit -m '%s'", mensaje)
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )
        
        # Si no hay cambios, Git lo avisa (no es error)
        if "nothing to commit" in resultado.stdout.lower():
            logger.info("[AVISO] No hay cambios que hacer commit")
            return
        
        # git push
        logger.info("[GIT] git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        logger.error("[ERROR] Error en Git: %s", e)
    except Exception as e:
        logger.error("[ERROR] Error al subir: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """Muestra resumen final de la ejecución"""
    
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
        logger.info("Tiempo promedio/workbook: %.2fs", promedio)
    
    logger.info("="*60)


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def main():
    """
    Orquestador principal que coordina TODO el proceso.
    
    ORDEN DE EJECUCIÓN (importante porque cada paso depende del anterior):
    1. Procesar argumentos de línea de comandos
    2. Cargar configuración
    3. Limpiar directorio de descargas
    4. Obtener lista de workbooks (SQL PLUS o archivo)
    5. Autenticar en Tableau Server
    6. Descargar todos los workbooks
    7. Subir a GitHub (opcional)
    8. Generar reporte final
    """
    
    # Procesar argumentos
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
    
    inicio_total = datetime.now()
    
    # Cargar configuración
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    # Obtener directorio base
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    
    # Limpiar directorio
    limpiar_directorio(directorio_base)
    
    # Obtener datos
    df, fuente_datos = obtener_datos_inteligente(config, args.forzar_excel)
    
    # Autenticar en Tableau
    logger.info("="*60)
    logger.info("AUTENTICAR EN TABLEAU")
    logger.info("="*60)
    server = autenticar_tableau(config)
    
    # Información de ejecución
    logger.info("\n[DIRECTORIO] %s", directorio_base)
    logger.info("[FUENTE] %s", fuente_datos.upper())
    logger.info("[WORKBOOKS] %d para descargar", len(df))
    
    # Descargar workbooks
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # Subir a GitHub (opcional)
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado o --sin-github especificado")
    
    # Cerrar sesión
    server.auth.sign_out()
    
    # Reporte final
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


# ============================================================================
# PUNTO DE ENTRADA
# ============================================================================

if __name__ == '__main__':
    main()
