import pandas as pd

print("[LEYENDO] prueba.dsv SIN PROCESAR...")
df = pd.read_csv('prueba.dsv', sep=',', quotechar='"', doublequote=True, skipinitialspace=True, on_bad_lines='skip')

print("\n" + "="*100)
print("COLUMNAS ORIGINALES (tal como pandas las lee):")
print("="*100)

for i, col in enumerate(df.columns):
    print(f"{i+1}. [{col}]")

print("\n" + "="*100)
print("PRIMEROS 3 VALORES DE CADA COLUMNA (para ver si están desalineados):")
print("="*100)

for col in df.columns:
    print(f"\nCOLUMNA: {col}")
    for idx in range(min(3, len(df))):
        valor = str(df[col].iloc[idx])[:80]
        print(f"  Fila {idx+1}: {valor}")

print("\n" + "="*100)
print("DATOS CRUDOS - Primera fila completa:")
print("="*100)
for col in df.columns:
    print(f"{col} = {df[col].iloc[0]}")
