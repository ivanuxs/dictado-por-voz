#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Compila el APK del teclado de dictado.
#
# Usa las herramientas instaladas por herramientas/instalar_sdk.sh en ~/Android
# (o en $ANDROID_HERRAMIENTAS si se define). No necesita Android Studio.
#
#   bash cliente_android/construir_apk.sh              # APK de depuración
#   bash cliente_android/construir_apk.sh assembleRelease
#
# Para un APK destinado a OTRA persona (sin el token maestro dentro):
#   bash cliente_android/construir_apk.sh assembleDebug -PsinToken
# deja además una copia en .../outputs/apk/debug/dictado-sin-token.apk, así no
# se pisa el APK privado (el que sí lleva el token) al compilar los dos.
#
# El APK queda en cliente_android/app/build/outputs/apk/debug/app-debug.apk
# ---------------------------------------------------------------------------
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${ANDROID_HERRAMIENTAS:-$HOME/Android}"

if [ ! -x "$BASE/jdk-17/bin/java" ]; then
    echo "Falta el JDK 17. Ejecuta: bash cliente_android/herramientas/instalar_sdk.sh" >&2
    exit 1
fi
if [ ! -d "$BASE/Sdk/platforms/android-34" ]; then
    echo "Falta el SDK de Android. Ejecuta: bash cliente_android/herramientas/instalar_sdk.sh" >&2
    exit 1
fi

export JAVA_HOME="$BASE/jdk-17"
export PATH="$JAVA_HOME/bin:$PATH"

# local.properties no se versiona: dice dónde está el SDK en ESTE equipo y
# además lleva el token y la clave que se meten dentro del APK.
# Se AÑADE la línea del SDK solo si falta: reescribir el archivo borraría
# esas credenciales y la app volvería a pedirlas a mano.
if ! grep -q "^sdk.dir=" "$AQUI/local.properties" 2>/dev/null; then
    echo "sdk.dir=$BASE/Sdk" >> "$AQUI/local.properties"
fi

cd "$AQUI"
TAREA="${1:-assembleDebug}"
shift || true

# Argumentos extra para Gradle, p. ej. -PsinToken (APK de otro usuario).
SIN_TOKEN=0
for argumento in "$@"; do
    case "$argumento" in
        -PsinToken|--project-prop=sinToken*|-PsinToken=*) SIN_TOKEN=1 ;;
    esac
done

ORIGEN="$AQUI/app/build/outputs/apk/debug/app-debug.apk"

# La compilación SIEMPRE escribe app-debug.apk. Si ahora se compila sin token,
# el APK privado (el que sí lo lleva) se perdería: se guarda ANTES de compilar,
# que es cuando ese archivo todavía es el de la compilación anterior.
if [ "$SIN_TOKEN" = 1 ] && [ -f "$ORIGEN" ]; then
    cp -f "$ORIGEN" "$AQUI/app/build/outputs/apk/debug/dictado-anterior.apk"
    echo "El APK que había antes quedó como dictado-anterior.apk"
fi

echo "== Compilando $TAREA (JDK 17 en $JAVA_HOME) =="
"$BASE/gradle-8.7/bin/gradle" --no-daemon "$TAREA" "$@"

# El APK sin token maestro se guarda con otro nombre.
if [ "$SIN_TOKEN" = 1 ]; then
    DESTINO="$AQUI/app/build/outputs/apk/debug/dictado-sin-token.apk"
    if [ -f "$ORIGEN" ]; then
        cp -f "$ORIGEN" "$DESTINO"
        echo "APK para otro usuario (sin token maestro): $DESTINO"
    else
        echo "Ojo: no está $ORIGEN; el APK quedó donde lo dejó la tarea $TAREA." >&2
    fi
fi
