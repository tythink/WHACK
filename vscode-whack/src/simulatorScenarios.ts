import * as path from "path";
import * as vscode from "vscode";
import { DiagnosisResponse } from "./contracts";

export type ScenarioId = "sliding_window" | "active_editor" | "uncertain_probe";

/**
 * Built-in Mock Brain scenarios so you can test passive -> active triggering,
 * multi-range line highlighting, side-by-side diff preview, 1-click fix, and
 * right-side chat BEFORE Role 3's Brain LLM server is finished.
 */
export function buildSimulatedDiagnosis(
  scenario: ScenarioId,
  extensionPath: string
): DiagnosisResponse {
  if (scenario === "active_editor") {
    const editor = vscode.window.activeTextEditor;
    if (
      editor &&
      editor.document.uri.scheme === "file" &&
      editor.document.lineCount >= 2
    ) {
      const doc = editor.document;
      const cursorZero = editor.selection.active.line;
      const startLine = Math.max(1, cursorZero); // 1-indexed
      const endLine = Math.min(doc.lineCount, startLine + 3);
      const targetLineObj = doc.lineAt(startLine - 1);
      const indent = targetLineObj.text.slice(
        0,
        targetLineObj.firstNonWhitespaceCharacterIndex
      );
      const origLine = targetLineObj.text;
      const baseName = path.basename(doc.fileName);
      const commentPrefix =
        doc.languageId === "python" || doc.languageId === "shellscript"
          ? "#"
          : "//";

      // Secondary citation slightly further down if file is long enough
      const secStart = Math.min(doc.lineCount, endLine + 3);
      const secEnd = Math.min(doc.lineCount, secStart + 1);

      const citations = [
        {
          file: doc.fileName,
          lines: [startLine, endLine] as [number, number],
          snippet: origLine.trim() || "(selected line)",
          note: "Primary focus region near your caret where execution stalled",
        },
      ];
      if (secStart > endLine) {
        citations.push({
          file: doc.fileName,
          lines: [secStart, secEnd] as [number, number],
          snippet: doc.lineAt(secStart - 1).text.trim(),
          note: "Downstream call site / state dependency to verify",
        });
      }

      return {
        summary: `Potential logic / boundary check needed in \`${baseName}\` around lines ${startLine}–${endLine}.`,
        file: doc.fileName,
        lines: [startLine, endLine],
        quip: "Staring hard at a line doesn't scare the bug out—let's check its invariants.",
        uncertain: false,
        confidence: 0.91,
        hintLevel: "hint",
        reasons: [
          `caret dwell in ${baseName}:${startLine}`,
          "simulated Brain trigger on active file",
        ],
        explanation:
          `### Active Editor Inspection (\`${baseName}:${startLine}-${endLine}\`)\n` +
          `WHACK highlighted your active code region starting at line **${startLine}**.\n\n` +
          `**guardrail annotation preview:**\n` +
          `\`\`\`${doc.languageId}\n` +
          `${indent}${commentPrefix} TODO(whack-verified): double-check boundary & null invariants here\n` +
          `${origLine}\n` +
          `\`\`\``,
        citations,
        suggestedFix: {
          file: doc.fileName,
          lines: [startLine, startLine],
          replacement: `${indent}${commentPrefix} TODO(whack-verified): double-check boundary & null invariants here\n${origLine}`,
        },
        tierVariants: {
          nudge: {
            summary: `Pause and trace the inputs entering \`${baseName}\` at line ${startLine}—what happens on empty or boundary values?`,
          },
          hint: {
            summary: `Boundary / invariant check flagged in \`${baseName}\` (lines ${startLine}–${endLine}).`,
            explanation: `Inspect how line ${startLine} interacts with lines ${secStart}–${secEnd} when inputs hit edge conditions.`,
          },
          full_explanation: {
            summary: `Full inspection for \`${baseName}\` (lines ${startLine}–${endLine}).`,
            explanation:
              `WHACK pinpointed lines **${startLine}–${endLine}** as the primary edit target.\n\n` +
              `Click **Preview Diff** to inspect the proposed guardrail edit side-by-side or ask a question in the chatbox below.`,
          },
        },
      };
    }
  }

  const sampleUtilsPath = path.join(extensionPath, "samples", "utils.py");

  if (scenario === "uncertain_probe") {
    return {
      summary:
        "Possible state or slice mismatch in `utils.py`, though no recent stack trace confirmed the exact caller.",
      file: sampleUtilsPath,
      lines: [36, 48],
      quip: "I'm 62% sure it's index rounding, and 38% sure we need one quick `pytest` run.",
      uncertain: true,
      confidence: 0.62,
      hintLevel: "nudge",
      reasons: [
        "sustained brow furrow (0.76)",
        "attention split across 2 functions",
      ],
      explanation:
        "Check `compute_percentiles` interpolation indices `f` and `c` when `percentiles` includes `0` or `100`, or run `pytest` so Brain can lock onto the traceback.",
      citations: [
        {
          file: sampleUtilsPath,
          lines: [42, 47],
          snippet: "k = (len(sorted_vals) - 1) * (p / 100.0)",
          note: "Verify percentile boundary behavior when len(sorted_vals) == 1",
        },
      ],
      tierVariants: {
        nudge: {
          summary:
            "What does `compute_percentiles` return when `values` has only a single element `[42.0]`?",
        },
        hint: {
          summary:
            "Ambiguous fault without terminal output—check `compute_percentiles` (L42–47) vs `calculate_window_offsets` (L18–21).",
        },
        full_explanation: {
          summary:
            "Low-confidence diagnosis (`Uncertain` badge active): run `pytest` or ask in chat to narrow down the exact failing input.",
        },
      },
    };
  }

  // Default Scenario ("sliding_window"): Matches Role 3's planted demo-repo bug with TWO cited locations + 1-click fix!
  return {
    summary:
      "Off-by-one loop bound in `calculate_window_offsets` (`+ 2` instead of `+ 1`) produces an out-of-bounds slice in `batch_slice`.",
    file: sampleUtilsPath,
    lines: [18, 21],
    quip: "Off-by-one errors: the two hardest problems in computer science.",
    uncertain: false,
    confidence: 0.98,
    hintLevel: "hint",
    reasons: [
      "pytest failed 3x: IndexError Offset end 6 exceeds length 5",
      "furrowed brow (0.87)",
      "caret frozen near line 18",
    ],
    explanation:
      `### Root Cause (\`utils.py:18\`)\n` +
      `\`range(0, total_items - window_size + 2)\` iterates one step too far. On the final iteration, \`i = total_items - window_size + 1\`, making \`end = total_items + 1\`, which triggers \`IndexError\` inside \`batch_slice\` at line 30.\n\n` +
      "```python\n" +
      "# Before (Buggy):\n" +
      "for i in range(0, total_items - window_size + 2):\n\n" +
      "# After (Fix):\n" +
      "for i in range(0, total_items - window_size + 1):\n" +
      "```",
    citations: [
      {
        file: sampleUtilsPath,
        lines: [18, 21],
        snippet: "for i in range(0, total_items - window_size + 2):",
        note: "Primary fix: change `+ 2` -> `+ 1` so final window stops at `total_items`",
      },
      {
        file: sampleUtilsPath,
        lines: [29, 30],
        snippet: 'raise IndexError(f"Offset end {end} exceeds data length {len(data)}")',
        note: "Symptom site: raises IndexError when `calculate_window_offsets` emits `end == len(data) + 1`",
      },
    ],
    suggestedFix: {
      file: sampleUtilsPath,
      lines: [18, 21],
      find: "total_items - window_size + 2",
      replacement: "total_items - window_size + 1",
    },
    tierVariants: {
      nudge: {
        summary:
          "Trace `calculate_window_offsets(total_items=5, window_size=3)` on line 18—what is the value of `end` on the very last iteration?",
      },
      hint: {
        summary:
          "Off-by-one error on `utils.py` line 18: `range(0, total_items - window_size + 2)` generates one extra window past `total_items`.",
        explanation:
          "That extra `(start, end)` tuple flows into `batch_slice` (lines 29–30) where `end > len(data)` raises `IndexError`.",
      },
      full_explanation: {
        summary:
          "Off-by-one bug in `utils.py:18` causes `IndexError` in `batch_slice` (`utils.py:30`).",
        explanation:
          `Change the upper bound in \`calculate_window_offsets\` from \`+ 2\` to \`+ 1\`:\n\n` +
          "```python\n" +
          "for i in range(0, total_items - window_size + 1):\n" +
          "    start = i\n" +
          "    end = i + window_size\n" +
          "    offsets.append((start, end))\n" +
          "```",
      },
    },
  };
}
