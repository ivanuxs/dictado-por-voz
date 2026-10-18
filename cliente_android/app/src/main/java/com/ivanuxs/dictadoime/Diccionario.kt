package com.ivanuxs.dictadoime

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Diccionario del teclado, con tres piezas:
 *
 *  1. La lista base de palabras españolas con su frecuencia (recurso `assets`),
 *     que se carga en segundo plano y luego se consulta desde memoria.
 *  2. Las palabras que aprende del usuario, con cuántas veces las ha escrito.
 *  3. Los bigramas aprendidos (qué palabra suele seguir a cuál), que dan la
 *     predicción de la siguiente palabra sin necesidad de ningún dato extra.
 *
 * Todo se queda en el celular: no hay red, ni permisos, ni servicios externos.
 *
 * Las consultas se hacen desde el hilo principal (hay que responder mientras se
 * teclea), así que se apoyan en dos vectores ya ordenados y en una búsqueda
 * binaria: nada de recorrer 40 000 palabras en cada tecla.
 */
class Diccionario(private val contexto: Context) {

    /** Las palabras base ordenadas por frecuencia (de más a menos usadas). */
    private var porFrecuencia: Array<String> = emptyArray()

    /** Las mismas palabras en orden alfabético, con su frecuencia en paralelo. */
    private var alfabetico: Array<String> = emptyArray()
    private var frecuencias: IntArray = IntArray(0)

    /** Palabras del usuario (nombres, términos) y veces que las escribió. */
    private val propias = HashMap<String, Int>()

    /** palabra -> (palabra siguiente -> veces). */
    private val bigramas = HashMap<String, HashMap<String, Int>>()

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

    /** Lo aprendido antes (palabras del usuario y bigramas). */
    private fun cargarAprendido() {
        runCatching {
            archivo(ARCHIVO_PROPIAS).takeIf { it.exists() }?.forEachLine { linea ->
                val corte = linea.lastIndexOf('\t')
                if (corte <= 0) return@forEachLine
                val veces = linea.substring(corte + 1).toIntOrNull() ?: 1
                if (veces > 0) propias[linea.substring(0, corte)] = veces
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
        }
        generacion++
    }

    private fun archivo(nombre: String) = File(contexto.filesDir, nombre)

    // --- Consultas -------------------------------------------------------

    /** ¿La palabra existe (en la lista base o como palabra aprendida)? */
    fun esConocida(palabra: String): Boolean {
        if (!listo) return false
        val texto = palabra.lowercase()
        if (frecuenciaDe(texto) > 0) return true
        synchronized(candado) { return (propias[texto] ?: 0) >= VECES_PARA_CONOCIDA }
    }

    /** ¿La escribió el usuario alguna vez? (para poder borrarla con el dedo largo) */
    fun esPropia(palabra: String): Boolean =
        synchronized(candado) { propias.containsKey(palabra.lowercase()) }

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
        // La tilde que falta (avion -> avión): se ofrece aunque lo escrito exista.
        varianteConTilde(texto, FACTOR_TILDE_SUGERENCIA)?.let { salida.add(it) }
        // Si nada empieza así, es casi seguro una errata: se busca una corrección.
        if (encontradas.isEmpty() && texto.length >= LARGO_MINIMO_CORRECCION) {
            mejorEdicion(texto)?.let { salida.add(it) }
        }
        // «Conservar lo escrito» solo tiene sentido a mitad de palabra: con una o
        // dos letras la casilla se aprovecha mejor con palabras de verdad.
        if (texto.length >= LARGO_MINIMO_CORRECCION) salida.add(texto)
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
                        .take(limite).map { it.key }
                }
            }
            // Sin contexto: lo que más escribe esta persona.
            return propias.entries.asSequence()
                .filter { it.value >= VECES_PARA_CONOCIDA }
                .sortedByDescending { it.value }
                .take(limite).map { it.key }.toList()
        }
    }

    /** Anota que se escribió una palabra (y detrás de cuál). */
    fun registrar(palabra: String, anterior: String?) {
        val texto = palabra.lowercase()
        if (texto.length < 2 || texto.any { !it.isLetter() }) return
        synchronized(candado) {
            propias[texto] = (propias[texto] ?: 0) + 1
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

    /** Guarda una palabra desconocida como propia (pulsado largo en la barra). */
    fun aprender(palabra: String) {
        val texto = palabra.lowercase().trim()
        if (texto.isEmpty()) return
        synchronized(candado) {
            propias[texto] = maxOf(propias[texto] ?: 0, VECES_PARA_CONOCIDA)
            generacion++
        }
        programarGuardado()
    }

    /** Olvida una palabra propia. Devuelve true si estaba guardada. */
    fun olvidar(palabra: String): Boolean {
        val quitada = synchronized(candado) {
            generacion++
            propias.remove(palabra.lowercase().trim()) != null
        }
        if (quitada) programarGuardado()
        return quitada
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
            if (palabra.length > texto.length && palabra.startsWith(texto) && vistas.add(palabra)) {
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
        return salida.sortedByDescending { destinos[it] ?: 0 }
    }

    /** Las palabras propias ordenadas por uso (se rehace solo si cambiaron). */
    private fun listasPropias(): List<String> = synchronized(candado) {
        if (generacionCache != generacion) {
            ordenPropias = propias.entries.sortedByDescending { it.value }.map { it.key }
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
        generacion++
    }

    private fun podarBigramas() {
        val conservar = bigramas.entries.sortedByDescending { entrada ->
            entrada.value.values.sum()
        }.take(MAX_ORIGENES * 3 / 4)
        bigramas.clear()
        for ((clave, valor) in conservar) bigramas[clave] = valor
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
        val (textoPalabras, textoBigramas) = withContext(Dispatchers.Main.immediate) {
            synchronized(candado) {
                val palabras = propias.entries.joinToString("\n") { "${it.key}\t${it.value}" }
                val pares = bigramas.entries.joinToString("\n") { (origen, destinos) ->
                    origen + "\t" + destinos.entries.joinToString(",") { "${it.key}:${it.value}" }
                }
                palabras to pares
            }
        }
        withContext(Dispatchers.IO) {
            runCatching {
                archivo(ARCHIVO_PROPIAS).writeText(textoPalabras)
                archivo(ARCHIVO_BIGRAMAS).writeText(textoBigramas)
            }
        }
    }

    companion object {
        private const val ARCHIVO_BASE = "diccionario_es.txt"
        private const val ARCHIVO_PROPIAS = "dictado_usuario.txt"
        private const val ARCHIVO_BIGRAMAS = "dictado_bigramas.txt"

        /** Con dos veces que la escribas, la palabra es tuya y ya no se corrige. */
        private const val VECES_PARA_CONOCIDA = 2

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
