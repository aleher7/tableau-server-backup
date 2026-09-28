"""
COMPROBACION: que pasa cuando la fuente de datos falla o no esta actualizada
=============================================================================

Simula, contra la logica real de enviar_csv_dashboards.py, varios escenarios
de fuente de datos con problemas: no se refresca hoy, el refresco falla, o
solo se toco la definicion sin refrescar datos. No usa tu Tableau ni tu
Microsoft Graph reales -- todo son datos de prueba, es seguro ejecutarlo
cuando quieras (las fechas se calculan relativas al dia de hoy, asi que
sigue siendo valido lo ejecutes cuando lo ejecutes).

Uso:
    python comprobar_fuente_desactualizada.py

Se ejecuta en la misma carpeta que enviar_csv_dashboards.py (lo importa
directamente). Necesita las mismas librerias que el script principal
(requests, tableauserverclient, openpyxl) ya instaladas.
"""
import sys
from datetime import date, timedelta

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


class MetadataFalsa:
    """Simula servidor.metadata.query() devolviendo la fuente que se le pida."""
    def __init__(self, fuente):
        self.fuente = fuente

    def query(self, consulta):
        return {"data": {"workbooks": [{
            "name": "x",
            "upstreamDatasources": [self.fuente],
            "embeddedDatasources": [],
        }]}}


class ServidorFalso:
    def __init__(self, fuente):
        self.metadata = MetadataFalsa(fuente)


BASE = {
    "name": "Fuente de prueba",
    "extractLastRefreshTime": None,
    "extractLastIncrementalUpdateTime": None,
    "extractLastUpdateTime": None,
}


print("=" * 70)
print("ESCENARIO 1: la carga de datos de hoy aun no ha llegado (caso normal)")
print("=" * 70)
fuente = {**BASE, "extractLastRefreshTime": marca(AYER)}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == AYER, f"detecta correctamente que el ultimo refresco fue ayer (fecha leida: {fecha})")
check(fecha != HOY, "esa fecha NO coincide con hoy -> el envio se descartaria (DESCARTADO)")

print()
print("=" * 70)
print("ESCENARIO 2: el refresco de hoy fallo (Tableau no registra ninguna fecha)")
print("=" * 70)
fuente = {**BASE}  # sin ninguna fecha en ningun campo
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha is None, f"sin ninguna fecha registrada -> None (fue: {fecha}), "
                      "se trataria como error tecnico, no se envia nada")

print()
print("=" * 70)
print("ESCENARIO 3 (el bug real que se corrigio): alguien republico la fuente hoy,")
print("pero el ultimo refresco de datos de verdad fue hace dos dias")
print("=" * 70)
fuente = {**BASE,
          "extractLastRefreshTime": marca(HACE_DOS_DIAS),   # dato real: hace 2 dias
          "extractLastUpdateTime": marca(HOY)}              # republicado hoy, sin refrescar
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == HACE_DOS_DIAS,
      f"usa la fecha del refresco REAL, ignora la republicacion de hoy (fue: {fecha})")
check(fecha != HOY, "no se deja enganar por la fecha de hoy de extractLastUpdateTime")

print()
print("=" * 70)
print("ESCENARIO 4: refresco incremental de hoy, aunque el ultimo refresco COMPLETO")
print("sea antiguo (fuente con carga incremental diaria)")
print("=" * 70)
fuente = {**BASE,
          "extractLastRefreshTime": marca(HACE_TRES_SEMANAS),   # completo, antiguo
          "extractLastIncrementalUpdateTime": marca(HOY)}       # incremental, hoy
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha == HOY, f"un incremental de hoy SI cuenta como actualizado (fue: {fecha})")

print()
print("=" * 70)
print("ESCENARIO 5: fuente conectada EN VIVO (sin ningun extracto que refrescar)")
print("=" * 70)
fuente = {**BASE, "name": "Fuente en vivo (sin extracto)"}
fecha = m.fecha_actualizacion_fuentes(ServidorFalso(fuente), "wb-prueba")
check(fecha is None, f"sin extracto -> None (fue: {fecha}), se ignora esta fuente")

print()
print("=" * 70)
print(f"RESULTADO FINAL: {fallos} fallo(s)")
print("=" * 70)
if fallos:
    print("Algo no se comporta como se espera -- revisar enviar_csv_dashboards.py")
    sys.exit(1)
else:
    print("Todos los escenarios de fuente fallida/desactualizada se detectan "
          "correctamente: en ninguno de ellos se enviaria el correo.")
