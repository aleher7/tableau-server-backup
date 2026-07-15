#!/usr/bin/env python3
"""
Script de diagnóstico: Compara tu Excel con dashboards reales en Tableau
Identifica cuáles faltan, tienen LUID incorrecto, etc.
"""

import tableauserverclient as TSC
import pandas as pd

# CONFIGURACIÓN
TABLEAU_SERVER = "https://tu_tableau_server"
TOKEN_NAME = "tu_token_name"
TOKEN = "tu_token"
SITE_ID = "tu_site"
ARCHIVO_EXCEL = "workbooks.xlsx"

# Autenticar
print("[CONECTANDO] Tableau Server...")
tableau_auth = TSC.PersonalAccessTokenAuth(
    token_name=TOKEN_NAME,
    personal_access_token=TOKEN,
    site_id=SITE_ID
)

server = TSC.Server(TABLEAU_SERVER)
server.auth.sign_in(tableau_auth)
print("[OK] Conectado\n")

# Obtener dashboards reales
print("[OBTENIENDO] Dashboards desde Tableau...")
dashboards_reales = {}

for workbook in TSC.Pager(server.workbooks.get):
    dashboards_reales[workbook.id] = {
        'nombre': workbook.name,
        'proyecto': workbook.project_name
    }

print(f"[OK] {len(dashboards_reales)} dashboards encontrados en Tableau\n")

# Leer Excel
print(f"[LEYENDO] {ARCHIVO_EXCEL}...")
df = pd.read_excel(ARCHIVO_EXCEL)
print(f"[OK] {len(df)} filas en Excel\n")

# Comparar
print("=" * 80)
print("DIAGNÓSTICO")
print("=" * 80)

encontrados = 0
no_encontrados = 0
luid_incorrecto = 0

for idx, fila in df.iterrows():
    luid = str(fila['WORKBOOK_LUID']).strip()
    nombre_excel = str(fila['WORKBOOK']).strip()
    
    if luid in dashboards_reales:
        nombre_real = dashboards_reales[luid]['nombre']
        proyecto_real = dashboards_reales[luid]['proyecto']
        
        if nombre_real == nombre_excel:
            encontrados += 1
            print(f"✅ {idx+1}. {nombre_excel}")
        else:
            luid_incorrecto += 1
            print(f"⚠️  {idx+1}. LUID correcto pero NOMBRE DIFERENTE:")
            print(f"   Excel: {nombre_excel}")
            print(f"   Tableau: {nombre_real}")
    else:
        no_encontrados += 1
        print(f"❌ {idx+1}. NO ENCONTRADO: {nombre_excel}")
        print(f"   LUID: {luid}")
        
        # Buscar si existe con otro LUID
        nombres_similares = [
            db for luid_real, db in dashboards_reales.items() 
            if db['nombre'].lower() == nombre_excel.lower()
        ]
        
        if nombres_similares:
            print(f"   💡 Sugerencia: Encontrado con LUID diferente:")
            for db in nombres_similares:
                print(f"      Nombre: {db['nombre']}")
                # Buscar el LUID correcto
                for luid_real, db_info in dashboards_reales.items():
                    if db_info == db:
                        print(f"      LUID CORRECTO: {luid_real}")

print("\n" + "=" * 80)
print("RESUMEN")
print("=" * 80)
print(f"✅ Encontrados:        {encontrados}")
print(f"⚠️  LUID incorrecto:    {luid_incorrecto}")
print(f"❌ No encontrados:     {no_encontrados}")
print(f"   TOTAL:             {len(df)}")
print("=" * 80)

server.auth.sign_out()
