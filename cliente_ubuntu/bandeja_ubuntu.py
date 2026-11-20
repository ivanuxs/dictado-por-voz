"""Icono del cliente de dictado en la barra superior de Ubuntu (AppIndicator).

Es el equivalente de `bandeja.py` en Windows: permite encender o apagar la
escucha de la palabra clave, pausar los comandos de voz, dictar sin tocar el
teclado, abrir la ventana de **Configuración**, ver el registro y cerrar el
cliente.

Tres decisiones que conviene conocer antes de tocarlo:

**1. Se ejecuta con el Python DEL SISTEMA** (`/usr/bin/python3`), como
`mpris_dictado.py`: necesita `gi` (PyGObject), que no está en el entorno
virtual del cliente. `cliente_voz.py` lo lanza y lo vigila como proceso hijo.
En el **binario empaquetado** esta misma lógica corre dentro del ejecutable
(`cliente_voz.py --bandeja`), porque allí no hay Python del sistema con `gi`
a mano; el instalador deja las librerías de GTK instaladas por apt.

**2. GNOME no tiene bandeja nativa.** El icono es un *AppIndicator* y necesita
la extensión `ubuntu-appindicators@ubuntu.com` (viene activa en Ubuntu). Y
necesita además que **GTK3 arranque**, que en este equipo estaba roto por
`gsettings-desktop-schemas` 50 — ver el parche en
`~/.local/share/glib-2.0/schemas/` y la nota de `AGENTS.md`.

**3. No hay IPC propio: señales y un archivo de estado.**
- El menú **manda señales** al cliente con `senal_cliente.py`
  (`SIGUSR1` dictado, `SIGRTMIN+1` escucha, `SIGRTMIN+2` comandos).
- El cliente **escribe** `~/.cache/dictado-voz/estado.json` con lo que está
  pasando; este proceso lo relee cada segundo para el color del icono y las
  casillas del menú.

El icono es un círculo del color del estado con un micrófono: gris (escucha
apagada), verde (esperando la palabra clave), ámbar (comandos en pausa) y rojo
(grabando).
"""
import json
import os
import subprocess
import sys

CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "dictado-voz",
)
RUTA_ESTADO = os.path.join(CACHE, "estado.json")

# Intérprete del entorno virtual, para abrir la ventana de configuración con
# él (la bandeja corre con el Python del sistema). Se rellena en `main()`.
# En el binario empaquetado se queda vacío: la ventana la abre el propio
# ejecutable con la bandera `--ventana`.
PYTHON_ENTORNO = ""


def _congelado():
    """True si la bandeja corre dentro del ejecutable empaquetado."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _ruta_programa():
    """Ruta del binario real, también dentro del paquete.

    En Nuitka standalone `sys.executable` vale `…/python`, que **no existe** en
    el paquete: lanzar la ventana con él fallaba con «No such file or
    directory» (2026-11-19). El binario de verdad es `sys.argv[0]`.
    """
    for candidato in (sys.argv[0], sys.executable):
        if not candidato:
            continue
        ruta = os.path.realpath(candidato)
        if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
            return ruta
    return sys.executable


def _directorio_programa():
    """Carpeta del programa: la del ejecutable o la de este script."""
    if _congelado():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _ruta_config():
    """`comandos.json`: en `~/.config/skipkey` si está instalado, o junto al script."""
    if _congelado():
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "skipkey", "comandos.json")
    return os.path.join(_directorio_programa(), "comandos.json")


def _directorio_logs():
    """Carpeta de registros (la misma que usa `utils_logging`)."""
    try:
        from utils_logging import directorio_logs

        return directorio_logs()
    except Exception:
        estado = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(
            "~/.local/state"
        )
        return os.path.join(estado, "skipkey", "logs")

ESTADOS = {
    "apagado": (128, 128, 128),      # gris: no escucha
    "escuchando": (46, 160, 67),     # verde: esperando la palabra clave
    "pausado": (200, 150, 0),        # ámbar: escucha, pero comandos en pausa
    "grabando": (200, 40, 40),       # rojo: grabando
}


# ---------------------------------------------------------------------------
# Iconos (se dibujan una vez y se guardan en la caché)
# ---------------------------------------------------------------------------

def _dibujar(color):
    """Círculo del color del estado con un micrófono blanco dentro."""
    from PIL import Image, ImageDraw

    tamano = 22
    imagen = Image.new("RGBA", (tamano, tamano), (0, 0, 0, 0))
    lapiz = ImageDraw.Draw(imagen)
    lapiz.ellipse((1, 1, tamano - 1, tamano - 1), fill=color + (255,))
    # Cápsula del micrófono.
    lapiz.rounded_rectangle((9, 4, 13, 13), radius=2, fill=(255, 255, 255, 240))
    # Arco y pie.
    lapiz.arc((6, 7, 16, 15), start=0, end=180, fill=(255, 255, 255, 240), width=2)
    lapiz.line((11, 15, 11, 18), fill=(255, 255, 255, 240), width=2)
    lapiz.line((8, 18, 14, 18), fill=(255, 255, 255, 240), width=2)
    return imagen


def _ruta_icono(estado):
    """Devuelve la ruta del PNG de ese estado (lo crea si no está)."""
    os.makedirs(CACHE, exist_ok=True)
    ruta = os.path.join(CACHE, f"icono-{estado}.png")
    if not os.path.exists(ruta):
        _dibujar(ESTADOS[estado]).save(ruta)
    return ruta


def _nombre_icono(estado):
    """Nombre de tema del icono de ese estado (sin ruta ni extensión).

    Es lo que entiende gnome-shell: el archivo se busca dentro de
    `IconThemePath` (la carpeta `CACHE`). Ver `iniciar()`.
    """
    _ruta_icono(estado)  # se asegura de que el PNG existe
    return f"icono-{estado}"


def _estado_guardado():
    """Lo que el cliente dejó escrito en estado.json (vacío si no hay)."""
    try:
        with open(RUTA_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _senal(funcion, *args):
    """Manda una señal al cliente (ver senal_cliente.py)."""
    try:
        import senal_cliente

        return getattr(senal_cliente, funcion)(*args)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Icono y menú
# ---------------------------------------------------------------------------

class Bandeja:
    """AppIndicator con el menú del cliente."""

    def __init__(self):
        self._indicador = None
        self._estado = "apagado"
        self._items = {}
        self._actualizando = False
        self._Gtk = None

    def iniciar(self):
        """Crea el indicador y su menú. Devuelve True si quedó puesto."""
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3, GLib, Gtk

        self._Gtk = Gtk

        menu = Gtk.Menu()
        self._items["escucha"] = self._item_check(
            menu, "Escucha continua (palabra clave)", self._alternar_escucha
        )
        self._items["comandos"] = self._item_check(
            menu, "Comandos por voz", self._alternar_comandos
        )
        menu.append(Gtk.SeparatorMenuItem())
        self._items["dictar"] = self._item(
            menu, "Dictar ahora", self._dictar
        )
        menu.append(Gtk.SeparatorMenuItem())
        self._items["configuracion"] = self._item(
            menu, "Configuración…", self._abrir_configuracion
        )
        # Un gancho de teclado puede quedarse sin entregar teclas; el cliente
        # lo vigila solo y aquí está el botón para forzarlo (2026-11-19).
        self._items["teclado"] = self._item(
            menu, "Reiniciar escucha de teclas", self._reiniciar_teclado
        )
        self._items["registro"] = self._item(
            menu, "Ver registro", self._ver_registro
        )
        self._items["salir"] = self._item(menu, "Salir", self._salir)
        menu.show_all()

        self._indicador = AyatanaAppIndicator3.Indicator.new(
            "dictado-voz",
            _nombre_icono(self._estado),
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        # `IconName` tiene que ser un **nombre de tema**, no una ruta absoluta.
        # Con la ruta, el indicador se publicaba como Active pero gnome-shell no
        # dibujaba nada (`IconThemePath` vacío): el icono no aparecía en la barra
        # (2026-11-19). Con la carpeta declarada, resuelve `icono-<estado>`.
        self._indicador.set_icon_theme_path(CACHE)
        self._indicador.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self._indicador.set_title("SkipKey")
        self._indicador.set_menu(menu)

        # Primera lectura y luego una por segundo: así el color y las casillas
        # siguen al cliente sin que este tenga que avisar a nadie.
        self._leer_estado()
        GLib.timeout_add_seconds(1, self._leer_estado)
        return True

    # --- Construcción del menú ---

    def _item(self, menu, etiqueta, accion):
        item = self._Gtk.MenuItem(label=etiqueta)
        item.connect("activate", lambda *_: accion())
        menu.append(item)
        return item

    def _item_check(self, menu, etiqueta, accion):
        item = self._Gtk.CheckMenuItem(label=etiqueta)
        item.connect("toggled", lambda *_: accion())
        menu.append(item)
        return item

    # --- Acciones del menú ---

    def _alternar_escucha(self):
        if not self._actualizando:
            _senal("enviar_escucha")

    def _alternar_comandos(self):
        if not self._actualizando:
            _senal("enviar_comandos")

    def _reiniciar_teclado(self):
        """Vuelve a enganchar el teclado global (por si dejó de responder)."""
        _senal("enviar_teclado")

    def _dictar(self):
        _senal("enviar_toggle", "normal")

    def _abrir_configuracion(self):
        """Abre la ventana de configuración con el intérprete del proyecto.

        La bandeja corre con el Python del sistema (necesita `gi`), pero la
        ventana importa `cliente_voz` para el botón «Probar», así que tiene que
        arrancarse con el intérprete que tiene los paquetes del proyecto. Con
        el cliente empaquetado ese intérprete es el propio ejecutable.
        """
        json_ruta = _ruta_config()
        if _congelado():
            # `sys.executable` es `…/python` en Nuitka standalone y no existe;
            # el binario real es `sys.argv[0]` (2026-11-19).
            comando = [_ruta_programa(), "--ventana", "--json", json_ruta]
        else:
            ventana = os.path.join(_directorio_programa(), "ventana_configuracion.py")
            python = PYTHON_ENTORNO or sys.executable
            comando = [python, ventana, "--json", json_ruta]
        try:
            subprocess.Popen(
                comando,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"[BANDEJA] no se pudo abrir la configuración: {e}", flush=True)

    def _ver_registro(self):
        carpeta = _directorio_logs()
        try:
            subprocess.Popen(
                ["xdg-open", carpeta],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"[BANDEJA] no se pudo abrir {carpeta}: {e}", flush=True)

    def _salir(self):
        # El nombre del servicio cambia según cómo se instaló: el paquete
        # nuevo usa `skipkey.service`, el cliente de siempre `cliente-voz`.
        # Parar uno que no existe no hace nada, así que se prueban los dos.
        for servicio in ("skipkey", "cliente-voz"):
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", servicio], check=False
                )
            except Exception:
                pass
        self._Gtk.main_quit()

    # --- Estado ---

    def _leer_estado(self):
        """Relee estado.json y actualiza el color y las casillas."""
        datos = _estado_guardado()
        escucha = bool(datos.get("escucha", True))
        comandos = bool(datos.get("comandos", True))
        grabando = bool(datos.get("grabando", False))

        if grabando:
            estado = "grabando"
        elif not escucha:
            estado = "apagado"
        elif not comandos:
            estado = "pausado"
        else:
            estado = "escuchando"

        if estado != self._estado:
            self._estado = estado
            try:
                self._indicador.set_icon_full(_nombre_icono(estado), "SkipKey")
            except Exception:
                pass

        self._marcar("escucha", escucha)
        self._marcar("comandos", comandos)
        return True  # que GLib siga llamando al temporizador

    def _marcar(self, clave, valor):
        """Marca una casilla sin que parezca una pulsación del usuario."""
        item = self._items.get(clave)
        if item is None or item.get_active() == valor:
            return
        self._actualizando = True
        try:
            item.set_active(valor)
        finally:
            self._actualizando = False


def main():
    global PYTHON_ENTORNO
    if "--python" in sys.argv:
        try:
            PYTHON_ENTORNO = sys.argv[sys.argv.index("--python") + 1]
        except IndexError:
            PYTHON_ENTORNO = ""
    try:
        bandeja = Bandeja()
        bandeja.iniciar()
    except Exception as e:
        print(
            f"[BANDEJA] no se pudo crear el icono: {e}\n"
            "  Casi siempre falta una biblioteca del escritorio. Instala:\n"
            "    sudo apt install libgtk-3-0 libayatana-appindicator3-1 "
            "gir1.2-ayatanaappindicator3-0.1",
            flush=True,
        )
        return 1
    print("[BANDEJA] icono listo.", flush=True)
    bandeja._Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
