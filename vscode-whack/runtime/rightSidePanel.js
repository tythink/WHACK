"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.WhackRightSidePanel = void 0;
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
/**
 * Right-Hand Diagnosis + Chat Panel (`vscode.ViewColumn.Beside`).
 *
 * Behaviour:
 * - Stays 100% CLOSED during normal passive coding so your editor keeps full screen width.
 * - Slides open on the right-hand side automatically when the Brain LLM returns a result
 *   (or when you click `Simulate Brain` / `Ctrl+Shift+K`), playing a subtle harmonic chime.
 * - Collapses & disposes cleanly when you click `Dismiss`.
 */
class WhackRightSidePanel {
    handlers;
    panel = null;
    currentDiagnosis = null;
    currentSourceLabel = "Mock Brain";
    brainOnline = false;
    hasFix = false;
    soundEnabled = true;
    pendingChime = null;
    chatHistory = [];
    constructor(handlers) {
        this.handlers = handlers;
    }
    get isVisible() {
        return Boolean(this.panel && this.panel.visible);
    }
    get isSoundEnabled() {
        return this.soundEnabled;
    }
    setSoundEnabled(enabled) {
        this.soundEnabled = enabled;
        this.pushStateToWebview();
    }
    playChime(kind) {
        if (!this.soundEnabled)
            return;
        if (this.panel) {
            this.panel.webview.postMessage({ type: "play_chime", kind });
        }
        else {
            this.pendingChime = kind;
        }
    }
    showDiagnosis(diagnosis, options) {
        this.currentDiagnosis = diagnosis;
        this.currentSourceLabel = options.sourceLabel;
        this.brainOnline = options.brainOnline;
        this.hasFix = options.hasFix;
        if (options.playSound && this.soundEnabled) {
            this.pendingChime = diagnosis.uncertain ? "uncertain" : "diagnosis";
        }
        if (options.resetChat) {
            this.chatHistory = [
                {
                    role: "assistant",
                    content: `I highlighted **${diagnosis.citations?.length || 1} place(s)** in \`${diagnosis.file ? path.basename(diagnosis.file) : "your code"}\`. ` +
                        `Switch between **Nudge / Hint / Full Fix** above, click any line badge to jump there, or ask me anything below!`,
                    timestamp: Date.now(),
                    mode: options.brainOnline ? "brain_live" : "mock_fallback",
                },
            ];
        }
        this.ensurePanelOpen();
        this.pushStateToWebview();
    }
    appendChatMessage(msg) {
        this.chatHistory.push(msg);
        this.pushStateToWebview();
    }
    setChatThinking(thinking) {
        this.panel?.webview.postMessage({ type: "chat_thinking", thinking });
    }
    focusChatInput() {
        this.ensurePanelOpen();
        this.pushStateToWebview();
        this.panel?.webview.postMessage({ type: "focus_chat" });
    }
    close() {
        if (this.panel) {
            const p = this.panel;
            this.panel = null;
            p.dispose();
        }
    }
    toggle() {
        if (this.panel) {
            this.close();
        }
        else {
            this.ensurePanelOpen();
            this.pushStateToWebview();
        }
    }
    ensurePanelOpen() {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.Two, true);
            return;
        }
        this.panel = vscode.window.createWebviewPanel("whackBrainAssistant", "WHACK · Brain & Chat", {
            viewColumn: vscode.ViewColumn.Beside,
            preserveFocus: true,
        }, {
            enableScripts: true,
            retainContextWhenHidden: true,
        });
        this.panel.webview.html = this.buildHtml();
        this.panel.webview.onDidReceiveMessage(async (msg) => {
            if (!msg || typeof msg !== "object")
                return;
            switch (msg.type) {
                case "ready":
                    this.pushStateToWebview();
                    break;
                case "set_hint_level":
                    await this.handlers.onSelectHintLevel(msg.level);
                    break;
                case "jump_citation":
                    if (msg.citation) {
                        await this.handlers.onJumpToCitation(msg.citation);
                    }
                    break;
                case "preview_diff":
                    await this.handlers.onPreviewDiff();
                    break;
                case "apply_fix":
                    await this.handlers.onApplyFix();
                    break;
                case "dismiss":
                    await this.handlers.onDismiss();
                    break;
                case "toggle_sound":
                    this.soundEnabled = Boolean(msg.enabled);
                    this.handlers.onToggleSound?.(this.soundEnabled);
                    this.pushStateToWebview();
                    break;
                case "simulate":
                    await this.handlers.onSimulateScenario(msg.scenario || "sliding_window");
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
    pushStateToWebview() {
        if (!this.panel)
            return;
        const d = this.currentDiagnosis;
        const normalizedCitations = (d?.citations ?? []).map((c) => ({
            ...c,
            shortFile: path.basename(c.file),
        }));
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
        });
    }
    buildHtml() {
        return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WHACK Brain & Chat</title>
  <style>
    :root {
      --bg: var(--vscode-sideBar-background, #141416);
      --surface: var(--vscode-editorWidget-background, #1c1c20);
      --subtle: var(--vscode-textBlockQuote-background, rgba(255,255,255,0.04));
      --border: var(--vscode-panel-border, rgba(255,255,255,0.12));
      --fg: var(--vscode-foreground, #f4f4f5);
      --muted: var(--vscode-descriptionForeground, #9ca3af);
      --coral: #f25533;
      --coral-soft: rgba(242, 85, 51, 0.16);
      --amber: #d97706;
      --amber-soft: rgba(217, 119, 6, 0.16);
      --emerald: #10b981;
      --emerald-soft: rgba(16, 185, 129, 0.16);
      --input-bg: var(--vscode-input-background, #232329);
      --input-fg: var(--vscode-input-foreground, #f4f4f5);
      --input-border: var(--vscode-input-border, rgba(255,255,255,0.16));
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
    }

    /* 1. Top Incident Bar */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 9px 12px;
      background: #141413;
      color: #faf9f5;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      flex-shrink: 0;
    }

    .topbar-left {
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }

    .sev-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      background: var(--coral-soft);
      color: #ff7a5c;
      border: 1px solid rgba(242, 85, 51, 0.45);
      white-space: nowrap;
    }

    .sev-badge.uncertain {
      background: var(--amber-soft);
      color: #fbbf24;
      border-color: rgba(251, 191, 36, 0.4);
    }

    .mode-pill {
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(255,255,255,0.08);
      color: #cbd5e1;
      white-space: nowrap;
    }

    .mode-pill.live {
      background: var(--emerald-soft);
      color: #6ee7b7;
    }

    .topbar-actions {
      display: flex;
      align-items: center;
      gap: 5px;
      flex-shrink: 0;
    }

    select.sim-select {
      background: #242422;
      color: #e5e5e0;
      border: 1px solid rgba(255,255,255,0.16);
      border-radius: 5px;
      padding: 3px 6px;
      font-size: 11px;
      cursor: pointer;
    }

    .btn-top {
      background: #262624;
      color: #e5e5e0;
      border: 1px solid rgba(255,255,255,0.14);
      border-radius: 5px;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
    }

    .btn-top:hover {
      background: var(--coral);
      border-color: var(--coral);
      color: #fff;
    }

    .btn-top.muted {
      opacity: 0.65;
    }

    /* 2. Scrollable Diagnosis + Places to Correct */
    .diagnosis-scroll {
      flex: 1 1 54%;
      overflow-y: auto;
      padding: 11px 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      border-bottom: 1px solid var(--border);
    }

    .reasons-row {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }

    .reason-chip {
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      background: var(--coral-soft);
      color: #ff8a70;
      border: 1px solid rgba(242, 85, 51, 0.3);
    }

    .segmented {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 3px;
      padding: 3px;
      background: var(--subtle);
      border: 1px solid var(--border);
      border-radius: 7px;
    }

    .seg-btn {
      background: transparent;
      color: var(--muted);
      border: none;
      border-radius: 5px;
      padding: 5px 6px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
    }

    .seg-btn.active {
      background: var(--coral);
      color: #ffffff;
    }

    @keyframes cardPulse {
      0% { box-shadow: 0 0 0 0 rgba(242, 85, 51, 0.55); border-color: var(--coral); }
      100% { box-shadow: 0 0 0 10px rgba(242, 85, 51, 0); border-color: var(--border); }
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .card.pulse-alert {
      animation: cardPulse 0.85s ease-out 1;
    }

    .quip {
      font-size: 11.5px;
      font-style: italic;
      color: var(--muted);
      border-left: 2px solid var(--coral);
      padding-left: 8px;
    }

    .md-body {
      line-height: 1.55;
      font-size: 12.5px;
      white-space: pre-wrap;
    }

    .md-body code, .chat-bubble code {
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 11.5px;
      background: rgba(242, 85, 51, 0.14);
      color: #ff8a70;
      padding: 1px 4px;
      border-radius: 3px;
    }

    .md-body pre, .chat-bubble pre {
      margin-top: 6px;
      padding: 8px 10px;
      background: #101012;
      color: #e4e4e7;
      border-left: 3px solid var(--emerald);
      border-radius: 5px;
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 11.5px;
      overflow-x: auto;
    }

    .fix-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding-top: 4px;
    }

    .btn-primary {
      background: var(--emerald);
      color: #ffffff;
      border: none;
      border-radius: 6px;
      padding: 6px 11px;
      font-size: 11.5px;
      font-weight: 700;
      cursor: pointer;
    }

    .btn-primary:hover {
      opacity: 0.9;
    }

    .btn-secondary {
      background: transparent;
      color: var(--fg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 5px 10px;
      font-size: 11.5px;
      font-weight: 600;
      cursor: pointer;
    }

    .btn-secondary:hover {
      background: var(--subtle);
    }

    /* Places to Correct List */
    .section-title {
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }

    .citations-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .citation-item {
      display: flex;
      flex-direction: column;
      gap: 3px;
      text-align: left;
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 3px solid var(--coral);
      border-radius: 6px;
      padding: 7px 10px;
      cursor: pointer;
      color: var(--fg);
    }

    .citation-item.secondary {
      border-left-color: var(--amber);
    }

    .citation-item:hover {
      background: var(--subtle);
    }

    .cit-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-family: var(--vscode-editor-font-family, Consolas, monospace);
      font-size: 11.5px;
      font-weight: 700;
    }

    .cit-note {
      font-size: 11.5px;
      color: var(--muted);
    }

    /* 3. Docked Right-Side Brain Chatbox */
    .chat-pane {
      flex: 1 1 46%;
      min-height: 195px;
      display: flex;
      flex-direction: column;
      background: var(--surface);
    }

    .chat-header {
      padding: 6px 12px;
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 9px 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .chat-bubble {
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.48;
      max-width: 94%;
      white-space: pre-wrap;
    }

    .chat-bubble.user {
      align-self: flex-end;
      background: var(--coral);
      color: #ffffff;
    }

    .chat-bubble.assistant {
      align-self: flex-start;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--fg);
    }

    .quick-chips {
      display: flex;
      gap: 5px;
      padding: 4px 12px 6px;
      overflow-x: auto;
    }

    .quick-chip {
      background: var(--bg);
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 10.5px;
      white-space: nowrap;
      cursor: pointer;
    }

    .quick-chip:hover {
      color: var(--fg);
      border-color: var(--coral);
    }

    .chat-input-row {
      display: flex;
      gap: 6px;
      padding: 7px 12px 10px;
      border-top: 1px solid var(--border);
    }

    .chat-input {
      flex: 1;
      background: var(--input-bg);
      color: var(--input-fg);
      border: 1px solid var(--input-border);
      border-radius: 6px;
      padding: 6px 9px;
      font-family: inherit;
      font-size: 12px;
      resize: none;
    }

    .btn-send {
      background: var(--coral);
      color: #ffffff;
      border: none;
      border-radius: 6px;
      padding: 0 12px;
      font-size: 11.5px;
      font-weight: 700;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <!-- 1. Header -->
  <header class="topbar">
    <div class="topbar-left">
      <span id="sevBadge" class="sev-badge">SEV-1 · Root Cause</span>
      <span id="modePill" class="mode-pill">Mock Brain</span>
    </div>
    <div class="topbar-actions">
      <button id="btnSound" class="btn-top" title="Toggle Brain notification chime sound (click to test chime)">🔔 Sound</button>
      <select id="simSelect" class="sim-select" title="Test simulated Brain scenarios before Role 3 Brain is online">
        <option value="" selected disabled>🧪 Simulate...</option>
        <option value="sliding_window">Planted Bug (utils.py)</option>
        <option value="active_editor">Highlight Active File</option>
        <option value="uncertain_probe">Uncertain Nudge (62%)</option>
      </select>
      <button id="btnDismiss" class="btn-top" title="Clear highlights and collapse back to passive Status Bar">✕ Dismiss</button>
    </div>
  </header>

  <!-- 2. Diagnosis & Highlighted Locations -->
  <section class="diagnosis-scroll">
    <div id="reasonsRow" class="reasons-row"></div>

    <div class="segmented">
      <button class="seg-btn" data-level="nudge">1 · Nudge</button>
      <button class="seg-btn active" data-level="hint">2 · Hint</button>
      <button class="seg-btn" data-level="full_explanation">3 · Full Fix</button>
    </div>

    <div id="diagCard" class="card">
      <div id="quipBox" class="quip">Waiting for Brain trigger...</div>
      <div id="mdBody" class="md-body">Click <strong>🧪 Simulate...</strong> above or press <code>Ctrl+Alt+S</code> to test editor line highlighting and right-side diagnosis!</div>
      <div id="fixBar" class="fix-bar" style="display: none;">
        <button id="btnApplyFix" class="btn-primary">✓ Apply Suggested Fix</button>
        <button id="btnPreviewDiff" class="btn-secondary">⇄ Preview Side-by-Side Diff</button>
      </div>
    </div>

    <div>
      <div class="section-title" style="margin-bottom: 5px;">Places Highlighted in Editor (Click to Jump)</div>
      <div id="citationsList" class="citations-list"></div>
    </div>
  </section>

  <!-- 3. Right-Side Follow-Up Chatbox -->
  <section class="chat-pane">
    <div class="chat-header">
      <span>Ask WHACK Brain (Code Context Attached)</span>
      <span id="thinkingLabel" style="display:none; color:#ff7a5c;">Thinking...</span>
    </div>

    <div id="chatMessages" class="chat-messages"></div>

    <div class="quick-chips">
      <button class="quick-chip" data-q="Why does this line fail?">Why does this fail?</button>
      <button class="quick-chip" data-q="Give me a hint without spoilers">Hint without spoilers</button>
      <button class="quick-chip" data-q="Are there any edge cases left?">Edge cases?</button>
    </div>

    <div class="chat-input-row">
      <textarea id="chatInput" class="chat-input" rows="1" placeholder="Ask about the highlighted lines... (Enter to send)"></textarea>
      <button id="btnSend" class="btn-send">Send</button>
    </div>
  </section>

  <script>
    const vscode = acquireVsCodeApi();

    const sevBadge = document.getElementById("sevBadge");
    const modePill = document.getElementById("modePill");
    const btnSound = document.getElementById("btnSound");
    const simSelect = document.getElementById("simSelect");
    const btnDismiss = document.getElementById("btnDismiss");
    const reasonsRow = document.getElementById("reasonsRow");
    const diagCard = document.getElementById("diagCard");
    const quipBox = document.getElementById("quipBox");
    const mdBody = document.getElementById("mdBody");
    const fixBar = document.getElementById("fixBar");
    const btnApplyFix = document.getElementById("btnApplyFix");
    const btnPreviewDiff = document.getElementById("btnPreviewDiff");
    const citationsList = document.getElementById("citationsList");
    const chatMessages = document.getElementById("chatMessages");
    const chatInput = document.getElementById("chatInput");
    const btnSend = document.getElementById("btnSend");
    const thinkingLabel = document.getElementById("thinkingLabel");

    let soundEnabled = true;
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
      btnSound.textContent = soundEnabled ? "🔔 Sound" : "🔕 Muted";
      btnSound.classList.toggle("muted", !soundEnabled);
    }

    btnSound.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      updateSoundButtonUI();
      if (soundEnabled) {
        playSynthChime("diagnosis");
      }
      vscode.postMessage({ type: "toggle_sound", enabled: soundEnabled });
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

    document.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const level = btn.getAttribute("data-level") || "hint";
        vscode.postMessage({ type: "set_hint_level", level });
      });
    });

    simSelect.addEventListener("change", () => {
      if (simSelect.value) {
        vscode.postMessage({ type: "simulate", scenario: simSelect.value });
        simSelect.selectedIndex = 0;
      }
    });

    btnDismiss.addEventListener("click", () => vscode.postMessage({ type: "dismiss" }));
    btnApplyFix.addEventListener("click", () => vscode.postMessage({ type: "apply_fix" }));
    btnPreviewDiff.addEventListener("click", () => vscode.postMessage({ type: "preview_diff" }));

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
        thinkingLabel.style.display = msg.thinking ? "inline" : "none";
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

        const d = msg.diagnosis;
        modePill.textContent = msg.brainOnline ? "Live Brain :5002" : "Mock Brain Ready";
        modePill.className = "mode-pill" + (msg.brainOnline ? " live" : "");

        if (d) {
          const confPct = Math.round((d.confidence ?? 0.95) * 100);
          sevBadge.textContent = d.uncertain
            ? "Uncertain · " + confPct + "%"
            : "Root Cause · " + confPct + "%";
          sevBadge.className = "sev-badge" + (d.uncertain ? " uncertain" : "");

          document.querySelectorAll(".seg-btn").forEach((b) => {
            b.classList.toggle("active", b.getAttribute("data-level") === d.hintLevel);
          });

          reasonsRow.innerHTML = "";
          (d.reasons || []).forEach((r) => {
            const chip = document.createElement("span");
            chip.className = "reason-chip";
            chip.textContent = r;
            reasonsRow.appendChild(chip);
          });

          quipBox.textContent = d.quip || "Root cause isolated from code & telemetry.";

          const variant = d.tierVariants && d.tierVariants[d.hintLevel];
          let displayMd = variant
            ? variant.summary + (variant.explanation ? "\\n\\n" + variant.explanation : "")
            : d.summary + (d.hintLevel === "full_explanation" && d.explanation ? "\\n\\n" + d.explanation : "");

          mdBody.innerHTML = renderMarkdown(displayMd);
          fixBar.style.display = msg.hasFix && d.hintLevel !== "nudge" ? "flex" : "none";

          citationsList.innerHTML = "";
          (msg.citations || []).forEach((cit, idx) => {
            const btn = document.createElement("button");
            btn.className = "citation-item" + (idx > 0 ? " secondary" : "");

            const top = document.createElement("div");
            top.className = "cit-top";
            const fileSpan = document.createElement("span");
            fileSpan.textContent =
              (cit.shortFile || cit.file) + " · L" + cit.lines[0] + "–" + cit.lines[1];
            const tagSpan = document.createElement("span");
            tagSpan.textContent = idx === 0 ? "Primary ↗" : "Related ↗";
            top.appendChild(fileSpan);
            top.appendChild(tagSpan);

            const note = document.createElement("div");
            note.className = "cit-note";
            note.textContent = cit.note || cit.snippet || "Jump to highlighted lines";

            btn.appendChild(top);
            btn.appendChild(note);
            btn.addEventListener("click", () => {
              vscode.postMessage({ type: "jump_citation", citation: cit });
            });
            citationsList.appendChild(btn);
          });
        }

        chatMessages.innerHTML = "";
        (msg.chatHistory || []).forEach((m) => {
          const div = document.createElement("div");
          div.className = "chat-bubble " + m.role;
          div.innerHTML = m.role === "assistant" ? renderMarkdown(m.content) : escapeHtml(m.content);
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
    dispose() {
        this.close();
    }
}
exports.WhackRightSidePanel = WhackRightSidePanel;
