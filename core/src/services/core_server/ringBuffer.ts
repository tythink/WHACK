import {
  BaseEvent,
  EditorPayload,
  FacePayload,
  FeedbackPayload,
  TerminalPayload,
} from "../../contracts";

const DEFAULT_CAPACITY = 100;

/**
 * In-memory ring buffer storing the last 100 events ingested at `POST /events`,
 * plus the latest snapshot of Workspace, Face, Editor, and Terminal states.
 */
export class EventRingBuffer {
  private readonly capacity: number;
  private events: BaseEvent[] = [];

  private workspaceRoot: string | null = null;
  private latestFace: FacePayload | null = null;
  private latestEditor: EditorPayload | null = null;
  private latestTerminal: TerminalPayload | null = null;

  private cooldownUntil: number | null = null;
  private cameraPaused = false;
  private scoreThresholdOffset = 0; // Nudged upwards when user clicks "I'm not confused"

  constructor(capacity = DEFAULT_CAPACITY) {
    this.capacity = capacity;
  }

  public push(event: BaseEvent): void {
    this.events.push(event);
    if (this.events.length > this.capacity) {
      this.events.shift();
    }

    const payload = (event.payload || {}) as Record<string, unknown>;

    if (event.source === "face") {
      if (!this.cameraPaused) {
        const rawScore =
          typeof payload.score === "number"
            ? payload.score
            : typeof payload.smoothedScore === "number"
            ? payload.smoothedScore
            : 0;
        const adjustedScore = Math.max(
          0,
          Math.min(1, rawScore - this.scoreThresholdOffset)
        );
        this.latestFace = {
          state: payload.state === "confused" ? "confused" : "calm",
          score: adjustedScore,
          confidence:
            typeof payload.confidence === "number" ? payload.confidence : 0.8,
        };
      }
    } else if (event.source === "editor") {
      if (typeof payload.workspaceRoot === "string" && payload.workspaceRoot.trim()) {
        this.workspaceRoot = payload.workspaceRoot.trim();
      }
      this.latestEditor = {
        workspaceRoot: this.workspaceRoot ?? undefined,
        activeFile:
          typeof payload.activeFile === "string"
            ? payload.activeFile
            : "unknown",
        isUndo: Boolean(payload.isUndo),
        isRedo: Boolean(payload.isRedo),
        diagnosticsCount:
          typeof payload.diagnosticsCount === "number"
            ? payload.diagnosticsCount
            : 0,
        diagnosticsSummary:
          typeof payload.diagnosticsSummary === "string"
            ? payload.diagnosticsSummary
            : undefined,
        cursorDwellMs:
          typeof payload.cursorDwellMs === "number" ? payload.cursorDwellMs : 0,
        topDiagnostics: Array.isArray(payload.topDiagnostics)
          ? (payload.topDiagnostics as string[])
          : undefined,
        cursorLine:
          typeof payload.cursorLine === "number"
            ? payload.cursorLine
            : undefined,
        cursorColumn:
          typeof payload.cursorColumn === "number"
            ? payload.cursorColumn
            : undefined,
        selectionText:
          typeof payload.selectionText === "string"
            ? payload.selectionText
            : undefined,
      };
    } else if (event.source === "terminal") {
      this.latestTerminal = {
        command: typeof payload.command === "string" ? payload.command : "",
        exitCode: typeof payload.exitCode === "number" ? payload.exitCode : 0,
        durationMs:
          typeof payload.durationMs === "number" ? payload.durationMs : 0,
        errorHash:
          typeof payload.errorHash === "string" ? payload.errorHash : undefined,
        tailOutput:
          typeof payload.tailOutput === "string" ? payload.tailOutput : "",
      };
    }
  }

  public updateContext(ctx: {
    workspaceRoot?: string | null;
    editorState?: Partial<EditorPayload> | null;
    terminalState?: Partial<TerminalPayload> | null;
  }): void {
    if (typeof ctx.workspaceRoot === "string" && ctx.workspaceRoot.trim()) {
      this.workspaceRoot = ctx.workspaceRoot.trim();
    }

    if (ctx.editorState && typeof ctx.editorState === "object") {
      const ed = ctx.editorState;
      this.latestEditor = {
        workspaceRoot: this.workspaceRoot ?? this.latestEditor?.workspaceRoot,
        activeFile:
          typeof ed.activeFile === "string"
            ? ed.activeFile
            : this.latestEditor?.activeFile ?? "",
        isUndo: Boolean(ed.isUndo ?? this.latestEditor?.isUndo),
        isRedo: Boolean(ed.isRedo ?? this.latestEditor?.isRedo),
        diagnosticsCount:
          typeof ed.diagnosticsCount === "number"
            ? ed.diagnosticsCount
            : this.latestEditor?.diagnosticsCount ?? 0,
        diagnosticsSummary:
          typeof ed.diagnosticsSummary === "string"
            ? ed.diagnosticsSummary
            : this.latestEditor?.diagnosticsSummary ?? "",
        cursorDwellMs:
          typeof ed.cursorDwellMs === "number"
            ? ed.cursorDwellMs
            : this.latestEditor?.cursorDwellMs ?? 0,
        topDiagnostics: ed.topDiagnostics ?? this.latestEditor?.topDiagnostics,
        cursorLine:
          typeof ed.cursorLine === "number"
            ? ed.cursorLine
            : this.latestEditor?.cursorLine ?? 1,
        cursorColumn:
          typeof ed.cursorColumn === "number"
            ? ed.cursorColumn
            : this.latestEditor?.cursorColumn ?? 1,
        selectionText:
          typeof ed.selectionText === "string"
            ? ed.selectionText
            : this.latestEditor?.selectionText ?? "",
      };
    }

    if (ctx.terminalState && typeof ctx.terminalState === "object") {
      const tm = ctx.terminalState;
      if (typeof tm.command === "string") {
        this.latestTerminal = {
          command: tm.command,
          exitCode: typeof tm.exitCode === "number" ? tm.exitCode : 0,
          durationMs: typeof tm.durationMs === "number" ? tm.durationMs : 0,
          errorHash: tm.errorHash ?? this.latestTerminal?.errorHash,
          tailOutput:
            typeof tm.tailOutput === "string"
              ? tm.tailOutput
              : this.latestTerminal?.tailOutput ?? "",
        };
      }
    }
  }

  public getWorkspaceRoot(): string | null {
    return this.workspaceRoot;
  }

  public applyFeedback(feedback: FeedbackPayload): void {
    const duration = feedback.cooldownMs ?? 3 * 60 * 1000; // Default 3-minute cooldown
    if (
      feedback.action === "dismiss" ||
      feedback.action === "not_confused" ||
      feedback.action === "snooze"
    ) {
      this.cooldownUntil = Date.now() + duration;
    }

    if (feedback.action === "not_confused") {
      // Nudge threshold slightly so we don't false-positive on concentration
      this.scoreThresholdOffset = Math.min(
        0.25,
        this.scoreThresholdOffset + 0.05
      );
      if (this.latestFace) {
        this.latestFace = {
          ...this.latestFace,
          state: "calm",
          score: Math.max(0, this.latestFace.score - 0.2),
        };
      }
    }

    if (feedback.action === "toggle_camera") {
      this.cameraPaused =
        typeof feedback.cameraPaused === "boolean"
          ? feedback.cameraPaused
          : !this.cameraPaused;
    }
  }

  public isInCooldown(): boolean {
    if (!this.cooldownUntil) return false;
    if (Date.now() >= this.cooldownUntil) {
      this.cooldownUntil = null;
      return false;
    }
    return true;
  }

  public getAll(): BaseEvent[] {
    return [...this.events];
  }

  public getSnapshot() {
    return {
      workspaceRoot: this.workspaceRoot,
      faceState: this.latestFace?.state ?? "calm",
      confusionScore: this.latestFace?.score ?? 0,
      latestFace: this.latestFace,
      latestEditor: this.latestEditor,
      latestTerminal: this.latestTerminal,
      cooldownUntil: this.isInCooldown() ? this.cooldownUntil : null,
      cameraPaused: this.cameraPaused,
      eventCount: this.events.length,
    };
  }
}
