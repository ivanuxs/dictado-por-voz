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
skipkey://config?url=https://dictado.tu-dominio.com&token=EL_TOKEN&clave=LA_CLAVE
```

Te lo mandas por WhatsApp, lo tocas y la app queda configurada sin escribir
nada. Se puede reenviar a otro celular cuando haga falta.

### APK para otro usuario (sin el token maestro)

Si el celular es de otra persona, el APK **no** debe llevar tu `dictadoToken`: la
app arrancaría con tu sesión maestra (sin plan ni cupos) y la pantalla «Cuenta»
no ofrecería el registro. Se compila así:

```bash
bash cliente_android/construir_apk.sh assembleDebug -PsinToken
```

Deja el APK en `.../outputs/apk/debug/dictado-sin-token.apk` (con la dirección y
la clave del proxy dentro, el token fuera). Ya instalado, esa persona abre
**Ajustes → Cuenta → Crear cuenta** con su correo y una contraseña de 8+
caracteres; si el puente tiene correo configurado, mete el código de 6 dígitos.

## Instalar y activar

```bash
bash cliente_android/construir_apk.sh
~/Android/Sdk/platform-tools/adb install -r cliente_android/app/build/outputs/apk/debug/app-debug.apk
# o copia el APK al celular y ábrelo (habilitando "instalar apps desconocidas")
```

Después:

1. Abre la app **SkipKey**: ya viene configurada (es también la pantalla
   que pide el permiso de micrófono, porque un teclado no puede pedirlo).
2. Toca **Probar conexión**: debe decir *"Conexión correcta. Servidor de dictado:
   responde."*
3. Toca **Activar el teclado en Android** y habilita **SkipKey**.
4. En cualquier app, toca el campo de texto y elige el teclado con **Elegir
   teclado** (o el botón de teclado del propio teclado).

## Uso

| Botón | Qué hace |
|---|---|
| 🎤 (verde, en la banda de arriba) | Empieza a grabar. Se pone rojo. |
| 🎤 (rojo) | Termina, transcribe e inserta el texto. |
| 🌐 (banda) | Traduce el texto del campo al idioma elegido. **Manténlo pulsado** para cambiar de idioma (inglés → portugués → italiano → chino → español). |
| 📋 (banda, solo si hay algo) | Un toque **pega** lo último copiado; **mantenlo pulsado** para abrir el historial. |
| ⌫ | Tecla del teclado de letras: borra un carácter (mantenla y borra de corrido). |
| ↵ | Salto de línea. |

Estados del micrófono: **verde** listo, **rojo** grabando, **naranja**
transcribiendo, **gris** con error (el motivo aparece al lado, en pequeño).

### Una sola pantalla: letras y micrófono

Ya **no hay dos modos que se sustituyan**. El teclado es siempre el mismo: arriba la
**banda de dictado** (línea de estado a la izquierda; a la derecha los botones de
**ajustes**, **traducir**, **portapapeles** —solo si hay algo copiado— y el **micrófono**,
todos de 36 dp de alto) y debajo la barra de sugerencias y el
**teclado de letras** completo. Así se dicta y se corrige a mano sin cambiar de pantalla
ni saltar a Gboard. El engranaje abre la pantalla de ajustes de la app sin salir del
teclado; al volver, «Volver al teclado de dictado» devuelve al campo donde estabas.

Antes había que tocar **⌨** para pasar a las letras y el micrófono de la franja para
volver al dictado; esos dos botones ya no existen. El micrófono está siempre arriba, en
sitio del texto de estado, y el ⌫/↵ son teclas del teclado de letras.

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
- **Para dictar** no hay que volver a ningún modo: el **micrófono** está siempre en la banda
  de arriba, a la derecha de la línea de estado.
- **Si el teclado no cabe, encoge las filas solo.** `pintarTeclado()` llama a
  `encajarEnElHueco()`, que mide lo que ocupa el teclado, lo compara con la ventana del IME
  y reparte lo que sobra entre las filas (`TecladoLetras.encoger`, mínimo 28 dp). Así la
  fila de números y la de `?123` caben enteras en pantallas bajas.
- Las letras van **planas y sin separación horizontal**: cada fondo llena su celda y
  **sin esquinas redondeadas**. Las esquinas curvas, con las teclas pegadas, dejaban un
  rombo en cada cruce de cuatro teclas. La zona que responde al dedo siempre fue la celda
  completa; al pulsar, la tecla solo cambia de color.
- **Entre filas hay 4 dp de hueco** (2026-11-18), y no es adorno: las filas iban pegadas y
  buscar el espacio daba en la `b` de la fila de arriba. Ahora el toque que se queda corto
  cae en el hueco, que no pertenece a ninguna tecla, y **no escribe nada** (mejor no escribir
  que escribir una letra equivocada). El coste: los huecos suman ~16 dp y `encajar()` encoge
  las filas unos 3 dp (42 → ~39 dp).
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

Si tienes algo copiado, en la banda aparece un **botón 📋** entre el estado y el micrófono
(antes era una franja entera encima del teclado, que gastaba ~50 dp de alto). Un toque
**pega** donde esté el cursor y **mantenerlo pulsado** abre el historial. El botón queda
atenuado cuando lo único que puede hacer es abrir el historial. Se puede apagar en los ajustes.
Ya **no se ve el texto** copiado: se gana el alto de la franja a cambio.
El portapapeles se lee **solo mientras el teclado está en pantalla** (Android 10 y
posteriores solo lo permiten a quien tiene el foco, y ahí ese es el teclado) y **no se
guarda en ninguna parte**: se relee cada vez que aparece el teclado o cambia el
portapapeles. Si lo copiado pasa de 2 000 caracteres, no se ofrece.

### Traducir

El botón **🌐** de la banda traduce el texto del campo: lo que tengas **seleccionado** o, si
no hay selección, la **línea** donde está el cursor. El resultado **sustituye** al original.

- **Un toque**: traduce al idioma elegido.
- **Pulsación larga**: cambia el idioma de destino (inglés → portugués → italiano → chino →
  español) y lo avisa con un mensaje corto.
- Mientras traduce, la línea de estado dice *«Traduciendo al inglés…»*; si falla, el texto no
  se toca y el motivo sale ahí.

La traducción la hace el **puente** (`POST /api/v1/traducir`), en CPU, con Opus-MT; no pasa
por Whisper ni toca el dictado. Con los modelos ya cargados tarda 0,1–0,8 s por frase; la
primera vez de cada par puede tardar unos segundos (carga del modelo). Los pares con chino y
portugués van por inglés (es→en→zh), porque no hay modelo directo español↔chino.

### Diccionario y sugerencias
Encima del teclado hay una **barra de tres sugerencias** y, al final, un botón estrecho para guardar palabras:

| Toque | Qué hace |
|---|---|
| Una casilla | Escribe esa palabra en lugar de la que estás escribiendo (y añade un espacio). |
| **＋** (al final) | Guarda la palabra que estás escribiendo en tu diccionario. |
| **✓** (al final) | Esa palabra ya es tuya: un toque la quita. |
| **↩** (primera casilla) | Deshace la corrección que se acaba de hacer y guarda la palabra como la escribiste. |

El botón **＋/✓** solo sale cuando hay algo que decidir: si la palabra ya viene en la lista base
(49 600 palabras), no se ofrece guardarla. Y la casilla **↩** solo está mientras dura el aviso;
entretanto ocupa uno de los tres sitios y quedan dos sugerencias.

Qué corrige y qué predice:

- **Las palabras que escribes se aprenden solas, a la primera** (2026-10-31). Antes hacía
  falta escribirlas dos veces: la segunda todavía salía la corrección automática y había
  que borrarla. Ahora, en cuanto cierras una palabra con espacio o puntuación, queda
  guardada como tuya y no se vuelve a corregir.
- **Si te corrige y no querías, toca ↩**: el texto vuelve a como lo escribiste **y la
  palabra queda aprendida para siempre**. Esa es la forma de decir «esto era lo que yo
  quería» sin pasar por los ajustes. (Hasta 2026-11-18 esto lo hacía el primer ⌫, sin
  avisar: la tecla cambiaba de función y el texto se reescribía solo. Ya no: **⌫ borra
  siempre**, y deshacer es una casilla que se ve.)
- **Los nombres propios no se tocan**: si la mayúscula inicial la pusiste tú (⇧ a mano) y
  no era la mayúscula automática de empezar frase, la palabra no se autocorrige. Así se
  respetan apellidos, marcas y topónimos.
- **Erratas de una letra**: `casq` → `casa` (letra cambiada, que sobra, que falta o dos
  vecinas cambiadas de sitio).
- **Tildes que faltan**: `avion` → `avión`, `cancion` → `canción`. Solo cuando lo
  escrito **no** es una palabra: `esta` y `está` se respetan tal cual, porque las dos
  existen.
- **Predicción de la siguiente palabra**: aprende qué palabra suele seguir a cuál
  (tus propios bigramas) y, cuando no hay contexto, muestra tus palabras más usadas
  (las que has escrito al menos dos veces, para no llenar la barra de ruido).
- **Mayúscula automática** al empezar oración (después de `.`, `!`, `?` o salto de
  línea), **solo si el campo la pide**. En campos de **correo, URL o contraseña** no se
  pone mayúscula sola, no se sugiere nada y no se corrige nada: son campos en los que el
  teclado tiene que dejar escribir el correo tal cual (antes la primera letra salía en
  mayúscula y parecía que volvía a ponerse sola cada vez que la cambiabas).
- **Léxico peruano en la lista base** (2026-10-31): chela, chévere, bacán, palta, guagua,
  jerma, pisco, lúcuma, granadilla, maracuyá, tumbo, chirimoya, guanábana, sánguche,
  pollada, salchipapa, juanes, tacacho, rachi, yape, plin, replana, custer… Se añaden en
  `herramientas/generar_diccionario.py` y se regenera `assets/diccionario_es.txt` (el
  script avisa si el corrector estropearía alguna).
- **Contexto**: las sugerencias de un mismo prefijo se reordenan según la palabra
  anterior, con los bigramas que va aprendiendo.
- **Deshacer**: si la corrección no te gusta, toca **↩** en la barra; el ⌫ ya solo borra.

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

#### Correcciones del dictado y perfiles

Cuando el motor escribe una palabra y tú la cambias —la seleccionas y escribes otra, o la
seleccionas y dictas otra vez— el teclado **aprende la pareja** *lo que dictó → lo que
querías* (2026-11-01). Con esa pareja:

- **La primera vez que corriges, ya queda aprendida**: la corrección aparece como primera
  casilla de la barra al escribir esa palabra y, **en el dictado siguiente, el texto sale ya
  con tu palabra** (respetando si era nombre propio o iba en mayúsculas). Antes hacían falta
  dos correcciones y, en la práctica, parecía que no se había aprendido nada.
- **Si te equivocaste al enseñarla, toca ↩**: deshacer la corrección también **olvida la
  pareja**, así no vuelve a aplicarse.

Solo se aprende en campos normales: en contraseñas, correos y campos sin sugerencias no se
toca nada.

**Mis palabras (2026-11-18).** En Ajustes hay un botón **«Mis palabras y correcciones»** que
abre la lista de lo que el teclado ha guardado de ti en ese perfil: las palabras propias que
**no** vienen en la lista base, con cuántas veces las usaste, y las correcciones del dictado
(`lo que dijo el motor → lo que querías`). Cada fila lleva una **✕** para quitarla; antes eso
solo se podía hacer con un dedo largo en la barra, a ciegas, y no había manera de ver qué
estaba guardado.

**Perfiles.** En Ajustes hay un **perfil del diccionario** (por defecto `personal`). Cada
perfil tiene sus propias palabras, bigramas y correcciones, en archivos
`dictado_<perfil>_*.txt` dentro de la app: dos personas que compartan el celular no se
mezclan. El cambio se aplica al volver a abrir el teclado, y el botón **«Borrar el
diccionario de este perfil»** deja ese perfil como nuevo sin tocar los demás.

Para llegar a Gboard, **mantén pulsado `?123`**: sale el
selector de teclados.

### Cuenta y planes

La app se publica en Google Play, así que **cualquiera puede crearse una cuenta desde la
propia app**: botón **Cuenta y plan** en Ajustes → **usuario**, contraseña (mínimo 8),
correo opcional (solo para recuperar) y, si el servidor tiene correo configurado, el código
de 6 números que llega por email.

- **Sin cuenta el teclado funciona igual**: dicta el reconocedor del celular, que es
  gratis, no usa la red y no gasta nada del servidor.
- **Dentro de la app hay tres planes** (2026-11-18): Gratis dicta con el motor del celular;
  Básico da 30 min de servidor y 50 traducciones al día; Pro no tiene tope diario, suma el
  dictado en vivo y 3 dispositivos. El puente comprueba el plan en cada petición (403): la
  comprobación no depende del APK.
- **Planes**: Gratis (motor del celular) · Básico (30 min de servidor y 50 traducciones al
  día, 1 dispositivo) · Pro (minutos y traducciones **ilimitados**, dictado en vivo y 3
  dispositivos).
- El botón **Ver planes** pinta el catálogo que publica el puente
  (`GET /api/v1/planes`, público: se ve con o sin sesión) y marca en cuál estás. Es solo
  informativo: **aquí no se cobra ni se cambia nada**; el plan lo escribe el puente y los
  cambios se piden al administrador (`puente_web/herramientas/cambiar_plan.py`). El cobro
  dentro de la app llegará con Google Play Billing (entrega 3), que Google exige para
  vender desde la tienda.
- La pantalla enseña el **plan y los cupos de hoy** («Quedan hoy: 128 min de servidor»).
- **Cerrar sesión** no borra el diccionario del celular; **Borrar mi cuenta** sí borra
  todo en el servidor (lo exige Google Play).
- La contraseña **no se guarda**: se queda el token de ese dispositivo, cifrado en
  `EncryptedSharedPreferences`.

### Ajustes del teclado (en la app)

| Ajuste | Qué hace |
|---|---|
| **Fila de números arriba** | Añade `1 2 3 4 5 6 7 8 9 0` encima de las letras. |
| **Vibrar al pulsar las teclas** | Vibración corta en cada tecla. |
| **Sugerencias al escribir** | La barra de sugerencias y las predicciones (déjala activada salvo que quieras la barra fuera). |
| **Corregir al terminar la palabra** | Autocorrección de tildes y erratas al pulsar espacio o puntuación. |
| **Perfil del diccionario** | Con qué perfil se aprenden las palabras y correcciones (cada uno tiene su diccionario). |
| **Cuenta y plan** | Entrar, crear cuenta y ver el plan y los cupos de hoy. |
| **Ofrecer lo copiado para pegar** | La franja del portapapeles encima del teclado (un toque pega el texto copiado). |
| **Tamaño de las teclas** | De 36 a 64: se aplica a las teclas del teclado de letras. |

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
| `BarraSugerencias.kt` | La franja de tres sugerencias encima del teclado, con su botón ＋/✓ y el ↩ de deshacer. |
| `PalabrasActivity.kt` | «Mis palabras»: ver y borrar lo que el diccionario guardó del usuario. |
| `GestorAudio.kt` | `AudioRecord` a 16 kHz mono PCM16. |
| `ClienteRed.kt` | REST y WebSocket con OkHttp. |
| `Ajustes.kt` | Ajustes cifrados (`EncryptedSharedPreferences`). |
| `AjustesActivity.kt` | Pantalla de ajustes y permiso de micrófono. |

Total: **5553 líneas** con comentarios, repartidas en 15 archivos. El diccionario
aporta **655 KB** de datos que viajan dentro del APK (se comprimen solos al empaquetar).

> La palabra clave «computadora» y la ejecución de órdenes ya **no** están aquí: desde el
> 2026-11-14 viven en el proyecto hermano `Asistente por voz` (`android/`). El teclado dicta
> con el botón del micrófono y el campo a la vista, y el APK dejó de llevar el modelo Vosk
> (38 MB).

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
- **Todo cabe en una pantalla** (2026-10-31): antes el micrófono grande y las letras eran
  dos modos que se sustituían, y había que ir y volver con la tecla ⌨ / el micro de la franja.
  Ahora la banda (`vista_dictado.xml`: estado + portapapeles + `boton_micro`) es fija arriba
  y debajo va siempre el teclado de letras (`pintarTeclado`). No existe `modoEdicion`: el IME
  solo tiene un estado y no hay que recordar en cuál se quedó.
- **La barra de navegación se dibuja ENCIMA de la ventana del teclado.** Medido el
  2026-10-31 en el Xiaomi (Android 16): la ventana del IME llegaba hasta el borde de la
  pantalla y los últimos 47 dp quedaban debajo de los botones del sistema, así que el espacio
  y el ↵ salían cortados. Se arregla con `ViewCompat.setOnApplyWindowInsetsListener` en el
  root de `keyboard_view.xml` (relleno inferior = inset de `navigationBars`).
- **La ventana del IME no crece a voluntad** (317 dp en ese celular): con la banda, la fila
  de números y la barra de sugerencias la suma se pasaba. Por eso existe
  `encajarEnElHueco()` + `TecladoLetras.encoger()`: si no cabe, se bajan las filas lo justo
  (28 dp mínimo) en vez de recortar el teclado por abajo.
- **El alto de la franja de sugerencias bajó de 38 a 32 dp** y el micrófono de 56 a 36 dp
  (el 25 % que pidió el usuario) para dejar sitio a la fila de números.
- **El alto por defecto de tecla bajó de 46 a 42 dp** con ese cambio. El ajuste manual sigue
  de 36 a 64.
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
- **La franja del portapapeles ya no existe**: pasó a ser el botón 📋 de la banda (el
  botón grande del micrófono está siempre ahí) y su visibilidad se recalcula en
  `refrescarPortapapeles`: solo aparece si hay texto o historial que ofrecer, y nunca en un
  campo de contraseña.
