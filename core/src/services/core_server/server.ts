import http, { IncomingMessage, ServerResponse } from "http";
import {
  ChatRequest,
  CodeCitation,
  DiagnosisRequest,
  DiagnosisResponse,
  EditorPayload,
  HintLevel,
  SuggestedFix,
  TerminalPayload,
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
      const url = new URL(req.url || "/", "http://127.0.0.1");
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

      // 2. Dynamic Workspace & Editor/Terminal Context (`GET /context` & `POST /context`)
      if (method === "GET" && pathname === "/context") {
        const snap = store.getSnapshot();
        const ed = snap.latestEditor;
        const term = snap.latestTerminal;
        const workspaceRoot =
          store.getWorkspaceRoot() ??
          process.env.WHACK_WORKSPACE_ROOT?.replace(/\\/g, "/") ??
          null;

        sendJson(res, 200, {
          ok: true,
          workspaceRoot,
          cooldownUntil: snap.cooldownUntil,
          cameraPaused: snap.cameraPaused,
          editorState: {
            activeFile:
              ed?.activeFile && ed.activeFile !== "unknown" ? ed.activeFile : "",
            cursorLine: ed?.cursorLine ?? 1,
            cursorColumn: ed?.cursorColumn ?? 1,
            selectionText: ed?.selectionText ?? "",
            diagnosticsCount: ed?.diagnosticsCount ?? 0,
            diagnosticsSummary:
              ed?.diagnosticsSummary ??
              (ed?.topDiagnostics ? ed.topDiagnostics.join("\n") : ""),
          },
          terminalState: term
            ? {
                command: term.command,
                exitCode: term.exitCode,
                durationMs: term.durationMs,
                tailOutput: term.tailOutput,
              }
            : null,
        });
        return;
      }

      if (method === "POST" && pathname === "/context") {
        let body: Record<string, unknown> = {};
        try {
          body = ((await readJsonBody(req)) || {}) as Record<string, unknown>;
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        store.updateContext({
          workspaceRoot:
            typeof body.workspaceRoot === "string" ? body.workspaceRoot : null,
          editorState: (body.editorState as Partial<EditorPayload>) ?? null,
          terminalState: (body.terminalState as Partial<TerminalPayload>) ?? null,
        });
        sendJson(res, 200, { ok: true });
        return;
      }

      // 3. Read Ring Buffer History (`GET /events`)
      if (method === "GET" && pathname === "/events") {
        sendJson(res, 200, {
          ok: true,
          events: store.getAll(),
          snapshot: store.getSnapshot(),
        });
        return;
      }

      // 4. Ingest Event (`POST /events`)
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
        if (
          event.source === "brain" &&
          (event.type === "diagnosis" || event.type === "brain_diagnosis")
        ) {
          const isOffline = payloadObj.source === "offline";
          const diagPayload: DiagnosisResponse = {
            diagnosisId:
              typeof payloadObj.diagnosisId === "string"
                ? payloadObj.diagnosisId
                : typeof payloadObj.diagnosis_id === "string"
                ? payloadObj.diagnosis_id
                : undefined,
            summary: String(payloadObj.summary || ""),
            file:
              !isOffline && typeof payloadObj.file === "string"
                ? payloadObj.file
                : undefined,
            lines:
              !isOffline && Array.isArray(payloadObj.lines)
                ? (payloadObj.lines as [number, number])
                : undefined,
            quip:
              typeof payloadObj.quip === "string"
                ? payloadObj.quip
                : "Computers do what you say, not what you want.",
            uncertain: isOffline ? true : Boolean(payloadObj.uncertain),
            hintLevel:
              payloadObj.hintLevel === "nudge" ||
              payloadObj.hintLevel === "hint" ||
              payloadObj.hintLevel === "full_explanation"
                ? payloadObj.hintLevel
                : "hint",
            source: isOffline ? "offline" : "model",
            error:
              typeof payloadObj.error === "string"
                ? payloadObj.error
                : undefined,
            repoPath:
              typeof payloadObj.repoPath === "string"
                ? payloadObj.repoPath
                : typeof payloadObj.repo_path === "string"
                ? payloadObj.repo_path
                : undefined,
            reasons: Array.isArray(payloadObj.reasons)
              ? (payloadObj.reasons as string[])
              : undefined,
            confidence:
              typeof payloadObj.confidence === "number"
                ? payloadObj.confidence
                : isOffline
                ? 0
                : 0.95,
            explanation:
              typeof payloadObj.explanation === "string"
                ? payloadObj.explanation
                : undefined,
            citations:
              !isOffline && Array.isArray(payloadObj.citations)
                ? (payloadObj.citations as CodeCitation[])
                : [],
            suggestedFix:
              !isOffline &&
              payloadObj.suggestedFix &&
              typeof payloadObj.suggestedFix === "object"
                ? (payloadObj.suggestedFix as SuggestedFix)
                : null,
          };

          wsHub.broadcast({
            type: "diagnosis_done",
            diagnosis: diagPayload,
          });
        }

        // Check if sensor events trigger the multi-signal Fusion Engine (skip nudge on completed `brain` broadcasts)
        const snap = store.getSnapshot();
        const fusionDecision = fusion.evaluate(store.getAll(), {
          inCooldown: store.isInCooldown(),
          cameraPaused: snap.cameraPaused,
        });

        if (!store.isInCooldown() && event.source !== "brain") {
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

      // 5. Trigger Brain Diagnosis (`POST /diagnose`)
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
          repoPath:
            typeof body.repoPath === "string"
              ? body.repoPath
              : store.getWorkspaceRoot() ?? undefined,
          captureScreenshot:
            typeof body.captureScreenshot === "boolean"
              ? body.captureScreenshot
              : true,
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

      // 6. Follow-up Brain Chat (`POST /chat`)
      if (method === "POST" && pathname === "/chat") {
        let body: ChatRequest;
        try {
          body = ((await readJsonBody(req)) || { message: "" }) as ChatRequest;
        } catch {
          sendJson(res, 400, { ok: false, error: "Invalid JSON body" });
          return;
        }

        if (!body.repoPath) {
          body.repoPath = store.getWorkspaceRoot() ?? undefined;
        }

        const chatRes = await runBrainChat(body);
        sendJson(res, 200, {
          ok: true,
          ...chatRes,
        });
        return;
      }

      // 7. User Feedback (`POST /feedback`)
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
      `[WHACK Core Server] Listening on http://127.0.0.1:${boundPort}`
    );
    console.log(
      `[WHACK Core Server] WebSocket Hub at ws://127.0.0.1:${boundPort}/ws`
    );
  });
}
