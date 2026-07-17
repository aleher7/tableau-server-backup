def parsear_lista_workbooks(
    ruta_archivo,
    separador=";",
    encoding="cp1252"
):
    """Lee y valida el CSV generado por SQL*Plus."""

    ruta = Path(ruta_archivo)

    try:
        logger.info(
            "[PARSEANDO] Archivo: %s",
            ruta
        )
        logger.info(
            "[PARSEANDO] Separador: %r",
            separador
        )
        logger.info(
            "[PARSEANDO] Codificación: %s",
            encoding
        )

        if not ruta.is_file():
            logger.error(
                "[ERROR] El archivo no existe: %s",
                ruta
            )
            return None

        if ruta.stat().st_size == 0:
            logger.error(
                "[ERROR] El archivo está vacío: %s",
                ruta
            )
            return None

        df = pd.read_csv(
            ruta,
            sep=separador,
            dtype=str,
            encoding=encoding,
            quotechar='"',
            keep_default_na=False,
            skipinitialspace=True
        )

        # Limpiar y normalizar los encabezados.
        df.columns = [
            str(columna).strip().upper()
            for columna in df.columns
        ]

        # Limpiar espacios de todos los valores.
        for columna in df.columns:
            df[columna] = (
                df[columna]
                .astype(str)
                .str.strip()
            )

        columnas_requeridas = {
            "WORKBOOK_LUID",
            "WORKBOOK"
        }

        columnas_faltantes = (
            columnas_requeridas
            - set(df.columns)
        )

        if columnas_faltantes:
            logger.error(
                "[ERROR] Faltan columnas requeridas: %s",
                ", ".join(
                    sorted(columnas_faltantes)
                )
            )
            logger.error(
                "[INFO] Columnas disponibles: %s",
                ", ".join(df.columns)
            )
            return None

        if "RUTA_PROYECTO" not in df.columns:
            logger.warning(
                "[AVISO] RUTA_PROYECTO no encontrada. "
                "Se utilizará 'default'"
            )
            df["RUTA_PROYECTO"] = "default"

        if "RUTA_LOCAL_DESTINO" not in df.columns:
            logger.warning(
                "[AVISO] RUTA_LOCAL_DESTINO no encontrada"
            )
            df["RUTA_LOCAL_DESTINO"] = ""

        # Eliminar filas sin identificador o nombre.
        df = df[
            (df["WORKBOOK_LUID"] != "")
            & (df["WORKBOOK"] != "")
        ]

        # Protección adicional por si SQL no aplica el filtro.
        if "TIPO_ITEM" in df.columns:
            df = df[
                df["TIPO_ITEM"].str.upper()
                == "DESCARGAR"
            ]

        # Evitar descargas duplicadas.
        df = df.drop_duplicates(
            subset=["WORKBOOK_LUID"],
            keep="last"
        )

        df = df.reset_index(drop=True)

        logger.info(
            "[OK] Workbooks válidos: %d",
            len(df)
        )
        logger.info(
            "[COLUMNAS] %s",
            ", ".join(df.columns)
        )

        return df

    except UnicodeDecodeError:
        logger.exception(
            "[ERROR] La codificación del archivo "
            "no coincide con encoding_lista"
        )
        return None

    except pd.errors.ParserError:
        logger.exception(
            "[ERROR] El archivo no tiene un "
            "formato CSV válido"
        )
        return None

    except Exception:
        logger.exception(
            "[ERROR] No se pudo parsear el archivo"
        )
        return None
