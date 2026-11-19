"""Rutas de cuenta: registro, verificación, login y estado del plan.

El registro está ABIERTO a propósito: la app se publica en Google Play y
cualquiera que la descargue tiene que poder crearse su cuenta sin pedir permiso
a nadie. Lo que sí se limita es el abuso: nginx corta las ráfagas por IP sobre
`/registro` y `/login` (ver nginx/dictado-sitio.conf).

Las respuestas buenas llevan `"status": "ok"`; los errores los convierte el
manejador global de `servidor_web.py` a `{"status": "error", "motivo": ...}`,
que es lo que lee el teclado.
"""

from fastapi import APIRouter, HTTPException, Request

import auth
import config
import correo
import planes
import usuarios
from utils_logging import imprimir

router = APIRouter(prefix="/api/v1", tags=["cuentas"])


async def _cuerpo(request):
    """JSON del cuerpo como diccionario (400 si no lo es)."""
    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Se esperaba un JSON con los datos de la cuenta."
        ) from None
    if not isinstance(datos, dict):
        raise HTTPException(status_code=400, detail="Se esperaba un objeto JSON.")
    return datos


def _dispositivo(datos):
    return str(datos.get("dispositivo") or "")[:60]


def _estado_de_cuenta(usuario, con_uso=True):
    """Lo que el teclado necesita saber de la cuenta, su plan y su perfil."""
    uso = usuarios.uso_hoy(usuario["id"]) if (con_uso and usuario.get("id")) else None
    respuesta = {
        "status": "ok",
        "usuario": usuario,
        "plan": planes.cupos(usuario, uso),
    }
    if usuario.get("id"):
        # El perfil va desde el primer momento: el teclado guarda ahí su
        # diccionario cuando el plan lo permite, y su id es lo que lo separa
        # de cualquier otra cuenta sin mirar el aparato.
        respuesta["perfil"] = usuarios.perfil_de(usuario["id"])
    return respuesta


async def _mandar_codigo(usuario):
    """Crea el código y lo envía. Lanza 503 si el correo falla."""
    codigo = usuarios.crear_codigo(usuario["id"])
    try:
        await correo.enviar_async(
            usuario["email"],
            "Código para confirmar tu cuenta de dictado",
            correo.texto_codigo(codigo),
        )
    except Exception as e:  # smtplib lanza varios tipos según el fallo
        imprimir(f"[PUENTE] No se pudo enviar el correo a {usuario['email']}: {e}")
        raise HTTPException(
            status_code=503,
            detail=(
                "La cuenta se creó, pero no se pudo enviar el correo de "
                "confirmación. Pide que te lo reenvíen en unos minutos."
            ),
        ) from e
    imprimir(f"[PUENTE] Código de verificación enviado a {usuario['email']}.")


# ---------------------------------------------------------------------------
# Registro y verificación
# ---------------------------------------------------------------------------
@router.post("/registro")
async def registro(request: Request):
    """Crea una cuenta. Abierto: es lo que necesita la app de Play.

    La identidad es el **nombre de usuario**. El correo es opcional: solo hace
    falta si este servidor verifica correos (`DICTADO_VERIFICAR_CORREO`).
    """
    if not config.REGISTRO:
        raise HTTPException(
            status_code=403, detail="El registro está cerrado en este servidor."
        )
    datos = await _cuerpo(request)
    nombre = usuarios.normalizar_usuario(str(datos.get("usuario") or ""))
    email = usuarios.normalizar_email(str(datos.get("email") or "")) or None
    clave = str(datos.get("contrasena") or datos.get("clave") or "")
    dispositivo = _dispositivo(datos)

    if usuarios.buscar_por_usuario(nombre) is not None:
        raise HTTPException(
            status_code=409,
            detail="Ese nombre de usuario ya está ocupado. Prueba con otro.",
        )
    if email and usuarios.buscar_por_email(email) is not None:
        raise HTTPException(
            status_code=409,
            detail="Ya hay una cuenta con ese correo. Entra con tu contraseña.",
        )
    try:
        usuario = usuarios.crear_usuario(
            nombre, clave, email=email, plan=config.PLAN_POR_DEFECTO
        )
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    imprimir(f"[PUENTE] Cuenta nueva: {usuario['usuario']} (plan {usuario['plan']}).")

    if config.VERIFICAR_CORREO and email:
        await _mandar_codigo(usuario)
        return {
            "status": "ok",
            "verificado": False,
            "usuario": usuario["usuario"],
            "email": usuario["email"],
            "mensaje": f"Te enviamos un código a {usuario['email']}.",
        }

    token = usuarios.crear_token(
        usuario["id"], dispositivo, planes.limite_dispositivos(usuario)
    )
    respuesta = _estado_de_cuenta(usuario)
    respuesta["verificado"] = True
    respuesta["token"] = token
    respuesta["mensaje"] = "Cuenta creada."
    return respuesta


@router.post("/verificar")
async def verificar(request: Request):
    """Confirma el correo con el código y devuelve el token del dispositivo."""
    datos = await _cuerpo(request)
    email = usuarios.normalizar_email(str(datos.get("email") or ""))
    codigo = str(datos.get("codigo") or "")
    dispositivo = _dispositivo(datos)
    try:
        usuario = usuarios.comprobar_codigo(email, codigo)
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    token = usuarios.crear_token(
        usuario["id"], dispositivo, planes.limite_dispositivos(usuario)
    )
    imprimir(f"[PUENTE] Correo verificado: {usuario['email']}.")
    respuesta = _estado_de_cuenta(usuario)
    respuesta["verificado"] = True
    respuesta["token"] = token
    return respuesta


@router.post("/reenviar")
async def reenviar(request: Request):
    """Manda otro código de verificación (caducó o no llegó)."""
    datos = await _cuerpo(request)
    email = usuarios.normalizar_email(str(datos.get("email") or ""))
    fila = usuarios.buscar_por_email(email)
    if fila is None:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta con ese correo.")
    usuario = usuarios.publico(fila)
    if usuario["verificado"]:
        raise HTTPException(status_code=409, detail="Ese correo ya está confirmado.")
    if not config.VERIFICAR_CORREO:
        raise HTTPException(
            status_code=503, detail="Este servidor no verifica correos."
        )
    await _mandar_codigo(usuario)
    return {"status": "ok", "mensaje": f"Te enviamos otro código a {usuario['email']}."}


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------
@router.post("/login")
async def login(request: Request):
    """Entra con usuario (o correo) y contraseña; devuelve el token de este dispositivo."""
    datos = await _cuerpo(request)
    identificador = str(
        datos.get("usuario") or datos.get("email") or datos.get("nombre") or ""
    )
    clave = str(datos.get("contrasena") or datos.get("clave") or "")
    dispositivo = _dispositivo(datos)

    fila = usuarios.buscar_para_entrar(identificador)
    if fila is None or not usuarios.comprobar_clave(fila["id"], clave):
        raise HTTPException(
            status_code=401, detail="Usuario o contraseña incorrectos."
        )
    usuario = usuarios.publico(fila)
    if not usuario["activo"]:
        raise HTTPException(status_code=403, detail="Esa cuenta está desactivada.")

    if config.VERIFICAR_CORREO and not usuario["verificado"]:
        raise HTTPException(
            status_code=403,
            detail="Falta confirmar tu correo. Revisa el código que te enviamos.",
        )

    try:
        token = usuarios.crear_token(
            usuario["id"], dispositivo, planes.limite_dispositivos(usuario)
        )
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    respuesta = _estado_de_cuenta(usuario)
    respuesta["token"] = token
    return respuesta


@router.post("/salir")
async def salir(request: Request):
    """Cierra la sesión de ESTE dispositivo (no la de los demás)."""
    token = auth.token_de_cabecera(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail="Falta el token de la sesión.")
    usuarios.revocar_token(token)
    return {"status": "ok", "mensaje": "Sesión cerrada en este dispositivo."}


@router.get("/yo")
async def yo(request: Request):
    """Cuenta, plan y consumo de hoy. El teclado lo pide al abrir los ajustes."""
    usuario = await auth.verificar_peticion(request)
    return _estado_de_cuenta(usuario)


@router.post("/contrasena")
async def contrasena(request: Request):
    """Cambia la contraseña de la cuenta autenticada."""
    usuario = await auth.verificar_peticion(request)
    if not usuario.get("id"):
        raise HTTPException(
            status_code=403, detail="El token maestro no tiene contraseña que cambiar."
        )
    datos = await _cuerpo(request)
    actual = str(datos.get("actual") or "")
    nueva = str(datos.get("nueva") or "")
    if not usuarios.comprobar_clave(usuario["id"], actual):
        raise HTTPException(status_code=401, detail="La contraseña actual no es correcta.")
    try:
        usuarios.actualizar(usuario["id"], clave=nueva)
    except usuarios.ErrorUsuario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    # Se cierran las otras sesiones: si cambias la contraseña es porque algo pasó.
    usuarios.revocar_todos(usuario["id"])
    return {
        "status": "ok",
        "mensaje": "Contraseña cambiada. Vuelve a entrar en tus dispositivos.",
    }


@router.delete("/cuenta")
async def borrar_cuenta(request: Request):
    """Borra la cuenta y todo lo suyo. Google Play lo exige."""
    usuario = await auth.verificar_peticion(request)
    if not usuario.get("id"):
        raise HTTPException(
            status_code=403, detail="El token maestro no es una cuenta que borrar."
        )
    usuarios.borrar(usuario["id"])
    imprimir(f"[PUENTE] Cuenta borrada: {usuario['usuario']}.")
    return {"status": "ok", "mensaje": "Cuenta eliminada."}


# ---------------------------------------------------------------------------
# Planes (público: se ven sin cuenta, para poder enseñarlos en la app)
# ---------------------------------------------------------------------------
@router.get("/planes")
async def catalogo_planes():
    return {
        "status": "ok",
        "planes": planes.catalogo(),
        "plan_por_defecto": config.PLAN_POR_DEFECTO,
    }
