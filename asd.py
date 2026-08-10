--------------------------------------------------------------------------------
-- Actualizar_MDM_TABLEAU_GIT_CONTENT.sql
--------------------------------------------------------------------------------
-- Subproceso nocturno, se ejecuta ANTES de Descarga.sql. Hace tres cosas,
-- en este orden, y confirma con COMMIT al final:
--
--   1. Registra las versiones NUEVAS detectadas en Tableau (comparando
--      ITEM_REVISION contra lo que ya hay guardado para ese WORKBOOK_LUID).
--   2. Recalcula que fila es la "ultima version" viva de cada workbook.
--   3. Aplica la politica de retencion y marca que hay que eliminar.
--
-- Un mismo workbook puede modificarse varias veces el mismo dia: no es un
-- problema, porque se compara VERSION exacta (=ITEM_REVISION), no la
-- fecha -- cada revision nueva de Tableau genera su propia fila.
--
-- Politica de retencion:
--   CASO A (workbook activo) -- el workbook sigue en Tableau y ha tenido
--     alguna version dentro de los ultimos 6 meses VENCIDOS (mes en curso
--     no cuenta como cumplido): se retiran solo las versiones individuales
--     mas antiguas que ese corte.
--   CASO B (workbook parado o borrado) -- el workbook ya no existe en
--     Tableau, O existe pero no ha tenido ninguna version nueva en los
--     ultimos 6 meses: se conservan unicamente sus 3 versiones mas
--     recientes, sea cual sea su antiguedad, y se retira el resto.
--------------------------------------------------------------------------------

WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET SERVEROUTPUT ON;

-- Sin esto, el entorno (golddb_high) ejecuta el INSERT del PASO 1 en modo
-- paralelo/direct-path por defecto. Eso deja la tabla bloqueada para
-- lectura/escritura DENTRO de la misma transaccion hasta que se hace
-- COMMIT -- y el UPDATE del PASO 2, que lee y modifica esa misma tabla
-- antes del COMMIT final, chocaba con eso (ORA-12838). Desactivando el
-- DML paralelo para esta sesion, el INSERT se ejecuta en modo normal y
-- el resto del bloque puede leer/modificar la tabla sin problema.
ALTER SESSION DISABLE PARALLEL DML;

DECLARE
    v_fecha_corte TIMESTAMP;   -- limite de los "6 meses vencidos"
    v_hoy         TIMESTAMP := SYSTIMESTAMP;
BEGIN
    -- Se trunca al primer dia del mes actual y se retrocede 6 meses
    -- completos, para que el mes en curso nunca cuente como "cumplido".
    v_fecha_corte := ADD_MONTHS(TRUNC(v_hoy, 'MM'), -6);

    ----------------------------------------------------------------------
    -- PASO 1: insertar las versiones nuevas
    ----------------------------------------------------------------------
    -- DESCARGA_WORKBOOKS.VERSION_ACTUAL es la version que hay AHORA en
    -- Tableau. Si no existe ya una fila con ese WORKBOOK_LUID + VERSION,
    -- es una version nueva que no se conocia.
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
    -- PASO 2: recalcular FLG_LAST_VERSION (solo entre versiones vivas)
    ----------------------------------------------------------------------
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

    -- CASO A: version individual mas antigua que el corte, en un workbook
    -- que SI ha tenido alguna version dentro de los ultimos 6 meses.
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

    DBMS_OUTPUT.PUT_LINE('Caso A (workbook activo, version vieja retirada): ' || SQL%ROWCOUNT);

    -- CASO B: todo lo que sobre de las 3 versiones mas recientes, en
    -- workbooks borrados de Tableau o sin actividad en los ultimos 6 meses.
    UPDATE MDM_TABLEAU_GIT_CONTENT g
    SET g.FLG_DELETE = 1, g.DATE_DELETE = v_hoy
    WHERE g.FLG_DELETE = 0
      AND (
            NOT EXISTS (   -- ya no existe en Tableau
                SELECT 1 FROM DESCARGA_WORKBOOKS d
                WHERE d.WORKBOOK_LUID = g.WORKBOOK_LUID
            )
            OR NOT EXISTS (   -- existe, pero sin version en los ultimos 6 meses
                SELECT 1
                FROM MDM_TABLEAU_GIT_CONTENT g2
                WHERE g2.WORKBOOK_LUID = g.WORKBOOK_LUID
                  AND g2.FLG_DELETE = 0
                  AND g2.DATE_UPLOAD >= v_fecha_corte
            )
      )
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

    DBMS_OUTPUT.PUT_LINE('Caso B (workbook parado/borrado, exceso sobre 3 versiones): ' || SQL%ROWCOUNT);

    -- Se repite el PASO 2: el PASO 3 puede haber retirado la version que
    -- antes era la "ultima viva" de algun workbook.
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
    DBMS_OUTPUT.PUT_LINE('MDM_TABLEAU_GIT_CONTENT actualizada y confirmada.');
END;
/

----------------------------------------------------------------------------
-- Volcar a CSV las versiones marcadas para eliminar HOY
----------------------------------------------------------------------------
-- Es lo unico que necesita Python para saber que archivo (.twbx/.twb) tiene
-- que retirar de GitHub en la ejecucion de esta noche. TRUNC(DATE_DELETE) =
-- TRUNC(SYSDATE) filtra solo las marcadas hoy, no las de noches anteriores.
SET MARKUP CSV ON DELIMITER ',' QUOTE ON
SET HEADING ON
SET FEEDBACK OFF
SET TERMOUT OFF

SPOOL C:\tabcmd\TableauGitHub\lista_workbooks_eliminar.csv

SELECT WORKBOOK_LUID, NAME, NAVIGATION
FROM MDM_TABLEAU_GIT_CONTENT
WHERE FLG_DELETE = 1
  AND TRUNC(DATE_DELETE) = TRUNC(SYSDATE);

SPOOL OFF

SET MARKUP CSV OFF

EXIT;
