"""Traducción de texto con los modelos Opus-MT (transformers, en CPU).

Va **dentro del puente**, no en el servidor de dictado: son procesos distintos,
con venv distinto. El dictado sigue igual (ZMQ → Whisper en la GPU) y esto solo
toca texto, en la CPU, con dos hilos como mucho para no quitarle CPU a
Whisper cuando coincidan.

Se usa `transformers` + `torch` (CPU) y NO CTranslate2: la conversión de estos
modelos Marian a CT2 repetía la frase tres veces (probado el 2026-10-31 con
int8 y fp32, beam 1/2/5, con y sin `end_token`, transformers 4 y 5). Con el
modelo original, `generate` devuelve una frase correcta.

Los pares directos son los que existen en Helsinki-NLP:

    es→en, en→es, es→it, it→es, en→zh, zh→en
    en→pt (en-roa, token >>por<<), pt→en (roa-en, token >>por<<)

Todo lo demás se resuelve con **pivote por inglés** (`es→zh` = `es→en→zh`), que
es como lo hace LibreTranslate. El portugués no tiene par directo con el
español: por eso van los modelos de lenguas romances en vez de es-pt.

Los modelos se descargan con `herramientas/preparar_modelos.py` y viven en
`modelos/` (fuera de Git). Se cargan **bajo demanda** y se guardan como mucho
`MAX_EN_MEMORIA` a la vez: cada uno ocupa unos 300 MB de RAM.
"""
import re
import threading
from collections import OrderedDict
from pathlib import Path

import config

# Idiomas que entiende el puente. El código es el que se usa en la API y en el
# teclado; el nombre largo, solo para los mensajes.
IDIOMAS = {
    "es": "español",
    "en": "inglés",
    "pt": "portugués",
    "it": "italiano",
    "zh": "chino",
}

# (origen, destino) -> (carpeta del modelo, token de idioma o None)
RUTAS = {
    ("es", "en"): ("es-en", None),
    ("en", "es"): ("en-es", None),
    ("es", "it"): ("es-it", None),
    ("it", "es"): ("it-es", None),
    ("en", "zh"): ("en-zh", None),
    ("zh", "en"): ("zh-en", None),
    ("en", "pt"): ("en-pt", None),
    ("pt", "en"): ("roa-en", ">>por<<"),
}

# Modelos abiertos a la vez (cada uno ~300 MB de RAM en fp32). Se puede subir
# con DICTADO_MODELOS_EN_MEMORIA: cada modelo cargado evita pagar 3–5 s de
# carga la próxima vez.
MAX_EN_MEMORIA = max(1, config.MODELOS_EN_MEMORIA)

# Ficheros de pesos que deja `save_pretrained` (uno u otro, según la versión).
PESOS = ("model.safetensors", "pytorch_model.bin")

_modelos = OrderedDict()
_candado = threading.Lock()


class ErrorTraduccion(Exception):
    """No se pudo traducir: idioma no soportado, modelo ausente, texto vacío…"""


class _Modelo:
    """Un modelo Opus-MT cargado con transformers (CPU)."""

    def __init__(self, carpeta):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        # Dos hilos como mucho: Whisper también vive en esta máquina.
        torch.set_num_threads(max(1, config.HILOS_TRADUCCION))
        self.torch = torch
        self.tokenizador = AutoTokenizer.from_pretrained(str(carpeta))
        self.modelo = AutoModelForSeq2SeqLM.from_pretrained(str(carpeta))
        self.modelo.eval()

    def traducir(self, texto, token):
        # Los modelos de lenguas romances piden el idioma destino delante.
        entrada = f"{token} {texto}" if token else texto
        lotes = self.tokenizador(
            [entrada], return_tensors="pt", truncation=True, max_length=512
        )
        with self.torch.no_grad():
            salida = self.modelo.generate(
                **lotes, max_new_tokens=256, num_beams=1, early_stopping=True
            )
        return self.tokenizador.batch_decode(
            salida, skip_special_tokens=True
        )[0].strip()


def carpeta_de(nombre):
    return Path(config.MODELOS_DIR) / nombre


def modelos_instalados():
    """Carpetas de modelo que hay en disco (sin cargarlas)."""
    base = Path(config.MODELOS_DIR)
    if not base.is_dir():
        return []
    return sorted(
        p.name
        for p in base.iterdir()
        if (p / "config.json").exists()
        and any((p / n).exists() for n in PESOS)
    )


def disponible():
    """¿Hay al menos un modelo instalado?"""
    return bool(modelos_instalados())


def detectar(texto):
    """Idioma probable del texto, con reglas muy simples.

    No pretende ser un detector de verdad: solo decide entre los cinco idiomas
    que soporta el puente. Lo que no encaja en nada se trata como español, que
    es el idioma del usuario.
    """
    if any("\u4e00" <= c <= "\u9fff" for c in texto):
        return "zh"
    bajo = texto.lower()
    if any(c in bajo for c in "ãõçâê"):
        return "pt"
    if re.search(r"\b(the|and|you|are|is|how|hello|thanks|please|what)\b", bajo):
        return "en"
    return "es"


def _cargar(nombre):
    """Abre un modelo y lo deja en la caché (cerrando el más viejo si hace falta)."""
    with _candado:
        if nombre in _modelos:
            _modelos.move_to_end(nombre)
            return _modelos[nombre]
        carpeta = carpeta_de(nombre)
        if not (carpeta / "config.json").exists():
            raise ErrorTraduccion(
                f"Falta el modelo «{nombre}»: ejecuta herramientas/preparar_modelos.py"
            )
        modelo = _Modelo(carpeta)
        _modelos[nombre] = modelo
        while len(_modelos) > MAX_EN_MEMORIA:
            _modelos.popitem(last=False)
        return modelo


def _tramo(nombre, texto, token):
    """Traduce un tramo con un modelo concreto."""
    return _cargar(nombre).traducir(texto, token)


def _pasos(origen, destino):
    """Cadena de pares a recorrer: directa o pasando por inglés."""
    if origen == destino:
        return []
    if (origen, destino) in RUTAS:
        return [(origen, destino)]
    # Pivote por inglés. Solo vale si el inglés está en un extremo.
    if origen != "en" and destino != "en":
        if (origen, "en") in RUTAS and ("en", destino) in RUTAS:
            return [(origen, "en"), ("en", destino)]
    raise ErrorTraduccion(f"No hay camino para traducir de {origen} a {destino}.")


def traducir(texto, destino, origen="auto"):
    """Traduce `texto` al idioma `destino`. Devuelve (texto, origen, destino).

    Es una operación de CPU y bloquea: quien la llame desde FastAPI debe usar
    `asyncio.to_thread`, y así el bucle de eventos sigue atendiendo dictados.
    """
    limpio = (texto or "").strip()
    if not limpio:
        raise ErrorTraduccion("No hay texto que traducir.")
    if len(limpio) > config.MAX_CARACTERES_TRADUCCION:
        raise ErrorTraduccion(
            f"El texto pasa del tope de {config.MAX_CARACTERES_TRADUCCION} caracteres."
        )

    destino = (destino or "").strip().lower()
    if destino not in IDIOMAS:
        raise ErrorTraduccion(
            f"Idioma de destino no soportado: «{destino}». "
            f"Se admiten: {', '.join(sorted(IDIOMAS))}."
        )
    origen = (origen or "auto").strip().lower()
    if origen == "auto":
        origen = detectar(limpio)
    if origen not in IDIOMAS:
        raise ErrorTraduccion(f"Idioma de origen no soportado: «{origen}».")

    if origen == destino:
        return limpio, origen, destino

    resultado = limpio
    for (desde, hasta) in _pasos(origen, destino):
        nombre, token = RUTAS[(desde, hasta)]
        resultado = _tramo(nombre, resultado, token)
    return resultado, origen, destino


def liberar():
    """Suelta los modelos abiertos (al parar el puente)."""
    with _candado:
        _modelos.clear()


def calentar(nombres):
    """Carga modelos por adelantado y traduce una frase corta.

    Sin esto, la primera traducción de cada par paga la carga del modelo
    (3–5 s en el servidor; medido el 2026-10-31: 63 s en la Dell, con los
    modelos dentro de una carpeta sincronizada por Nextcloud). Se llama al
    arrancar el puente, en un hilo aparte, así que no retrasa el servicio.
    """
    for nombre in nombres:
        try:
            modelo = _cargar(nombre)
            modelo.traducir("Hola.", None)
        except Exception as e:  # noqa: BLE001 - calentar nunca debe tumbar nada
            print(f"[PUENTE] No se pudo precalentar «{nombre}»: {e}")
