"""Configuración del puente web, leída de variables de entorno.

Los secretos NO van en el repositorio: el servicio los carga desde
~/.config/puente-web.env (ver puente_web/.env.example).
"""
import os


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
