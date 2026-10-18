"""Publica el cliente de dictado como reproductor MPRIS (botón del auricular).

¿Por qué hace falta?
    El botón principal de un auricular Bluetooth NO llega como tecla: viaja
    por AVRCP y BlueZ lo entrega al reproductor MPRIS registrado (lo hace
    `mpris-proxy`, que ya corre como servicio de usuario). Si no hay ningún
    reproductor MPRIS, el botón no hace absolutamente nada: es lo que pasaba
    con el SOUNDPEATS RunFree Lite del usuario. `pynput` nunca lo verá, por
    eso `media_play_pause` en comandos.json no puede funcionar.

Qué hace este archivo:
    Publica un reproductor MPRIS llamado «Dictado por Voz». Al pulsar el
    botón del auricular:
      - un toque  (PlayPause / Play / Pause) -> SIGUSR1: dictado normal.
      - dos toques (Next / Previous)         -> SIGUSR2: escribir en el cursor.

Modos (variable de entorno DICTADO_MPRIS_MODO):
    ambos          (por defecto) el botón activa el dictado Y reenvía el
                   mismo comando al reproductor de música, de modo que la
                   música se pausa y el dictado empieza a la vez.
    sin_interferir el reproductor se declara «Stopped», así que con música
                   sonando el botón sigue siendo de la música; solo cuando
                   no hay nada sonando activa el dictado.

Con DICTADO_MPRIS_DOBLE=no se ignora el botón doble (Next/Previous).

Se ejecuta con el Python DEL SISTEMA (`/usr/bin/python3`), no con el entorno
virtual del cliente: necesita `dbus-python`, que viene en los paquetes de
Ubuntu y no está en `entorno_dictado/`. Lo lanza y vigila `cliente_voz.py`.
"""
import os
import signal
import subprocess
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from senal_cliente import enviar_toggle

NOMBRE = "org.mpris.MediaPlayer2.dictado"
RUTA = "/org/mpris/MediaPlayer2"
IFAZ_RAIZ = "org.mpris.MediaPlayer2"
IFAZ_REP = "org.mpris.MediaPlayer2.Player"
IFAZ_PROPS = "org.freedesktop.DBus.Properties"

MODO = os.environ.get("DICTADO_MPRIS_MODO", "ambos").strip().lower()
DOBLE = os.environ.get("DICTADO_MPRIS_DOBLE", "si").strip().lower()
USAR_DOBLE = DOBLE not in ("0", "no", "false", "off")


def _escribir(mensaje):
    """Aviso al log del cliente (su salida se reenvía a utils_logging)."""
    print(f"[MPRIS] {mensaje}", flush=True)


def _pid_mpris_proxy():
    """PID del servicio mpris-proxy, para no reenviarle sus propios comandos."""
    try:
        res = subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", "mpris-proxy"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return int(res.stdout.strip() or 0)
    except Exception:
        return 0


class ReproductorDictado(dbus.service.Object):
    """Reproductor MPRIS mínimo cuyo único trabajo es dar los botones al cliente."""

    def __init__(self, bus):
        super().__init__(bus, RUTA)
        self._bus = bus
        self._proxy_pid = _pid_mpris_proxy()

    # --- Propiedades -------------------------------------------------------

    def _propiedades(self, interfaz):
        """Propiedades de la interfaz pedida ({} si no la conocemos).

        Devolver {} en vez de un error es importante: mpris-proxy reenvía a
        este objeto las consultas que hace BlueZ (con nombres de la interfaz
        org.bluez.MediaPlayer1) y no debe recibir un fallo.
        """
        if interfaz == IFAZ_REP:
            estado = "Playing" if MODO == "ambos" else "Stopped"
            metadatos = dbus.Dictionary(
                {
                    "mpris:trackid": dbus.ObjectPath(
                        "/org/mpris/MediaPlayer2/TrackList/NoTrack",
                        variant_level=1,
                    ),
                    "xesam:title": dbus.String("Dictado por Voz", variant_level=1),
                    "xesam:artist": dbus.Array(
                        [dbus.String("Dictado por Voz")],
                        signature="s",
                        variant_level=1,
                    ),
                },
                signature="sv",
                variant_level=1,
            )
            return {
                "PlaybackStatus": dbus.String(estado, variant_level=1),
                "LoopStatus": dbus.String("None", variant_level=1),
                "Rate": dbus.Double(1.0, variant_level=1),
                "MinimumRate": dbus.Double(1.0, variant_level=1),
                "MaximumRate": dbus.Double(1.0, variant_level=1),
                "Shuffle": dbus.Boolean(False, variant_level=1),
                "Metadata": metadatos,
                "Volume": dbus.Double(1.0, variant_level=1),
                "Position": dbus.Int64(0, variant_level=1),
                "CanGoNext": dbus.Boolean(True, variant_level=1),
                "CanGoPrevious": dbus.Boolean(True, variant_level=1),
                "CanPlay": dbus.Boolean(True, variant_level=1),
                "CanPause": dbus.Boolean(True, variant_level=1),
                "CanSeek": dbus.Boolean(False, variant_level=1),
                "CanControl": dbus.Boolean(True, variant_level=1),
            }
        if interfaz == IFAZ_RAIZ:
            return {
                "CanQuit": dbus.Boolean(False, variant_level=1),
                "CanRaise": dbus.Boolean(False, variant_level=1),
                "HasTrackList": dbus.Boolean(False, variant_level=1),
                "Identity": dbus.String("Dictado por Voz", variant_level=1),
                "DesktopEntry": dbus.String("", variant_level=1),
                "SupportedUriSchemes": dbus.Array([], signature="s", variant_level=1),
                "SupportedMimeTypes": dbus.Array([], signature="s", variant_level=1),
            }
        return {}

    @dbus.service.method(IFAZ_PROPS, in_signature="ss", out_signature="v")
    def Get(self, interfaz, propiedad):
        valores = self._propiedades(interfaz)
        if propiedad not in valores:
            raise dbus.exceptions.DBusException(
                f"No existe la propiedad {propiedad} en {interfaz}",
                name="org.freedesktop.DBus.Error.UnknownProperty",
            )
        return valores[propiedad]

    @dbus.service.method(IFAZ_PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interfaz):
        return self._propiedades(interfaz)

    @dbus.service.method(IFAZ_PROPS, in_signature="ssv", out_signature="")
    def Set(self, interfaz, propiedad, valor):
        raise dbus.exceptions.DBusException(
            "Las propiedades de este reproductor son de solo lectura",
            name="org.freedesktop.DBus.Error.PropertyReadOnly",
        )

    # --- Botón del auricular ----------------------------------------------

    def _boton(self, modo, miembro):
        """Actúa como si el cliente hubiera recibido su tecla activadora.

        `miembro` es el nombre MPRIS que llegó (PlayPause, Play, Pause, Next…)
        y solo se usa para reenviarlo al reproductor de música en el modo
        «ambos»: así el mismo toque pausa la música y arranca el dictado.
        """
        destino = "escribir en el cursor" if modo == "escribir" else "dictado normal"
        _escribir(f"botón del auricular: {miembro} -> {destino}")
        if not enviar_toggle(modo):
            _escribir("no se encontró el cliente de dictado en ejecución")
        if MODO == "ambos":
            self._reenviar(miembro)

    def _es_no_nuestro(self, nombre):
        """True si el nombre MPRIS es de otro reproductor (no de mpris-proxy).

        mpris-proxy publica en la sesión los reproductores del dispositivo
        remoto (el propio auricular / el celular). Reenviarle un comando
        mandaría un AVRCP de vuelta al auricular, así que se descarta
        comparando el PID del dueño del nombre.
        """
        if self._proxy_pid:
            try:
                if self._bus.get_unix_process_id(nombre) == self._proxy_pid:
                    return False
            except Exception:
                pass
        return True

    def _reenviar(self, miembro):
        """Repite el comando al reproductor de música real, si hay alguno."""
        try:
            nombres = [
                n
                for n in self._bus.list_names()
                if n.startswith("org.mpris.MediaPlayer2.") and n != NOMBRE
            ]
        except Exception as e:
            _escribir(f"no se pudo listar reproductores: {e}")
            return

        candidatos = []
        for nombre in nombres:
            if not self._es_no_nuestro(nombre):
                continue
            try:
                props = dbus.Interface(
                    self._bus.get_object(nombre, RUTA), IFAZ_PROPS
                )
                estado = str(props.Get(IFAZ_REP, "PlaybackStatus"))
            except Exception:
                continue
            # Preferir el que está sonando; luego el pausado; luego cualquiera
            prioridad = {"Playing": 0, "Paused": 1}.get(estado, 2)
            candidatos.append((prioridad, nombre))

        if not candidatos:
            return
        candidatos.sort()
        destino = candidatos[0][1]
        try:
            interfaz = dbus.Interface(self._bus.get_object(destino, RUTA), IFAZ_REP)
            getattr(interfaz, miembro)()
            _escribir(f"comando «{miembro}» reenviado a {destino}")
        except Exception as e:
            _escribir(f"no se pudo reenviar «{miembro}» a {destino}: {e}")

    # Un toque: dictado normal
    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def PlayPause(self):
        self._boton("normal", "PlayPause")

    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def Play(self):
        self._boton("normal", "Play")

    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def Pause(self):
        self._boton("normal", "Pause")

    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def Stop(self):
        # Raro en un auricular: solo se reenvía, no activa el dictado.
        if MODO == "ambos":
            self._reenviar("Stop")

    # Botón doble: escribir donde está el cursor
    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def Next(self):
        if USAR_DOBLE:
            self._boton("escribir", "Next")
        elif MODO == "ambos":
            self._reenviar("Next")

    @dbus.service.method(IFAZ_REP, in_signature="", out_signature="")
    def Previous(self):
        if USAR_DOBLE:
            self._boton("escribir", "Previous")
        elif MODO == "ambos":
            self._reenviar("Previous")

    # Métodos del estándar que no aplican, pero deben existir
    @dbus.service.method(IFAZ_REP, in_signature="x", out_signature="")
    def Seek(self, offset):
        pass

    @dbus.service.method(IFAZ_REP, in_signature="ox", out_signature="")
    def SetPosition(self, identificador, posicion):
        pass

    @dbus.service.method(IFAZ_REP, in_signature="s", out_signature="")
    def OpenUri(self, uri):
        pass

    @dbus.service.method(IFAZ_RAIZ, in_signature="", out_signature="")
    def Raise(self):
        pass

    @dbus.service.method(IFAZ_RAIZ, in_signature="", out_signature="")
    def Quit(self):
        pass


def main():
    if sys.platform == "win32":
        _escribir("MPRIS no aplica en Windows; nada que hacer.")
        return 0

    # Este proceso vive en el mismo grupo de procesos que el cliente: un
    # `systemctl --user kill -s SIGUSR1 cliente-voz` (atajo de GNOME o el
    # comando interno) llegaría también aquí y lo mataría. Las señales las
    # entiende el cliente, no nosotros.
    for nombre in ("SIGUSR1", "SIGUSR2"):
        senal_ignorada = getattr(signal, nombre, None)
        if senal_ignorada is not None:
            signal.signal(senal_ignorada, signal.SIG_IGN)

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    try:
        bus = dbus.SessionBus()
    except Exception as e:
        print(f"[MPRIS] No hay bus de sesión: {e}", file=sys.stderr, flush=True)
        return 1

    if not bus.request_name(NOMBRE, dbus.bus.NAME_FLAG_DO_NOT_QUEUE):
        # Ya hay otra instancia publicando el reproductor: se sale sin error
        # para que el cliente no lo esté reiniciando en bucle.
        _escribir("ya había un reproductor de dictado publicado; saliendo.")
        return 0

    reproductor = ReproductorDictado(bus)  # noqa: F841 (debe vivir en el mainloop)

    bucle = GLib.MainLoop()
    for signame in ("SIGTERM", "SIGINT"):
        senal = getattr(signal, signame, None)
        if senal is not None:
            signal.signal(senal, lambda *_: bucle.quit())

    _escribir(
        f"reproductor «Dictado por Voz» publicado (modo={MODO}, doble="
        f"{'sí' if USAR_DOBLE else 'no'}). El botón del auricular ya puede dictar."
    )
    bucle.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
