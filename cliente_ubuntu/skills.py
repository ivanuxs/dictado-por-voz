"""Catálogo de las *skills* (capacidades) del cliente de dictado.

Abrir una aplicación es una skill; cerrarla, subir el volumen o dictar texto,
también. Este módulo solo las **describe** para poder listarlas en la ventana de
configuración: no ejecuta nada ni decide qué es un comando.

La verdad sigue estando donde siempre:

- `marcas.py` → `VERBOS` y `FRASES` (configurables desde `comandos.json`);
- `comandos.json` → el catálogo de programas de `abre` y `cerrar`;
- `voz_asistente.py` → las respuestas locales (`COMANDOS_SIMPLES`).

Así, si añades una frase en la ventana, aparece sola en la lista de skills.
"""
import unicodedata

# Acciones que ejecuta `ejecutar_comando()` en `cliente_voz.py`.
#   probable: se puede lanzar desde la ventana sin voz.
#   necesita_argumento: hay que elegir un programa del catálogo.
ACCIONES = {
    "abre": {
        "titulo": "Abrir una aplicación",
        "requiere": "catálogo de programas",
        "necesita_argumento": True,
    },
    "cerrar": {
        "titulo": "Cerrar una aplicación",
        "requiere": "catálogo de programas",
        "necesita_argumento": True,
    },
    "subir_volumen": {
        "titulo": "Subir el volumen un 10 %",
        "ejemplo": "sube volumen",
    },
    "bajar_volumen": {
        "titulo": "Bajar el volumen un 10 %",
        "ejemplo": "baja volumen",
    },
    "silenciar": {
        "titulo": "Silenciar / quitar el silencio",
        "ejemplo": "silencio",
    },
    "reproducir": {
        "titulo": "Reproducir o pausar la música",
        "ejemplo": "reproduce",
        "requiere": "xdotool",
    },
    "siguiente": {
        "titulo": "Pasar a la canción siguiente",
        "ejemplo": "siguiente",
        "requiere": "xdotool",
    },
    "anterior": {
        "titulo": "Volver a la canción anterior",
        "ejemplo": "anterior",
        "requiere": "xdotool",
    },
    "captura": {
        "titulo": "Hacer una captura de pantalla",
        "ejemplo": "captura pantalla",
        "requiere": "xdotool",
    },
    "bloquear": {
        "titulo": "Bloquear la pantalla",
        "ejemplo": "bloquear pantalla",
        "requiere": "xdg-screensaver",
    },
    "web": {
        "titulo": "Abrir una página web",
        "requiere": "catálogo de sitios web",
        "necesita_argumento": True,
        "tabla": "web",
    },
    "buscar": {
        "titulo": "Buscar en Google",
        "ejemplo": "busca recetas de tortilla",
        "requiere": "navegador predeterminado",
    },
    "apagar": {
        "titulo": "Apagar el equipo",
        "ejemplo": "apagar equipo",
        "estado": "deshabilitado por seguridad",
    },
    "reiniciar": {
        "titulo": "Reiniciar el equipo",
        "ejemplo": "reiniciar equipo",
        "estado": "deshabilitado por seguridad",
    },
}

# Cosas que el cliente sabe hacer y que no pasan por `ejecutar_comando()`.
ESPECIALES = [
    {
        "accion": "dictar",
        "titulo": "Dictar texto",
        "como": "iniciar dictado",
        "requiere": "escucha local",
        "estado": "se cierra con «fin del dictado»",
        "probable": False,
    },
    {
        "accion": "consulta",
        "titulo": "Preguntar al asistente (DeepSeek)",
        "como": "cualquier frase de 3 o más palabras",
        "requiere": "DEEPSEEK_API_KEY, edge-tts y ffplay",
        "estado": "la respuesta se habla",
        "probable": False,
    },
    {
        "accion": "respuesta_local",
        "titulo": "Respuestas locales (hora, día, saludos)",
        "como": "qué hora es · hola · gracias",
        "requiere": "edge-tts y ffplay",
        "estado": "sin gastar API",
        "probable": False,
    },
]


def _normalizar(texto):
    """Minúsculas, sin tildes y sin puntuación (igual que en el cliente)."""
    plano = unicodedata.normalize("NFKD", (texto or "").lower())
    plano = plano.encode("ascii", "ignore").decode("ascii")
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in plano)
    return " ".join(limpio.split())


def listar(verbos=None, frases=None, programas=None, sitios=None):
    """Devuelve las filas de la tabla de skills.

    `verbos` y `frases` son los diccionarios ya configurados (`marcas.VERBOS` y
    `marcas.FRASES`); `programas` es el dict del catálogo de `abre` y `sitios`
    el de `web`. Cada fila es un dict con: `titulo`, `como`, `requiere`,
    `estado`, `accion`, `argumento` (None si no hay que elegir programa o
    sitio) y `probable`.
    """
    verbos = verbos or {}
    frases = frases or {}
    programas = programas or {}
    sitios = sitios or {}

    # Primera frase de cada acción en el orden del JSON (están puestas de la más
    # natural a la más rara: «sube volumen» antes que «aumenta volumen»).
    ejemplo = {}
    for frase, accion in frases.items():
        ejemplo.setdefault(accion, frase)

    filas = []
    for accion, datos in ACCIONES.items():
        if datos.get("necesita_argumento"):
            verbos_de_la_accion = [v for v, a in verbos.items() if a == accion]
            es_web = datos.get("tabla") == "web"
            catalogo = sitios if es_web else programas
            que = "sitios web" if es_web else "programas"
            sustantivo = "sitio" if es_web else "programa"
            como = (
                f"{verbos_de_la_accion[0]} <{sustantivo}>"
                if verbos_de_la_accion else "(sin verbos configurados)"
            )
            requiere = f"{len(catalogo)} {que} en el catálogo"
            estado = "listo" if catalogo else f"sin {que} configurados"
            argumento = next(iter(catalogo), None)
        else:
            como = ejemplo.get(accion, datos.get("ejemplo", ""))
            requiere = datos.get("requiere", "—")
            estado = datos.get("estado", "listo")
            argumento = None
        filas.append(
            {
                "titulo": datos["titulo"],
                "como": como,
                "requiere": requiere,
                "estado": estado,
                "accion": accion,
                "argumento": argumento,
                "probable": estado == "listo" and accion not in ("apagar", "reiniciar"),
            }
        )

    for especial in ESPECIALES:
        filas.append(
            {
                "titulo": especial["titulo"],
                "como": especial["como"],
                "requiere": especial["requiere"],
                "estado": especial["estado"],
                "accion": especial["accion"],
                "argumento": None,
                "probable": bool(especial.get("probable")),
            }
        )
    return filas
