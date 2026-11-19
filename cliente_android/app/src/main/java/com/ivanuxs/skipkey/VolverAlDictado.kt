package com.ivanuxs.skipkey

import android.content.Context
import android.view.inputmethod.InputMethodManager

/**
 * Vuelve al teclado de dictado desde otro teclado.
 *
 * Hace falta porque, al saltar a Gboard para editar a mano, la vuelta depende
 * del otro teclado (su tecla de globo) y no siempre está a la vista.
 *
 * ⚠️ Android **no** deja que una app elija el teclado por sí sola:
 * `InputMethodManager.setInputMethod(...)` solo vale desde el propio teclado
 * mientras es el activo, y sin testigo exige `WRITE_SECURE_SETTINGS` (permiso
 * de apps de sistema). La única vía real es el selector del sistema: se toca
 * «Dictado por voz» en la lista y cambia.
 */
object VolverAlDictado {

    /** Muestra el selector de teclados del sistema (API pública). */
    fun volver(contexto: Context) {
        val gestor = contexto.getSystemService(Context.INPUT_METHOD_SERVICE)
            as InputMethodManager
        gestor.showInputMethodPicker()
    }
}
