"""Señales de activación al cliente de dictado en ejecución.

Traduce «activar el dictado» a la señal que el cliente ya entiende
(SIGUSR1 = dictado normal, SIGUSR2 = escribir donde está el cursor) y
localiza la instancia viva de tres formas: servicio systemd de usuario,
archivo PID o pgrep.

Lo usan `cliente_voz.py` (opciones --toggle / --toggle-escribir) y
`mpris_dictado.py` (botón del auricular Bluetooth).

Este archivo a propósito no importa nada pesado: `mpris_dictado.py` se
ejecuta con el Python del sistema, que no tiene los paquetes del entorno
virtual del cliente.
"""
import os
import signal
import subprocess

SERVICIO = "cliente-voz"


def obtener_ruta_pid():
    """Ruta del archivo PID del cliente (la misma que escribe cliente_voz.py)."""
    directorio = os.environ.get("XDG_RUNTIME_DIR")
    if not directorio or not os.path.isdir(directorio):
        directorio = os.path.expanduser("~/.cache")
        os.makedirs(directorio, exist_ok=True)
    return os.path.join(directorio, "cliente_voz.pid")


def nombre_senal(modo):
    """Nombre POSIX de la señal que corresponde al modo pedido."""
    return "SIGUSR1" if modo == "normal" else "SIGUSR2"


def _pid_del_cliente():
    """PID del cliente si el archivo PID apunta de verdad a él, o None.

    Se comprueba el cmdline del proceso porque un PID reciclado por otro
    programa haría que le enviáramos la señal a quien no debe.
    """
    try:
        with open(obtener_ruta_pid(), "r") as f:
            pid = int(f.read().strip())
    except Exception:
        return None
    if pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            linea = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    return pid if "cliente_voz.py" in linea else None


def enviar_toggle(modo="normal"):
    """Pide al cliente en ejecución que empiece o termine un dictado.

    Devuelve True si se pudo avisar a alguna instancia.
    """
    nombre = nombre_senal(modo)
    numero = getattr(signal, nombre, None)
    if numero is None:  # Windows no tiene SIGUSR1
        return False

    # 1. El PID del cliente: lo más preciso, no toca a ningún otro proceso
    pid = _pid_del_cliente()
    if pid is not None:
        try:
            os.kill(pid, numero)
            return True
        except Exception:
            pass

    # 2. Servicio systemd de usuario, SOLO al proceso principal.
    #    Sin `--kill-who=main`, systemctl señala a TODO el grupo de procesos del
    #    servicio y ahí vive también mpris_dictado.py, que moriría en cada atajo.
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", SERVICIO],
            capture_output=True,
        )
        if res.returncode == 0:
            subprocess.run(
                [
                    "systemctl", "--user", "kill", "--kill-who=main",
                    "-s", nombre, SERVICIO,
                ],
                check=True,
                capture_output=True,
            )
            return True
    except Exception:
        pass

    # 3. Cliente lanzado a mano: buscarlo con pgrep
    try:
        res = subprocess.run(
            ["pgrep", "-f", "python3.*cliente_voz.py"],
            capture_output=True,
            text=True,
        )
        pids = [int(p) for p in res.stdout.split() if p and int(p) != os.getpid()]
        for pid in pids:
            try:
                os.kill(pid, numero)
            except Exception:
                pass
        return bool(pids)
    except Exception:
        pass

    return False
