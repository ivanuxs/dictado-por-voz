"""Prueba del puente con audio real: mide latencia y muestra el JSON.

Uso (desde la raíz del proyecto):
    entorno_web/bin/python puente_web/prueba_puente.py
    entorno_web/bin/python puente_web/prueba_puente.py --archivo mi_audio.wav
    entorno_web/bin/python puente_web/prueba_puente.py --url https://dictado.midominio.com \
        --token XXX --clave LA_CLAVE_DEL_PROXY
    entorno_web/bin/python puente_web/prueba_puente.py --ws --modo vivo

Sin --archivo se busca el primer .wav en las carpetas de muestras conocidas.
El token se puede pasar con --token o con la variable DICTADO_WEB_TOKEN; la clave
del proxy (cabecera X-Clave-Cliente), con --clave o con DICTADO_CLAVE.
"""
import argparse
import asyncio
import contextlib
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Carpetas donde suelen estar las muestras de voz (en orden de preferencia).
BUSQUEDAS = [
    "muestras_voz/frases/*.wav",
    "muestras_voz/*.wav",
    "../servidor_dictado/muestras_voz/frases/*.wav",
    os.path.expanduser("~/Documentos/servidor_dictado/muestras_voz/frases/*.wav"),
    os.path.expanduser("~/Documentos/servidor_dictado/muestras_voz/*.wav"),
]


def buscar_audio():
    """Devuelve la primera muestra .wav que encuentre, o None."""
    for patron in BUSQUEDAS:
        encontrados = sorted(glob.glob(patron))
        if encontrados:
            return encontrados[0]
    return None


def probar_rest(url, token, ruta, clave=""):
    """Manda el WAV completo por POST y muestra la respuesta."""
    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    cabeceras = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "audio/wav",
    }
    if clave:
        cabeceras["X-Clave-Cliente"] = clave

    peticion = urllib.request.Request(
        url.rstrip("/") + "/api/v1/transcribe",
        data=datos,
        headers=cabeceras,
        method="POST",
    )
    inicio = time.monotonic()
    try:
        with urllib.request.urlopen(peticion, timeout=180) as respuesta:
            estado = respuesta.status
            cuerpo = respuesta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        estado = e.code
        cuerpo = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        print(f"No se pudo conectar con {url}: {e.reason}")
        return False

    latencia = int((time.monotonic() - inicio) * 1000)
    print(f"HTTP {estado}  (ida y vuelta: {latencia} ms)")
    try:
        print(json.dumps(json.loads(cuerpo), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(cuerpo)
    return estado == 200


async def probar_ws(url, token, ruta, modo, clave=""):
    """Prueba el WebSocket: manda el audio por tramas binarias."""
    try:
        import websockets
    except ImportError:
        print("Falta el paquete 'websockets' en este intérprete.")
        return False

    uri = url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    uri += "/ws/transcribe"
    cabeceras = {"Authorization": f"Bearer {token}"}
    if clave:
        cabeceras["X-Clave-Cliente"] = clave

    try:
        conectar = websockets.connect(uri, additional_headers=cabeceras)
    except TypeError:  # websockets < 14 usa otro nombre
        conectar = websockets.connect(uri, extra_headers=cabeceras)

    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    recibidos = []
    listo = asyncio.Event()

    async def leer(ws):
        """Imprime lo que llega y avisa cuando el dictado termina."""
        while True:
            mensaje = await ws.recv()
            print(f"  ← {mensaje}")
            evento = json.loads(mensaje)
            recibidos.append(evento)
            if evento.get("tipo") == "listo":
                listo.set()
            if evento.get("tipo") in ("final", "error"):
                return

    inicio = time.monotonic()
    async with conectar as ws:
        lector = asyncio.create_task(leer(ws))
        await ws.send(json.dumps({"tipo": "inicio", "modo": modo}))
        try:
            await asyncio.wait_for(listo.wait(), 60)
        except asyncio.TimeoutError:
            pass

        # Se manda en tramos de 0,5 s con una pausa, para imitar el habla.
        tramo = 16000 * 2 * 2 // 2
        for posicion in range(0, len(datos), tramo):
            await ws.send(datos[posicion:posicion + tramo])
            await asyncio.sleep(0.05)
        await ws.send(json.dumps({"tipo": "fin"}))

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(lector, 180)

    latencia = int((time.monotonic() - inicio) * 1000)
    finales = [m for m in recibidos if m.get("tipo") == "final"]
    if finales:
        print(f"WebSocket OK ({latencia} ms): «{finales[0].get('texto', '')}»")
        return True
    print("El WebSocket no devolvió texto.")
    return False


async def probar_cancelacion(url, token, ruta, clave=""):
    """Comprueba que cancelar a medias no deja el servidor bloqueado.

    Es la trampa del protocolo: un dictado cortado sin DICTADO_STOP deja al
    servidor en "dictando" e IGNORA el siguiente DICTADO_START. Aquí se
    cancela un dictado a medias y se hace otro completo: si el segundo
    devuelve texto, la limpieza del puente funciona.
    """
    try:
        import websockets
    except ImportError:
        print("Falta el paquete 'websockets' en este intérprete.")
        return False

    uri = url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    uri += "/ws/transcribe"
    cabeceras = {"Authorization": f"Bearer {token}"}
    if clave:
        cabeceras["X-Clave-Cliente"] = clave
    try:
        conectar = websockets.connect(uri, additional_headers=cabeceras)
    except TypeError:
        conectar = websockets.connect(uri, extra_headers=cabeceras)

    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    async with conectar as ws:
        print("1) Dictado que se cancela a los 3 s de audio...")
        await ws.send(json.dumps({"tipo": "inicio", "modo": "normal"}))
        print(f"  ← {await ws.recv()}")
        await ws.send(datos[: 16000 * 2 * 3])
        await ws.send(json.dumps({"tipo": "cancelar"}))
        print(f"  ← {await ws.recv()}")

        print("2) Dictado completo inmediatamente después...")
        await ws.send(json.dumps({"tipo": "inicio", "modo": "normal"}))
        print(f"  ← {await ws.recv()}")
        await ws.send(datos)
        await ws.send(json.dumps({"tipo": "fin"}))
        respuesta = json.loads(await ws.recv())
        print(f"  ← {json.dumps(respuesta, ensure_ascii=False)}")

    if respuesta.get("tipo") == "final" and respuesta.get("texto"):
        print("Cancelación OK: el segundo dictado sí se transcribió.")
        return True
    print("FALLO: tras cancelar, el segundo dictado no devolvió texto.")
    return False


def main():
    analizador = argparse.ArgumentParser(description="Prueba del puente de dictado.")
    analizador.add_argument("--archivo", help="Audio WAV de prueba.")
    analizador.add_argument("--url", default="http://127.0.0.1:8000", help="URL base del puente.")
    analizador.add_argument(
        "--token",
        default=os.environ.get("DICTADO_WEB_TOKEN", ""),
        help="Token de acceso (o variable DICTADO_WEB_TOKEN).",
    )
    analizador.add_argument(
        "--clave",
        default=os.environ.get("DICTADO_CLAVE", ""),
        help="Clave del reverse proxy (cabecera X-Clave-Cliente), si lo pide.",
    )
    analizador.add_argument("--ws", action="store_true", help="Probar el WebSocket.")
    analizador.add_argument(
        "--cancelar",
        action="store_true",
        help="Comprobar que un dictado cancelado no bloquea al servidor.",
    )
    analizador.add_argument(
        "--modo", default="normal", choices=["normal", "vivo"], help="Modo del WebSocket."
    )
    opciones = analizador.parse_args()

    if not opciones.token:
        print("Falta el token: usa --token o exporta DICTADO_WEB_TOKEN.")
        return 2

    ruta = opciones.archivo or buscar_audio()
    if not ruta:
        print("No encontré ningún WAV de prueba: pasa --archivo.")
        return 2
    print(f"Audio: {ruta}  ({os.path.getsize(ruta) / 1024:.0f} KB)")
    print(f"Puente: {opciones.url}")

    if opciones.cancelar:
        correcto = asyncio.run(
            probar_cancelacion(opciones.url, opciones.token, ruta, opciones.clave)
        )
    elif opciones.ws:
        correcto = asyncio.run(
            probar_ws(opciones.url, opciones.token, ruta, opciones.modo, opciones.clave)
        )
    else:
        correcto = probar_rest(opciones.url, opciones.token, ruta, opciones.clave)
    return 0 if correcto else 1


if __name__ == "__main__":
    sys.exit(main())
