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
exports.activate = activate;
exports.deactivate = deactivate;
const child_process_1 = require("child_process");
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const brainAdapter_1 = require("./brainAdapter");
const fixEngine_1 = require("./fixEngine");
const highlightManager_1 = require("./highlightManager");
const rightSidePanel_1 = require("./rightSidePanel");
const simulatorScenarios_1 = require("./simulatorScenarios");
const telemetryWatcher_1 = require("./telemetryWatcher");
let brainBridge = null;
/**
 * Plays a lightweight, non-blocking OS audio chime on Windows/macOS so the user
 * hears the notification even before clicking inside the Webview (where Chromium
 * autoplay policy can otherwise defer WebAudio).
 */
function playNativeOsChime(kind) {
    if (process.env.WHACK_TEST_MODE === "1") {
        return;
    }
    try {
        if (process.platform === "win32") {
            const cmd = kind === "applied"
                ? "[console]::beep(523,70); [console]::beep(659,70); [console]::beep(784,110)"
                : kind === "uncertain"
                    ? "[console]::beep(440,80); [console]::beep(523,110)"
                    : "[console]::beep(587,85); [console]::beep(880,125)";
            const child = (0, child_process_1.spawn)("powershell.exe", ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd], { stdio: "ignore", windowsHide: true });
            child.unref();
        }
        else if (process.platform === "darwin") {
            const soundFile = kind === "applied"
                ? "/System/Library/Sounds/Glass.aiff"
                : "/System/Library/Sounds/Ping.aiff";
            const child = (0, child_process_1.spawn)("afplay", [soundFile], { stdio: "ignore" });
            child.unref();
        }
    }
    catch {
        // Ignore OS audio errors
    }
}
async function activate(context) {
    const config = vscode.workspace.getConfiguration("whack");
    const coreUrl = config.get("coreServerUrl", "http://127.0.0.1:3000");
    const brainUrl = config.get("brainServerUrl", "http://127.0.0.1:5002");
    const showSimBtn = config.get("showSimulateStatusBarButton", true);
    let enableToast = config.get("enableNotificationToast", true);
    let enableSound = config.get("enableNotificationSound", true);
    const workspaceRoots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
    // 1. Passive Status Bar Pill (stays tiny & quiet while you code!)
    const statusPill = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 102);
    statusPill.name = "WHACK Passive Status";
    statusPill.command = "whack.statusMenu";
    statusPill.text = "$(pulse) WHACK · Flow 0.08";
    statusPill.tooltip =
        "WHACK is passively monitoring. Click for options or press Ctrl+Shift+K to diagnose.";
    statusPill.show();
    // 2. One-click "Simulate Brain" Status Bar test button (for testing before Role 3 Brain is ready)
    const simulatePill = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 101);
    simulatePill.name = "WHACK Simulate Brain";
    simulatePill.command = "whack.simulateBrain";
    simulatePill.text = "$(beaker) Simulate Brain";
    simulatePill.tooltip =
        "Test WHACK Brain trigger: plays chime + notification toast, highlights target lines, and opens the right-side Diagnosis + Chat panel (Ctrl+Alt+S)";
    if (showSimBtn) {
        simulatePill.show();
    }
    // 3. Diff Provider + Highlight Manager + Right-Side Panel
    const diffProvider = new fixEngine_1.WhackDiffContentProvider();
    const highlightManager = new highlightManager_1.WhackHighlightManager(context.extensionPath);
    let activeDiagnosis = null;
    let activePatch;
    let chatHistory = [];
    const updateStatusBar = (snap) => {
        const scoreStr = snap.confusionScore.toFixed(2);
        const brainTag = snap.brainOnline ? "Brain:Live" : "Brain:Mock";
        if (activeDiagnosis) {
            const shortFile = activeDiagnosis.file
                ? path.basename(activeDiagnosis.file)
                : "code";
            const lineStr = activeDiagnosis.lines
                ? `:${activeDiagnosis.lines[0]}`
                : "";
            statusPill.text = `$(bell-dot) WHACK · ${activePatch ? "Fix Ready" : "Diagnosis"} (${shortFile}${lineStr})`;
            statusPill.backgroundColor = new vscode.ThemeColor(activeDiagnosis.uncertain
                ? "statusBarItem.warningBackground"
                : "statusBarItem.errorBackground");
        }
        else if (snap.cooldownUntil && snap.cooldownUntil > Date.now()) {
            statusPill.text = `$(bell-slash) WHACK · Snoozed`;
            statusPill.backgroundColor = undefined;
        }
        else if (snap.faceState === "confused" || snap.confusionScore >= 0.7) {
            statusPill.text = `$(flame) WHACK · Stuck ${scoreStr}`;
            statusPill.backgroundColor = new vscode.ThemeColor("statusBarItem.errorBackground");
        }
        else if (snap.confusionScore >= 0.4) {
            statusPill.text = `$(eye) WHACK · Thinking ${scoreStr}`;
            statusPill.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
        }
        else {
            statusPill.text = `$(pulse) WHACK · ${snap.userStatusLabel || "Flow"} ${scoreStr}`;
            statusPill.backgroundColor = undefined;
        }
        statusPill.tooltip =
            `WHACK Passive Monitor (${brainTag} | Core:${snap.coreMode} | Sound:${enableSound ? "ON" : "OFF"})\n` +
                `Click for WHACK Quick Menu · Ctrl+Shift+K to Diagnose · Ctrl+Alt+S to Simulate`;
    };
    /**
     * Fires a native VS Code interactive Notification Toast (`showInformationMessage` / `showWarningMessage`)
     * with direct action buttons (`✓ Apply Fix`, `⇄ Preview Diff`, `↗ Jump to Line`, `✕ Dismiss`).
     */
    const fireBrainNotificationToast = (diagnosis, hasFix) => {
        if (!enableToast)
            return;
        const confPct = Math.round((diagnosis.confidence ?? 0.95) * 100);
        const shortFile = diagnosis.file ? path.basename(diagnosis.file) : "code";
        const lineRange = diagnosis.lines
            ? `:${diagnosis.lines[0]}–${diagnosis.lines[1]}`
            : "";
        const cleanSummary = diagnosis.summary.replace(/`/g, "");
        const message = `⚡ WHACK Brain (${confPct}% · ${shortFile}${lineRange}): ${cleanSummary}`;
        const actions = hasFix
            ? ["✓ Apply Fix", "⇄ Preview Diff", "↗ Jump to Line", "✕ Dismiss"]
            : ["↗ Jump to Line", "💬 Ask in Chat", "✕ Dismiss"];
        const notifyPromise = diagnosis.uncertain
            ? vscode.window.showWarningMessage(message, ...actions)
            : vscode.window.showInformationMessage(message, ...actions);
        notifyPromise.then(async (selected) => {
            if (!selected)
                return;
            if (selected === "✓ Apply Fix") {
                await vscode.commands.executeCommand("whack.applyFix");
            }
            else if (selected === "⇄ Preview Diff") {
                await vscode.commands.executeCommand("whack.previewDiff");
            }
            else if (selected === "↗ Jump to Line") {
                const primaryCit = diagnosis.citations?.[0] ||
                    (diagnosis.file && diagnosis.lines
                        ? { file: diagnosis.file, lines: diagnosis.lines }
                        : undefined);
                if (primaryCit) {
                    await highlightManager.jumpToCitation(primaryCit);
                }
            }
            else if (selected === "💬 Ask in Chat") {
                await vscode.commands.executeCommand("whack.focusChat");
            }
            else if (selected === "✕ Dismiss") {
                await vscode.commands.executeCommand("whack.dismiss");
            }
        }, () => undefined);
    };
    /**
     * Central handler invoked whenever Brain returns a result (real LLM or Simulator):
     * 1. Computes applicable patch (if any)
     * 2. Opens target file on left (`ViewColumn.One`), scrolls to `lines`, and highlights lines + citations
     * 3. Slides open the Right-Side Panel (`ViewColumn.Beside`) with diagnosis & follow-up chatbox
     * 4. Plays notification chime (WebAudio + OS chime) + shows interactive VS Code notification toast
     */
    const presentBrainDiagnosis = async (diagnosis, sourceLabel, resetChat = true) => {
        activeDiagnosis = diagnosis;
        activePatch = await (0, fixEngine_1.computeSuggestedPatch)(diagnosis, context.extensionPath);
        await highlightManager.showDiagnosisHighlights(diagnosis, Boolean(activePatch));
        if (resetChat) {
            chatHistory = [];
        }
        rightPanel.showDiagnosis(diagnosis, {
            sourceLabel,
            brainOnline: Boolean(brainBridge?.status.brainOnline),
            hasFix: Boolean(activePatch),
            resetChat,
            playSound: resetChat && enableSound,
        });
        if (brainBridge) {
            updateStatusBar(brainBridge.status);
        }
        if (resetChat) {
            if (enableSound) {
                playNativeOsChime(diagnosis.uncertain ? "uncertain" : "diagnosis");
            }
            fireBrainNotificationToast(diagnosis, Boolean(activePatch));
        }
    };
    const rightPanel = new rightSidePanel_1.WhackRightSidePanel({
        onToggleSound: (enabled) => {
            enableSound = enabled;
            if (brainBridge) {
                updateStatusBar(brainBridge.status);
            }
        },
        onSelectHintLevel: async (level) => {
            if (!activeDiagnosis)
                return;
            if (activeDiagnosis.tierVariants?.[level]) {
                activeDiagnosis = { ...activeDiagnosis, hintLevel: level };
                activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis, context.extensionPath);
                rightPanel.showDiagnosis(activeDiagnosis, {
                    sourceLabel: "Tier Switch",
                    brainOnline: Boolean(brainBridge?.status.brainOnline),
                    hasFix: Boolean(activePatch),
                    resetChat: false,
                    playSound: false,
                });
                return;
            }
            if (brainBridge) {
                const refreshed = await brainBridge.requestDiagnosis(level, telemetry.getActiveEditorContext());
                await presentBrainDiagnosis(refreshed, "Brain Tier Refresh", false);
            }
        },
        onJumpToCitation: async (citation) => {
            await highlightManager.jumpToCitation(citation);
        },
        onPreviewDiff: async () => {
            if (!activeDiagnosis)
                return;
            activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis, context.extensionPath);
            if (!activePatch) {
                vscode.window.showInformationMessage("No structured code replacement block was found in this diagnosis.");
                return;
            }
            const fileName = path.basename(activePatch.uri.fsPath);
            const proposedUri = vscode.Uri.parse(`${fixEngine_1.DIFF_SCHEME}://proposed/${encodeURIComponent(fileName)}?ts=${Date.now()}`);
            diffProvider.setContent(proposedUri, activePatch.previewFullDocument);
            await vscode.commands.executeCommand("vscode.diff", activePatch.uri, proposedUri, `${fileName}: Current ↔ WHACK Proposed Fix (${activePatch.summaryLabel})`, { viewColumn: vscode.ViewColumn.One, preserveFocus: false });
        },
        onApplyFix: async () => {
            await vscode.commands.executeCommand("whack.applyFix");
        },
        onDismiss: async () => {
            await vscode.commands.executeCommand("whack.dismiss");
        },
        onSimulateScenario: async (scenario) => {
            const simDiag = (0, simulatorScenarios_1.buildSimulatedDiagnosis)(scenario, context.extensionPath);
            await presentBrainDiagnosis(simDiag, `Simulated (${scenario})`, true);
        },
        onSendChatMessage: async (text) => {
            const userMsg = {
                role: "user",
                content: text,
                timestamp: Date.now(),
            };
            chatHistory.push(userMsg);
            rightPanel.appendChatMessage(userMsg);
            rightPanel.setChatThinking(true);
            const hl = highlightManager.getHighlightedSnippet();
            const res = brainBridge
                ? await brainBridge.sendChatMessage({
                    message: text,
                    history: chatHistory,
                    activeFile: hl.file || activeDiagnosis?.file,
                    highlightedLines: hl.lines || activeDiagnosis?.lines,
                    codeSnippet: hl.snippet,
                    latestDiagnosis: activeDiagnosis,
                })
                : {
                    reply: "Brain bridge initializing...",
                    mode: "mock_fallback",
                };
            rightPanel.setChatThinking(false);
            const assistantMsg = {
                role: "assistant",
                content: res.reply,
                timestamp: Date.now(),
                mode: res.mode,
            };
            chatHistory.push(assistantMsg);
            rightPanel.appendChatMessage(assistantMsg);
        },
    });
    rightPanel.setSoundEnabled(enableSound);
    // 4. Start Brain & Core Bridge + Passive Editor/Terminal Telemetry
    brainBridge = new brainAdapter_1.WhackBrainBridge(coreUrl, brainUrl, context.extensionPath, workspaceRoots, {
        onStatusUpdate: updateStatusBar,
        onDiagnosisTriggered: (diag, src) => {
            presentBrainDiagnosis(diag, src, true).catch(() => undefined);
        },
    });
    const telemetry = new telemetryWatcher_1.WhackTelemetryWatcher(brainBridge);
    brainBridge.start().catch(() => undefined);
    // 5. Register VS Code Commands
    context.subscriptions.push(statusPill, simulatePill, diffProvider, highlightManager, rightPanel, telemetry, vscode.workspace.registerTextDocumentContentProvider(fixEngine_1.DIFF_SCHEME, diffProvider), vscode.commands.registerCommand("whack.simulateBrain", async () => {
        const sim = (0, simulatorScenarios_1.buildSimulatedDiagnosis)("sliding_window", context.extensionPath);
        await presentBrainDiagnosis(sim, "Simulated Brain", true);
    }), vscode.commands.registerCommand("whack.simulateActiveFile", async () => {
        const sim = (0, simulatorScenarios_1.buildSimulatedDiagnosis)("active_editor", context.extensionPath);
        await presentBrainDiagnosis(sim, "Active File Simulation", true);
    }), vscode.commands.registerCommand("whack.triggerDiagnose", async () => {
        if (!brainBridge)
            return;
        statusPill.text = "$(sync~spin) WHACK · Diagnosing...";
        const diag = await brainBridge.requestDiagnosis("hint", telemetry.getActiveEditorContext());
        updateStatusBar(brainBridge.status);
        await presentBrainDiagnosis(diag, "Manual Trigger (Ctrl+Shift+K)", true);
    }), vscode.commands.registerCommand("whack.togglePanel", () => {
        rightPanel.toggle();
    }), vscode.commands.registerCommand("whack.toggleSound", () => {
        enableSound = !enableSound;
        rightPanel.setSoundEnabled(enableSound);
        if (enableSound) {
            rightPanel.playChime("diagnosis");
            playNativeOsChime("diagnosis");
        }
        if (brainBridge) {
            updateStatusBar(brainBridge.status);
        }
        vscode.window.showInformationMessage(`WHACK Brain notification sound: ${enableSound ? "🔔 ON" : "🔕 OFF"}`);
    }), vscode.commands.registerCommand("whack.previewDiff", async () => {
        await vscode.commands.executeCommand("whack.focusPanelInternalPreview");
    }), vscode.commands.registerCommand("whack.focusPanelInternalPreview", async () => {
        if (!activeDiagnosis)
            return;
        activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis, context.extensionPath);
        if (!activePatch)
            return;
        const fileName = path.basename(activePatch.uri.fsPath);
        const proposedUri = vscode.Uri.parse(`${fixEngine_1.DIFF_SCHEME}://proposed/${encodeURIComponent(fileName)}?ts=${Date.now()}`);
        diffProvider.setContent(proposedUri, activePatch.previewFullDocument);
        await vscode.commands.executeCommand("vscode.diff", activePatch.uri, proposedUri, `${fileName}: Current ↔ WHACK Proposed Fix`, { viewColumn: vscode.ViewColumn.One, preserveFocus: false });
    }), vscode.commands.registerCommand("whack.applyFix", async () => {
        if (!activeDiagnosis)
            return;
        activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis, context.extensionPath);
        if (!activePatch) {
            vscode.window.showWarningMessage("No automatic patch available for this hint—ask in the WHACK chatbox for a snippet!");
            return;
        }
        const edit = new vscode.WorkspaceEdit();
        edit.replace(activePatch.uri, activePatch.range, activePatch.newText);
        if (await vscode.workspace.applyEdit(edit)) {
            if (enableSound) {
                rightPanel.playChime("applied");
                playNativeOsChime("applied");
            }
            await highlightManager.flashAppliedFix(activePatch.uri, activePatch.range);
            rightPanel.appendChatMessage({
                role: "assistant",
                content: `✓ Applied fix (${activePatch.summaryLabel}) in \`${path.basename(activePatch.uri.fsPath)}\`. Press \`Ctrl+Z\` in the editor anytime to undo, or run \`pytest\` to verify!`,
                timestamp: Date.now(),
            });
            brainBridge?.applyLocalFeedback("helpful");
        }
    }), vscode.commands.registerCommand("whack.jumpToCitation", async (cit) => {
        if (cit) {
            await highlightManager.jumpToCitation(cit);
        }
    }), vscode.commands.registerCommand("whack.focusChat", () => {
        rightPanel.focusChatInput();
    }), vscode.commands.registerCommand("whack.dismiss", () => {
        highlightManager.clearHighlights();
        rightPanel.close();
        activeDiagnosis = null;
        activePatch = undefined;
        brainBridge?.applyLocalFeedback("dismiss");
    }), vscode.commands.registerCommand("whack.statusMenu", async () => {
        const picked = await vscode.window.showQuickPick([
            {
                label: "$(beaker) Simulate Brain Result (Planted Bug in utils.py)",
                description: "Plays chime + toast, highlights L18–21 & L29–30, opens right-side panel",
                id: "sim_bug",
            },
            {
                label: "$(sparkle) Simulate Brain Result on Currently Open File",
                description: "Highlights lines around your current cursor",
                id: "sim_active",
            },
            {
                label: "$(pulse) Trigger Live / Fallback Diagnosis (Ctrl+Shift+K)",
                description: "Calls Brain :5002 if online, else Mock Brain",
                id: "diag",
            },
            {
                label: `$(bell) Toggle Notification Sound (Currently ${enableSound ? "ON" : "OFF"})`,
                description: "Two-note harmonic chime when Brain returns a result",
                id: "sound",
            },
            {
                label: "$(layout-sidebar-right) Toggle Right-Side Diagnosis & Chat Panel",
                id: "toggle",
            },
            {
                label: "$(close) Dismiss Highlights & Snooze 3m",
                id: "dismiss",
            },
        ], {
            title: "WHACK — Passive Confusion & Brain Assistant",
            placeHolder: "Choose an action to test or control WHACK",
        });
        if (!picked)
            return;
        if (picked.id === "sim_bug") {
            await vscode.commands.executeCommand("whack.simulateBrain");
        }
        else if (picked.id === "sim_active") {
            await vscode.commands.executeCommand("whack.simulateActiveFile");
        }
        else if (picked.id === "diag") {
            await vscode.commands.executeCommand("whack.triggerDiagnose");
        }
        else if (picked.id === "sound") {
            await vscode.commands.executeCommand("whack.toggleSound");
        }
        else if (picked.id === "toggle") {
            await vscode.commands.executeCommand("whack.togglePanel");
        }
        else if (picked.id === "dismiss") {
            await vscode.commands.executeCommand("whack.dismiss");
        }
    }));
}
function deactivate() {
    brainBridge?.dispose();
    brainBridge = null;
}
