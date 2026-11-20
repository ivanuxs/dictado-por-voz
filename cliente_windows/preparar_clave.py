"""Genera `clave_proxy_compilada.py` con la clave del proxy (uso interno).

Lo llama `construir_exe_windows.bat`; el cliente **no** lo ejecuta nunca.

La clave del proxy (`X-Clave-Cliente`, la que exige nginx) es un secreto del
despliegue, no del usuario: se incrusta en el `.exe` igual que en el APK, así
que quien usa el cliente solo escribe usuario y contraseña (2026-11-19).

Orden de búsqueda, sin pedirle nada a nadie:

1. `clave_proxy.txt` junto a este archivo (lo que viaja con la carpeta).
2. La variable de entorno `DICTADO_PUENTE_CLAVE` (para compilar sin el archivo).
3. `"puente_clave"` de `comandos_windows.json`, si alguien la puso a mano.

El archivo generado está excluido de Git: la clave no se versiona nunca.
"""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
ARCHIVO = os.path.join(AQUI, "clave_proxy.txt")
JSON = os.path.join(AQUI, "comandos_windows.json")
DESTINO = os.path.join(AQUI, "clave_proxy_compilada.py")


def _del_archivo():
    try:
        with open(ARCHIVO, encoding="utf-8") as archivo:
            return archivo.readline().strip()
    except OSError:
        return ""


def _del_json():
    try:
        with open(JSON, encoding="utf-8") as archivo:
            return str(json.load(archivo).get("puente_clave") or "").strip()
    except (OSError, ValueError):
        return ""


def main():
    clave = (
        _del_archivo()
        or (os.environ.get("DICTADO_PUENTE_CLAVE") or "").strip()
        or _del_json()
    )
    with open(DESTINO, "w", encoding="utf-8") as archivo:
        archivo.write(f'CLAVE = "{clave}"\n')
    if clave:
        print(f"      clave del proxy incrustada ({len(clave)} caracteres)")
    else:
        print("      [AVISO] sin clave del proxy: el .exe solo servira contra")
        print("              el puente local (http://127.0.0.1:8000).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
