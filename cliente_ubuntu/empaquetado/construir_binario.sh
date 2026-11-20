#!/usr/bin/env bash
# Construye el paquete instalable del cliente de dictado para Linux.
#
#   bash cliente_ubuntu/empaquetado/construir_binario.sh
#   VERSION=1.1.0 bash cliente_ubuntu/empaquetado/construir_binario.sh
#   bash cliente_ubuntu/empaquetado/construir_binario.sh --reconstruir-imagen
#
# Por qué dentro de un contenedor: el binario queda atado a la glibc con la
# que se compila. Compilando en Ubuntu 22.04 corre también en 24.04 y 25.x;
# compilado en 24.04 no arrancaría en 22.04.
#
# Resultado:
#   cliente_ubuntu/empaquetado/salida/skipkey_<versión>_amd64.deb
#   cliente_ubuntu/empaquetado/salida/skipkey-<versión>-x86_64.AppImage
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROYECTO="$(dirname "$AQUI")"      # cliente_ubuntu/
RAIZ="$(dirname "$PROYECTO")"      # raíz del repositorio
SALIDA="$AQUI/salida"
CACHE="$AQUI/.construccion"
IMAGEN="skipkey-compilador:22.04"
VERSION="${VERSION:-1.0.0}"

reconstruir_imagen=0
for arg in "$@"; do
    case "$arg" in
        --reconstruir-imagen) reconstruir_imagen=1 ;;
        -h|--help)
            sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Opción desconocida: $arg" >&2
            exit 2
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: hace falta Docker para compilar (el binario se arma en Ubuntu 22.04)." >&2
    exit 1
fi

if [ "$reconstruir_imagen" -eq 1 ] || ! docker image inspect "$IMAGEN" >/dev/null 2>&1; then
    echo "== Construyendo la imagen de compilación ($IMAGEN) =="
    docker build -t "$IMAGEN" -f "$AQUI/Dockerfile.compilacion" "$AQUI"
fi

# ─── Clave del proxy ────────────────────────────────────────────────────────
# Se incrusta en el binario, igual que se hace en el APK: el usuario final no
# la ve ni la escribe (2026-11-19). Se toma de DICTADO_PUENTE_CLAVE o, si no
# está, de cliente_android/local.properties (que no se versiona).
CLAVE_PROXY="${DICTADO_PUENTE_CLAVE:-}"
if [ -z "$CLAVE_PROXY" ] && [ -f "$PROYECTO/clave_proxy.txt" ]; then
    CLAVE_PROXY="$(head -n1 "$PROYECTO/clave_proxy.txt" | tr -d '\r\n')"
fi
if [ -z "$CLAVE_PROXY" ] && [ -f "$RAIZ/cliente_android/local.properties" ]; then
    CLAVE_PROXY="$(sed -n 's/^dictadoClave=//p' "$RAIZ/cliente_android/local.properties" | head -n1)"
fi
if [ -n "$CLAVE_PROXY" ]; then
    echo "== La clave del proxy se incrustará en el binario (${#CLAVE_PROXY} caracteres) =="
else
    echo "AVISO: no hay clave del proxy (DICTADO_PUENTE_CLAVE ni local.properties)."
    echo "       El paquete solo servirá contra el puente local (http://127.0.0.1:8000)."
fi

mkdir -p "$SALIDA" "$CACHE/ccache" "$CACHE/nuitka" "$CACHE/pip"

echo "== Compilando la versión $VERSION en Ubuntu 22.04 =="
echo "   (la primera vez tarda bastante: crea el venv y compila todo)"
docker run --rm \
    -e VERSION="$VERSION" \
    -e UID_HOST="$(id -u)" \
    -e GID_HOST="$(id -g)" \
    -e DICTADO_PUENTE_CLAVE="$CLAVE_PROXY" \
    -e CCACHE_DIR=/ccache \
    -e PIP_NO_CACHE_DIR=0 \
    -v "$RAIZ":/src:ro \
    -v "$SALIDA":/salida \
    -v "$CACHE/ccache":/ccache \
    -v "$CACHE/nuitka":/root/.cache/Nuitka \
    -v "$CACHE/pip":/root/.cache/pip \
    "$IMAGEN" \
    bash /src/cliente_ubuntu/empaquetado/compilar_en_contenedor.sh

echo
echo "Paquete listo en: $SALIDA"
