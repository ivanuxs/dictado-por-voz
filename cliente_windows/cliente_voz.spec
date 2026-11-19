# -*- mode: python ; coding: utf-8 -*-
"""Spec de PyInstaller para construir ClienteVoz.exe (Windows, un solo archivo).

Uso (en una PC Windows):
    pyinstaller --clean --noconfirm cliente_voz.spec
Salida: dist\\ClienteVoz.exe
"""

from PyInstaller.utils.hooks import collect_all

# --- Paquetes con datos/binarios/hidden-imports especiales (Windows) ---
# `faster_whisper` trae ctranslate2 (DLL nativas), tokenizers y av: hay que
# recolectarlos o el .exe arranca y falla al cargar el modelo de la escucha.
_datos_extra, _binarios_extra, _ocultos_extra = [], [], []
for _paquete in (
    "pycaw", "comtypes", "pystray", "PIL", "cffi",
    "faster_whisper", "ctranslate2", "tokenizers", "av", "huggingface_hub",
):
    try:
        _d, _b, _h = collect_all(_paquete)
        _datos_extra += _d
        _binarios_extra += _b
        _ocultos_extra += _h
    except Exception:
        pass

a = Analysis(
    ["cliente_voz.py"],
    pathex=[SPECPATH],
    binaries=_binarios_extra,
    datas=[
        ("comandos_windows.json", "."),
    ] + _datos_extra,
    hiddenimports=[
        "pyautogui",
        "pyperclip",
        "sounddevice",
        "numpy",
        "zmq",
        "pynput",
        "edge_tts",
        "requests",
        "pygame",
        "indicador_grabacion",
        "protocolo",
        "marcas",
        "skills",
        "ventana_configuracion",
        "bandeja",
        "escucha_local",
        # Cuenta y perfil (2026-11-18). Se importan desde cliente_voz.py y desde
        # ventana_configuracion.py; van aquí para que el .exe no arranque sin
        # ellos si algún día el import pasa a ser dinámico.
        "perfil",
        "sesion",
        "diccionario_desktop",
        # Vosk importa estos en su __init__, pero PyInstaller solo analiza
        # «vosk»: sin ellos el exe arranca y falla con
        # «No module named 'srt'» al cargar el modelo.
        "srt",
        "tqdm",
        "websockets",
        "_cffi_backend",
        "utils_logging",
        "sonidos",
        "voz_asistente",
    ] + _ocultos_extra,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Modelos y librerías del SERVIDOR: no hacen falta en el cliente y pesan
    # cientos de MB. `vosk` NO va aquí: el cliente lo usa para la palabra clave
    # (excluirlo dejaba el exe sin sus dependencias y fallaba con
    # «No module named 'srt'» al cargar el modelo).
    excludes=["whisper", "torch", "scipy", "openai", "tensorflow"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ClienteVoz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # sin ventana de consola (equivale a pythonw.exe)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
