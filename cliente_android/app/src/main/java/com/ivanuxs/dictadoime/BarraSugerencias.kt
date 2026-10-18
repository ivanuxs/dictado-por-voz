package com.ivanuxs.dictadoime

import android.content.Context
import android.graphics.Typeface
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView

/** Lo que la barra avisa cuando el usuario toca una de sus casillas. */
interface OyenteSugerencias {
    /** Tocar una sugerencia: se escribe en lugar de la palabra en curso. */
    fun alElegir(texto: String)

    /** Mantener pulsada: añade la palabra al diccionario propio, o la quita. */
    fun alMantener(texto: String)
}

/**
 * Franja de sugerencias encima del teclado (tres casillas).
 *
 * La franja ocupa SIEMPRE su alto: cuando no hay nada que ofrecer, las casillas
 * quedan invisibles pero con su sitio. Antes la fila pasaba a `GONE` en cuanto no
 * había sugerencias y eso obligaba a recolocar el teclado entero cada vez que
 * aparecían o desaparecían (medido el 2026-10-16: un fotograma de más por palabra).
 */
class BarraSugerencias(
    private val contexto: Context,
    private val oyente: OyenteSugerencias
) {

    private val fila = LinearLayout(contexto)
    private val casillas = ArrayList<TextView>(CASILLAS)

    /** Construye la vista de la barra. */
    fun construir(): View {
        fila.orientation = LinearLayout.HORIZONTAL
        fila.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )
        // Fondo propio y una línea fina entre casillas: sin ellas, tres palabras
        // seguidas se leían como un solo texto suelto sobre el teclado.
        fila.setBackgroundColor(contexto.getColor(R.color.fondo_sugerencias))
        fila.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE)
        fila.dividerDrawable = contexto.getDrawable(R.drawable.separador)
        val alto = contexto.resources.getDimensionPixelSize(R.dimen.alto_sugerencias)
        val medium = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        repeat(CASILLAS) {
            val casilla = TextView(contexto)
            casilla.layoutParams = LinearLayout.LayoutParams(0, alto, 1f)
            casilla.gravity = Gravity.CENTER
            casilla.maxLines = 1
            casilla.ellipsize = TextUtils.TruncateAt.END
            casilla.setTextSize(15f)
            casilla.setTextColor(contexto.getColor(R.color.texto_tecla))
            casilla.typeface = medium
            // Aclara la casilla al tocarla (antes no había ninguna respuesta).
            casilla.setBackgroundResource(R.drawable.fondo_sugerencia)
            casilla.isClickable = true
            // Invisible, NO `GONE`: así la fila mide su alto desde el principio y
            // nunca cambia de tamaño al aparecer las sugerencias.
            casilla.visibility = View.INVISIBLE
            casilla.setOnClickListener { oyente.alElegir(casilla.text.toString()) }
            casilla.setOnLongClickListener {
                oyente.alMantener(casilla.text.toString())
                true
            }
            fila.addView(casilla)
            casillas.add(casilla)
        }
        return fila
    }

    /** Muestra las sugerencias que quepan (la barra mantiene siempre su alto). */
    fun mostrar(lista: List<String>) {
        for (i in casillas.indices) {
            val casilla = casillas[i]
            val texto = lista.getOrNull(i).orEmpty()
            casilla.text = texto
            casilla.visibility = if (texto.isBlank()) View.INVISIBLE else View.VISIBLE
        }
    }

    private companion object {
        /** Tres casillas: la corrección y dos sugerencias. */
        const val CASILLAS = 3
    }
}
