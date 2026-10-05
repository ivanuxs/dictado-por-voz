"""Ingesta de audio: PCM crudo int16 o WAV → PCM int16 mono a 16 kHz.

El puente no interpreta el audio: solo lo deja con la forma exacta que espera
el servidor de dictado (16 kHz, mono, int16).
"""
import io
import wave

import numpy as np

from protocolo import CHUNK_MUESTRAS

FRECUENCIA = 16000
BYTES_MUESTRA = 2
BYTES_TROZO = CHUNK_MUESTRAS * BYTES_MUESTRA  # 2048 bytes = 64 ms


class AudioInvalido(Exception):
    """El audio recibido no se puede usar."""


def normalizar(datos):
    """Devuelve PCM int16 mono a 16 kHz.

    Acepta un WAV (se reconoce por la cabecera RIFF) o PCM crudo, que es lo que
    entrega AudioRecord en Android.
    """
    if datos[:4] == b"RIFF":
        pcm, frecuencia, canales = _leer_wav(datos)
    else:
        pcm, frecuencia, canales = datos, FRECUENCIA, 1

    if not pcm:
        raise AudioInvalido("No llegó audio (el cuerpo está vacío).")
    if len(pcm) % BYTES_MUESTRA:
        pcm = pcm[:-1]  # medio byte suelto: se descarta
    if canales > 1:
        pcm = _a_mono(pcm, canales)
    if frecuencia != FRECUENCIA:
        pcm = _remuestrear(pcm, frecuencia)
    return pcm


def _leer_wav(datos):
    """Extrae el PCM de un WAV en memoria."""
    try:
        with wave.open(io.BytesIO(datos), "rb") as wav:
            canales = wav.getnchannels()
            ancho = wav.getsampwidth()
            frecuencia = wav.getframerate()
            pcm = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as e:
        raise AudioInvalido(f"El WAV no se pudo leer: {e}") from e

    if ancho != BYTES_MUESTRA:
        raise AudioInvalido(
            f"El WAV debe ser PCM de 16 bits (llegó de {ancho * 8} bits)."
        )
    return pcm, frecuencia, canales


def _a_mono(pcm, canales):
    """Promedia los canales (los WAV de móvil suelen venir estéreo)."""
    muestras = np.frombuffer(pcm, dtype="<i2")
    utiles = len(muestras) - (len(muestras) % canales)
    if not utiles:
        raise AudioInvalido("El audio estéreo llegó incompleto.")
    promedio = muestras[:utiles].reshape(-1, canales).mean(axis=1)
    return promedio.astype("<i2").tobytes()


def _remuestrear(pcm, frecuencia_origen):
    """Remuestreo lineal: suficiente para voz y sin depender de scipy."""
    if frecuencia_origen <= 0:
        raise AudioInvalido("El WAV declara una frecuencia de muestreo inválida.")
    muestras = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    cantidad = int(round(len(muestras) * FRECUENCIA / frecuencia_origen))
    if cantidad <= 0:
        raise AudioInvalido("El audio es demasiado corto para remuestrearlo.")
    origen = np.arange(len(muestras), dtype=np.float64)
    destino = np.linspace(0.0, len(muestras) - 1.0, num=cantidad)
    return np.interp(destino, origen, muestras).astype("<i2").tobytes()


def estadisticas(pcm):
    """Duración, RMS y pico del audio (los mismos números que registra el servidor)."""
    muestras = np.frombuffer(pcm, dtype="<i2")
    if not muestras.size:
        return {"duracion_s": 0.0, "rms": 0.0, "pico": 0}
    return {
        "duracion_s": len(muestras) / FRECUENCIA,
        "rms": float(np.sqrt(np.mean(muestras.astype(np.float32) ** 2))),
        "pico": int(np.abs(muestras.astype(np.int32)).max()),
    }


def trocear(pcm):
    """Genera trozos de 64 ms (1024 muestras), como espera el protocolo ZMQ."""
    for inicio in range(0, len(pcm), BYTES_TROZO):
        yield pcm[inicio:inicio + BYTES_TROZO]


def validar(pcm, max_segundos):
    """Rechaza audios más largos que el tope (protege la GPU). Devuelve las medidas."""
    medidas = estadisticas(pcm)
    if medidas["duracion_s"] > max_segundos:
        raise AudioInvalido(
            f"El audio dura {medidas['duracion_s']:.1f} s y el tope es "
            f"{max_segundos} s."
        )
    return medidas
