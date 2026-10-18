package com.ivanuxs.dictadoime

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.json.JSONArray
import org.json.JSONObject

/**
 * Historial de lo copiado, guardado cifrado en el dispositivo.
 *
 * Es lo que hace Gboard: además de ofrecer lo último copiado, se recuerdan las
 * copias anteriores y algunas se pueden FIJAR para que no se pierdan.
 *
 * Va cifrado porque en el portapapeles acaban tokens, claves y contraseñas (y
 * este teclado se usa justo para eso). Si el archivo no se puede descifrar —por
 * ejemplo, tras borrar la clave del keystore del sistema— se empieza vacío en
 * vez de romper el teclado: el historial es una comodidad, no algo crítico.
 *
 * Se guarda como un único JSON en las preferencias: son pocas entradas (40
 * recientes + 100 fijadas como mucho) y no hace falta una base de datos.
 */
class Portapapeles(contexto: Context) {

    /** Una copia guardada: el texto, si está fijada y cuándo llegó. */
    data class Entrada(val texto: String, val fijada: Boolean, val cuando: Long)

    private val preferencias = runCatching {
        EncryptedSharedPreferences.create(
            contexto,
            ARCHIVO,
            MasterKey.Builder(contexto).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }.getOrNull()

    /** ¿Se puede guardar? Si el archivo cifrado no se abrió, no hay historial. */
    val disponible: Boolean
        get() = preferencias != null

    /** Lo guardado, con los fijados primero y después los recientes. */
    fun listar(): List<Entrada> = recortar(cargar())

    /** ¿Hay algo guardado? Lo usa la franja para decidir si se muestra. */
    fun hayAlgo(): Boolean = cargar().isNotEmpty()

    /**
     * Guarda una copia.
     *
     * Lo repetido no se duplica: se sube al principio y **conserva la estrella**
     * (si estaba fijado, sigue fijado). Devuelve true si el historial cambió.
     */
    fun anadir(texto: String): Boolean {
        if (preferencias == null) return false
        val limpio = texto.trim()
        if (limpio.isEmpty() || limpio.length > LARGO_MAXIMO) return false
        val lista = cargar().toMutableList()
        val anterior = lista.firstOrNull { it.texto == limpio }
        // Ya era la primera: no hay nada que reordenar.
        if (anterior != null && lista.first() === anterior) return false
        lista.removeAll { it.texto == limpio }
        lista.add(0, Entrada(limpio, anterior?.fijada ?: false, System.currentTimeMillis()))
        guardar(recortar(lista))
        return true
    }

    /** Fija o desfija una copia (la estrella del panel). */
    fun fijar(texto: String, fijada: Boolean) {
        val lista = cargar().toMutableList()
        val indice = lista.indexOfFirst { it.texto == texto }
        if (indice < 0) return
        lista[indice] = lista[indice].copy(fijada = fijada)
        guardar(recortar(lista))
    }

    /** Borra una copia concreta. */
    fun borrar(texto: String) {
        val lista = cargar().toMutableList()
        if (lista.removeAll { it.texto == texto }) guardar(recortar(lista))
    }

    /** Borra los recientes; los fijados se conservan (como en Gboard). */
    fun vaciarRecientes() {
        guardar(cargar().filter { it.fijada })
    }

    /** Borra TODO, fijados incluidos. */
    fun vaciarTodo() {
        guardar(emptyList())
    }

    // --- Almacenamiento ---------------------------------------------------

    /**
     * Recorta la lista a los límites y la deja ordenada para mostrar: fijados
     * primero (los más nuevos arriba) y después los recientes.
     */
    private fun recortar(lista: List<Entrada>): List<Entrada> {
        val fijadas = lista.filter { it.fijada }
            .sortedByDescending { it.cuando }
            .take(MAXIMO_FIJADOS)
        val recientes = lista.filter { !it.fijada }
            .sortedByDescending { it.cuando }
            .take(MAXIMO_RECIENTES)
        return fijadas + recientes
    }

    private fun cargar(): List<Entrada> {
        val crudo = preferencias?.getString(HISTORIAL, null) ?: return emptyList()
        return runCatching {
            val arreglo = JSONArray(crudo)
            (0 until arreglo.length()).mapNotNull { i ->
                val objeto = arreglo.optJSONObject(i) ?: return@mapNotNull null
                val texto = objeto.optString(TEXTO, "")
                if (texto.isBlank()) {
                    null
                } else {
                    Entrada(
                        texto = texto,
                        fijada = objeto.optBoolean(FIJADA, false),
                        cuando = objeto.optLong(CUANDO, 0L)
                    )
                }
            }
        }.getOrDefault(emptyList())
    }

    private fun guardar(lista: List<Entrada>) {
        val prefs = preferencias ?: return
        val arreglo = JSONArray()
        for (entrada in lista) {
            arreglo.put(
                JSONObject()
                    .put(TEXTO, entrada.texto)
                    .put(FIJADA, entrada.fijada)
                    .put(CUANDO, entrada.cuando)
            )
        }
        prefs.edit().putString(HISTORIAL, arreglo.toString()).apply()
    }

    companion object {
        private const val ARCHIVO = "dictado_portapapeles"
        private const val HISTORIAL = "historial"
        private const val TEXTO = "t"
        private const val FIJADA = "f"
        private const val CUANDO = "c"

        /** Tope por copia: lo mismo que la franja (2 000 caracteres). */
        const val LARGO_MAXIMO = 2000

        /** Cuántas copias se recuerdan sin fijar. */
        const val MAXIMO_RECIENTES = 40

        /** Tope de fijados (nadie va a fijar más, pero no puede crecer sin fin). */
        const val MAXIMO_FIJADOS = 100
    }
}
