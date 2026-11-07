import { IncomingMessage, Server as HttpServer } from "http";
import { WebSocket, WebSocketServer } from "ws";
import { WsMessage } from "../../contracts";
import { EventRingBuffer } from "./ringBuffer";

/**
 * Manages WebSocket connections at `ws://localhost:3000/ws`.
 * Pushes initial status on connection and broadcasts real-time updates to all Electron HUD clients.
 */
export class WsHub {
  private wss: WebSocketServer;
  private clients = new Set<WebSocket>();

  constructor(server: HttpServer, private readonly store: EventRingBuffer) {
    this.wss = new WebSocketServer({ server, path: "/ws" });

    this.wss.on("connection", (ws: WebSocket, _req: IncomingMessage) => {
      this.clients.add(ws);

      // Immediately send current status snapshot to newly connected UI client
      const snap = this.store.getSnapshot();
      this.sendTo(ws, {
        type: "status",
        timestamp: Date.now(),
        faceState: snap.faceState,
        confusionScore: snap.confusionScore,
        cooldownUntil: snap.cooldownUntil,
        cameraPaused: snap.cameraPaused,
        eventCount: snap.eventCount,
      });

      ws.on("close", () => {
        this.clients.delete(ws);
      });

      ws.on("error", () => {
        this.clients.delete(ws);
      });
    });
  }

  private sendTo(ws: WebSocket, message: WsMessage): void {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
    }
  }

  public broadcast(message: WsMessage): void {
    const payload = JSON.stringify(message);
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(payload);
      }
    }
  }

  public broadcastStatus(): void {
    const snap = this.store.getSnapshot();
    this.broadcast({
      type: "status",
      timestamp: Date.now(),
      faceState: snap.faceState,
      confusionScore: snap.confusionScore,
      cooldownUntil: snap.cooldownUntil,
      cameraPaused: snap.cameraPaused,
      eventCount: snap.eventCount,
    });
  }

  public close(): Promise<void> {
    return new Promise((resolve) => {
      for (const client of this.clients) {
        client.terminate();
      }
      this.clients.clear();
      this.wss.close(() => resolve());
    });
  }
}
