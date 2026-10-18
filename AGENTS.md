# Dictado por Voz — AGENTS.md

## Idioma
**Todo el diálogo con el usuario debe ser en español.** El código, nombres de variables, funciones y comentarios pueden estar en español o inglés según convenga al proyecto, pero la comunicación con el usuario es siempre en español.

**El usuario es de Perú: usar español neutro peruano.** Hablar con "tú" (tienes, quieres, puedes), nunca con "vos" ni voseo ("querés", "podés", "tenés"). Preferir léxico neutro latinoamericano (computadora, celular, programar).

## Flujo de trabajo (regla obligatoria)
**Siempre presentar un plan antes de ejecutar cualquier cambio.**

- Ante cualquier solicitud que implique modificar, crear o eliminar archivos: **no tocar nada todavía**.
- Primero presentar un plan claro con:
  - Qué se va a hacer y por qué.
  - Archivos que se crearán, modificarán o eliminarán.
  - Cambios concretos y riesgos/consideraciones.
- Esperar la confirmación explícita del usuario (p. ej. "procede", "adelante", "hazlo") antes de empezar a editar.
- Si el usuario hace preguntas o pide entender algo, responder y aclarar antes de proponer cambios.
- Las preguntas de decisión se pueden resolver con preguntas de opción múltiple (recomendada) para no asumir.

## Descripción del proyecto
Herramienta de dictado por voz para Ubuntu que transcribe audio a texto y ejecuta comandos del sistema mediante voz.
Desde 2026-10 hay además un **puente web** (`puente_web/`) y un **teclado para Android**
(`cliente_android/`) que permiten dictar desde el celular, incluso con datos móviles, sin
tocar el servidor de transcripción.

## Estructura del proyecto

```
dictado por voz/                    ← Carpeta del CLIENTE (PC usuario)
├── cliente_voz.py                  # Cliente ZMQ: micrófono, teclado, inyección texto, comandos, TTS
├── mpris_dictado.py                # Reproductor MPRIS «Dictado por Voz» (botón del auricular Bluetooth)
├── senal_cliente.py                # Manda SIGUSR1/SIGUSR2 al cliente en ejecución (compartido)
├── cliente-voz.service             # Servicio systemd para el cliente
├── comandos.json                   # Configuración de comandos de voz y teclas
├── voz_asistente.py                # TTS (edge-tts) y consultas DeepSeek
├── indicador_grabacion.py          # Ventana flotante "REC" (Tkinter)
├── protocolo.py                    # Tipos de mensaje ZMQ (compartido con el servidor)
├── utils_logging.py                # Utilidades de logging (compartido con el servidor)
├── AGENTS.md                       # Este archivo
├── README.md                       # Portada del repositorio (instalación y uso)
├── LICENSE                         # Licencia MIT
├── requirements.txt                # Dependencias del cliente (Linux)
├── .gitignore                      # Exclusiones de Git (venv, modelos, logs, .env)
├── .gitattributes                  # Normalización de saltos de línea
├── .env.example                    # Plantilla de variables de entorno
├── entorno_dictado/                # Entorno virtual de Python
├── vosk-model-es/                  # Modelo Vosk (copiar también al servidor)
├── logs/                           # Archivos de log generados
│   └── historial/                  # Logs rotados
├── scratch/
│   └── detectar_tecla.py           # Diagnóstico de teclas
├── resguardos/                     # Archivos retirados (dictado.py, dictado-voz.service)
├── entorno_web/                    # Entorno virtual del puente web (excluido de Git)
├── cliente_windows/                # Cliente para Windows (carpeta para copiar a esa PC)
├── puente_web/                     # Puente HTTPS/WSS → servidor de dictado (ver su README)
└── cliente_android/                # Teclado (IME) de Android en Kotlin (ver su README)
```

> **El servidor de transcripción ya no vive en este repositorio.** Está en su propio
> repositorio y carpeta: `/home/ivan/Documentos/servidor_dictado`
> (`github.com/ivanuxs/servidor-voz`), donde es un **servicio de sistema** que usa
> faster-whisper `large-v3` en la GPU. Este repositorio **no lo modifica**: solo se
> comunica con él (el cliente de Linux/Windows por ZMQ y el puente web por ZMQ local).

## Características principales

### Arquitectura Cliente-Servidor (ZMQ DEALER/ROUTER)

```
CLIENTE (PC usuario)                    SERVIDOR (PC procesamiento)
┌──────────────────────┐                ┌──────────────────────────┐
│ cliente_voz.py        │   ZMQ TCP     │ servidor_voz.py           │
│                       │               │                           │
│ sounddevice ──────────┼──────────────►│ Vosk (detección keywords) │
│ (captura micrófono)   │  AUDIO        │                           │
│                       │               │ Whisper (transcripción)   │
│ pynput ───────────────┼──────────────►│                           │
│ (tecla activadora)    │  DICTADO_*    │                           │
│                       │               │                           │
│ ◄─────────────────────┼───────────────│ TEXTO / COMANDO           │
│ pyautogui + pyperclip │               │                           │
│ comandos sistema      │               │                           │
│ edge-tts (TTS)        │               │                           │
│ indicador grabación   │               │                           │
└──────────────────────┘                └──────────────────────────┘
```

- **Comunicación**: ZMQ con patrón DEALER (cliente) / ROUTER (servidor)
- **Mensajes**: multipart 2 frames `[tipo, payload]`
- **Audio**: chunks int16 1024 muestras (~64 ms) a 16 kHz, enviados continuamente
- **Puerto**: TCP 5555 (configurable con `--servidor IP:PUERTO`)

### Puente web para Android (`puente_web/`)

Android **no habla ZMQ** y el servidor tiene **un solo modelo en memoria**, así que no se
puede poner un backend que cargue Whisper por su cuenta (no cabe en 6 GB de VRAM). La
solución es un puente que **es un cliente ZMQ más** del servidor (no lo toca) y expone
HTTP/WebSocket:

```
Android (IME) ──HTTPS/WSS──▶ nginx (192.168.1.100) ──▶ puente (192.168.1.2:8000) ──ZMQ──▶ 127.0.0.1:5555
```

- Es un **servicio de usuario** (`systemctl --user`, sin sudo) en `puente_web/`, con su
  propio entorno virtual `entorno_web/`.
- Rutas: `POST /api/v1/transcribe`, `WS /ws/transcribe`, `GET /api/v1/salud` y
  `/api/v1/estado`. Detalle completo en `puente_web/README.md`.
- Token obligatorio (`DICTADO_WEB_TOKEN` en `~/.config/puente-web.env`); arranca con 64
  caracteres aleatorios. Sin token el puente **no arranca**.
- La transcripción es **de una en una** (un modelo, un candado). Los límites (60 s, 5 MB,
  cola de 2) protegen la GPU de un abuso con el token.

⚠️ **Trampas del protocolo que ya están resueltas en el puente; no las deshagas:**

1. Si un dictado se corta sin `DICTADO_STOP`, el servidor se queda en "dictando" e **ignora
   el siguiente `DICTADO_START`**. El puente cierra siempre la sesión y **descarta la
   respuesta** de ese cierre (si no, el próximo dictado leería el texto del anterior).
   Probado con `prueba_puente.py --cancelar`.
2. **Nunca reenviar el puerto 5555 en nginx.** En el servidor, el flag `_autenticado` es
   **global**, no por cliente: en cuanto un cliente se autentica, cualquier otro cliente
   ZMQ queda aceptado. La frontera de seguridad es el puente (Bearer + la clave que valida
   nginx), nunca el token del ZMQ.
3. El puente mantiene **una sola conexión persistente**: el servidor crea un reconocedor
   Vosk por cada cliente que se conecta.

### Cliente Android (`cliente_android/`)

Teclado del sistema (IME) en Kotlin, `minSdk 26`, sin frameworks pesados: captura audio,
lo manda al puente y escribe el texto. **No hay lógica de transcripción aquí.**

- Compilar: `bash cliente_android/construir_apk.sh` (APK en `app/build/outputs/apk/debug/`).
- Las herramientas se instalan sin sudo con `cliente_android/herramientas/instalar_sdk.sh`
  (JDK 17 + SDK 34 + Gradle 8.7 en `~/Android`).
- **Java 25 no sirve para el plugin de Android**: hay que usar el JDK 17.
- Las credenciales (dirección, token y clave) van **dentro del APK**, leídas de
  `cliente_android/local.properties` (no versionado) como `BuildConfig`. La app no
  obliga a escribir nada en el celular. También acepta configurarse por enlace:
  `dictadoime://config?url=...&token=...&clave=...`.
- El teclado tiene **dos modos**: dictado (micrófono grande) y **edición** (teclado de letras
  propio, `TecladoLetras.kt`, construido en código: página de letras y de símbolos, ⇧/⇪ y ⌫
  con repetición). El botón ⌨ cambia de modo, así **no hace falta salir a Gboard**; mantener
  pulsado ⌨ (o `?123`) abre el selector del sistema para quien quiera Gboard igualmente.
- Arranque y uso: `cliente_android/README_android.md`.

⚠️ **Trampas ya pagadas:**

1. El `android.jar` de `platforms/android-34` instalado aquí **no expone
   `switchToPreviousInputMethod()`** (solo las variantes con `IBinder`, que son API interna).
   El botón de teclado usa `switchToLastInputMethod(token)` para volver al teclado anterior,
   con respaldo al público `showInputMethodPicker()` si el sistema lo bloquea (el selector
   queda en el pulsado largo).
2. El **permiso de micrófono** lo pide `AjustesActivity`: un `InputMethodService` no puede
   mostrar el diálogo de permisos.
3. `python3 -m zipfile -e` **no conserva el bit de ejecución** (a diferencia de `unzip`): el
   instalador del SDK tiene que hacer `chmod +x` de los binarios que descomprime.

### Dictado por voz
- La transcripción se hace en el servidor (Vosk para detección de keywords, Whisper para transcripción).
- El texto transcrito se inyecta en la aplicación activa mediante portapapeles (`pyperclip` + `pyautogui`).
- Idioma: español (`es`).

### Comandos de voz
- Palabra clave: **"computadora"** al inicio de la frase.
- Acciones soportadas:
  - `abrir`/`cerrar` programas (configurables en `comandos.json`).
  - Control de volumen: `subir volumen`, `bajar volumen`, `silenciar`.
  - Multimedia: `reproducir`/`pausar`, `siguiente`, `anterior`.
  - Sistema: `capturar pantalla`, `bloquear pantalla`.
  - Alarma: `pon una alarma X minutos/segundos/horas`, `coloca una alarma X minutos`.
- Ejemplos:
  - *"computadora abrir firefox"* → abre Firefox
  - *"computadora cerrar terminal"* → cierra la terminal
  - *"computadora subir volumen"* → volumen +10%
  - *"computadora siguiente canción"* → siguiente pista
  - *"computadora pon una alarma 5 minutos"* → alarma en 5 minutos (sonido + TTS)

### Modos de activación
- **Toggle** (por defecto): un clic para empezar a grabar, otro clic para detener.
- **Mantener**: mantener presionada la tecla mientras se habla.
- Configurable en `comandos.json` (`modo_activacion`).

### Teclas activadoras (múltiples simultáneas)
- `ctrl+space` por defecto, y el **botón del auricular Bluetooth** cuando está en modo música
  (ver la sección siguiente).
- Soporta teclas multimedia, modificadores y combinaciones con `+`.
- Configurable en `comandos.json` (`tecla_activadora`, admite lista).

### Botón del auricular Bluetooth (AVRCP → MPRIS)

Un auricular Bluetooth **no manda una tecla**: manda un comando **AVRCP** que BlueZ entrega
al **reproductor MPRIS** publicado en la sesión (lo hace `mpris-proxy`, un servicio de
usuario). Por eso `media_play_pause` en `comandos.json` **no puede funcionar con Bluetooth**:
ese botón no aparece nunca en evdev ni en `pynput`.

**La función viene APAGADA** (`"boton_auricular_bluetooth": false` en `comandos.json`).
Solo tiene sentido con un auricular **dedicado a la computadora**: el del usuario es
**multipunto** (también emparejado al celular) y el celular se queda con el botón en cuanto
hay una llamada o música allí — el botón deja de llegar a la PC y **no se recupera desde el
cliente** (hay que reconectar el auricular en la PC o hacer que la PC suene). Con la función
apagada, BlueZ usa su respaldo de siempre: crea un **teclado virtual (uinput)** y emite
`KEY_PLAYCD`/`KEY_PAUSECD`, así que el botón sigue controlando la música como antes.

- `mpris_dictado.py` publica un reproductor MPRIS «Dictado por Voz» (solo si la función está
  encendida); `cliente_voz.py` lo lanza y vigila como proceso hijo (`_bucle_mpris`). Un toque
  = `SIGUSR1` (dictado normal) y dos toques (`Next`/`Previous`) = `SIGUSR2` (escribir donde
  está el cursor).
- Se ejecuta con el **Python del sistema** (`/usr/bin/python3`): necesita `dbus-python`, que
  no está en `entorno_dictado/`. Modos con `DICTADO_MPRIS_MODO` (`ambos` por defecto, o
  `sin_interferir`) y `DICTADO_MPRIS_DOBLE=no` para ignorar el botón doble.
- Los procesos del cliente viven en el **mismo grupo de procesos**: un
  `systemctl --user kill -s SIGUSR1 cliente-voz` **sin `--kill-who=main`** mataría también a
  `mpris_dictado.py` (le pasaba: se suicidaba con cada pulsación). Por eso `senal_cliente.py`
  y los atajos de GNOME usan `--kill-who=main`, y el reproductor ignora SIGUSR1/SIGUSR2.

⚠️ **Trampa gorda, costó una sesión entera: si el dictado captura del micrófono del
auricular, el botón deja de servir.** Capturar del micro del auricular obliga a PipeWire a
ponerlo en **modo manos libres** (`headset-head-unit`, HFP/SCO); el auricular entonces *cree
que está en una llamada* y su botón envía **`AT+CHUP` («colgar»)** en lugar del comando
multimedia. BlueZ responde `ERROR` y no ocurre nada: comprobado con `sudo btmon`, 3
pulsaciones = 3 `AT+CHUP` y cero AVRCP. Para que el botón funcione el auricular tiene que
estar en **`a2dp-sink`** (modo música), y eso exige **no usar su micrófono**
(`pactl list cards | grep 'Perfil activo'`).

- Por eso existe `dispositivo_entrada` en `comandos.json`:
  - `"interno"` (por defecto): micrófono incorporado de la computadora;
  - `""` / `"default"`: el predeterminado del sistema (⚠️ si es el del auricular, el botón
    volverá a ser «colgar»);
  - un número (índice de `sounddevice`) o un trozo del nombre del dispositivo.
- Dato del equipo: el **jack con cable** de este Dell (`sof-hda-dsp Headphone Mic`) no expone
  teclas (`key=0`, solo `EV_SW`), así que un auricular con cable tampoco daría botón.

### Logging
- Toda la salida usa `imprimir()` de `utils_logging.py`.
- Los logs se guardan en `logs/registro_cliente_voz.log`.
- No se imprime en consola; usar `tail -f` para monitorear.

## Dependencias
- `pyzmq` (comunicación con el servidor)
- `numpy` (procesamiento de audio)
- `sounddevice` (captura de audio del micrófono)
- `pynput` (detección de teclas global)
- `pyautogui`, `pyperclip` (inyección de texto)
- `edge-tts` (texto a voz, en `voz_asistente.py`)
- `requests` (consultas a DeepSeek, en `voz_asistente.py`)
- `openai-whisper`, `torch`, `scipy` (transcripción — solo en el servidor)
- `python3-dbus` (paquete del **sistema**, no del entorno virtual): lo necesita
  `mpris_dictado.py` para el botón del auricular Bluetooth. Si falta, el cliente funciona
  igual: solo se pierde ese botón de auricular (queda avisado en el log).

Instaladas en el entorno virtual `entorno_dictado/`.

## Ejecución

### Manual
```bash
# Servidor en IP_DEL_SERVIDOR:5555 (por defecto 127.0.0.1:5555):
"dictado por voz/entorno_dictado/bin/python3" "dictado por voz/cliente_voz.py"

# Con servidor remoto (explícito):
"dictado por voz/entorno_dictado/bin/python3" "dictado por voz/cliente_voz.py" --servidor IP_DEL_SERVIDOR:5555
```

### Como servicio systemd (usuario)
El servicio se ejecuta como usuario (no como root) porque necesita acceso a la pantalla, audio y teclado.

**Instalar:**
```bash
mkdir -p ~/.config/systemd/user
cp "dictado por voz/cliente-voz.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable cliente-voz.service
systemctl --user start cliente-voz.service
```

**Comandos útiles:**
```bash
systemctl --user status cliente-voz   # Ver estado
systemctl --user restart cliente-voz  # Reiniciar
systemctl --user stop cliente-voz     # Detener
journalctl --user -u cliente-voz -f   # Ver logs del servicio
```

### Logs de la aplicación
```bash
tail -f "dictado por voz/logs/registro_cliente_voz.log"
```

### Puente web (servicio de usuario, sin sudo)
```bash
systemctl --user status puente-web    # Ver estado
systemctl --user restart puente-web   # Reiniciar (tras cambiar el .env)
journalctl --user -u puente-web -f    # Consola (avisos de uvicorn)
tail -f "dictado por voz/puente_web/logs/registro_servidor_web.log"

# Comprobaciones con audio real
set -a; . ~/.config/puente-web.env; set +a
entorno_web/bin/python puente_web/prueba_puente.py                 # REST
entorno_web/bin/python puente_web/prueba_puente.py --ws --modo vivo # WebSocket
entorno_web/bin/python puente_web/prueba_puente.py --cancelar       # robustez
```
## Extensibilidad
Para añadir nuevos comandos de voz:
1. Editar `comandos.json` añadiendo entradas en `abrir` o `cerrar`.
2. Para nuevas acciones (volumen, brillo, etc.), extender `procesar_comando()` en `cliente_voz.py`.

## Repositorio Git
- El repositorio es **público**: no subir nunca claves, tokens ni datos personales.
- `DEEPSEEK_API_KEY` vive en `~/.config/cliente-voz.env` (fuera del repositorio), cargado con
  `EnvironmentFile=` desde `cliente-voz.service`. Está excluido por `.gitignore`.
- Excluidos por `.gitignore`: `entorno_dictado/`, `entorno_web/`, `vosk-model-es/`, `logs/`, `resguardos/`,
  `.qwen/`, `.env`, `cliente-voz.env`, `puente-web.env` y los artefactos de compilación de
  PyInstaller **y de Android** (`cliente_android/.gradle|build|local.properties`, `*.apk`).
- La dirección del servidor se resuelve en este orden: `--servidor IP:PUERTO` >
  `DICTADO_SERVIDOR` (variable de entorno) > campo `"servidor"` de `comandos.json` >
  `127.0.0.1:5555`.
- Antes de cada `git push`, comprobar con `git status` y `git ls-files` que no se cuele nada
  pesado ni ninguna credencial.
