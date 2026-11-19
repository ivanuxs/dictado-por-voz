#!/usr/bin/env python3
"""Cambia el plan de una cuenta (y corta los dispositivos que sobren).

    set -a; . ~/.config/puente-web.env; set +a
    entorno_web/bin/python puente_web/herramientas/cambiar_plan.py ivan pro

Se acepta el nombre de usuario (o el correo de las cuentas viejas). Al bajar
de plan, los dispositivos por encima del límite quedan con la sesión cerrada en
la siguiente petición (no se borra nada de la cuenta).
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import config  # noqa: E402
import planes  # noqa: E402
import usuarios  # noqa: E402


def main():
    analizador = argparse.ArgumentParser(description="Cambia el plan de una cuenta.")
    analizador.add_argument("usuario", help="nombre de usuario (o correo)")
    analizador.add_argument("plan", help="gratis | basico | pro")
    argumentos = analizador.parse_args()

    pedido = argumentos.plan.strip().lower()
    if pedido not in planes.PLANES:
        print(f"Ese plan no existe. Hay: {', '.join(planes.ORDEN)}", file=sys.stderr)
        sys.exit(2)

    usuarios.iniciar()
    fila = usuarios.buscar_para_entrar(argumentos.usuario)
    if fila is None:
        print(f"No hay ninguna cuenta {argumentos.usuario}.", file=sys.stderr)
        sys.exit(1)

    cuenta = usuarios.actualizar(fila["id"], plan=pedido)
    limite = planes.limite_dispositivos(cuenta)
    if limite is not None:
        usuarios.cortar_tokens(cuenta["id"], limite)
    print(f"{cuenta['usuario']}: plan {cuenta['plan']} (dispositivos: {limite or '∞'})")
    print(f"Base de datos: {config.BD}")


if __name__ == "__main__":
    main()
