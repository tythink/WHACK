/**
 * ============================================================================
 * PLUG-AND-PLAY BRAIN & CORE ADAPTER (`brainAdapter.ts`)
 * ============================================================================
 * This is the SINGLE file connecting the VS Code Mode UI to:
 *   1. Role 3's Brain LLM Server (`http://127.0.0.1:5002/diagnose` & `/chat`)
 *   2. Role 4's Core Event/WS Server (`http://127.0.0.1:3000` & `ws://127.0.0.1:3000/ws`)
 *   3. Role 1+2's Face & Signals streams (`POST :3000/events` & `signals/latest_brain_call.json`)
 *
 * Today (before Brain is finished):
 *   - Automatically runs an embedded `:3000` receiver if Core isn't running externally.
 *   - Uses a built-in Mock Brain fallback for `/diagnose` and right-side `/chat`.
 *
 * When your teammate finishes Brain (`:5002`):
 *   - Zero UI changes needed! As soon as `:5002` responds or broadcasts a `"brain"`
 *     diagnosis event to `:3000/events`, `normalizeBrainDiagnosis()` adapts it and
 *     triggers editor line highlighting + the right-side panel automatically.
 * ============================================================================
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as http from "http";
import * as net from "net";
import * as path from "path";
import {
  ChatMessage,
  CodeCitation,
  DiagnosisResponse,
  HintLevel,
  PassiveStatusSnapshot,
  SuggestedFix,
} from "./contracts";
import { buildSimulatedDiagnosis } from "./simulatorScenarios";

/**
 * Adapts ANY reasonable JSON shape returned by Role 3's Brain LLM into our strict `DiagnosisResponse`.
 * If your teammate renames fields later (e.g. `file_path`, `line_range`, `locations`), map them right here!
 */
export function normalizeBrainDiagnosis(rawInput: unknown): DiagnosisResponse {
  const raw = (rawInput && typeof rawInput === "object" ? rawInput : {}) as Record<
    string,
    any
  >;

  const file: string | undefined =
    raw.file || raw.file_path || raw.filepath || raw.targetFile || undefined;

  let lines: [number, number] | undefined;
  const rawLines = raw.lines || raw.line_range || raw.lineRange;
  if (Array.isArray(rawLines) && rawLines.length >= 2) {
    lines = [Number(rawLines[0]) || 1, Number(rawLines[1]) || Number(rawLines[0]) || 1];
  } else if (typeof raw.start_line === "number") {
    lines = [raw.start_line, Number(raw.end_line ?? raw.start_line)];
  } else if (typeof raw.line === "number") {
    lines = [raw.line, raw.line];
  }

  const rawCitations =
    raw.citations || raw.locations || raw.places_to_correct || [];
  const citations: CodeCitation[] = Array.isArray(rawCitations)
    ? rawCitations
        .map((c: any): CodeCitation | null => {
          if (!c || typeof c !== "object") return null;
          const cFile = c.file || c.file_path || file || "utils.py";
          const cLines: [number, number] =
            Array.isArray(c.lines) && c.lines.length >= 2
              ? [Number(c.lines[0]) || 1, Number(c.lines[1]) || 1]
              : typeof c.line === "number"
              ? [c.line, c.line]
              : lines || [1, 1];
          return {
            file: String(cFile),
            lines: cLines,
            snippet: typeof c.snippet === "string" ? c.snippet : undefined,
            note:
              typeof c.note === "string"
                ? c.note
                : typeof c.message === "string"
                ? c.message
                : undefined,
            replacement:
              typeof c.replacement === "string" ? c.replacement : undefined,
          };
        })
        .filter((x): x is CodeCitation => x !== null)
    : [];

  if (citations.length === 0 && file && lines) {
    citations.push({
      file,
      lines,
      note: typeof raw.summary === "string" ? raw.summary : "Identified target lines",
    });
  }

  let suggestedFix: SuggestedFix | undefined;
  const rawFix = raw.suggestedFix || raw.suggested_fix || raw.structuredEdit || raw.fix;
  if (rawFix && typeof rawFix === "object") {
    const rep =
      typeof rawFix.replacement === "string"
        ? rawFix.replacement
        : typeof rawFix.newText === "string"
        ? rawFix.newText
        : typeof rawFix.new_text === "string"
        ? rawFix.new_text
        : undefined;
    if (typeof rep === "string") {
      const fStart = rawFix.startLine ?? rawFix.start_line;
      const fEnd = rawFix.endLine ?? rawFix.end_line ?? fStart;
      const fixLines: [number, number] | undefined = Array.isArray(rawFix.lines)
        ? [Number(rawFix.lines[0]), Number(rawFix.lines[1])]
        : typeof fStart === "number"
        ? [Number(fStart), Number(fEnd)]
        : lines;
      const findStr =
        typeof rawFix.find === "string"
          ? rawFix.find
          : typeof rawFix.oldText === "string"
          ? rawFix.oldText
          : typeof rawFix.old_text === "string"
          ? rawFix.old_text
          : undefined;

      suggestedFix = {
        file: rawFix.file || file,
        lines: fixLines,
        startLine: fixLines?.[0],
        endLine: fixLines?.[1],
        find: findStr,
        oldText: findStr,
        replacement: rep,
        newText: rep,
      };
    }
  } else if (typeof raw.replacement === "string") {
    suggestedFix = { file, lines, replacement: raw.replacement };
  }

  const rawLevel = raw.hintLevel || raw.hint_level;
  const hintLevel: HintLevel =
    rawLevel === "nudge" || rawLevel === "hint" || rawLevel === "full_explanation"
      ? rawLevel
      : rawLevel === "full"
      ? "full_explanation"
      : "hint";

  return {
    summary:
      typeof raw.summary === "string" && raw.summary.trim()
        ? raw.summary
        : "WHACK Brain identified a code region to inspect.",
    file: file || citations[0]?.file,
    lines: lines || citations[0]?.lines,
    quip:
      typeof raw.quip === "string" && raw.quip.trim()
        ? raw.quip
        : "Computers do what you say, not what you want.",
    uncertain: Boolean(raw.uncertain),
    hintLevel,
    reasons: Array.isArray(raw.reasons) ? raw.reasons.map(String) : undefined,
    confidence: typeof raw.confidence === "number" ? raw.confidence : 0.95,
    explanation:
      typeof raw.explanation === "string" ? raw.explanation : undefined,
    citations,
    suggestedFix,
    tierVariants: raw.tierVariants,
  };
}

/**
 * Zero-dependency RFC-6455 WebSocket client using Node's built-in `http`/`net`/`crypto`
 * so the extension works out-of-the-box without requiring native `node_modules`.
 */
class LightweightWsClient {
  private socket: net.Socket | null = null;
  private closedByApp = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private rxBuffer = Buffer.alloc(0);

  constructor(
    private readonly wsUrl: string,
    private readonly onMessage: (msg: any) => void,
    private readonly onDisconnected: () => void
  ) {}

  public connect(): void {
    this.closedByApp = false;
    let parsed: URL;
    try {
      parsed = new URL(this.wsUrl);
    } catch {
      return;
    }

    const key = crypto.randomBytes(16).toString("base64");
    const req = http.request({
      hostname: parsed.hostname || "127.0.0.1",
      port: Number(parsed.port || 3000),
      path: parsed.pathname || "/ws",
      method: "GET",
      headers: {
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": key,
      },
    });

    req.on("upgrade", (_res, socket) => {
      this.socket = socket;
      this.rxBuffer = Buffer.alloc(0);

      socket.on("data", (chunk: Buffer) => {
        this.rxBuffer = Buffer.concat([this.rxBuffer, chunk]);
        this.drainFrames();
      });

      socket.on("close", () => {
        this.socket = null;
        if (!this.closedByApp) {
          this.onDisconnected();
          this.scheduleReconnect();
        }
      });

      socket.on("error", () => {
        socket.destroy();
      });
    });

    req.on("error", () => {
      if (!this.closedByApp) {
        this.onDisconnected();
        this.scheduleReconnect();
      }
    });

    req.end();
  }

  private drainFrames(): void {
    while (this.rxBuffer.length >= 2) {
      const first = this.rxBuffer[0];
      const second = this.rxBuffer[1];
      const opcode = first & 0x0f;
      const isMasked = (second & 0x80) !== 0;
      let payloadLen = second & 0x7f;
      let offset = 2;

      if (payloadLen === 126) {
        if (this.rxBuffer.length < 4) return;
        payloadLen = this.rxBuffer.readUInt16BE(2);
        offset = 4;
      } else if (payloadLen === 127) {
        if (this.rxBuffer.length < 10) return;
        payloadLen = Number(this.rxBuffer.readBigUInt64BE(2));
        offset = 10;
      }

      const maskLen = isMasked ? 4 : 0;
      if (this.rxBuffer.length < offset + maskLen + payloadLen) {
        return;
      }

      let payload = this.rxBuffer.subarray(
        offset + maskLen,
        offset + maskLen + payloadLen
      );
      if (isMasked) {
        const mask = this.rxBuffer.subarray(offset, offset + 4);
        const unmasked = Buffer.alloc(payload.length);
        for (let i = 0; i < payload.length; i++) {
          unmasked[i] = payload[i] ^ mask[i % 4];
        }
        payload = unmasked;
      }

      this.rxBuffer = this.rxBuffer.subarray(offset + maskLen + payloadLen);

      if (opcode === 0x1) {
        // Text frame
        try {
          const parsed = JSON.parse(payload.toString("utf8"));
          this.onMessage(parsed);
        } catch {
          // Ignore malformed JSON
        }
      } else if (opcode === 0x9 && this.socket) {
        // Ping -> Masked Pong
        const mask = crypto.randomBytes(4);
        const pong = Buffer.alloc(6);
        pong[0] = 0x8a;
        pong[1] = 0x80;
        mask.copy(pong, 2);
        this.socket.write(pong);
      } else if (opcode === 0x8 && this.socket) {
        this.socket.end();
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer || this.closedByApp) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByApp) {
        this.connect();
      }
    }, 3000);
  }

  public close(): void {
    this.closedByApp = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
    }
  }
}

export interface BrainBridgeCallbacks {
  onStatusUpdate: (snapshot: PassiveStatusSnapshot) => void;
  onDiagnosisTriggered: (diagnosis: DiagnosisResponse, sourceLabel: string) => void;
}

export class WhackBrainBridge {
  private wsClient: LightweightWsClient | null = null;
  private embeddedServer: http.Server | null = null;
  private embeddedWsSockets = new Set<net.Socket>();
  private healthTimer: NodeJS.Timeout | null = null;
  private outboxWatcher: fs.FSWatcher | null = null;
  private lastOutboxTimestamp = 0;
  private disposed = false;

  public status: PassiveStatusSnapshot = {
    faceState: "calm",
    confusionScore: 0.08,
    cooldownUntil: null,
    cameraPaused: false,
    userStatusLabel: "Flow",
    brainOnline: false,
    coreMode: "offline",
  };

  constructor(
    private readonly coreUrl: string,
    private readonly brainUrl: string,
    private readonly extensionPath: string,
    private readonly workspaceRoots: string[],
    private readonly callbacks: BrainBridgeCallbacks
  ) {}

  public async start(): Promise<void> {
    await this.checkBrainHealth();
    if (this.disposed) return;
    await this.connectOrStartCore();
    if (this.disposed) {
      this.dispose();
      return;
    }
    this.watchSignalsOutbox();

    this.healthTimer = setInterval(() => {
      this.checkBrainHealth().catch(() => undefined);
    }, 8000);
    this.healthTimer.unref?.();
  }

  private async checkBrainHealth(): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 1200);
      const res = await fetch(`${this.brainUrl.replace(/\/$/, "")}/health`, {
        signal: controller.signal,
      });
      clearTimeout(timer);
      const online = res.ok;
      if (online !== this.status.brainOnline) {
        this.status.brainOnline = online;
        this.callbacks.onStatusUpdate({ ...this.status });
      }
      return online;
    } catch {
      if (this.status.brainOnline) {
        this.status.brainOnline = false;
        this.callbacks.onStatusUpdate({ ...this.status });
      }
      return false;
    }
  }

  private async connectOrStartCore(): Promise<void> {
    // 1. Check if external Core Server (`npm start` in `core/`) is already running on :3000
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 900);
      const res = await fetch(`${this.coreUrl}/health`, {
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        this.status.coreMode = "external";
        this.callbacks.onStatusUpdate({ ...this.status });
        this.connectExternalWs();
        return;
      }
    } catch {
      // External Core Server not running -> start embedded :3000 receiver inside VS Code!
    }

    await this.startEmbeddedCoreReceiver();
  }

  private connectExternalWs(): void {
    const wsUrl = this.coreUrl.replace(/^http/, "ws") + "/ws";
    this.wsClient?.close();
    this.wsClient = new LightweightWsClient(
      wsUrl,
      (msg) => this.handleIncomingCoreMessage(msg),
      () => {
        // Disconnected from external Core
      }
    );
    this.wsClient.connect();
  }

  private handleIncomingCoreMessage(msg: any): void {
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "status") {
      this.status.faceState = msg.faceState === "confused" ? "confused" : "calm";
      this.status.confusionScore = Number(msg.confusionScore ?? 0);
      this.status.cooldownUntil = msg.cooldownUntil ?? null;
      this.status.cameraPaused = Boolean(msg.cameraPaused);
      this.status.userStatusLabel =
        this.status.faceState === "confused" || this.status.confusionScore >= 0.7
          ? "Stuck"
          : this.status.confusionScore >= 0.4
          ? "Thinking"
          : "Flow";
      this.callbacks.onStatusUpdate({ ...this.status });
    } else if (msg.type === "diagnosis_done" && msg.diagnosis) {
      if (!this.isInCooldown()) {
        const normalized = normalizeBrainDiagnosis(msg.diagnosis);
        this.callbacks.onDiagnosisTriggered(normalized, "Brain Stream");
      }
    } else if (msg.type === "nudge") {
      this.status.faceState = "confused";
      this.status.confusionScore = Math.max(
        this.status.confusionScore,
        Number(msg.score ?? 0.82)
      );
      this.status.userStatusLabel = "Stuck";
      this.callbacks.onStatusUpdate({ ...this.status });
    }
  }

  /**
   * Embedded lightweight `:3000` receiver so `face/prototype_gui.py`, `signals/`,
   * and Role 3's `brain/server.py` (`broadcast_to_core`) work directly with VS Code
   * even if the user didn't start `core/` in a separate terminal!
   */
  private async startEmbeddedCoreReceiver(): Promise<void> {
    let parsedPort = 3000;
    try {
      parsedPort = Number(new URL(this.coreUrl).port || 3000);
    } catch {
      parsedPort = 3000;
    }

    const srv = http.createServer(async (req, res) => {
      res.setHeader("Access-Control-Allow-Origin", "*");
      res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      res.setHeader("Access-Control-Allow-Headers", "Content-Type");

      if (req.method === "OPTIONS") {
        res.writeHead(200);
        res.end("{}");
        return;
      }

      const url = new URL(req.url || "/", "http://127.0.0.1");
      if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            ok: true,
            service: "whack-vscode-embedded-core",
            faceState: this.status.faceState,
            confusionScore: this.status.confusionScore,
            cooldownUntil: this.status.cooldownUntil,
            cameraPaused: this.status.cameraPaused,
          })
        );
        return;
      }

      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", async () => {
        let body: any = {};
        try {
          const raw = Buffer.concat(chunks).toString("utf8").trim();
          body = raw ? JSON.parse(raw) : {};
        } catch {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: false, error: "Invalid JSON" }));
          return;
        }

        if (req.method === "POST" && url.pathname === "/events") {
          const source = body.source;
          const payload = body.payload !== undefined ? body.payload : body;

          if (source === "face" && !this.status.cameraPaused) {
            const score = Number(payload.score ?? 0);
            const state = payload.state === "confused" || score >= 0.7 ? "confused" : "calm";
            this.status.faceState = state;
            this.status.confusionScore = score;
            this.status.userStatusLabel =
              state === "confused" ? "Stuck" : score >= 0.4 ? "Thinking" : "Flow";
            this.callbacks.onStatusUpdate({ ...this.status });
            this.broadcastEmbeddedWs({
              type: "status",
              timestamp: Date.now(),
              faceState: this.status.faceState,
              confusionScore: this.status.confusionScore,
              cooldownUntil: this.status.cooldownUntil,
              cameraPaused: this.status.cameraPaused,
              eventCount: 1,
            });
          } else if (source === "brain" && (body.type === "diagnosis" || payload.summary)) {
            const diag = normalizeBrainDiagnosis(payload);
            this.broadcastEmbeddedWs({ type: "diagnosis_done", diagnosis: diag });
            if (!this.isInCooldown()) {
              this.callbacks.onDiagnosisTriggered(diag, "Role 3 Brain");
            }
          }

          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: true }));
          return;
        }

        if (req.method === "POST" && url.pathname === "/diagnose") {
          const diag = await this.requestDiagnosis(body.hintLevel || "hint", {
            activeFile: body.editorState?.activeFile,
            cursorLine: body.editorState?.cursorLine,
          });
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: true, diagnosis: diag }));
          return;
        }

        if (req.method === "POST" && url.pathname === "/feedback") {
          this.applyLocalFeedback(body.action || "dismiss");
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: true }));
          return;
        }

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      });
    });

    // Minimal RFC-6455 upgrade handler for any external WS subscribers
    srv.on("upgrade", (req, socket: net.Socket) => {
      const key = req.headers["sec-websocket-key"];
      if (!key || typeof key !== "string") {
        socket.destroy();
        return;
      }
      const accept = crypto
        .createHash("sha1")
        .update(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
        .digest("base64");
      socket.write(
        "HTTP/1.1 101 Switching Protocols\r\n" +
          "Upgrade: websocket\r\n" +
          "Connection: Upgrade\r\n" +
          `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
      );
      this.embeddedWsSockets.add(socket);
      socket.on("close", () => this.embeddedWsSockets.delete(socket));
      socket.on("error", () => this.embeddedWsSockets.delete(socket));
    });

    await new Promise<void>((resolve) => {
      srv.once("error", () => {
        // Port became busy concurrently -> connect as external WS client instead
        this.status.coreMode = "external";
        this.connectExternalWs();
        resolve();
      });
      srv.listen(parsedPort, "127.0.0.1", () => {
        this.embeddedServer = srv;
        this.status.coreMode = "embedded";
        this.callbacks.onStatusUpdate({ ...this.status });
        resolve();
      });
    });
  }

  private broadcastEmbeddedWs(obj: unknown): void {
    if (this.embeddedWsSockets.size === 0) return;
    const data = Buffer.from(JSON.stringify(obj), "utf8");
    let header: Buffer;
    if (data.length < 126) {
      header = Buffer.from([0x81, data.length]);
    } else {
      header = Buffer.alloc(4);
      header[0] = 0x81;
      header[1] = 126;
      header.writeUInt16BE(data.length, 2);
    }
    const frame = Buffer.concat([header, data]);
    for (const sock of this.embeddedWsSockets) {
      try {
        sock.write(frame);
      } catch {
        this.embeddedWsSockets.delete(sock);
      }
    }
  }

  /**
   * Watches `signals/latest_brain_call.json` so `signals/prototype_signals_gui.py`
   * immediately updates the VS Code Status Bar (and triggers Brain if stuck!).
   */
  private watchSignalsOutbox(): void {
    for (const root of this.workspaceRoots) {
      const outboxPath = path.join(root, "signals", "latest_brain_call.json");
      const signalsDir = path.dirname(outboxPath);
      if (!fs.existsSync(signalsDir)) continue;

      try {
        this.outboxWatcher = fs.watch(signalsDir, (_eventType, filename) => {
          if (filename !== "latest_brain_call.json") return;
          try {
            const raw = JSON.parse(fs.readFileSync(outboxPath, "utf8"));
            const ts = Number(raw.timestamp || 0);
            if (ts <= this.lastOutboxTimestamp) return;
            this.lastOutboxTimestamp = ts;

            const stuckScore = Number(raw.user_status?.stuck_score ?? 0);
            const stateStr = String(raw.status || raw.user_status?.state || "focused_flow");
            this.status.confusionScore = stuckScore;
            this.status.faceState = stuckScore >= 0.65 ? "confused" : "calm";
            this.status.userStatusLabel = stateStr.includes("stuck")
              ? "Stuck"
              : stateStr.includes("afk")
              ? "AFK"
              : stuckScore >= 0.4
              ? "Thinking"
              : "Flow";
            this.callbacks.onStatusUpdate({ ...this.status });

            // If Signals+Face actively triggered a stuck call and Brain :5002 isn't running yet, trigger diagnosis!
            if (
              (stateStr.startsWith("stuck_") || stateStr === "frustrated_blocked") &&
              !this.isInCooldown()
            ) {
              this.requestDiagnosis("hint", {}).then((diag) => {
                this.callbacks.onDiagnosisTriggered(diag, "Signals+Face Trigger");
              });
            }
          } catch {
            // Ignore partial writes
          }
        });
        break;
      } catch {
        // Ignore fs.watch errors
      }
    }
  }

  public isInCooldown(): boolean {
    if (!this.status.cooldownUntil) return false;
    if (Date.now() >= this.status.cooldownUntil) {
      this.status.cooldownUntil = null;
      return false;
    }
    return true;
  }

  public applyLocalFeedback(action: "dismiss" | "snooze" | "helpful"): void {
    if (action === "dismiss" || action === "snooze") {
      this.status.cooldownUntil = Date.now() + 3 * 60 * 1000;
      this.status.faceState = "calm";
      this.status.confusionScore = 0.05;
      this.status.userStatusLabel = "Snoozed";
      this.callbacks.onStatusUpdate({ ...this.status });
    }

    if (this.status.coreMode === "external") {
      fetch(`${this.coreUrl}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action === "dismiss" ? "not_confused" : action }),
      }).catch(() => undefined);
    }
  }

  /**
   * Calls Role 3 Brain (`:5002/diagnose`) -> External Core (`:3000/diagnose`) -> Built-in Mock Brain!
   */
  public async requestDiagnosis(
    hintLevel: HintLevel,
    editorContext: {
      activeFile?: string;
      cursorLine?: number;
      selectionText?: string;
      diagnosticsCount?: number;
      terminalCommand?: string;
      terminalExitCode?: number;
      terminalTail?: string;
    }
  ): Promise<DiagnosisResponse> {
    // 1. Try Role 3's Brain server directly on :5002
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3200);
      const res = await fetch(`${this.brainUrl.replace(/\/$/, "")}/diagnose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hintLevel,
          editorState: {
            activeFile: editorContext.activeFile,
            cursorLine: editorContext.cursorLine,
            selectionText: editorContext.selectionText,
            diagnosticsCount: editorContext.diagnosticsCount ?? 0,
          },
          terminalState: editorContext.terminalCommand
            ? {
                command: editorContext.terminalCommand,
                exitCode: editorContext.terminalExitCode ?? 1,
                tailOutput: editorContext.terminalTail ?? "",
              }
            : undefined,
          stream: false,
        }),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        this.status.brainOnline = true;
        this.callbacks.onStatusUpdate({ ...this.status });
        const json = await res.json();
        return normalizeBrainDiagnosis(json);
      }
    } catch {
      // Brain :5002 not running yet -> fall through cleanly
    }

    // 2. Fallback to built-in Mock Brain scenario (prefer active file if user has one open, else planted bug)
    const fallback = buildSimulatedDiagnosis("sliding_window", this.extensionPath);
    fallback.hintLevel = hintLevel;
    return fallback;
  }

  /**
   * Sends a right-side Chatbox question to Role 3 Brain (`:5002`), External Core (`:3000/chat`),
   * or the built-in context-aware Mock Brain responder.
   */
  public async sendChatMessage(params: {
    message: string;
    history: ChatMessage[];
    activeFile?: string;
    highlightedLines?: [number, number];
    codeSnippet?: string;
    latestDiagnosis?: DiagnosisResponse | null;
  }): Promise<{ reply: string; mode: "brain_live" | "mock_fallback" }> {
    const baseBrain = this.brainUrl.replace(/\/$/, "");

    // 1. Try Role 3 Brain `/chat` or `/diagnose` if `:5002` is online
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3200);
      const res = await fetch(`${baseBrain}/diagnose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hintLevel: "full_explanation",
          attemptLog: `Developer follow-up question in IDE chat: "${params.message}"\nHighlighted code:\n${params.codeSnippet || ""}`,
          editorState: {
            activeFile: params.activeFile || params.latestDiagnosis?.file,
            cursorLine: params.highlightedLines?.[0] ?? 1,
            selectionText: params.codeSnippet,
          },
          stream: false,
        }),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        const data = (await res.json()) as any;
        const reply = data.reply
          ? String(data.reply)
          : data.explanation
          ? `${data.summary || ""}\n\n${data.explanation}`.trim()
          : String(data.summary || "Checked repository context.");
        return { reply, mode: "brain_live" };
      }
    } catch {
      // Role 3 Brain not running yet
    }

    // 2. Try External Core `/chat` if connected
    if (this.status.coreMode === "external") {
      try {
        const res = await fetch(`${this.coreUrl}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params),
        });
        if (res.ok) {
          const data = (await res.json()) as any;
          if (data.reply) {
            return {
              reply: String(data.reply),
              mode: data.mode === "brain_live" ? "brain_live" : "mock_fallback",
            };
          }
        }
      } catch {
        // Fall through to local Mock Brain
      }
    }

    // 3. Built-in Context-Aware Mock Brain Chat Responder (reads actual highlighted code!)
    const fileLabel = params.activeFile
      ? path.basename(params.activeFile)
      : params.latestDiagnosis?.file
      ? path.basename(params.latestDiagnosis.file)
      : "utils.py";
    const lineLabel = params.highlightedLines
      ? `L${params.highlightedLines[0]}–${params.highlightedLines[1]}`
      : "highlighted lines";
    const q = params.message.toLowerCase();

    if (q.includes("hint") || q.includes("spoiler") || q.includes("without")) {
      return {
        mode: "mock_fallback",
        reply:
          `**Socratic Hint (\`${fileLabel}\` · ${lineLabel}):**\n` +
          `Walk through \`total_items = 5\` and \`window_size = 3\` by hand. ` +
          `Valid windows start at \`0\`, \`1\`, and \`2\` (3 windows total). What is the last \`i\` produced by your \`range(...)\` right now?`,
      };
    }

    if (q.includes("why") || q.includes("explain") || q.includes("cause")) {
      return {
        mode: "mock_fallback",
        reply:
          `**Why \`${fileLabel}\` (${lineLabel}) fails:**\n` +
          `${params.latestDiagnosis?.summary || "The upper bound steps 1 index past the sequence end."}\n\n` +
          `Because Python's \`range(start, stop)\` stops at \`stop - 1\`, using \`total_items - window_size + 2\` sets the final \`start = total_items - window_size + 1\`, so \`end = start + window_size = total_items + 1\` — 1 element past \`len(data)\`.`,
      };
    }

    if (q.includes("edge") || q.includes("other") || q.includes("case")) {
      return {
        mode: "mock_fallback",
        reply:
          `**Edge Cases Checked for \`${fileLabel}\`:**\n` +
          `- \`window_size > total_items\`: \`range(0, negative_or_zero)\` returns \`[]\` cleanly.\n` +
          `- \`window_size == total_items\`: \`range(0, 1)\` yields \`[(0, total_items)]\`.\n` +
          `- \`total_items <= 0\`: Guarded at the top of the function.`,
      };
    }

    return {
      mode: "mock_fallback",
      reply:
        `**Mock Brain (\`${fileLabel}\` · ${lineLabel}):**\n` +
        `I'm inspecting your highlighted snippet${params.codeSnippet ? `:\n\`\`\`python\n${params.codeSnippet.trim()}\n\`\`\`` : "."}\n` +
        `${params.latestDiagnosis?.summary || ""}\n\n` +
        `Click **Preview Diff** above to compare the exact line fix side-by-side, or **Apply Suggested Fix** to patch it directly.`,
    };
  }

  public postTelemetryEvent(source: "editor" | "terminal", type: string, payload: unknown): void {
    if (this.status.coreMode === "external") {
      fetch(`${this.coreUrl}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source,
          type,
          timestamp: Date.now(),
          payload,
        }),
      }).catch(() => undefined);
    }
  }

  public dispose(): void {
    this.disposed = true;
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
    if (this.outboxWatcher) {
      this.outboxWatcher.close();
      this.outboxWatcher = null;
    }
    this.wsClient?.close();
    for (const sock of this.embeddedWsSockets) {
      sock.destroy();
    }
    this.embeddedWsSockets.clear();
    if (this.embeddedServer) {
      this.embeddedServer.close();
      this.embeddedServer = null;
    }
  }
}
