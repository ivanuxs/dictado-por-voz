package com.ivanuxs.dictadoime

import android.content.Context
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.Button
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout

/** Qué hacer cuando se pulsa una tecla del teclado de letras. */
interface OyenteTeclas {
    /** Una letra, un número, un símbolo o un espacio. */
    fun alEscribir(texto: String)

    /** Tecla de borrar (⌫). */
    fun alBorrar()

    /** Salto de línea (↵). */
    fun alEnter()

    /** Abrir el selector de teclados del sistema (respaldo: ir a Gboard). */
    fun alSelectorTeclados()
}

/**
 * Teclado de letras propio, para editar sin salir a Gboard.
 *
 * Se construye en código y no en XML porque las tres páginas (letras, símbolos
 * básicos y símbolos ampliados) y el estado de mayúsculas comparten las mismas
 * teclas: cambiar de página o de mayúsculas es volver a pintar las etiquetas,
 * sin duplicar layouts.
 *
 * Anchos: cada fila suma 10, así que las teclas normales valen 1 y las anchas
 * (⇧, ⌫, ?123, ABC, ↵) 1,5 o más. La barra espaciadora se queda con lo que
 * sobraba del micrófono: media fila.
 */
class TecladoLetras(
    private val contexto: Context,
    private val ajustes: Ajustes,
    private val oyente: OyenteTeclas
) {

    /** Páginas del teclado: letras, símbolos básicos y símbolos ampliados. */
    private enum class Pagina { LETRAS, SIMBOLOS, MAS }

    private var pagina = Pagina.LETRAS
    private var mayusculas = false   // la próxima letra sale en mayúscula
    private var fijas = false        // mayúsculas fijas (⇪)
    // Los ajustes se leen UNA sola vez: al estar cifrados, leerlos en cada tecla
    // costaba varios milisegundos y se notaba como un pequeño retardo al pulsar.
    private val altoTecla = ajustes.alturaTecla

    // El mismo alto, en píxeles: es el que manda en toda la rejilla (filas, teclas
    // y tamaños de rótulo).
    private val altoTeclaPx = (altoTecla * contexto.resources.displayMetrics.density).toInt()
    private val mostrarNumeros = ajustes.filaNumeros
    private val vibrador = if (ajustes.vibrar) {
        contexto.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    } else {
        null
    }

    private val contenedor = LinearLayout(contexto)
    private val repeticiones = Handler(Looper.getMainLooper())

    /**
     * Las teclas del pintado actual, con su nombre interno, para poder poner al
     * día sus etiquetas (⇧/⇪) sin volver a crear la rejilla: recrear las ~44
     * vistas costaba un fotograma larguísimo (medido el 2026-10-16: 60-104 ms de
     * hilo principal al abrir el modo letras, y picos en cada cambio de ⇧).
     */
    private val teclas = ArrayList<Pair<String, View>>()

    /** El efecto de vibración se crea UNA vez y se reutiliza en cada tecla. */
    private val efectoVibracion by lazy {
        VibrationEffect.createOneShot(VIBRACION_MS, VibrationEffect.DEFAULT_AMPLITUDE)
    }

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

    /**
     * Si ahora mismo el teclado escribiría en mayúscula (⇧ de una letra o ⇪ fijas).
     * Lo pregunta el servicio para saber cómo escribir una sugerencia.
     */
    fun quiereMayuscula(): Boolean = mayusculas || fijas

    /**
     * El servicio avisa de si la próxima palabra va en mayúscula (empezó oración,
     * el campo la pide…). Es una SUGERENCIA: si el usuario fijó las mayúsculas no
     * se toca nada, así que apagar el ⇧ a mano manda hasta la frase o el campo
     * siguiente.
     */
    fun sugerirMayuscula(auto: Boolean) {
        if (fijas || mayusculas == auto) return
        mayusculas = auto
        etiquetasDespues()
    }

    // --- Pintado ---------------------------------------------------------

    private fun pintar() {
        contenedor.removeAllViews()
        teclas.clear()
        when (pagina) {
            // La fila de números arriba es opcional (en las páginas de símbolos ya está).
            Pagina.LETRAS -> {
                if (mostrarNumeros) contenedor.addView(construirFila(FILA_NUMEROS))
                for (fila in FILAS_LETRAS) contenedor.addView(construirFila(fila))
            }
            Pagina.SIMBOLOS -> for (fila in FILAS_SIMBOLOS) contenedor.addView(construirFila(fila))
            Pagina.MAS -> for (fila in FILAS_MAS) contenedor.addView(construirFila(fila))
        }
    }

    /**
     * Repinta al terminar el toque. Quitar y volver a crear las vistas dentro del
     * propio evento táctil es frágil (la vista pulsada desaparece a mitad del
     * gesto); con un frame de espera no se nota y es seguro.
     */
    private fun pintarDespues() {
        contenedor.post { pintar() }
    }

    /**
     * Pone al día solo las etiquetas, al terminar el toque.
     *
     * Es lo único que cambia al encender o apagar las mayúsculas: antes se volvía
     * a construir la rejilla entera (44 vistas nuevas) y eso se veía como un tirón
     * al empezar cada frase.
     */
    private fun etiquetasDespues() {
        contenedor.post { actualizarEtiquetas() }
    }

    private fun actualizarEtiquetas() {
        if (teclas.isEmpty()) {
            // Sin rejilla pintada (no debería pasar): se pinta entera.
            pintar()
            return
        }
        for ((nombre, vista) in teclas) {
            if (nombre == "MAYUS") {
                // ⇧/⇪ encendido: la tecla se tiñe y el glifo pasa al acento. Antes
                // el fondo seguía igual y no se sabía si las mayúsculas estaban
                // puestas salvo mirando el carácter ⇧/⇪. Solo se tocan los dos
                // atributos, sin recrear la rejilla.
                vista.background = contexto.getDrawable(fondoDeTecla(nombre))
                (vista as? Button)?.setTextColor(contexto.getColor(colorDeTecla(nombre)))
            }
            val boton = vista as? Button ?: continue
            val visible = etiqueta(nombre)
            if (boton.text.toString() != visible) {
                boton.text = visible
                boton.setTextSize(TypedValue.COMPLEX_UNIT_PX, tamanoTexto(visible))
            }
        }
    }

    private fun construirFila(fila: List<Pair<String, Float>>): View {
        val linea = LinearLayout(contexto)
        linea.orientation = LinearLayout.HORIZONTAL
        linea.weightSum = 10f
        // Alto de fila EXACTO. Antes la fila medía lo que pidiera su tecla más
        // alta (los glifos ⇧ ⌫ ↵ piden más) y las demás quedaban más cortas:
        // esa franja de fondo era la que se veía fea al lado de esas filas.
        linea.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, altoTeclaPx
        )
        for ((nombre, peso) in fila) {
            linea.addView(construirTecla(nombre, peso))
        }
        return linea
    }

    private fun construirTecla(nombre: String, peso: Float): View {
        val visible = etiqueta(nombre)
        val tecla: View
        if (nombre == "ENTER") {
            // La flecha de ↵ va DIBUJADA, no escrita: el glifo «↵» de la tipografía
            // es diminuto y no hay manera de agrandarlo sin que se salga de la
            // tecla. El dibujo (24dp) se estira hasta llenar la tecla, así que
            // queda más del doble de grande que el glifo de antes.
            val boton = ImageButton(contexto)
            boton.setImageResource(R.drawable.ic_enter)
            boton.scaleType = ImageView.ScaleType.FIT_CENTER
            tecla = boton
        } else {
            val boton = Button(contexto)
            boton.text = visible
            boton.isAllCaps = false
            // En PÍXELES, no en sp: el rótulo se mide sobre el alto real de la
            // tecla (ver `tamanoTexto`), así no depende del «tamaño de fuente»
            // del celular.
            boton.setTextSize(TypedValue.COMPLEX_UNIT_PX, tamanoTexto(visible))
            boton.setTextColor(contexto.getColor(colorDeTecla(nombre)))
            boton.gravity = Gravity.CENTER
            // Las teclas de función van en «sans-serif-medium»: es la diferencia
            // tipográfica que las separa de las letras, que siguen en la fuente
            // normal del sistema.
            boton.typeface = if (nombre in TECLAS_ESPECIALES) {
                Typeface.create("sans-serif-medium", Typeface.NORMAL)
            } else if (visible.length <= 2) {
                Typeface.DEFAULT
            } else {
                Typeface.DEFAULT_BOLD
            }
            boton.includeFontPadding = false
            boton.maxLines = 1
            tecla = boton
        }
        // Fondo según el papel de la tecla: espacio con contorno, función oscura
        // (y verde cuando está encendida) y letras en el gris normal.
        tecla.background = contexto.getDrawable(fondoDeTecla(nombre))
        // Sin la animación de elevación de los Button: responde al instante.
        tecla.stateListAnimator = null
        // Los Button traen tamaños mínimos que romperían la rejilla.
        tecla.minimumWidth = 0
        tecla.minimumHeight = 0
        tecla.setPadding(0, 0, 0, 0)

        // La tecla ocupa TODO el alto de su fila: así todas las de una fila miden
        // exactamente lo mismo, aunque su rótulo pida más o menos sitio.
        val parametros = LinearLayout.LayoutParams(
            0, LinearLayout.LayoutParams.MATCH_PARENT, peso
        )
        parametros.setMargins(0, 0, 0, 0)
        tecla.layoutParams = parametros

        configurarPulsacion(tecla, nombre)
        // Se guarda para poder repintar su etiqueta sin recrear la rejilla.
        teclas.add(nombre to tecla)
        return tecla
    }

    /**
     * Tamaño del rótulo EN PÍXELES, proporcional al alto real de la tecla.
     *
     * Va en píxeles a propósito, no en `sp`: en `sp` el tamaño crecía con el
     * «tamaño de fuente» del celular y los glifos grandes (⇧ ⌫ ↵) pedían más alto
     * que la tecla. La fila crecía para que cupieran, las letras de al lado
     * quedaban cortas y se veía una franja de fondo alrededor de esas filas.
     *
     * Los factores dejan margen: el alto de línea de una tipografía ronda 1,2-1,5
     * veces su tamaño, así que todo cabe dentro de la tecla.
     */
    private fun tamanoTexto(visible: String): Float = when {
        visible.isEmpty() -> altoTeclaPx * 0.42f
        SIMBOLOS_GRANDES.contains(visible) -> altoTeclaPx * 0.62f
        visible.length == 1 -> altoTeclaPx * 0.52f
        visible.length <= 2 -> altoTeclaPx * 0.46f
        else -> altoTeclaPx * 0.38f
    }
    /** Etiqueta visible de una tecla según la página y las mayúsculas. */
    private fun etiqueta(nombre: String): String = when (nombre) {
        "MAYUS" -> if (fijas) "⇪" else "⇧"
        "BORRAR" -> "⌫"
        "ENTER" -> "↵"
        "espacio" -> ""
        "coma" -> ","
        "punto" -> "."
        "SIMBOLOS", "BASICO" -> "?123"
        "MAS" -> "más"
        "LETRAS" -> "ABC"
        // La mayúscula solo afecta a letras: los símbolos no cambian de forma.
        else -> if ((mayusculas || fijas) && esLetra(nombre)) nombre.uppercase() else nombre
    }

    private fun esLetra(nombre: String) = nombre.length == 1 && nombre[0].isLetter()

    /**
     * Fondo de una tecla según su papel: la espaciadora es la única con
     * contorno; las de función van más oscuras y, si el ⇧ está encendido, en
     * verde tenue; las letras, en el gris normal.
     */
    private fun fondoDeTecla(nombre: String): Int = when {
        nombre == "MAYUS" && (mayusculas || fijas) -> R.drawable.forma_tecla_activa
        nombre == "espacio" -> R.drawable.fondo_espacio
        nombre in TECLAS_ESPECIALES -> R.drawable.fondo_tecla_especial
        else -> R.drawable.fondo_boton
    }

    /** Color del rótulo: acento cuando ⇧/⇪ está encendido, atenuado en las teclas
     *  de función y claro en las letras. */
    private fun colorDeTecla(nombre: String): Int = when {
        nombre == "MAYUS" && (mayusculas || fijas) -> R.color.acento
        nombre in TECLAS_ESPECIALES -> R.color.texto_tecla_secundario
        else -> R.color.texto_tecla
    }

    // --- Pulsaciones -----------------------------------------------------

    private fun configurarPulsacion(tecla: View, nombre: String) {
        when (nombre) {
            "BORRAR" -> prepararBorrado(tecla)
            "MAYUS" -> prepararMayusculas(tecla)
            // Estos dos actúan al soltar para no perder el pulsado largo.
            "SIMBOLOS", "LETRAS" -> prepararCambioDePagina(tecla, nombre)
            else -> prepararToqueInmediato(tecla, nombre)
        }
    }

    /**
     * El carácter entra al APOYAR el dedo, no al soltarlo. Eso es lo que hace que
     * la tecla se sienta instantánea: antes se esperaba al soltado (ACTION_UP) y
     * se notaba un pequeño retardo.
     *
     * Se cubren además los dos casos que hacían perder pulsaciones al teclear
     * rápido: un segundo dedo sobre la MISMA tecla (letras dobles: ll, rr, ss…),
     * que llega como ACTION_POINTER_DOWN; y un toque del que el sistema no llegó
     * a entregar el ACTION_DOWN, que se recupera al soltar.
     */
    private fun prepararToqueInmediato(tecla: View, nombre: String) {
        var escrito = false
        tecla.setOnTouchListener { vista, evento ->
            when (evento.actionMasked) {
                MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                    vista.isPressed = true
                    escrito = true
                    pulsar(nombre)
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (!escrito) pulsar(nombre)
                    escrito = false
                    vista.isPressed = false
                }
            }
            true
        }
    }

    /**
     * El ⇧, como el de Gboard: **un toque** enciende o apaga la mayúscula de una
     * letra, y **mantenerlo pulsado** fija las mayúsculas (⇪).
     *
     * Actúa al soltar (no al apoyar, como las letras): aquí no se pierde ningún
     * carácter y hace falta esperar al soltado para distinguir el toque del
     * pulsado largo. Un toque desde el inicio -cuando el teclado arranca en
     * mayúscula automática- deja la letra en minúscula, que es justo lo que hace
     * falta para escribir un correo o cualquier cosa que empiece en minúscula.
     */
    private fun prepararMayusculas(tecla: View) {
        var larga = false
        val fijar = Runnable {
            larga = true
            pulsar("MAYUS_FIJAS")
        }
        tecla.setOnTouchListener { vista, evento ->
            when (evento.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    vista.isPressed = true
                    larga = false
                    repeticiones.postDelayed(fijar, ViewConfiguration.getLongPressTimeout().toLong())
                }
                MotionEvent.ACTION_UP -> {
                    repeticiones.removeCallbacks(fijar)
                    vista.isPressed = false
                    if (!larga) pulsar("MAYUS")
                }
                MotionEvent.ACTION_CANCEL -> {
                    repeticiones.removeCallbacks(fijar)
                    vista.isPressed = false
                }
            }
            true
        }
    }

    /** Cambio de página al soltar; mantener pulsado abre el selector de teclados. */
    private fun prepararCambioDePagina(tecla: View, nombre: String) {
        tecla.setOnClickListener { pulsar(nombre) }
        tecla.setOnLongClickListener {
            oyente.alSelectorTeclados()
            true
        }
    }

    /** Borra al apoyar y, si se mantiene pulsado, sigue borrando hasta soltar. */
    private fun prepararBorrado(tecla: View) {
        val repetir = object : Runnable {
            override fun run() {
                oyente.alBorrar()
                repeticiones.postDelayed(this, 80)
            }
        }
        var borrado = false
        tecla.setOnTouchListener { vista, evento ->
            when (evento.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    vista.isPressed = true
                    borrado = true
                    pulsar("BORRAR")
                    repeticiones.postDelayed(repetir, 300)
                }
                MotionEvent.ACTION_POINTER_DOWN -> {
                    vista.isPressed = true
                    pulsar("BORRAR")
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (!borrado) pulsar("BORRAR")
                    borrado = false
                    vista.isPressed = false
                    repeticiones.removeCallbacks(repetir)
                }
            }
            true
        }
    }

    private fun pulsar(nombre: String) {
        when (nombre) {
            "MAYUS" -> {
                // Un toque apaga y enciende la mayúscula de UNA letra. Desde ⇪, un
                // toque deja las minúsculas (no pasa por ⇧).
                if (fijas) {
                    fijas = false
                    mayusculas = false
                } else {
                    mayusculas = !mayusculas
                }
                etiquetasDespues()
            }

            // Pulsado largo en ⇧: mayúsculas fijas, encender y apagar.
            "MAYUS_FIJAS" -> {
                fijas = !fijas
                mayusculas = false
                etiquetasDespues()
            }

            "BORRAR" -> oyente.alBorrar()
            "ENTER" -> oyente.alEnter()
            "espacio" -> {
                // Igual que con las letras: primero se apaga el ⇧ de un solo uso y
                // después se avisa al servicio, porque al cerrar la palabra puede
                // volver a encenderlo (campos con mayúscula en cada palabra).
                bajarMayusculas()
                oyente.alEscribir(" ")
            }

            "SIMBOLOS", "BASICO" -> cambiarDePagina(Pagina.SIMBOLOS)
            "MAS" -> cambiarDePagina(Pagina.MAS)
            "LETRAS" -> cambiarDePagina(Pagina.LETRAS)

            else -> {
                // La etiqueta se lee ANTES de apagar el ⇧: si es una letra con la
                // mayúscula de un solo uso, sale en mayúscula una vez. Y el ⇧ se
                // apaga antes de avisar al servicio, porque al cerrar la oración
                // (`.` `!` `?`) el servicio lo vuelve a encender para la palabra
                // siguiente: apagarlo después borraría esa sugerencia.
                val visible = etiqueta(nombre)
                bajarMayusculas()
                oyente.alEscribir(visible)
            }
        }
        // La vibración va AL FINAL: primero el carácter, que es lo que el usuario
        // ve. Antes se avisaba al vibrador antes de escribir y el texto llegaba
        // después de ese aviso.
        vibrar()
    }

    /** Cambia de página y quita las mayúsculas (en los símbolos no aplican). */
    private fun cambiarDePagina(nueva: Pagina) {
        pagina = nueva
        mayusculas = false
        fijas = false
        pintarDespues()
    }

    /** Tras escribir, la mayúscula de un solo uso se apaga (⇪ se mantiene). */
    private fun bajarMayusculas() {
        if (mayusculas) {
            mayusculas = false
            etiquetasDespues()
        }
    }

    private fun vibrar() {
        vibrador?.vibrate(efectoVibracion)
    }

    companion object {
        /** Vibración al pulsar, en milisegundos. */
        private const val VIBRACION_MS = 15L

        /** Etiquetas que la tipografía dibuja pequeñas y por eso van más grandes.
         *  El ↵ no está: esa tecla lleva un dibujo (ver `construirTecla`). */
        private val SIMBOLOS_GRANDES = setOf("⇧", "⇪", "⌫")

        /** Teclas de FUNCIÓN: fondo más oscuro y rótulo atenuado, para separarlas
         *  de las letras sin romper el estilo plano del teclado. */
        private val TECLAS_ESPECIALES = setOf(
            "MAYUS", "BORRAR", "SIMBOLOS", "BASICO", "MAS", "LETRAS", "ENTER"
        )

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
            // Sin la tecla 🎤 (el micrófono está en la franja de arriba): ese ancho
            // se lo queda la barra espaciadora, que ahora ocupa media fila.
            listOf("SIMBOLOS" to 1.5f, "coma" to 1f, "espacio" to 5f,
                "punto" to 1f, "ENTER" to 1.5f)
        )

        /** Página de símbolos básicos: signos de interrogación y exclamación de los
         *  dos idiomas, vocales con tilde y los signos de siempre. */
        private val FILAS_SIMBOLOS = listOf(
            listOf("1" to 1f, "2" to 1f, "3" to 1f, "4" to 1f, "5" to 1f,
                "6" to 1f, "7" to 1f, "8" to 1f, "9" to 1f, "0" to 1f),
            listOf("?" to 1f, "¿" to 1f, "!" to 1f, "¡" to 1f, "'" to 1f,
                "\u0022" to 1f, "-" to 1f, "_" to 1f, "@" to 1f, "#" to 1f),
            listOf("á" to 1f, "é" to 1f, "í" to 1f, "ó" to 1f, "ú" to 1f,
                "(" to 1f, ")" to 1f, ":" to 1f, ";" to 1f, "$" to 1f),
            listOf("LETRAS" to 1.5f, "MAS" to 1f, "espacio" to 3.5f,
                "coma" to 1f, "punto" to 1f,
                "BORRAR" to 1f, "ENTER" to 1f)
        )

        /** Página de símbolos ampliados: matemáticas, monedas y llaves. */
        private val FILAS_MAS = listOf(
            listOf("~" to 1f, "`" to 1f, "|" to 1f, "•" to 1f, "√" to 1f,
                "÷" to 1f, "×" to 1f, "π" to 1f, "∆" to 1f, "^" to 1f),
            listOf("+" to 1f, "=" to 1f, "<" to 1f, ">" to 1f, "[" to 1f,
                "]" to 1f, "{" to 1f, "}" to 1f, "§" to 1f, "°" to 1f),
            listOf("/" to 1f, "\u005C" to 1f, "%" to 1f, "&" to 1f, "*" to 1f,
                "€" to 1f, "£" to 1f, "¢" to 1f, "¥" to 1f, "¬" to 1f),
            listOf("LETRAS" to 1.5f, "BASICO" to 1f, "espacio" to 3.5f,
                "coma" to 1f, "punto" to 1f,
                "BORRAR" to 1f, "ENTER" to 1f)
        )
    }
}
