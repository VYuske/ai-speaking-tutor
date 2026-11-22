import type { ServerEvent } from "../types";

export type SocketStatus = "connecting" | "open" | "closed";

interface Handlers {
  onEvent: (event: ServerEvent) => void;
  onStatus: (status: SocketStatus) => void;
  onOpen?: () => void;
}

export class TutorSocket {
  private ws: WebSocket | null = null;

  constructor(private readonly handlers: Handlers) {}

  connect(): void {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);
    this.ws = ws;
    this.handlers.onStatus("connecting");

    ws.onopen = () => {
      this.handlers.onStatus("open");
      this.handlers.onOpen?.();
    };
    ws.onclose = () => this.handlers.onStatus("closed");
    ws.onerror = () => this.handlers.onStatus("closed");
    ws.onmessage = (message) => {
      if (typeof message.data !== "string") return;
      try {
        this.handlers.onEvent(JSON.parse(message.data) as ServerEvent);
      } catch {
        // Ignore malformed frames; the server is the only writer.
      }
    };
  }

  /**
   * Sends a JSON frame. Returns `false` when the socket is not open, so callers
   * never lose input silently: the caller shows the toast and keeps the draft.
   */
  send(payload: Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(payload));
    return true;
  }

  /** Sends raw audio; same boolean contract as `send`. */
  sendBytes(buffer: ArrayBuffer): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(buffer);
    return true;
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
  }
}
