@echo off
setlocal EnableExtensions
title Crear SkipKey_Instalador.exe

cd /d "%~dp0"
set "LOG=%~dp0creacion_instalador.log"
set "VERSION=%~1"
if /i "%VERSION%"=="--recompilar" set "VERSION="
if "%VERSION%"=="" set "VERSION=1.0.0"
set "ISCC_ARG=%~2"
REM `--recompilar` fuerza a rehacer ClienteVoz.exe aunque ya exista: si no,
REM el instalador empaquetaria el cliente viejo, sin los cambios del dia.
set "RECOMPILAR=0"
echo %* | findstr /i /c:"--recompilar" >nul && set "RECOMPILAR=1"

REM La versión viaja con el cliente: es lo que responde `ClienteVoz.exe
REM --version` y lo que queda en el registro al arrancar.
> "..\version.txt" echo %VERSION%

REM El registro guarda los pasos y el mensaje de ISCC. La compilacion del
REM cliente NO se redirige: si se oculta su salida, la ventana parece colgada
REM durante los muchos minutos que tarda (visto el 2026-11-19).
> "%LOG%" echo === Creacion del instalador de SkipKey: %DATE% %TIME% ===

echo ============================================================
echo   CREAR EL INSTALADOR DE SKIPKEY  (Windows, un solo .exe)
echo ============================================================
echo.

call :principal
set "CODIGO=%ERRORLEVEL%"

echo.
echo ============================================================
if "%CODIGO%"=="0" echo   LISTO: %~dp0salida\SkipKey_Instalador_%VERSION%.exe
if not "%CODIGO%"=="0" echo   [FALLO] Revisa el mensaje de arriba.
echo   Registro: %LOG%
echo ============================================================
echo.
pause
exit /b %CODIGO%

REM ---------------------------------------------------------------------------
:principal
echo == 1. Cliente compilado ==
if "%RECOMPILAR%"=="1" (
    echo    Pedido con --recompilar: se rehace ClienteVoz.exe.
    goto :compilar
)
if not exist "..\ClienteVoz.exe" goto :compilar
echo    ClienteVoz.exe ya existe.
>>"%LOG%" echo 1. ClienteVoz.exe ya existia.
goto :clave

:compilar
echo.
echo    No hay ClienteVoz.exe: se compila ahora.
echo    La primera vez tarda varios minutos (descarga dependencias).
echo    NO CIERRES ESTA VENTANA.
echo.
>>"%LOG%" echo 1. Compilando el cliente con construir_exe_windows.bat
pushd ".."
call construir_exe_windows.bat <nul
popd
if not exist "..\ClienteVoz.exe" (
    echo [ERROR] No se pudo compilar ClienteVoz.exe. Mira dist\ y el mensaje de PyInstaller.
    >>"%LOG%" echo ERROR: no se genero ClienteVoz.exe
    exit /b 1
)

:clave
echo == 2. Clave del proxy ==
if exist "..\clave_proxy.txt" echo    clave_proxy.txt presente.
if not exist "..\clave_proxy.txt" echo    [AVISO] Sin clave_proxy.txt: el cliente solo servira contra el puente local.

:buscar_iscc
echo == 3. Inno Setup ==
set "ISCC="
if defined ISCC_ARG if exist "%ISCC_ARG%" set "ISCC=%ISCC_ARG%"
for %%p in (
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
) do if not defined ISCC if exist "%%~p" set "ISCC=%%~p"
if defined ISCC goto :iscc_listo
for /f "delims=" %%f in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%f"
if defined ISCC goto :iscc_listo

echo    Buscando ISCC.exe por el disco (unos segundos)...
for %%d in ("%ProgramFiles(x86)%" "%ProgramFiles%" "%LOCALAPPDATA%\Programs") do (
    if exist "%%~d" for /f "delims=" %%f in ('dir /s /b "%%~d\ISCC.exe" 2^>nul') do if not defined ISCC set "ISCC=%%f"
)
if defined ISCC goto :iscc_listo

echo.
echo    [ERROR] No encontre Inno Setup en este equipo.
echo.
echo    Instalalo UNA vez en esta PC (la del usuario final no lo necesita):
echo        https://jrsoftware.org/isdl.php
echo    El cliente ya esta compilado: despues solo hay que volver a
echo    ejecutar este archivo.
echo.
set "RUTA="
set /p "RUTA=Si ya lo instalaste, pega la ruta completa de ISCC.exe (Intro para salir): "
if not defined RUTA (
    >>"%LOG%" echo ERROR: Inno Setup no encontrado y sin ruta indicada.
    exit /b 1
)
if not exist "%RUTA%" (
    echo    [ERROR] No existe esa ruta: %RUTA%
    >>"%LOG%" echo ERROR: ruta de ISCC inexistente: %RUTA%
    exit /b 1
)
set "ISCC=%RUTA%"

:iscc_listo
echo    ISCC: %ISCC%
echo == 4. Generando SkipKey_Instalador_%VERSION%.exe (puede tardar: comprime el cliente) ==
>>"%LOG%" echo 4. Generando SkipKey_Instalador_%VERSION%.exe
"%ISCC%" /DVersion=%VERSION% SkipKey.iss >>"%LOG%" 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la compilacion del instalador. Mensaje de ISCC:
    echo ------------------------------------------------------------
    type "%LOG%"
    echo ------------------------------------------------------------
    exit /b 1
)
echo    LISTO: %~dp0salida\SkipKey_Instalador_%VERSION%.exe
exit /b 0
