/**
 * Shared WHACK Contracts for the Native VS Code Mode Extension.
 * Mirrors `core/src/contracts/events.ts` and Role 3's `brain/models.py`.
 */

export type HintLevel = "nudge" | "hint" | "full_explanation";

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
  suggestedFix?: SuggestedFix;
  /** Optional pre-rendered tier variants so switching Nudge/Hint/Full Fix is instant */
  tierVariants?: Partial<
    Record<HintLevel, { summary: string; explanation?: string }>
  >;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  mode?: "brain_live" | "mock_fallback";
}

export interface PassiveStatusSnapshot {
  faceState: "calm" | "confused";
  confusionScore: number;
  cooldownUntil: number | null;
  cameraPaused: boolean;
  userStatusLabel?: string;
  brainOnline: boolean;
  coreMode: "external" | "embedded" | "offline";
}
