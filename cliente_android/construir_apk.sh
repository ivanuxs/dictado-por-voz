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
echo "== Compilando $TAREA (JDK 17 en $JAVA_HOME) =="
exec "$BASE/gradle-8.7/bin/gradle" --no-daemon "$TAREA"
