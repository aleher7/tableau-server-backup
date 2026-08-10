WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
 
SET ECHO OFF
SET FEEDBACK OFF
SET VERIFY OFF
SET HEADING ON 
SET TERMOUT OFF
 
SET MARKUP CSV ON DELIMITER ',' QUOTE ON
 
SPOOL C:\tabcmd\TableauGitHub\lista_workbooks.csv
 
-- NUEVO: VERSION_ACTUAL y YA_SUBIDO vienen de DESCARGA_WORKBOOKS (LEFT JOIN
-- contra MDM_TABLEAU_GIT_CONTENT). El WHERE excluye las versiones que ya
-- estan subidas y vivas en GitHub (YA_SUBIDO='S') -- asi el CSV que lee
-- Python solo trae lo que de verdad hace falta descargar/subir hoy: las
-- filas de "carpeta intermedia" (YA_SUBIDO IS NULL) se siguen incluyendo
-- igual que antes, para no perder ninguna carpeta.
SELECT WORKBOOK_LUID, WORKBOOK, RUTA_PROYECTO, RUTA_LOCAL_DESTINO, OWNER_EMAIL,
       ULTIMA_ACTUALIZACION, TIPO_ITEM, VERSION_ACTUAL
FROM DESCARGA_WORKBOOKS
WHERE YA_SUBIDO IS NULL OR YA_SUBIDO = 'N';
 
SPOOL OFF
 
SET MARKUP CSV OFF
 
EXIT
 
