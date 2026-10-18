package com.ivanuxs.dictadoime

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
 *     dictadoime://config?url=https://dictado.tu-dominio.com&token=...&clave=...
 *
 * Esta pantalla es también la que pide el permiso de micrófono: un teclado
 * (IME) no puede mostrar el diálogo de permisos por sí solo.
 */
class AjustesActivity : AppCompatActivity() {

    private lateinit var ajustes: Ajustes
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
    private lateinit var campoTamano: SeekBar
    private lateinit var etiquetaTamano: TextView
    private lateinit var resultado: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ajustes)
        ajustes = Ajustes(this)

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
        campoTamano = findViewById(R.id.campo_tamano)
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
        findViewById<Button>(R.id.boton_teja).setOnClickListener { ofrecerTeja() }
        findViewById<Button>(R.id.boton_borrar_historial).setOnClickListener {
            confirmarBorradoHistorial()
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

    /** Refleja en pantalla lo guardado (o lo que vino dentro del APK). */
    private fun mostrarAjustes() {
        campoUrl.setText(ajustes.urlBase)
        campoToken.setText(ajustes.token)
        campoClave.setText(ajustes.claveCliente)
        campoVivo.isChecked = ajustes.modoVivo
        campoServidor.isChecked = ajustes.usarServidor
        campoEspera.setText(ajustes.segundosEspera.toString())
        campoNumeros.isChecked = ajustes.filaNumeros
        campoVibrar.isChecked = ajustes.vibrar
        campoSugerencias.isChecked = ajustes.sugerencias
        campoAutocorreccion.isChecked = ajustes.autocorreccion
        campoPortapapeles.isChecked = ajustes.sugerirPortapapeles
        campoHistorial.isChecked = ajustes.guardarHistorial
        // El deslizador va de 0 a (máximo - mínimo); el valor real es mínimo + posición.
        campoTamano.max = Ajustes.ALTURA_MAXIMA - Ajustes.ALTURA_MINIMA
        campoTamano.progress = ajustes.alturaTecla - Ajustes.ALTURA_MINIMA
        mostrarTamano()
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
        ajustes.alturaTecla = Ajustes.ALTURA_MINIMA + campoTamano.progress
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
     *     dictadoime://config?url=...&token=...&clave=...
     *
     * Sirve para dejar listo otro celular sin escribir nada a mano (se lo
     * puedes mandar por WhatsApp y lo tocas).
     */
    private fun aplicarEnlace(entrada: Intent?) {
        val datos = entrada?.data ?: return
        if (datos.scheme != "dictadoime") return

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
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), CODIGO_MICRO)
        }
    }

    override fun onDestroy() {
        alcance.cancel()
        super.onDestroy()
    }

    companion object {
        /** Acción con la que la teja rápida abre esta pantalla para el selector. */
        const val ACCION_SELECTOR = "com.ivanuxs.dictadoime.SELECTOR"

        private const val CODIGO_MICRO = 1001
    }
}
