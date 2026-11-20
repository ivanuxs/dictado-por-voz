# Cliente de SkipKey — Windows 10

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

> La PC donde solo se va a **usar** el cliente no necesita Python: basta copiar
> `ClienteVoz.exe`, entrar con la cuenta (ver §3) y dejarlo arrancar una vez con
> internet para que baje el modelo de la escucha local.

---

## 2. Construir el producto final (una sola vez)

1. Copia a la PC Windows la carpeta `cliente_windows/` del repositorio. Lo
   mínimo para compilar es:
   `cliente_voz.py`, `bandeja.py`, `escucha_local.py`, `indicador_grabacion.py`,
   `ventana_configuracion.py`, `skills.py`, `marcas.py`, `sonidos.py`,
   `voz_asistente.py`, `protocolo.py`, `utils_logging.py`, `sesion.py`,
   `perfil.py`, `diccionario_desktop.py`, `preparar_clave.py`,
   `comandos_windows.json`, `requisitos_windows.txt`, `cliente_voz.spec` y
   `construir_exe_windows.bat` (si viene también `clave_proxy.txt`, mejor: lleva
   la clave del proxy y no hay que tocar nada).
   El `.spec` los necesita **todos**: si falta uno, el `.exe` se compila pero
   arranca sin esa función (icono, palabra clave, ventana…) o falla al importar.
   (No hace falta copiar `entorno_dictado/`, ni `vosk-model-*`, ni ningún modelo
   de Whisper: los de Vosk eran del desarrollo y el de Whisper se descarga solo
   la primera vez que se usa la escucha local.)

2. Doble clic en **`construir_exe_windows.bat`**. **No hay que configurar nada ni
   ejecutar comandos**: la clave del proxy se toma sola de `clave_proxy.txt`
   (viene en la carpeta), de la variable `DICTADO_PUENTE_CLAVE` si existiera o de
   `"puente_clave"` del JSON. Hará automáticamente:
   - Crear un entorno virtual `entorno_dictado_win`.
   - Instalar dependencias (pyzmq, numpy, sounddevice, pynput, pyautogui,
     pyperclip, edge-tts, requests, pycaw, comtypes, pygame, pystray, Pillow,
     faster-whisper, pyinstaller).
   - Generar `clave_proxy_compilada.py` con la clave (archivo **no** versionado:
     lo excluye `.gitignore`).
   - Compilar con PyInstaller.
   - Generar **`ClienteVoz.exe`** en la misma carpeta.

3. Ese `ClienteVoz.exe` ya es el **producto final**: cópialo a la PC destino
   (USB, red, etc.), a una carpeta **escribible** (`C:\ClienteVoz\`, nunca
   `C:\Program Files`).

4. **Para entregar un instalador de un solo archivo** (recomendado): doble clic
   en `instalador\crear_instalador.bat`. Genera
   `instalador\salida\SkipKey_Instalador_<versión>.exe`, que ya lleva dentro el
   cliente, la configuración y la clave del proxy. Si ya hay un `ClienteVoz.exe`
   viejo y quieres rehacerlo con los últimos cambios, ejecuta
   `crear_instalador.bat --recompilar`. Necesita **Inno Setup 6 o superior** en
   la PC de construcción (probado con 7:
   <https://jrsoftware.org/isdl.php>); la PC donde se instala no lo necesita.

---

## 3. Instalar el cliente en una PC Windows

### Con el instalador (lo normal)

Doble clic en **`SkipKey_Instalador_<versión>.exe`**. Hace todo:

- Instala **sin pedir administrador** en `%LOCALAPPDATA%\Programs\SkipKey`
  (carpeta escribible: ahí van `comandos_windows.json` y `logs\`).
- Cierra el cliente si estaba abierto, y **conserva la configuración y los
  registros** al actualizar.
- Crea los accesos del menú Inicio (cliente y configuración) y, si lo marcas,
  el del escritorio.
- Deja el arranque con Windows con un **acceso directo en la carpeta Inicio**
  del usuario (`shell:startup`), que se borra solo al desinstalar. No usa
  tareas programadas: `schtasks` falla sin permisos de administrador y el
  instalador instala por usuario (2026-11-19).
- Si la clave del proxy iba en el paquete, no hay que escribir nada: solo
  usuario y contraseña la primera vez.

### Instalación manual (sin instalador)

1. Copia `ClienteVoz.exe` a la PC destino (por ejemplo a `C:\ClienteVoz\`).
2. Ejecuta:

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

### Instalar en otra PC (lista de comprobación)

El **mismo `ClienteVoz.exe`** sirve para cualquier PC Windows de **64 bits**
(Windows 10 u 11): lleva Python y todas las dependencias dentro, así que en el
destino no hay que instalar nada. No sirve para Windows de 32 bits ni ARM.
Lo que **sí** es propio de cada PC:

1. **Carpeta escribible**: cópialo a `C:\ClienteVoz\` (o similar), nunca a
   `C:\Program Files`. El cliente crea `comandos_windows.json` y `logs\`
   **junto al exe**, y ahí no se puede escribir sin permisos de administrador.
2. **Instalar una vez por PC**: `ClienteVoz.exe --instalar`. La tarea del
   Programador de tareas guarda la **ruta completa** del exe: si después lo
   mueves o renombras la carpeta, repite `--desinstalar` y `--instalar`.
3. **Entrar con la cuenta**: el cliente de escritorio **no dicta sin sesión**
   (desde el 2026-11-18). Icono de la bandeja → **Configuración…** → pestaña
   **Cuenta**: arriba se ve solo el **servidor** al que apunta
   (`https://dictado.hlquantlabs.trade`, el **dominio público**); escribe
   usuario y contraseña y pulsa «Entrar» (o «Crear cuenta» si es nueva).
   **No hay que escribir ninguna clave del proxy**: el `.exe` la lleva
   incrustada, igual que el APK (2026-11-19).
4. **Si el `.exe` se compiló sin la clave** (el `.bat` lo avisa), al entrar
   dirá «El servidor rechazó la conexión (403): a este cliente le falta la
   clave del proxy». Con el cliente actualizado se arregla **sin recompilar**:
   deja `clave_proxy.txt` (una línea con la clave) junto al `.exe` y reinícialo.
   Los campos manuales de dirección y clave siguen dentro de **«Avanzado»**,
   como salida de emergencia para el técnico.
5. **Configuración propia**: cada PC tiene su `comandos_windows.json`
   (servidor, teclas, programas, web, cuenta). Cambiarlo en una **no** cambia
   las demás; si quieres la misma, copia también ese JSON (entonces esa PC
   llevará la clave del proxy escrita, así que ese archivo no se sube al
   repositorio).
6. **Modelo de la escucha**: no va dentro del `.exe`; faster-whisper lo descarga
   (~142 MB) en la primera ejecución de la escucha local, así que esa primera vez
   hace falta **internet** (o se deja el modelo junto al exe, ver «Dictado por
   palabra clave»).
7. **Consultas al asistente**: define `DEEPSEEK_API_KEY` en las variables de
   entorno de esa PC. Sin ella, el dictado y los comandos funcionan igual.
8. **Avisos esperados**: el `.exe` no está firmado, así que Windows puede mostrar
   «Windows protegió su PC» la primera vez (Más información → Ejecutar) y algún
   antivirus puede marcarlo. No es un fallo del cliente.

Los registros de cada PC quedan en `logs\registro_cliente_voz.log`, junto al exe.

---

## 4. Uso (igual que en Linux)

- **Cuenta**: el cliente **no dicta sin sesión**. Se entra por el icono de la
  bandeja → **Configuración…** → pestaña **Cuenta**: dominio público del puente
  (`https://dictado.hlquantlabs.trade`), usuario y contraseña (botón «Crear
  cuenta» si es nueva). La clave del proxy ya va dentro del `.exe`. Sin sesión,
  cada intento de dictado suena el pitido de error y queda en el registro como
  `[CUENTA] Dictado bloqueado: no hay sesión`.
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

### Dictado por palabra clave (Whisper)

Con `"escucha_local": true` en `comandos_windows.json`, el cliente oye la palabra
clave con **faster-whisper `base`** —el mismo motor que el cliente de Ubuntu y el
teclado de Android— y abre el dictado (o el modo comando) sin tocar `Ctrl+Espacio`.

**Antes hacía falta el paquete de voz de Windows y `pywin32`; ya no.** SAPI
resultó inviable: en un equipo sin motor de reconocimiento instalado,
`CreateRecoContext` falla, y aunque lo haya, se pelea por el micrófono con la
captura del cliente. Whisper se alimenta con los mismos chunks que el cliente ya
está grabando, así que no abre el micrófono por su cuenta.

**Modelo**: no se copia junto al exe. faster-whisper lo descarga la primera vez
que se usa (~142 MB) en `C:\Users\<usuario>\.cache\huggingface`, así que esa
primera vez hace falta internet. Para no depender de la red se puede dejar un
modelo ya convertido junto al `.exe`, en `whisper-base\` o en
`modelos\whisper-base\`, o indicar otra cosa en `DICTADO_MODELO_WHISPER`
(`tiny`, `base`, `small` o una ruta). Si no lo encuentra, el log lo dice y el
dictado por tecla sigue funcionando.

Se comprueba en 20 s, sin arrancar el cliente entero:

```
ClienteVoz.exe --probar-escucha 20
```

Palabras configurables en el JSON:

```json
"clave": "computadora",
"frase_fin": ["fin del dictado", "fin de dictado", "finalización del dictado"]
```

- Cualquiera de las frases de `frase_fin` cierra el dictado. `frase_fin` puede
  ser una cadena suelta o una lista.
- Una **pausa al hablar no cierra nada**: el dictado termina cuando dices la
  frase. Quedan dos seguros: 6 s sin haber dicho nada desde el pitido (un
  disparo por error se cierra solo) y un tope de 180 s.
- La frase de cierre la **quita el propio cliente** del final del texto. Al
  servidor solo se le pide que marque el dictado como de voz; no sabe qué frase
  se dijo.

Cuatro sonidos, todos generados por código:

| Momento | Sonido |
|---|---|
| Se oye la palabra clave | ding **ascendente** (`inicio`) |
| Se oye la frase de cierre | ding **descendente** (`fin`) |
| El texto ya está en el portapapeles | **pip corto** (`texto`) |
| Un aviso o un fallo | dos tonos graves (`error`) |

### Icono en el área de notificación

El cliente pone un icono en el área de notificación (junto al reloj) con un menú
para lo que no se puede hacer con la tecla activadora:

| Elemento del menú | Para qué sirve |
|---|---|
| ☑ **Escucha continua (palabra clave)** | Enciende o apaga la escucha local (Whisper) en caliente |
| ☑ **Comandos por voz** | **Pausa los comandos**: lo que dictes se escribe como texto, sin ejecutar acciones |
| **Dictar ahora** | Empieza o termina un dictado sin tocar `Ctrl+Espacio` |
| **Configuración…** | Abre la ventana: palabras, programas, web, voz y la pestaña **Cuenta** |
| **Ver registro** | Abre la carpeta de registros del cliente |
| **Salir** | Cierra el cliente |

El icono es un círculo con un micrófono y cambia de color:

| Color | Estado |
|---|---|
| gris | escucha apagada |
| verde | esperando la palabra clave |
| ámbar | escucha activa pero comandos en pausa |
| rojo | grabando |

**Si no ves el icono:**

1. **Windows 11 lo esconde.** Los iconos nuevos van dentro de la **flecha `^`**
   del área de notificación. Se activan en Configuración → Personalización →
   Barra de tareas → **Otros iconos del sistema** → «SkipKey».
2. **Falta `pystray` o `Pillow`** en el entorno de compilación. Ahora el cliente
   avisa con un cuadro de diálogo y con el sonido de error, y el registro dice
   `[BANDEJA] sin icono en la bandeja: falta pystray o Pillow`.
3. **El `.exe` es anterior a `bandeja.py`.** Vuelve a compilarlo con
   `construir_exe_windows.bat`.

Para comprobarlo sin arrancar el cliente entero:

```bat
ClienteVoz.exe --probar-bandeja 30
```

Pone el icono y lo retira a los 30 s. Si aparece, el icono funciona y el
problema está en otro sitio; si no aparece, el aviso dirá qué falta.

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
incluye cinco ayudas:

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

4. Escucha local: `ClienteVoz.exe --probar-escucha 20` carga el modelo de
   Whisper, abre el micrófono y avisa cuántas veces oyó la palabra clave. Sirve
   para separar el problema del micrófono del problema del modelo.

5. Icono de la bandeja: `ClienteVoz.exe --probar-bandeja 30` pone el icono y lo
   retira a los 30 s; si no aparece, el aviso dice qué falta.

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
| No dicta y el log dice «Dictado bloqueado: no hay sesión» | El cliente de escritorio necesita cuenta: icono → **Configuración…** → pestaña **Cuenta** (dirección del puente + usuario y contraseña, o «Crear cuenta»). |
| Al entrar dice «falta la clave del proxy» (403) | Con el cliente actualizado: deja `clave_proxy.txt` junto al `.exe` y reinícialo. Con un `.exe` anterior al 2026-11-19 hay que recompilar (o escribirla en «Avanzado»). |
| No se escucha el TTS | Verifica que haya un dispositivo de salida de audio activo en Windows. |
| No llega texto del servidor | Confirma que el servidor esté corriendo en la PC de procesamiento y que la IP/`servidor` en `comandos_windows.json` sea correcta. |
| El dictado "se queda transcribiendo" y no aparece texto | Casi siempre es el micrófono: ejecuta `ClienteVoz.exe --probar-microfono` y revisa el dispositivo de entrada. El servidor ahora responde con un aviso que el cliente muestra en pantalla. |
| Al iniciar con Windows no detecta el micrófono | El cliente se abre antes que el sistema de audio (y que el auricular Bluetooth). Ahora reintenta la captura cada 10 s hasta conseguirlo; también puedes retrasar el arranque unos segundos (ver "Arranque automático con Windows"). |
| El volumen no cambia | El control de volumen necesita un dispositivo de audio activo; revisa los logs en `logs\`. |
| El cliente «deja de funcionar solo» | Mira `logs\registro_cliente_voz.log`: cualquier excepción no controlada queda como `[ERROR NO CONTROLADO]` con su traza. Si el proceso se cae, se **relanza solo** con espera creciente (se rinde tras 5 caídas en menos de 30 s). Si el icono sigue en la bandeja, no murió: está bloqueando el dictado por falta de sesión. |
| Abro la configuración y el registro parece vacío | Ya no pasa: desde el 2026-11-19 la ventana de configuración y las pruebas (`--probar-*`) **no** rotan el registro, y una segunda copia del cliente se cierra sola sin tocarlo. |

Los logs se guardan en `logs\registro_cliente_voz.log` (junto al exe).
