--------------------------------------------------------------------------------
-- DESCARGA_DATASOURCES
--------------------------------------------------------------------------------
-- Equivalente a DESCARGA_WORKBOOKS, pero para fuentes de datos. Mas simple
-- porque no hay que comparar version exacta contra la tabla de control:
-- basta con que VERSION_ACTUAL sea DISTINTA de la ultima registrada (o que
-- no haya fila todavia) para considerarla pendiente.
--
-- OJO: verificar el valor real de ITEM_TYPE para las fuentes de datos con
--   SELECT DISTINCT ITEM_TYPE FROM MDM_TABLEAU_SITE_CONTENT;
-- Este script asume 'Datasource'; ajustar si el valor real es distinto.
--------------------------------------------------------------------------------

CREATE OR REPLACE VIEW DESCARGA_DATASOURCES AS
WITH PROYECTOS AS (
    SELECT
        ITEM_ID,
        ITEM_NAME,
        ITEM_PARENT_PROJECT_ID AS PARENT_ID
    FROM MDM_TABLEAU_SITE_CONTENT
    WHERE ITEM_TYPE = 'Project'
),
RUTAS_JERARQUICAS AS (
    SELECT
        ITEM_ID,
        ITEM_NAME,
        PARENT_ID,
        LTRIM(SYS_CONNECT_BY_PATH(ITEM_NAME, '/'), '/') AS RUTA_COMPLETA
    FROM PROYECTOS
    START WITH PARENT_ID IS NULL
    CONNECT BY PRIOR ITEM_ID = PARENT_ID
),
-- Cada republicacion de una fuente de datos en Tableau crea un LUID
-- nuevo; el/los LUID anteriores quedan como objetos ya inexistentes, pero
-- MDM_TABLEAU_SITE_CONTENT nunca los retira. Sin esta deduplicacion, la
-- vista intentaria descargar tambien esos LUID muertos, y Tableau
-- respondería con "404 Resource Not Found" en cada uno.
-- Se identifica "la misma fuente de datos a lo largo del tiempo" por su
-- nombre dentro del mismo proyecto, y se descarta todo lo que no sea la
-- fila mas reciente de ese grupo.
--
-- Ademas, se filtra DATA_SOURCE_CONTENT_TYPE = 'Published': las fuentes
-- "Embedded" viven incrustadas dentro de un workbook concreto (no son un
-- recurso descargable por separado -- la API siempre devuelve 404 al
-- intentarlo) y ya quedan respaldadas junto con su workbook via
-- descargar_workbooks.py, asi que no hace falta ni es posible tratarlas
-- aqui.
DATASOURCES_ACTUALES AS (
    SELECT
        ds.*,
        ROW_NUMBER() OVER (
            PARTITION BY ds.ITEM_NAME, ds.ITEM_PARENT_PROJECT_ID
            ORDER BY ds.UPDATED_AT_LOC_HORA DESC
        ) AS RN
    FROM MDM_TABLEAU_SITE_CONTENT ds
    WHERE ds.ITEM_TYPE = 'Datasource'  -- verificar valor real, ver aviso arriba
      AND ds.DATA_SOURCE_CONTENT_TYPE = 'Published'
)

SELECT
    d.ITEM_LUID                                   AS DATASOURCE_LUID,
    d.ITEM_NAME                                   AS DATASOURCE,
    rj.RUTA_COMPLETA                              AS RUTA_PROYECTO,
    -- Carpeta de destino real: si la carpeta del proyecto NO tiene ninguna
    -- subcarpeta (es "hoja"), la fuente de datos va dentro de una
    -- subcarpeta "DataSources"; si SI tiene subcarpetas, va suelta
    -- directamente en la carpeta del proyecto (de momento).
    --
    -- EXCEPCION: si la propia carpeta hoja YA se llama "Data Sources" /
    -- "DataSources" (indistinto de espacios/mayusculas) -- porque alguien
    -- la creo asi a mano en Tableau -- se usa esa carpeta directamente,
    -- sin anadir otra "DataSources" encima (evita la duplicacion
    -- ".../Data Sources/DataSources" detectada en produccion).
    CASE
        WHEN NOT EXISTS (
                SELECT 1 FROM PROYECTOS p2
                WHERE p2.PARENT_ID = d.ITEM_PARENT_PROJECT_ID
             )
         AND UPPER(REPLACE(rj.ITEM_NAME, ' ', '')) != 'DATASOURCES'
        THEN rj.RUTA_COMPLETA || '/DataSources'
        ELSE rj.RUTA_COMPLETA
    END                                            AS RUTA_DESTINO,
    d.OWNER_EMAIL,
    d.UPDATED_AT_LOC_HORA                         AS ULTIMA_ACTUALIZACION,
    'DATASOURCE'                                  AS TIPO_ITEM,
    d.ITEM_REVISION                               AS VERSION_ACTUAL,
    -- 'S' solo si YA existe una fila para este LUID Y su VERSION coincide
    -- con la actual de Tableau (nada nuevo que subir). Si la fila no
    -- existe (primera vez) o la version es distinta (cambio), sale 'N'.
    CASE
        WHEN g.DATASOURCE_LUID IS NOT NULL
         AND g.VERSION = d.ITEM_REVISION
         AND g.FLG_SUBIDO_GITHUB = 1
        THEN 'S' ELSE 'N'
    END                                            AS YA_SUBIDO
FROM DATASOURCES_ACTUALES d
JOIN RUTAS_JERARQUICAS rj
  ON rj.ITEM_ID = d.ITEM_PARENT_PROJECT_ID
LEFT JOIN MDM_TABLEAU_GIT_CONTENT_DATASOURCES g
  ON g.DATASOURCE_LUID = d.ITEM_LUID
WHERE d.RN = 1

ORDER BY RUTA_PROYECTO, DATASOURCE;
