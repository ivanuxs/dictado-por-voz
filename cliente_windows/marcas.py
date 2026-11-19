"""Intérprete de las órdenes dictadas por voz, del lado del **cliente**.

Aquí vivía el contrato con el servidor: las marcas `§ ¬ ¶` que él ponía en el
texto para decir «esto es una orden y su argumento es este». **Ese contrato ya
no existe.** El servidor solo transcribe y devuelve el texto limpio; el cliente
es el que decide si lo dictado es una orden, porque es el único que oye la
palabra clave, sabe si el usuario se calló después del argumento y tiene el
catálogo de programas (`comandos.json`).

Las listas `VERBOS` y `FRASES` son **del cliente**. Para añadir una acción se
toca este archivo y la tabla de `ejecutar_comando()` en `cliente_voz.py`.

- `parsear()` convierte el texto en `(accion, argumento)`;
- `resolver()` traduce la acción a lo que sabe hacer el sistema;
- `es_comando()` responde rápido «¿esto empieza por una orden?».

⚠️ Este archivo existe por duplicado: `marcas.py` (Linux) y
`cliente_windows/marcas.py`. Los dos tienen que decir exactamente lo mismo.
"""
import re
import unicodedata

# Lo que se recorta al principio y al final del argumento.
_BORDE = " \t,.;:!¡¿?…\"'«»()[]"

# Valores **por defecto**. Los efectivos viven en `VERBOS` y `FRASES`, que la
# ventana de configuración puede cambiar: se guardan en `comandos.json` (claves
# "verbos" y "frases") y se aplican con `aplicar_configuracion()`.
#
# Formas que puede decir el usuario → verbo canónico. Se comparan ya
# normalizadas (minúsculas y sin tildes).
VERBOS_POR_DEFECTO = {
    "abre": "abre", "abrir": "abre", "abreme": "abre", "abrirme": "abre",
    "abrelo": "abre", "abrirlo": "abre", "abrela": "abre", "abrirla": "abre",
    "abrenos": "abre", "abrirnos": "abre",
    "cierra": "cerrar", "cerrar": "cerrar", "cierrame": "cerrar",
    "cerrarme": "cerrar", "cierralo": "cerrar", "cerrarlo": "cerrar",
    "cierrala": "cerrar", "cerrarla": "cerrar", "cierranos": "cerrar",
    "cerrarnos": "cerrar",
    # Abrir una página web. El argumento es el sitio («seace», «youtube.com»);
    # «abre» también vale: si no es un programa del catálogo, se busca en el de
    # sitios web (ver `_resolver_sitio` en cliente_voz.py).
    "ingresa": "web", "ingresar": "web", "ingresamos": "web",
    "entra": "web", "entrar": "web",
    "visita": "web", "visitar": "web",
    "navega": "web", "navegar": "web",
    "ve": "web", "ir": "web", "anda": "web",
    # Buscar en internet (Google).
    "busca": "buscar", "buscar": "buscar", "buscame": "buscar",
}

# Frases de sistema (el comando es la frase entera, sin argumento) → acción
# local. Si el usuario dice otra cosa, no es una orden y se dicta.
FRASES_POR_DEFECTO = {
    "sube volumen": "subir_volumen",
    "subir volumen": "subir_volumen",
    "aumenta volumen": "subir_volumen",
    "baja volumen": "bajar_volumen",
    "bajar volumen": "bajar_volumen",
    "reduce volumen": "bajar_volumen",
    "silencio": "silenciar",
    "silencia": "silenciar",
    "mute": "silenciar",
    "mudo": "silenciar",
    "reproduce": "reproducir",
    "reproducir": "reproducir",
    "play": "reproducir",
    "pausa": "reproducir",
    "pausar": "reproducir",
    "siguiente": "siguiente",
    "siguiente cancion": "siguiente",
    "next": "siguiente",
    "anterior": "anterior",
    "anterior cancion": "anterior",
    "prev": "anterior",
    "captura pantalla": "captura",
    "capturar pantalla": "captura",
    "pantallazo": "captura",
    "bloquea pantalla": "bloquear",
    "bloquear pantalla": "bloquear",
    "bloquear": "bloquear",
    "apaga": "apagar",
    "apagar": "apagar",
    "apagar equipo": "apagar",
    "reinicia": "reiniciar",
    "reiniciar": "reiniciar",
    "reiniciar equipo": "reiniciar",
    # Dictado: la palabra clave abre el **modo comando**, así que para dictar
    # texto hay que pedirlo. Estas frases son el único camino al dictado. Se
    # incluyen las variantes que el motor local reconoce peor (singular/plural,
    # con y sin artículo) porque fallar aquí manda el dictado a la IA.
    "iniciar dictado": "dictar",
    "iniciar el dictado": "dictar",
    "iniciar dictados": "dictar",
    "inicia dictado": "dictar",
    "inicia el dictado": "dictar",
    "empezar dictado": "dictar",
    "empezar el dictado": "dictar",
    "empieza dictado": "dictar",
    "empieza el dictado": "dictar",
    "comenzar dictado": "dictar",
    "comenzar el dictado": "dictar",
    "comienza el dictado": "dictar",
    "modo dictado": "dictar",
    "pasar a dictado": "dictar",
    "dictar": "dictar",
    "dictado": "dictar",
    # El modelo pequeño parte «iniciar dictado» y solo entrega la primera
    # palabra (medido el 2026-11-05: «[ESCUCHA] «iniciar»»). Estas se aceptan
    # **solo** si son la frase entera (ver `_FRASES_EXACTAS`).
    "iniciar": "dictar",
    "inicia": "dictar",
    "empezar": "dictar",
    "empieza": "dictar",
    "comenzar": "dictar",
    "comienza": "dictar",
}

# Frases genéricas que solo valen **solas**: «iniciar sesión» o «empieza a
# llover» no deben abrir un dictado.
_FRASES_EXACTAS = {
    "iniciar", "inicia", "empezar", "empieza", "comenzar", "comienza",
}

# Listas efectivas: se cargan de `comandos.json` con `aplicar_configuracion()`.
VERBOS = dict(VERBOS_POR_DEFECTO)
FRASES = dict(FRASES_POR_DEFECTO)


def aplicar_configuracion(datos):
    """Toma `verbos` y `frases` de `comandos.json` (si vienen) y los aplica.

    Sin esas claves, o si vienen vacías o mal formadas, se vuelve a los valores
    por defecto: un JSON roto no puede dejar al cliente sin comandos. Las claves
    se normalizan porque es así como se comparan.
    """
    global VERBOS, FRASES
    if not isinstance(datos, dict):
        VERBOS = dict(VERBOS_POR_DEFECTO)
        FRASES = dict(FRASES_POR_DEFECTO)
        return

    verbos = datos.get("verbos")
    if isinstance(verbos, dict) and verbos:
        VERBOS = {
            normalizar(k): normalizar(v)
            for k, v in verbos.items()
            if str(k).strip() and str(v).strip()
        }
    else:
        VERBOS = dict(VERBOS_POR_DEFECTO)

    frases = datos.get("frases")
    if isinstance(frases, dict) and frases:
        FRASES = {
            normalizar(k): str(v).strip()
            for k, v in frases.items()
            if str(k).strip() and str(v).strip()
        }
    else:
        FRASES = dict(FRASES_POR_DEFECTO)


def normalizar(texto):
    """Minúsculas, sin tildes, sin puntuación y con espacios simples.

    La misma normalización que usa `escucha_local` al comparar lo que oye, para
    que las dos partes hablen del mismo texto.
    """
    plano = unicodedata.normalize("NFKD", (texto or "").lower())
    plano = plano.encode("ascii", "ignore").decode("ascii")
    plano = re.sub(r"[^a-z0-9]+", " ", plano)
    return " ".join(plano.split())


def es_comando(texto):
    """True si el texto empieza por un verbo o una frase de sistema conocida.

    Lo usa el cliente para cerrar solo un dictado de voz cuando lo que se dijo
    es una orden («abre calculadora»), sin esperar a «fin del dictado». Solo
    mira el principio, que es donde se busca el verbo.
    """
    palabras = normalizar(texto).split()
    if not palabras:
        return False
    if palabras[0] in VERBOS:
        return True
    inicio = " ".join(palabras)
    return any(inicio.startswith(frase) for frase in FRASES)


def parsear(texto):
    """Convierte lo que se dijo en una orden, si empieza por una.

    Devuelve `(accion, argumento)` —«abre», «calculadora» para «abre
    calculadora»— o `(None, "")` si no empieza por un verbo ni por una frase de
    sistema.

    Lo usa el cliente para ejecutar **sin** pasar por el servidor: `resolver()`
    y `ejecutar_comando()` esperan justo esta pareja.
    """
    normal = normalizar(texto)
    if not normal:
        return None, ""
    # Frase de sistema: se prueba de la más larga a la más corta, para que
    # «captura pantalla» gane a «captura».
    for frase in sorted(FRASES, key=len, reverse=True):
        if normal == frase:
            return frase, ""
        if frase in _FRASES_EXACTAS:
            continue
        if normal.startswith(frase + " "):
            return frase, ""
    palabras = normal.split()
    if palabras[0] in VERBOS:
        return VERBOS[palabras[0]], " ".join(palabras[1:]).strip()
    return None, ""


def resolver(accion, argumento=""):
    """Traduce la acción a una acción local.

    Devuelve `(accion_local, argumento)` o `None` si no se reconoce. El
    argumento solo tiene sentido en `abre`, `cerrar`, `web` y `buscar`; en las
    frases de sistema se devuelve vacío.

    Acepta tanto el texto que se dijo («ingresa», «sube volumen») como la acción
    ya canónica («web», «subir_volumen»), porque `parsear()` devuelve la
    segunda.
    """
    clave = normalizar(accion)
    if not clave:
        return None
    if clave in VERBOS:
        return VERBOS[clave], (argumento or "").strip(_BORDE)
    if clave in FRASES:
        return FRASES[clave], ""
    # Acción ya canónica: es lo que sale de `parsear()`.
    if clave in VERBOS.values() or clave in FRASES.values():
        return clave, (argumento or "").strip(_BORDE)
    return None
