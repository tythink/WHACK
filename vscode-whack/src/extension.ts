import * as path from "path";
import * as vscode from "vscode";
import { WhackBrainBridge } from "./brainAdapter";
import {
  ChatMessage,
  CodeCitation,
  DiagnosisResponse,
  HintLevel,
  PassiveStatusSnapshot,
} from "./contracts";
import {
  ComputedPatch,
  DIFF_SCHEME,
  WhackDiffContentProvider,
  computeSuggestedPatch,
} from "./fixEngine";
import { WhackHighlightManager } from "./highlightManager";
import { WhackRightSidePanel } from "./rightSidePanel";
import { ScenarioId, buildSimulatedDiagnosis } from "./simulatorScenarios";
import { WhackTelemetryWatcher } from "./telemetryWatcher";

let brainBridge: WhackBrainBridge | null = null;

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  const config = vscode.workspace.getConfiguration("whack");
  const coreUrl = config.get<string>("coreServerUrl", "http://127.0.0.1:3000");
  const brainUrl = config.get<string>("brainServerUrl", "http://127.0.0.1:5002");
  const showSimBtn = config.get<boolean>("showSimulateStatusBarButton", true);

  const workspaceRoots = (vscode.workspace.workspaceFolders ?? []).map(
    (f) => f.uri.fsPath
  );

  // 1. Passive Status Bar Pill (stays tiny & quiet while you code!)
  const statusPill = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    102
  );
  statusPill.name = "WHACK Passive Status";
  statusPill.command = "whack.statusMenu";
  statusPill.text = "$(pulse) WHACK · Flow 0.08";
  statusPill.tooltip =
    "WHACK is passively monitoring. Click for options or press Ctrl+Shift+K to diagnose.";
  statusPill.show();

  // 2. One-click "Simulate Brain" Status Bar test button (for testing before Role 3 Brain is ready)
  const simulatePill = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    101
  );
  simulatePill.name = "WHACK Simulate Brain";
  simulatePill.command = "whack.simulateBrain";
  simulatePill.text = "$(beaker) Simulate Brain";
  simulatePill.tooltip =
    "Test WHACK Brain trigger: opens code, highlights target lines, and opens the right-side Diagnosis + Chat panel (Ctrl+Alt+S)";
  if (showSimBtn) {
    simulatePill.show();
  }

  // 3. Diff Provider + Highlight Manager + Right-Side Panel
  const diffProvider = new WhackDiffContentProvider();
  const highlightManager = new WhackHighlightManager(context.extensionPath);

  let activeDiagnosis: DiagnosisResponse | null = null;
  let activePatch: ComputedPatch | undefined;
  let chatHistory: ChatMessage[] = [];

  const updateStatusBar = (snap: PassiveStatusSnapshot): void => {
    const scoreStr = snap.confusionScore.toFixed(2);
    const brainTag = snap.brainOnline ? "Brain:Live" : "Brain:Mock";

    if (snap.cooldownUntil && snap.cooldownUntil > Date.now()) {
      statusPill.text = `$(bell-slash) WHACK · Snoozed`;
      statusPill.backgroundColor = undefined;
    } else if (snap.faceState === "confused" || snap.confusionScore >= 0.7) {
      statusPill.text = `$(flame) WHACK · Stuck ${scoreStr}`;
      statusPill.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.errorBackground"
      );
    } else if (snap.confusionScore >= 0.4) {
      statusPill.text = `$(eye) WHACK · Thinking ${scoreStr}`;
      statusPill.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.warningBackground"
      );
    } else {
      statusPill.text = `$(pulse) WHACK · ${snap.userStatusLabel || "Flow"} ${scoreStr}`;
      statusPill.backgroundColor = undefined;
    }

    statusPill.tooltip =
      `WHACK Passive Monitor (${brainTag} | Core:${snap.coreMode})\n` +
      `Click for WHACK Quick Menu · Ctrl+Shift+K to Diagnose · Ctrl+Alt+S to Simulate`;
  };

  /**
   * Central handler invoked whenever Brain returns a result (real LLM or Simulator):
   * 1. Computes applicable patch (if any)
   * 2. Opens target file on left (`ViewColumn.One`), scrolls to `lines`, and highlights lines + citations
   * 3. Slides open the Right-Side Panel (`ViewColumn.Beside`) with diagnosis & follow-up chatbox
   */
  const presentBrainDiagnosis = async (
    diagnosis: DiagnosisResponse,
    sourceLabel: string,
    resetChat = true
  ): Promise<void> => {
    activeDiagnosis = diagnosis;
    activePatch = await computeSuggestedPatch(diagnosis, context.extensionPath);

    await highlightManager.showDiagnosisHighlights(
      diagnosis,
      Boolean(activePatch)
    );

    if (resetChat) {
      chatHistory = [];
    }

    rightPanel.showDiagnosis(diagnosis, {
      sourceLabel,
      brainOnline: Boolean(brainBridge?.status.brainOnline),
      hasFix: Boolean(activePatch),
      resetChat,
    });
  };

  const rightPanel = new WhackRightSidePanel({
    onSelectHintLevel: async (level: HintLevel) => {
      if (!activeDiagnosis) return;
      if (activeDiagnosis.tierVariants?.[level]) {
        activeDiagnosis = { ...activeDiagnosis, hintLevel: level };
        activePatch = await computeSuggestedPatch(
          activeDiagnosis,
          context.extensionPath
        );
        rightPanel.showDiagnosis(activeDiagnosis, {
          sourceLabel: "Tier Switch",
          brainOnline: Boolean(brainBridge?.status.brainOnline),
          hasFix: Boolean(activePatch),
          resetChat: false,
        });
        return;
      }

      if (brainBridge) {
        const refreshed = await brainBridge.requestDiagnosis(
          level,
          telemetry.getActiveEditorContext()
        );
        await presentBrainDiagnosis(refreshed, "Brain Tier Refresh", false);
      }
    },

    onJumpToCitation: async (citation: CodeCitation) => {
      await highlightManager.jumpToCitation(citation);
    },

    onPreviewDiff: async () => {
      if (!activeDiagnosis) return;
      activePatch = await computeSuggestedPatch(
        activeDiagnosis,
        context.extensionPath
      );
      if (!activePatch) {
        vscode.window.showInformationMessage(
          "No structured code replacement block was found in this diagnosis."
        );
        return;
      }

      const fileName = path.basename(activePatch.uri.fsPath);
      const proposedUri = vscode.Uri.parse(
        `${DIFF_SCHEME}://proposed/${encodeURIComponent(fileName)}?ts=${Date.now()}`
      );
      diffProvider.setContent(proposedUri, activePatch.previewFullDocument);

      await vscode.commands.executeCommand(
        "vscode.diff",
        activePatch.uri,
        proposedUri,
        `${fileName}: Current ↔ WHACK Proposed Fix (${activePatch.summaryLabel})`,
        { viewColumn: vscode.ViewColumn.One, preserveFocus: false }
      );
    },

    onApplyFix: async () => {
      if (!activeDiagnosis) return;
      activePatch = await computeSuggestedPatch(
        activeDiagnosis,
        context.extensionPath
      );
      if (!activePatch) {
        vscode.window.showWarningMessage(
          "No automatic patch available for this hint—ask in the WHACK chatbox for a snippet!"
        );
        return;
      }

      const edit = new vscode.WorkspaceEdit();
      edit.replace(activePatch.uri, activePatch.range, activePatch.newText);
      const ok = await vscode.workspace.applyEdit(edit);
      if (ok) {
        await highlightManager.flashAppliedFix(
          activePatch.uri,
          activePatch.range
        );
        rightPanel.appendChatMessage({
          role: "assistant",
          content: `✓ Applied fix (${activePatch.summaryLabel}) in \`${path.basename(
            activePatch.uri.fsPath
          )}\`. Press \`Ctrl+Z\` in the editor anytime to undo, or run \`pytest\` to verify!`,
          timestamp: Date.now(),
        });
        brainBridge?.applyLocalFeedback("helpful");
      }
    },

    onDismiss: async () => {
      highlightManager.clearHighlights();
      rightPanel.close();
      activeDiagnosis = null;
      activePatch = undefined;
      brainBridge?.applyLocalFeedback("dismiss");
    },

    onSimulateScenario: async (scenario: ScenarioId) => {
      const simDiag = buildSimulatedDiagnosis(scenario, context.extensionPath);
      await presentBrainDiagnosis(simDiag, `Simulated (${scenario})`, true);
    },

    onSendChatMessage: async (text: string) => {
      const userMsg: ChatMessage = {
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
            mode: "mock_fallback" as const,
          };

      rightPanel.setChatThinking(false);
      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: res.reply,
        timestamp: Date.now(),
        mode: res.mode,
      };
      chatHistory.push(assistantMsg);
      rightPanel.appendChatMessage(assistantMsg);
    },
  });

  // 4. Start Brain & Core Bridge + Passive Editor/Terminal Telemetry
  brainBridge = new WhackBrainBridge(
    coreUrl,
    brainUrl,
    context.extensionPath,
    workspaceRoots,
    {
      onStatusUpdate: updateStatusBar,
      onDiagnosisTriggered: (diag, src) => {
        presentBrainDiagnosis(diag, src, true).catch(() => undefined);
      },
    }
  );
  const telemetry = new WhackTelemetryWatcher(brainBridge);
  brainBridge.start().catch(() => undefined);

  // 5. Register VS Code Commands
  context.subscriptions.push(
    statusPill,
    simulatePill,
    diffProvider,
    highlightManager,
    rightPanel,
    telemetry,
    vscode.workspace.registerTextDocumentContentProvider(
      DIFF_SCHEME,
      diffProvider
    ),

    vscode.commands.registerCommand("whack.simulateBrain", async () => {
      const sim = buildSimulatedDiagnosis(
        "sliding_window",
        context.extensionPath
      );
      await presentBrainDiagnosis(sim, "Simulated Brain", true);
    }),

    vscode.commands.registerCommand("whack.simulateActiveFile", async () => {
      const sim = buildSimulatedDiagnosis(
        "active_editor",
        context.extensionPath
      );
      await presentBrainDiagnosis(sim, "Active File Simulation", true);
    }),

    vscode.commands.registerCommand("whack.triggerDiagnose", async () => {
      if (!brainBridge) return;
      statusPill.text = "$(sync~spin) WHACK · Diagnosing...";
      const diag = await brainBridge.requestDiagnosis(
        "hint",
        telemetry.getActiveEditorContext()
      );
      updateStatusBar(brainBridge.status);
      await presentBrainDiagnosis(diag, "Manual Trigger (Ctrl+Shift+K)", true);
    }),

    vscode.commands.registerCommand("whack.togglePanel", () => {
      rightPanel.toggle();
    }),

    vscode.commands.registerCommand("whack.previewDiff", async () => {
      await vscode.commands.executeCommand("whack.focusPanelInternalPreview");
    }),

    vscode.commands.registerCommand(
      "whack.focusPanelInternalPreview",
      async () => {
        if (!activeDiagnosis) return;
        activePatch = await computeSuggestedPatch(
          activeDiagnosis,
          context.extensionPath
        );
        if (!activePatch) return;
        const fileName = path.basename(activePatch.uri.fsPath);
        const proposedUri = vscode.Uri.parse(
          `${DIFF_SCHEME}://proposed/${encodeURIComponent(fileName)}?ts=${Date.now()}`
        );
        diffProvider.setContent(proposedUri, activePatch.previewFullDocument);
        await vscode.commands.executeCommand(
          "vscode.diff",
          activePatch.uri,
          proposedUri,
          `${fileName}: Current ↔ WHACK Proposed Fix`,
          { viewColumn: vscode.ViewColumn.One, preserveFocus: false }
        );
      }
    ),

    vscode.commands.registerCommand("whack.applyFix", async () => {
      if (!activeDiagnosis) return;
      activePatch = await computeSuggestedPatch(
        activeDiagnosis,
        context.extensionPath
      );
      if (!activePatch) return;
      const edit = new vscode.WorkspaceEdit();
      edit.replace(activePatch.uri, activePatch.range, activePatch.newText);
      if (await vscode.workspace.applyEdit(edit)) {
        await highlightManager.flashAppliedFix(
          activePatch.uri,
          activePatch.range
        );
        rightPanel.appendChatMessage({
          role: "assistant",
          content: `✓ Applied fix (${activePatch.summaryLabel}) in \`${path.basename(
            activePatch.uri.fsPath
          )}\`.`,
          timestamp: Date.now(),
        });
      }
    }),

    vscode.commands.registerCommand(
      "whack.jumpToCitation",
      async (cit?: CodeCitation) => {
        if (cit) {
          await highlightManager.jumpToCitation(cit);
        }
      }
    ),

    vscode.commands.registerCommand("whack.focusChat", () => {
      rightPanel.focusChatInput();
    }),

    vscode.commands.registerCommand("whack.dismiss", () => {
      highlightManager.clearHighlights();
      rightPanel.close();
      activeDiagnosis = null;
      activePatch = undefined;
      brainBridge?.applyLocalFeedback("dismiss");
    }),

    vscode.commands.registerCommand("whack.statusMenu", async () => {
      const picked = await vscode.window.showQuickPick(
        [
          {
            label: "$(beaker) Simulate Brain Result (Planted Bug in utils.py)",
            description: "Highlights L18–21 & L29–30 + opens right-side panel",
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
            label: "$(layout-sidebar-right) Toggle Right-Side Diagnosis & Chat Panel",
            id: "toggle",
          },
          {
            label: "$(close) Dismiss Highlights & Snooze 3m",
            id: "dismiss",
          },
        ],
        {
          title: "WHACK — Passive Confusion & Brain Assistant",
          placeHolder: "Choose an action to test or control WHACK",
        }
      );

      if (!picked) return;
      if (picked.id === "sim_bug") {
        await vscode.commands.executeCommand("whack.simulateBrain");
      } else if (picked.id === "sim_active") {
        await vscode.commands.executeCommand("whack.simulateActiveFile");
      } else if (picked.id === "diag") {
        await vscode.commands.executeCommand("whack.triggerDiagnose");
      } else if (picked.id === "toggle") {
        await vscode.commands.executeCommand("whack.togglePanel");
      } else if (picked.id === "dismiss") {
        await vscode.commands.executeCommand("whack.dismiss");
      }
    })
  );
}

export function deactivate(): void {
  brainBridge?.dispose();
  brainBridge = null;
}
