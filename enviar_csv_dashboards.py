"""
ENVIO DIARIO POR CORREO DE TABLAS DE TABLEAU EN CSV
====================================================

Flujo, para cada informe de la lista 'informes' de config_envio.json:
    1. Consulta a la Metadata API de Tableau la fecha de actualizacion de las
       fuentes de datos de las que depende el workbook (extractLastRefreshTime
       / extractLastUpdateTime). Si un informe indica 'fecha_columna', la
       fecha se lee en cambio de esa columna de la propia tabla.
    2. Si esa fecha es HOY  -> descarga la tabla en CSV y la envia por correo.
       Si no es hoy         -> NO se envia (la carga del dia no ha llegado o
                               ha fallado) y se anota en el log.
       Con varias fuentes de datos, todas deben estar actualizadas hoy.
       Si el workbook lee EN VIVO de una base de datos (Tableau no guarda
       fecha de refresco), se busca a traves de sus tablas de origen: se toma
       el extracto publicado mas reciente que se alimenta de esas mismas
       tablas, dejando aviso en el log. Sin configuracion por informe.

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
    python enviar_csv_dashboards.py --forzar            # ignora lo ya enviado
    python enviar_csv_dashboards.py --aviso             # ultima pasada del dia:
                                                        # avisa al equipo de lo
                                                        # que no salio

Codigo de salida: 0 si todo fue bien (incluidos los informes descartados por
fecha, que es un caso normal), 1 si hubo errores tecnicos (Tableau, SMTP...).
"""

import re
import sys
import csv
import json
import time
import logging
import unicodedata
import smtplib
import argparse
import mimetypes
from io import BytesIO, StringIO
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from datetime import datetime, date
from email.message import EmailMessage


# ============================================================================
# LOG
# ============================================================================
# Sin emojis: la consola del servidor no siempre esta en UTF-8.
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
CLAVES_SMTP = ['smtp_servidor', 'remitente']
CLAVES_OPCIONALES = {
    'metodo_correo': 'outlook',
    'remitente': '',
    'directorio_salida': './csv_generados',
    'archivo_estado': './estado_envios.json',
    'csv_separador': ';',
    'cache_maxima_minutos': 1,
    'decimales_porcentaje': 1,
    'decimales_numeros': 0,
    'origen_datos': 'csv',
    'smtp_puerto': 25,
    'smtp_starttls': False,
    'smtp_usuario': '',
    'smtp_password': '',
    'destinatarios_aviso': [],
    # Formatos con los que se intenta interpretar la fecha de actualizacion
    # que devuelve Tableau (depende del idioma de la cuenta que exporta).
    'formatos_fecha': ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d.%m.%Y'],
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

    if config['metodo_correo'] not in ('outlook', 'smtp'):
        log.error("'metodo_correo' debe ser 'outlook' o 'smtp'")
        sys.exit(1)

    origenes = [config['origen_datos']] + [i['origen_datos'] for i in config.get('informes', [])
                                           if 'origen_datos' in i]
    if any(o not in ('csv', 'crosstab') for o in origenes):
        log.error("'origen_datos' debe ser 'csv' o 'crosstab'")
        sys.exit(1)

    obligatorias = CLAVES_TABLEAU + CLAVES_CORREO + ['informes']
    if config['metodo_correo'] == 'smtp':
        obligatorias += CLAVES_SMTP
    faltan = [c for c in obligatorias if c not in config or config[c] in ('', [])]
    if faltan:
        log.error("Faltan claves obligatorias en %s: %s", fichero, ", ".join(faltan))
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


def parsear_fecha(texto, formatos):
    """
    Convierte el texto de una fecha (con o sin hora) en un date.

    Args:
        texto: valor tal como viene en el CSV, p. ej. '21/09/2026' o
            '2026-09-21 07:45:12'.
        formatos: lista de formatos strptime a probar, en orden.

    Returns:
        Objeto date, o None si el texto no coincide con ningun formato.
    """
    texto = (texto or '').strip()
    if not texto:
        return None

    # Se prueba el texto entero y, si lleva hora, solo la parte de la fecha.
    candidatos = [texto, re.split(r'[ T]', texto)[0]]
    for candidato in candidatos:
        for formato in formatos:
            try:
                return datetime.strptime(candidato, formato).date()
            except ValueError:
                continue
    return None


def fecha_actualizacion(filas, columna, formatos):
    """
    Devuelve la fecha de actualizacion mas reciente de una columna del CSV.

    Es la mas reciente (y no la primera) porque en el dashboard puede
    aparecer repetida en cada fila, o una sola vez en una vista aparte.

    Args:
        filas: lista de diccionarios (una por fila del CSV).
        columna: nombre de la columna que contiene la fecha.
        formatos: formatos de fecha admitidos (ver parsear_fecha).

    Returns:
        Objeto date con la fecha mas reciente. None si la columna no existe
        o ninguno de sus valores se pudo interpretar como fecha.
    """
    if not filas or columna not in filas[0]:
        return None
    fechas = [parsear_fecha(f.get(columna), formatos) for f in filas]
    fechas = [f for f in fechas if f]
    return max(fechas) if fechas else None


def leer_csv(contenido):
    """
    Convierte el texto de un CSV en cabecera + lista de filas.

    Args:
        contenido: texto completo del CSV (separado por comas, como lo
            entrega Tableau).

    Returns:
        Tupla (columnas, filas): la lista de nombres de columna y una lista
        de diccionarios, una por fila.
    """
    lector = csv.DictReader(StringIO(contenido))
    filas = list(lector)
    return list(lector.fieldnames or []), filas


def pivotar_medidas(columnas, filas, col_nombre='Measure Names', col_valor='Measure Values'):
    """
    Convierte el formato "largo" de Tableau en la tabla tal como se ve.

    Cuando un dashboard tiene varias medidas como columnas, Tableau exporta
    una fila por cada combinacion de dimensiones y medida, con las columnas
    'Measure Names' (nombre de la medida) y 'Measure Values' (su valor).
    Aqui se agrupan las filas por sus dimensiones y cada medida pasa a ser
    una columna, en el orden en que aparecen.

    Si el CSV no tiene esas dos columnas, se devuelve tal cual. Si el
    pivotado fuera ambiguo (una medida repetida para las mismas
    dimensiones, o con el mismo nombre que una dimension), tambien se
    devuelve tal cual para no perder ni mezclar datos.

    Args:
        columnas: lista de nombres de columna del CSV.
        filas: lista de diccionarios, una por fila.
        col_nombre: nombre de la columna con el nombre de la medida.
        col_valor: nombre de la columna con el valor de la medida.

    Returns:
        Tupla (columnas, filas) ya pivotada, o las originales si no
        procede pivotar.
    """
    if col_nombre not in columnas or col_valor not in columnas:
        return columnas, filas

    dimensiones = [c for c in columnas if c not in (col_nombre, col_valor)]
    medidas = []
    grupos = {}
    for fila in filas:
        clave = tuple(fila[c] for c in dimensiones)
        grupo = grupos.setdefault(clave, {c: fila[c] for c in dimensiones})
        medida = fila[col_nombre]
        if medida in grupo:
            log.warning("        No se pivota: la medida '%s' se repite para las mismas dimensiones "
                        "(o coincide con una dimension)", medida)
            return columnas, filas
        if medida not in medidas:
            medidas.append(medida)
        grupo[medida] = fila[col_valor]

    return dimensiones + medidas, list(grupos.values())


def dar_formato_columnas(columnas, filas, renombrar=None, orden=None):
    """
    Adapta los encabezados y el orden de columnas al aspecto del dashboard.

    Args:
        columnas: lista de nombres de columna.
        filas: lista de diccionarios, una por fila.
        renombrar: diccionario {nombre_en_el_CSV: nombre_final}, p. ej.
            {"Linea Negocio": "Linea Negocio Act."}. Los que no aparecen no
            cambian.
        orden: lista de nombres FINALES en el orden deseado. Las columnas
            que no aparezcan en la lista van al final, en su orden actual.

    Returns:
        Tupla (columnas, filas) con los nombres y el orden aplicados.
    """
    if renombrar:
        columnas = [renombrar.get(c, c) for c in columnas]
        filas = [{renombrar.get(k, k): v for k, v in f.items()} for f in filas]
    if orden:
        primeras = [c for c in orden if c in columnas]
        columnas = primeras + [c for c in columnas if c not in primeras]
    return columnas, filas


def formatear_porcentajes(filas, columnas_porcentaje, decimales=1):
    """
    Convierte en porcentaje (0.5 -> '50,0%') los valores de las columnas dadas.

    Tableau entrega la fraccion sin formato (0.5) y no el porcentaje que se
    ve en el dashboard. Los valores que ya llevan '%', los vacios y los que
    no son un numero se dejan tal cual. El resultado usa coma decimal, y
    Excel en espanol lo reconoce como numero con formato de porcentaje.

    Al leer el numero de entrada: si trae coma y punto, el ultimo es el
    decimal; si solo trae uno de los dos, se toma como decimal (en una
    columna de porcentajes, '1.438' es 1,438 = 143,8%, no mil cuatrocientos).

    Args:
        filas: lista de diccionarios, una por fila. Se modifican en sitio.
        columnas_porcentaje: nombres de las columnas a convertir.
        decimales: decimales del porcentaje resultante.

    Returns:
        La misma lista de filas, con las columnas convertidas.
    """
    paso = Decimal(1).scaleb(-decimales)   # 0.1 para 1 decimal
    for fila in filas:
        for columna in columnas_porcentaje:
            texto = (fila.get(columna) or '').strip()
            if not texto or texto.endswith('%'):
                continue
            limpio = texto.replace(' ', '')
            if ',' in limpio and '.' in limpio:
                if limpio.rfind(',') > limpio.rfind('.'):
                    limpio = limpio.replace('.', '').replace(',', '.')
                else:
                    limpio = limpio.replace(',', '')
            else:
                limpio = limpio.replace(',', '.')
            try:
                valor = (Decimal(limpio) * 100).quantize(paso, rounding=ROUND_HALF_UP)
            except InvalidOperation:
                continue
            fila[columna] = f"{valor:.{decimales}f}".replace('.', ',') + '%'
    return filas


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


def redondear_numeros(filas, columnas_numericas, decimales=0):
    """
    Redondea a 'decimales' los valores numericos de las columnas dadas.

    Los valores que no son un numero (vacios, 'Null'...) no se tocan. Los
    que ya son enteros se dejan tal cual cuando decimales es 0, para no
    alterar su formato. El resultado no lleva separador de miles ni simbolo
    de moneda, y usa coma decimal si decimales > 0.

    Args:
        filas: lista de diccionarios, una por fila. Se modifican en sitio.
        columnas_numericas: nombres de las columnas a redondear.
        decimales: numero de decimales del resultado.

    Returns:
        La misma lista de filas, con las columnas redondeadas.
    """
    paso = Decimal(1).scaleb(-decimales)
    for fila in filas:
        for columna in columnas_numericas:
            texto = fila.get(columna)
            valor = interpretar_numero(texto)
            if valor is None:
                continue
            if decimales == 0 and valor == valor.to_integral_value():
                continue
            redondeado = valor.quantize(paso, rounding=ROUND_HALF_UP)
            if redondeado == 0:
                redondeado = abs(redondeado)   # evita '-0'
            fila[columna] = f"{redondeado:.{decimales}f}".replace('.', ',')
    return filas


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


def escribir_csv(ruta, columnas, filas, separador):
    """
    Guarda la tabla en CSV con UTF-8 con BOM, para que Excel lo abra bien.

    Args:
        ruta: ruta del fichero a crear.
        columnas: lista de nombres de columna, en orden.
        filas: lista de diccionarios con los datos.
        separador: caracter separador de columnas del CSV de salida.

    Returns:
        No devuelve nada.
    """
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, 'w', encoding='utf-8-sig', newline='') as f:
        escritor = csv.DictWriter(f, fieldnames=columnas, delimiter=separador,
                                  quoting=csv.QUOTE_MINIMAL, extrasaction='ignore')
        escritor.writeheader()
        escritor.writerows(filas)


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
    """
    Deja una ruta de proyecto comparable: sin acentos, en minusculas y sin
    espacios alrededor de las barras ('BI Espana' == 'bi españa').

    Args:
        texto: ruta tal como se escribe, p. ej. 'Production/Dashboards/BI España'.

    Returns:
        La ruta normalizada.
    """
    sin_acentos = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode()
    return "/".join(p.strip() for p in sin_acentos.casefold().split('/'))


_CACHE_PROYECTOS = {}


def ids_proyecto_por_ruta(servidor, ruta):
    """
    Devuelve los IDs de los proyectos de Tableau cuya ruta completa coincide
    con la indicada. Distingue dos carpetas con el mismo nombre en sitios
    distintos, que es lo que un simple filtro por nombre no puede hacer.

    Args:
        servidor: objeto Server ya autenticado.
        ruta: ruta completa desde la raiz, con '/', p. ej.
            'Production/Dashboards/Ad hoc Reports/BI Local/BI España/Informes Automaticos'.

    Returns:
        Lista de IDs de proyecto que coinciden (normalmente uno).

    Raises:
        LookupError: si ningun proyecto tiene esa ruta. El mensaje lista las
            rutas de proyectos con el mismo nombre final, para corregirla.
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
        # Si el usuario del PAT no ve las carpetas padre, la ruta que se
        # reconstruye llega truncada ('Informes Automaticos' en vez de la
        # completa). Se acepta un proyecto cuya ruta visible sea la COLA de
        # la configurada, pero solo si es unico: con dos candidatos no se
        # adivina cual es.
        colas = [i for i, r in rutas.items()
                 if objetivo.endswith('/' + normalizar_ruta(r))]
        if len(colas) == 1:
            log.warning("        Ruta visible de '%s': '%s' (Tableau no muestra sus carpetas "
                        "padre a este usuario). Se acepta por ser la unica con ese nombre",
                        ruta.rsplit('/', 1)[-1], rutas[colas[0]])
            ids = colas

    if not ids:
        hoja = objetivo.rsplit('/', 1)[-1]
        parecidas = [r for r in rutas.values() if normalizar_ruta(r).rsplit('/', 1)[-1] == hoja]
        raise LookupError(f"no existe el proyecto '{ruta}'. Rutas con ese nombre final: "
                          f"{parecidas or 'ninguna'}")
    _CACHE_PROYECTOS[ruta] = ids
    return ids


def localizar_vista(servidor, config, informe):
    """
    Localiza la vista de un informe y el workbook al que pertenece.

    Dos formas de indicarlo:
      - 'view_luid': la mas robusta, no cambia aunque se renombre el dashboard.
      - Por nombre: workbook = informe['workbook'] (o, si no se indica,
        informe['nombre']), dentro del proyecto 'proyecto_ruta' (del informe
        o, si no, el global de la config). La vista es informe['vista'] o,
        si el workbook tiene una sola, esa.

    Args:
        servidor: objeto Server ya autenticado.
        config: diccionario de configuracion (para 'proyecto_ruta').
        informe: diccionario del informe (una entrada de 'informes').

    Returns:
        Tupla (vista, workbook_luid): el ViewItem de tableauserverclient y
        el LUID de su workbook (lo necesita la Metadata API).

    Raises:
        LookupError: si el proyecto, el workbook o la vista no existen, o
            son ambiguos.
    """
    if informe.get('view_luid'):
        vista = servidor.views.get_by_id(informe['view_luid'])
        return vista, vista.workbook_id

    import tableauserverclient as TSC

    nombre_wb = informe.get('workbook') or informe['nombre']
    ruta = informe.get('proyecto_ruta') or config.get('proyecto_ruta')

    opciones = TSC.RequestOptions(pagesize=100)
    opciones.filter.add(TSC.Filter(TSC.RequestOptions.Field.Name,
                                   TSC.RequestOptions.Operator.Equals, nombre_wb))
    workbooks = list(TSC.Pager(servidor.workbooks, opciones))
    if ruta:
        ids = ids_proyecto_por_ruta(servidor, ruta)
        workbooks = [w for w in workbooks if w.project_id in ids]
    if len(workbooks) != 1:
        raise LookupError(
            f"workbook '{nombre_wb}': {len(workbooks)} coincidencias"
            + ("" if ruta else " (indica 'proyecto_ruta' para acotar)"))

    servidor.workbooks.populate_views(workbooks[0])
    vistas = workbooks[0].views
    if informe.get('vista'):
        vistas = [v for v in vistas if v.name == informe['vista']]
    if len(vistas) != 1:
        raise LookupError(
            f"'{nombre_wb}': {len(vistas)} vistas candidatas ("
            + ", ".join(v.name for v in workbooks[0].views)
            + "). Indica cual con 'vista'")
    return vistas[0], workbooks[0].id


def descargar_tabla(servidor, vista, cache_maxima_minutos=1):
    """
    Descarga los datos de una vista de Tableau como CSV.

    Con 'maxAge' se limita la antiguedad de la cache de Tableau: en una
    conexion en vivo, sin esto podria servirse un resultado de hace horas
    aunque la base de datos ya se haya cargado.

    Args:
        servidor: objeto Server ya autenticado.
        vista: ViewItem devuelto por localizar_vista.
        cache_maxima_minutos: antiguedad maxima admitida de la cache, en
            minutos (minimo 1).

    Returns:
        Texto del CSV tal como lo entrega Tableau.
    """
    try:
        import tableauserverclient as TSC
        opciones = TSC.CSVRequestOptions(maxage=cache_maxima_minutos)
    except (ImportError, TypeError):   # libreria antigua sin maxage
        opciones = None
    servidor.views.populate_csv(vista, opciones)
    return b"".join(vista.csv).decode('utf-8-sig')


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


def numero_con_formato_excel(valor, formato):
    """
    Escribe un numero tal como lo mostraria Excel con su formato de celda,
    pero sin separador de miles ni simbolo de moneda, y con coma decimal.

    Se respetan dos cosas del formato: los decimales (0, 0.0, 0.00...) y el
    porcentaje (un formato con '%' multiplica por 100 y anade el simbolo).
    Con formato 'General' se escribe el numero con los decimales que tenga.

    Args:
        valor: numero de la celda (int, float o Decimal).
        formato: formato de celda de Excel, p. ej. '#,##0 "€"' o '0.0%'.

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
    return f"{valor:.{decimales}f}".replace('.', ',') + ('%' if es_porcentaje else '')


def texto_celda_excel(celda):
    """
    Convierte una celda de openpyxl en el texto que va al CSV.

    Args:
        celda: celda de openpyxl (con .value y .number_format).

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
        return numero_con_formato_excel(valor, celda.number_format or 'General')
    return str(valor)


def excel_a_filas(contenido, hoja=None):
    """
    Lee el Excel (crosstab) de Tableau y lo deja como una tabla de texto,
    con la misma disposicion que en el dashboard.

    Se quitan las filas vacias del principio y del final y las columnas
    completamente vacias. Requiere 'pip install openpyxl'.

    Args:
        contenido: bytes del fichero .xlsx.
        hoja: nombre de la hoja a leer; por defecto, la primera.

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
    filas = [[texto_celda_excel(c) for c in fila] for fila in hoja_excel.iter_rows()]

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
        config: diccionario de configuracion.
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

def enviar_correo(config, destinatarios, asunto, cuerpo, adjunto=None):
    """
    Envia un correo con el metodo de config['metodo_correo'] ('outlook' por
    defecto, o 'smtp').

    Args:
        config: diccionario de configuracion.
        destinatarios: lista de direcciones de destino.
        asunto: asunto del mensaje.
        cuerpo: texto plano del mensaje.
        adjunto: ruta de un fichero a adjuntar, o None.

    Returns:
        True si el correo se envio (o se dejo en la cola de Outlook). False
        si fallo.
    """
    if config['metodo_correo'] == 'smtp':
        return enviar_correo_smtp(config, destinatarios, asunto, cuerpo, adjunto)
    return enviar_correo_outlook(config, destinatarios, asunto, cuerpo, adjunto)


def enviar_correo_outlook(config, destinatarios, asunto, cuerpo, adjunto=None):
    """
    Envia un correo con el Outlook de escritorio instalado en este equipo
    (automatizacion COM, requiere 'pip install pywin32').

    Sale desde la cuenta predeterminada de Outlook, o desde
    config['remitente'] si se indica (buzon compartido o alias con permiso
    'Enviar como'). Outlook debe poder abrirse con el usuario que ejecuta la
    tarea programada.

    Args:
        config: diccionario de configuracion ('remitente' opcional).
        destinatarios: lista de direcciones de destino.
        asunto: asunto del mensaje.
        cuerpo: texto plano del mensaje.
        adjunto: ruta de un fichero a adjuntar, o None.

    Returns:
        True si Outlook acepto el mensaje. False si fallo.
    """
    try:
        import win32com.client
    except ImportError:
        log.error("        Falta pywin32 para usar Outlook (pip install pywin32)")
        return False

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mensaje = outlook.CreateItem(0)   # 0 = olMailItem
        mensaje.To = "; ".join(destinatarios)
        mensaje.Subject = asunto
        mensaje.Body = cuerpo
        if config.get('remitente'):
            mensaje.SentOnBehalfOfName = config['remitente']
        if adjunto:
            mensaje.Attachments.Add(str(Path(adjunto).resolve()))
        mensaje.Send()
        return True
    except Exception as e:
        log.error("        No se pudo enviar con Outlook: %s", e)
        return False


def enviar_correo_smtp(config, destinatarios, asunto, cuerpo, adjunto=None):
    """
    Envia un correo por SMTP, con un CSV adjunto opcional.

    La contrasena SMTP se toma de config['smtp_password'] o, si esta vacia,
    de la variable de entorno SMTP_PASSWORD.

    Args:
        config: diccionario de configuracion con las claves smtp_*.
        destinatarios: lista de direcciones de destino.
        asunto: asunto del mensaje.
        cuerpo: texto plano del mensaje.
        adjunto: ruta de un fichero a adjuntar, o None.

    Returns:
        True si el servidor SMTP acepto el mensaje. False si fallo.
    """
    import os

    mensaje = EmailMessage()
    mensaje['From'] = config['remitente']
    mensaje['To'] = ", ".join(destinatarios)
    mensaje['Subject'] = asunto
    mensaje.set_content(cuerpo)

    if adjunto:
        adjunto = Path(adjunto)
        tipo, _ = mimetypes.guess_type(adjunto.name)
        principal, secundario = (tipo or 'text/csv').split('/', 1)
        mensaje.add_attachment(adjunto.read_bytes(), maintype=principal,
                               subtype=secundario, filename=adjunto.name)

    try:
        with smtplib.SMTP(config['smtp_servidor'], int(config['smtp_puerto']), timeout=60) as smtp:
            if config['smtp_starttls']:
                smtp.starttls()
            if config['smtp_usuario']:
                smtp.login(config['smtp_usuario'],
                           config['smtp_password'] or os.environ.get('SMTP_PASSWORD', ''))
            smtp.send_message(mensaje)
        return True
    except Exception as e:
        log.error("        No se pudo enviar el correo: %s", e)
        return False


# ============================================================================
# PROCESO DE UN INFORME
# ============================================================================

def procesar_informe(servidor, config, informe, hoy, enviar):
    """
    Descarga un informe, comprueba su fecha y, si toca, lo envia.

    Args:
        servidor: objeto Server ya autenticado.
        config: diccionario de configuracion.
        informe: diccionario del informe (una entrada de 'informes').
        hoy: objeto date del dia de envio.
        enviar: si es False, hace todo salvo el envio real (--sin-enviar).

    Returns:
        Uno de: 'enviado', 'descartado' (fecha no es hoy: caso normal),
        'error' (fallo tecnico: Tableau, formato, SMTP).
    """
    nombre = informe['nombre']

    # 'fecha_columna' necesita las filas del CSV de datos, asi que fuerza ese origen.
    origen = 'csv' if informe.get('fecha_columna') else informe.get('origen_datos', config['origen_datos'])

    # La fecha se comprueba ANTES de descargar la tabla: si no es la de hoy,
    # no hace falta bajar nada.
    try:
        vista, workbook_luid = localizar_vista(servidor, config, informe)
        if not informe.get('fecha_columna'):
            fecha = fecha_actualizacion_fuentes(servidor, workbook_luid)
            if fecha is None:
                log.error("        No se puede comprobar la fecha de actualizacion de este workbook")
                log.error("        Si la fecha esta dentro del dashboard, indica 'fecha_columna'. "
                          "Ejecuta con --diagnostico para ver de donde salen sus datos")
                return 'error'
            if fecha != hoy:
                log.warning("        DESCARTADO: datos actualizados el %s, no el %s",
                            fecha.strftime('%d/%m/%Y'), hoy.strftime('%d/%m/%Y'))
                return 'descartado'
        if origen == 'crosstab':
            contenido_excel = descargar_excel_bytes(servidor, vista, config['cache_maxima_minutos'])
        else:
            contenido = descargar_tabla(servidor, vista, config['cache_maxima_minutos'])
    except Exception as e:
        log.error("        Error al consultar Tableau: %s", e)
        return 'error'

    ruta = Path(config['directorio_salida']) / f"{sanear_nombre_archivo(nombre)}_{hoy.isoformat()}.csv"

    # Origen 'crosstab': el Excel que exporta Tableau ya tiene la disposicion
    # del dashboard; solo se convierte a CSV, que es lo que se envia.
    if origen == 'crosstab':
        try:
            tabla = excel_a_filas(contenido_excel, informe.get('hoja_excel'))
        except Exception as e:
            log.error("        No se pudo leer el Excel de Tableau: %s", e)
            return 'error'
        if len(tabla) < 2:
            log.warning("        La tabla llego sin filas: no se envia")
            return 'error'
        log.info("        Fecha de actualizacion correcta (%s), %d filas (disposicion del dashboard)",
                 fecha.strftime('%d/%m/%Y'), len(tabla) - 1)
        escribir_filas_csv(ruta, tabla, config['csv_separador'])
        return enviar_informe(config, informe, ruta, hoy, enviar)

    columnas, filas = leer_csv(contenido)
    if not filas:
        log.warning("        La tabla llego sin filas: no se envia")
        return 'error'

    # Alternativa: la fecha esta en una columna del propio dashboard.
    if informe.get('fecha_columna'):
        fecha = fecha_actualizacion(filas, informe['fecha_columna'], config['formatos_fecha'])
        if fecha is None:
            log.error("        No se pudo leer la fecha de actualizacion en la columna '%s'",
                      informe['fecha_columna'])
            log.error("        Columnas recibidas: %s", ", ".join(columnas))
            return 'error'
        if fecha != hoy:
            log.warning("        DESCARTADO: datos actualizados el %s, no el %s",
                        fecha.strftime('%d/%m/%Y'), hoy.strftime('%d/%m/%Y'))
            return 'descartado'

    log.info("        Fecha de actualizacion correcta (%s), %d filas", fecha.strftime('%d/%m/%Y'), len(filas))

    medidas_pivot = []   # columnas que han salido de pivotar 'Measure Names'
    if informe.get('pivotar_medidas', True):
        filas_largas = len(filas)
        columnas_largas = columnas
        columnas, filas = pivotar_medidas(columnas, filas)
        medidas_pivot = [c for c in columnas if c not in columnas_largas]
        if len(filas) != filas_largas:
            log.info("        Medidas pivotadas: %d filas largas -> %d filas, %d columnas",
                     filas_largas, len(filas), len(columnas))

    if informe.get('excluir_columnas'):
        columnas = [c for c in columnas if c not in informe['excluir_columnas']]

    columnas, filas = dar_formato_columnas(
        columnas, filas, informe.get('renombrar_columnas'), informe.get('orden_columnas'))

    # Por defecto son porcentajes las columnas cuyo nombre empieza por '%'
    # (p. ej. '% S/ Ppto'); 'columnas_porcentaje' lo fija a mano (con []
    # se desactiva). Los nombres son los finales, tras renombrar.
    columnas_pct = informe.get('columnas_porcentaje')
    if columnas_pct is None:
        columnas_pct = [c for c in columnas if c.lstrip().startswith('%')]
    if columnas_pct:
        formatear_porcentajes(filas, columnas_pct,
                              informe.get('decimales_porcentaje', config['decimales_porcentaje']))
        log.info("        Columnas de porcentaje: %s", ", ".join(columnas_pct))

    # Importes: por defecto, las medidas que salen del pivotado (sin las de
    # porcentaje). 'columnas_numericas' lo fija a mano; 'decimales_numeros'
    # a null (None) desactiva el redondeo.
    decimales = informe.get('decimales_numeros', config['decimales_numeros'])
    columnas_num = informe.get('columnas_numericas')
    if columnas_num is None:
        renombrar = informe.get('renombrar_columnas') or {}
        columnas_num = [renombrar.get(c, c) for c in medidas_pivot]
    columnas_num = [c for c in columnas_num if c in columnas and c not in columnas_pct]
    if decimales is not None and columnas_num:
        redondear_numeros(filas, columnas_num, decimales)
        log.info("        Columnas redondeadas a %d decimales: %s", decimales, ", ".join(columnas_num))

    escribir_csv(ruta, columnas, filas, config['csv_separador'])
    return enviar_informe(config, informe, ruta, hoy, enviar)


def enviar_informe(config, informe, ruta, hoy, enviar):
    """
    Envia por correo el CSV ya generado de un informe (o, con --sin-enviar,
    solo lo deja en disco).

    Args:
        config: diccionario de configuracion.
        informe: diccionario del informe (una entrada de 'informes').
        ruta: ruta del CSV generado.
        hoy: objeto date del dia de envio.
        enviar: si es False, no se envia nada.

    Returns:
        'enviado' si se envio (o si es una prueba sin envio), 'error' si
        el correo fallo.
    """
    nombre = informe['nombre']
    if not enviar:
        log.info("        (modo --sin-enviar) CSV generado en %s", ruta)
        return 'enviado'

    destinatarios = informe.get('destinatarios') or config['destinatarios']
    asunto = f"{nombre} - datos a {hoy.strftime('%d/%m/%Y')}"
    cuerpo = (f"Buenos dias,\n\nadjuntamos el informe \"{nombre}\" con los datos "
              f"actualizados a {hoy.strftime('%d/%m/%Y')}.\n\nUn saludo.")
    if not enviar_correo(config, destinatarios, asunto, cuerpo, ruta):
        return 'error'

    log.info("        Enviado a %s", ", ".join(destinatarios))
    return 'enviado'


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
    parser.add_argument('--aviso', action='store_true',
                        help="envia a 'destinatarios_aviso' la lista de informes que no salieron "
                             "(usar solo en la ultima ejecucion del dia)")
    args = parser.parse_args()

    inicio = time.time()
    config = cargar_config(args.config)
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

    estado = cargar_estado(config['archivo_estado'])
    ya_enviados = set() if args.forzar or not enviar else set(estado.get(hoy_txt, []))

    pendientes = [i for i in config['informes'] if i['nombre'] not in ya_enviados]
    for i in config['informes']:
        if i['nombre'] in ya_enviados:
            log.info("[ya enviado hoy] %s", i['nombre'])

    resultados = {'enviado': [], 'descartado': [], 'error': []}
    if pendientes:
        servidor = conectar_tableau(config)
        for numero, informe in enumerate(pendientes, start=1):
            log.info("[%d/%d] %s", numero, len(pendientes), informe['nombre'])
            resultado = procesar_informe(servidor, config, informe, hoy, enviar)
            resultados[resultado].append(informe['nombre'])
            if resultado == 'enviado' and enviar:
                estado.setdefault(hoy_txt, []).append(informe['nombre'])
                guardar_estado(config['archivo_estado'], estado, hoy_txt)
        try:
            servidor.auth.sign_out()
        except Exception:
            pass

    # Aviso interno de lo que no salio, para que no pase desapercibido. Solo
    # con --aviso: si la tarea se repite varias veces al dia, se pide solo en
    # la ultima para no mandar un aviso en cada pasada.
    incidencias = resultados['descartado'] + resultados['error']
    if args.aviso and incidencias and enviar and config['destinatarios_aviso']:
        cuerpo = (f"Informes que NO se han enviado hoy ({hoy.strftime('%d/%m/%Y')}):\n\n"
                  + "\n".join(f"- {n} (fecha de datos no actualizada)" for n in resultados['descartado'])
                  + ("\n" if resultados['descartado'] else "")
                  + "\n".join(f"- {n} (error tecnico, ver log)" for n in resultados['error']))
        enviar_correo(config, config['destinatarios_aviso'],
                      f"Aviso envio CSV Tableau {hoy.strftime('%d/%m/%Y')}: {len(incidencias)} sin enviar",
                      cuerpo)

    log.info("=" * 60)
    log.info("RESUMEN: enviados %d | descartados por fecha %d | errores %d | ya enviados hoy %d | %ds",
             len(resultados['enviado']), len(resultados['descartado']),
             len(resultados['error']), len(ya_enviados), int(time.time() - inicio))
    log.info("=" * 60)

    if resultados['error']:
        sys.exit(1)


if __name__ == '__main__':
    main()
