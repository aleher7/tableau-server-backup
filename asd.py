:: EjecutarProcesoCompleto.bat
::
:: Encadena en SERIE los dos procesos de Oracle que antes se lanzaban por
:: separado. "call" ejecuta cada .bat y ESPERA a que termine antes de
:: continuar con el siguiente.
::
:: Orden:
::   1. ActualizarGitContent.bat  -> versiona y aplica retencion en Oracle,
::      genera lista_workbooks_eliminar.csv
::   2. ConexionOracle.bat        -> genera lista_workbooks.csv, ya con el
::      filtro de "YA_SUBIDO" aplicado (depende de que el paso 1 haya
::      terminado, porque usa el resultado de MDM_TABLEAU_GIT_CONTENT)
::
:: Si el paso 1 falla, NO se continua con el paso 2: generar el CSV de
:: descarga con el versionado a medias podria dar resultados inconsistentes.
:: %ERRORLEVEL% recoge el codigo de salida de SQL*Plus -- distinto de 0
:: significa que el WHENEVER SQLERROR/OSERROR de Actualizar_MDM_TABLEAU_
:: GIT_CONTENT.sql se disparo. Ese mismo codigo se propaga con "exit /b",
:: y es el que Python ya sabe interpretar como fallo (ver ejecutar_sqlplus).
::
:: Los dos .bat originales se mantienen intactos y siguen pudiendo lanzarse
:: sueltos a mano, para probar cada paso por separado si hace falta.

call "C:\tabcmd\TableauGitHub\ActualizarGitContent.bat"
if %ERRORLEVEL% NEQ 0 (
    echo ActualizarGitContent.bat fallo con codigo %ERRORLEVEL% -- no se ejecuta ConexionOracle.bat
    exit /b %ERRORLEVEL%
)

call "C:\tabcmd\TableauGitHub\ConexionOracle.bat"
exit /b %ERRORLEVEL%
