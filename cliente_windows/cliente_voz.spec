# -*- mode: python ; coding: utf-8 -*-
"""Spec de PyInstaller para construir ClienteVoz.exe (Windows, un solo archivo).

Uso (en una PC Windows):
    pyinstaller --clean --noconfirm cliente_voz.spec
Salida: dist\\ClienteVoz.exe
"""

from PyInstaller.utils.hooks import collect_all

# --- Paquetes con datos/binarios/hidden-imports especiales (Windows) ---
_datos_extra, _binarios_extra, _ocultos_extra = [], [], []
for _paquete in ("pycaw", "comtypes"):
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
        "utils_logging",
        "sonidos",
        "voz_asistente",
    ] + _ocultos_extra,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # El cliente no usa el modelo de voz del servidor: se excluyen para reducir tamaño
    excludes=["vosk", "whisper", "torch", "scipy", "openai", "tensorflow"],
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
