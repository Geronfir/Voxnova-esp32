import { NovaBridge } from "./nova-do";

export interface Env {
  GEMINI_API_KEY?: string;
  NOVA_MODEL?: string;
  NOVA_VOICE?: string;
  NOVA: DurableObjectNamespace;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/esp32") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("Expected WebSocket upgrade", { status: 426 });
      }
      const sessionId = crypto.randomUUID();
      const stub = env.NOVA.get(env.NOVA.idFromName(sessionId));
      return stub.fetch(request);
    }

    if (url.pathname === "/") {
      return new Response(JSON.stringify({ ok: true, service: "nova-esp32-bridge" }), {
        headers: { "content-type": "application/json" },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};

export { NovaBridge };
