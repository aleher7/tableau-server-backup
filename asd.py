#!/usr/bin/env python3

"""
Script para descargar workbooks de Tableau usando:
1. SQL PLUS (ejecuta consulta, genera lista_workbooks.txt)
2. Python (parsea archivo, descarga workbooks, sube a GitHub)

Ventajas:
- Usa tu setup actual de SQL PLUS
- Python solo descarga y sube
- Separación de responsabilidades
- Más fácil de mantener
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


def ejecutar_sqlplus(comando_sqlplus, timeout=15):
    """
    Ejecuta comando SQL PLUS como proceso externo.
    
    El comando es típicamente:
    cd C:\oracle\instantclient_23_0 && sqlplus -S usuario/password@dsn @archivo.sql
    
    El comando genera: C:\tabcmd\TableauGitHub\lista_workbooks.txt
    
    Parámetros:
    - comando_sqlplus: Comando completo a ejecutar
    - timeout: Máximo de segundos a esperar
    """
    try:
        logger.info("[SQLPLUS] Ejecutando comando...")
        logger.info("[SQLPLUS] Comando: %s", comando_sqlplus[:100] + "..." if len(comando_sqlplus) > 100 else comando_sqlplus)
        
        # Ejecutar comando en Windows (shell=True para que interprete && correctamente)
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
        return False
    except Exception as e:
        logger.error("[ERROR] Error ejecutando SQL PLUS: %s", e)
        return False


def esperar_archivo(ruta_archivo, timeout=15, intervalo=1):
    """
    Espera a que se genere el archivo lista_workbooks.txt
    
    Verifica cada 'intervalo' segundos hasta 'timeout' segundos máximo
    """
    logger.info("[ESPERANDO] Archivo: %s", ruta_archivo)
    
    inicio = time.time()
    ruta = Path(ruta_archivo)
    
    while time.time() - inicio < timeout:
        if ruta.exists():
            tamaño = ruta.stat().st_size
            logger.info("[OK] Archivo encontrado (%d bytes)", tamaño)
            
            # Esperar un poco más para asegurar que se terminó de escribir
            time.sleep(2)
            return True
        
        tiempo_transcurrido = int(time.time() - inicio)
        logger.info("[ESPERANDO] %d/%d segundos...", tiempo_transcurrido, timeout)
        time.sleep(intervalo)
    
    logger.error("[ERROR] Archivo no generado después de %d segundos", timeout)
    return False


def parsear_lista_workbooks(ruta_archivo):
    """
    Parsea el archivo lista_workbooks.txt generado por SQL PLUS.
    
    El archivo tiene un formato especial:
    ─────────────────────────────────────────────────────────
    WORKBOOK_LUID
    ────────────────────────────────────────────────────────
    cede88a2-52d7-439a-8d72-f16b42a73b89
    
    WORKBOOK
    ────────────────────────────────────────────────────────
    Admin Insights Starter
    
    RUTA_PROYECTO
    ────────────────────────────────────────────────────────
    Finance
    
    OWNER_EMAIL
    ────────────────────────────────────────────────────────
    ...
    
    TIPO_ITEM                DESCARGAR
    ────────────────────────────────────────────────────────
    cede88a2-52d7-439a...    DESCARGAR
    
    WORKBOOK_LUID
    ────────────────────────────────────────────────────────
    (siguiente workbook)
    ─────────────────────────────────────────────────────────
    
    Algoritmo:
    1. Leer línea por línea
    2. Detectar encabezados conocidos (WORKBOOK_LUID, WORKBOOK, RUTA_PROYECTO)
    3. Saltarse líneas de guiones y vacías
    4. Leer el valor (siguiente línea no vacía)
    5. Agrupar por WORKBOOK_LUID
    6. Cuando vuelve WORKBOOK_LUID, es un nuevo registro
    """
    
    try:
        logger.info("[PARSEANDO] Archivo: %s", ruta_archivo)
        
        workbooks = []
        workbook_actual = {}
        
        # Encabezados que nos interesan
        encabezados_buscados = ['WORKBOOK_LUID', 'WORKBOOK', 'RUTA_PROYECTO']
        
        with open(ruta_archivo, 'r', encoding='utf-8', errors='replace') as f:
            lineas = f.readlines()
        
        i = 0
        while i < len(lineas):
            linea = lineas[i].strip()
            
            # Buscar encabezados conocidos
            if linea in encabezados_buscados:
                # Si WORKBOOK_LUID y ya tenemos datos, es un nuevo workbook
                if linea == 'WORKBOOK_LUID' and workbook_actual:
                    # Guardar el workbook anterior si está completo
                    if 'WORKBOOK_LUID' in workbook_actual and 'WORKBOOK' in workbook_actual:
                        workbooks.append(workbook_actual)
                    workbook_actual = {}
                
                # Saltarse líneas de guiones (─────────────)
                i += 1
                while i < len(lineas) and ('─' in lineas[i] or '-' in lineas[i]):
                    i += 1
                
                # Leer el valor (siguiente línea no vacía)
                while i < len(lineas):
                    valor = lineas[i].strip()
                    if valor and not ('─' in valor or '-' in valor):
                        workbook_actual[linea] = valor
                        break
                    i += 1
            
            i += 1
        
        # Agregar el último workbook si está completo
        if workbook_actual and 'WORKBOOK_LUID' in workbook_actual and 'WORKBOOK' in workbook_actual:
            workbooks.append(workbook_actual)
        
        logger.info("[OK] Parseado: %d workbooks", len(workbooks))
        
        # Convertir a DataFrame para facilitar manejo
        df = pd.DataFrame(workbooks)
        
        # Asegurar que tenemos las columnas necesarias
        columnas_necesarias = ['WORKBOOK_LUID', 'WORKBOOK']
        columnas_faltantes = [col for col in columnas_necesarias if col not in df.columns]
        
        if columnas_faltantes:
            logger.error("[ERROR] Columnas faltantes: %s", columnas_faltantes)
            return None
        
        # Si RUTA_PROYECTO no existe, usar valor por defecto
        if 'RUTA_PROYECTO' not in df.columns:
            logger.warning("[AVISO] RUTA_PROYECTO no encontrada, usando valor por defecto")
            df['RUTA_PROYECTO'] = 'default'
        
        logger.info("[OK] DataFrame creado: %d filas, %d columnas", len(df), len(df.columns))
        
        return df
        
    except Exception as e:
        logger.error("[ERROR] Error parseando archivo: %s", e)
        return None


def limpiar_directorio(directorio_base):
    """
    Elimina completamente la carpeta de descargas y la recrea vacía.
    """
    logger.info("="*60)
    logger.info("LIMPIEZA DE DIRECTORIO")
    logger.info("="*60)
    
    ruta = Path(directorio_base)
    
    if ruta.exists():
        logger.info("[LIMPIANDO] Eliminando directorio: %s", directorio_base)
        try:
            shutil.rmtree(directorio_base)
            logger.info("[OK] Directorio eliminado completamente")
        except Exception as e:
            logger.error("[ERROR] Error al eliminar directorio: %s", e)
    
    try:
        Path(directorio_base).mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Directorio recreado: %s", directorio_base)
    except Exception as e:
        logger.error("[ERROR] No se pudo recrear directorio: %s", e)
        sys.exit(1)


def autenticar_tableau(config):
    """Autentica en Tableau Server usando PAT"""
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
    """Descarga UN workbook de Tableau Server"""
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
                logger.error("[ERROR] No se encontró .twbx dentro de carpeta")
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
    """Descarga todos los workbooks del DataFrame"""
    
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
    """Sube cambios a GitHub"""
    
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
        description='Descarga workbooks Tableau usando SQL PLUS + Python'
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
    
    args = parser.parse_args()
    
    inicio_total = datetime.now()
    
    # Cargar configuración
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
    
    # PASO 2: Esperar a que se genere el archivo
    logger.info("="*60)
    logger.info("PASO 2: ESPERAR ARCHIVO")
    logger.info("="*60)
    
    archivo_lista = config['archivo_lista_workbooks']
    
    if not esperar_archivo(archivo_lista, timeout):
        logger.error("[FATAL] El archivo lista_workbooks.txt no se generó")
        sys.exit(1)
    
    # PASO 3: Parsear el archivo
    logger.info("="*60)
    logger.info("PASO 3: PARSEAR ARCHIVO")
    logger.info("="*60)
    
    df = parsear_lista_workbooks(archivo_lista)
    
    if df is None or len(df) == 0:
        logger.error("[FATAL] No se pudo parsear el archivo o está vacío")
        sys.exit(1)
    
    # PASO 4: Limpiar directorio de descargas
    limpiar_directorio(directorio_base)
    
    # PASO 5: Autenticar en Tableau
    logger.info("="*60)
    logger.info("PASO 5: AUTENTICAR TABLEAU")
    logger.info("="*60)
    
    server = autenticar_tableau(config)
    
    # PASO 6: Descargar workbooks
    estadisticas = procesar_descargas(server, df, directorio_base)
    
    # PASO 7: Subir a GitHub
    if not args.sin_github and config.get('github_enabled', True):
        subir_github(directorio_base, config)
    else:
        logger.info("[AVISO] GitHub deshabilitado o --sin-github especificado")
    
    # Cerrar sesión
    server.auth.sign_out()
    
    # PASO 8: Generar reporte
    tiempo_total = (datetime.now() - inicio_total).total_seconds()
    mostrar_reporte(estadisticas, tiempo_total)


# ============================================================================
# PUNTO DE ENTRADA
# ============================================================================

if __name__ == '__main__':
    main()
