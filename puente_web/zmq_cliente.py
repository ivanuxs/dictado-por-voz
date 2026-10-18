"""Cliente ZMQ (DEALER) del servidor de dictado, con una transcripción a la vez.

El servidor tiene un solo modelo Whisper en memoria, así que dos dictados
simultáneos no acelerarían nada y sí podrían mezclar buffers: las sesiones se
serializan con un candado.

Se usa UNA conexión persistente porque el servidor crea un reconocedor Vosk por
cada cliente que se conecta: abrir y cerrar una conexión por petición dejaría
reconocedores creados y destruidos sin parar.
"""
import asyncio
import contextlib

import zmq
import zmq.asyncio

from audio import trocear
from protocolo import (
    AUDIO, DICTADO_START, DICTADO_STOP, MODO_VIVO, PING, PONG,
    TEXTO, TEXTO_PARCIAL, FIN_DICTADO, ESTADO, AUTH_FAIL, crear_auth,
)

# Cuánto se espera a que el servidor confirme el cierre de un dictado
# abandonado (el servidor responde siempre, pero un lote largo puede tardar).
TIMEOUT_CIERRE = 30.0


class ErrorDictado(Exception):
    """El servidor de dictado no pudo completar la transcripción."""


class ServidorSinRespuesta(ErrorDictado):
    """El servidor de dictado no contestó dentro del plazo."""


class SesionDictado:
    """Un dictado: DICTADO_START → AUDIO × N → DICTADO_STOP.

    Ciclo de vida explícito (abrir/enviar/terminar/cerrar) porque el WebSocket
    lo necesita a medias: el cliente puede enviar audio durante minutos, o
    cerrar la pestaña a mitad y hay que dejar el servidor limpio igual.
    """

    def __init__(self, cliente, vivo=False, al_parcial=None, timeout=120.0,
                 modelo=""):
        self._cliente = cliente
        self._vivo = vivo
        self._al_parcial = al_parcial
        self._timeout = timeout
        self._modelo = modelo or ""
        self._socket = None
        self._parciales = []
        self._futuro = None
        self._tarea = None
        self._abierta = False
        self._cerrada = False
        self._terminada = False

    async def abrir(self):
        """Toma el turno, conecta si hace falta y pide empezar a acumular."""
        await self._cliente.entrar()
        self._socket = self._cliente.socket()
        # El modelo de los parciales va en el propio arranque: `vivo:medium`.
        orden = b""
        if self._vivo:
            orden = MODO_VIVO
            if self._modelo:
                orden += b":" + self._modelo.encode("ascii", "ignore")
        try:
            await self._socket.send_multipart([DICTADO_START, orden])
        except Exception:
            self._cliente.salir()
            raise
        self._abierta = True
        self._futuro = asyncio.get_running_loop().create_future()
        self._tarea = asyncio.create_task(self._recibir())
        return self

    async def enviar(self, pcm):
        """Envía audio PCM int16 (se trocea al tamaño del protocolo)."""
        if not self._abierta:
            raise ErrorDictado("La sesión de dictado no está abierta.")
        for trozo in trocear(pcm):
            await self._socket.send_multipart([AUDIO, trozo])

    async def terminar(self):
        """Cierra el dictado y espera el texto final."""
        if not self._abierta:
            raise ErrorDictado("La sesión de dictado no está abierta.")
        if not self._terminada:
            self._terminada = True
            await self._socket.send_multipart([DICTADO_STOP, b""])
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._futuro), self._timeout
            )
        except asyncio.TimeoutError:
            raise ServidorSinRespuesta(
                "El servidor de dictado no terminó la transcripción a tiempo."
            ) from None

    async def cerrar(self):
        """Libera el turno dejando el servidor limpio.

        Un dictado cortado a medias deja el buffer abierto en el servidor: si no
        se manda DICTADO_STOP, el servidor se queda en "dictando" e IGNORA el
        siguiente DICTADO_START (solo lo inicia si dictando_tecla es False).
        También hay que DESCARTAR la respuesta de ese cierre, o el próximo
        dictado leería el texto del anterior.
        """
        if self._cerrada:
            return
        self._cerrada = True

        if self._tarea is not None:
            self._tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tarea
            self._tarea = None

        if self._abierta and not self._terminada:
            self._terminada = True
            with contextlib.suppress(Exception):
                await self._socket.send_multipart([DICTADO_STOP, b""])
                await asyncio.wait_for(self._descartar_respuesta(), TIMEOUT_CIERRE)

        # Un dictado en vivo termina con TEXTO y DESPUÉS FIN_DICTADO: el lector
        # ya resolvió el futuro con el TEXTO y se canceló, así que el
        # FIN_DICTADO (y cualquier parcial atrasado) queda en el socket. Sin
        # vaciarlo, el próximo dictado leería mensajes del anterior y todo iría
        # un dictado por detrás. El turno sigue tomado, así que aquí no puede
        # haber mensajes del dictado siguiente.
        await self._descartar_resto()

        self._abierta = False
        self._cliente.salir()

    async def _recibir(self):
        """Escucha las respuestas del servidor y resuelve el futuro."""
        try:
            while True:
                frames = await self._socket.recv_multipart()
                tipo = frames[0]
                payload = frames[1] if len(frames) > 1 else b""
                if tipo == TEXTO_PARCIAL:
                    texto = payload.decode("utf-8", "replace")
                    self._parciales.append(texto)
                    await self._avisar_parcial(texto)
                elif tipo == TEXTO:
                    self._resolver(payload.decode("utf-8", "replace"))
                elif tipo == FIN_DICTADO:
                    self._resolver(" ".join(self._parciales))
                elif tipo == ESTADO:
                    self._fallar(ErrorDictado(payload.decode("utf-8", "replace")))
                # PONG y COMANDO se ignoran: el puente no usa comandos de voz.
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._fallar(
                ErrorDictado(f"Se perdió la conexión con el servidor de dictado: {e}")
            )

    async def _avisar_parcial(self, texto):
        if self._al_parcial is None:
            return
        resultado = self._al_parcial(texto)
        if asyncio.iscoroutine(resultado):
            await resultado

    def _resolver(self, texto):
        if self._futuro is not None and not self._futuro.done():
            self._futuro.set_result(
                {"texto": texto, "parciales": list(self._parciales)}
            )

    def _fallar(self, error):
        if self._futuro is not None and not self._futuro.done():
            self._futuro.set_exception(error)

    async def _descartar_respuesta(self):
        """Consume (y tira) la respuesta del cierre: TEXTO, ESTADO o FIN_DICTADO."""
        while True:
            frames = await self._socket.recv_multipart()
            if frames[0] in (TEXTO, ESTADO, FIN_DICTADO):
                return

    async def _descartar_resto(self):
        """Tira los mensajes que queden pendientes de esta sesión.

        Lee hasta que el socket se queda callado (0,2 s). Es lo que evita que
        el FIN_DICTADO del cierre en vivo, o un TEXTO_PARCIAL atrasado, se lea
        en la sesión siguiente.
        """
        if self._socket is None:
            return
        while True:
            try:
                await asyncio.wait_for(self._socket.recv_multipart(), 0.2)
            except (asyncio.TimeoutError, zmq.ZMQError):
                return


class ClienteDictado:
    """Conexión persistente con el servidor de dictado."""

    def __init__(self, direccion, token_zmq="", timeout=120.0,
                 modelo_parciales=""):
        self.direccion = direccion
        self.token = token_zmq
        self.timeout = timeout
        self.modelo_parciales = modelo_parciales or ""
        self._contexto = zmq.asyncio.Context()
        self._socket = None
        self._candado = None  # se crea al primer uso, ya dentro del bucle

    # --- Turno (una transcripción a la vez) ---

    def _turno(self):
        if self._candado is None:
            self._candado = asyncio.Lock()
        return self._candado

    async def entrar(self):
        """Toma el turno y asegura la conexión."""
        turno = self._turno()
        await turno.acquire()
        try:
            await self._conectar()
        except Exception:
            turno.release()
            raise

    def salir(self):
        """Suelta el turno."""
        turno = self._turno()
        if turno.locked():
            turno.release()

    def socket(self):
        if self._socket is None:
            raise ErrorDictado("No hay conexión con el servidor de dictado.")
        return self._socket

    def sesion(self, vivo=False, al_parcial=None):
        """Crea una sesión de dictado (no la abre todavía)."""
        return SesionDictado(
            self,
            vivo=vivo,
            al_parcial=al_parcial,
            timeout=self.timeout,
            modelo=self.modelo_parciales if vivo else "",
        )

    async def transcribir(self, pcm, vivo=False):
        """Atajo para el caso simple: mandar todo el audio y esperar el texto."""
        sesion = self.sesion(vivo=vivo)
        await sesion.abrir()
        try:
            await sesion.enviar(pcm)
            return await sesion.terminar()
        finally:
            await sesion.cerrar()

    # --- Conexión ---

    async def _conectar(self):
        if self._socket is not None:
            return
        socket = self._contexto.socket(zmq.DEALER)
        socket.setsockopt(zmq.LINGER, 0)
        socket.connect(self.direccion)
        self._socket = socket
        if self.token:
            await self._autenticar(socket)

    async def _autenticar(self, socket):
        await socket.send_multipart(crear_auth(self.token))
        try:
            frames = await asyncio.wait_for(socket.recv_multipart(), self.timeout)
        except asyncio.TimeoutError:
            self.cerrar_conexion()
            raise ServidorSinRespuesta(
                "El servidor de dictado no respondió al saludo de autenticación."
            ) from None
        if frames[0] == AUTH_FAIL:
            motivo = frames[1].decode("utf-8", "replace") if len(frames) > 1 else ""
            self.cerrar_conexion()
            raise ErrorDictado(
                f"El servidor de dictado rechazó el token del puente: {motivo}"
            )

    def cerrar_conexion(self):
        """Cierra el socket para que la próxima petición vuelva a conectar."""
        if self._socket is not None:
            with contextlib.suppress(Exception):
                self._socket.close(linger=0)
            self._socket = None

    async def cerrar(self):
        """Cierra todo (al apagar el puente)."""
        self.cerrar_conexion()
        with contextlib.suppress(Exception):
            self._contexto.term()

    async def salud(self):
        """Comprueba con un PING que el servidor de dictado responde."""
        turno = self._turno()
        try:
            await asyncio.wait_for(turno.acquire(), 5.0)
        except asyncio.TimeoutError:
            return False
        try:
            await self._conectar()
            await self._socket.send_multipart([PING, b""])
            while True:
                frames = await asyncio.wait_for(self._socket.recv_multipart(), 5.0)
                if frames[0] == PONG:
                    return True
        except Exception:
            self.cerrar_conexion()
            return False
        finally:
            turno.release()
