package com.ivanuxs.skipkey

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.inputmethodservice.InputMethodService
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.text.InputType
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
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
    private lateinit var botonPortapapeles: ImageButton
    private lateinit var botonTraducir: ImageButton
    private lateinit var botonAjustes: ImageButton
    private lateinit var raizTeclado: ViewGroup
    private lateinit var portapapeles: ClipboardManager
    private lateinit var diccionario: Diccionario
    private lateinit var barra: BarraSugerencias
    private lateinit var teclado: TecladoLetras
    private lateinit var historial: Portapapeles
    private lateinit var nativo: ReconocedorNativo
    private var panel: PanelPortapapeles? = null
    private var panelAbierto = false

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
    private var motorNativo = true

    /** Idioma al que traduce el botón 🌐 (se lee al abrir cada campo). */
    private var idiomaTraduccion = "en"

    /** El campo es de contraseña: aquí no se ofrece ni se guarda el portapapeles. */
    private var enCampoSecreto = false

    /**
     * El campo admite TEXTO EN COMPOSICIÓN (todos los de texto; no los TYPE_NULL).
     *
     * Se decide al abrir el campo: en los que no son de texto se escribe
     * confirmado, que es lo que entienden.
     */
    private var admiteComposicion = true

    /** Hay un recálculo de la barra de sugerencias esperando al siguiente fotograma. */
    private var barraPendiente = false

    /** Lo copiado que se ofrece en la franja (null si no hay nada que ofrecer). */
    private var copiado: String? = null

    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var tarea: Job? = null
    private var sesion: SesionViva? = null
    private var largoInsertado = 0

    /** Cliente del perfil de la cuenta (diccionario y ajustes sincronizados). */
    private val perfilCuenta: ClientePerfil by lazy { ClientePerfil(this) }

    /**
     * Tramos del dictado vivo ya escritos en el campo.
     *
     * Cada parcial del puente es un TRAMO NUEVO (una frase), no el texto
     * acumulado: el texto final es la unión de todos. Hay que guardarlos para
     * poder enseñar el dictado completo y no solo el último tramo.
     */
    private val tramosVivos = ArrayList<String>()

    // --- Estado del diccionario (sugerencias y corrección) ----------------

    /** Palabra que se está escribiendo, en minúsculas. */
    private val palabra = StringBuilder()

    /**
     * La misma palabra en curso, tal como se escribió (con sus mayúsculas).
     *
     * `palabra` va en minúsculas para consultar el diccionario, pero al cerrar
     * la palabra hay que guardar lo que el usuario escribió: si no, «CallBy» se
     * aprende como «Callby» (lo reportó el usuario el 2026-11-18).
     */
    private var palabraVisible = StringBuilder()

    /**
     * Las letras de la palabra en curso que ya se mandaron como COMPOSICIÓN.
     *
     * Es exactamente lo que el campo considera provisional y lo que
     * `setComposingText` reemplaza en cada tecla. Solo cuenta lo que tecleamos
     * nosotros: si el cursor se metió en medio de una palabra que ya estaba
     * escrita, esas letras no se vuelven a mandar (se duplicarían).
     */
    private val compuesta = StringBuilder()

    /** Palabras ya terminadas en este campo (la última alimenta los bigramas). */
    private val terminadas = ArrayDeque<String>()

    /** La palabra en curso empezó con mayúscula. */
    private var capitalizada = false

    /** La próxima palabra abre oración (para la mayúscula automática). */
    private var capitalizarOracion = true

    /** Cuándo escribimos por última vez nosotros (para distinguir cambios ajenos). */
    private var ultimaEdicion = 0L

    /** Autocorrección recién hecha, para poder deshacerla desde la barra. */
    private var deshacer: Deshacer? = null

    /**
     * Cómo reponer lo escrito cuando el usuario toca «↩» en la barra.
     *
     * `esPalabra` distingue la corrección de una palabra (que aprende y deshace
     * en el diccionario) del bloque de un dictado entero (que solo se repone).
     * `deSustitucion` marca las que salen de una corrección aprendida, para
     * poder olvidarla si el usuario la deshace.
     */
    private data class Deshacer(
        val original: String,
        val corregida: String,
        val separador: String,
        val esPalabra: Boolean = true,
        val deSustitucion: Boolean = false
    )

    /** Las últimas palabras que puso el dictado (para saber qué corrige el usuario). */
    private val dictadas = ArrayDeque<String>()

    /** Palabra que había seleccionada al empezar a escribir: la que se reemplaza. */
    private var reemplazada: String? = null

    /** Perfil con el que se abrió el diccionario que está en uso. */
    private var perfilDiccionario = Ajustes.PERFIL_POR_DEFECTO

    /** Recuento de borrados visto la última vez (ver `Ajustes.generacionDiccionario`). */
    private var generacionDiccionario = 0

    override fun onCreate() {
        super.onCreate()
        ajustes = Ajustes(this)
        refrescarAjustesDeTecleo()
        micro = GestorAudio()
        perfilDiccionario = ajustes.perfil
        generacionDiccionario = ajustes.generacionDiccionario
        diccionario = Diccionario(this, perfilDiccionario)
        historial = Portapapeles(this)
        nativo = ReconocedorNativo(this)
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
        // Edge-to-edge en la ventana del IME. Sin esto, en Android 15/16 la
        // ventana se dibuja bajo la barra de navegación pero los insets llegan
        // a CERO, así que el relleno inferior no se aplicaba y la última fila
        // (espacio y ↵) quedaba tapada por los botones del sistema. Medido el
        // 2026-11-01: padding=20/20 cuando debería ser 20/173.
        // `getWindow()` del IME devuelve el Dialog del teclado; de ahí sale la Window.
        window?.window?.let { WindowCompat.setDecorFitsSystemWindows(it, false) }
        val raiz = layoutInflater.inflate(R.layout.keyboard_view, null)
        raizTeclado = raiz as ViewGroup
        textoEstado = raiz.findViewById(R.id.texto_estado)
        puntoEstado = raiz.findViewById(R.id.punto_estado)
        contenedor = raiz.findViewById(R.id.contenedor)
        // El portapapeles es un botón de la banda: un toque pega lo último
        // copiado y mantenerlo pulsado abre el historial.
        botonPortapapeles = raiz.findViewById(R.id.boton_portapapeles)
        botonPortapapeles.setOnClickListener {
            if (copiado != null) pegarCopiado() else abrirPanel()
        }
        botonPortapapeles.setOnLongClickListener {
            abrirPanel()
            true
        }
        // Traducir: un toque traduce el texto del campo al idioma elegido en
        // Ajustes. El idioma NO se cambia con el botón: antes lo cambiaba una
        // pulsación larga y, como no siempre se veía el aviso, parecía que
        // traducía al azar.
        botonTraducir = raiz.findViewById(R.id.boton_traducir)
        botonTraducir.setOnClickListener { traducirTexto() }
        // Ajustes: abre la pantalla de la app desde el propio teclado, para no
        // tener que ir al cajón de aplicaciones y volver.
        botonAjustes = raiz.findViewById(R.id.boton_ajustes)
        botonAjustes.setOnClickListener { abrirAjustes() }
        // El micrófono grande vive en la banda de arriba y está SIEMPRE a la
        // vista: un toque graba, otro toca termina. Ya no hay que cambiar de
        // modo para dictar ni volver atrás para escribir.
        botonMicro = raiz.findViewById(R.id.boton_micro)
        botonMicro.setOnClickListener { alternar() }
        // La ventana del IME llega hasta el borde de la pantalla y la barra de
        // navegación se dibuja ENCIMA de ella: sin este relleno, la última fila
        // del teclado quedaba debajo de los botones del sistema (medido el
        // 2026-10-31 en el Xiaomi: 47 dp tapados y el espacio y el ↵ cortados).
        val base = (6 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(raiz) { vista, insets ->
            val abajo = insets.getInsets(WindowInsetsCompat.Type.navigationBars()).bottom
            // Abajo va SOLO el inset: sumar el margen de 6 dp de más hacía que
            // la última fila no cupiera en la ventana del IME.
            vista.setPadding(base, base, base, abajo)
            // El inset llega DESPUÉS del primer layout, así que hay que volver a
            // comprobar el hueco ahora que el relleno ya ocupa su sitio (medido
            // el 2026-10-31: sin esto, `exceso=0` antes de rellenar y la fila
            // del espacio se salía por abajo).
            encajarEnElHueco()
            insets
        }
        // El tamaño final de la ventana del IME no se conoce hasta que hay
        // layout: se comprueba cada vez que cambia (y solo encoge, así que
        // converge en una o dos pasadas).
        raiz.addOnLayoutChangeListener { _, _, arriba, _, abajo, _, viejoArriba, _, viejoAbajo ->
            if (abajo - arriba != viejoAbajo - viejoArriba) encajarEnElHueco()
        }
        pintarTeclado()
        refrescarPortapapeles()
        mostrar(Estado.LISTO)
        return raiz
    }

    /**
     * Pinta lo que cambia con los ajustes: la franja de sugerencias y, debajo,
     * nuestro teclado de letras.
     *
     * La banda de dictado (estado + micrófono) NO se pinta aquí: es fija y vive
     * en `keyboard_view.xml`. Antes esta función cambiaba de MODO, sustituyendo
     * las letras por el micrófono grande; ahora conviven en una sola pantalla.
     */
    private fun pintarTeclado() {
        // El panel del historial nunca sobrevive a un repintado.
        panel?.liberar()
        panel = null
        panelAbierto = false
        alturaPintada = ajustes.alturaTecla
        numerosPintados = ajustes.filaNumeros
        contenedor.removeAllViews()
        barra = BarraSugerencias(this, oyenteSugerencias)
        contenedor.addView(barra.construir())
        teclado = TecladoLetras(this, ajustes, oyenteTeclas)
        contenedor.addView(teclado.construir())
        pintarPunto(R.color.acento)
        // Se relee el campo: puede haber texto de antes (o del dictado).
        sincronizarDesdeTexto()
        refrescarPortapapeles()
        encajarEnElHueco()
    }

    /**
     * Si el teclado no cabe en la ventana del IME, encoge las filas lo justo.
     *
     * La ventana del teclado no da más de lo que el sistema quiera (unos 317 dp
     * en el Xiaomi); con la banda del micrófono y la fila de números, la suma se
     * pasaba y el espacio y el ↵ quedaban cortados por abajo. Se mide lo que
     * ocupa de verdad y lo que sobra se reparte entre las filas.
     */
    private fun encajarEnElHueco() {
        if (!::raizTeclado.isInitialized) return
        raizTeclado.post {
            if (!::teclado.isInitialized) return@post
            // Lo que tapa la barra de navegación. Los insets deberían traerlo,
            // pero al IME llegan a cero en Android 16 aunque la ventana se
            // dibuje debajo; entonces se usa el alto real del recurso del
            // sistema (48 dp en la barra de tres botones).
            val inset = ViewCompat.getRootWindowInsets(raizTeclado)
                ?.getInsets(WindowInsetsCompat.Type.navigationBars())?.bottom ?: 0
            val altoBarra = maxOf(inset, altoBarraNavegacion())
            val rellenoAbajo = maxOf(raizTeclado.paddingBottom, altoBarra)
            val disponible = raizTeclado.height - raizTeclado.paddingTop - rellenoAbajo
            var total = 0
            for (i in 0 until raizTeclado.childCount) {
                total += raizTeclado.getChildAt(i).height
            }
            val exceso = total - disponible
            val filas = teclado.filas().coerceAtLeast(1)
            Log.d(
                ETIQUETA,
                "encaje: alto=${raizTeclado.height} inset=$inset barra=$altoBarra " +
                    "disponible=$disponible total=$total exceso=$exceso filas=$filas"
            )
            if (disponible <= 0) return@post
            if (exceso <= 0) return@post
            teclado.encoger((exceso + filas - 1) / filas)
        }
    }

    /** Alto de la barra de navegación según el recurso del sistema (px). */
    private fun altoBarraNavegacion(): Int {
        val id = resources.getIdentifier("navigation_bar_height", "dimen", "android")
        return if (id > 0) resources.getDimensionPixelSize(id) else 0
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

    /** Lo que hace la barra de sugerencias al tocar una de sus casillas. */
    private val oyenteSugerencias = object : OyenteSugerencias {
        override fun alElegir(texto: String) = aceptarSugerencia(texto)

        override fun alAccion() = alternarPalabraEnCurso()

        override fun alVolver() = deshacerCorreccion()
    }

    /** Si el usuario cierra el campo de texto a media grabación, se corta. */
    override fun onFinishInput() {
        if (micro.grabando) terminarDictado()
        limpiarEstado()
        // Las palabras dictadas eran de este campo: en el siguiente ya no valen.
        dictadas.clear()
        super.onFinishInput()
    }

    /**
     * Al aparecer el teclado en un campo se hace siempre lo mismo:
     *  - se lee lo que pide el campo (mayúsculas y corrección),
     *  - se relee el texto que hay delante del cursor y el portapapeles.
     * No hay que «volver al dictado»: el micrófono grande está siempre en la
     * banda de arriba, junto a las letras.
     * También se remonta si cambiaron de tamaño o la fila de números en los
     * ajustes, para que se note sin reiniciar nada.
     */
    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        // Con cuenta abierta, el perfil se pone al día en segundo plano (como
        // mucho cada 15 minutos): lo aprendido en el otro aparato aparece aquí.
        sincronizarCuenta()
        leerCampo(info)
        // Los ajustes que se miran al teclear, frescos (por si se cambiaron).
        refrescarAjustesDeTecleo()
        // Y si se cambió el perfil del diccionario, se cambia de diccionario.
        cambiarPerfilSiHaceFalta()
        // Si se vuelve a abrir el teclado no hay dictado en marcha: la línea de
        // estado vuelve a «Listo» (antes podía quedarse en «Escuchando…»).
        if (!nativo.escuchando && !micro.grabando) mostrar(Estado.LISTO)
        if (::contenedor.isInitialized &&
            (panelAbierto ||
                alturaPintada != ajustes.alturaTecla || numerosPintados != ajustes.filaNumeros)
        ) {
            pintarTeclado()
        }
        // Al regresar al teclado siempre se empieza en las letras: Android reutiliza
        // la vista del IME, así que el teclado de letras conservaba la página en la
        // que se había salido (símbolos) y el usuario volvía a `?123` (2026-11-12).
        if (::teclado.isInitialized) teclado.volverALetras()
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
        // Solo los campos de texto admiten composición. En números y en los
        // TYPE_NULL (vistas que manejan las teclas por su cuenta) se escribe
        // confirmado, que es lo único que entienden.
        admiteComposicion = esTexto
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
        idiomaTraduccion = ajustes.idiomaTraduccion
        // El servidor se usa solo si el usuario lo eligió Y hay credenciales Y
        // el plan es de pago: sin sesión o con el plan Gratis siempre dicta el
        // reconocedor del celular (gratis, sin red y sin cuenta).
        motorNativo = !ajustes.usarServidor || !ajustes.configurado || !ajustes.planPago
    }

    /**
     * Si cambió el perfil o se borró el diccionario desde Ajustes, se cambia de
     * diccionario sin reiniciar el teclado: se guarda lo que hubiera pendiente y
     * se carga el nuevo en segundo plano.
     */
    private fun cambiarPerfilSiHaceFalta() {
        val perfil = ajustes.perfil
        val generacion = ajustes.generacionDiccionario
        if (perfil == perfilDiccionario && generacion == generacionDiccionario) return
        val anterior = diccionario
        val mismoPerfil = perfil == perfilDiccionario
        perfilDiccionario = perfil
        generacionDiccionario = generacion
        diccionario = Diccionario(this, perfil)
        alcance.launch {
            // Al cambiar de perfil se guarda el anterior; si lo que pasó es que
            // se borró, se descarta sin escribir (volvería a crearlo).
            if (!mismoPerfil) {
                anterior.volcar()
            }
            anterior.descartar()
            diccionario.cargar()
            actualizarBarra()
        }
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
        // Teclear despacio también mueve el cursor (la composición deja el cursor
        // al final): si el texto sigue siendo el nuestro, no hay nada que
        // resincronizar y se conserva la composición abierta. Sin esto, la
        // siguiente letra la sustituiría y la palabra quedaría en una sola letra.
        if (composicionEnPie()) return
        if (inicioNuevo != inicioViejo || finNuevo != finViejo) sincronizarDesdeTexto()
    }

    /** ¿La composición abierta sigue siendo la que estamos tecleando? */
    private fun composicionEnPie(): Boolean {
        if (compuesta.isEmpty()) return false
        val antes = currentInputConnection
            ?.getTextBeforeCursor(compuesta.length, 0)?.toString() ?: return false
        return antes == compuesta.toString()
    }

    override fun onDestroy() {
        // Un último volcado de lo aprendido, en un alcance aparte: el del
        // servicio se cancela justo después y no le daría tiempo.
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            diccionario.volcar()
            // Y si hay cuenta, lo aprendido se sube ahora: es el momento en que
            // el teclado se va y nadie va a notar la espera.
            if (perfilCuenta.puedeSincronizar()) {
                runCatching { perfilCuenta.sincronizar(diccionario) }
                diccionario.volcar()
            }
        }
        nativo.liberar()
        portapapeles.removePrimaryClipChangedListener(oyentePortapapeles)
        alcance.cancel()
        micro.liberar()
        super.onDestroy()
    }

    /**
     * Baja, fusiona y sube lo aprendido, sin que lo note quien teclea.
     *
     * Va en un alcance aparte del servicio y no bloquea nada: el diccionario
     * local ya funciona sin la red, así que esto solo pone al día la cuenta.
     */
    private fun sincronizarCuenta() {
        if (!perfilCuenta.puedeSincronizar()) return
        if (!perfilCuenta.tocaSincronizar()) return
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            runCatching {
                diccionario.volcar()
                perfilCuenta.sincronizar(diccionario)
            }
        }
    }

    /**
     * Al ocultarse el teclado se cancela el reconocedor del celular: no puede
     * quedarse escuchando de fondo (gastaría batería y podría escribir en otra
     * app).
     */
    override fun onFinishInputView(finishingInput: Boolean) {
        nativo.cancelar()
        super.onFinishInputView(finishingInput)
    }

    // --- Dictado ---------------------------------------------------------

    private fun alternar() {
        when {
            nativo.escuchando -> pararNativo()
            micro.grabando -> terminarDictado()
            else -> empezarDictado()
        }
    }

    /**
     * Elige el motor: el reconocedor del celular (versión gratuita) o nuestro
     * servidor (miembros, con dirección y token). Si el celular no trae
     * reconocedor y hay servidor configurado, se usa el servidor.
     */
    private fun empezarDictado() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            mostrar(Estado.ERROR, getString(R.string.falta_permiso_micro))
            return
        }
        // Un dictado nuevo trae sus propias palabras: las de antes no cuentan
        // para reconocer correcciones.
        confirmarComposicion()
        dictadas.clear()
        if (motorNativo) {
            if (nativo.disponible()) {
                empezarNativo()
                return
            }
            if (!ajustes.configurado) {
                mostrar(Estado.ERROR, getString(R.string.nativo_no_disponible))
                return
            }
        }
        empezarServidor()
    }

    /** Dictado con el reconocedor del propio celular (sin servidor ni cuenta). */
    private fun empezarNativo() {
        largoInsertado = 0
        if (!nativo.empezar(avisosNativo)) {
            mostrar(Estado.ERROR, getString(R.string.nativo_no_disponible))
            return
        }
        vibrar(VIBRACION_CORTA)
        mostrar(Estado.GRABANDO)
    }

    private fun pararNativo() {
        nativo.parar()
        vibrar(VIBRACION_CORTA)
        mostrar(Estado.PROCESANDO)
    }

    /** Lo que avisa el reconocedor del celular mientras se dicta. */
    private val avisosNativo = object : ReconocedorNativo.Avisos {
        override fun alParcial(texto: String) {
            // El parcial solo se enseña en la línea de estado: el texto se
            // inserta al final (si el reconocedor reinicia la frase, escribir
            // en vivo dejaría texto duplicado).
            mostrar(Estado.GRABANDO, parcial = texto)
        }

        override fun alFinal(texto: String) {
            val conexion = currentInputConnection
            if (conexion == null) {
                mostrar(Estado.ERROR, getString(R.string.nativo_sin_campo))
                return
            }
            val antes = palabraSeleccionada()
            val corregido = corregirSiToca(texto)
            marcarEdicion()
            deshacer = null
            // Si había una palabra a medio teclear, se da por buena antes de
            // escribir el dictado (o acabaría dentro de la composición).
            confirmarComposicion()
            conexion.commitText(corregido, 1)
            recordarDictadas(corregido)
            if (antes != null) guardarCorreccion(antes, corregido)
            vibrar(VIBRACION_CORTA)
            mostrar(Estado.LISTO)
            sincronizarDesdeTexto()
            // Después de releer el campo (que limpia el deshacer): si el
            // diccionario cambió algo, la casilla «↩» devuelve lo que dijo el motor.
            if (corregido != texto) {
                deshacer = Deshacer(texto, corregido, "", esPalabra = false)
            }
        }

        override fun alError(motivo: String) {
            mostrar(Estado.ERROR, motivo)
        }
    }

    /** Dictado con nuestro servidor (el de siempre, para miembros). */
    private fun empezarServidor() {
        if (!ajustes.configurado) {
            mostrar(Estado.ERROR, getString(R.string.falta_configurar))
            return
        }
        val arranque = SystemClock.elapsedRealtime()
        if (!micro.iniciar()) {
            mostrar(Estado.ERROR, getString(R.string.error_micro))
            return
        }
        val microAbierto = SystemClock.elapsedRealtime()
        Log.i(
            ETIQUETA,
            "dictado: micro listo a los ${microAbierto - arranque} ms " +
                "(${SystemClock.elapsedRealtime() - arranque} ms en total)"
        )
        vibrar(VIBRACION_CORTA)
        largoInsertado = 0
        tramosVivos.clear()
        mostrar(Estado.GRABANDO)

        if (ajustes.modoVivo) empezarVivo() else empezarNormal()
    }

    /**
     * Modo normal: el texto sale al terminar, sin parciales.
     *
     * El audio se envía por WebSocket MIENTRAS se habla: así, al pulsar
     * terminar, la subida ya está hecha y solo queda la transcripción. El
     * búfer local se guarda igual, y si el WebSocket falla o no confirma a
     * tiempo se manda el audio completo por REST como antes.
     *
     * ⚠️ La conexión **no** retrasa la lectura del micrófono: se vigila en
     * paralelo. Antes se esperaba al `listo` antes de leer y todo lo hablado en
     * ese rato se quedaba en el búfer del `AudioRecord`, que solo aguanta unos
     * 800 ms: al empezar a leer llegaba audio viejo y se perdía el principio de
     * lo dictado (2026-11-06).
     */
    private fun empezarNormal() {
        val acumulado = ByteArrayOutputStream()
        val nueva = ClienteRed(ajustes).sesionViva(avisosSinTexto(), vivo = false)
        sesion = nueva
        val toque = SystemClock.elapsedRealtime()
        val puedeFluir = nueva.abrir()
        tarea = alcance.launch {
            // Un solo candado para el búfer: decide si un bloque se manda ya o
            // se guarda para mandarlo junto cuando el puente confirme. Así no
            // se pierde ni se duplica ninguno.
            val candado = Any()
            var conectado = false
            val conexion = launch(Dispatchers.IO) {
                if (!puedeFluir) return@launch
                if (!nueva.esperarListo(ESPERA_INICIO_MS)) return@launch
                Log.i(
                    ETIQUETA,
                    "dictado: puente listo a los " +
                        "${SystemClock.elapsedRealtime() - toque} ms"
                )
                synchronized(candado) {
                    conectado = true
                    // Lo hablado mientras conectaba se manda de golpe.
                    val rezago = acumulado.toByteArray()
                    if (rezago.isNotEmpty()) nueva.enviar(rezago)
                }
            }
            var primerBloque = true
            while (isActive && micro.grabando) {
                val bloque = withContext(Dispatchers.IO) { micro.leer() }
                if (bloque != null) {
                    if (primerBloque) {
                        primerBloque = false
                        Log.i(
                            ETIQUETA,
                            "dictado: primer bloque a los " +
                                "${SystemClock.elapsedRealtime() - toque} ms " +
                                "(${bloque.size} bytes)"
                        )
                    }
                    var directo = false
                    synchronized(candado) {
                        if (conectado && !nueva.fallo) directo = true
                        else acumulado.write(bloque)
                    }
                    if (directo) nueva.enviar(bloque)
                }
            }
            conexion.join()
            val huboLive = synchronized(candado) { conectado }
            liberarMicrofono()
            mostrar(Estado.PROCESANDO)

            var texto: String? = null
            if (huboLive && !nueva.fallo) {
                nueva.terminar()
                texto = withContext(Dispatchers.IO) { nueva.esperarFinal(ESPERA_FINAL_MS) }
            }
            cerrarSesion()

            if (texto.isNullOrBlank()) {
                // Respaldo: el audio que quedó en memoria va entero por REST.
                val pcm = synchronized(candado) { acumulado.toByteArray() }
                if (pcm.isEmpty()) {
                    mostrar(Estado.ERROR, getString(R.string.sin_audio))
                    return@launch
                }
                val resultado = withContext(Dispatchers.IO) {
                    ClienteRed(ajustes).transcribir(pcm)
                }
                cuandoTermine(resultado)
                return@launch
            }

            val conexionCampo = currentInputConnection
            if (conexionCampo == null) {
                mostrar(Estado.ERROR, getString(R.string.nativo_sin_campo))
                return@launch
            }
            marcarEdicion()
            deshacer = null
            conexionCampo.commitText(texto, 1)
            vibrar(VIBRACION_CORTA)
            mostrar(Estado.LISTO)
            sincronizarDesdeTexto()
        }
    }

    /**
     * Avisos del modo normal con streaming: el texto final se recoge con
     * `esperarFinal` y un fallo del WebSocket no corta la grabación (se
     * resuelve con el respaldo por REST al terminar).
     */
    private fun avisosSinTexto() = object : AvisosVivo {
        override fun alParcial(texto: String) = Unit
        override fun alFinal(texto: String) = Unit
        override fun alError(motivo: String) = Unit
    }

    /** Modo vivo: el audio sale por WebSocket y el texto se inserta al vuelo. */
    private fun empezarVivo() {
        val nueva = ClienteRed(ajustes).sesionViva(avisosVivo())
        sesion = nueva
        val toque = SystemClock.elapsedRealtime()
        if (!nueva.abrir()) {
            liberarMicrofono()
            mostrar(Estado.ERROR)
            return
        }
        tarea = alcance.launch {
            // El audio se acumula hasta que el puente confirme «listo»: si el
            // inicio se rechaza, ese audio se tira y el `fin` llegaría a una
            // sesión inexistente («No hay ningún dictado en curso»). Pero el
            // micrófono se lee **desde el primer bloque**: esperar aquí antes de
            // leer perdía lo hablado al empezar (2026-11-06).
            val pendientes = ByteArrayOutputStream()
            val candado = Any()
            var conectado = false
            val conexion = launch(Dispatchers.IO) {
                if (!nueva.esperarListo(ESPERA_INICIO_MS)) return@launch
                Log.i(
                    ETIQUETA,
                    "dictado vivo: puente listo a los " +
                        "${SystemClock.elapsedRealtime() - toque} ms"
                )
                synchronized(candado) {
                    conectado = true
                    val rezago = pendientes.toByteArray()
                    if (rezago.isNotEmpty()) nueva.enviar(rezago)
                }
            }
            while (isActive && micro.grabando && !nueva.fallo) {
                val bloque = withContext(Dispatchers.IO) { micro.leer() } ?: continue
                var directo = false
                synchronized(candado) {
                    if (conectado) directo = true else pendientes.write(bloque)
                }
                if (directo) nueva.enviar(bloque)
            }
            conexion.join()
            val huboLive = synchronized(candado) { conectado }
            liberarMicrofono()
            if (!huboLive) {
                if (sesion === nueva) {
                    // Si el puente dio un motivo, ya se está mostrando.
                    if (!nueva.fallo) {
                        mostrar(Estado.ERROR, getString(R.string.error_inicio_vivo))
                    }
                    nueva.cancelar()
                    cerrarSesion()
                }
                return@launch
            }
            if (!nueva.fallo) {
                mostrar(Estado.PROCESANDO)
                nueva.terminar()
            }
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
                val texto = resultado.texto
                // Lo que había seleccionado, si el usuario corrigió dictando otra vez.
                val antes = palabraSeleccionada()
                val corregido = corregirSiToca(texto)
                deshacer = null
                confirmarComposicion()
                currentInputConnection?.commitText(corregido, 1)
                recordarDictadas(corregido)
                if (antes != null) guardarCorreccion(antes, corregido)
                mostrar(Estado.LISTO)
                vibrar(VIBRACION_CORTA)
                sincronizarDesdeTexto()
                // Si el diccionario cambió algo, la casilla «↩» devuelve el texto del motor.
                if (corregido != texto) {
                    deshacer = Deshacer(texto, corregido, "", esPalabra = false)
                }
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
            alcance.launch {
                // Se corta la grabación: si se sigue enviando audio, al parar
                // se manda un `fin` a una sesión que el puente ya no tiene.
                micro.detener()
                // Lo que alcanzó a verse en vivo queda como texto normal: si no
                // se cierra la composición, el campo puede descartarlo luego.
                confirmarComposicion()
                mostrar(Estado.ERROR, motivo)
            }
            cerrarSesion()
        }
    }

    /**
     * Escribe el avance del dictado vivo.
     *
     * El puente manda cada parcial como un TRAMO NUEVO (una frase), no el texto
     * acumulado, así que se guardan todos y se reescribe el bloque completo: el
     * texto se ve desde el principio y sigue creciendo.
     *
     * Si un tramo CONTINÚA al anterior (empieza igual y es más largo), lo
     * reemplaza: cubre también a los servidores que mandan la frase creciendo.
     *
     * Se escribe con `setComposingText`, o sea como TEXTO EN COMPOSICIÓN: el
     * propio campo reemplaza la composición anterior, sin borrar por cuenta de
     * caracteres. El borrado con `deleteSurroundingText` no es fiable en todas
     * las apps (en WhatsApp se quedaba lo escrito en vivo y encima se añadía el
     * texto final completo, que es el duplicado que se veía al terminar).
     */
    private fun insertarParcial(texto: String) {
        val conexion = currentInputConnection ?: return
        val tramo = texto.trim()
        if (tramo.isEmpty()) return
        // Si el usuario venía tecleando, su palabra se da por buena: el parcial
        // del dictado no puede reemplazarla (ni al revés).
        if (compuesta.isNotEmpty()) {
            conexion.finishComposingText()
            compuesta.setLength(0)
        }
        if (tramosVivos.isEmpty()) {
            tramosVivos.add(tramo)
        } else {
            val ultimo = tramosVivos.last()
            if (tramo == ultimo) return
            if (tramo.startsWith(ultimo)) {
                tramosVivos[tramosVivos.lastIndex] = tramo
            } else {
                tramosVivos.add(tramo)
            }
        }
        val completo = tramosVivos.joinToString(" ") + " "
        conexion.setComposingText(completo, 1)
        largoInsertado = completo.length
    }

    /**
     * Cambia la composición por el texto definitivo y limpia los tramos.
     *
     * `commitText` sustituye por sí mismo la composición en curso, así que aquí
     * no se borra nada: se confirma el texto final o, si ya es exactamente el
     * que se está viendo, solo se cierra la composición (sin parpadeo).
     */
    private fun reemplazarParcial(texto: String) {
        val conexion = currentInputConnection ?: return
        val definitivo = texto.trim()
        val corregido = corregirSiToca(definitivo)
        val visto = tramosVivos.joinToString(" ").trim()
        if (largoInsertado > 0 && (corregido.isEmpty() || corregido == visto)) {
            // Nada nuevo que escribir: se confirma lo que ya está en el campo.
            conexion.finishComposingText()
        } else if (corregido.isNotEmpty()) {
            conexion.commitText(corregido, 1)
            conexion.finishComposingText()
        }
        if (corregido != definitivo) {
            deshacer = Deshacer(definitivo, corregido, "", esPalabra = false)
        }
        recordarDictadas(corregido)
        largoInsertado = 0
        tramosVivos.clear()
    }

    /** Da por bueno lo que haya en composición y deja de seguirlo. */
    private fun confirmarComposicion() {
        // La del dictado en vivo y la de la palabra que se está tecleando.
        if (largoInsertado > 0) currentInputConnection?.finishComposingText()
        largoInsertado = 0
        tramosVivos.clear()
        if (compuesta.isNotEmpty()) currentInputConnection?.finishComposingText()
        compuesta.setLength(0)
    }

    private fun cerrarSesion() {
        sesion?.cerrar()
        sesion = null
    }

    /** Suelta el micrófono (lo llama todo el que termina de grabar). */
    private fun liberarMicrofono() {
        micro.liberar()
    }

    // --- Diccionario, sugerencias y corrección ----------------------------

    /** Escribe lo que llega del teclado, llevando la cuenta de la palabra. */
    private fun escribir(texto: String) {
        marcarEdicion()
        deshacer = null
        // Si hay dictado en vivo a medio escribir (y no es nuestra palabra), se
        // da por bueno antes de teclear: nuestra composición lo reemplazaría.
        if (largoInsertado > 0 && compuesta.isEmpty()) confirmarComposicion()
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
            // Si había una palabra seleccionada (doble toque, mantener pulsado),
            // es la que se va a reemplazar: se guarda para aprender la pareja.
            reemplazada = palabraSeleccionada()
            palabra.append(texto.lowercase())
            palabraVisible.setLength(0)
            palabraVisible.append(texto)
        } else {
            palabra.append(texto.lowercase())
            palabraVisible.append(texto)
        }
        // Se escribe EN COMPOSICIÓN, como Gboard: el campo no da la palabra por
        // buena hasta que se cierra (espacio, puntuación o sugerencia). Así la
        // app no reacciona a media palabra —en Flutter (Flujocash) reaccionar
        // significa cerrar el teclado— y el diccionario puede arreglarla antes
        // de que quede escrita.
        compuesta.append(texto)
        if (admiteComposicion) {
            componer()
        } else {
            enviar(texto)
            compuesta.setLength(0)
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
        // La forma tal como la escribió el usuario (CallBy, no Callby). Si el
        // espejo no tuviera nada, se usa la de siempre.
        val visible = palabraVisible.toString().ifEmpty { conMayusculas(escrita, capitalizada) }
        // La palabra se da por buena (o se corrige) DESPUÉS de cerrar la
        // composición: borrar texto que el campo aún considera provisional no es
        // fiable en todas las apps.
        confirmarComposicion()
        var cerrada: String? = null
        if (escrita.isEmpty()) {
            deshacer = null
        } else {
            // Si la mayúscula la puso el USUARIO (⇧ a mano) y no era la
            // automática de empezar frase, se lee como nombre propio y no se
            // autocorrige. Mirar solo «empieza en mayúscula» dejaría sin
            // corregir la primera palabra de cada oración.
            val nombrePropio = capitalizada && !capitalizarOracion
            // Tampoco se corrige la palabra que el usuario acaba de escribir para
            // REEMPLAZAR una seleccionada: está corrigiendo a propósito y el
            // diccionario podría devolverle justo la que quería quitar.
            val reemplazaAlgo = reemplazada != null
            val correccion =
                if (autocorreccionActiva && correccionEnCampo && !nombrePropio && !reemplazaAlgo) {
                    diccionario.corregirPalabra(escrita)
                } else {
                    null
                }
            if (correccion != null) {
                val forma = conMayusculas(correccion, capitalizada)
                val conexion = currentInputConnection
                // La palabra bajo el cursor puede tener COLA a la derecha (el
                // cursor quedó en medio): si solo se borra lo escrito, la cola
                // se queda pegada a la palabra nueva. Se calcula antes de
                // escribir nada.
                val cola = letrasDespuesDelCursor()
                conexion?.deleteSurroundingText(escrita.length, cola.length)
                conexion?.commitText(forma, 1)
                // Si la corrección sale de algo que el usuario enseñó, deshacerla
                // tiene que olvidar la pareja (si no, volvería a aplicarse).
                val deSustitucion =
                    diccionario.correccionPara(escrita) == correccion.lowercase()
                deshacer = Deshacer(
                    visible, forma, separador,
                    deSustitucion = deSustitucion
                )
                cerrada = forma
            } else {
                deshacer = null
                cerrada = visible
            }
        }
        if (escribirSeparador) enviar(separador)
        if (cerrada != null) {
            anotar(cerrada)
            anotarReemplazo(cerrada)
        } else {
            reemplazada = null
        }
        palabra.setLength(0)
        palabraVisible.setLength(0)
        capitalizada = false
        // Mayúscula de empezar frase. Ojo: el ESPACIO no la cancela —tras «. »
        // sigue tocando mayúscula—. Antes esto la apagaba al pulsar espacio, así
        // que la oferta de mayúscula desaparecía justo cuando hacía falta
        // (lo reportó el usuario el 2026-11-18).
        capitalizarOracion = when {
            capitalizaPalabras -> true
            !capitalizaFrases -> false
            separador.length == 1 && FINES_DE_ORACION.contains(separador) -> true
            // Un separador que no cierra oración solo la quita si venía de una
            // palabra: si el campo estaba esperando la primera letra, se queda.
            else -> capitalizarOracion && escrita.isEmpty()
        }
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
        // La palabra en composición se confirma antes de reemplazarla: el
        // borrado de abajo tiene que caer sobre texto normal.
        confirmarComposicion()
        // Con una palabra seleccionada, la casilla la reemplaza: es una corrección.
        // Si no hay selección, se conserva la que se apuntó al empezar a escribir.
        val seleccion = palabraSeleccionada()
        seleccion?.let { reemplazada = it }
        // El cursor puede estar EN MEDIO de la palabra: lo que sigue hasta el
        // separador también es parte de ella y hay que borrarlo, o la cola queda
        // cortada («armoniz|acion» -> «armonización acion»).
        // Con una selección NO se toca la cola: `commitText` ya la reemplaza y
        // borrar alrededor se llevaría la palabra siguiente.
        val cola = if (seleccion == null) letrasDespuesDelCursor() else ""
        val original = palabraVisible.toString().ifEmpty { palabra.toString() } + cola
        if (seleccion == null && (palabra.isNotEmpty() || cola.isNotEmpty())) {
            // Se borra solo lo escrito de esta palabra (cada tecla escribió una).
            conexion.deleteSurroundingText(palabra.length, cola.length)
        }
        val forma = conMayusculas(
            elegida,
            if (palabra.isEmpty()) quiereMayusculaElTeclado() else capitalizada
        )
        conexion.commitText("$forma ", 1)
        anotar(forma)
        anotarReemplazo(forma)
        // Si la palabra corregida la puso el dictado, se aprende la pareja aunque
        // no se haya seleccionado a mano: basta con tocar la casilla.
        guardarCorreccion(original, forma)
        palabra.setLength(0)
        palabraVisible.setLength(0)
        capitalizada = false
        capitalizarOracion = capitalizaPalabras
        actualizarBarra()
        avisarMayuscula()
    }

    /**
     * Botón «＋ / ✓» de la barra: guarda la palabra en curso o la quita.
     *
     * Se toca mientras se escribe, así que guarda la palabra ANTES de cerrarla:
     * al cerrarse, el corrector ya la ve como propia y no la toca.
     */
    private fun alternarPalabraEnCurso() {
        // La forma escrita (CallBy): el diccionario la recuerda tal cual. Si no
        // hay palabra en curso, se guarda el último trozo del campo, números
        // incluidos («CallBy1»).
        val texto = palabraVisible.toString().ifEmpty { palabra.toString() }.trim()
            .ifEmpty { tokenAntesDelCursor().orEmpty() }
        if (texto.isEmpty()) return
        if (diccionario.esPropia(texto)) {
            diccionario.olvidar(texto)
            avisar(getString(R.string.palabra_olvidada, texto))
        } else {
            diccionario.aprender(texto)
            avisar(getString(R.string.palabra_aprendida, texto))
        }
        actualizarBarra()
    }

    /**
     * Casilla «↩» de la barra: deja el texto como estaba antes de la corrección.
     *
     * Es el deshacer VISIBLE que sustituye al viejo «primer ⌫», que cambiaba el
     * texto por sorpresa. Además de reponer lo escrito, guarda la palabra que el
     * usuario defiende (si no, volvería a corregirse) y olvida la pareja
     * aprendida que la había cambiado.
     */
    private fun deshacerCorreccion() {
        val pendiente = deshacer ?: return
        val conexion = currentInputConnection ?: return
        marcarEdicion()
        deshacer = null
        confirmarComposicion()
        conexion.deleteSurroundingText(
            pendiente.corregida.length + pendiente.separador.length, 0
        )
        conexion.commitText(pendiente.original + pendiente.separador, 1)
        palabra.setLength(0)
        palabraVisible.setLength(0)
        capitalizada = false
        if (pendiente.esPalabra) {
            diccionario.aprender(pendiente.original)
            if (pendiente.deSustitucion) {
                diccionario.olvidarCorreccion(pendiente.original, pendiente.corregida)
            }
            avisar(getString(R.string.palabra_restaurada, pendiente.original))
        } else {
            avisar(getString(R.string.dictado_restaurado))
        }
        // Se rehace el estado desde el campo: lo restaurado vuelve a ser la
        // palabra en curso y las predicciones quedan al día.
        sincronizarDesdeTexto()
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
        if (!::barra.isInitialized) return
        if (barraPendiente) return
        barraPendiente = true
        contenedor.post {
            barraPendiente = false
            pintarBarra()
        }
    }

    /** Recalcula las casillas de la barra según lo que se está escribiendo. */
    private fun pintarBarra() {
        if (!::barra.isInitialized) return
        // En un campo que no admite sugerencias (correo, contraseña, URL) no se
        // ofrece nada: ni palabras ni correcciones.
        if (!sugerenciasActivas || !correccionEnCampo) {
            barra.mostrar(emptyList(), null, AccionBarra.OCULTA)
            return
        }
        val enCurso = palabra.toString()
        // Al diccionario se le pasa la forma escrita (CallB) para que la casilla
        // de «conservar lo escrito» no le quite las mayúsculas.
        val visible = palabraVisible.toString()
        val lista = if (enCurso.isEmpty()) {
            diccionario.predicciones(terminadas.lastOrNull(), CASILLAS)
        } else {
            diccionario.sugerencias(
                visible.ifEmpty { enCurso }, terminadas.lastOrNull(), capitalizada, CASILLAS
            )
        }
        // El aviso de deshacer ocupa la primera casilla. Cuando se corrigió un
        // bloque dictado el texto es largo: ahí solo se dice «↩ volver».
        val volver = deshacer?.let { pendiente ->
            if (pendiente.esPalabra) {
                getString(R.string.barra_volver_a, pendiente.original)
            } else {
                getString(R.string.barra_volver)
            }
        }
        // Guardar solo tiene sentido con algo que el diccionario no traiga ya:
        // las 49 600 de la lista base no se guardan ni se quitan. Si no hay
        // palabra en curso, se ofrece el último trozo escrito; así se pueden
        // guardar cosas como «CallBy1» (el número cierra la palabra y, sin esto,
        // no habría nada que ofrecer).
        val guardable = if (enCurso.isEmpty()) {
            tokenAntesDelCursor()
        } else {
            visible.ifEmpty { enCurso }
        }
        val accion = when {
            guardable == null -> AccionBarra.OCULTA
            diccionario.estadoPalabra(guardable) == Diccionario.EstadoPalabra.EN_BASE ->
                AccionBarra.OCULTA
            diccionario.esPropia(guardable) -> AccionBarra.GUARDADA
            else -> AccionBarra.GUARDAR
        }
        barra.mostrar(lista, volver, accion)
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
     * Guarda las palabras que ha puesto el dictado.
     *
     * Sirve para saber que una palabra que el usuario reemplaza salió del motor
     * y no de sus dedos: así solo se aprende la pareja cuando es una corrección
     * de dictado, no cuando reescribe algo que ya había escrito él.
     */
    private fun recordarDictadas(texto: String) {
        for (parte in texto.split(Regex("[^\\p{L}]+"))) {
            if (parte.length < 2) continue
            dictadas.addLast(parte.lowercase())
            if (dictadas.size > MAXIMO_DICTADAS) dictadas.removeFirst()
        }
    }

    private fun esDictada(palabra: String): Boolean = dictadas.contains(palabra.lowercase())

    /**
     * El último trozo escrito antes del cursor: letras, números, `_` y apóstrofos.
     *
     * Es lo que ofrece el botón ＋ cuando no hay palabra en curso. Se pide al
     * campo (no al estado interno) porque un número cierra la palabra: en
     * «CallBy1» lo aprendido sería solo «CallBy» y falta el «1». Devuelve null
     * si no hay nada que guardar (vacío, una sola letra o solo números).
     */
    private fun tokenAntesDelCursor(): String? {
        val antes = runCatching {
            currentInputConnection?.getTextBeforeCursor(LARGO_MAXIMO_REEMPLAZO, 0)?.toString()
        }.getOrNull() ?: return null
        var inicio = antes.length
        while (inicio > 0 && esCaracterDeToken(antes[inicio - 1])) inicio--
        val texto = antes.substring(inicio).trim()
        if (texto.length < 2) return null
        // Solo números (una fecha, un importe) no se guardan: no aportan nada.
        if (texto.none { it.isLetter() }) return null
        return texto
    }

    /** Caracteres que forman un trozo guardable (letras, cifras y poco más). */
    private fun esCaracterDeToken(caracter: Char): Boolean =
        caracter.isLetterOrDigit() || caracter == '\'' || caracter == '_'

    /**
     * Las letras que siguen al cursor hasta el primer separador.
     *
     * Es la cola de la palabra cuando el cursor está metido en medio de ella
     * (el usuario pone el puntero dentro de una palabra y toca una sugerencia):
     * esa cola también hay que borrarla al reemplazarla.
     */
    private fun letrasDespuesDelCursor(): String {
        val texto = runCatching {
            currentInputConnection?.getTextAfterCursor(LARGO_MAXIMO_REEMPLAZO, 0)?.toString()
        }.getOrNull() ?: return ""
        var fin = 0
        while (fin < texto.length && esLetraDePalabra(texto[fin])) fin++
        return texto.substring(0, fin)
    }

    /**
     * La palabra que hay seleccionada, si es UNA sola y no un párrafo.
     *
     * En los campos sin sugerencias devuelve null (y `getSelectedText` puede
     * devolver null en campos que lo prohíben): en esos casos no se aprende nada.
     */
    private fun palabraSeleccionada(): String? {
        if (!correccionEnCampo) return null
        val texto = runCatching {
            currentInputConnection?.getSelectedText(0)?.toString()
        }.getOrNull()?.trim() ?: return null
        if (texto.isEmpty() || texto.length > LARGO_MAXIMO_REEMPLAZO) return null
        val partes = texto.split(Regex("[^\\p{L}]+")).filter { it.isNotEmpty() }
        return if (partes.size == 1) partes[0] else null
    }

    /** Guarda la pareja «lo que dictó el motor / lo que el usuario quería». */
    private fun guardarCorreccion(antes: String, nueva: String) {
        if (!correccionEnCampo || antes.isEmpty()) return
        if (!esDictada(antes)) return
        val partes = nueva.split(Regex("[^\\p{L}]+")).filter { it.isNotEmpty() }
        if (partes.size != 1) return
        if (partes[0].equals(antes, ignoreCase = true)) return
        diccionario.registrarCorreccion(antes, partes[0])
    }

    /** Al terminar una palabra escrita, apunta si reemplazó a una dictada. */
    private fun anotarReemplazo(nueva: String) {
        val antes = reemplazada ?: return
        reemplazada = null
        guardarCorreccion(antes, nueva)
    }

    /** Aplica las correcciones aprendidas al texto que llega dictado. */
    private fun corregirSiToca(texto: String): String =
        if (autocorreccionActiva && correccionEnCampo) diccionario.corregirDictado(texto) else texto

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
            palabraVisible.setLength(0)
            palabraVisible.append(enCurso)
            capitalizada = enCurso.firstOrNull()?.isUpperCase() == true
            val previo = antes.substring(0, maxOf(corte, 0))
            // La mayúscula de empezar frase se decide con el último carácter que
            // NO sea espacio: tras «. » sigue tocando, igual que tras «.».
            val ultimo = previo.trimEnd().lastOrNull()
            capitalizarOracion = capitalizaPalabras || (capitalizaFrases &&
                (ultimo == null || FINES_DE_ORACION.contains(ultimo)))
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
        if (::teclado.isInitialized) teclado.sugerirMayuscula(capitalizarOracion)
    }

    /** Si el teclado de letras está pidiendo mayúscula ahora mismo. */
    private fun quiereMayusculaElTeclado(): Boolean =
        ::teclado.isInitialized && teclado.quiereMayuscula()

    /** Olvida la palabra en curso (al cambiar de campo o de modo). */
    private fun limpiarEstado() {
        // Lo que estuviera en composición se da por bueno: el texto se queda en
        // el campo y el campo deja de considerarlo provisional.
        if (compuesta.isNotEmpty()) currentInputConnection?.finishComposingText()
        compuesta.setLength(0)
        palabra.setLength(0)
        palabraVisible.setLength(0)
        terminadas.clear()
        deshacer = null
        reemplazada = null
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

    /**
     * Reemplaza la composición abierta por lo que llevamos tecleado de la palabra.
     *
     * `newCursorPosition = 1` deja el cursor al final, que es donde sigue el
     * usuario. Es la forma que usa Gboard: el campo reemplaza la composición
     * anterior sin que haya que borrar por cuenta de caracteres.
     */
    private fun componer() {
        currentInputConnection?.setComposingText(compuesta.toString(), 1)
    }

    /** Devuelve la palabra con la mayúscula inicial si le toca. */
    private fun conMayusculas(texto: String, capitalizar: Boolean): String =
        if (capitalizar && texto.isNotEmpty()) texto.replaceFirstChar { it.uppercase() } else texto

    private fun esLetraDePalabra(caracter: Char) = caracter.isLetter() || caracter == '\''

    // --- Portapapeles ----------------------------------------------------

    /**
     * Relee el portapapeles del sistema y pone al día el botón de la banda.
     *
     * El botón sale solo si hay algo que pegar o algo en el historial, y queda
     * apagado (icono atenuado) cuando lo único que hace es abrir el historial.
     *
     * Desde Android 10 solo puede leer el portapapeles quien tiene el foco, y
     * mientras el teclado está en pantalla ese es el teclado; si Android no deja,
     * el botón no aparece y no pasa nada más. Lo copiado se guarda en el historial
     * cifrado (ver `guardarCopiado`); el botón sigue leyendo el portapapeles del
     * sistema, que es la fuente de verdad de «lo último copiado».
     */
    private fun refrescarPortapapeles() {
        if (!::botonPortapapeles.isInitialized) return
        val permitido = portapapelesActivo && !enCampoSecreto
        val texto = if (permitido) leerPortapapeles() else null
        copiado = texto
        val hayTexto = texto != null
        // El historial da acceso al panel aunque ahora no haya nada copiado.
        val hayHistorial = permitido && historial.disponible && historial.hayAlgo()
        // En un campo de contraseña no se ofrece nada del portapapeles.
        val visible = permitido && (hayTexto || hayHistorial)
        botonPortapapeles.visibility = if (visible) View.VISIBLE else View.GONE
        // Brillante si hay algo que pegar; atenuado si solo abre el historial.
        if (visible) {
            botonPortapapeles.imageTintList = ColorStateList.valueOf(
                getColor(if (hayTexto) R.color.acento else R.color.texto_tecla_secundario)
            )
        }
        // Si el panel está abierto, se repinta con lo que acaba de entrar.
        if (panelAbierto) panel?.refrescar()
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

    /** Cierra el panel y repinta el teclado. */
    private fun cerrarPanel() {
        panel?.liberar()
        panel = null
        panelAbierto = false
        pintarTeclado()
    }

    /** Lo que hace el panel del historial al tocar sus filas y botones. */
    private val oyentePanel = object : OyentePortapapeles {
        override fun alPegar(texto: String) {
            val conexion = currentInputConnection ?: return
            marcarEdicion()
            deshacer = null
            confirmarComposicion()
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

    /**
     * Tecla ⌫: borra un carácter, SIEMPRE.
     *
     * Antes, si se acababa de autocorregir una palabra, el primer ⌫ no borraba:
     * reponía la palabra entera, la guardaba en el diccionario y olvidaba la
     * pareja aprendida. Un estado invisible cambiaba lo que hace una tecla y no
     * había forma de adivinarlo (2026-11-18). Ahora deshacer es la casilla «↩»
     * de la barra, que se ve.
     */
    private fun borrarCaracter() {
        marcarEdicion()
        // Cualquier borrado invalida el «↩»: si el texto ya cambió, reponer la
        // palabra anterior borraría lo que el usuario acaba de escribir.
        deshacer = null
        val conexion = currentInputConnection ?: return
        // Dentro de una palabra en composición, el ⌫ se resuelve en la propia
        // composición: se recorta y se vuelve a mandar. Mandar un KEYCODE_DEL
        // dejaría al campo y al teclado con ideas distintas de dónde empieza la
        // palabra, y con la composición abierta.
        if (compuesta.isNotEmpty()) {
            compuesta.deleteAt(compuesta.length - 1)
            if (palabra.isNotEmpty()) palabra.deleteAt(palabra.length - 1)
            if (palabraVisible.isNotEmpty()) palabraVisible.deleteAt(palabraVisible.length - 1)
            if (compuesta.isEmpty()) {
                // Se vacía la composición antes de cerrarla: si solo se cerrara,
                // el campo confirmaría la última letra y el ⌫ no borraría nada.
                conexion.setComposingText("", 1)
                conexion.finishComposingText()
            } else {
                componer()
            }
            actualizarBarra()
            return
        }
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DEL))
        conexion.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_DEL))
        if (palabra.isNotEmpty()) {
            palabra.deleteAt(palabra.length - 1)
            // El espejo con mayúsculas se borra a la par, o al cerrar la palabra
            // se guardaría una con una letra de más.
            if (palabraVisible.isNotEmpty()) palabraVisible.deleteAt(palabraVisible.length - 1)
        } else {
            // Se borró el separador: la palabra anterior vuelve a estar en curso.
            val previa = terminadas.removeLastOrNull()
            if (previa != null) {
                palabra.append(previa.lowercase())
                palabraVisible.setLength(0)
                palabraVisible.append(previa)
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

    /**
     * Abre la pantalla de ajustes desde el teclado.
     *
     * Desde un IME el intent NECESITA `FLAG_ACTIVITY_NEW_TASK`: sin él el
     * sistema lo rechaza, porque el teclado no corre en una tarea propia. Al
     * volver, el botón «Volver al teclado de dictado» de esa pantalla devuelve
     * al usuario al campo donde estaba.
     */
    private fun abrirAjustes() {
        startActivity(
            Intent(this, AjustesActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    // --- Traducción ------------------------------------------------------

    /**
     * Traduce lo que hay en el campo: la **selección** si la hay o, si no, la
     * línea en la que está el cursor. El resultado sustituye al original.
     *
     * La llamada va al puente (`/api/v1/traducir`), que traduce en CPU con
     * Opus-MT; el dictado no se toca.
     */
    private fun traducirTexto() {
        if (!ajustes.configurado) {
            mostrar(Estado.ERROR, getString(R.string.traduccion_sin_configurar))
            return
        }
        // Traducir es de pago: se avisa aquí y no se gasta una petición que el
        // puente iba a rechazar con 403.
        if (!ajustes.planPago) {
            mostrar(Estado.ERROR, getString(R.string.traduccion_requiere_plan))
            return
        }
        val texto = textoParaTraducir()
        if (texto == null) {
            mostrar(Estado.ERROR, getString(R.string.traduccion_sin_texto))
            return
        }
        val destino = ajustes.idiomaTraduccion
        mostrar(Estado.PROCESANDO, getString(R.string.traduciendo, nombreIdioma(destino)))
        alcance.launch {
            val resultado = withContext(Dispatchers.IO) {
                ClienteRed(ajustes).traducir(texto, destino)
            }
            when (resultado) {
                is Resultado.Texto -> sustituirPorTraduccion(texto, resultado.texto)
                is Resultado.Error -> mostrar(Estado.ERROR, resultado.motivo)
            }
        }
    }

    /** La selección, o la línea del cursor (sin el salto de línea). */
    private fun textoParaTraducir(): String? {
        val conexion = currentInputConnection ?: return null
        val seleccion = conexion.getSelectedText(0)?.toString()?.trim()
        if (!seleccion.isNullOrEmpty()) return seleccion
        val antes = conexion.getTextBeforeCursor(LECTURA_TRADUCCION, 0)?.toString().orEmpty()
        return antes.substringAfterLast('\n').trim().ifEmpty { null }
    }

    /** Cambia el texto del campo por su traducción, con el cursor donde estaba. */
    private fun sustituirPorTraduccion(original: String, traducido: String) {
        val conexion = currentInputConnection ?: return
        marcarEdicion()
        deshacer = null
        confirmarComposicion()
        val seleccion = conexion.getSelectedText(0)?.toString()?.trim()
        if (!seleccion.isNullOrEmpty()) {
            // Con selección, `commitText` la reemplaza directamente.
            conexion.commitText(traducido, 1)
        } else {
            conexion.deleteSurroundingText(original.length, 0)
            conexion.commitText(traducido, 1)
        }
        palabra.setLength(0)
        palabraVisible.setLength(0)
        capitalizada = false
        mostrar(Estado.LISTO)
        vibrar(VIBRACION_CORTA)
        sincronizarDesdeTexto()
    }

    /** Nombre del idioma para los mensajes («inglés», «chino»…). */
    private fun nombreIdioma(codigo: String): String = getString(
        when (codigo) {
            "en" -> R.string.idioma_en
            "pt" -> R.string.idioma_pt
            "it" -> R.string.idioma_it
            "zh" -> R.string.idioma_zh
            else -> R.string.idioma_es_texto
        }
    )

    // --- Presentación ----------------------------------------------------

    private fun mostrar(estado: Estado, mensaje: String? = null, parcial: String? = null) {
        if (!::textoEstado.isInitialized) return
        textoEstado.text = when {
            // Lo que va entendiendo el reconocedor del celular: se enseña aquí y
            // el texto definitivo se inserta al terminar.
            parcial != null -> getString(R.string.estado_escuchando, parcial)
            // Un mensaje propio con el estado «procesando» (traduciendo…).
            estado == Estado.PROCESANDO && mensaje != null -> mensaje
            estado == Estado.LISTO ->
                getString(R.string.estado_listo_traduccion, nombreIdioma(idiomaTraduccion))
            estado == Estado.GRABANDO -> getString(R.string.estado_grabando)
            estado == Estado.PROCESANDO -> getString(R.string.estado_procesando)
            else -> mensaje ?: getString(R.string.estado_error)
        }
        textoEstado.setTextColor(
            getColor(if (estado == Estado.ERROR) R.color.texto_error else R.color.texto_estado)
        )
        // Punto de la línea de estado: el mismo aviso que el color del micrófono,
        // para que se vea aunque el botón esté fuera de la vista.
        pintarPunto(
            when (estado) {
                Estado.LISTO -> R.color.acento
                Estado.GRABANDO -> R.color.micro_grabando
                Estado.PROCESANDO -> R.color.micro_procesando
                Estado.ERROR -> R.color.texto_error
            }
        )
        // El micrófono grande está siempre en la banda: se tiñe con el estado.
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

        /** Etiqueta de los avisos de depuración del teclado (`adb logcat -s`). */
        const val ETIQUETA = "DictadoIME"

        /** Cuánto se espera a que el puente confirme el inicio del dictado. */
        private const val ESPERA_INICIO_MS = 15_000L

        /** Cuánto se espera al texto final (el puente corta a los 120 s). */
        private const val ESPERA_FINAL_MS = 120_000L

        /** Casillas de la barra de sugerencias. */
        private const val CASILLAS = 3

        /** Palabras que se recuerdan del campo para las predicciones. */
        private const val MAXIMO_TERMINADAS = 40

        /** Palabras dictadas que se recuerdan para reconocer correcciones. */
        private const val MAXIMO_DICTADAS = 30

        /** Largo máximo de una palabra que se considera seleccionada. */
        private const val LARGO_MAXIMO_REEMPLAZO = 40

        /** Cuánto texto se relee por delante del cursor al sincronizar. */
        private const val LECTURA_MAXIMA = 80

        /** Cuánto texto se lee hacia atrás al traducir (una línea larga). */
        private const val LECTURA_TRADUCCION = 1000

        /** Lo copiado más largo que se ofrece en la franja (2 000 caracteres). */
        private const val LARGO_MAXIMO_PORTAPAPELES = 2000

        /** Si la última edición fue nuestra hace tan poco, no se resincroniza. */
        private const val VENTANA_EDICION_PROPIA = 250L

        /** Después de estos signos, la palabra siguiente va en mayúscula. */
        private const val FINES_DE_ORACION = ".!?…\n"
    }
}
