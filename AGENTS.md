# SkipKey — AGENTS.md

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
**SkipKey** es la aplicación de dictado por voz, en una sola carpeta y un solo repositorio:

- **Android**: un teclado (IME) que dicta y escribe donde estás escribiendo (`cliente_android/`).
- **Ubuntu y Windows**: clientes que dictan en cualquier aplicación, con palabra clave,
  comandos por voz y consultas al asistente (`cliente_ubuntu/`, `cliente_windows/`).
- **Puente web** (`puente_web/`): lleva el audio del celular al servidor de transcripción por
  HTTPS/WSS, incluso con datos móviles.

El **servidor de transcripción** no vive aquí: es un servicio aparte del que dependen todos
los clientes (ver la sección del servidor más abajo).

> **Mudanza del 2026-11-19:** los clientes de escritorio volvieron a este repositorio. Antes
> vivían en el proyecto `dictado por voz/`, que ya no existe: **`SkipKey/` es el proyecto
> padre** y ahí están el escritorio, Android, el puente y sus entornos.

## Estructura del proyecto

```
SkipKey/                            ← Repositorio: escritorio, Android, puente y entornos
├── cliente_ubuntu/                 ← CLIENTE DE UBUNTU (esta PC)
│   ├── cliente_voz.py              # Cliente ZMQ: micrófono, teclado, inyección texto, comandos, TTS
│   ├── escucha_local.py            # Palabra clave y órdenes en local (faster-whisper)
│   ├── bandeja_ubuntu.py           # Icono en la barra superior (AppIndicator)
│   ├── ventana_configuracion.py    # Ventana de configuración y catálogo de skills (Tkinter)
│   ├── skills.py                   # Descripción de las skills del cliente (para la ventana)
│   ├── mpris_dictado.py            # Reproductor MPRIS «Dictado por Voz» (botón del auricular Bluetooth)
│   ├── senal_cliente.py            # Manda SIGUSR1/SIGUSR2 al cliente en ejecución
│   ├── cliente-voz.service         # Servicio systemd para el cliente
│   ├── comandos.json               # Configuración: teclas, programas, verbos y frases
│   ├── voz_asistente.py            # TTS (edge-tts) y consultas DeepSeek
│   ├── indicador_grabacion.py      # Ventana flotante "REC" (Tkinter)
│   ├── marcas.py                   # Verbos y frases de comando (configurables)
│   ├── sonidos.py                  # Efectos de sonido del cliente
│   ├── protocolo.py                # Tipos de mensaje ZMQ (copia compartida con el servidor)
│   ├── utils_logging.py            # Utilidades de logging (copia compartida con el servidor)
│   ├── requirements.txt            # Dependencias del cliente (Linux)
│   ├── .env.example                # Plantilla de variables de entorno
│   ├── empaquetado/                # Compilación con Nuitka + instalador del paquete
│   └── logs/                       # Registros antiguos (hoy van a ~/.local/state/skipkey)
│       └── historial/              # Logs rotados
├── cliente_windows/                # Cliente para Windows (carpeta para copiar a esa PC)
├── cliente_android/                # Teclado (IME) de Android en Kotlin (ver su README)
├── puente_web/                     # Puente HTTPS/WSS → servidor de dictado (ver su README)
├── AGENTS.md                       # Este archivo
├── README.md                       # Portada del repositorio (instalación y uso)
├── LICENSE                         # Licencia MIT
├── .gitignore                      # Exclusiones de Git (venv, modelos, logs, .env)
├── .gitattributes                  # Normalización de saltos de línea
├── entorno_dictado/                # Entorno virtual del cliente Ubuntu (excluido de Git)
├── entorno_web/                    # Entorno virtual del puente web (excluido de Git)
├── vosk-model-es/                  # Modelos Vosk antiguos (2,3 GB, ya sin uso; fuera de Git)
├── scratch/
│   └── detectar_tecla.py           # Diagnóstico de teclas
└── resguardos/                     # Archivos retirados (dictado.py, dictado-voz.service)
```

> **Cada cliente lleva su copia de los módulos compartidos** (`protocolo.py`, `utils_logging.py`,
> `sonidos.py`, `marcas.py`, `voz_asistente.py`, `indicador_grabacion.py`): las carpetas se
> copian enteras a otra PC. Si cambias uno, **cópialo también a `cliente_windows/`** (y al
> servidor, en el caso de `protocolo.py` y `utils_logging.py`).
> Los entornos virtuales (`entorno_dictado/`, `entorno_web/`) se quedan en la raíz porque
> mover un venv rompe sus rutas internas.

> **El servidor de transcripción ya no vive en este repositorio.** Está en su propio
> repositorio y carpeta: `/home/ivan/Nextcloud/Proyectos/servidor_dictado`
> (`github.com/ivanuxs/servidor-voz`), donde es un **servicio de sistema** que usa
> faster-whisper `large-v3` en la GPU: los parciales del modo vivo se decodifican en voraz
> (beam 1, temperatura 0, sin timestamps) y el `TEXTO` final con beam 5, fallback de
> temperaturas y timestamps. Este repositorio **no lo modifica**: solo se
> comunica con él (el cliente de Linux/Windows por ZMQ y el puente web por ZMQ local).
> El modelo se elige en el servidor, así que el cambio alcanza a **todos** los clientes.

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

### Cuentas y planes (2026-11-01)

La app va a **Google Play**, así que el registro es **abierto**: cualquiera que la
descargue se crea su cuenta desde el teclado. Sin cuenta se dicta igual, con el motor
del celular (gratis); **el servidor y las funciones avanzadas son de pago**.

- Base de datos: `puente_web/datos/usuarios.db` (SQLite, **excluida de Git**). Contraseñas
  con `hashlib.scrypt`; tokens y códigos guardados **con hash**, nunca en claro.
- Módulos: `usuarios.py`, `planes.py` (única autoridad de los cupos), `correo.py`,
  `rutas_cuentas.py`.
- Rutas: `POST /api/v1/registro|verificar|reenviar|login|salir|contrasena`,
  `GET /api/v1/yo`, `GET /api/v1/planes` (público), `DELETE /api/v1/cuenta`.
- `auth.py` acepta **dos** credenciales: el token maestro `DICTADO_WEB_TOKEN` (clientes de
  escritorio y scripts; sin plan ni cupos) **o** el token de una cuenta. Por eso
  `prueba_puente.py` y los clientes de PC siguen funcionando.
- Planes (tres): Gratis (motor del celular: no usa nuestro servidor ni gasta GPU) ·
  Básico (30 min y 50 traducciones al día, 1
  dispositivo) · Pro
  (dictado en vivo y 3 dispositivos). **Pro no tiene tope diario** (2026-11-18):
  `minutos_dia` y `traducciones_dia` son `None` (ilimitado, sale como `null` en el JSON).
  Lo que sigue acotando el abuso es la cola (una transcripción y una traducción a la vez),
  los 60 s por petición y los 3 dispositivos. Uso por día en UTC;
  **429** con el motivo al agotarse.
- Sin SMTP el registro funciona **sin verificar** el correo (modo pruebas) y el puente lo
  avisa al arrancar. Con SMTP (Gmail con contraseña de aplicación o uno propio), el código
  de 6 dígitos es obligatorio.
- nginx: `/api/v1/(registro|verificar|reenviar|login)` tienen zona propia
  `dictado_cuentas` (6 r/m, ráfaga 5): son las únicas rutas sin token.
- Herramientas: `puente_web/herramientas/{crear_usuario,cambiar_plan,listar_usuarios}.py`.
- ⚠️ Para el APK de Play: compilar **sin** `dictadoToken`/`dictadoClave` en
  `local.properties`; si van, el token maestro viaja dentro del APK.
- **APK para otro usuario (2026-11-04):**
  `bash cliente_android/construir_apk.sh assembleDebug -PsinToken` deja
  `dictado-sin-token.apk` (dirección y clave del proxy dentro, **token maestro
  fuera**). Con el token dentro la app arranca con la sesión maestra y «Cuenta»
  no ofrece el registro. La cuenta la crea el propio usuario en el teclado.
- **Regla de Google Play (pedido 2026-11-04):** al publicar en Play, una cuenta
  nueva empieza en **Básico** (`DICTADO_PLAN_POR_DEFECTO=basico` en
  `~/.config/puente-web.env`; hoy vale `gratis`). Mientras la app no esté en
  Play, las cuentas de prueba se suben a Pro a mano con
  `herramientas/cambiar_plan.py`.
- **La identidad es el nombre de usuario**, no el correo (2026-11-18): 3–24 caracteres
  (`[a-z0-9._-]`, sin tilde), único y en minúsculas; el correo es **opcional** y solo sirve
  para confirmar y recuperar. `/login` acepta el correo si el texto trae `@` (cuentas
  viejas). La base se migra sola al arrancar conservando `id`, cuentas y tokens.
- **Perfil** (`GET /api/v1/perfil`, `PUT /api/v1/perfil/ajustes`,
  `GET|POST /api/v1/perfil/diccionario` y `…/sustituciones`): cuelga de la **cuenta**, no
  del aparato. Reglas de fusión: `veces` es el **máximo** (no la suma), `conocida` solo
  avanza, `olvidada` lo decide la fecha más reciente y la fila no se borra al olvidar.
  Sincronizar exige plan Básico o Pro; los ajustes se guardan con cualquier plan.
  **El token maestro no tiene perfil (403).**
- **Revisión y conflictos (2026-11-18)**: los ajustes se guardan con `revision`; si no
  coincide se responde `"status": "conflicto"` y el cliente decide, nunca se pisa en silencio.
- **Migración de la base (2026-11-18)**: `usuarios.iniciar()` detecta el esquema viejo
  (`email NOT NULL UNIQUE`) y lo cambia por el de usuario sin perder cuentas ni tokens. Se
  hace copiando a `usuarios_nuevo`, `DROP` de la vieja y `RENAME`, con `foreign_keys=OFF` y
  **sin renombrar la tabla vieja** (renombrarla haría que SQLite reescribiera las claves
  ajenas de `tokens`, `uso` y `verificaciones`). Los `id` se conservan. Es idempotente.
- **Qué se comparte**: el diccionario y las correcciones viajan entre los aparatos de una
  misma cuenta; el endpoint valida el perfil contra la cuenta de la sesión. Lo único común a
  todos es el léxico base `assets/diccionario_es.txt`, de solo lectura.
- **Permiso vs. preferencia**: el plan decide qué *puede* usar la cuenta (`planes.exigir`);
  los ajustes del perfil deciden qué *tiene* activado.
- **Cuenta en el escritorio (2026-11-18).** Ubuntu y Windows **ya no dictan sin cuenta**.
  - Módulos nuevos, copiados en las dos carpetas: `sesion.py` (token en
    `~/.config/skipkey/sesion.json`, permisos **600**; la contraseña no se guarda),
    `perfil.py` (HTTP al puente con `urllib`, sin dependencias nuevas) y
    `diccionario_desktop.py` (lo aprendido en `dictado_<perfil>_aprendido.json`).
  - Configuración: `"puente": "https://…"` y `"puente_clave": "…"` en `comandos.json`
    (es el **puente web**, no el servidor de dictado). Se editan en la pestaña **Cuenta**
    de la ventana, que también tiene Entrar / Crear cuenta / Cerrar sesión / Sincronizar.
  - El diccionario **local manda**: si el puente no responde se dicta y se aprende igual;
    una hebra sincroniza cada 15 minutos y nunca toca el camino del dictado.
  - Las correcciones se aplican al texto dictado (`se ace` → `seace`), también las de
    varias palabras, conservando la mayúscula.
  - ⚠️ El cliente de Windows es una copia **más vieja** que el de Ubuntu (sin señales, con
    `alternar_dictado` y `_directorio_base()` para el JSON). Se le añadió lo mismo, pero
    los dos archivos **no** son iguales: no copies uno sobre otro.
- Pendiente: entrega 3 (Google Play Billing: Google exige Billing para vender dentro de la
  app) y que el teclado de Android haga su primera sincronización real contra el puente.

### Cliente Android (`cliente_android/`)

Teclado del sistema (IME) en Kotlin, `minSdk 26`, sin frameworks pesados: captura audio,
lo manda al puente y escribe el texto. **No hay lógica de transcripción aquí.**

- Compilar: `bash cliente_android/construir_apk.sh` (APK en `app/build/outputs/apk/debug/`).
- Las herramientas se instalan sin sudo con `cliente_android/herramientas/instalar_sdk.sh`
  (JDK 17 + SDK 34 + Gradle 8.7 en `~/Android`).
- **Java 25 no sirve para el plugin de Android**: hay que usar el JDK 17.
- **Dictado**: en el modo normal (por defecto) el audio se manda por WebSocket
  **mientras hablas** y el texto final llega al terminar; si esa conexión falla o no
  confirma, el búfer completo se reenvía por REST. El **modo en vivo** (parciales por
  frase) sigue existiendo pero es opcional y está apagado: el texto provisional se
  sustituye al final y no aporta velocidad.
- Las credenciales (dirección, token y clave) van **dentro del APK**, leídas de
  `cliente_android/local.properties` (no versionado) como `BuildConfig`. La app no
  obliga a escribir nada en el celular. También acepta configurarse por enlace:
  `dictadoime://config?url=...&token=...&clave=...`.
- El teclado es **una sola pantalla** (desde 2026-10-31): arriba la **banda de dictado**
  (`vista_dictado.xml`: línea de estado + botones de **ajustes**, **traducir**,
  **portapapeles** y **micrófono**) y debajo la barra de sugerencias y el **teclado de letras
  propio** (`TecladoLetras.kt`, construido en código: página de letras y de símbolos, ⇧/⇪ y ⌫ con repetición). No hay modos que se
  sustituyan ni botón ⌨: se dicta y se escribe a la vez, así que **no hace falta salir a
  Gboard**; mantener pulsado `?123` abre el selector del sistema para quien lo quiera igualmente.
  Por eso el alto de tecla por defecto bajó a 42 dp.
- ⚠️ **La barra de navegación se dibuja ENCIMA de la ventana del teclado** (medido en el
  Xiaomi con Android 16): sin aplicar el inset `navigationBars` al root del IME, la última
  fila (espacio y ↵) queda cortada. **Y los insets llegan a CERO al IME** aunque la ventana
  esté debajo de la barra: hay que poner la ventana en *edge-to-edge* con
  `WindowCompat.setDecorFitsSystemWindows(window.window, false)` (ojo: `getWindow()` devuelve
  un `Dialog`, no una `Window`). Desde ahí el inset llega (153 px) y el relleno inferior lo
  aplica `ViewCompat.setOnApplyWindowInsetsListener`. Y la ventana del IME **no crece a
  voluntad** (317–370 dp en ese celular), así que `encajarEnElHueco()` +
  `TecladoLetras.encoger()` bajan las filas si no caben. Se comprueba con
  `adb logcat -s DictadoIME` (imprime alto, inset, total y exceso).
- Arranque y uso: `cliente_android/README_android.md`.
- **Traducción** (2026-11-01): el botón 🌐 de la banda traduce el texto del campo (la
  *selección* o, si no hay, la línea del cursor) con `POST /api/v1/traducir` del **puente**.
  Un toque traduce; la pulsación larga cicla el idioma destino (en → pt → it → zh → es),
  guardado en `Ajustes.idiomaTraduccion`. El puente usa Opus-MT con `transformers` + `torch`
  **en CPU** (la conversión a CTranslate2 repetía frases) y carga los modelos bajo demanda
  con precalentado al arrancar.
- **Diccionario** (2026-10-31): una palabra tecleada se aprende **a la primera**
  (`VECES_PARA_CONOCIDA = 1`); el botón **＋/✓** de la barra la guarda o la quita a mano
  (antes era un dedo largo invisible); la autocorrección **no toca** las palabras cuya
  mayúscula la puso el usuario (nombres propios). El léxico peruano vive en
  `herramientas/generar_diccionario.py`; tras cambiarlo hay que regenerar
  `assets/diccionario_es.txt` con ese script.
- **Correcciones del dictado y perfiles** (2026-11-01): al reemplazar una palabra dictada
  (seleccionarla y escribir o dictar otra) se guarda la pareja `antes → después` en
  `dictado_<perfil>_sustituciones.txt`; se ofrece en la barra y se aplica sola al texto
  dictado **desde la primera corrección** (`VECES_PARA_SUSTITUIR = 1`: con 2, el dictado
  siguiente volvía a salir mal). Deshacer esa corrección es la casilla **«↩»** de la barra.
  El diccionario es **por perfil** (`Ajustes.perfil`, archivos `dictado_<perfil>_*.txt`);
  los archivos viejos sin perfil se migran al perfil `personal` la primera vez.
- ⚠️ **El ⌫ borra, siempre** (2026-11-18). Antes, tras una autocorrección, el primer ⌫
  reponía la palabra entera, la aprendía y olvidaba la pareja: dependía de un estado
  invisible y no había forma de adivinarlo. No lo vuelvas a meter: **deshacer es la casilla
  `↩`** que pinta `BarraSugerencias` (primera celda, en color de acento, con las otras dos
  para sugerencias) y guardar/quitar una palabra es el botón **`＋/✓`** del final. La lista
  de lo guardado se ve y se borra en **«Mis palabras»** (`PalabrasActivity`, desde Ajustes).
- **Cuenta en el teclado (2026-11-18)**: `ClienteCuenta.kt` registra y entra con
  **nombre de usuario** (correo opcional, solo para recuperar) y guarda el `id_perfil` y la
  `revision` que devuelve el puente. `CuentaActivity` tiene campo de usuario, correo
  opcional y botón **Sincronizar diccionario y ajustes**.
- **Sincronización** (`ClientePerfil.kt`): al entrar, al abrir el teclado (como mucho cada
  15 min) y al cerrarse, baja → fusiona → sube. `Diccionario.kt` exporta e importa en el
  formato del puente y guarda lo olvidado en `dictado_<perfil>_olvidadas*.txt` para que el
  olvido no vuelva desde otro aparato. Los ajustes solo se suben cuando el usuario pulsa el
  botón. **Al cerrar sesión se borra lo aprendido en el celular** (lo que está en la cuenta
  sigue en el puente).
- **Órdenes por voz: ya no viven aquí (2026-11-14).** El teclado se queda con lo suyo
  —dictado, diccionario, traducción y portapapeles—. La palabra clave, el intérprete de órdenes,
  el catálogo de acciones, los pitidos, la hora y el día en voz y el dictado sin campo **se
  mudaron al proyecto hermano `Asistente por voz`** (`android/`, paquete
  `com.hlquant.driveassistant`): allí un servicio en primer plano con Vosk oye «computadora» y la
  frase se ejecuta con el reconocedor del celular. Por eso aquí ya no están `EscuchaClave`,
  `DictadoSinCampo`, `Marcas`, `Acciones`, `Sonidos`, `AsistenteLocal`, `Voz`, `FinPorSilencio`
  ni `ReceptorArranque`, y el APK dejó de llevar el modelo Vosk (38 MB) y las dependencias
  `vosk-android`/`jna`. Consecuencia: la palabra clave ya no abre dictados; el teclado dicta con
  el botón del micrófono y el campo a la vista.
⚠️ **El arranque del dictado se comía lo primero que decías** (2026-11-06, lo reportó el usuario:
«aprieto el botón y si hablo al instante no lo detecta, lo corta y transcribe otra cosa»). El fallo era la lectura del micrófono: `empezarNormal()` y `empezarVivo()` hacían
`esperarListo(15 s)` **antes** del bucle que lee el micrófono. El `AudioRecord` ya estaba grabando,
pero nadie lo leía: su búfer (unos 800 ms) se desbordaba, lo hablado se descartaba y al empezar a
leer llegaba audio viejo. Ahora la conexión se vigila **en paralelo** y el micrófono se lee desde
el primer bloque; lo que se habla mientras conecta se acumula y se manda en cuanto llega el `listo`
(un solo candado sobre el búfer decide si un bloque se manda ya o se guarda, así no se pierde ni
se duplica).

El dictado dejó además trazas con tiempo (`adb logcat -s DictadoIME`): `micro listo a los N ms`,
`puente listo a los N ms` y `primer bloque a los N ms`, que es como se mide cuánto se pierde.

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

### Protocolo con el servidor (Android)

- El servidor solo **transcribe**: el `TEXTO` llega **limpio**, sin marcas (`§ ¬ ¶`). Antes
  mandaba `computadora§abre¬Firefox¶` y el cliente las interpretaba; eso **ya no existe**.
- El puente reenvía el audio y devuelve el texto. La lógica de diccionario, autocorrección,
  correcciones del dictado y sugerencias vive en el teclado (`Diccionario.kt`, `Marcas.kt`).
- El modo vivo (parciales por frase) existe en el servidor y el puente puede pedirlo, pero el
  teclado lo tiene **apagado**: el texto provisional se sustituye al final y no aporta velocidad.

### Dictado por voz
- La transcripción se hace en el servidor (Whisper). La **palabra clave la detecta el cliente**
  con su propio Vosk (ver la sección siguiente): el servidor ya no escucha ni carga modelo de
  reconocimiento.
- El texto transcrito se inyecta en la aplicación activa mediante portapapeles (`pyperclip` + `pyautogui`).
- Idioma: español (`es`).

### Palabra clave y modo comando (cliente Ubuntu)

**El servidor no escucha la palabra clave ni sabe de comandos**: la clave la detecta cada cliente
con su propio motor (`cliente_ubuntu/escucha_local.py`) y todo lo que se decide después se decide
en el cliente.

⚙️ **Motor: faster-whisper `base` desde el 2026-11-05** (antes Vosk `small-es-0.42`, guardado en
`resguardos/escucha_local_vosk.py`). No fue una preferencia: sobre 6 audios reales, con CPU,
`int8` y 4 hilos, el WER fue **103 % con Vosk small** (además de fallar, inventaba palabras),
**48 % con faster-whisper `base`** y 30 % con `small`; y `small` tarda 1,26 s por frase de 2 s,
así que se eligió `base` (~142 MB de modelo, ~400 MB de RAM, 0,74 s por frase de 2 s). Los
fallos de Vosk eran exactamente los que se veían en el registro: «iniciar dictado» llegaba como
«iniciar». El modelo se puede cambiar con `DICTADO_MODELO_WHISPER`.

- El detector **acumula** los chunks y transcribe la frase cuando hay voz y luego
  `SILENCIO_CIERRA_SEGUNDOS` (0,6 s) de silencio; no es streaming como Vosk. La interfaz
  (`alimentar()` → `clave`/`fin`/`comando`/`frase`/`None`) es la misma, así que el resto del
  cliente no cambió.

- La escucha **reutiliza los chunks que el cliente ya manda al servidor**: no se abre un segundo
  lector del micrófono (la trampa que ya se pagó en Android).
- **La clave abre el MODO COMANDO, no un dictado** (rediseño del 2026-11-05). Tras el pitido el
  cliente espera una orden con su argumento, y para dictar texto hay que pedirlo con
  **«iniciar dictado»** (variantes en `FRASES` de `marcas.py`: `empezar el dictado`, `modo
  dictado`, `dictado`). Dentro de un dictado **por tecla** Vosk no se alimenta.
- **La frase se cierra sola con 2 s de silencio** (`ORDEN_SILENCIO_SEGUNDOS`): al dar una orden
  el usuario dice el verbo y el argumento y **se calla**, esperando que se ejecute. No hace falta
  decir «fin del dictado» para un comando.
- Qué se hace con lo oído (`_resolver_comando`):
  - **frase de sistema** o **argumento en el catálogo** («abre calculadora», «sube volumen») →
    se ejecuta aquí, suena `orden` y al servidor **no se le manda nada**, ni el audio.
  - **1-2 palabras que el catálogo no conoce** («abre cromo») → tras los 2 s de silencio se manda
    el audio a Whisper para que corrija el argumento; el cliente reparsea la respuesta.
  - **saludos y la hora** («hola», «qué hora es») → los contesta `voz_asistente` en local, sin
    gastar API.
  - **3 o más palabras** («abre cromo azul y lo pinto de verde», o cualquier pregunta) → **es una
    consulta al asistente**: se transcribe con Whisper y la responde DeepSeek
    (`voz_asistente.preguntar_deepseek`), que se **habla** con edge-tts. No se escribe nada en el
    documento.
  ⚠️ El orden importa: el audio guardado (`_buffer_voz`) se suelta **después** del `DICTADO_START`;
  si llegara antes, el servidor lo descartaría. Por eso hay dos banderas (`_start_encolado` y
  `_dictado_enviado`) y el audio sigue guardándose hasta que el evento se procesa.
- ⚠️ **La clave y la orden pueden venir en la MISMA frase de Vosk** («computadora iniciar
  dictado»): antes esa rama devolvía `"clave"` y **tiraba el resto**, así que el dictado no se
  abría y lo dicho acababa en DeepSeek (visto el 2026-11-05). Ahora `EscuchaLocal` guarda
  `resto_tras_clave` y `_alimentar_escucha()` lo resuelve en el acto. Se lee **antes** de abrir la
  sesión, porque abrirla reinicia el reconocedor y borra ese resto.
- ⚠️ **Red de seguridad en la consulta**: `_responder_consulta()` prueba primero `_orden_local()`
  con el texto que devolvió Whisper. Así, un «Iniciar dictado. Probando 1, 2, 3.» que se coló por
  el camino de consulta abre el dictado en vez de preguntárselo a la IA.
- ⚠️ **El modelo pequeño parte «iniciar dictado»** y a veces solo entrega «iniciar» (medido el
  2026-11-05 con un pico de 8869: oyó bien, transcribió a medias). Por eso `iniciar`, `inicia`,
  `empezar`, `empieza`, `comenzar` y `comienza` están en `FRASES` como `dictar`, pero en
  `_FRASES_EXACTAS`: solo valen **solos**, para que «iniciar sesión» o «empieza a llover» no
  abran un dictado. Y `PICO_MINIMO` (900) ya no descarta segmentos de dos o más palabras, que era
  lo que hacía desaparecer las órdenes dichas en voz baja.
- ⚠️ **«iniciar dictado» se lo tragaba el recorte del cierre** (2026-11-05, y costó media sesión):
  `quitar_frase_cierre()` recortaba toda frase acabada en «dictado» aunque delante no hubiera
  verbo de cierre, así que «Iniciar dictado.» quedaba en «Iniciar», `_es_cierre()` decía que sí y
  `_clasificar()` devolvía **`fin`**. Estando en modo comando esa rama se ignoraba en silencio:
  el usuario decía «iniciar dictado» y **no pasaba nada, sin una sola línea en el registro**
  (la rama `fin` tampoco fijaba `ultimo_texto`, así que la traza `[VOSK]` no se imprimía).
  Ahora el recorte **exige un verbo de cierre** («fin / termina / finalización / …»), «dictado»
  pelado sigue cerrando, y tanto `ultimo_texto` como el aviso de cierre fuera de dictado dejan
  rastro. «modo dictado» y «empieza el dictado» tenían el mismo fallo.
- Seguros del modo comando: **60 s** sin oír nada (`MODO_COMANDO_MAX_SEGUNDOS`) → se cierra con
  pitido `error` y **no se transcribe nada**; no hay ventana de 8 s ni dictado implícito.
  Con los comandos **en pausa** desde el icono, la clave abre un dictado normal (vía rápida).
- El **dictado** (tras «iniciar dictado») se cierra diciendo «fin del dictado». Una pausa al
  hablar no lo corta: quedan **6 s sin haber dicho nada** desde el pitido y un **tope de 180 s**.
- Sonidos, todos generados por código en `sonidos.py`: **`inicio`** (ding ascendente) al abrir el
  modo comando y al pasar a dictado; **`fin`** al oír la frase de cierre; **`orden`** (dos pips
  dobles) cuando la orden se ejecuta; **`texto`** (pip corto y agudo) cuando el texto está en el
  portapapeles o cuando el asistente va a hablar; **`error`** en los avisos.
- **El pitido de una orden sale cuando ya se sabe el resultado**, no al oírla: en el camino de
  «abre cromo» hay que esperar al servidor, así que suena `orden` si la orden devuelta se ejecuta
  y `error` si no (`_ejecutar_y_avisar`). Al cerrar por silencio **no** suena `fin`. Si el
  servidor devuelve algo que no es una orden ejecutable, no se pega texto en el documento: solo
  el pitido de error y la razón en el registro (`avisar=False` en `ejecutar_comando`).
- Configuración en `comandos.json`: `modo_escucha` (encendido/apagado), `clave` y `frase_fin`.
  El texto de un dictado de voz va al **portapapeles**, igual que el de tecla.
- Si falta `vosk` o el modelo, el cliente lo avisa en el log y sigue funcionando con la tecla
  activadora: la escucha local nunca es un error fatal.

### Icono en la barra superior (cliente Ubuntu)
`cliente_ubuntu/bandeja_ubuntu.py` pone un **AppIndicator** con: escucha continua, comandos por
voz, dictar ahora, **Configuración…**, ver el registro y salir. Es el equivalente de
`cliente_windows/bandeja.py`; en GNOME el icono necesita la extensión
`ubuntu-appindicators@ubuntu.com` (viene activa en Ubuntu).

- Se ejecuta con el **Python del sistema** (`/usr/bin/python3`) porque necesita PyGObject
  (`gi`), que no está en `entorno_dictado`. `cliente_voz.py` lo lanza y lo vigila como proceso
  hijo (`_bucle_bandeja`), igual que hace con `mpris_dictado.py`.
- **No hay IPC propio**: el menú manda señales (`senal_cliente.py`: `SIGUSR1` dictado,
  `SIGRTMIN+1` escucha, `SIGRTMIN+2` comandos, `SIGRTMIN+3` recargar configuración) y el
  cliente escribe
  `~/.cache/dictado-voz/estado.json`, que el icono relee cada segundo para el color y las
  casillas del menú.
- Colores: gris (escucha apagada), verde (esperando la palabra clave), ámbar (comandos en
  pausa), rojo (grabando).
- Se apaga con `"bandeja": false` en `comandos.json`.

⚠️ **GTK3 estaba roto en este equipo (2026-11-04)** y sin arreglarlo no hay icono posible:
`gsettings-desktop-schemas` 50.0 movió `antialiasing`, `hinting` y `rgba-order` a un esquema
`.deprecated` y GTK3 aborta con `GLib-GIO-ERROR ... does not contain a key named 'antialiasing'`
(la misma razón por la que Inkscape, que es snap, no arrancaba). Arreglo: el esquema redefinido en
`~/.local/share/glib-2.0/schemas/org.gnome.settings-daemon.plugins.xsettings.gschema.xml`
más una copia de `org.gnome.settings-daemon.enums.xml`, y `glib-compile-schemas`. Revertir =
borrar esos dos archivos y recompilar.

⚠️ **Al lanzar el icono hay que limpiar el entorno.** Un VS Code instalado como snap deja
`GDK_PIXBUF_MODULE_FILE`, `GIO_MODULE_DIR` o `XDG_DATA_DIRS` apuntando a `/snap/...`, y
`/usr/bin/python3` revienta al cargar GTK (`symbol lookup error ... libpthread.so.0`). Lo hace
`_entorno_sistema()` en `cliente_voz.py`; `DISPLAY` y `WAYLAND_DISPLAY` se conservan.

### Ventana de configuración y catálogo de skills (2026-11-05)

`cliente_ubuntu/ventana_configuracion.py` (Tkinter/ttk, sin dependencias nuevas: Tk 8.6 está en
el venv y en `/usr/bin/python3`) se abre desde **Configuración…** del menú del icono, y en
Windows desde el mismo sitio (`--configuracion` del `.exe`). Lee y escribe `comandos.json`.

- **Palabras**: la clave, las frases de cierre y las listas de **verbos** y **frases de comando**.
  Esas dos listas ya **no viven en el código**: se guardan en `comandos.json` (`"verbos"` y
  `"frases"`) y `marcas.aplicar_configuracion()` las aplica al arrancar y al recargar. Los
  valores de `VERBOS_POR_DEFECTO` / `FRASES_POR_DEFECTO` son el respaldo si la clave no existe o
  el JSON está roto, así que un JSON malo no puede dejar al cliente sin comandos.
- **Skills**: catálogo de lo que sabe hacer el cliente (`skills.py` + `marcas.VERBOS/FRASES` +
  catálogo de programas). Se arma leyendo esas fuentes, no duplicándolas: añadir una frase la
  hace aparecer sola. Cada fila trae un botón **Probar** que ejecuta la acción sin voz
  (`cliente_voz.ejecutar_comando(..., avisar=False)`); las de «apagar» y «reiniciar» no se
  prueban (deshabilitadas por seguridad) y «dictar»/«consulta» necesitan el micrófono.
- **Programas**: el catálogo `nombre → ruta` de `abre` y `cerrar`, con «Examinar…».
- **Web**: el catálogo `nombre → URL` de las páginas («seace», «youtube»…), con «Probar» para
  abrir una y ver que va. Solo acepta direcciones `http`/`https`.
- **Voz y activación**: teclas, toggle/mantener, salida, dispositivo de entrada, casillas de
  escucha/comandos/bandeja/auricular y `servidor` (solo si ya existe en el JSON).
- **Aplicar sin reiniciar**: al guardar, la ventana manda `SIGRTMIN+3`
  (`senal_cliente.enviar_recargar()`) y el cliente relee (`_recargar_configuracion`). En Windows
  no hay señales: el cliente **vigila la fecha del JSON** (`_vigilar_configuracion`) y recarga
  igual. Las teclas y el dispositivo de audio sí necesitan reiniciar.
- ⚠️ La bandeja corre con `/usr/bin/python3`, pero la ventana tiene que abrirse con el **Python
  del venv** (importa `cliente_voz` para «Probar»): `cliente_voz` le pasa `--python
  sys.executable` a la bandeja y esta lo usa en `_abrir_configuracion()`.

### Comandos de voz
**El cliente lo decide todo; el servidor solo transcribe.** Antes el servidor
mandaba el texto con marcas (`computadora§abre¬Firefox¶`) y el cliente las
interpretaba. Eso **ya no existe**: el TEXTO llega **limpio**, sin `§ ¬ ¶`, y
`servidor_dictado` no tiene catálogo de verbos ni de frases (el viejo
`comandos.py` está en `resguardos/` y `normalizar()` vive ahora en `texto.py`).
El servidor sirve para dos cosas: dictar y devolver corregido el argumento de
una orden cuando el cliente se lo pide.

- **`marcas.py`** (y su copia `cliente_windows/marcas.py`) es el intérprete, y
  sus listas (`VERBOS`, `FRASES`) son **del cliente**:
  - `parsear(texto)` → `(accion, argumento)` o `(None, "")`;
  - `resolver(accion, argumento)` → acción local, o `None` si no se reconoce;
  - `es_comando(texto)` → «¿esto empieza por una orden?».
  Para añadir una acción se toca `marcas.py` **y** la tabla de
  `ejecutar_comando()` en `cliente_voz.py`.
- `_manejar_mensaje()` trata el TEXTO como texto y lo entrega, **salvo** cuando
  venía de una orden pendiente (`_esperando_orden`): entonces pasa por
  `_orden_corregida()`, que reinterpreta el texto ya corregido por Whisper.
- Acciones soportadas en el escritorio: `abre`/`cerrar` programas (con el
  catálogo de `comandos.json`), volumen (+10 %, −10 %, silencio), multimedia,
  captura de pantalla, bloquear pantalla. Apagar y reiniciar están
  deshabilitados por seguridad.
- **`voz_asistente.py`** se usa ahora de verdad (antes estaba huérfano). En modo comando, lo que
  no es una orden es una **consulta**: primero `COMANDOS_SIMPLES` (la hora, el día, los saludos)
  y, si no hay coincidencia, `preguntar_deepseek()`. La respuesta se **habla** con edge-tts
  (`hablar()`), en su propio hilo y **sin escribir nada** en el documento. Necesita
  `DEEPSEEK_API_KEY` (en `~/.config/cliente-voz.env`), el paquete `edge-tts` y **`ffplay`**
  (`ffmpeg`) para reproducir el MP3.
- ⚠️ **`edge-tts` 7.2.8 moría con 403 y el fallo era invisible** (2026-11-05). El servicio de
  Microsoft rechaza el `Sec-MS-GEC` de esa versión (`WSServerHandshakeError 403 Invalid response
  status`), y `_hablar_async` lo tragaba con `except Exception: pass`: en el registro aparecía
  «Respuesta local a «qué hora es»» y no se oía nada. Arreglo: subir a **7.3.1** (fijado como
  `edge-tts>=7.3.1` en los requisitos) y `hablar()` ahora **registra** el error y devuelve `True`
  o `False`, con pitido `error` si falla. El reloj no era: `timedatectl` daba sincronizado al
  segundo con la red.
- Ejemplos (todos resueltos en el cliente):
  - *"computadora abre firefox"* → `parsear` → `("abre", "firefox")` → camino corto
  - *"computadora cierra la terminal"* → `("cerrar", "la terminal")`
  - *"computadora sube volumen"* → frase de sistema → `subir_volumen`
  - *"computadora abre cromo"* → `("abre", "cromo")`, no está en el catálogo →
    espera 2 s de silencio y lo manda al servidor a corregir el argumento.
  - *"computadora qué hora es"* → respuesta local, hablada, sin gastar API.
  - *"computadora cómo hago una tortilla"* → consulta: Whisper la transcribe y DeepSeek
    contesta, hablando.
  - *"computadora iniciar dictado … fin del dictado"* → dictado de texto normal.
- **Abrir páginas web y buscar** (2026-11-05). No es un programa: es el catálogo `"web"` de
  `comandos.json` (nombre → URL), y se resuelve en `_resolver_sitio()`:
  - *"ingresa a la página del seace"* → se quita el relleno («a», «la», «página», «del») y se
    busca «seace» en el catálogo;
  - *"abre youtube"* → `abre` no lo encuentra en los programas y cae aquí;
  - *"youtube punto com"* → la normalización se come el punto, así que se reconoce el final de
    dominio (`_EXTENSIONES`) y se abre `https://youtube.com`;
  - *"busca recetas de tortilla"* → `https://www.google.com/search?q=…` (`quote_plus`).
  Se abre con `webbrowser.open()`, el navegador **predeterminado**, y solo se aceptan `http` y
  `https` (`_abrir_sitio`): por voz no puede colarse un `file://`. Si Vosk oye mal el nombre
  («se ace»), la orden va al servidor y `_orden_corregida()` la vuelve a pasar por
  `_orden_local()`, que es la misma resolución.
- ⚠️ **Android esperaba las marcas** y desde el 2026-11-05 no las recibe: **resuelto el
  2026-11-06** con la detección local en `Marcas.kt` (ver «Puesta al día con el escritorio» en
  la sección del cliente Android).

### Cliente de Windows: escucha local y bandeja (2026-11-04)
- **`bandeja.py`** pone un icono en el área de notificación (`pystray` +
  `Pillow`) con: escucha continua, comandos por voz, dictar ahora, ver el
  registro y salir. El color dice el estado: gris apagado, verde escuchando,
  ámbar comandos en pausa, rojo grabando. Si el icono no se crea, avisa con
  cuadro de diálogo (`al_fallar=_avisar_usuario`), porque antes el fallo se
  quedaba solo en el registro y parecía que la función no existía. Se prueba
  aparte con `ClienteVoz.exe --probar-bandeja 30`.
- **`escucha_local.py`** (copia del de Ubuntu): la palabra clave la detecta **faster-whisper**
  `base`, alimentado con los mismos chunks que el cliente ya captura y manda al servidor — **no
  abre el micrófono**. El modelo se descarga a `~/.cache/huggingface` la primera vez (necesita
  internet una vez); también vale `DICTADO_MODELO_WHISPER` con una ruta local. Se prueba con
  `ClienteVoz.exe --probar-escucha 20`.
- ⚠️ **`escucha_windows.py` (SAPI 5) está retirado** en `resguardos/`. En la PC de Windows no hay
  **ningún** motor de reconocimiento instalado (`HKLM\SOFTWARE\Microsoft\Speech\Recognizers\Tokens`
  no existe): `CreateRecoContext` fallaba con acceso denegado en proceso y con `0x80045077` en el
  compartido. El diagnóstico por pasos y el listado de motores que se añadieron sirvieron para
  localizarlo; no se pierde nada retirándolo.
- El dictado de voz se abre con `crear_dictado_start_voz(fin=False)` y el servidor devuelve el
  **texto limpio**; la frase de cierre la quita el cliente (recorte tolerante).
- Igual que en Ubuntu, la clave abre el **modo comando** (`_al_detectar_clave(modo="comando")`):
  la orden se ejecuta **aquí** cuando el argumento está en `comandos_windows.json` («abre
  calculadora», «sube volumen»), suena `orden` y al servidor no se le manda nada. El audio de esos
  segundos se guarda en `_buffer_voz` y solo se suelta, tras el `DICTADO_START`, cuando hay que
  pedirle algo al servidor.
- Con 1-2 palabras de argumento que el catálogo no conoce, espera **2 s de silencio** antes de
  mandarla al servidor a corregir; si vuelve la voz, es una consulta. Con 3 o más palabras
  («abre cromo azul y lo pinto de verde») es una **consulta al asistente**: Whisper la transcribe
  y DeepSeek la responde por voz. El pitido (`orden`, `texto` o `error`) sale cuando el servidor
  contesta, no al oír la frase.
- Para dictar texto hay que decir **«iniciar dictado»** (`_abrir_dictado_desde_comando`): descarta
  el audio del comando y pide el `DICTADO_START`. El dictado se cierra con una de las frases
  configuradas, con **6 s sin voz** o con el tope de 180 s.
- Seguros del modo comando: **15 s** sin oír nada → se cierra con pitido `error` y no se transcribe
  nada. Con los comandos **en pausa** desde el icono, la clave abre un dictado normal.
- En la PC la escucha local viene **apagada** (`"escucha_local": false`): sin
  ella, la clave la sigue detectando el Vosk del servidor, como siempre.- Windows no publica ningún evento de «otro programa tomó el micrófono»: la
  escucha se pausa a mano desde el icono, y se libera al dictar.

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

### Empaquetado para otras máquinas (2026-11-18)

Del cliente Linux sale un **paquete con un binario compilado** (no se reparte el código), en
dos formatos desde el mismo binario: `skipkey_<versión>_amd64.deb` y
`skipkey-<versión>-x86_64.AppImage`. Se arman con
`bash cliente_ubuntu/empaquetado/construir_binario.sh` (Docker + Nuitka) y todo vive en
`cliente_ubuntu/empaquetado/`:

| Archivo | Para qué |
|---|---|
| `Dockerfile.compilacion` | Imagen `ubuntu:22.04` con Python 3.10, `python3-tk`, `python3-gi`, `python3-dbus`, compilador, `patchelf` y `appimagetool`. |
| `construir_binario.sh` | Lo lanza el usuario: crea la imagen y ejecuta la compilación en el contenedor. |
| `compilar_en_contenedor.sh` | Copia las fuentes a `/tmp`, crea el venv, compila con Nuitka y arma el `.deb` y el `.AppImage`. |
| `skipkey.service` | Unidad de usuario para `/usr/lib/systemd/user/`; `%U` en `PULSE_RUNTIME_PATH` (no `1000` fijo) y sin `DISPLAY`. |
| `skipkey.desktop` / `skipkey-autostart.desktop` | Entrada del menú y arranque al iniciar sesión (un `.deb` no puede activar un servicio de usuario desde `postinst`). |
| `icono.png` | Icono 256×256, el mismo dibujo del indicador. |

- **Nuitka `--standalone`** (no PyInstaller): compila a C y deja el código fuera de alcance.
  `--onefile` **no** se usa: el cliente reinicia y relanza hijos a menudo, y onefile se
  descomprime en cada arranque.
- **Se compila en `ubuntu:22.04`** a propósito: el binario queda atado a la glibc de la
  imagen. Compilado en 22.04 corre en 24.04 y 25.x; al revés no.
- El venv de compilación se crea con **`--system-site-packages`** para que `gi` (bandeja) y
  `dbus` (auricular) entren en el binario.
- ⚠️ **Un solo ejecutable**: los hijos que en modo fuente se lanzan como scripts ahora se
  lanzan con banderas internas del propio binario — `--bandeja`, `--mpris`, `--ventana`,
  atendidas en el `if __name__ == "__main__"` de `cliente_voz.py`. En modo fuente **no** se
  usan: se sigue lanzando `bandeja_ubuntu.py`/`mpris_dictado.py` con `/usr/bin/python3`.
  Si se añade otro proceso hijo, tiene que seguir el mismo patrón o se rompe solo el paquete.
- `_congelado()` (en `cliente_voz.py`, `bandeja_ubuntu.py` y `ventana_configuracion.py`)
  reconoce PyInstaller (`sys.frozen`) y Nuitka (`__compiled__`). No usar
  `getattr(sys, "frozen", False)` suelto.
- ⚠️ **`--disable-plugin=gi` es obligatorio**: el plugin de PyGObject de Nuitka pide los
  typelibs de medio sistema (`Vte`, luego `Poppler`, …) y aborta con
  «Typelib file for namespace … not found». Con `--include-package=gi` y el plugin apagado
  el binario importa Gtk/AyatanaAppIndicator3 y funciona (probado el 2026-11-18).
- ⚠️ **Nuitka 4.x deja el ejecutable como `skipkey.bin`**, no `skipkey`. El script lo
  renombra porque el nombre es lo que usan la unidad systemd, el enlace `/usr/bin/skipkey`
  y `sys.executable` al relanzar los hijos.
- ⚠️ **`patchelf` 0.14 (el de Ubuntu 22.04) corrompe las librerías de numpy**: al
  reescribirles el RPATH quedan «ELF load command address/offset not page-aligned» y
  `import numpy` revienta al arrancar (`libscipy_openblas64_…`, luego `libgfortran…`).
  La imagen instala **patchelf 0.18 desde PyPI** en `/usr/local/bin` (va antes en el PATH,
  es la que usa Nuitka). Sin eso el paquete se compila pero no arranca.
- ⚠️ **`--include-package-data` choca con los `.so` de onnxruntime/ctranslate2**
  («data file … conflicts with dll»). Solo se pide para `faster_whisper` (su
  `silero_vad_v6.onnx`) y `huggingface_hub`.
- **Destino con `.deb`**: programa en `/opt/skipkey/`, configuración en
  `~/.config/skipkey/comandos.json` (en `/opt` no se puede escribir: es de root),
  registros en `~/.local/state/skipkey/logs/` y unidad en `/usr/lib/systemd/user/`.
  `senal_cliente.py` y el menú del icono prueban **`skipkey` y `cliente-voz`**, así que
  conviven la instalación nueva y la de siempre (y `senal_cliente` descarta los hijos por
  `--bandeja`/`--mpris`/`--ventana` al buscar por `pgrep`: se llaman igual que el proceso
  principal).
- **AppImage**: se arma con `appimagetool` (que es a su vez un AppImage: dentro del
  contenedor hay que ejecutarlo con `APPIMAGE_EXTRACT_AND_RUN=1 … --appimage-extract-and-run`)
  y el `AppRun` ejecuta el binario **en su sitio**, porque sus librerías se resuelven con
  `$ORIGIN`. No lleva GTK dentro: el host necesita `libgtk-3`, `libayatana-appindicator3`,
  `libdbus-1-3`, `ffmpeg` y `xclip`, y en Ubuntu 24.04 `libfuse2t64`.
- El `.deb` **no** puede meter `libgtk-3-0`, `libayatana-appindicator3-1`, `libdbus-1-3`,
  `gir1.2-*`, `ffmpeg`, `xclip`, `scrot` ni `xdotool`: van en `Depends:` y los instala
  `apt`. Tampoco el modelo de voz: faster-whisper `base` se descarga la primera vez que se
  usa la escucha. **Tamaño medido (2026-11-18): `.deb` 127 MB, `.AppImage` 166 MB, 570 MB
  instalado.** El binario de Nuitka son 260 MB de código nativo (LTO no lo reduce); el resto
  es `ctranslate2` 72 MB, `onnxruntime` 44 MB, `numpy`+OpenBLAS 40 MB y los códecs de PyAV
  (~60 MB).
- 🔒 El repositorio es público y la licencia MIT: el binario evita repartir la carpeta, no
  protege el código de verdad.

### Logging
- Toda la salida usa `imprimir()` de `utils_logging.py`.
- Los logs se guardan en `~/.local/state/skipkey/logs/registro_cliente_voz.log`
  (`directorio_logs()` de `utils_logging.py`, que respeta `XDG_STATE_HOME`). Antes
  estaban en `cliente_ubuntu/logs/`; **en Windows siguen junto al `.exe`**.
- No se imprime en consola; usar `tail -f` para monitorear.

### Logging del puente web
- Toda la salida del puente usa el mismo `imprimir()` de `puente_web/utils_logging.py`.
- Los logs se guardan en `puente_web/logs/registro_servidor_web.log`.

## Dependencias
- `pyzmq` (comunicación con el servidor)
- `numpy` (procesamiento de audio)
- `sounddevice` (captura de audio del micrófono)
- `pynput` (detección de teclas global)
- `pyautogui`, `pyperclip` (inyección de texto)
- `edge-tts` (texto a voz, en `voz_asistente.py`)
- `requests` (consultas a DeepSeek, en `voz_asistente.py`)
- `vosk` (detección local de la palabra clave, en `escucha_local.py`)
- `openai-whisper`, `torch`, `scipy` (transcripción — solo en el servidor)
- `python3-dbus` (paquete del **sistema**, no del entorno virtual): lo necesita
  `mpris_dictado.py` para el botón del auricular Bluetooth. Si falta, el cliente funciona
  igual: solo se pierde ese botón de auricular (queda avisado en el log).

Instaladas en el entorno virtual `entorno_dictado/`.

**Puente web** (`puente_web/requirements.txt`, instaladas en `entorno_web/`): `pyzmq` (cliente
ZMQ del servidor de dictado), `fastapi` + `uvicorn` (HTTP y WebSocket), `transformers` + `torch`
(traducción Opus-MT en CPU) y `requests`.

**Android** (`cliente_android/`): Kotlin con Gradle 8.7, JDK 17 y SDK 34. Detalle en
`cliente_android/README_android.md`.

## Ejecución

### Manual
```bash
# Servidor en IP_DEL_SERVIDOR:5555 (por defecto 127.0.0.1:5555):
"SkipKey/entorno_dictado/bin/python3" "SkipKey/cliente_ubuntu/cliente_voz.py"

# Con servidor remoto (explícito):
"SkipKey/entorno_dictado/bin/python3" "SkipKey/cliente_ubuntu/cliente_voz.py" --servidor IP_DEL_SERVIDOR:5555
```

### Como servicio systemd (usuario)
El servicio se ejecuta como usuario (no como root) porque necesita acceso a la pantalla, audio y teclado.

**Instalar:**
```bash
mkdir -p ~/.config/systemd/user
cp "SkipKey/cliente_ubuntu/cliente-voz.service" ~/.config/systemd/user/
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
tail -f ~/.local/state/skipkey/logs/registro_cliente_voz.log
```

### Puente web (servicio de usuario, sin sudo)
```bash
systemctl --user status puente-web    # Ver estado
systemctl --user restart puente-web   # Reiniciar (tras cambiar el .env)
journalctl --user -u puente-web -f    # Consola (avisos de uvicorn)
tail -f "SkipKey/puente_web/logs/registro_servidor_web.log"

# Comprobaciones con audio real
set -a; . ~/.config/puente-web.env; set +a
entorno_web/bin/python puente_web/prueba_puente.py                 # REST
entorno_web/bin/python puente_web/prueba_puente.py --ws --modo vivo # WebSocket
entorno_web/bin/python puente_web/prueba_puente.py --cancelar       # robustez
```

### Teclado Android
```bash
bash cliente_android/construir_apk.sh                          # APK con lo de local.properties
bash cliente_android/construir_apk.sh assembleDebug -PsinToken # APK sin token maestro
```

## Extensibilidad
Para añadir nuevos comandos de voz:
1. Editar `comandos.json` añadiendo entradas en `abrir` o `cerrar`.
2. Para nuevas acciones (volumen, brillo, etc.), extender `procesar_comando()` en `cliente_voz.py`.

Para una ruta nueva del puente: añadirla en `puente_web/servidor_web.py`, documentarla en
`puente_web/README.md` y, si queda **sin token**, darle su `location` en nginx dentro de la
zona `dictado_cuentas`, que es el único grupo sin autenticación.

## Repositorio Git
- El repositorio es **público**: no subir nunca claves, tokens ni datos personales.
- `DEEPSEEK_API_KEY` vive en `~/.config/cliente-voz.env` (fuera del repositorio), cargado con
  `EnvironmentFile=` desde `cliente-voz.service`. Está excluido por `.gitignore`.
- Excluidos por `.gitignore`: `entorno_dictado/`, `entorno_web/`, `vosk-model-es/`, `scratch/`,
  `resguardos/`, `logs/`, `cliente_ubuntu/logs/`, `cliente_ubuntu/empaquetado/{salida,.construccion}/`,
  `.qwen/`, `.env`, `cliente-voz.env`, `puente-web.env`, `puente_web/datos/` y los artefactos de
  compilación de PyInstaller, de Nuitka **y de Android**
  (`cliente_android/.gradle|build|local.properties`, `*.apk`, `*.deb`).
- Remotos: `origin` es `github.com/ivanuxs/dictado-por-voz` (público) y `privado` su espejo
  privado. La carpeta del proyecto se llama `SkipKey/` aunque el repositorio conserve el nombre
  `dictado-por-voz`.
- La dirección del servidor se resuelve en este orden: `--servidor IP:PUERTO` >
  `DICTADO_SERVIDOR` (variable de entorno) > campo `"servidor"` de `comandos.json` >
  `127.0.0.1:5555`.
- Antes de cada `git push`, comprobar con `git status` y `git ls-files` que no se cuele nada
  pesado ni ninguna credencial.
