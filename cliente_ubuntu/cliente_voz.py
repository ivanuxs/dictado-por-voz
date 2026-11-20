"""Cliente de SkipKey — ZMQ DEALER.

Captura audio del micrófono, lo envía al servidor, recibe texto transcrito
y comandos de voz, y los ejecuta localmente (inyección de texto, apertura
de aplicaciones, control de volumen, TTS, etc.).
"""
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote_plus

# `--ayuda` se atiende ANTES de importar pyautogui/zmq: esas bibliotecas
# necesitan pantalla y, sin ella (SSH, servidor), el import revienta con
# `KeyError: 'DISPLAY'`. El manual tiene que poder leerse en cualquier sitio.
if __name__ == "__main__" and any(
    a in sys.argv[1:] for a in ("--ayuda", "--help", "-h")
):
    _bases = []
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        _bases.append(os.path.dirname(sys.executable))
    _bases.append(os.path.dirname(os.path.abspath(__file__)))
    for _base in _bases:
        _ruta = os.path.join(_base, "INSTALAR_EN_OTRA_PC.md")
        if os.path.exists(_ruta):
            with open(_ruta, encoding="utf-8") as _archivo:
                print(_archivo.read())
            raise SystemExit(0)
    print(
        "No encuentro INSTALAR_EN_OTRA_PC.md dentro del paquete.\n"
        "El manual está en el repositorio, en cliente_ubuntu/."
    )
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
from senal_cliente import enviar_toggle, obtener_ruta_pid as _ruta_pid_compartida
from sonidos import reproducir_sonido
from utils_logging import imprimir, rotar_logs_al_iniciar

# --- Detección local de la palabra clave ---
# El servidor ya NO escucha la palabra clave: la detecta el cliente y se lo dice
# con la bandera `voz`. Se hace con faster-whisper, en `escucha_local.py`, igual
# en Ubuntu y en Windows. La importación va protegida porque este mismo archivo
# se copia a `cliente_windows/` y no debe reventar si falta el módulo.
try:
    from escucha_local import EscuchaLocal, quitar_frase_cierre
    ESCUCHA_LOCAL_DISPONIBLE = True
except Exception:
    EscuchaLocal = None
    ESCUCHA_LOCAL_DISPONIBLE = False

    def quitar_frase_cierre(texto):
        return texto

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
AVISO_AUDIO_SEGUNDOS = 30            # avisar en pantalla tras este tiempo

# --- Dictado abierto por la palabra clave ---
# El dictado se cierra diciendo «fin del dictado». Las pausas al hablar NO lo
# cierran (antes sí: 2,5 s de silencio lo cortaban a mitad de una frase). Quedan
# dos seguros que no se confunden con una pausa:
#   - un dictado en el que no se dijo nada desde el pitido se cierra solo;
#   - un tope de duración, para el que se quedó abierto por olvido.
DICTADO_VOZ_SIN_VOZ_SEGUNDOS = 6     # sin decir nada tras el pitido = cerrado
DICTADO_VOZ_MAX_SEGUNDOS = 180       # tope de duración de un dictado de voz
# Pico int16 que cuenta como «hay voz». Estaba en 1500 y no se alcanzaba
# hablando normal a un metro del portátil, así que el modo comando se cerraba
# por silencio aunque el usuario hubiera hablado (2026-11-05).
PICO_VOZ = 800
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

# Rebote de las señales de control: la autorrepetición del atajo de GNOME (o un
# doble envío) mandaría dos SIGUSR1 separados por milisegundos y el dictado se
# arrancaría y se pararía a la vez (2026-11-19).
DEBOUNCE_SENAL = 0.35


def _congelado():
    """True si corremos dentro de un ejecutable empaquetado.

    PyInstaller marca `sys.frozen`; Nuitka deja `__compiled__` en el espacio
    de nombres de los módulos que compila. Se comprueban los dos para que el
    mismo código sirva en las dos formas de empaquetar y en modo fuente.
    """
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _ruta_programa():
    """Ruta del binario real, también dentro del paquete.

    En Nuitka standalone `sys.executable` vale `…/python`, que **no existe** en
    el paquete: los hijos (`--bandeja`, `--mpris`) fallaban con «No such file
    or directory» y el icono no aparecía nunca (2026-11-19). El binario de
    verdad es el que se lanzó, es decir `sys.argv[0]`.
    """
    for candidato in (sys.argv[0], sys.executable):
        if not candidato:
            continue
        ruta = os.path.realpath(candidato)
        if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
            return ruta
    return sys.executable


def _directorio_base():
    """Directorio del programa: junto al exe (congelado) o junto al script."""
    if _congelado():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _directorio_config():
    """Carpeta donde el cliente lee y guarda `comandos.json`.

    Con el paquete instalado (`/opt/skipkey`, de root) no se puede escribir
    junto al programa, así que la configuración vive en `~/.config/skipkey/` y
    se crea desde la plantilla la primera vez. En modo fuente sigue junto al
    script, como siempre.
    """
    if _congelado():
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "skipkey")
    return _directorio_base()


def _nombre_config():
    return "comandos_windows.json" if sys.platform == "win32" else "comandos.json"


RUTA_JSON = os.path.join(_directorio_config(), _nombre_config())
RUTA_INDICADOR = os.path.join(_directorio_base(), "indicador_grabacion.py")


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

# --- Botón del auricular Bluetooth (AVRCP -> MPRIS) ---
# El botón principal de un auricular Bluetooth no llega como tecla: viaja por
# AVRCP y BlueZ se lo entrega al reproductor MPRIS publicado en la sesión (lo
# hace `mpris-proxy`). Si no hay ninguno, el botón no hace nada. Por eso el
# cliente publica un reproductor «SkipKey» con mpris_dictado.py, que se
# ejecuta con el Python DEL SISTEMA: necesita dbus-python, que no está en el
# entorno virtual del cliente. Ver AGENTS.md.
RUTA_MPRIS = os.path.join(_directorio_base(), "mpris_dictado.py")
PYTHON_SISTEMA = "/usr/bin/python3"

# --- Icono de la barra superior (AppIndicator) ---
# Se ejecuta con el Python del sistema porque necesita PyGObject, que no está en
# el entorno virtual (igual que mpris_dictado.py). Los dos procesos se hablan
# por señales (senal_cliente.py) y por un archivo de estado: aquí se escribe lo
# que está pasando y el icono lo relee para poner el color y las casillas.
RUTA_BANDEJA = os.path.join(_directorio_base(), "bandeja_ubuntu.py")
RUTA_ESTADO = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "dictado-voz",
    "estado.json",
)


def _escribir_estado(escucha, comandos, grabando):
    """Deja el estado donde lo lee el icono de la bandeja.

    Se escribe en un temporal y se renombra: el icono lee el archivo cada
    segundo y no debe encontrarlo a medias.
    """
    try:
        os.makedirs(os.path.dirname(RUTA_ESTADO), exist_ok=True)
        datos = {
            "escucha": bool(escucha),
            "comandos": bool(comandos),
            "grabando": bool(grabando),
        }
        temporal = RUTA_ESTADO + ".tmp"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.replace(temporal, RUTA_ESTADO)
    except Exception:
        pass


def _asegurar_config():
    """Crea la configuración si no existe. Devuelve True si la creó."""
    if os.path.exists(RUTA_JSON):
        return False
    try:
        fuente = None
        if _congelado():
            # PyInstaller deja los datos en sys._MEIPASS; Nuitka, junto al
            # binario. El respaldo cubre las dos formas.
            fuente = os.path.join(
                getattr(sys, "_MEIPASS", _directorio_base()), _nombre_config()
            )
        else:
            fuente = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), _nombre_config()
            )
        if fuente and os.path.exists(fuente):
            os.makedirs(os.path.dirname(RUTA_JSON), exist_ok=True)
            shutil.copy2(fuente, RUTA_JSON)
            imprimir(f"[CLIENTE] Configuración creada: {RUTA_JSON}")
            return True
    except Exception as e:
        imprimir(f"[CLIENTE] No se pudo crear configuración: {e}")
    return False


def _avisar_primer_arranque():
    """Explica lo que falta tras copiar el paquete a un equipo nuevo.

    Sin este aviso, el que copia el AppImage cree que está todo y culpa a la
    aplicación cuando en realidad faltan la cuenta, el arranque y el atajo.
    """
    imprimir(
        "[PRIMER ARRANQUE] Cliente recién instalado. Falta esto, una sola vez:\n"
        "  1) Entrar con tu cuenta: icono de la bandeja → Configuración… → "
        "Cuenta.\n"
        "  2) Arranque al iniciar sesión: systemctl --user enable --now skipkey "
        "(o un .desktop en ~/.config/autostart).\n"
        "  3) Ctrl+Espacio: en Wayland lo manda GNOME con «systemctl --user kill "
        "--kill-who=main -s SIGUSR1 skipkey»; el cliente no puede capturar "
        "teclas globales.\n"
        "  Paquetes del escritorio: libgtk-3-0 libayatana-appindicator3-1 "
        "libdbus-1-3 ffmpeg xclip.\n"
        "  Manual completo: ejecuta este programa con --ayuda."
    )


def _revisar_dependencias():
    """Avisa de los programas externos que faltan, con la orden de instalarlos."""
    paquetes = {"ffmpeg": "ffmpeg", "xclip": "xclip"}
    faltan = [nombre for nombre in paquetes if shutil.which(nombre) is None]
    if faltan:
        imprimir(
            "[AVISO] Faltan programas del sistema: "
            + ", ".join(faltan)
            + ". Instálalos con: sudo apt install "
            + " ".join(paquetes[nombre] for nombre in faltan)
            + " (o su equivalente en tu distribución)."
        )


# Dirección del servidor usada cuando no se indica otra cosa.
# Prioridad:  --servidor IP:PUERTO  >  variable DICTADO_SERVIDOR
#             >  campo "servidor" de comandos.json  >  este valor.
SERVIDOR_POR_DEFECTO = "127.0.0.1:5555"


def _bandeja_activada():
    """True si el icono de la barra superior debe arrancar."""
    if _congelado():
        # Un solo binario: la bandeja corre con él mismo. `find_spec` solo
        # consulta el registro de módulos, no importa GTK en este proceso.
        import importlib.util

        if importlib.util.find_spec("gi") is None:
            imprimir("[BANDEJA] El binario no trae PyGObject: icono desactivado.")
            return False
    elif not os.path.exists(RUTA_BANDEJA) or not os.path.exists(PYTHON_SISTEMA):
        return False
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            activada = bool(json.load(f).get("bandeja", True))
    except Exception:
        activada = True
    if not activada:
        imprimir('[BANDEJA] Icono desactivado ("bandeja": false en comandos.json).')
    return activada


def _entorno_sistema():
    """Entorno limpio para el proceso del icono (Python del sistema + GTK).

    Las aplicaciones instaladas como snap (VS Code, entre otras) dejan
    variables como `GDK_PIXBUF_MODULE_FILE`, `GIO_MODULE_DIR` o `XDG_DATA_DIRS`
    apuntando a `/snap/...`. Con ellas, `/usr/bin/python3` revienta al cargar
    GTK:

        symbol lookup error: /snap/core20/.../libpthread.so.0:
        undefined symbol: __libc_pthread_init

    Se quitan esas variables. `DISPLAY` y `WAYLAND_DISPLAY` se conservan: sin
    ellas el icono no tiene dónde dibujarse.
    """
    entorno = dict(os.environ)
    for clave in (
        "LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH",
        "GTK_PATH", "GTK_EXE_PREFIX", "GDK_PIXBUF_MODULE_FILE",
        "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "GI_TYPELIB_PATH",
        "XDG_DATA_DIRS",
    ):
        entorno.pop(clave, None)
    return entorno


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


def _resolver_dispositivo_entrada():
    """Índice del micrófono a usar, o None para el predeterminado del sistema.

    Se configura en comandos.json con `dispositivo_entrada`:
      - vacío, "default" o "sistema": el que tenga el sistema por defecto;
      - "interno": el micrófono incorporado de la computadora. **Nunca debe
        usarse el micrófono de un auricular Bluetooth para dictar**: eso pone
        el auricular en modo manos libres (perfil HSP/HFP) y su botón deja de
        enviar el comando multimedia AVRCP (el auricular cree que está en una
        llamada y envía "colgar"), así que el botón no puede activar el
        dictado. Ver AGENTS.md;
      - un número: el índice que asigna sounddevice;
      - cualquier otro texto: se busca como parte del nombre del dispositivo.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        valor = config.get("dispositivo_entrada", "")
    except Exception:
        return None
    if valor is None:
        return None
    valor = str(valor).strip()
    if not valor or valor.lower() in ("default", "predeterminado", "sistema"):
        return None

    try:
        dispositivos = list(enumerate(sd.query_devices()))
    except Exception as e:
        imprimir(f"[AUDIO] No se pudo listar los dispositivos: {e}")
        return None
    entradas = [
        (i, d) for i, d in dispositivos if d.get("max_input_channels", 0) > 0
    ]

    if valor.isdigit():
        indice = int(valor)
        if any(i == indice for i, _ in entradas):
            return indice
        imprimir(
            f"[AUDIO] El dispositivo de entrada {indice} no existe; "
            "se usará el predeterminado."
        )
        return None

    if valor.lower() in ("interno", "interna", "incorporado", "incorporada"):
        for i, d in entradas:
            nombre = d["name"].lower()
            if "bluez" in nombre or "headset" in nombre or "auricular" in nombre:
                continue
            if "mic1" in nombre or "digital microphone" in nombre:
                return i
        imprimir(
            "[AUDIO] No se encontró el micrófono interno; se usará el "
            "predeterminado del sistema."
        )
        return None

    busca = valor.lower()
    for i, d in entradas:
        if busca in d["name"].lower():
            return i
    imprimir(
        f"[AUDIO] Ningún dispositivo de entrada coincide con '{valor}'; "
        "se usará el predeterminado del sistema."
    )
    return None


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
    """Configuración de la escucha local: (activada, clave, frases de cierre).

    Se controla con "modo_escucha" en comandos.json (activado por defecto).
    "frase_fin" admite una cadena o una lista: cualquiera de ellas cierra.
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            config = json.load(f)
        activado = bool(config.get("modo_escucha", True))
        clave = str(config.get("clave", "computadora")).strip()
        frases = config.get("frase_fin", ["fin del dictado"])
        # Verbos y frases de comando son configurables: se aplican aquí.
        aplicar_marcas(config)
    except Exception:
        activado, clave, frases = True, "computadora", ["fin del dictado"]
    if isinstance(frases, str):
        frases = [frases]
    frases = [str(f).strip() for f in frases if str(f).strip()]
    if not frases:
        frases = ["fin del dictado"]
    imprimir(
        f"Escucha local: {'activada' if activado else 'desactivada'} "
        f"(clave «{clave}», cierre {frases})"
    )
    return activado, clave, frases


def _nombre_modelo_whisper():
    """Modelo de Whisper que reconoce la palabra clave y las órdenes.

    Por defecto `base` (~142 MB, WER medido del 48 % frente al 103 % de Vosk
    small). Se puede cambiar con DICTADO_MODELO_WHISPER (`tiny`, `base`,
    `small`), o con una ruta local a un modelo ya convertido.
    """
    nombre = os.environ.get("DICTADO_MODELO_WHISPER", "").strip()
    if nombre:
        return nombre
    base = _directorio_base()
    candidatos = [
        os.path.join(base, "modelos", "whisper-base"),
        os.path.join(os.path.dirname(base), "modelos", "whisper-base"),
    ]
    for ruta in candidatos:
        if ruta and os.path.isdir(ruta):
            return ruta
    return "base"


def _obtener_ruta_pid():
    """Ruta del archivo PID (compartida con senal_cliente.py)."""
    return _ruta_pid_compartida()


def _enviar_toggle_cli(modo="normal"):
    """Envía la señal de toggle a la instancia de cliente_voz en ejecución.

    La búsqueda del cliente vive en `senal_cliente.py` porque
    `mpris_dictado.py` (botón del auricular Bluetooth) necesita exactamente la
    misma lógica.
    """
    return enviar_toggle(modo)


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

    1. el nombre tal cual en el catálogo `web` de comandos.json («seace»);
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
    """Busca el programa en comandos.json y devuelve su ruta, o None.

    `accion` es «abre» o «cerrar». Se prueba el nombre tal cual y, si no está,
    sin el artículo de delante (el servidor manda lo que transcribió Whisper).
    """
    try:
        with open(RUTA_JSON, "r", encoding="utf-8") as f:
            comandos = json.load(f)
    except Exception:
        _avisar_comando(
            "Configuración de comandos", "No se pudo leer comandos.json.", avisar
        )
        return None

    tabla = comandos.get(accion)
    if not isinstance(tabla, dict):
        _avisar_comando(
            "Comando no disponible", f"«{accion}» no está en comandos.json.", avisar
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
    # En el paquete se relanza el propio binario con `--indicador` (igual que
    # `--bandeja` o `--mpris`): Tk **nunca** se crea dentro de este proceso.
    # Antes, en el binario, se creaba la ventana en un hilo de este mismo
    # proceso y Tkinter dejaba el cliente **congelado** justo al empezar a
    # dictar: no había latidos, no llegaba la tecla de parar y había que cerrar
    # la aplicación (visto el 2026-11-19, 17:42).
    if _congelado():
        orden = [_ruta_programa(), "--indicador"]
    else:
        orden = [sys.executable, RUTA_INDICADOR]
    _indicador_proceso = subprocess.Popen(
        orden,
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
        # Sin servidor, un DEALER acumula mensajes hasta el HWM y então
        # `send_multipart` se queda **esperando para siempre**: el bucle
        # principal dejaba de latir, el segundo Ctrl+Espacio no se atendía y
        # había que cerrar la aplicación (visto el 2026-11-19 en una instalación
        # nueva que apuntaba a 127.0.0.1). Con el HWM bajo y envíos NOBLOCK, el
        # cliente pierde audio en vez de morirse.
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDHWM, 300)
        self._audio_sin_servidor = False   # ya se avisó de que se descarta

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

        # Proceso que publica el cliente como reproductor MPRIS (botón del
        # auricular Bluetooth). Lo lanza y vigila la hebra _bucle_mpris.
        self._mpris = None

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
                "la barra → Configuración… → Cuenta y entra con tu usuario y contraseña."
            )
        self._hilo_perfil = threading.Thread(
            target=self._bucle_sincronizacion, daemon=True, name="perfil"
        )
        self._hilo_perfil.start()

        # Escucha local de la palabra clave (faster-whisper)
        self._escucha = None
        # Palabras de la escucha: las refresca `_crear_escucha` y la recarga
        # desde la ventana de configuración.
        self._clave = "computadora"
        self._frases_fin = ["fin del dictado"]
        # Icono de la barra superior: proceso hijo con el Python del sistema.
        self._bandeja = None
        # Gancho global de teclado (2026-11-19): se guarda para vigilarlo. Un
        # gancho de bajo nivel puede quedarse sin entregar teclas —en Windows
        # pasa cuando el callback tarda, y aquí el indicador se pinta desde el
        # callback— y entonces el cliente se queda sordo sin avisar. Se revisa
        # cada 5 s y se levanta otra vez si se cayó.
        self._listener = None
        self._teclas_vistas = 0
        self._ultimo_chequeo_teclas = 0.0
        self._ultimo_latido_teclas = time.time()

        # Transporte del audio (2026-11-19): "puente" (HTTPS, funciona desde
        # cualquier red) o "zmq" (red local). Lo decide `_elegir_transporte()`
        # al arrancar; el puente es el que usa el celular y el único publicado
        # en internet (el 5555 no lo está).
        self._transporte = "zmq"
        self._token_transporte = sesion.token()  # con el que se eligió
        self._pcm_puente = bytearray()      # audio acumulado para el puente
        self._puente_ocupado = False        # hay un POST en curso
        self._aviso_largo = False           # ya se avisó de que se recorta
        # Señales de control externo (atajos de GNOME e icono) desde el primer
        # instante: si un SIGUSR1 llega antes de `ejecutar()`, el manejador por
        # defecto MATA el proceso (visto el 2026-11-19: status=10/USR1).
        self._senal_pendiente = None
        self._senal_momento = 0.0
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, self._manejador_sigusr1)
        if hasattr(signal, "SIGUSR2"):
            signal.signal(signal.SIGUSR2, self._manejador_sigusr2)
        if hasattr(signal, "SIGRTMIN"):
            signal.signal(signal.SIGRTMIN + 1, self._manejador_escucha)
            signal.signal(signal.SIGRTMIN + 2, self._manejador_comandos)
            signal.signal(signal.SIGRTMIN + 3, self._manejador_recargar)
            signal.signal(signal.SIGRTMIN + 4, self._manejador_teclado)
        # Se puede pausar desde el icono: lo dictado se escribe como texto.
        self.comandos_activos = True
        # True mientras el dictado en curso lo abrió la palabra clave: solo esos
        # se cierran con «fin del dictado» y solo a ellos el servidor los marca.
        self._dictado_voz = False
        self._dictado_voz_inicio = 0.0
        self._hubo_voz_dictado = False  # ya se dijo algo desde el pitido
        self._frase_cierre = ""         # frase de cierre que se oyó (si la hubo)
        # Dictado de voz: el audio se guarda hasta saber qué es (orden,
        # consulta o dictado). Así un comando se ejecuta sin subir su audio.
        self._buffer_voz = []
        self._dictado_enviado = False   # el DICTADO_START ya salió al socket
        self._start_encolado = False    # el evento start_voz ya está en cola
        # La palabra clave abre el **modo comando** (ver `_iniciar_dictado_voz`).
        # Solo el comando «iniciar dictado» pasa a transcribir texto.
        self._modo_comando = False
        # Frase oída esperando decisión: silencio de 2 s para saber si es una
        # orden (la corrige el servidor) o una consulta (la responde la IA).
        self._orden_pendiente = None
        # Qué se está esperando del servidor: "orden", "consulta" o None.
        self._esperando = None
        # Última frase que reconoció la escucha (solo para no repetirla en el
        # registro).
        self._ultimo_texto_oido = ""

    # --- Reproductor MPRIS (botón del auricular Bluetooth) ---

    def _mpris_disponible(self):
        """True si se puede publicar el reproductor MPRIS en esta máquina.

        Se comprueba una sola vez al arrancar: si el Python del sistema no
        tiene dbus-python, el botón del auricular no puede funcionar y no tiene
        sentido estar reiniciando el proceso hijo cada pocos segundos.

        El botón del auricular Bluetooth **viene apagado**: solo tiene sentido
        con un auricular dedicado a la computadora. Con uno multipunto (también
        emparejado al celular), el celular se queda con el botón en cuanto hay
        una llamada o suena música allí, y no hay forma de recuperarlo desde la
        PC. Se enciende con "boton_auricular_bluetooth": true en comandos.json
        (o con DICTADO_MPRIS=1 en el entorno). Ver AGENTS.md.
        """
        if sys.platform == "win32":
            return False

        manual = os.environ.get("DICTADO_MPRIS", "").strip().lower()
        if manual in ("0", "no", "false", "off"):
            activado = False
        elif manual in ("1", "si", "sí", "yes", "true", "on"):
            activado = True
        else:
            try:
                with open(RUTA_JSON, "r", encoding="utf-8") as f:
                    activado = bool(
                        json.load(f).get("boton_auricular_bluetooth", False)
                    )
            except Exception:
                activado = False
        if not activado:
            imprimir(
                "[MPRIS] Botón del auricular Bluetooth desactivado "
                '(para activarlo: "boton_auricular_bluetooth": true en '
                "comandos.json). El dictado sigue con la tecla activadora."
            )
            return False

        if _congelado():
            # Un solo binario: dbus-python viaja dentro y el reproductor se
            # lanza relanzando este mismo ejecutable con --mpris.
            import importlib.util

            if importlib.util.find_spec("dbus") is None:
                imprimir(
                    "[MPRIS] El binario no trae dbus-python: el botón del "
                    "auricular Bluetooth queda desactivado."
                )
                return False
            return True

        if not os.path.exists(RUTA_MPRIS):
            imprimir(
                f"[MPRIS] No se encontró {RUTA_MPRIS}; botón del auricular desactivado."
            )
            return False
        try:
            prueba = subprocess.run(
                [PYTHON_SISTEMA, "-c", "import dbus"],
                capture_output=True,
            )
        except Exception as e:
            imprimir(f"[MPRIS] No se pudo comprobar {PYTHON_SISTEMA}: {e}")
            return False
        if prueba.returncode != 0:
            imprimir(
                "[MPRIS] El Python del sistema no tiene dbus-python: el botón del "
                "auricular Bluetooth queda desactivado (instala python3-dbus)."
            )
            return False
        return True

    def _bucle_mpris(self):
        """Mantiene vivo el reproductor MPRIS que escucha el botón del auricular.

        El proceso hijo se ejecuta con el Python del sistema y su salida se
        reenvía al log del cliente. Si termina con error se reintenta cada
        10 s; si termina bien (por ejemplo, porque ya había otro publicado) no
        se reinicia, para no caer en un bucle.
        """
        entorno = dict(os.environ)
        entorno["DICTADO_MPRIS_MODO"] = os.environ.get("DICTADO_MPRIS_MODO", "ambos")
        entorno["DICTADO_MPRIS_DOBLE"] = os.environ.get("DICTADO_MPRIS_DOBLE", "si")

        while self._activo:
            try:
                if _congelado():
                    comando = [_ruta_programa(), "--mpris"]
                else:
                    comando = [PYTHON_SISTEMA, RUTA_MPRIS]
                proceso = subprocess.Popen(
                    comando,
                    env=entorno,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except Exception as e:
                imprimir(f"[MPRIS] No se pudo iniciar el reproductor: {e}")
                return
            self._mpris = proceso
            for linea in proceso.stdout:
                linea = linea.rstrip()
                if linea:
                    imprimir(linea)
                if not self._activo:
                    break
            codigo = proceso.wait()
            self._mpris = None
            if not self._activo:
                return
            if codigo == 0:
                imprimir("[MPRIS] El reproductor terminó; no se reiniciará.")
                return
            imprimir(
                f"[MPRIS] El reproductor terminó con código {codigo}; reintento en 10 s."
            )
            for _ in range(20):
                if not self._activo:
                    return
                time.sleep(0.5)

    def _bucle_bandeja(self):
        """Mantiene vivo el icono de la barra superior (proceso hijo).

        Se ejecuta con el Python DEL SISTEMA porque necesita PyGObject, que no
        está en el entorno virtual. Su salida se reenvía al registro del
        cliente. Si termina con error se reintenta cada 10 s; si termina bien,
        no se reinicia para no caer en un bucle.
        """
        while self._activo:
            try:
                if _congelado():
                    comando = [_ruta_programa(), "--bandeja"]
                else:
                    comando = [
                        PYTHON_SISTEMA, RUTA_BANDEJA, "--python", sys.executable
                    ]
                proceso = subprocess.Popen(
                    comando,
                    env=_entorno_sistema(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except Exception as e:
                imprimir(f"[BANDEJA] No se pudo iniciar el icono: {e}")
                return
            self._bandeja = proceso
            for linea in proceso.stdout:
                linea = linea.rstrip()
                if linea:
                    imprimir(linea)
                if not self._activo:
                    break
            codigo = proceso.wait()
            self._bandeja = None
            if not self._activo:
                return
            if codigo == 0:
                imprimir("[BANDEJA] El icono terminó; no se reiniciará.")
                return
            imprimir(
                f"[BANDEJA] El icono terminó con código {codigo}; reintento en 10 s."
            )
            for _ in range(20):
                if not self._activo:
                    return
                time.sleep(0.5)

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

    # --- Captura de audio ---

    def _abrir_stream_audio(self):
        """Abre (o reabre) la captura del micrófono. Devuelve el stream o None.

        Se usa al arrancar y cada vez que la captura se pierde. Reintentar es
        importante: si el cliente arranca junto con Windows (carpeta Inicio o
        Programador de tareas), el sistema de audio todavía no está listo y la
        primera apertura falla (o abre un dispositivo equivocado).
        """
        # Qué dispositivo de entrada se va a usar: útil para detectar un
        # micrófono muteado, desconectado o equivocado (sobre todo en Windows)
        # y para no capturar del auricular Bluetooth (su botón dejaría de
        # enviar el comando AVRCP; ver _resolver_dispositivo_entrada).
        indice_entrada = _resolver_dispositivo_entrada()
        try:
            if indice_entrada is None:
                info_entrada = sd.query_devices(kind="input")
            else:
                info_entrada = sd.query_devices(indice_entrada)
            imprimir(
                f"[AUDIO] Dispositivo de entrada: {info_entrada['name']} "
                f"(canales={info_entrada['max_input_channels']}, "
                f"frecuencia por defecto="
                f"{info_entrada['default_samplerate']:.0f} Hz)"
            )
        except Exception as e:
            imprimir(f"[AUDIO] No se pudo consultar el dispositivo de entrada: {e}")
        try:
            parametros = dict(
                samplerate=FRECUENCIA,
                channels=1,
                callback=self._callback_audio,
                dtype="int16",
                blocksize=CHUNK_MUESTRAS,
            )
            if indice_entrada is not None:
                parametros["device"] = indice_entrada
            stream = sd.InputStream(**parametros)
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

    def _manejador_sigusr1(self, signum, frame):
        # Solo se deja una marca: dentro de un manejador de señal no se puede
        # tocar una `queue` (puede quedar bloqueada para siempre si la señal
        # interrumpe justo esa misma cola). El trabajo lo hace `_atender_senal`.
        self._senal_pendiente = "toggle_normal"

    def _manejador_sigusr2(self, signum, frame):
        self._senal_pendiente = "toggle_escribir"

    def _manejador_escucha(self, signum, frame):
        """El icono de la bandeja pidió encender/apagar la escucha."""
        self._senal_pendiente = "toggle_escucha"

    def _manejador_comandos(self, signum, frame):
        """El icono de la bandeja pidió pausar/reanudar los comandos."""
        self._senal_pendiente = "toggle_comandos"

    def _manejador_recargar(self, signum, frame):
        """La ventana de configuración guardó: hay que releer el JSON."""
        self._senal_pendiente = "recargar"

    def _manejador_teclado(self, signum, frame):
        """El icono pidió volver a enganchar la escucha de teclas."""
        self._senal_pendiente = "reiniciar_teclado"

    def _atender_senal(self):
        """Pasa al bucle la señal pendiente, con rebote.

        El rebote descarta la autorrepetición del atajo de GNOME: dos SIGUSR1
        separados por milisegundos arrancarían y pararían el dictado a la vez.
        """
        pendiente = self._senal_pendiente
        if not pendiente:
            return
        self._senal_pendiente = None
        ahora = time.time()
        if ahora - self._senal_momento < DEBOUNCE_SENAL:
            return
        self._senal_momento = ahora
        self._cola_eventos.put(pendiente)

    def _recargar_configuracion(self):
        """Relee comandos.json sin reiniciar el cliente.

        Se encarga de lo que la ventana de configuración puede cambiar: la clave
        y las frases de la escucha, los verbos y frases de comando (que se
        aplican sobre `marcas`), y los interruptores de escucha y comandos.
        Las teclas y el moderador de audio necesitan reiniciar y se avisa.
        """
        activado, clave, frases = cargar_escucha_local()
        self._clave = clave
        self._frases_fin = list(frases)
        if self._escucha is not None:
            self._escucha.clave = clave
            self._escucha.frases_fin = tuple(frases)
            self._escucha.reiniciar()
        imprimir(
            f"[CONFIG] Recargada: clave «{clave}», {len(marcas.FRASES)} frases y "
            f"{len(marcas.VERBOS)} verbos de comando."
        )
        if not activado and self._escucha is not None:
            self._escucha = None
        elif activado and self._escucha is None:
            self._escucha = self._crear_escucha()
        self._publicar_estado()

    # --- Cuenta y perfil -------------------------------------------------

    def _cuenta_lista(self):
        """¿Se puede dictar? Sin cuenta, no (decisión del 2026-11-18)."""
        if sesion.hay_sesion():
            return True
        imprimir(
            "[CUENTA] Dictado bloqueado: no hay sesión. Entra en el icono de la "
            "barra → Configuración… → Cuenta."
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

    def _alternar_grabacion(self, modo="normal"):
        """Inicia o detiene la grabación en modo toggle."""
        if self._inyectando:
            return

        # Sin cuenta no se dicta: el perfil (correcciones y ajustes) es de un
        # usuario concreto y sin él el dictado no tendría dueño.
        if not self._grabando and not self._cuenta_lista():
            return

        # Si el dictado en curso lo abrió la palabra clave, la tecla lo cierra
        # (y deja el estado limpio) en vez de abrir otro encima.
        if self._dictado_voz:
            self._terminar_dictado_voz("tecla activadora")
            return

        if not self._grabando:
            self._grabando = True
            self._tecla_suelta = False
            self._escribir_este_dictado = (
                modo == "escribir" or self._modo_salida == "escribir"
            )
            imprimir(f"\n[GRABANDO... ({modo}) Pulsa de nuevo para detener]")
            try:
                _mostrar_indicador()
            except Exception as e:
                imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
            self._cola_eventos.put("start")
        else:
            self._grabando = False
            self._tecla_suelta = False
            try:
                _ocultar_indicador()
            except Exception as e:
                imprimir(f"[INDICADOR] No se pudo ocultar: {e}")
            imprimir("[DETENIDO. Procesando...]")
            self._cola_eventos.put("stop")
        self._publicar_estado()

    # --- Dictado abierto por la palabra clave ---

    def _crear_escucha(self):
        """Crea el detector de la palabra clave, o None si no se puede.

        Nunca es un error fatal: sin escucha local el dictado por tecla sigue
        funcionando igual, solo se pierde el arranque por voz.
        """
        activado, clave, frases_fin = cargar_escucha_local()
        self._clave = clave
        self._frases_fin = list(frases_fin)
        if not activado:
            return None
        if not ESCUCHA_LOCAL_DISPONIBLE:
            imprimir(
                "[VOZ] Escucha local desactivada: falta escucha_local.py o la "
                "librería faster-whisper (entorno_dictado/bin/python3 -m pip "
                "install faster-whisper)."
            )
            return None
        ruta_modelo = _nombre_modelo_whisper()
        escucha = EscuchaLocal(
            clave=clave, frase_fin=frases_fin, ruta_modelo=ruta_modelo
        )
        if not escucha.disponible:
            imprimir(
                f"[VOZ] Escucha local desactivada: {escucha.error}. "
                "El dictado por tecla sigue funcionando."
            )
            return None
        imprimir(
            f"[VOZ] Escucha local lista (Whisper {os.path.basename(ruta_modelo)}): "
            f"«{clave}» abre el modo comando y «{frases_fin[0]}» cierra el dictado."
        )
        return escucha

    def _alimentar_escucha(self, chunk):
        """Pasa el chunk al detector y actúa según el modo en que estemos.

        Se alimenta con los mismos chunks que ya van al servidor, así que no hay
        un segundo lector del micrófono.

        - Sin nada en curso: se busca la palabra clave.
        - **Modo comando** (lo abre la clave): cada frase se resuelve aquí
          (`_resolver_comando`): orden, consulta al asistente o error.
        - **Dictado**: se busca «fin del dictado» y se mide si hay voz.
        - En un dictado **por tecla** no se escucha nada.
        """
        if self._escucha is None:
            return
        if self._grabando and not self._dictado_voz:
            return
        try:
            pico = int(np.abs(chunk.astype(np.int32)).max())
        except Exception:
            pico = 0
        if self._dictado_voz and pico >= PICO_VOZ:
            self._hubo_voz_dictado = True
            self._ultima_voz_modo = time.time()
            if self._orden_pendiente is not None:
                # Después de la frase siguió hablando: lo que dijo no era una
                # orden corta («abre cromo») sino algo más largo. Lo resuelve
                # `_vigilar_dictado_voz`, que cambia la espera a consulta.
                self._orden_pendiente["sigue_hablando"] = True
                self._orden_pendiente["ultima_voz"] = time.time()
        evento = self._escucha.alimentar(chunk)
        # Traza de lo que reconoce la escucha local: sin esto, cuando algo no se
        # ejecuta no hay forma de saber si lo oyó mal o si no lo oyó (2026-11-05).
        texto_oido = self._escucha.ultimo_texto
        if texto_oido and texto_oido != self._ultimo_texto_oido:
            self._ultimo_texto_oido = texto_oido
            imprimir(f"[ESCUCHA] «{texto_oido}» (pico {self._escucha.ultimo_pico})")
        if evento == "clave" and not self._grabando:
            # La clave y la orden pueden venir en la MISMA frase
            # («computadora iniciar dictado»). Se lee antes de abrir la sesión,
            # porque abrirla reinicia el reconocedor y borra ese resto.
            resto = self._escucha.resto_tras_clave
            modo = "comando" if self.comandos_activos else "dictado"
            if modo == "comando":
                imprimir("[VOZ] Palabra clave detectada; modo comando.")
            else:
                # Comandos en pausa desde el icono: la clave dicta directamente.
                imprimir("[VOZ] Palabra clave detectada (comandos en pausa).")
            self._iniciar_dictado_voz(modo=modo)
            if resto and self._modo_comando:
                imprimir(f"[VOZ] Y en la misma frase se oyó: «{resto}».")
                self._escucha.ultimo_texto = resto
                self._resolver_comando("orden" if es_comando(resto) else "frase")
        elif evento == "fin":
            if self._dictado_voz and not self._modo_comando:
                self._terminar_dictado_voz("«fin del dictado»")
            else:
                # Un cierre oído fuera de un dictado se ignora, pero **dejando
                # rastro**: tragárselo en silencio hacía parecer que no se oía
                # nada, y así se perdió una sesión de diagnóstico (2026-11-05).
                imprimir(
                    "[VOZ] Se oyó un cierre («%s») y no hay dictado abierto; "
                    "se ignora." % (self._escucha.cierre_detectado or "?")
                )
        elif evento == "comando" and self._modo_comando:
            self._resolver_comando("orden")
        elif evento == "frase" and self._modo_comando:
            self._resolver_comando("frase")

    def _iniciar_dictado_voz(self, modo="comando"):
        """Abre la sesión que dispara la palabra clave.

        `modo="comando"` (lo normal): la clave deja al cliente **esperando una
        orden**; no se manda nada al servidor todavía. Lo que se oiga se resuelve
        en `_resolver_comando()`: se ejecuta aquí, se le pide al servidor que
        corrija el argumento, o se manda al asistente (DeepSeek).

        `modo="dictado"`: la clave abre directamente un dictado de texto (es lo
        que pasa con los comandos en pausa desde el icono). Se pide el
        `DICTADO_START` y se transcribe hasta «fin del dictado».
        """
        if self._grabando or self._inyectando:
            return
        if not self._cuenta_lista():
            return
        self._dictado_voz = True
        self._grabando = True
        self._tecla_suelta = False
        self._escribir_este_dictado = self._modo_salida == "escribir"
        self._dictado_voz_inicio = time.time()
        self._ultima_voz_modo = self._dictado_voz_inicio
        self._hubo_voz_dictado = False
        self._orden_pendiente = None
        self._esperando = None
        self._modo_comando = modo == "comando"
        if self._modo_comando:
            imprimir("\n[GRABANDO... (comando) Di la orden o «iniciar dictado»]")
        else:
            imprimir("\n[GRABANDO... (voz) Di «fin del dictado» para terminar]")
        # El indicador lo pinta el bucle principal, nunca quien llama aquí:
        # este camino puede venir del hilo de audio (2026-11-19).
        self._cola_eventos.put("indicador_on")
        reproducir_sonido("inicio")
        if self._escucha is not None:
            self._escucha.reiniciar()
            # La frase de cierre se guarda al cerrar; aqui se limpia la del
            # dictado anterior para no recortar texto que no toca.
            self._escucha.cierre_detectado = ""
        self._frase_cierre = ""
        self._dictado_enviado = False
        self._start_encolado = False
        self._buffer_voz = []
        if not self._modo_comando:
            # Dictado directo: el audio se transcribe desde ya.
            self._pasar_a_servidor()
        self._publicar_estado()

    def _pasar_a_servidor(self):
        """Decide que esto es texto: se pide el dictado al servidor.

        El audio guardado se suelta en `_procesar_eventos`, **después** del
        `DICTADO_START`: si llegara antes, el servidor lo tiraría por no estar
        dictando todavía. Por eso el audio sigue guardándose hasta que el
        evento se procesa (`_dictado_enviado`).
        """
        if self._start_encolado:
            return
        self._start_encolado = True
        imprimir(
            f"[VOZ] Es texto: se envía al servidor con los "
            f"{len(self._buffer_voz)} trozos guardados."
        )
        self._cola_eventos.put("start_voz")

    def _terminar_dictado_voz(self, motivo="", enviar=True, sonido="fin"):
        """Cierra el dictado abierto por la palabra clave.

        `enviar=False` es el camino corto: la orden ya se ejecutó aquí y al
        servidor no se le manda nada, ni el audio guardado.
        """
        if not self._dictado_voz:
            return
        self._dictado_voz = False
        self._grabando = False
        self._tecla_suelta = False
        self._modo_comando = False
        self._orden_pendiente = None
        # Si el cierre fue por la frase, la escucha dejó ahí lo que oyó: es la
        # señal de que hay que recortarla del texto cuando llegue. Si se cerró
        # por otra razon (tiempo, silencio, tecla), queda vacio y no se toca nada.
        self._frase_cierre = (
            self._escucha.cierre_detectado if self._escucha is not None else ""
        )
        self._orden_pendiente = None
        self._cola_eventos.put("indicador_off")
        imprimir(f"[DETENIDO ({motivo}). Procesando...]")
        # Confirmación inmediata de que se oyó el cierre: el texto todavía tiene
        # que transcribirse en el servidor (2-10 s), así que este pitido dice
        # «te oí, cierro»; el pip de «texto» llegará cuando esté en el portapapeles.
        reproducir_sonido(sonido)
        if self._escucha is not None:
            self._escucha.reiniciar()
        if enviar:
            # Si aún no se había pedido el dictado, lo que hay es texto: se
            # manda ahora, con lo que se guardó durante la decisión.
            self._pasar_a_servidor()
            self._cola_eventos.put("stop")
        else:
            self._buffer_voz = []
        self._publicar_estado()

    def _resolver_comando(self, clase):
        """Resuelve una frase oída dentro del modo comando.

        `clase` es «orden» (la escucha la reconoció como verbo o frase de
        sistema) o «frase» (cualquier otra cosa). Cuatro salidas:

        - **dictado**: se dijo «iniciar dictado» → se pasa a transcribir texto.
        - **corto**: el argumento está en `comandos.json` («abre calculadora»)
          → se ejecuta aquí, suena `orden` y al servidor no se le manda nada.
        - **espera**: lo demás (una orden con argumento desconocido o una frase
          larga) se queda esperando **silencio** de `ORDEN_SILENCIO_SEGUNDOS`:
          al llegar, se manda el audio al servidor para que corrija el argumento
          de la orden o para que el asistente responda la consulta.
        """
        texto = self._escucha.ultimo_texto if self._escucha is not None else ""
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
        """El usuario dijo «iniciar dictado»: se pasa a transcribir texto.

        Se descarta lo hablado hasta ahora (la clave y el propio comando: no
        deben llegar a Whisper) y se pide el `DICTADO_START`. El dictado se
        cierra como siempre, diciendo «fin del dictado».
        """
        imprimir("[VOZ] Comando «iniciar dictado»: se abre el dictado.")
        reproducir_sonido("inicio")
        self._modo_comando = False
        self._orden_pendiente = None
        self._buffer_voz = []
        self._dictado_voz_inicio = time.time()
        self._ultima_voz_modo = self._dictado_voz_inicio
        # Ya se habló (el comando), así que el seguro de «no se oyó nada» no
        # debe cerrar el dictado enseguida.
        self._hubo_voz_dictado = True
        self._pasar_a_servidor()

    def _orden_local(self, texto):
        """Resuelve la orden con lo que oyó la escucha local, sin el servidor.

        Devuelve `(accion, argumento)` listo para `ejecutar_comando()`, o None
        si hay que preguntarle al servidor: el argumento tiene que estar en
        alguno de los catálogos de `comandos.json` (programas o sitios web), si
        no es que la escucha oyó mal.
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
                self._iniciar_dictado_voz(modo="dictado")
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

    def _vigilar_dictado_voz(self, ahora):
        """Resuelve la espera pendiente y cierra la sesión de voz.

        - Con una frase esperando: al cumplirse `ORDEN_SILENCIO_SEGUNDOS` se
          manda al servidor —la orden, para que corrija el argumento, o la
          consulta, para que responda el asistente—. Si el usuario siguió
          hablando, lo dicho ya no es una orden corta: pasa a consulta.
        - **Dictado**: se cierra con «fin del dictado», con 6 s sin voz o con el
          tope de 180 s.
        - **Modo comando**: se cierra con 6 s sin voz o con el tope de 15 s. En
          ese caso no se transcribe nada: no había comando.
        """
        if not self._dictado_voz:
            return
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

        if ahora - self._dictado_voz_inicio > DICTADO_VOZ_MAX_SEGUNDOS:
            self._terminar_dictado_voz("tope de tiempo")
        elif (not self._hubo_voz_dictado
              and ahora - self._dictado_voz_inicio > DICTADO_VOZ_SIN_VOZ_SEGUNDOS):
            self._terminar_dictado_voz("no se oyó nada")

    def _cerrar_modo_comando(self, motivo):
        """Cierra el modo comando sin transcribir nada (disparo por error).

        A diferencia de un dictado, aquí no hay texto que mandar: si nadie dijo
        un comando, al servidor no se le pide nada. Se avisa con el pitido de
        error porque la palabra clave no llevó a ninguna parte.
        """
        imprimir(
            f"[VOZ] Modo comando cerrado: {motivo} "
            f"(abierto hace {time.time() - self._dictado_voz_inicio:.1f} s)."
        )
        self._terminar_dictado_voz(motivo, enviar=False, sonido="error")

    def _alternar_comandos(self):
        """Pausa o reanuda la ejecución de comandos de voz (menú del icono)."""
        self.comandos_activos = not self.comandos_activos
        imprimir(
            "[VOZ] Comandos "
            + (
                "activados."
                if self.comandos_activos
                else "en pausa: lo que digas se escribe como texto."
            )
        )
        self._publicar_estado()

    def _alternar_escucha_local(self):
        """Enciende o apaga la escucha de la palabra clave (menú del icono)."""
        if self._escucha is not None:
            self._escucha = None
            imprimir("[VOZ] Escucha de la palabra clave apagada desde el icono.")
        else:
            self._escucha = self._crear_escucha()
        self._publicar_estado()

    def _publicar_estado(self):
        """Deja el estado en el archivo que lee el icono de la bandeja."""
        _escribir_estado(
            self._escucha is not None, self.comandos_activos, self._grabando
        )

    def _guardar_pid(self):
        try:
            with open(_obtener_ruta_pid(), "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

    def _limpiar_pid(self):
        try:
            ruta_pid = _obtener_ruta_pid()
            if os.path.exists(ruta_pid):
                os.remove(ruta_pid)
        except Exception:
            pass

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
        # Corre dentro del gancho de teclado del sistema: aquí solo se marca
        # estado y se encolan eventos. Pintar la ventana del indicador desde
        # aquí bloquea el gancho (Tk puede tardar segundos) y en Windows el
        # sistema lo desengancha: la tecla deja de llegar (2026-11-19).
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
            self._alternar_grabacion(modo)
        else:
            if not self._grabando:
                # En modo «mantener» la tecla tampoco puede saltarse la cuenta
                # (el «toggle» ya la comprueba en `_alternar_grabacion`).
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

    def _sin_frase_cierre(self, texto):
        """Quita del final la frase de cierre, si este dictado la oyó.

        El servidor no sabe nada de esa frase: la quita el cliente, que es el
        único que sabe que se dijo y cómo la escribió Whisper. El recorte es
        tolerante y solo mira la cola.
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
        texto = self._sin_frase_cierre(texto)
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
            return

        if tipo == TEXTO:
            self._esperando_transcripcion = False
            texto = decodificar_texto(frames)
            imprimir(f"Texto detectado: {texto}")
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

        # ¿Por dónde va el audio? Puente HTTPS (funciona desde cualquier red) o
        # ZMQ (solo en la red del servidor). Ver `_elegir_transporte`.
        self._transporte = self._elegir_transporte()

        # Guardar PID para control externo (señales / CLI)
        self._guardar_pid()

        # Los manejadores de SIGUSR1/SIGUSR2 ya se instalaron en `__init__`:
        # tienen que existir aunque la señal llegue durante el arranque.

        # Iniciar escucha de teclado ANTES que el audio: si el micrófono
        # falla, la activación por teclado debe seguir funcionando.
        try:
            self._crear_listener()
        except Exception as e:
            imprimir(f"[CLIENTE] No se pudo iniciar el listener de pynput: {e}")

        # Hebra que escribe en el cursor los textos dictados (en orden)
        threading.Thread(target=self._bucle_escritura, daemon=True).start()

        # Reproductor MPRIS: es lo que permite que el botón del auricular
        # Bluetooth active el dictado (el botón viaja por AVRCP, no como tecla).
        if self._mpris_disponible():
            threading.Thread(target=self._bucle_mpris, daemon=True).start()

        # Icono de la barra superior (AppIndicator). Necesita PyGObject, así que
        # se ejecuta con el Python del sistema, como el reproductor MPRIS.
        if _bandeja_activada():
            threading.Thread(target=self._bucle_bandeja, daemon=True).start()

        # Iniciar captura de audio (stream continuo para la escucha local). Si
        # falla, el
        # cliente sigue vivo (teclado, texto y comandos) y la reintenta en el
        # bucle principal hasta que el sistema de audio esté disponible.
        stream = self._abrir_stream_audio()
        if stream is None:
            imprimir(
                "[AUDIO] La captura no arrancó todavía; se reintentará cada "
                f"{REINTENTO_AUDIO_SEGUNDOS:.0f} s."
            )

        # Escucha local de la palabra clave (Vosk). Reutiliza los chunks que ya
        # se manda al servidor, así que no abre el micrófono por su cuenta.
        self._escucha = self._crear_escucha()
        self._publicar_estado()

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
                self._atender_senal()
                self._procesar_eventos()

                # 2a. Gancho de teclado: si dejó de entregar teclas, se levanta
                #     otra vez (en Windows el sistema lo desengancha solo).
                self._vigilar_teclado()

                # 2b. Entrar o salir de la cuenta cambia el transporte.
                self._revisar_transporte()

                # 2b. Cerrar el dictado de voz si se quedó sin voz o pasó el tope
                self._vigilar_dictado_voz(time.time())

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
        finally:
            self._activo = False
            self._limpiar_pid()
            if self._bandeja is not None:
                try:
                    self._bandeja.terminate()
                except Exception:
                    pass
                self._bandeja = None
            if self._mpris is not None:
                try:
                    self._mpris.terminate()
                except Exception:
                    pass
                self._mpris = None
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

        Con el servidor caído o inalcanzable, el búfer del socket se llena y
        `send_multipart` se queda esperando indefinidamente: el bucle principal
        dejaba de latir y el cliente había que cerrarlo a la fuerza (2026-11-19).
        Mejor perder audio y avisar que quedarse mudo.
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
        abierto), así que con sesión y puente disponible se usa siempre. ZMQ
        queda para cuando no hay sesión o el puente no responde (por ejemplo,
        el servidor de dictado local sin puente levantado).
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
        """Vuelve a elegir el transporte si la sesión cambió.

        Entrar con la cuenta desde la ventana (o cerrar sesión) no reinicia el
        cliente: sin esto se seguía dictando por ZMQ hasta reiniciar, cuando lo
        normal es pasar al puente en cuanto hay cuenta.
        """
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
        """Envía chunks de audio acumulados al servidor.

        Es también donde se alimenta la escucha local de la palabra clave: se
        usan los mismos chunks que ya van al servidor, así que no hace falta
        abrir el micrófono por segunda vez.
        """
        enviados = 0
        while enviados < 20:  # Máximo 20 chunks por iteración (~1.3 s de audio)
            try:
                chunk = self._cola_audio.get_nowait()
                if self._transporte == "puente":
                    # Al puente se le manda el audio completo al cerrar, y solo
                    # el del dictado: el micrófono se captura siempre (para la
                    # palabra clave) y acumular todo llenaba el tope de 55 s
                    # antes de empezar a dictar (visto el 2026-11-19).
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

    # --- Gancho de teclado -------------------------------------------------

    def _crear_listener(self):
        """Crea (o recrea) el gancho global de teclado.

        Se guarda en `self._listener` para vigilarlo: un gancho de bajo nivel
        deja de entregar teclas si el sistema lo desengancha, y sin vigilancia
        el cliente se queda sordo sin una sola línea en el registro.
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
        """Revisa el gancho cada 5 s y lo vuelve a levantar si se cayó."""
        ahora = time.time()
        if ahora - self._ultimo_chequeo_teclas < 5:
            return
        self._ultimo_chequeo_teclas = ahora
        if not (self._listener is not None and self._listener.running):
            imprimir(
                "[TECLA] ⚠ La escucha de teclado se cayó; se vuelve a levantar."
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
        """Vuelve a levantar el gancho (menú del icono / atajo externo)."""
        imprimir("[TECLA] Reinicio de la escucha pedido desde el icono.")
        try:
            self._crear_listener()
        except Exception as e:
            imprimir(f"[TECLA] No se pudo reiniciar la escucha: {e}")
            try:
                reproducir_sonido("error")
            except Exception:
                pass
        self._publicar_estado()

    def _procesar_eventos(self):
        """Procesa eventos de teclado encolados."""
        while True:
            try:
                evento = self._cola_eventos.get_nowait()
                if evento == "indicador_on":
                    # Lo pidió el gancho de teclado o el hilo de audio: Tk se
                    # toca aquí, en el bucle principal (2026-11-19).
                    try:
                        _mostrar_indicador()
                    except Exception as e:
                        imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
                elif evento == "indicador_off":
                    try:
                        _ocultar_indicador()
                    except Exception as e:
                        imprimir(f"[INDICADOR] No se pudo ocultar: {e}")
                elif evento == "toggle_normal":
                    self._alternar_grabacion("normal")
                elif evento == "toggle_escribir":
                    self._alternar_grabacion("escribir")
                elif evento == "toggle_escucha":
                    self._alternar_escucha_local()
                elif evento == "toggle_comandos":
                    self._alternar_comandos()
                elif evento == "recargar":
                    self._recargar_configuracion()
                elif evento == "reiniciar_teclado":
                    self.reiniciar_escucha_teclado()
                elif evento == "start":
                    if self._transporte == "puente":
                        # Se acumula el audio y se manda al cerrar.
                        self._pcm_puente = bytearray()
                        self._aviso_largo = False
                        imprimir("[CLIENTE] Dictado por HTTPS (puente).")
                    elif self._escribir_este_dictado:
                        # Dictado en vivo: el servidor manda el texto por frases
                        imprimir("[CLIENTE] Enviando DICTADO_START en vivo al servidor...")
                        self._enviar(crear_dictado_start_vivo())
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_START al servidor...")
                        self._enviar(crear_dictado_start())
                elif evento == "start_voz":
                    # La palabra clave la oyó este cliente: el servidor marca.
                    # Sin `fin`: la frase de cierre la recorta el propio cliente.
                    if self._transporte == "puente":
                        self._pcm_puente = bytearray(b"".join(self._buffer_voz))
                        self._buffer_voz = []
                        self._aviso_largo = False
                        self._dictado_enviado = True
                        imprimir("[CLIENTE] Dictado por HTTPS (voz).")
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_START de voz al servidor...")
                        self._enviar(crear_dictado_start_voz(fin=False))
                        # Lo guardado mientras se decidía si era orden o texto. Va
                        # DESPUÉS del START: antes, el servidor lo descartaría.
                        if self._buffer_voz:
                            imprimir(
                                f"[CLIENTE] Enviando {len(self._buffer_voz)} trozos "
                                "guardados."
                            )
                            for trozo in self._buffer_voz:
                                self._enviar(crear_audio(trozo))
                            self._buffer_voz = []
                        # A partir de aquí el audio va directo al socket.
                        self._dictado_enviado = True
                elif evento == "stop":
                    if self._transporte == "puente":
                        self._lanzar_transcripcion_puente()
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_STOP al servidor. Esperando transcripción...")
                        # Si el envío no cupo (servidor caído), no se espera una
                        # transcripción que nunca va a llegar.
                        if self._enviar(crear_dictado_stop()):
                            self._esperando_transcripcion = True
                            self._transcripcion_inicio = time.time()
                elif isinstance(evento, tuple) and evento[:1] == ("puente_texto",):
                    # Resultado de la hebra que habló con el puente: aquí ya se
                    # puede tocar el portapapeles, sonar y hablar.
                    _, texto, error = evento
                    self._esperando_transcripcion = False
                    if error:
                        imprimir(f"[PUENTE] {error}")
                        esperando = self._esperando
                        self._esperando = None
                        if esperando in ("orden", "consulta"):
                            reproducir_sonido("error")
                        else:
                            _avisar_usuario("Dictado por HTTPS", error)
                            reproducir_sonido("error")
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
    # Un aviso es un problema: se oye aunque no se esté mirando la pantalla
    # (el ejecutable de Windows no tiene consola y en Linux el aviso solo va al
    # registro). Es un tono distinto del inicio y del fin del dictado.
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
    if _congelado():
        return f'"{sys.executable}"'
    python = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cliente_voz.py")
    return f'"{python}" "{script}"'


def _instalar_autostart():
    _asegurar_config()
    nombre_tarea = "ClienteVoz"
    resultado = subprocess.run(
        ["schtasks", "/Create", "/TN", nombre_tarea, "/TR", _comando_autostart(),
         "/SC", "ONLOGON", "/F"],
        capture_output=True, text=True,
    )
    if resultado.returncode == 0:
        _mensaje_windows(
            "ClienteVoz",
            "Cliente de dictado instalado correctamente.\n\n"
            "Se iniciará automáticamente al iniciar sesión.\n"
            f"Servidor: {_servidor_por_defecto()}\n\n"
            "Para desinstalar: ClienteVoz.exe --desinstalar",
        )
    else:
        _mensaje_windows("ClienteVoz", "Error al instalar:\n" + resultado.stderr)


def _desinstalar_autostart():
    nombre_tarea = "ClienteVoz"
    resultado = subprocess.run(
        ["schtasks", "/Delete", "/TN", nombre_tarea, "/F"],
        capture_output=True, text=True,
    )
    if resultado.returncode == 0:
        _mensaje_windows("ClienteVoz", "Autostart eliminado correctamente.")
    else:
        _mensaje_windows("ClienteVoz", "No se pudo eliminar:\n" + resultado.stderr)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    # --- Modo hijo: el binario empaquetado se relanza a sí mismo ---
    # Congelado no hay scripts sueltos ni un Python del sistema con `gi`/`dbus`:
    # el mismo ejecutable hace de icono, de reproductor MPRIS y de ventana de
    # configuración cuando lo pide `cliente_voz.py` con estas banderas internas.
    # En modo fuente no se usan (los hijos se lanzan como scripts de siempre).
    if "--bandeja" in args:
        from bandeja_ubuntu import main as _main_bandeja
        sys.exit(_main_bandeja())
    if "--mpris" in args:
        from mpris_dictado import main as _main_mpris
        sys.exit(_main_mpris())
    if "--ventana" in args:
        from ventana_configuracion import main as _main_ventana
        sys.exit(_main_ventana())
    if "--indicador" in args:
        # Ventana flotante «REC» en su propio proceso: el cliente nunca crea
        # Tkinter dentro de sí mismo (2026-11-19).
        from indicador_grabacion import main as _main_indicador
        sys.exit(_main_indicador())

    # Alternancia rápida por CLI (Wayland / GNOME shortcuts)
    if "--toggle" in args or "--toggle-escribir" in args:
        modo = "escribir" if "--toggle-escribir" in args else "normal"
        if _enviar_toggle_cli(modo):
            sys.exit(0)
        else:
            print("Error: cliente_voz no se está ejecutando.")
            sys.exit(1)

    rotar_logs_al_iniciar()
    imprimir("=== CLIENTE DE SKIPKEY ===")

    # Sin sesión gráfica no hay ni icono ni teclado ni portapapeles: mejor
    # decirlo que reventar con un KeyError de pyautogui.
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        mensaje = (
            "SkipKey necesita una sesión gráfica (DISPLAY o WAYLAND_DISPLAY).\n"
            "Si lo lanzas por SSH o desde una terminal sin escritorio no va a "
            "funcionar: ábrelo desde el propio escritorio."
        )
        print(mensaje, file=sys.stderr)
        imprimir(f"[ERROR] {mensaje.replace(chr(10), ' ')}")
        sys.exit(1)

    _revisar_dependencias()

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

    primera_vez = _asegurar_config()
    if primera_vez:
        _avisar_primer_arranque()

    # Dirección del servidor: --servidor IP:PUERTO > DICTADO_SERVIDOR >
    # campo "servidor" del JSON > 127.0.0.1:5555.
    direccion = None
    for i, arg in enumerate(args):
        if arg == "--servidor" and i + 1 < len(args):
            direccion = f"tcp://{args[i + 1]}"
        elif arg.startswith("--servidor="):
            direccion = f"tcp://{arg.split('=', 1)[1]}"

    if direccion is None:
        direccion = f"tcp://{_servidor_por_defecto()}"

    cliente = ClienteVoz(direccion)
    cliente.ejecutar()
