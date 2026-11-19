package com.ivanuxs.skipkey

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Lo que el panel pide al teclado cuando el usuario toca algo. */
interface OyentePortapapeles {
    /** Pegar una copia donde está el cursor. */
    fun alPegar(texto: String)

    /** Fijar o desfijar una copia. */
    fun alFijar(texto: String, fijada: Boolean)

    /** Borrar una copia concreta. */
    fun alBorrar(texto: String)

    /** Vaciar los recientes (los fijados se conservan). */
    fun alVaciar()

    /** Cerrar el panel y volver al teclado. */
    fun alCerrar()
}

/**
 * Panel del historial del portapapeles.
 *
 * Ocupa el sitio del teclado (se mete en el mismo contenedor), como hace
 * Gboard: no es un diálogo, así que el campo de texto NO pierde el foco y al
 * pegar se escribe donde estaba el cursor.
 *
 * Se construye en código, igual que el teclado de letras, para poder pintarlo
 * de nuevo entero cada vez que cambia el historial sin duplicar layouts.
 */
class PanelPortapapeles(
    private val contexto: Context,
    private val historial: Portapapeles,
    private val oyente: OyentePortapapeles
) {

    private val contenedor = LinearLayout(contexto)
    private val lista = LinearLayout(contexto)
    private val confirmacion = Handler(Looper.getMainLooper())
    private lateinit var botonVaciar: TextView
    private var confirmando = false

    /** Construye el panel con lo que haya guardado ahora mismo. */
    fun construir(): View {
        contenedor.orientation = LinearLayout.VERTICAL
        contenedor.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )

        contenedor.addView(cabecera())

        val scroll = ScrollView(contexto)
        scroll.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(ALTO_LISTA_DP)
        )
        scroll.isFillViewport = true
        lista.orientation = LinearLayout.VERTICAL
        scroll.addView(lista, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT
        ))
        contenedor.addView(scroll)

        contenedor.addView(nota())
        refrescar()
        return contenedor
    }

    /** Vuelve a pintar la lista (tras fijar, borrar o cuando cambia el portapapeles). */
    fun refrescar() {
        lista.removeAllViews()
        val entradas = historial.listar()
        if (!historial.disponible) {
            lista.addView(mensaje(R.string.portapapeles_sin_almacen))
            actualizarVaciar(false)
            return
        }
        if (entradas.isEmpty()) {
            lista.addView(mensaje(R.string.portapapeles_vacio))
            actualizarVaciar(false)
            return
        }
        val fijadas = entradas.filter { it.fijada }
        val recientes = entradas.filterNot { it.fijada }
        if (fijadas.isNotEmpty()) {
            lista.addView(seccion(R.string.portapapeles_fijados))
            fijadas.forEach { lista.addView(fila(it)) }
        }
        if (recientes.isNotEmpty()) {
            lista.addView(seccion(R.string.portapapeles_recientes))
            recientes.forEach { lista.addView(fila(it)) }
        }
        actualizarVaciar(recientes.isNotEmpty())
    }

    /** Quita los avisos pendientes al cerrar el panel (no toca el historial). */
    fun liberar() {
        confirmacion.removeCallbacksAndMessages(null)
    }

    // --- Piezas -----------------------------------------------------------

    private fun cabecera(): View {
        val cabecera = LinearLayout(contexto)
        cabecera.orientation = LinearLayout.HORIZONTAL
        cabecera.gravity = Gravity.CENTER_VERTICAL
        cabecera.setPadding(dp(10), dp(2), dp(2), dp(2))

        val titulo = TextView(contexto)
        titulo.text = contexto.getString(R.string.portapapeles_titulo)
        titulo.setTextColor(contexto.getColor(R.color.texto_tecla))
        titulo.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        titulo.typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        cabecera.addView(titulo, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))

        botonVaciar = TextView(contexto)
        botonVaciar.text = contexto.getString(R.string.portapapeles_vaciar)
        botonVaciar.setTextColor(contexto.getColor(R.color.texto_tecla_secundario))
        botonVaciar.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        botonVaciar.setPadding(dp(10), dp(8), dp(10), dp(8))
        botonVaciar.isClickable = true
        botonVaciar.background = contexto.getDrawable(R.drawable.fondo_accion)
        botonVaciar.setOnClickListener { pedirVaciar() }
        cabecera.addView(botonVaciar)

        cabecera.addView(
            botonIcono(R.drawable.ic_cerrar, R.string.portapapeles_cerrar) { oyente.alCerrar() }
        )
        return cabecera
    }

    private fun seccion(texto: Int): View {
        val etiqueta = TextView(contexto)
        etiqueta.text = contexto.getString(texto)
        etiqueta.setTextColor(contexto.getColor(R.color.texto_tecla_secundario))
        etiqueta.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
        etiqueta.letterSpacing = 0.08f
        etiqueta.setPadding(dp(12), dp(10), dp(12), dp(4))
        return etiqueta
    }

    private fun fila(entrada: Portapapeles.Entrada): View {
        val linea = LinearLayout(contexto)
        linea.orientation = LinearLayout.HORIZONTAL
        linea.gravity = Gravity.CENTER_VERTICAL
        linea.setBackgroundResource(R.drawable.fondo_fila_portapapeles)
        linea.setPadding(dp(12), 0, dp(2), 0)
        linea.minimumHeight = dp(48)
        linea.isClickable = true
        linea.isFocusable = true
        linea.setOnClickListener { oyente.alPegar(entrada.texto) }

        val texto = TextView(contexto)
        texto.text = entrada.texto
        texto.setTextColor(contexto.getColor(R.color.texto_tecla))
        texto.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        texto.maxLines = 2
        texto.ellipsize = TextUtils.TruncateAt.END
        texto.setPadding(0, dp(9), dp(8), dp(9))
        linea.addView(texto, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))

        val estrella = botonIcono(
            if (entrada.fijada) R.drawable.ic_estrella_llena else R.drawable.ic_estrella,
            if (entrada.fijada) R.string.portapapeles_quitar_fijado else R.string.portapapeles_fijar
        ) { oyente.alFijar(entrada.texto, !entrada.fijada) }
        estrella.imageTintList = ColorStateList.valueOf(
            contexto.getColor(
                if (entrada.fijada) R.color.acento else R.color.texto_tecla_secundario
            )
        )
        linea.addView(estrella)

        linea.addView(
            botonIcono(R.drawable.ic_papelera, R.string.portapapeles_borrar_uno) {
                oyente.alBorrar(entrada.texto)
            }
        )
        return linea
    }

    private fun mensaje(texto: Int): View {
        val aviso = TextView(contexto)
        aviso.text = contexto.getString(texto)
        aviso.setTextColor(contexto.getColor(R.color.texto_tecla_secundario))
        aviso.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        aviso.gravity = Gravity.CENTER
        aviso.setPadding(dp(20), dp(28), dp(20), dp(28))
        return aviso
    }

    private fun nota(): View {
        val aviso = TextView(contexto)
        aviso.text = contexto.getString(R.string.portapapeles_nota)
        aviso.setTextColor(contexto.getColor(R.color.texto_tecla_secundario))
        aviso.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
        aviso.setPadding(dp(12), dp(6), dp(12), dp(8))
        return aviso
    }

    private fun botonIcono(icono: Int, descripcion: Int, alTocar: () -> Unit): ImageButton {
        val boton = ImageButton(contexto)
        boton.setImageResource(icono)
        boton.contentDescription = contexto.getString(descripcion)
        boton.setBackgroundResource(R.drawable.fondo_accion)
        boton.imageTintList =
            ColorStateList.valueOf(contexto.getColor(R.color.texto_tecla_secundario))
        boton.scaleType = ImageView.ScaleType.FIT_CENTER
        boton.setPadding(dp(9), dp(9), dp(9), dp(9))
        boton.layoutParams = LinearLayout.LayoutParams(dp(40), dp(40))
        boton.setOnClickListener { alTocar() }
        return boton
    }

    /** Vaciar pide confirmación: el primer toque avisa, el segundo borra. */
    private fun pedirVaciar() {
        if (!confirmando) {
            confirmando = true
            botonVaciar.text = contexto.getString(R.string.portapapeles_confirmar_vaciar)
            botonVaciar.setTextColor(contexto.getColor(R.color.texto_error))
            confirmacion.postDelayed({ cancelarConfirmacion() }, ESPERA_CONFIRMACION_MS)
            return
        }
        confirmacion.removeCallbacksAndMessages(null)
        cancelarConfirmacion()
        oyente.alVaciar()
    }

    private fun cancelarConfirmacion() {
        confirmando = false
        if (!::botonVaciar.isInitialized) return
        botonVaciar.text = contexto.getString(R.string.portapapeles_vaciar)
        botonVaciar.setTextColor(contexto.getColor(R.color.texto_tecla_secundario))
    }

    private fun actualizarVaciar(hayRecientes: Boolean) {
        botonVaciar.isEnabled = hayRecientes
        botonVaciar.alpha = if (hayRecientes) 1f else 0.4f
    }

    private fun dp(valor: Int): Int =
        (valor * contexto.resources.displayMetrics.density).toInt()

    companion object {
        /** Alto visible de la lista; con más entradas se desplaza. */
        private const val ALTO_LISTA_DP = 232

        /** Cuánto dura el «¿Vaciar?» antes de volver a «Vaciar». */
        private const val ESPERA_CONFIRMACION_MS = 3000L
    }
}
