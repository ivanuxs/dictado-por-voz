"""Diccionario y correcciones aprendidas del escritorio.

El teclado del celular ya aprende mientras se usa; en la PC no hay campo donde
ver «qué escribió el usuario», así que aquí se guarda lo mismo pero se aplica de
otra forma: **al texto que llega dictado**. Si en el celular corregiste «se ace»
por «seace», en la PC el dictado sale ya corregido.

Qué se guarda, por perfil (`dictado_<perfil>_aprendido.json` en
`~/.config/skipkey/`):

- `palabras`: lo que el usuario enseñó (nombre propio, sigla) con sus veces.
- `sustituciones`: las parejas «antes → después» del dictado.
- `olvidadas`: lo que se borró y cuándo. **No se borra la marca**: es lo que
  viaja a la cuenta para que el olvido no vuelva desde otro aparato.

Las reglas de fusión son las mismas que las del puente, a propósito: `veces` es
el máximo (sumar infla el número cada vez que un aparato resube lo mismo),
`conocida` solo avanza, y el olvido lo decide la fecha más reciente.
"""

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone

# Una palabra del texto: letras españolas, con tilde y diéresis.
_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")

# Con UNA vez que la escriba el usuario, la palabra es suya (igual que el
# teclado del celular: con 2, el dictado siguiente volvía a salir mal).
VECES_PARA_CONOCIDA = 1


def _ahora():
    """Instante ISO en UTC, como el que usa el puente."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _carpeta(perfil):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "skipkey")


def _nombre_perfil(perfil):
    limpio = "".join(
        letra for letra in (perfil or "").strip().lower() if letra.isalnum() or letra in "-_"
    )
    return limpio[:20] or "personal"


class DiccionarioEscritorio:
    """Palabras y correcciones aprendidas, en un JSON por perfil."""

    def __init__(self, perfil="personal", carpeta=None):
        self.perfil = _nombre_perfil(perfil)
        self.carpeta = carpeta or _carpeta(self.perfil)
        self.ruta = os.path.join(self.carpeta, f"dictado_{self.perfil}_aprendido.json")
        self._candado = threading.Lock()
        self.palabras = {}          # palabra -> veces
        self.sustituciones = {}     # antes -> {despues: veces}
        self.olvidadas = {}         # palabra -> ISO
        self.olvidadas_correcciones = {}  # "antes\tdespues" -> ISO
        # Fecha del archivo la última vez que se leyó o escribió: sirve para
        # saber si otra ventana (o el cliente, si es la ventana quien escribe)
        # lo cambió por fuera.
        self._mtime = 0.0

    # --- Disco -----------------------------------------------------------

    def cargar(self):
        """Lee lo aprendido (si el archivo no existe, empieza vacío)."""
        try:
            with open(self.ruta, encoding="utf-8") as archivo:
                datos = json.load(archivo)
        except (OSError, ValueError):
            return self
        if not isinstance(datos, dict):
            return self
        with self._candado:
            self.palabras = {
                str(k): int(v)
                for k, v in (datos.get("palabras") or {}).items()
                if str(k).strip()
            }
            self.sustituciones = {}
            for antes, destinos in (datos.get("sustituciones") or {}).items():
                if isinstance(destinos, dict) and destinos:
                    self.sustituciones[str(antes)] = {
                        str(k): int(v) for k, v in destinos.items()
                    }
            self.olvidadas = dict(datos.get("olvidadas") or {})
            self.olvidadas_correcciones = dict(datos.get("olvidadas_correcciones") or {})
        self._mtime = self._fecha()
        return self

    def _fecha(self):
        try:
            return os.path.getmtime(self.ruta)
        except OSError:
            return 0.0

    def recargar_si_cambio(self):
        """Relee el archivo si alguien lo cambió (ventana, otro proceso).

        Sin esto, lo que se enseña en la ventana de configuración no llega al
        dictado hasta reiniciar el cliente.
        """
        if self._fecha() == self._mtime:
            return False
        self.cargar()
        return True

    def guardar(self):
        """Escribe lo aprendido sin dejar el archivo a medias."""
        with self._candado:
            datos = {
                "palabras": dict(self.palabras),
                "sustituciones": {k: dict(v) for k, v in self.sustituciones.items()},
                "olvidadas": dict(self.olvidadas),
                "olvidadas_correcciones": dict(self.olvidadas_correcciones),
            }
        try:
            os.makedirs(self.carpeta, exist_ok=True)
            descriptor, temporal = tempfile.mkstemp(dir=self.carpeta)
            with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                json.dump(datos, archivo, ensure_ascii=False, indent=1)
            os.replace(temporal, self.ruta)
            self._mtime = self._fecha()
        except OSError:
            pass

    # --- Uso -------------------------------------------------------------

    def corregir(self, texto):
        """Aplica las correcciones aprendidas al texto dictado.

        Se resuelven primero las correcciones de **varias palabras** («se ace» →
        «seace»), de la más larga a la más corta, y después las de una sola
        palabra. Se conserva la mayúscula del original: al empezar una frase
        «Se ace» sale «Seace», y todo en mayúsculas sigue en mayúsculas.
        """
        if not texto:
            return texto
        with self._candado:
            if not self.sustituciones:
                return texto
            sustituciones = {k: dict(v) for k, v in self.sustituciones.items()}

        def elegida(clave):
            destinos = sustituciones.get(clave)
            if not destinos:
                return None
            candidatos = [
                (veces, destino)
                for destino, veces in destinos.items()
                if veces >= VECES_PARA_CONOCIDA
            ]
            return max(candidatos)[1] if candidatos else None

        def con_mayusculas(destino, original):
            if original.isupper():
                return destino.upper()
            if original[:1].isupper():
                return destino[:1].upper() + destino[1:]
            return destino

        for frase in sorted((k for k in sustituciones if " " in k), key=len, reverse=True):
            destino = elegida(frase)
            if not destino:
                continue
            patron = re.compile(
                r"(?<!\w)" + re.escape(frase) + r"(?!\w)", re.IGNORECASE
            )
            texto = patron.sub(
                lambda m, d=destino: con_mayusculas(d, m.group(0)), texto
            )

        def cambiar(coincidencia):
            original = coincidencia.group(0)
            destino = elegida(original.lower())
            if not destino:
                return original
            return con_mayusculas(destino, original)

        return _PALABRA.sub(cambiar, texto)

    def aprender(self, palabra):
        """Guarda una palabra como propia (una vez basta)."""
        limpio = " ".join(str(palabra or "").split())
        if not limpio:
            return False
        clave = limpio.lower()
        with self._candado:
            self.palabras[clave] = max(self.palabras.get(clave, 0), VECES_PARA_CONOCIDA)
            self.olvidadas.pop(clave, None)
        return True

    def olvidar(self, palabra):
        clave = str(palabra or "").strip().lower()
        with self._candado:
            if self.palabras.pop(clave, None) is None:
                return False
            self.olvidadas[clave] = _ahora()
        return True

    def registrar_correccion(self, antes, despues):
        """Anota que el usuario cambió una palabra dictada por otra."""
        origen = str(antes or "").strip().lower()
        destino = str(despues or "").strip().lower()
        if len(origen) < 2 or len(destino) < 2 or origen == destino:
            return False
        with self._candado:
            destinos = self.sustituciones.setdefault(origen, {})
            destinos[destino] = destinos.get(destino, 0) + 1
            self.olvidadas_correcciones.pop(f"{origen}\t{destino}", None)
        return True

    def olvidar_correccion(self, antes, despues):
        origen = str(antes or "").strip().lower()
        destino = str(despues or "").strip().lower()
        with self._candado:
            destinos = self.sustituciones.get(origen)
            if not destinos or destino not in destinos:
                return False
            destinos.pop(destino, None)
            if not destinos:
                self.sustituciones.pop(origen, None)
            # Se apunta el olvido para que no vuelva desde otro aparato.
            self.olvidadas_correcciones[f"{origen}\t{destino}"] = _ahora()
        return True

    # --- Sincronización --------------------------------------------------

    def exportar_palabras(self):
        """Lo aprendido en el formato del puente (lista de diccionarios)."""
        momento = _ahora()
        with self._candado:
            filas = [
                {
                    "palabra": palabra,
                    "veces": veces,
                    "conocida": veces >= VECES_PARA_CONOCIDA,
                    "actualizado": momento,
                }
                for palabra, veces in self.palabras.items()
            ]
            for palabra, instante in self.olvidadas.items():
                if palabra in self.palabras:
                    continue
                filas.append(
                    {
                        "palabra": palabra,
                        "veces": 1,
                        "olvidada": True,
                        "actualizado": instante,
                    }
                )
        return filas

    def exportar_sustituciones(self):
        momento = _ahora()
        with self._candado:
            filas = []
            for antes, destinos in self.sustituciones.items():
                if not destinos:
                    continue
                destino, veces = max(destinos.items(), key=lambda par: par[1])
                filas.append(
                    {
                        "antes": antes,
                        "despues": destino,
                        "veces": veces,
                        "actualizado": momento,
                    }
                )
            for clave, instante in self.olvidadas_correcciones.items():
                antes, _, despues = clave.partition("\t")
                if self.sustituciones.get(antes, {}).get(despues):
                    continue
                filas.append(
                    {
                        "antes": antes,
                        "despues": despues,
                        "veces": 1,
                        "olvidada": True,
                        "actualizado": instante,
                    }
                )
        return filas

    def aplicar(self, palabras, sustituciones):
        """Fusiona lo que devuelve el puente con lo que hay aquí.

        Devuelve cuántas filas cambiaron. Las reglas son las del puente:
        `veces` máximo, `conocida` solo avanza y el olvido lo decide la fecha.
        """
        cambios = 0
        with self._candado:
            for fila in palabras or []:
                if not isinstance(fila, dict):
                    continue
                clave = str(fila.get("palabra") or "").strip().lower()
                if not clave:
                    continue
                instante = str(fila.get("actualizado") or "")
                if fila.get("olvidada"):
                    if instante >= self.olvidadas.get(clave, ""):
                        self.olvidadas[clave] = instante or _ahora()
                        if self.palabras.pop(clave, None) is not None:
                            cambios += 1
                    continue
                if instante < self.olvidadas.get(clave, ""):
                    continue  # aquí se olvidó más recientemente
                if self.olvidadas.pop(clave, None) is not None:
                    cambios += 1
                veces = max(1, int(fila.get("veces") or 1))
                if self.palabras.get(clave, 0) < veces:
                    self.palabras[clave] = veces
                    cambios += 1

            for fila in sustituciones or []:
                if not isinstance(fila, dict):
                    continue
                antes = str(fila.get("antes") or "").strip().lower()
                despues = str(fila.get("despues") or "").strip().lower()
                if not antes or not despues:
                    continue
                instante = str(fila.get("actualizado") or "")
                marca = f"{antes}\t{despues}"
                if fila.get("olvidada"):
                    if instante >= self.olvidadas_correcciones.get(marca, ""):
                        self.olvidadas_correcciones[marca] = instante or _ahora()
                        destinos = self.sustituciones.get(antes)
                        if destinos and destinos.pop(despues, None) is not None:
                            cambios += 1
                            if not destinos:
                                self.sustituciones.pop(antes, None)
                    continue
                if instante < self.olvidadas_correcciones.get(marca, ""):
                    continue
                if self.olvidadas_correcciones.pop(marca, None) is not None:
                    cambios += 1
                veces = max(1, int(fila.get("veces") or 1))
                destinos = self.sustituciones.setdefault(antes, {})
                if destinos.get(despues, 0) < veces:
                    destinos[despues] = veces
                    cambios += 1
        return cambios

    # --- Para la ventana y el registro -----------------------------------

    def palabras_aprendidas(self, limite=200):
        with self._candado:
            return sorted(self.palabras.items(), key=lambda par: -par[1])[:limite]

    def correcciones_aprendidas(self, limite=200):
        with self._candado:
            filas = []
            for antes, destinos in self.sustituciones.items():
                for despues, veces in destinos.items():
                    filas.append((antes, despues, veces))
        return sorted(filas, key=lambda fila: -fila[2])[:limite]
