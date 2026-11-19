"""Configuración del puente web, leída de variables de entorno.

Los secretos NO van en el repositorio: el servicio los carga desde
~/.config/puente-web.env (ver puente_web/.env.example).
"""
import os
from pathlib import Path


def _entero(nombre, por_defecto):
    """Lee una variable entera; si no es válida, usa el valor por defecto."""
    try:
        return int(os.environ.get(nombre, por_defecto))
    except (TypeError, ValueError):
        return por_defecto


# Token que deben presentar los clientes (cabecera Authorization: Bearer).
# Si está vacío el puente se niega a arrancar: quedaría expuesto sin control.
TOKEN_WEB = os.environ.get("DICTADO_WEB_TOKEN", "").strip()

# Servidor de dictado (ZMQ). Por defecto, el mismo equipo.
DIRECCION_ZMQ = os.environ.get("DICTADO_ZMQ", "tcp://127.0.0.1:5555")
# Token del servidor de dictado (DICTADO_TOKEN en su unidad). Suele estar vacío.
TOKEN_ZMQ = os.environ.get("DICTADO_ZMQ_TOKEN", "").strip()

# Escucha del puente. 0.0.0.0 para que lo alcance el proxy de la red local;
# el firewall (ufw) decide quién puede entrar.
HOST = os.environ.get("DICTADO_PUENTE_HOST", "0.0.0.0")
PUERTO = _entero("DICTADO_PUENTE_PUERTO", 8000)

# Límites que protegen la GPU (una sola, de 6 GB, compartida con el dictado
# de la PC y con los lotes de audios largos).
MAX_SEGUNDOS = _entero("DICTADO_MAX_SEGUNDOS", 60)
MAX_MB = _entero("DICTADO_MAX_MB", 5)
TAMANO_MAXIMO = MAX_MB * 1024 * 1024
# Peticiones REST simultáneas (las que esperan en cola; la transcripción en sí
# siempre es de una en una, porque el modelo es único).
CONCURRENTES = _entero("DICTADO_CONCURRENTES", 2)
ESPERA_MAXIMA = _entero("DICTADO_ESPERA_MAXIMA", 30)
TIMEOUT_TRANSCRIPCION = _entero("DICTADO_TIMEOUT", 120)

# Modelo para los parciales del dictado en vivo. Solo afecta a este puente, es
# decir, al teclado del celular: el servidor lo recibe en el DICTADO_START
# (`vivo:medium`) y los clientes de escritorio siguen con el modelo principal.
# Vacío = el que tenga configurado el servidor. Medido con el micrófono de un
# celular: small ~0,7 s por frase pero inventa palabras, medium ~1,7 s y
# acierta casi todo, large-v3 ~2,6 s y es exacto.
MODELO_PARCIALES = os.environ.get("DICTADO_MODELO_PARCIALES", "").strip().lower()

# ---------------------------------------------------------------------------
# Traducción (Opus-MT en CPU, DENTRO del puente)
#
# No toca el servidor de dictado ni la GPU: son modelos Marian pequeños
# (~80 MB cada uno en int8) que se cargan bajo demanda. Los ficheros se generan
# con herramientas/preparar_modelos.py y no se versionan.
# ---------------------------------------------------------------------------
TRADUCCION = os.environ.get("DICTADO_TRADUCCION", "1").strip().lower() not in (
    "0", "no", "false", "apagado"
)

# Carpeta con los modelos convertidos (por defecto, puente_web/modelos).
MODELOS_DIR = os.environ.get(
    "DICTADO_MODELOS", str(Path(__file__).resolve().parent / "modelos")
)

# Lo que se puede traducir de una vez (el teclado manda frases, no libros).
MAX_CARACTERES_TRADUCCION = _entero("DICTADO_MAX_CARACTERES", 2000)

# Hilos de CPU por traducción. Dos, para no quitarle CPU a Whisper cuando una
# transcripción y una traducción coinciden.
HILOS_TRADUCCION = _entero("DICTADO_HILOS_TRADUCCION", 2)

# Modelos que se mantienen cargados a la vez (~300 MB de RAM cada uno). Con 6,
# casi todos los pares caben y la traducción no vuelve a pagar 10 s de carga
# cuando se ha usado otro idioma antes (medido el 2026-11-01: es-en se desalojó
# con el límite de 4 y la siguiente traducción tardó 10,9 s).
MODELOS_EN_MEMORIA = _entero("DICTADO_MODELOS_EN_MEMORIA", 6)

# ---------------------------------------------------------------------------
# Cuentas de usuario
#
# La app se reparte por Google Play y cualquiera puede crearse una cuenta: por
# eso el registro está ABIERTO. La base de datos NO se versiona (lleva correos
# y hashes de contraseñas): ver .gitignore → puente_web/datos/.
# ---------------------------------------------------------------------------
DATOS_DIR = os.environ.get(
    "DICTADO_DATOS", str(Path(__file__).resolve().parent / "datos")
)

# Base de datos de cuentas, tokens, uso del día y (más adelante) sincronización.
BD = os.environ.get("DICTADO_BD", str(Path(DATOS_DIR) / "usuarios.db"))

# Registro abierto = cualquiera puede crear cuenta desde la app. Se puede
# cerrar en pruebas con DICTADO_REGISTRO=cerrado.
REGISTRO = os.environ.get("DICTADO_REGISTRO", "abierto").strip().lower() not in (
    "0", "no", "false", "cerrado", "apagado"
)

# Plan de una cuenta recién creada.
PLAN_POR_DEFECTO = os.environ.get("DICTADO_PLAN_POR_DEFECTO", "gratis").strip().lower()

# Días que vale el token de un dispositivo antes de volver a pedir la
# contraseña. Largo a propósito: es un teclado, no un banco.
TOKEN_DIAS = _entero("DICTADO_TOKEN_DIAS", 365)

# --- Correo (código de verificación) ---------------------------------------
#
# Sirve igual un Gmail con contraseña de aplicación que un SMTP propio: solo
# cambian host, puerto y el modo de TLS.
#   Gmail:  host smtp.gmail.com, puerto 587, TLS starttls,
#           usuario = la cuenta, clave = contraseña de aplicación (16 letras).
#   Propio: host y puerto del servidor, TLS starttls (587) o ssl (465).
SMTP_HOST = os.environ.get("DICTADO_SMTP_HOST", "").strip()
SMTP_PUERTO = _entero("DICTADO_SMTP_PUERTO", 587)
SMTP_USUARIO = os.environ.get("DICTADO_SMTP_USUARIO", "").strip()
SMTP_CLAVE = os.environ.get("DICTADO_SMTP_CLAVE", "").strip()
SMTP_TLS = os.environ.get("DICTADO_SMTP_TLS", "starttls").strip().lower()
SMTP_DE = os.environ.get("DICTADO_SMTP_DE", "").strip() or SMTP_USUARIO

# ¿Hay que confirmar el correo para usar la cuenta? Por defecto, sí en cuanto
# haya SMTP configurado; sin SMTP no se puede verificar a nadie y el puente
# arranca avisándolo (modo pruebas). Se fuerza con DICTADO_VERIFICAR_CORREO.
if os.environ.get("DICTADO_VERIFICAR_CORREO", "").strip().lower() in (
    "0", "no", "false", "apagado"
):
    VERIFICAR_CORREO = False
elif os.environ.get("DICTADO_VERIFICAR_CORREO", "").strip().lower() in (
    "1", "si", "sí", "yes", "true", "encendido"
):
    VERIFICAR_CORREO = True
else:
    VERIFICAR_CORREO = bool(SMTP_HOST and SMTP_USUARIO and SMTP_CLAVE)

# Minutos que vale el código que se manda por correo.
CODIGO_MINUTOS = _entero("DICTADO_CODIGO_MINUTOS", 30)
