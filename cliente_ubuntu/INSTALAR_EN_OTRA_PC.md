# Llevar SkipKey a otra PC Linux (AppImage)

El cliente se entrega como **un solo archivo**: el AppImage. No hay que instalar
Python, ni el `.deb`, ni copiar configuración. Pero el AppImage **no** es
autosuficiente del todo: la sesión gráfica, los paquetes del escritorio y tu
cuenta son de cada equipo.

## 1. Requisitos del equipo destino

| Requisito | Cómo comprobarlo |
|---|---|
| Arquitectura **x86-64** (no ARM, no 32 bits) | `uname -m` → `x86_64` |
| **glibc 2.35 o mayor** (Ubuntu 22.04+, Debian 12+, Fedora 36+, Mint 21+) | `ldd --version \| head -1` |
| **Sesión gráfica** (X11 o Wayland) con audio | no vale por SSH ni en un servidor |

## 2. Paquetes del sistema

Ubuntu/Debian:

```bash
sudo apt install libgtk-3-0 libayatana-appindicator3-1 libdbus-1-3 ffmpeg xclip
# En Ubuntu 24.04 y posteriores, además, para montar el AppImage:
sudo apt install libfuse2t64
```

Fedora (nombres equivalentes):

```bash
sudo dnf install gtk3 libayatana-appindicator-gtk3 dbus-libs ffmpeg-free xclip fuse-libs
```

Si el equipo no tiene FUSE (contenedores, algunos servidores), el AppImage se
puede abrir igual con `APPIMAGE_EXTRACT_AND_RUN=1 ./skipkey-1.1.3-x86_64.AppImage`.

## 3. Copiar y ejecutar

```bash
chmod +x skipkey-1.1.3-x86_64.AppImage
./skipkey-1.1.3-x86_64.AppImage
```

La primera vez crea `~/.config/skipkey/comandos.json` a partir de la plantilla
(con el dominio del puente) y deja los registros en
`~/.local/state/skipkey/logs/registro_cliente_voz.log`.

**No hay que escribir la clave del proxy**: va incrustada en el binario.

## 4. Entrar con la cuenta (una vez por equipo)

Icono de la bandeja → **Configuración… → Cuenta** → usuario y contraseña
(o «Crear cuenta»).

La sesión se guarda en `~/.config/skipkey/sesion.json`, **por equipo**.

⚠️ **No copies ese archivo de una PC a otra**: es un token de sesión. Dos equipos
con el mismo token se pisan la sesión y el servidor puede liberar cupos sin que te
des cuenta. Entra una vez en cada máquina.

Con la cuenta abierta, el cliente manda el audio **por el puente HTTPS**, así que
funciona desde **cualquier red** (no hace falta estar en la red del servidor).
Si el puente no responde, cae a ZMQ (`IP:5555`), que solo sirve dentro de la red
local. Se puede forzar con `"transporte": "puente"` o `"zmq"` en
`~/.config/skipkey/comandos.json`.

## 5. Arranque al iniciar sesión (recomendado)

Copia el AppImage a una ruta estable y crea una unidad de usuario. Ejemplo:

```bash
mkdir -p ~/.local/bin
cp skipkey-1.2.0-x86_64.AppImage ~/.local/bin/skipkey
chmod +x ~/.local/bin/skipkey

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/skipkey.service <<'EOF'
[Unit]
Description=SkipKey — dictado por voz (AppImage)
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/skipkey
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now skipkey
```

La unidad hereda `DISPLAY`, `XAUTHORITY`, `WAYLAND_DISPLAY` y el bus de sesión
del gestor de usuario: no hay que fijarlos, y `XAUTHORITY` cambia en cada sesión.

## 6. Ctrl+Espacio (atajo de GNOME)

En **Wayland** el cliente **no** puede capturar teclas globales: el atajo lo
manda GNOME como señal al servicio. Sin esto, el dictado solo se abre desde el
icono.

```bash
R=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$R \
    name 'Dictado por Voz'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$R \
    binding '<Control>space'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$R \
    command 'bash -c "systemctl --user kill --kill-who=main -s SIGUSR1 skipkey || pkill -USR1 -f \"usr/lib/skipkey/skipkey$\""'
```

Y para «escribir donde está el cursor» (`Ctrl+Alt+Espacio`), lo mismo con
`custom1`, `SIGUSR2` y `<Control><Alt>space`.

## 7. Primera vez con la palabra clave

La escucha local usa faster-whisper `base` (~142 MB) y lo descarga la primera
vez a `~/.cache/huggingface`: hace falta internet esa vez. También se puede
copiar esa carpeta desde otro equipo.

## 8. Comprobar que va bien

```bash
tail -f ~/.local/state/skipkey/logs/registro_cliente_voz.log
```

Debe aparecer, en este orden:

```
[TRANSPORTE] Puente HTTPS (https://…): se dicta desde cualquier red
[SERVIDOR] Cuenta validada: el servidor acepta el dictado
[BANDEJA] icono listo
[CLIENTE] Listo. Usa la tecla activadora para dictar
```

Al pulsar el atajo: `[GRABANDO… (normal)]` y, al pulsar de nuevo,
`[DETENIDO. Procesando…]`; después, `[PUENTE] Enviando N s de audio por HTTPS…`
y el texto en el portapapeles. Si en vez de eso dice
`[TRANSPORTE] El puente no responde; se usa ZMQ`, revisa la conexión o la clave
del proxy.

## 9. Actualizar

Se reemplaza el AppImage (`~/.local/bin/skipkey`) y se reinicia la unidad:

```bash
systemctl --user restart skipkey
```
