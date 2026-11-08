import * as path from "path";
import * as vscode from "vscode";
import { WhackBrainBridge } from "./brainAdapter";

/**
 * Passively tracks active file, cursor line, undo/redo edits, error diagnostic counts,
 * and terminal exits inside VS Code so Role 3's Brain always receives real editor context.
 */
export class WhackTelemetryWatcher implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  private lastEditAt = Date.now();
  public latestTerminalCommand?: string;
  public latestTerminalExitCode?: number;
  public latestTerminalTail?: string;

  constructor(private readonly bridge: WhackBrainBridge) {
    this.disposables.push(
      vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (!editor || editor.document.uri.scheme !== "file") return;
        this.emitEditorSnapshot(editor, false, false);
      }),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.document.uri.scheme !== "file") return;
        this.lastEditAt = Date.now();
        const isUndo = e.reason === vscode.TextDocumentChangeReason.Undo;
        const isRedo = e.reason === vscode.TextDocumentChangeReason.Redo;
        if (isUndo || isRedo) {
          const active = vscode.window.activeTextEditor;
          if (active && active.document === e.document) {
            this.emitEditorSnapshot(active, isUndo, isRedo);
          }
        }
      })
    );

    // Optional VS Code Shell Integration hook (VS Code 1.93+)
    if (vscode.window.onDidEndTerminalShellExecution) {
      this.disposables.push(
        vscode.window.onDidEndTerminalShellExecution(async (ev) => {
          const cmd = ev.execution.commandLine.value || "command";
          const exitCode = typeof ev.exitCode === "number" ? ev.exitCode : 0;
          let tail = "";
          if (ev.execution.read) {
            try {
              for await (const chunk of ev.execution.read()) {
                tail = (tail + chunk).slice(-800);
              }
            } catch {
              // Ignore stream errors
            }
          }
          this.latestTerminalCommand = cmd;
          this.latestTerminalExitCode = exitCode;
          this.latestTerminalTail = tail;

          this.bridge.postTelemetryEvent("terminal", "command_exit", {
            command: cmd,
            exitCode,
            durationMs: 300,
            tailOutput: tail,
          });
        })
      );
    }
  }

  public getActiveEditorContext(): {
    activeFile?: string;
    cursorLine?: number;
    selectionText?: string;
    diagnosticsCount?: number;
    terminalCommand?: string;
    terminalExitCode?: number;
    terminalTail?: string;
  } {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file") {
      return {
        terminalCommand: this.latestTerminalCommand,
        terminalExitCode: this.latestTerminalExitCode,
        terminalTail: this.latestTerminalTail,
      };
    }

    const diags = vscode.languages
      .getDiagnostics(editor.document.uri)
      .filter((d) => d.severity === vscode.DiagnosticSeverity.Error);
    const selText = editor.selection.isEmpty
      ? undefined
      : editor.document.getText(editor.selection);

    return {
      activeFile: path.basename(editor.document.fileName),
      cursorLine: editor.selection.active.line + 1,
      selectionText: selText,
      diagnosticsCount: diags.length,
      terminalCommand: this.latestTerminalCommand,
      terminalExitCode: this.latestTerminalExitCode,
      terminalTail: this.latestTerminalTail,
    };
  }

  private emitEditorSnapshot(
    editor: vscode.TextEditor,
    isUndo: boolean,
    isRedo: boolean
  ): void {
    const diags = vscode.languages
      .getDiagnostics(editor.document.uri)
      .filter((d) => d.severity === vscode.DiagnosticSeverity.Error);

    this.bridge.postTelemetryEvent("editor", isUndo ? "undo_burst" : "file_edit", {
      activeFile: path.basename(editor.document.fileName),
      isUndo,
      isRedo,
      diagnosticsCount: diags.length,
      cursorDwellMs: Date.now() - this.lastEditAt,
      cursorLine: editor.selection.active.line + 1,
    });
  }

  public dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}
