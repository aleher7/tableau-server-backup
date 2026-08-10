--------------------------------------------------------------------------------
-- DESCARGA_WORKBOOKS (version con versionado)
--------------------------------------------------------------------------------
-- Cambios respecto a la version anterior (ver Descarga_vPreVersionWorkbook.sql
-- y descargar_workbooks_vPreVersionWorkbook.py como referencia del sistema
-- previo al versionado):
--
--   1. VERSION_ACTUAL (= ITEM_REVISION) -- YA ESTABA en la version real que
--      esta en Oracle ahora mismo (confirmado por el usuario), se mantiene igual.
--
--   2. NUEVO: LEFT JOIN contra MDM_TABLEAU_GIT_CONTENT. Compara la version
--      actual de Tableau contra lo que YA esta registrado y VIVO (FLG_DELETE=0)
--      en la tabla de versionado. Si ya existe esa version exacta, YA_SUBIDO
--      vale 'S' y el script Python se salta ese workbook -- no lo descarga
--      ni lo sube de nuevo. Esto es lo que evita repetir trabajo cuando un
--      workbook no ha cambiado de una noche a otra.
--
-- IMPORTANTE: esta vista sigue sin tener fichero local propio en el servidor
-- de la aplicacion, vive unicamente como objeto en Oracle. Este fichero es
-- la copia de referencia para el repositorio de scripts de base de datos;
-- cualquier cambio debe aplicarse aqui Y en Oracle a la vez.
--------------------------------------------------------------------------------

CREATE OR REPLACE VIEW DESCARGA_WORKBOOKS AS
WITH PROYECTOS AS (
    -- Todos los proyectos (carpetas): forman el arbol
    SELECT
        ITEM_ID,
        ITEM_NAME,
        ITEM_PARENT_PROJECT_ID AS PARENT_ID
    FROM MDM_TABLEAU_SITE_CONTENT
    WHERE ITEM_TYPE = 'Project'
),
RUTAS_JERARQUICAS AS (
    -- Recorrido jerarquico: construye ruta completa de cada proyecto
    -- Incluyendo proyectos intermedios sin workbooks directos
    SELECT
        ITEM_ID,
        ITEM_NAME,
        PARENT_ID,
        LTRIM(SYS_CONNECT_BY_PATH(ITEM_NAME, '/'), '/') AS RUTA_COMPLETA,
        LEVEL AS PROFUNDIDAD
    FROM PROYECTOS
    START WITH PARENT_ID IS NULL          -- proyectos raiz
    CONNECT BY PRIOR ITEM_ID = PARENT_ID
)

-- ==========================================================
-- PARTE 1: WORKBOOKS (cada uno con su ruta completa y version)
-- ==========================================================
SELECT
    w.ITEM_LUID                                   AS WORKBOOK_LUID,
    w.ITEM_NAME                                   AS WORKBOOK,
    rj.RUTA_COMPLETA                              AS RUTA_PROYECTO,
    rj.RUTA_COMPLETA || '/' || w.ITEM_NAME        AS RUTA_LOCAL_DESTINO,
    w.OWNER_EMAIL,
    w.UPDATED_AT_LOC_HORA                         AS ULTIMA_ACTUALIZACION,
    'WORKBOOK'                                    AS TIPO_ITEM,
    w.ITEM_REVISION                               AS VERSION_ACTUAL,
    -- ### NUEVO ### 'S' si esta version exacta ya esta subida y viva en
    -- GitHub (segun la tabla de control) -- el script Python se salta
    -- estos workbooks, no los vuelve a descargar ni a subir.
    CASE WHEN g.WORKBOOK_LUID IS NOT NULL THEN 'S' ELSE 'N' END
                                                   AS YA_SUBIDO,
    'SÍ'                                           AS DESCARGAR
FROM MDM_TABLEAU_SITE_CONTENT w
JOIN RUTAS_JERARQUICAS rj
  ON rj.ITEM_ID = w.ITEM_PARENT_PROJECT_ID
LEFT JOIN MDM_TABLEAU_GIT_CONTENT g
  ON g.WORKBOOK_LUID = w.ITEM_LUID
 AND g.VERSION       = w.ITEM_REVISION
 AND g.FLG_DELETE    = 0
WHERE w.ITEM_TYPE = 'Workbook'

UNION ALL

-- ==========================================================
-- PARTE 2: CONTROL - Proyectos intermedios sin workbooks
-- ==========================================================
-- Esto te permite verificar que NINGUNA carpeta intermedia se pierde
SELECT
    NULL AS WORKBOOK_LUID,
    'N/A (carpeta intermedia)' AS WORKBOOK,
    rj.RUTA_COMPLETA AS RUTA_PROYECTO,
    rj.RUTA_COMPLETA AS RUTA_LOCAL_DESTINO,
    'N/A' AS OWNER_EMAIL,
    NULL AS ULTIMA_ACTUALIZACION,
    'CARPETA INTERMEDIA' AS TIPO_ITEM,
    NULL AS VERSION_ACTUAL,
    NULL AS YA_SUBIDO,
    'Solo crear la carpeta' AS DESCARGAR
FROM RUTAS_JERARQUICAS rj
WHERE NOT EXISTS (
    -- Excluir si tiene workbooks DIRECTOS (ya salen en la PARTE 1)
    SELECT 1
    FROM MDM_TABLEAU_SITE_CONTENT w
    WHERE w.ITEM_TYPE = 'Workbook'
      AND w.ITEM_PARENT_PROJECT_ID = rj.ITEM_ID
)

ORDER BY RUTA_LOCAL_DESTINO, WORKBOOK, TIPO_ITEM;
