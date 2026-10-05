package com.ivanuxs.dictadoime

import android.content.Context
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.LinearLayout

/** Qué hacer cuando se pulsa una tecla del teclado de letras. */
interface OyenteTeclas {
    /** Una letra, un número, un símbolo o un espacio. */
    fun alEscribir(texto: String)

    /** Tecla de borrar (⌫). */
    fun alBorrar()

    /** Salto de línea (↵). */
    fun alEnter()

    /** Pasar al modo dictado (🎤). */
    fun alDictar()

    /** Abrir el selector de teclados del sistema (respaldo: ir a Gboard). */
    fun alSelectorTeclados()
}

/**
 * Teclado de letras propio, para editar sin salir a Gboard.
 *
 * Se construye en código y no en XML porque las dos páginas (letras y símbolos)
 * y el estado de mayúsculas comparten las mismas teclas: cambiar de página o de
 * mayúsculas es volver a pintar las etiquetas, sin duplicar layouts.
 *
 * Anchos: cada fila suma 10, así que las teclas normales valen 1 y las anchas
 * (⇧, ⌫, ?123, 🎤, ↵) 1,5 o más.
 */
class TecladoLetras(
    private val contexto: Context,
    private val ajustes: Ajustes,
    private val oyente: OyenteTeclas
) {

    private var simbolos = false
    private var mayusculas = false   // la próxima letra sale en mayúscula
    private var fijas = false        // mayúsculas fijas (⇪)

    private val contenedor = LinearLayout(contexto)
    private val repeticiones = Handler(Looper.getMainLooper())
    private var borradoContinuo: Runnable? = null

    /** Construye la vista del teclado (con la página de letras). */
    fun construir(): View {
        contenedor.orientation = LinearLayout.VERTICAL
        contenedor.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )
        pintar()
        return contenedor
    }

    // --- Pintado ---------------------------------------------------------

    private fun pintar() {
        contenedor.removeAllViews()
        val filas = if (simbolos) FILAS_SIMBOLOS else FILAS_LETRAS
        // La fila de números arriba es opcional (en la página de símbolos ya está).
        if (!simbolos && ajustes.filaNumeros) {
            contenedor.addView(construirFila(FILA_NUMEROS))
        }
        for (fila in filas) {
            contenedor.addView(construirFila(fila))
        }
    }

    private fun construirFila(fila: List<Pair<String, Float>>): View {
        val linea = LinearLayout(contexto)
        linea.orientation = LinearLayout.HORIZONTAL
        linea.weightSum = 10f
        for ((nombre, peso) in fila) {
            linea.addView(construirTecla(nombre, peso))
        }
        return linea
    }

    private fun construirTecla(nombre: String, peso: Float): Button {
        val tecla = Button(contexto)
        tecla.text = etiqueta(nombre)
        tecla.isAllCaps = false
        tecla.setTextSize(if (nombre.length > 1) 15f else 18f)
        tecla.setTextColor(contexto.getColor(R.color.icono))
        tecla.background = contexto.getDrawable(R.drawable.fondo_boton)
        tecla.gravity = Gravity.CENTER
        tecla.typeface = if (nombre.length == 1) Typeface.DEFAULT else Typeface.DEFAULT_BOLD
        // Los Button traen tamaños mínimos que romperían la rejilla.
        tecla.minimumWidth = 0
        tecla.minimumHeight = 0
        tecla.setPadding(0, 0, 0, 0)

        val parametros = LinearLayout.LayoutParams(
            0,
            (ajustes.alturaTecla * contexto.resources.displayMetrics.density).toInt(),
            peso
        )
        val margen = contexto.resources.getDimensionPixelSize(R.dimen.margen_tecla)
        parametros.setMargins(margen, margen, margen, margen)
        tecla.layoutParams = parametros

        configurarPulsacion(tecla, nombre)
        return tecla
    }

    /** Etiqueta visible de una tecla según la página y las mayúsculas. */
    private fun etiqueta(nombre: String): String = when (nombre) {
        "MAYUS" -> if (fijas) "⇪" else "⇧"
        "BORRAR" -> "⌫"
        "ENTER" -> "↵"
        "DICTAR" -> "🎤"
        "espacio" -> ""
        "coma" -> ","
        "punto" -> "."
        "SIMBOLOS" -> "?123"
        "LETRAS" -> "ABC"
        else -> if (mayusculas || fijas) nombre.uppercase() else nombre
    }

    // --- Pulsaciones -----------------------------------------------------

    private fun configurarPulsacion(tecla: Button, nombre: String) {
        tecla.setOnClickListener { pulsar(nombre) }
        if (nombre == "BORRAR") {
            conRepeticion(tecla) { oyente.alBorrar() }
        }
        if (nombre == "SIMBOLOS" || nombre == "LETRAS") {
            // Mantener pulsado: salida al selector de teclados (por si se quiere Gboard).
            tecla.setOnLongClickListener {
                oyente.alSelectorTeclados()
                true
            }
        }
    }

    private fun pulsar(nombre: String) {
        vibrar()
        when (nombre) {
            "MAYUS" -> {
                when {
                    fijas -> { fijas = false; mayusculas = false }      // ⇪ -> normal
                    mayusculas -> { fijas = true; mayusculas = false }  // ⇧  -> ⇪
                    else -> mayusculas = true                           // normal -> ⇧
                }
                pintar()
            }

            "BORRAR" -> oyente.alBorrar()
            "ENTER" -> oyente.alEnter()
            "DICTAR" -> oyente.alDictar()
            "espacio" -> {
                oyente.alEscribir(" ")
                bajarMayusculas()
            }

            "SIMBOLOS" -> {
                simbolos = true
                pintar()
            }

            "LETRAS" -> {
                simbolos = false
                pintar()
            }

            else -> {
                oyente.alEscribir(etiqueta(nombre))
                bajarMayusculas()
            }
        }
    }

    /** Tras escribir, la mayúscula de un solo uso se apaga (⇪ se mantiene). */
    private fun bajarMayusculas() {
        if (mayusculas) {
            mayusculas = false
            pintar()
        }
    }

    /** Borrado continuo: al mantener ⌫ se repite hasta soltar. */
    private fun conRepeticion(tecla: Button, accion: () -> Unit) {
        val repetir = object : Runnable {
            override fun run() {
                accion()
                repeticiones.postDelayed(this, 80)
            }
        }
        borradoContinuo = repetir
        tecla.setOnLongClickListener {
            repeticiones.postDelayed(repetir, 250)
            true
        }
        tecla.setOnTouchListener { _, evento ->
            if (evento.action == MotionEvent.ACTION_UP ||
                evento.action == MotionEvent.ACTION_CANCEL
            ) {
                repeticiones.removeCallbacks(repetir)
            }
            false
        }
    }

    private fun vibrar() {
        if (!ajustes.vibrar) return
        val vibrador = contexto.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator ?: return
        vibrador.vibrate(VibrationEffect.createOneShot(15, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    companion object {
        /** Fila de números, opcional, encima de las letras. */
        private val FILA_NUMEROS = listOf(
            "1" to 1f, "2" to 1f, "3" to 1f, "4" to 1f, "5" to 1f,
            "6" to 1f, "7" to 1f, "8" to 1f, "9" to 1f, "0" to 1f
        )

        /** Página de letras. Cada fila suma 10 de ancho. */
        private val FILAS_LETRAS = listOf(
            listOf("q" to 1f, "w" to 1f, "e" to 1f, "r" to 1f, "t" to 1f,
                "y" to 1f, "u" to 1f, "i" to 1f, "o" to 1f, "p" to 1f),
            listOf("a" to 1f, "s" to 1f, "d" to 1f, "f" to 1f, "g" to 1f,
                "h" to 1f, "j" to 1f, "k" to 1f, "l" to 1f, "ñ" to 1f),
            listOf("MAYUS" to 1.5f, "z" to 1f, "x" to 1f, "c" to 1f, "v" to 1f,
                "b" to 1f, "n" to 1f, "m" to 1f, "BORRAR" to 1.5f),
            listOf("SIMBOLOS" to 1.5f, "coma" to 1f, "DICTAR" to 1.5f,
                "espacio" to 3.5f, "punto" to 1f, "ENTER" to 1.5f)
        )

        /** Página de números y símbolos (incluye las vocales con tilde y ¿¡). */
        private val FILAS_SIMBOLOS = listOf(
            listOf("1" to 1f, "2" to 1f, "3" to 1f, "4" to 1f, "5" to 1f,
                "6" to 1f, "7" to 1f, "8" to 1f, "9" to 1f, "0" to 1f),
            listOf("-" to 1f, "/" to 1f, ":" to 1f, ";" to 1f, "(" to 1f,
                ")" to 1f, "$" to 1f, "&" to 1f, "@" to 1f, "\"" to 1f),
            listOf("á" to 1f, "é" to 1f, "í" to 1f, "ó" to 1f, "ú" to 1f,
                "¿" to 1f, "¡" to 1f, "%" to 1f, "+" to 1f, "=" to 1f),
            listOf("LETRAS" to 1.5f, "coma" to 1f, "DICTAR" to 1.5f,
                "espacio" to 2.5f, "punto" to 1f, "BORRAR" to 1.5f, "ENTER" to 1f)
        )
    }
}
