"""
COMPROBACION: que pasa cuando la fuente de datos falla o no esta actualizada
=============================================================================

Simula, contra la logica real de enviar_csv_dashboards.py, varios escenarios
de fuente de datos con problemas: no se refresca hoy, Oracle no responde,
Tableau no responde, o solo se toco la definicion sin refrescar datos.
No usa tu Oracle ni tu Tableau ni tu Microsoft Graph reales -- todo son
datos de prueba, es seguro ejecutarlo cuando quieras (las fechas se
calculan relativas al dia de hoy).

Uso:
    python comprobar_fuente_desactualizada.py

Se ejecuta en la misma carpeta que enviar_csv_dashboards.py (lo importa
directamente). Necesita las mismas librerias que el script principal
(requests, tableauserverclient, openpyxl, oracledb) ya instaladas.
"""
import sys
from datetime import date, timedelta, datetime

import enviar_csv_dashboards as m

HOY = date.today()
AYER = HOY - timedelta(days=1)
HACE_DOS_DIAS = HOY - timedelta(days=2)
HACE_TRES_SEMANAS = HOY - timedelta(days=21)


def marca(dia):
    """Convierte un date en una marca de tiempo UTC como las de la Metadata API."""
    return f"{dia.isoformat()}T05:00:00Z"


fallos = 0


def check(condicion, mensaje):
    global fallos
    print(("OK  " if condicion else "MAL "), mensaje)
    if not condicion:
        fallos += 1


# ============================================================================
# PARTE 1: Oracle (fecha_actualizacion_oracle) -- la comprobacion PRIORITARIA
# ============================================================================

class FakeCursorOracle:
    def __init__(s, valor):
        s.valor = valor
    def execute(s, sql):
        pass
    def fetchone(s):
        return (s.valor,)
    def __enter__(s):
        return s
    def __exit__(s, *a):
        return False


class FakeConexionOracle:
    def __init__(s, valor):
        s.valor = valor
    def cursor(s):
        return FakeCursorOracle(s.valor)
    def __enter__(s):
        return s
    def __exit__(s, *a):
        return False


class FakeOracleOK:
    """Simula una conexion Oracle que SI responde, con el valor que se le pida."""
    def __init__(s, valor):
        s.valor = valor
    def connect(s, **kw):
        return FakeConexionOracle(s.valor)


class FakeOracleRoto:
    """Simula que Oracle no esta disponible (red caida, credenciales, etc.)."""
    def connect(s, **kw):
        raise RuntimeError("no se pudo conectar a Oracle (simulado)")


CFG_ORACLE = {
    "oracle_dsn": "golddb_high", "oracle_usuario": "CLABS_ANL_PRO",
    "oracle_password": "prueba", "oracle_tabla": "ANL_VENTA_INTERNA_ESP",
    "oracle_columna_fecha": "DATE_UPD",
}

print("=" * 70)
print("PARTE 1: Oracle (los datos de origen, comprobacion obligatoria)")
print("=" * 70)

print()
print("--- 1.1: Oracle responde con la fecha de hoy ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(HOY, datetime.min.time()))
fecha = m.fecha_actualizacion_oracle(CFG_ORACLE)
check(fecha == HOY, f"detecta que los datos estan al dia (fecha leida: {fecha})")

print()
print("--- 1.2: Oracle responde con una fecha antigua (dato realmente desactualizado) ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(HACE_DOS_DIAS, datetime.min.time()))
fecha = m.fecha_actualizacion_oracle(CFG_ORACLE)
check(fecha == HACE_DOS_DIAS and fecha != HOY,
      f"detecta que los datos NO estan al dia (fecha leida: {fecha}) -> se descartaria el envio")

print()
print("--- 1.3: la tabla esta vacia (MAX devuelve NULL) ---")
sys.modules['oracledb'] = FakeOracleOK(None)
fecha = m.fecha_actualizacion_oracle(CFG_ORACLE)
check(fecha is None, f"tabla vacia -> None (fue: {fecha}); como Oracle es obligatoria, "
                      "no se enviaria nada esta pasada")

print()
print("--- 1.4: Oracle no esta disponible (fallo de conexion) ---")
sys.modules['oracledb'] = FakeOracleRoto()
try:
    m.fecha_actualizacion_oracle(CFG_ORACLE)
    check(False, "deberia lanzar una excepcion")
except Exception as e:
    check(True, f"lanza la excepcion tal cual ({e}) -- main() la atrapa como error tecnico, "
                "no se enviaria nada aunque Tableau estuviera perfectamente al dia")

del sys.modules['oracledb']


# ============================================================================
# PARTE 2: Tableau (fecha_actualizacion_fuentes) -- la OTRA comprobacion
# obligatoria (el dashboard/extracto tiene que estar al dia, ademas de Oracle)
# ============================================================================

class MetadataFalsa:
    def __init__(self, fuente):
        self.fuente = fuente
    def query(self, consulta):
        return {"data": {"workbooks": [{
            "name": "x", "upstreamDatasources": [self.fuente], "embeddedDatasources": [],
        }]}}


class ServidorFalso:
    def __init__(self, fuente):
        self.metadata = MetadataFalsa(fuente)


BASE_TABLEAU = {
    "name": "Fuente de prueba",
    "extractLastRefreshTime": None,
    "extractLastIncrementalUpdateTime": None,
    "extractLastUpdateTime": None,
}

print()
print("=" * 70)
print("PARTE 2: Tableau (el respaldo, si Oracle falla)")
print("=" * 70)

print()
print("--- 2.1: la carga de datos de hoy aun no ha llegado (caso normal) ---")
fuente = {**BASE_TABLEAU, "extractLastRefreshTime": marca(AYER)}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == AYER, f"detecta correctamente que el ultimo refresco fue ayer (fecha leida: {fecha})")

print()
print("--- 2.2: el refresco de hoy fallo (Tableau no registra ninguna fecha) ---")
fuente = {**BASE_TABLEAU}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha is None, f"sin ninguna fecha registrada -> None (fue: {fecha})")

print()
print("--- 2.3: fuente republicada hoy sin refrescar datos de verdad ---")
fuente = {**BASE_TABLEAU,
          "extractLastRefreshTime": marca(HACE_DOS_DIAS),
          "extractLastUpdateTime": marca(HOY)}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == HACE_DOS_DIAS, f"usa la fecha del refresco REAL, ignora la republicacion (fue: {fecha})")

print()
print("--- 2.4: refresco incremental de hoy, completo antiguo ---")
fuente = {**BASE_TABLEAU,
          "extractLastRefreshTime": marca(HACE_TRES_SEMANAS),
          "extractLastIncrementalUpdateTime": marca(HOY)}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == HOY, f"un incremental de hoy SI cuenta como actualizado (fue: {fecha})")


# ============================================================================
# PARTE 3: la doble comprobacion completa -- Oracle Y Tableau son las dos
# obligatorias; solo se enviaria si las DOS confirman que es hoy.
# ============================================================================

def se_enviaria(config_oracle, servidor_tableau_falso, workbook_luid, hoy):
    """Reproduce la misma logica AND que usa main()."""
    try:
        fecha_oracle = m.fecha_actualizacion_oracle(config_oracle)
    except Exception:
        fecha_oracle = None
    try:
        fecha_tableau = m.fecha_actualizacion_fuentes(servidor_tableau_falso, workbook_luid)
    except Exception:
        fecha_tableau = None
    if fecha_oracle is None or fecha_tableau is None:
        return None  # error tecnico: no se puede confirmar ninguna de las dos
    return fecha_oracle == hoy and fecha_tableau == hoy


print()
print("=" * 70)
print("PARTE 3: doble comprobacion completa (Oracle Y Tableau, las dos obligatorias)")
print("=" * 70)

print()
print("--- 3.1: Oracle=hoy Y Tableau=hoy -> SI se enviaria ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(HOY, datetime.min.time()))
fuente_hoy = {**BASE_TABLEAU, "extractLastRefreshTime": marca(HOY)}
resultado = se_enviaria(CFG_ORACLE, ServidorFalso(fuente_hoy), "wb", HOY)
check(resultado is True, f"las dos confirman -> se enviaria (fue: {resultado})")

print()
print("--- 3.2: Oracle=hoy PERO Tableau=ayer (extracto sin refrescar) -> NO se enviaria ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(HOY, datetime.min.time()))
fuente_ayer = {**BASE_TABLEAU, "extractLastRefreshTime": marca(AYER)}
resultado = se_enviaria(CFG_ORACLE, ServidorFalso(fuente_ayer), "wb", HOY)
check(resultado is False,
      f"Oracle dice hoy pero el dashboard de Tableau aun no lo refleja -> NO se enviaria (fue: {resultado})")

print()
print("--- 3.3: Oracle=ayer PERO Tableau=hoy -> tampoco se enviaria ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(AYER, datetime.min.time()))
resultado = se_enviaria(CFG_ORACLE, ServidorFalso(fuente_hoy), "wb", HOY)
check(resultado is False,
      f"Oracle no esta al dia aunque Tableau si -> NO se enviaria (fue: {resultado})")

print()
print("--- 3.4: Oracle falla tecnicamente (aunque Tableau este bien) -> error, no se enviaria ---")
sys.modules['oracledb'] = FakeOracleRoto()
resultado = se_enviaria(CFG_ORACLE, ServidorFalso(fuente_hoy), "wb", HOY)
check(resultado is None, f"Oracle no responde -> no se puede confirmar, error (fue: {resultado})")

del sys.modules['oracledb']

print()
print("=" * 70)
print(f"RESULTADO FINAL: {fallos} fallo(s)")
print("=" * 70)
if fallos:
    print("Algo no se comporta como se espera -- revisar enviar_csv_dashboards.py")
    sys.exit(1)
else:
    print("Todos los escenarios (Oracle, Tableau y la doble comprobacion) se "
          "detectan correctamente.")
