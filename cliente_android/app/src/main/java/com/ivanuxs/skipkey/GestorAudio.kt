package com.ivanuxs.skipkey

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log

/**
 * Captura del micrófono en el formato que espera el servidor de dictado:
 * PCM 16 bits, mono, 16 kHz.
 *
 * Envuelve AudioRecord y no lanza excepciones hacia el teclado: si el
 * micrófono no está disponible, devuelve false y el teclado muestra el aviso.
 */
class GestorAudio {

    private var registro: AudioRecord? = null

    @Volatile
    private var activo = false

    val grabando: Boolean get() = activo

    /** Abre el micrófono. Devuelve false si no se puede. */
    fun iniciar(): Boolean {
        if (activo) return true
        val minimo = AudioRecord.getMinBufferSize(
            FRECUENCIA, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minimo <= 0) {
            Log.w(ETIQUETA, "El micrófono no admite 16 kHz mono.")
            return false
        }
        val tamano = maxOf(minimo, TAMANO_BLOQUE * 4)
        val nuevo = try {
            // VOICE_RECOGNITION: sin procesado pensado para llamadas, que es
            // el que mejor se comporta con los modelos de voz.
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                FRECUENCIA,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                tamano
            )
        } catch (e: Exception) {
            Log.w(ETIQUETA, "No se pudo crear AudioRecord: ${e.message}")
            return false
        }
        if (nuevo.state != AudioRecord.STATE_INITIALIZED) {
            Log.w(ETIQUETA, "AudioRecord no quedó inicializado.")
            nuevo.release()
            return false
        }
        registro = nuevo
        activo = true
        return try {
            nuevo.startRecording()
            true
        } catch (e: Exception) {
            Log.w(ETIQUETA, "No se pudo empezar a grabar: ${e.message}")
            activo = false
            nuevo.release()
            registro = null
            false
        }
    }

    /** Lee un bloque de 200 ms. Devuelve null si ya no hay nada útil. */
    fun leer(): ByteArray? {
        val actual = registro ?: return null
        if (!activo) return null
        val bloque = ByteArray(TAMANO_BLOQUE)
        val leidos = try {
            actual.read(bloque, 0, bloque.size)
        } catch (e: Exception) {
            Log.w(ETIQUETA, "Error leyendo del micrófono: ${e.message}")
            return null
        }
        return if (leidos > 0) bloque.copyOf(leidos) else null
    }

    /** Para la grabación (la lectura en curso termina sola). */
    fun detener() {
        activo = false
        try {
            registro?.stop()
        } catch (e: Exception) {
            Log.w(ETIQUETA, "Error al parar: ${e.message}")
        }
    }

    /** Suelta el micrófono. Se llama cuando la corrutina de lectura ya salió. */
    fun liberar() {
        activo = false
        try {
            registro?.release()
        } catch (e: Exception) {
            Log.w(ETIQUETA, "Error al liberar: ${e.message}")
        }
        registro = null
    }

    companion object {
        const val FRECUENCIA = 16000
        private const val TAMANO_BLOQUE = FRECUENCIA / 5 * 2 // 200 ms en bytes
        private const val ETIQUETA = "GestorAudio"
    }
}
