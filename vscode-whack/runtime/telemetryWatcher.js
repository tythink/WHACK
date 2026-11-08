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
exports.WhackTelemetryWatcher = void 0;
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
/**
 * Passively tracks active file, cursor line, undo/redo edits, error diagnostic counts,
 * and terminal exits inside VS Code so Role 3's Brain always receives real editor context.
 */
class WhackTelemetryWatcher {
    bridge;
    disposables = [];
    lastEditAt = Date.now();
    latestTerminalCommand;
    latestTerminalExitCode;
    latestTerminalTail;
    constructor(bridge) {
        this.bridge = bridge;
        this.disposables.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (!editor || editor.document.uri.scheme !== "file")
                return;
            this.emitEditorSnapshot(editor, false, false);
        }), vscode.workspace.onDidChangeTextDocument((e) => {
            if (e.document.uri.scheme !== "file")
                return;
            this.lastEditAt = Date.now();
            const isUndo = e.reason === vscode.TextDocumentChangeReason.Undo;
            const isRedo = e.reason === vscode.TextDocumentChangeReason.Redo;
            if (isUndo || isRedo) {
                const active = vscode.window.activeTextEditor;
                if (active && active.document === e.document) {
                    this.emitEditorSnapshot(active, isUndo, isRedo);
                }
            }
        }));
        // Optional VS Code Shell Integration hook (VS Code 1.93+)
        if (vscode.window.onDidEndTerminalShellExecution) {
            this.disposables.push(vscode.window.onDidEndTerminalShellExecution(async (ev) => {
                const cmd = ev.execution.commandLine.value || "command";
                const exitCode = typeof ev.exitCode === "number" ? ev.exitCode : 0;
                let tail = "";
                if (ev.execution.read) {
                    try {
                        for await (const chunk of ev.execution.read()) {
                            tail = (tail + chunk).slice(-800);
                        }
                    }
                    catch {
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
            }));
        }
    }
    getActiveEditorContext() {
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
    emitEditorSnapshot(editor, isUndo, isRedo) {
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
    dispose() {
        for (const d of this.disposables) {
            d.dispose();
        }
    }
}
exports.WhackTelemetryWatcher = WhackTelemetryWatcher;
