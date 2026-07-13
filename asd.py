#!/usr/bin/env python3
"""
Script Dual: Lee estructura de workbooks desde EXCEL o ORACLE
Descarga workbooks de Tableau respetando estructura de carpetas
Sube automáticamente a GitHub

Uso:
    python descargar_workbooks_excel_oracle.py --modo excel
    python descargar_workbooks_excel_oracle.py --modo oracle
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
    print("❌ tableauserverclient no está instalado")
    print("Instala con: pip install tableauserverclient")
    sys.exit(1)

# Para Oracle (opcional)
try:
    import oracledb
    ORACLE_AVAILABLE = True
except ImportError:
    ORACLE_AVAILABLE = False
    print("⚠️  oracledb no instalado (Excel funcionará igual)")

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
        logger.info("✓ Configuración cargada correctamente")
        return config
    except FileNotFoundError:
        logger.error(f"❌ Archivo {config_file} no encontrado")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(f"❌ Error al parsear {config_file}")
        sys.exit(1)

# ============================================================================
# LEER DATOS DESDE EXCEL
# ============================================================================

def leer_excel(ruta_excel):
    """
    Lee el Excel con la estructura de workbooks
    
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
        logger.info(f"📖 Leyendo Excel: {ruta_excel}")
        df = pd.read_excel(ruta_excel)
        
        logger.info(f"✓ Excel cargado: {len(df)} filas")
        logger.info(f"  Columnas: {', '.join(df.columns)}")
        
        # Filtrar solo si DESCARGAR=True (si existe esa columna)
        if 'DESCARGAR' in df.columns:
            df = df[df['DESCARGAR'] == True]
            logger.info(f"✓ Filtrados workbooks para descargar: {len(df)}")
        
        return df
    except Exception as e:
        logger.error(f"❌ Error al leer Excel: {e}")
        sys.exit(1)

# ============================================================================
# LEER DATOS DESDE ORACLE
# ============================================================================

def conectar_oracle(config):
    """Conecta a Oracle y obtiene la estructura de workbooks"""
    
    if not ORACLE_AVAILABLE:
        logger.error("❌ oracledb no está instalado")
        sys.exit(1)
    
    try:
        logger.info("🔗 Conectando a Oracle...")
        
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
        
        logger.info("✓ Conectado a Oracle")
        
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
        logger.info(f"✓ Oracle: {len(df)} workbooks encontrados")
        
        return df
        
    except Exception as e:
        logger.error(f"❌ Error al conectar Oracle: {e}")
        sys.exit(1)

# ============================================================================
# AUTENTICARSE EN TABLEAU
# ============================================================================

def autenticar_tableau(config):
    """Autentica en Tableau Server/Cloud"""
    try:
        logger.info("🔐 Autenticando en Tableau...")
        
        tableau_auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site']
        )
        
        server = TSC.Server(config['tableau_server'])
        server.auth.sign_in(tableau_auth)
        
        logger.info("✓ Autenticado en Tableau")
        return server
        
    except Exception as e:
        logger.error(f"❌ Error al autenticar en Tableau: {e}")
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
        
        logger.info(f"⬇️  Descargando: {workbook_luid}")
        
        # Descargar workbook
        content = server.workbooks.download(workbook_luid, filepath=str(ruta_destino))
        
        logger.info(f"✓ Descargado: {ruta_destino.name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error descargando {workbook_luid}: {e}")
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
    
    logger.info(f"\n{'='*60}")
    logger.info(f"INICIANDO DESCARGA DE {len(df)} WORKBOOKS")
    logger.info(f"{'='*60}\n")
    
    for idx, fila in df.iterrows():
        workbook_luid = fila['WORKBOOK_LUID']
        workbook_nombre = fila['WORKBOOK']
        ruta_proyecto = fila['RUTA_PROYECTO']
        
        # Construir ruta local respetando estructura
        ruta_local = Path(directorio_base) / ruta_proyecto / f"{workbook_nombre}.twbx"
        
        logger.info(f"\n[{idx+1}/{len(df)}] {workbook_nombre}")
        logger.info(f"     Proyecto: {ruta_proyecto}")
        logger.info(f"     LUID: {workbook_luid}")
        
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
        logger.info(f"\n{'='*60}")
        logger.info("SUBIENDO A GITHUB")
        logger.info(f"{'='*60}\n")
        
        os.chdir(directorio_base)
        
        # Git add
        logger.info("📤 git add .")
        subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
        
        # Git commit
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mensaje = f"Tableau Backup - {timestamp}"
        logger.info(f"📤 git commit -m '{mensaje}'")
        resultado = subprocess.run(
            ['git', 'commit', '-m', mensaje],
            check=True,
            capture_output=True,
            text=True
        )
        
        if "nothing to commit" in resultado.stdout:
            logger.info("ℹ️  No hay cambios que hacer commit")
            return
        
        # Git push
        logger.info("📤 git push origin main")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("✓ Subido a GitHub correctamente")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Error en Git: {e}")
    except Exception as e:
        logger.error(f"❌ Error al subir a GitHub: {e}")

# ============================================================================
# REPORTE FINAL
# ============================================================================

def mostrar_reporte(estadisticas, tiempo_total):
    """Muestra reporte de ejecución"""
    
    logger.info(f"\n{'='*60}")
    logger.info("REPORTE FINAL")
    logger.info(f"{'='*60}")
    logger.info(f"Total de workbooks:    {estadisticas['total']}")
    logger.info(f"Descargados:           {estadisticas['descargados']} ✓")
    logger.info(f"Errores:               {estadisticas['errores']} ✗")
    logger.info(f"Tasa de éxito:         {(estadisticas['descargados']/estadisticas['total']*100):.1f}%")
    logger.info(f"Tiempo total:          {tiempo_total:.2f}s")
    
    if estadisticas['tiempos']:
        promedio = sum(estadisticas['tiempos'].values()) / len(estadisticas['tiempos'])
        logger.info(f"Tiempo promedio/workbook: {promedio:.2f}s")
    
    logger.info(f"{'='*60}\n")

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Función principal"""
    
    # Argumentos de línea de comandos
    parser = argparse.ArgumentParser(
        description='Descarga workbooks Tableau desde Excel u Oracle y los sube a GitHub'
    )
    parser.add_argument(
        '--modo',
        choices=['excel', 'oracle'],
        default='excel',
        help='Fuente de datos: excel u oracle (default: excel)'
    )
    parser.add_argument(
        '--config',
        default='config.json',
        help='Archivo de configuración (default: config.json)'
    )
    parser.add_argument(
        '--sin-github',
        action='store_true',
        help='Solo descargar, sin subir a GitHub'
    )
    
    args = parser.parse_args()
    
    inicio_total = datetime.now()
    
    # Cargar configuración
    logger.info("📋 Cargando configuración...")
    config = cargar_config(args.config)
    
    # Leer datos (Excel u Oracle)
    logger.info(f"\n📊 Modo: {args.modo.upper()}")
    
    if args.modo == 'excel':
        if 'excel_ruta' not in config:
            logger.error("❌ 'excel_ruta' no está en config.json")
            sys.exit(1)
        df = leer_excel(config['excel_ruta'])
    
    elif args.modo == 'oracle':
        if not ORACLE_AVAILABLE:
            logger.error("❌ oracledb no está instalado")
            logger.info("Instala con: pip install oracledb")
            sys.exit(1)
        df = conectar_oracle(config)
    
    # Autenticar en Tableau
    server = autenticar_tableau(config)
    
    # Crear directorio base
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    Path(directorio_base).mkdir(parents=True, exist_ok=True)
    logger.info(f"📁 Directorio base: {directorio_base}")
    
    # Descargar workbooks
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # Subir a GitHub (opcional)
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    
    # Cerrar sesión Tableau
    server.auth.sign_out()
    
    # Reporte final
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)

if __name__ == '__main__':
    main()
