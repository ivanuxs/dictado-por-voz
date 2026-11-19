package com.ivanuxs.skipkey

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.json.JSONObject

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

    /**
     * Token de acceso del puente (Authorization: Bearer).
     *
     * Es el de la CUENTA con la que se entró (lo devuelve /api/v1/login). Si no
     * hay nada guardado se usa el del APK, que es el «token maestro» de los
     * clientes privados: así los APK que ya funcionaban siguen igual. Una vez
     * guardado, se respeta aunque sea una cadena vacía (cerrar sesión).
     */
    var token: String
        get() = preferencias.getString(TOKEN, null)?.trim() ?: BuildConfig.TOKEN
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

    /**
     * Idioma al que traduce el botón 🌐 del teclado: en, pt, it, zh o es.
     * Una pulsación larga en el botón lo cambia (cicla entre ellos).
     */
    var idiomaTraduccion: String
        get() = preferencias.getString(IDIOMA_TRADUCCION, "en").orEmpty().ifBlank { "en" }
        set(valor) = preferencias.edit().putString(IDIOMA_TRADUCCION, valor).apply()

    /**
     * Perfil del diccionario.
     *
     * Cada perfil tiene sus propias palabras aprendidas, bigramas y
     * correcciones (los archivos llevan el perfil en el nombre). Sirve para que
     * dos personas que usen el mismo celular no se mezclen el diccionario.
     * El cambio se aplica la próxima vez que se abre el teclado.
     */
    var perfil: String
        get() = nombrePerfil(preferencias.getString(PERFIL, "").orEmpty())
        set(valor) = preferencias.edit().putString(PERFIL, nombrePerfil(valor)).apply()

    /**
     * Cuenta con la que se ha entrado al puente.
     *
     * El teclado no guarda contraseñas: solo el token de este dispositivo. Si
     * no hay sesión se dicta con el motor del celular, que es gratis y no usa
     * la red; el servidor y las funciones avanzadas son de pago.
     *
     * La identidad es el **nombre de usuario**; el correo es opcional y solo
     * sirve para confirmar y recuperar la cuenta.
     */
    var usuario: String
        get() = preferencias.getString(USUARIO, "").orEmpty().trim()
        set(valor) = preferencias.edit().putString(USUARIO, valor.trim()).apply()

    /** Correo de contacto de la cuenta (puede estar vacío). */
    var email: String
        get() = preferencias.getString(EMAIL, "").orEmpty()
        set(valor) = preferencias.edit().putString(EMAIL, valor.trim()).apply()

    /**
     * Id del perfil en el puente: es lo que separa el diccionario de esta
     * cuenta del de cualquier otra, incluso en el mismo celular.
     */
    var idPerfil: Int
        get() = preferencias.getInt(ID_PERFIL, 0)
        set(valor) = preferencias.edit().putInt(ID_PERFIL, valor).apply()

    /** Revisión de los ajustes del perfil (para detectar choques al guardar). */
    var revisionAjustes: Int
        get() = preferencias.getInt(REVISION, 0)
        set(valor) = preferencias.edit().putInt(REVISION, valor).apply()

    /** Plan que manda el puente: gratis, basico, pro (o maestro con el token del APK). */
    var plan: String
        get() = preferencias.getString(PLAN, "").orEmpty().trim().lowercase()
        set(valor) = preferencias.edit().putString(PLAN, valor.trim().lowercase()).apply()

    /** Nombre para enseñar: «Gratis», «Básico», «Pro». */
    var planNombre: String
        get() = preferencias.getString(PLAN_NOMBRE, "").orEmpty()
        set(valor) = preferencias.edit().putString(PLAN_NOMBRE, valor.trim()).apply()

    /** Minutos de dictado por servidor que quedan hoy (-1 = sin dato/ilimitado). */
    var minutosRestantes: Int
        get() = preferencias.getInt(MINUTOS, -1)
        set(valor) = preferencias.edit().putInt(MINUTOS, valor).apply()

    /** Traducciones que quedan hoy (-1 = sin dato/ilimitado). */
    var traduccionesRestantes: Int
        get() = preferencias.getInt(TRADUCCIONES, -1)
        set(valor) = preferencias.edit().putInt(TRADUCCIONES, valor).apply()

    /** ¿Hay una cuenta con sesión abierta? */
    val haySesion: Boolean
        get() = token.isNotBlank()

    /**
     * ¿El plan da derecho a servidor y traducción?
     *
     * Si hay sesión y el plan aún no se conoce (APK privado con el token
     * maestro), se deja intentar: el puente manda y responde 403 si no toca.
     */
    val planPago: Boolean
        get() = when (plan) {
            "gratis" -> false
            "basico", "pro", "maestro" -> true
            else -> haySesion
        }

    /** ¿El plan incluye el dictado en vivo (texto mientras hablas)? */
    val planVivo: Boolean
        get() = plan == "pro" || plan == "maestro"

    /** Cierra la sesión en este celular (el token se queda vacío a propósito). */
    fun cerrarSesion() {
        preferencias.edit()
            .putString(TOKEN, "")
            .putString(USUARIO, "")
            .putString(EMAIL, "")
            .putString(PLAN, "")
            .putString(PLAN_NOMBRE, "")
            .putInt(MINUTOS, -1)
            .putInt(TRADUCCIONES, -1)
            .putInt(ID_PERFIL, 0)
            .putInt(REVISION, 0)
            .apply()
    }

    /**
     * Los ajustes que se guardan en el perfil de la cuenta (JSON plano).
     *
     * Se exportan solo las preferencias: el token, la dirección y la clave del
     * proxy NUNCA salen del celular. Así la misma cuenta se encuentra sus
     * casillas en otro aparato, pero las credenciales de cada uno son suyas.
     */
    fun exportarAjustes(): JSONObject = JSONObject()
        .put("modo_vivo", modoVivo)
        .put("usar_servidor", usarServidor)
        .put("segundos_espera", segundosEspera)
        .put("fila_numeros", filaNumeros)
        .put("altura_tecla", alturaTecla)
        .put("vibrar", vibrar)
        .put("sugerencias", sugerencias)
        .put("autocorreccion", autocorreccion)
        .put("sugerir_portapapeles", sugerirPortapapeles)
        .put("guardar_historial", guardarHistorial)
        .put("idioma_traduccion", idiomaTraduccion)
        .put("perfil", perfil)

    /** Aplica lo que vino del perfil sin tocar lo que no venga en el JSON. */
    fun aplicarAjustes(datos: JSONObject) {
        if (datos.has("modo_vivo")) modoVivo = datos.optBoolean("modo_vivo", modoVivo)
        if (datos.has("usar_servidor")) usarServidor = datos.optBoolean("usar_servidor", usarServidor)
        if (datos.has("segundos_espera")) {
            segundosEspera = datos.optInt("segundos_espera", segundosEspera)
        }
        if (datos.has("fila_numeros")) filaNumeros = datos.optBoolean("fila_numeros", filaNumeros)
        if (datos.has("altura_tecla")) alturaTecla = datos.optInt("altura_tecla", alturaTecla)
        if (datos.has("vibrar")) vibrar = datos.optBoolean("vibrar", vibrar)
        if (datos.has("sugerencias")) sugerencias = datos.optBoolean("sugerencias", sugerencias)
        if (datos.has("autocorreccion")) {
            autocorreccion = datos.optBoolean("autocorreccion", autocorreccion)
        }
        if (datos.has("sugerir_portapapeles")) {
            sugerirPortapapeles = datos.optBoolean("sugerir_portapapeles", sugerirPortapapeles)
        }
        if (datos.has("guardar_historial")) {
            guardarHistorial = datos.optBoolean("guardar_historial", guardarHistorial)
        }
        if (datos.has("idioma_traduccion")) {
            idiomaTraduccion = datos.optString("idioma_traduccion", idiomaTraduccion)
        }
        if (datos.has("perfil")) perfil = datos.optString("perfil", perfil)
    }

    /**
     * Cuenta las veces que se borró el diccionario desde la pantalla de ajustes.
     *
     * El teclado lleva el diccionario en memoria: al ver que este número cambió,
     * suelta el suyo y carga el de disco. Sin esto, borrar no servía de nada si
     * el teclado seguía vivo (volvía a escribir lo aprendido al guardar).
     */
    var generacionDiccionario: Int
        get() = preferencias.getInt(GENERACION_DICCIONARIO, 0)
        set(valor) = preferencias.edit().putInt(GENERACION_DICCIONARIO, valor).apply()

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
        private const val IDIOMA_TRADUCCION = "idioma_traduccion"
        private const val PERFIL = "perfil_diccionario"
        private const val GENERACION_DICCIONARIO = "generacion_diccionario"
        private const val USUARIO = "cuenta_usuario"
        private const val EMAIL = "cuenta_email"
        private const val ID_PERFIL = "cuenta_id_perfil"
        private const val REVISION = "cuenta_revision_ajustes"
        private const val PLAN = "cuenta_plan"
        private const val PLAN_NOMBRE = "cuenta_plan_nombre"
        private const val MINUTOS = "cuenta_minutos_restantes"
        private const val TRADUCCIONES = "cuenta_traducciones_restantes"

        /** Perfil que se usa cuando no se ha escrito ninguno. */
        const val PERFIL_POR_DEFECTO = "personal"

        /**
         * Deja el nombre del perfil en algo que sirva como nombre de archivo:
         * minúsculas, sin acentos ni espacios y con 20 caracteres como máximo.
         * Si no queda nada, se usa el perfil por defecto.
         */
        fun nombrePerfil(valor: String): String {
            val plano = valor.trim().lowercase()
                .replace('á', 'a').replace('é', 'e').replace('í', 'i')
                .replace('ó', 'o').replace('ú', 'u').replace('ü', 'u').replace('ñ', 'n')
            val limpio = plano.filter { it in 'a'..'z' || it in '0'..'9' || it == '-' || it == '_' }
            return limpio.take(20).ifEmpty { PERFIL_POR_DEFECTO }
        }

        const val ESPERA_POR_DEFECTO = 60

        /**
         * Alto de las teclas: por defecto y límites que se ofrecen en la app.
         * El valor por defecto bajó de 46 a 42 el 2026-10-31: el micrófono
         * grande pasó a estar arriba, en la misma pantalla que las letras, y
         * con 46 dp el teclado tapaba demasiado del campo de texto
         * (5 filas + banda). El ajuste manual sigue existiendo.
         */
        const val ALTURA_POR_DEFECTO = 42
        const val ALTURA_MINIMA = 36
        const val ALTURA_MAXIMA = 64
    }
}
