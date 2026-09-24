"""
ENVIO DIARIO POR CORREO DE TABLAS DE TABLEAU EN CSV
====================================================

Flujo, para la lista 'informes' de config_envio.json (cada uno por su
'nombre', localizado en Tableau dentro de la carpeta 'proyecto_ruta'):
    1. Los 8 informes comparten la misma fuente de datos, asi que su fecha
       de actualizacion se comprueba UNA sola vez (consultando la Metadata
       API con el primer informe pendiente), no una vez por informe.
       Si el workbook lee EN VIVO de una base de datos (Tableau no guarda
       fecha de refresco), se busca a traves de sus tablas de origen: se toma
       el extracto publicado mas reciente que se alimenta de esas mismas
       tablas, dejando aviso en el log.
    2. Si esa fecha NO es HOY (la carga del dia no ha llegado o ha fallado),
       no se descarga ni se envia nada, bajo ninguna circunstancia: se anota
       en el log y se reintenta todo junto en la siguiente pasada.
    3. Si es HOY, se descarga la tabla en CSV de cada uno de los 8 informes
       (misma disposicion visual que el dashboard). Todo o nada: se envia
       si y solo si los 8 se han podido generar. Si alguno falla por un
       problema TECNICO al descargarlo (no por fecha, que ya se sabe que
       esta bien), no se envia NADA esta pasada -- no hay aviso a medias,
       se reintenta todo junto en la siguiente. Si todos salen bien, se
       agrupan por destinatarios y se envian en el MENOR numero de correos
       posible (uno por grupo, con todos sus CSV adjuntos), siempre por
       Microsoft Graph.

El script es idempotente por dia: guarda en estado_envios.json que informes
ya se enviaron hoy y no los repite. Por eso la tarea programada puede
lanzarse varias veces por la manana (por ejemplo cada 30 min de 08:00 a
11:00): cada informe sale en cuanto sus datos estan actualizados, y una sola
vez.

Uso:
    python enviar_csv_dashboards.py                     # proceso normal
    python enviar_csv_dashboards.py --sin-enviar        # prueba: descarga y
                                                        # comprueba, no envia
    python enviar_csv_dashboards.py --fecha 2026-09-21  # simular otro "hoy"
    python enviar_csv_dashboards.py --diagnostico       # que fuentes ve la
                                                        # Metadata API por informe
    python enviar_csv_dashboards.py --crosstab-excel "CdM SRI Marca MTD"
                                                        # prueba: baja ese informe
                                                        # como Excel (crosstab)
    python enviar_csv_dashboards.py --probar-correo tu@correo.com
                                                        # prueba solo el envio de
                                                        # correo por Graph (sin Tableau)
    python enviar_csv_dashboards.py --forzar            # ignora lo ya enviado

Codigo de salida: 0 si todo fue bien (incluidos los informes descartados por
fecha, que es un caso normal), 1 si hubo errores tecnicos (Tableau, Graph...).
"""

import re
import sys
import csv
import json
import time
import base64
import logging
import argparse
import requests
from io import BytesIO
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from datetime import datetime, date


# ============================================================================
# LOG
# ============================================================================
# Sin emojis: la consola del servidor no siempre esta en UTF-8.
# Python pone los niveles en ingles (INFO/WARNING/ERROR/...) por defecto; se
# traducen aqui para que el log quede entero en espanol.
logging.addLevelName(logging.WARNING, 'AVISO')
logging.addLevelName(logging.CRITICAL, 'CRITICO')
logging.addLevelName(logging.DEBUG, 'DEPURACION')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('envio_csv_dashboards.log', encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ============================================================================
# CONFIGURACION
# ============================================================================

CLAVES_TABLEAU = ['tableau_server', 'tableau_token_name', 'tableau_token', 'tableau_site']
CLAVES_CORREO = ['destinatarios']
CLAVES_GRAPH = ['graph_tenant_id', 'graph_client_id', 'graph_remitente']
CLAVES_OPCIONALES = {
    'graph_client_secret': '',
    'directorio_salida': './csv_generados',
    'archivo_estado': './estado_envios.json',
    'csv_separador': ';',
    'cache_maxima_minutos': 1,
    'rellenar_etiquetas': True,
    'separador_miles': False,
}


def cargar_config(fichero):
    """
    Carga config_envio.json, aplica valores por defecto y valida.

    Args:
        fichero: ruta del fichero de configuracion.

    Returns:
        Diccionario de configuracion validado. Si algo falta o esta mal, el
        programa termina aqui (sys.exit) con un mensaje claro.
    """
    try:
        with open(fichero, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error("No se encuentra %s (carpeta actual: %s)", fichero, Path.cwd())
        log.error("Comprueba el campo 'Iniciar en' de la tarea programada")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("El fichero %s tiene un error de sintaxis: %s", fichero, e)
        sys.exit(1)

    for clave, valor in CLAVES_OPCIONALES.items():
        config.setdefault(clave, valor)

    obligatorias = CLAVES_TABLEAU + CLAVES_CORREO + CLAVES_GRAPH + ['informes', 'proyecto_ruta']
    faltan = [c for c in obligatorias if c not in config or config[c] in ('', [])]
    if faltan:
        log.error("Faltan claves obligatorias en %s: %s", fichero, ", ".join(faltan))
        sys.exit(1)

    import os
    if not config['graph_client_secret'] and not os.environ.get('GRAPH_CLIENT_SECRET'):
        log.error("Falta 'graph_client_secret' en %s (o la variable de entorno "
                  "GRAPH_CLIENT_SECRET)", fichero)
        sys.exit(1)

    if not config['informes']:
        log.error("La lista 'informes' esta vacia")
        sys.exit(1)

    for i, informe in enumerate(config['informes'], start=1):
        if not informe.get('nombre'):
            log.error("El informe %d no tiene 'nombre'", i)
            sys.exit(1)

    return config


# ============================================================================
# UTILIDADES
# ============================================================================

CARACTERES_INVALIDOS_WINDOWS = str.maketrans('', '', '<>:"/\\|?*')


def sanear_nombre_archivo(nombre):
    """
    Quita los caracteres que Windows no permite en nombres de archivo.

    Args:
        nombre: nombre original del informe.

    Returns:
        El nombre sin caracteres invalidos ni espacios sobrantes.
    """
    return nombre.translate(CARACTERES_INVALIDOS_WINDOWS).strip()


_SUFIJO_FECHA_CSV = re.compile(r'_\d{4}-\d{2}-\d{2}\.csv$')


def limpiar_csv_antiguos(directorio, hoy):
    """
    Borra del directorio de salida los CSV de informes de dias anteriores:
    solo se conserva el de hoy de cada informe, no se acumulan indefinida-
    mente. Un fichero que no siga el patron '..._AAAA-MM-DD.csv' (por
    ejemplo prueba_correo.csv) se deja intacto.

    Args:
        directorio: carpeta de CSV generados.
        hoy: objeto date del dia de envio.

    Returns:
        No devuelve nada.
    """
    carpeta = Path(directorio)
    if not carpeta.is_dir():
        return

    sufijo_hoy = f"_{hoy.isoformat()}.csv"
    borrados = 0
    for fichero in carpeta.glob('*.csv'):
        if fichero.name.endswith(sufijo_hoy) or not _SUFIJO_FECHA_CSV.search(fichero.name):
            continue
        try:
            fichero.unlink()
            borrados += 1
        except OSError as e:
            log.warning("No se pudo borrar %s: %s", fichero.name, e)

    if borrados:
        log.info("Limpieza: %d CSV de dias anteriores borrados de %s", borrados, carpeta)




_NUMERO = re.compile(r'^[\s€$£]*[-+]?[\d.,]+[\s€$£]*$')


def interpretar_numero(texto):
    """
    Lee un numero escrito en formato espanol o ingles, con o sin simbolo de
    moneda, y lo devuelve como Decimal.

    Reglas para separar miles de decimales: si trae coma y punto, el ultimo
    es el decimal; si trae un solo tipo de separador repetido, o una vez con
    exactamente 3 cifras detras, es de miles ('3.580.783', '1.438');
    en cualquier otro caso es decimal ('1500,5', '1500.25'). Un decimal con
    exactamente 3 cifras ('0,500') se leeria como miles: es la unica
    ambiguedad, poco habitual en importes.

    Args:
        texto: valor tal como viene en el CSV.

    Returns:
        Decimal, o None si el texto no es un numero.
    """
    t = (texto or '').replace(' ', ' ')
    if not _NUMERO.match(t):
        return None
    t = re.sub(r'[^\d,.+-]', '', t)
    if ',' in t and '.' in t:
        if t.rfind(',') > t.rfind('.'):
            t = t.replace('.', '').replace(',', '.')
        else:
            t = t.replace(',', '')
    else:
        for sep in ',.':
            if sep in t:
                partes = t.split(sep)
                if len(partes) > 2 or len(partes[-1]) == 3:
                    t = t.replace(sep, '')
                else:
                    t = t.replace(sep, '.')
    try:
        return Decimal(t)
    except InvalidOperation:
        return None



def escribir_filas_csv(ruta, filas, separador):
    """
    Guarda una tabla de texto (lista de listas) en CSV, UTF-8 con BOM.

    Args:
        ruta: ruta del fichero a crear.
        filas: lista de filas, cada una una lista de textos (la primera es
            la cabecera).
        separador: caracter separador de columnas del CSV de salida.

    Returns:
        No devuelve nada.
    """
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, 'w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f, delimiter=separador, quoting=csv.QUOTE_MINIMAL).writerows(filas)


# ============================================================================
# ESTADO (que se ha enviado hoy)
# ============================================================================

def cargar_estado(ruta):
    """
    Lee estado_envios.json: {"2026-09-21": ["Informe A", ...]}.

    Args:
        ruta: ruta del fichero de estado.

    Returns:
        Diccionario fecha -> lista de informes enviados. Vacio si el fichero
        no existe o esta corrupto (peor caso: se reenviaria un informe).
    """
    try:
        return json.loads(Path(ruta).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("No se pudo leer %s (%s); se parte de estado vacio", ruta, e)
        return {}


def guardar_estado(ruta, estado, hoy):
    """
    Guarda el estado conservando solo el dia de hoy (lo anterior no sirve).

    Args:
        ruta: ruta del fichero de estado.
        estado: diccionario completo en memoria.
        hoy: cadena 'YYYY-MM-DD' del dia en curso.

    Returns:
        No devuelve nada.
    """
    Path(ruta).write_text(
        json.dumps({hoy: estado.get(hoy, [])}, ensure_ascii=False, indent=2),
        encoding='utf-8')


# ============================================================================
# TABLEAU
# ============================================================================

def conectar_tableau(config):
    """
    Inicia sesion en Tableau Cloud con el token de acceso personal (PAT).

    Args:
        config: diccionario de configuracion con las claves tableau_*.

    Returns:
        Objeto Server de tableauserverclient ya autenticado. Si falla, el
        programa termina (sys.exit).
    """
    try:
        import tableauserverclient as TSC
    except ImportError:
        log.error("Falta la libreria tableauserverclient (pip install tableauserverclient)")
        sys.exit(1)

    try:
        auth = TSC.PersonalAccessTokenAuth(
            token_name=config['tableau_token_name'],
            personal_access_token=config['tableau_token'],
            site_id=config['tableau_site'],
        )
        servidor = TSC.Server(config['tableau_server'])
        # Por defecto la libreria usa la API 2.4; la Metadata API pide 3.5 o
        # superior. Esto la sube a la version que soporta el servidor.
        servidor.use_server_version()
        servidor.auth.sign_in(auth)
        return servidor
    except Exception as e:
        log.error("No se pudo conectar con Tableau: %s", e)
        log.error("Si el error es 401, el PAT ha caducado: renuevalo en Tableau Cloud")
        sys.exit(1)



def normalizar_ruta(texto):
    """Deja una ruta de proyecto comparable: sin acentos, minusculas."""
    import unicodedata
    sin_acentos = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode()
    return "/".join(p.strip() for p in sin_acentos.casefold().split('/'))


_CACHE_PROYECTOS = {}


def ids_proyecto_por_ruta(servidor, ruta):
    """
    Encuentra el/los proyecto(s) cuya ruta completa coincide con 'ruta'.
    Cachea el resultado por ruta: los 8 informes comparten la misma, asi que
    solo se recorren los proyectos de Tableau una vez por ejecucion.
    """
    if ruta in _CACHE_PROYECTOS:
        return _CACHE_PROYECTOS[ruta]

    import tableauserverclient as TSC
    proyectos = {p.id: p for p in TSC.Pager(servidor.projects)}

    def ruta_de(proyecto):
        partes = []
        while proyecto is not None:
            partes.append(proyecto.name)
            proyecto = proyectos.get(proyecto.parent_id)
        return "/".join(reversed(partes))

    rutas = {p.id: ruta_de(p) for p in proyectos.values()}
    objetivo = normalizar_ruta(ruta)
    ids = [i for i, r in rutas.items() if normalizar_ruta(r) == objetivo]
    if not ids:
        colas = [i for i, r in rutas.items() if objetivo.endswith('/' + normalizar_ruta(r))]
        if len(colas) == 1:
            log.info("        (aviso: solo se ve la ruta '%s', se acepta por ser unica)", rutas[colas[0]])
            ids = colas
    if not ids:
        hoja = objetivo.rsplit('/', 1)[-1]
        parecidas = [r for r in rutas.values() if normalizar_ruta(r).rsplit('/', 1)[-1] == hoja]
        raise LookupError(f"no existe el proyecto '{ruta}'. Rutas con ese nombre final: {parecidas or 'ninguna'}")

    _CACHE_PROYECTOS[ruta] = ids
    return ids


def localizar_vista(servidor, config, informe):
    """
    Localiza la vista de un informe por su nombre, dentro de la carpeta de
    Tableau indicada en 'proyecto_ruta'.

    Args:
        servidor: objeto Server ya autenticado.
        config: diccionario de configuracion (usa 'proyecto_ruta').
        informe: diccionario del informe (una entrada de 'informes'), con
            'nombre'.

    Returns:
        Tupla (vista, workbook_luid): el ViewItem de tableauserverclient y
        el LUID de su workbook (lo necesita la Metadata API).
    """
    import tableauserverclient as TSC
    nombre = informe['nombre']
    ids_proyecto = ids_proyecto_por_ruta(servidor, config['proyecto_ruta'])

    opciones = TSC.RequestOptions(pagesize=100)
    opciones.filter.add(TSC.Filter(TSC.RequestOptions.Field.Name,
                                   TSC.RequestOptions.Operator.Equals, nombre))
    workbooks = [w for w in TSC.Pager(servidor.workbooks, opciones) if w.project_id in ids_proyecto]
    if len(workbooks) != 1:
        raise LookupError(f"{len(workbooks)} workbooks encontrados con el nombre '{nombre}' "
                          f"en la ruta de proyecto configurada")

    servidor.workbooks.populate_views(workbooks[0])
    vistas = workbooks[0].views
    if len(vistas) != 1:
        raise LookupError(f"el workbook '{nombre}' tiene {len(vistas)} vistas, se esperaba 1")

    return vistas[0], workbooks[0].id



def descargar_excel_bytes(servidor, vista, cache_maxima_minutos=1):
    """
    Descarga la vista como Excel (crosstab), tal como la exporta Tableau con
    'Descargar > Crosstab': mismas columnas, mismo orden y mismos
    encabezados y formatos de numero que en el dashboard.

    Args:
        servidor: objeto Server ya autenticado.
        vista: ViewItem devuelto por localizar_vista.
        cache_maxima_minutos: antiguedad maxima admitida de la cache.

    Returns:
        Contenido del fichero .xlsx, en bytes.
    """
    import tableauserverclient as TSC
    servidor.views.populate_excel(vista, TSC.ExcelRequestOptions(maxage=cache_maxima_minutos))
    return b"".join(vista.excel)


def descargar_crosstab_excel(servidor, vista, ruta, cache_maxima_minutos=1):
    """
    Guarda en disco el Excel (crosstab) de una vista. Solo para comprobar
    como queda (--crosstab-excel); no forma parte del envio.

    Args:
        servidor: objeto Server ya autenticado.
        vista: ViewItem devuelto por localizar_vista.
        ruta: ruta del .xlsx a crear.
        cache_maxima_minutos: antiguedad maxima admitida de la cache.

    Returns:
        No devuelve nada (escribe el fichero).
    """
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    Path(ruta).write_bytes(descargar_excel_bytes(servidor, vista, cache_maxima_minutos))


_DECIMALES_FORMATO = re.compile(r'\.([0#?]+)')


def numero_con_formato_excel(valor, formato, miles=False):
    """
    Escribe un numero tal como lo mostraria Excel con su formato de celda,
    pero sin simbolo de moneda, y con coma decimal.

    Se respetan tres cosas del formato: los decimales (0, 0.0, 0.00...), el
    porcentaje (un formato con '%' multiplica por 100 y anade el simbolo) y,
    solo si se pide con 'miles', el separador de miles (punto), unicamente
    cuando el propio formato de la celda lo lleva. Asi un ano o un codigo
    (formato General o '0') nunca se convierten en '2.026'.
    Con formato 'General' se escribe el numero con los decimales que tenga.

    Args:
        valor: numero de la celda (int, float o Decimal).
        formato: formato de celda de Excel, p. ej. '#,##0 "€"' o '0.0%'.
        miles: si es True, se pone punto de miles en las celdas cuyo
            formato lo lleva (y no son porcentajes).

    Returns:
        El numero como texto.
    """
    valor = Decimal(repr(valor)) if isinstance(valor, float) else Decimal(valor)
    secciones = formato.split(';')
    seccion = secciones[1] if valor < 0 and len(secciones) > 1 else secciones[0]
    seccion = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', seccion)   # sin literales ni colores

    if not re.search(r'[0#?]', seccion):   # 'General' o sin formato numerico
        return format(valor.normalize(), 'f').replace('.', ',')

    es_porcentaje = '%' in seccion
    coincidencia = _DECIMALES_FORMATO.search(seccion)
    decimales = len(coincidencia.group(1)) if coincidencia else 0
    if es_porcentaje:
        valor *= 100
    valor = valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    if valor == 0:
        valor = abs(valor)
    if miles and not es_porcentaje and ',' in seccion:
        texto = f"{valor:,.{decimales}f}"   # 3,580,783.49 -> 3.580.783,49
        return texto.replace(',', '\0').replace('.', ',').replace('\0', '.')
    return f"{valor:.{decimales}f}".replace('.', ',') + ('%' if es_porcentaje else '')


def texto_celda_excel(celda, miles=False):
    """
    Convierte una celda de openpyxl en el texto que va al CSV.

    Args:
        celda: celda de openpyxl (con .value y .number_format).
        miles: si es True, separador de miles en los formatos que lo llevan.

    Returns:
        Texto: vacio si no hay valor, fecha en dd/mm/aaaa, numeros segun su
        formato de celda (ver numero_con_formato_excel) y el resto tal cual.
    """
    valor = celda.value
    if valor is None:
        return ''
    if isinstance(valor, bool):
        return 'TRUE' if valor else 'FALSE'
    if isinstance(valor, datetime):
        solo_fecha = valor.time() == datetime.min.time()
        return valor.strftime('%d/%m/%Y' if solo_fecha else '%d/%m/%Y %H:%M:%S')
    if isinstance(valor, date):
        return valor.strftime('%d/%m/%Y')
    if isinstance(valor, (int, float, Decimal)):
        return numero_con_formato_excel(valor, celda.number_format or 'General', miles)
    return str(valor)


def columnas_de_etiquetas(tabla):
    """
    Detecta las columnas de etiquetas de una tabla: las de la izquierda que
    solo contienen texto (Linea de negocio, Marca...), hasta la primera que
    contiene numeros o porcentajes.

    Args:
        tabla: lista de filas (listas de texto); la primera es la cabecera.

    Returns:
        Lista de indices de columna, de izquierda a derecha.
    """
    indices = []
    for i in range(len(tabla[0])):
        valores = [f[i] for f in tabla[1:] if f[i]]
        if not valores or any(interpretar_numero(v.replace('%', '')) is not None for v in valores):
            break
        indices.append(i)
    return indices


def rellenar_etiquetas(tabla, indices):
    """
    Repite las etiquetas de grupo en todas las filas.

    En un dashboard, 'PROMO' aparece una vez y las filas de debajo quedan en
    blanco; en un CSV cada fila debe llevar su etiqueta para poder filtrar,
    ordenar o hacer tablas dinamicas. Una celda en blanco se rellena con el
    valor de arriba solo si todas las columnas de etiqueta a su izquierda
    tambien estan en blanco en esa fila (es decir, la fila sigue en el mismo
    grupo). Asi una fila de total ('Total general' | vacio) no hereda la
    marca de la fila anterior.

    Args:
        tabla: lista de filas (listas de texto); la primera es la cabecera.
            Se modifica en sitio.
        indices: indices de las columnas de etiqueta, de izquierda a derecha.

    Returns:
        Numero de celdas rellenadas.
    """
    ultimo = {}
    rellenadas = 0
    for fila in tabla[1:]:
        if not any(fila):
            continue
        original = list(fila)
        for k, i in enumerate(indices):
            if original[i]:
                ultimo[i] = original[i]
            elif i in ultimo and not any(original[j] for j in indices[:k]):
                fila[i] = ultimo[i]
                rellenadas += 1
    return rellenadas


def excel_a_filas(contenido, hoja=None, miles=False):
    """
    Lee el Excel (crosstab) de Tableau y lo deja como una tabla de texto,
    con la misma disposicion que en el dashboard.

    Se quitan las filas vacias del principio y del final y las columnas
    completamente vacias. Requiere 'pip install openpyxl'.

    Args:
        contenido: bytes del fichero .xlsx.
        hoja: nombre de la hoja a leer; por defecto, la primera.
        miles: si es True, separador de miles en los formatos que lo llevan.

    Returns:
        Lista de filas, cada una una lista de textos, todas de la misma
        longitud. La primera fila es la cabecera.

    Raises:
        RuntimeError: si falta openpyxl.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("falta la libreria openpyxl (pip install openpyxl)")

    libro = load_workbook(BytesIO(contenido), data_only=True)
    hoja_excel = libro[hoja] if hoja else libro.worksheets[0]
    filas = [[texto_celda_excel(c, miles) for c in fila] for fila in hoja_excel.iter_rows()]

    while filas and not any(filas[0]):
        filas.pop(0)
    while filas and not any(filas[-1]):
        filas.pop()
    if not filas:
        return []

    ancho = max(len(f) for f in filas)
    filas = [f + [''] * (ancho - len(f)) for f in filas]
    con_datos = [i for i in range(ancho) if any(f[i] for f in filas)]
    return [[f[i] for i in con_datos] for f in filas]


def fecha_por_tablas_origen(servidor, workbook_luid):
    """
    Estima la fecha de actualizacion de un workbook que lee EN VIVO de una
    base de datos (Tableau no guarda fecha de refresco en ese caso).

    Se apoya en dos datos de la Metadata API: las tablas de origen del
    workbook (upstreamTables) y, para cada tabla, las fuentes de datos que
    dependen de ella (downstreamDatasources). De cada tabla se toma el
    extracto PUBLICADO mas recientemente refrescado: si algun extracto
    construido sobre esa tabla se ha refrescado hoy, la tabla ya tiene la
    carga de hoy. Si el workbook lee de varias tablas, se devuelve la mas
    antigua de ellas (todas deben estar al dia).

    Es una comprobacion indirecta: no mira la base de datos, sino los
    extractos que se alimentan de ella. Se deja constancia en el log.

    Args:
        servidor: objeto Server ya autenticado.
        workbook_luid: LUID del workbook.

    Returns:
        Objeto date, o None si el workbook no tiene tablas de origen o
        alguna de ellas no tiene ningun extracto publicado del que fiarse
        (en ese caso no se puede comprobar y se anota el motivo).
    """
    consulta = (
        "query { workbooks(filter: {luid: %s}) { upstreamTables { name schema "
        "downstreamDatasources { __typename name ... on PublishedDatasource "
        "{ projectName hasExtracts extractLastRefreshTime extractLastUpdateTime } } } } }"
    ) % json.dumps(workbook_luid)

    try:
        respuesta = servidor.metadata.query(consulta)
    except Exception as e:
        log.warning("        No se pudo consultar las tablas de origen: %s", e)
        return None
    if respuesta.get('errors'):
        log.warning("        La Metadata API rechazo la consulta de tablas de origen: %s",
                    respuesta['errors'])
        return None

    tablas = ((respuesta['data']['workbooks'] or [{}])[0]).get('upstreamTables') or []
    if not tablas:
        log.info("        El workbook no tiene tablas de origen registradas")
        return None

    # Tableau puede registrar la misma tabla varias veces en un workbook (una
    # por conexion): se agrupan por esquema.nombre para que cuenten como una.
    por_tabla = {}
    for tabla in tablas:
        etiqueta = f"{tabla.get('schema') or '?'}.{tabla['name']}"
        candidatas = por_tabla.setdefault(etiqueta, [])
        for fuente in tabla.get('downstreamDatasources') or []:
            if fuente.get('__typename') != 'PublishedDatasource':
                continue
            marcas = [fuente.get('extractLastRefreshTime'), fuente.get('extractLastUpdateTime')]
            marcas = [a_fecha_local(m) for m in marcas if m]
            if marcas:
                candidatas.append((max(marcas), fuente['name'], fuente.get('projectName')))

    fechas_tablas = []
    for etiqueta, candidatas in por_tabla.items():
        if not candidatas:
            log.warning("        Tabla %s: ningun extracto publicado se alimenta de ella, "
                        "no se puede comprobar su fecha", etiqueta)
            return None
        fecha, nombre, proyecto = max(candidatas)
        log.warning("        Tabla %s (lectura en vivo): se toma la fecha del extracto publicado "
                    "'%s' (%s), actualizado el %s", etiqueta, nombre, proyecto, fecha.strftime('%d/%m/%Y'))
        fechas_tablas.append(fecha)

    return min(fechas_tablas)


def diagnosticar_informe(servidor, config, informe):
    """
    Muestra en el log que ve la Metadata API para el workbook de un informe:
    fuentes publicadas, fuentes embebidas (con o sin extracto) y bases de
    datos/tablas de origen. No envia nada. Sirve para saber de donde salen
    de verdad los datos de un workbook cuya fecha no se puede leer.

    Args:
        servidor: objeto Server ya autenticado.
        config: diccionario de configuracion (usa 'proyecto_ruta').
        informe: diccionario del informe (una entrada de 'informes').

    Returns:
        No devuelve nada (escribe en el log).
    """
    try:
        _, workbook_luid = localizar_vista(servidor, config, informe)
    except Exception as e:
        log.error("        No se pudo localizar el workbook: %s", e)
        return

    bloques = {
        "fuentes publicadas": "upstreamDatasources { name ... on PublishedDatasource "
                              "{ projectName extractLastRefreshTime extractLastUpdateTime } }",
        "fuentes embebidas": "embeddedDatasources { name hasExtracts "
                             "extractLastRefreshTime extractLastUpdateTime }",
        "bases de datos y tablas": "upstreamDatabases { name connectionType } "
                                   "upstreamTables { name schema }",
    }
    for titulo, cuerpo in bloques.items():
        consulta = ("query { workbooks(filter: {luid: %s}) { name %s } }"
                    % (json.dumps(workbook_luid), cuerpo))
        try:
            respuesta = servidor.metadata.query(consulta)
        except Exception as e:
            log.info("        [%s] fallo la consulta: %s", titulo, e)
            continue
        if respuesta.get('errors'):
            log.info("        [%s] la API rechazo la consulta: %s", titulo, respuesta['errors'])
            continue
        datos = (respuesta['data']['workbooks'] or [{}])[0]
        datos.pop('name', None)
        log.info("        [%s] %s", titulo, json.dumps(datos, ensure_ascii=False))


def a_fecha_local(texto):
    """
    Convierte una marca de tiempo de la Metadata API (UTC, ISO 8601) en la
    fecha local del servidor. Importa: una carga a las 00:30 en Espana es
    del dia anterior en UTC.

    Args:
        texto: p. ej. '2026-09-21T05:12:33Z'.

    Returns:
        Objeto date en la zona horaria del equipo que ejecuta el script.
    """
    return datetime.fromisoformat(texto.replace('Z', '+00:00')).astimezone().date()


def fecha_actualizacion_fuentes(servidor, workbook_luid):
    """
    Consulta a la Metadata API la fecha de actualizacion de las fuentes de
    datos publicadas de las que depende un workbook.

    De cada fuente se toma la mas reciente entre extractLastRefreshTime y
    extractLastUpdateTime. Las fuentes sin fecha (conexion en vivo) no se
    pueden comprobar y se ignoran. Si el workbook depende de varias fuentes
    con extracto, se devuelve la MAS ANTIGUA: el informe solo esta al dia si
    todas lo estan.

    Args:
        servidor: objeto Server ya autenticado.
        workbook_luid: LUID del workbook (de localizar_vista).

    Returns:
        Objeto date con la fecha de la fuente menos actualizada. None si
        ninguna fuente tiene fecha de extracto.

    Raises:
        LookupError: si la Metadata API no devuelve exactamente un workbook.
        RuntimeError: si la Metadata API devuelve errores.
    """
    # Fuentes publicadas (upstreamDatasources) y embebidas en el propio
    # workbook (embeddedDatasources): un workbook puede usar cualquiera.
    consulta = (
        "query { workbooks(filter: {luid: %s}) { name "
        "upstreamDatasources { name ... on PublishedDatasource "
        "{ extractLastRefreshTime extractLastUpdateTime } } "
        "embeddedDatasources { name hasExtracts "
        "extractLastRefreshTime extractLastUpdateTime } } }"
    ) % json.dumps(workbook_luid)
    consulta_solo_publicadas = (
        "query { workbooks(filter: {luid: %s}) { name "
        "upstreamDatasources { name ... on PublishedDatasource "
        "{ extractLastRefreshTime extractLastUpdateTime } } } }"
    ) % json.dumps(workbook_luid)

    respuesta = servidor.metadata.query(consulta)
    if respuesta.get('errors'):
        # Si el esquema del servidor no admite algun campo de las embebidas,
        # se reintenta solo con las publicadas (la consulta ya validada).
        log.warning("        La Metadata API rechazo la consulta de fuentes embebidas: %s",
                    respuesta['errors'])
        respuesta = servidor.metadata.query(consulta_solo_publicadas)
        if respuesta.get('errors'):
            raise RuntimeError(f"Metadata API: {respuesta['errors']}")
    workbooks = respuesta['data']['workbooks']
    if len(workbooks) != 1:
        raise LookupError(f"Metadata API: {len(workbooks)} workbooks para el LUID {workbook_luid}")

    publicadas = workbooks[0].get('upstreamDatasources') or []
    embebidas = workbooks[0].get('embeddedDatasources') or []
    if not publicadas and not embebidas:
        log.warning("        La Metadata API no devuelve ninguna fuente de datos para este workbook")

    fechas = []
    hay_en_vivo = not (publicadas or embebidas)
    for fuente in publicadas + embebidas:
        marcas = [fuente.get('extractLastRefreshTime'), fuente.get('extractLastUpdateTime')]
        marcas = [a_fecha_local(m) for m in marcas if m]
        if not marcas:
            log.info("        Fuente '%s': sin fecha de extracto (conexion en vivo)", fuente['name'])
            hay_en_vivo = True
            continue
        log.info("        Fuente '%s': actualizada el %s", fuente['name'], max(marcas).strftime('%d/%m/%Y'))
        fechas.append(max(marcas))

    # Fuentes en vivo: Tableau no tiene fecha de refresco, se busca a traves
    # de las tablas de origen (ver fecha_por_tablas_origen). Si no hay forma
    # y el workbook tiene otras fuentes con fecha, la en vivo se ignora.
    if hay_en_vivo:
        fecha_tablas = fecha_por_tablas_origen(servidor, workbook_luid)
        if fecha_tablas:
            fechas.append(fecha_tablas)
        elif fechas:
            log.info("        Las fuentes en vivo no se pueden comprobar y se ignoran")
    return min(fechas) if fechas else None


# ============================================================================
# CORREO
# ============================================================================

def obtener_token_graph(config):
    """
    Consigue un token de aplicacion (client credentials) para Microsoft
    Graph, valido para enviar correo con el permiso de APLICACION
    'Mail.Send' que debe conceder un administrador de Microsoft Entra ID.

    El secreto se toma de config['graph_client_secret'] o, si esta vacio, de
    la variable de entorno GRAPH_CLIENT_SECRET.

    Args:
        config: diccionario de configuracion, con 'graph_tenant_id',
            'graph_client_id' y 'graph_client_secret'.

    Returns:
        Texto con el token de acceso, o None si Microsoft lo rechaza.
    """
    import os
    url = f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/token"
    datos = {
        'client_id': config['graph_client_id'],
        'client_secret': config['graph_client_secret'] or os.environ.get('GRAPH_CLIENT_SECRET', ''),
        'scope': 'https://graph.microsoft.com/.default',
        'grant_type': 'client_credentials',
    }
    try:
        respuesta = requests.post(url, data=datos, timeout=15)
    except Exception as e:
        log.error("        No se pudo contactar con Microsoft para el token de Graph: %s", e)
        return None

    if respuesta.status_code != 200:
        log.error("        Microsoft rechazo la autenticacion de Graph (codigo %d)", respuesta.status_code)
        log.error("        Respuesta: %s", respuesta.text[:300])
        return None

    return respuesta.json()['access_token']


def enviar_correo(config, destinatarios, asunto, cuerpo, adjuntos=None):
    """
    Envia un correo con Microsoft Graph (API REST): no usa Outlook ni SMTP,
    asi que no depende de ningun perfil ni aviso de seguridad de escritorio.

    Necesita una aplicacion registrada en Microsoft Entra ID (Azure AD), con
    permiso de APLICACION 'Mail.Send' concedido por un administrador (mejor
    restringido a un buzon concreto con una 'application access policy', no
    a todo el tenant). 'graph_remitente' es el buzon (UPN) desde el que se
    envia, que debe ser justo ese buzon autorizado.

    Args:
        config: diccionario de configuracion, con 'graph_tenant_id',
            'graph_client_id', 'graph_client_secret' y 'graph_remitente'.
        destinatarios: lista de direcciones de destino.
        asunto: asunto del mensaje.
        cuerpo: texto plano del mensaje.
        adjuntos: lista de rutas de ficheros a adjuntar, o None.

    Returns:
        True si Microsoft Graph acepto el envio. False si fallo.
    """
    token = obtener_token_graph(config)
    if not token:
        return False

    mensaje = {
        'subject': asunto,
        'body': {'contentType': 'Text', 'content': cuerpo},
        'toRecipients': [{'emailAddress': {'address': d}} for d in destinatarios],
    }
    if adjuntos:
        mensaje['attachments'] = [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': Path(a).name,
            'contentBytes': base64.b64encode(Path(a).read_bytes()).decode('ascii'),
        } for a in adjuntos]

    url = f"https://graph.microsoft.com/v1.0/users/{config['graph_remitente']}/sendMail"
    cabeceras = {'Authorization': f"Bearer {token}", 'Content-Type': 'application/json'}

    try:
        respuesta = requests.post(url, headers=cabeceras,
                                  json={'message': mensaje, 'saveToSentItems': True}, timeout=30)
    except Exception as e:
        log.error("        No se pudo enviar con Microsoft Graph: %s", e)
        return False

    if respuesta.status_code != 202:
        log.error("        Microsoft Graph rechazo el envio (codigo %d)", respuesta.status_code)
        log.error("        Respuesta: %s", respuesta.text[:300])
        if respuesta.status_code == 403:
            log.error("        Probable falta de permiso: la aplicacion necesita 'Mail.Send' "
                      "(de APLICACION, con consentimiento de administrador) sobre este buzon")
        return False

    return True


def probar_correo(config, direccion):
    """
    Envia un correo de prueba con un CSV pequeno adjunto, sin tocar Tableau,
    para comprobar el envio por Microsoft Graph y su rapidez.

    Args:
        config: diccionario de configuracion.
        direccion: direccion de destino de la prueba. Se pide siempre
            explicita para no mandar una prueba al cliente por error.

    Returns:
        True si el envio funciono. False si fallo.
    """
    ruta = Path(config['directorio_salida']) / "prueba_correo.csv"
    try:
        escribir_filas_csv(ruta, [["columna_a", "columna_b"], ["1", "2"]], config['csv_separador'])
    except PermissionError:
        log.error("No se pudo escribir %s: esta abierto en otro programa (ciérralo e intenta de nuevo)", ruta)
        return False

    inicio = time.time()
    correcto = enviar_correo(
        config, [direccion], "Prueba de envio - CSV Tableau",
        "Correo de prueba del proceso de envio de CSV de Tableau.", [ruta])
    segundos = time.time() - inicio

    if correcto:
        log.info("PRUEBA CORRECTA en %.1f s: revisa la bandeja de %s", segundos, direccion)
    else:
        log.error("PRUEBA FALLIDA tras %.1f s", segundos)
    return correcto


# ============================================================================
# PROCESO DE UN INFORME
# ============================================================================

def preparar_informe(servidor, config, informe, hoy, fecha):
    """
    Descarga un informe y genera su CSV. Su fecha de actualizacion ya se
    comprobo en main() (los 8 informes comparten la misma fuente, asi que
    se comprueba una sola vez para todos, no aqui por cada uno). No envia
    nada: main() agrupa los informes que preparar_informe() deja listos en
    la misma pasada y los manda en el menor numero de correos posible (ver
    enviar_lote).

    Args:
        servidor: objeto Server ya autenticado.
        config: diccionario de configuracion.
        informe: diccionario del informe (una entrada de 'informes').
        hoy: objeto date del dia de envio.
        fecha: fecha de actualizacion de la fuente compartida, ya
            comprobada por main() (solo para el mensaje de log).

    Returns:
        Tupla (estado, ruta). estado es 'listo' (con la ruta del CSV
        generado) o 'error' (fallo tecnico: Tableau, formato de fichero;
        ruta None).
    """
    nombre = informe['nombre']

    try:
        vista, _ = localizar_vista(servidor, config, informe)
        contenido_excel = descargar_excel_bytes(servidor, vista, config['cache_maxima_minutos'])
    except Exception as e:
        log.error("        Error al consultar Tableau: %s", e)
        return 'error', None

    ruta = Path(config['directorio_salida']) / f"{sanear_nombre_archivo(nombre)}_{hoy.isoformat()}.csv"

    # El Excel que exporta Tableau ya tiene la disposicion del dashboard;
    # solo se convierte a CSV, que es lo que se envia.
    try:
        tabla = excel_a_filas(contenido_excel, informe.get('hoja_excel'),
                              informe.get('separador_miles', config['separador_miles']))
    except Exception as e:
        log.error("        No se pudo leer el Excel de Tableau: %s", e)
        return 'error', None
    if len(tabla) < 2:
        log.warning("        La tabla llego sin filas: no se envia")
        return 'error', None

    # Etiquetas de grupo (PROMO, NO PROMO...) repetidas en cada fila.
    # 'rellenar_columnas' fija cuales (por nombre de cabecera); con []
    # se desactiva; por defecto, las columnas de texto de la izquierda.
    rellenar = informe.get('rellenar_columnas')
    if rellenar is None and config['rellenar_etiquetas']:
        indices = columnas_de_etiquetas(tabla)
    else:
        indices = [tabla[0].index(n) for n in (rellenar or []) if n in tabla[0]]
    if indices:
        repetidas = rellenar_etiquetas(tabla, indices)
        if repetidas:
            log.info("        Etiquetas repetidas en cada fila (%s): %d celdas rellenadas",
                     ", ".join(tabla[0][i] for i in indices), repetidas)
    log.info("        Fecha de actualizacion correcta (%s), %d filas (disposicion del dashboard)",
             fecha.strftime('%d/%m/%Y'), len(tabla) - 1)
    try:
        escribir_filas_csv(ruta, tabla, config['csv_separador'])
    except PermissionError:
        log.error("        No se pudo escribir %s: esta abierto en otro programa (ciérralo e "
                  "intenta de nuevo)", ruta)
        return 'error', None
    return 'listo', ruta


def enviar_lote(config, listos, hoy, estado, hoy_txt):
    """
    Envia TODOS los informes preparados en esta pasada (se llama solo cuando
    los 8 han quedado listos: si alguno hubiera fallado, main() no la
    llama). En el MENOR numero de correos posible: los que comparten los
    mismos destinatarios (lo habitual, salvo que un informe fije los suyos
    propios) van juntos en un unico correo, con todos sus CSV adjuntos.

    Tras cada grupo enviado con exito se marca de inmediato en 'estado' y se
    guarda en disco: si el proceso se interrumpe a mitad, no se pierde ni se
    duplica ningun informe ya confirmado.

    Args:
        config: diccionario de configuracion.
        listos: lista de tuplas (informe, ruta) ya preparadas (CSV escrito
            en disco), pendientes de enviar.
        hoy: objeto date del dia de envio.
        estado: diccionario de cargar_estado(), se actualiza en sitio.
        hoy_txt: cadena 'YYYY-MM-DD' de hoy, la clave de 'estado'.

    Returns:
        Diccionario {nombre_del_informe: 'enviado' | 'error'}.
    """
    grupos = {}
    for informe, ruta in listos:
        destinatarios = tuple(informe.get('destinatarios') or config['destinatarios'])
        grupos.setdefault(destinatarios, []).append((informe, ruta))

    resultados = {}
    for destinatarios, items in grupos.items():
        nombres = [informe['nombre'] for informe, _ in items]
        rutas = [ruta for _, ruta in items]

        if len(items) == 1:
            asunto = f"{nombres[0]} - datos a {hoy.strftime('%d/%m/%Y')}"
            cuerpo = (f"Buenos dias,\n\nadjuntamos el informe \"{nombres[0]}\" con los datos "
                      f"actualizados a {hoy.strftime('%d/%m/%Y')}.\n\nUn saludo.")
        else:
            lista = "\n".join(f"- {n}" for n in nombres)
            asunto = f"Informes Tableau - datos a {hoy.strftime('%d/%m/%Y')}"
            cuerpo = (f"Buenos dias,\n\nadjuntamos los siguientes informes con los datos "
                      f"actualizados a {hoy.strftime('%d/%m/%Y')}:\n\n{lista}\n\nUn saludo.")

        if enviar_correo(config, list(destinatarios), asunto, cuerpo, rutas):
            log.info("        Enviado a %s: %s", ", ".join(destinatarios), ", ".join(nombres))
            for nombre in nombres:
                resultados[nombre] = 'enviado'
            estado.setdefault(hoy_txt, []).extend(nombres)
            guardar_estado(config['archivo_estado'], estado, hoy_txt)
        else:
            log.error("        No se pudo enviar el correo con: %s", ", ".join(nombres))
            for nombre in nombres:
                resultados[nombre] = 'error'

    return resultados


# ============================================================================
# PROGRAMA PRINCIPAL
# ============================================================================

def main():
    """
    Recorre los informes de la configuracion y envia los que esten al dia.

    Returns:
        No devuelve nada. Termina con sys.exit(1) si hubo errores tecnicos.
    """
    parser = argparse.ArgumentParser(description="Envio diario de tablas de Tableau en CSV")
    parser.add_argument('--config', default='config_envio.json')
    parser.add_argument('--sin-enviar', action='store_true',
                        help="descarga y comprueba fechas, pero no envia correos")
    parser.add_argument('--fecha', help="simula otro dia de envio (YYYY-MM-DD)")
    parser.add_argument('--forzar', action='store_true',
                        help="ignora los informes ya enviados hoy")
    parser.add_argument('--diagnostico', action='store_true',
                        help="muestra de donde salen los datos de cada workbook segun la "
                             "Metadata API; no descarga ni envia nada")
    parser.add_argument('--crosstab-excel', metavar='INFORME',
                        help="prueba: descarga como Excel (crosstab) el informe con ese nombre, "
                             "para ver la disposicion del dashboard; no envia nada")
    parser.add_argument('--probar-correo', metavar='DIRECCION',
                        help="envia un correo de prueba a esa direccion (sin usar Tableau) "
                             "para comprobar el envio por Graph")
    args = parser.parse_args()

    inicio = time.time()
    config = cargar_config(args.config)

    if args.probar_correo:
        sys.exit(0 if probar_correo(config, args.probar_correo) else 1)
    hoy = date.fromisoformat(args.fecha) if args.fecha else date.today()
    hoy_txt = hoy.isoformat()
    enviar = not args.sin_enviar

    log.info("=" * 60)
    log.info("ENVIO CSV DASHBOARDS - fecha de envio %s", hoy.strftime('%d/%m/%Y'))
    log.info("=" * 60)

    if args.crosstab_excel:
        elegidos = [i for i in config['informes'] if i['nombre'] == args.crosstab_excel]
        if not elegidos:
            log.error("No hay ningun informe llamado '%s' en config_envio.json", args.crosstab_excel)
            sys.exit(1)
        servidor = conectar_tableau(config)
        try:
            vista, _ = localizar_vista(servidor, config, elegidos[0])
            ruta = Path(config['directorio_salida']) / f"{sanear_nombre_archivo(args.crosstab_excel)}_crosstab.xlsx"
            descargar_crosstab_excel(servidor, vista, ruta, config['cache_maxima_minutos'])
            log.info("Excel (crosstab) guardado en %s", ruta)
        except Exception as e:
            log.error("No se pudo descargar el Excel: %s", e)
            sys.exit(1)
        finally:
            try:
                servidor.auth.sign_out()
            except Exception:
                pass
        return

    if args.diagnostico:
        servidor = conectar_tableau(config)
        for numero, informe in enumerate(config['informes'], start=1):
            log.info("[%d/%d] %s", numero, len(config['informes']), informe['nombre'])
            diagnosticar_informe(servidor, config, informe)
        try:
            servidor.auth.sign_out()
        except Exception:
            pass
        return

    limpiar_csv_antiguos(config['directorio_salida'], hoy)

    estado = cargar_estado(config['archivo_estado'])
    ya_enviados = set() if args.forzar or not enviar else set(estado.get(hoy_txt, []))

    pendientes = [i for i in config['informes'] if i['nombre'] not in ya_enviados]
    for i in config['informes']:
        if i['nombre'] in ya_enviados:
            log.info("[ya enviado hoy] %s", i['nombre'])

    resultados = {'enviado': [], 'descartado': [], 'error': []}
    if pendientes:
        servidor = conectar_tableau(config)
        try:
            # Los 8 informes comparten la misma fuente de datos: se comprueba
            # su fecha UNA sola vez, con el primer informe pendiente, en vez
            # de una consulta a la Metadata API por cada uno de los 8.
            informe_referencia = pendientes[0]
            fecha_fuente = None
            try:
                _, workbook_referencia = localizar_vista(servidor, config, informe_referencia)
                fecha_fuente = fecha_actualizacion_fuentes(servidor, workbook_referencia)
            except Exception as e:
                log.error("No se pudo comprobar la fecha de la fuente compartida (via '%s'): %s",
                          informe_referencia['nombre'], e)

            if fecha_fuente is None:
                log.error("No se puede comprobar la fecha de actualizacion de la fuente compartida")
                log.error("Ejecuta con --diagnostico para ver de donde salen los datos")
                resultados['error'].extend(i['nombre'] for i in pendientes)
            elif fecha_fuente != hoy:
                log.warning("DESCARTADO: la fuente compartida esta actualizada el %s, no el %s -- "
                            "no se envia ningun correo esta pasada", fecha_fuente.strftime('%d/%m/%Y'),
                            hoy.strftime('%d/%m/%Y'))
                resultados['descartado'].extend(i['nombre'] for i in pendientes)
            else:
                log.info("Fuente compartida actualizada correctamente (%s)",
                        fecha_fuente.strftime('%d/%m/%Y'))
                listos = []     # [(informe, ruta), ...] listos en esta pasada
                for numero, informe in enumerate(pendientes, start=1):
                    log.info("[%d/%d] %s", numero, len(pendientes), informe['nombre'])
                    estado_informe, ruta = preparar_informe(servidor, config, informe, hoy, fecha_fuente)
                    if estado_informe == 'listo':
                        listos.append((informe, ruta))
                    else:
                        resultados['error'].append(informe['nombre'])

                # Todo o nada: se envia si y solo si los 8 informes han
                # quedado listos. Si alguno fallo por un problema tecnico,
                # no se envia NADA esta pasada -- no hay aviso a medias, se
                # reintenta todo junto en la siguiente pasada.
                if resultados['error']:
                    log.warning("No se envia ningun correo esta pasada: no se pudo generar %s",
                                ", ".join(resultados['error']))
                elif not enviar:
                    for informe, ruta in listos:
                        log.info("        (modo --sin-enviar) CSV generado en %s", ruta)
                    resultados['enviado'].extend(informe['nombre'] for informe, _ in listos)
                else:
                    resultados_envio = enviar_lote(config, listos, hoy, estado, hoy_txt)
                    for nombre, resultado in resultados_envio.items():
                        resultados[resultado].append(nombre)
        finally:
            try:
                servidor.auth.sign_out()
            except Exception:
                pass

    log.info("=" * 60)
    log.info("RESUMEN: enviados %d | descartados por fecha %d | errores %d | %ds",
             len(resultados['enviado']), len(resultados['descartado']),
             len(resultados['error']), int(time.time() - inicio))
    log.info("=" * 60)

    if resultados['error']:
        sys.exit(1)


if __name__ == '__main__':
    main()
