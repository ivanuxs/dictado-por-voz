"""Escucha local de la palabra clave y de las órdenes, con Whisper.

Este módulo sustituye al detector basado en **Vosk** (guardado en
`resguardos/escucha_local_vosk.py`). El motivo es una medición, no una
preferencia: sobre 6 audios reales del usuario y con la misma configuración
(CPU, `int8`, 4 hilos), el WER fue

    Vosk small-es-0.42 ........ 103 %   (además de fallar, inventa palabras)
    faster-whisper ``base`` ....  48 %
    faster-whisper ``small`` ...  30 %

y los fallos de Vosk son justo los que se veían: «iniciar dictado» llegaba como
«iniciar». `small` acierta más pero tarda 1,26 s por frase de 2 s, así que se
eligió `base` (0,74 s, ~400 MB de RAM).

**No abre el micrófono**: igual que antes, recibe los mismos chunks int16 que el
cliente ya captura y manda al servidor. Lo que cambia es lo de dentro:

1. los chunks se **acumulan** en un búfer;
2. se vigila la energía: cuando hay voz y luego `SILENCIO_CIERRA_SEGUNDOS` de
   silencio, la frase está dicha;
3. se transcribe **ese** segmento de una vez con Whisper (no es streaming como
   Vosk: se transcribe al cerrar la frase).

`alimentar()` devuelve `"clave"`, `"fin"`, `"comando"`, `"frase"` o `None`. Qué
se hace con eso lo decide `cliente_voz.py`; aquí no se envía nada al servidor ni
se escribe texto.

⚠️ Este archivo existe por duplicado: `cliente_ubuntu/` y `cliente_windows/`.
Los dos tienen que decir exactamente lo mismo.
"""
import json
import os
import unicodedata

import numpy as np

from marcas import es_comando
from utils_logging import imprimir
from collections import deque

FRECUENCIA = 16000

# Silencio (en segundos) que cierra la frase y dispara la transcripción. Basta
# con medio segundo largo: el usuario dice el comando y se calla.
SILENCIO_CIERRA_SEGUNDOS = 0.6
# Audio que se guarda antes de que empiece la voz. Sin esto el búfer incluía
# los segundos de silencio anteriores y Whisper **alucinaba** con ellos
# («¡adiós!», «0,81»): visto el 2026-11-05.
PRE_ROLL_SEGUNDOS = 0.3
# Tope de una frase: si alguien habla sin parar (o hay ruido constante), se
# transcribe igual cada tantos segundos en vez de acumular sin fin.
MAX_FRASE_SEGUNDOS = 8.0
# Pico int16 para considerar que hay voz. Un segmento de dos o más palabras
# nunca se descarta por energía: un golpe o un ruido no producen dos palabras.
PICO_MINIMO = 900

MODELO_POR_DEFECTO = "base"
HILOS = 4

# Palabras del dominio: se le pasan a Whisper como pista (`hotwords`) para que
# no confunda «dictado» con otra cosa parecida. Con voz real se midió que
# `base` oía «fin del helico» en lugar de «fin del dictado».
HOTWORDS = (
    "computadora, iniciar dictado, fin del dictado, modo dictado, abre, "
    "cierra, sube volumen, baja volumen, silencio, captura pantalla, "
    "bloquear pantalla, busca"
)

# Frases que cierran el dictado. Se pueden cambiar en comandos.json ("frase_fin").
FRASES_FIN_POR_DEFECTO = (
    "fin del dictado",
    "fin de dictado",
    "finalización del dictado",
)

# Piezas opcionales de la frase de cierre, delante de «dictado».
_ARTICULOS_CIERRE = ("del", "de", "el", "la")
_VERBOS_CIERRE = (
    "fin", "final", "finalizacion", "finalizar", "termina", "terminar",
    "termino", "acaba", "acabar", "cierra", "cerrar",
)

_BORDE = " \t,.;:!¡¿?…—-"


def normalizar(texto):
    """Minúsculas, sin tildes y sin puntuación: para comparar lo que se oyó."""
    bajo = (texto or "").strip().lower()
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", bajo)
        if unicodedata.category(c) != "Mn"
    )
    palabras = [
        "".join(c for c in palabra if c.isalnum())
        for palabra in sin_tildes.replace(",", " ").replace(".", " ").split()
    ]
    return " ".join(p for p in palabras if p).strip()


def quitar_frase_cierre(texto):
    """Quita del final la frase de cierre, tolerando cómo la escribió Whisper.

    Desde la última palabra, si empieza por «dicta», se retrocede un artículo
    opcional y **se exige un verbo de cierre**. Así se recortan «fin del
    dictado», «fin de dictado», «fin dictado», «finalización del dictado» y
    «dictado» pelado, y **no** se recortan las órdenes que abren el dictado.

    ⚠️ Antes se recortaba cualquier frase acabada en «dictado», aunque delante
    no hubiera verbo: «iniciar dictado» quedaba en «Iniciar» y se clasificaba
    como **cierre**, así que estando en modo comando se ignoraba en silencio y
    parecía que el detector no oía nada (visto el 2026-11-05). Con «modo
    dictado» pasaba lo mismo.
    """
    partes = (texto or "").split()
    if not partes:
        return texto
    palabras = [normalizar(p) for p in partes]
    if not palabras[-1].startswith("dicta"):
        return texto

    indice = len(partes) - 1
    if indice >= 1 and palabras[indice - 1] in _ARTICULOS_CIERRE:
        indice -= 1
    if indice >= 1 and palabras[indice - 1] in _VERBOS_CIERRE:
        return " ".join(partes[:indice - 1]).rstrip(_BORDE)
    if indice == 0:
        # «dictado» pelado (o «del dictado»): cierra igual.
        return ""
    # Delante hay otra cosa que no es un verbo de cierre: no es un cierre.
    return texto


class EscuchaLocal:
    """Reconoce la palabra clave y las órdenes con Whisper, en local.

    Acumula audio y transcribe la frase cuando el usuario se calla. La interfaz
    es la misma que tenía la versión con Vosk, para no tocar el cliente.
    """

    def __init__(self, clave="computadora", frase_fin=FRASES_FIN_POR_DEFECTO,
                 ruta_modelo="", frecuencia=FRECUENCIA,
                 pico_minimo=PICO_MINIMO):
        self.clave = (clave or "").strip().lower()
        frases = frase_fin or FRASES_FIN_POR_DEFECTO
        if isinstance(frases, str):
            frases = [frases]
        self.frases_fin = tuple(str(f).strip() for f in frases if str(f).strip())
        self.frecuencia = frecuencia
        self.pico_minimo = pico_minimo
        self.error = ""
        # Texto que se oyó al cerrar (solo para el registro).
        self.cierre_detectado = ""
        # Última frase reconocida (para el registro y para el aviso de orden).
        self.ultimo_texto = ""
        # Lo que se dijo DESPUÉS de la clave en la misma frase.
        self.resto_tras_clave = ""
        # Pico del último segmento reconocido (para el registro).
        self.ultimo_pico = 0

        self._modelo = None
        self._disponible = False
        self._buffer = []
        self._muestras = 0
        self._hablando = False
        self._silencio_muestras = 0
        self._pico_segmento = 0
        tamano_pre_roll = max(1, int(PRE_ROLL_SEGUNDOS * frecuencia / 1024))
        self._pre_roll = deque(maxlen=tamano_pre_roll)

        if not self.clave:
            self.error = "no hay palabra clave configurada"
            return

        nombre = (ruta_modelo or "").strip() or MODELO_POR_DEFECTO
        try:
            from faster_whisper import WhisperModel

            self._modelo = WhisperModel(
                nombre, device="cpu", compute_type="int8", cpu_threads=HILOS
            )
            self._disponible = True
        except Exception as e:  # falta el paquete, no hay modelo, sin memoria...
            self.error = str(e)
            self._modelo = None
            self._disponible = False

    @property
    def disponible(self):
        return self._disponible

    def alimentar(self, chunk):
        """Procesa un chunk int16. Devuelve el evento o None.

        Devuelve `'clave'`, `'fin'`, `'comando'`, `'frase'` o `None`. `'frase'`
        es cualquier otra transcripción con voz: en modo comando es una consulta
        para el asistente, y quien decide eso es el cliente.
        """
        if not self._disponible or self._modelo is None:
            return None
        try:
            # `sounddevice` entrega los chunks con forma (muestras, canales):
            # con un canal quedan (1024, 1). Whisper necesita audio **1D**, y
            # concatenar en 2D hacía fallar la transcripción en silencio
            # (2026-11-05: el detector no reconocía nada dentro del cliente).
            datos = np.asarray(chunk, dtype=np.int16).reshape(-1)
            pico = int(np.abs(datos.astype(np.int32)).max()) if datos.size else 0
        except Exception:
            return None

        if pico > self._pico_segmento:
            self._pico_segmento = pico

        if not datos.size:
            return None

        if pico >= self.pico_minimo:
            if not self._hablando:
                # Empieza la frase: se arranca el búfer con el pre-roll. Antes
                # se transcribían los segundos de silencio anteriores, y con el
                # silencio Whisper inventa frases.
                self._buffer = list(self._pre_roll)
                self._muestras = sum(int(x.size) for x in self._buffer)
                self._hablando = True
            self._buffer.append(datos)
            self._muestras += datos.size
            self._silencio_muestras = 0
            self._pre_roll.clear()
        else:
            self._pre_roll.append(datos)
            if self._hablando:
                self._buffer.append(datos)
                self._muestras += datos.size
                self._silencio_muestras += datos.size

        cierra_por_silencio = (
            self._hablando
            and self._silencio_muestras >= SILENCIO_CIERRA_SEGUNDOS * self.frecuencia
        )
        cierra_por_tope = self._muestras >= MAX_FRASE_SEGUNDOS * self.frecuencia
        if not (cierra_por_silencio or cierra_por_tope):
            return None
        return self._cerrar_frase()

    def _cerrar_frase(self):
        """Transcribe lo acumulado y clasifica el texto."""
        if not self._buffer:
            self._limpiar_buffer()
            return None
        try:
            audio = np.concatenate(self._buffer).astype(np.float32).reshape(-1) / 32768.0
        except Exception as e:
            imprimir(f"[ESCUCHA] No se pudo unir el audio: {e}")
            self._limpiar_buffer()
            return None
        pico = self._pico_segmento
        self._limpiar_buffer()

        if pico < self.pico_minimo:
            # Ruido o un golpe: no hay nada que interpretar.
            return None

        try:
            segmentos, _info = self._modelo.transcribe(
                audio,
                language="es",
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=True,
                without_timestamps=True,
                hotwords=HOTWORDS,
            )
            texto = " ".join(s.text for s in segmentos).strip()
        except Exception as e:
            self.error = str(e)
            imprimir(f"[ESCUCHA] No se pudo transcribir: {e}")
            return None

        self.ultimo_pico = pico
        return self._clasificar(texto)

    def _clasificar(self, texto):
        """Traduce el texto a un evento, igual que hacía la versión con Vosk.

        Se compara sobre el texto **normalizado** (sin tildes ni puntuación)
        porque Whisper escribe «¡computadora!» o «computadora.»: buscar la
        clave con el texto tal cual no la encontraba (visto el 2026-11-05).
        """
        texto = (texto or "").strip().lower()
        if not texto:
            return None
        normalizado = normalizar(texto)
        if not normalizado:
            return None

        palabras = normalizado.split()
        clave = normalizar(self.clave)
        if clave and clave in palabras:
            # Lo que va detrás de la clave no se tira: puede ser la orden entera
            # («computadora iniciar dictado»). Lo resuelve el cliente.
            posicion = palabras.index(clave)
            self.resto_tras_clave = " ".join(palabras[posicion + 1:]).strip()
            self.ultimo_texto = normalizado
            return "clave"
        # Se fija **antes** de mirar el cierre: si no, la rama de cierre dejaba
        # `ultimo_texto` vacío y el cliente no imprimía la traza, así que en el
        # registro no quedaba rastro de lo que se había oído (2026-11-05).
        self.ultimo_texto = normalizado
        if self._es_cierre(texto):
            self.cierre_detectado = texto
            return "fin"

        if es_comando(normalizado):
            return "comando"
        return "frase"

    def _es_cierre(self, texto):
        """True si el texto acaba en la frase de cierre, en cualquier variante."""
        normal = normalizar(texto)
        if not normal:
            return False
        for frase in self.frases_fin:
            if normal.endswith(normalizar(frase)):
                return True
        return quitar_frase_cierre(texto) != texto

    def _limpiar_buffer(self):
        self._buffer = []
        self._muestras = 0
        self._hablando = False
        self._silencio_muestras = 0
        self._pico_segmento = 0
        self._pre_roll.clear()

    def reiniciar(self):
        """Olvida lo oído hasta ahora (se llama al abrir y cerrar cada dictado)."""
        self._limpiar_buffer()
        self.resto_tras_clave = ""
