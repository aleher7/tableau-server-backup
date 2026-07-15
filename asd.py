#!/usr/bin/env python3
"""
Script de diagnóstico: Compara tu Excel/TXT con dashboards reales en Tableau
Identifica cuáles faltan, tienen LUID incorrecto, etc.

ACTUALIZADO: Ahora lee automáticamente .txt, .csv, .xlsx, .dsv, etc.
"""

import tableauserverclient as TSC
import pandas as pd
import sys
from pathlib import Path

# ============================================================================
# CONFIGURACIÓN - EDITA ESTOS VALORES
# ============================================================================

TABLEAU_SERVER = "https://tu_tableau_server"          # Ejemplo: https://tableau.miempresa.com
TOKEN_NAME = "tu_token_name"                          # Tu PAT token name
TOKEN = "tu_token"                                    # Tu PAT token
SITE_ID = "tu_site"                                  # Tu site ID (default, site2, etc.)
ARCHIVO_DATOS = "workbooks.txt"                       # .txt, .xlsx, .csv, .dsv, etc.

# ============================================================================
# LECTURA FLEXIBLE DE ARCHIVO
# ============================================================================

def leer_archivo_datos(ruta_archivo):
    """Lee automáticamente .txt, .csv, .xlsx, .dsv, etc."""
    
    print(f"[LEYENDO] {ruta_archivo}...")
    
    try:
        extension = Path(ruta_archivo).suffix.lower()
        
        # Detectar formato y leer
        if extension == '.dsv':
            # DSV: usa coma como separador con comillas
            df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
            print(f"[OK] Formato detectado: DSV (coma separada)")
        elif extension in ['.xlsx', '.xls']:
            # Excel
            df = pd.read_excel(ruta_archivo)
            print(f"[OK] Formato detectado: Excel (.xlsx/.xls)")
        elif extension == '.csv':
            # CSV: usa coma como separador
            df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
            print(f"[OK] Formato detectado: CSV (coma separada)")
        elif extension == '.txt':
            # TXT: intentar varias opciones
            # Primero intentar con tabulación
            try:
                df = pd.read_csv(ruta_archivo, sep='\t', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
                print(f"[OK] Formato detectado: TXT (tabulación)")
            except:
                # Si falla, intentar con coma
                try:
                    df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
                    print(f"[OK] Formato detectado: TXT (coma separada)")
                except:
                    # Si aún falla, intentar con espacio
                    df = pd.read_csv(ruta_archivo, sep='\s+', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
                    print(f"[OK] Formato detectado: TXT (espacio separado)")
        else:
            # Por defecto intentar como TXT con tabulación
            print(f"[AVISO] Extensión no reconocida, intentando como TXT...")
            try:
                df = pd.read_csv(ruta_archivo, sep='\t', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
                print(f"[OK] Formato detectado: TXT (tabulación)")
            except:
                df = pd.read_csv(ruta_archivo, sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')
                print(f"[OK] Formato detectado: TXT (coma)")
        
        # Limpiar espacios y comillas de nombres de columnas
        df.columns = [col.strip().replace('"', '') for col in df.columns]
        
        # Limpiar comillas de valores
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()
        
        print(f"[OK] {len(df)} filas leídas\n")
        
        # Mostrar columnas encontradas
        print(f"Columnas detectadas: {', '.join(df.columns)}\n")
        
        return df
        
    except Exception as e:
        print(f"[ERROR] No se pudo leer el archivo: {e}")
        sys.exit(1)

# ============================================================================
# BUSCAR COLUMNAS
# ============================================================================

def buscar_columna(df, patrones):
    """Busca columna de forma flexible"""
    # Primero exacta
    for col in df.columns:
        col_limpio = col.strip().upper().replace('"', '')
        for patron in patrones:
            if col_limpio == patron.upper():
                return col
    
    # Luego parcial
    for col in df.columns:
        col_limpio = col.strip().upper().replace('"', '')
        for patron in patrones:
            patron_upper = patron.upper()
            if patron_upper in col_limpio:
                if patron_upper == 'WORKBOOK' and 'LUID' in col_limpio:
                    continue
                if patron_upper == 'RUTA' and 'LOCAL' in col_limpio:
                    continue
                return col
    return None

# ============================================================================
# MAIN
# ============================================================================

def main():
    # Autenticar
    print("=" * 80)
    print("[CONECTANDO] Tableau Server...")
    print("=" * 80)
    
    try:
        tableau_auth = TSC.PersonalAccessTokenAuth(
            token_name=TOKEN_NAME,
            personal_access_token=TOKEN,
            site_id=SITE_ID
        )
        
        server = TSC.Server(TABLEAU_SERVER)
        server.auth.sign_in(tableau_auth)
        print("[OK] Conectado\n")
    except Exception as e:
        print(f"[ERROR] Error autenticando: {e}")
        print(f"\nVerifica:")
        print(f"  - TABLEAU_SERVER: {TABLEAU_SERVER}")
        print(f"  - TOKEN_NAME: {TOKEN_NAME}")
        print(f"  - SITE_ID: {SITE_ID}")
        sys.exit(1)
    
    # Obtener dashboards reales
    print("=" * 80)
    print("[OBTENIENDO] Dashboards desde Tableau...")
    print("=" * 80)
    dashboards_reales = {}
    
    try:
        for workbook in TSC.Pager(server.workbooks.get):
            dashboards_reales[workbook.id] = {
                'nombre': workbook.name,
                'proyecto': workbook.project_name
            }
        
        print(f"[OK] {len(dashboards_reales)} dashboards encontrados en Tableau\n")
    except Exception as e:
        print(f"[ERROR] Error obteniendo dashboards: {e}")
        sys.exit(1)
    
    # Leer archivo de datos
    print("=" * 80)
    print("[LEYENDO] Archivo de datos")
    print("=" * 80)
    
    if not Path(ARCHIVO_DATOS).exists():
        print(f"[ERROR] No se encontró: {ARCHIVO_DATOS}")
        sys.exit(1)
    
    df = leer_archivo_datos(ARCHIVO_DATOS)
    
    # Mapear columnas
    col_luid = buscar_columna(df, ['WORKBOOK_LUID', 'LUID'])
    col_nombre = buscar_columna(df, ['WORKBOOK'])
    col_ruta = buscar_columna(df, ['RUTA_PROYECTO', 'PROYECTO', 'RUTA'])
    
    if not col_luid:
        print(f"[ERROR] No se encontró columna WORKBOOK_LUID")
        print(f"Columnas disponibles: {', '.join(df.columns)}")
        sys.exit(1)
    if not col_nombre:
        print(f"[ERROR] No se encontró columna WORKBOOK")
        sys.exit(1)
    
    # Renombrar
    df = df.rename(columns={
        col_luid: 'WORKBOOK_LUID',
        col_nombre: 'WORKBOOK'
    })
    
    if col_ruta:
        df = df.rename(columns={col_ruta: 'RUTA_PROYECTO'})
    
    # Comparar
    print("=" * 80)
    print("DIAGNÓSTICO")
    print("=" * 80 + "\n")
    
    encontrados = 0
    no_encontrados = 0
    luid_incorrecto = 0
    
    # Mapeo de LUID correcto para sugerencias
    nombre_a_luid = {db['nombre'].lower(): luid for luid, db in dashboards_reales.items()}
    
    for idx, fila in df.iterrows():
        luid = str(fila['WORKBOOK_LUID']).strip()
        nombre_excel = str(fila['WORKBOOK']).strip()
        
        # Omitir filas vacías
        if not luid or luid.lower() == 'nan':
            continue
        
        if luid in dashboards_reales:
            nombre_real = dashboards_reales[luid]['nombre']
            proyecto_real = dashboards_reales[luid]['proyecto']
            
            if nombre_real == nombre_excel:
                encontrados += 1
                print(f"✅ [{idx+1}] {nombre_excel}")
                print(f"    Proyecto: {proyecto_real}")
                print(f"    LUID: {luid[:20]}...\n")
            else:
                luid_incorrecto += 1
                print(f"⚠️  [{idx+1}] LUID CORRECTO pero NOMBRE DIFERENTE:")
                print(f"    Excel:    {nombre_excel}")
                print(f"    Tableau:  {nombre_real}")
                print(f"    Proyecto: {proyecto_real}")
                print(f"    LUID:     {luid[:20]}...\n")
        else:
            no_encontrados += 1
            print(f"❌ [{idx+1}] NO ENCONTRADO: {nombre_excel}")
            print(f"    LUID: {luid}")
            
            # Buscar si existe con otro LUID
            nombre_excel_lower = nombre_excel.lower()
            if nombre_excel_lower in nombre_a_luid:
                luid_correcto = nombre_a_luid[nombre_excel_lower]
                db_correcto = dashboards_reales[luid_correcto]
                print(f"    💡 Sugerencia: Encontrado en Tableau con LUID diferente:")
                print(f"       Nombre:  {db_correcto['nombre']}")
                print(f"       LUID:    {luid_correcto}")
                print(f"       Proyecto: {db_correcto['proyecto']}")
            else:
                # Buscar nombres similares
                nombres_similares = [
                    (luid_real, db['nombre']) 
                    for luid_real, db in dashboards_reales.items() 
                    if nombre_excel_lower in db['nombre'].lower() or 
                       db['nombre'].lower() in nombre_excel_lower
                ]
                
                if nombres_similares:
                    print(f"    💡 Sugerencias de nombres similares:")
                    for luid_real, nombre_similar in nombres_similares[:3]:
                        print(f"       - {nombre_similar}")
                        print(f"         LUID: {luid_real}")
            print()
    
    # Resumen
    print("=" * 80)
    print("RESUMEN")
    print("=" * 80)
    total = encontrados + luid_incorrecto + no_encontrados
    print(f"✅ Encontrados:        {encontrados}/{total}")
    print(f"⚠️  LUID incorrecto:    {luid_incorrecto}/{total}")
    print(f"❌ No encontrados:     {no_encontrados}/{total}")
    print("=" * 80)
    
    if no_encontrados > 0 or luid_incorrecto > 0:
        print("\n[ACCIÓN RECOMENDADA]")
        print("1. Verifica los LUID con el script de generación de Excel")
        print("2. Revisa los nombres exactamente como aparecen en Tableau")
        print("3. Asegúrate de que tienes permisos para descargar estos dashboards")
    else:
        print("\n✅ ¡TODOS LOS DASHBOARDS ESTÁN LISTOS!")
    
    server.auth.sign_out()

if __name__ == '__main__':
    main()
