package com.ivanuxs.skipkey

import android.content.Context
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Lo que devuelve el puente al hablar de cuentas. */
sealed class ResultadoCuenta {
    /** Todo bien: viene el JSON de la respuesta (token, usuario, plan…). */
    data class Bien(val datos: JSONObject) : ResultadoCuenta()

    /** Algo salió mal; el texto ya está en español y se puede enseñar. */
    data class Error(val motivo: String) : ResultadoCuenta()
}

/**
 * Cuentas del puente: crear, verificar el correo, entrar, salir y ver el plan.
 *
 * Usa el mismo `OkHttpClient` que el resto de la app y las mismas cabeceras
 * (token y, si está configurada, la clave del proxy). Lo único que cambia
 * respecto al dictado es que aquí, cuando no hay sesión, no se manda token.
 *
 * La contraseña NO se guarda nunca: viaja una vez y lo que se queda en el
 * celular es el token del dispositivo (cifrado, en `Ajustes`).
 */
class ClienteCuenta(contexto: Context) {

    private val ajustes = Ajustes(contexto)

    private val cliente: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    /** Nombre del celular, para que el usuario reconozca sus sesiones. */
    private val dispositivo: String
        get() = listOf(android.os.Build.MANUFACTURER, android.os.Build.MODEL)
            .joinToString(" ")
            .trim()
            .ifEmpty { "android" }

    /** ¿Hay dirección del puente configurada? Sin ella no se puede nada. */
    fun configurado(): Boolean = ajustes.urlBase.isNotBlank()

    // --- Cuenta ----------------------------------------------------------

    fun registrar(usuario: String, contrasena: String, email: String = ""): ResultadoCuenta =
        enviar(
            "/api/v1/registro",
            JSONObject()
                .put("usuario", usuario.trim())
                .put("contrasena", contrasena)
                .put("email", email.trim())
                .put("dispositivo", dispositivo)
        )

    fun verificar(email: String, codigo: String): ResultadoCuenta =
        enviar(
            "/api/v1/verificar",
            JSONObject()
                .put("email", email.trim())
                .put("codigo", codigo.trim())
                .put("dispositivo", dispositivo)
        )

    fun reenviarCodigo(email: String): ResultadoCuenta =
        enviar("/api/v1/reenviar", JSONObject().put("email", email.trim()))

    fun entrar(usuario: String, contrasena: String): ResultadoCuenta =
        enviar(
            "/api/v1/login",
            JSONObject()
                .put("usuario", usuario.trim())
                .put("contrasena", contrasena)
                .put("dispositivo", dispositivo)
        )

    /** Estado de la cuenta y del plan (GET /yo). */
    fun estado(): ResultadoCuenta = pedir("/api/v1/yo")

    /** Catálogo de planes. Es público: se puede mirar sin cuenta. */
    fun planes(): ResultadoCuenta = pedir("/api/v1/planes")

    fun salir(): ResultadoCuenta {
        val resultado = enviar("/api/v1/salir", JSONObject())
        // Aunque el puente no conteste, en este celular se cierra igual.
        ajustes.cerrarSesion()
        return resultado
    }

    fun borrarCuenta(): ResultadoCuenta {
        val resultado = borrar("/api/v1/cuenta")
        if (resultado is ResultadoCuenta.Bien) ajustes.cerrarSesion()
        return resultado
    }

    // --- Guardado de la sesión -------------------------------------------

    /**
     * Guarda el token y el plan que acaba de devolver el puente.
     *
     * El plan NO se inventa aquí: se copia lo que responde el servidor. Si
     * alguien manipula el celular para ponerse «pro», el puente le sigue
     * contestando 403 donde no toque.
     */
    fun guardarSesion(datos: JSONObject) {
        val token = datos.optString("token")
        if (token.isNotBlank()) ajustes.token = token
        val usuario = datos.optJSONObject("usuario")
        if (usuario != null) {
            ajustes.usuario = usuario.optString("usuario")
            ajustes.email = usuario.optString("email")
            ajustes.plan = usuario.optString("plan")
        }
        // El perfil es lo que separa el diccionario de esta cuenta del de
        // cualquier otra: sin su id no se puede sincronizar nada.
        val perfil = datos.optJSONObject("perfil")
        if (perfil != null) {
            ajustes.idPerfil = perfil.optInt("id", ajustes.idPerfil)
            ajustes.revisionAjustes = perfil.optInt("revision", ajustes.revisionAjustes)
        }
        val plan = datos.optJSONObject("plan")
        if (plan != null) {
            ajustes.planNombre = plan.optString("nombre")
            if (plan.isNull("minutos_restantes")) {
                ajustes.minutosRestantes = -1
            } else {
                ajustes.minutosRestantes = plan.optDouble("minutos_restantes", -1.0).toInt()
            }
            if (plan.isNull("traducciones_restantes")) {
                ajustes.traduccionesRestantes = -1
            } else {
                ajustes.traduccionesRestantes = plan.optInt("traducciones_restantes", -1)
            }
            if (ajustes.plan.isBlank()) ajustes.plan = plan.optString("plan")
        }
    }

    // --- Red -------------------------------------------------------------

    private fun enviar(ruta: String, cuerpo: JSONObject): ResultadoCuenta {
        if (!configurado()) {
            return ResultadoCuenta.Error(
                "Falta la dirección del servidor. Sin ella solo se puede dictar " +
                    "con el motor del celular."
            )
        }
        val peticion = constructor(ruta)
            .post(cuerpo.toString().toRequestBody(TIPO_JSON))
            .build()
        return ejecutar(peticion)
    }

    private fun pedir(ruta: String): ResultadoCuenta {
        if (!configurado()) return ResultadoCuenta.Error("Falta la dirección del servidor.")
        return ejecutar(constructor(ruta).get().build())
    }

    private fun borrar(ruta: String): ResultadoCuenta {
        if (!configurado()) return ResultadoCuenta.Error("Falta la dirección del servidor.")
        return ejecutar(constructor(ruta).delete().build())
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
        Log.w(ETIQUETA, "Fallo de red en cuentas: ${e.message}")
        ResultadoCuenta.Error("No se pudo contactar con el servidor: ${e.message}")
    } catch (e: Exception) {
        Log.w(ETIQUETA, "Respuesta inesperada en cuentas: ${e.message}")
        ResultadoCuenta.Error("Respuesta inesperada del servidor.")
    }

    private fun constructor(ruta: String): Request.Builder {
        val peticion = Request.Builder().url(ajustes.urlBase.trimEnd('/') + ruta)
        // Solo se manda token si hay sesión: registro y login van sin él.
        if (ajustes.token.isNotBlank()) {
            peticion.header("Authorization", "Bearer ${ajustes.token}")
        }
        if (ajustes.claveCliente.isNotBlank()) {
            peticion.header(CABECERA_CLAVE, ajustes.claveCliente)
        }
        return peticion
    }

    /** Traduce el error del puente a algo que se pueda enseñar. */
    private fun motivoDe(cuerpo: String, codigo: Int): String {
        val delServidor = try {
            val json = JSONObject(cuerpo)
            json.optString("motivo").ifBlank { json.optString("detail") }
        } catch (e: Exception) {
            ""
        }
        if (delServidor.isNotBlank()) return delServidor
        return when (codigo) {
            401 -> "Usuario o contraseña incorrectos, o la sesión caducó."
            403 -> "El servidor no permitió esa operación."
            409 -> "Ese usuario o correo ya tiene una cuenta."
            422 -> "Revisa los datos: el servidor no los aceptó."
            429 -> "Demasiados intentos seguidos; espera un momento."
            503 -> "El servidor está ocupado; prueba en un momento."
            else -> "Error $codigo del servidor."
        }
    }

    companion object {
        private const val ETIQUETA = "ClienteCuenta"
        private const val CABECERA_CLAVE = "X-Clave-Cliente"
        private val TIPO_JSON = "application/json; charset=utf-8".toMediaType()
    }
}
