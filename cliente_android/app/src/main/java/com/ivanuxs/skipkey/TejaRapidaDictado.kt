package com.ivanuxs.skipkey

import android.content.Intent
import android.graphics.drawable.Icon
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * Teja de ajustes rápidos: vuelve al teclado de dictado desde cualquier app.
 *
 * Es la salida cuando el otro teclado (Gboard, por ejemplo) no ofrece una tecla
 * clara para volver: se baja el panel de ajustes rápidos, se toca la teja y sale
 * el **selector de teclados**; al tocar «Dictado por voz» vuelve el nuestro.
 *
 * Para que aparezca hay que añadirla al panel: en la app hay un botón que pide a
 * Android que se la ofrezca al usuario (Android 13+) o que explica cómo hacerlo.
 */
class TejaRapidaDictado : TileService() {

    override fun onStartListening() {
        super.onStartListening()
        qsTile?.apply {
            label = getString(R.string.ajustes_volver)
            icon = Icon.createWithResource(this@TejaRapidaDictado, R.drawable.ic_micro)
            state = Tile.STATE_INACTIVE
            updateTile()
        }
    }

    override fun onClick() {
        super.onClick()
        // El selector se pide desde la app: si se pidiera desde este servicio, el
        // diálogo podría quedar por detrás del panel de ajustes rápidos.
        startActivityAndCollapse(
            Intent(this, AjustesActivity::class.java)
                .setAction(AjustesActivity.ACCION_SELECTOR)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }
}
