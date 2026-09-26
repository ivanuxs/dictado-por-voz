# Cliente de Dictado por Voz — Windows 10

Producto final de **un solo archivo** (`ClienteVoz.exe`) que convierte una PC
Windows 10 en un **cliente dedicado** del servidor de dictado (la dirección del
servidor se configura en `comandos_windows.json`, campo `servidor`).

El `.exe` incluye todo (Python, dependencias, código y sonidos generados en
tiempo de ejecución). **No es necesario instalar Python en la PC destino**.

---

## 1. Requisitos previos (solo para CONSTRUIR el exe)

- Una PC **Windows 10/11** con internet.
- **Python 3.12** instalado desde <https://www.python.org/downloads/>
  (marcar **"Add Python to PATH"** durante la instalación).

> La PC donde solo se va a **usar** el cliente no necesita nada: solo copiar
> `ClienteVoz.exe`.

---

## 2. Construir el producto final (una sola vez)

1. Copia a la PC Windows la carpeta con los archivos del cliente:
   `cliente_voz.py`, `voz_asistente.py`, `indicador_grabacion.py`,
   `protocolo.py`, `utils_logging.py`, `sonidos.py`,
   `comandos_windows.json`, `requisitos_windows.txt`,
   `cliente_voz.spec` y `construir_exe_windows.bat`.
   (No hace falta copiar `entorno_dictado/` ni `vosk-model-*`; son del
   desarrollo en Linux o del servidor.)

2. Doble clic en **`construir_exe_windows.bat`**. Hará automáticamente:
   - Crear un entorno virtual `entorno_dictado_win`.
   - Instalar dependencias (pyzmq, numpy, sounddevice, pynput, pyautogui,
     pyperclip, edge-tts, requests, pycaw, comtypes, pygame, pyinstaller).
   - Compilar con PyInstaller.
   - Generar **`ClienteVoz.exe`** en la misma carpeta.

3. Ese `ClienteVoz.exe` ya es el **producto final**: cópialo donde quieras
   (USB, red, etc.).

---

## 3. Instalar el cliente dedicado en una PC Windows

1. Copia `ClienteVoz.exe` a la PC destino (por ejemplo a `C:\ClienteVoz\`).
2. Ejecuta el instalador:

   ```
   ClienteVoz.exe --instalar
   ```

   Esto:
   - Crea `comandos_windows.json` junto al exe (si no existe).
   - Registra la tarea **"ClienteVoz"** en el Programador de tareas
     (disparador: *al iniciar sesión*).
   - Te muestra un mensaje de confirmación con el servidor configurado
     (el que figure en `comandos_windows.json`).

3. A partir de ese momento, cada vez que esa PC inicie sesión, el cliente
   dedicado arranca automáticamente, sin ventana de consola, y se conecta al
   servidor configurado.

### Desinstalar

```
ClienteVoz.exe --desinstalar
```

Elimina la tarea de inicio automático. Para quitar también el programa,
borra la carpeta donde está el exe.

---

## 4. Uso (igual que en Linux)

- **Tecla activadora**: botón del auricular (`media_play_pause`) o `Ctrl+Espacio`
  (configurable en `comandos_windows.json`, campo `tecla_activadora`).
- **Modo**: `toggle` por defecto (un clic para empezar a grabar, otro para
  detener). Se cambia con `modo_activacion` (`toggle` o `mantener`).
- **Dictado normal**: el texto transcrito se copia al portapapeles y tú lo pegas
  con `Ctrl+V` donde quieras.
- **Dictado que escribe en vivo (donde está el cursor)**: pulsa
  `Ctrl+Alt+Espacio` (campo `tecla_activadora_escribir` del JSON) y el texto va
  **apareciendo mientras hablas** en la ventana que tengas activa, sin que hagas
  nada. El servidor corta el audio por frases (cuando detecta medio segundo de
  pausa, o cada 8 s si hablas sin parar) y el cliente pega cada frase al vuelo.
  Si prefieres que **todo** el dictado funcione así, pon
  `"modo_salida": "escribir"`.
- Para el modo en vivo el **servidor** también debe estar actualizado
  (`servidor_voz.py` y `protocolo.py`): con un servidor viejo el cliente sigue
  funcionando, pero el texto se pega recién al terminar de dictar.
- La ventanita roja **REC** aparece mientras grabas.

### Comandos de voz (con "computadora")

| Comando | Acción en Windows |
|---|---|
| `computadora abrir firefox` | Abre la app (asociación por nombre/URI) |
| `computadora cerrar firefox` | `taskkill /F /IM firefox.exe` |
| `computadora subir/bajar volumen` | Volumen ±10% (pycaw) |
| `computadora silenciar` | Silencia/activa el sonido |
| `computadora reproducir / siguiente / anterior` | Teclas multimedia (pyautogui) |
| `computadora capturar pantalla` | `Impr Pant` |
| `computadora bloquear pantalla` | Bloquea la sesión de Windows |
| `computadora pon una alarma 5 minutos` | Alarma con sonido + TTS |

Las apps disponibles se configuran en `comandos_windows.json`
(secciones `abre` y `cerrar`).

### Notas

- **Abrir** usa `os.startfile`, así que admite nombres de programa
  registrados (p. ej. `chrome`, `msedge`, `spotify`) y URIs de Windows
  (p. ej. `ms-settings:`).
- **Cerrar** usa `taskkill /F /IM <proceso>.exe`; no agregues `explorer.exe`
  a la sección `cerrar` (apagaría el shell de Windows).
- Los sonidos (alarma y fin de dictado) se generan en tiempo de ejecución,
  no necesitan archivos adicionales.
- El TTS (edge-tts) se reproduce con **pygame** en Windows (sin necesidad de
  FFmpeg).
- **DeepSeek**: para el comando `computadora consulta global ...`, define la
  variable de entorno `DEEPSEEK_API_KEY` en la PC Windows.

### Diagnóstico del micrófono

Si el dictado "se queda en transcribiendo" y no aparece texto, casi siempre
el problema está en el micrófono de la PC, no en el servidor. El cliente
incluye tres ayudas:

1. Prueba del micrófono (recomendado para empezar):

   ```
   ClienteVoz.exe --probar-microfono 5
   ```

   Graba 5 segundos y muestra en pantalla el dispositivo de entrada elegido y
   el nivel captado. Si dice "Casi no se captó señal", revisa en Windows el
   dispositivo de entrada (Configuración → Sistema → Sonido → Entrada), que no
   esté muteado y que sea el micrófono que realmente usas.

2. Avisos en pantalla: como el ejecutable no tiene consola, ahora muestra un
   cuadro de diálogo cuando el micrófono no entrega audio, cuando el servidor
   no responde en 180 s y cuando el servidor responde "no se detectó voz" o
   "no se recibió audio".

3. Log: en `logs\registro_cliente_voz.log` (junto al exe) queda registrado el
   dispositivo de entrada usado, el nivel captado cada 60 s y los errores de
   la tarjeta de sonido.

### Arranque automático con Windows y el micrófono

Si el cliente se inicia solo (carpeta **Inicio**, Programador de tareas o
`--instalar`), puede abrirse **antes** de que el sistema de audio esté listo:
en ese momento el micrófono o el auricular Bluetooth todavía no existen para
Windows y la apertura de la captura falla.

Desde la versión con "recuperación automática", el cliente **reintenta la
captura cada 10 s** (y cada 60 s después del primer minuto) hasta lograrlo, así
que basta con esperar unos segundos después de iniciar sesión: en el log se ve
`Captura de audio iniciada a 16000 Hz` o `Captura de audio recuperada.`

Si prefieres evitarlo del todo, retrasa el arranque unos segundos:

- Programador de tareas: edita la tarea `ClienteVoz` y marca
  *"Retrasar la tarea durante: 30 segundos"*.
- Carpeta Inicio: abre un Bloc de notas, pega
  `timeout /t 30 /nobreak & start "" "C:\ClienteVoz\ClienteVoz.exe"`,
  guárdalo como `ClienteVoz.cmd` y usa ese archivo en la carpeta Inicio.

---

## 5. Solución de problemas

| Problema | Solución |
|---|---|
| `--instalar` no crea la tarea | Ejecuta el exe desde una consola como usuario normal (no admin) y revisa el mensaje de error. |
| No se escucha el TTS | Verifica que haya un dispositivo de salida de audio activo en Windows. |
| No llega texto del servidor | Confirma que el servidor esté corriendo en la PC de procesamiento y que la IP/`servidor` en `comandos_windows.json` sea correcta. |
| El dictado "se queda transcribiendo" y no aparece texto | Casi siempre es el micrófono: ejecuta `ClienteVoz.exe --probar-microfono` y revisa el dispositivo de entrada. El servidor ahora responde con un aviso que el cliente muestra en pantalla. |
| Al iniciar con Windows no detecta el micrófono | El cliente se abre antes que el sistema de audio (y que el auricular Bluetooth). Ahora reintenta la captura cada 10 s hasta conseguirlo; también puedes retrasar el arranque unos segundos (ver "Arranque automático con Windows"). |
| El volumen no cambia | El control de volumen necesita un dispositivo de audio activo; revisa los logs en `logs\`. |

Los logs se guardan en `logs\registro_cliente_voz.log` (junto al exe).
