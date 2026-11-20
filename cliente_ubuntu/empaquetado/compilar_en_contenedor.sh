#!/usr/bin/env bash
# Compila el cliente dentro del contenedor Ubuntu 22.04 y arma el paquete.
#
# Lo invoca `construir_binario.sh`; a mano solo tiene sentido si ya estás
# dentro de la imagen (monta el proyecto en /src y la salida en /salida).
set -euo pipefail

VERSION="${VERSION:-1.0.0}"
FUENTE=/src/cliente_ubuntu
TRABAJO=/tmp/compilacion
ENTORNO=/tmp/entorno
SALIDA_NUITKA=/tmp/nuitka

echo "== Limpiando el árbol de trabajo =="
rm -rf "$TRABAJO" "$ENTORNO" "$SALIDA_NUITKA"
mkdir -p "$TRABAJO"
# Copia del código: Nuitka escribe .build/ junto a las fuentes y no queremos
# que eso acabe en el repositorio (que se monta en solo lectura).
cp -a "$FUENTE"/. "$TRABAJO"/
rm -rf "$TRABAJO/logs" "$TRABAJO/empaquetado" "$TRABAJO"/__pycache__
cd "$TRABAJO"

echo "== Clave del proxy para el binario =="
# Se incrusta como módulo, igual que en el APK: el usuario final no la ve ni la
# escribe. Se genera SIEMPRE (aunque esté vacía) para que el import exista.
printf 'CLAVE = "%s"\n' "${DICTADO_PUENTE_CLAVE:-}" > "$TRABAJO/clave_proxy_compilada.py"
# La versión del paquete, para que el propio cliente pueda decirla
# (`--version`, registro de arranque y ventana de configuración).
printf '%s\n' "$VERSION" > "$TRABAJO/version.txt"
if [ -n "${DICTADO_PUENTE_CLAVE:-}" ]; then
    echo "   incrustada (${#DICTADO_PUENTE_CLAVE} caracteres)"
else
    echo "   AVISO: vacía; el paquete solo servirá contra el puente local"
fi

echo "== Creando el entorno de compilación =="
# --system-site-packages es obligatorio: `gi` y `dbus` solo existen en el
# Python del sistema (python3-gi / python3-dbus) y hay que compilarlos dentro
# del binario para que la bandeja y el botón del auricular funcionen.
python3 -m venv --system-site-packages "$ENTORNO"
"$ENTORNO/bin/pip" install --upgrade pip >/dev/null
"$ENTORNO/bin/pip" install -r requirements.txt
"$ENTORNO/bin/pip" install "nuitka>=2.4" ordered-set zstandard

echo "== Paquetes que se incluirán en el binario =="
# `--include-package-data` SOLO vale para paquetes de verdad (con un módulo
# suelto Nuitka aborta con «is a module not a package») y además **choca** con
# los DLL: onnxruntime y ctranslate2 traen sus .so como bibliotecas y Nuitka
# falla con «data file ... conflicts with dll». Por eso solo se piden datos
# para los que de verdad tienen ficheros que no son código:
#   faster_whisper → assets/silero_vad.onnx (lo usa vad_filter=True)
#   huggingface_hub → plantillas del CLI
# `_sounddevice_data` va aparte porque es lo que trae la libportaudio.so.
CON_DATOS=" faster_whisper huggingface_hub "
PAQUETES=""
for paquete in numpy zmq sounddevice _sounddevice_data pynput pyautogui \
               pyperclip edge_tts requests faster_whisper ctranslate2 \
               tokenizers av onnxruntime huggingface_hub gi dbus PIL; do
    TIPO="$("$ENTORNO/bin/python" -c "
import importlib.util
espec = importlib.util.find_spec('$paquete')
if espec is None:
    print('FALTA')
elif getattr(espec, 'submodule_search_locations', None):
    print('PAQUETE')
else:
    print('MODULO')
" 2>/dev/null || echo FALTA)"
    case "$TIPO" in
        PAQUETE)
            PAQUETES="$PAQUETES --include-package=$paquete"
            if echo "$CON_DATOS" | grep -q " $paquete "; then
                PAQUETES="$PAQUETES --include-package-data=$paquete"
                echo "   + $paquete (paquete con datos)"
            else
                echo "   + $paquete (paquete)"
            fi
            ;;
        MODULO)
            PAQUETES="$PAQUETES --include-module=$paquete"
            echo "   + $paquete (módulo)"
            ;;
        *)
            echo "   - $paquete (no instalado, se omite)"
            ;;
    esac
done

echo "== Compilando con Nuitka (esto tarda 30-60 min la primera vez) =="
# --disable-plugin=gi: el plugin de PyGObject de Nuitka pide los typelibs de
#   medio sistema (Vte, Poppler, …) y aborta la compilación. Con `gi` incluido
#   a mano funciona igual: el importador dinámico de typelibs sigue dentro.
# --python-flag=no_docstrings: quita los docstrings del bytecode (menos peso).
# --noinclude-dlls de la pila de fuentes (2026-11-19): Nuitka metía la
#   libfontconfig/libfreetype/libharfbuzz de Ubuntu 22.04, y el libpangoft2 del
#   sistema (más nuevo) necesita símbolos que esa versión no tiene:
#       undefined symbol: FcConfigSetDefaultSubstitute
#   El icono de la bandeja (PyGObject + GTK del sistema) no arrancaba con el
#   paquete. Esa pila la pone el escritorio; no debe viajar dentro.
# shellcheck disable=SC2086  # la lista de paquetes se expande a propósito
"$ENTORNO/bin/python" -m nuitka \
    --standalone \
    --assume-yes-for-downloads \
    --disable-plugin=gi \
    --enable-plugin=tk-inter \
    --python-flag=no_docstrings \
    --lto=yes \
    --nofollow-import-to=unittest,pydoc,doctest,setuptools,pip,test,tests \
    --noinclude-dlls=libfontconfig* \
    --noinclude-dlls=libfreetype* \
    --noinclude-dlls=libharfbuzz* \
    --include-data-file=comandos.json=comandos.json \
    --include-data-file=INSTALAR_EN_OTRA_PC.md=INSTALAR_EN_OTRA_PC.md \
    --include-data-file=version.txt=version.txt \
    --report=/salida/informe-nuitka.xml \
    --output-dir="$SALIDA_NUITKA" \
    --output-filename=skipkey \
    $PAQUETES \
    cliente_voz.py

DIST="$(ls -d "$SALIDA_NUITKA"/*.dist | head -1)"
# Nuitka 4.x llamaba al ejecutable `skipkey`; ahora lo deja como `skipkey.bin`.
# Se renombra porque el nombre es el que usan la unidad systemd y las banderas
# de los procesos hijos (sys.executable).
if [ -f "$DIST/skipkey.bin" ] && [ ! -e "$DIST/skipkey" ]; then
    mv "$DIST/skipkey.bin" "$DIST/skipkey"
fi
if [ ! -x "$DIST/skipkey" ]; then
    echo "ERROR: no se generó el binario en $DIST" >&2
    ls -la "$DIST" | head -20 >&2
    exit 1
fi

echo "== Quitando símbolos =="
# El binario es el que más engorda: Nuitka deja los símbolos dentro y sin
# quitarlos son ~260 MB. `--strip-unneeded` conserva lo que necesita el
# enlazador dinámico.
strip --strip-unneeded "$DIST/skipkey" 2>/dev/null || true
find "$DIST" -name "*.so*" -type f -exec strip --strip-unneeded {} + 2>/dev/null || true

# ⚠️ Las librerías de `numpy.libs` (OpenBLAS, libgfortran…) llevan los
# segmentos alineados a 64 KB y patchelf (0.14 y también 0.18) las rompe al
# reescribirles el RPATH: quedan «ELF load command address/offset not
# page-aligned» y `import numpy` muere al arrancar. Se devuelven los archivos
# originales del entorno: como todas acaban en la misma carpeta, su `$ORIGIN`
# las sigue encontrando entre ellas.
echo "== Restaurando librerías que patchelf estropea =="
RESTAURADAS=0
for destino in "$DIST"/*.so*; do
    [ -f "$destino" ] || continue
    nombre="$(basename "$destino")"
    origen="$(find "$ENTORNO" -name "$nombre" -type f 2>/dev/null | head -1)"
    if [ -z "$origen" ]; then
        soname="$(readelf -d "$destino" 2>/dev/null | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p' | head -1)"
        if [ -n "$soname" ]; then
            origen="$(find "$ENTORNO" -name "$soname" -type f 2>/dev/null | head -1)"
        fi
    fi
    if [ -n "$origen" ]; then
        cp -f "$origen" "$destino"
        RESTAURADAS=$((RESTAURADAS + 1))
    fi
done
echo "   restauradas: $RESTAURADAS"

du -sh "$DIST"

echo "--- peso por entrada del dist ---"
du -sh "$DIST"/* 2>/dev/null | sort -hr | head -12
echo "--- librerías repetidas con el mismo nombre ---"
find "$DIST" -name "*.so*" -printf "%f\n" | sort | uniq -d | head -10

echo "== Armando el .deb =="
RAIZ="/tmp/deb/skipkey"
rm -rf /tmp/deb
mkdir -p "$RAIZ/DEBIAN" \
         "$RAIZ/opt/skipkey" \
         "$RAIZ/usr/bin" \
         "$RAIZ/usr/lib/systemd/user" \
         "$RAIZ/usr/share/applications" \
         "$RAIZ/usr/share/icons/hicolor/256x256/apps" \
         "$RAIZ/etc/xdg/autostart"

cp -a "$DIST"/. "$RAIZ/opt/skipkey/"
cp -a "$FUENTE/comandos.json" "$RAIZ/opt/skipkey/comandos.json"
ln -s /opt/skipkey/skipkey "$RAIZ/usr/bin/skipkey"
cp -a "$FUENTE/empaquetado/skipkey.service" "$RAIZ/usr/lib/systemd/user/"
cp -a "$FUENTE/empaquetado/skipkey.desktop" "$RAIZ/usr/share/applications/"
cp -a "$FUENTE/empaquetado/skipkey-autostart.desktop" "$RAIZ/etc/xdg/autostart/"
cp -a "$FUENTE/empaquetado/icono.png" \
      "$RAIZ/usr/share/icons/hicolor/256x256/apps/skipkey.png"

TAM_KB="$(du -sk "$RAIZ" | cut -f1)"
cat > "$RAIZ/DEBIAN/control" <<EOF
Package: skipkey
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: $TAM_KB
Depends: ffmpeg, libgtk-3-0, libayatana-appindicator3-1, libdbus-1-3, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, libportaudio2, libgomp1, libstdc++6, pulseaudio-utils, xclip, scrot, xdotool, xdg-utils
Maintainer: Ivan <ivan@localhost>
Description: Dictado por voz para el escritorio
 Transcribe la voz a texto, lo inyecta en la ventana activa y ejecuta
 ordenes del sistema hablando. Necesita el servidor de dictado (ZMQ).
 Incluye icono en la barra superior y escucha local de la palabra clave.
EOF

cat > "$RAIZ/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
cat <<'MSJ'

SkipKey instalado.
  Arrancarlo ahora:  systemctl --user start skipkey
  Al iniciar sesión: se arranca solo (autostart de escritorio).
  Registro:          ~/.local/state/skipkey/logs/
  Configuración:     ~/.config/skipkey/comandos.json

MSJ
exit 0
EOF
chmod 755 "$RAIZ/DEBIAN/postinst"

dpkg-deb -Zxz --build "$RAIZ" "/salida/skipkey_${VERSION}_amd64.deb" >/dev/null

echo "== Armando el AppImage =="
APPDIR=/tmp/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/skipkey" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$DIST"/. "$APPDIR/usr/lib/skipkey/"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# El binario usa rutas relativas ($ORIGIN) para sus librerías, así que se
# ejecuta EN SU SITIO: no se copia a /usr/bin.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/skipkey/skipkey" "$@"
EOF
chmod 755 "$APPDIR/AppRun"
cp -a "$FUENTE/empaquetado/skipkey.desktop" "$APPDIR/"
cp -a "$FUENTE/empaquetado/icono.png" "$APPDIR/skipkey.png"
cp -a "$FUENTE/empaquetado/icono.png" \
      "$APPDIR/usr/share/icons/hicolor/256x256/apps/skipkey.png"

# `appimagetool` es a su vez un AppImage: sin FUSE dentro del contenedor hay
# que extraerlo antes de ejecutarlo.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 appimagetool --no-appstream \
    "$APPDIR" "/salida/skipkey-${VERSION}-x86_64.AppImage" >/dev/null

if [ -n "${UID_HOST:-}" ]; then
    chown -R "$UID_HOST:${GID_HOST:-$UID_HOST}" /salida
fi

echo
echo "Listo:"
ls -lh /salida/*.deb /salida/*.AppImage

