#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Instala las herramientas para compilar el APK, SIN sudo, todo en ~/Android.
#
#   - JDK 17 (Temurin): Java 25 no sirve para el plugin de Android (AGP).
#   - Android command-line tools + platform-tools + android-34 + build-tools.
#   - Gradle 8.7 (solo para generar el wrapper del proyecto).
#
# Uso:  bash cliente_android/herramientas/instalar_sdk.sh
# ---------------------------------------------------------------------------
set -euo pipefail

BASE="$HOME/Android"
mkdir -p "$BASE"

# --- JDK 17 (Temurin) ------------------------------------------------------
if [ ! -x "$BASE/jdk-17/bin/javac" ]; then
    echo "== Descargando JDK 17 (Temurin) =="
    curl -fL --retry 3 -o "$BASE/jdk17.tar.gz" \
        "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
    mkdir -p "$BASE/jdk-17"
    tar -xzf "$BASE/jdk17.tar.gz" -C "$BASE/jdk-17" --strip-components=1
    rm -f "$BASE/jdk17.tar.gz"
fi

# --- Android command-line tools -------------------------------------------
SDK="$BASE/Sdk"
SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMANAGER" ]; then
    echo "== Descargando Android command-line tools =="
    URL=""
    for u in \
        "https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip" \
        "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"; do
        if curl -fsI "$u" >/dev/null 2>&1; then URL="$u"; break; fi
    done
    if [ -z "$URL" ]; then
        echo "ERROR: no encontré los command-line tools" >&2
        exit 1
    fi
    curl -fL -o "$BASE/cmdline-tools.zip" "$URL"
    rm -rf "$SDK/cmdline-tools/tmp"
    mkdir -p "$SDK/cmdline-tools/tmp"
    python3 -m zipfile -e "$BASE/cmdline-tools.zip" "$SDK/cmdline-tools/tmp"
    mv "$SDK/cmdline-tools/tmp/cmdline-tools" "$SDK/cmdline-tools/latest"
    rm -rf "$SDK/cmdline-tools/tmp" "$BASE/cmdline-tools.zip"
fi

# `python3 -m zipfile` no conserva el bit de ejecución (a diferencia de unzip),
# así que hay que dárselo a mano. Es idempotente: sirve también para arreglar
# una instalación a medias.
chmod +x "$SDK"/cmdline-tools/latest/bin/* 2>/dev/null || true

export JAVA_HOME="$BASE/jdk-17"
export PATH="$JAVA_HOME/bin:$PATH"

# --- Paquetes del SDK ------------------------------------------------------
echo "== Licencias del SDK =="
yes | "$SDKMANAGER" --sdk_root="$SDK" --licenses > "$BASE/licencias.log" 2>&1 || true
echo "== Instalando platform-tools, android-34 y build-tools =="
"$SDKMANAGER" --sdk_root="$SDK" \
    "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# --- Gradle ----------------------------------------------------------------
if [ ! -x "$BASE/gradle-8.7/bin/gradle" ]; then
    echo "== Descargando Gradle 8.7 =="
    curl -fL --retry 3 -o "$BASE/gradle.zip" \
        "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
    python3 -m zipfile -e "$BASE/gradle.zip" "$BASE"
    rm -f "$BASE/gradle.zip"
fi

# Mismo detalle que con los command-line tools: hay que dar el bit de ejecución.
chmod +x "$BASE"/gradle-8.7/bin/* 2>/dev/null || true

echo "LISTO-DE-INSTALAR"
"$BASE/jdk-17/bin/java" -version
"$BASE/gradle-8.7/bin/gradle" -v | sed -n '1,8p'
