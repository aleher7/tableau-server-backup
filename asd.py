#!/usr/bin/env python3
"""
Script dual: Lee estructura de workbooks desde EXCEL o ORACLE
Intenta Oracle primero, fallback automático a Excel como backup
Descarga workbooks de Tableau y los sube a GitHub

Uso:
    python descargar_workbooks_excel_oracle.py
    python descargar_workbooks_excel_oracle.py --forzar-excel
    python descargar_workbooks_excel_oracle.py --sin-github
"""

import os
import sys
import json
import logging
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd

# Importar librerías Tableau
try:
    import tableauserverclient as TSC
except ImportError:
    print("ERROR: tableauserverclient no está instalado")
    print("Instala con: pip install tableauserverclient")
    sys.exit(1)

# Para Oracle (opcional)
try:
    import oracledb
    ORACLE_AVAILABLE = True
except ImportError:
    ORACLE_AVAILABLE = False
    print("ADVERTENCIA: oracledb no instalado (Excel funcionará igual)")

# ============================================================================
# LOGGING
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
# CARGAR CONFIGURACIÓN
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

# ============================================================================
# LEER DATOS DESDE EXCEL
# ============================================================================

def leer_archivo_datos(ruta_archivo):
    """
    Lee el archivo de datos (Excel o DSV)
    Detecta automáticamente el formato por extensión
    
    Retorna DataFrame con columnas:
    - WORKBOOK_LUID
    - WORKBOOK
    - RUTA_PROYECTO
    - RUTA_LOCAL_DESTINO
    - OWNER_EMAIL
    - ULTIMA_ACTUALIZACION
    - TIPO_ITEM
    - DESCARGAR (si existe)
    """
    try:
        logger.info("[LEYENDO] Archivo: %s", ruta_archivo)
        
        extension = Path(ruta_archivo).suffix.lower()
        
        # Detectar formato y leer
        if extension == '.dsv':
            # DSV: usa pipe como separador, puede tener comillas en valores
            # Usar quoting='none' para no interpretar comillas
            df = pd.read_csv(ruta_archivo, sep='|', quoting=3, skipinitialspace=True)
        elif extension in ['.xlsx', '.xls']:
            # Excel
            df = pd.read_excel(ruta_archivo)
        elif extension == '.csv':
            # CSV: usa coma como separador
            df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', skipinitialspace=True)
        else:
            # Por defecto intentar como pipe DSV
            logger.warning("[AVISO] Extension no reconocida, intentando como DSV con pipe")
            df = pd.read_csv(ruta_archivo, sep='|', quoting=3, skipinitialspace=True)
        
        # IMPORTANTE: Limpiar espacios y comillas de nombres de columnas
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        
        # IMPORTANTE: Limpiar comillas de TODOS los valores (strings)
        for col in df.columns:
            if df[col].dtype == 'object':  # Solo si es texto
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        logger.info("[OK] Archivo cargado: %d filas", len(df))
        logger.info("[INFO] Columnas encontradas: %s", ", ".join(df.columns))
        
        # Buscar columnas de forma flexible (sin importar espacios/comillas)
        def buscar_columna(df, patrones):
            """Busca una columna que contenga uno de los patrones"""
            for col in df.columns:
                col_limpio = col.strip().upper().replace('"', '')
                for patron in patrones:
                    if patron.upper() in col_limpio:
                        logger.info("[MAPEO] Columna '%s' mapeada a '%s'", col, patron)
                        return col
            return None
        
        # Mapear columnas
        col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID', 'ID'])
        col_nombre = buscar_columna(df, ['WORKBOOK', 'NOMBRE'])
        col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA'])
        col_descargar = buscar_columna(df, ['DESCARGAR', 'DOWNLOAD'])
        
        if not col_luid:
            logger.error("[ERROR] No se encontró columna WORKBOOK_LUID (o similar)")
            logger.error("[INFO] Columnas disponibles: %s", ", ".join(df.columns))
            sys.exit(1)
        if not col_nombre:
            logger.error("[ERROR] No se encontró columna WORKBOOK")
            sys.exit(1)
        if not col_ruta:
            logger.error("[ERROR] No se encontró columna RUTA_PROYECTO")
            sys.exit(1)
        
        # Renombrar columnas para que sean estándar
        df = df.rename(columns={
            col_luid: 'WORKBOOK_LUID',
            col_nombre: 'WORKBOOK',
            col_ruta: 'RUTA_PROYECTO'
        })
        
        # Filtrar solo si DESCARGAR=True (si existe esa columna)
        if col_descargar:
            df = df.rename(columns={col_descargar: 'DESCARGAR'})
            df = df[df['DESCARGAR'].astype(str).str.upper().isin(['TRUE', '1', 'TRUE'])]
            logger.info("[FILTRADO] Workbooks para descargar: %d", len(df))
        
        return df
    except Exception as e:
        logger.error("[ERROR] Error al leer archivo: %s", e)
        sys.exit(1)

# ============================================================================
# CONECTAR A ORACLE
# ============================================================================

def conectar_oracle(config):
    """Conecta a Oracle y obtiene la estructura de workbooks"""
    
    if not ORACLE_AVAILABLE:
        logger.error("[ERROR] oracledb no está instalado")
        sys.exit(1)
    
    try:
        logger.info("[CONECTANDO] Oracle...")
        
        # Usar wallet si está configurado
        if config.get('oracle_wallet_location'):
            oracledb.init_oracle_client(
                wallet_location=config['oracle_wallet_location'],
                wallet_password=config.get('oracle_wallet_password', '')
            )
        
        # Conectar
        conexion = oracledb.connect(
            user=config['oracle_user'],
            password=config['oracle_password'],
            dsn=config['oracle_dsn']
        )
        
        logger.info("[OK] Conectado a Oracle")
        
        # Ejecutar query
        cursor = conexion.cursor()
        
        # MODIFICAR ESTA QUERY SEGÚN TU ESQUEMA ORACLE
        query = """
        SELECT 
            WORKBOOK_LUID,
            WORKBOOK,
            RUTA_PROYECTO,
            RUTA_LOCAL_DESTINO,
            OWNER_EMAIL,
            ULTIMA_ACTUALIZACION,
            TIPO_ITEM
        FROM tu_tabla_workbooks
        WHERE DESCARGAR = 1
        """
        
        cursor.execute(query)
        columnas = [desc[0] for desc in cursor.description]
        filas = cursor.fetchall()
        
        cursor.close()
        conexion.close()
        
        # Convertir a DataFrame
        df = pd.DataFrame(filas, columns=columnas)
        logger.info("[OK] Oracle: %d workbooks encontrados", len(df))
        
        return df
        
    except Exception as e:
        logger.error("[ERROR] Error al conectar Oracle: %s", e)
        raise

# ============================================================================
# OBTENER DATOS INTELIGENTE (ORACLE + FALLBACK EXCEL)
# ============================================================================

def obtener_datos_inteligente(config):
    """
    Intenta obtener datos de Oracle primero.
    Si falla, cae automáticamente a Excel (backup) en la misma ruta del script.
    
    Returns:
        DataFrame con los datos y tipo de fuente (oracle/excel)
    """
    
    fuente_datos = None
    df = None
    
    # ========== PASO 1: INTENTAR ORACLE ==========
    logger.info("[INTENTANDO] Conectar a Oracle...")
    
    if not ORACLE_AVAILABLE:
        logger.warning("[AVISO] oracledb no está instalado - saltando Oracle")
    else:
        try:
            df = conectar_oracle(config)
            fuente_datos = 'oracle'
            logger.info("[EXITO] Datos obtenidos desde ORACLE")
            return df, fuente_datos
        except Exception as e:
            logger.warning("[AVISO] Oracle fallo: %s", str(e)[:100])
            logger.info("[FALLBACK] Cayendo a Excel de respaldo...")
    
    # ========== PASO 2: FALLBACK A ARCHIVO DE DATOS ==========
    logger.info("[BUSCANDO] Archivo de datos de respaldo...")
    
    directorio_script = Path(__file__).parent
    
    # Buscar archivo "prueba" con cualquier extensión (.dsv, .xlsx, .csv, etc)
    archivos_prueba = []
    
    # Buscar todos los archivos que empiezan con "prueba" (case-insensitive)
    for archivo in directorio_script.glob('prueba*'):
        if archivo.is_file():
            archivos_prueba.append(archivo)
    
    if not archivos_prueba:
        logger.error("[ERROR] No se encontró archivo 'prueba' en la carpeta del script")
        logger.error("[INFO] Ubicacion: %s", directorio_script)
        logger.error("[ACCION] Coloca un archivo llamado 'prueba' (prueba.dsv, prueba.xlsx, prueba.csv, etc)")
        sys.exit(1)
    
    # Si hay múltiples archivos "prueba", usar el más reciente
    archivo_datos = sorted(archivos_prueba, key=lambda x: x.stat().st_mtime, reverse=True)[0]
    
    if not archivo_datos.exists():
        logger.error("[ERROR] Archivo de datos no encontrado: %s", archivo_datos)
        sys.exit(1)
    
    logger.info("[USANDO] Archivo de datos: %s", archivo_datos.name)
    
    try:
        df = leer_archivo_datos(str(archivo_datos))
        fuente_datos = 'archivo_datos'
        logger.info("[EXITO] Datos obtenidos desde archivo de datos (respaldo)")
        return df, fuente_datos
    except Exception as e:
        logger.error("[ERROR] Error al leer archivo de datos: %s", e)
        sys.exit(1)

# ============================================================================
# AUTENTICARSE EN TABLEAU
# ============================================================================

def autenticar_tableau(config):
    """Autentica en Tableau Server/Cloud"""
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
        logger.error("[ERROR] Error al autenticar en Tableau: %s", e)
        sys.exit(1)

# ============================================================================
# DESCARGAR WORKBOOK DE TABLEAU
# ============================================================================

def descargar_workbook(server, workbook_luid, ruta_destino):
    """
    Descarga un workbook de Tableau usando su LUID
    
    Args:
        server: Conexión a Tableau
        workbook_luid: ID del workbook en Tableau
        ruta_destino: Ruta local donde guardar
    
    Returns:
        True si se descargó correctamente, False en caso contrario
    """
    try:
        # Crear carpeta si no existe
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info("[DESCARGANDO] %s", workbook_luid)
        
        # Descargar workbook
        content = server.workbooks.download(workbook_luid, filepath=str(ruta_destino))
        
        logger.info("[OK] Descargado: %s", ruta_destino.name)
        return True
        
    except Exception as e:
        logger.error("[ERROR] Error descargando %s: %s", workbook_luid, e)
        return False

# ============================================================================
# PROCESAR DESCARGAS
# ============================================================================

def procesar_descargas(server, df, directorio_base):
    """
    Procesa las descargas de todos los workbooks
    
    Estructura:
    directorio_base/
    ├── RUTA_PROYECTO_1/
    │   ├── Workbook1.twbx
    │   └── Workbook2.twbx
    ├── RUTA_PROYECTO_2/
    │   └── Workbook3.twbx
    """
    
    estadisticas = {
        'total': len(df),
        'descargados': 0,
        'errores': 0,
        'tiempos': {}
    }
    
    logger.info("="*60)
    logger.info("INICIANDO DESCARGA DE %d WORKBOOKS", len(df))
    logger.info("="*60)
    
    for idx, fila in df.iterrows():
        workbook_luid = fila['WORKBOOK_LUID']
        workbook_nombre = fila['WORKBOOK']
        ruta_proyecto = fila['RUTA_PROYECTO']
        
        # Construir ruta local respetando estructura
        ruta_local = Path(directorio_base) / ruta_proyecto / f"{workbook_nombre}.twbx"
        
        logger.info("\n[%d/%d] %s", idx+1, len(df), workbook_nombre)
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

# ============================================================================
# SUBIR A GITHUB
# ============================================================================

def subir_github(directorio_base, config):
    """Hace git add, commit y push a GitHub"""
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        os.chdir(directorio_base)
        
        # Git add
        logger.info("[GIT] git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        # Git commit
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"
        logger.info("[GIT] git commit -m '%s'", mensaje)
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )
        
        if "nothing to commit" in resultado.stdout:
            logger.info("[AVISO] No hay cambios que hacer commit")
            return
        
        # Git push
        logger.info("[GIT] git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        logger.error("[ERROR] Error en Git: %s", e)
    except Exception as e:
        logger.error("[ERROR] Error al subir a GitHub: %s", e)

# ============================================================================
# REPORTE FINAL
# ============================================================================

def mostrar_reporte(estadisticas, tiempo_total):
    """Muestra reporte de ejecución"""
    
    logger.info("="*60)
    logger.info("REPORTE FINAL")
    logger.info("="*60)
    logger.info("Total de workbooks:    %d", estadisticas['total'])
    logger.info("Descargados:           %d [OK]", estadisticas['descargados'])
    logger.info("Errores:               %d [ERROR]", estadisticas['errores'])
    
    if estadisticas['total'] > 0:
        tasa = (estadisticas['descargados']/estadisticas['total']*100)
        logger.info("Tasa de exito:         %.1f%%", tasa)
    
    logger.info("Tiempo total:          %.2fs", tiempo_total)
    
    if estadisticas['tiempos']:
        promedio = sum(estadisticas['tiempos'].values()) / len(estadisticas['tiempos'])
        logger.info("Tiempo promedio/workbook: %.2fs", promedio)
    
    logger.info("="*60)

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Función principal"""
    
    # Argumentos de línea de comandos
    parser = argparse.ArgumentParser(
        description='Descarga workbooks Tableau (intenta Oracle, fallback a Excel)'
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
        help='Forzar uso de Excel (sin intentar Oracle)'
    )
    
    args = parser.parse_args()
    
    inicio_total = datetime.now()
    
    # Cargar configuración
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    # Obtener datos (Oracle con fallback a Excel)
    logger.info("="*60)
    logger.info("OBTENER DATOS")
    logger.info("="*60)
    
    if args.forzar_excel:
        logger.info("[FORZANDO] Archivo de datos (--forzar-excel)")
        directorio_script = Path(__file__).parent
        
        # Buscar archivo "prueba" con cualquier extensión
        archivos_prueba = []
        for archivo in directorio_script.glob('prueba*'):
            if archivo.is_file():
                archivos_prueba.append(archivo)
        
        if not archivos_prueba:
            logger.error("[ERROR] No hay archivos 'prueba' en la carpeta del script")
            sys.exit(1)
        
        archivo_datos = sorted(archivos_prueba, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        logger.info("[USANDO] %s", archivo_datos.name)
        df = leer_archivo_datos(str(archivo_datos))
        fuente_datos = 'archivo_datos'
    else:
        # Intenta Oracle, fallback a archivo de datos
        df, fuente_datos = obtener_datos_inteligente(config)
    
    # Autenticar en Tableau
    logger.info("="*60)
    logger.info("AUTENTICAR EN TABLEAU")
    logger.info("="*60)
    server = autenticar_tableau(config)
    
    # Crear directorio base
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    Path(directorio_base).mkdir(parents=True, exist_ok=True)
    logger.info("\n[DIRECTORIO] %s", directorio_base)
    logger.info("[FUENTE] %s", fuente_datos.upper())
    
    # Descargar workbooks
    logger.info("="*60)
    logger.info("DESCARGANDO WORKBOOKS")
    logger.info("="*60)
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # Subir a GitHub (opcional)
    if not args.sin_github and config.get('github_enabled', True):
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        subir_github(directorio_base, config)
    
    # Cerrar sesión Tableau
    server.auth.sign_out()
    
    # Reporte final
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)

if __name__ == '__main__':
    main()
