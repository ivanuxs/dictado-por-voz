package com.ivanuxs.skipkey

import android.content.Context
import android.graphics.Typeface
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView

/** Estado del botón «＋ / ✓» del final de la barra. */
enum class AccionBarra {
    /** No hay nada que guardar (ni palabra en curso, ni palabra que añadir). */
    OCULTA,

    /** La palabra en curso no está en el diccionario: un toque la guarda. */
    GUARDAR,

    /** La palabra en curso ya es del usuario: un toque la quita. */
    GUARDADA
}

/** Lo que la barra avisa cuando el usuario toca una de sus casillas. */
interface OyenteSugerencias {
    /** Tocar una sugerencia: se escribe en lugar de la palabra en curso. */
    fun alElegir(texto: String)

    /** Tocar «＋» o «✓»: guarda o quita la palabra que se está escribiendo. */
    fun alAccion()

    /** Tocar «↩»: deshace la corrección que se acaba de hacer. */
    fun alVolver()
}

/**
 * Franja de sugerencias encima del teclado (tres casillas) más un botón
 * estrecho al final para guardar la palabra en el diccionario.
 *
 * La franja ocupa SIEMPRE su alto: cuando no hay nada que ofrecer, las casillas
 * quedan invisibles pero con su sitio. Antes la fila pasaba a `GONE` en cuanto no
 * había sugerencias y eso obligaba a recolocar el teclado entero cada vez que
 * aparecían o desaparecían (medido el 2026-10-16: un fotograma de más por palabra).
 *
 * Desde 2026-11-18 hay dos cosas fuera de las sugerencias:
 *  - el botón «＋ / ✓» del final, que guarda o quita la palabra en curso (antes
 *    era un dedo largo invisible);
 *  - la casilla «↩», que ocupa el primer sitio mientras dura el aviso de
 *    deshacer una corrección y deja las otras dos para sugerencias.
 */
class BarraSugerencias(
    private val contexto: Context,
    private val oyente: OyenteSugerencias
) {

    private val fila = LinearLayout(contexto)
    private val casillas = ArrayList<TextView>(CASILLAS)
    private lateinit var casillaAccion: TextView

    /**
     * Casilla que en este momento enseña el «↩» de deshacer (-1 si ninguna).
     *
     * Se guarda porque al tocar una casilla hay que distinguir «elegir esta
     * sugerencia» de «deshacer la corrección»: el texto solo no basta.
     */
    private var indiceVolver = -1

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
        repeat(CASILLAS) { indice ->
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
            casilla.setOnClickListener {
                if (indice == indiceVolver) {
                    oyente.alVolver()
                } else {
                    oyente.alElegir(casilla.text.toString())
                }
            }
            fila.addView(casilla)
            casillas.add(casilla)
        }
        // Botón de guardar: estrecho y siempre en el mismo sitio, para que no se
        // confunda con una sugerencia y no haga falta adivinar un dedo largo.
        casillaAccion = TextView(contexto)
        casillaAccion.layoutParams = LinearLayout.LayoutParams(
            contexto.resources.getDimensionPixelSize(R.dimen.ancho_accion),
            alto
        )
        casillaAccion.gravity = Gravity.CENTER
        casillaAccion.setTextSize(16f)
        casillaAccion.typeface = medium
        casillaAccion.setTextColor(contexto.getColor(R.color.acento))
        casillaAccion.setBackgroundResource(R.drawable.fondo_sugerencia)
        casillaAccion.isClickable = true
        casillaAccion.visibility = View.INVISIBLE
        casillaAccion.setOnClickListener { oyente.alAccion() }
        fila.addView(casillaAccion)
        return fila
    }

    /**
     * Pinta la barra.
     *
     * `volver` es el aviso de deshacer (si acaba de haber una corrección): ocupa
     * la primera casilla y deja dos para sugerencias. `accion` decide el botón
     * del final.
     */
    fun mostrar(sugerencias: List<String>, volver: String?, accion: AccionBarra) {
        val textos = ArrayList<String>(CASILLAS + 1)
        if (volver != null) textos.add(volver)
        textos.addAll(sugerencias)
        indiceVolver = if (volver != null) 0 else -1

        for (i in casillas.indices) {
            val casilla = casillas[i]
            val texto = textos.getOrNull(i).orEmpty()
            casilla.text = texto
            casilla.visibility = if (texto.isBlank()) View.INVISIBLE else View.VISIBLE
            // El «↩» va en el color de acento: no es una sugerencia más.
            casilla.setTextColor(
                contexto.getColor(if (i == indiceVolver) R.color.acento else R.color.texto_tecla)
            )
        }

        when (accion) {
            AccionBarra.OCULTA -> {
                casillaAccion.text = ""
                casillaAccion.contentDescription = null
                casillaAccion.visibility = View.INVISIBLE
            }
            AccionBarra.GUARDAR -> {
                casillaAccion.text = ETIQUETA_GUARDAR
                casillaAccion.contentDescription = contexto.getString(R.string.barra_guardar)
                casillaAccion.visibility = View.VISIBLE
            }
            AccionBarra.GUARDADA -> {
                casillaAccion.text = ETIQUETA_GUARDADA
                casillaAccion.contentDescription = contexto.getString(R.string.barra_guardada)
                casillaAccion.visibility = View.VISIBLE
            }
        }
    }

    private companion object {
        /** Tres casillas: el «↩» de deshacer y dos sugerencias, o tres sugerencias. */
        const val CASILLAS = 3

        /** Símbolo del botón para añadir la palabra en curso al diccionario. */
        const val ETIQUETA_GUARDAR = "＋"

        /** Símbolo del botón cuando la palabra en curso ya es del usuario. */
        const val ETIQUETA_GUARDADA = "✓"
    }
}
