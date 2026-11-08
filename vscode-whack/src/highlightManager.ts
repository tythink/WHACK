import * as path from "path";
import * as vscode from "vscode";
import { CodeCitation, DiagnosisResponse } from "./contracts";
import { resolveTargetFileUri } from "./fixEngine";

interface ResolvedHighlight {
  uri: vscode.Uri;
  startLine1: number;
  endLine1: number;
  isPrimary: boolean;
  note: string;
  snippet?: string;
}

/**
 * Manages real-time VS Code Editor line highlights, inline ghost annotations,
 * hover cards, and inline CodeLens actions (`Preview Diff | Apply Fix | Ask in Chat | Dismiss`).
 */
export class WhackHighlightManager
  implements vscode.CodeLensProvider, vscode.HoverProvider, vscode.Disposable
{
  private readonly primaryDecoration: vscode.TextEditorDecorationType;
  private readonly secondaryDecoration: vscode.TextEditorDecorationType;
  private readonly appliedFlashDecoration: vscode.TextEditorDecorationType;
  private readonly codeLensChange = new vscode.EventEmitter<void>();
  public readonly onDidChangeCodeLenses = this.codeLensChange.event;

  private activeDiagnosis: DiagnosisResponse | null = null;
  private resolvedHighlights: ResolvedHighlight[] = [];
  private hasApplicableFix = false;
  private flashTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly extensionPath: string) {
    const primarySvg = encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">` +
        `<circle cx="8" cy="8" r="5.5" fill="#f25533" stroke="#ffffff" stroke-width="1.2"/></svg>`
    );
    const secondarySvg = encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">` +
        `<circle cx="8" cy="8" r="4.5" fill="#d97706"/></svg>`
    );

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
      gutterIconPath: vscode.Uri.parse(
        `data:image/svg+xml;utf8,${secondarySvg}`
      ),
      gutterIconSize: "75%",
    });

    this.appliedFlashDecoration = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      backgroundColor: "rgba(15, 157, 118, 0.24)",
      borderWidth: "0 0 0 3px",
      borderStyle: "solid",
      borderColor: "#0f9d76",
    });

    this.disposables.push(
      vscode.window.onDidChangeVisibleTextEditors(() => {
        this.refreshVisibleEditors();
      }),
      vscode.languages.registerCodeLensProvider(
        { scheme: "file" },
        this
      ),
      vscode.languages.registerHoverProvider({ scheme: "file" }, this)
    );
  }

  /**
   * Opens the primary cited file in ViewColumn.One (left side), scrolls to the target lines,
   * and highlights both the primary range and any additional `citations[]`.
   */
  public async showDiagnosisHighlights(
    diagnosis: DiagnosisResponse,
    hasFix: boolean
  ): Promise<{ primaryUri?: vscode.Uri; primaryLines?: [number, number] }> {
    this.activeDiagnosis = diagnosis;
    this.hasApplicableFix = hasFix;
    this.resolvedHighlights = [];

    // 1. Resolve primary target range
    const primaryUri = await resolveTargetFileUri(
      diagnosis.file || diagnosis.citations?.[0]?.file,
      this.extensionPath
    );
    const primaryLines: [number, number] =
      diagnosis.lines || diagnosis.citations?.[0]?.lines || [1, 1];

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
      const citUri = await resolveTargetFileUri(cit.file, this.extensionPath);
      if (!citUri) continue;
      const isDuplicatePrimary =
        primaryUri &&
        citUri.fsPath.toLowerCase() === primaryUri.fsPath.toLowerCase() &&
        cit.lines[0] === primaryLines[0] &&
        cit.lines[1] === primaryLines[1];
      if (isDuplicatePrimary) continue;

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
      const s0 = Math.max(0, Math.min(doc.lineCount - 1, primaryLines[0] - 1));
      const e0 = Math.max(s0, Math.min(doc.lineCount - 1, primaryLines[1] - 1));
      const revealRange = new vscode.Range(
        new vscode.Position(s0, 0),
        doc.lineAt(e0).range.end
      );

      const editor = await vscode.window.showTextDocument(doc, {
        viewColumn: vscode.ViewColumn.One,
        preserveFocus: false,
        preview: false,
      });
      editor.revealRange(revealRange, vscode.TextEditorRevealType.InCenter);
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
  public async jumpToCitation(citation: CodeCitation): Promise<void> {
    const uri = await resolveTargetFileUri(citation.file, this.extensionPath);
    if (!uri) return;

    const doc = await vscode.workspace.openTextDocument(uri);
    const s0 = Math.max(0, Math.min(doc.lineCount - 1, citation.lines[0] - 1));
    const e0 = Math.max(s0, Math.min(doc.lineCount - 1, citation.lines[1] - 1));
    const range = new vscode.Range(
      new vscode.Position(s0, 0),
      doc.lineAt(e0).range.end
    );

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
  public async flashAppliedFix(
    uri: vscode.Uri,
    range: vscode.Range
  ): Promise<void> {
    this.clearHighlights();
    const doc = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      preserveFocus: false,
    });

    const endLine = Math.min(doc.lineCount - 1, range.end.line);
    const fullLineRange = new vscode.Range(
      new vscode.Position(range.start.line, 0),
      doc.lineAt(endLine).range.end
    );

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

  public clearHighlights(): void {
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

  public getHighlightedSnippet(): {
    file?: string;
    lines?: [number, number];
    snippet?: string;
  } {
    const primary = this.resolvedHighlights.find((h) => h.isPrimary);
    if (!primary) return {};
    const openDoc = vscode.workspace.textDocuments.find(
      (d) => d.uri.fsPath.toLowerCase() === primary.uri.fsPath.toLowerCase()
    );
    if (!openDoc) {
      return {
        file: path.basename(primary.uri.fsPath),
        lines: [primary.startLine1, primary.endLine1],
      };
    }
    const s0 = Math.max(
      0,
      Math.min(openDoc.lineCount - 1, primary.startLine1 - 1)
    );
    const e0 = Math.max(
      s0,
      Math.min(openDoc.lineCount - 1, primary.endLine1 - 1)
    );
    const range = new vscode.Range(
      new vscode.Position(s0, 0),
      openDoc.lineAt(e0).range.end
    );
    return {
      file: path.basename(primary.uri.fsPath),
      lines: [primary.startLine1, primary.endLine1],
      snippet: openDoc.getText(range),
    };
  }

  private refreshVisibleEditors(): void {
    for (const editor of vscode.window.visibleTextEditors) {
      const editorPath = editor.document.uri.fsPath.toLowerCase();
      const matching = this.resolvedHighlights.filter(
        (h) => h.uri.fsPath.toLowerCase() === editorPath
      );

      if (matching.length === 0) {
        editor.setDecorations(this.primaryDecoration, []);
        editor.setDecorations(this.secondaryDecoration, []);
        continue;
      }

      const primaryOpts: vscode.DecorationOptions[] = [];
      const secondaryOpts: vscode.DecorationOptions[] = [];
      const doc = editor.document;

      for (const item of matching) {
        const s0 = Math.max(0, Math.min(doc.lineCount - 1, item.startLine1 - 1));
        const e0 = Math.max(s0, Math.min(doc.lineCount - 1, item.endLine1 - 1));
        const range = new vscode.Range(
          new vscode.Position(s0, 0),
          doc.lineAt(e0).range.end
        );

        const shortLabel =
          item.note.length > 68 ? `${item.note.slice(0, 65)}...` : item.note;

        // Place inline ghost note ONLY on the first line of the range so multi-line blocks stay clean
        const firstLineRange = doc.lineAt(s0).range;
        const blockOpt: vscode.DecorationOptions = { range };
        const labelOpt: vscode.DecorationOptions = {
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
        } else {
          secondaryOpts.push(blockOpt, labelOpt);
        }
      }

      editor.setDecorations(this.primaryDecoration, primaryOpts);
      editor.setDecorations(this.secondaryDecoration, secondaryOpts);
    }
  }

  public provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    if (!this.activeDiagnosis) return [];
    const docPath = document.uri.fsPath.toLowerCase();
    const lenses: vscode.CodeLens[] = [];

    for (const h of this.resolvedHighlights) {
      if (h.uri.fsPath.toLowerCase() !== docPath) continue;
      const line0 = Math.max(
        0,
        Math.min(document.lineCount - 1, h.startLine1 - 1)
      );
      const topRange = new vscode.Range(line0, 0, line0, 0);
      const confPct = Math.round((this.activeDiagnosis.confidence ?? 0.95) * 100);

      lenses.push(
        new vscode.CodeLens(topRange, {
          title: h.isPrimary
            ? `⚡ WHACK Diagnosis (${confPct}%)`
            : `⚡ WHACK Related Citation`,
          command: "whack.togglePanel",
          tooltip: h.note,
        })
      );

      if (h.isPrimary && this.hasApplicableFix) {
        lenses.push(
          new vscode.CodeLens(topRange, {
            title: "$(diff) Preview Diff",
            command: "whack.previewDiff",
            tooltip: "Compare current file with WHACK's proposed fix side-by-side",
          }),
          new vscode.CodeLens(topRange, {
            title: "$(check) Apply Suggested Fix",
            command: "whack.applyFix",
            tooltip: "Apply fix to highlighted lines (Ctrl+Z to undo)",
          })
        );
      }

      lenses.push(
        new vscode.CodeLens(topRange, {
          title: "$(comment-discussion) Ask in Chat",
          command: "whack.focusChat",
        }),
        new vscode.CodeLens(topRange, {
          title: "$(close) Dismiss",
          command: "whack.dismiss",
        })
      );
    }

    return lenses;
  }

  public provideHover(
    document: vscode.TextDocument,
    position: vscode.Position
  ): vscode.Hover | undefined {
    if (!this.activeDiagnosis) return undefined;
    const docPath = document.uri.fsPath.toLowerCase();
    const hit = this.resolvedHighlights.find((h) => {
      if (h.uri.fsPath.toLowerCase() !== docPath) return false;
      const s0 = h.startLine1 - 1;
      const e0 = h.endLine1 - 1;
      return position.line >= s0 && position.line <= e0;
    });

    if (!hit) return undefined;

    const md = new vscode.MarkdownString("", true);
    md.isTrusted = true;
    const confPct = Math.round((this.activeDiagnosis.confidence ?? 0.95) * 100);
    md.appendMarkdown(
      `### ⚡ WHACK Brain (${confPct}% confidence)\n\n` +
        `**${hit.note}**\n\n` +
        `_${this.activeDiagnosis.quip}_\n\n` +
        `[Preview Diff](command:whack.previewDiff) · ` +
        `[Apply Suggested Fix](command:whack.applyFix) · ` +
        `[Open Right-Side Chat](command:whack.focusChat) · ` +
        `[Dismiss](command:whack.dismiss)`
    );
    return new vscode.Hover(md);
  }

  public dispose(): void {
    this.primaryDecoration.dispose();
    this.secondaryDecoration.dispose();
    this.appliedFlashDecoration.dispose();
    this.codeLensChange.dispose();
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}
