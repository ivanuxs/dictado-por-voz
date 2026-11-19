package com.ivanuxs.skipkey

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Diccionario del teclado, con cuatro piezas:
 *
 *  1. La lista base de palabras españolas con su frecuencia (recurso `assets`),
 *     que se carga en segundo plano y luego se consulta desde memoria.
 *  2. Las palabras que aprende del usuario, con cuántas veces las ha escrito.
 *  3. Los bigramas aprendidos (qué palabra suele seguir a cuál), que dan la
 *     predicción de la siguiente palabra sin necesidad de ningún dato extra.
 *  4. Las correcciones aprendidas: qué palabra dictada sustituyó el usuario por
 *     cuál (por ejemplo, porque el motor no conocía un nombre propio). Sirven
 *     para ofrecerla la próxima vez y para arreglar el texto que llega dictado.
 *
 * Todo se queda en el celular mientras no haya sesión. Con cuenta, lo aprendido
 * se sincroniza con el perfil del puente (`ClientePerfil`): así el mismo usuario
 * encuentra sus palabras en otro celular o en la PC. Sin cuenta no sale nada.
 *
 * Cada usuario del celular tiene su propio perfil (`Ajustes.perfil`): los
 * archivos llevan el perfil en el nombre, así que dos personas que compartan un
 * celular no se mezclan las palabras ni las correcciones.
 *
 * Las consultas se hacen desde el hilo principal (hay que responder mientras se
 * teclea), así que se apoyan en dos vectores ya ordenados y en una búsqueda
 * binaria: nada de recorrer 40 000 palabras en cada tecla.
 */
class Diccionario(
    private val contexto: Context,
    private val perfil: String = Ajustes.PERFIL_POR_DEFECTO
) {

    /** Las palabras base ordenadas por frecuencia (de más a menos usadas). */
    private var porFrecuencia: Array<String> = emptyArray()

    /** Las mismas palabras en orden alfabético, con su frecuencia en paralelo. */
    private var alfabetico: Array<String> = emptyArray()
    private var frecuencias: IntArray = IntArray(0)

    /** Palabras del usuario (nombres, términos) y veces que las escribió. */
    private val propias = HashMap<String, Int>()

    /**
     * Cómo escribe el usuario cada palabra propia (`callby` -> `CallBy`).
     *
     * `propias` va en minúsculas porque todas las consultas comparan así, pero
     * guardar solo minúsculas perdía las mayúsculas de dentro: el usuario ponía
     * «CallBy» y el teclado devolvía «Callby», en la barra y al volver a
     * escribirla (2026-11-18). Aquí se apunta la forma tal como la puso él.
     */
    private val formas = HashMap<String, String>()

    /** palabra -> (palabra siguiente -> veces). */
    private val bigramas = HashMap<String, HashMap<String, Int>>()

    /** palabra dictada -> (palabra que el usuario prefirió -> veces). */
    private val sustituciones = HashMap<String, HashMap<String, Int>>()

    /**
     * Palabras que el usuario olvidó, con el instante en que las olvidó.
     *
     * Sin esto, el otro aparato (que aún las tiene) las devolvería en la
     * siguiente sincronización. No se borra la marca: es lo que se manda al
     * puente para que el olvido viaje.
     */
    private val olvidadas = HashMap<String, Long>()

    /** Correcciones olvidadas: "origen\tdestino" -> instante. */
    private val olvidadasCorrecciones = HashMap<String, Long>()

    private val candado = Any()
    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var guardado: Job? = null
    private var cargando = false

    /** Lista de las palabras propias ordenada por uso, para no reordenar en cada tecla. */
    private var ordenPropias: List<String> = emptyList()
    private var generacion = 0
    private var generacionCache = -1

    /** Se pone a true AL FINAL de la carga: hasta entonces no se responde nada. */
    @Volatile private var listo = false

    // --- Carga -----------------------------------------------------------

    /** Carga la lista base y lo aprendido. Solo la primera vez. */
    suspend fun cargar() {
        if (listo || cargando) return
        cargando = true
        try {
            withContext(Dispatchers.IO) {
                cargarAprendido()
                val palabras = ArrayList<String>(52000)
                val cuentas = ArrayList<Int>(52000)
                contexto.assets.open(ARCHIVO_BASE).bufferedReader().useLines { lineas ->
                    for (linea in lineas) {
                        val corte = linea.indexOf('\t')
                        if (corte <= 0) continue
                        palabras.add(linea.substring(0, corte))
                        cuentas.add(linea.substring(corte + 1).toIntOrNull() ?: 1)
                    }
                }
                val indices = palabras.indices.toMutableList()
                val porUso = indices.sortedByDescending { cuentas[it] }
                val porLetra = indices.sortedBy { palabras[it] }
                porFrecuencia = Array(porUso.size) { palabras[porUso[it]] }
                alfabetico = Array(porLetra.size) { palabras[porLetra[it]] }
                frecuencias = IntArray(porLetra.size) { cuentas[porLetra[it]] }
                listo = true
            }
        } finally {
            cargando = false
        }
    }

    /** Lo aprendido antes (palabras del usuario, bigramas y correcciones). */
    private fun cargarAprendido() {
        migrarArchivosViejos()
        runCatching {
            archivo(ARCHIVO_PROPIAS).takeIf { it.exists() }?.forEachLine { linea ->
                // Formato: «clave \t veces [\t forma]». Las líneas viejas solo
                // traen dos columnas y se quedan como estaban.
                val partes = linea.split('\t')
                if (partes.size < 2) return@forEachLine
                val veces = partes[1].toIntOrNull() ?: return@forEachLine
                if (veces <= 0) return@forEachLine
                propias[partes[0]] = veces
                if (partes.size >= 3 && partes[2].isNotBlank()) formas[partes[0]] = partes[2]
            }
            archivo(ARCHIVO_BIGRAMAS).takeIf { it.exists() }?.forEachLine { linea ->
                val corte = linea.indexOf('\t')
                if (corte <= 0) return@forEachLine
                val destinos = HashMap<String, Int>()
                for (par in linea.substring(corte + 1).split(',')) {
                    val partes = par.split(':')
                    if (partes.size == 2) {
                        partes[1].toIntOrNull()?.let { if (it > 0) destinos[partes[0]] = it }
                    }
                }
                if (destinos.isNotEmpty()) bigramas[linea.substring(0, corte)] = destinos
            }
            archivo(ARCHIVO_SUSTITUCIONES).takeIf { it.exists() }?.forEachLine { linea ->
                val corte = linea.indexOf('\t')
                if (corte <= 0) return@forEachLine
                val destinos = HashMap<String, Int>()
                for (par in linea.substring(corte + 1).split(',')) {
                    val partes = par.split(':')
                    if (partes.size == 2) {
                        partes[1].toIntOrNull()?.let { if (it > 0) destinos[partes[0]] = it }
                    }
                }
                if (destinos.isNotEmpty()) sustituciones[linea.substring(0, corte)] = destinos
            }
            archivo(ARCHIVO_OLVIDADAS).takeIf { it.exists() }?.forEachLine { linea ->
                val partes = linea.split('\t')
                if (partes.size >= 2) {
                    partes[1].toLongOrNull()?.let { olvidadas[partes[0]] = it }
                }
            }
            archivo(ARCHIVO_OLVIDADAS_CORRECCIONES).takeIf { it.exists() }?.forEachLine { linea ->
                val partes = linea.split('\t')
                if (partes.size >= 2) {
                    partes[1].toLongOrNull()?.let { olvidadasCorrecciones[partes[0]] = it }
                }
            }
        }
        generacion++
    }

    /**
     * Los archivos de antes no llevaban perfil: el primero que abre el teclado
     * después de la actualización se los queda (perfil por defecto) en vez de
     * empezar con el diccionario vacío.
     */
    private fun migrarArchivosViejos() {
        if (perfil != Ajustes.PERFIL_POR_DEFECTO) return
        val viejos = listOf(
            "dictado_usuario.txt" to archivo(ARCHIVO_PROPIAS),
            "dictado_bigramas.txt" to archivo(ARCHIVO_BIGRAMAS)
        )
        for ((nombreViejo, nuevo) in viejos) {
            val origen = File(contexto.filesDir, nombreViejo)
            if (origen.exists() && !nuevo.exists()) origen.renameTo(nuevo)
        }
    }

    private fun archivo(nombre: String) = File(contexto.filesDir, "dictado_${perfil}_$nombre")

    // --- Consultas -------------------------------------------------------

    /** ¿La palabra existe (en la lista base o como palabra aprendida)? */
    fun esConocida(palabra: String): Boolean {
        if (!listo) return false
        val texto = palabra.lowercase()
        if (frecuenciaDe(texto) > 0) return true
        synchronized(candado) { return (propias[texto] ?: 0) >= VECES_PARA_CONOCIDA }
    }

    /** ¿La guardó el usuario alguna vez? */
    fun esPropia(palabra: String): Boolean =
        synchronized(candado) { propias.containsKey(palabra.lowercase()) }

    /**
     * Si la palabra ya está cubierta por el diccionario, y de qué forma.
     *
     * Lo usa el botón «＋ / ✓» de la barra: no tiene sentido ofrecer guardar
     * una palabra que ya viene en la lista base (49 600 palabras).
     */
    enum class EstadoPalabra { EN_BASE, PROPIA, DESCONOCIDA }

    fun estadoPalabra(palabra: String): EstadoPalabra {
        if (!listo) return EstadoPalabra.DESCONOCIDA
        val texto = palabra.lowercase().trim()
        if (texto.isEmpty()) return EstadoPalabra.DESCONOCIDA
        if (frecuenciaDe(texto) > 0) return EstadoPalabra.EN_BASE
        return if (synchronized(candado) { propias.containsKey(texto) }) {
            EstadoPalabra.PROPIA
        } else {
            EstadoPalabra.DESCONOCIDA
        }
    }

    /**
     * Sugerencias para la palabra que se está escribiendo.
     *
     * La primera casilla es la corrección (si la hay) o lo que se ha escrito, de
     * modo que siempre se pueda conservar tal cual.
     */
    fun sugerencias(
        prefijo: String,
        anterior: String?,
        capitalizado: Boolean,
        limite: Int
    ): List<String> {
        if (!listo) return emptyList()
        val texto = prefijo.lowercase()
        if (texto.isEmpty()) return predicciones(anterior, limite).map { conMayusculas(it, null, capitalizado) }

        val salida = LinkedHashSet<String>()
        val encontradas = coincidencias(texto, limite * 4, anterior)
        // La corrección que el usuario enseñó (chifa -> chifá): en cuanto se
        // escribe la palabra entera, se ofrece como primera casilla.
        correccionPara(texto, 1)?.let { salida.add(it) }
        // La tilde que falta (avion -> avión): se ofrece aunque lo escrito exista.
        varianteConTilde(texto, FACTOR_TILDE_SUGERENCIA)?.let { salida.add(it) }
        // Si nada empieza así, es casi seguro una errata: se busca una corrección.
        if (encontradas.isEmpty() && texto.length >= LARGO_MINIMO_CORRECCION) {
            mejorEdicion(texto)?.let { salida.add(it) }
        }
        // «Conservar lo escrito» solo tiene sentido a mitad de palabra: con una o
        // dos letras la casilla se aprovecha mejor con palabras de verdad. Se
        // ofrece tal como se escribió, con sus mayúsculas (CallB).
        if (texto.length >= LARGO_MINIMO_CORRECCION) salida.add(prefijo.ifEmpty { texto })
        salida.addAll(encontradas)
        return salida.take(limite).map { conMayusculas(it, prefijo, capitalizado) }
    }

    /**
     * Corrección de una palabra que se acaba de terminar (al pulsar espacio o
     * puntuación). Devuelve null si no hay nada mejor que lo escrito.
     */
    fun corregirPalabra(escrita: String): String? {
        if (!listo) return null
        val texto = escrita.lowercase()
        if (texto.length < LARGO_MINIMO_CORRECCION) return null
        // Lo que el usuario ya corrigió a mano se respeta por encima de todo:
        // si enseñó que «chifa» debe ser «chifá», se aplica sin discutir. Con
        // UNA corrección basta: antes hacían falta dos y el dictado siguiente
        // volvía a salir mal, como si no se hubiera aprendido nada.
        // La pareja está en minúsculas; se devuelve con su forma (CallBy).
        correccionPara(texto)?.let { if (it != texto) return forma(it) }
        // La tilde que falta se corrige aunque lo escrito exista como palabra (en
        // los subtítulos aparecen formas sin tilde), pero solo si la versión con
        // tilde se usa MUCHÍSIMO más: avion/avión sí; esta/está no, porque las dos
        // son correctas y están parejas en frecuencia.
        varianteConTilde(texto, FACTOR_TILDE_CORRECCION)?.let { return it }
        if (esConocida(texto)) return errataRara(texto)
        // La errata ya arreglada puede llevar además tilde: imformacion -> información.
        val arreglada = mejorEdicion(texto) ?: return null
        return varianteConTilde(arreglada, FACTOR_TILDE_DE_ARRITADA) ?: arreglada
    }

    /** Qué palabra suele seguir a `anterior`. */
    fun predicciones(anterior: String?, limite: Int): List<String> {
        if (!listo) return emptyList()
        val clave = anterior?.lowercase()
        synchronized(candado) {
            if (!clave.isNullOrEmpty()) {
                val destinos = bigramas[clave]
                if (!destinos.isNullOrEmpty()) {
                    return destinos.entries.sortedByDescending { it.value }
                        .take(limite).map { forma(it.key) }
                }
            }
            // Sin contexto: lo que más escribe esta persona.
            return propias.entries.asSequence()
                .filter { it.value >= VECES_PARA_PREDECIR }
                .sortedByDescending { it.value }
                .take(limite).map { forma(it.key) }.toList()
        }
    }

    /** Anota que se escribió una palabra (y detrás de cuál). */
    fun registrar(palabra: String, anterior: String?) {
        val forma = palabra.trim()
        val texto = forma.lowercase()
        if (texto.length < 2 || texto.any { !it.isLetter() }) return
        // Solo se guarda la forma cuando tiene mayúsculas DE DENTRO (CallBy,
        // iPhone, NASA). La del principio la pone la frase al escribir, y
        // guardarla haría que «Casa» saliera siempre con mayúscula.
        val formaPropia = forma.drop(1).any { it.isUpperCase() && it.isLetter() }
        synchronized(candado) {
            propias[texto] = (propias[texto] ?: 0) + 1
            if (formaPropia) formas[texto] = forma
            generacion++
            if (propias.size > MAX_PROPIAS) podarPropias()
            val previa = anterior?.lowercase()
            if (!previa.isNullOrEmpty() && previa != texto) {
                val destinos = bigramas.getOrPut(previa) { HashMap() }
                destinos[texto] = (destinos[texto] ?: 0) + 1
                if (destinos.size > MAX_DESTINOS) {
                    val mejores = destinos.entries.sortedByDescending { it.value }
                        .take(MAX_DESTINOS / 2)
                    destinos.clear()
                    for ((clave, valor) in mejores) destinos[clave] = valor
                }
                if (bigramas.size > MAX_ORIGENES) podarBigramas()
            }
        }
        programarGuardado()
    }

    /** Guarda una palabra desconocida como propia (botón ＋ de la barra). */
    fun aprender(palabra: String) {
        val forma = palabra.trim()
        val texto = forma.lowercase()
        if (texto.isEmpty()) return
        synchronized(candado) {
            propias[texto] = maxOf(propias[texto] ?: 0, VECES_PARA_CONOCIDA)
            if (forma.drop(1).any { it.isUpperCase() && it.isLetter() }) formas[texto] = forma
            // Aprenderla otra vez deshace el olvido: si no se quitara la marca,
            // la siguiente sincronización la volvería a olvidar.
            olvidadas.remove(texto)
            generacion++
        }
        programarGuardado()
    }

    /** Olvida una palabra propia. Devuelve true si estaba guardada. */
    fun olvidar(palabra: String): Boolean {
        val texto = palabra.lowercase().trim()
        val quitada = synchronized(candado) {
            generacion++
            formas.remove(texto)
            if (propias.remove(texto) != null) {
                olvidadas[texto] = System.currentTimeMillis()
                true
            } else {
                false
            }
        }
        if (quitada) programarGuardado()
        return quitada
    }

    // --- Lo que se puede ver y borrar -------------------------------------

    /**
     * Las palabras que el usuario guardó y que NO están en la lista base.
     *
     * Se filtran las de la lista base (49 600) porque la pantalla «Mis
     * palabras» quedaría inservible: lo que interesa es lo que añadió él.
     * Se devuelven de la más usada a la menos usada.
     */
    fun palabrasAprendidas(limite: Int = 500): List<Pair<String, Int>> =
        synchronized(candado) {
            propias.entries.asSequence()
                .filter { frecuenciaDe(it.key) == 0 }
                .sortedByDescending { it.value }
                .take(limite)
                .map { forma(it.key) to it.value }
                .toList()
        }

    /** Las correcciones del dictado: lo que dijo el motor, lo que querías y cuántas veces. */
    fun correccionesAprendidas(limite: Int = 500): List<Triple<String, String, Int>> =
        synchronized(candado) {
            sustituciones.entries.asSequence()
                .flatMap { entrada ->
                    entrada.value.entries.asSequence()
                        .map { Triple(forma(entrada.key), forma(it.key), it.value) }
                }
                .sortedByDescending { it.third }
                .take(limite)
                .toList()
        }

    // --- Correcciones aprendidas (dictado) -------------------------------

    /**
     * Anota que el usuario cambió una palabra dictada por otra.
     *
     * Se guarda en minúsculas y sin puntuación: la mayúscula que trajera el
     * texto se vuelve a poner al aplicar la corrección.
     */
    fun registrarCorreccion(antes: String, despues: String) {
        val origen = antes.lowercase().trim()
        val destino = despues.lowercase().trim()
        if (origen.length < 2 || destino.length < 2) return
        if (origen == destino) return
        if (origen.length > LARGO_MAXIMO_PALABRA || destino.length > LARGO_MAXIMO_PALABRA) return
        if (origen.any { !it.isLetter() } || destino.any { !it.isLetter() }) return
        synchronized(candado) {
            val destinos = sustituciones.getOrPut(origen) { HashMap() }
            destinos[destino] = (destinos[destino] ?: 0) + 1
            if (destinos.size > MAX_DESTINOS_SUSTITUCION) {
                val mejores = destinos.entries.sortedByDescending { it.value }
                    .take(MAX_DESTINOS_SUSTITUCION)
                destinos.clear()
                for ((clave, valor) in mejores) destinos[clave] = valor
            }
            if (sustituciones.size > MAX_SUSTITUCIONES) podarSustituciones()
            olvidadasCorrecciones.remove("$origen\t$destino")
            generacion++
        }
        programarGuardado()
    }

    /**
     * Qué palabra prefiere el usuario en lugar de esta, si lo ha enseñado.
     *
     * `minimo` es el número de veces que lo tiene que haber corregido; con el
     * valor por defecto basta una.
     */
    fun correccionPara(palabra: String, minimo: Int = VECES_PARA_SUSTITUIR): String? =
        synchronized(candado) {
            val destinos = sustituciones[palabra.lowercase().trim()]
            destinos?.entries?.filter { it.value >= minimo }
                ?.maxByOrNull { it.value }?.key
        }

    /** El usuario deshizo esta corrección: deja de aplicarse. */
    fun olvidarCorreccion(antes: String, despues: String) {
        val origen = antes.lowercase().trim()
        val destino = despues.lowercase().trim()
        val quitada = synchronized(candado) {
            val destinos = sustituciones[origen] ?: return@synchronized false
            val habia = destinos.remove(destino) != null
            if (destinos.isEmpty()) sustituciones.remove(origen)
            if (habia) {
                // Se apunta el olvido para que no vuelva desde otro aparato.
                olvidadasCorrecciones["$origen\t$destino"] = System.currentTimeMillis()
                generacion++
            }
            habia
        }
        if (quitada) programarGuardado()
    }

    /**
     * Aplica las correcciones aprendidas al texto que llega del dictado.
     *
     * Solo toca palabras que el usuario corrigió a mano, y conserva la
     * mayúscula que trajera el original (nombre propio, inicio de frase). Así el
     * dictado sale como el usuario lo dejó la última vez, sin tocar el resto.
     */
    fun corregirDictado(texto: String): String {
        if (!listo || texto.isEmpty()) return texto
        if (synchronized(candado) { sustituciones.isEmpty() }) return texto
        val salida = StringBuilder(texto.length)
        var i = 0
        while (i < texto.length) {
            if (!texto[i].isLetter()) {
                salida.append(texto[i])
                i++
                continue
            }
            var fin = i
            while (fin < texto.length && texto[fin].isLetter()) fin++
            salida.append(corregirPalabraDictada(texto.substring(i, fin)))
            i = fin
        }
        return salida.toString()
    }

    /** Una palabra del dictado, con la corrección aprendida y su mayúscula. */
    private fun corregirPalabraDictada(original: String): String {
        val aprendida = correccionPara(original) ?: return original
        // Con la forma del usuario: las mayúsculas de dentro (CallBy) son suyas.
        val nueva = forma(aprendida)
        if (nueva.equals(original, ignoreCase = true)) return original
        return when {
            original.length > 1 && original.all { it.isUpperCase() } -> nueva.uppercase()
            original.first().isUpperCase() -> nueva.replaceFirstChar { it.uppercase() }
            else -> nueva
        }
    }

    /** Borra todo lo aprendido en este perfil (palabras, bigramas y correcciones). */
    suspend fun borrarAprendido() {
        synchronized(candado) {
            propias.clear()
            formas.clear()
            bigramas.clear()
            sustituciones.clear()
            olvidadas.clear()
            olvidadasCorrecciones.clear()
            generacion++
        }
        volcar()
    }

    // --- Sincronización con la cuenta ------------------------------------

    /**
     * Lo aprendido, en el formato que entiende el puente.
     *
     * La fecha de cada palabra es la del ARCHIVO que la guarda: es lo más
     * cercano a «cuándo la aprendí» sin meter una fecha por palabra en el
     * archivo. El olvido sí lleva su instante exacto, que es el único que
     * necesita ganar o perder contra el otro aparato.
     */
    fun exportarPalabras(): JSONArray {
        val momento = iso(archivo(ARCHIVO_PROPIAS).lastModified().takeIf { it > 0 })
        val lista = JSONArray()
        synchronized(candado) {
            for ((palabra, veces) in propias) {
                lista.put(
                    JSONObject()
                        .put("palabra", palabra)
                        .put("veces", veces)
                        .put("conocida", veces >= VECES_PARA_CONOCIDA)
                        .put("actualizado", momento)
                )
            }
            for ((palabra, millis) in olvidadas) {
                if (propias.containsKey(palabra)) continue
                lista.put(
                    JSONObject()
                        .put("palabra", palabra)
                        .put("veces", 1)
                        .put("olvidada", true)
                        .put("actualizado", iso(millis))
                )
            }
        }
        return lista
    }

    /** Las correcciones aprendidas, en el formato del puente. */
    fun exportarSustituciones(): JSONArray {
        val momento = iso(archivo(ARCHIVO_SUSTITUCIONES).lastModified().takeIf { it > 0 })
        val lista = JSONArray()
        synchronized(candado) {
            for ((origen, destinos) in sustituciones) {
                val elegido = destinos.maxByOrNull { it.value } ?: continue
                lista.put(
                    JSONObject()
                        .put("antes", origen)
                        .put("despues", elegido.key)
                        .put("veces", elegido.value)
                        .put("actualizado", momento)
                )
            }
            for ((clave, millis) in olvidadasCorrecciones) {
                val partes = clave.split('\t')
                if (partes.size < 2) continue
                // Si la pareja sigue viva es porque se volvió a aprender: ahí
                // manda lo que hay en `sustituciones`, no el olvido viejo.
                if (sustituciones[partes[0]]?.containsKey(partes[1]) == true) continue
                lista.put(
                    JSONObject()
                        .put("antes", partes[0])
                        .put("despues", partes[1])
                        .put("veces", 1)
                        .put("olvidada", true)
                        .put("actualizado", iso(millis))
                )
            }
        }
        return lista
    }

    /**
     * Aplica lo que devuelve el puente, con las mismas reglas que él.
     *
     * Devuelve true si algo cambió, para no reescribir archivos de balde.
     */
    fun aplicarAprendido(palabras: JSONArray?, correcciones: JSONArray?): Boolean {
        var hubo = false
        synchronized(candado) {
            palabras?.let { lista ->
                for (i in 0 until lista.length()) {
                    val fila = lista.optJSONObject(i) ?: continue
                    val palabra = fila.optString("palabra").lowercase().trim()
                    if (palabra.isEmpty()) continue
                    val instante = milis(fila.optString("actualizado"))
                    val olvidoPrevio = olvidadas[palabra] ?: 0L
                    if (fila.optBoolean("olvidada")) {
                        if (instante >= olvidoPrevio) {
                            olvidadas[palabra] = instante
                            formas.remove(palabra)
                            if (propias.remove(palabra) != null) hubo = true
                        }
                        continue
                    }
                    // Una marca de olvido más reciente gana: la palabra no revive.
                    if (instante < olvidoPrevio) continue
                    if (olvidadas.remove(palabra) != null) hubo = true
                    val veces = maxOf(fila.optInt("veces", 1), 1)
                    if ((propias[palabra] ?: 0) < veces) {
                        propias[palabra] = veces
                        hubo = true
                    }
                }
            }
            correcciones?.let { lista ->
                for (i in 0 until lista.length()) {
                    val fila = lista.optJSONObject(i) ?: continue
                    val antes = fila.optString("antes").lowercase().trim()
                    val despues = fila.optString("despues").lowercase().trim()
                    if (antes.isEmpty() || despues.isEmpty()) continue
                    val instante = milis(fila.optString("actualizado"))
                    val clave = "$antes\t$despues"
                    val olvidoPrevio = olvidadasCorrecciones[clave] ?: 0L
                    if (fila.optBoolean("olvidada")) {
                        if (instante >= olvidoPrevio) {
                            olvidadasCorrecciones[clave] = instante
                            val destinos = sustituciones[antes]
                            if (destinos?.remove(despues) != null) hubo = true
                            if (destinos != null && destinos.isEmpty()) sustituciones.remove(antes)
                        }
                        continue
                    }
                    if (instante < olvidoPrevio) continue
                    if (olvidadasCorrecciones.remove(clave) != null) hubo = true
                    val veces = maxOf(fila.optInt("veces", 1), 1)
                    val destinos = sustituciones.getOrPut(antes) { HashMap() }
                    if ((destinos[despues] ?: 0) < veces) {
                        destinos[despues] = veces
                        hubo = true
                    }
                }
            }
            if (hubo) generacion++
        }
        if (hubo) programarGuardado()
        return hubo
    }

    /** Instante ISO-8601 en UTC, que es lo que espera el puente. */
    private fun iso(millis: Long?): String =
        java.time.Instant.ofEpochMilli(millis ?: System.currentTimeMillis()).toString()

    /** Texto ISO a milisegundos; 0 si no se entiende. */
    private fun milis(texto: String): Long = runCatching {
        java.time.OffsetDateTime.parse(texto.trim().replace("Z", "+00:00"))
            .toInstant().toEpochMilli()
    }.getOrDefault(0L)

    /**
     * Abandona esta instancia sin guardar nada más.
     *
     * Se usa al cambiar de perfil o cuando otra pantalla borró el diccionario:
     * cancela el guardado que pudiera estar esperando, para que no devuelva a
     * disco lo que ya no toca.
     */
    fun descartar() {
        guardado?.cancel()
        guardado = null
        alcance.cancel()
    }

    // --- Búsquedas internas ----------------------------------------------

    /** Frecuencia de una palabra en la lista base (0 si no está). */
    private fun frecuenciaDe(palabra: String): Int {
        var bajo = 0
        var alto = alfabetico.size - 1
        while (bajo <= alto) {
            val medio = (bajo + alto) ushr 1
            val orden = alfabetico[medio].compareTo(palabra)
            when {
                orden == 0 -> return frecuencias[medio]
                orden < 0 -> bajo = medio + 1
                else -> alto = medio - 1
            }
        }
        return 0
    }

    /**
     * Palabras que empiezan por el texto, primero las del usuario y por uso.
     *
     * Cuando se sabe la palabra anterior, se adelantan las que suelen ir detrás
     * de ella según lo aprendido (los bigramas): así el mismo prefijo ofrece
     * cosas distintas según la frase. El orden por frecuencia se mantiene entre
     * las que no tienen relación con la anterior (la ordenación es estable).
     */
    private fun coincidencias(texto: String, limite: Int, anterior: String?): List<String> {
        val salida = ArrayList<String>(limite + 1)
        val vistas = HashSet<String>()
        for (palabra in listasPropias()) {
            // Las propias vienen con la forma del usuario (CallBy): se compara en
            // minúsculas, que es como llega el prefijo.
            if (palabra.length > texto.length && palabra.lowercase().startsWith(texto) &&
                vistas.add(palabra)
            ) {
                salida.add(palabra)
                if (salida.size >= limite) return salida
            }
        }
        for (palabra in porFrecuencia) {
            if (palabra.startsWith(texto) && vistas.add(palabra)) {
                salida.add(palabra)
                if (salida.size >= limite) break
            }
        }
        if (salida.size < 2 || anterior.isNullOrEmpty()) return salida
        val destinos = synchronized(candado) { bigramas[anterior.lowercase()] }
        if (destinos.isNullOrEmpty()) return salida
        return salida.sortedByDescending { destinos[it.lowercase()] ?: 0 }
    }

    /** La forma con la que el usuario escribe una palabra (o la clave en minúsculas). */
    private fun forma(clave: String): String = synchronized(candado) { formas[clave] ?: clave }

    /** Las palabras propias ordenadas por uso (se rehace solo si cambiaron). */
    private fun listasPropias(): List<String> = synchronized(candado) {
        if (generacionCache != generacion) {
            ordenPropias = propias.entries.sortedByDescending { it.value }.map { forma(it.key) }
            generacionCache = generacion
        }
        ordenPropias
    }

    /**
     * Cambia una letra por su versión con tilde (avion -> avión, solo por «vino
     * una vez más que lo escrito»). Es barato: unas pocas palabras por letra.
     */
    private fun varianteConTilde(palabra: String, factor: Int): String? {
        val base = frecuenciaDe(palabra)
        var mejor: String? = null
        var mejorCuenta = 0
        for (i in palabra.indices) {
            for (letra in ACENTOS[palabra[i]] ?: continue) {
                val candidata = palabra.substring(0, i) + letra + palabra.substring(i + 1)
                val cuenta = frecuenciaDe(candidata)
                if (cuenta > mejorCuenta) {
                    mejorCuenta = cuenta
                    mejor = candidata
                }
            }
        }
        if (mejor == null || mejorCuenta < FRECUENCIA_MINIMA) return null
        return if (mejorCuenta >= maxOf(base + 1, base * factor)) mejor else null
    }

    /**
     * La mejor candidata a una sola edición de distancia (letra cambiada, de
     * más, de menos o dos vecinas cambiadas de sitio), eligiendo la más frecuente.
     */
    private fun mejorEdicion(palabra: String): String? {
        var mejor: String? = null
        var mejorCuenta = 0
        for (candidata in ediciones(palabra)) {
            val cuenta = frecuenciaDe(candidata)
            if (cuenta > mejorCuenta) {
                mejorCuenta = cuenta
                mejor = candidata
            }
        }
        return if (mejorCuenta >= FRECUENCIA_MINIMA) mejor else null
    }

    /** Todas las palabras a una edición de distancia. */
    private fun ediciones(palabra: String): Sequence<String> = sequence {
        val largo = palabra.length
        for (i in 0 until largo) {
            yield(palabra.substring(0, i) + palabra.substring(i + 1))
        }
        for (i in 0 until largo - 1) {
            yield(
                palabra.substring(0, i) + palabra[i + 1] + palabra[i] + palabra.substring(i + 2)
            )
        }
        for (i in 0 until largo) {
            for (letra in ALFABETO) {
                if (letra != palabra[i]) {
                    yield(palabra.substring(0, i) + letra + palabra.substring(i + 1))
                }
            }
        }
        for (i in 0..largo) {
            for (letra in ALFABETO) {
                yield(palabra.substring(0, i) + letra + palabra.substring(i))
            }
        }
    }

    /**
     * Corrige una errata que la propia lista base trae escrita mal (los subtítulos
     * tienen «manana», «hai»…). Solo se toca lo de verdad raro y que no sea del
     * usuario: así no se estropea «aun» (36908), «mas» (103445) ni «tubo» (6954),
     * que son palabras correctas con un vecino mucho más frecuente al lado.
     */
    private fun errataRara(palabra: String): String? {
        val veces = frecuenciaDe(palabra)
        if (veces == 0 || veces >= FRECUENCIA_RARA) return null
        if (esPropia(palabra)) return null
        val mejor = mejorEdicion(palabra) ?: return null
        return if (frecuenciaDe(mejor) >= veces * FACTOR_ERRATA_RARA) mejor else null
    }

    /** Copia el uso de mayúsculas de lo escrito a la palabra sugerida. */
    private fun conMayusculas(nueva: String, escrita: String?, capitalizado: Boolean): String {
        val enMayusculas = escrita != null && escrita.length > 1 &&
            escrita.all { !it.isLetter() || it.isUpperCase() }
        return when {
            enMayusculas -> nueva.uppercase()
            capitalizado || escrita?.firstOrNull()?.isUpperCase() == true ->
                nueva.replaceFirstChar { it.uppercase() }
            else -> nueva
        }
    }

    // --- Poda y guardado -------------------------------------------------

    private fun podarPropias() {
        val conservar = propias.entries.sortedByDescending { it.value }.take(MAX_PROPIAS * 3 / 4)
        propias.clear()
        for ((clave, valor) in conservar) propias[clave] = valor
        // La tabla de formas no puede crecer con palabras que ya no están.
        formas.keys.retainAll(propias.keys)
        generacion++
    }

    private fun podarBigramas() {
        val conservar = bigramas.entries.sortedByDescending { entrada ->
            entrada.value.values.sum()
        }.take(MAX_ORIGENES * 3 / 4)
        bigramas.clear()
        for ((clave, valor) in conservar) bigramas[clave] = valor
    }

    /** Deja las correcciones más usadas (las demás se pierden). */
    private fun podarSustituciones() {
        val conservar = sustituciones.entries.sortedByDescending { entrada ->
            entrada.value.values.sum()
        }.take(MAX_SUSTITUCIONES * 3 / 4)
        sustituciones.clear()
        for ((clave, valor) in conservar) sustituciones[clave] = valor
    }

    /** Guarda con un pequeño retardo, para no escribir en cada tecla. */
    private fun programarGuardado() {
        if (guardado?.isActive == true) return
        guardado = alcance.launch {
            delay(RETARDO_GUARDADO)
            volcar()
        }
    }

    /** Escribe a disco lo aprendido (el resumen se arma en el hilo principal). */
    suspend fun volcar() {
        val textos = withContext(Dispatchers.Main.immediate) {
            synchronized(candado) {
                // La tercera columna es la forma tal como la escribe el usuario
                // (CallBy). Solo se escribe cuando aporta algo.
                val palabras = propias.entries.joinToString("\n") { entrada ->
                    val forma = formas[entrada.key]
                    if (forma == null) {
                        "${entrada.key}\t${entrada.value}"
                    } else {
                        "${entrada.key}\t${entrada.value}\t$forma"
                    }
                }
                val pares = bigramas.entries.joinToString("\n") { (origen, destinos) ->
                    origen + "\t" + destinos.entries.joinToString(",") { "${it.key}:${it.value}" }
                }
                val correcciones = sustituciones.entries.joinToString("\n") { (origen, destinos) ->
                    origen + "\t" + destinos.entries.joinToString(",") { "${it.key}:${it.value}" }
                }
                listOf(
                    ARCHIVO_PROPIAS to palabras,
                    ARCHIVO_BIGRAMAS to pares,
                    ARCHIVO_SUSTITUCIONES to correcciones,
                    ARCHIVO_OLVIDADAS to
                        olvidadas.entries.joinToString("\n") { "${it.key}\t${it.value}" },
                    ARCHIVO_OLVIDADAS_CORRECCIONES to
                        olvidadasCorrecciones.entries.joinToString("\n") { "${it.key}\t${it.value}" }
                )
            }
        }
        withContext(Dispatchers.IO) {
            runCatching {
                for ((nombre, texto) in textos) archivo(nombre).writeText(texto)
            }
        }
    }

    companion object {
        private const val ARCHIVO_BASE = "diccionario_es.txt"
        private const val ARCHIVO_PROPIAS = "usuario.txt"
        private const val ARCHIVO_BIGRAMAS = "bigramas.txt"
        private const val ARCHIVO_SUSTITUCIONES = "sustituciones.txt"
        private const val ARCHIVO_OLVIDADAS = "olvidadas.txt"
        private const val ARCHIVO_OLVIDADAS_CORRECCIONES = "olvidadas_correcciones.txt"

        /**
         * Con UNA vez que la escribas, la palabra es tuya y ya no se corrige.
         *
         * Antes eran DOS, y eso hacía que la segunda vez que tecleabas una
         * palabra nueva todavía saliera la corrección automática: había que
         * borrarla a mano. Ahora se aprende desde la primera escritura.
         */
        private const val VECES_PARA_CONOCIDA = 1

        /** Para las predicciones se pide algo más: verla una sola vez es ruido. */
        private const val VECES_PARA_PREDECIR = 2

        private const val LARGO_MINIMO_CORRECCION = 3

        /** Por debajo de esto, la candidata es demasiado rara para ofrecerla. */
        private const val FRECUENCIA_MINIMA = 400

        /** La tilde se sugiere si la versión con tilde es claramente más usada. */
        private const val FACTOR_TILDE_SUGERENCIA = 3

        /** Y se corrige sola solo si lo es muchísimo (esta/está no se tocan). */
        private const val FACTOR_TILDE_CORRECCION = 8

        /** Por debajo de esto, la palabra se considera rara y se puede corregir. */
        private const val FRECUENCIA_RARA = 3000

        /** Cuántas veces más usada tiene que ser la alternativa para sustituirla. */
        private const val FACTOR_ERRATA_RARA = 20

        /** Tras arreglar una errata, la tilde se aplica con esta holgura. */
        private const val FACTOR_TILDE_DE_ARRITADA = 2

        private const val MAX_PROPIAS = 4000
        private const val MAX_DESTINOS = 6
        private const val MAX_ORIGENES = 4000
        private const val RETARDO_GUARDADO = 1500L

        /**
         * Con UNA corrección ya se aplica sola.
         *
         * Estaba en 2, y en la práctica no servía: el usuario corregía una
         * palabra dictada una vez y el dictado siguiente volvía a escribirla
         * mal, sin ninguna señal de que la corrección se hubiera aprendido. Si
         * una corrección puntual molesta, la casilla «↩ volver» de la barra
         * deshace la corrección y olvida la pareja.
         */
        private const val VECES_PARA_SUSTITUIR = 1

        private const val MAX_SUSTITUCIONES = 2000
        private const val MAX_DESTINOS_SUSTITUCION = 3
        private const val LARGO_MAXIMO_PALABRA = 40

        /** Letras del teclado español, con tildes y diéresis, para las erratas. */
        private const val ALFABETO = "abcdefghijklmnopqrstuvwxyzáéíóúüñ"

        private val ACENTOS: Map<Char, Set<Char>> = mapOf(
            'a' to setOf('á'),
            'e' to setOf('é'),
            'i' to setOf('í'),
            'o' to setOf('ó'),
            'u' to setOf('ú', 'ü')
        )
    }
}
