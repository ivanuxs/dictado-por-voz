#!/usr/bin/env python3
"""Crea (o actualiza) una cuenta del puente desde la consola.

Se usa para las cuentas que se dan a mano —la tuya de pruebas, por ejemplo—
sin pasar por la app.

Uso:
    set -a; . ~/.config/puente-web.env; set +a       # misma BD que el servicio
    entorno_web/bin/python puente_web/herramientas/crear_usuario.py \\
        ivan --plan pro --generar

Opciones:
    --plan NOMBRE     gratis | basico | pro. Al CREAR, si no se dice, el plan por
                      defecto del servidor. Al ACTUALIZAR solo se toca si se
                      escribe: sin él se respeta el que ya tiene.
    --email X         correo de contacto (opcional: solo para recuperar)
    --generar         inventa una contraseña y la imprime UNA vez
    --contrasena X    la pone a mano (ojo: queda en el historial del shell)
    --actualizar      si la cuenta ya existe, le cambia plan y/o contraseña
    --email-nuevo X   corrige el correo de una cuenta existente
    --renombrar NUEVO cambia el nombre de usuario (conserva perfil y tokens)

La identidad es el NOMBRE DE USUARIO (también se acepta el correo de las
cuentas viejas). Las contraseñas se guardan con scrypt; este script no guarda
nada en claro.
"""

import argparse
import getpass
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import config  # noqa: E402
import planes  # noqa: E402
import usuarios  # noqa: E402


def main():
    analizador = argparse.ArgumentParser(
        description="Crea o actualiza una cuenta del puente de dictado."
    )
    analizador.add_argument("usuario", help="nombre de usuario de la cuenta")
    analizador.add_argument("--email", help="correo de contacto (opcional)")
    # OJO: el valor por defecto es None a propósito. Antes era el plan por
    # defecto del servidor y, como argparse SIEMPRE lo rellena, un
    # `--actualizar` sin `--plan` bajaba la cuenta a «gratis» sin avisar
    # (pasó el 2026-11-18 con una cuenta en «pro»).
    analizador.add_argument(
        "--plan", default=None, help="gratis | basico | pro"
    )
    analizador.add_argument("--generar", action="store_true", help="inventa la contraseña")
    # El alias con ñ existe porque es lo primero que se teclea (y argparse, si
    # no coincide exacto, sale con código 2 sin decir cuál era el bueno).
    analizador.add_argument(
        "--contrasena",
        "--contraseña",
        dest="contrasena",
        help="contraseña a mano (ojo: queda en el historial del shell)",
    )
    analizador.add_argument(
        "--actualizar", action="store_true", help="actualiza si ya existe"
    )
    analizador.add_argument("--email-nuevo", help="corrige el correo de la cuenta")
    analizador.add_argument(
        "--renombrar",
        metavar="NUEVO",
        help="cambia el nombre de usuario (conserva perfil, diccionario y tokens)",
    )
    argumentos = analizador.parse_args()

    if argumentos.plan is not None:
        pedido = argumentos.plan.strip().lower()
        if pedido not in planes.PLANES:
            print(
                f"«{argumentos.plan}» no es un plan. Hay: {', '.join(planes.ORDEN)}",
                file=sys.stderr,
            )
            sys.exit(2)

    usuarios.iniciar()
    nombre = usuarios.normalizar_usuario(argumentos.usuario)
    correo = usuarios.normalizar_email(argumentos.email) or None
    existente = usuarios.buscar_para_entrar(nombre)

    # Renombrar ya es «actualizar»: no hace falta pedir las dos banderas.
    modificar = argumentos.actualizar or bool(argumentos.renombrar)
    if existente is not None and not modificar:
        print(
            f"Ya existe la cuenta {existente['usuario']} (plan {existente['plan']}).\n"
            "Añade --actualizar para cambiarle el plan, la contraseña o el nombre.",
            file=sys.stderr,
        )
        sys.exit(1)

    clave = None
    if argumentos.generar:
        clave = secrets.token_urlsafe(12)
    elif argumentos.contrasena:
        clave = argumentos.contrasena
    elif not existente:
        clave = getpass.getpass("Contraseña de la cuenta: ")
        if clave != getpass.getpass("Repite la contraseña: "):
            print("Las contraseñas no coinciden.", file=sys.stderr)
            sys.exit(2)

    try:
        if existente is None:
            cuenta = usuarios.crear_usuario(
                nombre,
                clave,
                email=correo,
                plan=planes.normalizar(argumentos.plan or config.PLAN_POR_DEFECTO),
                verificado=True,
            )
            print(f"Cuenta creada: {cuenta['usuario']} ({cuenta['plan']})")
            if cuenta["email"]:
                print(f"Correo de contacto: {cuenta['email']}")
        else:
            cuenta = usuarios.actualizar(
                existente["id"],
                usuario=argumentos.renombrar,
                plan=planes.normalizar(argumentos.plan) if argumentos.plan else None,
                clave=clave,
                email=argumentos.email_nuevo or correo,
                verificado=True,
            )
            print(f"Cuenta actualizada: {cuenta['usuario']} ({cuenta['plan']})")
            if clave is None:
                print("Contraseña sin cambios (usa --generar o --contrasena).")
    except usuarios.ErrorUsuario as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if argumentos.generar:
        print()
        print("  Contraseña: " + clave)
        print("  (Apúntala ahora: no se vuelve a mostrar. Para cambiarla, repite")
        print("   este script con --contrasena o --generar y --actualizar.)")
    print(f"  Base de datos: {config.BD}")


if __name__ == "__main__":
    main()
