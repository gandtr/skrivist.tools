# Skrivist Custom TTS Server API

Skrivist (Maker tier) lets you configure your own TTS server. Any server that implements the protocol described in this document will work — you can use any speech synthesis engine, language, or runtime you like.

This document covers everything you need to build a Skrivist-compatible TTS server from scratch.

---

## How it fits together

```
┌─────────────────────┐         HTTPS / WSS          ┌──────────────────────┐
│   Skrivist (web /   │  ────────────────────────>   │   Your TTS Server    │
│   mobile app)       │  GET /voices                 │                      │
│                     │  POST /tts                   │  Any language/engine │
│  Settings →         │  WS  /ws                     │  Any host            │
│  Custom TTS Server  │  <──── MP3 audio ──────────  │                      │
└─────────────────────┘                               └──────────────────────┘
```

In **Settings → Custom TTS Server** you enter:
- **Server URL** — the base URL of your server (e.g. `https://tts.example.com` or `localhost:5053`)
- **API Key** — optional secret for authentication

Skrivist then discovers voices from your server, and streams audio through it while reading.

---

## Quick reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | `GET` | Liveness probe (always auth-exempt) |
| `/voices` | `GET` | List available voices |
| `/tts` | `POST` | Non-streaming synthesis → MP3 |
| `/ws` | `WebSocket` | Streaming synthesis → MP3 chunks |

---

## Ports and WebSocket

Serve everything on **one port**. HTTP REST and WebSocket share the same host and port — the WebSocket is at path `/ws`. Skrivist always connects to `<server>/ws` on the same host/port you configure; it does **not** derive a second port.

```
https://tts.example.com/voices     ← REST
wss://tts.example.com/ws           ← WebSocket
```

One reverse-proxy rule with standard WebSocket upgrade headers covers both.

If you already run HTTP and WebSocket on separate ports, put a same-port reverse-proxy adapter in front (Nginx, Caddy, or similar) so `/ws` upgrades to the WebSocket backend while the other paths go to HTTP. Point Skrivist at that single public origin — do not expect the app to add 1 to the port.

The current app synthesizes exclusively over WebSocket, so WebSocket upgrades must work through any proxy. `POST /tts` is not used as a fallback.

---

## Authentication

Authentication is optional. If you set an API key on your server, Skrivist passes it in two ways depending on the transport:

| Transport | How the key is sent |
|-----------|-------------------|
| HTTP (`/voices`, `/tts`) | `Authorization: Bearer <key>` header |
| WebSocket (`/ws`) | `?token=<key>` query parameter |

The browser's native WebSocket API does not allow custom headers on the initial handshake, hence the query-parameter fallback.

**`/health` must always be accessible without authentication.** It is used by load balancers, Docker healthchecks, and reverse proxies.

---

## Endpoint Reference

### `GET /health`

Returns server status. Must respond `200 OK` regardless of whether auth is enabled.

**Response:**
```json
{ "status": "ok", "service": "my-tts-server" }
```

The value of `"service"` can be any string — it is not parsed by Skrivist.

---

### `GET /voices`

Returns the voices your server supports. Skrivist calls this on startup and whenever the user opens Settings.

**Request headers (when auth enabled):**
```
Authorization: Bearer <key>
```

**Response `200 OK`:**
```json
[
  {
    "ShortName": "my-voice-en",
    "DisplayName": "My Voice (English)",
    "Locale": "en-US",
    "LocalService": false
  },
  {
    "ShortName": "my-voice-ja",
    "DisplayName": "My Voice (Japanese)",
    "Locale": "ja-JP",
    "LocalService": false
  }
]
```

**Voice object fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ShortName` | string | ✓ | Unique identifier for this voice. Used in all synthesis requests. Alphanumeric, hyphens, underscores. |
| `DisplayName` | string | ✓ | Human-readable label shown in the Skrivist voice picker. |
| `Locale` | string | ✓ | BCP-47 language tag (e.g. `en-US`, `ja-JP`, `fr-FR`). Used for language-matching. |
| `LocalService` | boolean | — | `true` if the voice runs fully offline on the server host. Informational only. Defaults to `false`. |

You can return as many voices as your engine supports. The user may pick one in Settings; when they do, Skrivist sends that voice's `ShortName`. A synthesis request may omit `voice` and send only `language` — in that case pick your default voice for that language.

---

### `POST /tts`

Optional standalone/testing endpoint for non-streaming synthesis (text in, a complete audio file out). **The current Skrivist app does not call this endpoint** and does not fall back to it when WebSocket fails. Keep it if you want curl/CI checks; production reading always uses `/ws`.

**Request headers:**
```
Content-Type: application/json
Authorization: Bearer <key>    (when auth enabled)
```

**Request body:**
```json
{
  "text": "The quick brown fox jumped over the lazy dog.",
  "language": "en",
  "rate": "+0%",
  "pitch": "+0Hz"
}
```

`voice` may be omitted. When only `language` is present, pick the default voice for that language. When both are present, `voice` wins. Tolerate extra optional fields such as `shareable`.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | ✓ | — | UTF-8 text to synthesize. May contain punctuation and whitespace. |
| `voice` | string | — | default for `language` | `ShortName` of the voice to use. Omit to let the server choose. |
| `language` | string | — | — | Content language (BCP-47 or ISO 639-1, e.g. `en`, `ja`). Used to pick a default voice when `voice` is absent. |
| `rate` | string | — | `"+0%"` | Speech rate (see [Rate & Pitch](#rate--pitch-format)). |
| `pitch` | string | — | `"+0Hz"` | Pitch (see [Rate & Pitch](#rate--pitch-format)). |
| `shareable` | boolean | — | — | Optional extra field from the app; ignore if unused. |

**Response `200 OK`:**
```
Content-Type: audio/mpeg
Body: <complete MP3 file>
```

**Error response:**
```json
{ "error": "Human-readable description of what went wrong" }
```

---

### `GET /ws` — WebSocket streaming

The only audio delivery method the current app uses. Skrivist opens **one WebSocket per synthesis request** (typically one sentence or passage), streams binary chunks, reads `{"type":"complete"}`, then closes the connection. It does **not** keep one socket open for a whole reading session.

**Connecting (with auth):**
```
wss://tts.example.com/ws?token=<key>
```

**Connecting (no auth):**
```
wss://tts.example.com/ws
```

#### Client → Server

Send a **text frame** containing a JSON synthesis request:

```json
{
  "text": "In the beginning God created the heavens and the earth.",
  "language": "en",
  "rate": "+0%",
  "pitch": "+0Hz"
}
```

Same fields as `POST /tts`: `voice` may be omitted; `language` is included when known; extra optional fields such as `shareable` must be tolerated. When only `language` is present, pick the default voice for that language; when both `voice` and `language` are present, `voice` wins.

#### Server → Client

Stream the synthesized audio back as **binary frames**, followed by a **text frame** (JSON) signalling completion or error. The client accumulates binary frames until `complete`, then decodes the buffer.

**Binary frame — audio chunk:**
```
<raw audio bytes>
```

Send chunks as soon as they are produced by your synthesis engine. Do not buffer the entire result before sending — streaming is the point.

**Text frame — done:**
```json
{ "type": "complete", "contentType": "audio/mpeg" }
```

`contentType` is optional but recommended (`audio/mpeg`, `audio/ogg`, or `audio/wav`). MP3 is the preferred format. Ogg and WAV are accepted when `complete` carries the matching `contentType` (the client also sniffs the first bytes if the field is missing).

**Text frame — error:**
```json
{ "type": "error", "message": "Synthesis failed: voice not found" }
```

#### Message flow for one synthesis request

```
Client                               Server
  │                                    │
  │── WebSocket connect ───────────────▶│
  │                                    │
  │── { text, language, rate, pitch } ─▶│  (one request; voice optional)
  │                                    │
  │◀── <binary audio chunk> ───────────│
  │◀── <binary audio chunk> ───────────│
  │◀── <binary audio chunk> ───────────│
  │◀── { "type": "complete", ... } ────│
  │                                    │
  │── close ───────────────────────────▶│  (client closes after complete)
```

The next sentence is a **new** WebSocket connection. Your server may still accept multiple sequential requests on one connection (useful for testing), but the app will not reuse the socket.

---

## Audio Format

**MP3 is recommended.** The WebSocket client also accepts Ogg and WAV when the `complete` message includes `contentType` (or when the first bytes sniff as those formats).

| Property | Requirement |
|----------|-------------|
| Container | MP3 (`audio/mpeg`) recommended; Ogg (`audio/ogg`) and WAV (`audio/wav`) accepted |
| Channels | Mono or stereo |
| Sample rate | Any standard rate (22 050, 24 000, 44 100 Hz, etc.) |
| Bit rate | 64–192 kbps recommended for MP3 |

Skrivist decodes audio using the Web Audio API's `decodeAudioData`.

For WebSocket streaming: you do not need to send a complete file. The client accumulates binary frames until `{ "type": "complete" }` and then decodes the buffer. You can safely split at any byte boundary.

---

## Rate & Pitch Format

Both fields use the SSML `<prosody>` string convention:

**Rate (`rate`):**

| Example | Effect |
|---------|--------|
| `"+0%"` | Normal speed |
| `"+25%"` | 25% faster |
| `"-20%"` | 20% slower |

Practical range is roughly `−50%` to `+100%`. If your engine does not support rate control, ignore the field and synthesize at normal speed.

**Pitch (`pitch`):**

| Example | Effect |
|---------|--------|
| `"+0Hz"` | Normal pitch |
| `"+10Hz"` | Higher pitch |
| `"-5Hz"` | Lower pitch |

Practical range is roughly `−50Hz` to `+50Hz`. If your engine does not support pitch control, ignore the field.

---

## CORS

The Skrivist web app runs in the browser and makes cross-origin requests to your server. Your server must return these headers:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
```

Respond to `OPTIONS` preflight requests with `200 OK` and the headers above (no body needed).

---

## HTTP Status Codes

| Code | When to use |
|------|-------------|
| `200` | Success |
| `400` | Bad request — missing required field, unknown voice, malformed JSON |
| `401` | Unauthorized — missing or invalid API key |
| `500` | Server error — synthesis engine crashed or returned nothing |

**WebSocket auth rejection:** close the connection with code `1008` (Policy Violation) and reason `"Unauthorized"`. Do not send a normal close or leave the connection open.

---

## Reverse Proxy

If you expose your server via a domain name (recommended for use with Skrivist on mobile or across devices), you need a reverse proxy that passes WebSocket upgrade requests through.

### Nginx

```nginx
server {
    listen 443 ssl;
    server_name tts.example.com;

    # SSL config here ...

    location / {
        proxy_pass         http://localhost:5053;
        proxy_http_version 1.1;

        # Required for WebSocket upgrade
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";

        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
    }
}
```

### Caddy

```caddy
tts.example.com {
    reverse_proxy localhost:5053
}
```

Caddy handles WebSocket upgrades automatically.

### Synology (DSM Reverse Proxy)

1. **Control Panel → Login Portal → Advanced → Reverse Proxy → Create**
2. Source: `HTTPS`, your hostname, port `443`
3. Destination: `HTTP`, `localhost`, your server port
4. **Custom Header tab → Create → WebSocket** (adds `Upgrade` and `Connection` headers)
5. Save

In Skrivist Settings, enter just the hostname (e.g. `tts.example.com`) — no port. HTTPS on port 443 is implied.

---

## Configuring in Skrivist

1. Open Skrivist → **Settings** (gear icon in the header)
2. Expand **Custom TTS Server**
3. Enter your **Server URL**:
   - Local: `localhost:5053`
   - Remote: `tts.example.com` (HTTPS assumed when no port is given)
   - With explicit port: `tts.example.com:8443`
4. Enter your **API Key** if your server requires one
5. Click **Save**
6. Open any book → open the **Audio** settings panel → your voices should appear

---

## Testing with curl

```bash
BASE=https://tts.example.com
KEY=mysecret    # omit -H Authorization line if no auth

# 1. Health check
curl $BASE/health

# 2. List voices
curl -H "Authorization: Bearer $KEY" $BASE/voices

# 3. Synthesize to file
curl -X POST $BASE/tts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, world!", "voice": "my-voice-en"}' \
  --output test.mp3

# 4. Play
afplay test.mp3   # macOS
mpv    test.mp3   # Linux
```

---

## Minimal server example (Python / aiohttp)

A bare-bones server skeleton to get you started. Replace the `synthesize()` function with your chosen TTS engine.

```python
import asyncio, json, io
from aiohttp import web, WSMsgType

API_KEY = "changeme"   # set to None to disable auth

# ── Your synthesis engine goes here ──────────────────────────────────────────
async def synthesize(text: str, voice: str, rate: str, pitch: str,
                     language: str = "") -> bytes:
    """Return MP3 audio bytes for the given text.

    `voice` may be empty; if so, pick a default for `language`.
    """
    raise NotImplementedError("Plug in your TTS engine here")
# ─────────────────────────────────────────────────────────────────────────────

async def handle_health(req):
    return web.json_response({"status": "ok", "service": "my-tts-server"})

async def handle_voices(req):
    voices = [
        {"ShortName": "my-voice-en", "DisplayName": "My Voice (English)",
         "Locale": "en-US", "LocalService": False},
        {"ShortName": "my-voice-ja", "DisplayName": "My Voice (Japanese)",
         "Locale": "ja-JP", "LocalService": False},
    ]
    return web.json_response(voices)

DEFAULT_VOICES = {"en": "my-voice-en", "ja": "my-voice-ja"}

def pick_voice(data):
    # voice wins when both are present; language-only → default for that language
    voice = data.get("voice")
    if voice:
        return voice
    lang = (data.get("language") or "").lower()
    primary = lang.split("-")[0]
    return DEFAULT_VOICES.get(primary)

async def handle_tts(req):
    data = await req.json()
    voice = pick_voice(data)
    if not voice:
        return web.json_response({"error": "unsupported language"}, status=400)
    audio = await synthesize(
        data["text"], voice,
        data.get("rate", "+0%"), data.get("pitch", "+0Hz"),
        data.get("language") or "",
    )
    return web.Response(body=audio, content_type="audio/mpeg")

async def handle_ws(req):
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    if API_KEY and req.rel_url.query.get("token") != API_KEY:
        # Per spec: reject by closing with code 1008 and reason "Unauthorized"
        await ws.close(code=1008, message=b"Unauthorized")
        return ws
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            voice = pick_voice(data)
            if not voice:
                await ws.send_str(json.dumps({
                    "type": "error",
                    "message": "unsupported language",
                }))
                continue
            audio = await synthesize(
                data["text"], voice,
                data.get("rate", "+0%"), data.get("pitch", "+0Hz"),
                data.get("language") or "",
            )
            await ws.send_bytes(audio)
            await ws.send_str(json.dumps({
                "type": "complete",
                "contentType": "audio/mpeg",
            }))
    return ws

@web.middleware
async def auth(req, handler):
    if API_KEY and req.method != "OPTIONS" and req.path not in ("/health", "/ws"):
        if req.headers.get("Authorization") != f"Bearer {API_KEY}":
            return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(req)

@web.middleware
async def cors(req, handler):
    resp = web.Response() if req.method == "OPTIONS" else await handler(req)
    resp.headers.update({
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    })
    return resp

# CORS must wrap auth (listed first = outermost) so a 401 from auth still
# includes Access-Control-Allow-* headers. Browsers discard opaque 401s
# that lack CORS, which makes the failure look like a network error.
app = web.Application(middlewares=[cors, auth])
app.router.add_get("/health", handle_health)
app.router.add_get("/voices",  handle_voices)
app.router.add_post("/tts",    handle_tts)
app.router.add_get("/ws",      handle_ws)
app.router.add_options("/{path:.*}", lambda r: web.Response())

if __name__ == "__main__":
    web.run_app(app, port=5053)
```

Install: `pip install aiohttp`
Run: `python server.py`
Configure Skrivist: `localhost:5053`

---

## Checklist

Before pointing Skrivist at your server, verify:

- [ ] `GET /health` returns `200` without auth
- [ ] `GET /voices` returns a JSON array with at least one voice
- [ ] Each voice has `ShortName`, `DisplayName`, and `Locale`
- [ ] `POST /tts` (optional testing endpoint) returns `Content-Type: audio/mpeg` binary
- [ ] WebSocket at `/ws` streams binary chunks then `{ "type": "complete" }` (optionally with `contentType`); the app opens one connection per request and closes after complete
- [ ] Synthesis requests may omit `voice` and include `language`; extra fields such as `shareable` are ignored safely
- [ ] CORS headers are present on all responses
- [ ] `OPTIONS` preflight requests return `200`
- [ ] If using auth: `/health` is exempt, HTTP uses `Authorization: Bearer`, WS uses `?token=`
- [ ] Reverse proxy passes `Upgrade`/`Connection` headers (if applicable)
