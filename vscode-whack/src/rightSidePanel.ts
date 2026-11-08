import * as path from "path";
import * as vscode from "vscode";
import {
  ChatMessage,
  CodeCitation,
  DiagnosisResponse,
  HintLevel,
  PassiveStatusSnapshot,
  ServiceStackState,
} from "./contracts";

export type ChimeKind = "diagnosis" | "uncertain" | "applied";

export interface PanelActionHandlers {
  onSelectHintLevel: (level: HintLevel) => Promise<void>;
  onJumpToCitation: (citation: CodeCitation) => Promise<void>;
  onPreviewDiff: () => Promise<void>;
  onApplyFix: () => Promise<void>;
  onTriggerDiagnose: () => Promise<void>;
  onStartStack: () => Promise<void>;
  onStopStack: () => Promise<void>;
  onToggleSensorHud: () => Promise<void>;
  onConfigureApiKey: () => Promise<void>;
  onShowLogs: () => void;
  onDismiss: () => Promise<void>;
  onTakeBreak?: () => Promise<void>;
  onSendChatMessage: (text: string) => Promise<void>;
  onToggleSound?: (enabled: boolean) => void;
}

/**
 * Redesigned Right-Hand "Chef's Pass & Live Mission Control" Panel (`vscode.ViewColumn.Beside`).
 *
 * Combines:
 * 1. 1-Click Live Stack Control (`Start Live Stack`, `Sensor HUD`, `Set API Key`, `Diagnose`)
 * 2. Real-time Multimodal Affect & Status Telemetry Strip (`Confused`, `Frustrated`, `Flow`)
 * 3. Gordon Ramsay's "Chef's Pass" Diagnosis Card (`Nudge / Hint / Full Fix`, citations, 1-click diff/fix)
 * 4. Persistent Mentoring Chat (`POST /chat`)
 */
export class WhackRightSidePanel implements vscode.Disposable {
  private panel: vscode.WebviewPanel | null = null;
  private currentDiagnosis: DiagnosisResponse | null = null;
  private currentSourceLabel = "Live AI";
  private brainOnline = false;
  private hasFix = false;
  private soundEnabled = true;
  private pendingChime: ChimeKind | null = null;
  private chatHistory: ChatMessage[] = [];
  private liveStatus: PassiveStatusSnapshot | null = null;
  private stackState: ServiceStackState | null = null;

  constructor(private readonly handlers: PanelActionHandlers) {}

  public get isVisible(): boolean {
    return Boolean(this.panel && this.panel.visible);
  }

  public get isSoundEnabled(): boolean {
    return this.soundEnabled;
  }

  public setSoundEnabled(enabled: boolean): void {
    this.soundEnabled = enabled;
    this.pushStateToWebview();
  }

  public setBrainOnline(online: boolean): void {
    if (this.brainOnline !== online) {
      this.brainOnline = online;
      this.pushStateToWebview();
    }
  }

  public updateLiveTelemetry(
    status: PassiveStatusSnapshot,
    stack?: ServiceStackState
  ): void {
    this.liveStatus = status;
    this.brainOnline = status.brainOnline;
    if (stack) {
      this.stackState = stack;
    }
    this.pushStateToWebview();
  }

  public updateStackState(stack: ServiceStackState): void {
    this.stackState = stack;
    this.pushStateToWebview();
  }

  public playChime(kind: ChimeKind): void {
    if (!this.soundEnabled) return;
    if (this.panel) {
      this.panel.webview.postMessage({ type: "play_chime", kind });
    } else {
      this.pendingChime = kind;
    }
  }

  public showDiagnosis(
    diagnosis: DiagnosisResponse,
    options: {
      sourceLabel: string;
      brainOnline: boolean;
      hasFix: boolean;
      resetChat?: boolean;
      playSound?: boolean;
    }
  ): void {
    this.currentDiagnosis = diagnosis;
    this.currentSourceLabel = options.sourceLabel;
    this.brainOnline = diagnosis.source === "model" && options.brainOnline;
    this.hasFix = options.hasFix && diagnosis.source === "model";

    if (
      options.playSound &&
      this.soundEnabled &&
      diagnosis.source !== "offline"
    ) {
      this.pendingChime = diagnosis.uncertain ? "uncertain" : "diagnosis";
    }

    if (options.resetChat) {
      if (diagnosis.source === "offline") {
        this.chatHistory = [
          {
            role: "assistant",
            content:
              "Brain (:5002) isn't cooking yet! Click **🔑 API Key** (if you haven't set one) and **🚀 Launch Live Stack** at the top of this panel — no terminal commands needed.",
            timestamp: Date.now(),
            mode: "offline",
          },
        ];
      } else {
        const citeCount = diagnosis.citations?.length || (diagnosis.file ? 1 : 0);
        const fileNote = diagnosis.file
          ? ` in \`${path.basename(diagnosis.file)}\``
          : "";
        this.chatHistory = [
          {
            role: "assistant",
            content:
              (citeCount > 0
                ? `I highlighted **${citeCount} location(s)**${fileNote}. `
                : `I inspected your live workspace context. `) +
              `Use **Nudge / Hint / Full Fix** above, click any line pill to jump there, or ask me a follow-up below.`,
            timestamp: Date.now(),
            mode: "brain_live",
          },
        ];
      }
    }

    this.ensurePanelOpen();
    this.pushStateToWebview();
  }

  public updateDiagnosisInPlace(
    diagnosis: DiagnosisResponse,
    options: { brainOnline: boolean; hasFix: boolean }
  ): void {
    this.currentDiagnosis = diagnosis;
    this.brainOnline = options.brainOnline;
    this.hasFix = options.hasFix && diagnosis.source === "model";
    this.pushStateToWebview();
  }

  public clearDiagnosisCard(): void {
    this.currentDiagnosis = null;
    this.hasFix = false;
    this.pushStateToWebview();
  }

  public appendChatMessage(msg: ChatMessage): void {
    this.chatHistory.push(msg);
    this.pushStateToWebview();
  }

  public setChatThinking(thinking: boolean): void {
    this.panel?.webview.postMessage({ type: "chat_thinking", thinking });
  }

  public focusChatInput(): void {
    this.ensurePanelOpen();
    this.pushStateToWebview();
    this.panel?.webview.postMessage({ type: "focus_chat" });
  }

  public reveal(): void {
    this.ensurePanelOpen();
    this.pushStateToWebview();
  }

  public close(): void {
    if (this.panel) {
      const p = this.panel;
      this.panel = null;
      p.dispose();
    }
  }

  public toggle(): void {
    if (this.panel) {
      this.close();
    } else {
      this.ensurePanelOpen();
      this.pushStateToWebview();
    }
  }

  private ensurePanelOpen(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Two, true);
      return;
    }

    this.panel = vscode.window.createWebviewPanel(
      "whackBrainAssistant",
      "WHACK · Chef's Pass",
      {
        viewColumn: vscode.ViewColumn.Beside,
        preserveFocus: true,
      },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      }
    );

    this.panel.webview.html = this.buildHtml();

    this.panel.webview.onDidReceiveMessage(async (msg) => {
      if (!msg || typeof msg !== "object") return;
      switch (msg.type) {
        case "ready":
          this.pushStateToWebview();
          break;
        case "set_hint_level":
          await this.handlers.onSelectHintLevel(msg.level as HintLevel);
          break;
        case "jump_citation":
          if (msg.citation) {
            await this.handlers.onJumpToCitation(msg.citation as CodeCitation);
          }
          break;
        case "preview_diff":
          await this.handlers.onPreviewDiff();
          break;
        case "apply_fix":
          await this.handlers.onApplyFix();
          break;
        case "trigger_diagnose":
          await this.handlers.onTriggerDiagnose();
          break;
        case "start_stack":
          await this.handlers.onStartStack();
          break;
        case "stop_stack":
          await this.handlers.onStopStack();
          break;
        case "toggle_sensor":
          await this.handlers.onToggleSensorHud();
          break;
        case "configure_key":
          await this.handlers.onConfigureApiKey();
          break;
        case "show_logs":
          this.handlers.onShowLogs();
          break;
        case "dismiss":
          await this.handlers.onDismiss();
          break;
        case "take_break":
          if (this.handlers.onTakeBreak) {
            await this.handlers.onTakeBreak();
          } else {
            await this.handlers.onDismiss();
          }
          break;
        case "toggle_sound":
          this.soundEnabled = Boolean(msg.enabled);
          this.handlers.onToggleSound?.(this.soundEnabled);
          this.pushStateToWebview();
          break;
        case "chat_send":
          if (typeof msg.text === "string" && msg.text.trim()) {
            await this.handlers.onSendChatMessage(msg.text.trim());
          }
          break;
      }
    });

    this.panel.onDidDispose(() => {
      this.panel = null;
    });
  }

  private pushStateToWebview(): void {
    if (!this.panel) return;
    const d = this.currentDiagnosis;
    const normalizedCitations =
      d && d.source !== "offline"
        ? (d.citations ?? []).map((c) => ({
            ...c,
            shortFile: path.basename(c.file),
          }))
        : [];

    const chimeToPlay = this.pendingChime;
    this.pendingChime = null;

    this.panel.webview.postMessage({
      type: "state_sync",
      diagnosis: d,
      citations: normalizedCitations,
      sourceLabel: this.currentSourceLabel,
      brainOnline: this.brainOnline,
      hasFix: this.hasFix,
      soundEnabled: this.soundEnabled,
      chime: chimeToPlay,
      chatHistory: this.chatHistory,
      liveStatus: this.liveStatus,
      stackState: this.stackState,
    });
  }

  public buildHtml(): string {
    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WHACK Chef's Pass</title>
  <style>
    :root {
      --bg: var(--vscode-sideBar-background, #111216);
      --surface: var(--vscode-editorWidget-background, #191b22);
      --surface-elevated: rgba(255, 255, 255, 0.045);
      --subtle: rgba(255, 255, 255, 0.03);
      --border: var(--vscode-panel-border, rgba(255, 255, 255, 0.1));
      --fg: var(--vscode-foreground, #f4f4f6);
      --muted: var(--vscode-descriptionForeground, #9ca3af);
      --coral: #f95738;
      --coral-soft: rgba(249, 87, 56, 0.15);
      --amber: #f59e0b;
      --amber-soft: rgba(245, 158, 11, 0.16);
      --emerald: #10b981;
      --emerald-soft: rgba(16, 185, 129, 0.16);
      --cyan: #38bdf8;
      --cyan-soft: rgba(56, 189, 248, 0.16);
      --input-bg: var(--vscode-input-background, #21242d);
      --input-fg: var(--vscode-input-foreground, #f4f4f6);
      --input-border: var(--vscode-input-border, rgba(255, 255, 255, 0.15));
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
      background: var(--bg);
      color: var(--fg);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      font-size: 12.5px;
      line-height: 1.45;
    }

    /* 1. Brand & Live Status Header */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
      gap: 8px;
    }

    .brand-group {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex-wrap: wrap;
    }

    .brand-logo {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-weight: 800;
      font-size: 12px;
      letter-spacing: 0.06em;
      color: var(--fg);
    }

    .brand-flame {
      color: var(--coral);
      font-size: 13px;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 10px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--subtle);
      color: var(--muted);
      white-space: nowrap;
    }

    .status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #6b7280;
    }

    .status-pill.live {
      color: var(--emerald);
      background: var(--emerald-soft);
      border-color: rgba(16, 185, 129, 0.35);
    }
    .status-pill.live .status-dot {
      background: var(--emerald);
      box-shadow: 0 0 6px var(--emerald);
    }

    .status-pill.warn {
      color: var(--amber);
      background: var(--amber-soft);
      border-color: rgba(245, 158, 11, 0.35);
    }
    .status-pill.warn .status-dot {
      background: var(--amber);
    }

    .status-pill.sensor {
      color: var(--cyan);
      background: var(--cyan-soft);
      border-color: rgba(56, 189, 248, 0.35);
    }
    .status-pill.sensor .status-dot {
      background: var(--cyan);
      box-shadow: 0 0 6px var(--cyan);
    }

    .header-icons {
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .icon-btn {
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 3px 7px;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.12s ease;
    }
    .icon-btn:hover {
      color: var(--fg);
      background: var(--surface-elevated);
    }
    .icon-btn.muted {
      opacity: 0.55;
    }

    /* 2. 1-Click Live Stack Control Bar */
    .control-bar {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.02), transparent);
      flex-wrap: wrap;
    }

    .ctrl-btn {
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--fg);
      border-radius: 6px;
      padding: 5px 9px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      transition: all 0.12s ease;
    }
    .ctrl-btn:hover {
      background: var(--surface-elevated);
      border-color: rgba(255, 255, 255, 0.24);
    }

    .ctrl-btn.launch {
      background: var(--emerald);
      color: #06281e;
      border-color: transparent;
      font-weight: 700;
    }
    .ctrl-btn.launch:hover {
      filter: brightness(1.08);
    }

    .ctrl-btn.stop {
      background: rgba(239, 68, 68, 0.15);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.35);
    }

    .ctrl-btn.diagnose {
      background: var(--coral-soft);
      color: #ff856d;
      border-color: rgba(249, 87, 56, 0.38);
      font-weight: 700;
      margin-left: auto;
    }
    .ctrl-btn.diagnose:hover {
      background: rgba(249, 87, 56, 0.26);
    }

    /* 3. Live Multimodal Feed Strip */
    .feed-strip {
      padding: 7px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .feed-state {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }

    .feed-badge {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 800;
      background: var(--emerald-soft);
      color: var(--emerald);
    }
    .feed-badge.thinking {
      background: var(--amber-soft);
      color: var(--amber);
    }
    .feed-badge.stuck {
      background: var(--coral-soft);
      color: var(--coral);
    }
    .feed-badge.afk {
      background: rgba(148, 163, 184, 0.16);
      color: #cbd5e1;
    }
    .feed-badge.tired {
      background: rgba(192, 132, 252, 0.18);
      color: #d8b4fe;
    }

    .mini-meters {
      display: flex;
      align-items: center;
      gap: 7px;
      flex: 1;
      justify-content: flex-end;
      max-width: 268px;
    }

    .mini-meter {
      display: flex;
      flex-direction: column;
      gap: 2px;
      width: 56px;
    }
    .mini-meter-lbl {
      display: flex;
      justify-content: space-between;
      font-size: 9px;
      color: var(--muted);
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .mini-track {
      height: 4px;
      border-radius: 99px;
      background: rgba(255, 255, 255, 0.09);
      overflow: hidden;
    }
    .mini-fill {
      height: 100%;
      border-radius: 99px;
      transition: width 0.25s ease;
    }

    /* 4. Scrollable Main Stage */
    .scroll-body {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* Empty / Standby Onboarding Card */
    .standby-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .standby-title {
      font-size: 13px;
      font-weight: 700;
      color: var(--fg);
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .standby-sub {
      font-size: 11.5px;
      color: var(--muted);
    }

    .step-list {
      display: flex;
      flex-direction: column;
      gap: 7px;
      margin-top: 2px;
    }

    .step-item {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 7px 9px;
      border-radius: 7px;
      background: var(--subtle);
      border: 1px solid rgba(255, 255, 255, 0.05);
      font-size: 11.5px;
    }

    .step-num {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--coral-soft);
      color: var(--coral);
      font-size: 10px;
      font-weight: 800;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      margin-top: 1px;
    }

    /* Segmented Hint Tier Switcher */
    .segmented {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 3px;
      background: var(--surface);
      padding: 3px;
      border-radius: 8px;
      border: 1px solid var(--border);
    }

    .seg-btn {
      background: transparent;
      color: var(--muted);
      border: none;
      padding: 5px 6px;
      font-size: 11px;
      font-weight: 700;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.12s ease;
    }
    .seg-btn:hover {
      color: var(--fg);
    }
    .seg-btn.active {
      background: var(--coral);
      color: #ffffff;
    }

    /* Gordon Ramsay Diagnosis Card */
    .diag-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    @keyframes cardPulse {
      0% { box-shadow: 0 0 0 0 rgba(249, 87, 56, 0.55); border-color: var(--coral); }
      100% { box-shadow: 0 0 0 12px rgba(249, 87, 56, 0); border-color: var(--border); }
    }
    .diag-card.pulse-alert {
      animation: cardPulse 0.9s ease-out;
    }

    .diag-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      flex-wrap: wrap;
    }

    .sev-badge {
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 2px 7px;
      border-radius: 4px;
      background: var(--coral-soft);
      color: var(--coral);
    }
    .sev-badge.uncertain {
      background: var(--amber-soft);
      color: var(--amber);
    }

    .src-tag {
      font-size: 10px;
      color: var(--muted);
      font-family: var(--vscode-editor-font-family, monospace);
    }

    .roast-box {
      background: linear-gradient(90deg, rgba(249, 87, 56, 0.13), rgba(249, 87, 56, 0.03));
      border-left: 3px solid var(--coral);
      border-radius: 0 7px 7px 0;
      padding: 8px 10px;
    }

    .roast-kicker {
      font-size: 9.5px;
      font-weight: 800;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      color: var(--coral);
      margin-bottom: 3px;
    }

    .roast-text {
      font-style: italic;
      font-size: 12px;
      color: var(--fg);
      line-height: 1.4;
    }

    .reasons-row {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    .reason-chip {
      font-size: 10px;
      font-family: var(--vscode-editor-font-family, monospace);
      padding: 2px 6px;
      border-radius: 4px;
      background: var(--subtle);
      border: 1px solid var(--border);
      color: var(--muted);
    }

    .summary-text {
      font-size: 13px;
      font-weight: 650;
      line-height: 1.45;
      color: var(--fg);
    }

    .md-body {
      font-size: 12px;
      line-height: 1.55;
      color: var(--fg);
      border-top: 1px solid var(--border);
      padding-top: 9px;
      white-space: pre-wrap;
    }

    .md-body code, .summary-text code, .bubble code {
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      background: rgba(255, 255, 255, 0.08);
      padding: 1px 4px;
      border-radius: 3px;
      font-size: 11px;
    }

    .md-body pre, .bubble pre {
      margin: 6px 0;
      padding: 8px 10px;
      border-radius: 6px;
      background: rgba(0, 0, 0, 0.32);
      border: 1px solid var(--border);
      overflow-x: auto;
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 11px;
      line-height: 1.42;
      white-space: pre;
    }

    /* Fix & Offline Action Bars */
    .action-row {
      display: flex;
      gap: 7px;
      padding-top: 4px;
      flex-wrap: wrap;
    }

    .primary-btn {
      flex: 1;
      background: var(--emerald);
      color: #05261c;
      border: none;
      border-radius: 6px;
      padding: 7px 10px;
      font-size: 11.5px;
      font-weight: 800;
      cursor: pointer;
    }
    .primary-btn:hover {
      filter: brightness(1.08);
    }

    .secondary-btn {
      background: transparent;
      color: var(--fg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 10px;
      font-size: 11.5px;
      font-weight: 600;
      cursor: pointer;
    }
    .secondary-btn:hover {
      background: var(--surface-elevated);
    }

    /* Citations */
    .section-title {
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 5px;
    }

    .citations-list {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }

    .citation-item {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 7px 9px;
      border-radius: 7px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 3px solid var(--coral);
      cursor: pointer;
      text-align: left;
      color: var(--fg);
    }
    .citation-item.secondary {
      border-left-color: var(--amber);
    }
    .citation-item:hover {
      background: var(--surface-elevated);
    }

    .cit-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 11px;
      font-weight: 700;
    }
    .cit-note {
      font-size: 11px;
      color: var(--muted);
    }

    /* Chat History */
    .chat-section {
      display: flex;
      flex-direction: column;
      gap: 7px;
      margin-top: 2px;
    }

    .chat-messages {
      display: flex;
      flex-direction: column;
      gap: 7px;
    }

    .bubble {
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.45;
      max-width: 95%;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .bubble.user {
      align-self: flex-end;
      background: rgba(249, 87, 56, 0.18);
      border: 1px solid rgba(249, 87, 56, 0.35);
    }
    .bubble.assistant {
      align-self: flex-start;
      background: var(--surface);
      border: 1px solid var(--border);
    }

    .quick-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }

    .quick-chip {
      background: var(--subtle);
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 10.5px;
      cursor: pointer;
    }
    .quick-chip:hover {
      color: var(--fg);
      border-color: var(--coral);
    }

    /* Docked Chat Input */
    .chat-dock {
      padding: 9px 12px;
      border-top: 1px solid var(--border);
      background: var(--surface);
      display: flex;
      flex-direction: column;
      gap: 5px;
    }

    .chat-row {
      display: flex;
      gap: 6px;
      align-items: flex-end;
    }

    textarea {
      flex: 1;
      resize: none;
      min-height: 36px;
      max-height: 92px;
      padding: 7px 9px;
      border-radius: 6px;
      background: var(--input-bg);
      color: var(--input-fg);
      border: 1px solid var(--input-border);
      font-family: inherit;
      font-size: 12px;
      outline: none;
    }
    textarea:focus {
      border-color: var(--coral);
    }

    .send-btn {
      background: var(--coral);
      color: #ffffff;
      border: none;
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 11.5px;
      font-weight: 700;
      cursor: pointer;
    }
    .send-btn:hover {
      filter: brightness(1.08);
    }

    .thinking-indicator {
      font-size: 10.5px;
      color: var(--coral);
      display: none;
      font-weight: 600;
    }
  </style>
</head>
<body>
  <!-- 1. Brand & Live Status Header -->
  <div class="topbar">
    <div class="brand-group">
      <span class="brand-logo"><span class="brand-flame">🔥</span> WHACK</span>
      <span id="brainPill" class="status-pill"><span class="status-dot"></span><span id="brainPillText">Brain Offline</span></span>
      <span id="sensorPill" class="status-pill"><span class="status-dot"></span><span id="sensorPillText">Sensor Standby</span></span>
    </div>
    <div class="header-icons">
      <button id="btnLogs" class="icon-btn" title="Open WHACK service logs">Logs</button>
      <button id="btnSound" class="icon-btn" title="Toggle chime sound">🔔</button>
      <button id="btnDismiss" class="icon-btn" title="Clear highlights &amp; snooze 3m">✕</button>
    </div>
  </div>

  <!-- 2. 1-Click Live Stack Controls -->
  <div class="control-bar">
    <button id="btnStackToggle" class="ctrl-btn launch" title="Start Brain (:5002) and Live Camera/Signal Sensor HUD">🚀 Launch Live Stack</button>
    <button id="btnSensorToggle" class="ctrl-btn" title="Launch or stop Camera + 3D Gaze Sensor HUD">📷 Sensor</button>
    <button id="btnApiKey" class="ctrl-btn" title="Set Gemini / OpenAI / Anthropic API Key in brain/.env">🔑 API Key</button>
    <button id="btnDiagnose" class="ctrl-btn diagnose" title="Diagnose active code right now (Ctrl+Shift+K)">⚡ Diagnose</button>
  </div>

  <!-- 3. Live Multimodal Feed Strip -->
  <div class="feed-strip">
    <div class="feed-state">
      <span>LIVE FEED</span>
      <span id="feedBadge" class="feed-badge">FLOW</span>
    </div>
    <div class="mini-meters">
      <div class="mini-meter">
        <div class="mini-meter-lbl"><span>Conf</span><span id="valConf">0%</span></div>
        <div class="mini-track"><div id="barConf" class="mini-fill" style="width: 5%; background: #38bdf8;"></div></div>
      </div>
      <div class="mini-meter">
        <div class="mini-meter-lbl"><span>Frust</span><span id="valFrust">0%</span></div>
        <div class="mini-track"><div id="barFrust" class="mini-fill" style="width: 4%; background: #f95738;"></div></div>
      </div>
      <div class="mini-meter">
        <div class="mini-meter-lbl"><span>Tired</span><span id="valTired">0%</span></div>
        <div class="mini-track"><div id="barTired" class="mini-fill" style="width: 5%; background: #c084fc;"></div></div>
      </div>
      <div class="mini-meter">
        <div class="mini-meter-lbl"><span>Flow</span><span id="valFlow">82%</span></div>
        <div class="mini-track"><div id="barFlow" class="mini-fill" style="width: 82%; background: #10b981;"></div></div>
      </div>
    </div>
  </div>

  <!-- 4. Main Scrollable Stage -->
  <div class="scroll-body">
    <!-- Standby Onboarding Card (shown before first diagnosis) -->
    <div id="standbyCard" class="standby-card">
      <div class="standby-title">👨‍🍳 Chef's Pass Ready — Reacting in Real Time</div>
      <div class="standby-sub">WHACK watches your live camera, 3D gaze, cursor dwell, and terminal errors to step in the exact moment you get stuck — never when you're in flow or AFK.</div>
      <div class="step-list">
        <div class="step-item">
          <span class="step-num">1</span>
          <div>Click <strong>🔑 API Key</strong> (once) to save your Gemini/OpenAI/Anthropic key, then click <strong>🚀 Launch Live Stack</strong> above.</div>
        </div>
        <div class="step-item">
          <span class="step-num">2</span>
          <div>Code normally in any workspace! When you freeze or thrash on a bug, Gordon Ramsay chimes in automatically — or press <strong>Ctrl+Shift+K</strong> (<strong>⚡ Diagnose</strong>) anytime.</div>
        </div>
        <div class="step-item">
          <span class="step-num">3</span>
          <div>Click highlighted lines, preview side-by-side diffs, <strong>✓ Apply Fix</strong> in 1 click, or chat below.</div>
        </div>
      </div>
    </div>

    <!-- Segmented Hint Level Bar -->
    <div id="tierBar" class="segmented" style="display: none;">
      <button class="seg-btn" data-level="nudge">1 · Nudge</button>
      <button class="seg-btn active" data-level="hint">2 · Hint</button>
      <button class="seg-btn" data-level="full_explanation">3 · Full Fix</button>
    </div>

    <!-- Diagnosis Card -->
    <div id="diagCard" class="diag-card" style="display: none;">
      <div class="diag-meta">
        <span id="sevBadge" class="sev-badge">Root Cause · 95%</span>
        <span id="srcTag" class="src-tag">Live Feed</span>
      </div>

      <div id="roastBox" class="roast-box" style="display: none;">
        <div class="roast-kicker">🔥 Gordon's Live Take</div>
        <div id="quipText" class="roast-text"></div>
      </div>

      <div id="reasonsRow" class="reasons-row" style="display: none;"></div>
      <div id="summaryBox" class="summary-text"></div>
      <div id="mdBody" class="md-body" style="display: none;"></div>

      <div id="fixBar" class="action-row" style="display: none;">
        <button id="btnApplyFix" class="primary-btn">✓ Apply Suggested Fix</button>
        <button id="btnPreviewDiff" class="secondary-btn">⇄ Preview Diff</button>
      </div>

      <div id="breakBar" class="action-row" style="display: none;">
        <button id="btnTakeBreak" class="primary-btn" style="background: #a855f7; color: #fff;">☕ Take 5m Break (Snooze WHACK)</button>
      </div>

      <div id="offlineBar" class="action-row" style="display: none;">
        <button id="btnOfflineLaunch" class="primary-btn">🚀 Launch Brain (:5002)</button>
        <button id="btnOfflineKey" class="secondary-btn">🔑 Set API Key</button>
        <button id="btnRetryDiag" class="secondary-btn">↻ Retry</button>
      </div>
    </div>

    <!-- Citations -->
    <div id="citationsSection" style="display: none;">
      <div class="section-title">Highlighted Code Locations</div>
      <div id="citationsList" class="citations-list"></div>
    </div>

    <!-- Follow-up Chat -->
    <div class="chat-section">
      <div class="section-title">Ask Chef Follow-Up</div>
      <div class="quick-chips">
        <button class="quick-chip" data-q="Why does this bug happen?">Why does this happen?</button>
        <button class="quick-chip" data-q="Show me the exact code fix.">Show exact fix</button>
        <button class="quick-chip" data-q="Are there any edge cases I missed?">Edge cases?</button>
      </div>
      <div id="chatMessages" class="chat-messages"></div>
    </div>
  </div>

  <!-- 5. Docked Chat Box -->
  <div class="chat-dock">
    <div id="thinkingLabel" class="thinking-indicator">🔥 Gordon is inspecting your code...</div>
    <div class="chat-row">
      <textarea id="chatInput" rows="1" placeholder="Ask about your code or highlighted lines... (Enter to send)"></textarea>
      <button id="btnSend" class="send-btn">Send</button>
    </div>
  </div>

  <script>
    const vscode = acquireVsCodeApi();

    const brainPill = document.getElementById("brainPill");
    const brainPillText = document.getElementById("brainPillText");
    const sensorPill = document.getElementById("sensorPill");
    const sensorPillText = document.getElementById("sensorPillText");
    const btnLogs = document.getElementById("btnLogs");
    const btnSound = document.getElementById("btnSound");
    const btnDismiss = document.getElementById("btnDismiss");

    const btnStackToggle = document.getElementById("btnStackToggle");
    const btnSensorToggle = document.getElementById("btnSensorToggle");
    const btnApiKey = document.getElementById("btnApiKey");
    const btnDiagnose = document.getElementById("btnDiagnose");

    const feedBadge = document.getElementById("feedBadge");
    const valConf = document.getElementById("valConf");
    const valFrust = document.getElementById("valFrust");
    const valTired = document.getElementById("valTired");
    const valFlow = document.getElementById("valFlow");
    const barConf = document.getElementById("barConf");
    const barFrust = document.getElementById("barFrust");
    const barTired = document.getElementById("barTired");
    const barFlow = document.getElementById("barFlow");

    const standbyCard = document.getElementById("standbyCard");
    const tierBar = document.getElementById("tierBar");
    const diagCard = document.getElementById("diagCard");
    const sevBadge = document.getElementById("sevBadge");
    const srcTag = document.getElementById("srcTag");
    const roastBox = document.getElementById("roastBox");
    const quipText = document.getElementById("quipText");
    const reasonsRow = document.getElementById("reasonsRow");
    const summaryBox = document.getElementById("summaryBox");
    const mdBody = document.getElementById("mdBody");
    const fixBar = document.getElementById("fixBar");
    const breakBar = document.getElementById("breakBar");
    const offlineBar = document.getElementById("offlineBar");

    const btnApplyFix = document.getElementById("btnApplyFix");
    const btnPreviewDiff = document.getElementById("btnPreviewDiff");
    const btnTakeBreak = document.getElementById("btnTakeBreak");
    const btnOfflineLaunch = document.getElementById("btnOfflineLaunch");
    const btnOfflineKey = document.getElementById("btnOfflineKey");
    const btnRetryDiag = document.getElementById("btnRetryDiag");

    const citationsSection = document.getElementById("citationsSection");
    const citationsList = document.getElementById("citationsList");
    const chatMessages = document.getElementById("chatMessages");
    const chatInput = document.getElementById("chatInput");
    const btnSend = document.getElementById("btnSend");
    const thinkingLabel = document.getElementById("thinkingLabel");

    let soundEnabled = true;
    let stackRunning = false;
    let audioCtx = null;

    function playSynthChime(kind) {
      if (!soundEnabled) return;
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        if (!audioCtx) audioCtx = new AudioCtx();
        if (audioCtx.state === "suspended") {
          audioCtx.resume().catch(() => {});
        }
        const now = audioCtx.currentTime;
        const notes =
          kind === "applied"
            ? [
                { f: 523.25, t: 0.0, d: 0.14 },
                { f: 659.25, t: 0.08, d: 0.16 },
                { f: 783.99, t: 0.16, d: 0.24 },
              ]
            : kind === "uncertain"
            ? [
                { f: 440.0, t: 0.0, d: 0.14 },
                { f: 523.25, t: 0.11, d: 0.20 },
              ]
            : [
                { f: 587.33, t: 0.0, d: 0.15 },
                { f: 880.0, t: 0.10, d: 0.24 },
              ];

        notes.forEach((n) => {
          const osc = audioCtx.createOscillator();
          const gain = audioCtx.createGain();
          osc.type = "sine";
          osc.frequency.setValueAtTime(n.f, now + n.t);
          gain.gain.setValueAtTime(0.001, now + n.t);
          gain.gain.exponentialRampToValueAtTime(0.16, now + n.t + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0008, now + n.t + n.d);
          osc.connect(gain);
          gain.connect(audioCtx.destination);
          osc.start(now + n.t);
          osc.stop(now + n.t + n.d + 0.02);
        });
      } catch (e) {
        // Ignore WebAudio restrictions
      }
    }

    function flashCardPulse() {
      diagCard.classList.remove("pulse-alert");
      void diagCard.offsetWidth;
      diagCard.classList.add("pulse-alert");
    }

    function updateSoundButtonUI() {
      btnSound.textContent = soundEnabled ? "🔔" : "🔕";
      btnSound.classList.toggle("muted", !soundEnabled);
    }

    btnSound.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      updateSoundButtonUI();
      if (soundEnabled) playSynthChime("diagnosis");
      vscode.postMessage({ type: "toggle_sound", enabled: soundEnabled });
    });

    btnLogs.addEventListener("click", () => vscode.postMessage({ type: "show_logs" }));
    btnDismiss.addEventListener("click", () => vscode.postMessage({ type: "dismiss" }));

    btnStackToggle.addEventListener("click", () => {
      vscode.postMessage({ type: stackRunning ? "stop_stack" : "start_stack" });
    });
    btnSensorToggle.addEventListener("click", () => vscode.postMessage({ type: "toggle_sensor" }));
    btnApiKey.addEventListener("click", () => vscode.postMessage({ type: "configure_key" }));
    btnDiagnose.addEventListener("click", () => vscode.postMessage({ type: "trigger_diagnose" }));

    btnApplyFix.addEventListener("click", () => vscode.postMessage({ type: "apply_fix" }));
    btnPreviewDiff.addEventListener("click", () => vscode.postMessage({ type: "preview_diff" }));
    btnTakeBreak.addEventListener("click", () => vscode.postMessage({ type: "take_break" }));
    btnOfflineLaunch.addEventListener("click", () => vscode.postMessage({ type: "start_stack" }));
    btnOfflineKey.addEventListener("click", () => vscode.postMessage({ type: "configure_key" }));
    btnRetryDiag.addEventListener("click", () => vscode.postMessage({ type: "trigger_diagnose" }));

    document.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const level = btn.getAttribute("data-level") || "hint";
        vscode.postMessage({ type: "set_hint_level", level });
      });
    });

    function escapeHtml(str) {
      return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function renderMarkdown(text) {
      const codeBlocks = [];
      let escaped = escapeHtml(text).replace(/\`\`\`(?:\\w*)\\r?\\n([\\s\\S]*?)\`\`\`/g, (_, code) => {
        const idx = codeBlocks.push("<pre>" + code.replace(/\\s+$/, "") + "</pre>") - 1;
        return "@@CODEBLOCK_" + idx + "@@";
      });
      escaped = escaped
        .replace(/^###\\s+(.+)$/gm, "<strong>$1</strong>")
        .replace(/\`([^\`\\r\\n]+)\`/g, "<code>$1</code>")
        .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
      return escaped.replace(/@@CODEBLOCK_(\\d+)@@/g, (_, i) => codeBlocks[Number(i)] || "");
    }

    function sendChat() {
      const text = chatInput.value.trim();
      if (!text) return;
      chatInput.value = "";
      vscode.postMessage({ type: "chat_send", text });
    }

    btnSend.addEventListener("click", sendChat);
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    });

    document.querySelectorAll(".quick-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const q = chip.getAttribute("data-q");
        if (q) vscode.postMessage({ type: "chat_send", text: q });
      });
    });

    window.addEventListener("message", (event) => {
      const msg = event.data;
      if (!msg) return;

      if (msg.type === "play_chime") {
        playSynthChime(msg.kind || "diagnosis");
        flashCardPulse();
        return;
      }

      if (msg.type === "chat_thinking") {
        thinkingLabel.style.display = msg.thinking ? "block" : "none";
        return;
      }

      if (msg.type === "focus_chat") {
        chatInput.focus();
        return;
      }

      if (msg.type === "state_sync") {
        if (typeof msg.soundEnabled === "boolean") {
          soundEnabled = msg.soundEnabled;
          updateSoundButtonUI();
        }
        if (msg.chime) {
          playSynthChime(msg.chime);
          flashCardPulse();
        }

        const live = msg.liveStatus || {};
        const stack = msg.stackState || {};
        const d = msg.diagnosis;

        // 1. Update Header Status Pills & Stack Controls
        const brainOnline = Boolean(live.brainOnline || msg.brainOnline);
        const keyReady = live.apiKeyConfigured !== undefined
          ? Boolean(live.apiKeyConfigured)
          : Boolean(stack.hasEnvApiKey);
        const modelLabel = live.brainModel || "gemini-3.1-flash-lite-preview";

        if (brainOnline && keyReady) {
          brainPill.className = "status-pill live";
          brainPillText.textContent = "Brain · " + modelLabel;
        } else if (brainOnline && !keyReady) {
          brainPill.className = "status-pill warn";
          brainPillText.textContent = "Brain · Needs API Key";
        } else {
          brainPill.className = "status-pill";
          brainPillText.textContent = "Brain Offline";
        }

        const sensorStreaming = Boolean(live.sensorActive || stack.sensorManagedRunning);
        if (sensorStreaming) {
          sensorPill.className = "status-pill sensor";
          sensorPillText.textContent = live.sensorActive ? "Sensor · Live" : "Sensor · Starting";
        } else {
          sensorPill.className = "status-pill";
          sensorPillText.textContent = "Sensor Standby";
        }

        stackRunning = Boolean(stack.brainManagedRunning || stack.sensorManagedRunning);
        if (stack.startingStack) {
          btnStackToggle.textContent = "⏳ Starting...";
          btnStackToggle.className = "ctrl-btn";
        } else if (stackRunning) {
          btnStackToggle.textContent = "⏹ Stop Stack";
          btnStackToggle.className = "ctrl-btn stop";
        } else {
          btnStackToggle.textContent = brainOnline ? "📷 Start Sensor" : "🚀 Launch Live Stack";
          btnStackToggle.className = "ctrl-btn launch";
        }

        btnSensorToggle.textContent = stack.sensorManagedRunning ? "⏹ Close HUD" : "📷 Sensor HUD";
        btnApiKey.textContent = keyReady ? "🔑 Key ✓" : "🔑 Set Key";

        // 2. Update Live Multimodal Feed Strip
        const label = String(live.userStatusLabel || "Flow");
        feedBadge.textContent = label.toUpperCase();
        const lowerLbl = label.toLowerCase();
        if (lowerLbl.includes("tired") || lowerLbl.includes("break") || lowerLbl.includes("fatig")) {
          feedBadge.className = "feed-badge tired";
        } else if (lowerLbl.includes("stuck") || lowerLbl.includes("frust")) {
          feedBadge.className = "feed-badge stuck";
        } else if (lowerLbl.includes("think")) {
          feedBadge.className = "feed-badge thinking";
        } else if (lowerLbl.includes("afk") || lowerLbl.includes("dnd")) {
          feedBadge.className = "feed-badge afk";
        } else {
          feedBadge.className = "feed-badge";
        }

        const emos = live.emotions || {};
        const confPct = Math.round(Math.max(0, Math.min(1, Number(emos.confused ?? live.confusionScore ?? 0.05))) * 100);
        const frustPct = Math.round(Math.max(0, Math.min(1, Number(emos.frustrated ?? 0.04))) * 100);
        const tiredPct = Math.round(Math.max(0, Math.min(1, Number(emos.fatigued ?? 0.05))) * 100);
        const flowPct = Math.round(Math.max(0, Math.min(1, Number(emos.flow ?? (confPct > 50 || tiredPct > 60 ? 0.15 : 0.82)))) * 100);

        valConf.textContent = confPct + "%";
        barConf.style.width = Math.max(4, confPct) + "%";
        valFrust.textContent = frustPct + "%";
        barFrust.style.width = Math.max(4, frustPct) + "%";
        valTired.textContent = tiredPct + "%";
        barTired.style.width = Math.max(4, tiredPct) + "%";
        valFlow.textContent = flowPct + "%";
        barFlow.style.width = Math.max(4, flowPct) + "%";

        // 3. Render Diagnosis Card vs Standby Card
        if (d) {
          standbyCard.style.display = "none";
          diagCard.style.display = "flex";
          srcTag.textContent = msg.sourceLabel || "Live Trigger";

          const isOfflineDiag = d.source === "offline";
          const diagReasonsStr = Array.isArray(d.reasons) ? d.reasons.join(" ").toLowerCase() : "";
          const combinedDiagText = (diagReasonsStr + " " + String(d.summary || "") + " " + String(d.quip || "")).toLowerCase();
          const isBreakReminder = !isOfflineDiag && (
            diagReasonsStr.includes("fatig") ||
            diagReasonsStr.includes("tired") ||
            combinedDiagText.includes("take a break") ||
            combinedDiagText.includes("minute break") ||
            combinedDiagText.includes("step off the line")
          );

          if (isOfflineDiag) {
            sevBadge.textContent = "Brain Offline";
            sevBadge.className = "sev-badge uncertain";
            tierBar.style.display = "none";
            offlineBar.style.display = "flex";
            fixBar.style.display = "none";
            breakBar.style.display = "none";
          } else {
            const cPct = Math.round((d.confidence ?? 0.95) * 100);
            sevBadge.textContent = isBreakReminder
              ? "☕ Break Reminder · Chef's Orders"
              : (d.uncertain ? "Uncertain · " : "Root Cause · ") + cPct + "%";
            sevBadge.className = "sev-badge" + (d.uncertain ? " uncertain" : "");
            tierBar.style.display = (isBreakReminder && !d.file) ? "none" : "grid";
            offlineBar.style.display = "none";
            fixBar.style.display = msg.hasFix ? "flex" : "none";
            breakBar.style.display = isBreakReminder ? "flex" : "none";
          }

          document.querySelectorAll(".seg-btn").forEach((b) => {
            b.classList.toggle("active", b.getAttribute("data-level") === d.hintLevel);
          });

          const quipStr = String(d.quip || "").trim();
          if (quipStr) {
            quipText.textContent = "“" + quipStr + "”";
            roastBox.style.display = "block";
          } else {
            roastBox.style.display = "none";
          }

          reasonsRow.innerHTML = "";
          const reasons = Array.isArray(d.reasons) ? d.reasons.filter(Boolean) : [];
          reasons.forEach((r) => {
            const chip = document.createElement("span");
            chip.className = "reason-chip";
            chip.textContent = r;
            reasonsRow.appendChild(chip);
          });
          reasonsRow.style.display = reasons.length > 0 ? "flex" : "none";

          const variant = d.tierVariants && d.tierVariants[d.hintLevel];
          const summaryText = String((variant && variant.summary) || d.summary || "").trim();
          const explanationText = String((variant && variant.explanation) || d.explanation || "").trim();

          summaryBox.innerHTML = renderMarkdown(summaryText);

          // Always render explanation across all hint levels when distinct from summary
          if (explanationText && explanationText !== summaryText) {
            mdBody.innerHTML = renderMarkdown(explanationText);
            mdBody.style.display = "block";
          } else {
            mdBody.innerHTML = "";
            mdBody.style.display = "none";
          }

          citationsList.innerHTML = "";
          const cits = msg.citations || [];
          citationsSection.style.display = cits.length > 0 ? "block" : "none";
          cits.forEach((cit, idx) => {
            const btn = document.createElement("button");
            btn.className = "citation-item" + (idx > 0 ? " secondary" : "");

            const top = document.createElement("div");
            top.className = "cit-top";
            const fileSpan = document.createElement("span");
            fileSpan.textContent = (cit.shortFile || cit.file) + " · L" + cit.lines[0] + "–" + cit.lines[1];
            const tagSpan = document.createElement("span");
            tagSpan.textContent = idx === 0 ? "Primary ↗" : "Related ↗";
            top.appendChild(fileSpan);
            top.appendChild(tagSpan);

            const note = document.createElement("div");
            note.className = "cit-note";
            note.textContent = cit.note || (cit.snippet ? cit.snippet.trim() : "Click to jump to code");

            btn.appendChild(top);
            btn.appendChild(note);
            btn.addEventListener("click", () => {
              vscode.postMessage({ type: "jump_citation", citation: cit });
            });
            citationsList.appendChild(btn);
          });
        } else {
          standbyCard.style.display = "flex";
          diagCard.style.display = "none";
          tierBar.style.display = "none";
          citationsSection.style.display = "none";
        }

        // 4. Render Chat Messages
        chatMessages.innerHTML = "";
        (msg.chatHistory || []).forEach((m) => {
          const div = document.createElement("div");
          div.className = "bubble " + m.role;
          div.innerHTML = renderMarkdown(m.content);
          chatMessages.appendChild(div);
        });
        chatMessages.scrollTop = chatMessages.scrollHeight;
      }
    });

    vscode.postMessage({ type: "ready" });
  </script>
</body>
</html>`;
  }

  public dispose(): void {
    this.close();
  }
}
