"""Sesión de la cuenta del puente, guardada en el disco del usuario.

Qué se guarda y qué no:

- Se guarda el **token del dispositivo** (lo que devuelve `/api/v1/login`), el
  nombre de usuario, el plan y el id del perfil. Nada más.
- **La contraseña no se guarda nunca**: viaja una vez y se olvida.
- El archivo vive en `~/.config/skipkey/sesion.json` y se escribe con permisos
  `600` (solo el usuario). No va en `comandos.json` porque ese sí se comparte y
  se edita a mano.
- En Windows se usa el mismo `~/.config/skipkey`, para que las dos versiones se
  comporten igual y no haya dos sitios donde mirar.
"""

import json
import os
import tempfile

CAMPOS = ("usuario", "email", "token", "plan", "plan_nombre", "id_perfil", "perfil", "revision")


def ruta():
    """Ruta del archivo de sesión."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "skipkey", "sesion.json")


def cargar():
    """La sesión guardada, o un diccionario vacío si no hay o está rota."""
    try:
        with open(ruta(), encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {clave: datos.get(clave) for clave in CAMPOS if clave in datos}


def guardar(datos):
    """Escribe la sesión (mezclando con lo que ya hubiera)."""
    actuales = cargar()
    actuales.update({clave: datos[clave] for clave in CAMPOS if clave in datos})
    destino = ruta()
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=os.path.dirname(destino))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            json.dump(actuales, archivo, ensure_ascii=False, indent=2)
        if os.name != "nt":
            os.chmod(temporal, 0o600)
        os.replace(temporal, destino)
    except Exception:
        # Si no se pudo escribir, no se deja el temporal a medias.
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise
    return actuales


def borrar():
    """Cierra la sesión en este aparato (borra el archivo)."""
    try:
        os.unlink(ruta())
    except OSError:
        pass


def token():
    return (cargar().get("token") or "").strip()


def usuario():
    return (cargar().get("usuario") or "").strip()


def id_perfil():
    try:
        return int(cargar().get("id_perfil") or 0)
    except (TypeError, ValueError):
        return 0


def revision():
    try:
        return int(cargar().get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def perfil():
    return (cargar().get("perfil") or "personal").strip() or "personal"


def hay_sesion():
    """¿Hay token de una cuenta? El maestro no cuenta: no tiene perfil."""
    return bool(token())
