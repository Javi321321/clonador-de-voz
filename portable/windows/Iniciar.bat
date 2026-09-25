@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title clonavoz portable
set "ORIGEN=es"
set "DESTINO=en"
if exist "datos\idiomas.txt" for /f "usebackq tokens=1,2" %%a in ("datos\idiomas.txt") do (set "ORIGEN=%%a" & set "DESTINO=%%b")

:menu
cls
echo ==============================================================
echo    clonavoz portable - traductor de voz con tu propia voz
echo ==============================================================
echo    Hablas en: %ORIGEN%    Te escuchan en: %DESTINO%
if not exist "datos\mi_voz.wav" echo    Todavia no grabaste tu voz: usa la opcion 2.
if not exist "datos\modelos_listos.txt" echo    Faltan los modelos: usa la opcion 6 (una sola vez, con internet).
echo.
echo    1. Traducir en una videollamada (Zoom, Meet, Teams, Discord...)
echo    2. Grabar mi muestra de voz
echo    3. Probar mi microfono y el microfono virtual
echo    4. Traducir y escucharlo yo en auriculares (sin videollamada)
echo    5. Cambiar idiomas
echo    6. Descargar modelos para usar sin internet
echo    7. Instalar el microfono virtual VB-CABLE en esta PC
echo    8. Ver dispositivos de audio
echo    9. Apps que no dejan elegir microfono: poner CABLE Output como predeterminado
echo    0. Salir
echo.
set "OPCION="
set /p "OPCION=Elegi una opcion y presiona Enter: "
if "%OPCION%"=="1" goto llamada
if "%OPCION%"=="2" goto grabar
if "%OPCION%"=="3" goto probar
if "%OPCION%"=="4" goto auriculares
if "%OPCION%"=="5" goto idiomas
if "%OPCION%"=="6" goto descargar
if "%OPCION%"=="7" goto vbcable
if "%OPCION%"=="8" goto dispositivos
if "%OPCION%"=="9" goto predeterminado
if "%OPCION%"=="0" exit /b 0
goto menu

:llamada
if not exist "datos\mi_voz.wav" goto falta_voz
echo.
echo En la videollamada elegi como microfono: CABLE Output
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat run --source-lang %ORIGEN% --target-lang %DESTINO%
pause
goto menu

:auriculares
if not exist "datos\mi_voz.wav" goto falta_voz
echo.
echo Usa auriculares: si suena por parlantes, el microfono lo vuelve a escuchar.
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat run --source-lang %ORIGEN% --target-lang %DESTINO% --to-speakers
pause
goto menu

:falta_voz
echo.
echo Primero graba tu muestra de voz con la opcion 2.
pause
goto menu

:grabar
echo.
echo Vas a grabar 15 segundos de tu voz: habla con normalidad, sin ruido de fondo.
echo Queda guardada en la carpeta "datos" de este pendrive o carpeta, no en esta PC.
pause
call clonavoz.bat enroll --seconds 15
pause
goto menu

:probar
call clonavoz.bat test-audio
pause
goto menu

:idiomas
call clonavoz.bat languages
echo.
set /p "ORIGEN=Idioma en el que hablas, ej. es: "
set /p "DESTINO=Idioma en el que te van a escuchar, ej. en: "
>"datos\idiomas.txt" echo %ORIGEN% %DESTINO%
echo Si es un idioma nuevo, usa la opcion 6 para bajar su voz mientras tengas internet.
pause
goto menu

:descargar
call clonavoz.bat download-models --languages %ORIGEN% %DESTINO%
pause
goto menu

:vbcable
echo.
echo VB-CABLE es el microfono virtual gratuito por donde sale tu voz traducida.
echo Se instala una vez por PC y hace falta ser administrador:
echo   1. Se va a abrir la pagina oficial: descarga el ZIP del driver.
echo   2. Extraelo y ejecuta VBCABLE_Setup_x64.exe como administrador.
echo   3. Reinicia la PC.
echo Si no podes instalarlo en esta PC, usa la opcion 4 con auriculares.
start "" "https://vb-audio.com/Cable/"
pause
goto menu

:dispositivos
call clonavoz.bat devices
pause
goto menu

:predeterminado
echo.
echo Algunas apps y paginas web no te dejan elegir el microfono: usan el
echo predeterminado de Windows. Para que escuchen tu voz traducida:
echo   1. En la ventana que se abre (pestana Grabar), clic en "CABLE Output".
echo   2. Boton "Predeterminar" y Aceptar.
echo clonavoz igual sigue escuchando tu microfono real (lo busca solo).
echo Para volver atras, predetermina tu microfono de siempre.
start "" control mmsys.cpl,,1
pause
goto menu
