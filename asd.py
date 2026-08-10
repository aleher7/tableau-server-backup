--------------------------------------------------------------------------------
-- Actualizar_MDM_TABLEAU_GIT_CONTENT.sql
--------------------------------------------------------------------------------
-- Subproceso que se ejecuta CADA NOCHE, ANTES de Descarga_CSV.sql. Hace tres
-- cosas, en este orden, y termina con COMMIT:
--
--   1. Registra las versiones NUEVAS detectadas en Tableau (comparando
--      ITEM_REVISION contra lo que ya hay guardado).
--   2. Recalcula que fila es la "ultima version" de cada workbook.
--   3. Aplica la politica de retencion y marca que hay que eliminar.
--
-- Un mismo workbook puede haberse modificado varias veces en el mismo dia:
-- eso no es un problema aqui, porque se compara VERSION exacta (=
-- ITEM_REVISION), no la fecha -- cada revision nueva de Tableau genera su
-- propia fila, aunque haya varias en la misma noche.
--------------------------------------------------------------------------------

WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET SERVEROUTPUT ON;

DECLARE
    v_fecha_corte TIMESTAMP;   -- limite de los "6 meses vencidos"
    v_hoy         TIMESTAMP := SYSTIMESTAMP;
BEGIN
    -- "6 meses vencidos desde hoy": se trunca al primer dia del mes actual
    -- y se retrocede 6 meses completos. Asi un mes en curso nunca cuenta
    -- como "cumplido" a medias.
    v_fecha_corte := ADD_MONTHS(TRUNC(v_hoy, 'MM'), -6);

    ----------------------------------------------------------------------
    -- PASO 1: insertar las versiones nuevas
    ----------------------------------------------------------------------
    -- Se compara la version ACTUAL en Tableau (DESCARGA_WORKBOOKS.VERSION_ACTUAL)
    -- contra lo que ya existe en MDM_TABLEAU_GIT_CONTENT para ese mismo
    -- WORKBOOK_LUID + VERSION. Si no existe la fila, es una version nueva.
    INSERT INTO MDM_TABLEAU_GIT_CONTENT
        (WORKBOOK_LUID, FILE_TYPE, NAME, VERSION, NAVIGATION,
         DATE_UPLOAD, FLG_LAST_VERSION, FLG_DELETE, DATE_DELETE)
    SELECT
        d.WORKBOOK_LUID,
        d.TIPO_ITEM,
        d.WORKBOOK || '_v' || d.VERSION_ACTUAL,   -- "Mi mundo_v3"
        d.VERSION_ACTUAL,
        d.RUTA_PROYECTO,
        v_hoy,
        0,      -- se recalcula en el PASO 2
        0,
        NULL
    FROM DESCARGA_WORKBOOKS d
    WHERE d.WORKBOOK_LUID IS NOT NULL
      AND NOT EXISTS (
            SELECT 1
            FROM MDM_TABLEAU_GIT_CONTENT g
            WHERE g.WORKBOOK_LUID = d.WORKBOOK_LUID
              AND g.VERSION       = d.VERSION_ACTUAL
      );

    DBMS_OUTPUT.PUT_LINE('Versiones nuevas insertadas: ' || SQL%ROWCOUNT);

    ----------------------------------------------------------------------
    -- PASO 2: recalcular FLG_LAST_VERSION
    ----------------------------------------------------------------------
    -- Solo se compara entre las versiones que NO estan ya eliminadas.
    -- Se pone a 1 la de mayor VERSION de cada workbook, y a 0 el resto.
    UPDATE MDM_TABLEAU_GIT_CONTENT g
    SET g.FLG_LAST_VERSION = CASE
        WHEN g.VERSION = (
            SELECT MAX(g2.VERSION)
            FROM MDM_TABLEAU_GIT_CONTENT g2
            WHERE g2.WORKBOOK_LUID = g.WORKBOOK_LUID
              AND g2.FLG_DELETE = 0
        ) THEN 1
        ELSE 0
    END
    WHERE g.FLG_DELETE = 0;

    DBMS_OUTPUT.PUT_LINE('FLG_LAST_VERSION recalculado: ' || SQL%ROWCOUNT || ' filas');

    ----------------------------------------------------------------------
    -- PASO 3: politica de retencion
    ----------------------------------------------------------------------
    -- Por cada workbook (WORKBOOK_LUID) con versiones vivas (FLG_DELETE=0):
    --
    --   CASO A -- el workbook sigue en Tableau Y ha tenido alguna version
    --            dentro de los ultimos 6 meses vencidos:
    --            se eliminan solo las versiones mas antiguas que esos 6 meses.
    --
    --   CASO B -- el workbook ya NO existe en Tableau (fue borrado), O
    --            existe pero no ha tenido ninguna version nueva en los
    --            ultimos 6 meses (esta "parado"):
    --            se conservan unicamente sus 3 versiones mas recientes,
    --            sea cual sea su antiguedad, y se elimina el resto.
    ----------------------------------------------------------------------

    -- CASO A: versiones individuales mas antiguas que el corte, de
    -- workbooks que SI han tenido actividad reciente
    UPDATE MDM_TABLEAU_GIT_CONTENT g
    SET g.FLG_DELETE = 1, g.DATE_DELETE = v_hoy
    WHERE g.FLG_DELETE = 0
      AND g.DATE_UPLOAD < v_fecha_corte
      AND EXISTS (
            SELECT 1 FROM DESCARGA_WORKBOOKS d
            WHERE d.WORKBOOK_LUID = g.WORKBOOK_LUID
      )
      AND EXISTS (
            SELECT 1
            FROM MDM_TABLEAU_GIT_CONTENT g2
            WHERE g2.WORKBOOK_LUID = g.WORKBOOK_LUID
              AND g2.FLG_DELETE = 0
              AND g2.DATE_UPLOAD >= v_fecha_corte
      );

    DBMS_OUTPUT.PUT_LINE('Caso A (workbooks activos, version vieja retirada): ' || SQL%ROWCOUNT);

    -- CASO B: todo lo que sobre de las 3 versiones mas recientes, en
    -- workbooks borrados de Tableau o sin actividad en 6 meses
    UPDATE MDM_TABLEAU_GIT_CONTENT g
    SET g.FLG_DELETE = 1, g.DATE_DELETE = v_hoy
    WHERE g.FLG_DELETE = 0
      AND (
            -- ya no existe en Tableau
            NOT EXISTS (
                SELECT 1 FROM DESCARGA_WORKBOOKS d
                WHERE d.WORKBOOK_LUID = g.WORKBOOK_LUID
            )
            OR
            -- existe, pero sin ninguna version dentro de los ultimos 6 meses
            NOT EXISTS (
                SELECT 1
                FROM MDM_TABLEAU_GIT_CONTENT g2
                WHERE g2.WORKBOOK_LUID = g.WORKBOOK_LUID
                  AND g2.FLG_DELETE = 0
                  AND g2.DATE_UPLOAD >= v_fecha_corte
            )
      )
      -- y no esta entre las 3 versiones mas recientes de ese workbook
      AND g.VERSION NOT IN (
            SELECT VERSION FROM (
                SELECT g3.VERSION,
                       ROW_NUMBER() OVER (
                           PARTITION BY g3.WORKBOOK_LUID
                           ORDER BY g3.VERSION DESC
                       ) AS RN
                FROM MDM_TABLEAU_GIT_CONTENT g3
                WHERE g3.WORKBOOK_LUID = g.WORKBOOK_LUID
                  AND g3.FLG_DELETE = 0
            )
            WHERE RN <= 3
      );

    DBMS_OUTPUT.PUT_LINE('Caso B (workbooks parados/borrados, exceso sobre 3 versiones): ' || SQL%ROWCOUNT);

    -- El PASO 2 se repite aqui porque el PASO 3 puede haber marcado como
    -- eliminada la que antes era la "ultima version" de algun workbook
    -- (por ejemplo, un workbook borrado de Tableau cuya version mas nueva
    -- ya tenia mas de 6 meses cae en el caso B si sobraba de las 3).
    UPDATE MDM_TABLEAU_GIT_CONTENT g
    SET g.FLG_LAST_VERSION = CASE
        WHEN g.VERSION = (
            SELECT MAX(g2.VERSION)
            FROM MDM_TABLEAU_GIT_CONTENT g2
            WHERE g2.WORKBOOK_LUID = g.WORKBOOK_LUID
              AND g2.FLG_DELETE = 0
        ) THEN 1
        ELSE 0
    END
    WHERE g.FLG_DELETE = 0;

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Actualizacion de MDM_TABLEAU_GIT_CONTENT completada y confirmada.');
END;
/
EXIT;
