--------------------------------------------------------------------------------
-- DESCARGA_WORKBOOKS (modificada)
--------------------------------------------------------------------------------
-- Unico cambio respecto a la version anterior: se anade VERSION_ACTUAL,
-- tomada de ITEM_REVISION, para que Actualizar_MDM_TABLEAU_GIT_CONTENT.sql
-- pueda comparar la version que hay AHORA en Tableau contra la ultima
-- version que ya tenemos registrada en MDM_TABLEAU_GIT_CONTENT.
--
-- NOTA: esta vista sigue sin tener fichero local propio en el servidor de
-- la aplicacion -- vive unicamente como objeto en Oracle. Este fichero es
-- la copia de referencia para el repositorio de scripts de base de datos;
-- cualquier cambio futuro debe aplicarse aqui Y en Oracle a la vez.
--------------------------------------------------------------------------------

CREATE OR REPLACE VIEW DESCARGA_WORKBOOKS AS
WITH PROYECTOS AS (
    SELECT ITEM_ID, ITEM_NAME, PARENT_ID
    FROM MDM_TABLEAU_SITE_CONTENT
    WHERE ITEM_TYPE = 'Project'
),
RUTAS_JERARQUICAS AS (
    SELECT
        ITEM_ID,
        SYS_CONNECT_BY_PATH(ITEM_NAME, '/') AS RUTA_COMPLETA
    FROM PROYECTOS
    START WITH PARENT_ID IS NULL
    CONNECT BY PRIOR ITEM_ID = PARENT_ID
)
-- Parte 1: workbooks reales, con su version actual
SELECT
    w.ITEM_LUID                          AS WORKBOOK_LUID,
    w.ITEM_NAME                          AS WORKBOOK,
    LTRIM(r.RUTA_COMPLETA, '/')          AS RUTA_PROYECTO,
    NULL                                 AS RUTA_LOCAL_DESTINO,
    w.OWNER_EMAIL                        AS OWNER_EMAIL,
    w.ULTIMA_ACTUALIZACION               AS ULTIMA_ACTUALIZACION,
    w.ITEM_TYPE                          AS TIPO_ITEM,
    w.ITEM_REVISION                      AS VERSION_ACTUAL,   -- ### NUEVO ###
    'SI'                                 AS DESCARGAR
FROM MDM_TABLEAU_SITE_CONTENT w
JOIN RUTAS_JERARQUICAS r
    ON r.ITEM_ID = w.PARENT_ID
WHERE w.ITEM_TYPE = 'Workbook'

UNION ALL

-- Parte 2: filas de "carpeta intermedia", solo como control visual al
-- revisar la consulta a mano (el script Python las descarta siempre, por
-- no tener WORKBOOK_LUID)
SELECT
    NULL                                 AS WORKBOOK_LUID,
    'N/A (carpeta intermedia)'           AS WORKBOOK,
    LTRIM(r.RUTA_COMPLETA, '/')          AS RUTA_PROYECTO,
    NULL, NULL, NULL,
    'Project'                            AS TIPO_ITEM,
    NULL                                 AS VERSION_ACTUAL,
    NULL                                 AS DESCARGAR
FROM RUTAS_JERARQUICAS r
WHERE NOT EXISTS (
    SELECT 1 FROM MDM_TABLEAU_SITE_CONTENT w
    WHERE w.PARENT_ID = r.ITEM_ID AND w.ITEM_TYPE = 'Workbook'
)
;
