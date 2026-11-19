package com.ivanuxs.skipkey

import android.content.Context
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Sincronización del perfil de la cuenta: diccionario, correcciones y ajustes.
 *
 * **Qué se comparte y qué no**: lo aprendido viaja entre los aparatos de UNA
 * misma cuenta. El puente valida el perfil contra la cuenta del token, así que
 * no hay forma de leer el de otra persona. El token, la dirección del servidor y
 * la clave del proxy NUNCA se suben.
 *
 * **El diccionario local manda**: aquí nunca se espera a la red para corregir
 * una palabra. La sincronización ocurre en segundo plano y lo peor que puede
 * pasar es que se posponga.
 *
 * **Orden de la sincronización** (importa): primero se baja lo del servidor y se
 * fusiona en local con las mismas reglas que él (`veces` máximo, `conocida`
 * solo avanza, el olvido lo decide la fecha), y solo después se sube el estado
 * ya fusionado. Si se subiera primero, un aparato con datos viejos pisaría lo
 * bueno del otro.
 */
class ClientePerfil(contexto: Context) {

    private val ajustes = Ajustes(contexto)

    private val cliente: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    /**
     * ¿Tiene sentido intentar? Hace falta sesión y un plan con sincronización.
     *
     * El plan lo manda el puente: esto solo evita mandar peticiones que van a
     * recibir un 403. La comprobación de verdad se hace allá.
     */
    fun puedeSincronizar(): Boolean =
        ajustes.haySesion && ajustes.plan in PLANES_CON_SINCRONIZACION

    /**
     * ¿Ya toca sincronizar? Frena las llamadas repetidas del teclado: sin esto,
     * cada vez que aparece el teclado se pediría el diccionario entero.
     */
    fun tocaSincronizar(minutos: Int = MINUTOS_ENTRE_SINCRONIZACIONES): Boolean {
        val ahora = System.currentTimeMillis()
        if (ahora - ultimaSincronizacion < minutos * 60_000L) return false
        ultimaSincronizacion = ahora
        return true
    }

    /**
     * Baja, fusiona y sube el perfil completo.
     *
     * Se llama desde un hilo de fondo. No lanza excepciones: devuelve el motivo
     * en `ResultadoCuenta.Error` para que el llamador decida si lo enseña (el
     * teclado lo ignora; la pantalla de cuenta lo muestra).
     */
    fun sincronizar(diccionario: Diccionario): ResultadoCuenta {
        if (!puedeSincronizar()) {
            return ResultadoCuenta.Error("Sin sesión o sin plan de sincronización.")
        }

        // 1. Bajar lo del servidor.
        when (val bajado = pedir("/api/v1/perfil/diccionario")) {
            is ResultadoCuenta.Error -> return bajado
            is ResultadoCuenta.Bien -> {
                diccionario.aplicarAprendido(bajado.datos.optJSONArray("palabras"), null)
            }
        }
        when (val bajado = pedir("/api/v1/perfil/sustituciones")) {
            is ResultadoCuenta.Error -> return bajado
            is ResultadoCuenta.Bien -> {
                diccionario.aplicarAprendido(null, bajado.datos.optJSONArray("sustituciones"))
            }
        }

        // 2. Subir el estado ya fusionado; el puente devuelve el resultado final.
        val palabras = JSONObject().put("palabras", diccionario.exportarPalabras())
        when (val subido = enviar("/api/v1/perfil/diccionario", palabras)) {
            is ResultadoCuenta.Error -> return subido
            is ResultadoCuenta.Bien -> {
                diccionario.aplicarAprendido(subido.datos.optJSONArray("palabras"), null)
            }
        }
        val correcciones = JSONObject()
            .put("sustituciones", diccionario.exportarSustituciones())
        return when (val subido = enviar("/api/v1/perfil/sustituciones", correcciones)) {
            is ResultadoCuenta.Error -> subido
            is ResultadoCuenta.Bien -> {
                diccionario.aplicarAprendido(
                    null, subido.datos.optJSONArray("sustituciones")
                )
                subido
            }
        }
    }

    /**
     * Sube los ajustes del aparato y, si el servidor tiene otros más nuevos,
     * aplica los suyos (`"status": "conflicto"`).
     *
     * Solo se llama desde la pantalla de cuenta, cuando el usuario pide
     * explícitamente que sus casillas viajen. Nunca desde el teclado: pisar los
     * ajustes del otro aparato sin preguntar sería peor que no sincronizar.
     */
    fun subirAjustes(): ResultadoCuenta {
        if (!ajustes.haySesion) return ResultadoCuenta.Error("No hay sesión abierta.")
        val cuerpo = JSONObject()
            .put("revision", ajustes.revisionAjustes)
            .put("ajustes", ajustes.exportarAjustes())
        return when (val resultado = enviar("/api/v1/perfil/ajustes", cuerpo, metodo = "PUT")) {
            is ResultadoCuenta.Error -> resultado
            is ResultadoCuenta.Bien -> {
                val perfil = resultado.datos.optJSONObject("perfil")
                val choco = resultado.datos.optString("status") == "conflicto"
                if (perfil != null) {
                    if (choco) {
                        // Ganó el servidor: se aplica lo suyo y se avisa.
                        perfil.optJSONObject("ajustes")?.let { ajustes.aplicarAjustes(it) }
                    }
                    ajustes.revisionAjustes = perfil.optInt("revision", ajustes.revisionAjustes)
                }
                if (choco) {
                    ResultadoCuenta.Error(
                        "Los ajustes habían cambiado en otro aparato. Se aplicaron los de allá."
                    )
                } else {
                    resultado
                }
            }
        }
    }

    /**
     * Baja los ajustes del perfil (al entrar por primera vez en este aparato).
     *
     * Solo aplica si el celular no tiene revisión guardada: si ya hay ajustes
     * propios, se respetan y se espera a que el usuario pida subirlos.
     */
    fun bajarAjustes(): ResultadoCuenta = when (val resultado = pedir("/api/v1/perfil")) {
        is ResultadoCuenta.Error -> resultado
        is ResultadoCuenta.Bien -> {
            val perfil = resultado.datos.optJSONObject("perfil")
            if (perfil != null) {
                val revision = perfil.optInt("revision", 0)
                if (ajustes.revisionAjustes == 0) {
                    perfil.optJSONObject("ajustes")?.let { ajustes.aplicarAjustes(it) }
                }
                ajustes.revisionAjustes = revision
            }
            resultado
        }
    }

    // --- Red -------------------------------------------------------------

    private fun pedir(ruta: String): ResultadoCuenta =
        ejecutar(constructor(ruta).get().build())

    private fun enviar(
        ruta: String,
        cuerpo: JSONObject,
        metodo: String = "POST"
    ): ResultadoCuenta {
        val peticion = constructor(ruta)
            .method(metodo, cuerpo.toString().toRequestBody(TIPO_JSON))
            .build()
        return ejecutar(peticion)
    }

    private fun ejecutar(peticion: Request): ResultadoCuenta = try {
        cliente.newCall(peticion).execute().use { respuesta ->
            val cuerpo = respuesta.body?.string().orEmpty()
            if (respuesta.isSuccessful) {
                ResultadoCuenta.Bien(JSONObject(cuerpo))
            } else {
                ResultadoCuenta.Error(motivoDe(cuerpo, respuesta.code))
            }
        }
    } catch (e: IOException) {
        Log.w(ETIQUETA, "Sin red para el perfil: ${e.message}")
        ResultadoCuenta.Error("No se pudo contactar con el servidor: ${e.message}")
    } catch (e: Exception) {
        Log.w(ETIQUETA, "Respuesta inesperada del perfil: ${e.message}")
        ResultadoCuenta.Error("Respuesta inesperada del servidor.")
    }

    private fun constructor(ruta: String): Request.Builder {
        val peticion = Request.Builder().url(ajustes.urlBase.trimEnd('/') + ruta)
        peticion.header("Authorization", "Bearer ${ajustes.token}")
        if (ajustes.claveCliente.isNotBlank()) {
            peticion.header(CABECERA_CLAVE, ajustes.claveCliente)
        }
        return peticion
    }

    private fun motivoDe(cuerpo: String, codigo: Int): String {
        val delServidor = try {
            val json = JSONObject(cuerpo)
            json.optString("motivo").ifBlank { json.optString("detail") }
        } catch (e: Exception) {
            ""
        }
        if (delServidor.isNotBlank()) return delServidor
        return when (codigo) {
            401 -> "La sesión caducó. Vuelve a entrar en Cuenta."
            403 -> "Tu plan no incluye sincronizar entre aparatos."
            429 -> "Demasiadas peticiones seguidas; espera un momento."
            else -> "Error $codigo del servidor."
        }
    }

    companion object {
        private const val ETIQUETA = "ClientePerfil"
        private const val CABECERA_CLAVE = "X-Clave-Cliente"
        private val TIPO_JSON = "application/json; charset=utf-8".toMediaType()

        private val PLANES_CON_SINCRONIZACION = setOf("basico", "pro", "maestro")

        /** Cada cuánto se intenta, como mucho, desde el teclado. */
        private const val MINUTOS_ENTRE_SINCRONIZACIONES = 15

        /** Proceso: cuándo se intentó por última vez (no se guarda en disco). */
        @Volatile
        private var ultimaSincronizacion: Long = 0L
    }
}
