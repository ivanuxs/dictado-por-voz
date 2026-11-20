"""Planes de uso del puente: qué puede hacer cada cuenta y cuánto.

Este módulo es la ÚNICA autoridad sobre los planes. El teclado solo muestra lo
que el puente le dice; si alguien modifica el APK para saltarse un candado, el
puente le responde 403 igual, porque la comprobación se hace aquí, en cada
petición.

Cambiar un número de aquí cambia el límite de todos los clientes a la vez.
"""

from fastapi import HTTPException

# Planes de menos a más. El orden se usa para decir «necesitas el plan X».
#
# Tres planes (2026-11-18): Gratis dicta con el motor del propio celular (no usa
# nuestro servidor ni gasta GPU), Básico tiene cupo diario y Pro no tiene tope.
ORDEN = ("gratis", "basico", "pro")

# La matriz. «ilimitado» se pone con None en los cupos.
PLANES = {
    "gratis": {
        "nombre": "Gratis",
        "precio": "0",
        "servidor": False,          # dicta con el motor del propio celular
        "traduccion": False,
        "vivo": False,              # texto mientras hablas (WebSocket)
        "sincronizacion": False,    # diccionario y ajustes entre dispositivos
        "minutos_dia": 0,
        "traducciones_dia": 0,
        "dispositivos": 1,
    },
    "basico": {
        "nombre": "Básico",
        "precio": "—",
        "servidor": True,
        "traduccion": True,
        "vivo": False,
        "sincronizacion": True,
        "minutos_dia": 30,
        "traducciones_dia": 50,
        "dispositivos": 1,
    },
    "pro": {
        "nombre": "Pro",
        "precio": "—",
        "servidor": True,
        "traduccion": True,
        "vivo": True,
        "sincronizacion": True,
        # Pro no tiene tope diario: se dicta y se traduce lo que se quiera.
        # Lo que sigue limitando el abuso no es el cupo, sino la cola del
        # puente (una transcripción y una traducción a la vez), los 60 s por
        # petición y los 5 dispositivos.
        "minutos_dia": None,
        "traducciones_dia": None,
        "dispositivos": 5,
    },
}


def normalizar(nombre):
    """Devuelve un plan que exista; si no, el gratuito."""
    texto = (nombre or "").strip().lower()
    return texto if texto in PLANES else "gratis"


def datos(plan):
    """Copia de la matriz de un plan (para no exponer el original)."""
    return dict(PLANES[normalizar(plan)])


def catalogo():
    """Lo que se publica en GET /api/v1/planes: el teclado pinta esto."""
    return [
        {"plan": clave, **valor}
        for clave, valor in ((c, PLANES[c]) for c in ORDEN)
    ]


def es_maestro(usuario):
    """El token maestro (DICTADO_WEB_TOKEN) no tiene plan ni cupos.

    Es el que usan los clientes de escritorio y las pruebas del propio usuario;
    se mantiene para no romper nada de lo que ya funcionaba.
    """
    return bool(usuario) and bool(usuario.get("es_maestro"))


def minimo_para(capacidad):
    """Nombre del plan más barato que incluye una capacidad."""
    for nombre in ORDEN:
        if PLANES[nombre].get(capacidad):
            return nombre
    return None


def exigir(usuario, capacidad):
    """Lanza 403 si el plan del usuario no incluye esa capacidad."""
    if es_maestro(usuario):
        return
    plan = normalizar(usuario.get("plan") if usuario else None)
    if PLANES[plan].get(capacidad):
        return
    if capacidad == "servidor":
        motivo = (
            "El dictado con nuestro servidor es una función de pago. "
            "Con el plan Gratis se dicta con el motor del propio celular."
        )
    elif capacidad == "traduccion":
        motivo = "La traducción es una función de pago; no está incluida en el plan Gratis."
    elif capacidad == "vivo":
        motivo = "El dictado en vivo (texto mientras hablas) requiere el plan Pro."
    elif capacidad == "sincronizacion":
        motivo = (
            "Sincronizar el diccionario entre dispositivos es una función de pago; "
            "no está incluida en el plan Gratis."
        )
    else:
        motivo = "Tu plan no incluye esta función."
    necesita = minimo_para(capacidad)
    if necesita and necesita != plan:
        motivo += f" Necesitas el plan {PLANES[necesita]['nombre']} o superior."
    raise HTTPException(status_code=403, detail=motivo)


def cupos(usuario, uso_hoy=None):
    """Cupos del plan y lo que queda hoy.

    `uso_hoy` es el diccionario que devuelve `usuarios.uso_hoy()`: segundos,
    transcripciones, traducciones y caracteres. Si no se pasa, no se calculan
    los restantes.
    """
    if es_maestro(usuario):
        return {
            "plan": "maestro",
            "nombre": "Ilimitado",
            "minutos_dia": None,
            "traducciones_dia": None,
            "minutos_restantes": None,
            "traducciones_restantes": None,
            "dispositivos": None,
            "usado": uso_hoy or {},
        }

    plan = normalizar(usuario.get("plan") if usuario else None)
    matriz = PLANES[plan]
    resultado = {
        "plan": plan,
        "nombre": matriz["nombre"],
        "minutos_dia": matriz["minutos_dia"],
        "traducciones_dia": matriz["traducciones_dia"],
        "dispositivos": matriz["dispositivos"],
    }
    if uso_hoy is not None:
        usados = float(uso_hoy.get("segundos") or 0) / 60.0
        # None en la matriz = ilimitado: no se resta nada, se devuelve None y
        # el cliente lo enseña como «sin tope» (el candado del puente ya
        # distingue None de un número).
        if matriz["minutos_dia"] is None:
            resultado["minutos_restantes"] = None
        else:
            resultado["minutos_restantes"] = max(
                0.0, round(matriz["minutos_dia"] - usados, 1)
            )
        hechas = int(uso_hoy.get("traducciones") or 0)
        if matriz["traducciones_dia"] is None:
            resultado["traducciones_restantes"] = None
        else:
            resultado["traducciones_restantes"] = max(
                0, matriz["traducciones_dia"] - hechas
            )
        resultado["usado"] = uso_hoy
    return resultado


def minutos_restantes(usuario, uso_hoy):
    """Minutos de dictado por servidor que quedan hoy (None = ilimitado).

    ⚠️ Pro tiene `minutos_dia = None` (sin tope diario, 2026-11-18): restar a
    ciegas reventaba con «unsupported operand type(s) for -: 'NoneType' and
    'float'» y el puente devolvía 500 en cada dictado de una cuenta Pro
    (2026-11-19).
    """
    if es_maestro(usuario):
        return None
    plan = normalizar(usuario.get("plan") if usuario else None)
    tope = PLANES[plan]["minutos_dia"]
    if tope is None:
        return None
    usados = float(uso_hoy.get("segundos") or 0) / 60.0
    return max(0.0, tope - usados)


def traducciones_restantes(usuario, uso_hoy):
    """Traducciones que quedan hoy (None = ilimitado)."""
    if es_maestro(usuario):
        return None
    plan = normalizar(usuario.get("plan") if usuario else None)
    tope = PLANES[plan]["traducciones_dia"]
    if tope is None:
        return None
    hechas = int(uso_hoy.get("traducciones") or 0)
    return max(0, tope - hechas)


def limite_dispositivos(usuario):
    """Cuántos dispositivos puede tener conectados (None = ilimitado)."""
    if es_maestro(usuario):
        return None
    return PLANES[normalizar(usuario.get("plan") if usuario else None)]["dispositivos"]
