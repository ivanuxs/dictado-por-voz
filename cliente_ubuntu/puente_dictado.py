"""Dictado por HTTPS contra el puente (funciona desde cualquier red).

El cliente de escritorio hablaba ZMQ directo con el servidor de transcripción
(puerto 5555), que **no** está publicado en internet: fuera de la red local no
se podía dictar. El puente sí está publicado (`https://…`, puerto 443) y es lo
que usa el teclado de Android, así que aquí se manda el audio por ahí.

Contrato del puente (`puente_web/servidor_web.py`):

    POST /api/v1/transcribe
    Authorization: Bearer <token de la cuenta>
    X-Clave-Cliente: <clave del reverse proxy>
    Cuerpo: WAV PCM 16 bits mono 16 kHz (o PCM crudo, que es lo que manda el
            celular). Este módulo manda WAV para que quede inequívoco.

    → 200 {"status": "ok", "transcript": "…", …}
    → 401 sesión no válida; 403 falta la clave del proxy; 413/422 audio
      inválido; 429 cupo del día agotado; 503 ocupado; 504 sin respuesta.

Topes del puente: 60 s por petición y 5 MB (`DICTADO_MAX_SEGUNDOS` /
`DICTADO_MAX_MB`). Aquí se corta a `SEGUNDOS_MAXIMOS` para no chocar con ellos.

No añade dependencias: usa `urllib`, como `perfil.py`.
"""
import io
import json
import urllib.error
import urllib.request
import wave

import perfil

FRECUENCIA = 16000
BYTES_MUESTRA = 2
# Un poco por debajo del tope del puente (60 s) para dejar margen.
SEGUNDOS_MAXIMOS = 55
BYTES_MAXIMOS = SEGUNDOS_MAXIMOS * FRECUENCIA * BYTES_MUESTRA
# **No hay tope total**: un dictado puede durar lo que quiera. El cliente va
# mandando trozos de 55 s mientras se habla (ver `extraer_trozo`), así que la
# memoria no crece con la duración del dictado.


def _base(config):
    """URL del puente sin barra final."""
    return str((config or {}).get("puente") or "").strip().rstrip("/")


def _cabeceras(config):
    """Cabeceras que exige el puente: token de la cuenta y clave del proxy."""
    cabeceras = {"Content-Type": "audio/wav"}
    token = _token()
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    clave = perfil.clave_proxy(config)
    if clave:
        cabeceras["X-Clave-Cliente"] = clave
    return cabeceras


def _token():
    """Token de la cuenta con sesión abierta ("" si no hay)."""
    try:
        import sesion

        return sesion.token()
    except Exception:
        return ""


def disponible(config, espera=4):
    """¿Se puede dictar por el puente ahora mismo? (salud + clave + sesión)."""
    if not _base(config) or not _token():
        return False
    try:
        peticion = urllib.request.Request(
            _base(config) + "/api/v1/salud",
            headers=_cabeceras(config),
            method="GET",
        )
        with urllib.request.urlopen(peticion, timeout=espera) as respuesta:
            return respuesta.status == 200
    except Exception:
        return False


def a_wav(pcm):
    """Envuelve el PCM int16 mono 16 kHz en un WAV en memoria."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(BYTES_MUESTRA)
        wav.setframerate(FRECUENCIA)
        wav.writeframes(pcm)
    return buffer.getvalue()


def transcribir_largo(config, pcm, espera=120, progreso=None):
    """Transcribe un dictado de cualquier duración partiéndolo en trozos.

    El puente admite 60 s por petición (`DICTADO_MAX_SEGUNDOS`) y 5 MB, así que
    un dictado más largo se envía en trozos de `SEGUNDOS_MAXIMOS` y los textos se
    unen. Los cortes buscan el **silencio** más cercano al límite para no partir
    palabras por la mitad.

    `progreso(trozo, total)` se llama antes de cada envío (para el registro).
    Devuelve `(texto, error)`; si falla un trozo, se conserva lo ya transcrito.
    """
    if not pcm:
        return "", "no llegó audio al dictado"

    partes = _partir(pcm)
    total = len(partes)
    textos = []
    for numero, parte in enumerate(partes, start=1):
        if progreso is not None:
            try:
                progreso(numero, total)
            except Exception:
                pass
        texto, error = transcribir(config, parte, espera)
        if error:
            if textos:
                # Se queda con lo transcrito y se avisa de que falta el resto.
                return " ".join(textos).strip(), error
            return "", error
        if texto:
            textos.append(texto)
    return " ".join(textos).strip(), ""


def extraer_trozo(pcm):
    """Saca un trozo listo para enviar y devuelve `(trozo, resto)`.

    Se usa para dictar sin límite: mientras la persona habla, el cliente va
    cerrando trozos de ~55 s (cortando en el silencio más cercano) y los manda;
    el resto se queda acumulándose. Así la memoria no crece con la duración del
    dictado.
    """
    import numpy as np

    if not pcm:
        return b"", b""
    fin = min(len(pcm), BYTES_MAXIMOS)
    if len(pcm) > BYTES_MAXIMOS:
        fin = _fin_por_silencio(pcm, 0, fin, np)
    return pcm[:fin], pcm[fin:]


def _partir(pcm):
    """Parte el PCM en trozos de `SEGUNDOS_MAXIMOS`, cortando por silencios."""
    import numpy as np

    partes = []
    inicio = 0
    total_bytes = len(pcm)
    while inicio < total_bytes:
        fin = min(inicio + BYTES_MAXIMOS, total_bytes)
        if fin < total_bytes:
            fin = _fin_por_silencio(pcm, inicio, fin, np)
        parte = pcm[inicio:fin]
        if parte:
            partes.append(parte)
        inicio = fin
    return partes


def _fin_por_silencio(pcm, inicio, fin, np):
    """Devuelve un final de trozo cerca de `fin`, en el hueco más silencioso.

    Se mira una ventana de 2 s antes del límite, en bloques de 50 ms, y se corta
    en el más silencioso. Si ese punto cae en la primera mitad del trozo (lo que
    dejaría un trozo diminuto), se corta en `fin`.
    """
    try:
        total_muestras = (fin - inicio) // 2
        if total_muestras < 16000:  # menos de 1 s: no merece la pena
            return fin
        ventana = min(32000, total_muestras)  # últimos 2 s
        inicio_ventana = total_muestras - ventana
        datos = np.frombuffer(pcm[inicio:fin], dtype="<i2")[inicio_ventana:]
        bloque = 800  # 50 ms a 16 kHz
        usables = datos.size - datos.size % bloque
        if usables < bloque:
            return fin
        energias = (
            np.abs(datos[:usables].reshape(-1, bloque).astype(np.int32))
            .mean(axis=1)
        )
        indice = int(energias.argmin())
        corte_muestras = inicio_ventana + indice * bloque + bloque // 2
        if corte_muestras < total_muestras // 2:
            return fin
        return inicio + corte_muestras * 2
    except Exception:
        return fin


def _detalle(cuerpo):
    """Saca el mensaje de error del JSON del puente (o el texto crudo)."""
    try:
        datos = json.loads(cuerpo)
    except (ValueError, TypeError):
        return (cuerpo or "").strip() or "error desconocido del puente"
    if isinstance(datos, dict):
        # El puente usa `detail` (FastAPI) o `motivo` (su propio manejador).
        for clave in ("detail", "motivo", "error", "mensaje"):
            if datos.get(clave):
                detalle = datos[clave]
                if isinstance(detalle, (dict, list)):
                    return json.dumps(detalle, ensure_ascii=False)
                return str(detalle)
    if isinstance(datos, (dict, list)):
        return json.dumps(datos, ensure_ascii=False)
    return str(datos)


def transcribir(config, pcm, espera=120):
    """Manda el audio al puente y devuelve `(texto, error)`.

    `pcm` es PCM int16 mono a 16 kHz (los mismos trozos que se enviaban por
    ZMQ). Se recorta a `SEGUNDOS_MAXIMOS` para no pasarse del tope del puente:
    el trozo que sobre se descarta, y quien llama avisa si hace falta.
    """
    if not _base(config):
        return "", "no hay dirección del puente configurada"
    if not _token():
        return "", "no hay sesión: entra con tu cuenta para dictar"
    if pcm and len(pcm) > BYTES_MAXIMOS:
        pcm = pcm[:BYTES_MAXIMOS]
    if not pcm:
        return "", "no llegó audio al dictado"

    peticion = urllib.request.Request(
        _base(config) + "/api/v1/transcribe",
        data=a_wav(bytes(pcm)),
        headers=_cabeceras(config),
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=espera) as respuesta:
            cuerpo = respuesta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")
        detalle = _detalle(cuerpo)
        if e.code == 401:
            return "", "tu sesión no vale: vuelve a entrar con tu cuenta"
        if e.code == 403:
            return "", (
                "el puente rechazó la clave del proxy: falta la clave en este "
                "cliente o no coincide con la del servidor"
            )
        if e.code == 429:
            return "", detalle or "se agotó el cupo de dictado de hoy"
        if e.code in (413, 422):
            # Audio demasiado largo o sin voz: el motivo basta, el código no le
            # dice nada al usuario.
            return "", detalle
        return "", f"el puente respondió {e.code}: {detalle}"
    except urllib.error.URLError as e:
        return "", f"no se pudo conectar con el puente: {e.reason}"
    except Exception as e:
        return "", f"fallo al dictar por el puente: {e}"

    try:
        datos = json.loads(cuerpo)
    except ValueError:
        return "", f"respuesta inesperada del puente: {cuerpo[:120]}"
    texto = (datos.get("transcript") or "").strip()
    if not texto:
        return "", datos.get("detail") or "el puente no devolvió texto"
    return texto, ""
