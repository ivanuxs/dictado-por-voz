package com.ivanuxs.skipkey

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Cuenta del usuario en el puente: entrar, crear, confirmar el correo y ver el
 * plan.
 *
 * La app se publica en Google Play y cualquiera puede crear su cuenta desde
 * aquí. Sin cuenta se puede usar el teclado igual: dicta con el motor del
 * celular, que es gratis. La cuenta es lo que da acceso al servidor y a las
 * funciones avanzadas (planes de pago).
 *
 * La contraseña no se guarda: se manda una vez y lo que queda en el celular es
 * el token del dispositivo, cifrado.
 */
class CuentaActivity : AppCompatActivity() {

    private lateinit var ajustes: Ajustes
    private lateinit var cliente: ClienteCuenta
    private lateinit var clientePerfil: ClientePerfil
    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private lateinit var textoCuenta: TextView
    private lateinit var textoPlan: TextView
    private lateinit var textoResultado: TextView
    private lateinit var bloqueEntrar: LinearLayout
    private lateinit var bloqueCuenta: LinearLayout
    private lateinit var campoUsuario: EditText
    private lateinit var campoEmail: EditText
    private lateinit var campoContrasena: EditText
    private lateinit var campoCodigo: EditText
    private lateinit var botonVerificar: Button
    private lateinit var botonEntrar: Button
    private lateinit var botonCrear: Button
    private lateinit var botonSincronizar: Button
    private lateinit var botonActualizar: Button
    private lateinit var botonSalir: Button
    private lateinit var botonBorrar: Button
    private lateinit var botonPlanes: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_cuenta)
        ajustes = Ajustes(this)
        cliente = ClienteCuenta(this)
        clientePerfil = ClientePerfil(this)

        textoCuenta = findViewById(R.id.texto_cuenta)
        textoPlan = findViewById(R.id.texto_plan)
        textoResultado = findViewById(R.id.texto_resultado)
        bloqueEntrar = findViewById(R.id.bloque_entrar)
        bloqueCuenta = findViewById(R.id.bloque_cuenta)
        campoUsuario = findViewById(R.id.campo_usuario)
        campoEmail = findViewById(R.id.campo_email)
        campoContrasena = findViewById(R.id.campo_contrasena)
        campoCodigo = findViewById(R.id.campo_codigo)
        botonVerificar = findViewById(R.id.boton_verificar)
        botonEntrar = findViewById(R.id.boton_entrar)
        botonCrear = findViewById(R.id.boton_crear)
        botonSincronizar = findViewById(R.id.boton_sincronizar)
        botonActualizar = findViewById(R.id.boton_actualizar)
        botonSalir = findViewById(R.id.boton_salir)
        botonBorrar = findViewById(R.id.boton_borrar)
        botonPlanes = findViewById(R.id.boton_planes)

        campoUsuario.setText(ajustes.usuario)
        campoEmail.setText(ajustes.email)

        botonEntrar.setOnClickListener { entrar() }
        botonCrear.setOnClickListener { crear() }
        botonVerificar.setOnClickListener { verificar() }
        botonSincronizar.setOnClickListener { sincronizar(avisar = true) }
        botonActualizar.setOnClickListener { actualizar(avisar = true) }
        botonSalir.setOnClickListener { preguntarSalir() }
        botonBorrar.setOnClickListener { preguntarBorrar() }
        botonPlanes.setOnClickListener { verPlanes() }
        findViewById<Button>(R.id.boton_volver).setOnClickListener { finish() }

        refrescar()
    }

    override fun onResume() {
        super.onResume()
        refrescar()
        // Al volver se piden los cupos de hoy: el plan lo manda el puente.
        if (ajustes.haySesion) actualizar(avisar = false)
    }
    override fun onDestroy() {
        alcance.cancel()
        super.onDestroy()
    }

    // --- Pintado ---------------------------------------------------------

    private fun refrescar() {
        val hay = ajustes.haySesion
        bloqueEntrar.isVisible = !hay
        bloqueCuenta.isVisible = hay
        if (hay) {
            val nombre = ajustes.planNombre.ifBlank { ajustes.plan.ifBlank { "—" } }
            textoCuenta.text = ajustes.usuario.ifBlank { ajustes.email }
            textoPlan.text = getString(R.string.cuenta_plan, nombre) + describirUso()
        } else {
            campoCodigo.isVisible = false
            botonVerificar.isVisible = false
            textoCuenta.text = getString(R.string.cuenta_sin_sesion)
            textoPlan.text = ""
        }
    }

    /** «· 128 min de servidor y 42 traducciones hoy», cuando se sabe. */
    private fun describirUso(): String {
        val minutos = ajustes.minutosRestantes
        val traducciones = ajustes.traduccionesRestantes
        val partes = ArrayList<String>()
        if (minutos >= 0) partes.add("$minutos min de servidor")
        if (traducciones >= 0) partes.add("$traducciones traducciones")
        return if (partes.isEmpty()) "" else "\nQuedan hoy: " + partes.joinToString(" y ")
    }

    private fun aviso(texto: String) {
        textoResultado.text = texto
    }

    private fun esperando(activo: Boolean) {
        botonEntrar.isEnabled = activo
        botonCrear.isEnabled = activo
        botonVerificar.isEnabled = activo
        botonSincronizar.isEnabled = activo
        botonActualizar.isEnabled = activo
        botonSalir.isEnabled = activo
        botonBorrar.isEnabled = activo
        botonPlanes.isEnabled = activo
    }

    // --- Acciones --------------------------------------------------------

    /** Lo escrito en la pantalla. El correo es opcional: solo para recuperar. */
    private data class Escrito(val usuario: String, val email: String, val clave: String)

    private fun datosEscritos(): Escrito? {
        val usuario = campoUsuario.text.toString().trim()
        val email = campoEmail.text.toString().trim()
        val clave = campoContrasena.text.toString()
        if (usuario.isBlank() || clave.isBlank()) {
            aviso(getString(R.string.cuenta_falta_datos))
            return null
        }
        return Escrito(usuario, email, clave)
    }

    private fun entrar() {
        val datos = datosEscritos() ?: return
        conRed({ cliente.entrar(datos.usuario, datos.clave) }) { resultado ->
            cuandoBien(resultado) {
                campoContrasena.setText("")
                aviso(getString(R.string.cuenta_entrada, datos.usuario))
                // Recién entrada: se trae los ajustes del perfil (si este
                // aparato nunca los sincronizó) y el diccionario de la cuenta.
                conRed({ clientePerfil.bajarAjustes() }) { _ ->
                    sincronizar(avisar = false, subirAjustesAhora = false)
                }
            }
        }
    }

    private fun crear() {
        val datos = datosEscritos() ?: return
        if (datos.clave.length < 8) {
            aviso(getString(R.string.cuenta_clave_corta))
            return
        }
        conRed({ cliente.registrar(datos.usuario, datos.clave, datos.email) }) { resultado ->
            cuandoBien(resultado) { bien ->
                if (!bien.datos.optBoolean("verificado", true)) {
                    // Hay que confirmar el correo: se queda el correo puesto y
                    // aparece el campo del código.
                    campoCodigo.isVisible = true
                    botonVerificar.isVisible = true
                    aviso(
                        bien.datos.optString(
                            "mensaje", getString(R.string.cuenta_revisa_correo)
                        )
                    )
                } else {
                    campoContrasena.setText("")
                    aviso(getString(R.string.cuenta_creada))
                }
            }
        }
    }

    private fun verificar() {
        val email = campoEmail.text.toString().trim()
        val codigo = campoCodigo.text.toString().trim()
        if (email.isBlank() || codigo.isBlank()) {
            aviso(getString(R.string.cuenta_falta_codigo))
            return
        }
        conRed({ cliente.verificar(email, codigo) }) { resultado ->
            cuandoBien(resultado) {
                campoContrasena.setText("")
                campoCodigo.setText("")
                aviso(getString(R.string.cuenta_verificada))
            }
        }
    }

    private fun actualizar(avisar: Boolean) {
        if (!ajustes.haySesion) return
        conRed({ cliente.estado() }) { resultado ->
            if (resultado is ResultadoCuenta.Error && avisar) aviso(resultado.motivo)
        }
    }

    /**
     * Sincroniza el perfil de la cuenta.
     *
     * El diccionario y las correcciones se bajan, se fusionan y se suben con
     * las reglas del puente. Los ajustes solo se suben cuando el usuario lo
     * pide con el botón: pisar las casillas del otro aparato sin preguntar
     * sería peor que no sincronizarlas.
     */
    private fun sincronizar(avisar: Boolean, subirAjustesAhora: Boolean = avisar) {
        if (!ajustes.haySesion) return
        alcance.launch {
            esperando(false)
            val dicc = Diccionario(this@CuentaActivity, ajustes.perfil)
            var motivo = ""
            try {
                dicc.cargar()
                withContext(Dispatchers.IO) { clientePerfil.sincronizar(dicc) }.let {
                    if (it is ResultadoCuenta.Error) motivo = it.motivo
                }
                if (motivo.isEmpty() && subirAjustesAhora) {
                    withContext(Dispatchers.IO) { clientePerfil.subirAjustes() }.let {
                        if (it is ResultadoCuenta.Error) motivo = it.motivo
                    }
                }
                // Hay que volcar antes de soltar la instancia: el guardado
                // diferido del diccionario se cancela al descartarla.
                withContext(Dispatchers.IO) { dicc.volcar() }
            } finally {
                dicc.descartar()
            }
            if (avisar) {
                aviso(motivo.ifBlank { getString(R.string.cuenta_sincronizado) })
            }
            esperando(true)
            refrescar()
        }
    }

    /**
     * Al cerrar sesión se borra lo aprendido en este celular.
     *
     * Evita que la siguiente persona que use el teléfono herede el léxico de la
     * anterior. Lo que ya está en la cuenta no se pierde: vive en el puente.
     */
    private fun borrarAprendidoLocal() {
        alcance.launch {
            val dicc = Diccionario(this@CuentaActivity, ajustes.perfil)
            try {
                dicc.cargar()
                withContext(Dispatchers.IO) { dicc.borrarAprendido() }
            } finally {
                dicc.descartar()
            }
            // Que un teclado vivo suelte el suyo y vuelva a leer del disco.
            ajustes.generacionDiccionario += 1
        }
    }

    private fun preguntarSalir() {
        AlertDialog.Builder(this)
            .setTitle(R.string.cuenta_salir)
            .setMessage(R.string.cuenta_salir_pregunta)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.cuenta_salir) { _, _ ->
                conRed({ cliente.salir() }) { _ ->
                    aviso(getString(R.string.cuenta_sesion_cerrada))
                    borrarAprendidoLocal()
                }
            }
            .show()
    }

    private fun preguntarBorrar() {
        AlertDialog.Builder(this)
            .setTitle(R.string.cuenta_borrar)
            .setMessage(R.string.cuenta_borrar_pregunta)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.cuenta_borrar) { _, _ ->
                conRed({ cliente.borrarCuenta() }) { _ ->
                    aviso(getString(R.string.cuenta_borrada))
                }
            }
            .show()
    }

    // --- Planes ----------------------------------------------------------

    /**
     * Enseña el catálogo de planes que publica el puente (`GET /api/v1/planes`).
     *
     * Es solo informativo: aquí no se cobra ni se cambia nada. El plan lo manda
     * siempre el puente, y los cambios se piden al administrador (el cobro
     * dentro de la app llegará con Google Play Billing).
     */
    private fun verPlanes() {
        alcance.launch {
            esperando(false)
            val resultado = withContext(Dispatchers.IO) { cliente.planes() }
            when (resultado) {
                is ResultadoCuenta.Bien -> mostrarPlanes(resultado.datos)
                is ResultadoCuenta.Error -> aviso(
                    getString(R.string.cuenta_planes_error, resultado.motivo)
                )
            }
            esperando(true)
        }
    }

    private fun mostrarPlanes(datos: JSONObject) {
        val planes = datos.optJSONArray("planes") ?: return
        val texto = StringBuilder()
        for (i in 0 until planes.length()) {
            val plan = planes.optJSONObject(i) ?: continue
            val nombre = plan.optString("nombre")
            val esElMio = plan.optString("plan").isNotBlank() &&
                plan.optString("plan") == ajustes.plan
            texto.append(if (esElMio) "• $nombre ← ${getString(R.string.cuenta_planes_actual)}" else "• $nombre")
            texto.append("\n").append(describirPlan(plan)).append("\n\n")
        }
        texto.append(getString(R.string.cuenta_planes_ayuda))
        AlertDialog.Builder(this)
            .setTitle(R.string.cuenta_planes_titulo)
            .setMessage(texto.toString().trim())
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    /** Tres o cuatro líneas con lo que da un plan, sacadas del JSON del puente. */
    private fun describirPlan(plan: JSONObject): String {
        val partes = ArrayList<String>()
        if (plan.optBoolean("servidor")) {
            partes.add(getString(R.string.cuenta_planes_min, plan.optInt("minutos_dia")))
            partes.add(getString(R.string.cuenta_planes_trad, plan.optInt("traducciones_dia")))
        } else {
            partes.add(getString(R.string.cuenta_planes_motor))
        }
        if (plan.optBoolean("vivo")) partes.add(getString(R.string.cuenta_planes_vivo))
        partes.add(getString(R.string.cuenta_planes_dispositivos, plan.optInt("dispositivos")))
        return partes.joinToString("\n") { "    · $it" }
    }

    // --- Red -------------------------------------------------------------

    /**
     * Lanza una llamada al puente sin congelar la pantalla.
     *
     * Si sale bien, guarda la sesión (token y plan) y repinta. Si sale mal,
     * enseña el motivo tal como lo dio el puente: son mensajes pensados para
     * que el usuario los lea.
     */
    private fun conRed(
        llamada: () -> ResultadoCuenta,
        despues: (ResultadoCuenta) -> Unit
    ) {
        alcance.launch {
            esperando(false)
            val resultado = withContext(Dispatchers.IO) { llamada() }
            if (resultado is ResultadoCuenta.Bien) {
                cliente.guardarSesion(resultado.datos)
                refrescar()
            }
            if (resultado is ResultadoCuenta.Error) aviso(resultado.motivo)
            despues(resultado)
            esperando(true)
        }
    }

    private fun cuandoBien(
        resultado: ResultadoCuenta,
        accion: (ResultadoCuenta.Bien) -> Unit
    ) {
        if (resultado is ResultadoCuenta.Bien) accion(resultado)
    }
}
