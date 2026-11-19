"""Autenticación del puente: cabecera `Authorization: Bearer <token>`.

Hay dos formas de entrar, y las dos conviven a propósito:

1. El **token maestro** (`DICTADO_WEB_TOKEN`): el de siempre. Lo usan los
   clientes de escritorio y los scripts del dueño. No tiene plan ni cupos.
   Se mantiene para que nada de lo que ya funcionaba deje de funcionar.
2. Un **token de dispositivo** de una cuenta creada con `/api/v1/registro`.

Si el token no vale, se responde 401 con un mensaje que el teclado puede
enseñar tal cual.
"""

import hmac

from fastapi import HTTPException

import config
import usuarios

# El token maestro no es una cuenta: no tiene correo, plan ni cupos.
USUARIO_MAESTRO = {
    "id": None,
    "email": "",
    "plan": "maestro",
    "verificado": True,
    "activo": True,
    "creado": "",
    "es_maestro": True,
}


def token_de_cabecera(cabecera):
    """Saca el token de la cabecera, o None si no tiene la forma esperada."""
    if not cabecera:
        return None
    partes = cabecera.split(None, 1)
    if len(partes) != 2 or partes[0].lower() != "bearer":
        return None
    return partes[1].strip() or None


def token_valido(cabecera):
    """¿Es el token maestro? (Se conserva por compatibilidad.)"""
    token = token_de_cabecera(cabecera)
    if not token or not config.TOKEN_WEB:
        return False
    return hmac.compare_digest(token, config.TOKEN_WEB)


def usuario_de_cabecera(cabecera):
    """Cuenta que hay detrás de la cabecera, o None.

    Se compara el token maestro con `hmac.compare_digest` para que el tiempo de
    respuesta no revele cuántos caracteres se acertaron.
    """
    token = token_de_cabecera(cabecera)
    if token is None:
        return None
    if config.TOKEN_WEB and hmac.compare_digest(token, config.TOKEN_WEB):
        return dict(USUARIO_MAESTRO)
    return usuarios.usuario_por_token(token)


def verificar_cabecera(cabecera):
    """Devuelve la cuenta; si no hay, lanza 401."""
    usuario = usuario_de_cabecera(cabecera)
    if usuario is None:
        raise HTTPException(
            status_code=401,
            detail="Token inválido o sesión caducada. Vuelve a iniciar sesión.",
        )
    return usuario


async def verificar_peticion(request):
    """Comprueba el token de una petición HTTP normal y devuelve la cuenta."""
    return verificar_cabecera(request.headers.get("authorization"))

