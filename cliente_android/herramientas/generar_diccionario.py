#!/usr/bin/env python3
"""Genera `assets/diccionario_es.txt`, la lista de palabras del teclado Android.

De dónde sale el vocabulario:

1. La lista de frecuencias `es_50k.txt` de FrequencyWords (Hermit Dave), hecha
   con los subtítulos de OpenSubtitles (CC-BY-SA 4.0). Se descarga si no está
   al lado de este script. Se queda solo lo que son palabras: minúsculas y
   letras del español (a-z, á é í ó ú ü ñ); se van los nombres propios, las
   siglas con puntos y toda la basura del final de la lista. Antes se recortaba
   a 40 000 entradas; ahora entran todas las que pasan el filtro.

2. Una lista corta y curada (la de abajo), con términos del día a día que en
   subtítulos casi no salen: aparatos de casa, de la calle y de la tecnología
   (whatsapp, refrigeradora, pantallazo…). Sin esto, palabras normales no se
   reconocían.

La frecuencia de lo curado es un MÍNIMO, nunca un valor fijo: si la palabra ya
venía con más frecuencia, se queda con la suya. Los niveles están a propósito
por encima del umbral «rara» del corrector (FRECUENCIA_RARA = 3000 en
Diccionario.kt): así las trata como palabras buenas de pleno derecho y no las
«arregla» a otra parecida, que es lo que pasaba (medido con la réplica en
Python: el corrector convertía usb en usa, olla en ella, ají en así, chifa en
chica, tacho en techo, casaca en casada…). Siguen siendo frecuencias moderadas:
no le quitan la corrección a una palabra de verdad.

El formato de salida es `palabra<TAB>frecuencia`, de más usada a menos, que es
lo que lee `Diccionario.kt`.

Uso:
    python3 cliente_android/herramientas/generar_diccionario.py

Después de cambiar una palabra, el propio script avisa si el corrector podría
estropearla (la comprobación de abajo es una copia reducida de `errataRara` de
`Diccionario.kt`).
"""

import re
import sys
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ASSETS = AQUI.parent / "app" / "src" / "main" / "assets"
ORIGEN = AQUI / "es_50k.txt"
SALIDA = ASSETS / "diccionario_es.txt"

URL = (
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
    "master/content/2018/es/es_50k.txt"
)

# Solo palabras: minúsculas y letras del español.
PALABRA = re.compile(r"^[a-záéíóúüñ]+$")

# Piso de frecuencia de lo curado: lo que casi todo el mundo escribe, uso
# corriente y el resto (siempre por encima del umbral «rara» del corrector).
MUCHO = 3600
NORMAL = 3300
RESTO = 3050

CURADAS = {}

for _palabra in """
whatsapp facebook instagram telegram tiktok messenger youtube gmail hotmail
outlook google wifi internet bluetooth smartphone tablet laptop
cargador cable adaptador audífonos parlante bocina linterna táctil
pantallazo captura imprimir escanear adjuntar reenviar sincronizar conectar
desinstalar reinstalar configurar formatear respaldar comprimir descomprimir
""".split():
    CURADAS.setdefault(_palabra, MUCHO)

for _palabra in """
excel word powerpoint pdf usb hdmi enlace archivo carpeta contraseña usuario
clave menú botón icono pestaña ventana buscador navegador correo bandeja
adjunto borrador enviado recibido descarga descargar actualizar aplicación
reiniciar apagar encender reinicio
refrigeradora refrigerador congeladora congelador licuadora batidora freidora
arrocera tostadora microondas hervidor tetera termo olla sartén lavadora
secadora aspiradora plancha ventilador extractor terma calentador ducha grifo
caño inodoro mayólica cerámica albañil gasfitero gasfitería electricista
plomero carpintero mecánico llanta neumático repuesto gasolina gasolinera
estacionamiento brevete papeleta peaje paradero combi mototaxi colectivo vereda
cuadra detergente suavizante lejía escoba trapeador recogedor balde manguera
tacho destornillador alicate taladro lijadora martillo tornillo cemento ladrillo
""".split():
    CURADAS.setdefault(_palabra, NORMAL)

for _palabra in """
ceviche anticucho chicha camote cancha choclo rocoto huacatay ají chifa
chicharrón tamal humita causa butifarra zapatilla tomacorriente enchufe polos
casaca chompa buzo zapatillas chamba jato flaca pata toy enano
""".split():
    CURADAS.setdefault(_palabra, RESTO)


def descargar() -> None:
    """Trae la lista original si no está al lado del script."""
    print(f"Descargando {URL}")
    with urllib.request.urlopen(URL, timeout=120) as respuesta:
        ORIGEN.write_bytes(respuesta.read())
    print(f"Guardado en {ORIGEN} ({ORIGEN.stat().st_size} bytes)")


# --- Comprobación de la corrección ----------------------------------------
# Copia reducida de `errataRara` y `mejorEdicion` de Diccionario.kt: sirve para
# avisar aquí, al generar la lista, si el corrector cambiaría una palabra buena
# por otra parecida (usb -> usa, olla -> ella…). Los números tienen que ser los
# mismos que los de Diccionario.kt.

FRECUENCIA_MINIMA = 400
FRECUENCIA_RARA = 3000
FACTOR_ERRATA_RARA = 20
ALFABETO = "abcdefghijklmnopqrstuvwxyzáéíóúüñ"


def _frecuencia(ordenadas: list[tuple[str, int]], palabra: str) -> int:
    """Búsqueda binaria en la lista alfabética (la misma idea que en Kotlin)."""
    bajo, alto = 0, len(ordenadas) - 1
    while bajo <= alto:
        medio = (bajo + alto) // 2
        actual = ordenadas[medio][0]
        if actual == palabra:
            return ordenadas[medio][1]
        if actual < palabra:
            bajo = medio + 1
        else:
            alto = medio - 1
    return 0


def _ediciones(palabra: str):
    for i in range(len(palabra)):
        yield palabra[:i] + palabra[i + 1:]
    for i in range(len(palabra) - 1):
        yield palabra[:i] + palabra[i + 1] + palabra[i] + palabra[i + 2:]
    for i in range(len(palabra)):
        for letra in ALFABETO:
            if letra != palabra[i]:
                yield palabra[:i] + letra + palabra[i + 1:]
    for i in range(len(palabra) + 1):
        for letra in ALFABETO:
            yield palabra[:i] + letra + palabra[i:]


def errata_rara(alfabetica: list[tuple[str, int]], palabra: str) -> str | None:
    """Lo mismo que devuelve el corrector: la palabra con la que la confundiría."""
    veces = _frecuencia(alfabetica, palabra)
    if veces == 0 or veces >= FRECUENCIA_RARA:
        return None
    mejor, mejor_cuenta = None, 0
    for candidata in _ediciones(palabra):
        cuenta = _frecuencia(alfabetica, candidata)
        if cuenta > mejor_cuenta:
            mejor_cuenta, mejor = cuenta, candidata
    if mejor is None or mejor_cuenta < FRECUENCIA_MINIMA:
        return None
    return mejor if mejor_cuenta >= veces * FACTOR_ERRATA_RARA else None


def main() -> None:
    if not ORIGEN.exists():
        descargar()

    frecuencias: dict[str, int] = {}
    descartadas = 0
    for linea in ORIGEN.read_text(encoding="utf-8").splitlines():
        partes = linea.split(" ")
        if len(partes) != 2:
            continue
        palabra, cuenta = partes
        if not PALABRA.match(palabra):
            descartadas += 1
            continue
        frecuencias[palabra] = int(cuenta)

    de_la_lista = len(frecuencias)
    nuevas, subidas = [], []
    for palabra, piso in CURADAS.items():
        actual = frecuencias.get(palabra, 0)
        if actual == 0:
            nuevas.append(palabra)
        elif actual < piso:
            subidas.append((palabra, actual, piso))
        frecuencias[palabra] = max(actual, piso)
    nuevas.sort()

    ordenadas = sorted(frecuencias.items(), key=lambda par: (-par[1], par[0]))
    SALIDA.write_text(
        "".join(f"{palabra}\t{cuenta}\n" for palabra, cuenta in ordenadas),
        encoding="utf-8",
    )

    print(f"Palabras de la lista original: {de_la_lista}")
    print(f"Descartadas por no ser palabras: {descartadas}")
    print(f"Palabras de la lista curada: {len(CURADAS)}")
    print(f"  añadidas (no estaban): {len(nuevas)}")
    if nuevas:
        print("    " + " ".join(nuevas))
    print(f"  con el piso de frecuencia aplicado: {len(subidas)}")
    if subidas:
        print("    " + " ".join(f"{p} {a}->{b}" for p, a, b in sorted(subidas)))
    print(f"Total: {len(ordenadas)} palabras")
    print(f"Escrito {SALIDA} ({SALIDA.stat().st_size} bytes)")

    alfabetica = sorted(ordenadas)
    malas = [(p, errata_rara(alfabetica, p)) for p in sorted(CURADAS)]
    malas = [(p, destino) for p, destino in malas if destino]
    if malas:
        print("\nOJO: el corrector cambiaría estas palabras buenas:")
        for palabra, destino in malas:
            print(f"  {palabra} -> {destino}")
        print("Súbeles el piso de frecuencia (o quítalas de la lista) y repite.")
        sys.exit(1)
    print("\nComprobación del corrector: ninguna palabra curada se estropea ✔")


if __name__ == "__main__":
    main()
