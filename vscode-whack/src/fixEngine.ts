import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { DiagnosisResponse, SuggestedFix } from "./contracts";

export const DIFF_SCHEME = "whack-diff";

/**
 * Virtual read-only document provider so VS Code can open a native side-by-side
 * diff (`Current Code <-> WHACK Proposed Fix`) before the user clicks Apply Fix.
 */
export class WhackDiffContentProvider implements vscode.TextDocumentContentProvider {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<vscode.Uri>();
  public readonly onDidChange = this.onDidChangeEmitter.event;
  private readonly contents = new Map<string, string>();

  public setContent(uri: vscode.Uri, text: string): void {
    this.contents.set(uri.toString(), text);
    this.onDidChangeEmitter.fire(uri);
  }

  public provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  public dispose(): void {
    this.onDidChangeEmitter.dispose();
    this.contents.clear();
  }
}

/**
 * Resolves a file path cited by Role 3's Brain (`"utils.py"`, `"demo-repo/utils.py"`, or absolute)
 * to a concrete local file URI on disk.
 */
export async function resolveTargetFileUri(
  rawFile: string | undefined,
  extensionPath: string
): Promise<vscode.Uri | undefined> {
  if (!rawFile) {
    return vscode.window.activeTextEditor?.document.uri;
  }

  if (path.isAbsolute(rawFile) && fs.existsSync(rawFile)) {
    return vscode.Uri.file(rawFile);
  }

  const normalized = rawFile.replace(/^[/\\]+/, "");
  const candidates: string[] = [];

  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const root = folder.uri.fsPath;
    candidates.push(path.join(root, normalized));
    candidates.push(path.join(root, "demo-repo", normalized));
    candidates.push(path.join(root, "demo-repo", path.basename(normalized)));
  }

  candidates.push(path.join(os.homedir(), "whack-demo-repo", normalized));
  candidates.push(
    path.join(extensionPath, "samples", path.basename(normalized))
  );

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return vscode.Uri.file(candidate);
    }
  }

  // Check already-open editors matching by basename
  const targetBase = path.basename(normalized).toLowerCase();
  for (const doc of vscode.workspace.textDocuments) {
    if (
      doc.uri.scheme === "file" &&
      path.basename(doc.fileName).toLowerCase() === targetBase
    ) {
      return doc.uri;
    }
  }

  return undefined;
}

export interface ComputedPatch {
  uri: vscode.Uri;
  range: vscode.Range;
  newText: string;
  previewFullDocument: string;
  summaryLabel: string;
}

/**
 * Computes a clean, indentation-preserving edit from a `DiagnosisResponse`:
 * 1. Explicit `diagnosis.suggestedFix` (`find` + `replacement`, or line replacement)
 * 2. `# Before` / `# After` fenced code block pairs (matches Role 3's `brain/agent.py` output!)
 * 3. Single fenced code block in `explanation` / `summary`
 */
export async function computeSuggestedPatch(
  diagnosis: DiagnosisResponse,
  extensionPath: string
): Promise<ComputedPatch | undefined> {
  const targetFile = diagnosis.suggestedFix?.file || diagnosis.file;
  const uri = await resolveTargetFileUri(targetFile, extensionPath);
  if (!uri) {
    return undefined;
  }

  const doc = await vscode.workspace.openTextDocument(uri);
  const fullText = doc.getText();
  const rawLines = diagnosis.suggestedFix?.lines || diagnosis.lines || [1, 1];
  const startLine1 = Math.max(1, Math.min(doc.lineCount, rawLines[0]));
  const endLine1 = Math.max(startLine1, Math.min(doc.lineCount, rawLines[1]));

  // Strategy 1: Explicit `suggestedFix`
  const sf: SuggestedFix | undefined = diagnosis.suggestedFix;
  if (sf && typeof sf.replacement === "string") {
    if (sf.find) {
      // Search within cited line range first, then whole document
      for (let ln = startLine1 - 1; ln <= endLine1 - 1; ln++) {
        const lineText = doc.lineAt(ln).text;
        const idx = lineText.indexOf(sf.find);
        if (idx !== -1) {
          const range = new vscode.Range(ln, idx, ln, idx + sf.find.length);
          return buildPatchResult(doc, range, sf.replacement, `Replace \`${sf.find}\``);
        }
      }
      const eol = fullText.includes("\r\n") ? "\r\n" : "\n";
      const normalizedFind = sf.find.replace(/\r?\n/g, eol);
      const globalIdx = fullText.indexOf(normalizedFind);
      if (globalIdx !== -1) {
        const before = fullText.slice(0, globalIdx).split(/\r?\n/);
        const startLn = before.length - 1;
        const startCol = before[before.length - 1].length;
        const matchedLines = normalizedFind.split(/\r?\n/);
        const endLn = startLn + matchedLines.length - 1;
        const endCol =
          matchedLines.length === 1
            ? startCol + matchedLines[0].length
            : matchedLines[matchedLines.length - 1].length;
        const range = new vscode.Range(startLn, startCol, endLn, endCol);
        return buildPatchResult(
          doc,
          range,
          sf.replacement,
          `Update L${startLn + 1}–${endLn + 1}`
        );
      }
    }

    const lineStartPos = new vscode.Position(startLine1 - 1, 0);
    const lineEndPos = doc.lineAt(endLine1 - 1).range.end;
    return buildPatchResult(
      doc,
      new vscode.Range(lineStartPos, lineEndPos),
      sf.replacement,
      `Update L${startLine1}–${endLine1}`
    );
  }

  // Strategy 2: Parse Role 3's `# Before ... # After ...` fenced code block in `explanation`, `tierVariants`, or `summary`
  const combinedMd = `${diagnosis.explanation || ""}\n${diagnosis.tierVariants?.full_explanation?.explanation || ""}\n${diagnosis.summary || ""}`;
  const codeBlockMatch = combinedMd.match(/```(?:\w*)\r?\n([\s\S]*?)```/);
  if (!codeBlockMatch) {
    return undefined;
  }

  const blockBody = codeBlockMatch[1].trim();
  const beforeAfterMatch = blockBody.match(
    /#\s*Before[^\r\n]*\r?\n([\s\S]*?)\r?\n+#\s*After[^\r\n]*\r?\n([\s\S]*)/i
  );

  if (beforeAfterMatch) {
    const beforeLine = beforeAfterMatch[1]
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l.length > 0 && !l.startsWith("#"));
    const afterLine = beforeAfterMatch[2]
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l.length > 0 && !l.startsWith("#"));

    if (beforeLine && afterLine) {
      for (let ln = 0; ln < doc.lineCount; ln++) {
        const lineObj = doc.lineAt(ln);
        if (lineObj.text.trim() === beforeLine) {
          const indent = lineObj.text.slice(
            0,
            lineObj.firstNonWhitespaceCharacterIndex
          );
          return buildPatchResult(
            doc,
            lineObj.range,
            `${indent}${afterLine}`,
            `Replace L${ln + 1}`
          );
        }
      }
    }
  }

  // Strategy 3: General fenced code block — align indentation with startLine1
  const targetIndent = doc
    .lineAt(startLine1 - 1)
    .text.slice(0, doc.lineAt(startLine1 - 1).firstNonWhitespaceCharacterIndex);
  const rawBlockLines = blockBody.split(/\r?\n/);
  const minBlockIndent = rawBlockLines
    .filter((l) => l.trim().length > 0)
    .reduce((min, l) => {
      const lead = (l.match(/^\s*/) || [""])[0].length;
      return Math.min(min, lead);
    }, Infinity);

  const reindented = rawBlockLines
    .map((l) => {
      if (!l.trim()) return "";
      const stripped =
        minBlockIndent < Infinity ? l.slice(minBlockIndent) : l.trimStart();
      return `${targetIndent}${stripped}`;
    })
    .join("\n");

  // If single-line snippet matches a specific line header inside [startLine1..endLine1], replace just that line
  if (rawBlockLines.length === 1) {
    return buildPatchResult(
      doc,
      doc.lineAt(startLine1 - 1).range,
      reindented,
      `Update L${startLine1}`
    );
  }

  const range = new vscode.Range(
    new vscode.Position(startLine1 - 1, 0),
    doc.lineAt(endLine1 - 1).range.end
  );
  return buildPatchResult(
    doc,
    range,
    reindented,
    `Update L${startLine1}–${endLine1}`
  );
}

function buildPatchResult(
  doc: vscode.TextDocument,
  range: vscode.Range,
  newText: string,
  summaryLabel: string
): ComputedPatch {
  const full = doc.getText();
  const lines = full.split(/\r?\n/);
  const eol = full.includes("\r\n") ? "\r\n" : "\n";

  // Compute character offsets for previewFullDocument
  let startOffset = 0;
  for (let i = 0; i < range.start.line; i++) {
    startOffset += lines[i].length + eol.length;
  }
  startOffset += range.start.character;

  let endOffset = 0;
  for (let i = 0; i < range.end.line; i++) {
    endOffset += lines[i].length + eol.length;
  }
  endOffset += range.end.character;

  const previewFullDocument =
    full.slice(0, startOffset) + newText + full.slice(endOffset);

  return {
    uri: doc.uri,
    range,
    newText,
    previewFullDocument,
    summaryLabel,
  };
}
