package com.ivanuxs.skipkey

import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Cabecera extra que valida el reverse proxy (segunda barrera). */
private const val CABECERA_CLAVE = "X-Clave-Cliente"

/** Resultado de una operación contra el puente. */
sealed class Resultado {
    data class Texto(val texto: String) : Resultado()
    data class Error(val motivo: String) : Resultado()
}

/** Avisos del dictado en vivo (llegan desde el hilo de OkHttp). */
interface AvisosVivo {
    fun alParcial(texto: String)
    fun alFinal(texto: String)
    fun alError(motivo: String)
}

/**
 * Cliente del puente. Dos caminos:
 *   - REST: se manda el audio completo y se recibe el texto de una vez.
 *   - WebSocket: el audio sale mientras se habla y el texto va llegando.
 *
 * Los dos comparten el mismo OkHttpClient (conexiones reutilizadas) y las
 * mismas cabeceras: token y, si está configurada, la clave del proxy.
 */
class ClienteRed(private val ajustes: Ajustes) {

    private val cliente: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .readTimeout(ajustes.segundosEspera.toLong(), TimeUnit.SECONDS)
        .build()

    /**
     * Modo normal: manda el audio completo y espera el texto.
     *
     * El texto llega **limpio**: el servidor ya no pone marcas y el teclado se
     * limita a escribirlo (las órdenes por voz viven en el asistente).
     */
    fun transcribir(pcm: ByteArray): Resultado {
        if (!ajustes.configurado) {
            return Resultado.Error("Falta configurar la dirección o el token.")
        }
        val peticion = constructor("/api/v1/transcribe")
            .post(pcm.toRequestBody(TIPO_PCM))
            .build()
        return try {
            cliente.newCall(peticion).execute().use { respuesta ->
                val cuerpo = respuesta.body?.string().orEmpty()
                if (respuesta.isSuccessful) {
                    val texto = JSONObject(cuerpo).optString("transcript")
                    if (texto.isBlank()) {
                        Resultado.Error("El servidor no devolvió texto.")
                    } else {
                        Resultado.Texto(texto)
                    }
                } else {
                    Resultado.Error(motivoDe(cuerpo, respuesta.code))
                }
            }
        } catch (e: IOException) {
            Log.w(ETIQUETA, "Fallo de red: ${e.message}")
            Resultado.Error("No se pudo contactar con el puente: ${e.message}")
        } catch (e: Exception) {
            Log.w(ETIQUETA, "Respuesta inesperada: ${e.message}")
            Resultado.Error("Respuesta inesperada del puente.")
        }
    }

    /** Comprobación de conexión de la pantalla de ajustes. */
    fun probarSalud(): Resultado {
        if (!ajustes.configurado) {
            return Resultado.Error("Falta configurar la dirección o el token.")
        }
        return try {
            cliente.newCall(constructor("/api/v1/estado").get().build()).execute().use { respuesta ->
                val cuerpo = respuesta.body?.string().orEmpty()
                if (respuesta.isSuccessful) {
                    val servidor = JSONObject(cuerpo).optString("servidor_dictado")
                    Resultado.Texto("Conexión correcta. Servidor de dictado: $servidor.")
                } else {
                    Resultado.Error(motivoDe(cuerpo, respuesta.code))
                }
            }
        } catch (e: IOException) {
            Resultado.Error("No se pudo contactar con el puente: ${e.message}")
        } catch (e: Exception) {
            Resultado.Error("Respuesta inesperada del puente.")
        }
    }

    /** Abre una sesión de dictado por WebSocket (vivo = con texto parcial). */
    fun sesionViva(
        avisos: AvisosVivo,
        vivo: Boolean = true
    ): SesionViva = SesionViva(ajustes, cliente, avisos, vivo)

    /**
     * Traduce un texto con el puente (Opus-MT en CPU, dentro del puente).
     *
     * `origen` es "auto" por defecto: el puente lo adivina (chino por
     * caracteres, portugués por ã/õ/ç, inglés por palabras frecuentes, si no
     * español). No toca el dictado: es una petición de texto aparte.
     */
    fun traducir(texto: String, destino: String, origen: String = "auto"): Resultado {
        if (!ajustes.configurado) {
            return Resultado.Error("Falta configurar la dirección o el token.")
        }
        val cuerpo = JSONObject()
            .put("texto", texto)
            .put("destino", destino)
            .put("origen", origen)
            .toString()
        val peticion = constructor("/api/v1/traducir")
            .post(cuerpo.toRequestBody(TIPO_JSON))
            .build()
        return try {
            cliente.newCall(peticion).execute().use { respuesta ->
                val recibido = respuesta.body?.string().orEmpty()
                if (respuesta.isSuccessful) {
                    val traducido = JSONObject(recibido).optString("texto")
                    if (traducido.isBlank()) {
                        Resultado.Error("El servidor no devolvió la traducción.")
                    } else {
                        Resultado.Texto(traducido)
                    }
                } else {
                    Resultado.Error(motivoDe(recibido, respuesta.code))
                }
            }
        } catch (e: IOException) {
            Log.w(ETIQUETA, "Fallo de red al traducir: ${e.message}")
            Resultado.Error("No se pudo contactar con el puente: ${e.message}")
        } catch (e: Exception) {
            Log.w(ETIQUETA, "Respuesta inesperada al traducir: ${e.message}")
            Resultado.Error("Respuesta inesperada del puente.")
        }
    }

    private fun constructor(ruta: String): Request.Builder {
        val peticion = Request.Builder()
            .url(ajustes.urlBase.trimEnd('/') + ruta)
            .header("Authorization", "Bearer ${ajustes.token}")
        if (ajustes.claveCliente.isNotBlank()) {
            peticion.header(CABECERA_CLAVE, ajustes.claveCliente)
        }
        return peticion
    }

    /** Traduce la respuesta de error del puente a algo legible. */
    private fun motivoDe(cuerpo: String, codigo: Int): String {
        val delServidor = try {
            val json = JSONObject(cuerpo)
            json.optString("motivo").ifBlank { json.optString("detail") }
        } catch (e: Exception) {
            ""
        }
        if (delServidor.isNotBlank()) return delServidor
        return when (codigo) {
            401 -> "Token incorrecto o ausente."
            403 -> "El proxy rechazó la clave del cliente."
            413 -> "El audio es demasiado largo."
            422 -> "El servidor no entendió el audio."
            503 -> "El servidor está ocupado; prueba en un momento."
            504 -> "El servidor de dictado no respondió."
            else -> "Error $codigo del puente."
        }
    }

    companion object {
        private const val ETIQUETA = "ClienteRed"
        private val TIPO_PCM = "application/octet-stream".toMediaType()
        private val TIPO_JSON = "application/json".toMediaType()
    }
}

/**
 * Sesión de dictado en vivo por WebSocket.
 *
 * El puente responde con `listo`, luego `parcial` mientras se habla, y termina
 * con `final` (o `error`).
 */
class SesionViva(
    private val ajustes: Ajustes,
    private val cliente: OkHttpClient,
    private val avisos: AvisosVivo,
    private val vivo: Boolean = true
) {

    private var socket: WebSocket? = null

    /** Se cumple cuando el puente confirma que la sesión está abierta (`listo`). */
    private val listo = CountDownLatch(1)

    /** Se cumple cuando llega el texto final (o el dictado falla). */
    private val llegadaFinal = CountDownLatch(1)

    @Volatile
    private var textoFinal: String? = null

    /** El dictado se cayó o el puente lo rechazó: no se manda nada más. */
    @Volatile
    private var fallida = false

    /** El puente ya entregó el texto final. */
    @Volatile
    private var terminado = false

    /** true si el dictado falló (el aviso ya se dio). */
    val fallo: Boolean get() = fallida

    /** Abre el WebSocket y pide empezar el dictado vivo. */
    fun abrir(): Boolean {
        if (!ajustes.configurado) {
            avisos.alError("Falta configurar la dirección o el token.")
            return false
        }
        val peticion = Request.Builder()
            .url(ajustes.urlWebSocket())
            .header("Authorization", "Bearer ${ajustes.token}")
            .apply {
                if (ajustes.claveCliente.isNotBlank()) {
                    header(CABECERA_CLAVE, ajustes.claveCliente)
                }
            }
            .build()

        socket = cliente.newWebSocket(peticion, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                // `inicio` tiene que salir ANTES que cualquier audio: hasta que
                // el puente no lo recibe, no hay sesión y el audio se descarta.
                val orden = buildString {
                    append("{\"tipo\":\"inicio\",\"modo\":")
                    append(if (vivo) "\"vivo\"" else "\"normal\"")
                    append("}")
                }
                webSocket.send(orden)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val evento = JSONObject(text)
                    when (evento.optString("tipo")) {
                        "listo" -> listo.countDown()
                        "parcial" -> avisos.alParcial(evento.optString("texto"))
                        "final" -> {
                            terminado = true
                            textoFinal = evento.optString("texto")
                            listo.countDown()
                            llegadaFinal.countDown()
                            avisos.alFinal(textoFinal ?: "")
                        }
                        "error" -> fallar(evento.optString("mensaje"))
                        "cancelado" -> fallar("Dictado cancelado.")
                    }
                } catch (e: Exception) {
                    Log.w(ETIQUETA, "Mensaje no entendido: $text")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(ETIQUETA, "WebSocket caído: ${t.message}")
                fallar("Se perdió la conexión: ${t.message}")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                // Cierre sin `final` (por ejemplo, el puente se reinició): así
                // quien espera el inicio no se queda esperando.
                if (!terminado) fallar("El puente cerró la conexión.")
            }
        })
        return true
    }

    /**
     * Espera a que el puente confirme el inicio (`listo`).
     *
     * Devuelve false si el dictado falló o se agotó el plazo. Se llama desde un
     * hilo de trabajo porque bloquea.
     */
    fun esperarListo(esperaMs: Long): Boolean =
        try {
            listo.await(esperaMs, TimeUnit.MILLISECONDS) && !fallida
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        }

    /**
     * Espera el texto final del dictado. Devuelve null si falló o se agotó el
     * plazo. Se llama desde un hilo de trabajo porque bloquea.
     */
    fun esperarFinal(esperaMs: Long): String? =
        try {
            llegadaFinal.await(esperaMs, TimeUnit.MILLISECONDS)
            textoFinal
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            null
        }

    /** Manda un bloque de audio PCM. */
    fun enviar(pcm: ByteArray) {
        if (fallida) return
        socket?.send(pcm.toByteString())
    }

    /** Avisa de que ya no se habla más. */
    fun terminar() {
        // Si la sesión falló, el puente ya no la tiene: mandar `fin` solo
        // provoca el aviso «No hay ningún dictado en curso».
        if (fallida) return
        socket?.send(ORDEN_FIN)
    }

    /** Aborta el dictado. */
    fun cancelar() {
        socket?.send(ORDEN_CANCELAR)
    }

    /** Cierra el WebSocket. */
    fun cerrar() {
        socket?.close(1000, null)
        socket = null
    }

    /** Marca el dictado como fallido y avisa una sola vez. */
    private fun fallar(motivo: String) {
        if (fallida) return
        fallida = true
        listo.countDown()
        llegadaFinal.countDown()
        avisos.alError(motivo)
    }

    companion object {
        private const val ETIQUETA = "SesionViva"
        private const val ORDEN_FIN = """{"tipo":"fin"}"""
        private const val ORDEN_CANCELAR = """{"tipo":"cancelar"}"""
    }
}
