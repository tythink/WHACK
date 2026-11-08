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
const serviceManager_1 = require("./serviceManager");
const telemetryWatcher_1 = require("./telemetryWatcher");
let brainBridge = null;
let serviceManager = null;
/**
 * Plays a lightweight, non-blocking OS audio chime on Windows/macOS so the user
 * hears live diagnosis notifications even before clicking inside the Webview.
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
    const enableToast = config.get("enableNotificationToast", true);
    let enableSound = config.get("enableNotificationSound", true);
    const workspaceRoots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
    // 1. Passive Status Bar Pill (click opens the WHACK Chef's Pass & Mission Control panel)
    const statusPill = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 102);
    statusPill.name = "WHACK Live Status";
    statusPill.command = "whack.togglePanel";
    statusPill.text = "$(flame) WHACK · Ready";
    statusPill.tooltip =
        "Click to open WHACK Chef's Pass & 1-Click Live Stack Control (or press Ctrl+Shift+K to diagnose)";
    statusPill.show();
    // 2. Diff Provider + Highlight Manager + Service Manager
    const diffProvider = new fixEngine_1.WhackDiffContentProvider();
    const highlightManager = new highlightManager_1.WhackHighlightManager();
    let activeDiagnosis = null;
    let activePatch;
    let chatHistory = [];
    const seenDiagnosisTimestamps = new Map();
    const isDuplicateDiagnosis = (diag) => {
        const now = Date.now();
        for (const [k, ts] of seenDiagnosisTimestamps.entries()) {
            const ttl = k.startsWith("id:") ? 10000 : 5000;
            if (now - ts > ttl) {
                seenDiagnosisTimestamps.delete(k);
            }
        }
        const idKey = diag.diagnosisId ? `id:${diag.diagnosisId}` : null;
        const hashKey = `hash:${diag.source}|${diag.file ?? ""}|${diag.lines?.[0] ?? 0}-${diag.lines?.[1] ?? 0}|${diag.summary.trim()}`;
        if ((idKey && seenDiagnosisTimestamps.has(idKey)) || seenDiagnosisTimestamps.has(hashKey)) {
            return true;
        }
        if (idKey) {
            seenDiagnosisTimestamps.set(idKey, now);
        }
        seenDiagnosisTimestamps.set(hashKey, now);
        return false;
    };
    const updateStatusBar = (snap) => {
        const scoreStr = snap.confusionScore.toFixed(2);
        const stack = serviceManager?.getState();
        const brainTag = snap.brainOnline
            ? snap.apiKeyConfigured === false
                ? "Brain Needs API Key"
                : `Live AI (${snap.brainModel || "gemini-2.5-flash"})`
            : "Brain Offline";
        rightPanel.updateLiveTelemetry(snap, stack);
        if (snap.askingBrain) {
            statusPill.text = "$(sync~spin) WHACK · Asking Chef...";
            statusPill.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
        }
        else if (activeDiagnosis?.source === "offline") {
            statusPill.text = "$(warning) WHACK · Launch Stack";
            statusPill.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
        }
        else if (activeDiagnosis) {
            const shortFile = activeDiagnosis.file
                ? path.basename(activeDiagnosis.file)
                : "workspace";
            const lineStr = activeDiagnosis.lines
                ? `:${activeDiagnosis.lines[0]}`
                : "";
            statusPill.text = `$(bell-dot) WHACK · ${activePatch ? "Fix Ready" : "Diagnosis"} (${shortFile}${lineStr})`;
            statusPill.backgroundColor = new vscode.ThemeColor(activeDiagnosis.uncertain
                ? "statusBarItem.warningBackground"
                : "statusBarItem.errorBackground");
        }
        else if (!snap.brainOnline) {
            statusPill.text = "$(rocket) WHACK · Click to Start";
            statusPill.backgroundColor = undefined;
        }
        else if (snap.cooldownUntil && snap.cooldownUntil > Date.now()) {
            statusPill.text = "$(bell-slash) WHACK · Snoozed";
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
            `WHACK (${brainTag} | Sensor:${snap.sensorActive ? "LIVE" : "Standby"} | Sound:${enableSound ? "ON" : "OFF"})\nClick to open WHACK Chef's Pass & Live Stack Controls · Press Ctrl+Shift+K to Diagnose`;
    };
    const fireBrainNotificationToast = (diagnosis, hasFix) => {
        if (!enableToast)
            return;
        if (diagnosis.source === "offline") {
            const errText = diagnosis.error || "";
            const isMissingKey = /missing.*api key|configure.*api_key/i.test(errText);
            const isConnRefused = /econnrefused|fetch failed|unable to connect|offline/i.test(errText) && !/timed out/i.test(errText);
            const promptMsg = isMissingKey
                ? `WHACK Brain needs an LLM API key (${errText}).`
                : isConnRefused
                    ? `WHACK Brain (:5002) is not running yet. Click 🚀 Launch Live Stack to start it!`
                    : `WHACK Brain request error: ${errText || diagnosis.summary}`;
            const buttons = isMissingKey
                ? ["🔑 Set API Key", "Open Panel"]
                : isConnRefused
                    ? ["🚀 Launch Live Stack", "Open Panel"]
                    : ["↻ Retry Diagnose", "🚀 Launch Live Stack", "Open Panel"];
            vscode.window
                .showWarningMessage(promptMsg, ...buttons)
                .then(async (selected) => {
                if (selected === "🚀 Launch Live Stack") {
                    await vscode.commands.executeCommand("whack.startLiveStack");
                }
                else if (selected === "🔑 Set API Key") {
                    await vscode.commands.executeCommand("whack.configureApiKey");
                }
                else if (selected === "↻ Retry Diagnose") {
                    await vscode.commands.executeCommand("whack.triggerDiagnose");
                }
                else if (selected === "Open Panel") {
                    rightPanel.reveal();
                }
            }, () => undefined);
            return;
        }
        const confPct = Math.round((diagnosis.confidence ?? 0.95) * 100);
        const shortFile = diagnosis.file
            ? path.basename(diagnosis.file)
            : "workspace";
        const lineRange = diagnosis.lines
            ? `:${diagnosis.lines[0]}–${diagnosis.lines[1]}`
            : "";
        const cleanSummary = diagnosis.summary.replace(/`/g, "");
        const diagReasonsLower = (diagnosis.reasons ?? []).join(" ").toLowerCase();
        const diagTextLower = `${diagReasonsLower} ${cleanSummary} ${diagnosis.quip || ""}`.toLowerCase();
        const isBreakReminder = diagReasonsLower.includes("fatig") ||
            diagReasonsLower.includes("tired") ||
            diagTextLower.includes("take a break") ||
            diagTextLower.includes("minute break") ||
            diagTextLower.includes("step off the line");
        const message = isBreakReminder
            ? `☕ WHACK Chef's Break Order: ${cleanSummary}`
            : `🔥 WHACK (${confPct}% · ${shortFile}${lineRange}): ${cleanSummary}`;
        const hasJumpTarget = Boolean(diagnosis.citations?.length || (diagnosis.file && diagnosis.lines));
        const actions = [];
        if (isBreakReminder) {
            actions.push("☕ Take 5m Break");
        }
        if (hasFix) {
            actions.push("✓ Apply Fix", "⇄ Preview Diff");
        }
        if (hasJumpTarget) {
            actions.push("↗ Jump to Line");
        }
        actions.push("💬 Ask in Chat", "✕ Dismiss");
        const notifyPromise = diagnosis.uncertain
            ? vscode.window.showWarningMessage(message, ...actions)
            : vscode.window.showInformationMessage(message, ...actions);
        notifyPromise.then(async (selected) => {
            if (!selected)
                return;
            if (selected === "☕ Take 5m Break") {
                await vscode.commands.executeCommand("whack.takeBreak");
            }
            else if (selected === "✓ Apply Fix") {
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
                    await highlightManager.jumpToCitation(primaryCit, diagnosis.repoPath);
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
    const presentBrainDiagnosis = async (diagnosis, sourceLabel, resetChat = true) => {
        if (resetChat && isDuplicateDiagnosis(diagnosis)) {
            return;
        }
        const isOffline = diagnosis.source === "offline";
        activeDiagnosis = diagnosis;
        activePatch = isOffline ? undefined : await (0, fixEngine_1.computeSuggestedPatch)(diagnosis);
        if (isOffline) {
            highlightManager.clearHighlights();
        }
        else {
            await highlightManager.showDiagnosisHighlights(diagnosis, Boolean(activePatch));
        }
        if (resetChat) {
            chatHistory = [];
        }
        rightPanel.showDiagnosis(diagnosis, {
            sourceLabel,
            brainOnline: !isOffline && Boolean(brainBridge?.status.brainOnline),
            hasFix: Boolean(activePatch),
            resetChat,
            playSound: resetChat && enableSound && !isOffline,
        });
        if (brainBridge) {
            updateStatusBar(brainBridge.status);
        }
        if (resetChat) {
            if (enableSound && !isOffline) {
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
                activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis);
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
                statusPill.text = "$(sync~spin) WHACK · Asking Chef...";
                const refreshed = await brainBridge.requestDiagnosis(level, telemetry.getActiveEditorContext(true));
                updateStatusBar(brainBridge.status);
                await presentBrainDiagnosis(refreshed, "Brain Tier Refresh", false);
            }
        },
        onJumpToCitation: async (citation) => {
            await highlightManager.jumpToCitation(citation, activeDiagnosis?.repoPath);
        },
        onPreviewDiff: async () => {
            await vscode.commands.executeCommand("whack.previewDiff");
        },
        onApplyFix: async () => {
            await vscode.commands.executeCommand("whack.applyFix");
        },
        onTriggerDiagnose: async () => {
            await vscode.commands.executeCommand("whack.triggerDiagnose");
        },
        onStartStack: async () => {
            await vscode.commands.executeCommand("whack.startLiveStack");
        },
        onStopStack: async () => {
            await vscode.commands.executeCommand("whack.stopLiveStack");
        },
        onToggleSensorHud: async () => {
            await vscode.commands.executeCommand("whack.toggleSensorHud");
        },
        onConfigureApiKey: async () => {
            await vscode.commands.executeCommand("whack.configureApiKey");
        },
        onShowLogs: () => {
            serviceManager?.showLogs();
        },
        onDismiss: async () => {
            await vscode.commands.executeCommand("whack.dismiss");
        },
        onTakeBreak: async () => {
            await vscode.commands.executeCommand("whack.takeBreak");
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
            const editorCtx = telemetry.getActiveEditorContext(false);
            const res = brainBridge
                ? await brainBridge.sendChatMessage({
                    message: text,
                    history: chatHistory,
                    activeFile: hl.file || activeDiagnosis?.file || editorCtx.activeFile,
                    highlightedLines: hl.lines || activeDiagnosis?.lines,
                    codeSnippet: hl.snippet || editorCtx.selectionText || undefined,
                    latestDiagnosis: activeDiagnosis,
                    repoPath: activeDiagnosis?.repoPath ||
                        editorCtx.workspaceRoot ||
                        workspaceRoots[0],
                })
                : {
                    reply: "Brain server (:5002) is offline — click **🚀 Launch Live Stack** above to chat live!",
                    mode: "offline",
                };
            rightPanel.setChatThinking(false);
            if (res.suggestedFix) {
                const baseDiag = activeDiagnosis
                    ? {
                        ...activeDiagnosis,
                        source: res.mode === "brain_live" ? "model" : activeDiagnosis.source,
                        file: res.suggestedFix.file || activeDiagnosis.file || editorCtx.activeFile,
                        lines: res.suggestedFix.lines || activeDiagnosis.lines,
                        citations: res.citations?.length
                            ? res.citations
                            : activeDiagnosis.citations,
                        suggestedFix: res.suggestedFix,
                        repoPath: activeDiagnosis.repoPath ||
                            editorCtx.workspaceRoot ||
                            workspaceRoots[0],
                    }
                    : {
                        summary: "Suggested fix from WHACK Brain chat.",
                        file: res.suggestedFix.file || editorCtx.activeFile,
                        lines: res.suggestedFix.lines,
                        quip: "Check the diff before serving it!",
                        uncertain: false,
                        confidence: 0.9,
                        hintLevel: "full_explanation",
                        source: "model",
                        repoPath: editorCtx.workspaceRoot || workspaceRoots[0],
                        citations: res.citations,
                        suggestedFix: res.suggestedFix,
                    };
                activeDiagnosis = baseDiag;
                activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis);
                await highlightManager.showDiagnosisHighlights(activeDiagnosis, Boolean(activePatch));
                rightPanel.updateDiagnosisInPlace(activeDiagnosis, {
                    brainOnline: res.mode === "brain_live",
                    hasFix: Boolean(activePatch),
                });
                if (brainBridge) {
                    updateStatusBar(brainBridge.status);
                }
            }
            else {
                rightPanel.setBrainOnline(res.mode === "brain_live");
            }
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
    // 3. Service Manager + Brain & Core Bridge + Telemetry
    serviceManager = new serviceManager_1.WhackServiceManager(context.extensionPath, workspaceRoots, brainUrl, {
        onStateChanged: (st) => {
            rightPanel.updateStackState(st);
            if (brainBridge) {
                brainBridge.refreshHealth().then(() => {
                    if (brainBridge)
                        updateStatusBar(brainBridge.status);
                });
            }
        },
    });
    rightPanel.updateStackState(serviceManager.getState());
    brainBridge = new brainAdapter_1.WhackBrainBridge(coreUrl, brainUrl, context.extensionPath, workspaceRoots, {
        onStatusUpdate: updateStatusBar,
        onDiagnosisTriggered: (diag, src) => {
            presentBrainDiagnosis(diag, src, true).catch(() => undefined);
        },
    });
    const telemetry = new telemetryWatcher_1.WhackTelemetryWatcher(brainBridge);
    brainBridge.setContextProvider(() => telemetry.getActiveEditorContext(false));
    brainBridge.start().then(() => {
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    });
    // 4. Register VS Code Commands
    context.subscriptions.push(statusPill, diffProvider, highlightManager, rightPanel, serviceManager, telemetry, vscode.workspace.registerTextDocumentContentProvider(fixEngine_1.DIFF_SCHEME, diffProvider), vscode.commands.registerCommand("whack.startLiveStack", async () => {
        rightPanel.reveal();
        await serviceManager?.startFullStack();
        await brainBridge?.refreshHealth();
        if (activeDiagnosis?.source === "offline" && brainBridge?.status.brainOnline) {
            activeDiagnosis = null;
            rightPanel.clearDiagnosisCard();
        }
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    }), vscode.commands.registerCommand("whack.stopLiveStack", async () => {
        await serviceManager?.stopFullStack();
        await brainBridge?.refreshHealth();
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    }), vscode.commands.registerCommand("whack.toggleSensorHud", async () => {
        await serviceManager?.toggleSensorHud();
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    }), vscode.commands.registerCommand("whack.configureApiKey", async () => {
        rightPanel.reveal();
        await serviceManager?.configureApiKey();
        await brainBridge?.refreshHealth();
        if (activeDiagnosis?.source === "offline" && brainBridge?.status.apiKeyConfigured) {
            activeDiagnosis = null;
            rightPanel.clearDiagnosisCard();
        }
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    }), vscode.commands.registerCommand("whack.triggerDiagnose", async () => {
        if (!brainBridge)
            return;
        statusPill.text = "$(sync~spin) WHACK · Asking Chef...";
        statusPill.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
        const diag = await brainBridge.requestDiagnosis("hint", telemetry.getActiveEditorContext(true));
        updateStatusBar(brainBridge.status);
        await presentBrainDiagnosis(diag, "Manual Trigger (Ctrl+Shift+K)", true);
    }), vscode.commands.registerCommand("whack.togglePanel", () => {
        rightPanel.toggle();
        if (brainBridge) {
            rightPanel.updateLiveTelemetry(brainBridge.status, serviceManager?.getState());
        }
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
        if (!activeDiagnosis)
            return;
        activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis);
        if (!activePatch) {
            vscode.window.showInformationMessage("No verified code replacement block is available for this diagnosis.");
            return;
        }
        const fileName = path.basename(activePatch.uri.fsPath);
        const proposedUri = vscode.Uri.parse(`${fixEngine_1.DIFF_SCHEME}://proposed/${encodeURIComponent(fileName)}?ts=${Date.now()}`);
        diffProvider.setContent(proposedUri, activePatch.previewFullDocument);
        await vscode.commands.executeCommand("vscode.diff", activePatch.uri, proposedUri, `${fileName}: Current ↔ WHACK Proposed Fix (${activePatch.summaryLabel})`, { viewColumn: vscode.ViewColumn.One, preserveFocus: false });
    }), vscode.commands.registerCommand("whack.applyFix", async () => {
        if (!activeDiagnosis)
            return;
        activePatch = await (0, fixEngine_1.computeSuggestedPatch)(activeDiagnosis);
        if (!activePatch) {
            vscode.window.showWarningMessage("No verified automatic patch is available for this diagnosis — ask in the WHACK chatbox for a fix!");
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
                mode: "brain_live",
            });
            brainBridge?.applyLocalFeedback("helpful");
        }
    }), vscode.commands.registerCommand("whack.jumpToCitation", async (cit) => {
        if (cit) {
            await highlightManager.jumpToCitation(cit, activeDiagnosis?.repoPath);
        }
    }), vscode.commands.registerCommand("whack.focusChat", () => {
        rightPanel.focusChatInput();
    }), vscode.commands.registerCommand("whack.dismiss", () => {
        highlightManager.clearHighlights();
        activeDiagnosis = null;
        activePatch = undefined;
        rightPanel.clearDiagnosisCard();
        brainBridge?.applyLocalFeedback("dismiss");
        if (brainBridge)
            updateStatusBar(brainBridge.status);
    }), vscode.commands.registerCommand("whack.takeBreak", () => {
        highlightManager.clearHighlights();
        activeDiagnosis = null;
        activePatch = undefined;
        rightPanel.clearDiagnosisCard();
        brainBridge?.applyLocalFeedback("break");
        if (brainBridge)
            updateStatusBar(brainBridge.status);
        vscode.window.showInformationMessage("☕ Chef's orders: Enjoy your 5-minute break! WHACK proactive triggers are paused for 5 minutes.");
    }), vscode.commands.registerCommand("whack.statusMenu", async () => {
        const picked = await vscode.window.showQuickPick([
            {
                label: "$(rocket) Launch Full Live Stack (Brain :5002 + Camera Sensor)",
                description: "Starts Brain server & live camera/OS fusion sensor in 1 click",
                id: "start",
            },
            {
                label: "$(key) Set Gemini / OpenAI / Anthropic API Key",
                description: "Saves your API key locally to brain/.env",
                id: "key",
            },
            {
                label: "$(layout-sidebar-right) Open WHACK Chef's Pass Panel",
                description: "Open the right-side Mission Control, Diagnosis & Chat panel",
                id: "toggle",
            },
            {
                label: "$(pulse) Trigger Live Brain Diagnosis Now (Ctrl+Shift+K)",
                description: "Sends active workspace, diagnostics & screenshot to Brain (:5002)",
                id: "diag",
            },
            {
                label: "$(debug-stop) Stop Live Stack",
                description: "Stops background Brain server and Sensor HUD",
                id: "stop",
            },
            {
                label: "$(close) Clear Highlights & Snooze 3m",
                description: "Clear active editor highlights and pause proactive triggers for 3 minutes",
                id: "dismiss",
            },
        ], {
            title: "WHACK — Live Multimodal Chef's Pass",
            placeHolder: "Choose an action",
        });
        if (!picked)
            return;
        if (picked.id === "start") {
            await vscode.commands.executeCommand("whack.startLiveStack");
        }
        else if (picked.id === "key") {
            await vscode.commands.executeCommand("whack.configureApiKey");
        }
        else if (picked.id === "toggle") {
            rightPanel.reveal();
        }
        else if (picked.id === "diag") {
            await vscode.commands.executeCommand("whack.triggerDiagnose");
        }
        else if (picked.id === "stop") {
            await vscode.commands.executeCommand("whack.stopLiveStack");
        }
        else if (picked.id === "dismiss") {
            await vscode.commands.executeCommand("whack.dismiss");
        }
    }));
}
function deactivate() {
    serviceManager?.dispose();
    serviceManager = null;
    brainBridge?.dispose();
    brainBridge = null;
}
