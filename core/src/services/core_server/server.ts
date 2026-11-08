import http, { IncomingMessage, ServerResponse } from "http";
import {
  ChatRequest,
  CodeCitation,
  DiagnosisRequest,
  DiagnosisResponse,
  HintLevel,
  SuggestedFix,
  normalizeFeedbackPayload,
  normalizeIncomingEvent,
} from "../../contracts";
import { runBrainChat, runDiagnosis } from "./brainBridge";
import { FusionEngine } from "./fusionEngine";
import { EventRingBuffer } from "./ringBuffer";
import { WsHub } from "./wsHub";

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8").trim();
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(
  res: ServerResponse,
  statusCode: number,
  data: unknown
): void {
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  });
  res.end(JSON.stringify(data));
}

export function createCoreServer(capacity = 100) {
  const store = new EventRingBuffer(capacity);
  const fusion = new FusionEngine();
  let wsHub: WsHub;

  const server = http.createServer(
    async (req: IncomingMessage, res: ServerResponse) => {
      const method = req.method || "GET";
      const url = new URL(req.url || "/", "http://localhost");
      const pathname = url.pathname;

      // Handle CORS preflight
      if (method === "OPTIONS") {
        sendJson(res, 200, { ok: true });
        return;
      }

      // 1. Health & Current Status (`GET /health` or `GET /`)
      if (method === "GET" && (pathname === "/" || pathname === "/health")) {
        sendJson(res, 200, {
          ok: true,
          service: "whack-core-server",
          ...store.getSnapshot(),
        });
        return;
      }

      // 2. Read Ring Buffer History (`GET /events`)
      if (method === "GET" && pathname === "/events") {
        sendJson(res, 200, {
          ok: true,
          events: store.getAll(),
          snapshot: store.getSnapshot(),
        });
        return;
      }

      // 3. Ingest Event (`POST /events`)
      if (method === "POST" && pathname === "/events") {
        let body: unknown;
        try {
          body = await readJsonBody(req);
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        const normalized = normalizeIncomingEvent(body);
        if (!normalized.valid || !normalized.event) {
          sendJson(res, 400, { ok: false, error: normalized.error });
          return;
        }

        const event = normalized.event;
        store.push(event);

        // Broadcast raw ingested event for the Live Feed Debug Drawer
        wsHub.broadcast({
          type: "event_ingest",
          event,
        });

        // Broadcast updated status snapshot
        wsHub.broadcastStatus();

        const payloadObj = (event.payload || {}) as Record<string, unknown>;

        // If Role 3's Brain service pushed a direct diagnosis event via `broadcast_to_core`
        if (event.source === "brain" && event.type === "diagnosis") {
          const summaryText =
            typeof payloadObj.explanation === "string" &&
            payloadObj.hintLevel === "full_explanation"
              ? `${String(payloadObj.summary || "")}\n\n${payloadObj.explanation}`
              : String(payloadObj.summary || "");

          const diagPayload: DiagnosisResponse = {
            summary: summaryText,
            file:
              typeof payloadObj.file === "string" ? payloadObj.file : undefined,
            lines: Array.isArray(payloadObj.lines)
              ? (payloadObj.lines as [number, number])
              : undefined,
            quip:
              typeof payloadObj.quip === "string"
                ? payloadObj.quip
                : "Computers do what you say, not what you want.",
            uncertain: Boolean(payloadObj.uncertain),
            hintLevel:
              payloadObj.hintLevel === "nudge" ||
              payloadObj.hintLevel === "hint" ||
              payloadObj.hintLevel === "full_explanation"
                ? payloadObj.hintLevel
                : "hint",
            reasons: Array.isArray(payloadObj.reasons)
              ? (payloadObj.reasons as string[])
              : undefined,
            confidence:
              typeof payloadObj.confidence === "number"
                ? payloadObj.confidence
                : 0.95,
            explanation:
              typeof payloadObj.explanation === "string"
                ? payloadObj.explanation
                : undefined,
            citations: Array.isArray(payloadObj.citations)
              ? (payloadObj.citations as CodeCitation[])
              : undefined,
            suggestedFix:
              payloadObj.suggestedFix &&
              typeof payloadObj.suggestedFix === "object"
                ? (payloadObj.suggestedFix as SuggestedFix)
                : undefined,
          };

          wsHub.broadcast({
            type: "diagnosis_done",
            diagnosis: diagPayload,
          });
        }

        // Check if this event is an explicit nudge or triggers the multi-signal Fusion Engine
        const snap = store.getSnapshot();
        const fusionDecision = fusion.evaluate(store.getAll(), {
          inCooldown: store.isInCooldown(),
          cameraPaused: snap.cameraPaused,
        });

        if (!store.isInCooldown()) {
          if (
            event.type === "nudge" ||
            Array.isArray(payloadObj.reasons) ||
            fusionDecision.shouldNudge
          ) {
            const reasons = Array.isArray(payloadObj.reasons)
              ? (payloadObj.reasons as string[])
              : fusionDecision.reasons.length > 0
              ? fusionDecision.reasons
              : ["confusion threshold exceeded"];
            const severity =
              payloadObj.severity === "soft"
                ? "soft"
                : fusionDecision.severity;
            wsHub.broadcast({
              type: "nudge",
              timestamp: Date.now(),
              severity,
              reasons,
              score: Math.max(snap.confusionScore, fusionDecision.combinedScore),
            });
          }
        }

        sendJson(res, 200, {
          ok: true,
          ingested: event,
          eventCount: snap.eventCount,
          fusion: fusionDecision,
        });
        return;
      }

      // 4. Trigger Brain Diagnosis (`POST /diagnose`)
      if (method === "POST" && pathname === "/diagnose") {
        let body: Record<string, unknown> = {};
        try {
          body = ((await readJsonBody(req)) || {}) as Record<string, unknown>;
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        const snap = store.getSnapshot();
        const fusionDecision = fusion.evaluate(store.getAll(), {
          inCooldown: store.isInCooldown(),
          cameraPaused: snap.cameraPaused,
        });

        const hintLevel: HintLevel =
          body.hintLevel === "nudge" ||
          body.hintLevel === "hint" ||
          body.hintLevel === "full_explanation"
            ? body.hintLevel
            : "hint";

        const diagReq: DiagnosisRequest = {
          triggerType:
            body.triggerType === "auto_nudge" ? "auto_nudge" : "hotkey",
          hintLevel,
          fusionReasons: Array.isArray(body.fusionReasons)
            ? (body.fusionReasons as string[])
            : fusionDecision.reasons,
          attemptLog:
            typeof body.attemptLog === "string"
              ? body.attemptLog
              : fusionDecision.attemptLog,
          lastKnownGoodDiff:
            typeof body.lastKnownGoodDiff === "string"
              ? body.lastKnownGoodDiff
              : undefined,
          screenshot:
            typeof body.screenshot === "string" ? body.screenshot : undefined,
          screenshotPath:
            typeof body.screenshotPath === "string"
              ? body.screenshotPath
              : undefined,
          editorState:
            (body.editorState as DiagnosisRequest["editorState"]) ??
            snap.latestEditor,
          terminalState:
            (body.terminalState as DiagnosisRequest["terminalState"]) ??
            snap.latestTerminal,
          latestFace: snap.latestFace,
          latestEditor: snap.latestEditor,
          latestTerminal: snap.latestTerminal,
          recentEvents: store.getAll().slice(-20),
        };

        const diagnosis = await runDiagnosis(diagReq, wsHub);
        sendJson(res, 200, {
          ok: true,
          diagnosis,
        });
        return;
      }

      // 5. Follow-up Brain Chat (`POST /chat`)
      if (method === "POST" && pathname === "/chat") {
        let body: ChatRequest;
        try {
          body = ((await readJsonBody(req)) || { message: "" }) as ChatRequest;
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        const chatRes = await runBrainChat(body);
        sendJson(res, 200, {
          ok: true,
          ...chatRes,
        });
        return;
      }

      // 6. User Feedback (`POST /feedback`)
      if (method === "POST" && pathname === "/feedback") {
        let body: unknown;
        try {
          body = await readJsonBody(req);
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        const feedback = normalizeFeedbackPayload(body);
        store.applyFeedback(feedback);
        wsHub.broadcastStatus();

        sendJson(res, 200, {
          ok: true,
          feedback,
          snapshot: store.getSnapshot(),
        });
        return;
      }

      sendJson(res, 404, { ok: false, error: `Route not found: ${pathname}` });
    }
  );

  wsHub = new WsHub(server, store);

  return {
    server,
    store,
    wsHub,
    listen(port = 3000): Promise<number> {
      return new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, "127.0.0.1", () => {
          const addr = server.address();
          const actualPort =
            typeof addr === "object" && addr ? addr.port : port;
          resolve(actualPort);
        });
      });
    },
    async close(): Promise<void> {
      await wsHub.close();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

// Start server directly when run via `npm start` or `node dist/services/core_server/server.js`
if (require.main === module) {
  const port = Number(process.env.PORT || 3000);
  const core = createCoreServer(100);
  core.listen(port).then((boundPort) => {
    console.log(
      `[WHACK Core Server] Listening on http://localhost:${boundPort}`
    );
    console.log(
      `[WHACK Core Server] WebSocket Hub at ws://localhost:${boundPort}/ws`
    );
  });
}
