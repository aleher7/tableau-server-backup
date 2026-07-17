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
    """Elimina la carpeta de descargas y la recrea vacía."""
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado completamente")
        except PermissionError as e:
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
    
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def leer_consulta_sql(archivo_sql="consulta.sql"):
    """
    Lee la consulta SQL desde archivo local.
    
    Intenta:
    1. Leer archivo .sql en el mismo directorio
    2. Si no existe, retorna None
    
    El archivo debe contener la consulta SQL PLUS sin comandos de conexión.
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
    
    logger.info("[INFO] Archivo SQL no encontrado: %s", archivo_sql)
    return None


def ejecutar_sqlplus(usuario, contraseña, dsn, consulta_sql):
    """
    Ejecuta una consulta SQL PLUS y retorna DataFrame.
    
    SQL PLUS es herramienta externa de Oracle (subprocess).
    
    Proceso:
    1. Crear archivo SQL temporal
    2. Ejecutar sqlplus
    3. Capturar salida
    4. Parsear líneas (separadas por |)
    5. Convertir a DataFrame
    """
    try:
        logger.info("[SQLPLUS] Conectando con SQL PLUS...")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
            f.write("SET FEEDBACK OFF\n")
            f.write("SET PAGESIZE 0\n")
            f.write("SET LINESIZE 1000\n")
            f.write("SET COLSEP |\n")
            f.write("SET HEADING ON\n")
            f.write("WHENEVER SQLERROR EXIT SQL.SQLCODE\n")
            f.write(consulta_sql)
            f.write("\nEXIT;\n")
            archivo_sql = f.name
        
        conexion_string = f"{usuario}/{contraseña}@{dsn}"
        
        logger.info("[SQLPLUS] Ejecutando consulta...")
        resultado = subprocess.run(
            ['sqlplus', '-S', conexion_string],
            stdin=open(archivo_sql, encoding='utf-8'),
            capture_output=True,
            text=True,
            timeout=60
        )
        
        try:
            os.unlink(archivo_sql)
        except:
            pass
        
        if resultado.returncode != 0:
            logger.error("[ERROR] SQL PLUS error (código %d): %s", resultado.returncode, resultado.stderr)
            return None
        
        if not resultado.stdout.strip():
            logger.warning("[AVISO] SQL PLUS retornó sin datos")
            return None
        
        lineas = [l.strip() for l in resultado.stdout.strip().split('\n') if l.strip()]
        
        if len(lineas) < 2:
            logger.warning("[AVISO] No hay suficientes datos")
            return None
        
        encabezados = [col.strip() for col in lineas[0].split('|')]
        datos = []
        
        for linea in lineas[1:]:
            if linea.strip():
                valores = [val.strip() for val in linea.split('|')]
                datos.append(valores)
        
        if not datos:
            logger.warning("[AVISO] No hay datos en la tabla")
            return None
        
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
    
    Proceso:
    1. Detectar formato
    2. Leer archivo
    3. Limpiar datos
    4. Buscar columnas flexiblemente
    5. Validar columnas requeridas
    6. Renombrar a estándar
    7. Filtrar por DESCARGAR
    """
    try:
        logger.info("[LEYENDO] Archivo: %s", ruta_archivo)
        
        extension = Path(ruta_archivo).suffix.lower()
        
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
        
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        def buscar_columna(df, patrones):
            # Búsqueda exacta
            for col in df.columns:
                col_limpio = col.strip().upper().replace('"', '')
                for patron in patrones:
                    if col_limpio == patron.upper():
                        logger.info("[MAPEO] Columna '%s' mapeada a '%s'", col, patron)
                        return col
            
            # Búsqueda parcial
            for col in df.columns:
                col_limpio = col.strip().upper()
                for patron in patrones:
                    if patron.upper() in col_limpio:
                        if patron.upper() == 'WORKBOOK' and 'LUID' in col_limpio:
                            continue
                        if patron.upper() == 'RUTA' and 'LOCAL' in col_limpio:
                            continue
                        logger.info("[MAPEO] Columna '%s' mapeada parcialmente a '%s'", col, patron)
                        return col
            return None
        
        col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID', 'ID'])
        col_nombre = buscar_columna(df, ['WORKBOOK', 'NOMBRE', 'NAME'])
        col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA', 'PROJECT'])
        col_descargar = buscar_columna(df, ['DESCARGAR', 'DOWNLOAD', 'ACTIVO', 'ACTIVE'])
        
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
        
        df = df.rename(columns={
            col_luid: 'WORKBOOK_LUID',
            col_nombre: 'WORKBOOK',
            col_ruta: 'RUTA_PROYECTO'
        })
        
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
    Obtiene lista de workbooks: SQL PLUS (archivo local) → fallback a archivo.
    
    ESTRATEGIA:
    1. Intenta leer consulta.sql local
    2. Si existe, ejecuta SQL PLUS con esa consulta
    3. Si falla, intenta archivo local (workbooks.txt)
    """
    logger.info("="*60)
    logger.info("OBTENER DATOS")
    logger.info("="*60)
    
    if forzar_excel:
        logger.info("[FORZANDO] Usando archivo de datos (--forzar-excel)")
        archivo_datos = config.get('archivo_datos', 'workbooks.txt')
        df = leer_archivo_datos(archivo_datos)
        return df, "ARCHIVO_DATOS"
    
    # Intenta leer consulta SQL local
    consulta = leer_consulta_sql("consulta.sql")
    
    if consulta:
        try:
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
            logger.warning("[AVISO] Config incompleta: %s", e)
        except Exception as e:
            logger.error("[ERROR] SQL PLUS falló: %s", e)
    
    # FALLBACK: usar archivo
    logger.warning("[FALLBACK] Usando archivo de datos")
    archivo_datos = config.get('archivo_datos', 'workbooks.txt')
    df = leer_archivo_datos(archivo_datos)
    return df, "ARCHIVO_DATOS"


def autenticar_tableau(config):
    """Autentica en Tableau Server usando PAT token"""
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
    Descarga UN workbook. Soluciona problema de carpeta extra.
    
    Descargar sin extensión .twbx → TSC crea carpeta
    Mover archivo a ubicación final → Limpiar carpeta
    """
    try:
        ruta_destino = Path(ruta_destino)
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[DESCARGANDO] %s", workbook_luid)
        
        ruta_temporal = str(ruta_destino.parent / ruta_destino.stem)
        server.workbooks.download(workbook_luid, filepath=ruta_temporal)
        
        carpeta_descargada = Path(ruta_temporal)
        
        if carpeta_descargada.is_dir():
            archivos_twbx = list(carpeta_descargada.glob('*.twbx'))
            
            if archivos_twbx:
                shutil.move(str(archivos_twbx[0]), str(ruta_destino))
                
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
    """Descarga todos los workbooks. Registra estadísticas."""
    
    estadisticas = {
        'total': len(df),
        'descargados': 0,
        'errores': 0,
        'tiempos': {}
    }
    
    logger.info("="*60)
    logger.info("DESCARGANDO WORKBOOKS")
    logger.info("="*60)
    
    for contador, (idx, fila) in enumerate(df.iterrows(), 1):
        workbook_luid = str(fila['WORKBOOK_LUID']).strip()
        workbook_nombre = str(fila['WORKBOOK']).strip()
        ruta_proyecto = str(fila['RUTA_PROYECTO']).strip()
        
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
    
    return estadisticas


def subir_github(directorio_base, config):
    """Ejecuta: git add . → git commit → git push"""
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        os.chdir(directorio_base)
        
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"
        
        logger.info("[GIT] git commit -m '%s'", mensaje)
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )
        
        if "nothing to commit" in resultado.stdout.lower():
            logger.info("[AVISO] No hay cambios que hacer commit")
            return
        
        logger.info("[GIT] git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        logger.error("[ERROR] Error en Git: %s", e)
    except Exception as e:
        logger.error("[ERROR] Error al subir: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """Muestra resumen final"""
    
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
    """Orquestador principal"""
    
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
    
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    
    limpiar_directorio(directorio_base)
    
    df, fuente_datos = obtener_datos_inteligente(config, args.forzar_excel)
    
    logger.info("="*60)
    logger.info("AUTENTICAR EN TABLEAU")
    logger.info("="*60)
    server = autenticar_tableau(config)
    
    logger.info("\n[DIRECTORIO] %s", directorio_base)
    logger.info("[FUENTE] %s", fuente_datos.upper())
    logger.info("[WORKBOOKS] %d para descargar", len(df))
    
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado o --sin-github especificado")
    
    server.auth.sign_out()
    
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


if __name__ == '__main__':
    main()
