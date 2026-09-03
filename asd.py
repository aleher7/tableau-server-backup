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
)
 
SELECT
    d.ITEM_LUID                                   AS DATASOURCE_LUID,
    d.ITEM_NAME                                   AS DATASOURCE,
    rj.RUTA_COMPLETA                              AS RUTA_PROYECTO,
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
FROM MDM_TABLEAU_SITE_CONTENT d
JOIN RUTAS_JERARQUICAS rj
  ON rj.ITEM_ID = d.ITEM_PARENT_PROJECT_ID
LEFT JOIN MDM_TABLEAU_GIT_CONTENT_DATASOURCES g
  ON g.DATASOURCE_LUID = d.ITEM_LUID
WHERE d.ITEM_TYPE = 'Datasource'  -- verificar valor real, ver aviso arriba
 
ORDER BY RUTA_PROYECTO, DATASOURCE;






-------------------------------------------------------


--------------------------------------------------------------------------------
-- MDM_TABLEAU_GIT_CONTENT_DATASOURCES
--------------------------------------------------------------------------------
-- Tabla de control para el backup de fuentes de datos. A diferencia de
-- MDM_TABLEAU_GIT_CONTENT (workbooks), NO se conservan versiones historicas:
-- una fila = una fuente de datos, y se ACTUALIZA sobre si misma cuando hay
-- una version nueva en Tableau. No hay politica de retencion, no hay
-- FLG_DELETE, no hay DATE_DELETE.
--------------------------------------------------------------------------------

CREATE TABLE MDM_TABLEAU_GIT_CONTENT_DATASOURCES
(
    DATASOURCE_LUID   VARCHAR2(100 BYTE)  NOT NULL,
    -- Identificador de la fuente de datos en Tableau. Es la clave primaria:
    -- una fila por fuente de datos, no una por version.

    FILE_TYPE         VARCHAR2(500 BYTE),
    -- Tipo de item (heredado de MDM_TABLEAU_SITE_CONTENT.ITEM_TYPE).

    NAME              VARCHAR2(500 BYTE),
    -- Nombre del ARCHIVO tal como se sube a GitHub. Sin sufijo de version
    -- (a diferencia de los workbooks): "NombreFuenteDeDatos", sin mas.
    -- El archivo se sobrescribe en cada version nueva.

    VERSION           NUMBER,
    -- Ultima version conocida (ITEM_REVISION de Tableau). Se usa solo para
    -- detectar si hay una version MAS RECIENTE que la ya subida, no para
    -- conservar historial.

    NAVIGATION        VARCHAR2(500 BYTE),
    -- Ruta del proyecto en Tableau, para saber en que subcarpeta del
    -- repositorio va el archivo.

    DATE_UPLOAD       TIMESTAMP,
    -- Fecha de la ULTIMA actualizacion registrada (se pisa cada vez que
    -- hay una version nueva, no se acumula historico).

    FLG_SUBIDO_GITHUB NUMBER(1) DEFAULT 0 NOT NULL
    -- 0 al insertar la fila O al detectar una version nueva (VERSION
    -- cambiada). Python lo pone a 1 SOLO tras confirmar el push a GitHub.
    -- Mismo mecanismo de fiabilidad que en MDM_TABLEAU_GIT_CONTENT: si
    -- Python nunca llegara a confirmar la subida, se reintenta la
    -- siguiente noche en vez de darse por hecha.
)
;

ALTER TABLE MDM_TABLEAU_GIT_CONTENT_DATASOURCES
    ADD CONSTRAINT PK_MDM_TABLEAU_GIT_CONTENT_DS
    PRIMARY KEY (DATASOURCE_LUID)
;

CREATE INDEX IX_GIT_CONTENT_DS_SUBIDO
    ON MDM_TABLEAU_GIT_CONTENT_DATASOURCES (FLG_SUBIDO_GITHUB);

    --------------------------------------------------------------------------


    --------------------------------------------------------------------------------
-- Actualizar_MDM_TABLEAU_GIT_CONTENT_DATASOURCES.sql
--------------------------------------------------------------------------------
-- Subproceso nocturno, se ejecuta ANTES de Descarga_Datasources.sql. A
-- diferencia del de workbooks, no hay politica de retencion: solo hay que
-- registrar fuentes de datos nuevas, y marcar como "pendiente de subir"
-- (FLG_SUBIDO_GITHUB=0) las que hayan cambiado de version desde la ultima
-- vez. No genera ningun CSV de eliminacion (no aplica: sin versiones no
-- hay nada que retirar por antiguedad).
--------------------------------------------------------------------------------

WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET SERVEROUTPUT ON;

-- Igual que en el proceso de workbooks: evita el ORA-12838 si el entorno
-- ejecuta el INSERT/UPDATE en modo paralelo por defecto.
ALTER SESSION DISABLE PARALLEL DML;

DECLARE
    v_hoy TIMESTAMP := SYSTIMESTAMP;
BEGIN
    ----------------------------------------------------------------------
    -- PASO 1: registrar fuentes de datos que no existen todavia
    ----------------------------------------------------------------------
    INSERT INTO MDM_TABLEAU_GIT_CONTENT_DATASOURCES
        (DATASOURCE_LUID, FILE_TYPE, NAME, VERSION, NAVIGATION,
         DATE_UPLOAD, FLG_SUBIDO_GITHUB)
    SELECT
        d.DATASOURCE_LUID,
        d.TIPO_ITEM,
        d.DATASOURCE,          -- SIN sufijo de version: nombre tal cual
        d.VERSION_ACTUAL,
        d.RUTA_PROYECTO,
        v_hoy,
        0
    FROM DESCARGA_DATASOURCES d
    WHERE d.DATASOURCE_LUID IS NOT NULL
      AND NOT EXISTS (
            SELECT 1
            FROM MDM_TABLEAU_GIT_CONTENT_DATASOURCES g
            WHERE g.DATASOURCE_LUID = d.DATASOURCE_LUID
      );

    DBMS_OUTPUT.PUT_LINE('Fuentes de datos nuevas registradas: ' || SQL%ROWCOUNT);

    ----------------------------------------------------------------------
    -- PASO 2: actualizar las que YA existian pero tienen version nueva
    ----------------------------------------------------------------------
    -- Se sobrescribe la fila (no se conserva la version anterior) y se
    -- resetea FLG_SUBIDO_GITHUB a 0, para que la vista la vuelva a
    -- ofrecer como pendiente de descargar/subir.
    UPDATE MDM_TABLEAU_GIT_CONTENT_DATASOURCES g
    SET (VERSION, NAVIGATION, DATE_UPLOAD, FLG_SUBIDO_GITHUB) = (
        SELECT d.VERSION_ACTUAL, d.RUTA_PROYECTO, v_hoy, 0
        FROM DESCARGA_DATASOURCES d
        WHERE d.DATASOURCE_LUID = g.DATASOURCE_LUID
    )
    WHERE EXISTS (
        SELECT 1
        FROM DESCARGA_DATASOURCES d
        WHERE d.DATASOURCE_LUID = g.DATASOURCE_LUID
          AND d.VERSION_ACTUAL != g.VERSION
    );

    DBMS_OUTPUT.PUT_LINE('Fuentes de datos actualizadas (version nueva): ' || SQL%ROWCOUNT);

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('MDM_TABLEAU_GIT_CONTENT_DATASOURCES actualizada y confirmada.');
END;
/

EXIT;



----------------------------------------------------------


WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE

SET ECHO OFF
SET FEEDBACK OFF
SET VERIFY OFF
SET HEADING ON
SET TERMOUT OFF

SET MARKUP CSV ON DELIMITER ',' QUOTE ON

SPOOL C:\tabcmd\TableauGitHub\lista_datasources.csv

-- Sin filas de "carpeta intermedia" como en Descarga.sql: aqui no hace
-- falta verificar que ninguna carpeta se pierde, porque las fuentes de
-- datos no crean estructura propia, solo se guardan en la carpeta de
-- proyecto que ya existe (creada por el proceso de workbooks o por Python
-- si hiciera falta).
SELECT DATASOURCE_LUID, DATASOURCE, RUTA_PROYECTO, OWNER_EMAIL,
       ULTIMA_ACTUALIZACION, TIPO_ITEM, VERSION_ACTUAL
FROM DESCARGA_DATASOURCES
WHERE YA_SUBIDO = 'N';

SPOOL OFF

SET MARKUP CSV OFF

EXIT
