package com.ivanuxs.skipkey

import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * «Mis palabras»: lo que el teclado ha guardado del usuario en el perfil activo.
 *
 * Existe porque hasta 2026-11-18 el diccionario solo se podía tocar desde la
 * barra (con un dedo largo invisible) y no había forma de VER qué se había
 * guardado. Aquí están:
 *
 *  - las palabras propias que NO vienen en la lista base (las 49 600 de
 *    fábrica no se listan: no hay nada que hacer con ellas);
 *  - las correcciones aprendidas del dictado, «lo que dijo el motor → lo que
 *    querías».
 *
 * Cada fila lleva una ✕. Lo que se borra aquí no se puede recuperar.
 */
class PalabrasActivity : AppCompatActivity() {

    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var diccionario: Diccionario
    private lateinit var listaPalabras: LinearLayout
    private lateinit var listaCorrecciones: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_palabras)
        val ajustes = Ajustes(this)
        diccionario = Diccionario(this, ajustes.perfil)
        listaPalabras = findViewById(R.id.lista_palabras)
        listaCorrecciones = findViewById(R.id.lista_correcciones)
        findViewById<TextView>(R.id.texto_perfil).text =
            getString(R.string.palabras_subtitulo, ajustes.perfil)
        findViewById<Button>(R.id.boton_volver).setOnClickListener { finish() }
        // Hasta que la lista base no esté cargada no se puede saber qué palabras
        // son «tuyas» y cuáles vienen de fábrica.
        alcance.launch {
            diccionario.cargar()
            pintar()
        }
    }

    override fun onDestroy() {
        // NO se llama a `descartar()`: dejaría de escribirse el borrado que esté
        // esperando su turno (el diccionario guarda con 1,5 s de retardo y esa
        // escritura sigue viva en su propio alcance).
        alcance.cancel()
        super.onDestroy()
    }

    /** Repinta las dos listas (se llama al entrar y tras cada borrado). */
    private fun pintar() {
        listaPalabras.removeAllViews()
        val palabras = diccionario.palabrasAprendidas()
        if (palabras.isEmpty()) {
            listaPalabras.addView(ayuda(getString(R.string.palabras_vacio)))
        } else {
            for ((palabra, veces) in palabras) {
                listaPalabras.addView(
                    fila(
                        titulo = palabra,
                        detalle = usos(veces),
                        descripcionBorrar = getString(R.string.palabras_borrar, palabra)
                    ) {
                        diccionario.olvidar(palabra)
                        avisar(getString(R.string.palabras_borrada, palabra))
                        pintar()
                    }
                )
            }
        }

        listaCorrecciones.removeAllViews()
        val correcciones = diccionario.correccionesAprendidas()
        if (correcciones.isEmpty()) {
            listaCorrecciones.addView(ayuda(getString(R.string.palabras_sin_correcciones)))
        } else {
            for ((antes, despues, veces) in correcciones) {
                listaCorrecciones.addView(
                    fila(
                        titulo = "$antes → $despues",
                        detalle = usos(veces),
                        descripcionBorrar = getString(
                            R.string.palabras_correccion_borrar, antes, despues
                        )
                    ) {
                        diccionario.olvidarCorreccion(antes, despues)
                        avisar(getString(R.string.palabras_correccion_borrada, antes, despues))
                        pintar()
                    }
                )
            }
        }
    }

    /** «1 uso» / «7 usos». */
    private fun usos(veces: Int): String =
        getString(if (veces == 1) R.string.palabras_usos_uno else R.string.palabras_usos, veces)

    /** Una fila: el texto, sus usos y la ✕ que lo quita. */
    private fun fila(
        titulo: String,
        detalle: String,
        descripcionBorrar: String,
        borrar: () -> Unit
    ): View {
        val fila = LinearLayout(this)
        fila.orientation = LinearLayout.HORIZONTAL
        fila.gravity = Gravity.CENTER_VERTICAL
        fila.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )

        val textos = LinearLayout(this)
        textos.orientation = LinearLayout.VERTICAL
        textos.layoutParams = LinearLayout.LayoutParams(
            0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f
        )
        val nombre = TextView(this)
        nombre.text = titulo
        nombre.setTextColor(getColor(R.color.texto_tecla))
        nombre.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        textos.addView(nombre)
        val cuenta = TextView(this)
        cuenta.text = detalle
        cuenta.setTextColor(getColor(R.color.texto_tecla_secundario))
        cuenta.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f)
        textos.addView(cuenta)
        fila.addView(textos)

        val quitar = TextView(this)
        quitar.text = "✕"
        quitar.contentDescription = descripcionBorrar
        quitar.gravity = Gravity.CENTER
        quitar.setTextColor(getColor(R.color.acento))
        quitar.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
        quitar.isClickable = true
        // 44 dp de área táctil, el mínimo recomendado de accesibilidad.
        val lado = dp(TOQUE_DP)
        quitar.layoutParams = LinearLayout.LayoutParams(lado, lado)
        quitar.setBackgroundResource(R.drawable.fondo_sugerencia)
        quitar.setOnClickListener { borrar() }
        fila.addView(quitar)
        return fila
    }

    /** Texto de ayuda gris para una lista vacía. */
    private fun ayuda(texto: String): View {
        val vista = TextView(this)
        vista.text = texto
        vista.setTextColor(getColor(R.color.texto_tecla_secundario))
        vista.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        vista.setLineSpacing(0f, 1.25f)
        vista.setPadding(0, dp(6), 0, dp(6))
        return vista
    }

    private fun dp(valor: Int): Int = (valor * resources.displayMetrics.density).toInt()

    private fun avisar(mensaje: String) {
        Toast.makeText(this, mensaje, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        /** Área táctil mínima de la ✕ (dp). */
        const val TOQUE_DP = 44
    }
}
