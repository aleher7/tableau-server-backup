import pandas as pd

print("[LEYENDO] prueba.dsv...")
df = pd.read_csv('prueba.dsv', sep='|', quoting=3, skipinitialspace=True)

print("[LIMPIANDO] espacios y comillas...")
df.columns = [col.strip().replace('"', '') for col in df.columns]

print("\n" + "="*60)
print("COLUMNAS EN TU ARCHIVO:")
print("="*60)

for i, col in enumerate(df.columns, 1):
    print(f"{i}. [{col}]")

print("\n" + "="*60)
print(f"TOTAL: {len(df.columns)} columnas")
print("="*60)

print("\nPRIMERA FILA (primeros 5 valores):")
for i, col in enumerate(df.columns[:5], 1):
    valor = df[col].iloc[0] if len(df) > 0 else "SIN DATOS"
    print(f"  {col}: {valor}")
