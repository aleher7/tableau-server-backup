import pandas as pd
 
# Lee el archivo
print("[LEYENDO] archivo prueba.dsv...")
df = pd.read_csv('prueba.dsv', sep='|')
 
print(f"\n[TOTAL] Columnas encontradas: {len(df.columns)}")
print("\n[COLUMNAS] Nombres exactos (entre corchetes):")
for i, col in enumerate(df.columns, 1):
    print(f"  {i}. [{col}]")
 
print("\n[LIMPIADAS] Después de limpiar espacios:")
df.columns = df.columns.str.strip()
for i, col in enumerate(df.columns, 1):
    print(f"  {i}. [{col}]")
 
print(f"\n[FILAS] Total de filas: {len(df)}")
print(f"\n[PRIMERAS] 3 filas:")
print(df.head(3))
 
