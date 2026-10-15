WITH ORIG AS (
    SELECT /*+ MATERIALIZE */ * FROM ( <consulta original, sin el cruce> )
),
NUEVA AS (
    SELECT /*+ MATERIALIZE */ * FROM ( <consulta nueva, con el cruce> )
)
SELECT 'ORIG' t, ORIGEN_TIPO_SF, COUNT(*) filas, COUNT(DISTINCT ID_SF_VISITA) visitas,
       SUM(FLAG_DIA) flag_dia, SUM(FLAG_DIA_FICHERO) flag_fich,
       SUM(OBJETIVO_MED) objetivo, SUM(PCT_JORNADA) pct_jornada,
       NULL codigos_con_varios_nombres
FROM ORIG GROUP BY ORIGEN_TIPO_SF
UNION ALL
SELECT 'NUEVA', ORIGEN_TIPO_SF, COUNT(*), COUNT(DISTINCT ID_SF_VISITA),
       SUM(FLAG_DIA), SUM(FLAG_DIA_FICHERO), SUM(OBJETIVO_MED), SUM(PCT_JORNADA),
       NULL
FROM NUEVA GROUP BY ORIGEN_TIPO_SF
UNION ALL
SELECT 'UNICIDAD', NULL, NULL, NULL, NULL, NULL, NULL, NULL, COUNT(*)
FROM (SELECT CODIGO_JDE FROM NUEVA
      GROUP BY CODIGO_JDE
      HAVING COUNT(DISTINCT NOMBRE_CLIENTE_JDE) > 1)
ORDER BY 2, 1;








WITH ORIG AS (
    SELECT /*+ MATERIALIZE */ * FROM (
        <consulta original>
    )
),
NUEVA AS (
    SELECT /*+ MATERIALIZE */ * FROM (
        <consulta nueva>
    )
),
A AS (
    SELECT ID_SF_VISITA, Fecha_Visita, YEAR, MONTH, DAY, FRANJA_VISITA, Tipo_Visita, Subtipo_Visita,
           Estado_Visita, ORIGEN_TIPO_SF, Producto_Visita, Marca_Visita, ID_Agente, Agente, Gerente,
           Jefe_Ventas, TIPO_RED, Division, Estado_Agente, ID_CENTRO_SF, ID_CONTACTO_SF, CODIGO_JDE,
           Centro, CIUDAD, PT_CLASIFICACION_CENTRO, PT_GEO1, PT_GEO2, PT_NATURALEZA, PT_TIPO_CONTACTO,
           PT_TIPO_ENTIDAD, NOMBRE_CONTACTO, ESPECIALIDAD_PRINCIPAL, ESPECIALIDAD_SECUNDARIA,
           NUMERO_COLEGIADO, FICHERO_FLAG, Motivo_Ausencia, Fich_Agente_Contacto, PT_CLASIFICACION_CONT,
           flag_dia, Dias_Trabajados, Dias_Trabajados_Agentes, flag_dia_fichero, Dias_Trabajados_fichero,
           Dias_Trabajados_fichero_Agentes, flag_dia_fich_publ, Dias_Trabajados_fich_Publ,
           Dias_Trabajados_Fich_Agentes_Publ, flag_dia_fich_priv, Dias_Trabajados_fich_Priv,
           Dias_Trabajados_Fich_Agentes_Priv, color_ciclo, color_total, CICLO, FASE, OBJETIVO_MED,
           OBJETIVO_FAR, YTD, PREV_YTD, MTD, PREV_MTD, TAM, PREV_TAM, DATE_UPD, PCT_JORNADA
    FROM ORIG
    MINUS
    SELECT ID_SF_VISITA, Fecha_Visita, YEAR, MONTH, DAY, FRANJA_VISITA, Tipo_Visita, Subtipo_Visita,
           Estado_Visita, ORIGEN_TIPO_SF, Producto_Visita, Marca_Visita, ID_Agente, Agente, Gerente,
           Jefe_Ventas, TIPO_RED, Division, Estado_Agente, ID_CENTRO_SF, ID_CONTACTO_SF, CODIGO_JDE,
           Centro, CIUDAD, PT_CLASIFICACION_CENTRO, PT_GEO1, PT_GEO2, PT_NATURALEZA, PT_TIPO_CONTACTO,
           PT_TIPO_ENTIDAD, NOMBRE_CONTACTO, ESPECIALIDAD_PRINCIPAL, ESPECIALIDAD_SECUNDARIA,
           NUMERO_COLEGIADO, FICHERO_FLAG, Motivo_Ausencia, Fich_Agente_Contacto, PT_CLASIFICACION_CONT,
           flag_dia, Dias_Trabajados, Dias_Trabajados_Agentes, flag_dia_fichero, Dias_Trabajados_fichero,
           Dias_Trabajados_fichero_Agentes, flag_dia_fich_publ, Dias_Trabajados_fich_Publ,
           Dias_Trabajados_Fich_Agentes_Publ, flag_dia_fich_priv, Dias_Trabajados_fich_Priv,
           Dias_Trabajados_Fich_Agentes_Priv, color_ciclo, color_total, CICLO, FASE, OBJETIVO_MED,
           OBJETIVO_FAR, YTD, PREV_YTD, MTD, PREV_MTD, TAM, PREV_TAM, DATE_UPD, PCT_JORNADA
    FROM NUEVA
),
B AS (
    SELECT ID_SF_VISITA, Fecha_Visita, YEAR, MONTH, DAY, FRANJA_VISITA, Tipo_Visita, Subtipo_Visita,
           Estado_Visita, ORIGEN_TIPO_SF, Producto_Visita, Marca_Visita, ID_Agente, Agente, Gerente,
           Jefe_Ventas, TIPO_RED, Division, Estado_Agente, ID_CENTRO_SF, ID_CONTACTO_SF, CODIGO_JDE,
           Centro, CIUDAD, PT_CLASIFICACION_CENTRO, PT_GEO1, PT_GEO2, PT_NATURALEZA, PT_TIPO_CONTACTO,
           PT_TIPO_ENTIDAD, NOMBRE_CONTACTO, ESPECIALIDAD_PRINCIPAL, ESPECIALIDAD_SECUNDARIA,
           NUMERO_COLEGIADO, FICHERO_FLAG, Motivo_Ausencia, Fich_Agente_Contacto, PT_CLASIFICACION_CONT,
           flag_dia, Dias_Trabajados, Dias_Trabajados_Agentes, flag_dia_fichero, Dias_Trabajados_fichero,
           Dias_Trabajados_fichero_Agentes, flag_dia_fich_publ, Dias_Trabajados_fich_Publ,
           Dias_Trabajados_Fich_Agentes_Publ, flag_dia_fich_priv, Dias_Trabajados_fich_Priv,
           Dias_Trabajados_Fich_Agentes_Priv, color_ciclo, color_total, CICLO, FASE, OBJETIVO_MED,
           OBJETIVO_FAR, YTD, PREV_YTD, MTD, PREV_MTD, TAM, PREV_TAM, DATE_UPD, PCT_JORNADA
    FROM NUEVA
    MINUS
    SELECT ID_SF_VISITA, Fecha_Visita, YEAR, MONTH, DAY, FRANJA_VISITA, Tipo_Visita, Subtipo_Visita,
           Estado_Visita, ORIGEN_TIPO_SF, Producto_Visita, Marca_Visita, ID_Agente, Agente, Gerente,
           Jefe_Ventas, TIPO_RED, Division, Estado_Agente, ID_CENTRO_SF, ID_CONTACTO_SF, CODIGO_JDE,
           Centro, CIUDAD, PT_CLASIFICACION_CENTRO, PT_GEO1, PT_GEO2, PT_NATURALEZA, PT_TIPO_CONTACTO,
           PT_TIPO_ENTIDAD, NOMBRE_CONTACTO, ESPECIALIDAD_PRINCIPAL, ESPECIALIDAD_SECUNDARIA,
           NUMERO_COLEGIADO, FICHERO_FLAG, Motivo_Ausencia, Fich_Agente_Contacto, PT_CLASIFICACION_CONT,
           flag_dia, Dias_Trabajados, Dias_Trabajados_Agentes, flag_dia_fichero, Dias_Trabajados_fichero,
           Dias_Trabajados_fichero_Agentes, flag_dia_fich_publ, Dias_Trabajados_fich_Publ,
           Dias_Trabajados_Fich_Agentes_Publ, flag_dia_fich_priv, Dias_Trabajados_fich_Priv,
           Dias_Trabajados_Fich_Agentes_Priv, color_ciclo, color_total, CICLO, FASE, OBJETIVO_MED,
           OBJETIVO_FAR, YTD, PREV_YTD, MTD, PREV_MTD, TAM, PREV_TAM, DATE_UPD, PCT_JORNADA
    FROM ORIG
)
SELECT * FROM (
    SELECT 'ORIG' AS lado, A.* FROM A
    UNION ALL
    SELECT 'NUEVA' AS lado, B.* FROM B
)
ORDER BY ID_SF_VISITA, ID_CONTACTO_SF, ID_Agente, Fecha_Visita, lado
FETCH FIRST 40 ROWS ONLY;













WITH NUEVA AS (
    SELECT /*+ MATERIALIZE */ * FROM ( <consulta nueva> )
)
SELECT DISTINCT CODIGO_JDE, Centro AS NOMBRE_ACTUAL, NOMBRE_CLIENTE_JDE,
       CASE WHEN NOMBRE_CLIENTE_JDE IS NULL THEN 'SIN NOMBRE JDE'
            WHEN UPPER(TRIM(Centro)) <> UPPER(TRIM(NOMBRE_CLIENTE_JDE)) THEN 'DIFERENTE'
            ELSE 'IGUAL' END AS COMPARACION
FROM NUEVA
WHERE CODIGO_JDE IS NOT NULL
ORDER BY COMPARACION, CODIGO_JDE;
