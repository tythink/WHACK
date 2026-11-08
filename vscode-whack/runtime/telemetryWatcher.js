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
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
/**
 * Passively tracks workspace-relative active file, cursor line/column, selection,
 * real VS Code diagnostics summary, terminal execution duration & tail output,
 * and `.git/logs/HEAD` commits (to refresh Brain's repo wiki cache).
 */
class WhackTelemetryWatcher {
    bridge;
    disposables = [];
    fsWatchers = [];
    gitDebounceTimers = new Map();
    lastGitHeadLine = new Map();
    executionStartTimes = new WeakMap();
    contextSyncTimer = null;
    lastEditAt = Date.now();
    lastFileUri;
    lastCursorLine = 1;
    lastCursorColumn = 1;
    lastSelectionText = "";
    activityEntries = [];
    latestTerminalCommand;
    latestTerminalExitCode;
    latestTerminalDurationMs;
    latestTerminalTail;
    constructor(bridge) {
        this.bridge = bridge;
        const initialEditor = vscode.window.activeTextEditor;
        if (initialEditor && initialEditor.document.uri.scheme === "file") {
            this.captureEditorCursor(initialEditor);
        }
        this.disposables.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (!editor || editor.document.uri.scheme !== "file")
                return;
            this.captureEditorCursor(editor);
            this.emitEditorSnapshot(editor, false, false);
            this.scheduleExternalContextSync();
        }), vscode.window.onDidChangeTextEditorSelection((ev) => {
            if (ev.textEditor.document.uri.scheme !== "file")
                return;
            this.captureEditorCursor(ev.textEditor);
            this.scheduleExternalContextSync();
        }), vscode.languages.onDidChangeDiagnostics(() => {
            this.scheduleExternalContextSync();
        }), vscode.workspace.onDidChangeTextDocument((e) => {
            if (e.document.uri.scheme !== "file")
                return;
            this.lastEditAt = Date.now();
            const isUndo = e.reason === vscode.TextDocumentChangeReason.Undo;
            const isRedo = e.reason === vscode.TextDocumentChangeReason.Redo;
            const relFile = this.toRelativeFilePath(e.document.uri);
            if (isUndo) {
                this.pushActivity(`Undo in ${relFile}`);
            }
            const active = vscode.window.activeTextEditor;
            if (active && active.document === e.document) {
                this.captureEditorCursor(active);
                if (isUndo || isRedo) {
                    this.emitEditorSnapshot(active, isUndo, isRedo);
                }
                this.scheduleExternalContextSync();
            }
        }));
        // Track real terminal start & end times via VS Code Shell Integration
        if (vscode.window.onDidStartTerminalShellExecution) {
            this.disposables.push(vscode.window.onDidStartTerminalShellExecution((ev) => {
                this.executionStartTimes.set(ev.execution, Date.now());
            }));
        }
        if (vscode.window.onDidEndTerminalShellExecution) {
            this.disposables.push(vscode.window.onDidEndTerminalShellExecution(async (ev) => {
                const startedAt = this.executionStartTimes.get(ev.execution);
                this.executionStartTimes.delete(ev.execution);
                const durationMs = startedAt ? Math.max(0, Date.now() - startedAt) : 0;
                const cmd = ev.execution.commandLine.value || "command";
                const exitCode = typeof ev.exitCode === "number" ? ev.exitCode : 0;
                let tail = "";
                if (ev.execution.read) {
                    try {
                        for await (const chunk of ev.execution.read()) {
                            tail = (tail + chunk).slice(-1200);
                        }
                    }
                    catch {
                        // Ignore stream errors
                    }
                }
                this.latestTerminalCommand = cmd;
                this.latestTerminalExitCode = exitCode;
                this.latestTerminalDurationMs = durationMs;
                this.latestTerminalTail = tail;
                this.pushActivity(`Terminal \`${cmd}\` exited ${exitCode} (${durationMs}ms)`);
                this.bridge.postTelemetryEvent("terminal", "command_exit", {
                    command: cmd,
                    exitCode,
                    durationMs,
                    tailOutput: tail,
                });
                this.scheduleExternalContextSync();
            }));
        }
        this.setupGitHeadWatchers();
        this.scheduleExternalContextSync();
    }
    pushActivity(entry) {
        const stamp = new Date().toISOString().slice(11, 19);
        this.activityEntries.push(`[${stamp}] ${entry}`);
        if (this.activityEntries.length > 15) {
            this.activityEntries.shift();
        }
    }
    captureEditorCursor(editor) {
        this.lastFileUri = editor.document.uri;
        this.lastCursorLine = editor.selection.active.line + 1;
        this.lastCursorColumn = editor.selection.active.character + 1;
        this.lastSelectionText = editor.selection.isEmpty
            ? ""
            : editor.document.getText(editor.selection);
    }
    toRelativeFilePath(uri) {
        const wf = vscode.workspace.getWorkspaceFolder(uri);
        if (wf) {
            return vscode.workspace.asRelativePath(uri, false).replace(/\\/g, "/");
        }
        return uri.fsPath.replace(/\\/g, "/");
    }
    collectDiagnosticsForUri(uri, relFile) {
        const allDiags = vscode.languages.getDiagnostics(uri);
        const relevant = allDiags
            .filter((d) => d.severity === vscode.DiagnosticSeverity.Error ||
            d.severity === vscode.DiagnosticSeverity.Warning)
            .sort((a, b) => a.severity - b.severity);
        const topLines = relevant.slice(0, 8).map((d) => {
            const sev = d.severity === vscode.DiagnosticSeverity.Error ? "Error" : "Warning";
            const ln = d.range.start.line + 1;
            const msg = d.message.replace(/\s+/g, " ").trim();
            return `${relFile}:${ln} [${sev}] ${msg}`;
        });
        return {
            count: relevant.length,
            summary: topLines.join("\n"),
            topLines,
        };
    }
    resolveCurrentEditor() {
        const active = vscode.window.activeTextEditor;
        if (active && active.document.uri.scheme === "file") {
            this.captureEditorCursor(active);
            return {
                doc: active.document,
                cursorLine: this.lastCursorLine,
                cursorColumn: this.lastCursorColumn,
                selectionText: this.lastSelectionText,
            };
        }
        const visibleFile = vscode.window.visibleTextEditors.find((ed) => ed.document.uri.scheme === "file");
        if (visibleFile) {
            return {
                doc: visibleFile.document,
                cursorLine: visibleFile.selection.active.line + 1,
                cursorColumn: visibleFile.selection.active.character + 1,
                selectionText: visibleFile.selection.isEmpty
                    ? ""
                    : visibleFile.document.getText(visibleFile.selection),
            };
        }
        if (this.lastFileUri) {
            const cachedDoc = vscode.workspace.textDocuments.find((d) => d.uri.scheme === "file" &&
                d.uri.fsPath.toLowerCase() === this.lastFileUri?.fsPath.toLowerCase());
            if (cachedDoc) {
                return {
                    doc: cachedDoc,
                    cursorLine: this.lastCursorLine,
                    cursorColumn: this.lastCursorColumn,
                    selectionText: this.lastSelectionText,
                };
            }
        }
        return null;
    }
    getActiveEditorContext(captureScreenshot = true) {
        const resolved = this.resolveCurrentEditor();
        const defaultWorkspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath.replace(/\\/g, "/");
        if (!resolved) {
            return {
                workspaceRoot: defaultWorkspaceRoot,
                activeFile: "",
                cursorLine: 1,
                cursorColumn: 1,
                selectionText: "",
                diagnosticsCount: 0,
                diagnosticsSummary: "",
                terminalCommand: this.latestTerminalCommand,
                terminalExitCode: this.latestTerminalExitCode,
                terminalDurationMs: this.latestTerminalDurationMs,
                terminalTail: this.latestTerminalTail,
                attemptLog: this.activityEntries.join("\n"),
                captureScreenshot,
            };
        }
        const { doc, cursorLine, cursorColumn, selectionText } = resolved;
        const wf = vscode.workspace.getWorkspaceFolder(doc.uri);
        const workspaceRoot = (wf?.uri.fsPath ??
            vscode.workspace.workspaceFolders?.[0]?.uri.fsPath)?.replace(/\\/g, "/");
        const activeFile = this.toRelativeFilePath(doc.uri);
        const diagInfo = this.collectDiagnosticsForUri(doc.uri, activeFile);
        return {
            workspaceRoot,
            activeFile,
            cursorLine,
            cursorColumn,
            selectionText,
            diagnosticsCount: diagInfo.count,
            diagnosticsSummary: diagInfo.summary,
            terminalCommand: this.latestTerminalCommand,
            terminalExitCode: this.latestTerminalExitCode,
            terminalDurationMs: this.latestTerminalDurationMs,
            terminalTail: this.latestTerminalTail,
            attemptLog: this.activityEntries.join("\n"),
            captureScreenshot,
        };
    }
    emitEditorSnapshot(editor, isUndo, isRedo) {
        const wf = vscode.workspace.getWorkspaceFolder(editor.document.uri);
        const workspaceRoot = (wf?.uri.fsPath ??
            vscode.workspace.workspaceFolders?.[0]?.uri.fsPath)?.replace(/\\/g, "/");
        const activeFile = this.toRelativeFilePath(editor.document.uri);
        const diagInfo = this.collectDiagnosticsForUri(editor.document.uri, activeFile);
        this.bridge.postTelemetryEvent("editor", isUndo ? "undo_burst" : "file_edit", {
            workspaceRoot,
            activeFile,
            isUndo,
            isRedo,
            diagnosticsCount: diagInfo.count,
            diagnosticsSummary: diagInfo.summary,
            topDiagnostics: diagInfo.topLines,
            cursorDwellMs: Date.now() - this.lastEditAt,
            cursorLine: editor.selection.active.line + 1,
            cursorColumn: editor.selection.active.character + 1,
            selectionText: editor.selection.isEmpty
                ? ""
                : editor.document.getText(editor.selection),
        });
    }
    scheduleExternalContextSync() {
        if (this.contextSyncTimer) {
            clearTimeout(this.contextSyncTimer);
        }
        this.contextSyncTimer = setTimeout(() => {
            this.contextSyncTimer = null;
            this.bridge.pushExternalContext(this.getActiveEditorContext(false));
        }, 200);
        this.contextSyncTimer.unref?.();
    }
    /**
     * Watches .git/logs/HEAD across open workspace folders (debounced 800ms)
     * and triggers POST :5002/wiki/update when a new git commit entry appears.
     */
    setupGitHeadWatchers() {
        // 1. VS Code glob watcher across workspace folders
        const globWatcher = vscode.workspace.createFileSystemWatcher("**/.git/logs/HEAD");
        const onUriEvent = (uri) => {
            const wf = vscode.workspace.getWorkspaceFolder(uri);
            const root = wf?.uri.fsPath ?? path.resolve(path.dirname(uri.fsPath), "..", "..");
            this.onGitHeadChanged(root);
        };
        this.disposables.push(globWatcher, globWatcher.onDidCreate(onUriEvent), globWatcher.onDidChange(onUriEvent));
        // 2. Direct fs.watch on `<workspaceRoot>/.git/logs` so VS Code's `files.watcherExclude`
        //    default ignore on `.git` never suppresses commit detection
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            this.watchFolderGitLogs(folder.uri.fsPath);
        }
        if (vscode.workspace.onDidChangeWorkspaceFolders) {
            this.disposables.push(vscode.workspace.onDidChangeWorkspaceFolders((ev) => {
                for (const added of ev.added) {
                    this.watchFolderGitLogs(added.uri.fsPath);
                }
            }));
        }
    }
    readLastGitHeadLine(headLogPath) {
        try {
            if (!fs.existsSync(headLogPath))
                return null;
            const content = fs.readFileSync(headLogPath, "utf8");
            const lines = content
                .split(/\r?\n/)
                .map((l) => l.trim())
                .filter((l) => l.length > 0);
            return lines.length > 0 ? lines[lines.length - 1] : null;
        }
        catch {
            return null;
        }
    }
    watchFolderGitLogs(workspaceRoot) {
        const headLogPath = path.join(workspaceRoot, ".git", "logs", "HEAD");
        const initialLine = this.readLastGitHeadLine(headLogPath);
        if (initialLine) {
            this.lastGitHeadLine.set(workspaceRoot, initialLine);
        }
        const logsDir = path.dirname(headLogPath);
        if (!fs.existsSync(logsDir))
            return;
        try {
            const watcher = fs.watch(logsDir, (_eventType, filename) => {
                if (!filename || filename === "HEAD") {
                    this.onGitHeadChanged(workspaceRoot);
                }
            });
            this.fsWatchers.push(watcher);
        }
        catch {
            // Ignore fs.watch errors if directory is inaccessible
        }
    }
    onGitHeadChanged(workspaceRoot) {
        const existing = this.gitDebounceTimers.get(workspaceRoot);
        if (existing) {
            clearTimeout(existing);
        }
        const timer = setTimeout(() => {
            this.gitDebounceTimers.delete(workspaceRoot);
            const headLogPath = path.join(workspaceRoot, ".git", "logs", "HEAD");
            const latestLine = this.readLastGitHeadLine(headLogPath);
            if (!latestLine)
                return;
            const previousLine = this.lastGitHeadLine.get(workspaceRoot);
            if (latestLine === previousLine)
                return;
            this.lastGitHeadLine.set(workspaceRoot, latestLine);
            // Match git reflog commit lines (e.g. `\tcommit: ...`, `\tcommit (initial): ...`, `\tcommit (amend): ...`)
            if (/\tcommit(?:\s|\(|:)/i.test(latestLine)) {
                this.pushActivity(`Git commit detected in ${path.basename(workspaceRoot)}`);
                this.bridge.triggerWikiUpdate(workspaceRoot);
            }
        }, 800);
        timer.unref?.();
        this.gitDebounceTimers.set(workspaceRoot, timer);
    }
    dispose() {
        if (this.contextSyncTimer) {
            clearTimeout(this.contextSyncTimer);
            this.contextSyncTimer = null;
        }
        for (const timer of this.gitDebounceTimers.values()) {
            clearTimeout(timer);
        }
        this.gitDebounceTimers.clear();
        for (const w of this.fsWatchers) {
            try {
                w.close();
            }
            catch {
                // Ignore close errors
            }
        }
        this.fsWatchers.length = 0;
        for (const d of this.disposables) {
            d.dispose();
        }
    }
}
exports.WhackTelemetryWatcher = WhackTelemetryWatcher;
