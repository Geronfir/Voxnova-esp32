import { DurableObject } from "cloudflare:workers";
import type { Env } from "./index";

const SYSTEM_INSTRUCTION =
  "Kamu asisten suara berbahasa Indonesia. " +
  "Jawab MAKSIMAL 1 kalimat pendek. " +
  "Matematika: jawab ANGKA saja. " +
  "Fakta: jawab langsung, tanpa pembuka/penutup. " +
  "DILARANG: 'tentu', 'baik', 'saya akan', 'ada yang bisa dibantu'.";

const TOOLS = [
  {
    functionDeclarations: [
      { name: "end_chat_session", description: "End the conversation after saying goodbye." },
      { name: "rgb_red", description: "Turn the room lights red when the user asks for red lighting." },
      { name: "rgb_blue", description: "Turn the room lights blue when the user asks for blue lighting." },
      { name: "rgb_green", description: "Turn the room lights green when the user asks for green lighting." },
      { name: "rgb_on", description: "Turn the RGB room lights on." },
      { name: "rgb_off", description: "Turn the RGB room lights off." },
    ],
  },
];

const CHUNK_SIZE = 2048;
const PACER_BPS = 72000;
const IDLE_TIMEOUT_MS = 600000;
const CONNECT_ATTEMPTS = 4;

const GEMINI_HOST =
  "https://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";

function b64encode(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000) as unknown as number[]);
  }
  return btoa(s);
}

function b64decode(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface FunctionCall {
  id?: string;
  name: string;
  args?: Record<string, unknown>;
}

export class NovaBridge extends DurableObject {
  protected env: Env;
  private esp: WebSocket | null = null;
  private gemini: WebSocket | null = null;

  private alive = true;
  private debug = false;
  private speechOpen = false;
  private heardSpeech = false;
  private sessionClosing = false;
  private responseBuffer = "";
  private queue: Uint8Array[] = [];
  private clearFlag = false;
  private setupResolver: ((ok: boolean) => void) | null = null;
  private lastActivity = Date.now();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    this.debug = new URL(request.url).searchParams.get("debug") === "1";

    let geminiWs: WebSocket;
    try {
      geminiWs = await this.connectGemini();
    } catch (e) {
      this.log("gemini connect failed:", e);
      return new Response(`Gemini bridge failed: ${e}`, { status: 502 });
    }
    this.gemini = geminiWs;

    const pair = new WebSocketPair();
    this.esp = pair[1];
    this.ctx.acceptWebSocket(pair[1]);

    void this.pacerLoop();
    void this.idleWatchLoop();

    if (this.debug) this.sendText("BRIDGE_READY");
    this.log(`session started (debug=${this.debug})`);

    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  private async connectGemini(): Promise<WebSocket> {
    let lastErr: unknown;
    for (let attempt = 1; attempt <= CONNECT_ATTEMPTS; attempt++) {
      try {
        return await this.openGeminiOnce(attempt);
      } catch (e) {
        lastErr = e;
        this.log(`connect attempt ${attempt}/${CONNECT_ATTEMPTS} failed:`, String(e));
        await sleep(300);
      }
    }
    throw lastErr;
  }

  private async openGeminiOnce(attempt: number): Promise<WebSocket | never> {
    const key = this.env.GEMINI_API_KEY;
    if (!key) throw new Error("GEMINI_API_KEY secret not set");

    const model = this.env.NOVA_MODEL || "gemini-3.8-live";
    const voice = this.env.NOVA_VOICE || "Kore";

    const speechVoice = {
      voiceConfig: { prebuiltVoiceConfig: { voiceName: voice } },
    };

    const setup = {
      setup: {
        model: `models/${model}`,
        generationConfig: { responseModalities: ["AUDIO"], speechConfig: speechVoice },
        systemInstruction: { parts: [{ text: SYSTEM_INSTRUCTION }] },
        tools: TOOLS,
        inputAudioTranscription: {},
        realtimeInputConfig: { automaticActivityDetection: { disabled: true } },
      },
    };

    let resp: Response;
    try {
      resp = await fetch(`${GEMINI_HOST}?key=${encodeURIComponent(key)}`, {
        headers: { Upgrade: "websocket" },
      });
    } catch (e) {
      throw new Error(`attempt ${attempt}: mirror fetch threw ${e}`);
    }
    if (resp.status !== 101 || !resp.webSocket) {
      throw new Error(`attempt ${attempt}: Gemini WS upgrade failed: HTTP ${resp.status} ${(await resp.text()).slice(0, 160)}`);
    }

    const ws: WebSocket = resp.webSocket;
    ws.binaryType = "arraybuffer";

    let ackedOk = false;

    const closeHandler = (ev: any) => {
      this.log(`gemini closed (code=${ev.code})`);
      this.setupResolver?.(false);
      if (ackedOk && !this.sessionClosing && this.alive) {
        this.alive = false;
        this.hangupESP(1011, "gemini closed");
      }
    };

    const errorHandler = (ev: any) => this.log("gemini ws error:", String(ev.message || ev));

    try {
      ws.accept();
      ws.send(JSON.stringify(setup));
      ws.addEventListener("message", (ev) => this.onGeminiMessage(ev.data, closeHandler, errorHandler));
      ws.addEventListener("close", closeHandler);
      ws.addEventListener("error", errorHandler);

      const acked = await Promise.race([
        new Promise<boolean>((resolve) => (this.setupResolver = resolve)),
        sleep(8000).then(() => false),
      ]);
      this.setupResolver = null;
      if (!acked) {
        ackedOk = false;
        throw new Error(`attempt ${attempt}: no setupComplete from Gemini within 8s`);
      }
      ackedOk = true;
      this.log(`bridged to Gemini (model=${model}, voice=${voice}, attempt=${attempt})`);
      return ws;
    } catch (e) {
      this.setupResolver = null;
      try {
        ws.close(1000, "connect retry");
      } catch {
        /* ignore */
      }
      throw e;
    }
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    if (!this.alive || ws !== this.esp) return;
    this.lastActivity = Date.now();

    if (typeof message === "string") {
      if (message === "PTT_END") {
        if (this.speechOpen) {
          this.speechOpen = false;
          this.geminiSend({ realtimeInput: { activityEnd: {} } });
          this.log("PTT_END -> activity_end (akhir giliran)");
        }
        return;
      }
      if (this.debug && message.startsWith("SAY:")) {
        const text = message.slice(4);
        this.geminiSend({
          clientContent: {
            turns: [{ role: "user", parts: [{ text }] }],
            turnComplete: true,
          },
        });
        return;
      }
      return;
    }

    const bytes = message instanceof ArrayBuffer ? new Uint8Array(message) : null;
    if (!bytes || bytes.length === 0) return;

    if (!this.speechOpen) {
      this.speechOpen = true;
      this.geminiSend({ realtimeInput: { activityStart: {} } });
      this.log("audio pertama -> activity_start (mulai giliran)");
    }
    this.geminiSend({
      realtimeInput: { audio: { mimeType: "audio/pcm;rate=16000", data: b64encode(bytes) } },
    });
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    if (ws !== this.esp) return;
    this.log(`ESP disconnect (${code}) ${reason}`);
    this.alive = false;
    this.hangupGemini();
  }

  private onGeminiMessage(
    data: ArrayBuffer | string,
    closeHandler: (ev: any) => void,
    errorHandler: (ev: any) => void
  ) {
    let msg: any;
    try {
      msg = JSON.parse(typeof data === "string" ? data : new TextDecoder().decode(data));
    } catch {
      return;
    }

    try {
      if (msg.setupComplete) {
        this.setupResolver?.(true);
        return;
      }

      const sc = msg.serverContent;
      if (sc) {
        if (sc.interrupted) {
          this.log("interrupted! silencing the speaker...");
          this.clearFlag = true;
        }

        const itText = sc.inputTranscription?.text;
        if (typeof itText === "string" && itText.trim()) {
          if (!this.heardSpeech) this.log(`suara ditranskrip: '${itText.trim()}'`);
          this.heardSpeech = true;
        }

        const turn = sc.modelTurn;
        if (turn?.parts) {
          for (const part of turn.parts) {
            if (part.text) {
              this.responseBuffer += part.text;
              const lower = this.responseBuffer.toLowerCase();
              if (
                !this.sessionClosing &&
                (lower.includes(" bye") || lower.trim() === "bye" || lower.includes("goodbye"))
              ) {
                this.log("fallback farewell detector triggered.");
                void this.endSession();
                return;
              }
            }
            if (part.inlineData?.data && (this.debug || this.heardSpeech)) {
              this.queue.push(b64decode(part.inlineData.data));
            }
          }
        }

        if (sc.turnComplete) {
          if (!this.heardSpeech) {
            this.log("tak ada suara ditranskrip -> kirim IDLE (batal berpikir).");
            this.clearFlag = true;
            this.sendText("IDLE");
          }
          this.heardSpeech = false;
          this.responseBuffer = "";
        }
      }

      if (msg.toolCall?.functionCalls) {
        for (const fc of msg.toolCall.functionCalls as FunctionCall[]) {
          this.log("tool call:", fc.name, fc.id ?? "");
          if (fc.name === "end_chat_session") {
            void this.endSession();
          } else if (fc.name.startsWith("rgb_")) {
            this.sendText("RGB_" + fc.name.slice(4).toUpperCase());
          }
        }
      }
    } catch (e) {
      this.log("gemini receive crashed:", e);
      this.alive = false;
      this.hangupESP(1011, "gemini receive error");
    }
  }

  private async pacerLoop(): Promise<void> {
    while (this.alive) {
      if (this.clearFlag) {
        this.queue.length = 0;
        this.clearFlag = false;
      }
      const next = this.queue.shift();
      if (!next) {
        await sleep(100);
        continue;
      }
      for (let i = 0; i < next.length; i += CHUNK_SIZE) {
        if (this.clearFlag || !this.alive) break;
        const chunk = next.subarray(i, i + CHUNK_SIZE);
        try {
          this.esp?.send(chunk);
        } catch {
          this.alive = false;
          return;
        }
        await sleep((chunk.length / PACER_BPS) * 1000);
      }
    }
  }

  private async idleWatchLoop(): Promise<void> {
    while (this.alive) {
      await sleep(60000);
      if (!this.alive) return;
      const busy = this.queue.length > 0 || this.speechOpen || Date.now() - this.lastActivity < IDLE_TIMEOUT_MS;
      if (!busy) {
        this.log("idle timeout -> closing session");
        this.alive = false;
        this.hangupGemini();
        this.hangupESP(1001, "idle timeout");
        return;
      }
    }
  }

  private async endSession(): Promise<void> {
    if (this.sessionClosing) return;
    this.sessionClosing = true;
    this.log("ending session gracefully...");
    await sleep(2000);
    this.sendText("SLEEP");
    await sleep(500);
    this.alive = false;
    this.hangupGemini();
    this.hangupESP(1000, "session end");
  }

  private geminiSend(obj: unknown) {
    try {
      this.gemini?.send(JSON.stringify(obj));
    } catch (e) {
      this.log("gemini send failed:", e);
      this.alive = false;
      this.hangupESP(1011, "gemini send failed");
    }
  }

  private sendText(text: string) {
    try {
      this.esp?.send(text);
    } catch {
      this.alive = false;
    }
  }

  private hangupGemini() {
    try {
      this.gemini?.close(1000, "closing");
    } catch {
      /* already closed */
    }
  }

  private hangupESP(code: number, reason: string) {
    try {
      this.esp?.close(code, reason);
    } catch {
      /* already closed */
    }
  }

  private log(...args: unknown[]) {
    console.log("[nova-do]", ...args);
  }
}
