"""Cliente de dictado por voz — ZMQ DEALER.

Captura audio del micrófono, lo envía al servidor, recibe texto transcrito
y comandos de voz, y los ejecuta localmente (inyección de texto, apertura
de aplicaciones, control de volumen, TTS, etc.).
"""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata

import numpy as np
import pyautogui
import pyperclip
import sounddevice as sd
import zmq
from pynput import keyboard

from protocolo import (
    AUDIO, DICTADO_START, DICTADO_STOP,
    TEXTO, TEXTO_PARCIAL, FIN_DICTADO, COMANDO, PING, PONG, ESTADO,
    CHUNK_MUESTRAS,
    crear_audio, crear_dictado_start, crear_dictado_start_vivo,
    crear_dictado_stop, crear_ping,
    decodificar_texto,
)
from senal_cliente import enviar_toggle, obtener_ruta_pid as _ruta_pid_compartida
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
AVISO_AUDIO_SEGUNDOS = 30            # avisar en pantalla tras este tiempo


def _directorio_base():
    """Directorio persistente: junto al exe (congelado) o junto al script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _nombre_config():
    return "comandos_windows.json" if sys.platform == "win32" else "comandos.json"


RUTA_JSON = os.path.join(_directorio_base(), _nombre_config())
RUTA_INDICADOR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indicador_grabacion.py"
)

# --- Botón del auricular Bluetooth (AVRCP -> MPRIS) ---
# El botón principal de un auricular Bluetooth no llega como tecla: viaja por
# AVRCP y BlueZ se lo entrega al reproductor MPRIS publicado en la sesión (lo
# hace `mpris-proxy`). Si no hay ninguno, el botón no hace nada. Por eso el
# cliente publica un reproductor «Dictado por Voz» con mpris_dictado.py, que se
# ejecuta con el Python DEL SISTEMA: necesita dbus-python, que no está en el
# entorno virtual del cliente. Ver AGENTS.md.
RUTA_MPRIS = os.path.join(_directorio_base(), "mpris_dictado.py")
PYTHON_SISTEMA = "/usr/bin/python3"


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
#             >  campo "servidor" de comandos.json  >  este valor.
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


def procesar_comando(texto):
    """Detecta si el texto es un comando de voz y lo ejecuta.
    Retorna True si era un comando, False si es dictado normal."""
    texto_normalizado = (
        unicodedata.normalize("NFKD", texto.lower().strip())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    texto_normalizado = re.sub(r"[,.:;!¡¿?]", "", texto_normalizado)
    if not texto_normalizado.startswith("computadora "):
        return False

    resto = texto_normalizado[12:].strip()
    partes = resto.split(maxsplit=1)

    if len(partes) < 1:
        return True

    accion = partes[0]
    resto_comando = partes[1] if len(partes) > 1 else ""

    # --- Comandos de aplicación ---
    if accion in ("abre", "cierra", "cerrar"):
        accion_canonica = "cerrar" if accion in ("cierra", "cerrar") else "abre"
        if not resto_comando:
            imprimir(f"[TOCINA] ¿{'Abrir' if accion == 'abre' else 'Cerrar'} qué?")
            return True

        try:
            with open(RUTA_JSON, "r", encoding="utf-8") as f:
                comandos = json.load(f)
        except Exception:
            imprimir("[TOCINA] No se pudo leer la configuración de comandos.")
            return True

        if accion_canonica not in comandos:
            imprimir(f"[TOCINA] Acción '{accion}' no reconocida.")
            return True

        if resto_comando not in comandos[accion_canonica]:
            imprimir(f"[TOCINA] Programa '{resto_comando}' no encontrado en '{accion}'")
            return True

        objetivo = comandos[accion_canonica][resto_comando]

        if accion_canonica == "abre":
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
        elif accion_canonica == "cerrar":
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

    # --- Comandos de sistema ---
    if resto in ("sube volumen", "subir volumen", "aumenta volumen"):
        if sys.platform == "win32":
            _controlar_volumen_windows("subir")
        else:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Volumen +10%")
        return True
    if resto in ("baja volumen", "bajar volumen", "reduce volumen"):
        if sys.platform == "win32":
            _controlar_volumen_windows("bajar")
        else:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Volumen -10%")
        return True
    if resto in ("silencia", "silencio", "mute", "mudo"):
        if sys.platform == "win32":
            _controlar_volumen_windows("silenciar")
        else:
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Silencio alternado")
        return True
    if resto in ("reproduce", "reproducir", "play", "pausa", "pausar"):
        if sys.platform == "win32":
            pyautogui.press("playpause")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioPlay"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Reproducir/Pausa")
        return True
    if resto in ("siguiente", "siguiente cancion", "next"):
        if sys.platform == "win32":
            pyautogui.press("nexttrack")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioNext"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Siguiente")
        return True
    if resto in ("anterior", "anterior cancion", "prev"):
        if sys.platform == "win32":
            pyautogui.press("prevtrack")
        else:
            subprocess.run(
                ["xdotool", "key", "XF86AudioPrev"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Anterior")
        return True
    if resto in ("captura pantalla", "capturar pantalla", "pantallazo"):
        if sys.platform == "win32":
            pyautogui.press("printscreen")
        else:
            subprocess.run(
                ["xdotool", "key", "Print"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        imprimir("[TOCINA] Captura de pantalla")
        return True
    if resto in ("bloquea pantalla", "bloquear pantalla", "bloquear"):
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
    if resto in ("apaga", "apagar", "apagar equipo"):
        imprimir("[TOCINA] Apagado deshabilitado por seguridad. Hazlo manualmente.")
        return True
    if resto in ("reinicia", "reiniciar", "reiniciar equipo"):
        imprimir("[TOCINA] Reinicio deshabilitado por seguridad. Hazlo manualmente.")
        return True

    # --- Alarma ---
    _NUMEROS_TEXTO = {
        "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
        "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
        "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
        "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
        "dieciocho": 18, "diecinueve": 19, "veinte": 20,
        "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
        "media": 0.5,
    }

    match_alarma = re.match(
        r"(?:pon|coloca|con)\s+(?:un(?:a)?\s+)?alarma\s+"
        r"(?:de\s+)?(?:en\s+)?(?:dentro\s+de\s+)?"
        r"(\d+|[a-z]+)\s*"
        r"(segundos?|minutos?|horas?)",
        resto,
    )
    if match_alarma:
        cantidad_str = match_alarma.group(1)
        if cantidad_str.isdigit():
            cantidad = int(cantidad_str)
        else:
            cantidad = _NUMEROS_TEXTO.get(cantidad_str)
        if cantidad is None:
            return True
        unidad = match_alarma.group(2)
        if unidad.startswith("segundo"):
            segundos = cantidad
        elif unidad.startswith("minuto"):
            segundos = cantidad * 60
        else:  # hora
            segundos = cantidad * 3600

        imprimir(f"[TOCINA] ⏰ Alarma programada en {cantidad} {unidad}")
        from voz_asistente import hablar
        threading.Thread(
            target=hablar,
            args=(f"Alarma programada en {cantidad} {unidad}",),
            daemon=True,
        ).start()

        def _sonar_alarma():
            time.sleep(segundos)
            imprimir(f"[TOCINA] ⏰ ¡Alarma! ({cantidad} {unidad})")
            reproducir_sonido("alarma")
            from voz_asistente import hablar

            hablar(f"Alarma de {cantidad} {unidad}")

        threading.Thread(target=_sonar_alarma, daemon=True).start()
        return True

    # --- Asistente conversacional ---
    from voz_asistente import _buscar_comando_simple, hablar, preguntar_deepseek

    # 1. Comandos simples predefinidos
    respuesta = _buscar_comando_simple(resto)
    if respuesta:
        imprimir(f"[TOCINA] → {respuesta}")
        threading.Thread(target=hablar, args=(respuesta,), daemon=True).start()
        return True

    # 2. DeepSeek (solo con palabra clave "consulta global")
    if resto.startswith("consulta global "):
        consulta = resto[len("consulta global "):].strip()
        if consulta:
            imprimir(f"[TOCINA] Preguntando a DeepSeek: '{consulta}'")
            respuesta = preguntar_deepseek(consulta)
            if respuesta:
                imprimir(f"[TOCINA] DeepSeek → {respuesta}")
                threading.Thread(target=hablar, args=(respuesta,), daemon=True).start()
            else:
                fallback = (
                    "Lo siento, no puedo responder en este momento. "
                    "La clave de DeepSeek no está configurada o no hay conexión."
                )
                imprimir(f"[TOCINA] {fallback}")
                threading.Thread(target=hablar, args=(fallback,), daemon=True).start()
        return True

    # Comando no reconocido
    imprimir(f"[TOCINA] Comando no reconocido: '{resto}'")
    return True


# ---------------------------------------------------------------------------
# Indicador visual de grabación
# ---------------------------------------------------------------------------

def _mostrar_indicador():
    global _indicador_proceso, _indicador_ventana
    if getattr(sys, "frozen", False):
        # Ejecutable empaquetado: no hay python separado → ventana en un hilo
        if _indicador_ventana is None:
            from indicador_grabacion import iniciar_indicador
            _indicador_ventana = iniciar_indicador()
    else:
        if _indicador_proceso is None:
            _indicador_proceso = subprocess.Popen(
                [sys.executable, RUTA_INDICADOR],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _ocultar_indicador():
    global _indicador_proceso, _indicador_ventana
    if _indicador_ventana is not None:
        try:
            _indicador_ventana.detener()
        except Exception:
            pass
        _indicador_ventana = None
    if _indicador_proceso is not None:
        try:
            _indicador_proceso.terminate()
            _indicador_proceso.wait(timeout=5)
        except Exception:
            pass
        _indicador_proceso = None


_indicador_proceso = None
_indicador_ventana = None


# ---------------------------------------------------------------------------
# Cliente principal
# ---------------------------------------------------------------------------

class ClienteVoz:
    def __init__(self, direccion_servidor=SERVIDOR):
        self._direccion = direccion_servidor
        self._ctx = zmq.Context()
        self._socket = self._ctx.socket(zmq.DEALER)

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
                proceso = subprocess.Popen(
                    [PYTHON_SISTEMA, RUTA_MPRIS],
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
        self._cola_eventos.put("toggle_normal")

    def _manejador_sigusr2(self, signum, frame):
        self._cola_eventos.put("toggle_escribir")

    def _alternar_grabacion(self, modo="normal"):
        """Inicia o detiene la grabación en modo toggle."""
        if self._inyectando:
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
                self._grabando = True
                self._tecla_suelta = False
                imprimir("\n[GRABANDO... Mantén presionada la tecla activadora]")
                try:
                    _mostrar_indicador()
                except Exception as e:
                    imprimir(f"[INDICADOR] No se pudo mostrar: {e}")
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
            try:
                _ocultar_indicador()
            except Exception as e:
                imprimir(f"[INDICADOR] No se pudo ocultar: {e}")
            imprimir("[DETENIDO. Procesando...]")
            self._cola_eventos.put("stop")

    # --- Procesar mensajes del servidor ---

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

        if tipo == TEXTO:
            self._esperando_transcripcion = False
            texto = decodificar_texto(frames)
            imprimir(f"Texto detectado: {texto}")
            if procesar_comando(texto):
                pass  # Era un comando de voz: ya se ejecutó
            elif self._escribir_este_dictado:
                # Modo "escribir": el texto aparece donde está el cursor
                self._cola_escritura.put(texto)
                reproducir_sonido("listo")
            else:
                # Dictado normal → al portapapeles para pegar con Ctrl+V
                pyperclip.copy(texto + " ")
                imprimir("[PORTAPAPELES] Texto copiado. Pega donde quieras con Ctrl+V.")
                reproducir_sonido("listo")

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
            reproducir_sonido("listo")

        elif tipo == COMANDO:
            self._esperando_transcripcion = False
            texto = decodificar_texto(frames)
            imprimir(f"[VOZ] Comando detectado: '{texto}'")
            threading.Thread(
                target=procesar_comando, args=(texto,), daemon=True
            ).start()

        elif tipo == ESTADO:
            texto = decodificar_texto(frames)
            imprimir(f"[SERVIDOR] {texto}")
            # Si esperábamos una transcripción, este aviso ES la respuesta
            # del dictado: el servidor no devolvió texto (no llegó audio o
            # no se detectó voz). Hay que decírselo al usuario en pantalla,
            # porque el ejecutable de Windows no tiene consola donde verlo.
            if self._esperando_transcripcion:
                self._esperando_transcripcion = False
                _avisar_usuario("Dictado sin texto", texto)

        elif tipo == PONG:
            pass  # Silencioso, solo confirma que el servidor está vivo

    # --- Bucle principal ---

    def ejecutar(self):
        self._socket.connect(self._direccion)
        imprimir(f"[CLIENTE] Conectado a {self._direccion}")

        # Guardar PID para control externo (señales / CLI)
        self._guardar_pid()

        # Configurar señales POSIX para control externo (Wayland / GNOME shortcuts)
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, self._manejador_sigusr1)
        if hasattr(signal, "SIGUSR2"):
            signal.signal(signal.SIGUSR2, self._manejador_sigusr2)

        # Iniciar escucha de teclado ANTES que el audio: si el micrófono
        # falla, la activación por teclado debe seguir funcionando.
        try:
            listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            listener.start()
            imprimir("[CLIENTE] Escucha de teclado iniciada.")
        except Exception as e:
            imprimir(f"[CLIENTE] No se pudo iniciar el listener de pynput: {e}")

        # Hebra que escribe en el cursor los textos dictados (en orden)
        threading.Thread(target=self._bucle_escritura, daemon=True).start()

        # Reproductor MPRIS: es lo que permite que el botón del auricular
        # Bluetooth active el dictado (el botón viaja por AVRCP, no como tecla).
        if self._mpris_disponible():
            threading.Thread(target=self._bucle_mpris, daemon=True).start()

        # Iniciar captura de audio (stream continuo para Vosk). Si falla, el
        # cliente sigue vivo (teclado, texto y comandos) y la reintenta en el
        # bucle principal hasta que el sistema de audio esté disponible.
        stream = self._abrir_stream_audio()
        if stream is None:
            imprimir(
                "[AUDIO] La captura no arrancó todavía; se reintentará cada "
                f"{REINTENTO_AUDIO_SEGUNDOS:.0f} s."
            )

        # Verificar conectividad con el servidor (fire-and-forget)
        self._socket.send_multipart(crear_ping())

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
                    self._socket.send_multipart(crear_ping())
                    self._ultimo_ping = ahora

        except KeyboardInterrupt:
            imprimir("[CLIENTE] Detenido por el usuario.")
        finally:
            self._activo = False
            self._limpiar_pid()
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
                listener.stop()
            except Exception:
                pass
            try:
                _ocultar_indicador()
            except Exception:
                pass
            self._socket.close()
            self._ctx.term()

    def _enviar_audio(self):
        """Envía chunks de audio acumulados al servidor."""
        enviados = 0
        while enviados < 20:  # Máximo 20 chunks por iteración (~1.3 s de audio)
            try:
                chunk = self._cola_audio.get_nowait()
                self._socket.send_multipart(crear_audio(chunk))
                enviados += 1
            except queue.Empty:
                break

    def _procesar_eventos(self):
        """Procesa eventos de teclado encolados."""
        while True:
            try:
                evento = self._cola_eventos.get_nowait()
                if evento == "toggle_normal":
                    self._alternar_grabacion("normal")
                elif evento == "toggle_escribir":
                    self._alternar_grabacion("escribir")
                elif evento == "start":
                    if self._escribir_este_dictado:
                        # Dictado en vivo: el servidor manda el texto por frases
                        imprimir("[CLIENTE] Enviando DICTADO_START en vivo al servidor...")
                        self._socket.send_multipart(crear_dictado_start_vivo())
                    else:
                        imprimir("[CLIENTE] Enviando DICTADO_START al servidor...")
                        self._socket.send_multipart(crear_dictado_start())
                elif evento == "stop":
                    imprimir("[CLIENTE] Enviando DICTADO_STOP al servidor. Esperando transcripción...")
                    self._socket.send_multipart(crear_dictado_stop())
                    self._esperando_transcripcion = True
                    self._transcripcion_inicio = time.time()
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

    # Alternancia rápida por CLI (Wayland / GNOME shortcuts)
    if "--toggle" in args or "--toggle-escribir" in args:
        modo = "escribir" if "--toggle-escribir" in args else "normal"
        if _enviar_toggle_cli(modo):
            sys.exit(0)
        else:
            print("Error: cliente_voz no se está ejecutando.")
            sys.exit(1)

    rotar_logs_al_iniciar()
    imprimir("=== CLIENTE DE DICTADO POR VOZ ===")

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

    _asegurar_config()

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
