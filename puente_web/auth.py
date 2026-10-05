"""Autenticación del puente: cabecera Authorization: Bearer <token>."""
import hmac

from fastapi import HTTPException

import config


def token_valido(cabecera):
    """Compara la cabecera Authorization con el token configurado.

    Se usa hmac.compare_digest para que el tiempo de comparación no revele
    cuántos caracteres del token acertó quien esté probando.
    """
    if not cabecera:
        return False
    partes = cabecera.split(None, 1)
    if len(partes) != 2 or partes[0].lower() != "bearer":
        return False
    return hmac.compare_digest(partes[1].strip(), config.TOKEN_WEB)


def verificar_cabecera(cabecera):
    """Lanza HTTPException si la cabecera no trae el token correcto."""
    if not config.TOKEN_WEB:
        raise HTTPException(
            status_code=503,
            detail="El puente no tiene DICTADO_WEB_TOKEN configurado.",
        )
    if not token_valido(cabecera):
        raise HTTPException(status_code=401, detail="Token de acceso inválido o ausente.")


async def verificar_peticion(request):
    """Comprueba el token de una petición HTTP normal."""
    verificar_cabecera(request.headers.get("authorization"))
