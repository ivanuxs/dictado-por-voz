package com.ivanuxs.skipkey

import android.Manifest
import android.app.StatusBarManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.RadioGroup
import android.widget.SeekBar
import android.widget.Switch
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Ajustes del dictado.
 *
 * Normalmente NO hay que escribir nada aquí: la dirección, el token y la clave
 * pueden venir dentro del APK (ver `local.properties` y `build.gradle.kts`) o
 * aplicarse desde un enlace de configuración:
 *
 *     skipkey://config?url=https://dictado.tu-dominio.com&token=...&clave=...
 *
 * Esta pantalla es también la que pide el permiso de micrófono: un teclado
 * (IME) no puede mostrar el diálogo de permisos por sí solo.
 */
class AjustesActivity : AppCompatActivity() {

    private lateinit var ajustes: Ajustes
    private lateinit var clienteCuenta: ClienteCuenta
    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private lateinit var campoUrl: EditText
    private lateinit var campoToken: EditText
    private lateinit var campoClave: EditText
    private lateinit var campoVivo: Switch
    private lateinit var campoServidor: Switch
    private lateinit var campoEspera: EditText
    private lateinit var campoNumeros: Switch
    private lateinit var campoVibrar: Switch
    private lateinit var campoSugerencias: Switch
    private lateinit var campoAutocorreccion: Switch
    private lateinit var campoPortapapeles: Switch
    private lateinit var campoHistorial: Switch
    private lateinit var campoPerfil: EditText
    private lateinit var campoTamano: SeekBar
    private lateinit var campoIdiomas: RadioGroup
    private lateinit var etiquetaTamano: TextView
    private lateinit var resultado: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ajustes)
        ajustes = Ajustes(this)
        clienteCuenta = ClienteCuenta(this)

        campoUrl = findViewById(R.id.campo_url)
        campoToken = findViewById(R.id.campo_token)
        campoClave = findViewById(R.id.campo_clave)
        campoVivo = findViewById(R.id.campo_vivo)
        campoServidor = findViewById(R.id.campo_servidor)
        campoEspera = findViewById(R.id.campo_espera)
        campoNumeros = findViewById(R.id.campo_numeros)
        campoVibrar = findViewById(R.id.campo_vibrar)
        campoSugerencias = findViewById(R.id.campo_sugerencias)
        campoAutocorreccion = findViewById(R.id.campo_autocorreccion)
        campoPortapapeles = findViewById(R.id.campo_portapapeles)
        campoHistorial = findViewById(R.id.campo_historial)
        campoPerfil = findViewById(R.id.campo_perfil)
        campoTamano = findViewById(R.id.campo_tamano)
        campoIdiomas = findViewById(R.id.grupo_idioma_traduccion)
        etiquetaTamano = findViewById(R.id.etiqueta_tamano)
        resultado = findViewById(R.id.texto_resultado)
        mostrarAjustes()

        campoTamano.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(barra: SeekBar, valor: Int, delUsuario: Boolean) {
                mostrarTamano()
            }

            override fun onStartTrackingTouch(barra: SeekBar) = Unit
            override fun onStopTrackingTouch(barra: SeekBar) = Unit
        })

        findViewById<Button>(R.id.boton_guardar).setOnClickListener { guardar() }
        findViewById<Button>(R.id.boton_probar).setOnClickListener { probar() }
        findViewById<Button>(R.id.boton_abrir_teclados).setOnClickListener {
            startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
        }
        findViewById<Button>(R.id.boton_elegir_teclado).setOnClickListener {
            val gestor = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
            gestor.showInputMethodPicker()
        }

        findViewById<Button>(R.id.boton_volver).setOnClickListener {
            VolverAlDictado.volver(this@AjustesActivity)
        }
        // La cuenta (correo, plan y cupos) vive en su propia pantalla.
        findViewById<Button>(R.id.boton_cuenta).setOnClickListener {
            startActivity(Intent(this, CuentaActivity::class.java))
        }
        findViewById<Button>(R.id.boton_teja).setOnClickListener { ofrecerTeja() }
        findViewById<Button>(R.id.boton_borrar_historial).setOnClickListener {
            confirmarBorradoHistorial()
        }
        // Lo que el diccionario guardó de ti, con una ✕ por palabra: antes solo
        // se podía tocar con el dedo largo, a ciegas.
        findViewById<Button>(R.id.boton_mis_palabras).setOnClickListener {
            startActivity(Intent(this, PalabrasActivity::class.java))
        }
        findViewById<Button>(R.id.boton_borrar_diccionario).setOnClickListener {
            confirmarBorradoDiccionario()
        }

        pedirMicrofono()
        aplicarEnlace(intent)
        // Si nos abrió la teja rápida, se muestra el selector de teclados: al
        // tocar «Dictado por voz» vuelve el nuestro.
        if (intent?.action == ACCION_SELECTOR) VolverAlDictado.volver(this)
    }

    /** El enlace puede llegar con la app ya abierta (singleTop). */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        aplicarEnlace(intent)
    }

    /**
     * Al volver a esta pantalla se repinta lo que depende del plan y, si hay
     * sesión, se piden los cupos al puente.
     *
     * Hace falta porque el plan se cambia fuera de aquí (pantalla de Cuenta o
     * el propio servidor): sin esto, los interruptores de servidor y dictado en
     * vivo seguían apagados hasta cerrar la app entera (medido el 2026-11-04:
     * la cuenta ya era Pro y el interruptor no se dejaba tocar).
     */
    override fun onResume() {
        super.onResume()
        refrescarPlan()
        pedirEstado()
    }

    /**
     * Pregunta al puente el plan y los cupos de hoy y los guarda.
     *
     * Es el mismo `GET /api/v1/yo` que usa la pantalla de Cuenta: si no hay red
     * o el puente no contesta, no se toca nada y se queda lo guardado.
     */
    private fun pedirEstado() {
        if (!ajustes.haySesion) return
        alcance.launch {
            val resultado = withContext(Dispatchers.IO) { clienteCuenta.estado() }
            if (resultado is ResultadoCuenta.Bien) {
                clienteCuenta.guardarSesion(resultado.datos)
                refrescarPlan()
            }
        }
    }

    /**
     * Pinta lo que depende del plan: el botón de cuenta y los dos interruptores
     * de pago. NO toca los demás campos, así que se puede llamar en `onResume`
     * sin borrar lo que el usuario esté escribiendo.
     */
    private fun refrescarPlan() {
        // El botón de cuenta dice con quién se está (y con qué plan).
        val botonCuenta = findViewById<Button>(R.id.boton_cuenta)
        botonCuenta.text = if (ajustes.haySesion) {
            getString(
                R.string.ajustes_cuenta_con,
                ajustes.usuario.ifBlank { ajustes.email },
                ajustes.planNombre.ifBlank { ajustes.plan.ifBlank { "—" } }
            )
        } else {
            getString(R.string.ajustes_cuenta)
        }
        // El servidor y el dictado en vivo son funciones de pago: sin plan se
        // quedan apagados y se dice por qué. El puente los rechazaría igual
        // (403), así que esto solo evita el intento inútil.
        val pago = ajustes.planPago
        campoServidor.isEnabled = pago
        campoVivo.isEnabled = pago && ajustes.planVivo
        if (!pago) {
            campoServidor.isChecked = false
            campoVivo.isChecked = false
        }
    }

    /** Refleja en pantalla lo guardado (o lo que vino dentro del APK). */
    private fun mostrarAjustes() {
        campoUrl.setText(ajustes.urlBase)
        campoToken.setText(ajustes.token)
        campoClave.setText(ajustes.claveCliente)
        campoVivo.isChecked = ajustes.modoVivo
        campoServidor.isChecked = ajustes.usarServidor
        refrescarPlan()
        campoEspera.setText(ajustes.segundosEspera.toString())
        campoNumeros.isChecked = ajustes.filaNumeros
        campoVibrar.isChecked = ajustes.vibrar
        campoSugerencias.isChecked = ajustes.sugerencias
        campoAutocorreccion.isChecked = ajustes.autocorreccion
        campoPortapapeles.isChecked = ajustes.sugerirPortapapeles
        campoHistorial.isChecked = ajustes.guardarHistorial
        campoPerfil.setText(ajustes.perfil)
        // El deslizador va de 0 a (máximo - mínimo); el valor real es mínimo + posición.
        campoTamano.max = Ajustes.ALTURA_MAXIMA - Ajustes.ALTURA_MINIMA
        campoTamano.progress = ajustes.alturaTecla - Ajustes.ALTURA_MINIMA
        mostrarTamano()
        // Idioma de traducción: la opción que esté guardada. Se marca con
        // `post` porque en la primera pasada el RadioGroup todavía no aplica el
        // estado de sus hijos (comprobado el 2026-11-01: check() en onCreate no
        // marcaba nada; tocando el botón sí).
        val guardado = identificadorDeIdioma(ajustes.idiomaTraduccion)
        campoIdiomas.check(guardado)
        campoIdiomas.post { campoIdiomas.check(guardado) }
    }

    /** Resource id del botón de opción de cada idioma. */
    private fun identificadorDeIdioma(codigo: String): Int = when (codigo) {
        "pt" -> R.id.campo_idioma_pt
        "it" -> R.id.campo_idioma_it
        "zh" -> R.id.campo_idioma_zh
        "es" -> R.id.campo_idioma_es
        else -> R.id.campo_idioma_en
    }

    /** Código del idioma elegido en la pantalla. */
    private fun idiomaElegido(): String = when (campoIdiomas.checkedRadioButtonId) {
        R.id.campo_idioma_pt -> "pt"
        R.id.campo_idioma_it -> "it"
        R.id.campo_idioma_zh -> "zh"
        R.id.campo_idioma_es -> "es"
        else -> "en"
    }

    /** El número que se ve junto al deslizador del tamaño. */
    private fun mostrarTamano() {
        etiquetaTamano.text = getString(
            R.string.ajustes_tamano, Ajustes.ALTURA_MINIMA + campoTamano.progress
        )
    }

    private fun guardar() {
        ajustes.urlBase = campoUrl.text.toString()
        ajustes.token = campoToken.text.toString()
        ajustes.claveCliente = campoClave.text.toString()
        ajustes.modoVivo = campoVivo.isChecked
        ajustes.usarServidor = campoServidor.isChecked
        ajustes.segundosEspera =
            campoEspera.text.toString().toIntOrNull() ?: Ajustes.ESPERA_POR_DEFECTO
        ajustes.filaNumeros = campoNumeros.isChecked
        ajustes.vibrar = campoVibrar.isChecked
        ajustes.sugerencias = campoSugerencias.isChecked
        ajustes.autocorreccion = campoAutocorreccion.isChecked
        ajustes.sugerirPortapapeles = campoPortapapeles.isChecked
        ajustes.guardarHistorial = campoHistorial.isChecked
        // El nombre se normaliza (minúsculas, sin acentos ni espacios): es el que
        // lleva el nombre de los archivos del diccionario de cada perfil.
        ajustes.perfil = campoPerfil.text.toString()
        campoPerfil.setText(ajustes.perfil)
        ajustes.alturaTecla = Ajustes.ALTURA_MINIMA + campoTamano.progress
        ajustes.idiomaTraduccion = idiomaElegido()
        // El servidor sin credenciales no sirve: se avisa de que se usará el
        // reconocedor del celular (es la situación de la versión gratuita).
        resultado.text = getString(
            if (campoServidor.isChecked && !ajustes.configurado) {
                R.string.ajustes_servidor_sin_credenciales
            } else {
                R.string.ajustes_guardado
            }
        )
    }

    private fun probar() {
        if (!ajustes.configurado) {
            resultado.text = getString(R.string.ajustes_incompleto)
            return
        }
        resultado.text = getString(R.string.ajustes_probando)
        alcance.launch {
            val respuesta = withContext(Dispatchers.IO) {
                ClienteRed(ajustes).probarSalud()
            }
            resultado.text = when (respuesta) {
                is Resultado.Texto -> respuesta.texto
                is Resultado.Error -> respuesta.motivo
            }
        }
    }

    /** Borra el historial del portapapeles, con confirmación (no se puede deshacer). */
    private fun confirmarBorradoHistorial() {
        AlertDialog.Builder(this)
            .setTitle(R.string.ajustes_historial_titulo)
            .setMessage(R.string.ajustes_historial_pregunta)
            .setNegativeButton(R.string.ajustes_cancelar, null)
            .setPositiveButton(R.string.ajustes_borrar_confirmar) { _, _ ->
                Portapapeles(this).vaciarTodo()
                resultado.text = getString(R.string.ajustes_historial_borrado)
            }
            .show()
    }

    /**
     * Borra las palabras y correcciones del perfil que está escrito en pantalla.
     *
     * Se usa el perfil de la pantalla (no el guardado) porque el usuario puede
     * haberlo cambiado sin pulsar Guardar: así borra el que está viendo.
     */
    private fun confirmarBorradoDiccionario() {
        val perfil = Ajustes.nombrePerfil(campoPerfil.text.toString())
        AlertDialog.Builder(this)
            .setTitle(R.string.ajustes_borrar_diccionario_titulo)
            .setMessage(getString(R.string.ajustes_borrar_diccionario_pregunta, perfil))
            .setNegativeButton(R.string.ajustes_cancelar, null)
            .setPositiveButton(R.string.ajustes_borrar_confirmar) { _, _ ->
                val diccionario = Diccionario(this, perfil)
                alcance.launch {
                    diccionario.borrarAprendido()
                    // Sello para que el teclado (que tiene su propia copia en
                    // memoria) suelte la suya y cargue esta vacía.
                    ajustes.generacionDiccionario = ajustes.generacionDiccionario + 1
                    resultado.text = getString(R.string.ajustes_diccionario_borrado, perfil)
                }
            }
            .show()
    }

    /**
     * Pide a Android que ofrezca añadir la teja al panel de ajustes rápidos.
     *
     * Existe desde Android 13 (`requestAddTileService`): muestra un diálogo del
     * sistema («¿añadir esta teja?») y la coloca sin que el usuario tenga que
     * buscar el lápiz de editar. En versiones anteriores se explican los pasos.
     */
    private fun ofrecerTeja() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            resultado.text = getString(R.string.ajustes_teja_manual)
            return
        }
        getSystemService(StatusBarManager::class.java).requestAddTileService(
            ComponentName(this, TejaRapidaDictado::class.java),
            getString(R.string.ajustes_volver),
            Icon.createWithResource(this, R.drawable.ic_micro),
            mainExecutor
        ) { codigo ->
            val anadida =
                codigo == StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ADDED ||
                    codigo == StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ALREADY_ADDED
            resultado.text = getString(
                if (anadida) R.string.ajustes_teja_listo else R.string.ajustes_teja_manual
            )
        }
    }

    /**
     * Aplica la configuración que venga en el enlace:
     *
     *     skipkey://config?url=...&token=...&clave=...
     *
     * Sirve para dejar listo otro celular sin escribir nada a mano (se lo
     * puedes mandar por WhatsApp y lo tocas).
     */
    private fun aplicarEnlace(entrada: Intent?) {
        val datos = entrada?.data ?: return
        if (datos.scheme != "skipkey") return

        val url = datos.getQueryParameter("url")
        val token = datos.getQueryParameter("token")
        val clave = datos.getQueryParameter("clave")
        if (url.isNullOrBlank() && token.isNullOrBlank() && clave.isNullOrBlank()) return

        if (!url.isNullOrBlank()) ajustes.urlBase = url
        if (!token.isNullOrBlank()) ajustes.token = token
        if (!clave.isNullOrBlank()) ajustes.claveCliente = clave

        mostrarAjustes()
        resultado.text = getString(R.string.ajustes_desde_enlace)
    }

    /** El permiso lo pide esta pantalla, no el teclado. */
    private fun pedirMicrofono() {
        val faltan = ArrayList<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            faltan.add(Manifest.permission.RECORD_AUDIO)
        }
        // La escucha continua lleva una notificación fija: en Android 13+ hay
        // que pedirla, o el sistema no la enseña.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            faltan.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (faltan.isNotEmpty()) requestPermissions(faltan.toTypedArray(), CODIGO_MICRO)
    }

    override fun onDestroy() {
        alcance.cancel()
        super.onDestroy()
    }

    companion object {
        /** Acción con la que la teja rápida abre esta pantalla para el selector. */
        const val ACCION_SELECTOR = "com.ivanuxs.skipkey.SELECTOR"

        private const val CODIGO_MICRO = 1001
    }
}
