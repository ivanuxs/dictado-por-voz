"""Cuentas, tokens de dispositivo y uso diario del puente, en SQLite.

Todo lo de aquí es local del puente: no toca el servidor de dictado ni el
modelo. La base NO se versiona (lleva correos y hashes de contraseñas): vive en
`puente_web/datos/usuarios.db` y está en .gitignore.

Decisiones que conviene no deshacer:

- La identidad es el **nombre de usuario**, no el correo. El correo es
  opcional y solo sirve para confirmar y recuperar la cuenta: sin SMTP no hay
  forma de verificar a nadie, así que exigirlo dejaría a medio mundo fuera.
- Las contraseñas se guardan con `hashlib.scrypt` y su sal. Nunca en claro.
- Los tokens de dispositivo también se guardan CON HASH (sha256): si alguien
  copia la base, no puede suplantar a nadie con lo que hay dentro.
- El uso se cuenta por día en UTC. Es un contador para los cupos del plan, no
  una factura.
- El perfil es la unidad de lo «del usuario»: ajustes, diccionario y
  sustituciones cuelgan de él, y él cuelga de UNA cuenta. Dos cuentas nunca
  comparten perfil, aunque usen el mismo celular.
"""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config


class ErrorUsuario(Exception):
    """Error de negocio con un mensaje que se puede enseñar tal cual."""


ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario     TEXT    NOT NULL UNIQUE,
    email       TEXT    UNIQUE,
    sal         TEXT    NOT NULL,
    hash        TEXT    NOT NULL,
    plan        TEXT    NOT NULL DEFAULT 'gratis',
    verificado  INTEGER NOT NULL DEFAULT 0,
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    huella      TEXT    NOT NULL UNIQUE,
    dispositivo TEXT    NOT NULL DEFAULT '',
    creado      TEXT    NOT NULL,
    ultimo_uso  TEXT,
    expira      TEXT    NOT NULL,
    revocado    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tokens_usuario ON tokens(usuario_id);

CREATE TABLE IF NOT EXISTS verificaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    huella      TEXT    NOT NULL,
    expira      TEXT    NOT NULL,
    usado       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_verificaciones_usuario ON verificaciones(usuario_id);

CREATE TABLE IF NOT EXISTS uso (
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha           TEXT    NOT NULL,
    segundos        REAL    NOT NULL DEFAULT 0,
    transcripciones INTEGER NOT NULL DEFAULT 0,
    traducciones    INTEGER NOT NULL DEFAULT 0,
    caracteres      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (usuario_id, fecha)
);

-- Un perfil por cuenta (por ahora «personal»), con el esquema listo para
-- varios: el nombre es único dentro de la cuenta y todo lo aprendido cuelga
-- del id del perfil, no del dispositivo.
CREATE TABLE IF NOT EXISTS perfiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre      TEXT    NOT NULL DEFAULT 'personal',
    ajustes     TEXT    NOT NULL DEFAULT '{}',
    revision    INTEGER NOT NULL DEFAULT 1,
    creado      TEXT    NOT NULL,
    actualizado TEXT    NOT NULL,
    UNIQUE (usuario_id, nombre)
);
CREATE INDEX IF NOT EXISTS ix_perfiles_usuario ON perfiles(usuario_id);

-- Diccionario aprendido. `olvidada` no borra la fila a propósito: si se
-- borrara, el otro dispositivo la resubiría en la siguiente sincronización.
CREATE TABLE IF NOT EXISTS diccionario (
    perfil_id   INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
    palabra     TEXT    NOT NULL,
    veces       INTEGER NOT NULL DEFAULT 1,
    conocida    INTEGER NOT NULL DEFAULT 0,
    olvidada    INTEGER NOT NULL DEFAULT 0,
    actualizado TEXT    NOT NULL,
    PRIMARY KEY (perfil_id, palabra)
);

-- Correcciones del dictado («antes» se oía mal y debía ser «despues»).
CREATE TABLE IF NOT EXISTS sustituciones (
    perfil_id   INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
    antes       TEXT    NOT NULL,
    despues     TEXT    NOT NULL,
    veces       INTEGER NOT NULL DEFAULT 1,
    olvidada    INTEGER NOT NULL DEFAULT 0,
    actualizado TEXT    NOT NULL,
    PRIMARY KEY (perfil_id, antes)
);
"""

# Lote máximo por petición: protege la base y el ancho de banda del celular.
LIMITE_LOTE = 2000
# Tope de tamaño para el JSON de ajustes (es una lista de casillas, no un documento).
LIMITE_AJUSTES = 20000
NOMBRE_PERFIL = "personal"

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
# Nombre de usuario: letras, números, punto, guion y guion bajo. Debe empezar
# por letra o número para que no se confunda con un correo ni con una opción.
_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{2,23}$")
# Nombres que no se pueden tomar: se confunden con roles del servidor.
_USUARIOS_RESERVADOS = {
    "admin",
    "administrador",
    "root",
    "sistema",
    "soporte",
    "maestro",
    "dictado",
    "skipkey",
    "puente",
    "null",
    "none",
}

# Coste de scrypt que se usa aquí (n=2**14 son ~16 MB de memoria: rápido en el
# puente y caro de atacar en masa). No subirlo a 2**15 sin subir maxmem.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------
def conectar():
    """Abre la base (creando la carpeta si hace falta)."""
    ruta = Path(config.BD)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(ruta))
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA journal_mode=WAL")
    conexion.execute("PRAGMA foreign_keys=ON")
    return conexion


def iniciar():
    """Crea las tablas si no existen y pone al día las bases antiguas.

    Devuelve True si hubo que migrar alguna cuenta del esquema viejo.
    """
    with closing(conectar()) as conexion:
        # Sin claves ajenas durante la migración: se reemplaza la tabla
        # `usuarios` y las demás apuntan a ella por id.
        conexion.execute("PRAGMA foreign_keys=OFF")
        migrada = _migrar(conexion)
        conexion.commit()
        conexion.executescript(ESQUEMA)
        # Planes que ya no existen pasan al más bajo. La lista sale de
        # `planes.ORDEN` a propósito: cuando se borró «gratis» (y luego volvió)
        # esta línea tenía los nombres escritos a mano y hubo que acordarse de
        # cambiarla. El import va dentro de la función para no atar los dos
        # módulos al cargarse.
        import planes

        marcadores = ", ".join("?" for _ in planes.ORDEN)
        conexion.execute(
            f"UPDATE usuarios SET plan = ? WHERE plan NOT IN ({marcadores})",
            (planes.ORDEN[0], *planes.ORDEN),
        )
        conexion.execute("PRAGMA foreign_keys=ON")
        conexion.commit()
    return migrada


def _columnas(conexion, tabla):
    """Nombres de las columnas de una tabla (vacío si no existe)."""
    return {fila[1] for fila in conexion.execute(f"PRAGMA table_info({tabla})")}


def _usuario_desde_email(email, usados):
    """Nombre de usuario razonable para una cuenta vieja, sin repetir."""
    base = normalizar_email(email).split("@")[0]
    limpio = re.sub(r"[^a-z0-9._-]+", "", base).strip("._-")[:24] or "usuario"
    if len(limpio) < 3:
        limpio = (limpio + "usuario")[:24]
    candidato = limpio
    numero = 2
    while candidato in usados:
        sufijo = str(numero)
        candidato = limpio[: 24 - len(sufijo)] + sufijo
        numero += 1
    usados.add(candidato)
    return candidato


def _migrar(conexion):
    """Pasa `usuarios` del esquema viejo (correo obligatorio y único) al nuevo
    (nombre de usuario único y correo opcional) sin perder ninguna cuenta.

    SQLite no sabe quitar un NOT NULL ni añadir UNIQUE con ALTER TABLE, así que
    se construye la tabla nueva al lado, se copia y se cambia el nombre. Se hace
    con las claves ajenas apagadas y **sin renombrar la tabla vieja**: al
    renombrarla, SQLite reescribiría las claves ajenas de `tokens`, `uso` y
    `verificaciones` para que apuntaran a la vieja, que es la trampa clásica de
    esta migración. Como los `id` se conservan, todo sigue apuntando bien.
    """
    columnas = _columnas(conexion, "usuarios")
    if not columnas or "usuario" in columnas:
        return False

    conexion.execute(
        """
        CREATE TABLE usuarios_nuevo (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario     TEXT    NOT NULL UNIQUE,
            email       TEXT    UNIQUE,
            sal         TEXT    NOT NULL,
            hash        TEXT    NOT NULL,
            plan        TEXT    NOT NULL DEFAULT 'gratis',
            verificado  INTEGER NOT NULL DEFAULT 0,
            activo      INTEGER NOT NULL DEFAULT 1,
            creado      TEXT    NOT NULL
        )
        """
    )
    usados = set()
    filas = conexion.execute(
        "SELECT id, email, sal, hash, plan, verificado, activo, creado "
        "FROM usuarios ORDER BY id"
    ).fetchall()
    for fila in filas:
        conexion.execute(
            "INSERT INTO usuarios_nuevo (id, usuario, email, sal, hash, plan, "
            "verificado, activo, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fila["id"],
                _usuario_desde_email(fila["email"], usados),
                fila["email"] or None,
                fila["sal"],
                fila["hash"],
                fila["plan"],
                fila["verificado"],
                fila["activo"],
                fila["creado"],
            ),
        )
    conexion.execute("DROP TABLE usuarios")
    conexion.execute("ALTER TABLE usuarios_nuevo RENAME TO usuarios")
    return True


def ahora():
    """Instante actual en UTC, sin microsegundos (comparable como texto)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def _texto(instante):
    return instante.isoformat()


def _huella(texto):
    """sha256 en hexadecimal: lo que se guarda de tokens y códigos."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _derivar(clave, sal):
    """Hash de la contraseña con su sal."""
    return hashlib.scrypt(
        clave.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    ).hex()


def normalizar_email(email):
    return (email or "").strip().lower()


def validar_email(email):
    if not email or len(email) > 254 or not _CORREO.match(email):
        raise ErrorUsuario("Ese correo no parece válido.")


def normalizar_usuario(usuario):
    """El nombre de usuario se guarda en minúsculas y sin espacios."""
    return (usuario or "").strip().lower().lstrip("@")


def validar_usuario(usuario):
    if not usuario:
        raise ErrorUsuario("Elige un nombre de usuario.")
    if not _USUARIO.match(usuario):
        raise ErrorUsuario(
            "El nombre de usuario debe tener entre 3 y 24 caracteres: letras sin "
            "tilde, números, punto, guion o guion bajo, y empezar por letra o número."
        )
    if usuario in _USUARIOS_RESERVADOS:
        raise ErrorUsuario("Ese nombre de usuario está reservado. Elige otro.")


def validar_clave(clave):
    if not clave or len(clave) < 8:
        raise ErrorUsuario("La contraseña debe tener al menos 8 caracteres.")
    if len(clave) > 200:
        raise ErrorUsuario("La contraseña es demasiado larga.")


def _publico(fila):
    """Convierte una fila en el diccionario que sí puede salir del puente."""
    if fila is None:
        return None
    datos = dict(fila)
    return {
        "id": datos["id"],
        "usuario": datos.get("usuario") or "",
        "email": datos.get("email") or "",
        "plan": datos["plan"],
        "verificado": bool(datos["verificado"]),
        "activo": bool(datos["activo"]),
        "creado": datos["creado"],
        "es_maestro": False,
    }


# Nombre público de la conversión (la usan las rutas y los scripts).
publico = _publico


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------
def buscar_por_email(email):
    """Fila completa (con hash) o None. Uso interno: no sale del puente."""
    correo = normalizar_email(email)
    if not correo:
        return None
    with closing(conectar()) as conexion:
        fila = conexion.execute(
            "SELECT * FROM usuarios WHERE email = ?", (correo,)
        ).fetchone()
    return fila


def buscar_por_usuario(usuario):
    """Fila completa (con hash) por nombre de usuario, o None."""
    nombre = normalizar_usuario(usuario)
    if not nombre:
        return None
    with closing(conectar()) as conexion:
        fila = conexion.execute(
            "SELECT * FROM usuarios WHERE usuario = ?", (nombre,)
        ).fetchone()
    return fila


def buscar_para_entrar(identificador):
    """Entrar con nombre de usuario; si trae «@», se acepta el correo viejo."""
    texto = (identificador or "").strip()
    if "@" in texto:
        return buscar_por_email(texto)
    return buscar_por_usuario(texto)


def buscar_por_id(usuario_id):
    with closing(conectar()) as conexion:
        fila = conexion.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
    return fila


def crear_usuario(usuario, clave, email=None, plan=None, verificado=None):
    """Crea la cuenta y la devuelve. Lanza ErrorUsuario si el nombre está ocupado.

    El correo es opcional. Sin correo no hay nada que confirmar, así que la
    cuenta nace verificada; con correo, manda `DICTADO_VERIFICAR_CORREO`.
    """
    nombre = normalizar_usuario(usuario)
    validar_usuario(nombre)
    validar_clave(clave)
    correo = normalizar_email(email) or None
    if correo:
        validar_email(correo)
        if buscar_por_email(correo) is not None:
            raise ErrorUsuario("Ya hay una cuenta con ese correo. Entra con tu contraseña.")
    if buscar_por_usuario(nombre) is not None:
        raise ErrorUsuario("Ese nombre de usuario ya está ocupado. Prueba con otro.")
    if verificado is None:
        verificado = correo is None

    sal = secrets.token_bytes(16)
    plan = plan or config.PLAN_POR_DEFECTO
    with closing(conectar()) as conexion, conexion:
        cursor = conexion.execute(
            "INSERT INTO usuarios (usuario, email, sal, hash, plan, verificado, "
            "creado) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                nombre,
                correo,
                sal.hex(),
                _derivar(clave, sal),
                plan,
                1 if verificado else 0,
                _texto(ahora()),
            ),
        )
        nuevo = cursor.lastrowid
    perfil_de(nuevo)  # el perfil «personal» nace con la cuenta
    return _publico(buscar_por_id(nuevo))


def actualizar(usuario_id, plan=None, clave=None, email=None, verificado=None, usuario=None):
    """Cambia lo que se le pase (para los scripts de consola).

    Con `usuario` se renombra la cuenta: el `id` no cambia, así que el perfil,
    el diccionario y los tokens siguen siendo los mismos. Es lo que se usa
    cuando una cuenta vieja quedó con el nombre sacado del correo
    (`198158736ivanuxs`) y el dueño quiere entrar con el suyo (`ivanuxs`).
    """
    campos = []
    valores = []
    if usuario is not None:
        nombre = normalizar_usuario(usuario)
        validar_usuario(nombre)
        otro = buscar_por_usuario(nombre)
        if otro is not None and otro["id"] != usuario_id:
            raise ErrorUsuario("Ese nombre de usuario ya está ocupado. Prueba con otro.")
        campos.append("usuario = ?")
        valores.append(nombre)
    if plan is not None:
        campos.append("plan = ?")
        valores.append(plan)
    if clave is not None:
        validar_clave(clave)
        sal = secrets.token_bytes(16)
        campos.append("sal = ?")
        valores.append(sal.hex())
        campos.append("hash = ?")
        valores.append(_derivar(clave, sal))
    if email is not None:
        correo = normalizar_email(email)
        validar_email(correo)
        campos.append("email = ?")
        valores.append(correo)
    if verificado is not None:
        campos.append("verificado = ?")
        valores.append(1 if verificado else 0)
    if not campos:
        return _publico(buscar_por_id(usuario_id))
    valores.append(usuario_id)
    with closing(conectar()) as conexion, conexion:
        conexion.execute(
            f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", valores
        )
    return _publico(buscar_por_id(usuario_id))


def comprobar_clave(usuario_id, clave):
    """¿Es la contraseña de esa cuenta? (comparación en tiempo constante)."""
    fila = buscar_por_id(usuario_id)
    if fila is None or not fila["activo"]:
        return False
    esperado = fila["hash"]
    calculado = _derivar(clave or "", bytes.fromhex(fila["sal"]))
    return hmac.compare_digest(esperado, calculado)


def borrar(usuario_id):
    """Borra la cuenta y todo lo suyo (tokens, códigos y uso, por CASCADE)."""
    with closing(conectar()) as conexion, conexion:
        conexion.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))


def listar():
    with closing(conectar()) as conexion:
        filas = conexion.execute(
            "SELECT * FROM usuarios ORDER BY creado"
        ).fetchall()
    return [_publico(fila) for fila in filas]


def contar():
    with closing(conectar()) as conexion:
        return conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]


# ---------------------------------------------------------------------------
# Códigos de verificación por correo
# ---------------------------------------------------------------------------
def crear_codigo(usuario_id, minutos=None):
    """Genera un código de 6 dígitos, lo guarda con hash y lo devuelve."""
    minutos = minutos or config.CODIGO_MINUTOS
    codigo = f"{secrets.randbelow(10 ** 6):06d}"
    expira = _texto(ahora() + timedelta(minutes=minutos))
    with closing(conectar()) as conexion, conexion:
        # Los códigos anteriores dejan de servir: solo vale el último enviado.
        conexion.execute(
            "UPDATE verificaciones SET usado = 1 WHERE usuario_id = ? AND usado = 0",
            (usuario_id,),
        )
        conexion.execute(
            "INSERT INTO verificaciones (usuario_id, huella, expira) VALUES (?, ?, ?)",
            (usuario_id, _huella(codigo), expira),
        )
    return codigo


def comprobar_codigo(email, codigo):
    """Marca el correo como verificado si el código es correcto."""
    fila = buscar_por_email(email)
    if fila is None:
        raise ErrorUsuario("No hay ninguna cuenta con ese correo.")
    if fila["verificado"]:
        return _publico(fila)

    limpio = (codigo or "").strip().replace(" ", "")
    if not limpio.isdigit() or len(limpio) != 6:
        raise ErrorUsuario("El código son 6 números.")

    with closing(conectar()) as conexion, conexion:
        encontrado = conexion.execute(
            "SELECT * FROM verificaciones WHERE usuario_id = ? AND huella = ? "
            "AND usado = 0 AND expira > ? ORDER BY id DESC LIMIT 1",
            (fila["id"], _huella(limpio), _texto(ahora())),
        ).fetchone()
        if encontrado is None:
            raise ErrorUsuario("El código no es válido o ya caducó. Pide uno nuevo.")
        conexion.execute(
            "UPDATE verificaciones SET usado = 1 WHERE id = ?", (encontrado["id"],)
        )
        conexion.execute(
            "UPDATE usuarios SET verificado = 1 WHERE id = ?", (fila["id"],)
        )
    return _publico(buscar_por_id(fila["id"]))


# ---------------------------------------------------------------------------
# Tokens de dispositivo
# ---------------------------------------------------------------------------
def dispositivos(usuario_id):
    """Nombres de los dispositivos con token vivo."""
    with closing(conectar()) as conexion:
        filas = conexion.execute(
            "SELECT dispositivo FROM tokens WHERE usuario_id = ? AND revocado = 0 "
            "AND expira > ?",
            (usuario_id, _texto(ahora())),
        ).fetchall()
    return [fila["dispositivo"] for fila in filas]


def _avisar(texto):
    """Escribe en el registro del puente, sin atar el módulo a utils_logging."""
    try:
        from utils_logging import imprimir

        imprimir(texto)
    except Exception:
        pass


def crear_token(usuario_id, dispositivo="", limite=None):
    """Devuelve un token nuevo en claro (en la base solo queda su hash).

    Reglas (2026-11-19, después de que una cuenta Pro se quedara sin cupos):

    - **Volver a entrar desde el mismo aparato no gasta cupo**: se revoca el
      token vivo con esa etiqueta y se emite uno nuevo. Antes cada inicio de
      sesión insertaba otra fila y con tres logins el plan quedaba «lleno».
    - El límite cuenta **aparatos distintos**, no inicios de sesión.
    - Si los cupos están ocupados y entra un aparato nuevo, se libera el que
      lleva **más tiempo sin usarse** en vez de dejar al usuario fuera: desde
      el cliente no hay ninguna pantalla para cerrar sesiones ajenas.
    """
    etiqueta = (dispositivo or "").strip()[:60] or "dispositivo"
    with closing(conectar()) as conexion, conexion:
        vivos = conexion.execute(
            "SELECT id, dispositivo, COALESCE(ultimo_uso, creado) AS visto "
            "FROM tokens WHERE usuario_id = ? AND revocado = 0 AND expira > ? "
            "ORDER BY visto",
            (usuario_id, _texto(ahora())),
        ).fetchall()

        # 1. Mismo aparato: su token anterior se tira (sesión nueva, mismo cupo).
        repetidos = [f["id"] for f in vivos if f["dispositivo"] == etiqueta]
        if repetidos:
            conexion.executemany(
                "UPDATE tokens SET revocado = 1 WHERE id = ?",
                [(identificador,) for identificador in repetidos],
            )
        otros = [f for f in vivos if f["id"] not in repetidos]
        nombres = {f["dispositivo"] for f in otros}

        # 2. Aparato nuevo con los cupos llenos: se libera el más antiguo.
        if limite is not None and etiqueta not in nombres and len(nombres) >= limite:
            if not otros:
                raise ErrorUsuario(
                    "No hay sitio para un aparato más en tu plan. "
                    "Mejora el plan para conectar otro."
                )
            viejo = otros[0]  # la consulta ya viene ordenada por último uso
            conexion.execute(
                "UPDATE tokens SET revocado = 1 WHERE id = ?", (viejo["id"],)
            )
            _avisar(
                f"[CUENTAS] Cupos llenos ({limite}): se liberó el aparato "
                f"«{viejo['dispositivo']}» para dejar entrar a «{etiqueta}»."
            )

        token = secrets.token_urlsafe(32)
        conexion.execute(
            "INSERT INTO tokens (usuario_id, huella, dispositivo, creado, expira) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                usuario_id,
                _huella(token),
                etiqueta,
                _texto(ahora()),
                _texto(ahora() + timedelta(days=config.TOKEN_DIAS)),
            ),
        )
    return token


def usuario_por_token(token):
    """Resuelve el token a su cuenta, o None si no vale."""
    if not token:
        return None
    with closing(conectar()) as conexion, conexion:
        fila = conexion.execute(
            "SELECT u.* FROM tokens t JOIN usuarios u ON u.id = t.usuario_id "
            "WHERE t.huella = ? AND t.revocado = 0 AND u.activo = 1 AND t.expira > ?",
            (_huella(token), _texto(ahora())),
        ).fetchone()
        if fila is None:
            return None
        conexion.execute(
            "UPDATE tokens SET ultimo_uso = ? WHERE huella = ?",
            (_texto(ahora()), _huella(token)),
        )
    return _publico(fila)


def revocar_token(token):
    """Cierra la sesión de ESE dispositivo. Devuelve True si había algo."""
    if not token:
        return False
    with closing(conectar()) as conexion, conexion:
        cursor = conexion.execute(
            "UPDATE tokens SET revocado = 1 WHERE huella = ?", (_huella(token),)
        )
    return cursor.rowcount > 0


def revocar_todos(usuario_id):
    with closing(conectar()) as conexion, conexion:
        conexion.execute(
            "UPDATE tokens SET revocado = 1 WHERE usuario_id = ?", (usuario_id,)
        )


def cortar_tokens(usuario_id, cuantos):
    """Deja solo los `cuantos` tokens vivos más recientes (al bajar de plan)."""
    with closing(conectar()) as conexion, conexion:
        conexion.execute(
            "UPDATE tokens SET revocado = 1 WHERE usuario_id = ? AND revocado = 0 "
            "AND id NOT IN (SELECT id FROM tokens WHERE usuario_id = ? AND revocado = 0 "
            "ORDER BY id DESC LIMIT ?)",
            (usuario_id, usuario_id, max(0, int(cuantos))),
        )


# ---------------------------------------------------------------------------
# Uso del día (cupos de los planes)
# ---------------------------------------------------------------------------
def _fecha_hoy():
    return ahora().date().isoformat()


def uso_hoy(usuario_id):
    """Contadores de hoy. Si no hay nada apuntado, todo a cero."""
    with closing(conectar()) as conexion:
        fila = conexion.execute(
            "SELECT * FROM uso WHERE usuario_id = ? AND fecha = ?",
            (usuario_id, _fecha_hoy()),
        ).fetchone()
    if fila is None:
        return {
            "fecha": _fecha_hoy(),
            "segundos": 0.0,
            "transcripciones": 0,
            "traducciones": 0,
            "caracteres": 0,
        }
    return {
        "fecha": fila["fecha"],
        "segundos": float(fila["segundos"]),
        "transcripciones": int(fila["transcripciones"]),
        "traducciones": int(fila["traducciones"]),
        "caracteres": int(fila["caracteres"]),
    }


def sumar_uso(usuario_id, segundos=0.0, transcripciones=0, traducciones=0, caracteres=0):
    """Apunta lo consumido hoy (upsert)."""
    if usuario_id is None:
        return
    with closing(conectar()) as conexion, conexion:
        conexion.execute(
            "INSERT INTO uso (usuario_id, fecha, segundos, transcripciones, "
            "traducciones, caracteres) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(usuario_id, fecha) DO UPDATE SET "
            "segundos = segundos + excluded.segundos, "
            "transcripciones = transcripciones + excluded.transcripciones, "
            "traducciones = traducciones + excluded.traducciones, "
            "caracteres = caracteres + excluded.caracteres",
            (
                usuario_id,
                _fecha_hoy(),
                float(segundos),
                int(transcripciones),
                int(traducciones),
                int(caracteres),
            ),
        )

# ---------------------------------------------------------------------------
# Perfil: lo que es «del usuario» (ajustes, diccionario y correcciones)
#
# El perfil cuelga de la CUENTA, no del aparato. Así el teclado y la PC de la
# misma persona comparten lo aprendido, y dos cuentas en el mismo celular no se
# ven nada aunque usen el mismo `Ajustes.perfil` local.
# ---------------------------------------------------------------------------
def _perfil_publico(fila):
    if fila is None:
        return None
    try:
        ajustes = json.loads(fila["ajustes"] or "{}")
    except ValueError:
        ajustes = {}
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "revision": int(fila["revision"]),
        "ajustes": ajustes if isinstance(ajustes, dict) else {},
        "actualizado": fila["actualizado"],
    }


def perfil_de(usuario_id, nombre=NOMBRE_PERFIL, crear=True):
    """Perfil de la cuenta; si no existe y `crear`, lo crea."""
    if not usuario_id:
        raise ErrorUsuario("El token maestro no tiene perfil: entra con tu cuenta.")
    instante = _texto(ahora())
    with closing(conectar()) as conexion, conexion:
        fila = conexion.execute(
            "SELECT * FROM perfiles WHERE usuario_id = ? AND nombre = ?",
            (usuario_id, nombre),
        ).fetchone()
        if fila is None and crear:
            conexion.execute(
                "INSERT INTO perfiles (usuario_id, nombre, ajustes, revision, "
                "creado, actualizado) VALUES (?, ?, '{}', 1, ?, ?)",
                (usuario_id, nombre, instante, instante),
            )
            fila = conexion.execute(
                "SELECT * FROM perfiles WHERE usuario_id = ? AND nombre = ?",
                (usuario_id, nombre),
            ).fetchone()
    return _perfil_publico(fila)


def guardar_ajustes(usuario_id, ajustes, revision=None, nombre=NOMBRE_PERFIL):
    """Guarda los ajustes del perfil. Devuelve `(perfil, chocó)`.

    `revision` es la que el cliente tenía; si la del servidor es otra, no se
    escribe nada y `chocó` es True: el cliente decide entre pisar o recargar.
    Sin `revision` se guarda siempre (última escritura gana).
    """
    if not isinstance(ajustes, dict):
        raise ErrorUsuario("Los ajustes tienen que ser un objeto JSON.")
    texto = json.dumps(ajustes, ensure_ascii=False, sort_keys=True)
    if len(texto) > LIMITE_AJUSTES:
        raise ErrorUsuario("Los ajustes ocupan demasiado.")
    perfil = perfil_de(usuario_id, nombre)
    if revision is not None and int(revision) != perfil["revision"]:
        return perfil, True
    with closing(conectar()) as conexion, conexion:
        conexion.execute(
            "UPDATE perfiles SET ajustes = ?, revision = revision + 1, "
            "actualizado = ? WHERE id = ?",
            (texto, _texto(ahora()), perfil["id"]),
        )
    return perfil_de(usuario_id, nombre), False


def _entero(valor, por_defecto=1, minimo=1):
    try:
        return max(minimo, int(valor))
    except (TypeError, ValueError):
        return por_defecto


def _limpiar_palabra(valor):
    """Palabra o frase corta, sin espacios de más (vacío si no sirve)."""
    texto = " ".join(str(valor or "").split())
    if not texto or len(texto) > 60:
        return ""
    return texto


def _instante_de(valor):
    """Fecha ISO del cliente, normalizada a UTC. Si no se entiende, ahora."""
    try:
        instante = datetime.fromisoformat(str(valor or "").replace("Z", "+00:00"))
    except ValueError:
        return _texto(ahora())
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return _texto(instante.astimezone(timezone.utc))


def _filas_diccionario(conexion, perfil_id, desde=None, limite=LIMITE_LOTE):
    consulta = (
        "SELECT palabra, veces, conocida, olvidada, actualizado FROM diccionario "
        "WHERE perfil_id = ?"
    )
    parametros = [perfil_id]
    if desde:
        consulta += " AND actualizado > ?"
        parametros.append(desde)
    consulta += " ORDER BY actualizado LIMIT ?"
    parametros.append(limite)
    return [
        {
            "palabra": fila["palabra"],
            "veces": int(fila["veces"]),
            "conocida": bool(fila["conocida"]),
            "olvidada": bool(fila["olvidada"]),
            "actualizado": fila["actualizado"],
        }
        for fila in conexion.execute(consulta, parametros).fetchall()
    ]


def diccionario_desde(usuario_id, desde=None, nombre=NOMBRE_PERFIL, limite=LIMITE_LOTE):
    """Lo aprendido en el perfil; con `desde`, solo lo cambiado después."""
    perfil = perfil_de(usuario_id, nombre)
    with closing(conectar()) as conexion:
        return _filas_diccionario(conexion, perfil["id"], desde, limite)


def fusionar_diccionario(usuario_id, palabras, nombre=NOMBRE_PERFIL):
    """Une lo aprendido en el aparato con lo del perfil.

    Reglas (importan para no inventar ni perder aprendizaje):

    - `veces` es el MÁXIMO, no la suma: sumar infla la cuenta cada vez que un
      equipo resube lo mismo.
    - `conocida` solo avanza: una palabra conocida no vuelve a ser desconocida.
    - `olvidada` lo decide la fecha más reciente, y una palabra olvidada deja de
      ser conocida.
    - La fila NO se borra al olvidar: si se borrara, el otro dispositivo la
      resubiría en la siguiente vuelta.
    """
    if not isinstance(palabras, list):
        raise ErrorUsuario("Se esperaba una lista de palabras.")
    if len(palabras) > LIMITE_LOTE:
        raise ErrorUsuario(f"No se pueden mandar más de {LIMITE_LOTE} a la vez.")
    perfil = perfil_de(usuario_id, nombre)
    cambiadas = 0
    with closing(conectar()) as conexion, conexion:
        for entrada in palabras:
            if not isinstance(entrada, dict):
                continue
            palabra = _limpiar_palabra(entrada.get("palabra"))
            if not palabra:
                continue
            veces = _entero(entrada.get("veces"))
            conocida = 1 if entrada.get("conocida") else 0
            olvidada = 1 if entrada.get("olvidada") else 0
            instante = _instante_de(entrada.get("actualizado"))
            previa = conexion.execute(
                "SELECT * FROM diccionario WHERE perfil_id = ? AND palabra = ?",
                (perfil["id"], palabra),
            ).fetchone()
            if previa is None:
                conexion.execute(
                    "INSERT INTO diccionario (perfil_id, palabra, veces, conocida, "
                    "olvidada, actualizado) VALUES (?, ?, ?, ?, ?, ?)",
                    (perfil["id"], palabra, veces, conocida, olvidada, instante),
                )
                cambiadas += 1
                continue

            nuevo_veces = max(int(previa["veces"]), veces)
            nueva_conocida = max(int(previa["conocida"]), conocida)
            if instante >= previa["actualizado"]:
                nueva_olvidada = olvidada
            else:
                nueva_olvidada = int(previa["olvidada"])
            if nueva_olvidada:
                nueva_conocida = 0
            if (
                nuevo_veces == int(previa["veces"])
                and nueva_conocida == int(previa["conocida"])
                and nueva_olvidada == int(previa["olvidada"])
            ):
                continue
            # `actualizado` es SIEMPRE la fecha más nueva de las dos: si se
            # escribiera la que llega, una marca vieja dejaría la fila con
            # fecha del pasado y el siguiente equipo la volvería a pisar.
            conexion.execute(
                "UPDATE diccionario SET veces = ?, conocida = ?, olvidada = ?, "
                "actualizado = ? WHERE perfil_id = ? AND palabra = ?",
                (
                    nuevo_veces,
                    nueva_conocida,
                    nueva_olvidada,
                    max(instante, previa["actualizado"]),
                    perfil["id"],
                    palabra,
                ),
            )
            cambiadas += 1
    return cambiadas, diccionario_desde(usuario_id, nombre=nombre)


def _filas_sustituciones(conexion, perfil_id, desde=None, limite=LIMITE_LOTE):
    consulta = (
        "SELECT antes, despues, veces, olvidada, actualizado FROM sustituciones "
        "WHERE perfil_id = ?"
    )
    parametros = [perfil_id]
    if desde:
        consulta += " AND actualizado > ?"
        parametros.append(desde)
    consulta += " ORDER BY actualizado LIMIT ?"
    parametros.append(limite)
    return [
        {
            "antes": fila["antes"],
            "despues": fila["despues"],
            "veces": int(fila["veces"]),
            "olvidada": bool(fila["olvidada"]),
            "actualizado": fila["actualizado"],
        }
        for fila in conexion.execute(consulta, parametros).fetchall()
    ]


def sustituciones_desde(usuario_id, desde=None, nombre=NOMBRE_PERFIL, limite=LIMITE_LOTE):
    """Correcciones aprendidas del perfil («antes» → «despues»)."""
    perfil = perfil_de(usuario_id, nombre)
    with closing(conectar()) as conexion:
        return _filas_sustituciones(conexion, perfil["id"], desde, limite)


def fusionar_sustituciones(usuario_id, sustituciones, nombre=NOMBRE_PERFIL):
    """Une las correcciones del aparato con las del perfil.

    La clave es «antes»: si el dispositivo y el servidor tienen la misma
    palabra mal oída con distinto reemplazo, gana el de fecha más reciente; y
    `veces` es el máximo por la misma razón que en el diccionario.
    """
    if not isinstance(sustituciones, list):
        raise ErrorUsuario("Se esperaba una lista de sustituciones.")
    if len(sustituciones) > LIMITE_LOTE:
        raise ErrorUsuario(f"No se pueden mandar más de {LIMITE_LOTE} a la vez.")
    perfil = perfil_de(usuario_id, nombre)
    cambiadas = 0
    with closing(conectar()) as conexion, conexion:
        for entrada in sustituciones:
            if not isinstance(entrada, dict):
                continue
            antes = _limpiar_palabra(entrada.get("antes"))
            despues = _limpiar_palabra(entrada.get("despues"))
            if not antes or not despues:
                continue
            veces = _entero(entrada.get("veces"))
            olvidada = 1 if entrada.get("olvidada") else 0
            instante = _instante_de(entrada.get("actualizado"))
            previa = conexion.execute(
                "SELECT * FROM sustituciones WHERE perfil_id = ? AND antes = ?",
                (perfil["id"], antes),
            ).fetchone()
            if previa is None:
                conexion.execute(
                    "INSERT INTO sustituciones (perfil_id, antes, despues, veces, "
                    "olvidada, actualizado) VALUES (?, ?, ?, ?, ?, ?)",
                    (perfil["id"], antes, despues, veces, olvidada, instante),
                )
                cambiadas += 1
                continue

            nuevo_veces = max(int(previa["veces"]), veces)
            if instante >= previa["actualizado"]:
                nuevo_despues = despues
                nuevo_olvidada = olvidada
            else:
                nuevo_despues = previa["despues"]
                nuevo_olvidada = int(previa["olvidada"])
            if (
                nuevo_veces == int(previa["veces"])
                and nuevo_despues == previa["despues"]
                and nuevo_olvidada == int(previa["olvidada"])
            ):
                continue
            conexion.execute(
                "UPDATE sustituciones SET despues = ?, veces = ?, olvidada = ?, "
                "actualizado = ? WHERE perfil_id = ? AND antes = ?",
                (
                    nuevo_despues,
                    nuevo_veces,
                    nuevo_olvidada,
                    max(instante, previa["actualizado"]),
                    perfil["id"],
                    antes,
                ),
            )
            cambiadas += 1
    return cambiadas, sustituciones_desde(usuario_id, nombre=nombre)