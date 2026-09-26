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

## Estructura del proyecto

```
dictado por voz/                    ← Carpeta del CLIENTE (PC usuario)
├── cliente_voz.py                  # Cliente ZMQ: micrófono, teclado, inyección texto, comandos, TTS
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
└── servidor/                       # Carpeta para COPIAR a la PC de procesamiento
    ├── servidor_voz.py             # Servidor ZMQ: Vosk + Whisper
    ├── servidor-voz.service        # Servicio systemd para el servidor
    ├── escucha_continua.py         # Detección Vosk (recibe audio vía alimentar())
    ├── protocolo.py                # (copia del compartido)
    ├── utils_logging.py            # (copia del compartido)
    ├── requirements.txt            # Dependencias Python del servidor
    └── README.md                   # Instrucciones de instalación en el servidor
```

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
- Botón del auricular (`media_play_pause`) y `ctrl+space` por defecto.
- Soporta teclas multimedia, modificadores y combinaciones con `+`.
- Configurable en `comandos.json` (`tecla_activadora`, admite lista).

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

## Rotación de logs
Al iniciar, `cliente_voz.py` llama a `rotar_logs_al_iniciar()` que mueve los logs anteriores a `logs/historial/` con marca de tiempo y empieza un archivo limpio. Esto evita que los logs crezcan indefinidamente.

## Extensibilidad
Para añadir nuevos comandos de voz:
1. Editar `comandos.json` añadiendo entradas en `abrir` o `cerrar`.
2. Para nuevas acciones (volumen, brillo, etc.), extender `procesar_comando()` en `cliente_voz.py`.

## Repositorio Git
- El repositorio es **público**: no subir nunca claves, tokens ni datos personales.
- `DEEPSEEK_API_KEY` vive en `~/.config/cliente-voz.env` (fuera del repositorio), cargado con
  `EnvironmentFile=` desde `cliente-voz.service`. Está excluido por `.gitignore`.
- Excluidos por `.gitignore`: `entorno_dictado/`, `vosk-model-es/`, `logs/`, `resguardos/`,
  `.qwen/`, `.env` y los artefactos de compilación de PyInstaller.
- La dirección del servidor se resuelve en este orden: `--servidor IP:PUERTO` >
  `DICTADO_SERVIDOR` (variable de entorno) > campo `"servidor"` de `comandos.json` >
  `127.0.0.1:5555`.
- Antes de cada `git push`, comprobar con `git status` y `git ls-files` que no se cuele nada
  pesado ni ninguna credencial.
