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
volver):

| Fila | Teclas |
|---|---|
| 1 | `q w e r t y u i o p` |
| 2 | `a s d f g h j k l ñ` |
| 3 | `⇧ z x c v b n m ⌫` (mantén ⌫ y borra de corrido) |
| 4 | `?123` · `,` · **🎤** · espacio · `.` · `↵` |

- **⇧** una vez = una mayúscula; otra vez = mayúsculas fijas (⇪); otra vez = normal.
- **?123** abre números, símbolos y `á é í ó ú ¿ ¡`; **ABC** vuelve a las letras.
- **🎤** vuelve al modo dictado: dictar y corregir sin salir de la app.

Lo único que no tiene es autocorrección ni sugerencias. Para eso está Gboard, y para
llegar a él **mantén pulsado `?123`** (o el botón ⌨ del modo dictado): sale el selector de
teclados.

### Ajustes del teclado (en la app)

| Ajuste | Qué hace |
|---|---|
| **Fila de números arriba** | Añade `1 2 3 4 5 6 7 8 9 0` encima de las letras. |
| **Vibrar al pulsar las teclas** | Vibración corta en cada tecla. |
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

- **Normal** (por defecto): grabas, tocas para terminar, el audio se manda
  completo y el texto aparece de golpe. Es el más robusto y el que conviene si
  la red es mala.
- **En vivo**: mientras hablas, el servidor va cerrando frases por las pausas y
  el teclado las va escribiendo; al terminar, el último texto reemplaza al
  provisional. Se activa en los ajustes.

### Límites que conviene conocer

- Máximo **60 segundos** de audio por dictado y **5 MB** por petición (los pone
  el puente). Un dictado largo: pártelo en dos.
- Los dictados son **de uno en uno** (el servidor tiene un solo modelo): si hay
  otro dictado en curso, el tuyo espera unos segundos.
- Si hay un **lote de audio largo** transcribiéndose en el servidor, puedes
  esperar lo que dure el bloque en curso (3-6 s).

## Estructura y tamaño

| Archivo | Para qué |
|---|---|
| `ServicioTecladoVoz.kt` | El teclado (IME): botones, estados y escritura del texto. |
| `GestorAudio.kt` | `AudioRecord` a 16 kHz mono PCM16. |
| `ClienteRed.kt` | REST y WebSocket con OkHttp. |
| `Ajustes.kt` | Ajustes cifrados (`EncryptedSharedPreferences`). |
| `AjustesActivity.kt` | Pantalla de ajustes y permiso de micrófono. |

Total: **788 líneas** con comentarios (618 sin contar comentarios ni líneas en
blanco), repartidas en 5 archivos.

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
- El texto del modo en vivo se escribe con `commitText` y se corrige con
  `deleteSurroundingText`. Si mueves el cursor a mitad del dictado, el reemplazo
  puede afectar al texto equivocado: no cambies de posición mientras dictas.
- Sin el permiso de `RECORD_AUDIO` el teclado no falla en silencio: muestra el
  aviso en la barra de estado del propio teclado.
