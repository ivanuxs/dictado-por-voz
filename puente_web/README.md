# Puente HTTPS/WSS → servidor de dictado

Puente entre el **teclado de Android** y el **servidor de dictado** que ya corre
en este equipo. **No transcribe nada**: reenvía el audio por ZMQ y devuelve el
texto que produce el servidor, con sus correcciones y su diccionario aprendido.

```
Android (IME) ──HTTPS/WSS──▶ nginx (192.168.1.100) ──▶ este puente (192.168.1.2:8000)
                                                              │ ZMQ DEALER
                                                              ▼
                                              servidor de dictado (127.0.0.1:5555)
                                                     ← intacto, no se toca
```

## Por qué un puente y no exponer el servidor

- Android **no habla ZMQ**: sí habla HTTP/WebSocket, que es lo que trae OkHttp.
- El servidor mantiene **un solo modelo Whisper en memoria** (1,9 GB de VRAM en
  una GPU de 6 GB): un backend aparte que cargara su propio modelo no cabría.
- El ZMQ queda **solo en la red local**. Lo que se publica es este puente, con
  token, y a través del reverse proxy.

> ⚠️ **Nunca** reenvíes el puerto `5555` en nginx. En el servidor, el flag de
> autenticación es **global** (no por cliente): en cuanto un cliente se
> autentica, cualquier otro cliente ZMQ queda aceptado. La frontera de
> seguridad es este puente, no el token del ZMQ.

## Instalación

```bash
cd "~/Nextcloud/Proyectos/dictado por voz"

# 1. Entorno virtual (Python 3.14)
python3 -m venv entorno_web
entorno_web/bin/python -m pip install -r puente_web/requirements.txt

# 2. Token de acceso, FUERA del repositorio (es público)
cp puente_web/.env.example ~/.config/puente-web.env
openssl rand -hex 32          # pega el resultado en DICTADO_WEB_TOKEN
chmod 600 ~/.config/puente-web.env

# 3. Servicio de usuario (sin sudo)
mkdir -p ~/.config/systemd/user
cp puente_web/puente-web.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now puente-web.service
```

Comandos útiles:

```bash
systemctl --user status puente-web      # estado
systemctl --user restart puente-web     # reiniciar tras cambiar el .env
journalctl --user -u puente-web -f      # consola (avisos de uvicorn)
tail -f puente_web/logs/registro_servidor_web.log   # registro con latencias
```

## API

Todas las peticiones llevan `Authorization: Bearer <DICTADO_WEB_TOKEN>`.

### `POST /api/v1/transcribe` — push-to-talk

El cuerpo es el audio completo, en cualquiera de estas dos formas:

- **PCM crudo** int16 (lo que entrega `AudioRecord`), mono a 16 kHz.
- **WAV** (se reconoce por la cabecera `RIFF`): se acepta cualquier frecuencia o
  número de canales y se convierte a 16 kHz mono.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/transcribe \
    -H "Authorization: Bearer $DICTADO_WEB_TOKEN" \
    -H "Content-Type: audio/wav" \
    --data-binary @frase.wav
```

```json
{
  "status": "ok",
  "transcript": "Habilitemos también la mosca 2. Creo que con la mosca 1 y la mosca 2 será suficiente para hacer todas las pruebas reales.",
  "duracion_s": 6.4,
  "rms": 348,
  "latencia_ms": 2609
}
```

Errores (siempre `{"status": "error", "motivo": "..."}`):

| Código | Cuándo |
|---|---|
| `401` | Token ausente o incorrecto. |
| `413` | El audio supera `DICTADO_MAX_MB` (5 MB por defecto). |
| `422` | Audio inválido, o el servidor no detectó voz (devuelve **su** motivo). |
| `503` | Demasiados dictados en cola. |
| `504` | El servidor de dictado no respondió. |

### `WS /ws/transcribe` — dictado en vivo

Control en JSON, audio en tramas binarias:

```jsonc
// cliente → puente
{"tipo": "inicio", "modo": "vivo"}    // "vivo" = texto parcial; "normal" = solo el final
<binario: PCM int16 16 kHz mono>      // tantas tramas como haga falta
{"tipo": "fin"}
{"tipo": "cancelar"}                  // aborta y deja el servidor limpio

// puente → cliente
{"tipo": "listo", "modo": "vivo"}
{"tipo": "parcial", "texto": "Habilitemos también la mosca 2."}
{"tipo": "final", "texto": "...", "parciales": ["..."], "latencia_ms": 3559}
{"tipo": "error", "mensaje": "..."}
{"tipo": "cancelado"}
```

El modo `vivo` reutiliza el corte por pausas que ya tiene el servidor: los
parciales llegan mientras se habla, sin procesar nada extra.

### `GET /api/v1/salud` y `GET /api/v1/estado`

`/salud` responde `{"status":"ok"}` sin token (para la comprobación de salud de
nginx). `/estado` exige token y además hace un `PING` al servidor de dictado.

## Comprobaciones

```bash
# Con una muestra de dictado real (busca el .wav solo)
set -a; . ~/.config/puente-web.env; set +a
entorno_web/bin/python puente_web/prueba_puente.py            # REST
entorno_web/bin/python puente_web/prueba_puente.py --ws --modo vivo
entorno_web/bin/python puente_web/prueba_puente.py --cancelar

# Contra el dominio público (por nginx: hace falta la clave del proxy)
entorno_web/bin/python puente_web/prueba_puente.py \
    --url https://dictado.TU-DOMINIO.com --clave LA_CLAVE
```

Medido con este equipo (2026-10-05), sobre un dictado real de 6,4 s:

| Prueba | Resultado |
|---|---|
| REST | Texto **exacto** al de referencia, 2,6 s de ida y vuelta |
| WebSocket `vivo` | 2 parciales + final, 3,6 s |
| Cancelar a medias y dictar otra vez | Correcto (el servidor no queda bloqueado) |
| Sin token | `401` |
| Sin la clave del proxy (por nginx) | `403` |
| Por el dominio público, con clave y token | Certificado válido y dictado real en 2,6 s |

## Límites (protegen la GPU)

| Variable | Por defecto | Para qué |
|---|---|---|
| `DICTADO_WEB_TOKEN` | — | Token de acceso; **obligatorio**. |
| `DICTADO_MAX_SEGUNDOS` | `60` | Audio máximo por dictado. |
| `DICTADO_MAX_MB` | `5` | Cuerpo máximo de la petición. |
| `DICTADO_CONCURRENTES` | `2` | Peticiones REST que esperan en cola. |
| `DICTADO_ESPERA_MAXIMA` | `30` | Segundos que espera una petición antes de rendirse. |
| `DICTADO_TIMEOUT` | `120` | Espera máxima a la transcripción. |
| `DICTADO_PUENTE_HOST` / `PUERTO` | `0.0.0.0` / `8000` | Escucha. |
| `DICTADO_ZMQ` | `tcp://127.0.0.1:5555` | Servidor de dictado. |
| `DICTADO_ZMQ_TOKEN` | vacío | Solo si el servidor tiene `DICTADO_TOKEN`. |

La transcripción es **siempre de una en una** (un modelo, un candado): lo que
hace la cola es limitar cuántas peticiones esperan, para que un abuso con el
token no deje la GPU ocupada indefinidamente.

## Reverse proxy (nginx en 192.168.1.100)

Los archivos ya están escritos en `puente_web/nginx/` y probados contra la versión del proxy,
**nginx/1.24.0**. El TLS lo pone **certbot con el plugin de nginx** (`certbot --nginx`), el
mismo que usas para tus otros dominios: aquí solo se escribe el sitio del **puerto 80**, y
certbot le añade el bloque del 443 y la redirección. Así el 80 nunca queda "en espera" ni hay
dos sitios que puedan desincronizarse.

| Archivo | Va en | Qué trae |
|---|---|---|
| [`nginx/dictado-http.conf`](nginx/dictado-http.conf) | `/etc/nginx/conf.d/` | La cabecera secreta (segunda barrera), el `map` que pone `Connection: upgrade` solo al WebSocket y el `limit_req_zone`. |
| [`nginx/dictado-sitio.conf`](nginx/dictado-sitio.conf) | `/etc/nginx/sites-available/` | El `server` del **puerto 80**: la barrera de la clave, el límite de cuerpo, el reto de Let's Encrypt y el `proxy_pass` al puente. **No trae bloque 443**: el TLS y la redirección los añade certbot, así no hay dos sitios que se desincronicen. |
| [`nginx/instalar_en_proxy.sh`](nginx/instalar_en_proxy.sh) | se ejecuta ahí | Instalador: pone el dominio y la clave, pide el certificado con `certbot --nginx` y recarga. Si `nginx -t` falla, no recarga y tus otros sitios siguen igual. |

**Atajo (recomendado)**, en la máquina del proxy:

```bash
sudo bash puente_web/nginx/instalar_en_proxy.sh --dominio dictado.TU-DOMINIO.com

# Sin sudo y sin tocar nada, para revisar los archivos antes:
bash puente_web/nginx/instalar_en_proxy.sh --dominio dictado.TU-DOMINIO.com --simular /tmp/revisar
```

A mano, sería:

```bash
# 1. Clave de la segunda barrera: se pone en el archivo y en los ajustes de la app
openssl rand -hex 32

# 2. Edita el dominio y la clave en los dos archivos, y cópialos
sudo cp puente_web/nginx/dictado-http.conf  /etc/nginx/conf.d/
sudo cp puente_web/nginx/dictado-sitio.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/dictado-sitio.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 3. Certificado: certbot añade el bloque TLS y la redirección del 80 al 443
sudo certbot --nginx -d dictado.TU-DOMINIO.com --redirect

# 4. Comprobar
sudo nginx -t && sudo systemctl reload nginx
```

Para probar desde fuera de la red (y que la clave es obligatoria):

```bash
# Sin la cabecera: 403
curl -s -o /dev/null -w '%{http_code}\n' https://dictado.midominio.com/api/v1/salud \
    -H "X-Clave-Cliente: lo-que-sea"
# Con la clave y el token: 200
curl -s https://dictado.midominio.com/api/v1/estado \
    -H "X-Clave-Cliente: TU_CLAVE" -H "Authorization: Bearer TU_TOKEN"
```

Firewall del portátil (una sola vez, necesita `sudo`):

```bash
sudo ufw allow from 192.168.1.100 to any port 8000 proto tcp
```

Avisos:

- El portátil tiene **IP fija dentro de la red local** (`192.168.1.2`): nginx puede
  apuntar a ella sin riesgo de que cambie.
- El tramo `192.168.1.100 → 192.168.1.2` viaja en claro por la red local. Si se
  quiere cifrar también, nginx puede hablarle al puente por HTTPS con un
  certificado propio.
- La clave viaja en un `map` de nginx: con los 64 caracteres de `openssl rand -hex 32` hace
  falta `map_hash_bucket_size 128;`, que ya está en el archivo. Sin esa línea, nginx no
  arranca ("could not build map_hash").
- Si el dominio está detrás de **Cloudflare**: con el modo SSL/TLS en *Flexible* la
  redirección del puerto 80 a HTTPS provoca un **bucle infinito**. Usa *Full (strict)* y el
  certificado en el origen, o deja el registro en *DNS only*.
- El token del APK se puede extraer de un APK descompilado: por eso la cabecera
  secreta en nginx es una barrera útil, no un adorno.
- El certificado y su renovación los gestiona **certbot**. Para comprobar que la renovación
  funcionará: `sudo certbot renew --dry-run`. La barrera de la clave **no** bloquea el reto de
  Let's Encrypt: la ruta `/.well-known/acme-challenge/` se atiende antes del `location /`
  (verificado: devuelve 404, no 403).
- Si algún día actualizas el proxy a nginx 1.25 o superior, `listen 443 ssl http2` seguirá
  funcionando pero avisará: lo nuevo es `listen 443 ssl;` + `http2 on;`.
