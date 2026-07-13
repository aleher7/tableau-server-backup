import pandas as pd

print("[LEYENDO] prueba.dsv...")
df = pd.read_csv('prueba.dsv', sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')

print("[LIMPIANDO] espacios y comillas...")
df.columns = [col.strip().replace('"', '') for col in df.columns]

print("\n" + "="*80)
print("ORDEN DE COLUMNAS EN TU ARCHIVO:")
print("="*80)

for i, col in enumerate(df.columns):
    print(f"{i+1}. {col}")

print("\n" + "="*80)
print(f"TOTAL: {len(df.columns)} columnas")
print("="*80)

print("\nPRIMERA FILA - PRIMERAS 3 COLUMNAS (para ver qué datos tiene cada una):")
for i, col in enumerate(df.columns[:3]):
    valor = df[col].iloc[0] if len(df) > 0 else "SIN DATOS"
    print(f"\n{i+1}. [{col}]")
    print(f"   Valor: {valor}")
