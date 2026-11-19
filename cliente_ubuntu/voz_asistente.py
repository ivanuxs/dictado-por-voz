"""Asistente de voz: TTS con edge-tts + comandos simples + DeepSeek."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime

import edge_tts

from utils_logging import imprimir

VOZ = "es-MX-DaliaNeural"

# Respuestas predefinidas para comandos simples
COMANDOS_SIMPLES = {
    "que hora es": lambda: f"Son las {datetime.now().strftime('%H:%M')}",
    "que dia es": lambda: f"Hoy es {datetime.now().strftime('%A %d de %B')}",
    "que fecha es": lambda: f"Hoy es {datetime.now().strftime('%d de %B de %Y')}",
    "como estas": lambda: "Estoy bien, gracias por preguntar. ¿En qué puedo ayudarte?",
    "quien eres": lambda: "Soy tu asistente de voz. Estoy aquí para ayudarte con lo que necesites.",
    "gracias": lambda: "De nada, para eso estoy.",
    "buenos dias": lambda: "Buenos días. ¿En qué puedo ayudarte hoy?",
    "buenas tardes": lambda: "Buenas tardes. ¿Qué necesitas?",
    "buenas noches": lambda: "Buenas noches. Que descanses.",
    "hola": lambda: "Hola, ¿en qué puedo ayudarte?",
    "adios": lambda: "Adiós, que tengas un buen día.",
    "hasta luego": lambda: "Hasta luego, para eso estoy.",
    "que puedes hacer": lambda: "Puedo abrir y cerrar aplicaciones, decirte la hora, controlar el volumen, buscar en internet y más. Solo dime computadora y tu comando.",
}


def _buscar_comando_simple(texto):
    """Busca coincidencia parcial con comandos simples. Retorna la respuesta o None."""
    # Normalizar por si acaso (tildes, signos)
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", texto.lower().strip())
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[,.:;!¡¿?]", "", t)
    for clave, respuesta in COMANDOS_SIMPLES.items():
        if clave in t:
            return respuesta()
    return None


def hablar(texto):
    """Habla el texto usando edge-tts (bloquea hasta terminar).

    Devuelve True si se reprodujo. Un fallo **no** se silencia: queda en el
    registro. Antes se tragaba con `except Exception: pass` y el síntoma era el
    peor posible: el cliente decía «Respuesta local a…» en el log y el usuario
    solo veía que no respondía nada (pasó el 2026-11-05: `edge-tts` 7.2.8 daba
    403 al servicio de Microsoft y había que subir a 7.3.1).
    """
    if not texto or not texto.strip():
        return False
    try:
        asyncio.run(_hablar_async(texto))
        return True
    except Exception as e:
        imprimir(f"[TTS] No se pudo hablar: {type(e).__name__}: {e}")
        return False


async def _hablar_async(texto):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp = f.name
    try:
        communicate = edge_tts.Communicate(texto, VOZ)
        await communicate.save(tmp)
        _reproducir_mp3(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


_pygame_listo = False
_tts_bloqueo = threading.Lock()


def _reproducir_mp3(ruta):
    """Reproduce un MP3. Usa ffplay en Linux y pygame en Windows."""
    if sys.platform == "win32":
        _reproducir_mp3_windows(ruta)
        return
    # ffplay: sin ventana, auto-cierre al terminar, sin logs
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", ruta],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _reproducir_mp3_windows(ruta):
    """Reproduce con pygame en Windows (serializa para evitar conflictos)."""
    global _pygame_listo
    try:
        import pygame
        with _tts_bloqueo:
            if not _pygame_listo:
                pygame.mixer.init()
                _pygame_listo = True
            pygame.mixer.music.load(ruta)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(50)
    except Exception:
        pass


def preguntar_deepseek(pregunta):
    """Envía una pregunta a DeepSeek y retorna la respuesta como texto."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    try:
        import urllib.request

        datos = json.dumps(
            {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Eres un asistente de voz amigable que habla español latinoamericano. "
                            "Responde de forma concisa, clara y natural. "
                            "Máximo 2 oraciones a menos que sea indispensable extenderse."
                        ),
                    },
                    {"role": "user", "content": pregunta},
                ],
                "max_tokens": 200,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=datos,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            resultado = json.loads(resp.read().decode("utf-8"))
            return resultado["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
