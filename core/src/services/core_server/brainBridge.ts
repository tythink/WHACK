import {
  ChatRequest,
  ChatResponse,
  DiagnosisRequest,
  DiagnosisResponse,
  HintLevel,
} from "../../contracts";
import { WsHub } from "./wsHub";

const BRAIN_SERVICE_URL =
  process.env.BRAIN_SERVICE_URL || "http://127.0.0.1:5002/diagnose";
const BRAIN_CHAT_URL =
  process.env.BRAIN_CHAT_URL ||
  BRAIN_SERVICE_URL.replace(/\/diagnose\/?$/, "/chat");

const BRAIN_HTTP_TIMEOUT_MS = 28000;

function buildOfflineDiagnosis(
  req: DiagnosisRequest,
  errorDetail: string
): DiagnosisResponse {
  return {
    diagnosisId: `offline-${Date.now()}`,
    summary: "WHACK Brain (:5002) is offline.",
    file: undefined,
    lines: undefined,
    quip: "Start brain.server on port 5002 so I can actually see what you're cooking.",
    uncertain: true,
    hintLevel: req.hintLevel || "hint",
    source: "offline",
    error: errorDetail,
    repoPath: req.repoPath,
    reasons: req.fusionReasons,
    confidence: 0,
    explanation: `Could not reach ${BRAIN_SERVICE_URL} (${errorDetail}). Run \`python -m uvicorn brain.server:app --port 5002\` with an API key in \`brain/.env\`.`,
    citations: [],
    suggestedFix: null,
  };
}

/**
 * Forwards diagnosis requests to Role 3's Brain Service (`POST :5002/diagnose`) with a 28s timeout.
 * Returns an honest `source: "offline"` payload when `:5002` is unreachable (unless `USE_BRAIN_STUB=true`
 * is explicitly set by isolated `verify.ts` contract tests).
 */
export async function runDiagnosis(
  req: DiagnosisRequest,
  wsHub: WsHub
): Promise<DiagnosisResponse> {
  if (process.env.USE_BRAIN_STUB !== "true") {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), BRAIN_HTTP_TIMEOUT_MS);
      const res = await fetch(BRAIN_SERVICE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hintLevel: req.hintLevel,
          repoPath: req.repoPath ?? req.editorState?.workspaceRoot,
          captureScreenshot: req.captureScreenshot ?? true,
          screenshot: req.screenshot,
          fusionReasons: req.fusionReasons,
          attemptLog: req.attemptLog ?? req.recentEvents,
          editorState: req.editorState ?? req.latestEditor,
          terminalState: req.terminalState ?? req.latestTerminal,
          stream: false,
        }),
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (res.ok) {
        const data = (await res.json()) as Partial<DiagnosisResponse> & {
          diagnosis_id?: string;
          repo_path?: string;
          suggested_fix?: DiagnosisResponse["suggestedFix"];
        };

        const isOffline = data.source === "offline";
        const diagnosis: DiagnosisResponse = {
          diagnosisId: data.diagnosisId ?? data.diagnosis_id,
          summary:
            data.summary ||
            (isOffline
              ? "WHACK Brain (:5002) is offline."
              : "WHACK Brain completed analysis."),
          file: isOffline ? undefined : data.file || undefined,
          lines: isOffline
            ? undefined
            : Array.isArray(data.lines)
            ? data.lines
            : undefined,
          quip:
            data.quip ||
            (isOffline
              ? "Start brain.server on port 5002 so I can actually see what you're cooking."
              : "Computers do what you say, not what you want."),
          uncertain: isOffline ? true : Boolean(data.uncertain),
          hintLevel: data.hintLevel ?? req.hintLevel ?? "hint",
          source: isOffline ? "offline" : "model",
          error: data.error,
          repoPath: data.repoPath ?? data.repo_path ?? req.repoPath,
          reasons: data.reasons ?? req.fusionReasons,
          confidence:
            typeof data.confidence === "number"
              ? data.confidence
              : isOffline
              ? 0
              : 0.95,
          explanation: data.explanation,
          citations: isOffline ? [] : data.citations,
          suggestedFix: isOffline
            ? null
            : (data.suggestedFix ?? data.suggested_fix ?? null),
        };

        wsHub.broadcast({
          type: "diagnosis_chunk",
          chunk: diagnosis.summary,
        });
        wsHub.broadcast({
          type: "diagnosis_done",
          diagnosis,
        });
        return diagnosis;
      }

      const httpErr = `HTTP ${res.status} ${res.statusText || ""}`.trim();
      const offlineResp = buildOfflineDiagnosis(req, httpErr);
      wsHub.broadcast({
        type: "diagnosis_done",
        diagnosis: offlineResp,
      });
      return offlineResp;
    } catch (err: unknown) {
      const errDetail =
        err instanceof Error
          ? err.name === "AbortError"
            ? `Timed out after ${BRAIN_HTTP_TIMEOUT_MS / 1000}s`
            : err.message
          : String(err);
      const offlineResp = buildOfflineDiagnosis(req, errDetail);
      wsHub.broadcast({
        type: "diagnosis_done",
        diagnosis: offlineResp,
      });
      return offlineResp;
    }
  }

  // Isolated unit-test stub (`USE_BRAIN_STUB=true` in `verify.ts`)
  const activeFile =
    req.editorState?.activeFile || req.latestEditor?.activeFile || "utils.py";
  const hintLevel: HintLevel = req.hintLevel || "hint";

  const summaryByLevel: Record<HintLevel, string> = {
    nudge: `Check the loop boundary in \`${activeFile}\` around lines 40–45—what happens on the final iteration?`,
    hint: `Index error on line 42 in \`${activeFile}\`: \`range(len(items) + 1)\` steps one index past the end of \`items\`.`,
    full_explanation:
      `### Root Cause (\`${activeFile}:40-45\`)\n` +
      `Your recent edit changed the loop bound to \`range(len(items) + 1)\`, causing an \`IndexError\` on line 42 when accessing \`items[i]\`.\n\n` +
      `**Suggested Fix:**\n` +
      "```python\nfor i in range(len(items)):\n    process(items[i])\n```",
  };

  const fullText = summaryByLevel[hintLevel];

  const words = fullText.split(" ");
  const chunkSize = Math.max(2, Math.ceil(words.length / 5));
  for (let i = 0; i < words.length; i += chunkSize) {
    const chunk =
      words.slice(i, i + chunkSize).join(" ") +
      (i + chunkSize < words.length ? " " : "");
    wsHub.broadcast({
      type: "diagnosis_chunk",
      chunk,
    });
    await new Promise((r) => setTimeout(r, 25));
  }

  const response: DiagnosisResponse = {
    summary: fullText,
    file: activeFile,
    lines: [40, 45],
    quip: "Computers do what you say, not what you want.",
    uncertain: false,
    hintLevel,
    source: "model",
    reasons:
      req.fusionReasons && req.fusionReasons.length > 0
        ? req.fusionReasons
        : ["manual hotkey trigger", "repeated test failure"],
    confidence: 0.95,
    citations: [
      {
        file: activeFile,
        lines: [40, 45],
        note: "Off-by-one upper bound in range()",
      },
    ],
  };

  wsHub.broadcast({
    type: "diagnosis_done",
    diagnosis: response,
  });

  return response;
}

/**
 * Follow-up conversational endpoint (`POST /chat`) forwarding directly to `:5002/chat` (28s timeout).
 * Never falls back to `/diagnose` (which broadcasts to `:3000/events`) or fake Mock Brain prose.
 */
export async function runBrainChat(req: ChatRequest): Promise<ChatResponse> {
  if (process.env.USE_BRAIN_STUB !== "true") {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), BRAIN_HTTP_TIMEOUT_MS);
      const res = await fetch(BRAIN_CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (res.ok) {
        const data = (await res.json()) as Partial<ChatResponse> & {
          source?: string;
          suggested_fix?: ChatResponse["suggestedFix"];
        };
        const isOffline = data.mode === "offline" || data.source === "offline";
        return {
          reply: data.reply || "No reply returned from Brain.",
          mode: isOffline ? "offline" : "brain_live",
          error: data.error,
          citations: data.citations,
          suggestedFix: data.suggestedFix ?? data.suggested_fix ?? null,
        };
      }
      return {
        reply:
          "Brain server (:5002) is unreachable — start `uvicorn brain.server:app --port 5002` to chat live.",
        mode: "offline",
        error: `HTTP ${res.status}`,
      };
    } catch (err: unknown) {
      const errDetail =
        err instanceof Error
          ? err.name === "AbortError"
            ? `Timed out after ${BRAIN_HTTP_TIMEOUT_MS / 1000}s`
            : err.message
          : String(err);
      return {
        reply:
          "Brain server (:5002) is unreachable — start `uvicorn brain.server:app --port 5002` to chat live.",
        mode: "offline",
        error: errDetail,
      };
    }
  }

  return {
    mode: "offline",
    reply:
      "Brain server (:5002) is unreachable — start `uvicorn brain.server:app --port 5002` to chat live.",
  };
}
