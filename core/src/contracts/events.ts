/**
 * WHACK — Shared Event & API Contracts (Single Source of Truth)
 * Owned by: Role 4 — Frontend & Integration Lead (@frontend-core)
 *
 * Every module (Face, Signals, Brain, Core Server, Electron HUD) uses
 * these exact JSON shapes to communicate over HTTP (localhost:3000)
 * and WebSocket (ws://localhost:3000/ws).
 */

export type EventSource = "face" | "editor" | "terminal" | "hotkey" | "brain";

export type HintLevel = "nudge" | "hint" | "full_explanation";

/**
 * 1. THE ENVELOPE (`BaseEvent`)
 * Every HTTP POST to `http://localhost:3000/events` wraps its data in this object.
 */
export interface BaseEvent<T = unknown> {
  source: EventSource;
  type: string;
  timestamp: number;
  payload: T;
}

/**
 * 2. FACE SENSOR PAYLOAD (Sent by Role 1: @face-fusion-lead)
 * Emitted on confusion state change + every ~3s heartbeat.
 */
export interface FacePayload {
  state: "calm" | "confused";
  score: number; // 0.0 - 1.0 (smoothed brow/squint index)
  confidence: number; // 0.0 - 1.0
}

/**
 * 3. EDITOR SENSOR PAYLOAD (Sent by Role 2: @signals-lead)
 * Emitted by the VS Code extension when editing, undoing, or dwelling.
 */
export interface EditorPayload {
  activeFile: string;
  isUndo: boolean;
  isRedo: boolean;
  diagnosticsCount: number;
  cursorDwellMs: number;
  topDiagnostics?: string[];
  cursorLine?: number;
  cursorColumn?: number;
  selectionText?: string;
}

/**
 * 4. TERMINAL SENSOR PAYLOAD (Sent by Role 2: @signals-lead)
 * Emitted when a terminal command finishes running.
 */
export interface TerminalPayload {
  command: string;
  exitCode: number;
  durationMs: number;
  errorHash?: string;
  tailOutput: string;
}

/**
 * 5. HOTKEY / MANUAL TRIGGER PAYLOAD (Sent by Electron Global Hotkey Ctrl+Shift+K)
 */
export interface HotkeyPayload {
  shortcut: string;
  hintLevel?: HintLevel;
}

/**
 * 6. BRAIN DIAGNOSIS REQUEST (Sent from Core Server -> Role 3: @brain-lead on :5002)
 */
export interface DiagnosisRequest {
  triggerType: "hotkey" | "auto_nudge";
  hintLevel: HintLevel;
  fusionReasons?: string[];
  attemptLog?: string | BaseEvent[];
  lastKnownGoodDiff?: string;
  screenshot?: string;
  screenshotPath?: string;
  editorState?: EditorPayload | null;
  terminalState?: TerminalPayload | null;
  latestFace?: FacePayload | null;
  latestEditor?: EditorPayload | null;
  latestTerminal?: TerminalPayload | null;
  recentEvents?: BaseEvent[];
  stream?: boolean;
}

export interface CodeCitation {
  file: string;
  lines: [number, number];
  snippet?: string;
  note?: string;
}

/**
 * 7. BRAIN DIAGNOSIS RESPONSE (Returned by Role 3 -> Core Server -> Electron UI)
 */
export interface DiagnosisResponse {
  summary: string;
  file?: string;
  lines?: [number, number];
  quip: string;
  uncertain: boolean;
  hintLevel: HintLevel;
  reasons?: string[];
  confidence?: number;
  explanation?: string;
  citations?: CodeCitation[];
}

/**
 * 8. USER FEEDBACK PAYLOAD (Sent from Electron UI -> `POST /feedback`)
 */
export interface FeedbackPayload {
  action: "dismiss" | "not_confused" | "helpful" | "snooze" | "toggle_camera";
  timestamp: number;
  cooldownMs?: number;
  cameraPaused?: boolean;
}

/**
 * 9. WEBSOCKET MESSAGES (`ws://localhost:3000/ws` -> Electron UI)
 */
export type WsMessage =
  | {
      type: "status";
      timestamp: number;
      faceState: "calm" | "confused";
      confusionScore: number;
      cooldownUntil: number | null;
      cameraPaused: boolean;
      eventCount: number;
    }
  | {
      type: "event_ingest";
      event: BaseEvent;
    }
  | {
      type: "nudge";
      timestamp: number;
      severity: "soft" | "normal";
      reasons: string[];
      score: number;
    }
  | {
      type: "diagnosis_chunk";
      chunk: string;
    }
  | {
      type: "diagnosis_done";
      diagnosis: DiagnosisResponse;
    };
