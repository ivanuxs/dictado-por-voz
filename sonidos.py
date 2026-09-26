"""Generación y reproducción de sonidos (alarma, fin de dictado).

Genera los archivos .wav en tiempo de ejecución para no depender de
archivos externos. Esto permite que funcione también dentro del
ejecutable ClienteVoz.exe de un solo archivo (PyInstaller onefile).
"""
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

FRECUENCIA_MUESTREO = 44100

_carpeta_temporal = None


def _tono(frecuencia, duracion, amplitud=0.5):
    """Genera un tono con envolvente suave (evita 'clicks' al reproducir)."""
    n = int(FRECUENCIA_MUESTREO * duracion)
    ataque = max(1, int(0.005 * FRECUENCIA_MUESTREO))
    caida = max(1, int(0.03 * FRECUENCIA_MUESTREO))
    muestras = bytearray()
    for i in range(n):
        t = i / FRECUENCIA_MUESTREO
        env = 1.0
        if i < ataque:
            env = i / ataque
        if n - i < caida:
            env = min(env, (n - i) / caida)
        valor = int(amplitud * env * 32767 * math.sin(2 * math.pi * frecuencia * t))
        muestras += struct.pack("<h", valor)
    return bytes(muestras)


def _silencio(duracion):
    n = int(FRECUENCIA_MUESTREO * duracion)
    return b"\x00\x00" * n


def _alarma():
    """Patrón de alarma: tres bips más un tono final más grave."""
    return (
        _tono(880, 0.18) + _silencio(0.06) +
        _tono(880, 0.18) + _silencio(0.06) +
        _tono(880, 0.18) + _silencio(0.06) +
        _tono(1174.66, 0.30) +
        _silencio(0.05)
    )


def _listo():
    """Ding ascendente (fin de dictado)."""
    return _tono(1318.51, 0.12) + _silencio(0.03) + _tono(1760.0, 0.28)


_GENERADORES = {"alarma": _alarma, "listo": _listo}


def _obtener_wav(nombre):
    """Devuelve la ruta de un .wav generado (lo crea la primera vez)."""
    global _carpeta_temporal
    if _carpeta_temporal is None:
        _carpeta_temporal = tempfile.mkdtemp(prefix="dictado_voz_")
    ruta = os.path.join(_carpeta_temporal, f"{nombre}.wav")
    if not os.path.exists(ruta):
        datos = _GENERADORES[nombre]()
        with wave.open(ruta, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FRECUENCIA_MUESTREO)
            w.writeframes(datos)
    return ruta


def reproducir_sonido(nombre):
    """Reproduce un sonido ('alarma' o 'listo').

    Windows: winsound. Linux: paplay.
    """
    if nombre not in _GENERADORES:
        return
    try:
        ruta = _obtener_wav(nombre)
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(ruta, winsound.SND_FILENAME)
        else:
            subprocess.run(
                ["paplay", ruta],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
