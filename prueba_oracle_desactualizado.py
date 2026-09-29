"""
PRUEBA: Tableau al dia, pero Oracle NO -- descarte real con conexiones reales
================================================================================

Al reves que prueba_tableau_desactualizado.py: se conecta de VERDAD a Oracle
y a Tableau, pero sustituye unicamente la funcion que lee la fecha en Oracle
(fecha_actualizacion_oracle) para que siempre devuelva "ayer", sin tocar la
conexion real a Oracle ni el resto de la logica.

Con esto se ve el mensaje DESCARTADO exacto que saldria un dia real en que
el extracto de Tableau ya estuviera al dia pero los datos de origen en
Oracle aun no se hubieran actualizado.

Seguro de ejecutar: usa --sin-enviar (no puede enviar nada), y ademas el
descarte por fecha nunca llega a la parte de envio de todas formas.

Uso (desde la carpeta del proyecto, con config_envio.json al lado):
    python prueba_oracle_desactualizado.py
"""
import sys
from datetime import date, datetime, timedelta

import enviar_csv_dashboards as m

AYER = date.today() - timedelta(days=1)


def fecha_oracle_falsa(config):
    print(f"    (prueba) fecha_actualizacion_oracle() sustituida: devuelve {AYER} en vez de la real")
    return datetime.combine(AYER, datetime.min.time()).replace(minute=37)


m.fecha_actualizacion_oracle = fecha_oracle_falsa

sys.argv = ["enviar_csv_dashboards.py", "--sin-enviar"]
m.main()
