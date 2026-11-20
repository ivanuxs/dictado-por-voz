"""Señales de activación al cliente de dictado en ejecución.

Traduce «activar el dictado» a la señal que el cliente ya entiende
(SIGUSR1 = dictado normal, SIGUSR2 = escribir donde está el cursor) y
localiza la instancia viva de tres formas: servicio systemd de usuario,
archivo PID o pgrep.

Además de las dos señales de dictado hay tres más para lo que se abre o se
cambia desde el icono (`bandeja_ubuntu.py`): escucha de la palabra clave,
comandos de voz y relectura de la configuración. Son de **tiempo real**
(SIGRTMIN+N) para no confundirse con SIGUSR1/SIGUSR2 ni con las que usa el
sistema.

Lo usan `cliente_voz.py` (opciones --toggle / --toggle-escribir),
`mpris_dictado.py` (botón del auricular Bluetooth), `bandeja_ubuntu.py` (menú
del icono) y `ventana_configuracion.py` (al guardar).

Este archivo a propósito no importa nada pesado: `mpris_dictado.py` y
`bandeja_ubuntu.py` se ejecutan con el Python del sistema, que no tiene los
paquetes del entorno virtual del cliente.
"""
import os
import signal
import subprocess

SERVICIO = "cliente-voz"

# El paquete instalado publica `skipkey.service`; el cliente de siempre,
# `cliente-voz.service`. Se prueban los dos para que el mismo código sirva en
# las dos instalaciones (parar uno que no existe no hace nada).
SERVICIOS = (SERVICIO, "skipkey")

# Interruptores del icono de la bandeja. `hasattr` porque no todas las
# plataformas tienen señales de tiempo real (Windows no).
SENAL_ESCUCHA = (signal.SIGRTMIN + 1) if hasattr(signal, "SIGRTMIN") else None
SENAL_COMANDOS = (signal.SIGRTMIN + 2) if hasattr(signal, "SIGRTMIN") else None
SENAL_RECARGAR = (signal.SIGRTMIN + 3) if hasattr(signal, "SIGRTMIN") else None
# Vuelve a enganchar el teclado global. Existe porque un gancho de bajo nivel
# puede quedarse sin entregar teclas y sin esto no habría forma de recuperarlo
# sin reiniciar el cliente (2026-11-19).
SENAL_TECLADO = (signal.SIGRTMIN + 4) if hasattr(signal, "SIGRTMIN") else None


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


def _es_el_cliente(linea):
    """True si esa línea de proceso es el cliente principal.

    No basta con que aparezca la palabra: `pgrep -f skipkey` también encuentra
    `timeout 60 skipkey`, un `tail -f …skipkey.log` o un editor con la ruta
    abierta, y mandarles SIGUSR1 los mata. Se exige que el **ejecutable** sea
    el cliente: el binario `skipkey` o un `python… cliente_voz.py`. Y se
    descartan los hijos del binario (`--bandeja`, `--mpris`, `--ventana`), que
    se llaman igual pero no entienden estas señales.
    """
    partes = (linea or "").split()
    if not partes:
        return False
    ejecutable = os.path.basename(partes[0])
    if ejecutable == "skipkey":
        paso = True
    elif ejecutable.startswith("python") and len(partes) > 1:
        paso = partes[1].endswith("cliente_voz.py")
    else:
        paso = False
    if not paso:
        return False
    return not any(
        bandera in linea
        for bandera in ("--bandeja", "--mpris", "--ventana", "--indicador")
    )


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
            # El cmdline separa los argumentos con NUL.
            linea = f.read().decode("utf-8", "replace").replace("\x00", " ").strip()
    except Exception:
        return None
    return pid if _es_el_cliente(linea) else None


def enviar_senal(numero):
    """Manda una señal concreta al cliente en ejecución. True si pudo.

    Misma estrategia que usaba `enviar_toggle`: archivo PID (lo más preciso),
    servicio systemd de usuario —**solo al proceso principal**, porque sin
    `--kill-who=main` moriría también `mpris_dictado.py`— y, en último caso,
    pgrep para un cliente lanzado a mano.
    """
    if numero is None:
        return False

    pid = _pid_del_cliente()
    if pid is not None:
        try:
            os.kill(pid, numero)
            return True
        except Exception:
            pass

    try:
        for servicio in SERVICIOS:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", servicio],
                capture_output=True,
            )
            if res.returncode == 0:
                subprocess.run(
                    [
                        "systemctl", "--user", "kill", "--kill-who=main",
                        "-s", str(numero), servicio,
                    ],
                    check=True,
                    capture_output=True,
                )
                return True
    except Exception:
        pass

    # Último recurso: pgrep. Se excluyen los hijos del binario empaquetado
    # (`--bandeja`, `--mpris`, `--ventana`): se llaman igual que el proceso
    # principal, pero no entienden estas señales y un SIGUSR1 los mataría.
    for patron in ("python3.*cliente_voz.py", "skipkey"):
        try:
            res = subprocess.run(
                ["pgrep", "-af", patron], capture_output=True, text=True
            )
        except Exception:
            continue
        enviados = 0
        for linea in res.stdout.splitlines():
            partes = linea.split(None, 1)
            if len(partes) != 2:
                continue
            try:
                pid = int(partes[0])
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            if not _es_el_cliente(partes[1]):
                continue
            try:
                os.kill(pid, numero)
                enviados += 1
            except Exception:
                pass
        if enviados:
            return True

    return False


def enviar_toggle(modo="normal"):
    """Pide al cliente en ejecución que empiece o termine un dictado.

    Devuelve True si se pudo avisar a alguna instancia.
    """
    nombre = nombre_senal(modo)
    numero = getattr(signal, nombre, None)
    if numero is None:  # Windows no tiene SIGUSR1
        return False
    return enviar_senal(numero)


def enviar_escucha():
    """Enciende o apaga la escucha de la palabra clave (menú del icono)."""
    return enviar_senal(SENAL_ESCUCHA)


def enviar_comandos():
    """Pausa o reanuda los comandos de voz (menú del icono)."""
    return enviar_senal(SENAL_COMANDOS)


def enviar_recargar():
    """Pide al cliente que relea comandos.json (lo hace la ventana al guardar)."""
    return enviar_senal(SENAL_RECARGAR)


def enviar_teclado():
    """Vuelve a levantar el gancho de teclado (menú del icono)."""
    return enviar_senal(SENAL_TECLADO)
