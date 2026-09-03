DATASOURCES_ACTUALES AS (
    SELECT
        ds.*,
        ROW_NUMBER() OVER (
            PARTITION BY ds.ITEM_NAME
            ORDER BY ds.UPDATED_AT_LOC_HORA DESC
        ) AS RN
    FROM MDM_TABLEAU_SITE_CONTENT ds
    WHERE ds.ITEM_TYPE = 'Datasource'
)
````//Sustituye la partición por nombre**+proyecto** por partición **solo por nombre** — así, tanto si es "republicada con LUID nuevo en el mismo sitio" como "movida a otro proyecto", nos quedamos con la única fila que de verdad representa el estado actual.

## Antes de aplicarlo, una verificación final de seguridad

Para descartar del todo el riesgo de fusionar por error dos fuentes de datos que sí sean distintas y activas a la vez, ejecuta esto — busca cualquier nombre donde **dos proyectos distintos** hayan tenido actividad **reciente** (menos de 60 días de diferencia entre sus últimas actualizaciones):

```sql
WITH ULTIMAS_POR_PROYECTO AS (
    SELECT ITEM_NAME, ITEM_PARENT_PROJECT_ID,
           MAX(UPDATED_AT_LOC_HORA) AS ultima_actualizacion
    FROM MDM_TABLEAU_SITE_CONTENT
    WHERE ITEM_TYPE = 'Datasource'
      AND ITEM_PARENT_PROJECT_ID IS NOT NULL
    GROUP BY ITEM_NAME, ITEM_PARENT_PROJECT_ID
)
SELECT a.ITEM_NAME,
       a.ITEM_PARENT_PROJECT_ID AS proyecto_1, a.ultima_actualizacion AS fecha_1,
       b.ITEM_PARENT_PROJECT_ID AS proyecto_2, b.ultima_actualizacion AS fecha_2
FROM ULTIMAS_POR_PROYECTO a
JOIN ULTIMAS_POR_PROYECTO b
  ON a.ITEM_NAME = b.ITEM_NAME
 AND a.ITEM_PARENT_PROJECT_ID < b.ITEM_PARENT_PROJECT_ID
WHERE ABS(a.ultima_actualizacion - b.ultima_actualizacion) < 60
ORDER BY a.ITEM_NAME;
