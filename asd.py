:: Lanza el subproceso de versionado (Actualizar_MDM_TABLEAU_GIT_CONTENT.sql)
:: Analogo a ConexionOracle.bat -- usa las MISMAS credenciales de Oracle,
:: no es un acceso nuevo. Se ejecuta ANTES de ConexionOracle.bat cada noche.
chcp 65001 > nul
set NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd C:\oracle\instantclient_23_0
SQLPLUS CLABS_STG_PRO/CantabriaSTG2021Pro@golddb_high @"C:\tabcmd\TableauGitHub\Actualizar_MDM_TABLEAU_GIT_CONTENT.sql"
