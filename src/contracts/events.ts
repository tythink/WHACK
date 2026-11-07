/**
 * WHACK - Shared Event Contracts
 * Standard event payloads across Stream A (Frontend), Stream B (Emotion), Stream C (Action), and Stream D (Fusion/Orchestrator).
 * NOTE: DO NOT MODIFY without cross-stream consensus.
 */

export type DominantEmotion = 'neutral' | 'calm' | 'frustration' | 'anger' | 'stress' | 'panic';

export interface EmotionEvent {
  timestamp: number; // Unix epoch timestamp (ms)
  faceDetected: boolean;
  dominantEmotion: DominantEmotion;
  stressIndex: number; // 0.0 to 1.0 (Exponential Moving Average)
  confidence: number; // 0.0 to 1.0 (clamped >= 0.5)
  emotions: {
    neutral: number;
    frustration: number;
    anger: number;
    stress: number;
    panic: number;
    [key: string]: number;
  };
  landmarks?: {
    faceFound: boolean;
    box?: [number, number, number, number]; // [x, y, w, h] normalized 0..1
  };
  fps: number;
}

export interface ActionEvent {
  timestamp: number; // Unix epoch timestamp (ms)
  tabSwitchRatePerMin: number;
  panicScore: number; // 0.0 to 1.0
  clickBursts: number;
  jitterScore: number; // 0.0 to 1.0
  activeWindow: string;
}

export type TakeoverStatus =
  | 'monitoring'
  | 'arming'
  | 'takeover_triggered'
  | 'executing'
  | 'completed'
  | 'cancelled';

export interface TakeoverTriggerEvent {
  timestamp: number;
  fusionScore: number; // (w1 * stressIndex) + (w2 * panicScore), 0.0 to 1.0
  stressIndex: number;
  panicScore: number;
  weights: {
    w1: number; // emotion weight, e.g. 0.5
    w2: number; // action weight, e.g. 0.5
  };
  threshold: number; // default 0.8
  isTriggered: boolean; // fusionScore > threshold
  status: TakeoverStatus;
  agentSessionId?: string;
  activeWindow?: string;
  taskSummary?: string;
  suggestedPrompt?: string;
}
