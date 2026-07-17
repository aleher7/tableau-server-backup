#!/usr/bin/env python3

"""
Script CORREGIDO para descargar workbooks de Tableau usando:
1. SQL PLUS (ejecuta consulta, genera lista_workbooks.txt en formato CSV)
2. Python (parsea CSV, descarga workbooks, sube a GitHub)

CORRECCIONES v2:
- Validación robusta de config.json
- Mensajes de error claros
- Manejo seguro de variables faltantes
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
# FUNCIONES DE VALIDACIÓN
# ============================================================================

def validar_config(config):
    """Valida que el config.json tenga todas las claves necesarias"""
    
    logger.info("="*60)
    logger.info("VALIDANDO CONFIGURACIÓN")
    logger.info("="*60)
    
    # Claves requeridas para SQL PLUS
    claves_sqlplus_requeridas = [
        'sqlplus_comando',
        'archivo_lista_workbooks'
    ]
    
    # Claves requeridas para Tableau
    claves_tableau_requeridas = [
        'tableau_server',
        'tableau_token_name',
        'tableau_token',
        'tableau_site'
    ]
    
    # Claves opcionales con valores por defecto
    claves_opcionales = {
        'directorio_descarga': './tableau_workbooks',
        'timeout_sqlplus': 15,
        'github_enabled': True
    }
    
    # VALIDAR CLAVES SQL PLUS
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
            sys.exit(1)
        else:
            logger.info("  ✅ %s encontrada", clave)
    
    # VALIDAR CLAVES TABLEAU
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
            logger.info("  ✅ %s encontrada", clave)
    
    # CLAVES OPCIONALES
    logger.info("[VERIFICANDO] Claves opcionales...")
    for clave, valor_default in claves_opcionales.items():
        if clave not in config:
            logger.info("  ⚠️  %s no encontrada, usando default: %s", clave, valor_default)
            config[clave] = valor_default
        else:
            logger.info("  ✅ %s encontrada", clave)
    
    logger.info("[OK] Configuración validada correctamente")
    logger.info("")
    return config


# ============================================================================
# FUNCIONES PRINCIPALES
# ============================================================================

def cargar_config(config_file="config.json"):
    """Carga y valida la configuración desde JSON"""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logger.info("[OK] Archivo %s cargado", config_file)
        
        # VALIDAR CONFIGURACIÓN
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
        logger.error("[ERROR] Error al parsear %s (JSON inválido)", config_file)
        sys.exit(1)


def ejecutar_sqlplus(comando_sqlplus, timeout=15):
    """Ejecuta comando SQL PLUS"""
    try:
        logger.info("[SQLPLUS] Ejecutando comando...")
        logger.info("[SQLPLUS] Timeout: %d segundos", timeout)
        
        resultado = subprocess.run(
            comando_sqlplus,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        if resultado.returncode != 0:
            logger.error("[ERROR] SQL PLUS error (código %d)", resultado.returncode)
            logger.error("[ERROR] stderr: %s", resultado.stderr[:500])
            return False
        
        logger.info("[OK] Comando ejecutado correctamente")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("[ERROR] SQL PLUS timeout (>%d segundos)", timeout)
        logger.error("[ERROR] El comando tardó demasiado. Aumenta timeout_sqlplus en config.json")
        return False
    except Exception as e:
        logger.error("[ERROR] Error ejecutando SQL PLUS: %s", e)
        return False


def esperar_archivo(ruta_archivo, timeout=15, intervalo=1):
    """Espera a que se genere el archivo"""
    logger.info("[ESPERANDO] Archivo: %s", ruta_archivo)
    
    inicio = time.time()
    ruta = Path(ruta_archivo)
    
    while time.time() - inicio < timeout:
        if ruta.exists():
            tamaño = ruta.stat().st_size
            logger.info("[OK] Archivo encontrado (%d bytes)", tamaño)
            time.sleep(2)
            return True
        
        tiempo_transcurrido = int(time.time() - inicio)
        logger.info("[ESPERANDO] %d/%d segundos...", tiempo_transcurrido, timeout)
        time.sleep(intervalo)
    
    logger.error("[ERROR] Archivo no generado después de %d segundos", timeout)
    return False


def parsear_lista_workbooks(ruta_archivo, separador=','):
    """
    Parsea archivo CSV/TSV/PIPE generado por SQL PLUS
    """
    
    try:
        logger.info("[PARSEANDO] Archivo: %s", ruta_archivo)
        logger.info("[PARSEANDO] Separador: %r", separador)
        
        # Leer como CSV
        df = pd.read_csv(
            ruta_archivo,
            sep=separador,
            skipinitialspace=True,
            dtype=str,
            encoding='utf-8'
        )
        
        # Limpiar espacios en blanco
        df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)
        
        logger.info("[OK] Parseado: %d workbooks", len(df))
        logger.info("[COLUMNAS] %s", ", ".join(df.columns))
        
        # Validar columnas requeridas
        columnas_requeridas = ['WORKBOOK_LUID', 'WORKBOOK']
        
        for col in columnas_requeridas:
            if col not in df.columns:
                logger.error("[ERROR] Columna requerida no encontrada: %s", col)
                logger.error("[INFO] Columnas disponibles: %s", ", ".join(df.columns))
                logger.error("")
                logger.error("Verifica tu archivo Descarga.sql:")
                logger.error("- ¿Están todas las columnas en el SELECT?")
                logger.error("- ¿El separador (SET COLSEP) es el correcto?")
                return None
        
        # Si falta RUTA_PROYECTO, usar default
        if 'RUTA_PROYECTO' not in df.columns:
            logger.warning("[AVISO] RUTA_PROYECTO no encontrada, usando 'default'")
            df['RUTA_PROYECTO'] = 'default'
        
        logger.info("[OK] DataFrame validado")
        return df
        
    except Exception as e:
        logger.error("[ERROR] Error parseando archivo: %s", e)
        logger.error("")
        logger.error("Posibles causas:")
        logger.error("- El archivo no existe")
        logger.error("- El archivo está vacío")
        logger.error("- El separador (--separador) es incorrecto")
        logger.error("- El archivo tiene formato incorrecto")
        return None


def limpiar_directorio(directorio_base):
    """Limpia y recrea directorio"""
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado")
        except Exception as e:
            logger.error("[ERROR] Error al eliminar: %s", e)
    
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def autenticar_tableau(config):
    """Autentica en Tableau Server"""
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
        logger.error("")
        logger.error("Verifica en config.json:")
        logger.error("- tableau_server: %s", config.get('tableau_server', 'NO DEFINIDO'))
        logger.error("- tableau_token_name: %s", config.get('tableau_token_name', 'NO DEFINIDO'))
        logger.error("- tableau_token: [oculto]")
        logger.error("- tableau_site: %s", config.get('tableau_site', 'NO DEFINIDO'))
        sys.exit(1)


def descargar_workbook(server, workbook_luid, ruta_destino):
    """Descarga UN workbook"""
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
                except:
                    pass
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
    """Descarga todos los workbooks"""
    
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
        ruta_proyecto = str(fila.get('RUTA_PROYECTO', 'default')).strip()
        
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
    """Sube a GitHub"""
    
    try:
        logger.info("="*60)
        logger.info("SUBIENDO A GITHUB")
        logger.info("="*60)
        
        os.chdir(directorio_base)
        
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
        
        logger.info("[GIT] git push")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
        
        logger.info("[OK] Subido a GitHub")
        
    except Exception as e:
        logger.error("[ERROR] Error en GitHub: %s", e)


def mostrar_reporte(estadisticas, tiempo_total):
    """Muestra reporte final"""
    
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
# FUNCIÓN PRINCIPAL
# ============================================================================

def main():
    """Orquestador principal"""
    
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
        action='store_true',
        help='Solo descargar, sin subir a GitHub'
    )
    parser.add_argument(
        '--separador',
        default=',',
        help='Separador CSV (default: ,) - usar "\\t" para TSV o "|" para PIPE'
    )
    
    args = parser.parse_args()
    
    inicio_total = datetime.now()
    
    # Cargar y validar configuración
    logger.info("[CARGANDO] Configuración...")
    config = cargar_config(args.config)
    
    directorio_base = config.get('directorio_descarga', './tableau_workbooks')
    
    # PASO 1: Ejecutar SQL PLUS
    logger.info("="*60)
    logger.info("PASO 1: EJECUTAR SQL PLUS")
    logger.info("="*60)
    
    comando = config['sqlplus_comando']
    timeout = config.get('timeout_sqlplus', 15)
    
    if not ejecutar_sqlplus(comando, timeout):
        logger.error("[FATAL] No se pudo ejecutar SQL PLUS")
        sys.exit(1)
    
    # PASO 2: Esperar archivo
    logger.info("="*60)
    logger.info("PASO 2: ESPERAR ARCHIVO")
    logger.info("="*60)
    
    archivo_lista = config['archivo_lista_workbooks']
    
    if not esperar_archivo(archivo_lista, timeout):
        logger.error("[FATAL] El archivo no se generó")
        sys.exit(1)
    
    # PASO 3: Parsear archivo
    logger.info("="*60)
    logger.info("PASO 3: PARSEAR ARCHIVO (CSV/TSV/PIPE)")
    logger.info("="*60)
    
    df = parsear_lista_workbooks(archivo_lista, args.separador)
    
    if df is None or len(df) == 0:
        logger.error("[FATAL] No se pudo parsear el archivo")
        sys.exit(1)
    
    # PASO 4: Limpiar directorio
    limpiar_directorio(directorio_base)
    
    # PASO 5: Autenticar Tableau
    logger.info("="*60)
    logger.info("PASO 5: AUTENTICAR TABLEAU")
    logger.info("="*60)
    
    server = autenticar_tableau(config)
    
    # PASO 6: Descargar workbooks
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # PASO 7: Subir GitHub
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado")
    
    server.auth.sign_out()
    
    # PASO 8: Reporte
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


if __name__ == '__main__':
    main()
