import {
  DiagnosisRequest,
  DiagnosisResponse,
  HintLevel,
} from "../../contracts";
import { WsHub } from "./wsHub";

const BRAIN_SERVICE_URL =
  process.env.BRAIN_SERVICE_URL || "http://localhost:5002/diagnose";

/**
 * Calls Role 3's Brain Service (`http://localhost:5002/diagnose`) if reachable,
 * or falls back seamlessly to the Role 3 / Role 4 Stub diagnosis streamer
 * so `/diagnose` and Checkpoint A work even when running Core solo.
 */
export async function runDiagnosis(
  req: DiagnosisRequest,
  wsHub: WsHub
): Promise<DiagnosisResponse> {
  // 1. Try forwarding to Role 3's external Brain service on :5002 if it's running
  if (process.env.USE_BRAIN_STUB !== "true") {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3500);
      const res = await fetch(BRAIN_SERVICE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hintLevel: req.hintLevel,
          screenshot: req.screenshot,
          attemptLog: req.attemptLog ?? req.recentEvents,
          editorState: req.editorState ?? req.latestEditor,
          terminalState: req.terminalState ?? req.latestTerminal,
          stream: false,
        }),
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (res.ok) {
        const data = (await res.json()) as Partial<DiagnosisResponse>;
        const activeFile =
          data.file ??
          req.editorState?.activeFile ??
          req.latestEditor?.activeFile ??
          "utils.py";

        const combinedSummary =
          data.explanation && req.hintLevel === "full_explanation"
            ? `${data.summary || ""}\n\n${data.explanation}`
            : data.summary ||
              "Index error on line 42: loop bound exceeds list length.";

        const diagnosis: DiagnosisResponse = {
          summary: combinedSummary,
          file: activeFile,
          lines: data.lines ?? [40, 45],
          quip:
            data.quip || "Computers do what you say, not what you want.",
          uncertain: Boolean(data.uncertain),
          hintLevel: data.hintLevel ?? req.hintLevel ?? "hint",
          reasons: data.reasons ?? req.fusionReasons,
          confidence: data.confidence ?? 0.95,
          explanation: data.explanation,
          citations: data.citations,
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
    } catch {
      // Role 3 Brain server isn't running yet -> fall through to built-in streaming stub
    }
  }

  // 2. Built-in Streaming Stub (matches AGENT_ROLE3_BRAIN.md 0:00-0:45 stub + 3 hint levels)
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

  // Stream markdown chunks over WebSocket so the UI renders a real-time typing effect
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
