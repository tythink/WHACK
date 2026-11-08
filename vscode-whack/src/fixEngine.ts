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

function isExistingFile(filePath: string): boolean {
  try {
    return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

/**
 * Resolves a file path cited by Role 3's Brain against:
 *   1. `repoPath` (if provided and exists on disk),
 *   2. Open `vscode.workspace.workspaceFolders`,
 *   3. Active / open editor document path match,
 *   4. Fallback `~/whack-demo-repo` ONLY if it exists on disk.
 * Never falls back to dummy `vscode-whack/samples/utils.py`.
 */
export async function resolveTargetFileUri(
  rawFile: string | undefined,
  repoPath?: string
): Promise<vscode.Uri | undefined> {
  if (!rawFile || !rawFile.trim()) {
    return undefined;
  }

  const trimmed = rawFile.trim();
  if (path.isAbsolute(trimmed) && isExistingFile(trimmed)) {
    return vscode.Uri.file(trimmed);
  }

  const normalized = trimmed.replace(/^[/\\]+/, "");
  const normSlashes = normalized.replace(/\\/g, "/").toLowerCase();

  // 1. `diagnosis.repoPath` (if provided and exists on disk)
  if (repoPath && repoPath.trim() && fs.existsSync(repoPath)) {
    const directInRepo = path.join(repoPath, normalized);
    if (isExistingFile(directInRepo)) {
      return vscode.Uri.file(directInRepo);
    }
    const repoBase = path.basename(repoPath).toLowerCase();
    if (normSlashes.startsWith(`${repoBase}/`)) {
      const strippedInRepo = path.join(
        repoPath,
        normalized.slice(repoBase.length + 1)
      );
      if (isExistingFile(strippedInRepo)) {
        return vscode.Uri.file(strippedInRepo);
      }
    }
  }

  // 2. Open `vscode.workspace.workspaceFolders`
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const root = folder.uri.fsPath;
    const directCandidate = path.join(root, normalized);
    if (isExistingFile(directCandidate)) {
      return vscode.Uri.file(directCandidate);
    }

    const folderBase = folder.name.toLowerCase();
    if (normSlashes.startsWith(`${folderBase}/`)) {
      const strippedCandidate = path.join(
        root,
        normalized.slice(folder.name.length + 1)
      );
      if (isExistingFile(strippedCandidate)) {
        return vscode.Uri.file(strippedCandidate);
      }
    }

    const nestedDemoCandidate = path.join(root, "demo-repo", normalized);
    if (isExistingFile(nestedDemoCandidate)) {
      return vscode.Uri.file(nestedDemoCandidate);
    }
  }

  // 3. Active editor & open workspace text documents path match
  const candidateDocs: vscode.TextDocument[] = [];
  if (vscode.window.activeTextEditor?.document.uri.scheme === "file") {
    candidateDocs.push(vscode.window.activeTextEditor.document);
  }
  for (const vis of vscode.window.visibleTextEditors) {
    if (
      vis.document.uri.scheme === "file" &&
      !candidateDocs.includes(vis.document)
    ) {
      candidateDocs.push(vis.document);
    }
  }
  for (const doc of vscode.workspace.textDocuments) {
    if (doc.uri.scheme === "file" && !candidateDocs.includes(doc)) {
      candidateDocs.push(doc);
    }
  }

  for (const doc of candidateDocs) {
    const docSlashes = doc.uri.fsPath.replace(/\\/g, "/").toLowerCase();
    if (
      (docSlashes === normSlashes || docSlashes.endsWith(`/${normSlashes}`)) &&
      isExistingFile(doc.uri.fsPath)
    ) {
      return doc.uri;
    }
  }

  if (!normSlashes.includes("/")) {
    for (const doc of candidateDocs) {
      if (
        path.basename(doc.uri.fsPath).toLowerCase() === normSlashes &&
        isExistingFile(doc.uri.fsPath)
      ) {
        return doc.uri;
      }
    }
  }

  // 4. Fallback `~/whack-demo-repo` ONLY if it exists on disk
  const homeDemoRepo = path.join(os.homedir(), "whack-demo-repo");
  if (fs.existsSync(homeDemoRepo)) {
    const demoCandidate = path.join(homeDemoRepo, normalized);
    if (isExistingFile(demoCandidate)) {
      return vscode.Uri.file(demoCandidate);
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

function splitCleanLines(text: string): string[] {
  const normalized = text.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  if (lines.length > 1 && lines[0] === "") {
    lines.shift();
  }
  if (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines;
}

function matchAndReindentLineBlock(
  doc: vscode.TextDocument,
  oldBlock: string,
  newBlock: string,
  preferredStart1: number,
  preferredEnd1: number,
  maxRadius?: number
): { range: vscode.Range; replacement: string; startLine1: number; endLine1: number } | undefined {
  const oldLines = splitCleanLines(oldBlock);
  const newLines = splitCleanLines(newBlock);
  if (oldLines.length === 0 || !oldLines.some((l) => l.trim().length > 0)) {
    return undefined;
  }
  if (oldLines.length > doc.lineCount) {
    return undefined;
  }

  const eol = doc.getText().includes("\r\n") ? "\r\n" : "\n";
  const maxStart0 = doc.lineCount - oldLines.length;

  const winStart0 =
    typeof maxRadius === "number"
      ? Math.max(0, preferredStart1 - 1 - maxRadius)
      : 0;
  const winEnd0 =
    typeof maxRadius === "number"
      ? Math.min(maxStart0, preferredEnd1 - 1 + maxRadius)
      : maxStart0;

  let bestStart0 = -1;
  let bestDist = Infinity;

  for (let s0 = winStart0; s0 <= winEnd0; s0++) {
    let matches = true;
    for (let k = 0; k < oldLines.length; k++) {
      if (doc.lineAt(s0 + k).text.trim() !== oldLines[k].trim()) {
        matches = false;
        break;
      }
    }
    if (matches) {
      const dist = Math.abs(s0 - (preferredStart1 - 1));
      if (dist < bestDist) {
        bestDist = dist;
        bestStart0 = s0;
      }
    }
  }

  if (bestStart0 === -1) {
    return undefined;
  }

  const firstNonEmptyIdx = oldLines.findIndex((l) => l.trim().length > 0);
  const docRefLine = doc.lineAt(bestStart0 + Math.max(0, firstNonEmptyIdx));
  const docBaseIndent = docRefLine.text.slice(
    0,
    docRefLine.firstNonWhitespaceCharacterIndex
  );

  const oldMinIndent = oldLines
    .filter((l) => l.trim().length > 0)
    .reduce((min, l) => {
      const lead = (l.match(/^\s*/) || [""])[0].length;
      return Math.min(min, lead);
    }, Infinity);

  const newMinIndent = newLines
    .filter((l) => l.trim().length > 0)
    .reduce((min, l) => {
      const lead = (l.match(/^\s*/) || [""])[0].length;
      return Math.min(min, lead);
    }, Infinity);

  const alreadyHasDocIndent =
    oldMinIndent === 0 &&
    docBaseIndent.length > 0 &&
    newMinIndent >= docBaseIndent.length;

  const adjustedNewLines = newLines.map((line) => {
    if (!line.trim()) return "";
    if (alreadyHasDocIndent) return line;
    const leadLen = (line.match(/^\s*/) || [""])[0].length;
    const stripCount =
      oldMinIndent < Infinity ? Math.min(oldMinIndent, leadLen) : leadLen;
    return `${docBaseIndent}${line.slice(stripCount)}`;
  });

  const end0 = bestStart0 + oldLines.length - 1;
  const range = new vscode.Range(
    new vscode.Position(bestStart0, 0),
    doc.lineAt(end0).range.end
  );

  return {
    range,
    replacement: adjustedNewLines.join(eol),
    startLine1: bestStart0 + 1,
    endLine1: end0 + 1,
  };
}

/**
 * Computes a verified, indentation-preserving edit from a `DiagnosisResponse`:
 *   Strategy 1: Explicit `diagnosis.suggestedFix` (exact match + indentation-normalized line-block match)
 *   Strategy 2: `# Before` / `# After` explicit diff blocks verified against the target lines
 * Never replaces user files with arbitrary unverified prose code blocks.
 */
export async function computeSuggestedPatch(
  diagnosis: DiagnosisResponse
): Promise<ComputedPatch | undefined> {
  if (diagnosis.source === "offline") {
    return undefined;
  }

  const targetFile = diagnosis.suggestedFix?.file || diagnosis.file;
  const uri = await resolveTargetFileUri(targetFile, diagnosis.repoPath);
  if (!uri) {
    return undefined;
  }

  const doc = await vscode.workspace.openTextDocument(uri);
  if (doc.lineCount === 0) {
    return undefined;
  }

  const fullText = doc.getText();
  const eol = fullText.includes("\r\n") ? "\r\n" : "\n";
  const sf: SuggestedFix | null | undefined = diagnosis.suggestedFix;
  const rawLines =
    sf?.lines ||
    (typeof sf?.startLine === "number"
      ? [sf.startLine, sf.endLine ?? sf.startLine]
      : undefined) ||
    diagnosis.lines || [1, 1];
  const startLine1 = Math.max(1, Math.min(doc.lineCount, rawLines[0]));
  const endLine1 = Math.max(startLine1, Math.min(doc.lineCount, rawLines[1]));

  // Strategy 1: Explicit `suggestedFix` (`StructuredEdit`)
  if (sf) {
    const newStr =
      typeof sf.newText === "string"
        ? sf.newText
        : typeof sf.replacement === "string"
        ? sf.replacement
        : undefined;
    const oldStr =
      typeof sf.oldText === "string"
        ? sf.oldText
        : typeof sf.find === "string"
        ? sf.find
        : undefined;

    if (typeof newStr === "string") {
      if (oldStr && oldStr.length > 0) {
        const normalizedNew = newStr.replace(/\r?\n/g, eol);

        // 1a. Single-line exact substring search within cited lines first, then nearby window
        if (!oldStr.includes("\n") && !oldStr.includes("\r")) {
          const searchOrder: number[] = [];
          for (let ln = startLine1 - 1; ln <= endLine1 - 1; ln++) {
            searchOrder.push(ln);
          }
          for (
            let ln = Math.max(0, startLine1 - 5);
            ln <= Math.min(doc.lineCount - 1, endLine1 + 4);
            ln++
          ) {
            if (!searchOrder.includes(ln)) {
              searchOrder.push(ln);
            }
          }
          for (const ln of searchOrder) {
            const lineText = doc.lineAt(ln).text;
            const idx = lineText.indexOf(oldStr);
            if (idx !== -1) {
              const range = new vscode.Range(ln, idx, ln, idx + oldStr.length);
              return buildPatchResult(
                doc,
                range,
                normalizedNew,
                `Update L${ln + 1}`
              );
            }
          }
        }

        // 1b. Exact multi-line substring match in document (closest to `startLine1`)
        const normalizedOld = oldStr.replace(/\r?\n/g, eol);
        let searchFrom = 0;
        let bestRange: vscode.Range | undefined;
        let bestDist = Infinity;
        while (searchFrom <= fullText.length) {
          const hitIdx = fullText.indexOf(normalizedOld, searchFrom);
          if (hitIdx === -1) break;

          const before = fullText.slice(0, hitIdx).split(/\r?\n/);
          const startLn = before.length - 1;
          const startCol = before[before.length - 1].length;
          const matchedLines = normalizedOld.split(/\r?\n/);
          const endLn = startLn + matchedLines.length - 1;
          const endCol =
            matchedLines.length === 1
              ? startCol + matchedLines[0].length
              : matchedLines[matchedLines.length - 1].length;

          const dist = Math.abs(startLn - (startLine1 - 1));
          if (dist < bestDist) {
            bestDist = dist;
            bestRange = new vscode.Range(startLn, startCol, endLn, endCol);
          }
          searchFrom = hitIdx + Math.max(1, normalizedOld.length);
        }

        if (bestRange) {
          return buildPatchResult(
            doc,
            bestRange,
            normalizedNew,
            `Update L${bestRange.start.line + 1}–${bestRange.end.line + 1}`
          );
        }

        // 1c. Indentation-normalized line-block match (near target lines first, then full file)
        const nearMatch =
          matchAndReindentLineBlock(doc, oldStr, newStr, startLine1, endLine1, 8) ||
          matchAndReindentLineBlock(doc, oldStr, newStr, startLine1, endLine1);
        if (nearMatch) {
          return buildPatchResult(
            doc,
            nearMatch.range,
            nearMatch.replacement,
            `Update L${nearMatch.startLine1}–${nearMatch.endLine1}`
          );
        }
      } else if (sf.lines || typeof sf.startLine === "number") {
        // Explicit line-range replacement without `oldText`
        const lineStartPos = new vscode.Position(startLine1 - 1, 0);
        const lineEndPos = doc.lineAt(endLine1 - 1).range.end;
        return buildPatchResult(
          doc,
          new vscode.Range(lineStartPos, lineEndPos),
          newStr.replace(/\r?\n/g, eol),
          `Update L${startLine1}–${endLine1}`
        );
      }
    }
  }

  // Strategy 2: Verified `# Before ... # After ...` diff blocks in explanation / summary
  const combinedMd = `${diagnosis.explanation || ""}\n${
    diagnosis.tierVariants?.full_explanation?.explanation || ""
  }\n${diagnosis.summary || ""}`;

  const blockRegex = /```(?:\w*)\r?\n([\s\S]*?)```/g;
  let match: RegExpExecArray | null;
  while ((match = blockRegex.exec(combinedMd)) !== null) {
    const blockBody = match[1].trim();
    const beforeAfterMatch = blockBody.match(
      /(?:#|\/\/)\s*Before[^\r\n]*\r?\n([\s\S]*?)\r?\n+(?:#|\/\/)\s*After[^\r\n]*\r?\n([\s\S]*)/i
    );
    if (!beforeAfterMatch) continue;

    const beforeLines = beforeAfterMatch[1]
      .split(/\r?\n/)
      .filter(
        (l) =>
          l.trim().length > 0 &&
          !/^\s*(?:#|\/\/)\s*(?:Before|After)/i.test(l)
      );
    const afterLines = beforeAfterMatch[2]
      .split(/\r?\n/)
      .filter(
        (l) =>
          l.trim().length > 0 &&
          !/^\s*(?:#|\/\/)\s*(?:Before|After)/i.test(l)
      );

    if (beforeLines.length === 0 || afterLines.length === 0) continue;

    const verified = matchAndReindentLineBlock(
      doc,
      beforeLines.join("\n"),
      afterLines.join("\n"),
      startLine1,
      endLine1,
      12
    );
    if (verified) {
      return buildPatchResult(
        doc,
        verified.range,
        verified.replacement,
        `Replace L${verified.startLine1}–${verified.endLine1}`
      );
    }
  }

  return undefined;
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
