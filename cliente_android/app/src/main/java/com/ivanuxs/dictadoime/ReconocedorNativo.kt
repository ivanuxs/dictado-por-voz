package com.ivanuxs.dictadoime

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.Locale

/**
 * Dictado con el reconocedor del propio celular.
 *
 * Es el motor de la versión gratuita: no habla con nuestro puente, así que
 * funciona sin cuenta y sin servidor. La calidad es la del sistema, menor que la
 * de nuestro servidor con Whisper; en Android 12+ se usa además el modelo SIN
 * CONEXIÓN cuando el celular lo tiene descargado.
 *
 * El reconocedor captura el micrófono por su cuenta: cuando se usa este motor
 * NO se abre `GestorAudio` (serían dos capturas a la vez).
 *
 * Los avisos se entregan en el hilo principal, que es donde los da Android.
 */
class ReconocedorNativo(contexto: Context) {

    /** Avisos al teclado mientras se dicta con el celular. */
    interface Avisos {
        /** Texto que va entendiendo (solo para enseñarlo en la línea de estado). */
        fun alParcial(texto: String)

        /** Texto definitivo de esta intervención. */
        fun alFinal(texto: String)

        /** No se pudo reconocer; el motivo ya viene en castellano. */
        fun alError(motivo: String)
    }

    private val contexto = contexto.applicationContext
    private val manejador = Handler(Looper.getMainLooper())

    /**
     * UNA sola instancia durante toda la vida del teclado.
     *
     * Antes se destruía y se creaba una nueva en cada dictado. El servicio de
     * reconocimiento tarda en soltar la sesión anterior (sobre todo el modelo
     * sin conexión), así que el cliente nuevo recibía `ERROR_RECOGNIZER_BUSY`:
     * «ocupado». Reutilizar la instancia es además lo que recomienda Android.
     */
    private var reconocedor: SpeechRecognizer? = null
    private var avisos: Avisos? = null

    /** Ya se intentó una vez tras un «ocupado»: no se repite en bucle. */
    private var reintentado = false

    /** Hay un reconocimiento en marcha (grabando o esperando el resultado). */
    var escuchando = false
        private set

    /** ¿Este celular tiene reconocedor de voz? */
    fun disponible(): Boolean = runCatching {
        SpeechRecognizer.isRecognitionAvailable(contexto)
    }.getOrDefault(false)

    /**
     * Empieza a escuchar. Devuelve false si no hay reconocedor o si no arranca
     * (el teclado enseña entonces el aviso que corresponda).
     */
    fun empezar(nuevos: Avisos): Boolean {
        if (!disponible()) return false
        // Un reintento pendiente ya no tiene sentido: este es un dictado nuevo.
        manejador.removeCallbacksAndMessages(null)
        avisos = nuevos
        reintentado = false
        if (escuchando) {
            // Quedó algo de la vez anterior (p. ej. se paró y no llegó el
            // resultado): se cancela antes de volver a arrancar.
            runCatching { reconocedor?.cancel() }
            escuchando = false
        }
        val actual = reconocedor ?: crear(preferirSinConexion = true)?.also {
            reconocedor = it
        } ?: return false
        return arrancar(actual)
    }

    /** Para de escuchar y pide el resultado (se entrega en `alFinal`/`alError`). */
    fun parar() {
        val actual = reconocedor ?: return
        runCatching { actual.stopListening() }
    }

    /** Aborta sin escribir nada: al ocultar el teclado, al cambiar de campo… */
    fun cancelar() {
        manejador.removeCallbacksAndMessages(null)
        avisos = null
        escuchando = false
        // La instancia se conserva: recrearla en cada dictado era lo que hacía
        // que el servicio contestara «ocupado».
        runCatching { reconocedor?.cancel() }
    }

    /** Suelta el reconocedor del todo (al destruir el servicio). */
    fun liberar() {
        manejador.removeCallbacksAndMessages(null)
        avisos = null
        soltarReconocedor()
    }

    // --- Interno ----------------------------------------------------------

    /** Arranca la escucha con una instancia ya creada. */
    private fun arrancar(actual: SpeechRecognizer): Boolean = try {
        actual.startListening(intencion())
        escuchando = true
        true
    } catch (e: Exception) {
        escuchando = false
        false
    }

    /**
     * Crea un reconocedor. Primero el SIN CONEXIÓN (Android 12+ con el idioma
     * descargado); si eso falla, el normal del sistema.
     */
    private fun crear(preferirSinConexion: Boolean): SpeechRecognizer? {
        val nuevo = try {
            if (preferirSinConexion && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(contexto)
            ) {
                SpeechRecognizer.createOnDeviceSpeechRecognizer(contexto)
            } else {
                SpeechRecognizer.createSpeechRecognizer(contexto)
            }
        } catch (e: Exception) {
            null
        }
        nuevo?.setRecognitionListener(oyente)
        return nuevo
    }

    /**
     * Reintento tras «ocupado».
     *
     * Se descarta la instancia, se espera un instante (el servicio necesita
     * soltar la sesión) y se vuelve a arrancar con el servicio NORMAL del
     * sistema: el sin conexión es el que se queda atascado en este celular.
     */
    private fun reintentar() {
        soltarReconocedor()
        manejador.postDelayed({
            val destinatario = avisos ?: return@postDelayed
            val nuevo = crear(preferirSinConexion = false)
            if (nuevo == null) {
                avisos = null
                destinatario.alError(contexto.getString(R.string.nativo_no_disponible))
                return@postDelayed
            }
            reconocedor = nuevo
            if (!arrancar(nuevo)) {
                escuchando = false
                avisos = null
                destinatario.alError(contexto.getString(R.string.nativo_error))
            }
        }, ESPERA_REINTENTO_MS)
    }

    private fun soltarReconocedor() {
        val actual = reconocedor ?: return
        reconocedor = null
        escuchando = false
        runCatching { actual.cancel() }
        runCatching { actual.destroy() }
    }

    /**
     * Lo que se le pide al reconocedor.
     *
     * El idioma es el del celular (si está en español, dicta en español). Los
     * parciales se piden para poder enseñar el avance en la línea de estado,
     * pero el texto se inserta solo al final: si el reconocedor reinicia la
     * frase, un insertado en vivo dejaría texto duplicado.
     */
    private fun intencion(): Intent =
        Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, idiomaDelCelular().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, contexto.packageName)
        }

    /** La configuración regional principal del sistema. */
    private fun idiomaDelCelular(): Locale = runCatching {
        val locales = contexto.resources.configuration.locales
        if (locales.isEmpty) Locale.getDefault() else locales[0]
    }.getOrDefault(Locale.getDefault())

    private fun primero(resultados: Bundle?): String? = resultados
        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
        ?.firstOrNull()
        ?.trim()

    private val oyente = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit

        override fun onPartialResults(partialResults: Bundle?) {
            val texto = primero(partialResults) ?: return
            avisos?.alParcial(texto)
        }

        override fun onResults(results: Bundle?) {
            escuchando = false
            val destinatario = avisos
            avisos = null
            val texto = primero(results)
            if (texto.isNullOrBlank()) {
                destinatario?.alError(contexto.getString(R.string.nativo_sin_texto))
            } else {
                destinatario?.alFinal(texto)
            }
        }

        override fun onError(error: Int) {
            if (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY &&
                !reintentado && avisos != null
            ) {
                // «Ocupado» casi siempre es que el servicio aún no ha soltado la
                // sesión anterior: se reintenta una vez con el servicio normal.
                reintentado = true
                escuchando = false
                reintentar()
                return
            }
            escuchando = false
            val destinatario = avisos
            avisos = null
            destinatario?.alError(mensaje(error))
        }
    }

    /** Traduce los códigos de error del reconocedor a algo que se entienda. */
    private fun mensaje(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_NO_MATCH,
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> contexto.getString(R.string.nativo_sin_texto)

        SpeechRecognizer.ERROR_NETWORK,
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> contexto.getString(R.string.nativo_sin_red)

        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
            contexto.getString(R.string.falta_permiso_micro)

        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> contexto.getString(R.string.nativo_ocupado)
        SpeechRecognizer.ERROR_AUDIO -> contexto.getString(R.string.nativo_sin_micro)

        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> contexto.getString(R.string.nativo_idioma)

        else -> contexto.getString(R.string.nativo_error)
    }

    private companion object {
        /** Margen para que el servicio de reconocimiento suelte la sesión. */
        const val ESPERA_REINTENTO_MS = 350L
    }
}
