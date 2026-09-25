@echo off
rem Ejecuta clonavoz guardando todo (tu voz, modelos, caches) dentro de esta
rem carpeta, no en la computadora donde se usa. Ejemplo: clonavoz.bat devices
setlocal
set "CLONAVOZ_DIR=%~dp0"
set "CLONAVOZ_HOME=%CLONAVOZ_DIR%datos"
set "HF_HOME=%CLONAVOZ_DIR%datos\modelos"
set "TORCH_HOME=%CLONAVOZ_DIR%datos\torch"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
rem Con los modelos ya descargados no se usa internet para nada (salvo para
rem bajar la voz de un idioma nuevo).
if /i not "%~1"=="download-models" if exist "%CLONAVOZ_HOME%\modelos_listos.txt" set "HF_HUB_OFFLINE=1"
"%CLONAVOZ_DIR%python\python.exe" -m clonavoz %*
exit /b %ERRORLEVEL%
