package com.ivanuxs.dictadoime

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Ajustes persistentes del teclado, guardados cifrados en el dispositivo.
 *
 * Van cifrados porque aquí no hay configuración cualquiera: están el token del
 * puente y la clave que valida el reverse proxy.
 */
class Ajustes(contexto: Context) {

    private val preferencias = EncryptedSharedPreferences.create(
        contexto,
        ARCHIVO,
        MasterKey.Builder(contexto).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    /**
     * Dirección del puente, p. ej. https://dictado.tu-dominio.com
     *
     * Si no se ha guardado nada en el celular, se usa el valor que venga dentro
     * del APK (BuildConfig), para no tener que escribir nada aquí.
     */
    var urlBase: String
        get() = preferencias.getString(URL, "").orEmpty().trim()
            .ifBlank { BuildConfig.URL_BASE }
        set(valor) = preferencias.edit().putString(URL, valor.trim()).apply()

    /** Token de acceso del puente (Authorization: Bearer). Igual: del APK si no hay nada guardado. */
    var token: String
        get() = preferencias.getString(TOKEN, "").orEmpty().trim()
            .ifBlank { BuildConfig.TOKEN }
        set(valor) = preferencias.edit().putString(TOKEN, valor.trim()).apply()

    /** Clave extra que valida el reverse proxy (cabecera X-Clave-Cliente). */
    var claveCliente: String
        get() = preferencias.getString(CLAVE, "").orEmpty().trim()
            .ifBlank { BuildConfig.CLAVE }
        set(valor) = preferencias.edit().putString(CLAVE, valor.trim()).apply()

    /** true = dictado en vivo (WebSocket, texto mientras se habla). */
    var modoVivo: Boolean
        get() = preferencias.getBoolean(VIVO, false)
        set(valor) = preferencias.edit().putBoolean(VIVO, valor).apply()

    /**
     * true = dictar con NUESTRO servidor; false (por defecto) = con el
     * reconocedor del celular, que es el motor de la versión gratuita.
     *
     * Estando encendido, si faltan dirección y token se usa el del celular:
     * la APK pública no lleva credenciales, así que no puede usar el servidor.
     */
    var usarServidor: Boolean
        get() = preferencias.getBoolean(USAR_SERVIDOR, false)
        set(valor) = preferencias.edit().putBoolean(USAR_SERVIDOR, valor).apply()

    /** Cuánto se espera la respuesta antes de rendirse. */
    var segundosEspera: Int
        get() = preferencias.getInt(ESPERA, ESPERA_POR_DEFECTO)
        set(valor) = preferencias.edit().putInt(ESPERA, valor).apply()

    /** Mostrar la fila de números (1-0) encima de las letras. */
    var filaNumeros: Boolean
        get() = preferencias.getBoolean(FILA_NUMEROS, true)
        set(valor) = preferencias.edit().putBoolean(FILA_NUMEROS, valor).apply()

    /** Alto de las teclas, en dp. Se recorta al rango permitido. */
    var alturaTecla: Int
        get() = preferencias.getInt(ALTURA, ALTURA_POR_DEFECTO)
        set(valor) = preferencias.edit()
            .putInt(ALTURA, valor.coerceIn(ALTURA_MINIMA, ALTURA_MAXIMA))
            .apply()

    /** Vibración breve al pulsar las teclas. */
    var vibrar: Boolean
        get() = preferencias.getBoolean(VIBRAR, true)
        set(valor) = preferencias.edit().putBoolean(VIBRAR, valor).apply()

    /** Mostrar la barra de sugerencias (y las predicciones) mientras se escribe. */
    var sugerencias: Boolean
        get() = preferencias.getBoolean(SUGERENCIAS, true)
        set(valor) = preferencias.edit().putBoolean(SUGERENCIAS, valor).apply()

    /** Corregir la palabra al terminarla (tildes y erratas de una letra). */
    var autocorreccion: Boolean
        get() = preferencias.getBoolean(AUTOCORRECCION, true)
        set(valor) = preferencias.edit().putBoolean(AUTOCORRECCION, valor).apply()

    /**
     * Ofrecer en una franja lo que haya en el portapapeles, para pegarlo con un
     * toque (como hace Gboard). No se guarda nada: solo se lee el portapapeles
     * del sistema mientras el teclado está en pantalla.
     */
    var sugerirPortapapeles: Boolean
        get() = preferencias.getBoolean(PORTAPAPELES, true)
        set(valor) = preferencias.edit().putBoolean(PORTAPAPELES, valor).apply()

    /**
     * Guardar un historial de lo copiado (con fijados) además de la franja.
     * Va cifrado en su propio archivo; ver `Portapapeles`.
     */
    var guardarHistorial: Boolean
        get() = preferencias.getBoolean(HISTORIAL, true)
        set(valor) = preferencias.edit().putBoolean(HISTORIAL, valor).apply()

    /** ¿Está lo mínimo para intentar dictar? */
    val configurado: Boolean
        get() = urlBase.isNotBlank() && token.isNotBlank()

    /** Dirección WebSocket equivalente a la URL base (http→ws, https→wss). */
    fun urlWebSocket(): String {
        val base = urlBase.trimEnd('/')
        val ws = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> "ws://$base"
        }
        return "$ws/ws/transcribe"
    }

    companion object {
        private const val ARCHIVO = "dictado_seguro"
        private const val URL = "url_base"
        private const val TOKEN = "token"
        private const val CLAVE = "clave_cliente"
        private const val VIVO = "modo_vivo"
        private const val USAR_SERVIDOR = "usar_servidor"
        private const val ESPERA = "segundos_espera"
        private const val FILA_NUMEROS = "fila_numeros"
        private const val ALTURA = "altura_tecla"
        private const val VIBRAR = "vibrar"
        private const val SUGERENCIAS = "sugerencias"
        private const val AUTOCORRECCION = "autocorreccion"
        private const val PORTAPAPELES = "sugerir_portapapeles"
        private const val HISTORIAL = "guardar_historial"

        const val ESPERA_POR_DEFECTO = 60

        /** Alto de las teclas: por defecto y límites que se ofrecen en la app. */
        const val ALTURA_POR_DEFECTO = 46
        const val ALTURA_MINIMA = 36
        const val ALTURA_MAXIMA = 64
    }
}
