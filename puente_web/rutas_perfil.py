"""Perfil del usuario: sus ajustes y lo que ha aprendido dictando.

Esto es lo que hace que cada cuenta sea distinta de las demás:

- **Ajustes**: qué tiene activado (escucha, comandos, teclas, idioma de
  traducción…). Son preferencias del usuario, no permisos: el plan decide qué
  *puede* usar (`planes.exigir`) y esto decide qué *tiene* activado.
- **Diccionario y sustituciones**: las palabras que ha enseñado y las
  correcciones del dictado (`antes` → `despues`).

El perfil cuelga de la cuenta, así que el teclado y la PC de la misma persona
comparten lo aprendido, y dos cuentas en el mismo celular no se ven nada.

La sincronización entre dispositivos es de pago (`sincronizacion` en
`planes.py`); sin ella, el cliente aprende en local y ya. Los ajustes se
guardan con cualquiera de los dos, porque son cuatro casillas y no cambian el
costo del servidor.

Las respuestas buenas llevan `"status": "ok"`; los errores los convierte el
manejador global de `servidor_web.py` a `{"status": "error", "motivo": ...}`.
"""

from fastapi import APIRouter, HTTPException, Request

import auth
import planes
import usuarios

router = APIRouter(prefix="/api/v1/perfil", tags=["perfil"])


async def _cuerpo(request):
    """JSON del cuerpo como diccionario (400 si no lo es)."""
    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Se esperaba un JSON con los datos del perfil."
        ) from None
    if not isinstance(datos, dict):
        raise HTTPException(status_code=400, detail="Se esperaba un objeto JSON.")
    return datos


def _cuenta(usuario):
    """Una cuenta de verdad: el token maestro no tiene perfil."""
    if usuario.get("es_maestro") or not usuario.get("id"):
        raise HTTPException(
            status_code=403,
            detail=(
                "El token maestro no tiene perfil ni diccionario. "
                "Entra con tu nombre de usuario y contraseña."
            ),
        )
    return usuario


async def _perfil(request):
    """Atajo: cuenta autenticada que sí puede tener perfil."""
    return _cuenta(await auth.verificar_peticion(request))


def _con_sincronizacion(usuario):
    """Diccionario y correcciones: solo si el plan incluye sincronizar.

    El teclado sigue aprendiendo en local con el plan Gratis; lo que no puede
    es compartirlo entre aparatos.
    """
    planes.exigir(usuario, "sincronizacion")
    return usuario


# ---------------------------------------------------------------------------
# Perfil y ajustes
# ---------------------------------------------------------------------------
@router.get("")
async def ver_perfil(request: Request):
    """Perfil de la cuenta que pide (lo crea si es la primera vez)."""
    usuario = await _perfil(request)
    return {"status": "ok", "perfil": usuarios.perfil_de(usuario["id"])}


@router.put("/ajustes")
async def guardar_ajustes(request: Request):
    """Guarda los ajustes del perfil.

    Si el cuerpo trae `revision` y no coincide con la del servidor, otro
    dispositivo guardó antes: se responde `"status": "conflicto"` con el perfil
    bueno, para que el cliente decida entre pisar o recargar. Nunca se pierde
    la versión del servidor en silencio.
    """
    usuario = await _perfil(request)
    datos = await _cuerpo(request)
    ajustes = datos.get("ajustes")
    if ajustes is None:
        raise HTTPException(status_code=422, detail="Faltan los ajustes.")
    try:
        perfil, choco = usuarios.guardar_ajustes(
            usuario["id"], ajustes, datos.get("revision")
        )
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if choco:
        return {
            "status": "conflicto",
            "perfil": perfil,
            "motivo": (
                "Los ajustes cambiaron en otro dispositivo. Decide si los de "
                "aquí pisan a los de allá o al revés."
            ),
        }
    return {"status": "ok", "perfil": perfil}


# ---------------------------------------------------------------------------
# Diccionario
# ---------------------------------------------------------------------------
@router.get("/diccionario")
async def ver_diccionario(request: Request, desde: str | None = None):
    """Palabras aprendidas; con `desde`, solo las cambiadas después de esa fecha."""
    usuario = _con_sincronizacion(await _perfil(request))
    palabras = usuarios.diccionario_desde(usuario["id"], desde)
    return {"status": "ok", "total": len(palabras), "palabras": palabras}


@router.post("/diccionario")
async def subir_diccionario(request: Request):
    """Une lo aprendido en el aparato y devuelve el diccionario resultante."""
    usuario = _con_sincronizacion(await _perfil(request))
    datos = await _cuerpo(request)
    palabras = datos.get("palabras")
    if not isinstance(palabras, list):
        raise HTTPException(
            status_code=422, detail="Se esperaba una lista en «palabras»."
        )
    try:
        cambiadas, actuales = usuarios.fusionar_diccionario(usuario["id"], palabras)
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "status": "ok",
        "cambiadas": cambiadas,
        "total": len(actuales),
        "palabras": actuales,
    }


# ---------------------------------------------------------------------------
# Sustituciones (correcciones del dictado: «antes» → «despues»)
# ---------------------------------------------------------------------------
@router.get("/sustituciones")
async def ver_sustituciones(request: Request, desde: str | None = None):
    usuario = _con_sincronizacion(await _perfil(request))
    correcciones = usuarios.sustituciones_desde(usuario["id"], desde)
    return {"status": "ok", "total": len(correcciones), "sustituciones": correcciones}


@router.post("/sustituciones")
async def subir_sustituciones(request: Request):
    """Une las correcciones del aparato y devuelve las del perfil."""
    usuario = _con_sincronizacion(await _perfil(request))
    datos = await _cuerpo(request)
    correcciones = datos.get("sustituciones")
    if not isinstance(correcciones, list):
        raise HTTPException(
            status_code=422, detail="Se esperaba una lista en «sustituciones»."
        )
    try:
        cambiadas, actuales = usuarios.fusionar_sustituciones(
            usuario["id"], correcciones
        )
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "status": "ok",
        "cambiadas": cambiadas,
        "total": len(actuales),
        "sustituciones": actuales,
    }
