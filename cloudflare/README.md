# nova-cf — ESP32 voice assistant bridge on Cloudflare (Gemini Live)

Port of `~/IoT/nova-ai-adapted/app.py` (FastAPI + google-genai Python SDK) to a
Cloudflare Worker + Durable Object, speaking the Gemini Live API **raw WebSocket
JSON protocol** (no SDK available in Workers). The ESP32 (MAX98357A +
push-to-talk mic) now needs **no local machine on the LAN**.

Deployed URL: `https://nova-esp32.chikip.workers.dev` (Workers.dev, FREE plan)

## Files

| Path | Role |
| --- | --- |
| `src/index.ts` | Worker: routes `/esp32` WS upgrade to a fresh Durable Object instance (one per session) |
| `src/nova-do.ts` | `NovaBridge` Durable Object: holds the ESP WS + the outbound Gemini Live WS, gates audio, paces it, maps tool calls |
| `test/client.mjs` | Headless verification client (no ESP hardware needed) |
| `test/probe-gemini.mjs` | Direct Gemini-WS probe from a normal client; use to test API key/model/region outside Cloudflare |
| `wrangler.toml` | Config; Durable Object binding + SQLite migration (free-plan requirement) |

## Architecture

```
ESP32  --wss:443-->  Worker fetch  --WS upgrade-->  Durable Object (per session)
                                                    |  acceptWebSocket (Hibernation API)
                                                    |  outbound WSS --> generativelanguage.googleapis.com
                                                    |     BidiGenerateContent?key=<GEMINI_API_KEY>
                                                    `-- pacer loop 2048 B chunks @ 72000 B/s ~1.5x realtime
```

- **Session DO**: `idFromName(crypto.randomUUID())` per `/esp32` connection. A
  fresh object also re-rolls the Cloudflare egress IP (see "Gemini geo-block"
  below).
- **Protocol contract (identical to app.py)**

  ESP32 -> server:
  - binary frame = raw PCM 16-bit signed LE mono 16000 Hz (mic, push-to-talk)
  - text frame `PTT_END` = end of speech turn

  server -> ESP32:
  - binary frame = raw PCM 16-bit LE mono 24000 Hz (Gemini audio), paced in
    2048-byte chunks at `len/72000.0` s per chunk (~1.5x realtime, ESP ring
    buffer never starves)
  - text `IDLE`  : turn completed with no transcribable user speech
  - text `RGB_ON|RGB_OFF|RGB_RED|RGB_GREEN|RGB_BLUE` (tool calls)
  - text `SLEEP` (model ends session: tool `end_chat_session` or farewell text)

- **Gemini Live config** (all confirmed against the official schema,
  https://ai.google.dev/api/live):

  ```jsonc
  first message = {
    "setup": {
      "model": "models/gemini-3.8-live",            // env NOVA_MODEL
      "generationConfig": {
        "responseModalities": ["AUDIO"],            // MUST live under generationConfig
        "speechConfig": { "voiceConfig": { "prebuiltVoiceConfig": { "voiceName": "Kore" } } } // env NOVA_VOICE
      },
      // top-level responseModalities/speechConfig are REJECTED by the raw WS
      // v1beta schema ("Unknown name at 'setup': Cannot find field")
      "systemInstruction": { "parts": [{ "text": "<Indonesian short-answer prompt>" }] },
      "tools": [{ "functionDeclarations": [ end_chat_session, rgb_red/blue/green/on/off ] }],
      "inputAudioTranscription": {},
      "realtimeInputConfig": { "automaticActivityDetection": { "disabled": true } } // MANUAL VAD
    }
  }
  mic chunk    -> {"realtimeInput": {"audio": {"mimeType": "audio/pcm;rate=16000", "data": "<b64>"}}}
  first chunk  -> {"realtimeInput": {"activityStart": {}}}
  PTT_END      -> {"realtimeInput": {"activityEnd": {}}}
  reply to tool-> {"toolResponse": ...}   (NOT sent; app.py never sends rgb_* responses either)
  server msgs  : setupComplete, serverContent{interrupted | inputTranscription.text |
                 modelTurn.parts[{text, inlineData.data=b64 24kHz PCM}], turnComplete},
                 toolCall.functionCalls[{id,name,args}], goAway
  ```

- **Endpoints**: `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=API_KEY`
  (protocol docs: https://ai.google.dev/api/live and
  https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket)

## Deploy (from the proot Debian container)

```bash
proot-distro login debian-ex -- bash -c 'cd /root/nova-cf && \
  GEMINI_API_KEY=$(grep -m1 "^GEMINI_API_KEY=" /data/data/com.termux/files/home/IoT/nova-ai-adapted/.env | cut -d= -f2) \
  npx wrangler secret put GEMINI_API_KEY && npx wrangler deploy'
```

- Secret is used interactively via stdin; never hardcode/echo it.
- `NOVA_MODEL` / `NOVA_VOICE` env vars are optional (`gemini-3.8-live`, `Kore`).

## Verify without the ESP hardware

```bash
# inside proot: node 24 has a global WebSocket client
cd /root/nova-cf
node test/client.mjs  # wss://nova-esp32.chikip.workers.dev/esp32?debug=1
```

Observed PASS output (2026-10-17):

```
<- text: "BRIDGE_READY"      (Gemini WS established + setupComplete)
<- binary 2048 B ...         (debug "SAY:" turn -> model audio out, 2048 B paced chunks)
<- text: "IDLE" (+8000ms)    (turn completed, no transcribable user speech)
RESULT: PASS
```

Phases: (1) `?debug=1` sends text `BRIDGE_READY` after Gemini ACKs the setup.
(2) three 4800-B silence frames then `PTT_END` -> manual-VAD activity
start/round trip. (3) `SAY: halo apa kabar` -> `clientContent` turn (debug
stations only; the normal ESP gate `heardSpeech` still gates model audio for
real hardware, bypassed here so the audio path is observable).

## Firmware change (ESP32, links2004 WebSocketsClient)

```c
// BEFORE: webSocket.begin("192.168.1.22", 8000, "/esp32");
// AFTER:
webSocket.beginSSL("nova-esp32.chikip.workers.dev", 443, "/esp32");
webSocket.setReconnectInterval(5000);
```

No other protocol changes: the ESP keeps sending 16 kHz PCM binary frames +
`PTT_END`, and receiving 24 kHz PCM / `IDLE` / `RGB_*` / `SLEEP`.

## Free-plan limits and gotchas

- **No credit card needed.** Durable Objects are available on Workers Free
  (SQLite backend only). Free quotas: 100k DO requests/day (WS incoming count
  at 20:1), 13,000 GB-s duration/day, 5 GB SQLite storage.
  https://developers.cloudflare.com/durable-objects/platform/pricing/
- **WebSocket hibernation is only partially applicable**: the inbound (ESP)
  socket uses the Hibernation API (`acceptWebSocket`), but the session also
  holds an *outbound* WS to Gemini — outbound sockets never hibernate (docs:
  https://developers.cloudflare.com/durable-objects/best-practices/websockets/
  "Outgoing WebSockets do not hibernate"), so a DO with a live Gemini bridge
  stays in memory and accrues duration for as long as the session is open.
  This is why the DO runs a 10-minute idle sentinel closing both sockets. At
  128 MB (0.125 GB) per DO, the 13,000 GB-s/day budget is ~104,000 session
  seconds/day per account.
- **Local `wrangler dev` does NOT work on this device** (workerd's tcmalloc
  requests >47-bit virtual addresses; Android/proot only offers 39-bit). All
  verification has to run against the deployed worker.
- **Gemini geo-block (the big one)**: Google rejects some Cloudflare egress
  IPs with `"User location is not supported for the API use."` (HTTP 400 /
  WS close 1007) — egress IP selection is nondeterministic at Cloudflare. The
  DO therefore retries the Gemini connection up to 4 times (~300 ms delay);
  if all attempts are rejected the ESP handshake fails (HTTP 502) and the ESP
  simply reconnects. Fresh session DOs have had a high success rate.
- **Live API free-tier quotas**: opening sessions in tight loops can hit
  `"You exceeded your current quota, ple..."` (close 1011) — pace reconnects.
- **Session limits**: the Live API closes long sessions (it sends `goAway`).
  The bridge does NOT auto-resume (`sessionResumption` is unused in app.py
  either); the ESP reconnects after the close.
- The debug-only `?debug=1` parameter adds the text frames `BRIDGE_READY`
  (server->ESP) and `SAY:...` (ESP->server); a real ESP never sends those and
  never receives `BRIDGE_READY` — the protocol contract is unchanged.

## Ops cheatsheet

```bash
npx wrangler tail                                   # live logs
npx wrangler secret put GEMINI_API_KEY              # rotate key
npx wrangler deploy                                 # re-deploy (drops live sessions)
node test/probe-gemini.mjs                          # direct Gemini sanity check (uses .env key, no printing)
```
