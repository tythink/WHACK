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
exports.WhackHighlightManager = void 0;
const vscode = __importStar(require("vscode"));
const fixEngine_1 = require("./fixEngine");
/**
 * Manages real-time VS Code Editor line highlights, inline ghost annotations,
 * hover cards, and inline CodeLens actions (`Preview Diff | Apply Fix | Ask in Chat | Dismiss`).
 */
class WhackHighlightManager {
    primaryDecoration;
    secondaryDecoration;
    appliedFlashDecoration;
    codeLensChange = new vscode.EventEmitter();
    onDidChangeCodeLenses = this.codeLensChange.event;
    activeDiagnosis = null;
    resolvedHighlights = [];
    hasApplicableFix = false;
    flashTimer = null;
    disposables = [];
    constructor() {
        const primarySvg = encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">` +
            `<circle cx="8" cy="8" r="5.5" fill="#f25533" stroke="#ffffff" stroke-width="1.2"/></svg>`);
        const secondarySvg = encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">` +
            `<circle cx="8" cy="8" r="4.5" fill="#d97706"/></svg>`);
        this.primaryDecoration = vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: "rgba(242, 85, 51, 0.17)",
            borderWidth: "0 0 0 3px",
            borderStyle: "solid",
            borderColor: "#f25533",
            overviewRulerColor: "#f25533",
            overviewRulerLane: vscode.OverviewRulerLane.Full,
            gutterIconPath: vscode.Uri.parse(`data:image/svg+xml;utf8,${primarySvg}`),
            gutterIconSize: "80%",
        });
        this.secondaryDecoration = vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: "rgba(217, 119, 6, 0.13)",
            borderWidth: "0 0 0 3px",
            borderStyle: "solid",
            borderColor: "#d97706",
            overviewRulerColor: "#d97706",
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            gutterIconPath: vscode.Uri.parse(`data:image/svg+xml;utf8,${secondarySvg}`),
            gutterIconSize: "75%",
        });
        this.appliedFlashDecoration = vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: "rgba(15, 157, 118, 0.24)",
            borderWidth: "0 0 0 3px",
            borderStyle: "solid",
            borderColor: "#0f9d76",
        });
        this.disposables.push(vscode.window.onDidChangeVisibleTextEditors(() => {
            this.refreshVisibleEditors();
        }), vscode.languages.registerCodeLensProvider({ scheme: "file" }, this), vscode.languages.registerHoverProvider({ scheme: "file" }, this));
    }
    /**
     * Opens the primary cited file in ViewColumn.One (left side), scrolls to the target lines,
     * and highlights both the primary range and any additional `citations[]`.
     * Never highlights fake lines when `diagnosis.source === "offline"`.
     */
    async showDiagnosisHighlights(diagnosis, hasFix) {
        if (diagnosis.source === "offline") {
            this.clearHighlights();
            return {};
        }
        const primaryFileCandidate = diagnosis.file || diagnosis.citations?.[0]?.file;
        const primaryLinesCandidate = diagnosis.lines || diagnosis.citations?.[0]?.lines;
        if (!primaryFileCandidate || !primaryLinesCandidate) {
            this.clearHighlights();
            return {};
        }
        this.activeDiagnosis = diagnosis;
        this.hasApplicableFix = hasFix;
        this.resolvedHighlights = [];
        // 1. Resolve primary target range
        const primaryUri = await (0, fixEngine_1.resolveTargetFileUri)(primaryFileCandidate, diagnosis.repoPath);
        const primaryLines = primaryLinesCandidate;
        if (primaryUri) {
            this.resolvedHighlights.push({
                uri: primaryUri,
                startLine1: primaryLines[0],
                endLine1: primaryLines[1],
                isPrimary: true,
                note: diagnosis.citations?.[0]?.note || diagnosis.summary,
                snippet: diagnosis.citations?.[0]?.snippet,
            });
        }
        // 2. Resolve any additional secondary citations
        for (const cit of diagnosis.citations ?? []) {
            const citUri = await (0, fixEngine_1.resolveTargetFileUri)(cit.file, diagnosis.repoPath);
            if (!citUri)
                continue;
            const isDuplicatePrimary = primaryUri &&
                citUri.fsPath.toLowerCase() === primaryUri.fsPath.toLowerCase() &&
                cit.lines[0] === primaryLines[0] &&
                cit.lines[1] === primaryLines[1];
            if (isDuplicatePrimary)
                continue;
            this.resolvedHighlights.push({
                uri: citUri,
                startLine1: cit.lines[0],
                endLine1: cit.lines[1],
                isPrimary: false,
                note: cit.note || "Related location cited by WHACK Brain",
                snippet: cit.snippet,
            });
        }
        // 3. Open & reveal primary location in left-hand editor column (`ViewColumn.One`)
        if (primaryUri) {
            const doc = await vscode.workspace.openTextDocument(primaryUri);
            if (doc.lineCount > 0) {
                const s0 = Math.max(0, Math.min(doc.lineCount - 1, primaryLines[0] - 1));
                const e0 = Math.max(s0, Math.min(doc.lineCount - 1, primaryLines[1] - 1));
                const revealRange = new vscode.Range(new vscode.Position(s0, 0), doc.lineAt(e0).range.end);
                const editor = await vscode.window.showTextDocument(doc, {
                    viewColumn: vscode.ViewColumn.One,
                    preserveFocus: false,
                    preview: false,
                });
                editor.revealRange(revealRange, vscode.TextEditorRevealType.InCenter);
            }
        }
        this.refreshVisibleEditors();
        this.codeLensChange.fire();
        return {
            primaryUri,
            primaryLines: primaryUri ? primaryLines : undefined,
        };
    }
    /**
     * Jumps the left-hand code editor to a specific `CodeCitation` when clicked in the right-side panel.
     */
    async jumpToCitation(citation, repoPath) {
        const uri = await (0, fixEngine_1.resolveTargetFileUri)(citation.file, repoPath ?? this.activeDiagnosis?.repoPath);
        if (!uri)
            return;
        const doc = await vscode.workspace.openTextDocument(uri);
        if (doc.lineCount === 0)
            return;
        const s0 = Math.max(0, Math.min(doc.lineCount - 1, citation.lines[0] - 1));
        const e0 = Math.max(s0, Math.min(doc.lineCount - 1, citation.lines[1] - 1));
        const range = new vscode.Range(new vscode.Position(s0, 0), doc.lineAt(e0).range.end);
        const editor = await vscode.window.showTextDocument(doc, {
            viewColumn: vscode.ViewColumn.One,
            preserveFocus: false,
            preview: false,
        });
        editor.selection = new vscode.Selection(range.start, range.start);
        editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
    }
    /**
     * Flashes emerald green on the patched lines after clicking "Apply Suggested Fix"
     * and clears the red bug highlights.
     */
    async flashAppliedFix(uri, range) {
        this.clearHighlights();
        const doc = await vscode.workspace.openTextDocument(uri);
        if (doc.lineCount === 0)
            return;
        const editor = await vscode.window.showTextDocument(doc, {
            viewColumn: vscode.ViewColumn.One,
            preserveFocus: false,
        });
        const endLine = Math.min(doc.lineCount - 1, range.end.line);
        const fullLineRange = new vscode.Range(new vscode.Position(range.start.line, 0), doc.lineAt(endLine).range.end);
        editor.setDecorations(this.appliedFlashDecoration, [
            {
                range: fullLineRange,
                renderOptions: {
                    after: {
                        contentText: "  ✓ WHACK Fix Applied (Ctrl+Z to undo)",
                        color: "#10b981",
                        fontStyle: "italic",
                        margin: "0 0 0 18px",
                    },
                },
            },
        ]);
        if (this.flashTimer) {
            clearTimeout(this.flashTimer);
        }
        this.flashTimer = setTimeout(() => {
            this.flashTimer = null;
            for (const ed of vscode.window.visibleTextEditors) {
                ed.setDecorations(this.appliedFlashDecoration, []);
            }
        }, 2400);
        this.flashTimer.unref?.();
    }
    clearHighlights() {
        this.activeDiagnosis = null;
        this.resolvedHighlights = [];
        this.hasApplicableFix = false;
        if (this.flashTimer) {
            clearTimeout(this.flashTimer);
            this.flashTimer = null;
        }
        for (const editor of vscode.window.visibleTextEditors) {
            editor.setDecorations(this.primaryDecoration, []);
            editor.setDecorations(this.secondaryDecoration, []);
            editor.setDecorations(this.appliedFlashDecoration, []);
        }
        this.codeLensChange.fire();
    }
    getHighlightedSnippet() {
        const primary = this.resolvedHighlights.find((h) => h.isPrimary);
        if (!primary)
            return {};
        const wf = vscode.workspace.getWorkspaceFolder(primary.uri);
        const relFile = wf
            ? vscode.workspace.asRelativePath(primary.uri, false).replace(/\\/g, "/")
            : primary.uri.fsPath.replace(/\\/g, "/");
        const openDoc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath.toLowerCase() === primary.uri.fsPath.toLowerCase());
        if (!openDoc || openDoc.lineCount === 0) {
            return {
                file: relFile,
                lines: [primary.startLine1, primary.endLine1],
            };
        }
        const s0 = Math.max(0, Math.min(openDoc.lineCount - 1, primary.startLine1 - 1));
        const e0 = Math.max(s0, Math.min(openDoc.lineCount - 1, primary.endLine1 - 1));
        const range = new vscode.Range(new vscode.Position(s0, 0), openDoc.lineAt(e0).range.end);
        return {
            file: relFile,
            lines: [primary.startLine1, primary.endLine1],
            snippet: openDoc.getText(range),
        };
    }
    refreshVisibleEditors() {
        for (const editor of vscode.window.visibleTextEditors) {
            const editorPath = editor.document.uri.fsPath.toLowerCase();
            const matching = this.resolvedHighlights.filter((h) => h.uri.fsPath.toLowerCase() === editorPath);
            if (matching.length === 0 || editor.document.lineCount === 0) {
                editor.setDecorations(this.primaryDecoration, []);
                editor.setDecorations(this.secondaryDecoration, []);
                continue;
            }
            const primaryOpts = [];
            const secondaryOpts = [];
            const doc = editor.document;
            for (const item of matching) {
                const s0 = Math.max(0, Math.min(doc.lineCount - 1, item.startLine1 - 1));
                const e0 = Math.max(s0, Math.min(doc.lineCount - 1, item.endLine1 - 1));
                const range = new vscode.Range(new vscode.Position(s0, 0), doc.lineAt(e0).range.end);
                const shortLabel = item.note.length > 68 ? `${item.note.slice(0, 65)}...` : item.note;
                const firstLineRange = doc.lineAt(s0).range;
                const blockOpt = { range };
                const labelOpt = {
                    range: firstLineRange,
                    renderOptions: {
                        after: {
                            contentText: `  ⚡ WHACK: ${shortLabel}`,
                            color: item.isPrimary ? "#ff7a5c" : "#fbbf24",
                            fontStyle: "italic",
                            margin: "0 0 0 20px",
                        },
                    },
                };
                if (item.isPrimary) {
                    primaryOpts.push(blockOpt, labelOpt);
                }
                else {
                    secondaryOpts.push(blockOpt, labelOpt);
                }
            }
            editor.setDecorations(this.primaryDecoration, primaryOpts);
            editor.setDecorations(this.secondaryDecoration, secondaryOpts);
        }
    }
    provideCodeLenses(document) {
        if (!this.activeDiagnosis || document.lineCount === 0)
            return [];
        const docPath = document.uri.fsPath.toLowerCase();
        const lenses = [];
        for (const h of this.resolvedHighlights) {
            if (h.uri.fsPath.toLowerCase() !== docPath)
                continue;
            const line0 = Math.max(0, Math.min(document.lineCount - 1, h.startLine1 - 1));
            const topRange = new vscode.Range(line0, 0, line0, 0);
            const confPct = Math.round((this.activeDiagnosis.confidence ?? 0.95) * 100);
            lenses.push(new vscode.CodeLens(topRange, {
                title: h.isPrimary
                    ? `⚡ WHACK Diagnosis (${confPct}%)`
                    : `⚡ WHACK Related Citation`,
                command: "whack.togglePanel",
                tooltip: h.note,
            }));
            if (h.isPrimary && this.hasApplicableFix) {
                lenses.push(new vscode.CodeLens(topRange, {
                    title: "$(diff) Preview Diff",
                    command: "whack.previewDiff",
                    tooltip: "Compare current file with WHACK's proposed fix side-by-side",
                }), new vscode.CodeLens(topRange, {
                    title: "$(check) Apply Suggested Fix",
                    command: "whack.applyFix",
                    tooltip: "Apply fix to highlighted lines (Ctrl+Z to undo)",
                }));
            }
            lenses.push(new vscode.CodeLens(topRange, {
                title: "$(comment-discussion) Ask in Chat",
                command: "whack.focusChat",
            }), new vscode.CodeLens(topRange, {
                title: "$(close) Dismiss",
                command: "whack.dismiss",
            }));
        }
        return lenses;
    }
    provideHover(document, position) {
        if (!this.activeDiagnosis)
            return undefined;
        const docPath = document.uri.fsPath.toLowerCase();
        const hit = this.resolvedHighlights.find((h) => {
            if (h.uri.fsPath.toLowerCase() !== docPath)
                return false;
            const s0 = h.startLine1 - 1;
            const e0 = h.endLine1 - 1;
            return position.line >= s0 && position.line <= e0;
        });
        if (!hit)
            return undefined;
        const md = new vscode.MarkdownString("", true);
        md.isTrusted = true;
        const confPct = Math.round((this.activeDiagnosis.confidence ?? 0.95) * 100);
        md.appendMarkdown(`### ⚡ WHACK Brain (${confPct}% confidence)\n\n` +
            `**${hit.note}**\n\n` +
            `_${this.activeDiagnosis.quip}_\n\n` +
            `[Preview Diff](command:whack.previewDiff) · ` +
            `[Apply Suggested Fix](command:whack.applyFix) · ` +
            `[Open Right-Side Chat](command:whack.focusChat) · ` +
            `[Dismiss](command:whack.dismiss)`);
        return new vscode.Hover(md);
    }
    dispose() {
        this.primaryDecoration.dispose();
        this.secondaryDecoration.dispose();
        this.appliedFlashDecoration.dispose();
        this.codeLensChange.dispose();
        for (const d of this.disposables) {
            d.dispose();
        }
    }
}
exports.WhackHighlightManager = WhackHighlightManager;
