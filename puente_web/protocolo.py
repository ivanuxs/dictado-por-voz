"""Protocolo de mensajes ZMQ multipart para cliente-servidor de voz.

Formato: cada mensaje son 2 frames ZMQ:
  Frame 0: tipo (bytes)
  Frame 1: payload (bytes)

Tipos soportados (cliente → servidor):
  AUDIO         — chunk de audio int16 16kHz mono
  DICTADO_START — iniciar buffer para Whisper. El payload es una lista de
                  banderas separadas por comas: `vivo` (texto por frases, con
                  modelo opcional tras «:») y `voz` (el TEXTO sale marcado).
  DICTADO_STOP  — transcribir buffer con Whisper
  PING          — keepalive

Tipos soportados (servidor → cliente):
  TEXTO          — texto transcrito (Whisper)
  TEXTO_PARCIAL  — frase transcrita del dictado en vivo (escribir mientras habla)
  FIN_DICTADO    — fin del dictado en vivo (ya no llegan más frases)
  PONG           — respuesta keepalive
  ESTADO         — mensaje de estado/error
  AUTH_OK        — autenticación exitosa
  AUTH_FAIL      — autenticación rechazada
"""

import numpy as np

# Chunk de audio: 1024 muestras a 16kHz = 64 ms
CHUNK_MUESTRAS = 1024

# Banderas del payload de DICTADO_START (separadas por comas; cada una admite
# un modelo tras «:», p. ej. b"voz,vivo:medium").
#   vivo — dictado en vivo: el servidor devuelve el texto por frases
#   voz  — el TEXTO vuelve con marcas (§ ¬ ¶) para que el cliente ejecute la
#          acción. Lo pide el teclado del celular cuando trae intérprete.
MODO_VIVO = b"vivo"
MODO_VOZ = b"voz"

# Tipos de mensaje (como bytes para eficiencia)
AUDIO = b"AUDIO"
DICTADO_START = b"DICTADO_START"
DICTADO_STOP = b"DICTADO_STOP"
TEXTO = b"TEXTO"
TEXTO_PARCIAL = b"TEXTO_PARCIAL"
FIN_DICTADO = b"FIN_DICTADO"
PING = b"PING"
PONG = b"PONG"
ESTADO = b"ESTADO"
AUTH = b"AUTH"
AUTH_OK = b"AUTH_OK"
AUTH_FAIL = b"AUTH_FAIL"


def crear_audio(chunk):
    """Convierte un numpy array int16 a mensaje ZMQ [AUDIO, bytes].

    Args:
        chunk: numpy array int16 de shape (CHUNK_MUESTRAS,)

    Returns:
        list: [b"AUDIO", bytes]
    """
    return [AUDIO, chunk.tobytes()]


def crear_dictado_start():
    return [DICTADO_START, b""]


def crear_dictado_start_vivo():
    """Inicia un dictado en vivo: el servidor devuelve el texto por frases."""
    return [DICTADO_START, MODO_VIVO]


def crear_dictado_stop():
    return [DICTADO_STOP, b""]


def crear_ping():
    return [PING, b""]


def crear_texto(texto):
    return [TEXTO, texto.encode("utf-8")]


def crear_texto_parcial(texto):
    """Frase transcrita de un dictado en vivo (se escribe al vuelo)."""
    return [TEXTO_PARCIAL, texto.encode("utf-8")]


def crear_fin_dictado():
    """Aviso de que el dictado en vivo terminó (no vendrán más frases)."""
    return [FIN_DICTADO, b""]


def crear_pong():
    return [PONG, b""]


def crear_estado(mensaje):
    return [ESTADO, mensaje.encode("utf-8")]


def crear_auth_ok():
    return [AUTH_OK, b""]


def crear_auth_fail(motivo=""):
    return [AUTH_FAIL, motivo.encode("utf-8")]


def crear_auth(token):
    """Crea mensaje de autenticación [AUTH, token]."""
    return [AUTH, token.encode("utf-8")]


def decodificar_audio(frames):
    """Decodifica payload de audio a numpy array int16.

    Args:
        frames: lista de frames ZMQ (se usa frames[1])

    Returns:
        numpy array int16
    """
    return np.frombuffer(frames[1], dtype=np.int16).copy()


def decodificar_texto(frames):
    """Decodifica payload de texto UTF-8.

    Args:
        frames: lista de frames ZMQ (se usa frames[1])

    Returns:
        str
    """
    return frames[1].decode("utf-8")
