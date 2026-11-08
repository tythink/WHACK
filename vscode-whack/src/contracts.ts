/**
 * Shared WHACK Contracts for the Native VS Code Mode Extension.
 * Mirrors `core/src/contracts/events.ts` and Role 3's `brain/models.py`.
 */

export type HintLevel = "nudge" | "hint" | "full_explanation";

export type DiagnosisSource = "model" | "offline";

export interface CodeCitation {
  file: string;
  lines: [number, number];
  snippet?: string;
  note?: string;
  replacement?: string;
}

export interface SuggestedFix {
  file?: string;
  lines?: [number, number];
  startLine?: number;
  endLine?: number;
  find?: string;
  oldText?: string;
  replacement: string;
  newText?: string;
}

export interface DiagnosisResponse {
  diagnosisId?: string;
  summary: string;
  file?: string;
  lines?: [number, number];
  quip: string;
  uncertain: boolean;
  confidence?: number;
  hintLevel: HintLevel;
  source: DiagnosisSource;
  error?: string;
  repoPath?: string;
  reasons?: string[];
  explanation?: string;
  citations?: CodeCitation[];
  suggestedFix?: SuggestedFix | null;
  /** Optional pre-rendered tier variants so switching Nudge/Hint/Full Fix is instant */
  tierVariants?: Partial<
    Record<HintLevel, { summary: string; explanation?: string }>
  >;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  mode?: "brain_live" | "offline";
}

export interface ChatResponsePayload {
  reply: string;
  mode: "brain_live" | "offline";
  error?: string;
  citations?: CodeCitation[];
  suggestedFix?: SuggestedFix | null;
}

export interface EditorStateSnapshot {
  activeFile: string;
  cursorLine: number;
  cursorColumn: number;
  selectionText: string;
  diagnosticsCount: number;
  diagnosticsSummary: string;
}

export interface TerminalStateSnapshot {
  command: string;
  exitCode: number;
  durationMs: number;
  tailOutput: string;
}

export interface ActiveWorkspaceContext {
  workspaceRoot?: string;
  activeFile?: string;
  cursorLine?: number;
  cursorColumn?: number;
  selectionText?: string;
  diagnosticsCount?: number;
  diagnosticsSummary?: string;
  terminalCommand?: string;
  terminalExitCode?: number;
  terminalDurationMs?: number;
  terminalTail?: string;
  attemptLog?: string;
  captureScreenshot?: boolean;
}

export interface PassiveStatusSnapshot {
  faceState: "calm" | "confused";
  confusionScore: number;
  cooldownUntil: number | null;
  cameraPaused: boolean;
  userStatusLabel?: string;
  userStatusRaw?: string;
  brainOnline: boolean;
  apiKeyConfigured?: boolean;
  brainProvider?: string;
  brainModel?: string;
  sensorActive?: boolean;
  lastSensorSeenAt?: number;
  emotions?: Record<string, number>;
  gazeZone?: string;
  liveReasons?: string[];
  askingBrain?: boolean;
  coreMode: "external" | "embedded" | "offline";
}

export interface ServiceStackState {
  whackRootFound: boolean;
  whackRootPath: string | null;
  pythonPath: string;
  hasEnvApiKey: boolean;
  configuredProvider: string | null;
  brainManagedRunning: boolean;
  sensorManagedRunning: boolean;
  startingStack: boolean;
}
