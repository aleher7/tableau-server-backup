"""
COMPROBACION: que pasa cuando la fuente de datos falla o no esta actualizada
=============================================================================

Simula, contra la logica real de enviar_csv_dashboards.py, varios escenarios
de fuente de datos con problemas: no se refresca hoy, Oracle no responde,
Tableau no responde, solo se toco la definicion sin refrescar datos, la
Metadata API va con retraso, o el extracto se refresco antes de que
terminara la carga de Oracle.
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
from datetime import date, timedelta, datetime, timezone

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
# PARTE 1: Oracle (fecha_actualizacion_oracle) -- comprobacion obligatoria
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
    "oracle_dsn": "golddb_high", "oracle_user": "CLABS_ANL_PRO",
    "oracle_password": "prueba", "oracle_tabla": "ANL_VENTA_INTERNA_ESP",
    "oracle_columna_fecha": "DATE_UPD",
}

print("=" * 70)
print("PARTE 1: Oracle (los datos de origen, comprobacion obligatoria)")
print("=" * 70)

print()
print("--- 1.1: Oracle responde con la fecha de hoy ---")
sys.modules['oracledb'] = FakeOracleOK("{:%d/%m/%Y}, 00:37".format(HOY))
fecha = m.fecha_actualizacion_oracle(CFG_ORACLE)
check(fecha is not None and fecha.date() == HOY and fecha.strftime('%H:%M') == '00:37',
      f"detecta que los datos estan al dia, con la hora de la carga (leido: {fecha})")

print()
print("--- 1.2: Oracle responde con una fecha antigua (dato realmente desactualizado) ---")
sys.modules['oracledb'] = FakeOracleOK(datetime.combine(HACE_DOS_DIAS, datetime.min.time()))
fecha = m.fecha_actualizacion_oracle(CFG_ORACLE)
check(fecha is not None and fecha.date() == HACE_DOS_DIAS,
      f"detecta que los datos NO estan al dia (leido: {fecha}) -> se descartaria el envio")

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


class JobsFalsos:
    """Historial de refrescos de Tableau (API REST) simulado."""
    def __init__(self, trabajos):
        self.trabajos = trabajos
    def get(self, req_options=None):
        from tableauserverclient.models.pagination_item import PaginationItem
        return self.trabajos, PaginationItem()


class ServidorFalso:
    def __init__(self, fuente, trabajos=()):
        self.metadata = MetadataFalsa(fuente)
        self.jobs = JobsFalsos(list(trabajos))


BASE_TABLEAU = {
    "name": "Fuente de prueba",
    "extractLastRefreshTime": None,
    "extractLastIncrementalUpdateTime": None,
    "extractLastUpdateTime": None,
}


def dia(momento):
    """Fecha local de un momento de Tableau (fecha_actualizacion_fuentes da fecha y hora)."""
    return m.a_fecha_local(momento) if momento is not None else None

print()
print("=" * 70)
print("PARTE 2: Tableau (el extracto del dashboard, comprobacion obligatoria)")
print("=" * 70)

print()
print("--- 2.1: la carga de datos de hoy aun no ha llegado (caso normal) ---")
fuente = {**BASE_TABLEAU, "extractLastRefreshTime": marca(AYER)}
fecha = dia(m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba"))
check(fecha == AYER, f"detecta correctamente que el ultimo refresco fue ayer (fecha leida: {fecha})")

print()
print("--- 2.2: el refresco de hoy fallo (Tableau no registra ninguna fecha) ---")
fuente = {**BASE_TABLEAU}
fecha = dia(m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba"))
check(fecha is None, f"sin ninguna fecha registrada -> None (fue: {fecha})")

print()
print("--- 2.3: fuente republicada hoy sin refrescar datos de verdad ---")
fuente = {**BASE_TABLEAU,
          "extractLastRefreshTime": marca(HACE_DOS_DIAS),
          "extractLastUpdateTime": marca(HOY)}
fecha = dia(m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba"))
check(fecha == HACE_DOS_DIAS, f"usa la fecha del refresco REAL, ignora la republicacion (fue: {fecha})")

print()
print("--- 2.4: refresco incremental de hoy, completo antiguo ---")
fuente = {**BASE_TABLEAU,
          "extractLastRefreshTime": marca(HACE_TRES_SEMANAS),
          "extractLastIncrementalUpdateTime": marca(HOY)}
fecha = dia(m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba"))
check(fecha == HOY, f"un incremental de hoy SI cuenta como actualizado (fue: {fecha})")

print()
print("--- 2.5: la Metadata API se retrasa (dice ayer), el historial de refrescos dice hoy ---")
from tableauserverclient.models.job_item import BackgroundJobItem
fin_hoy = datetime.combine(HOY, datetime.min.time()).replace(hour=5, tzinfo=timezone.utc)
refresco_hoy = BackgroundJobItem("id", fin_hoy, 0, "refresh_extracts_via_bridge", "Success",
                                 "Fuente de prueba", "Data Source", fin_hoy, fin_hoy)
fuente = {**BASE_TABLEAU, "extractLastRefreshTime": marca(AYER)}
fecha = dia(m.fecha_actualizacion_fuentes(ServidorFalso(fuente, [refresco_hoy]), "wb-prueba"))
check(fecha == HOY, f"el historial de refrescos corrige el retraso de la Metadata API (fue: {fecha})")


# ============================================================================
# PARTE 3: la doble comprobacion completa, con la funcion REAL del script
# (comprobar_fechas): Oracle Y Tableau con fecha de hoy, y el refresco de
# Tableau posterior a la carga de Oracle. Solo se localiza el workbook de
# forma simulada; el resto es exactamente el codigo que corre cada manana.
# ============================================================================

m.localizar_vista = lambda servidor, config, informe: (None, "wb-prueba")
REFERENCIA = {"nombre": "Informe de prueba"}


def resultado_con(valor_oracle, fuente_tableau):
    sys.modules['oracledb'] = valor_oracle
    return m.comprobar_fechas(ServidorFalso(fuente_tableau), CFG_ORACLE, REFERENCIA, HOY)


print()
print("=" * 70)
print("PARTE 3: doble comprobacion completa (la logica real de cada manana)")
print("=" * 70)

fuente_hoy = {**BASE_TABLEAU, "extractLastRefreshTime": marca(HOY)}          # 05:00 UTC
fuente_ayer = {**BASE_TABLEAU, "extractLastRefreshTime": marca(AYER)}

print()
print("--- 3.1: carga de Oracle hoy de madrugada, refresco de Tableau despues -> SI se enviaria ---")
r = resultado_con(FakeOracleOK("{:%d/%m/%Y}, 00:37".format(HOY)), fuente_hoy)
check(r == 'ok', f"las dos confirman y el orden es correcto -> se enviaria (fue: {r})")

print()
print("--- 3.2: Oracle=hoy PERO Tableau=ayer (extracto sin refrescar) -> NO se enviaria ---")
r = resultado_con(FakeOracleOK("{:%d/%m/%Y}, 00:37".format(HOY)), fuente_ayer)
check(r == 'descartado', f"el dashboard de Tableau aun no lo refleja -> NO se enviaria (fue: {r})")

print()
print("--- 3.3: Oracle=ayer PERO Tableau=hoy -> tampoco se enviaria ---")
r = resultado_con(FakeOracleOK("{:%d/%m/%Y}, 00:37".format(AYER)), fuente_hoy)
check(r == 'descartado', f"Oracle no esta al dia aunque Tableau si -> NO se enviaria (fue: {r})")

print()
print("--- 3.4: Oracle falla tecnicamente (aunque Tableau este bien) -> error, no se enviaria ---")
r = resultado_con(FakeOracleRoto(), fuente_hoy)
check(r == 'error', f"Oracle no responde -> no se puede confirmar, error (fue: {r})")

print()
print("--- 3.5: la carga de Oracle se retrasa y termina DESPUES del refresco de Tableau ---")
r = resultado_con(FakeOracleOK("{:%d/%m/%Y}, 07:50".format(HOY)), fuente_hoy)
check(r == 'descartado', "las dos dicen hoy, pero el extracto se refresco antes de la carga "
                         f"(sigue con los datos de ayer) -> NO se enviaria (fue: {r})")

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
