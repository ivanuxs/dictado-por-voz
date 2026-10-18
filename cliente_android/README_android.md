# Teclado de dictado para Android (IME)

Teclado del sistema que dicta: capturas tu voz, la manda al **puente**
(`puente_web/`) y escribe el texto en el campo donde estés escribiendo
(WhatsApp, Telegram, notas, lo que sea). Está pensado para usarse **también con
datos móviles**, fuera de casa.

```
Teclado (Kotlin) ──HTTPS/WSS──▶ nginx ──▶ puente ──ZMQ──▶ servidor de dictado
```

Todo el trabajo de transcripción y correcciones vive en el servidor: aquí **no
hay modelo ni lógica de texto**, solo captura de audio, red y el teclado.

## Requisitos

- Android 8.0 (API 26) o superior.
- El puente publicado por HTTPS (ver `puente_web/README.md`).

## Compilar el APK

Las herramientas se instalan **sin sudo**, en `~/Android`:

```bash
bash cliente_android/herramientas/instalar_sdk.sh   # JDK 17 + SDK + Gradle (una vez)
bash cliente_android/construir_apk.sh               # compila el APK
```

El APK queda en `cliente_android/app/build/outputs/apk/debug/app-debug.apk`.

> **Java 25 no sirve** para el plugin de Android: el script usa el JDK 17
> instalado en `~/Android/jdk-17`. Si mueves las herramientas, define
> `ANDROID_HERRAMIENTAS=/ruta`.

## Configurar las credenciales (una sola vez, antes de compilar)

La app **no** pide escribir el token ni la clave: se meten **dentro del APK** al
compilarlo, leyéndolos de `cliente_android/local.properties`, archivo que **no se
versiona** (así no acaban en el repositorio).

```properties
sdk.dir=/home/tu-usuario/Android/Sdk
dictadoUrl=https://dictado.tu-dominio.com
dictadoToken=...   # en el portátil:  grep DICTADO_WEB_TOKEN ~/.config/puente-web.env
dictadoClave=...   # en el proxy:     sudo grep -oE '[0-9a-f]{64}' /etc/nginx/conf.d/dictado-http.conf
```

Si los dejas vacíos, la app arranca sin configurar y se rellenan en su pantalla
(útil si algún día quieres apuntarla a otro servidor).

> Ojo: `construir_apk.sh` **no** reescribe `local.properties`, solo añade la
> línea del SDK si falta. Si lo sobrescribiera, se llevarían por delante las
> credenciales y la app volvería a pedirlas a mano.

### Configurar otro celular sin recompilar

La app entiende un **enlace de configuración**:

```
dictadoime://config?url=https://dictado.tu-dominio.com&token=EL_TOKEN&clave=LA_CLAVE
```

Te lo mandas por WhatsApp, lo tocas y la app queda configurada sin escribir
nada. Se puede reenviar a otro celular cuando haga falta.

## Instalar y activar

```bash
bash cliente_android/construir_apk.sh
~/Android/Sdk/platform-tools/adb install -r cliente_android/app/build/outputs/apk/debug/app-debug.apk
# o copia el APK al celular y ábrelo (habilitando "instalar apps desconocidas")
```

Después:

1. Abre la app **Dictado por voz**: ya viene configurada (es también la pantalla
   que pide el permiso de micrófono, porque un teclado no puede pedirlo).
2. Toca **Probar conexión**: debe decir *"Conexión correcta. Servidor de dictado:
   responde."*
3. Toca **Activar el teclado en Android** y habilita **Dictado por voz**.
4. En cualquier app, toca el campo de texto y elige el teclado con **Elegir
   teclado** (o el botón de teclado del propio teclado).

## Uso

| Botón | Qué hace |
|---|---|
| 🎤 (verde) | Empieza a grabar. Se pone rojo. |
| 🎤 (rojo) | Termina, transcribe e inserta el texto. |
| ⌨ | Cambia a **nuestro teclado de letras** para editar a mano. **Manténlo pulsado** para el selector de teclados del sistema (solo si quieres usar Gboard). |
| ⌫ | Borra un carácter. |
| ↵ | Salto de línea. |

Estados del micrófono: **verde** listo, **rojo** grabando, **naranja**
transcribiendo, **gris** con error (el motivo aparece arriba, en pequeño).

### Modo edición: teclado propio

Al tocar **⌨** ya **no** salta a Gboard: se abre **nuestro propio teclado de letras**, en el
mismo sitio. Así se corrige a mano sin cambiar de app de teclado (y sin el problema de
volver).

El modo de letras **no se recuerda**: si dejas el teclado ahí y vuelves más tarde, aparece
otra vez el **modo dictado** (el micrófono grande). Para editar, ⌨ otra vez.

| Fila | Teclas |
|---|---|
| 1 | `q w e r t y u i o p` |
| 2 | `a s d f g h j k l ñ` |
| 3 | `⇧ z x c v b n m ⌫` (mantén ⌫ y borra de corrido) |
| 4 | `?123` · `,` · **espacio** (media fila) · `.` · `↵` |

- **⇧** un toque enciende y apaga la mayúscula de **una** letra (como en Gboard);
  **mantenlo pulsado** para las mayúsculas fijas (⇪). El ⇧ arranca **encendido** cuando toca
  mayúscula automática (campo nuevo, empezar oración), así que **un solo toque deja la letra
  en minúscula**. Lo que se ve en el teclado es lo que se escribe: si apagas el ⇧, la primera
  letra sale en minúscula aunque el campo pida mayúscula al empezar frase. El ⇧ se vuelve a
  encender solo al empezar la frase siguiente.
- **?123** abre los **símbolos básicos**: números, `? ¿ ! ¡ ( ) : ; $` y las vocales
  con tilde. **más** abre los **símbolos ampliados**: `+ = < > [ ] { } / \ % & *` y
  las monedas `€ £ ¢ ¥`. **ABC** vuelve a las letras desde cualquiera de las dos
  páginas.
- **↵** y **⌫** son las teclas de borrar y de salto de línea. El **↵** lleva un **dibujo**
  (no el carácter `↵`, que la tipografía dibuja diminuto) que llena la tecla, y es la única
  tecla ancha además de `⇧` y `⌫`.
- La **barra espaciadora ocupa media fila** (el ancho que antes gastaba la tecla 🎤).
- **Para volver al dictado** desde el teclado de letras: el **micrófono de la franja de
  arriba**, a la derecha del texto copiado (antes había una tecla 🎤 en la fila de abajo, que
  le quitaba sitio al espacio).
- Las letras van **pegadas y planas**: cada fondo llena toda su celda, sin separación y
  **sin esquinas redondeadas**. Las esquinas curvas, con las teclas pegadas, dejaban un
  rombo en cada cruce de cuatro teclas. La zona que responde al dedo siempre fue la celda
  completa; al pulsar, la tecla solo cambia de color.
- La rejilla tiene **altura exacta**: cada fila mide lo mismo y cada tecla llena su fila.
  Los rótulos se miden **en píxeles, sobre el alto real de la tecla** (no en `sp`): antes,
  con `sp`, el tamaño crecía con el «tamaño de fuente» del celular y los glifos grandes
  (⇧ ⌫ ↵) pedían más alto que la tecla, así que la fila crecía para que cupieran y las
  letras de al lado quedaban cortas (se veía una franja de fondo alrededor de esas filas).
- La **barra espaciadora es la única tecla con contorno** (un borde fino): es la más ancha
  y, con todo plano, no se veía dónde empezaba ni dónde acababa.
- El **⇧** y el **⌫** son glifos que la tipografía dibuja pequeños: llevan un tamaño propio,
  bastante mayor que el de una letra suelta. El **↵** va más allá y lleva un **dibujo**
  (`ic_enter`) que llena la tecla: es más del doble de grande que el carácter `↵` de antes.

### Portapapeles

Si tienes algo copiado, encima del teclado aparece una **franja con el texto** (y el icono
de portapapeles). Un toque y se **pega** donde esté el cursor. Sale en los dos modos
(dictado y letras) y se puede apagar en los ajustes.
En la **franja** está también el **micrófono**, a la derecha del texto copiado: un toque
pasa al modo de dictado (el micrófono grande). Se enseña solo en el modo de letras; en el
modo dictado está el grande y sobraría.
El portapapeles se lee **solo mientras el teclado está en pantalla** (Android 10 y
posteriores solo lo permiten a quien tiene el foco, y ahí ese es el teclado) y **no se
guarda en ninguna parte**: se relee cada vez que aparece el teclado o cambia el
portapapeles. Si lo copiado pasa de 2 000 caracteres, no se ofrece.

### Diccionario y sugerencias

Encima del teclado hay una **barra de tres sugerencias**, como la de Gboard:

| Toque | Qué hace |
|---|---|
| Una casilla | Escribe esa palabra en lugar de la que estás escribiendo (y añade un espacio). |
| **Dedo largo** en una casilla | La añade a tu diccionario propio; si ya era tuya, la quita. |

Qué corrige y qué predice:

- **Erratas de una letra**: `casq` → `casa` (letra cambiada, que sobra, que falta o dos
  vecinas cambiadas de sitio).
- **Tildes que faltan**: `avion` → `avión`, `cancion` → `canción`. Solo cuando lo
  escrito **no** es una palabra: `esta` y `está` se respetan tal cual, porque las dos
  existen.
- **Predicción de la siguiente palabra**: aprende qué palabra suele seguir a cuál
  (tus propios bigramas) y, cuando no hay contexto, muestra tus palabras más usadas.
- **Mayúscula automática** al empezar oración (después de `.`, `!`, `?` o salto de
  línea), **solo si el campo la pide**. En campos de **correo, URL o contraseña** no se
  pone mayúscula sola, no se sugiere nada y no se corrige nada: son campos en los que el
  teclado tiene que dejar escribir el correo tal cual (antes la primera letra salía en
  mayúscula y parecía que volvía a ponerse sola cada vez que la cambiabas).
- **Contexto**: las sugerencias de un mismo prefijo se reordenan según la palabra
  anterior, con los bigramas que va aprendiendo.
- **Deshacer**: si la corrección no te gusta, el primer ⌫ deja la palabra como la
  escribiste.

El diccionario base son **49 600 palabras del español con su frecuencia**, dentro del
APK (`assets/diccionario_es.txt`, lista libre de
[FrequencyWords](https://github.com/hermitdave/FrequencyWords), CC-BY-SA 4.0, más una
lista corta propia de términos del día a día que en subtítulos no salen -whatsapp,
refrigeradora, pantallazo, gasfitero, chifa…-; la atribución completa está en
`assets/DICCIONARIO_LICENCIA.txt`). Lo que aprende de ti se queda en el celular
(`filesDir`) y **nunca sale de ahí**: sin red, sin permisos y sin servicios de terceros.

La lista se puede rehacer con `python3 cliente_android/herramientas/generar_diccionario.py`
(descarga la lista original si no está, aplica el filtro y la lista curada, y **avisa si
el corrector estropearía alguna palabra curada**; si eso pasa, termina con error).

Para llegar a Gboard, **mantén pulsado `?123`** (o el botón ⌨ del modo dictado): sale el
selector de teclados.

### Ajustes del teclado (en la app)

| Ajuste | Qué hace |
|---|---|
| **Fila de números arriba** | Añade `1 2 3 4 5 6 7 8 9 0` encima de las letras. |
| **Vibrar al pulsar las teclas** | Vibración corta en cada tecla. |
| **Sugerencias al escribir** | La barra de sugerencias y las predicciones (déjala activada salvo que quieras la barra fuera). |
| **Corregir al terminar la palabra** | Autocorrección de tildes y erratas al pulsar espacio o puntuación. |
| **Ofrecer lo copiado para pegar** | La franja del portapapeles encima del teclado (un toque pega el texto copiado). |
| **Tamaño de las teclas** | De 36 a 64: se aplica a los dos modos (letras y dictado). |

Se cambian en la app y se guardan al tocar **Guardar**; el teclado lo nota al volver a
aparecer, sin reiniciar nada.

### Si alguna vez te vas a Gboard

La vuelta la da el propio Gboard: su tecla de globo 🌐, el icono de teclado de la barra
inferior o la notificación «Cambiar teclado». Y siempre quedan estos dos atajos nuestros:

1. **Teja rápida «Volver al teclado de dictado»** (panel de ajustes rápidos). Para añadirla:
en la app, botón **«Añadir la teja al panel de ajustes rápidos»** (en Android 13+ aparece
un diálogo del sistema que la coloca sola).
2. **Botón «Volver al teclado de dictado»** en la app.

Los dos abren el **selector de teclados**: no hay forma de un solo toque, porque Android no
permite que una app se ponga por sí sola como teclado (eso exige ser app de sistema).

### Dos modos

- **Normal** (por defecto): el audio se envía **mientras hablas** por WebSocket (sin
  parciales). Al tocar terminar, la subida ya está hecha y solo queda la transcripción,
  así que el texto sale enseguida. Si el WebSocket falla o no confirma, el audio completo
  se manda por REST como respaldo: el dictado no se pierde.
- **En vivo**: mientras hablas, el servidor va cerrando frases por las pausas y el teclado
  las va escribiendo (texto en composición, subrayado); al terminar, el texto definitivo lo
  sustituye. Se activa en los ajustes. **No hace falta** para que la respuesta sea rápida:
  el modo normal también responde en cuanto terminas y no reescribe lo ya mostrado.

### Límites que conviene conocer

- La ruta **REST** admite hasta **60 segundos** y **5 MB** por dictado (los pone el puente).
- El **streaming** del modo normal no lleva ese corte; el límite práctico es lo que tarde
  en transcribirse el audio completo.
- Los dictados son **de uno en uno** (el servidor tiene un solo modelo): si hay
  otro dictado en curso, el tuyo espera unos segundos.
- Si hay un **lote de audio largo** transcribiéndose en el servidor, puedes
  esperar lo que dure el bloque en curso (3-6 s).

## Estructura y tamaño

| Archivo | Para qué |
|---|---|
| `ServicioTecladoVoz.kt` | El teclado (IME): botones, estados, sugerencias y escritura del texto. |
| `TecladoLetras.kt` | El teclado de letras y símbolos, construido en código. |
| `Diccionario.kt` | Lista de palabras, corrección de erratas, diccionario propio y bigramas. |
| `BarraSugerencias.kt` | La franja de tres sugerencias encima del teclado. |
| `GestorAudio.kt` | `AudioRecord` a 16 kHz mono PCM16. |
| `ClienteRed.kt` | REST y WebSocket con OkHttp. |
| `Ajustes.kt` | Ajustes cifrados (`EncryptedSharedPreferences`). |
| `AjustesActivity.kt` | Pantalla de ajustes y permiso de micrófono. |

Total: **3965 líneas** con comentarios, repartidas en 13 archivos. El diccionario
aporta **655 KB** de datos que viajan dentro del APK (se comprimen solos al empaquetar).

La lista de palabras se rehace con `cliente_android/herramientas/generar_diccionario.py`,
que también comprueba que el corrector no estropee ninguna palabra curada.

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| *"Falta el permiso de micrófono"* | Abre la app y acepta el permiso. |
| *"No se pudo contactar con el puente"* | URL mal escrita, sin `https://`, o el puente está caído. |
| *"Token incorrecto o ausente"* | El token del celular no coincide con `DICTADO_WEB_TOKEN`. |
| *"El proxy rechazó la clave del cliente"* | La clave no coincide con el `map` de nginx. |
| *"El servidor no entendió el audio"* | El servidor no detectó voz (micrófono muy lejos, silencio). |
| Todo funciona en casa pero no con datos móviles | El puerto `8000` no está bien publicado, o el DNS/certificado del subdominio falla. |

## Notas de implementación (trampas ya pagadas)

- El **permiso de micrófono** lo pide `AjustesActivity`: un `InputMethodService`
  no puede mostrar el diálogo de permisos.
- El tema es **`Theme.Material3.Dark` a propósito**. Con `DayNight` y un fondo
  oscuro fijado, en un celular en modo claro el texto salía oscuro sobre oscuro
  y no se leía: por eso el tema es oscuro y los colores van explícitos.
- Las credenciales se leen de `local.properties` en tiempo de compilación
  (`BuildConfig`); si están vacías, la pantalla de ajustes las pide.
- El botón de teclado hace un **ida y vuelta**: un toque llama a
  `switchToLastInputMethod(token)` (API interna, la misma familia que usa el botón de
  planeta de Gboard) y, si el equipo la bloquea, cae al público
  `showInputMethodPicker()`. La variante pública `switchToPreviousInputMethod()` **no
existe** en el `android.jar` de android-34 con el que se compila aquí.
- El texto del modo en vivo se escribe como **composición** (`setComposingText`) y el
  texto final lo sustituye con `commitText`. Antes se borraba contando caracteres con
  `deleteSurroundingText`, y en WhatsApp ese borrado no se aplicaba: quedaba el texto en
  vivo y encima se añadía el completo, que es el duplicado que se veía al terminar.
- **No se manda audio hasta que el puente responde `listo`.** Si se mandaba antes, el
  `fin` podía llegar a una sesión que todavía no existía («No hay ningún dictado en
  curso») y ese aviso tapaba el error real.
- Sin el permiso de `RECORD_AUDIO` el teclado no falla en silencio: muestra el
  aviso en la barra de estado del propio teclado.
- **El modo del teclado no se guarda**: `onStartInputView` vuelve siempre al modo de
  dictado. El servicio (IME) vive mucho más que una pantalla de teclado, así que el
  campo `modoEdicion` se quedaba puesto de una vez para otra.
- **Lo que pide el campo manda**: `inputType` dice si toca mayúscula al empezar frase y si
  el campo admite sugerencias/corrección. En un correo o una contraseña no se capitaliza,
  no se sugiere y no se aprende nada (que no acaben direcciones y claves en el
  diccionario propio).
- **La separación entre teclas era dibujo, no tacto**: el fondo llevaba un `<inset>` de
  2dp por lado. Quitarlo no agranda la zona que responde (ya era la celda entera) pero sí
  el fondo visible, que es lo que se veía perdido entre letras.
- **Iconos con huecos** (`⌫`): se dibujan con `android:fillType="evenOdd"` y el recorrido
  interior se recorta. El icono viejo de borrar era el de «descargar» (una caja con una
  flecha hacia abajo).
- **Rótulos de tecla en píxeles, no en `sp`**: un teclado con alto fijo no puede depender
  del «tamaño de fuente» del sistema. Con `sp`, los glifos grandes (⇧ ⌫ ↵, que vienen de
  fuentes de respaldo con una caja más alta) pedían más alto que la tecla; la fila se
  estiraba para que cupieran y las teclas de al lado quedaban cortas: la franja oscura que
  se veía en la captura. Medido sobre la captura, esas teclas eran ~23px más altas.
  Arreglo: **alto de fila exacto** (`LinearLayout.LayoutParams` de la fila), **teclas
  `match_parent`** (llenan su fila) y **tamaños en píxeles** calculados sobre el alto real.
- **La franja de arriba ahora vive en los dos modos** (antes solo salía con algo copiado):
  en el modo de letras lleva el micrófono, y su visibilidad se recalcula en `mostrarModo`.
