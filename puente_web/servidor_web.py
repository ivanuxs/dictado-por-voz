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
from utils_logging import imprimir, rotar_logs_al_iniciar
from zmq_cliente import ClienteDictado, ErrorDictado, ServidorSinRespuesta

dictado = ClienteDictado(
    config.DIRECCION_ZMQ, config.TOKEN_ZMQ, config.TIMEOUT_TRANSCRIPCION
)

# Peticiones REST que pueden esperar en cola a que se libere el modelo.
cupo = asyncio.Semaphore(config.CONCURRENTES)


@contextlib.asynccontextmanager
async def ciclo(app):
    """Arranque y parada del puente."""
    if not config.TOKEN_WEB:
        raise RuntimeError(
            "Falta DICTADO_WEB_TOKEN: el puente no arranca sin token de acceso. "
            "Ver puente_web/.env.example"
        )
    rotar_logs_al_iniciar()
    imprimir(
        f"[PUENTE] Escuchando en {config.HOST}:{config.PUERTO} → {config.DIRECCION_ZMQ}"
    )
    imprimir(
        f"[PUENTE] Límites: {config.MAX_SEGUNDOS} s de audio, {config.MAX_MB} MB, "
        f"{config.CONCURRENTES} peticiones en cola."
    )
    yield
    await dictado.cerrar()
    imprimir("[PUENTE] Detenido.")


app = FastAPI(title="Puente de dictado por voz", lifespan=ciclo)


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
    await auth.verificar_peticion(request)
    responde = await dictado.salud()
    return {
        "status": "ok" if responde else "error",
        "servidor_dictado": "responde" if responde else "no responde",
    }


# ---------------------------------------------------------------------------
# REST: push-to-talk (se manda el audio completo)
# ---------------------------------------------------------------------------
@app.post("/api/v1/transcribe")
async def transcribir(request: Request):
    await auth.verificar_peticion(request)

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

    inicio = time.monotonic()
    try:
        await asyncio.wait_for(cupo.acquire(), config.ESPERA_MAXIMA)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="El servidor está ocupado con otros dictados; inténtalo en unos segundos.",
        ) from None

    try:
        resultado = await dictado.transcribir(pcm, vivo=False)
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
    return {
        "status": "ok",
        "transcript": resultado["texto"],
        "duracion_s": round(medidas["duracion_s"], 2),
        "rms": round(medidas["rms"]),
        "latencia_ms": latencia,
    }


# ---------------------------------------------------------------------------
# WebSocket: dictado en vivo (con texto parcial)
# ---------------------------------------------------------------------------
@app.websocket("/ws/transcribe")
async def ws_transcribir(ws: WebSocket):
    await ws.accept()
    try:
        auth.verificar_cabecera(ws.headers.get("authorization"))
    except HTTPException as e:
        await _enviar_json(ws, {"tipo": "error", "mensaje": e.detail})
        await ws.close(code=4401)
        return

    origen = ws.client.host if ws.client else "?"
    imprimir(f"[PUENTE] WebSocket abierto desde {origen}.")
    sesion = None
    pendiente = bytearray()
    inicio = time.monotonic()

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
