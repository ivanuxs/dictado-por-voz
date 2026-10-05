package com.ivanuxs.dictadoime

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

    /** Modo normal: manda el audio completo y espera el texto. */
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

    /** Abre una sesión de dictado en vivo (reutiliza este cliente). */
    fun sesionViva(avisos: AvisosVivo): SesionViva = SesionViva(ajustes, cliente, avisos)

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
    private val avisos: AvisosVivo
) {

    private var socket: WebSocket? = null

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
                webSocket.send(ORDEN_INICIO)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val evento = JSONObject(text)
                    when (evento.optString("tipo")) {
                        "parcial" -> avisos.alParcial(evento.optString("texto"))
                        "final" -> avisos.alFinal(evento.optString("texto"))
                        "error" -> avisos.alError(evento.optString("mensaje"))
                        "cancelado" -> avisos.alError("Dictado cancelado.")
                    }
                } catch (e: Exception) {
                    Log.w(ETIQUETA, "Mensaje no entendido: $text")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(ETIQUETA, "WebSocket caído: ${t.message}")
                avisos.alError("Se perdió la conexión: ${t.message}")
            }
        })
        return true
    }

    /** Manda un bloque de audio PCM. */
    fun enviar(pcm: ByteArray) {
        socket?.send(pcm.toByteString())
    }

    /** Avisa de que ya no se habla más. */
    fun terminar() {
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

    companion object {
        private const val ETIQUETA = "SesionViva"
        private const val ORDEN_INICIO = """{"tipo":"inicio","modo":"vivo"}"""
        private const val ORDEN_FIN = """{"tipo":"fin"}"""
        private const val ORDEN_CANCELAR = """{"tipo":"cancelar"}"""
    }
}
