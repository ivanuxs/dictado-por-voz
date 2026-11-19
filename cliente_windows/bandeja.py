"""Icono del cliente de dictado en el área de notificación de Windows.

Sirve para lo que no se puede hacer con la tecla activadora: encender o apagar
la escucha de la palabra clave, pausar los comandos de voz, dictar sin tocar el
teclado, ver el registro o cerrar el cliente sin buscar la carpeta.

Se apoya en `pystray` (y `Pillow` para dibujar el icono). Si no están
instalados, el cliente sigue funcionando igual: `iniciar()` devuelve False y lo
único que se pierde es el icono.

El objeto que se le pasa (`cliente`) tiene que ofrecer:

    cliente.alternar_escucha_local()   enciende/apaga la escucha de la clave
    cliente.alternar_comandos()        pausa/reanuda la ejecución de comandos
    cliente.alternar_dictado()         empieza o termina un dictado
    cliente.abrir_registro()           abre el archivo de log
    cliente.detener()                  cierra el cliente

y los atributos `escucha_local_activa` y `comandos_activos`.
"""
import threading

# Colores del icono según lo que esté pasando.
ESTADOS = {
    "apagado": (128, 128, 128),      # gris: no escucha
    "escuchando": (46, 160, 67),     # verde: esperando la palabra clave
    "pausado": (200, 150, 0),        # ámbar: escucha, pero comandos en pausa
    "grabando": (200, 40, 40),       # rojo: grabando
}


class Bandeja:
    """Icono de la bandeja con el menú del cliente."""

    def __init__(self, cliente, titulo="SkipKey", al_fallar=None):
        self._cliente = cliente
        self._titulo = titulo
        self._icono = None
        self._estado = "apagado"
        self._hilo = None
        # Se llama si el icono no se puede crear. El cliente de Windows pasa
        # aquí `_avisar_usuario`, que escribe el motivo en el registro y muestra
        # un cuadro de diálogo: el exe no tiene consola y si no, el fallo pasa
        # totalmente desapercibido (que es justo lo que pasó).
        self._al_fallar = al_fallar or (lambda mensaje: None)

    # --- Ciclo de vida ---

    def iniciar(self):
        """Crea el icono. Devuelve True si quedó puesto."""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as e:
            mensaje = (
                f"sin icono en la bandeja: falta pystray o Pillow ({e}). "
                "Instálalos con: pip install pystray Pillow"
            )
            self._avisar(mensaje)
            self._al_fallar(mensaje)
            return False

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: self._titulo,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Escucha continua (palabra clave)",
                lambda: self._cliente.alternar_escucha_local(),
                checked=lambda item: self._cliente.escucha_local_activa,
            ),
            pystray.MenuItem(
                "Comandos por voz",
                lambda: self._cliente.alternar_comandos(),
                checked=lambda item: self._cliente.comandos_activos,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Dictar ahora",
                lambda: self._cliente.alternar_dictado(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Configuración…", lambda: self._abrir_configuracion()
            ),
            pystray.MenuItem("Ver registro", lambda: self._cliente.abrir_registro()),
            pystray.MenuItem("Salir", lambda: self._cliente.detener()),
        )

        try:
            self._icono = pystray.Icon(
                "dictado_voz",
                icon=self._dibujar(
                    ESTADOS.get(self._estado, ESTADOS["apagado"]), Image, ImageDraw
                ),
                title=self._titulo,
                menu=menu,
            )
            # run_detached deja el bucle del icono en su propio hilo: el
            # cliente sigue con el suyo (audio, ZMQ, teclado).
            self._icono.run_detached()
            # El estado puede haber cambiado antes de que el icono existiera
            # (se crea en otro hilo): se pinta ahora con el color que toque.
            self._pintar()
        except Exception as e:
            mensaje = f"no se pudo crear el icono de la bandeja: {e}"
            self._avisar(mensaje)
            self._al_fallar(mensaje)
            self._icono = None
            return False

        self._avisar("icono de la bandeja listo.")
        return True

    def detener(self):
        if self._icono is not None:
            try:
                self._icono.stop()
            except Exception:
                pass
            self._icono = None

    # --- Estado ---

    def actualizar(self, estado):
        """Cambia el color del icono: 'apagado', 'escuchando', 'pausado' o
        'grabando'."""
        if estado not in ESTADOS or estado == self._estado:
            return
        self._estado = estado
        self._pintar()

    def _pintar(self):
        """Aplica al icono el color del estado actual (si ya existe)."""
        if self._icono is None:
            return
        try:
            from PIL import Image, ImageDraw

            self._icono.icon = self._dibujar(
                ESTADOS.get(self._estado, ESTADOS["apagado"]), Image, ImageDraw
            )
            self._icono.update_menu()
        except Exception:
            pass

    def refrescar_menu(self):
        """Relee los checkboxes del menú (tras cambiar un ajuste)."""
        if self._icono is None:
            return
        try:
            self._icono.update_menu()
        except Exception:
            pass

    def notificar(self, mensaje):
        """Globo de aviso del área de notificación (si el sistema lo permite)."""
        if self._icono is None:
            return
        try:
            self._icono.notify(mensaje, self._titulo)
        except Exception:
            pass

    # --- Utilidades ---

    def _abrir_configuracion(self):
        """Abre la ventana de configuración en otro proceso.

        Con el ejecutable congelado hay que volver a lanzar el propio `.exe` con
        `--configuracion` (no hay un `.py` que ejecutar); con el cliente desde el
        código fuente, se lanza el script del cliente con ese mismo flag.
        """
        import os
        import subprocess
        import sys

        carpeta = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, "frozen", False):
            orden = [sys.executable, "--configuracion"]
        else:
            orden = [
                sys.executable,
                os.path.join(carpeta, "cliente_voz.py"),
                "--configuracion",
            ]
        try:
            subprocess.Popen(
                orden,
                cwd=carpeta,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            try:
                from utils_logging import imprimir

                imprimir(f"[BANDEJA] No se pudo abrir la configuración: {e}")
            except Exception:
                pass

    @staticmethod
    def _dibujar(color, Image, ImageDraw):
        """Icono simple: un círculo del color del estado con un micrófono."""
        tamano = 64
        imagen = Image.new("RGBA", (tamano, tamano), (0, 0, 0, 0))
        lapiz = ImageDraw.Draw(imagen)
        lapiz.ellipse((2, 2, tamano - 2, tamano - 2), fill=color + (255,))
        # Cápsula del micrófono.
        lapiz.rounded_rectangle(
            (26, 14, 38, 38), radius=6, fill=(255, 255, 255, 240)
        )
        # Arco y pie.
        lapiz.arc((18, 24, 46, 46), start=0, end=180, fill=(255, 255, 255, 240), width=4)
        lapiz.line((32, 46, 32, 52), fill=(255, 255, 255, 240), width=4)
        lapiz.line((24, 52, 40, 52), fill=(255, 255, 255, 240), width=4)
        return imagen

    @staticmethod
    def _avisar(mensaje):
        try:
            from utils_logging import imprimir

            imprimir(f"[BANDEJA] {mensaje}")
        except Exception:
            pass


def iniciar_en_hilo(cliente, al_fallar=None):
    """Crea la bandeja y la deja corriendo aparte. Devuelve la instancia."""
    bandeja = Bandeja(cliente, al_fallar=al_fallar)
    hilo = threading.Thread(target=bandeja.iniciar, daemon=True)
    hilo.start()
    return bandeja
