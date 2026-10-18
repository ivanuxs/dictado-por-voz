package com.ivanuxs.dictadoime

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.inputmethodservice.InputMethodService
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.text.InputType
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/** Estados visuales del botón de micrófono. */
enum class Estado { LISTO, GRABANDO, PROCESANDO, ERROR }

/**
 * Teclado del sistema (IME). Es deliberadamente fino: captura audio, lo manda
 * al puente y escribe lo que devuelve. Ninguna lógica de transcripción ni de
 * normalización vive aquí: eso está en el servidor de dictado.
 */
class ServicioTecladoVoz : InputMethodService() {

    private lateinit var ajustes: Ajustes
    private lateinit var micro: GestorAudio
    private lateinit var botonMicro: ImageButton
    private lateinit var textoEstado: TextView
    private lateinit var puntoEstado: View
    private lateinit var contenedor: LinearLayout
    private lateinit var franjaPortapapeles: LinearLayout
    private lateinit var textoFranja: TextView
    private lateinit var iconoFranja: ImageView
    private lateinit var botonMicroFranja: ImageButton
    private lateinit var portapapeles: ClipboardManager
    private lateinit var diccionario: Diccionario
    private lateinit var barra: BarraSugerencias
    private lateinit var teclado: TecladoLetras
    private lateinit var historial: Portapapeles
    private var panel: PanelPortapapeles? = null
    private var panelAbierto = false
    private var modoEdicion = false
    private var alturaPintada = -1
    private var numerosPintados: Boolean? = null

    /** Lo que pide el campo de texto (ver `leerCampo`). */
    private var capitalizaFrases = true
    private var capitalizaPalabras = false
    private var correccionEnCampo = true

    /**
     * Ajustes que se consultan MIENTRAS se teclea. Se leen una sola vez, al abrir
     * el campo: van cifrados (EncryptedSharedPreferences) y leerlos en cada tecla
     * costaba milisegundos que se notaban como retardo al pulsar. Medido el
     * 2026-10-16: cada tecla hacía su propia lectura de `sugerencias`.
     * Si el usuario cambia algo en los ajustes, se aplica en el campo siguiente.
     */
    private var sugerenciasActivas = true
    private var autocorreccionActiva = true
    private var portapapelesActivo = true
    private var historialActivo = true

    /** El campo es de contraseña: aquí no se ofrece ni se guarda el portapapeles. */
    private var enCampoSecreto = false

    /** Hay un recálculo de la barra de sugerencias esperando al siguiente fotograma. */
    private var barraPendiente = false

    /** Lo copiado que se ofrece en la franja (null si no hay nada que ofrecer). */
    private var copiado: String? = null

    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var tarea: Job? = null
    private var sesion: SesionViva? = null
    private var largoInsertado = 0

    // --- Estado del diccionario (sugerencias y corrección) ----------------

    /** Palabra que se está escribiendo, en minúsculas. */
    private val palabra = StringBuilder()

    /** Palabras ya terminadas en este campo (la última alimenta los bigramas). */
    private val terminadas = ArrayDeque<String>()

    /** La palabra en curso empezó con mayúscula. */
    private var capitalizada = false

    /** La próxima palabra abre oración (para la mayúscula automática). */
    private var capitalizarOracion = true

    /** Cuándo escribimos por última vez nosotros (para distinguir cambios ajenos). */
    private var ultimaEdicion = 0L

    /** Autocorrección recién hecha, para poder deshacerla con el borrado. */
    private var deshacer: Deshacer? = null

    /** Cómo reponer lo escrito si el usuario borra justo después de una corrección. */
    private data class Deshacer(val original: String, val corregida: String, val separador: String)

    override fun onCreate() {
        super.onCreate()
        ajustes = Ajustes(this)
        refrescarAjustesDeTecleo()
        micro = GestorAudio()
        diccionario = Diccionario(this)
        historial = Portapapeles(this)
        portapapeles = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        portapapeles.addPrimaryClipChangedListener(oyentePortapapeles)
        // La lista de palabras se carga en segundo plano: el teclado no espera.
        alcance.launch { diccionario.cargar() }
    }

    /** El portapapeles cambió mientras el teclado estaba vivo: se guarda y se refresca. */
    private val oyentePortapapeles = ClipboardManager.OnPrimaryClipChangedListener {
        guardarCopiado()
        refrescarPortapapeles()
    }

    override fun onCreateInputView(): View {
        val raiz = layoutInflater.inflate(R.layout.keyboard_view, null)
        textoEstado = raiz.findViewById(R.id.texto_estado)
        puntoEstado = raiz.findViewById(R.id.punto_estado)
        contenedor = raiz.findViewById(R.id.contenedor)
        franjaPortapapeles = raiz.findViewById(R.id.fila_portapapeles)
        textoFranja = raiz.findViewById(R.id.texto_portapapeles)
        iconoFranja = raiz.findViewById(R.id.icono_portapapeles)
        botonMicroFranja = raiz.findViewById(R.id.boton_micro_franja)
        franjaPortapapeles.setOnClickListener { pegarCopiado() }
        // El icono de la franja abre el historial (el resto de la franja pega).
        iconoFranja.setOnClickListener { abrirPanel() }
        botonMicroFranja.setOnClickListener { pasarADictado() }
        mostrarModo(modoEdicion)
        refrescarPortapapeles()
        return raiz
    }

    /**
     * Cambia el modo del teclado:
     *   - dictado (el micrófono grande),
     *   - edición (nuestro teclado de letras, sin salir a Gboard).
     */
    private fun mostrarModo(edicion: Boolean) {
        // El panel del historial nunca sobrevive a un cambio de modo.
        panel?.liberar()
        panel = null
        panelAbierto = false
        modoEdicion = edicion
        alturaPintada = ajustes.alturaTecla
        numerosPintados = ajustes.filaNumeros
        contenedor.removeAllViews()
        if (edicion) {
            barra = BarraSugerencias(this, oyenteSugerencias)
            contenedor.addView(barra.construir())
            teclado = TecladoLetras(this, ajustes, oyenteTeclas)
            contenedor.addView(teclado.construir())
            textoEstado.text = getString(R.string.edicion_estado)
            textoEstado.setTextColor(getColor(R.color.texto_estado))
            // En edición el punto queda neutro: no hay dictado en marcha.
            pintarPunto(R.color.texto_tecla_secundario)
            // Se relee el campo: puede haber texto de antes (o del dictado).
            sincronizarDesdeTexto()
        } else {
            val vista = layoutInflater.inflate(R.layout.vista_dictado, contenedor, false)
            botonMicro = vista.findViewById(R.id.boton_micro)
            botonMicro.setOnClickListener { alternar() }
            vista.findViewById<View>(R.id.boton_borrar).setOnClickListener { borrarCaracter() }
            vista.findViewById<View>(R.id.boton_enter).setOnClickListener { sendKeyChar('\n') }
            // Un toque pasa a nuestro teclado de letras; mantener pulsado abre el
            // selector del sistema (respaldo, para quien quiera Gboard).
            val botonTeclado = vista.findViewById<View>(R.id.boton_teclado)
            botonTeclado.setOnClickListener { mostrarModo(true) }
            botonTeclado.setOnLongClickListener {
                mostrarSelectorTeclados()
                true
            }
            ajustarAltura(vista)
            contenedor.addView(vista)
            mostrar(Estado.LISTO)
        }
        // La franja de arriba cambia con el modo: el micrófono solo se enseña
        // cuando no está el micrófono grande.
        refrescarPortapapeles()
    }

    /** El tamaño elegido en los ajustes también manda en el modo dictado. */
    private fun ajustarAltura(vista: View) {
        val alto = (ajustes.alturaTecla * resources.displayMetrics.density).toInt() + 10
        for (id in intArrayOf(R.id.boton_teclado, R.id.boton_borrar, R.id.boton_enter)) {
            val boton = vista.findViewById<View>(id)
            val parametros = boton.layoutParams as LinearLayout.LayoutParams
            parametros.width = alto
            parametros.height = alto
            boton.layoutParams = parametros
        }
        val parametrosMicro = botonMicro.layoutParams as LinearLayout.LayoutParams
        parametrosMicro.height = alto
        botonMicro.layoutParams = parametrosMicro
    }

    /** Lo que hace el teclado de letras al pulsar sus teclas. */
    private val oyenteTeclas = object : OyenteTeclas {
        override fun alEscribir(texto: String) = escribir(texto)

        override fun alBorrar() = borrarCaracter()

        override fun alEnter() {
            cerrarPalabra("\n", escribirSeparador = false)
            sendKeyChar('\n')
        }

        override fun alSelectorTeclados() = mostrarSelectorTeclados()
    }

    /** Lo que hace la barra de sugerencias al tocar o mantener una casilla. */
    private val oyenteSugerencias = object : OyenteSugerencias {
        override fun alElegir(texto: String) = aceptarSugerencia(texto)

        override fun alMantener(texto: String) = alternarPalabraPropia(texto)
    }

    /** Si el usuario cierra el campo de texto a media grabación, se corta. */
    override fun onFinishInput() {
        if (micro.grabando) terminarDictado()
        limpiarEstado()
        super.onFinishInput()
    }

    /**
     * Al aparecer el teclado en un campo se hace siempre lo mismo:
     *  - se lee lo que pide el campo (mayúsculas y corrección),
     *  - se vuelve al modo de DICTADO: el de letras NO se recuerda de una vez
     *    para otra, así que al volver al teclado siempre aparece el micrófono,
     *  - se relee el texto que hay delante del cursor y el portapapeles.
     * También se remonta si cambiaron de tamaño o la fila de números en los
     * ajustes, para que se note sin reiniciar nada.
     */
    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        leerCampo(info)
        // Los ajustes que se miran al teclear, frescos (por si se cambiaron).
        refrescarAjustesDeTecleo()
        if (::contenedor.isInitialized &&
            (modoEdicion ||
                alturaPintada != ajustes.alturaTecla || numerosPintados != ajustes.filaNumeros)
        ) {
            mostrarModo(false)
        }
        sincronizarDesdeTexto()
        // Se guarda lo que haya en el portapapeles aunque se copiara con el
        // teclado cerrado: Android solo lo deja leer con foco, y ese momento
        // es justo este.
        guardarCopiado()
        refrescarPortapapeles()
    }

    /**
     * Lo que pide el campo de texto. Android lo dice en `inputType` y hay que
     * hacerle caso: en un correo, una URL o una contraseña no se pone la
     * mayúscula sola ni se sugiere ni se corrige nada. Sin esto, el primer
     * carácter de un correo salía en mayúscula y parecía que el teclado lo
     * «volvía a arreglar» cada vez que lo cambiabas a minúscula.
     *
     * Ojo: `EditorInfo.IME_FLAG_NO_AUTO_CORRECT` no está en el `android.jar`
     * público con el que se compila esto, así que la decisión se toma con lo
     * que sí está: la clase y la variante del campo, y la bandera de «sin
     * sugerencias» (que es la que ponen los campos de correo y de contraseña).
     */
    private fun leerCampo(info: EditorInfo) {
        val tipo = info.inputType
        val variacion = tipo and InputType.TYPE_MASK_VARIATION
        val esTexto = (tipo and InputType.TYPE_MASK_CLASS) == InputType.TYPE_CLASS_TEXT
        val tecnico = !esTexto ||
            variacion == InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS ||
            variacion == InputType.TYPE_TEXT_VARIATION_WEB_EMAIL_ADDRESS ||
            variacion == InputType.TYPE_TEXT_VARIATION_URI ||
            variacion == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
            variacion == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
            variacion == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD ||
            (tipo and InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS) != 0
        capitalizaFrases = !tecnico && (tipo and InputType.TYPE_TEXT_FLAG_CAP_SENTENCES) != 0
        capitalizaPalabras = !tecnico && (tipo and InputType.TYPE_TEXT_FLAG_CAP_WORDS) != 0
        correccionEnCampo = !tecnico
        // En los campos de contraseña no se enseña el portapapeles: es justo
        // donde acaban las credenciales que se copian de otros sitios.
        enCampoSecreto = variacion == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
            variacion == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
            variacion == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD
    }

    /**
     * Copia los ajustes que se consultan mientras se teclea.
     *
     * Se hace al crear el servicio y al abrir un campo, NO en cada tecla: los
     * ajustes están cifrados y leerlos dentro del toque se notaba como retardo.
     */
    private fun refrescarAjustesDeTecleo() {
        sugerenciasActivas = ajustes.sugerencias
        autocorreccionActiva = ajustes.autocorreccion
        portapapelesActivo = ajustes.sugerirPortapapeles
        historialActivo = ajustes.guardarHistorial
    }

    /**
     * El cursor se movió o el texto cambió por fuera del teclado (por ejemplo al
     * dictar): se relee lo que hay delante del cursor. Los cambios que hacemos
     * nosotros se reconocen por la marca de tiempo de la última tecla.
     */
    override fun onUpdateSelection(
        inicioViejo: Int,
        finViejo: Int,
        inicioNuevo: Int,
        finNuevo: Int,
        candidatosInicio: Int,
        candidatosFin: Int
    ) {
        super.onUpdateSelection(
            inicioViejo, finViejo, inicioNuevo, finNuevo, candidatosInicio, candidatosFin
        )
        if (SystemClock.uptimeMillis() - ultimaEdicion < VENTANA_EDICION_PROPIA) return
        if (inicioNuevo != inicioViejo || finNuevo != finViejo) sincronizarDesdeTexto()
    }

    override fun onDestroy() {
        // Un último volcado de lo aprendido, en un alcance aparte: el del
        // servicio se cancela justo después y no le daría tiempo.
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch { diccionario.volcar() }
        portapapeles.removePrimaryClipChangedListener(oyentePortapapeles)
        alcance.cancel()
        micro.liberar()
        super.onDestroy()
    }

    // --- Dictado ---------------------------------------------------------

    private fun alternar() {
        if (micro.grabando) {
            terminarDictado()
        } else {
            empezarDictado()
        }
    }

    private fun empezarDictado() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            mostrar(Estado.ERROR, getString(R.string.falta_permiso_micro))
            return
        }
        if (!ajustes.configurado) {
            mostrar(Estado.ERROR, getString(R.string.falta_configurar))
            return
        }
        if (!micro.iniciar()) {
            mostrar(Estado.ERROR, getString(R.string.error_micro))
            return
        }
        vibrar(VIBRACION_CORTA)
        largoInsertado = 0
        mostrar(Estado.GRABANDO)

        if (ajustes.modoVivo) empezarVivo() else empezarNormal()
    }

    /** Modo normal: se acumula el audio y se manda entero al terminar. */
    private fun empezarNormal() {
        val acumulado = ByteArrayOutputStream()
        tarea = alcance.launch {
            while (isActive && micro.grabando) {
                val bloque = withContext(Dispatchers.IO) { micro.leer() }
                if (bloque != null) acumulado.write(bloque)
            }
            micro.liberar()
            val pcm = acumulado.toByteArray()
            if (pcm.isEmpty()) {
                mostrar(Estado.ERROR, getString(R.string.sin_audio))
                return@launch
            }
            mostrar(Estado.PROCESANDO)
            val resultado = withContext(Dispatchers.IO) {
                ClienteRed(ajustes).transcribir(pcm)
            }
            cuandoTermine(resultado)
        }
    }

    /** Modo vivo: el audio sale por WebSocket y el texto se inserta al vuelo. */
    private fun empezarVivo() {
        val nueva = ClienteRed(ajustes).sesionViva(avisosVivo())
        sesion = nueva
        if (!nueva.abrir()) {
            micro.liberar()
            mostrar(Estado.ERROR)
            return
        }
        tarea = alcance.launch {
            while (isActive && micro.grabando) {
                val bloque = withContext(Dispatchers.IO) { micro.leer() }
                if (bloque != null) nueva.enviar(bloque)
            }
            micro.liberar()
            mostrar(Estado.PROCESANDO)
            nueva.terminar()
        }
    }

    /** Pide parar: la corrutina de lectura ve que ya no se graba y termina sola. */
    private fun terminarDictado() {
        micro.detener()
        vibrar(VIBRACION_CORTA)
    }

    private fun cuandoTermine(resultado: Resultado) {
        when (resultado) {
            is Resultado.Texto -> {
                currentInputConnection?.commitText(resultado.texto, 1)
                mostrar(Estado.LISTO)
                vibrar(VIBRACION_CORTA)
            }
            is Resultado.Error -> mostrar(Estado.ERROR, resultado.motivo)
        }
    }

    private fun avisosVivo() = object : AvisosVivo {
        override fun alParcial(texto: String) {
            alcance.launch { insertarParcial(texto) }
        }

        override fun alFinal(texto: String) {
            alcance.launch {
                reemplazarParcial(texto)
                mostrar(Estado.LISTO)
                vibrar(VIBRACION_CORTA)
            }
            cerrarSesion()
        }

        override fun alError(motivo: String) {
            alcance.launch { mostrar(Estado.ERROR, motivo) }
            cerrarSesion()
        }
    }

    /** Escribe el avance del dictado vivo, reemplazando el parcial anterior. */
    private fun insertarParcial(texto: String) {
        val conexion = currentInputConnection ?: return
        if (largoInsertado > 0) conexion.deleteSurroundingText(largoInsertado, 0)
        val nuevo = if (texto.isBlank()) "" else "$texto "
        conexion.commitText(nuevo, 1)
        largoInsertado = nuevo.length
    }

    /** Cambia el último parcial por el texto definitivo. */
    private fun reemplazarParcial(texto: String) {
        val conexion = currentInputConnection ?: return
        if (largoInsertado > 0) {
            conexion.deleteSurroundingText(largoInsertado, 0)
            largoInsertado = 0
        }
        if (texto.isNotBlank()) conexion.commitText(texto, 1)
    }

    private fun cerrarSesion() {
        sesion?.cerrar()
        sesion = null
    }

    // --- Diccionario, sugerencias y corrección ----------------------------

    /** Escribe lo que llega del teclado, llevando la cuenta de la palabra. */
    private fun escribir(texto: String) {
        marcarEdicion()
        deshacer = null
        val esLetra = texto.length == 1 && texto[0].isLetter()
        if (!esLetra) {
            cerrarPalabra(texto)
            return
        }
        if (palabra.isEmpty()) {
            // Primera letra de la palabra. La mayúscula la decide el TECLADO, que
            // es el que sabe si está encendida por la mayúscula automática o si
            // el usuario la apagó a mano: aquí no se re-capitaliza nada.
            capitalizada = texto[0].isUpperCase()
            palabra.append(texto.lowercase())
            enviar(texto)
        } else {
            palabra.append(texto.lowercase())
            enviar(texto)
        }
        actualizarBarra()
    }

    /**
     * Termina la palabra en curso: corrige si toca, escribe el separador y anota
     * la palabra (y detrás de cuál iba, para las predicciones).
     */
    private fun cerrarPalabra(separador: String, escribirSeparador: Boolean = true) {
        marcarEdicion()
        val escrita = palabra.toString()
        var cerrada: String? = null
        if (escrita.isEmpty()) {
            deshacer = null
        } else {
            val correccion = if (autocorreccionActiva && correccionEnCampo) {
                diccionario.corregirPalabra(escrita)
            } else {
                null
            }
            if (correccion != null) {
                val forma = conMayusculas(correccion, capitalizada)
                val conexion = currentInputConnection
                conexion?.deleteSurroundingText(escrita.length, 0)
                conexion?.commitText(forma, 1)
                deshacer = Deshacer(conMayusculas(escrita, capitalizada), forma, separador)
                cerrada = forma
            } else {
                deshacer = null
                cerrada = conMayusculas(escrita, capitalizada)
            }
        }
        if (escribirSeparador) enviar(separador)
        if (cerrada != null) anotar(cerrada)
        palabra.setLength(0)
        capitalizada = false
        capitalizarOracion = capitalizaPalabras ||
            (capitalizaFrases && separador.length == 1 && FINES_DE_ORACION.contains(separador))
        actualizarBarra()
        avisarMayuscula()
    }

    /** El usuario tocó una casilla: se escribe en lugar de la palabra en curso. */
    private fun aceptarSugerencia(texto: String) {
        val elegida = texto.trim()
        if (elegida.isEmpty()) return
        val conexion = currentInputConnection ?: return
        marcarEdicion()
        deshacer = null
        if (palabra.isNotEmpty()) {
            // Se borra solo lo escrito de esta palabra (cada tecla escribió una).
            conexion.deleteSurroundingText(palabra.length, 0)
        }
        val forma = conMayusculas(
            elegida,
            if (palabra.isEmpty()) quiereMayusculaElTeclado() else capitalizada
        )
        conexion.commitText("$forma ", 1)
        anotar(forma)
        palabra.setLength(0)
        capitalizada = false
        capitalizarOracion = capitalizaPalabras
        actualizarBarra()
        avisarMayuscula()
    }

    /** Dedo largo en la barra: añade la palabra al diccionario propio o la quita. */
    private fun alternarPalabraPropia(texto: String) {
        val elegida = texto.trim()
        if (elegida.isEmpty()) return
        if (diccionario.esPropia(elegida) && diccionario.olvidar(elegida)) {
            Toast.makeText(
                this, getString(R.string.palabra_olvidada, elegida), Toast.LENGTH_SHORT
            ).show()
        } else {
            diccionario.aprender(elegida)
            Toast.makeText(
                this, getString(R.string.palabra_aprendida, elegida), Toast.LENGTH_SHORT
            ).show()
        }
        actualizarBarra()
    }

    /**
     * Pide recalcular la barra, pero NO lo hace dentro del toque: el carácter ya
     * se ha escrito y el trabajo (diccionario, textos de las casillas) se hace en
     * el fotograma siguiente, una sola vez aunque se teclee rápido o se mantenga
     * pulsado el ⌫. Hacerlo dentro del toque costaba un segundo fotograma por
     * pulsación y picos de 20-37 ms (medido el 2026-10-16).
     */
    private fun actualizarBarra() {
        if (!modoEdicion || !::barra.isInitialized) return
        if (barraPendiente) return
        barraPendiente = true
        contenedor.post {
            barraPendiente = false
            pintarBarra()
        }
    }

    /** Recalcula las casillas de la barra según lo que se está escribiendo. */
    private fun pintarBarra() {
        if (!modoEdicion || !::barra.isInitialized) return
        // En un campo que no admite sugerencias (correo, contraseña, URL) no se
        // ofrece nada: ni palabras ni correcciones.
        if (!sugerenciasActivas || !correccionEnCampo) {
            barra.mostrar(emptyList())
            return
        }
        val lista = if (palabra.isEmpty()) {
            diccionario.predicciones(terminadas.lastOrNull(), CASILLAS)
        } else {
            diccionario.sugerencias(
                palabra.toString(), terminadas.lastOrNull(), capitalizada, CASILLAS
            )
        }
        barra.mostrar(lista)
    }

    /** Registra la palabra terminada: frecuencia propia y bigrama con la anterior. */
    private fun anotar(nueva: String) {
        // En un correo o una contraseña no se aprende nada: solo se ensuciaría
        // el diccionario con direcciones y claves.
        if (correccionEnCampo) diccionario.registrar(nueva, terminadas.lastOrNull())
        terminadas.addLast(nueva)
        if (terminadas.size > MAXIMO_TERMINADAS) terminadas.removeFirst()
    }

    /**
     * Relee lo que hay delante del cursor y rehace el estado: la palabra en curso,
     * la anterior (para las predicciones) y si toca mayúscula. Se usa al abrir un
     * campo, al mover el cursor a mano y cuando el texto lo cambia otro (dictado).
     */
    private fun sincronizarDesdeTexto() {
        val antes = currentInputConnection
            ?.getTextBeforeCursor(LECTURA_MAXIMA, 0)?.toString()
        limpiarEstado()
        if (antes != null) {
            val corte = antes.indexOfLast { !esLetraDePalabra(it) }
            val enCurso = antes.substring(corte + 1)
            palabra.append(enCurso.lowercase())
            capitalizada = enCurso.firstOrNull()?.isUpperCase() == true
            val separador = if (corte >= 0) antes[corte] else null
            capitalizarOracion = capitalizaPalabras || (capitalizaFrases &&
                (separador == null || FINES_DE_ORACION.contains(separador)))
            val previo = antes.substring(0, maxOf(corte, 0))
            val anteriores = previo.split(Regex("[^\\p{L}']+")).filter { it.isNotEmpty() }
            // Solo interesa la última: es la que alimenta las predicciones.
            anteriores.lastOrNull()?.let { terminadas.addLast(it) }
        }
        actualizarBarra()
        avisarMayuscula()
    }

    /**
     * Avisa al teclado de letras de si la próxima palabra va en mayúscula
     * automática: así el ⇧ se enciende y se apaga solo, y lo que se ve es lo que
     * se va a escribir. Si el usuario lo apagó a mano, el teclado lo respeta
     * hasta la frase (o el campo) siguiente.
     */
    private fun avisarMayuscula() {
        if (modoEdicion && ::teclado.isInitialized) teclado.sugerirMayuscula(capitalizarOracion)
    }

    /** Si el teclado de letras está pidiendo mayúscula ahora mismo. */
    private fun quiereMayusculaElTeclado(): Boolean =
        modoEdicion && ::teclado.isInitialized && teclado.quiereMayuscula()

    /** Olvida la palabra en curso (al cambiar de campo o de modo). */
    private fun limpiarEstado() {
        palabra.setLength(0)
        terminadas.clear()
        deshacer = null
        capitalizada = false
        capitalizarOracion = capitalizaFrases || capitalizaPalabras
    }

    /** Marca que lo último que tocó el texto fuimos nosotros. */
    private fun marcarEdicion() {
        ultimaEdicion = SystemClock.uptimeMillis()
    }

    private fun enviar(texto: String) {
        currentInputConnection?.commitText(texto, 1)
    }

    /** Devuelve la palabra con la mayúscula inicial si le toca. */
    private fun conMayusculas(texto: String, capitalizar: Boolean): String =
        if (capitalizar && texto.isNotEmpty()) texto.replaceFirstChar { it.uppercase() } else texto

    private fun esLetraDePalabra(caracter: Char) = caracter.isLetter() || caracter == '\''

    // --- Portapapeles ----------------------------------------------------

    /**
     * Relee el portapapeles del sistema y recompone la franja de arriba.
     *
     * La franja se ve en el modo de letras (ahí lleva el micrófono a mano) y, en
     * el modo dictado, cuando hay algo copiado o algo en el historial.
     *
     * Desde Android 10 solo puede leer el portapapeles quien tiene el foco, y
     * mientras el teclado está en pantalla ese es el teclado; si Android no deja,
     * no se muestra nada y no pasa nada más. Lo copiado se guarda en el historial
     * cifrado (ver `guardarCopiado`); la franja sigue leyendo el portapapeles del
     * sistema, que es la fuente de verdad de «lo último copiado».
     */
    private fun refrescarPortapapeles() {
        if (!::franjaPortapapeles.isInitialized) return
        val permitido = portapapelesActivo && !enCampoSecreto
        val texto = if (permitido) leerPortapapeles() else null
        copiado = texto
        val hayTexto = texto != null
        // El historial da acceso al panel aunque ahora no haya nada copiado.
        val hayHistorial = permitido && historial.disponible && historial.hayAlgo()
        // En la franja, todo en una línea (los saltos se ven como espacios).
        textoFranja.text = texto?.replace(Regex("\\s+"), " ").orEmpty()
        iconoFranja.visibility = if (hayTexto || hayHistorial) View.VISIBLE else View.GONE
        textoFranja.visibility = if (hayTexto) View.VISIBLE else View.GONE
        botonMicroFranja.visibility = if (modoEdicion) View.VISIBLE else View.GONE
        franjaPortapapeles.visibility = when {
            // En un campo de contraseña no se ofrece nada del portapapeles.
            enCampoSecreto -> View.GONE
            modoEdicion -> View.VISIBLE
            hayTexto || hayHistorial -> View.VISIBLE
            else -> View.GONE
        }
        // Si el panel está abierto, se repinta con lo que acaba de entrar.
        if (panelAbierto) panel?.refrescar()
    }

    /** El micrófono de la franja: pasa al modo dictado (el micrófono grande). */
    private fun pasarADictado() {
        if (!modoEdicion) return
        limpiarEstado()
        mostrarModo(false)
    }

    /** Lo copiado, o null si no hay nada que merezca la pena ofrecer. */
    private fun leerPortapapeles(): String? {
        val texto = runCatching {
            if (!portapapeles.hasPrimaryClip()) {
                null
            } else {
                portapapeles.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString()
            }
        }.getOrNull() ?: return null
        val limpio = texto.trim()
        if (limpio.isEmpty() || limpio.length > LARGO_MAXIMO_PORTAPAPELES) return null
        return limpio
    }

    /** Un toque en la franja: se pega lo copiado donde está el cursor. */
    private fun pegarCopiado() {
        val texto = copiado ?: return
        val conexion = currentInputConnection ?: return
        marcarEdicion()
        deshacer = null
        conexion.commitText(texto, 1)
        vibrar(VIBRACION_CORTA)
        sincronizarDesdeTexto()
    }

    /**
     * Guarda en el historial lo que hay ahora mismo en el portapapeles.
     *
     * Se llama al cambiar el portapapeles y al abrir un campo: desde Android 10
     * solo se puede leer con el teclado en pantalla, así que si el usuario copió
     * con el teclado cerrado, es ahora cuando se entera.
     */
    private fun guardarCopiado() {
        if (!historialActivo || enCampoSecreto || !historial.disponible) return
        val texto = leerPortapapeles() ?: return
        historial.anadir(texto)
    }

    /** Abre el historial ocupando el sitio del teclado (como el panel de Gboard). */
    private fun abrirPanel() {
        if (!::contenedor.isInitialized) return
        // Antes de enseñarlo, se guarda lo que haya ahora en el portapapeles.
        guardarCopiado()
        val nuevo = PanelPortapapeles(this, historial, oyentePanel)
        panel = nuevo
        panelAbierto = true
        contenedor.removeAllViews()
        contenedor.addView(nuevo.construir())
        textoEstado.text = getString(R.string.portapapeles_titulo)
        textoEstado.setTextColor(getColor(R.color.texto_estado))
        pintarPunto(R.color.acento)
    }

    /** Cierra el panel y repinta el modo que estaba activo. */
    private fun cerrarPanel() {
        panel?.liberar()
        panel = null
        panelAbierto = false
        mostrarModo(modoEdicion)
    }

    /** Lo que hace el panel del historial al tocar sus filas y botones. */
    private val oyentePanel = object : OyentePortapapeles {
        override fun alPegar(texto: String) {
            val conexion = currentInputConnection ?: return
            marcarEdicion()
            deshacer = null
            conexion.commitText(texto, 1)
            vibrar(VIBRACION_CORTA)
            cerrarPanel()
            sincronizarDesdeTexto()
        }

        override fun alFijar(texto: String, fijada: Boolean) {
            historial.fijar(texto, fijada)
            vibrar(VIBRACION_CORTA)
            avisar(getString(if (fijada) R.string.portapapeles_fijado else R.string.portapapeles_soltado))
            panel?.refrescar()
        }

        override fun alBorrar(texto: String) {
            historial.borrar(texto)
            panel?.refrescar()
        }

        override fun alVaciar() {
            historial.vaciarRecientes()
            avisar(getString(R.string.portapapeles_vaciado))
            panel?.refrescar()
            refrescarPortapapeles()
        }

        override fun alCerrar() = cerrarPanel()
    }

    /** Aviso corto (fijar, quitar, vaciar). */
    private fun avisar(mensaje: String) {
        Toast.makeText(this, mensaje, Toast.LENGTH_SHORT).show()
    }

    // --- Teclas del teclado ----------------------------------------------

    private fun borrarCaracter() {
        marcarEdicion()
        val conexion = currentInputConnection ?: return
        val pendiente = deshacer
        if (pendiente != null) {
            // Se acaba de corregir: el primer borrado devuelve lo que se escribió.
            deshacer = null
            conexion.deleteSurroundingText(
                pendiente.corregida.length + pendiente.separador.length, 0
            )
            conexion.commitText(pendiente.original + pendiente.separador, 1)
            palabra.setLength(0)
            capitalizada = false
            if (terminadas.lastOrNull() == pendiente.corregida) {
                terminadas.removeLast()
                terminadas.addLast(pendiente.original)
            }
            actualizarBarra()
            return
        }
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DEL))
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_DEL))
        if (palabra.isNotEmpty()) {
            palabra.deleteAt(palabra.length - 1)
        } else {
            // Se borró el separador: la palabra anterior vuelve a estar en curso.
            val previa = terminadas.removeLastOrNull()
            if (previa != null) {
                palabra.append(previa.lowercase())
                capitalizada = previa.firstOrNull()?.isUpperCase() == true
            }
        }
        actualizarBarra()
    }

    /** Selector de teclados del sistema (respaldo: permite ir a Gboard a mano). */
    private fun mostrarSelectorTeclados() {
        val gestor = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        gestor.showInputMethodPicker()
    }

    // --- Presentación ----------------------------------------------------

    private fun mostrar(estado: Estado, mensaje: String? = null) {
        if (!::textoEstado.isInitialized) return
        textoEstado.text = when (estado) {
            Estado.LISTO -> getString(R.string.estado_listo)
            Estado.GRABANDO -> getString(R.string.estado_grabando)
            Estado.PROCESANDO -> getString(R.string.estado_procesando)
            Estado.ERROR -> mensaje ?: getString(R.string.estado_error)
        }
        textoEstado.setTextColor(
            getColor(if (estado == Estado.ERROR) R.color.texto_error else R.color.texto_estado)
        )
        // Punto de la línea de estado: el mismo aviso que el color del micrófono,
        // pero visible también en el modo de letras.
        pintarPunto(
            when (estado) {
                Estado.LISTO -> R.color.acento
                Estado.GRABANDO -> R.color.micro_grabando
                Estado.PROCESANDO -> R.color.micro_procesando
                Estado.ERROR -> R.color.texto_error
            }
        )
        // El botón del micrófono solo existe en el modo dictado.
        if (!::botonMicro.isInitialized) return
        botonMicro.setBackgroundResource(
            when (estado) {
                Estado.LISTO -> R.drawable.fondo_micro_listo
                Estado.GRABANDO -> R.drawable.fondo_micro_grabando
                Estado.PROCESANDO -> R.drawable.fondo_micro_procesando
                Estado.ERROR -> R.drawable.fondo_micro_error
            }
        )
    }

    private fun vibrar(milisegundos: Long) {
        val vibrador = getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator ?: return
        vibrador.vibrate(
            VibrationEffect.createOneShot(milisegundos, VibrationEffect.DEFAULT_AMPLITUDE)
        )
    }

    /** Tiñe el punto de la línea de estado (el dibujo es blanco a propósito). */
    private fun pintarPunto(color: Int) {
        if (!::puntoEstado.isInitialized) return
        puntoEstado.backgroundTintList = ColorStateList.valueOf(getColor(color))
    }

    companion object {
        private const val VIBRACION_CORTA = 25L

        /** Casillas de la barra de sugerencias. */
        private const val CASILLAS = 3

        /** Palabras que se recuerdan del campo para las predicciones. */
        private const val MAXIMO_TERMINADAS = 40

        /** Cuánto texto se relee por delante del cursor al sincronizar. */
        private const val LECTURA_MAXIMA = 80

        /** Lo copiado más largo que se ofrece en la franja (2 000 caracteres). */
        private const val LARGO_MAXIMO_PORTAPAPELES = 2000

        /** Si la última edición fue nuestra hace tan poco, no se resincroniza. */
        private const val VENTANA_EDICION_PROPIA = 250L

        /** Después de estos signos, la palabra siguiente va en mayúscula. */
        private const val FINES_DE_ORACION = ".!?…\n"
    }
}
