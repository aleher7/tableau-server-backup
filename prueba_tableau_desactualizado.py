"""
PRUEBA: Oracle al dia, pero Tableau NO -- descarte real con conexiones reales
================================================================================

Se conecta de VERDAD a Oracle y a Tableau (mismas credenciales de
config_envio.json), pero sustituye unicamente la funcion que lee la fecha
del extracto de Tableau (fecha_actualizacion_fuentes) para que siempre
devuelva "ayer", sin tocar la conexion real ni el resto de la logica.

Con esto se ve el mensaje DESCARTADO exacto que saldria un dia real en que
Oracle ya tuviera los datos de hoy pero el extracto de Tableau aun no se
hubiera refrescado.

Seguro de ejecutar: usa --sin-enviar (no puede enviar nada), y ademas el
descarte por fecha nunca llega a la parte de envio de todas formas.

Uso (desde la carpeta del proyecto, con config_envio.json al lado):
    python prueba_tableau_desactualizado.py
"""
import sys
from datetime import date, datetime, timedelta, timezone

import enviar_csv_dashboards as m

AYER = date.today() - timedelta(days=1)


def fecha_tableau_falsa(servidor, workbook_luid):
    print(f"    (prueba) fecha_actualizacion_fuentes() sustituida: devuelve {AYER} en vez de la real")
    return datetime.combine(AYER, datetime.min.time()).replace(hour=5, tzinfo=timezone.utc)


m.fecha_actualizacion_fuentes = fecha_tableau_falsa

sys.argv = ["enviar_csv_dashboards.py", "--sin-enviar"]
m.main()
