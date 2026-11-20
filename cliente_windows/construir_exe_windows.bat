@echo off
setlocal
chcp 65001 >nul
title Construir ClienteVoz.exe

cd /d "%~dp0"

echo ============================================================
echo   CONSTRUIR PRODUCTO FINAL: ClienteVoz.exe
echo   (un unico archivo con Python + dependencias + codigo)
echo ============================================================
echo.

REM ------- Buscar Python 3 -------
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No se encontro Python 3.
    echo Instalalo desde https://www.python.org/downloads/
    echo y marca "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

echo [1/5] Creando entorno virtual...
%PY% -m venv entorno_dictado_win
if errorlevel 1 (
    echo [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)

echo [2/5] Instalando dependencias (la primera vez puede tardar)...
call "entorno_dictado_win\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requisitos_windows.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    echo Revisa tu conexion a internet.
    pause
    exit /b 1
)

echo [3/5] Incrustando la clave del proxy en el ejecutable...
REM Se hace SOLO, sin pedir nada: lee clave_proxy.txt (junto a este archivo),
REM o la variable DICTADO_PUENTE_CLAVE, o "puente_clave" del JSON. El usuario
REM final del cliente nunca ve ni escribe la clave (igual que en el APK).
python preparar_clave.py
if errorlevel 1 (
    echo [ERROR] No se pudo preparar la clave del proxy.
    pause
    exit /b 1
)

echo [4/5] Compilando con PyInstaller...
pyinstaller --clean --noconfirm cliente_voz.spec
if errorlevel 1 (
    echo [ERROR] Fallo la compilacion. Revisa el mensaje anterior.
    pause
    exit /b 1
)

echo [5/5] Copiando el producto final...
copy /y "dist\ClienteVoz.exe" "ClienteVoz.exe" >nul

echo.
echo ============================================================
echo   LISTO! Producto final generado:
echo     %~dp0ClienteVoz.exe
echo.
echo   Para instalar en cualquier PC Windows:
echo     1. Copia ClienteVoz.exe a esa PC.
echo     2. Ejecuta:  ClienteVoz.exe --instalar
echo     3. Quedara registrado para iniciarse al iniciar sesion,
echo        conectado al servidor indicado en comandos_windows.json.
echo ============================================================
echo.
pause
