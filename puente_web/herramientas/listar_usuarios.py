#!/usr/bin/env python3
"""Lista las cuentas del puente con su plan y lo que han usado hoy.

    set -a; . ~/.config/puente-web.env; set +a
    entorno_web/bin/python puente_web/herramientas/listar_usuarios.py
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import config  # noqa: E402
import planes  # noqa: E402
import usuarios  # noqa: E402


def main():
    usuarios.iniciar()
    cuentas = usuarios.listar()
    if not cuentas:
        print(f"No hay cuentas todavía. Base de datos: {config.BD}")
        return

    print(f"{'usuario':24} {'correo':30} {'plan':8} {'verif':6} {'min hoy':>8} {'trad hoy':>9}")
    print("-" * 92)
    for cuenta in cuentas:
        uso = usuarios.uso_hoy(cuenta["id"])
        minutos = float(uso["segundos"]) / 60.0
        tope = planes.cupos(cuenta)["minutos_dia"]
        print(
            f"{cuenta['usuario'][:23]:24} {(cuenta['email'] or '—')[:29]:30} "
            f"{cuenta['plan']:8} "
            f"{'sí' if cuenta['verificado'] else 'no':6} "
            f"{minutos:7.1f}/{tope if tope is not None else '∞':<3} "
            f"{uso['traducciones']:>9}"
        )
    print(f"\n{len(cuentas)} cuenta(s). Base de datos: {config.BD}")


if __name__ == "__main__":
    main()
