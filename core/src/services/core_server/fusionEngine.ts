import {
  BaseEvent,
  EditorPayload,
  FacePayload,
  TerminalPayload,
} from "../../contracts";

export interface FusionDecision {
  shouldNudge: boolean;
  severity: "soft" | "normal";
  combinedScore: number;
  behaviourScore: number;
  faceScore: number;
  reasons: string[];
  attemptLog: string;
}

const WINDOW_MS = 10 * 60 * 1000; // 10-minute rolling window
const MIN_NUDGE_INTERVAL_MS = 45 * 1000; // Prevent back-to-back nudge spam

/**
 * Multi-Signal Fusion Engine & Attempt-Log Summarizer (Lives in TypeScript Core).
 * Consumes `face`, `editor`, and `terminal` events from the ring buffer,
 * computes decaying behavioural + facial scores, evaluates the 4 PDF fusion rules,
 * and builds the compact Attempt-Log summary for Role 3's Brain.
 */
export class FusionEngine {
  private lastNudgeTimestamp = 0;

  public resetNudgeTimer(): void {
    this.lastNudgeTimestamp = 0;
  }

  public evaluate(
    events: BaseEvent[],
    options: { inCooldown: boolean; cameraPaused: boolean }
  ): FusionDecision {
    const now = Date.now();
    const recent = events.filter((e) => now - e.timestamp <= WINDOW_MS);

    // 1. Extract typed payloads in the 10-minute window
    const faceEvents = recent
      .filter((e) => e.source === "face")
      .map((e) => (e.payload || {}) as Partial<FacePayload>);

    const editorEvents = recent
      .filter((e) => e.source === "editor")
      .map((e) => (e.payload || {}) as Partial<EditorPayload>);

    const terminalEvents = recent
      .filter((e) => e.source === "terminal")
      .map((e) => (e.payload || {}) as Partial<TerminalPayload>);

    // 2. Compute Behavioural Signals & Reasons
    const reasons: string[] = [];
    let behaviourScore = 0;

    // Signal A: Repeated terminal failures / same error hash (heaviest weight)
    const failedTerminals = terminalEvents.filter(
      (t) => typeof t.exitCode === "number" && t.exitCode !== 0
    );
    const errorCounts = new Map<string, { count: number; cmd: string }>();
    for (const t of failedTerminals) {
      const key =
        t.errorHash ||
        (t.tailOutput ? t.tailOutput.trim().split("\n").pop() || "err" : "err");
      const prev = errorCounts.get(key) || {
        count: 0,
        cmd: t.command || "command",
      };
      prev.count += 1;
      errorCounts.set(key, prev);
    }

    let maxSameError = 0;
    let repeatedCmd = "test";
    for (const { count, cmd } of errorCounts.values()) {
      if (count > maxSameError) {
        maxSameError = count;
        repeatedCmd = cmd;
      }
    }

    if (maxSameError >= 3) {
      behaviourScore += 0.55;
      reasons.push(`same error repeated ${maxSameError} times (${repeatedCmd})`);
    } else if (maxSameError === 2) {
      behaviourScore += 0.35;
      reasons.push(`same test failed twice (${repeatedCmd})`);
    } else if (failedTerminals.length >= 2) {
      behaviourScore += 0.25;
      reasons.push(`${failedTerminals.length} failing commands in window`);
    }

    // Signal B: Undo / Redo burst
    const undoCount = editorEvents.filter((e) => e.isUndo || e.isRedo).length;
    if (undoCount >= 3) {
      behaviourScore += 0.25;
      reasons.push(`${undoCount} rapid undos`);
    } else if (undoCount === 2) {
      behaviourScore += 0.15;
      reasons.push(`2 recent undos`);
    }

    // Signal C: File ping-pong (switching A -> B -> A -> B)
    const fileSequence: string[] = [];
    for (const e of editorEvents) {
      if (
        e.activeFile &&
        fileSequence[fileSequence.length - 1] !== e.activeFile
      ) {
        fileSequence.push(e.activeFile);
      }
    }
    const uniqueFiles = new Set(fileSequence);
    if (fileSequence.length >= 4 && uniqueFiles.size >= 2) {
      behaviourScore += 0.15;
      reasons.push(`file ping-pong across ${uniqueFiles.size} files`);
    }

    // Signal D: Unchanged/elevated diagnostics count
    const latestEditor = editorEvents[editorEvents.length - 1];
    if (
      latestEditor &&
      typeof latestEditor.diagnosticsCount === "number" &&
      latestEditor.diagnosticsCount >= 2
    ) {
      behaviourScore += 0.12;
      reasons.push(`${latestEditor.diagnosticsCount} active editor errors`);
    }

    // Signal E: Long cursor dwell with no edits
    if (
      latestEditor &&
      typeof latestEditor.cursorDwellMs === "number" &&
      latestEditor.cursorDwellMs >= 4500
    ) {
      behaviourScore += 0.12;
      reasons.push(
        `cursor dwell ${(latestEditor.cursorDwellMs / 1000).toFixed(1)}s`
      );
    }

    behaviourScore = Math.min(1, behaviourScore);

    // 3. Compute Facial Confusion Score
    let faceScore = 0;
    let sustainedConfusedCount = 0;
    if (!options.cameraPaused && faceEvents.length > 0) {
      const latestFace = faceEvents[faceEvents.length - 1];
      faceScore =
        typeof latestFace.score === "number"
          ? Math.max(0, Math.min(1, latestFace.score))
          : 0;

      const tailFaces = faceEvents.slice(-3);
      sustainedConfusedCount = tailFaces.filter(
        (f) => f.state === "confused" || (typeof f.score === "number" && f.score >= 0.65)
      ).length;

      if (faceScore >= 0.65) {
        reasons.push(`furrowed brow (${faceScore.toFixed(2)})`);
      }
    }

    // Combined fusion index (weighted blend of face + behaviour, or max when one dominates)
    const combinedScore = options.cameraPaused
      ? behaviourScore
      : Number(
          Math.min(
            1,
            Math.max(
              faceScore * 0.45 + behaviourScore * 0.55,
              behaviourScore * 0.9,
              faceScore * 0.85
            )
          ).toFixed(2)
        );

    // 4. Evaluate the 4 PDF Fusion Rules
    let shouldNudge = false;
    let severity: "soft" | "normal" = "normal";

    const recentlyNudged =
      now - this.lastNudgeTimestamp < MIN_NUDGE_INTERVAL_MS;

    if (!options.inCooldown && !recentlyNudged) {
      // Rule 1: Behaviour score very high on its own -> normal nudge
      if (behaviourScore >= 0.72) {
        shouldNudge = true;
        severity = "normal";
      }
      // Rule 2: Face sustained AND behaviour score moderate -> normal nudge
      else if (
        !options.cameraPaused &&
        faceScore >= 0.65 &&
        sustainedConfusedCount >= 1 &&
        behaviourScore >= 0.35
      ) {
        shouldNudge = true;
        severity = "normal";
      }
      // Rule 3: Face alone very high for a sustained period -> soft nudge only
      else if (
        !options.cameraPaused &&
        faceScore >= 0.82 &&
        sustainedConfusedCount >= 3
      ) {
        shouldNudge = true;
        severity = "soft";
      }
    }

    if (shouldNudge) {
      this.lastNudgeTimestamp = now;
    }

    const attemptLog = this.summarizeAttemptLog(
      editorEvents,
      terminalEvents,
      maxSameError,
      undoCount
    );

    return {
      shouldNudge,
      severity,
      combinedScore,
      behaviourScore: Number(behaviourScore.toFixed(2)),
      faceScore: Number(faceScore.toFixed(2)),
      reasons,
      attemptLog,
    };
  }

  public summarizeAttemptLog(
    editorEvents: Partial<EditorPayload>[],
    terminalEvents: Partial<TerminalPayload>[],
    maxSameError: number,
    undoCount: number
  ): string {
    const parts: string[] = [];

    if (terminalEvents.length > 0) {
      const failCount = terminalEvents.filter(
        (t) => typeof t.exitCode === "number" && t.exitCode !== 0
      ).length;
      const lastCmd =
        terminalEvents[terminalEvents.length - 1].command || "test command";
      if (maxSameError >= 2) {
        parts.push(
          `ran '${lastCmd}' ${terminalEvents.length} times (${maxSameError} failing with the same error)`
        );
      } else {
        parts.push(
          `ran ${terminalEvents.length} terminal command(s) (${failCount} failed, latest: '${lastCmd}')`
        );
      }
    }

    if (editorEvents.length > 0) {
      const fileEdits = new Map<string, number>();
      for (const e of editorEvents) {
        const f = e.activeFile || "unknown";
        fileEdits.set(f, (fileEdits.get(f) || 0) + 1);
      }
      const fileSummary = Array.from(fileEdits.entries())
        .map(([file, count]) => `edited ${file} ${count}x`)
        .join(", ");
      parts.push(fileSummary);
    }

    if (undoCount > 0) {
      parts.push(`${undoCount} undo(s)`);
    }

    if (parts.length === 0) {
      return "Last 10 minutes: no failed commands or rapid undo loops recorded yet.";
    }

    return `Last 10 minutes: ${parts.join("; ")}.`;
  }
}
