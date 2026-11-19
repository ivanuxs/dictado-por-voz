# Modelos de traducción (licencias)

Los modelos convertidos no se guardan en Git: se generan con
`puente_web/herramientas/preparar_modelos.py`. Esta nota sí se versiona, para
dejar claro con qué licencia viene cada uno.

| Carpeta | Modelo | Licencia |
|---|---|---|
| `es-en`, `en-es` | `Helsinki-NLP/opus-mt-es-en`, `opus-mt-en-es` | Apache-2.0 |
| `es-it`, `it-es` | `Helsinki-NLP/opus-mt-es-it`, `opus-mt-it-es` | Apache-2.0 |
| `en-zh` | `Helsinki-NLP/opus-mt-en-zh` | Apache-2.0 |
| `zh-en` | `Helsinki-NLP/opus-mt-zh-en` | **CC-BY-4.0** (atribución obligatoria) |
| `en-roa`, `roa-en` | `Helsinki-NLP/opus-mt-en-ROMANCE`, `opus-mt-ROMANCE-en` | Apache-2.0 |

Los Opus-MT son modelos Marian entrenados por Helsinki-NLP sobre datos de
Tatoeba/OPUS. La conversión a CTranslate2 int8 la hace este proyecto y no
cambia la licencia.

`zh-en` es el único con CC-BY-4.0: si algún día se distribuye el producto, hay
que atribuir a Helsinki-NLP (y mantener este archivo).
