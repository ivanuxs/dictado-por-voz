package com.ivanuxs.dictadoime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.inputmethodservice.InputMethodService
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
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
    private lateinit var contenedor: FrameLayout
    private var modoEdicion = false
    private var alturaPintada = -1
    private var numerosPintados: Boolean? = null

    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var tarea: Job? = null
    private var sesion: SesionViva? = null
    private var largoInsertado = 0

    override fun onCreate() {
        super.onCreate()
        ajustes = Ajustes(this)
        micro = GestorAudio()
    }

    override fun onCreateInputView(): View {
        val raiz = layoutInflater.inflate(R.layout.keyboard_view, null)
        textoEstado = raiz.findViewById(R.id.texto_estado)
        contenedor = raiz.findViewById(R.id.contenedor)
        mostrarModo(modoEdicion)
        return raiz
    }

    /**
     * Cambia el modo del teclado:
     *   - dictado (el micrófono grande),
     *   - edición (nuestro teclado de letras, sin salir a Gboard).
     */
    private fun mostrarModo(edicion: Boolean) {
        modoEdicion = edicion
        alturaPintada = ajustes.alturaTecla
        numerosPintados = ajustes.filaNumeros
        contenedor.removeAllViews()
        if (edicion) {
            contenedor.addView(TecladoLetras(this, ajustes, oyenteTeclas).construir())
            textoEstado.text = getString(R.string.edicion_estado)
            textoEstado.setTextColor(getColor(R.color.texto_estado))
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
        override fun alEscribir(texto: String) {
            currentInputConnection?.commitText(texto, 1)
        }

        override fun alBorrar() = borrarCaracter()

        override fun alEnter() {
            sendKeyChar('\n')
        }

        override fun alDictar() = mostrarModo(false)

        override fun alSelectorTeclados() = mostrarSelectorTeclados()
    }

    /** Si el usuario cierra el campo de texto a media grabación, se corta. */
    override fun onFinishInput() {
        if (micro.grabando) terminarDictado()
        super.onFinishInput()
    }

    /**
     * Al aparecer el teclado en un campo, se comprueba si cambiaron el tamaño o
     * la fila de números en los ajustes: si cambiaron, se vuelve a montar para
     * que se note sin reiniciar el teclado.
     */
    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        if (::contenedor.isInitialized &&
            (alturaPintada != ajustes.alturaTecla || numerosPintados != ajustes.filaNumeros)
        ) {
            mostrarModo(modoEdicion)
        }
    }

    override fun onDestroy() {
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

    // --- Teclas del teclado ----------------------------------------------

    private fun borrarCaracter() {
        val conexion = currentInputConnection ?: return
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DEL))
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_DEL))
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

    companion object {
        private const val VIBRACION_CORTA = 25L
    }
}
