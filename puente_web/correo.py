"""Envío del código de verificación por correo.

Sirve igual un Gmail con contraseña de aplicación que un SMTP propio: son las
mismas variables (`DICTADO_SMTP_*`), solo cambia el host, el puerto y si el TLS
es `starttls` (587) o `ssl` (465).

`enviar()` es síncrono porque `smtplib` lo es; desde las rutas de FastAPI se
llama con `enviar_async()`, que lo manda a un hilo y no frena el bucle de
eventos mientras el servidor de correo contesta.
"""

import asyncio
import smtplib
from email.message import EmailMessage

import config


def disponible():
    """¿Hay correo configurado para poder verificar cuentas?"""
    return bool(config.SMTP_HOST and config.SMTP_USUARIO and config.SMTP_CLAVE)


def descripcion():
    """Texto corto para los avisos del arranque."""
    if not disponible():
        return "sin configurar (registro sin verificación de correo)"
    return f"{config.SMTP_HOST}:{config.SMTP_PUERTO} ({config.SMTP_TLS})"


def enviar(destino, asunto, cuerpo):
    """Manda un correo de texto. Lanza la excepción de smtplib si falla."""
    mensaje = EmailMessage()
    mensaje["From"] = config.SMTP_DE
    mensaje["To"] = destino
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)

    if config.SMTP_TLS == "ssl":
        servidor = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PUERTO, timeout=20)
    else:
        servidor = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PUERTO, timeout=20)
        if config.SMTP_TLS == "starttls":
            servidor.starttls()

    with servidor:
        servidor.login(config.SMTP_USUARIO, config.SMTP_CLAVE)
        servidor.send_message(mensaje)


async def enviar_async(destino, asunto, cuerpo):
    """Igual que `enviar`, pero fuera del bucle de eventos."""
    await asyncio.to_thread(enviar, destino, asunto, cuerpo)


def texto_codigo(codigo):
    """Cuerpo del correo con el código de verificación."""
    return (
        "Tu código para confirmar la cuenta del teclado de dictado es:\n\n"
        f"    {codigo}\n\n"
        f"Caduca en {config.CODIGO_MINUTOS} minutos.\n\n"
        "Si no has creado ninguna cuenta, ignora este mensaje.\n"
    )
