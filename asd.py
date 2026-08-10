"""
Prueba la logica de versionado SIN tocar Oracle, Tableau ni GitHub.

Crea una carpeta temporal con CSV y archivos ficticios, y llama directamente
a las funciones de descargar_workbooks.py que procesan esos datos. Sirve
para comprobar que el parseo, el nombrado "_vX" y el borrado de versiones
caducadas funcionan bien, antes de probar el proceso completo de verdad.

Uso: python prueba_logica_versionado.py
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import descargar_workbooks as dw

CARPETA_PRUEBA = Path("prueba_versionado_temp")


def preparar_escenario():
    """Crea una carpeta temporal con CSV y archivos .twbx ficticios."""
    if CARPETA_PRUEBA.exists():
        shutil.rmtree(CARPETA_PRUEBA)
    (CARPETA_PRUEBA / "descargas").mkdir(parents=True)

    # --- lista_workbooks.csv ficticio (2 workbooks con version nueva) ---
    # Mismo formato que genera Descarga.sql: comillas dobles, separador coma
    csv_workbooks = CARPETA_PRUEBA / "lista_workbooks.csv"
    csv_workbooks.write_text(
        'WORKBOOK_LUID,WORKBOOK,RUTA_PROYECTO,RUTA_LOCAL_DESTINO,OWNER_EMAIL,'
        'ULTIMA_ACTUALIZACION,TIPO_ITEM,VERSION_ACTUAL\n'
        '"luid-aaa-111","Ventas Norte","Comercial","Comercial/Ventas Norte",'
        '"ana@empresa.com","2026-08-10","WORKBOOK","5"\n'
        '"luid-bbb-222","Inventario","Logistica","Logistica/Inventario",'
        '"luis@empresa.com","2026-08-10","WORKBOOK","2"\n'
        '"","N/A (carpeta intermedia)","Comercial","Comercial",'
        '"N/A","","CARPETA INTERMEDIA",""\n',
        encoding='utf-8'
    )

    # --- lista_workbooks_eliminar.csv ficticio (1 version caducada) ---
    csv_eliminar = CARPETA_PRUEBA / "lista_workbooks_eliminar.csv"
    csv_eliminar.write_text(
        'WORKBOOK_LUID,NAME,NAVIGATION\n'
        '"luid-ccc-333","Reporte Antiguo_v1","Comercial"\n',
        encoding='utf-8'
    )

    # Archivo ficticio que representa la version caducada YA en disco
    # (simula que se descargo en una ejecucion anterior)
    carpeta_comercial = CARPETA_PRUEBA / "descargas" / "Comercial"
    carpeta_comercial.mkdir(parents=True, exist_ok=True)
    (carpeta_comercial / "Reporte Antiguo_v1.twbx").write_text("contenido ficticio")

    return csv_workbooks, csv_eliminar, CARPETA_PRUEBA / "descargas"


def main():
    print("=== Preparando escenario ficticio ===")
    csv_workbooks, csv_eliminar, directorio_descargas = preparar_escenario()
    print(f"Carpeta de prueba: {CARPETA_PRUEBA.resolve()}\n")

    # --- Prueba 1: leer_lista_workbooks() ---
    print("=== Prueba 1: leer_lista_workbooks() ===")
    df = dw.leer_lista_workbooks(csv_workbooks)
    if df is None:
        print("FALLO: devolvio None (deberia devolver un DataFrame)")
        return
    print(f"Filas leidas: {len(df)} (se esperan 2: las carpetas intermedias se filtran solas)")
    print(df[['WORKBOOK', 'RUTA_PROYECTO', 'VERSION_ACTUAL']].to_string(index=False))
    assert len(df) == 2, "Deberian quedar 2 filas tras filtrar la carpeta intermedia"
    print("OK\n")

    # --- Prueba 2: el nombrado "_vX" que usaria descargar_y_subir() ---
    print("=== Prueba 2: nombrado de archivo con version ===")
    for _, fila in df.iterrows():
        nombre_esperado = f"{fila['WORKBOOK']}_v{fila['VERSION_ACTUAL']}.twbx"
        destino = Path(directorio_descargas) / fila['RUTA_PROYECTO'] / nombre_esperado
        print(f"  {fila['WORKBOOK']} (v{fila['VERSION_ACTUAL']}) -> {destino.name}")
    print("OK (revisa a mano que los nombres tengan el formato Nombre_vX.twbx)\n")

    # --- Prueba 3: leer_lista_eliminar() ---
    print("=== Prueba 3: leer_lista_eliminar() ===")
    items = dw.leer_lista_eliminar(csv_eliminar)
    print(f"Elementos a eliminar: {len(items)}")
    for it in items:
        print(f"  {it}")
    assert len(items) == 1, "Se esperaba 1 elemento a eliminar"
    print("OK\n")

    # --- Prueba 4: procesar_eliminaciones() borra el archivo de verdad ---
    print("=== Prueba 4: procesar_eliminaciones() ===")
    archivo_antes = directorio_descargas / "Comercial" / "Reporte Antiguo_v1.twbx"
    print(f"Existe ANTES de eliminar: {archivo_antes.exists()}")
    borrados = dw.procesar_eliminaciones(directorio_descargas, items)
    print(f"Archivos borrados: {borrados}")
    print(f"Existe DESPUES de eliminar: {archivo_antes.exists()}")
    assert borrados == 1 and not archivo_antes.exists(), "El archivo deberia haberse borrado"
    print("OK\n")

    # --- Prueba 5: preparar_directorio() no borra nada existente ---
    print("=== Prueba 5: preparar_directorio() no vacia la carpeta ===")
    marcador = directorio_descargas / "Comercial" / "Ventas Norte_v5.twbx"
    marcador.write_text("version que NO deberia desaparecer")
    dw.preparar_directorio(directorio_descargas)
    print(f"Sigue existiendo tras preparar_directorio(): {marcador.exists()}")
    assert marcador.exists(), "preparar_directorio() no deberia borrar archivos existentes"
    print("OK\n")

    print("=== TODAS LAS PRUEBAS PASARON ===")
    print(f"\nPuedes revisar a mano el contenido en: {CARPETA_PRUEBA.resolve()}")
    print("Borra esa carpeta cuando termines de inspeccionarla (no la borra este script).")


if __name__ == '__main__':
    main()
