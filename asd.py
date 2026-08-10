:: MarcarSubidoGitHub.bat
::
:: Lo llama Python (marcar_subido_en_oracle) DESPUES de confirmar que un
:: push a GitHub tuvo exito, para poner FLG_SUBIDO_GITHUB=1 en las
:: versiones incluidas en ese push.
::
:: A diferencia de ConexionOracle.bat y ActualizarGitContent.bat (que
:: siempre ejecutan el MISMO .sql fijo), este recibe como primer argumento
:: (%1) la ruta al .sql que Python genera en cada llamada, con el UPDATE
:: concreto de esa tanda de versiones.
::
:: Mismas credenciales de Oracle que ConexionOracle.bat -- no es un acceso
:: nuevo.

chcp 65001 > nul
set NLS_LANG=AMERICAN_AMERICA.AL32UTF8
cd C:\oracle\instantclient_23_0

SQLPLUS CLABS_STG_PRO/CantabriaSTG2021Pro@golddb_high @%1
