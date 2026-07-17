#!/usr/bin/env python3

"""
Script para descargar workbooks de Tableau desde SQL PLUS o archivo,
y subirlos automáticamente a GitHub.

Versión FINAL: Lee SQL desde archivo local + menos comentarios (pero suficientes)
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
    
    Importante: Esto garantiza que cada ejecución comienza con estado limpio.
    Intenta dos estrategias:
    1. Eliminar carpeta completa (más rápido)
    2. Si falla por permisos, limpiar archivo por archivo (fallback)
    """
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    # Intento 1: Eliminar la carpeta completa
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado completamente")
        except PermissionError as e:
            # Intento 2: Si no hay permisos, limpiar archivo por archivo
            logger.warning("[AVISO] Permiso denegado, limpiando contenido: %s", e)
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
    
    # Recrear carpeta vacía para las nuevas descargas
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def leer_consulta_sql(archivo_sql="consulta.sql"):
    """
    Lee la consulta SQL desde archivo local.
    
    IMPORTANTE: Esta es la NUEVA FUNCIONALIDAD.
    El archivo consulta.sql debe contener SOLO la consulta SQL,
    sin comandos SQL PLUS (SET FEEDBACK, etc). El script agrega 
    automáticamente esos comandos.
    
    Proceso:
    1. Verificar si consulta.sql existe en el directorio actual
    2. Si existe, leer y retornar la consulta
    3. Si no existe, retornar None (se usará fallback a archivo)
    
    Ejemplo de contenido de consulta.sql:
    SELECT * FROM DESCARGA_WORKBOOKS;
    """
    sql_file = Path(archivo_sql)
    
    if sql_file.exists():
        try:
            with open(sql_file, 'r', encoding='utf-8') as f:
                consulta = f.read().strip()
            if consulta:
                logger.info("[OK] Consulta SQL leída de: %s", archivo_sql)
                return consulta
        except Exception as e:
            logger.warning("[AVISO] Error al leer archivo SQL: %s", e)
    
    # Si no existe o hay error, retornar None para usar fallback
    logger.info("[INFO] Archivo SQL no encontrado: %s", archivo_sql)
    return None


def ejecutar_sqlplus(usuario, contraseña, dsn, consulta_sql):
    """
    Ejecuta una consulta SQL PLUS y retorna DataFrame con resultados.
    
    IMPORTANTE: SQL PLUS es una herramienta externa de Oracle (no conexión nativa).
    El proceso:
    1. Crear archivo SQL temporal con la consulta y configuración
    2. Ejecutar sqlplus como proceso externo (subprocess)
    3. Capturar la salida
    4. Parsear líneas (separadas por |)
    5. Convertir a DataFrame de pandas
    
    Configuración SQL PLUS:
    - SET FEEDBACK OFF: No mostrar "n rows selected"
    - SET PAGESIZE 0: No dividir en páginas
    - SET LINESIZE 1000: Líneas completas sin truncar
    - SET COLSEP |: Usar | como separador de columnas
    - SET HEADING ON: Mostrar nombres de columnas
    """
    try:
        logger.info("[SQLPLUS] Conectando con SQL PLUS...")
        
        # ============================================================
        # Crear archivo SQL temporal
        # ============================================================
        # Necesitamos un archivo porque SQL PLUS lo lee de stdin.
        # Lo hacemos temporal para no dejar archivos sueltos después.
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
            # Agregar configuración SQL PLUS
            f.write("SET FEEDBACK OFF\n")
            f.write("SET PAGESIZE 0\n")
            f.write("SET LINESIZE 1000\n")
            f.write("SET COLSEP |\n")
            f.write("SET HEADING ON\n")
            f.write("WHENEVER SQLERROR EXIT SQL.SQLCODE\n")
            
            # Agregar la consulta del usuario
            f.write(consulta_sql)
            f.write("\nEXIT;\n")  # Terminar sesión SQL PLUS
            
            archivo_sql = f.name
        
        # ============================================================
        # Ejecutar sqlplus como proceso externo
        # ============================================================
        # subprocess.run() ejecuta un programa externo.
        # -S = modo silencioso (no muestra banner de Oracle).
        # capture_output=True = capturar stdout y stderr.
        # timeout=60 = máximo 60 segundos (si tarda más, hay problema).
        
        conexion_string = f"{usuario}/{contraseña}@{dsn}"
        
        logger.info("[SQLPLUS] Ejecutando consulta...")
        resultado = subprocess.run(
            ['sqlplus', '-S', conexion_string],
            stdin=open(archivo_sql, encoding='utf-8'),
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Limpiar archivo temporal
        try:
            os.unlink(archivo_sql)
        except:
            pass
        
        # ============================================================
        # Verificar si la consulta tuvo éxito
        # ============================================================
        # returncode=0 significa que sqlplus terminó correctamente.
        # Si es distinto de 0, hubo error (credenciales, sintaxis SQL, etc.).
        
        if resultado.returncode != 0:
            logger.error("[ERROR] SQL PLUS error (código %d): %s", resultado.returncode, resultado.stderr)
            return None
        
        if not resultado.stdout.strip():
            logger.warning("[AVISO] SQL PLUS retornó sin datos")
            return None
        
        # ============================================================
        # Parsear la salida de SQL PLUS
        # ============================================================
        # SQL PLUS devuelve algo como:
        # WORKBOOK_LUID|WORKBOOK|RUTA_PROYECTO
        # a1b2c3d4|Dashboard_Ventas|Finance
        # b2c3d4e5|Dashboard_Budget|Finance
        #
        # Separamos por líneas, luego por |
        
        lineas = [l.strip() for l in resultado.stdout.strip().split('\n') if l.strip()]
        
        if len(lineas) < 2:
            logger.warning("[AVISO] No hay suficientes datos")
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
        # Convertir a DataFrame
        # ============================================================
        # DataFrame es como una tabla Excel en Python.
        # Mucho más fácil de manipular que listas.
        
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
    Lee archivo de datos (Excel, CSV, TXT, DSV).
    
    El objetivo es ser FLEXIBLE: soportar múltiples formatos y nombres de columnas.
    El usuario no tiene que modificar su archivo, el script lo entiende.
    
    Proceso principal:
    1. Detectar formato por extensión (.xlsx, .csv, .txt, .dsv)
    2. Leer con el parser aproppiado para ese formato
    3. Limpiar espacios y comillas (archivos suelen tener "suciedad")
    4. Buscar columnas FLEXIBLEMENTE (tolera nombres distintos)
    5. Validar que existan LAS COLUMNAS REQUERIDAS
    6. Renombrar a NOMBRES ESTÁNDAR
    7. Filtrar solo lo que debe descargarse (si existe columna DESCARGAR)
    """
    try:
        logger.info("[LEYENDO] Archivo: %s", ruta_archivo)
        
        # ============================================================
        # Paso 1: Detectar formato por extensión
        # ============================================================
        extension = Path(ruta_archivo).suffix.lower()
        
        # ============================================================
        # Paso 2: Leer con el parser aproppiado
        # ============================================================
        # Cada formato tiene sus particularidades:
        # - Excel (.xlsx): binario, complejo
        # - CSV: texto con comas, puede tener comillas
        # - TXT: texto con tabulaciones
        # - DSV: Delimited Separated Values, similar a CSV
        
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
        # Paso 3: Limpiar datos
        # ============================================================
        # A veces los archivos tienen espacios o comillas extra:
        # "  WORKBOOK_LUID  " → debería ser WORKBOOK_LUID
        # ""Dashboard"" → debería ser Dashboard
        
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        for col in df.columns:
            if df[col].dtype == 'object':  # 'object' = texto en pandas
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        # ============================================================
        # Paso 4: Buscar columnas flexiblemente
        # ============================================================
        # Los usuarios pueden nombrar las columnas de forma distinta.
        # Estrategia de DOS FASES:
        # 1. Búsqueda EXACTA (WORKBOOK_LUID == WORKBOOK_LUID)
        # 2. Búsqueda PARCIAL (contiene la palabra clave)
        # 
        # Con validaciones para evitar falsas coincidencias.
        # Por ejemplo: si buscamos "WORKBOOK", no queremos "WORKBOOK_LUID".
        
        def buscar_columna(df, patrones):
            # FASE 1: Búsqueda exacta (más preciso)
            for col in df.columns:
                col_limpio = col.strip().upper().replace('"', '')
                for patron in patrones:
                    if col_limpio == patron.upper():
                        logger.info("[MAPEO] Columna '%s' mapeada a '%s'", col, patron)
                        return col
            
            # FASE 2: Búsqueda parcial (fallback)
            for col in df.columns:
                col_limpio = col.strip().upper()
                for patron in patrones:
                    if patron.upper() in col_limpio:
                        # Validaciones para evitar falsas coincidencias
                        if patron.upper() == 'WORKBOOK' and 'LUID' in col_limpio:
                            continue
                        if patron.upper() == 'RUTA' and 'LOCAL' in col_limpio:
                            continue
                        logger.info("[MAPEO] Columna '%s' mapeada parcialmente a '%s'", col, patron)
                        return col
            return None
        
        # Buscar cada columna requerida
        col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID', 'ID'])
        col_nombre = buscar_columna(df, ['WORKBOOK', 'NOMBRE', 'NAME'])
        col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA', 'PROJECT'])
        col_descargar = buscar_columna(df, ['DESCARGAR', 'DOWNLOAD', 'ACTIVO', 'ACTIVE'])
        
        # ============================================================
        # Paso 5: Validar que existan TODAS las columnas requeridas
        # ============================================================
        # Sin estas columnas no podemos hacer nada.
        # Es mejor fallar AQUÍ que después con errores confusos.
        
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
        # Paso 6: Renombrar a NOMBRES ESTÁNDAR
        # ============================================================
        # El resto del script ESPERA columnas con estos nombres exactos.
        
        df = df.rename(columns={
            col_luid: 'WORKBOOK_LUID',
            col_nombre: 'WORKBOOK',
            col_ruta: 'RUTA_PROYECTO'
        })
        
        # ============================================================
        # Paso 7: Filtrar solo lo que debe descargarse
        # ============================================================
        # Si existe columna DESCARGAR, usarla para filtrar.
        # Solo descargar si DESCARGAR = TRUE, SI, 1, etc.
        
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
    Obtiene lista de workbooks con estrategia de FALLBACK.
    
    ESTRATEGIA:
    1. Intenta leer consulta.sql local (NUEVA FUNCIONALIDAD)
    2. Si existe, ejecuta SQL PLUS con esa consulta
    3. Si SQL PLUS falla, intenta archivo local (workbooks.txt)
    4. Retorna los datos y la FUENTE para logging
    
    Parámetro --forzar-excel salta directamente a archivo (útil para testing).
    """
    logger.info("="*60)
    logger.info("OBTENER DATOS")
    logger.info("="*60)
    
    # ============================================================
    # Si el usuario pasó --forzar-excel, saltarse SQL PLUS
    # ============================================================
    if forzar_excel:
        logger.info("[FORZANDO] Usando archivo de datos (--forzar-excel)")
        archivo_datos = config.get('archivo_datos', 'workbooks.txt')
        df = leer_archivo_datos(archivo_datos)
        return df, "ARCHIVO_DATOS"
    
    # ============================================================
    # Intenta leer consulta SQL local y ejecutar SQL PLUS
    # ============================================================
    consulta = leer_consulta_sql("consulta.sql")
    
    if consulta:
        try:
            # ¡IMPORTANTE! La consulta viene del archivo local
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
            # KeyError = falta parámetro en config
            logger.warning("[AVISO] Config incompleta: %s", e)
        except Exception as e:
            logger.error("[ERROR] SQL PLUS falló: %s", e)
    
    # ============================================================
    # FALLBACK: usar archivo local como respaldo
    # ============================================================
    # Si no existe consulta.sql o SQL PLUS falla,
    # leer la lista de workbooks desde archivo local.
    logger.warning("[FALLBACK] Usando archivo de datos")
    archivo_datos = config.get('archivo_datos', 'workbooks.txt')
    df = leer_archivo_datos(archivo_datos)
    return df, "ARCHIVO_DATOS"


def autenticar_tableau(config):
    """
    Autentica en Tableau Server usando PAT (Personal Access Token).
    
    PAT es más seguro que usuario/contraseña:
    - No expone credenciales de usuario
    - Puedes revocar token sin cambiar contraseña
    - Cada token puede tener permisos limitados
    """
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
    
    PROBLEMA SOLUCIONADO: TSC crea una carpeta extra.
    
    Cuando descargas "Dashboard_Ventas.twbx", crea:
    Dashboard_Ventas.twbx/ (carpeta)
        └── Dashboard_Ventas.twbx (archivo dentro)
    
    SOLUCIÓN: Descargar sin extensión .twbx
    - Descargar como "Dashboard_Ventas" (sin .twbx)
    - TSC crea: Dashboard_Ventas/ (carpeta)
    - Dentro está: Dashboard_Ventas.twbx (archivo)
    - Mover archivo a ubicación final
    - Borrar carpeta temporal
    """
    try:
        ruta_destino = Path(ruta_destino)
        
        # Crear carpeta destino si no existe
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[DESCARGANDO] %s", workbook_luid)
        
        # ============================================================
        # EL TRUCO: Descargar SIN la extensión .twbx
        # ============================================================
        # ruta_destino.stem quita la extensión.
        # Finance/Dashboard_Ventas.twbx → Finance/Dashboard_Ventas
        
        ruta_temporal = str(ruta_destino.parent / ruta_destino.stem)
        server.workbooks.download(workbook_luid, filepath=ruta_temporal)
        
        # Procesar el archivo descargado
        carpeta_descargada = Path(ruta_temporal)
        
        if carpeta_descargada.is_dir():
            # TSC creó una carpeta, buscar el .twbx dentro
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
                logger.error("[ERROR] No se encontró .twbx dentro de carpeta")
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
    
    Registra estadísticas:
    - Cuántos se descargaron OK
    - Cuántos fallaron
    - Cuánto tiempo tardó cada uno
    
    Estas estadísticas se usan después para el reporte final.
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
    
    # Loop: Iterar sobre cada fila del DataFrame
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
        
        # Intentar descargar
        if descargar_workbook(server, workbook_luid, ruta_local):
            estadisticas['descargados'] += 1
            tiempo = (datetime.now() - inicio).total_seconds()
            estadisticas['tiempos'][workbook_nombre] = tiempo
        else:
            estadisticas['errores'] += 1
    
    return estadisticas


def subir_github(directorio_base, config):
    """
    Sube los cambios a GitHub: git add . → git commit → git push
    
    IMPORTANTE: Git debe estar inicializado en la carpeta de antemano.
    
    Proceso:
    1. Cambiar al directorio del proyecto
    2. git add . (agregar TODOS los cambios)
    3. git commit (crear snapshot con timestamp)
    4. git push (enviar a GitHub)
    """
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        os.chdir(directorio_base)
        
        # ============================================================
        # git add . (agregar cambios)
        # ============================================================
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        # ============================================================
        # git commit (crear snapshot)
        # ============================================================
        # El commit es una "foto" de todos los cambios en un momento específico.
        # Usamos timestamp para que cada commit sea único y identificable.
        
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
        
        # ============================================================
        # git push (enviar a GitHub)
        # ============================================================
        logger.info("[GIT] git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        logger.error("[ERROR] Error en Git: %s", e)
    except Exception as e:
        logger.error("[ERROR] Error al subir: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """
    Muestra resumen final de la ejecución.
    
    Información útil para:
    - Validar que todo funcionó
    - Detectar si hay muchos errores
    - Optimizar si tarda mucho
    - Reporting automático
    """
    
    logger.info("="*60)
    logger.info("REPORTE FINAL")
    logger.info("="*60)
    
    logger.info("Total de workbooks:    %d", estadisticas['total'])
    logger.info("Descargados:           %d [OK]", estadisticas['descargados'])
    logger.info("Errores:               %d [ERROR]", estadisticas['errores'])
    
    # Calcular y mostrar porcentaje de éxito
    if estadisticas['total'] > 0:
        tasa = (estadisticas['descargados'] / estadisticas['total'] * 100)
        logger.info("Tasa de exito:         %.1f%%", tasa)
    
    logger.info("Tiempo total:          %.2fs", tiempo_total)
    
    # Calcular tiempo promedio por workbook
    if estadisticas['tiempos']:
        promedio = sum(estadisticas['tiempos'].values()) / len(estadisticas['tiempos'])
        logger.info("Tiempo promedio/workbook: %.2fs", promedio)
    
    logger.info("="*60)


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def main():
    """
    Orquestador principal que coordina TODA la ejecución.
    
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
    
    # ============================================================
    # Paso 1: Procesar argumentos de línea de comandos
    # ============================================================
    # Permite personalizar el comportamiento sin editar código.
    
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
    
    # ============================================================
    # Paso 2: Cargar configuración
    # ============================================================
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    # ============================================================
    # Paso 3: Limpiar directorio
    # ============================================================
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    limpiar_directorio(directorio_base)
    
    # ============================================================
    # Paso 4: Obtener datos (SQL PLUS o archivo)
    # ============================================================
    df, fuente_datos = obtener_datos_inteligente(config, args.forzar_excel)
    
    # ============================================================
    # Paso 5: Autenticar en Tableau
    # ============================================================
    logger.info("="*60)
    logger.info("AUTENTICAR EN TABLEAU")
    logger.info("="*60)
    server = autenticar_tableau(config)
    
    # ============================================================
    # Mostrar información de la ejecución
    # ============================================================
    logger.info("\n[DIRECTORIO] %s", directorio_base)
    logger.info("[FUENTE] %s", fuente_datos.upper())
    logger.info("[WORKBOOKS] %d para descargar", len(df))
    
    # ============================================================
    # Paso 6: Descargar workbooks
    # ============================================================
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # ============================================================
    # Paso 7: Subir a GitHub (opcional)
    # ============================================================
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado o --sin-github especificado")
    
    # ============================================================
    # Cerrar sesión con Tableau
    # ============================================================
    server.auth.sign_out()
    
    # ============================================================
    # Paso 8: Generar reporte final
    # ============================================================
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


# ============================================================================
# PUNTO DE ENTRADA
# ============================================================================

if __name__ == '__main__':
    # Esto significa: "Si este archivo se ejecuta directamente, ejecutar main()"
    main()
