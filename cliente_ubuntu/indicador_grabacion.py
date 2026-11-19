#!/usr/bin/env python3
"""Indicador visual de grabación — ventana flotante con punto rojo.

Se ejecuta como proceso independiente. Muestra la ventana y espera
a recibir SIGTERM para cerrarla limpiamente.
"""
import signal
import sys
import threading
import tkinter as tk


class VentanaGrabacion:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.85)
        self.root.configure(bg="black")

        ancho, alto = 80, 80
        x = 30
        y = 8
        self.root.geometry(f"{ancho}x{alto}+{x}+{y}")

        self.lienzo = tk.Canvas(
            self.root, width=ancho, height=alto,
            bg="black", highlightthickness=0
        )
        self.lienzo.pack()

        # Círculo rojo central (más grande)
        self.punto = self.lienzo.create_oval(
            18, 22, 62, 66, fill="#ff2222", outline="#cc0000", width=2
        )
        # Texto "REC"
        self.lienzo.create_text(
            40, 12, text="REC", fill="#ff2222",
            font=("Helvetica", 10, "bold")
        )

        self._fase = 0
        self._animar()

        # Cerrar limpiamente con SIGTERM (solo disponible en el hilo principal)
        try:
            signal.signal(signal.SIGTERM, self._cerrar)
        except (ValueError, AttributeError):
            pass

    def _animar(self):
        self._fase = (self._fase + 1) % 30
        if self._fase < 15:
            delta = self._fase * 0.7
        else:
            delta = (30 - self._fase) * 0.7
        self.lienzo.coords(self.punto, 18 - delta, 22 - delta, 62 + delta, 66 + delta)
        self.root.after(50, self._animar)

    def _cerrar(self, signum, frame):
        self.root.after(0, self.root.destroy)

    def detener(self):
        """Cierra la ventana (usado cuando corre en un hilo del mismo proceso)."""
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def ejecutar(self):
        self.root.mainloop()


def iniciar_indicador():
    """Crea la ventana en un hilo dedicado y devuelve un objeto con .detener().

    Toda la interacción con Tk (creación y mainloop) ocurre en el MISMO hilo,
    para evitar problemas al crear Tk desde otro hilo (p. ej. el del teclado).
    Si Tk no está disponible, devuelve un indicador nulo que no hace nada.
    """
    resultado = {}
    listo = threading.Event()

    def _run():
        try:
            ventana = VentanaGrabacion()
            resultado["ventana"] = ventana
            listo.set()
            ventana.ejecutar()
        except Exception:
            listo.set()  # Evitar que el llamador se quede esperando

    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    listo.wait(timeout=10)
    return resultado.get("ventana") or _IndicadorNulo()


class _IndicadorNulo:
    """Indicador ficticio cuando Tk no está disponible (no hace nada)."""

    def detener(self):
        pass


if __name__ == "__main__":
    VentanaGrabacion().ejecutar()
