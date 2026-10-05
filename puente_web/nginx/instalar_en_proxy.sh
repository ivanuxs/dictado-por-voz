#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Instala el puente de dictado en el nginx del proxy (192.168.1.100).
#
# Se ejecuta EN LA MÁQUINA DEL PROXY, con sudo:
#
#     sudo bash instalar_en_proxy.sh --dominio dictado.TU-DOMINIO.com
#
# Qué hace, en este orden:
#   1. Copia /etc/nginx/conf.d/dictado-http.conf  (la clave secreta y los map).
#   2. Copia /etc/nginx/sites-available/dictado-sitio.conf con TU dominio y lo
#      deja sirviendo por el puerto 80 (no se pone nada "en espera").
#   3. Si aún no hay certificado, lo pide con el plugin de nginx, que es el que
#      ya usas para tus otros dominios:
#          certbot --nginx -d TU-DOMINIO --redirect
#      certbot se encarga de añadir el bloque TLS y la redirección del 80.
#   4. nginx -t y recarga. Si el test falla, NO se recarga nada: el nginx en
#      marcha sigue sirviendo tus otros sitios tal como están.
#
# Ojo: el script comprueba ANTES que la configuración actual de nginx sea
# válida. Si ya estaba rota (típico: un ssl_certificate que apunta a un
# certificado borrado), se detiene sin tocar nada.
#
# Opciones:
#   --dominio DOMINIO   Obligatorio. Ejemplo: dictado.mi-dominio.com
#   --clave CLAVE       Clave de la segunda barrera. Si no se indica, se genera.
#   --email CORREO      Correo para Let's Encrypt. Solo hace falta si este
#                       servidor todavía no tiene una cuenta de certbot.
#   --simular RUTA      No toca el sistema: escribe el resultado en RUTA y sale.
#                       (Sirve para revisar los archivos antes de instalarlos.)
# ---------------------------------------------------------------------------
set -euo pipefail

DOMINIO=""
CLAVE=""
EMAIL=""
SIMULAR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dominio) DOMINIO="${2:-}"; shift 2 ;;
        --clave)   CLAVE="${2:-}";   shift 2 ;;
        --email)   EMAIL="${2:-}";   shift 2 ;;
        --simular) SIMULAR="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "Opción no reconocida: $1" >&2; exit 2 ;;
    esac
done

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_D="/etc/nginx/conf.d/dictado-http.conf"
SITIO="/etc/nginx/sites-available/dictado-sitio.conf"
ENLACE="/etc/nginx/sites-enabled/dictado-sitio.conf"

if [ -n "$SIMULAR" ]; then
    CONF_D="$SIMULAR/conf.d/dictado-http.conf"
    SITIO="$SIMULAR/sites-available/dictado-sitio.conf"
    ENLACE="$SIMULAR/sites-enabled/dictado-sitio.conf"
    mkdir -p "$SIMULAR/conf.d" "$SIMULAR/sites-available" "$SIMULAR/sites-enabled"
elif [ "$(id -u)" -ne 0 ]; then
    echo "Ejecútalo con sudo (o usa --simular RUTA para solo ver los archivos)." >&2
    exit 1
fi

if [ -z "$DOMINIO" ]; then
    echo "Falta --dominio. Ejemplo: --dominio dictado.mi-dominio.com" >&2
    exit 2
fi

# El nombre de ejemplo del repositorio NO es de nadie: dominio.com pertenece a
# otra empresa y su DNS comodín también resuelve los subdominios. Mejor avisar
# que instalar un dominio ajeno.
case "$DOMINIO" in
    dictado.midominio.com|dictado.dominio.com)
        echo "ERROR: '$DOMINIO' es un nombre de ejemplo, no es tuyo." >&2
        echo "       Usa tu dominio real, p. ej. --dominio dictado.mi-casa.com" >&2
        exit 2 ;;
esac

echo "== Instalando el puente de dictado para $DOMINIO =="

# --- 0. Comprobar la configuración ACTUAL de nginx ------------------------
# Si ya está rota (p. ej. un ssl_certificate que apunta a un certificado
# borrado), cualquier cambio fallará y el error no sería de este instalador.
# Mejor decirlo claro y no tocar nada.
if [ -z "$SIMULAR" ]; then
    if ! command -v nginx >/dev/null 2>&1; then
        echo "ERROR: aquí no hay nginx instalado. Este script se ejecuta en el proxy." >&2
        exit 1
    fi
    if ! nginx -t; then
        echo >&2
        echo "ERROR: la configuración ACTUAL de nginx ya falla, antes de tocar nada." >&2
        echo "       Arréglalo primero: no lo causa este instalador." >&2
        echo "       Si el error de arriba menciona un ssl_certificate que no" >&2
        echo "       existe, ese sitio quedó apuntando a un certificado borrado:" >&2
        echo "       vuelve a emitirlo o quita ese sitio, y repite." >&2
        exit 1
    fi
fi

# --- 1. Clave de la segunda barrera ---------------------------------------
if [ -z "$CLAVE" ]; then
    CLAVE="$(openssl rand -hex 32)"
    echo
    echo "   Clave generada (ponla también en los ajustes de la app):"
    echo "   $CLAVE"
    echo
fi

# --- 2. Nivel http{} (clave + map de Connection + limit_req_zone) ---------
sed "s/\"CAMBIAR_CLAVE_DEL_PROXY\"/\"$CLAVE\"/" \
    "$AQUI/dictado-http.conf" > "$CONF_D"
# El archivo lleva la clave secreta: que no lo pueda leer cualquiera del equipo.
# nginx lo lee como root al arrancar y al recargar, así que no le afecta.
chmod 600 "$CONF_D"
echo "   escrito: $CONF_D"

# --- 3. Sitio (puerto 80) -------------------------------------------------
sed "s/dictado\.midominio\.com/$DOMINIO/g" \
    "$AQUI/dictado-sitio.conf" > "$SITIO"
ln -sfn "$SITIO" "$ENLACE"
echo "   escrito: $SITIO"

# --- 4. Certificado con el plugin de nginx (el que ya usas) ---------------
# No se deja el 80 "en espera": el sitio ya sirve el puente desde el primer
# momento, y certbot añade el TLS al mismo bloque y la redirección del 80.
CERT="/etc/letsencrypt/live/$DOMINIO/fullchain.pem"
if [ -f "$CERT" ]; then
    echo "   certificado ya presente: $CERT"
elif [ -n "$SIMULAR" ]; then
    echo "   (simulado: aquí correría 'certbot --nginx -d $DOMINIO --redirect')"
else
    echo "   recargando nginx para servir el 80 mientras se pide el certificado..."
    # OJO: con "nginx -t && systemctl reload" el fallo del test se ignora (set -e
    # no dispara en el primer comando de un '&&'), así que se comprueba aparte.
    if ! nginx -t; then
        echo "ERROR: nginx no pasa el test; no se recarga y no se pide certificado." >&2
        exit 1
    fi
    systemctl reload nginx

    if ! certbot plugins --text 2>/dev/null | grep -qi nginx; then
        echo "ERROR: certbot no tiene instalado el plugin de nginx." >&2
        echo "       Instálalo con: sudo apt install python3-certbot-nginx" >&2
        exit 1
    fi

    echo "   pidiendo el certificado a Let's Encrypt (certbot --nginx)..."
    ARG_CERTBOT=(--nginx -d "$DOMINIO" --non-interactive --agree-tos
                 --redirect --keep-until-expiring)
    if [ -n "$EMAIL" ]; then
        ARG_CERTBOT+=(--email "$EMAIL")
    fi
    if ! certbot "${ARG_CERTBOT[@]}"; then
        echo "ERROR: certbot falló." >&2
        echo "       Comprueba que $DOMINIO apunte a este servidor y que el 80" >&2
        echo "       llegue desde internet. Si este servidor aún no tiene cuenta" >&2
        echo "       de certbot, pasa tu correo con --email." >&2
        exit 1
    fi
    echo "   listo: certbot ya añadió el bloque TLS y la redirección."
fi

# El reto de renovación se sirve desde aquí (lo usa el plugin igual).
mkdir -p /var/www/html 2>/dev/null || true

if [ -n "$SIMULAR" ]; then
    echo
    echo "== Simulación terminada: revisa los archivos en $SIMULAR =="
    exit 0
fi

# --- 5. Comprobar y recargar (con vuelta atrás si algo está mal) ----------
if nginx -t; then
    systemctl reload nginx
    echo
    echo "== Listo. Comprobación desde fuera =="
    echo "   curl -s https://$DOMINIO/api/v1/salud -H 'X-Clave-Cliente: $CLAVE'"
    echo "   (debe responder {\"status\":\"ok\"})"
    echo "   El TLS y la redirección del 80 los gestiona ahora certbot:"
    echo "   para renovar, 'sudo certbot renew --dry-run'."
else
    echo "ERROR: la configuración no pasa 'nginx -t'; no se recargó nada." >&2
    echo "       El nginx que está corriendo sigue igual (sin cortar tus otros sitios)." >&2
    exit 1
fi
