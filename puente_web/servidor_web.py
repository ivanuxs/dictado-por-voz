"""Puente HTTPS/WSS hacia el servidor de dictado que ya corre en este equipo.

El puente NO transcribe: reenvía el audio por ZMQ al servidor de dictado
(Vosk + faster-whisper), que es quien aplica el modelo y las correcciones
aprendidas, y devuelve el resultado tal cual.

    Android ──HTTPS/WSS──▶ nginx ──▶ este puente ──ZMQ──▶ servidor de dictado

El ZMQ nunca se expone a internet: solo escucha en la red local.
"""
import asyncio
import contextlib
import json
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import audio
import auth
import config
import correo
import planes
import rutas_cuentas
import rutas_perfil
import traduccion
import usuarios
from utils_logging import imprimir, rotar_logs_al_iniciar
from zmq_cliente import ClienteDictado, ErrorDictado, ServidorSinRespuesta

# El audio viaja en PCM 16 kHz mono de 2 bytes: así se pasa de bytes a segundos.
BYTES_POR_SEGUNDO = audio.FRECUENCIA * audio.BYTES_MUESTRA

dictado = ClienteDictado(
    config.DIRECCION_ZMQ,
    config.TOKEN_ZMQ,
    config.TIMEOUT_TRANSCRIPCION,
    config.MODELO_PARCIALES,
)

# Peticiones REST que pueden esperar en cola a que se libere el modelo.
cupo = asyncio.Semaphore(config.CONCURRENTES)

# La traducción es de CPU y bloquea: de una en una, y sin dejar sin hilos a
# Whisper. Se ejecuta en un hilo aparte (`asyncio.to_thread`), así que el bucle
# de eventos sigue atendiendo dictados mientras traduce.
cupo_traduccion = asyncio.Semaphore(1)


@contextlib.asynccontextmanager
async def ciclo(app):
    """Arranque y parada del puente."""
    if not config.TOKEN_WEB:
        # Ya no es obligatorio: ahora la puerta de entrada son las cuentas.
        # El token maestro se conserva para los clientes de escritorio y los
        # scripts del dueño.
        imprimir(
            "[PUENTE] Aviso: sin DICTADO_WEB_TOKEN. El token maestro queda "
            "apagado; solo entran las cuentas creadas desde la app."
        )
    usuarios.iniciar()
    imprimir(
        f"[PUENTE] Cuentas: registro "
        f"{'abierto' if config.REGISTRO else 'cerrado'}, "
        f"verificación de correo "
        f"{'sí' if config.VERIFICAR_CORREO else 'no'} ({correo.descripcion()}), "
        f"{usuarios.contar()} cuenta(s) en {config.BD}"
    )
    if config.VERIFICAR_CORREO and not correo.disponible():
        imprimir(
            "[PUENTE] Aviso: se pide verificar el correo pero no hay SMTP "
            "configurado; nadie podrá confirmar su cuenta. Ver .env.example."
        )
    rotar_logs_al_iniciar()
    imprimir(
        f"[PUENTE] Escuchando en {config.HOST}:{config.PUERTO} → {config.DIRECCION_ZMQ}"
    )
    imprimir(
        f"[PUENTE] Límites: {config.MAX_SEGUNDOS} s de audio, {config.MAX_MB} MB, "
        f"{config.CONCURRENTES} peticiones en cola."
    )
    if config.TRADUCCION:
        instalados = traduccion.modelos_instalados()
        if instalados:
            imprimir(f"[PUENTE] Traducción: {', '.join(instalados)}")
            # Se carga SOLO es→en (el par más usado) antes de aceptar
            # peticiones: así la primera traducción no espera, y el arranque no
            # se va a 40 s cargando todo (medido el 2026-11-01: 44 s con tres
            # modelos). Los demás pares se cargan en la primera traducción que
            # los pida, y a partir de ahí se quedan en memoria.
            base = [m for m in ("es-en",) if m in instalados]
            if base:
                imprimir(f"[PUENTE] Precalentando: {', '.join(base)}")
                await asyncio.to_thread(traduccion.calentar, base)
        else:
            imprimir(
                "[PUENTE] Traducción sin modelos: ejecuta herramientas/preparar_modelos.py "
                "(la ruta /api/v1/traducir responderá 422)."
            )
    else:
        imprimir("[PUENTE] Traducción apagada (DICTADO_TRADUCCION=0).")
    yield
    await dictado.cerrar()
    traduccion.liberar()
    imprimir("[PUENTE] Detenido.")


app = FastAPI(title="Puente de dictado por voz", lifespan=ciclo)

# Registrar, entrar, ver el plan y borrar la cuenta.
app.include_router(rutas_cuentas.router)
# Perfil del usuario: ajustes, diccionario y correcciones del dictado.
app.include_router(rutas_perfil.router)


@app.exception_handler(HTTPException)
async def error_http(request: Request, excepcion: HTTPException):
    """Todos los errores salen con la misma forma: status/motivo."""
    return JSONResponse(
        status_code=excepcion.status_code,
        content={"status": "error", "motivo": excepcion.detail},
    )


# ---------------------------------------------------------------------------
# Comprobaciones de salud
# ---------------------------------------------------------------------------
@app.get("/api/v1/salud")
async def salud():
    """Vivo y respondiendo. Sin token: lo usa nginx como comprobación de salud."""
    return {"status": "ok"}


@app.get("/api/v1/estado")
async def estado(request: Request):
    """Comprueba además que el servidor de dictado responde (PING/PONG)."""
    usuario = await auth.verificar_peticion(request)
    responde = await dictado.salud()
    modelos = traduccion.modelos_instalados() if config.TRADUCCION else []
    uso = usuarios.uso_hoy(usuario["id"]) if usuario.get("id") else None
    return {
        "status": "ok" if responde else "error",
        "servidor_dictado": "responde" if responde else "no responde",
        "traduccion": "lista" if modelos else "sin modelos",
        "modelos": modelos,
        "usuario": usuario.get("email") or "token-maestro",
        "plan": planes.cupos(usuario, uso),
    }


# ---------------------------------------------------------------------------
# REST: push-to-talk (se manda el audio completo)
# ---------------------------------------------------------------------------
@app.post("/api/v1/transcribe")
async def transcribir(request: Request):
    usuario = await auth.verificar_peticion(request)
    # El dictado con servidor es de pago: con el plan Gratis la app se queda
    # con el motor del propio celular y nunca llega aquí.
    planes.exigir(usuario, "servidor")

    declarado = request.headers.get("content-length")
    if declarado and declarado.isdigit() and int(declarado) > config.TAMANO_MAXIMO:
        raise HTTPException(
            status_code=413,
            detail=f"El audio supera el tope de {config.MAX_MB} MB.",
        )

    datos = await request.body()
    if len(datos) > config.TAMANO_MAXIMO:
        raise HTTPException(
            status_code=413, detail=f"El audio supera el tope de {config.MAX_MB} MB."
        )

    try:
        pcm = audio.normalizar(datos)
        medidas = audio.validar(pcm, config.MAX_SEGUNDOS)
    except audio.AudioInvalido as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Cupo del día: se comprueba ANTES de gastar GPU.
    uso = usuarios.uso_hoy(usuario["id"]) if usuario.get("id") else {}
    restantes = planes.minutos_restantes(usuario, uso)
    if restantes is not None and medidas["duracion_s"] > restantes * 60 + 0.5:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Te quedan {restantes:.1f} minutos de dictado por servidor hoy. "
                "Puedes seguir dictando con el motor del celular."
            ),
        )

    inicio = time.monotonic()
    try:
        await asyncio.wait_for(cupo.acquire(), config.ESPERA_MAXIMA)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="El servidor está ocupado con otros dictados; inténtalo en unos segundos.",
        ) from None

    # `marcas` lo pide el teclado cuando trae intérprete: el TEXTO vuelve con
    # § ¬ ¶ en vez de texto limpio. Sin la bandera (APK antiguos, scripts) el
    # texto sigue llegando limpio, como siempre.
    marcas = _verdad(request.query_params.get("marcas"))

    try:
        resultado = await dictado.transcribir(pcm, vivo=False, marcas=marcas)
    except ErrorDictado as e:
        imprimir(f"[PUENTE] Dictado fallido: {e}")
        if isinstance(e, ServidorSinRespuesta):
            raise HTTPException(status_code=504, detail=str(e)) from e
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        cupo.release()

    latencia = int((time.monotonic() - inicio) * 1000)
    imprimir(
        f"[PUENTE] REST {medidas['duracion_s']:.1f}s → «{resultado['texto']}» "
        f"({latencia} ms, RMS={medidas['rms']:.0f})"
    )
    # Se apunta lo gastado DESPUÉS de transcribir: si falló, no se cobra cupo.
    usuarios.sumar_uso(
        usuario["id"], segundos=medidas["duracion_s"], transcripciones=1
    )
    quedan = planes.minutos_restantes(usuario, usuarios.uso_hoy(usuario["id"])) \
        if usuario.get("id") else None
    return {
        "status": "ok",
        "transcript": resultado["texto"],
        "duracion_s": round(medidas["duracion_s"], 2),
        "rms": round(medidas["rms"]),
        "latencia_ms": latencia,
        "minutos_restantes": None if quedan is None else round(quedan, 1),
    }


# ---------------------------------------------------------------------------
# REST: traducción de texto (Opus-MT en CPU, sin tocar el servidor de dictado)
#
# El teclado del celular manda lo escrito y recibe el texto traducido. El
# origen puede ser "auto": se adivina con reglas simples (chino por caracteres,
# portugués por ã/õ/ç, inglés por palabras frecuentes, si no español).
# ---------------------------------------------------------------------------
@app.post("/api/v1/traducir")
async def traducir_texto(request: Request):
    usuario = await auth.verificar_peticion(request)
    planes.exigir(usuario, "traduccion")
    if not config.TRADUCCION:
        raise HTTPException(
            status_code=503, detail="La traducción está apagada en el puente."
        )

    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Se esperaba un JSON con «texto» y «destino».",
        ) from None
    if not isinstance(datos, dict):
        raise HTTPException(
            status_code=400, detail="Se esperaba un objeto JSON con «texto» y «destino»."
        )

    texto = str(datos.get("texto") or "")
    destino = str(datos.get("destino") or "en")
    origen = str(datos.get("origen") or "auto")

    # Cupo de traducciones del plan (una traducción = un toque del botón 🌐).
    uso = usuarios.uso_hoy(usuario["id"]) if usuario.get("id") else {}
    quedan = planes.traducciones_restantes(usuario, uso)
    if quedan is not None and quedan <= 0:
        raise HTTPException(
            status_code=429,
            detail="Llegaste al tope de traducciones de hoy. Vuelve mañana o mejora el plan.",
        )

    try:
        await asyncio.wait_for(cupo_traduccion.acquire(), config.ESPERA_MAXIMA)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Hay otra traducción en marcha; prueba en un momento.",
        ) from None

    inicio = time.monotonic()
    try:
        traduccion_texto, de, a = await asyncio.to_thread(
            traduccion.traducir, texto, destino, origen
        )
    except traduccion.ErrorTraduccion as e:
        imprimir(f"[PUENTE] Traducción fallida: {e}")
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        cupo_traduccion.release()

    latencia = int((time.monotonic() - inicio) * 1000)
    imprimir(
        f"[PUENTE] Traducción {de}→{a} ({len(texto)} caracteres): "
        f"«{texto[:40]}» → «{traduccion_texto[:40]}» ({latencia} ms)"
    )
    usuarios.sumar_uso(
        usuario["id"], traducciones=1, caracteres=len(texto)
    )
    return {
        "status": "ok",
        "texto": traduccion_texto,
        "origen": de,
        "destino": a,
        "ms": latencia,
    }


# ---------------------------------------------------------------------------
# WebSocket: dictado en vivo (con texto parcial)
# ---------------------------------------------------------------------------
@app.websocket("/ws/transcribe")
async def ws_transcribir(ws: WebSocket):
    await ws.accept()
    try:
        usuario = auth.verificar_cabecera(ws.headers.get("authorization"))
        # El dictado por servidor es de pago (y el modo vivo, de Pro).
        planes.exigir(usuario, "servidor")
        uso = usuarios.uso_hoy(usuario["id"]) if usuario.get("id") else {}
        restantes = planes.minutos_restantes(usuario, uso)
        if restantes is not None and restantes <= 0:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Se acabó tu cupo de dictado por servidor de hoy. "
                    "Puedes seguir dictando con el motor del celular."
                ),
            )
    except HTTPException as e:
        await _enviar_json(
            ws, {"tipo": "error", "mensaje": e.detail, "codigo": e.status_code}
        )
        await ws.close(code=4401 if e.status_code == 401 else 4403)
        return

    origen = ws.client.host if ws.client else "?"
    imprimir(f"[PUENTE] WebSocket abierto desde {origen}.")
    sesion = None
    pendiente = bytearray()
    inicio = time.monotonic()
    # Bytes de audio recibidos: de aquí sale lo que se descuenta del cupo.
    bytes_recibidos = 0

    try:
        while True:
            mensaje = await ws.receive()
            if mensaje.get("type") == "websocket.disconnect":
                break

            texto = mensaje.get("text")
            datos = mensaje.get("bytes")

            if texto is not None:
                orden = _leer_orden(texto)
                if orden is None:
                    await _enviar_json(
                        ws, {"tipo": "error", "mensaje": "Mensaje de control no reconocido."}
                    )
                    continue
                tipo = str(orden.get("tipo", "")).lower()

                if tipo == "inicio":
                    if sesion is not None:
                        await _enviar_json(
                            ws, {"tipo": "error", "mensaje": "Ya hay un dictado en curso."}
                        )
                        continue
                    vivo = str(orden.get("modo", "normal")).lower() == "vivo"
                    sesion = dictado.sesion(
                        vivo=vivo,
                        marcas=_verdad(orden.get("marcas")),
                        al_parcial=lambda t: _enviar_json(ws, {"tipo": "parcial", "texto": t}),
                    )
                    try:
                        await asyncio.wait_for(sesion.abrir(), config.ESPERA_MAXIMA)
                    except asyncio.TimeoutError:
                        sesion = None
                        await _enviar_json(
                            ws,
                            {"tipo": "error", "mensaje": "El servidor está ocupado; reintenta."},
                        )
                        continue
                    except ErrorDictado as e:
                        sesion = None
                        await _enviar_json(ws, {"tipo": "error", "mensaje": str(e)})
                        continue
                    inicio = time.monotonic()
                    await _enviar_json(ws, {"tipo": "listo", "modo": "vivo" if vivo else "normal"})

                elif tipo == "fin":
                    if sesion is None:
                        await _enviar_json(
                            ws, {"tipo": "error", "mensaje": "No hay ningún dictado en curso."}
                        )
                        continue
                    if pendiente:
                        await sesion.enviar(bytes(pendiente))
                        pendiente.clear()
                    try:
                        resultado = await sesion.terminar()
                    except ErrorDictado as e:  # incluye ServidorSinRespuesta
                        await _enviar_json(ws, {"tipo": "error", "mensaje": str(e)})
                    else:
                        await _enviar_json(
                            ws,
                            {
                                "tipo": "final",
                                "texto": resultado["texto"],
                                "parciales": resultado["parciales"],
                                "latencia_ms": int((time.monotonic() - inicio) * 1000),
                            },
                        )
                        imprimir(f"[PUENTE] WS final: «{resultado['texto']}»")
                    await sesion.cerrar()
                    sesion = None

                elif tipo == "cancelar":
                    if sesion is not None:
                        await sesion.cerrar()
                        sesion = None
                    pendiente.clear()
                    await _enviar_json(ws, {"tipo": "cancelado"})

                else:
                    await _enviar_json(
                        ws, {"tipo": "error", "mensaje": f"Orden desconocida: {tipo}"}
                    )

            elif datos:
                if sesion is None:
                    continue  # audio sin haber pedido "inicio": se ignora
                bytes_recibidos += len(datos)
                if bytes_recibidos > config.MAX_SEGUNDOS * BYTES_POR_SEGUNDO:
                    await _enviar_json(
                        ws,
                        {
                            "tipo": "error",
                            "mensaje": (
                                f"El dictado supera el tope de {config.MAX_SEGUNDOS} s."
                            ),
                        },
                    )
                    with contextlib.suppress(Exception):
                        await sesion.cerrar()
                    sesion = None
                    pendiente.clear()
                    continue
                pendiente.extend(datos)
                while len(pendiente) >= audio.BYTES_TROZO:
                    await sesion.enviar(bytes(pendiente[: audio.BYTES_TROZO]))
                    del pendiente[: audio.BYTES_TROZO]

    except WebSocketDisconnect:
        pass
    except Exception as e:
        imprimir(f"[PUENTE] Error en el WebSocket: {e}")
    finally:
        if sesion is not None:
            with contextlib.suppress(Exception):
                await sesion.cerrar()
        if bytes_recibidos:
            usuarios.sumar_uso(
                usuario["id"],
                segundos=bytes_recibidos / BYTES_POR_SEGUNDO,
                transcripciones=1,
            )
        imprimir("[PUENTE] WebSocket cerrado.")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _leer_orden(texto):
    """Convierte el mensaje de control JSON en un diccionario (o None)."""
    try:
        orden = json.loads(texto)
    except json.JSONDecodeError:
        return None
    return orden if isinstance(orden, dict) else None


def _verdad(valor):
    """Interpreta una bandera que llega por REST o por el WebSocket."""
    if isinstance(valor, bool):
        return valor
    return str(valor or "").strip().lower() in (
        "1", "true", "si", "sí", "on", "yes",
    )


async def _enviar_json(ws, objeto):
    """Envía por el WebSocket tolerando que el cliente se haya ido."""
    with contextlib.suppress(Exception):
        await ws.send_json(objeto)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PUERTO,
        access_log=False,  # el registro propio (con la latencia) ya lo escribe
        log_level="warning",
    )
