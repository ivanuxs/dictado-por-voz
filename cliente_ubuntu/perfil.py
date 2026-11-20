"""Cuenta y perfil del usuario en el puente web, desde el escritorio.

Habla con el **puente**, no con el servidor de dictado: el puente es quien tiene
las cuentas, los planes y el perfil (ajustes y diccionario). El dictado sigue
yendo por ZMQ como siempre; esto solo añade identidad y sincronización.

Tres reglas que no se rompen:

1. **El diccionario local manda.** Si no hay red, se dicta igual y se aprende
   igual; lo que se pospone es subirlo.
2. **Primero se baja, luego se sube.** Al revés, un aparato con datos viejos
   pisaría lo bueno del otro.
3. **La contraseña no se guarda.** Solo el token, en `sesion.json` (ver
   `sesion.py`).

La dirección del puente y la clave del proxy se leen de `comandos.json`
(`"puente"` y `"puente_clave"`), que es lo que edita la pestaña «Cuenta».
Como ese JSON se publica, la clave también puede venir de la variable de
entorno `DICTADO_PUENTE_CLAVE` (ver `clave_proxy`).
"""

import json
import os
import platform
import sys
import urllib.error
import urllib.request

import sesion

# Planes que incluyen sincronizar (el puente es quien manda; esto solo evita
# mandar peticiones que van a recibir un 403).
CON_SINCRONIZACION = ("basico", "pro", "maestro")


class ErrorPerfil(Exception):
    """Fallo con un mensaje que se puede enseñar tal cual."""


def direccion(config):
    """URL del puente, sin barra final."""
    return str((config or {}).get("puente") or "").strip().rstrip("/")


def configurado(config):
    return bool(direccion(config))


def dispositivo():
    """Nombre del aparato, para que el usuario reconozca sus sesiones."""
    nombre = platform.node() or platform.system() or "escritorio"
    return nombre[:60]


try:  # módulo generado al compilar (ver empaquetado/); no está en el repositorio
    from clave_proxy_compilada import CLAVE as _CLAVE_COMPILADA
except Exception:  # sin módulo (modo fuente): manda el entorno o el JSON
    _CLAVE_COMPILADA = ""


def _clave_del_archivo():
    """Clave del proxy en `clave_proxy.txt`, junto al programa (si existe).

    Permite cambiar la clave sin recompilar: se reemplaza el archivo y se
    reinicia el cliente. El archivo no se versiona (`.gitignore`).
    """
    try:
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        with open(
            os.path.join(base, "clave_proxy.txt"), encoding="utf-8"
        ) as archivo:
            return archivo.readline().strip()
    except OSError:
        return ""


def clave_proxy(config):
    """Clave del proxy (`X-Clave-Cliente`) que exige nginx.

    Orden de resolución:

    1. `DICTADO_PUENTE_CLAVE` del entorno (para probar o rotar sin recompilar).
    2. La clave **incrustada al compilar** (`clave_proxy_compilada`), igual que
       en el APK: el usuario final no tiene que saber que existe.
    3. `clave_proxy.txt` junto al programa, para cambiar la clave sin recompilar.
    4. `puente_clave` del JSON: solo si alguien la escribió a mano en la ventana.

    El JSON del repositorio es público, así que nunca lleva la clave.
    """
    del_entorno = (os.environ.get("DICTADO_PUENTE_CLAVE") or "").strip()
    if del_entorno:
        return del_entorno
    if _CLAVE_COMPILADA:
        return str(_CLAVE_COMPILADA).strip()
    del_archivo = _clave_del_archivo()
    if del_archivo:
        return del_archivo
    return str((config or {}).get("puente_clave") or "").strip()


def _cabeceras(config, con_token=True):
    cabeceras = {"Content-Type": "application/json"}
    clave = clave_proxy(config)
    if clave:
        cabeceras["X-Clave-Cliente"] = clave
    token = sesion.token()
    if con_token and token:
        cabeceras["Authorization"] = f"Bearer {token}"
    return cabeceras


def _pedir(config, metodo, ruta, cuerpo=None, con_token=True, espera=20):
    """Una petición al puente. Lanza ErrorPerfil con el motivo del servidor.

    Se usa `urllib` (biblioteca estándar) a propósito: el cliente no gana una
    dependencia nueva por hablar de cuentas, y funciona igual en Ubuntu y en el
    Windows empaquetado.
    """
    base = direccion(config)
    if not base:
        raise ErrorPerfil(
            "Falta la dirección del puente. Ponla en Configuración → Cuenta."
        )

    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    peticion = urllib.request.Request(base + ruta, data=datos, method=metodo)
    for cabecera, valor in _cabeceras(config, con_token).items():
        peticion.add_header(cabecera, valor)

    try:
        with urllib.request.urlopen(peticion, timeout=espera) as respuesta:
            texto = respuesta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        if e.code == 403 and not clave_proxy(config):
            raise ErrorPerfil(
                "El servidor rechazó la conexión (403): a este cliente le "
                "falta la clave del proxy. No es tu cuenta: avisa a quien "
                "instaló el cliente."
            ) from e
        motivo = ""
        try:
            fallo = json.loads(texto)
            if isinstance(fallo, dict):
                motivo = str(fallo.get("motivo") or fallo.get("detail") or "")
        except ValueError:
            pass
        raise ErrorPerfil(motivo or f"El puente respondió {e.code}.") from e
    except urllib.error.URLError as e:
        raise ErrorPerfil(f"No se pudo contactar con el puente: {e.reason}") from e
    except OSError as e:
        raise ErrorPerfil(f"No se pudo contactar con el puente: {e}") from e

    try:
        devuelto = json.loads(texto)
    except ValueError:
        devuelto = {}
    return devuelto if isinstance(devuelto, dict) else {}


def _guardar_sesion(datos):
    """Guarda token, usuario y perfil de la respuesta del puente."""
    usuario = datos.get("usuario") or {}
    perfil = datos.get("perfil") or {}
    plan = datos.get("plan") or {}
    sesion.guardar(
        {
            "token": datos.get("token") or "",
            "usuario": usuario.get("usuario") or "",
            "email": usuario.get("email") or "",
            "plan": usuario.get("plan") or plan.get("plan") or "",
            "plan_nombre": plan.get("nombre") or "",
            "id_perfil": perfil.get("id") or 0,
            "perfil": perfil.get("nombre") or "personal",
            "revision": perfil.get("revision") or 0,
        }
    )
    return datos


# ---------------------------------------------------------------------------
# Cuenta
# ---------------------------------------------------------------------------
def registrar(config, usuario, clave, email=""):
    return _guardar_sesion(
        _pedir(
            config,
            "POST",
            "/api/v1/registro",
            {
                "usuario": usuario.strip(),
                "contrasena": clave,
                "email": (email or "").strip(),
                "dispositivo": dispositivo(),
            },
            con_token=False,
        )
    )


def entrar(config, usuario, clave):
    return _guardar_sesion(
        _pedir(
            config,
            "POST",
            "/api/v1/login",
            {
                "usuario": usuario.strip(),
                "contrasena": clave,
                "dispositivo": dispositivo(),
            },
            con_token=False,
        )
    )


def verificar(config, email, codigo):
    return _guardar_sesion(
        _pedir(
            config,
            "POST",
            "/api/v1/verificar",
            {"email": email.strip(), "codigo": codigo.strip(), "dispositivo": dispositivo()},
            con_token=False,
        )
    )


def salir(config):
    """Cierra la sesión en el puente y aquí (aunque el puente no conteste)."""
    try:
        if sesion.hay_sesion():
            _pedir(config, "POST", "/api/v1/salir", {})
    except ErrorPerfil:
        pass
    sesion.borrar()


def estado(config):
    """Cuenta, plan, cupos y perfil de hoy."""
    datos = _pedir(config, "GET", "/api/v1/yo")
    perfil = datos.get("perfil") or {}
    if perfil:
        sesion.guardar(
            {"id_perfil": perfil.get("id") or 0, "revision": perfil.get("revision") or 0}
        )
    return datos


def puede_sincronizar():
    desde_plan = str(sesion.cargar().get("plan") or "").strip().lower()
    return sesion.hay_sesion() and desde_plan in CON_SINCRONIZACION


# ---------------------------------------------------------------------------
# Perfil: ajustes y diccionario
# ---------------------------------------------------------------------------
def bajar_perfil(config):
    return _pedir(config, "GET", "/api/v1/perfil").get("perfil") or {}


def subir_ajustes(config, ajustes):
    """Sube las casillas al perfil. Devuelve (perfil, chocó)."""
    datos = _pedir(
        config,
        "PUT",
        "/api/v1/perfil/ajustes",
        {"revision": sesion.revision(), "ajustes": ajustes},
    )
    perfil = datos.get("perfil") or {}
    if perfil:
        sesion.guardar({"revision": perfil.get("revision") or 0})
    return perfil, datos.get("status") == "conflicto"


def bajar_diccionario(config, desde=None):
    ruta = "/api/v1/perfil/diccionario"
    if desde:
        ruta += "?desde=" + desde
    return _pedir(config, "GET", ruta).get("palabras") or []


def fusionar_diccionario(config, filas):
    return _pedir(config, "POST", "/api/v1/perfil/diccionario", {"palabras": filas})


def bajar_sustituciones(config, desde=None):
    ruta = "/api/v1/perfil/sustituciones"
    if desde:
        ruta += "?desde=" + desde
    return _pedir(config, "GET", ruta).get("sustituciones") or []


def fusionar_sustituciones(config, filas):
    return _pedir(config, "POST", "/api/v1/perfil/sustituciones", {"sustituciones": filas})


def sincronizar(config, diccionario, espera=20):
    """Baja, fusiona y sube lo aprendido. Devuelve cuántas filas cambiaron.

    Si el plan no incluye sincronización, no hace nada (el diccionario local
    sigue funcionando). Se llama desde un hilo de fondo y nunca lanza: devuelve
    el motivo en texto para que el registro lo pueda enseñar.
    """
    if not puede_sincronizar():
        return (0, "Sin cuenta o sin plan de sincronización.")
    try:
        # Se relee antes de fusionar: la ventana de configuración u otro proceso
        # pueden haber enseñado algo mientras este estaba abierto.
        diccionario.cargar()
        diccionario.aplicar(bajar_diccionario(config), bajar_sustituciones(config))
        cambios = 0
        respuesta = fusionar_diccionario(config, diccionario.exportar_palabras())
        diccionario.aplicar(respuesta.get("palabras"), None)
        cambios += int(respuesta.get("cambiadas") or 0)
        respuesta = fusionar_sustituciones(config, diccionario.exportar_sustituciones())
        diccionario.aplicar(None, respuesta.get("sustituciones"))
        cambios += int(respuesta.get("cambiadas") or 0)
        diccionario.guardar()
        return (cambios, "")
    except ErrorPerfil as e:
        return (0, str(e))
