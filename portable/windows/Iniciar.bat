@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title clonavoz portable
rem Lo que elegiste queda guardado en la carpeta "datos" (en el pendrive).
set "ORIGEN=es"
set "DESTINO=auto"
set "ESCUCHO="
set "SUS_IDIOMAS=en pt"
set "VOZ=auto"
set "SU_VOZ=parecida"
set "VACIOS=0"
if exist "datos\idiomas.txt" for /f "usebackq tokens=1,2" %%a in ("datos\idiomas.txt") do (set "ORIGEN=%%a" & set "DESTINO=%%b")
if exist "datos\escucho.txt" for /f "usebackq tokens=1" %%a in ("datos\escucho.txt") do set "ESCUCHO=%%a"
if exist "datos\sus_idiomas.txt" for /f "usebackq delims=" %%a in ("datos\sus_idiomas.txt") do set "SUS_IDIOMAS=%%a"
if exist "datos\voz.txt" for /f "usebackq tokens=1" %%a in ("datos\voz.txt") do set "VOZ=%%a"
if exist "datos\su_voz.txt" for /f "usebackq tokens=1" %%a in ("datos\su_voz.txt") do set "SU_VOZ=%%a"
if not defined ESCUCHO set "ESCUCHO=%ORIGEN%"
if not defined DESTINO set "DESTINO=auto"

rem La primera vez, todo solo: se bajan los modelos y se graba tu voz.
if not exist "datos\modelos_listos.txt" if not defined CLONAVOZ_SIN_DESCARGA goto primera_vez
if not exist "datos\mi_voz.wav" if not defined CLONAVOZ_SIN_DESCARGA goto primera_voz

:menu
call :calcular
cls
echo ==============================================================
echo    clonavoz portable - traductor de voz con tu propia voz
echo ==============================================================
echo    Hablas en: %ORIGEN%   Te escuchan en: %DESTINO_TEXTO%
echo    Te pueden hablar en: %SUS_IDIOMAS% (o cualquier otro)   Lo escuchas en: %ESCUCHO%
echo    Tu voz: %VOZ%   La voz de los demas: %SU_VOZ%
if not exist "datos\mi_voz.wav" echo    Todavia no grabaste tu voz: usa la opcion 5.
if not exist "datos\modelos_listos.txt" echo    Faltan los modelos: usa la opcion 10 (una sola vez, con internet).
echo.
echo    1. Conversacion en videollamada: vos y ellos, traducidos (automatico)
echo    2. Solo escuchar traducido lo que dicen (llamadas, videos, reuniones)
echo    3. Solo traducir mi voz en la videollamada
echo    4. Traducir mi voz y escucharla yo (sin videollamada)
echo    5. Grabar mi muestra de voz
echo    6. Probar mi microfono y el microfono virtual
echo    7. Cambiar idiomas
echo    8. Elegir la voz de los demas (parecida, clonada con permiso, o solo texto)
echo    9. Elegir mi voz (automatica, siempre clonada, o rapida)
echo   10. Descargar o actualizar los modelos (con internet)
echo   11. Instalar el microfono virtual VB-CABLE en esta PC
echo   12. Ver dispositivos de audio
echo   13. Apps que no dejan elegir microfono: poner CABLE Output como predeterminado
echo    0. Salir
echo.
set "OPCION="
set /p "OPCION=Elegi una opcion y presiona Enter: "
if not defined OPCION (set /a VACIOS+=1) else (set "VACIOS=0")
if %VACIOS% GEQ 5 exit /b 0
if "%OPCION%"=="1" goto conversar
if "%OPCION%"=="2" goto escuchar
if "%OPCION%"=="3" goto llamada
if "%OPCION%"=="4" goto auriculares
if "%OPCION%"=="5" goto grabar
if "%OPCION%"=="6" goto probar
if "%OPCION%"=="7" goto idiomas
if "%OPCION%"=="8" goto su_voz
if "%OPCION%"=="9" goto voz
if "%OPCION%"=="10" goto descargar
if "%OPCION%"=="11" goto vbcable
if "%OPCION%"=="12" goto dispositivos
if "%OPCION%"=="13" goto predeterminado
if "%OPCION%"=="0" exit /b 0
goto menu

:calcular
rem Idioma fijo para traducir solo tu voz (con "auto", el ingles) y los
rem idiomas a descargar.
if /i "%DESTINO%"=="auto" (set "FIJO=en") else (set "FIJO=%DESTINO%")
if /i "%DESTINO%"=="auto" (set "DESTINO_TEXTO=auto - el idioma en que te hablen") else (set "DESTINO_TEXTO=%DESTINO%")
set "DESCARGAR=%ORIGEN% %FIJO% %SUS_IDIOMAS% %ESCUCHO%"
exit /b 0

:permiso
rem Clonar la voz de otra persona, solo con su permiso (se pregunta cada vez).
set "SESION=%SU_VOZ%"
set "PERMISO="
if /i not "%SU_VOZ%"=="clonada" exit /b 0
echo.
echo Elegiste escuchar a los demas con SU voz clonada. Eso requiere su permiso:
echo avisale a la otra persona que su voz se va a clonar para traducirte lo que
echo dice (solo suena en tus auriculares y no se guarda) y pedile que acepte.
set "RESP="
set /p "RESP=La persona con la que vas a hablar te dio permiso? (s/n): "
if /i "%RESP%"=="s" (set "PERMISO=--permiso-clonar") else (set "SESION=parecida")
if /i not "%RESP%"=="s" echo Sin su permiso se usa una voz parecida (hombre o mujer, segun su tono).
exit /b 0

:conversar
if not exist "datos\mi_voz.wav" goto falta_voz
call :permiso
echo.
echo En la videollamada elegi como microfono: CABLE Output. Usa auriculares.
echo Lo que te digan aparece en pantalla y suena traducido en tus auriculares.
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat conversar --source-lang %ORIGEN% --target-lang %DESTINO% --listen-lang %ESCUCHO% --their-langs %SUS_IDIOMAS% --their-voice %SESION% %PERMISO% --voice-engine %VOZ%
pause
goto menu

:escuchar
call :permiso
echo.
echo Lo que suene en esta PC (la llamada, un video) aparece traducido en pantalla y en tus auriculares.
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat escuchar --listen-lang %ESCUCHO% --their-langs %SUS_IDIOMAS% --their-voice %SESION% %PERMISO%
pause
goto menu

:llamada
if not exist "datos\mi_voz.wav" goto falta_voz
echo.
echo En la videollamada elegi como microfono: CABLE Output
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat run --source-lang %ORIGEN% --target-lang %FIJO% --voice-engine %VOZ%
pause
goto menu

:auriculares
if not exist "datos\mi_voz.wav" goto falta_voz
echo.
echo Usa auriculares: si suena por parlantes, el microfono lo vuelve a escuchar.
echo Para terminar, presiona Ctrl+C (y si pregunta si terminar el trabajo por lotes, responde N).
call clonavoz.bat run --source-lang %ORIGEN% --target-lang %FIJO% --to-speakers --voice-engine %VOZ%
pause
goto menu

:falta_voz
echo.
echo Primero graba tu muestra de voz con la opcion 5.
pause
goto menu

:primera_vez
call :calcular
cls
echo ==============================================================
echo    Bienvenido a clonavoz: primera vez en este pendrive
echo ==============================================================
echo  1. Se descargan los modelos: unos 2.5 GB, con internet, una sola vez.
echo  2. Grabas tu voz: 15 segundos.
echo Despues funciona sin internet en cualquier PC con Windows 10 u 11.
echo (Para cancelar, cerra esta ventana.)
echo.
call clonavoz.bat download-models --languages %DESCARGAR%
if errorlevel 1 (
  echo.
  echo No se pudo completar la descarga. Revisa la conexion a internet y volve a
  echo intentarlo con la opcion 10 del menu.
  pause
  goto menu
)
if exist "datos\mi_voz.wav" goto menu

:primera_voz
cls
echo ==============================================================
echo    Ahora tu voz: 15 segundos
echo ==============================================================
echo Habla con normalidad, sin ruido de fondo, como si charlaras con alguien.
echo Queda guardada en la carpeta "datos" de este pendrive, no en esta PC.
pause
call clonavoz.bat enroll --seconds 15
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
echo En que idioma te escuchan: "auto" = el idioma en que te hablen (empieza en ingles),
set /p "DESTINO=o uno fijo, ej. en: "
set /p "SUS_IDIOMAS=Idiomas en que es mas probable que te hablen, ej. en pt: "
set /p "ESCUCHO=Idioma en que queres escuchar lo que te dicen, ej. es: "
>"datos\idiomas.txt" echo %ORIGEN% %DESTINO%
>"datos\sus_idiomas.txt" echo %SUS_IDIOMAS%
>"datos\escucho.txt" echo %ESCUCHO%
echo Si agregaste un idioma nuevo, usa la opcion 10 para bajarlo mientras tengas internet.
pause
goto menu

:su_voz
echo.
echo Con que voz escuchas la traduccion de lo que te dicen:
echo   1. Parecida: de hombre o de mujer segun el tono de quien habla (no es su voz).
echo   2. Su voz clonada: SOLO con permiso de la otra persona (se pregunta cada vez).
echo   3. Ninguna: solo el texto en pantalla (subtitulos).
set "ELEGIDA="
set /p "ELEGIDA=Elegi 1, 2 o 3 y presiona Enter: "
if "%ELEGIDA%"=="1" set "SU_VOZ=parecida"
if "%ELEGIDA%"=="2" set "SU_VOZ=clonada"
if "%ELEGIDA%"=="3" set "SU_VOZ=ninguna"
>"datos\su_voz.txt" echo %SU_VOZ%
echo Voz de los demas: %SU_VOZ%
pause
goto menu

:voz
echo.
echo Que voz usar para tu traduccion:
echo   1. Automatica: tu voz clonada si esta PC llega a generarla en vivo; si no,
echo      la rapida (clonavoz lo mide al arrancar y te avisa).
echo   2. Siempre mi voz clonada, aunque en una PC lenta la traduccion tarde mas.
echo   3. Rapida: una voz de tono parecido al tuyo, sin clonar. La mas rapida.
set "ELEGIDA="
set /p "ELEGIDA=Elegi 1, 2 o 3 y presiona Enter: "
if "%ELEGIDA%"=="1" set "VOZ=auto"
if "%ELEGIDA%"=="2" set "VOZ=natural"
if "%ELEGIDA%"=="3" set "VOZ=rapida"
>"datos\voz.txt" echo %VOZ%
echo Voz elegida: %VOZ%
pause
goto menu

:descargar
call clonavoz.bat download-models --languages %DESCARGAR%
pause
goto menu

:vbcable
echo.
echo VB-CABLE es el microfono virtual gratuito por donde sale tu voz traducida.
echo Se instala una vez por PC y hace falta ser administrador:
echo   1. Se va a abrir la pagina oficial: descarga el ZIP del driver.
echo   2. Extraelo y ejecuta VBCABLE_Setup_x64.exe como administrador.
echo   3. Reinicia la PC.
echo Sin VB-CABLE igual podes usar la opcion 2 (escuchar traducido) y la 4.
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
