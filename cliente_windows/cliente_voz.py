"""Cliente de SkipKey — ZMQ DEALER.

Captura audio del micrófono, lo envía al servidor, recibe texto transcrito
y comandos de voz, y los ejecuta localmente (inyección de texto, apertura
de aplicaciones, control de volumen, TTS, etc.).
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from urllib.parse import quote_plus


def _bases_programa():
    """Carpetas donde buscar los archivos que acompañan al programa."""
    bases = []
    ejecutable = getattr(sys, "executable", "") or ""
    if ejecutable:
        bases.append(os.path.dirname(os.path.realpath(ejecutable)))
    temporal = getattr(sys, "_MEIPASS", "") or ""
    if temporal:
        bases.append(temporal)
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    return bases


def leer_version():
    """Versión del cliente, la que dejó escrita la compilación.

    `version.txt` lo copia el instalador junto al `.exe`. Si no está, es un
    cliente ejecutado desde el código fuente.
    """
    for base in _bases_programa():
        ruta = os.path.join(base, "version.txt")
        try:
            with open(ruta, encoding="utf-8") as archivo:
                version = archivo.read().strip()
        except OSError:
            continue
        if version:
            return version
    return "desarrollo"


# `--version` antes de las importaciones pesadas: tiene que responder rápido y
# sin depender de pantalla, audio ni red.
if __name__ == "__main__" and any(
    a in sys.argv[1:] for a in ("--version", "--versión", "-V")
):
    print(f"SkipKey {leer_version()}")
    raise SystemExit(0)

import numpy as np
import pyautogui
import pyperclip
import sounddevice as sd
import zmq
from pynput import keyboard

from protocolo import (
    AUDIO, DICTADO_START, DICTADO_STOP,
    TEXTO, TEXTO_PARCIAL, FIN_DICTADO, PING, PONG, ESTADO,
    AUTH_OK, AUTH_FAIL,
    CHUNK_MUESTRAS,
    crear_audio, crear_auth, crear_dictado_start, crear_dictado_start_vivo,
    crear_dictado_start_voz, crear_dictado_stop, crear_ping,
    decodificar_texto,
)
from marcas import aplicar_configuracion as aplicar_marcas
from marcas import es_comando, parsear, resolver
import marcas
import perfil as perfil_cuenta
import puente_dictado
import sesion
from diccionario_desktop import DiccionarioEscritorio

try:
    from voz_asistente import (
        _buscar_comando_simple as buscar_comando_simple,
        hablar,
        preguntar_deepseek,
    )
    ASISTENTE_DISPONIBLE = True
except Exception as _e:  # sin edge-tts el dictado y los comandos siguen igual
    ASISTENTE_DISPONIBLE = False
    _ERROR_ASISTENTE = str(_e)

    def buscar_comando_simple(_texto):
        return None

    def hablar(_texto):
        return None

    def preguntar_deepseek(_texto):
        return None
from bandeja import iniciar_en_hilo
from escucha_local import EscuchaLocal, quitar_frase_cierre
from sonidos import reproducir_sonido
from utils_logging import imprimir, rotar_logs_al_iniciar

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
FRECUENCIA = 16000

# --- Recuperación automática de la captura de audio ---
# Al arrancar con Windows (carpeta Inicio, Programador de tareas) el cliente
# suele abrirse ANTES que el sistema de audio: la tarjeta, el micrófono o el
# auricular Bluetooth todavía no están listos y la captura falla. Por eso el
# cliente reintenta abrirla en vez de quedarse sin micrófono para siempre.
SIN_AUDIO_SEGUNDOS = 5               # sin chunks durante este tiempo = caída
REINTENTO_AUDIO_SEGUNDOS = 10        # reintento durante el primer minuto
REINTENTO_AUDIO_SEGUNDOS_LARGO = 60  # reintento después de ese minuto

# --- Dictado por palabra clave con el motor local (solo Windows) ---
# El dictado se cierra diciendo una de las frases configuradas en
# comandos_windows.json. Una pausa al hablar **no** lo cierra (antes sí: 1,6 s
# de silencio lo cortaban a mitad de una frase). Quedan dos seguros que no se
# confunden con una pausa: uno para el dictado en que no se dijo nada y otro
# para el que se quedó abierto por olvido.
UMBRAL_VOZ = 500          # pico mínimo para considerar que se está hablando
DICTADO_VOZ_SIN_VOZ_SEGUNDOS = 6.0   # sin decir nada tras la clave = cerrado
DICTADO_VOZ_MAX_SEGUNDOS = 180.0     # tope de duración de un dictado de voz
# Silencio que confirma que lo dicho era una orden (o una consulta) y no el
# principio de otra frase. Son 2 s a propósito: al dictar un comando el usuario
# dice el verbo y el argumento y **se calla**, esperando que se ejecute. Una
# pausa de 0,8 s es demasiado corta: se confundiría con el aliento entre dos
# frases habladas.
ORDEN_SILENCIO_SEGUNDOS = 2.0
# Palabras que puede tener el argumento de una orden. Con 3 o más ya no es una
# orden: es una frase hablada, y en modo comando se manda al asistente (Whisper
# la transcribe y `voz_asistente` responde o la consulta a DeepSeek).
ORDEN_MAX_PALABRAS_ARGUMENTO = 2
# Tope del modo comando: si pasan estos segundos sin oír nada desde la última
# voz (o desde el pitido), se cierra. Se midió que con 15 s no daba tiempo a
# decir la orden después de oír el pitido (2026-11-05): el modo se cerraba y el
# «iniciar dictado» caía en el vacío. Un minuto es margen de sobra y el cierre
# solo ocurre si de verdad no se dice nada.
MODO_COMANDO_MAX_SEGUNDOS = 60
AVISO_AUDIO_SEGUNDOS = 30            # avisar en pantalla tras este tiempo


def _directorio_base():
    """Directorio persistente: junto al exe (congelado) o junto al script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _nombre_config():
    return "comandos_windows.json" if sys.platform == "win32" else "comandos.json"


RUTA_JSON = os.path.join(_directorio_base(), _nombre_config())


def leer_configuracion():
    """El JSON de configuración tal cual (vacío si falta o está roto).

    Se relee a propósito en cada uso: la ventana de configuración lo cambia sin
    reiniciar, y la cuenta necesita ver la dirección del puente al día.
    """
    try:
        with open(RUTA_JSON, encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}
RUTA_INDICADOR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indicador_grabacion.py"
)


def _asegurar_config():
    """Crea la configuración junto al exe si no existe (usa la empaquetada)."""
    if os.path.exists(RUTA_JSON):
        return
    try:
        fuente = None
        if getattr(sys, "frozen", False):
            fuente = os.path.join(sys._MEIPASS, _nombre_config())
        else:
            fuente = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), _nombre_config()
            )
        if fuente and os.path.exists(fuente):
            shutil.copy2(fuente, RUTA_JSON)
            imprimir(f"[CLIENTE] Configuración creada: {RUTA_JSON}")
    except Exception as e:
        imprimir(f"[CLIENTE] No se pudo crear configuración: {e}")


# Dirección del servidor usada cuando no se indica otra cosa.
# Prioridad:  --servidor IP:PUERTO  >  variable DICTADO_SERVIDOR
#             >  campo "servidor" de comandos_windows.json  >  este valor.
SERVIDOR_POR_DEFECTO = "127.0.0.1:5555"


def _servidor_por_defecto():
    """Devuelve la dirección del servidor según la prioridad indicada arriba."""
    env = os.environ.get("DICTADO_SERVIDOR")
    if env:
        return env
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("servidor", SERVIDOR_POR_DEFECTO)
    except Exception:
        return SERVIDOR_POR_DEFECTO


# Servidor por defecto (sobrescribir con --servidor IP:PUERTO)
SERVIDOR = f"tcp://{SERVIDOR_POR_DEFECTO}"

# Mapa de nombres de tecla a objetos pynput
MAPA_TECLAS = {
    "ctrl_l": keyboard.Key.ctrl_l,
    "ctrl_r": keyboard.Key.ctrl_r,
    "ctrl": keyboard.Key.ctrl,
    "media_play_pause": keyboard.Key.media_play_pause,
    "media_previous": keyboard.Key.media_previous,
    "media_next": keyboard.Key.media_next,
    "media_volume_mute": keyboard.Key.media_volume_mute,
    "shift_l": keyboard.Key.shift_l,
    "shift_r": keyboard.Key.shift_r,
    "shift": keyboard.Key.shift,
    "alt_l": keyboard.Key.alt_l,
    "alt_r": keyboard.Key.alt_r,
    "alt_gr": keyboard.Key.alt_gr,
    "alt": keyboard.Key.alt,
    "cmd": keyboard.Key.cmd,
    "tab": keyboard.Key.tab,
    "esc": keyboard.Key.esc,
    "tecla_pipe": keyboard.KeyCode.from_vk(94),
    "space": keyboard.Key.space,
}

# ---------------------------------------------------------------------------
# Helpers de configuración (mismos que dictado.py)
# ---------------------------------------------------------------------------

def _parsear_teclas(nombres):
    """Convierte nombres de teclas en (simples, combinaciones).

    Una combinación es una tupla con todas sus teclas, p. ej.
    ("ctrl", "alt", "space"): se dispara al pulsar la última con el resto
    presionadas.
    """
    if isinstance(nombres, str):
        nombres = [nombres]

    teclas_simples = []
    teclas_combinadas = []

    for nombre in nombres:
        if "+" in nombre:
            partes = [p.strip() for p in nombre.split("+")]
            if all(p in MAPA_TECLAS for p in partes):
                teclas_combinadas.append(tuple(MAPA_TECLAS[p] for p in partes))
            else:
                imprimir(f"Combinación '{nombre}' no soportada. Se ignorará.")
        elif nombre in MAPA_TECLAS:
            teclas_simples.append(MAPA_TECLAS[nombre])
        else:
            imprimir(f"Tecla '{nombre}' no soportada. Se ignorará.")

    return teclas_simples, teclas_combinadas


def cargar_tecla_activadora():
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        nombres = config.get("tecla_activadora", ["ctrl_l"])
    except Exception:
        nombres = ["ctrl_l"]

    teclas_simples, teclas_combinadas = _parsear_teclas(nombres)

    if not teclas_simples and not teclas_combinadas:
        teclas_simples = [keyboard.Key.ctrl_l]

    if isinstance(nombres, str):
        nombres = [nombres]
    imprimir(f"Tecla(s) activadora(s): {', '.join(nombres)}")
    return teclas_simples, teclas_combinadas


def cargar_tecla_escribir():
    """Tecla(s) que dictan escribiendo directamente donde está el cursor.

    Se configura en el JSON con `tecla_activadora_escribir`. Por defecto es
    ctrl+alt+espacio; poner `[]` desactiva este modo.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        nombres = config.get("tecla_activadora_escribir", ["ctrl+alt+space"])
    except Exception:
        nombres = ["ctrl+alt+space"]

    if isinstance(nombres, str):
        nombres = [nombres]
    if not nombres:
        return [], []

    teclas_simples, teclas_combinadas = _parsear_teclas(nombres)
    imprimir(f"Tecla(s) para escribir en el cursor: {', '.join(nombres)}")
    return teclas_simples, teclas_combinadas


def cargar_modo_salida():
    """Cómo se entrega el texto dictado: 'portapapeles' o 'escribir'."""
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        modo = str(config.get("modo_salida", "portapapeles")).lower().strip()
    except Exception:
        modo = "portapapeles"
    if modo not in ("portapapeles", "escribir"):
        imprimir(f"modo_salida '{modo}' no válido; se usará 'portapapeles'.")
        modo = "portapapeles"
    imprimir(f"Modo de salida del dictado: {modo}")
    return modo


def cargar_modo_activacion():
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        modo = config.get("modo_activacion", "mantener")
    except Exception:
        modo = "mantener"
    imprimir(f"Modo de activación: {modo}")
    return modo


def cargar_escucha_local():
    """¿Arrancar la escucha local de la palabra clave al abrir el cliente?

    Viene **apagada** por defecto: necesita el paquete faster-whisper instalado
    ejecutable, y no siempre está. Se puede encender y apagar en caliente desde
    el icono de la bandeja.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        activa = bool(config.get("escucha_local", False))
    except Exception:
        activa = False
    if activa:
        imprimir("Escucha local de la palabra clave: activada en la configuración.")
    return activa


def _nombre_modelo_whisper():
    """Modelo de Whisper que reconoce la palabra clave y las órdenes.

    Por defecto `base` (~142 MB, WER medido del 48 % frente al 103 % de Vosk
    small). Se puede cambiar con DICTADO_MODELO_WHISPER (`tiny`, `base`,
    `small`), o con una ruta local a un modelo ya convertido junto al `.exe`.
    """
    nombre = os.environ.get("DICTADO_MODELO_WHISPER", "").strip()
    if nombre:
        return nombre
    base = _directorio_base()
    for ruta in (
        os.path.join(base, "whisper-base"),
        os.path.join(base, "modelos", "whisper-base"),
    ):
        if os.path.isdir(ruta):
            return ruta
    return "base"
    return ""


def cargar_palabras_escucha():
    """Clave y frases de cierre configuradas en comandos_windows.json.

    `frase_fin` puede ser una cadena o una lista: cualquiera de ellas cierra el
    dictado. Las frases se usan tal cual en la gramática de SAPI y normalizadas
    para comparar lo que el motor devuelve.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        clave = str(config.get("clave", "computadora")).strip() or "computadora"
        frases = config.get("frase_fin", ["fin del dictado"])
        # Verbos y frases de comando son configurables: se aplican aquí.
        aplicar_marcas(config)
    except Exception:
        clave, frases = "computadora", ["fin del dictado"]
    if isinstance(frases, str):
        frases = [frases]
    frases = [str(f).strip() for f in frases if str(f).strip()]
    if not frases:
        frases = ["fin del dictado"]
    imprimir(f"Palabras de la escucha: clave «{clave}», cierre {frases}")
    return clave, frases


# ---------------------------------------------------------------------------
# Procesamiento de comandos (mismo que dictado.py)
# ---------------------------------------------------------------------------

def _controlar_volumen_windows(accion):
    """Controla el volumen con pycaw (solo Windows).

    accion: 'subir' | 'bajar' | 'silenciar'
    """
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        dispositivos = AudioUtilities.GetSpeakers()
        interfaz = dispositivos.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volumen = cast(interfaz, POINTER(IAudioEndpointVolume))
        if accion == "silenciar":
            volumen.SetMute(0 if volumen.GetMute() else 1, None)
        else:
            paso = 0.10 if accion == "subir" else -0.10
            actual = volumen.GetMasterVolumeLevelScalar()
            volumen.SetMasterVolumeLevelScalar(max(0.0, min(1.0, actual + paso)), None)
    except Exception as e:
        imprimir(f"[VOLUMEN] Error en Windows: {e}")


# Artículos que se descartan delante del programa: «el navegador» → «navegador».
_ARTICULOS = ("el ", "la ", "los ", "las ", "un ", "una ")


def _avisar_comando(titulo, texto, avisar=True):
    """Avisa de un problema con un comando, con diálogo o solo en el registro.

    `avisar=False` es para el dictado por voz: un diálogo encima de la ventana
    en la que estas dictando es intrusivo. El usuario oye el pitido de error y,
    si quiere el detalle, mira el registro del cliente.
    """
    if avisar:
        _avisar_usuario(titulo, texto)
    else:
        imprimir(f"[VOZ] {titulo}: {texto}")


# Palabras de relleno que se pueden decir delante del sitio: «ingresa a la
# página del seace» → «seace». Se van quitando del principio, probando el
# catálogo en cada paso (ver `_resolver_sitio`).
_RELLENO_SITIO = (
    "a", "al", "en", "el", "la", "los", "las", "un", "una", "de", "del",
    "pagina", "sitio", "web", "internet", "navegador",
)

# Finales de dominio que se dicen como palabra suelta: «youtube punto com» o
# «youtube com» llegan así porque la normalización quita el punto.
_EXTENSIONES = (
    "com", "org", "net", "pe", "gob", "edu", "io", "es", "tv", "info", "app",
)


def _resolver_sitio(nombre):
    """Devuelve la URL del sitio dicho por el usuario, o None.

    Se miran cuatro cosas, en este orden:

    1. el nombre tal cual en el catálogo `web` de comandos_windows.json;
    2. quitando relleno por delante («a la página del seace» → «seace»);
    3. quedándose con la parte del nombre si lo último es un dominio («youtube
       com» → «youtube» → el catálogo);
    4. si parece un dominio («youtube com» → «youtube.com»), se abre tal cual.

    Solo se aceptan direcciones http/https: por voz no se puede colar un
    `file://` ni nada parecido.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            sitios = json.load(f).get("web")
    except Exception:
        sitios = None
    if not isinstance(sitios, dict):
        sitios = {}

    texto = (nombre or "").strip().lower()
    if not texto:
        return None

    # «youtube punto com» se oye con «punto» en medio.
    palabras = [p for p in texto.split() if p not in ("punto", "punto.")]
    if not palabras:
        return None

    while palabras:
        candidato = " ".join(palabras)
        if candidato in sitios:
            return str(sitios[candidato])
        if palabras[0] not in _RELLENO_SITIO:
            break
        palabras = palabras[1:]

    if len(palabras) >= 2 and palabras[-1] in _EXTENSIONES:
        sin_extension = " ".join(palabras[:-1])
        if sin_extension in sitios:
            return str(sitios[sin_extension])
        return "https://" + ".".join(palabras)

    plano = "".join(palabras)
    if plano and "." in plano:
        if not plano.startswith(("http://", "https://")):
            plano = "https://" + plano
        return plano
    return None


def _buscar_programa(accion, nombre, avisar=True):
    """Busca el programa en comandos_windows.json y devuelve su ruta, o None.

    `accion` es «abre» o «cerrar». Se prueba el nombre tal cual y, si no está,
    sin el artículo de delante (el servidor manda lo que transcribió Whisper).
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            comandos = json.load(f)
    except Exception:
        _avisar_comando(
            "Configuración de comandos",
            "No se pudo leer comandos_windows.json.",
            avisar,
        )
        return None

    tabla = comandos.get(accion)
    if not isinstance(tabla, dict):
        _avisar_comando(
            "Comando no disponible",
            f"«{accion}» no está en comandos_windows.json.",
            avisar,
        )
        return None

    clave = (nombre or "").strip().lower()
    if clave in tabla:
        return tabla[clave]
    for articulo in _ARTICULOS:
        if clave.startswith(articulo):
            corto = clave[len(articulo):].strip()
            if corto in tabla:
                return tabla[corto]
            break
    return None


def _abrir_sitio(url, avisar=True):
    """Abre una dirección en el navegador predeterminado del sistema.

    Solo se aceptan `http` y `https`: lo que llega puede venir de una voz mal
    transcrita, y por aquí no debe poder colarse un `file://` ni un
    `javascript:`.
    """
    direccion = (url or "").strip()
    if not direccion.lower().startswith(("http://", "https://")):
        _avisar_comando(
            "Dirección no válida",
            f"«{direccion}» no empieza por http ni https.",
            avisar,
        )
        return False
    imprimir(f"[TOCINA] Abriendo {direccion}")
    try:
        webbrowser.open(direccion)
    except Exception as e:
        _avisar_comando("No se pudo abrir la página", str(e), avisar)
        return False
    return True


def ejecutar_comando(accion, argumento="", avisar=True):
    """Ejecuta una acción que el servidor marcó con `¬`.

    `accion` es lo que venía antes de la marca (un verbo de programa o una frase
    de sistema) y `argumento` lo que venía después. La lista válida es la del
    servidor (`comandos.py` de `servidor_dictado`): si aquí no se reconoce, se
    avisa y no se ejecuta nada.

    `avisar=False` deja el aviso solo en el registro: lo usa el dictado por voz
    (ver `_avisar_comando`).

    Devuelve True si se ejecutó algo.
    """
    destino = resolver(accion, argumento)
    if destino is None:
        _avisar_comando(
            "Comando no reconocido",
            f"El servidor envió «{(accion + ' ' + (argumento or '')).strip()}», "
            "que este cliente no sabe ejecutar.",
            avisar,
        )
        return False

    clave, dato = destino

    # --- Abrir y cerrar programas ---
    if clave in ("abre", "cerrar"):
        if not dato:
            _avisar_comando(
                "Comando incompleto",
                f"¿{'Abrir' if clave == 'abre' else 'Cerrar'} qué?",
                avisar,
            )
            return False
        objetivo = _buscar_programa(clave, dato, avisar)
        if not objetivo:
            # Puede ser una página web: «abre youtube».
            sitio = _resolver_sitio(dato) if clave == "abre" else None
            if sitio:
                return _abrir_sitio(sitio, avisar)
            return False
        if clave == "abre":
            imprimir(f"[TOCINA] Abriendo {objetivo}...")
            if sys.platform == "win32":
                try:
                    os.startfile(objetivo)
                except Exception:
                    subprocess.Popen(
                        [objetivo], shell=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
            else:
                subprocess.Popen(
                    [objetivo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        else:
            imprimir(f"[TOCINA] Cerrando {objetivo}...")
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/IM", objetivo],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", objetivo],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        return True

    # --- Dictado ---
    if clave == "dictar":
        # Abrir el dictado necesita el micrófono y el bucle del cliente: desde
        # aquí (ventana de configuración o prueba) no se puede.
        imprimir("[TOCINA] «dictar» solo se ejecuta en modo comando, por voz.")
        return False

    # --- Páginas web y búsquedas ---
    if clave == "web":
        if not dato:
            _avisar_comando("Comando incompleto", "¿Qué página abro?", avisar)
            return False
        return _abrir_sitio(dato, avisar)
    if clave == "buscar":
        if not dato:
            _avisar_comando("Comando incompleto", "¿Qué busco?", avisar)
            return False
        return _abrir_sitio(
            "https://www.google.com/search?q=" + quote_plus(dato), avisar
        )

    # --- Comandos de sistema ---
    if clave == "subir_volumen":
        if sys.platform == "win32":
            _controlar_volumen_windows("subir")
        else:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Volumen +10%")
        return True
    if clave == "bajar_volumen":
        if sys.platform == "win32":
            _controlar_volumen_windows("bajar")
        else:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Volumen -10%")
        return True
    if clave == "silenciar":
        if sys.platform == "win32":
            _controlar_volumen_windows("silenciar")
        else:
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Silencio alternado")
        return True
    if clave == "reproducir":
        if sys.platform == "win32":
            pyautogui.press("playpause")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioPlay"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Reproducir/Pausa")
        return True
    if clave == "siguiente":
        if sys.platform == "win32":
            pyautogui.press("nexttrack")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioNext"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Siguiente")
        return True
    if clave == "anterior":
        if sys.platform == "win32":
            pyautogui.press("prevtrack")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioPrev"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Anterior")
        return True
    if clave == "captura":
        if sys.platform == "win32":
            pyautogui.press("printscreen")
        else:
            subprocess.run(
                ["xdotool", "key", "Print"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Captura de pantalla")
        return True
    if clave == "bloquear":
        if sys.platform == "win32":
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(
                ["xdg-screensaver", "lock"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Pantalla bloqueada")
        return True
    if clave == "apagar":
        imprimir("[TOCINA] Apagado deshabilitado por seguridad. Hazlo manualmente.")
        return True
    if clave == "reiniciar":
        imprimir("[TOCINA] Reinicio deshabilitado por seguridad. Hazlo manualmente.")
        return True

    _avisar_comando(
        "Comando no disponible", f"«{clave}» no se pudo ejecutar.", avisar
    )
    return False


def _ejecutar_y_avisar(accion, argumento="", avisar=True):
    """Ejecuta la orden y avisa con un pitido de si salió o no.

    El pitido se da **aquí**, cuando ya se sabe el resultado: en el dictado por
    voz de una orden con argumento desconocido («abre cromo») hay que esperar a
    que el servidor devuelva el argumento corregido, así que no se puede avisar
    al oírlo.
    """
    if ejecutar_comando(accion, argumento, avisar=avisar):
        reproducir_sonido("orden")
    else:
        detalle = (accion + " " + (argumento or "")).strip()
        imprimir(f"[VOZ] La orden «{detalle}» no se ejecutó.")
        reproducir_sonido("error")


def _hablar_respuesta(texto):
    """Dice la respuesta del asistente en voz alta (edge-tts).

    Va en su propio hilo: `hablar()` bloquea hasta terminar y el bucle del
    cliente no puede quedarse esperando a que acabe la locución. Si el TTS
    falla, se avisa con el pitido de error: antes el fallo quedaba invisible.
    """
    reproducir_sonido("texto")
    try:
        if not hablar(texto):
            reproducir_sonido("error")
    except Exception as e:
        imprimir(f"[VOZ] No se pudo hablar la respuesta: {e}")
        reproducir_sonido("error")


# ---------------------------------------------------------------------------
# Indicador visual de grabación
# ---------------------------------------------------------------------------

def _mostrar_indicador():
    global _indicador_proceso
    if _indicador_proceso is not None:
        return
    # El indicador va en **otro proceso** con la bandera `--indicador`: Tk no
    # se crea dentro del cliente. Crearlo en un hilo de este proceso congeló el
    # cliente en Linux justo al empezar a dictar (2026-11-19); en Windows, ademá
    # crearlo desde el gancho desenganchaba el teclado.
    _indicador_proceso = subprocess.Popen(
        [sys.executable, "--indicador"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ocultar_indicador():
    global _indicador_proceso
    if _indicador_proceso is not None:
        try:
            _indicador_proceso.terminate()
            _indicador_proceso.wait(timeout=5)
        except Exception:
            pass
        _indicador_proceso = None


_indicador_proceso = None


# ---------------------------------------------------------------------------
# Cliente principal
# ---------------------------------------------------------------------------

class ClienteVoz:
    def __init__(self, direccion_servidor=SERVIDOR):
        self._direccion = direccion_servidor
        self._ctx = zmq.Context()
        self._socket = self._ctx.socket(zmq.DEALER)
        # Sin servidor, un DEALER acumula mensajes hasta el HWM y entonces
        # `send_multipart` se queda esperando para siempre: el cliente dejaba de
        # responder y había que cerrarlo (2026-11-19). HWM bajo + envíos NOBLOCK:
        # se pierde audio, no el cliente.
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDHWM, 300)
        self._audio_sin_servidor = False

        # Cola para audio (desde callback de sounddevice → bucle ZMQ)
        self._cola_audio = queue.Queue(maxsize=200)

        # Cola para eventos de teclado (desde pynput → bucle ZMQ)
        self._cola_eventos = queue.Queue()

        # Cola de textos a escribir en el cursor (una sola hebra los pega en
        # orden, así las frases del dictado en vivo no se pisan entre sí)
        self._cola_escritura = queue.Queue()

        # Estado
        self._grabando = False
        self._inyectando = False
        self._tecla_suelta = True
        self._activo = True

        # Teclas
        self._teclas_simples, self._teclas_combinadas = cargar_tecla_activadora()
        self._teclas_escribir_simples, self._teclas_escribir_combinadas = (
            cargar_tecla_escribir()
        )
        self._modo_activacion = cargar_modo_activacion()
        self._modo_salida = cargar_modo_salida()
        self._teclas_presionadas = set()
        # True si el dictado en curso debe escribirse donde está el cursor
        self._escribir_este_dictado = self._modo_salida == "escribir"

        # Ping periódico y health check
        self._ultimo_ping = time.time()
        self._ultimo_mensaje = time.time()  # Último mensaje recibido del servidor
        self._servidor_aviso = False  # Ya avisamos que no hay servidor
        self._servidor_confirmado = False  # Primera respuesta recibida
        self._esperando_transcripcion = False
        self._transcripcion_inicio = 0.0

        # Diagnóstico del micrófono
        self._ultimo_chunk_audio = time.time()  # Último chunk recibido
        self._pico_ventana = 0                  # Pico captado en la ventana
        self._chunks_ventana = 0                # Chunks recibidos en la ventana
        self._aviso_mic = False                 # Ya avisamos que no hay audio
        self._ultimo_informe_audio = time.time()

        # Recuperación de la captura de audio
        self._inicio_audio = time.time()        # Desde cuándo intentamos capturar
        self._ultimo_intento_audio = 0.0        # Último reintento de apertura
        self._ultimo_error_audio = ""           # Último error (para no repetirlo)

        # --- Cuenta y perfil (2026-11-18) ---------------------------------
        # Sin cuenta NO se dicta: el perfil es lo que hace que cada usuario
        # tenga sus correcciones y sus ajustes. El diccionario es local y manda;
        # la cuenta solo lo pone al día con el resto de sus aparatos.
        self._config_cuenta = leer_configuracion()
        self._diccionario = DiccionarioEscritorio(sesion.perfil()).cargar()
        if sesion.hay_sesion():
            imprimir(
                f"[CUENTA] Sesión de «{sesion.usuario()}» "
                f"(perfil «{sesion.perfil()}», plan {sesion.cargar().get('plan') or '—'})."
            )
        else:
            imprimir(
                "[CUENTA] Sin sesión: el dictado está desactivado. Abre el icono de "
                "la bandeja → Configuración… → Cuenta y entra con tu usuario."
            )
        threading.Thread(
            target=self._bucle_sincronizacion, daemon=True, name="perfil"
        ).start()

        # --- Escucha local de la palabra clave y bandeja ---
        self._escucha_local = None       # escucha_local.EscuchaLocal (Whisper)
        self._bandeja = None             # bandeja.Bandeja
        self._dictado_voz = False        # El dictado en curso lo abrió la clave
        self._voz_empezo = 0.0           # Cuándo se abrió el dictado por voz
        self._voz_hablado = False        # Ya se oyó algo en este dictado
        self._frase_cierre = ""          # Frase de cierre que oyó el motor local
        # Dictado de voz: el audio se guarda hasta saber qué es (orden,
        # consulta o dictado). Así un comando se ejecuta sin subir su audio.
        self._buffer_voz = []
        self._dictado_enviado = False   # el DICTADO_START ya salió al socket
        self._start_encolado = False    # el evento start ya está en cola
        # La palabra clave abre el **modo comando** (ver `_al_detectar_clave`).
        # Solo el comando «iniciar dictado» pasa a transcribir texto.
        self._modo_comando = False
        self._ultima_voz_modo = 0.0      # Última voz en modo comando
        # Frase oída esperando decisión: silencio de 2 s para saber si es una
        # orden (la corrige el servidor) o una consulta (la responde la IA).
        self._orden_pendiente = None
        # Qué se está esperando del servidor: "orden", "consulta" o None.
        self._esperando = None
        # Última frase que reconoció el motor local (para no repetirla en el log).
        self._ultimo_texto_oido = ""
        self.comandos_activos = True     # Se puede pausar desde la bandeja
        self._escucha_configurada = cargar_escucha_local()
        # Palabras: la clave abre el modo comando y cualquiera de las frases de
        # cierre termina el dictado. Salen de comandos_windows.json.
        self._clave, self._frases_fin = cargar_palabras_escucha()
        # La ventana de configuración escribe comandos_windows.json; aquí se
        # vigila su fecha para releerlo sin reiniciar (Windows no tiene señales).
        self._mtime_config = self._mtime_json()
        # Cómo terminó el bucle: el supervisor de `_bucle_cliente()` distingue
        # un cierre pedido («Salir» del icono) de un fallo que hay que relanzar.
        self._fallo = False
        self._cierre_pedido = False

        # --- Escucha de teclado (2026-11-19) ------------------------------
        # Windows desengancha los ganchos de bajo nivel en cuanto el callback
        # tarda unos cientos de milisegundos, y entonces el cliente se queda
        # sordo **sin decir nada** (pasó en la PC de las 14:21: 14 minutos sin
        # una sola pulsación en el registro y el proceso vivo). Se guarda el
        # listener para vigilarlo cada pocos segundos y se levanta de nuevo si
        # se cayó. Además, el callback ya no toca Tkinter (ver `_on_press`).
        self._listener = None
        self._teclas_vistas = 0
        self._ultimo_chequeo_teclas = 0.0
        self._ultimo_latido_teclas = time.time()

        # Transporte del audio (2026-11-19): "puente" (HTTPS, cualquier red) o
        # "zmq" (solo la red del servidor). Ver `_elegir_transporte`.
        self._transporte = "zmq"
        self._token_transporte = sesion.token()
        self._auth_avisada = False
        self._pcm_puente = bytearray()
        self._puente_ocupado = False
        self._aviso_largo = False

    def _mtime_json(self):
        """Fecha de modificación del JSON (0.0 si no se puede leer)."""
        try:
            return os.path.getmtime(RUTA_JSON)
        except Exception:
            return 0.0

    # --- Gancho de teclado -------------------------------------------------

    def _crear_listener(self):
        """Crea (o recrea) el gancho global de teclado.

        Se guarda en `self._listener` para poder vigilarlo: en Windows el gancho
        de bajo nivel se desengancha cuando el sistema deja de llamarlo a tiempo
        o al bloquear la sesión, y entonces deja de llegar cualquier tecla.
        """
        anterior = self._listener
        if anterior is not None:
            try:
                anterior.stop()
            except Exception:
                pass
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()
        imprimir("[CLIENTE] Escucha de teclado iniciada.")

    def _vigilar_teclado(self):
        """Revisa el gancho cada 5 s y lo vuelve a levantar si se cayó.

        Deja un latido cada 5 min con las pulsaciones vistas: si alguna vez
        vuelve a quedarse sordo, el registro lo dice en vez de quedarse mudo.
        """
        ahora = time.time()
        if ahora - self._ultimo_chequeo_teclas < 5:
            return
        self._ultimo_chequeo_teclas = ahora
        if not (self._listener is not None and self._listener.running):
            imprimir(
                "[TECLA] ⚠ La escucha de teclado se cayó (Windows desengancha "
                "los ganchos que tardan). Se vuelve a levantar."
            )
            try:
                reproducir_sonido("error")
            except Exception:
                pass
            try:
                self._crear_listener()
            except Exception as e:
                imprimir(f"[TECLA] No se pudo levantar la escucha: {e}")
            return
        if ahora - self._ultimo_latido_teclas >= 300:
            self._ultimo_latido_teclas = ahora
            imprimir(
                f"[TECLA] Escucha viva; {self._teclas_vistas} pulsaciones "
                "desde que arrancó el cliente."
            )

    def reiniciar_escucha_teclado(self):
        """Vuelve a levantar el gancho (menú del icono)."""
        imprimir("[TECLA] Reinicio de la escucha pedido desde el icono.")
        try:
            self._crear_listener()
        except Exception as e:
            imprimir(f"[TECLA] No se pudo reiniciar la escucha: {e}")
            try:
                reproducir_sonido("error")
            except Exception:
                pass
        self._actualizar_bandeja()

    def _vigilar_configuracion(self):
        """Recarga la configuración si la ventana la guardó mientras corre."""
        ahora = self._mtime_json()
        if ahora and ahora != self._mtime_config:
            self._mtime_config = ahora
            self._recargar_configuracion()

    def _recargar_configuracion(self):
        """Relee comandos_windows.json sin reiniciar el cliente.

        Cubre lo que la ventana de configuración cambia: la clave y las frases de
        la escucha, y los verbos y frases de comando (que se aplican sobre
        `marcas`). Las teclas y el dispositivo de audio necesitan reiniciar.
        """
        self._clave, frases = cargar_palabras_escucha()
        self._frases_fin = list(frases)
        if self._escucha_local is not None:
            self._escucha_local.clave = self._clave
            self._escucha_local.frases_fin = tuple(frases)
            self._escucha_local.reiniciar()
        imprimir(
            f"[CONFIG] Recargada: clave «{self._clave}», {len(marcas.FRASES)} "
            f"frases y {len(marcas.VERBOS)} verbos de comando."
        )
        self._actualizar_bandeja()

    # --- Callback de audio ---

    def _callback_audio(self, indata, frames, time_info, status):
        if status:
            imprimir(f"[AUDIO ERROR] {status}")
        # Diagnóstico del micrófono: registrar que el dispositivo entrega
        # datos y el nivel máximo captado. Permite distinguir en el log
        # "no llega audio" de "llega audio en silencio o muy bajo".
        ahora = time.time()
        self._ultimo_chunk_audio = ahora
        self._chunks_ventana += 1
        pico = 0
        try:
            pico = int(np.abs(indata.astype(np.int32)).max())
            if pico > self._pico_ventana:
                self._pico_ventana = pico
        except Exception:
            pass
        try:
            self._cola_audio.put_nowait(indata.copy())
        except queue.Full:
            pass  # Descartar si el buffer está lleno
        # ¿Se quedó callado un dictado abierto con la palabra clave?
        self._vigilar_fin_dictado_voz(pico)

    # --- Captura de audio ---

    def _abrir_stream_audio(self):
        """Abre (o reabre) la captura del micrófono. Devuelve el stream o None.

        Se usa al arrancar y cada vez que la captura se pierde. Reintentar es
        importante: si el cliente arranca junto con Windows (carpeta Inicio o
        Programador de tareas), el sistema de audio todavía no está listo y la
        primera apertura falla (o abre un dispositivo equivocado).
        """
        # Qué dispositivo de entrada se va a usar: útil para detectar un
        # micrófono muteado, desconectado o equivocado (sobre todo en Windows).
        try:
            info_entrada = sd.query_devices(kind="input")
            imprimir(
                f"[AUDIO] Dispositivo de entrada: {info_entrada['name']} "
                f"(canales={info_entrada['max_input_channels']}, "
                f"frecuencia por defecto="
                f"{info_entrada['default_samplerate']:.0f} Hz)"
            )
        except Exception as e:
            imprimir(f"[AUDIO] No se pudo consultar el dispositivo de entrada: {e}")
        try:
            stream = sd.InputStream(
                samplerate=FRECUENCIA,
                channels=1,
                callback=self._callback_audio,
                dtype="int16",
                blocksize=CHUNK_MUESTRAS,
            )
            stream.start()
            self._ultimo_chunk_audio = time.time()
            self._ultimo_error_audio = ""
            imprimir(
                f"[CLIENTE] Captura de audio iniciada a {stream.samplerate:.0f} Hz."
            )
            return stream
        except Exception as e:
            # El mismo error repetido cada 10 s llenaría el log sin aportar nada
            if str(e) != self._ultimo_error_audio:
                self._ultimo_error_audio = str(e)
                imprimir(
                    f"[AUDIO] No se pudo iniciar la captura: {e} "
                    "(se seguirá intentando)"
                )
            return None

    @staticmethod
    def _cerrar_stream_audio(stream):
        """Cierra la captura de audio sin propagar errores."""
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # --- Callbacks de teclado ---

    def _coincide_combinacion(self, key, combinaciones):
        """True si `key` completa alguna combinación (el resto ya pulsadas)."""
        for combo in combinaciones:
            if key == combo[-1] and all(
                tecla in self._teclas_presionadas for tecla in combo[:-1]
            ):
                return True
        return False

    def _tecla_activadora_pulsada(self, key):
        """Devuelve 'escribir', 'normal' o None según la tecla pulsada.

        Si una tecla estuviera configurada en las dos listas, gana 'escribir'.
        """
        if key in self._teclas_escribir_simples or self._coincide_combinacion(
            key, self._teclas_escribir_combinadas
        ):
            return "escribir"
        if key in self._teclas_simples or self._coincide_combinacion(
            key, self._teclas_combinadas
        ):
            return "normal"
        return None

    def _es_tecla_relevante(self, key):
        """True si la tecla pertenece a alguna lista/combinación configurada.

        Se usa al soltar: sirve para terminar el dictado, incluso cuando se
        suelta antes un modificador (Ctrl, Alt, Shift) que la tecla principal.
        """
        for combo in self._teclas_combinadas + self._teclas_escribir_combinadas:
            if key in combo:
                return True
        return key in self._teclas_simples or key in self._teclas_escribir_simples

    def _on_press(self, key):
        # Ojo: esto corre dentro del gancho de teclado del sistema. Todo lo que
        # se haga aquí retrasa la entrega de teclas y, si pasa de unos cientos
        # de milisegundos, Windows **desengancha el gancho** y la tecla deja de
        # llegar hasta reiniciar el cliente. Por eso aquí solo se marca estado
        # y se encolan eventos: la ventana del indicador la pinta el bucle
        # principal (2026-11-19).
        self._teclas_vistas += 1
        # Mantener registro de teclas presionadas
        self._teclas_presionadas.add(key)
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self._teclas_presionadas.add(keyboard.Key.shift)
        elif key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._teclas_presionadas.add(keyboard.Key.ctrl)
        elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            self._teclas_presionadas.add(keyboard.Key.alt)

        # ¿Es una tecla activadora? Puede ser la normal (deja el texto en el
        # portapapeles) o la de "escribir donde está el cursor".
        modo = self._tecla_activadora_pulsada(key)

        if modo is None:
            return

        imprimir("[TECLA] Activador detectado.")

        if self._inyectando:
            return

        if not self._tecla_suelta:
            return

        # Este dictado escribirá en el cursor si se activó con la tecla de
        # escribir o si modo_salida está configurado como "escribir".
        self._escribir_este_dictado = (
            modo == "escribir" or self._modo_salida == "escribir"
        )

        if self._modo_activacion == "toggle":
            if not self._grabando:
                # La cuenta se comprueba en TODOS los caminos que abren un
                # dictado. Faltaba justo aquí: con la tecla activadora se
                # dictaba sin sesión (2026-11-19).
                if not self._cuenta_lista():
                    return
                self._grabando = True
                self._tecla_suelta = False
                imprimir("\n[GRABANDO... Pulsa de nuevo para detener]")
                self._cola_eventos.put("indicador_on")
                self._cola_eventos.put("start")
            else:
                self._grabando = False
                self._tecla_suelta = False
                imprimir("[DETENIDO. Procesando...]")
                self._cola_eventos.put("indicador_off")
                self._cola_eventos.put("stop")
        else:
            if not self._grabando:
                # Igual que en «toggle»: sin cuenta no se empieza a grabar.
                if not self._cuenta_lista():
                    return
                self._grabando = True
                self._tecla_suelta = False
                imprimir("\n[GRABANDO... Mantén presionada la tecla activadora]")
                self._cola_eventos.put("indicador_on")
                self._cola_eventos.put("start")

    def _on_release(self, key):
        self._teclas_presionadas.discard(key)
        if key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
            self._teclas_presionadas.discard(keyboard.Key.shift)
        elif key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self._teclas_presionadas.discard(keyboard.Key.ctrl)
        elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            self._teclas_presionadas.discard(keyboard.Key.alt)

        es_relevante = self._es_tecla_relevante(key)

        if self._modo_activacion == "toggle":
            if es_relevante:
                self._tecla_suelta = True
            return

        if es_relevante and self._grabando:
            self._grabando = False
            self._tecla_suelta = True
            imprimir("[DETENIDO. Procesando...]")
            self._cola_eventos.put("indicador_off")
            self._cola_eventos.put("stop")

    # --- Procesar mensajes del servidor ---

    def _quitar_frase_cierre(self, texto):
        """Quita del final la frase de cierre que oyó el motor local.

        El servidor ya no necesita saber que se dijo (no se le manda `fin` en
        el protocolo): la quita el cliente, que es el único que sabe que la
        frase se dijo. El recorte es tolerante —«fin de dictado» y «fin del
        dictado» acaban igual— y solo mira la cola del texto, así que no puede
        tocar nada del medio.
        """
        if not self._frase_cierre or not (texto or "").strip():
            return texto
        return quitar_frase_cierre(texto)

    def _entregar_texto(self, texto):
        """Escribe o copia el texto de un dictado, según el modo de salida.

        El servidor ya devuelve el texto limpio: aquí solo se quita la frase de
        cierre cuando la oyó este cliente, y se aplican las correcciones
        aprendidas en la cuenta («se ace» → «seace»).
        """
        texto = self._quitar_frase_cierre(texto)
        # Si la ventana de configuración enseñó una corrección hace un momento,
        # se usa ya: no hace falta reiniciar el cliente.
        self._diccionario.recargar_si_cambio()
        texto = self._diccionario.corregir(texto)
        if not (texto or "").strip():
            return
        # El texto que se entrega se registra: lo que devuelve el servidor
        # todavía lleva la frase de cierre, así que en el registro no había
        # forma de ver qué acababa realmente en el portapapeles (2026-11-05).
        if self._escribir_este_dictado:
            # Modo "escribir": el texto aparece donde está el cursor
            self._cola_escritura.put(texto)
            imprimir(f"[TEXTO] Escrito donde está el cursor: «{texto}»")
        else:
            # Dictado normal → al portapapeles para pegar con Ctrl+V
            pyperclip.copy(texto + " ")
            imprimir(f"[TEXTO] Copiado al portapapeles: «{texto}»")
        reproducir_sonido("texto")

    def _autenticar(self):
        """Manda al servidor el token de la cuenta (si hay sesión).

        Se envía al conectar, cuando cambia la sesión (entrar o salir desde la
        ventana) y **cada 60 s** de recordatorio: al reiniciarse el servidor de
        dictado se pierde su estado de autenticación por cliente y, con la
        exigencia de cuenta activada, el cliente dejaría de dictar en silencio.
        """
        token = sesion.token() or ""
        momento = time.time()
        cambio = token != getattr(self, "_token_enviado", "")
        if not cambio and momento - getattr(self, "_auth_momento", 0.0) < 60:
            return
        self._token_enviado = token
        self._auth_momento = momento
        if not token:
            if cambio:
                imprimir("[SERVIDOR] Sin sesión: no hay token que presentar.")
            return
        try:
            self._enviar(crear_auth(token))
            if cambio:
                imprimir("[SERVIDOR] Enviada la autenticación de la cuenta.")
        except Exception as e:
            imprimir(f"[SERVIDOR] No se pudo enviar la autenticación: {e}")

    def _manejar_mensaje(self, frames):
        if len(frames) < 1:
            return
        tipo = frames[0]

        # Cada mensaje del servidor actualiza el heartbeat
        self._ultimo_mensaje = time.time()
        self._servidor_aviso = False
        if not self._servidor_confirmado:
            self._servidor_confirmado = True
            imprimir("[CLIENTE] ✓ Servidor responde. Conexión establecida.")

        if tipo == AUTH_OK:
            imprimir("[SERVIDOR] Cuenta validada: el servidor acepta el dictado.")
            return

        if tipo == AUTH_FAIL:
            motivo = (
                frames[1].decode("utf-8", "replace") if len(frames) > 1 else ""
            )
            imprimir(
                f"[SERVIDOR] ⚠ El servidor rechazó la autenticación: {motivo}. "
                "Sin una cuenta válida no se transcribe nada."
            )
            # Un solo cuadro por sesión (si no, cada 60 s salía otra ventana):
            # pasa cuando la cuenta se abre en otro equipo y el puente libera
            # este dispositivo por el límite del plan.
            if not self._auth_avisada:
                self._auth_avisada = True
                _avisar_usuario(
                    "Sesión no reconocida",
                    "El servidor no acepta la sesión de este equipo: suele pasar "
                    "cuando la cuenta se abre en más aparatos de los que permite "
                    "el plan. Vuelve a entrar en Configuración → Cuenta y el "
                    "dictado se reanuda.",
                )
            return

        if tipo == TEXTO:
            self._esperando_transcripcion = False
            texto = decodificar_texto(frames)
            imprimir(f"Texto detectado: {texto}")
            self._actualizar_bandeja()
            # El servidor ya no marca comandos: devuelve el texto limpio. Qué
            # hacer con él depende de lo que se estuviera esperando: corregir el
            # argumento de una orden, contestar una consulta o pegarlo.
            if self._esperando == "orden":
                self._esperando = None
                self._orden_corregida(texto)
            elif self._esperando == "consulta":
                self._esperando = None
                self._responder_consulta(texto)
            else:
                self._entregar_texto(texto)

        elif tipo == TEXTO_PARCIAL:
            # Frase del dictado en vivo: se escribe ya, donde está el cursor
            texto = decodificar_texto(frames)
            imprimir(f"Texto en vivo: {texto}")
            self._cola_escritura.put(texto)
            # Reiniciar el reloj del timeout: el servidor sigue respondiendo
            self._transcripcion_inicio = time.time()

        elif tipo == FIN_DICTADO:
            self._esperando_transcripcion = False
            imprimir("[CLIENTE] Fin del dictado en vivo.")
            reproducir_sonido("texto")

        elif tipo == ESTADO:
            texto = decodificar_texto(frames)
            imprimir(f"[SERVIDOR] {texto}")
            self._actualizar_bandeja()
            # Si esperábamos una transcripción, este aviso ES la respuesta
            # del dictado: el servidor no devolvió texto (no llegó audio o
            # no se detectó voz). Hay que decírselo al usuario en pantalla,
            # porque el ejecutable de Windows no tiene consola donde verlo.
            if self._esperando_transcripcion:
                self._esperando_transcripcion = False
                self._esperando = None
                _avisar_usuario("Dictado sin texto", texto)

        elif tipo == PONG:
            pass  # Silencioso, solo confirma que el servidor está vivo

    # --- Bucle principal ---

    def ejecutar(self):
        self._socket.connect(self._direccion)
        imprimir(f"[CLIENTE] Conectado a {self._direccion}")
        # Si hay sesión, se saluda con su token: el servidor puede exigir
        # cuenta para transcribir (DICTADO_EXIGIR_CUENTA).
        self._autenticar()

        # ¿Por dónde va el audio? Puente HTTPS (cualquier red) o ZMQ (red del
        # servidor). Ver `_elegir_transporte`.
        self._transporte = self._elegir_transporte()

        # Iniciar escucha de teclado ANTES que el audio: si el micrófono
        # falla, la activación por teclado debe seguir funcionando.
        self._crear_listener()

        # Hebra que escribe en el cursor los textos dictados (en orden)
        threading.Thread(target=self._bucle_escritura, daemon=True).start()

        # Icono en el área de notificación: enciende y apaga la escucha,
        # pausa los comandos y permite cerrar el cliente sin buscar la carpeta.
        try:
            self._bandeja = iniciar_en_hilo(self, al_fallar=_avisar_usuario)
        except Exception as e:
            imprimir(f"[BANDEJA] Sin icono en la bandeja: {e}")
            _avisar_usuario("Icono de la bandeja", str(e))

        # Escucha local de la palabra clave (motor de voz de Windows)
        if self._escucha_configurada:
            self._encender_escucha_local()
        self._actualizar_bandeja()

        # Iniciar captura de audio (stream continuo para la escucha local). Si falla, el
        # cliente sigue vivo (teclado, texto y comandos) y la reintenta en el
        # bucle principal hasta que el sistema de audio esté disponible.
        stream = self._abrir_stream_audio()
        if stream is None:
            imprimir(
                "[AUDIO] La captura no arrancó todavía; se reintentará cada "
                f"{REINTENTO_AUDIO_SEGUNDOS:.0f} s."
            )

        # Verificar conectividad con el servidor (fire-and-forget)
        self._enviar(crear_ping())

        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)

        imprimir("[CLIENTE] Listo. Usa la tecla activadora para dictar.")
        imprimir("[CLIENTE] Esperando respuesta del servidor para confirmar conexión...")

        try:
            while self._activo:
                # 1. Enviar audio acumulado
                self._enviar_audio()

                # 2. Procesar eventos de teclado
                self._procesar_eventos()

                # 2b. Releer la configuración si la ventana la guardó
                self._vigilar_configuracion()

                # 2c. Gancho de teclado: si Windows lo desenganchó, se levanta
                #     de nuevo. Sin esto el cliente se queda sordo en silencio.
                self._vigilar_teclado()

                # 2d. Entrar o salir de la cuenta cambia el transporte.
                self._revisar_transporte()

                # 3. Recibir mensajes del servidor (sin bloquear)
                socks = dict(poller.poll(timeout=50))
                if self._socket in socks:
                    frames = self._socket.recv_multipart()
                    self._manejar_mensaje(frames)

                # 4. Timeout de transcripción (180 s sin respuesta tras STOP;
                #    margen para CPUs lentas donde Whisper puede tardar
                #    varios minutos en transcribir dictados largos)
                ahora = time.time()
                if self._esperando_transcripcion:
                    if ahora - self._transcripcion_inicio > 180:
                        self._esperando_transcripcion = False
                        imprimir("[CLIENTE] ⚠ Timeout: el servidor no respondió con la transcripción en 180 s.")
                        _avisar_usuario(
                            "Sin transcripción",
                            "El servidor no respondió con la transcripción en "
                            "180 s. Revisa que servidor_voz.py esté corriendo.",
                        )

                # 4b. Supervisor de audio: si la captura no está abierta o dejó
                #     de entregar datos, reintentar abrirla. Esto es lo que
                #     permite que el cliente arrancado junto con Windows
                #     (carpeta Inicio / Programador de tareas) empiece a oír en
                #     cuanto el sistema de audio y el micrófono están listos.
                sin_audio = stream is None or (
                    ahora - self._ultimo_chunk_audio > SIN_AUDIO_SEGUNDOS
                )
                if sin_audio:
                    intervalo = (
                        REINTENTO_AUDIO_SEGUNDOS
                        if ahora - self._inicio_audio < 60
                        else REINTENTO_AUDIO_SEGUNDOS_LARGO
                    )
                    if ahora - self._ultimo_intento_audio >= intervalo:
                        self._ultimo_intento_audio = ahora
                        if stream is not None:
                            self._cerrar_stream_audio(stream)
                            stream = None
                        stream = self._abrir_stream_audio()
                    if (sin_audio and not self._aviso_mic
                            and ahora - self._inicio_audio > AVISO_AUDIO_SEGUNDOS):
                        self._aviso_mic = True
                        _avisar_usuario(
                            "Micrófono sin señal",
                            "El cliente no consigue audio del micrófono y seguirá "
                            "reintentando. Revisa el dispositivo de entrada: puede "
                            "estar muteado, desconectado o ser otro distinto al "
                            "que usas.",
                        )
                elif self._aviso_mic:
                    self._aviso_mic = False
                    imprimir("[AUDIO] Captura de audio recuperada.")

                # 4c. Informe periódico del nivel de entrada (diagnóstico)
                if ahora - self._ultimo_informe_audio >= 60:
                    imprimir(
                        f"[AUDIO] Nivel de entrada en los últimos 60 s: "
                        f"pico={self._pico_ventana}, "
                        f"chunks={self._chunks_ventana}"
                    )
                    if self._chunks_ventana == 0:
                        imprimir(
                            "[AUDIO] ⚠ El micrófono no entregó audio: revisa el "
                            "dispositivo de entrada."
                        )
                    self._pico_ventana = 0
                    self._chunks_ventana = 0
                    self._ultimo_informe_audio = ahora

                # 5. Health check: avisar si no hay respuesta del servidor en 15 s
                #    (se suprime mientras se espera una transcripción, ya que el
                #    servidor puede tardar legítimamente en procesarla)
                if (not self._servidor_aviso and not self._esperando_transcripcion
                        and ahora - self._ultimo_mensaje > 15):
                    self._servidor_aviso = True
                    imprimir("[CLIENTE] ⚠ Sin respuesta del servidor en 15 s. ¿Está corriendo servidor_voz.py?")

                # 6. Ping periódico (cada 10 s)
                if ahora - self._ultimo_ping > 10:
                    self._enviar(crear_ping())
                    self._ultimo_ping = ahora
                    # Entrar o salir de la cuenta desde la ventana se aplica
                    # aquí, sin reiniciar el cliente.
                    self._autenticar()

        except KeyboardInterrupt:
            imprimir("[CLIENTE] Detenido por el usuario.")
        except Exception as e:
            # Un fallo no puede llevarse el cliente en silencio: el `.exe` se
            # compila sin consola, así que sin esto no queda ni una línea (es
            # lo que pasaba cuando «dejaba de funcionar solo», 2026-11-19).
            self._fallo = True
            imprimir(f"[CLIENTE] ⚠ Fallo en el bucle principal: {e}")
            imprimir("[CLIENTE] " + traceback.format_exc().replace("\n", " | "))
        finally:
            self._activo = False
            self._apagar_escucha_local()
            if self._bandeja is not None:
                try:
                    self._bandeja.detener()
                except Exception:
                    pass
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            try:
                if self._listener is not None:
                    self._listener.stop()
            except Exception:
                pass
            self._listener = None
            try:
                _ocultar_indicador()
            except Exception:
                pass
            self._socket.close()
            self._ctx.term()

    def _enviar(self, frames):
        """Envía al servidor **sin bloquear jamás**.

        Sin servidor, el búfer del DEALER se llena y `send_multipart` se queda
        esperando para siempre: el cliente dejaba de responder y había que
        cerrarlo (2026-11-19). Mejor perder audio y avisar que quedarse mudo.
        """
        try:
            self._socket.send_multipart(frames, flags=zmq.NOBLOCK)
        except zmq.Again:
            if not self._audio_sin_servidor:
                self._audio_sin_servidor = True
                imprimir(
                    "[SERVIDOR] ⚠ Sin conexión: se descarta el audio para no "
                    "bloquear el cliente. Revisa la dirección del servidor "
                    f"({self._direccion})."
                )
                try:
                    reproducir_sonido("error")
                except Exception:
                    pass
            return False
        if self._audio_sin_servidor:
            self._audio_sin_servidor = False
            imprimir("[SERVIDOR] Conexión recuperada: se vuelve a enviar el audio.")
        return True

    # --- Transporte del audio: puente HTTPS o ZMQ -------------------------

    def _elegir_transporte(self):
        """Decide por dónde va el audio: `puente` (HTTPS) o `zmq` (red local).

        El puente es el único camino publicado en internet (el 5555 no está
        abierto), así que con sesión y puente disponible se usa siempre; ZMQ
        queda para cuando no hay sesión o el puente no responde.
        """
        modo = str(self._config_cuenta.get("transporte") or "auto").strip().lower()
        if modo == "zmq":
            imprimir("[TRANSPORTE] ZMQ (forzado en la configuración).")
            return "zmq"
        if not sesion.hay_sesion():
            imprimir(
                "[TRANSPORTE] Sin sesión: se usa ZMQ (el puente exige cuenta)."
            )
            return "zmq"
        if puente_dictado.disponible(self._config_cuenta):
            imprimir(
                "[TRANSPORTE] Puente HTTPS "
                f"({self._config_cuenta.get('puente')}): se dicta desde cualquier red."
            )
            return "puente"
        imprimir(
            "[TRANSPORTE] El puente no responde; se usa ZMQ "
            f"({self._direccion})."
        )
        return "zmq"

    def _revisar_transporte(self):
        """Vuelve a elegir el transporte si la sesión cambió (entrar/salir)."""
        token = sesion.token()
        if token == self._token_transporte:
            return
        self._token_transporte = token
        nuevo = self._elegir_transporte()
        if nuevo != self._transporte:
            self._transporte = nuevo
            self._pcm_puente = bytearray()
            imprimir(f"[TRANSPORTE] Cambiado a {nuevo}.")

    def _acumular_puente(self, chunk):
        """Guarda el trozo para mandarlo al puente al cerrar el dictado."""
        self._pcm_puente.extend(chunk.tobytes())
        if (
            len(self._pcm_puente) > puente_dictado.BYTES_MAXIMOS
            and not self._aviso_largo
        ):
            self._aviso_largo = True
            imprimir(
                "[PUENTE] ⚠ El dictado pasa de "
                f"{puente_dictado.SEGUNDOS_MAXIMOS} s: se envía solo hasta ahí."
            )

    def _lanzar_transcripcion_puente(self):
        """Manda lo acumulado al puente en una hebra (no bloquea el bucle)."""
        pcm = bytes(self._pcm_puente)
        self._pcm_puente = bytearray()
        self._aviso_largo = False
        if self._puente_ocupado:
            imprimir("[PUENTE] Ya hay un dictado en curso; este se descarta.")
            return
        if not pcm:
            imprimir("[PUENTE] No hay audio que enviar.")
            return
        self._puente_ocupado = True
        segundos = len(pcm) / (puente_dictado.FRECUENCIA * 2)
        imprimir(f"[PUENTE] Enviando {segundos:.1f} s de audio por HTTPS...")
        threading.Thread(
            target=self._transcribir_puente, args=(pcm,), daemon=True
        ).start()

    def _transcribir_puente(self, pcm):
        """Hebra: POST al puente y el resultado a la cola de eventos."""
        try:
            texto, error = puente_dictado.transcribir(self._config_cuenta, pcm)
        except Exception as e:  # nunca puede tumbar la hebra
            texto, error = "", f"fallo al dictar por el puente: {e}"
        self._puente_ocupado = False
        self._cola_eventos.put(("puente_texto", texto, error))

    def _enviar_audio(self):
        """Envía chunks de audio acumulados al servidor."""
        enviados = 0
        while enviados < 20:  # Máximo 20 chunks por iteración (~1.3 s de audio)
            try:
                chunk = self._cola_audio.get_nowait()
                if self._transporte == "puente":
                    # Al puente se le manda el audio del dictado al cerrar; el
                    # resto del tiempo solo se usa para la palabra clave.
                    if self._dictado_voz and not self._dictado_enviado:
                        self._buffer_voz.append(chunk)
                    elif self._grabando:
                        self._acumular_puente(chunk)
                elif self._dictado_voz and not self._dictado_enviado:
                    # Fase de decisión: se guarda en memoria para no mandar al
                    # servidor el audio de una orden que se ejecutará aquí.
                    self._buffer_voz.append(chunk)
                else:
                    self._enviar(crear_audio(chunk))
                self._alimentar_escucha(chunk)
                enviados += 1
            except queue.Empty:
                break

    def _procesar_eventos(self):
        """Procesa eventos de teclado encolados."""
        while True:
            try:
                evento = self._cola_eventos.get_nowait()
                if evento == "indicador_on":
                    # Lo pidió el gancho de teclado: Tkinter se toca aquí, en el
                    # bucle principal, nunca dentro del callback (2026-11-19).
                    try:
                        _mostrar_indicador()
                    except Exception as e:
                        imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
                elif evento == "indicador_off":
                    try:
                        _ocultar_indicador()
                    except Exception as e:
                        imprimir(f"[INDICADOR] No se pudo ocultar: {e}")
                elif evento == "start":
                    if self._transporte == "puente":
                        # Se acumula el audio y se manda al cerrar.
                        self._pcm_puente = bytearray()
                        self._aviso_largo = False
                        imprimir("[CLIENTE] Dictado por HTTPS (puente).")
                    elif self._dictado_voz:
                        # La palabra clave la oyó el motor local: no viaja en
                        # el audio, así que se pide el texto marcado. Sin `fin`:
                        # la frase de cierre la quita el propio cliente.
                        imprimir("[CLIENTE] Enviando DICTADO_START de voz al servidor...")
                        self._enviar(crear_dictado_start_voz(fin=False))
                        # Lo guardado mientras se decidía si era orden o texto.
                        # Va DESPUÉS del START: antes, el servidor lo tiraría.
                        if self._buffer_voz:
                            imprimir(
                                f"[CLIENTE] Enviando {len(self._buffer_voz)} "
                                "trozos guardados."
                            )
                            for trozo in self._buffer_voz:
                                self._enviar(crear_audio(trozo))
                            self._buffer_voz = []
                        # A partir de aquí el audio va directo al socket.
                        self._dictado_enviado = True
                    elif self._escribir_este_dictado:
                        # Dictado en vivo: el servidor manda el texto por frases
                        imprimir("[CLIENTE] Enviando DICTADO_START en vivo al servidor...")
                        self._enviar(crear_dictado_start_vivo())
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_START al servidor...")
                        self._enviar(crear_dictado_start())
                elif evento == "stop":
                    if self._transporte == "puente":
                        self._lanzar_transcripcion_puente()
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_STOP al servidor. Esperando transcripción...")
                        if self._enviar(crear_dictado_stop()):
                            self._esperando_transcripcion = True
                            self._transcripcion_inicio = time.time()
                elif isinstance(evento, tuple) and evento[:1] == ("puente_texto",):
                    # Resultado de la hebra que habló con el puente.
                    _, texto, error = evento
                    self._esperando_transcripcion = False
                    if error:
                        imprimir(f"[PUENTE] {error}")
                        esperando = self._esperando
                        self._esperando = None
                        reproducir_sonido("error")
                        if esperando not in ("orden", "consulta"):
                            _avisar_usuario("Dictado por HTTPS", error)
                    else:
                        imprimir(f"Texto detectado: {texto}")
                        if self._esperando == "orden":
                            self._esperando = None
                            self._orden_corregida(texto)
                        elif self._esperando == "consulta":
                            self._esperando = None
                            self._responder_consulta(texto)
                        else:
                            self._entregar_texto(texto)
            except queue.Empty:
                break

    # --- Escucha local de la palabra clave y bandeja ---

    @property
    def escucha_local_activa(self):
        return self._escucha_local is not None and self._escucha_local.disponible

    def alternar_escucha_local(self):
        """Enciende o apaga la escucha de la palabra clave (menú del icono)."""
        if self.escucha_local_activa:
            self._apagar_escucha_local()
        else:
            self._encender_escucha_local()
        self._actualizar_bandeja()

    def alternar_comandos(self):
        """Pausa o reanuda la ejecución de comandos de voz."""
        self.comandos_activos = not self.comandos_activos
        imprimir(
            "[VOZ] Comandos "
            + (
                "activados."
                if self.comandos_activos
                else "en pausa: lo que digas se escribe como texto."
            )
        )
        self._actualizar_bandeja()

    # --- Cuenta y perfil -------------------------------------------------

    def _cuenta_lista(self):
        """¿Se puede dictar? Sin cuenta, no (decisión del 2026-11-18)."""
        if sesion.hay_sesion():
            return True
        imprimir(
            "[CUENTA] Dictado bloqueado: no hay sesión. Entra en el icono de la "
            "bandeja → Configuración… → Cuenta."
        )
        try:
            reproducir_sonido("error")
        except Exception:
            pass
        return False

    def _bucle_sincronizacion(self):
        """Pone al día el perfil de la cuenta cada 15 minutos.

        Va en su propia hebra y nunca toca el camino del dictado: si el puente
        no responde, se sigue dictando y aprendiendo en local.
        """
        while self._activo:
            try:
                # La dirección del puente puede cambiar desde la ventana.
                self._config_cuenta = leer_configuracion()
                if sesion.hay_sesion():
                    cambios, error = perfil_cuenta.sincronizar(
                        self._config_cuenta, self._diccionario
                    )
                    if error:
                        imprimir(f"[CUENTA] No se pudo sincronizar: {error}")
                    elif cambios:
                        imprimir(f"[CUENTA] Perfil sincronizado ({cambios} cambios).")
            except Exception as e:  # nunca puede tumbar la hebra
                imprimir(f"[CUENTA] Error al sincronizar: {e}")
            for _ in range(15 * 60):
                if not self._activo:
                    return
                time.sleep(1)

    def alternar_dictado(self):
        """Empieza o termina un dictado, igual que la tecla activadora."""
        if self._grabando:
            self._dictado_voz = False
            self._grabando = False
            try:
                _ocultar_indicador()
            except Exception:
                pass
            imprimir("[DETENIDO. Procesando...]")
            reproducir_sonido("fin")
            self._cola_eventos.put("stop")
        else:
            if not self._cuenta_lista():
                return
            self._escribir_este_dictado = self._modo_salida == "escribir"
            self._dictado_voz = False
            self._grabando = True
            try:
                _mostrar_indicador()
            except Exception as e:
                imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
            imprimir("[GRABANDO... «Dictar ahora» otra vez para detener]")
            reproducir_sonido("inicio")
            self._cola_eventos.put("start")
        self._actualizar_bandeja()

    def abrir_registro(self):
        """Abre la carpeta de logs del cliente (menú del icono)."""
        base = (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__))
        )
        carpeta = os.path.join(base, "logs")
        try:
            os.startfile(carpeta)
        except Exception as e:
            _avisar_usuario("Registro", f"No se pudo abrir {carpeta}: {e}")

    def detener(self):
        """Cierra el cliente (menú del icono)."""
        imprimir("[CLIENTE] Cerrando (Salir del icono).")
        self._cierre_pedido = True
        self._activo = False

    def _encender_escucha_local(self):
        r"""Crea el detector local (Whisper) y lo deja listo.

        Whisper **no abre el micrófono**: se alimenta con los mismos chunks que el
        cliente ya captura y manda al servidor, así que no hay dos lectores
        peleando por el dispositivo. Ese era el fallo con SAPI en esta clase de
        equipos (y en esta PC, además, no hay ningún motor de reconocimiento
        instalado: `HKLM\SOFTWARE\Microsoft\Speech\Recognizers\Tokens` no
        existe).
        """
        if self.escucha_local_activa:
            return
        ruta_modelo = _nombre_modelo_whisper()
        if not ruta_modelo:
            imprimir(
                "[ESCUCHA] No se pudo cargar el modelo de Whisper "
                f"({ruta_modelo}). La palabra clave queda apagada; el "
                "dictado por tecla sigue funcionando."
            )
            self._escucha_local = None
            return
        try:
            self._escucha_local = EscuchaLocal(
                clave=self._clave,
                frase_fin=self._frases_fin,
                ruta_modelo=ruta_modelo,
            )
        except Exception as e:
            imprimir(f"[ESCUCHA] No se pudo crear la escucha local: {e}")
            self._escucha_local = None
            return
        if not self._escucha_local.disponible:
            imprimir(f"[ESCUCHA] Escucha local apagada: {self._escucha_local.error}")
            self._escucha_local = None
            return
        imprimir(
            "[ESCUCHA] escucha local activa (Whisper, "
            f"{os.path.basename(ruta_modelo)}): di «{self._clave}» para dictar."
        )

    def _apagar_escucha_local(self):
        if self._escucha_local is not None:
            self._escucha_local = None
            imprimir("[ESCUCHA] Escucha local apagada.")

    def _alimentar_escucha(self, chunk):
        """Pasa el chunk al detector y actúa según el modo en que estemos.

        - Sin nada en curso: se busca la palabra clave.
        - **Modo comando** (lo abre la clave): cada frase se resuelve aquí
          (`_resolver_comando`): orden, consulta al asistente o error.
        - **Dictado**: se busca la frase de cierre.
        - En un dictado **por tecla** no se escucha nada.
        """
        if self._escucha_local is None:
            return
        if self._grabando and not self._dictado_voz:
            return
        try:
            pico = int(np.abs(chunk.astype(np.int32)).max())
        except Exception:
            pico = 0
        if self._dictado_voz and pico >= UMBRAL_VOZ:
            self._voz_hablado = True
            self._ultima_voz_modo = time.time()
            if self._orden_pendiente is not None:
                # Después de la frase siguió hablando: lo que dijo no era una
                # orden corta («abre cromo») sino algo más largo. Lo resuelve
                # `_vigilar_fin_dictado_voz`, que cambia la espera a consulta.
                self._orden_pendiente["sigue_hablando"] = True
                self._orden_pendiente["ultima_voz"] = time.time()
        evento = self._escucha_local.alimentar(chunk)
        # Traza de lo que reconoce el motor local: sin esto, cuando algo no se
        # ejecuta no hay forma de saber si lo oyó mal o si no lo oyó.
        texto_oido = self._escucha_local.ultimo_texto
        if texto_oido and texto_oido != self._ultimo_texto_oido:
            self._ultimo_texto_oido = texto_oido
            imprimir(
                f"[ESCUCHA] «{texto_oido}» (pico {self._escucha_local.ultimo_pico})"
            )
        if evento == "clave" and not self._grabando:
            # La clave y la orden pueden venir en la MISMA frase («computadora
            # iniciar dictado»). Se lee antes de abrir la sesión, porque abrirla
            # reinicia el reconocedor y borra ese resto.
            resto = self._escucha_local.resto_tras_clave
            modo = "comando" if self.comandos_activos else "dictado"
            if modo == "comando":
                imprimir("[VOZ] Palabra clave detectada; modo comando.")
            else:
                # Comandos en pausa desde el icono: la clave dicta directamente.
                imprimir("[VOZ] Palabra clave detectada (comandos en pausa).")
            self._al_detectar_clave(self._clave, modo=modo)
            if resto and self._modo_comando:
                imprimir(f"[VOZ] Y en la misma frase se oyó: «{resto}».")
                self._escucha_local.ultimo_texto = resto
                self._resolver_comando("orden" if es_comando(resto) else "frase")
        elif evento == "fin":
            if self._dictado_voz and not self._modo_comando:
                self._al_cerrar_clave(self._escucha_local.cierre_detectado)
            else:
                # Un cierre oído fuera de un dictado se ignora, pero **dejando
                # rastro**: tragárselo en silencio hacía parecer que no se oía
                # nada, y así se perdió una sesión de diagnóstico (2026-11-05).
                imprimir(
                    "[VOZ] Se oyó un cierre («%s») y no hay dictado abierto; "
                    "se ignora." % (self._escucha_local.cierre_detectado or "?")
                )
        elif evento == "comando" and self._modo_comando:
            self._resolver_comando("orden")
        elif evento == "frase" and self._modo_comando:
            self._resolver_comando("frase")

    def _resolver_comando(self, clase):
        """Resuelve una frase oída dentro del modo comando.

        `clase` es «orden» (el motor local la reconoció como verbo o frase de
        sistema) o «frase» (cualquier otra cosa). Cuatro salidas:

        - **dictado**: se dijo «iniciar dictado» → se pasa a transcribir texto.
        - **corto**: el argumento está en `comandos_windows.json` («abre
          calculadora») → se ejecuta aquí, suena `orden` y al servidor no se le
          manda nada.
        - **consulta local**: saludos y la hora (`voz_asistente`), sin gastar API.
        - **espera**: lo demás se queda esperando **silencio** de
          `ORDEN_SILENCIO_SEGUNDOS`; al llegar, el audio se manda al servidor
          para que corrija el argumento de la orden o para que el asistente
          responda la consulta.
        """
        texto = (
            self._escucha_local.ultimo_texto
            if self._escucha_local is not None else ""
        )
        if not (texto or "").strip():
            return

        # «Iniciar dictado» y sus variantes: se cambia de modo.
        accion, argumento = parsear(texto)
        if clase == "orden" and accion and not argumento:
            destino = resolver(accion, argumento)
            if destino is not None and destino[0] == "dictar":
                self._abrir_dictado_desde_comando()
                return

        orden = self._orden_local(texto)
        if orden is not None:
            accion, argumento = orden
            imprimir(
                f"[VOZ] Orden local: «{accion}» → "
                f"«{argumento or '(sin argumento)'}»; no se envía nada al "
                "servidor."
            )
            self._terminar_dictado_voz("orden local", enviar=False, sonido=None)
            threading.Thread(
                target=_ejecutar_y_avisar, args=(accion, argumento), daemon=True
            ).start()
            return

        # Saludos y preguntas de reloj: se contestan aquí, sin gastar API.
        simple = buscar_comando_simple(texto)
        if simple:
            imprimir(f"[VOZ] Respuesta local a «{texto}».")
            self._terminar_dictado_voz("respuesta local", enviar=False, sonido=None)
            threading.Thread(
                target=_hablar_respuesta, args=(simple,), daemon=True
            ).start()
            return

        palabras = len((argumento or "").split())
        if clase == "orden" and palabras > ORDEN_MAX_PALABRAS_ARGUMENTO:
            # «abre cromo azul y lo pinto de verde»: no es una orden corta, es
            # una frase. Se trata como consulta.
            clase = "consulta"
        elif clase == "orden":
            # Orden con 0-2 palabras de argumento que el catálogo no conoce.
            clase = "orden"
        else:
            clase = "consulta"

        imprimir(
            f"[VOZ] «{texto}» → {clase}; espero "
            f"{ORDEN_SILENCIO_SEGUNDOS:.0f} s de silencio para mandarla al "
            "servidor."
        )
        ahora = time.time()
        self._orden_pendiente = {
            "texto": texto,
            "clase": clase,
            "ultima_voz": ahora,
            "sigue_hablando": False,
        }

    def _abrir_dictado_desde_comando(self):
        """Se dijo «iniciar dictado»: se pasa a transcribir texto.

        Se descarta lo hablado hasta ahora (la clave y el propio comando: no
        deben llegar a Whisper) y se pide el `DICTADO_START`. Se cierra como
        siempre, con una de las frases de cierre.
        """
        imprimir("[VOZ] Comando «iniciar dictado»: se abre el dictado.")
        reproducir_sonido("inicio")
        self._modo_comando = False
        self._orden_pendiente = None
        self._buffer_voz = []
        self._voz_empezo = time.time()
        self._ultima_voz_modo = self._voz_empezo
        # Ya se habló (el comando), así que el seguro de «no se oyó nada» no
        # debe cerrar el dictado enseguida.
        self._voz_hablado = True
        self._pasar_a_servidor()

    def _orden_local(self, texto):
        """Resuelve la orden con lo que oyó el motor local, sin el servidor.

        Devuelve `(accion, argumento)` listo para `ejecutar_comando()`, o None
        si hay que preguntarle al servidor: el argumento tiene que estar en el
        catálogo de `comandos_windows.json`, si no es que se oyó mal.
        """
        accion, argumento = parsear(texto)
        if not accion:
            return None
        traducida = resolver(accion, argumento)
        if traducida is None:
            return None
        accion_local, argumento_local = traducida

        if accion_local == "cerrar":
            if _buscar_programa("cerrar", argumento_local, avisar=False):
                return "cerrar", argumento_local
            return None

        if accion_local == "abre":
            if _buscar_programa("abre", argumento_local, avisar=False):
                return "abre", argumento_local
            # No es un programa: ¿una página web? («abre youtube»).
            sitio = _resolver_sitio(argumento_local)
            if sitio:
                return "web", sitio
            return None

        if accion_local == "web":
            sitio = _resolver_sitio(argumento_local)
            if sitio:
                return "web", sitio
            return None

        if accion_local == "buscar":
            if (argumento_local or "").strip():
                return "buscar", argumento_local
            return None

        return accion_local, argumento_local

    def _orden_corregida(self, texto):
        """Ejecuta (o rechaza) la orden que el servidor devolvió corregida.

        Solo se llama cuando el cliente le mandó el audio para que corrigiera
        el argumento de una orden («abre cromo»). El texto vuelve **limpio** —el
        servidor ya no marca comandos—, así que se vuelve a interpretar aquí: si
        sale una orden que el catálogo conoce, se ejecuta y suena `orden`; si no,
        suena `error` y la razón queda en el registro. En ninguno de los dos
        casos se escribe nada en el documento: ahí iría basura.
        """
        texto = quitar_frase_cierre(texto)
        if not self.comandos_activos:
            imprimir("[VOZ] Comandos en pausa: se escribe lo que dictó.")
            self._entregar_texto(texto)
            return

        accion, argumento = parsear(texto)
        traducida = resolver(accion, argumento) if accion else None
        if traducida is None:
            imprimir(
                f"[VOZ] El servidor devolvió «{texto}», que no empieza por una "
                "orden conocida; no se escribe nada."
            )
            reproducir_sonido("error")
            return

        # La misma resolución que el camino corto: programas, sitios web,
        # búsquedas y acciones del sistema.
        orden = self._orden_local(texto)
        if orden is None:
            imprimir(
                f"[VOZ] El servidor devolvió «{texto}» y el argumento no está "
                "en ningún catálogo de comandos."
            )
            reproducir_sonido("error")
            return

        accion_local, argumento_local = orden

        imprimir(
            f"[VOZ] Orden corregida por el servidor: «{accion_local}» → "
            f"«{argumento_local or '(sin argumento)'}»"
        )
        # `avisar=False`: el aviso de un fallo queda en el registro, sin diálogo.
        threading.Thread(
            target=_ejecutar_y_avisar,
            args=(accion_local, argumento_local, False),
            daemon=True,
        ).start()

    def _responder_consulta(self, texto):
        """Contesta una frase que no era un comando.

        La transcripción viene de Whisper (el audio se mandó al servidor), así
        que la pregunta llega bien escrita. Primero se miran los comandos
        simples de `voz_asistente` (la hora, el día, los saludos), que no gastan
        API; si no hay coincidencia, se le pregunta a DeepSeek. La respuesta se
        **habla**: no se escribe nada en el documento.
        """
        texto = quitar_frase_cierre(texto).strip()
        imprimir(f"[VOZ] Consulta: «{texto}»")
        if not texto:
            imprimir("[VOZ] La consulta llegó vacía; no hay nada que preguntar.")
            reproducir_sonido("error")
            return

        # Red de seguridad: el camino se eligió al vuelo, pero lo que devolvió
        # Whisper puede empezar por una orden («Iniciar dictado. Probando 1, 2,
        # 3»). Se prueba como comando antes de molestar a la IA.
        orden = self._orden_local(texto)
        if orden is not None:
            accion_local, argumento_local = orden
            if accion_local == "dictar":
                imprimir(
                    "[VOZ] Era «iniciar dictado», no una consulta: abro el "
                    "dictado."
                )
                self._al_detectar_clave(self._clave, modo="dictado")
                return
            imprimir(
                f"[VOZ] Era una orden, no una consulta: «{accion_local}» → "
                f"«{argumento_local or '(sin argumento)'}»"
            )
            threading.Thread(
                target=_ejecutar_y_avisar,
                args=(accion_local, argumento_local, False),
                daemon=True,
            ).start()
            return

        respuesta = buscar_comando_simple(texto)
        if respuesta is None:
            respuesta = preguntar_deepseek(texto)
        if not respuesta:
            imprimir(
                "[VOZ] Sin respuesta: falta DEEPSEEK_API_KEY, no hay internet o "
                "la consulta tardó más de 15 s."
            )
            reproducir_sonido("error")
            return
        imprimir(f"[VOZ] Respuesta: {respuesta}")
        threading.Thread(
            target=_hablar_respuesta, args=(respuesta,), daemon=True
        ).start()

    def _al_detectar_clave(self, texto="", modo="comando"):
        """El motor local oyó la palabra clave: abre la sesión.

        `modo="comando"` (lo normal): queda esperando una orden; no se manda
        nada al servidor todavía. `modo="dictado"` es lo que pasa con los
        comandos en pausa desde el icono: dicta texto directamente.
        """
        if self._grabando:
            imprimir("[VOZ] Ya hay un dictado en curso; la clave se ignora.")
            return
        if not self._cuenta_lista():
            return
        self._escribir_este_dictado = self._modo_salida == "escribir"
        self._dictado_voz = True
        self._voz_empezo = time.time()
        self._ultima_voz_modo = self._voz_empezo
        self._voz_hablado = False
        self._frase_cierre = ""
        self._orden_pendiente = None
        self._esperando = None
        self._modo_comando = modo == "comando"
        self._grabando = True
        if self._modo_comando:
            imprimir(
                f"[VOZ] Palabra clave «{texto or self._clave}» → modo comando "
                "(di la orden o «iniciar dictado»)."
            )
        else:
            imprimir(f"[VOZ] Palabra clave «{texto or self._clave}» → dictado.")
        reproducir_sonido("inicio")
        try:
            _mostrar_indicador()
        except Exception as e:
            imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
        # En modo comando NO se le dice nada al servidor: primero hay que saber
        # si lo que viene es una orden, una consulta o un dictado.
        self._dictado_enviado = False
        self._start_encolado = False
        self._buffer_voz = []
        self._actualizar_bandeja()

    def _al_cerrar_clave(self, texto=""):
        """El motor local oyó la frase de cierre: se cierra el dictado.

        Se guarda **qué** frase se oyó: al llegar el texto hay que quitarla del
        final, y el servidor ya no sabe de ella (no se le manda `fin`).
        """
        frase = (texto or "").strip()
        if self._escucha_local is not None:
            frase = self._escucha_local.cierre_detectado or frase
        self._frase_cierre = frase
        self._terminar_dictado_voz(f"«{frase}»" if frase else "frase de cierre")

    def _vigilar_fin_dictado_voz(self, pico):
        """Resuelve la espera pendiente y cierra la sesión de voz.

        - Con una frase esperando: al cumplirse `ORDEN_SILENCIO_SEGUNDOS` se
          manda al servidor —la orden, para que corrija el argumento, o la
          consulta, para que responda el asistente—. Si el usuario siguió
          hablando, lo dicho ya no es una orden corta: pasa a consulta.
        - **Dictado**: se cierra con la frase de cierre, con 6 s sin voz o con
          el tope de 180 s.
        - **Modo comando**: se cierra con 6 s sin voz o con el tope de 15 s. En
          ese caso no se transcribe nada: no había comando.
        """
        if not (self._dictado_voz and self._grabando):
            return
        ahora = time.time()
        if pico >= UMBRAL_VOZ:
            self._voz_hablado = True

        if self._orden_pendiente is not None:
            pendiente = self._orden_pendiente
            if pendiente["sigue_hablando"] and pendiente["clase"] == "orden":
                imprimir(
                    f"[VOZ] Después de «{pendiente['texto']}» siguió hablando: "
                    "es una consulta, no una orden corta."
                )
                pendiente["clase"] = "consulta"
            if ahora - pendiente["ultima_voz"] >= ORDEN_SILENCIO_SEGUNDOS:
                clase = pendiente["clase"]
                imprimir(
                    f"[VOZ] {ORDEN_SILENCIO_SEGUNDOS:.0f} s de silencio tras "
                    f"«{pendiente['texto']}»: {clase} al servidor."
                )
                self._orden_pendiente = None
                self._esperando = clase
                # Sin pitido al cerrar: el aviso es el `orden`, el `texto` de la
                # respuesta o el `error`, y los pone el servidor al contestar.
                self._terminar_dictado_voz(
                    f"{clase} por silencio (al servidor)", sonido=None
                )
                return

        if self._modo_comando:
            if ahora - self._ultima_voz_modo > MODO_COMANDO_MAX_SEGUNDOS:
                self._cerrar_modo_comando(
                    f"{MODO_COMANDO_MAX_SEGUNDOS:.0f} s sin oír nada más"
                )
            return

        if ahora - self._voz_empezo >= DICTADO_VOZ_MAX_SEGUNDOS:
            self._terminar_dictado_voz("tope de tiempo")
        elif (not self._voz_hablado
              and ahora - self._voz_empezo >= DICTADO_VOZ_SIN_VOZ_SEGUNDOS):
            self._terminar_dictado_voz("no se oyó nada")

    def _cerrar_modo_comando(self, motivo):
        """Cierra el modo comando sin transcribir nada (disparo por error).

        A diferencia de un dictado, aquí no hay texto que mandar: si nadie dijo
        un comando, al servidor no se le pide nada. Se avisa con el pitido de
        error porque la palabra clave no llevó a ninguna parte.
        """
        imprimir(
            f"[VOZ] Modo comando cerrado: {motivo} "
            f"(abierto hace {time.time() - self._voz_empezo:.1f} s)."
        )
        self._terminar_dictado_voz(motivo, enviar=False, sonido="error")

    def _pasar_a_servidor(self):
        """Decide que esto es texto: se pide el dictado al servidor.

        El audio guardado se suelta en `_procesar_eventos`, **después** del
        `DICTADO_START`: si llegara antes, el servidor lo descartaría. Por eso
        el audio sigue guardándose hasta que el evento se procesa.
        """
        if self._start_encolado:
            return
        self._start_encolado = True
        imprimir(
            f"[VOZ] Es texto: se envía al servidor con los "
            f"{len(self._buffer_voz)} trozos guardados."
        )
        self._cola_eventos.put("start")

    def _terminar_dictado_voz(self, motivo="", enviar=True, sonido="fin"):
        """Cierra el dictado de voz.

        `enviar=False` es el camino corto: la orden ya se ejecutó aquí y al
        servidor no se le manda nada, ni el audio guardado.
        """
        if not self._dictado_voz:
            return
        self._dictado_voz = False
        self._grabando = False
        self._voz_hablado = False
        self._modo_comando = False
        self._orden_pendiente = None
        detalle = f" ({motivo})" if motivo else ""
        imprimir(f"[VOZ] Dictado terminado{detalle}. Procesando...")
        # Confirmación inmediata: el texto todavía tiene que transcribirse en el
        # servidor (2-10 s). Este pitido dice «te oí, cierro»; el pip de «texto»
        # llegará cuando esté en el portapapeles.
        reproducir_sonido(sonido)
        try:
            _ocultar_indicador()
        except Exception:
            pass
        if enviar:
            # Si aún no se había pedido el dictado, lo que hay es texto: se
            # manda ahora, con lo que se guardó durante la decisión.
            self._pasar_a_servidor()
            self._cola_eventos.put("stop")
        else:
            self._buffer_voz = []
        self._actualizar_bandeja()

    def _actualizar_bandeja(self):
        """Pone el color del icono según lo que esté pasando."""
        if self._bandeja is None:
            return
        if self._grabando:
            estado = "grabando"
        elif not self.escucha_local_activa:
            estado = "apagado"
        elif not self.comandos_activos:
            estado = "pausado"
        else:
            estado = "escuchando"
        self._bandeja.actualizar(estado)

    def _escribir_en_cursor(self, texto):
        """Escribe el texto en la ventana activa, donde esté el cursor.

        Se hace copiando al portapapeles y simulando Ctrl+V: es lo único que
        respeta tildes, eñes y mayúsculas en cualquier aplicación (escribir
        letra por letra con pyautogui falla con á, é, í, ó, ú, ñ y en otros
        idiomas/teclados).
        """
        try:
            pyperclip.copy(texto + " ")
        except Exception as e:
            _avisar_usuario(
                "No se pudo escribir",
                f"No se pudo copiar el texto al portapapeles: {e}",
            )
            return

        # El Ctrl+V que enviamos también lo verá el listener de pynput: se
        # marca _inyectando para que no lo tome como tecla activadora.
        self._inyectando = True
        try:
            time.sleep(0.15)  # que la tecla activadora termine de soltarse
            pyautogui.hotkey("ctrl", "v")
            imprimir("[ESCRIBIR] Texto pegado donde estaba el cursor (Ctrl+V).")
        except Exception as e:
            imprimir(f"[ESCRIBIR] No se pudo pegar con Ctrl+V: {e}")
            _avisar_usuario(
                "No se pudo escribir",
                "El texto quedó en el portapapeles: pégalo con Ctrl+V. "
                f"({e})",
            )
        finally:
            self._inyectando = False

    def _bucle_escritura(self):
        """Escribe en el cursor, en orden, los textos dictados.

        Una sola hebra: las frases del dictado en vivo llegan seguidas y hay
        que pegarlas en el mismo orden (si se lanzara una hebra por frase,
        podrían adelantarse unas a otras).
        """
        while self._activo:
            try:
                texto = self._cola_escritura.get(timeout=0.2)
            except queue.Empty:
                continue
            self._escribir_en_cursor(texto)


# ---------------------------------------------------------------------------
# Instalación como cliente dedicado (autostart en Windows)
# ---------------------------------------------------------------------------

def _mensaje_windows(titulo, texto):
    """Muestra un cuadro de diálogo en Windows (o lo registra en log en Linux)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, texto, titulo, 0x40)
            return
        except Exception:
            pass
    imprimir(f"{titulo}: {texto}")


def _avisar_usuario(titulo, texto):
    """Avisa al usuario de un problema.

    Escribe siempre en el log y, además, muestra un cuadro de diálogo en
    Windows: el ejecutable del cliente (ClienteVoz.exe) se compila sin
    consola (console=False), así que de otro modo el usuario no vería
    ningún mensaje cuando el dictado falla.
    """
    imprimir(f"[AVISO] {titulo}: {texto}")
    # Un aviso es un problema: se oye aunque no se esté mirando la pantalla.
    # Es un tono distinto del inicio, del cierre y del texto listo.
    reproducir_sonido("error")
    if sys.platform == "win32":
        # En un hilo aparte: el cuadro de diálogo es modal y no debe
        # detener el bucle principal del cliente.
        threading.Thread(
            target=_mensaje_windows, args=(titulo, texto), daemon=True
        ).start()


def _probar_microfono(segundos=5.0):
    """Graba del micrófono predeterminado y muestra el nivel captado.

    Sirve para comprobar en unos segundos si el equipo entrega señal de
    audio: en Windows el cliente no tiene consola y un micrófono muteado,
    desconectado o equivocado no produce ningún error visible.

    Uso: ClienteVoz.exe --probar-microfono [segundos]
    """
    try:
        info = sd.query_devices(kind="input")
        imprimir(
            f"[MIC] Dispositivo de entrada: {info['name']} "
            f"(frecuencia por defecto {info['default_samplerate']:.0f} Hz)"
        )
    except Exception as e:
        _avisar_usuario(
            "Prueba de micrófono",
            f"No hay dispositivo de entrada disponible: {e}",
        )
        return

    total = max(1, int(FRECUENCIA * segundos))
    imprimir(f"[MIC] Grabando {total / FRECUENCIA:.0f} s... habla ahora.")
    try:
        grabacion = sd.rec(total, samplerate=FRECUENCIA, channels=1, dtype="int16")
        sd.wait()
    except Exception as e:
        _avisar_usuario("Prueba de micrófono", f"No se pudo grabar: {e}")
        return

    audio = np.asarray(grabacion, dtype=np.int16).reshape(-1)
    if audio.size:
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        pico = int(np.abs(audio.astype(np.int32)).max())
    else:
        rms, pico = 0.0, 0

    if pico < 50:
        mensaje = (
            f"Casi no se captó señal (pico={pico}, nivel={rms:.0f}). Revisa el "
            "micrófono: puede estar muteado, desconectado o ser otro "
            "dispositivo de entrada distinto al que usas."
        )
    else:
        mensaje = f"Micrófono OK: pico={pico}, nivel={rms:.0f}."
    imprimir(f"[MIC] {mensaje}")
    _avisar_usuario("Prueba de micrófono", mensaje)


def _comando_autostart():
    """Comando que se registrará en el autostart (Task Scheduler)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cliente_voz.py")
    return f'"{python}" "{script}"'


def _probar_escucha(segundos=20.0):
    """Detecta la palabra clave con Whisper, sin arrancar el cliente.

    Abre el micrófono con `sounddevice` (el mismo camino que el cliente) y le
    pasa los chunks al detector. No usa el motor de voz de Windows: SAPI necesita
    un motor de reconocimiento instalado y, además, se pelea por el micrófono
    con la captura del cliente.

    Uso: ClienteVoz.exe --probar-escucha [segundos]
    """
    clave, frases_fin = cargar_palabras_escucha()
    ruta_modelo = _nombre_modelo_whisper()

    escucha = EscuchaLocal(
        clave=clave, frase_fin=frases_fin, ruta_modelo=ruta_modelo
    )
    if not escucha.disponible:
        _mensaje_windows(
            "Prueba de escucha",
            "No se pudo cargar el modelo de Whisper "
            f"({ruta_modelo}):\n\n{escucha.error}",
        )
        return

    imprimir(
        f"[PRUEBA] Whisper cargado ({os.path.basename(ruta_modelo)}). Di «{clave}» "
        f"y después «{frases_fin[0]}» en los próximos {segundos:.0f} s."
    )
    detectadas = []

    def procesar(indata, frames, time_info, status):
        evento = escucha.alimentar(indata)
        if evento == "clave":
            detectadas.append(clave)
            imprimir("[PRUEBA] Palabra clave oída.")
            reproducir_sonido("inicio")
        elif evento == "fin":
            imprimir("[PRUEBA] Frase de cierre oída.")
            reproducir_sonido("fin")

    try:
        with sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=1024,
            callback=procesar,
        ):
            time.sleep(segundos)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _mensaje_windows("Prueba de escucha", f"No se pudo abrir el micrófono: {e}")
        return

    if detectadas:
        detalle = (
            "Funciona. Puedes encender «Escucha continua» en el icono de la "
            "bandeja o poner \"escucha_local\": true en comandos_windows.json."
        )
    else:
        detalle = (
            "No se oyó la palabra clave. Revisa que el micrófono predeterminado "
            "sea el que usas, que no esté muteado y que el modelo esté copiado "
            "junto al ClienteVoz.exe."
        )
    _mensaje_windows(
        "Prueba de escucha",
        f"Se oyó la palabra clave {len(detectadas)} vez/veces.\n\n{detalle}",
    )


class _ClienteFalso:
    """Sustituto mínimo del cliente para probar el icono sin arrancar nada."""

    escucha_local_activa = False
    comandos_activos = True

    def alternar_escucha_local(self):
        self.escucha_local_activa = not self.escucha_local_activa
        imprimir(f"[PRUEBA] Escucha continua: {self.escucha_local_activa}")

    def alternar_comandos(self):
        self.comandos_activos = not self.comandos_activos
        imprimir(f"[PRUEBA] Comandos por voz: {self.comandos_activos}")

    def alternar_dictado(self):
        imprimir("[PRUEBA] «Dictar ahora» pulsado.")

    def abrir_registro(self):
        imprimir("[PRUEBA] «Ver registro» pulsado.")

    def detener(self):
        imprimir("[PRUEBA] «Salir» pulsado.")


def _probar_bandeja(segundos=30.0):
    """Pone el icono de la bandeja sin abrir audio ni sockets.

    Sirve para saber si el icono funciona en esta PC: si aparece, el problema
    está en otra parte del cliente; si no aparece, el aviso dice qué falta.

    En Windows 11 los iconos nuevos se quedan escondidos dentro de la flecha ^
    del área de notificación hasta que se activan en Configuración →
    Personalización → Barra de tareas → Otros iconos del sistema.
    """
    cliente = _ClienteFalso()
    bandeja = iniciar_en_hilo(cliente, al_fallar=_avisar_usuario)
    imprimir(
        "[PRUEBA] Icono puesto: búscalo en el área de notificación (Windows 11: "
        f"dentro de la flecha ^). Se retira en {segundos:.0f} s."
    )
    try:
        time.sleep(segundos)
    except KeyboardInterrupt:
        pass
    finally:
        bandeja.detener()
    _mensaje_windows(
        "Prueba de la bandeja",
        "Si viste el icono (un círculo con un micrófono), el icono funciona.\n\n"
        "En Windows 11 los iconos nuevos se esconden dentro de la flecha ^ del "
        "área de notificación: se activan en Configuración → Personalización → "
        "Barra de tareas → Otros iconos del sistema.",
    )


def _avisar_instalacion(titulo, texto):
    """Aviso de instalación: cuadro de diálogo, salvo con `--silencioso`.

    El instalador de Inno Setup llama a `ClienteVoz.exe --instalar
    --silencioso` y `--desinstalar --silencioso`: así no aparece un diálogo
    modal que detenga la instalación (y no la rompe si va desatendida).
    """
    if "--silencioso" in sys.argv:
        imprimir(f"{titulo}: {texto.replace(chr(10), ' ')}")
        return
    _mensaje_windows(titulo, texto)


def _ruta_inicio_usuario():
    """Carpeta «Inicio» del usuario (arranque al iniciar sesión, sin admin)."""
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )


def _crear_acceso_inicio():
    """Deja un acceso directo en la carpeta Inicio y devuelve su ruta.

    Se usa esto en vez de `schtasks`: crear una tarea con disparador ONLOGON
    devuelve «acceso denegado» cuando el proceso no es elevado, y el instalador
    corre **sin administrador** a propósito. La carpeta Inicio del usuario
    siempre es escribible (2026-11-19).
    """
    if not getattr(sys, "frozen", False):
        # En modo fuente no se toca el arranque del sistema.
        return ""
    ejecutable = sys.executable
    carpeta = _ruta_inicio_usuario()
    destino = os.path.join(carpeta, "SkipKey.lnk")
    if not os.environ.get("APPDATA"):
        return ""
    orden = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"
        + destino.replace("'", "''")
        + "');$s.TargetPath='"
        + ejecutable.replace("'", "''")
        + "';$s.WorkingDirectory='"
        + os.path.dirname(ejecutable).replace("'", "''")
        + "';$s.Save()"
    )
    try:
        os.makedirs(carpeta, exist_ok=True)
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", orden],
            capture_output=True,
            text=True,
        )
        if resultado.returncode == 0 and os.path.exists(destino):
            imprimir(f"[ARRANQUE] Acceso de Inicio creado: {destino}")
            return destino
        imprimir(
            "[ARRANQUE] No se pudo crear el acceso de Inicio: "
            f"{resultado.stderr.strip() or resultado.stdout.strip()}"
        )
    except Exception as e:
        imprimir(f"[ARRANQUE] No se pudo crear el acceso de Inicio: {e}")
    return ""


def _instalar_autostart():
    _asegurar_config()
    # Limpia la tarea de instalaciones anteriores: era la que fallaba sin
    # elevación y la que hacía que el cliente no arrancara con Windows.
    subprocess.run(
        ["schtasks", "/Delete", "/TN", "ClienteVoz", "/F"],
        capture_output=True,
        text=True,
    )
    acceso = _crear_acceso_inicio()
    if acceso:
        _avisar_instalacion(
            "ClienteVoz",
            "Cliente de dictado instalado correctamente.\n\n"
            "Arrancará al iniciar sesión (acceso directo en la carpeta Inicio).\n"
            f"Servidor: {_servidor_por_defecto()}\n\n"
            "Para desinstalar: ClienteVoz.exe --desinstalar",
        )
    else:
        _avisar_instalacion(
            "ClienteVoz",
            "Instalado, pero NO se pudo dejar el arranque automático.\n\n"
            "Crea a mano un acceso directo a este programa en Win+R → shell:startup.",
        )


def _desinstalar_autostart():
    ruta = os.path.join(_ruta_inicio_usuario(), "SkipKey.lnk")
    quitado = False
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
            quitado = True
            imprimir(f"[ARRANQUE] Acceso de Inicio borrado: {ruta}")
    except OSError as e:
        imprimir(f"[ARRANQUE] No se pudo borrar {ruta}: {e}")
    resultado = subprocess.run(
        ["schtasks", "/Delete", "/TN", "ClienteVoz", "/F"],
        capture_output=True,
        text=True,
    )
    if quitado or resultado.returncode == 0:
        _avisar_instalacion("ClienteVoz", "Arranque automático desinstalado.")
    else:
        _avisar_instalacion(
            "ClienteVoz",
            "No se encontró el arranque automático (no había nada que quitar).",
        )


# ---------------------------------------------------------------------------
# Robustez del arranque (2026-11-19)
# ---------------------------------------------------------------------------

_CERROJO_INSTANCIA = None


def _registrar_excepcion(tipo, valor, traza):
    """Escribe en el registro las excepciones que nadie atrapa.

    `ClienteVoz.exe` se compila sin consola (`console=False`): sin esto, un
    fallo mata el proceso y no deja ni una línea en el log, que es lo que hacía
    imposible saber por qué «dejaba de funcionar solo» (2026-11-19).
    """
    try:
        texto = "".join(traceback.format_exception(tipo, valor, traza))
        imprimir("[ERROR NO CONTROLADO] " + texto.replace("\n", " | "))
    except Exception:
        pass


def _registrar_excepcion_hilo(args):
    """Lo mismo para las hebras (`threading.excepthook`)."""
    _registrar_excepcion(args.exc_type, args.exc_value, args.exc_traceback)


def _instalar_captura_de_errores():
    """Apunta los enganches de excepción al registro del cliente."""
    sys.excepthook = _registrar_excepcion
    threading.excepthook = _registrar_excepcion_hilo


def _instancia_unica_disponible():
    """¿Se puede arrancar? Deja un cerrojo para que no corran dos clientes.

    Dos copias (la de la tarea programada y otra abierta a mano) se pelean por
    el micrófono y por el icono de la bandeja, y una de las dos acaba cayéndose
    (visto el 2026-11-19). En Windows se usa un mutex con nombre; fuera de
    Windows no hace falta (systemd ya garantiza una sola instancia).
    """
    global _CERROJO_INSTANCIA
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _CERROJO_INSTANCIA = kernel32.CreateMutexW(None, False, "SkipKeyClienteVoz")
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(_CERROJO_INSTANCIA))
            _CERROJO_INSTANCIA = None
            return False
        return True
    except Exception as e:  # si falla la comprobación, se arranca igual
        imprimir(f"[CLIENTE] No se pudo comprobar la instancia única: {e}")
        return True


def _bucle_cliente(direccion):
    """Arranca el cliente y lo relanza si muere por un fallo.

    Un `.exe` sin consola que se cae deja la PC muda sin avisar. Mientras el
    cierre no se pida desde el icono, se vuelve a arrancar con espera
    creciente; solo se rinde si cae 5 veces seguidas en menos de 30 s, para no
    entrar en un bucle de reinicios.
    """
    rapidos = 0
    while True:
        cliente = ClienteVoz(direccion)
        inicio = time.time()
        cliente.ejecutar()
        if cliente._cierre_pedido:
            imprimir("[CLIENTE] Cierre pedido: no se relanza.")
            return
        duracion = time.time() - inicio
        rapidos = rapidos + 1 if duracion < 30 else 0
        if rapidos >= 5:
            imprimir(
                f"[CLIENTE] ⚠ El cliente cayó {rapidos} veces seguidas en menos "
                "de 30 s; no se relanza. Revisa el registro."
            )
            _avisar_usuario(
                "SkipKey se detuvo",
                "El cliente se cerró varias veces seguidas. Revisa el registro "
                "(icono de la bandeja → Ver registro) y vuelve a abrirlo.",
            )
            return
        espera = min(10 * max(rapidos, 1), 60)
        imprimir(
            "[CLIENTE] ⚠ El cliente se detuvo por un fallo. Se relanza en "
            f"{espera} s (intento {rapidos + 1}/5)."
        )
        time.sleep(espera)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _instalar_captura_de_errores()

    args = sys.argv[1:]

    # ⚠️ La rotación VACÍA el registro. Solo se hace cuando de verdad arranca
    # el cliente: la ventana de configuración y las pruebas se lanzan como un
    # proceso más del propio `.exe`, y antes trituraban el registro del cliente
    # que estaba corriendo (2026-11-19). Por eso el registro «perdía» líneas.
    _MODOS_AYUDA = (
        "--instalar",
        "--desinstalar",
        "--probar-microfono",
        "--probar-escucha",
        "--probar-bandeja",
        "--configuracion",
        # `--indicador` es el proceso hijo que dibuja el recuadro «REC»: también
        # tiene que saltarse el cerrojo. Sin esto el hijo creía que había otra
        # copia, mostraba «El cliente ya está en ejecución» y se cerraba, así
        # que el símbolo de grabación **nunca** aparecía (2026-11-19).
        "--indicador",
    )
    if not any(modo in args for modo in _MODOS_AYUDA):
        # El cerrojo va ANTES de rotar: una segunda copia no debe vaciar el
        # registro del cliente que ya está corriendo.
        if not _instancia_unica_disponible():
            imprimir(
                "[CLIENTE] Ya hay otra copia del cliente en marcha; esta se "
                "cierra sin tocar el registro."
            )
            _mensaje_windows(
                "SkipKey",
                "El cliente ya está en ejecución.\n\n"
                "Para cerrarlo: icono del área de notificación → botón derecho → "
                "Salir.",
            )
            sys.exit(0)
        rotar_logs_al_iniciar()
    imprimir(f"=== CLIENTE DE SKIPKEY {leer_version()} === ({' '.join(args) or 'sin argumentos'})")

    # Modos de instalación del cliente dedicado (Windows)
    if "--instalar" in args:
        _instalar_autostart()
        sys.exit(0)
    if "--desinstalar" in args:
        _desinstalar_autostart()
        sys.exit(0)

    # Diagnóstico del micrófono: no arranca el cliente, solo prueba el audio
    if "--probar-microfono" in args:
        indice = args.index("--probar-microfono")
        segundos = 5.0
        if indice + 1 < len(args):
            try:
                segundos = float(args[indice + 1])
            except ValueError:
                pass
        _probar_microfono(segundos)
        sys.exit(0)

    # Diagnóstico de la escucha local: solo oye la palabra clave y la muestra
    if "--probar-escucha" in args:
        indice = args.index("--probar-escucha")
        segundos = 20.0
        if indice + 1 < len(args):
            try:
                segundos = float(args[indice + 1])
            except ValueError:
                pass
        _probar_escucha(segundos)
        sys.exit(0)

    # Diagnóstico del icono de la bandeja: no arranca el cliente, solo el icono
    if "--probar-bandeja" in args:
        indice = args.index("--probar-bandeja")
        segundos = 30.0
        if indice + 1 < len(args):
            try:
                segundos = float(args[indice + 1])
            except ValueError:
                pass
        _probar_bandeja(segundos)
        sys.exit(0)

    # Ventana de configuración (la abre el icono de la bandeja, y también el
    # propio .exe: en el ejecutable congelado no hay un .py que lanzar).
    if "--configuracion" in args:
        import ventana_configuracion

        ventana_configuracion.main()
        sys.exit(0)

    # Ventana flotante «REC». Va en su propio proceso, igual que la
    # configuración: el cliente **no** crea Tkinter dentro de sí mismo
    # (2026-11-19).
    if "--indicador" in args:
        import indicador_grabacion

        sys.exit(indicador_grabacion.main())

    _asegurar_config()

    direccion = None
    for i, arg in enumerate(args):
        if arg == "--servidor" and i + 1 < len(args):
            direccion = f"tcp://{args[i + 1]}"
        elif arg.startswith("--servidor="):
            direccion = f"tcp://{arg.split('=', 1)[1]}"
    if direccion is None:
        direccion = f"tcp://{_servidor_por_defecto()}"

    _bucle_cliente(direccion)
