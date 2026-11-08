"""Context Assembler strictly implementing the 6-part context ordering rule.

Assembles model input in exact order:
(1) failing output
(2) diff between last passing commit and HEAD
(3) enclosing symbol and callers of each changed function and traceback frame
(4) wiki section for only affected modules (never the whole wiki)
(5) attempt log and editor state if provided
(6) screenshot if enabled
"""

import json
import re
from typing import Dict, Any, Optional, List, Tuple
from brain.models import DiagnoseRequest
from brain.tools import git_diff
from brain.codemap import get_codemap, enclosing_symbol, find_callers
from brain.wiki import get_wiki

SYSTEM_INSTRUCTIONS = """You are Antigravity Brain, an expert pair-programming copilot.
You specialize in diagnosing why a developer is stuck.

CRITICAL INSTRUCTIONS:
1. Ground truth priority: The architecture wiki is background context and may be out of date; code and git diff are the absolute source of truth. If they conflict, trust the code and say so explicitly.
2. Start your reasoning with the diff.
3. SPATIAL & GAZE TRUST HIERARCHY:
   - Caret position (exact text insertion point) and cursor position are strong, reliable signals of what the user is actively working on.
   - Gaze is weaker and noisier — people often look away from the actual problem when confused.
   - Use gaze ONLY when it agrees with or meaningfully adds to what caret, cursor, and diff already show (e.g. gaze is on a terminal, browser, or panel that cursor/caret can't represent).
   - NEVER let gaze override a clear diff-and-error-based diagnosis.
4. Cite file path and exact 1-indexed line numbers for EVERY claim you make.
5. If evidence is ambiguous, set uncertain: true.
6. Provide a response matching the requested hintLevel:
   - 'nudge': high-level directional clue pointing at the boundary condition or concept.
   - 'hint': pointed clue identifying the exact file, function, and issue mechanism.
   - 'full_explanation': complete root-cause analysis with before/after code diff.
7. Provide a witty, supportive engineering quip.
"""

def extract_affected_files_and_lines_from_diff(diff_text: str) -> List[Tuple[str, int]]:
    """Extract (file, line) pairs from unified diff."""
    results = []
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
        elif line.startswith("@@ ") and current_file:
            match = re.search(r"\+(\d+)", line)
            if match:
                results.append((current_file, int(match.group(1))))
    return results

def extract_traceback_frames(terminal_tail: str) -> List[Tuple[str, int]]:
    """Extract (file, line) frames from traceback."""
    results = []
    # e.g. File "utils.py", line 22, in batch_slice
    matches = re.findall(r'File "([^"]+)", line (\d+)', terminal_tail)
    for f, l in matches:
        results.append((f.strip(), int(l)))
    return results

def assemble_ordered_context(
    req: DiagnoseRequest,
    use_codemap: bool = True,
    use_wiki: bool = True,
    normalized_spatial_points: Optional[Dict[str, List[int]]] = None,
    max_total_chars: int = 14000,
) -> Tuple[str, Dict[str, int]]:
    """Assemble the 6 context parts in strict order, calculating and returning part sizes."""
    part_sizes: Dict[str, int] = {}
    sections: List[str] = []

    # (1) Failing output
    fail_parts = []
    if req.terminalState:
        if req.terminalState.command:
            fail_parts.append(f"Command executed: {req.terminalState.command}")
        if req.terminalState.exitCode is not None:
            fail_parts.append(f"Exit code: {req.terminalState.exitCode}")
        if req.terminalState.tailOutput:
            fail_parts.append(f"Output / Traceback:\n```\n{req.terminalState.tailOutput.strip()}\n```")
    part1_str = "## (1) FAILING OUTPUT\n" + ("\n".join(fail_parts) if fail_parts else "None provided.")
    part_sizes["failing_output"] = len(part1_str)
    sections.append(part1_str)

    # (2) Diff between last passing commit and HEAD
    diff_res = git_diff("HEAD~1", "HEAD")
    raw_diff = diff_res.get("diff", "").strip() or "No uncommitted or commit diff found."
    part2_str = f"## (2) GIT DIFF (Last Passing Commit -> HEAD)\n```diff\n{raw_diff}\n```"
    part_sizes["git_diff"] = len(part2_str)
    sections.append(part2_str)

    # (3) Enclosing symbol and callers of each changed function and traceback frame
    part3_lines = ["## (3) CODE MAP & SYMBOL CALLERS"]
    affected_modules = set()
    if use_codemap:
        symbols_seen = set()
        # From diff
        diff_locations = extract_affected_files_and_lines_from_diff(raw_diff)
        # From traceback
        tb_locations = extract_traceback_frames(req.terminalState.tailOutput if req.terminalState else "")
        all_locs = diff_locations + tb_locations

        if req.editorState and req.editorState.activeFile and req.editorState.cursorLine:
            all_locs.append((req.editorState.activeFile, req.editorState.cursorLine))

        for file_name, line_num in all_locs:
            clean_file = file_name.split("/")[-1]
            mod_name = clean_file.replace(".py", "")
            affected_modules.add(mod_name)

            enc = enclosing_symbol(clean_file, line_num)
            if enc and enc["name"] not in symbols_seen:
                symbols_seen.add(enc["name"])
                callers = find_callers(enc["name"])
                callers_str = ", ".join([f"{c['file']}:{c['line']} in {c['caller']}" for c in callers[:4]]) or "None found"
                part3_lines.append(
                    f"- Symbol: `{enc['name']}` ({enc['type']}) in `{enc['file']}` L{enc['start_line']}-L{enc['end_line']}\n"
                    f"  Signature: `{enc.get('signature', 'N/A')}`\n"
                    f"  Callers: {callers_str}"
                )

        if len(part3_lines) == 1:
            part3_lines.append("No active function or call edges identified.")
    else:
        part3_lines.append("Code map disabled by flag.")

    part3_str = "\n".join(part3_lines)
    part_sizes["codemap"] = len(part3_str)
    sections.append(part3_str)

    # (4) Wiki section for ONLY affected modules
    part4_lines = ["## (4) ARCHITECTURE WIKI (Affected Modules Only)"]
    if use_wiki:
        if not affected_modules:
            # Fallback to utils / pipeline if none explicitly identified
            affected_modules = {"utils", "pipeline"}

        wiki_snippets = []
        for mod in sorted(affected_modules):
            sec = get_wiki(mod)
            if sec:
                wiki_snippets.append(sec)

        if wiki_snippets:
            part4_lines.extend(wiki_snippets)
        else:
            part4_lines.append("No specific module wiki section available.")
    else:
        part4_lines.append("Architecture wiki disabled by flag.")

    part4_str = "\n\n".join(part4_lines)
    part_sizes["wiki"] = len(part4_str)
    sections.append(part4_str)

    # (5) Attempt log and editor state
    part5_lines = ["## (5) TELEMETRY & ATTEMPT LOG"]
    if req.editorState:
        part5_lines.append(
            f"Editor: activeFile={req.editorState.activeFile}, line={req.editorState.cursorLine}, "
            f"diagnostics={req.editorState.diagnosticsCount}, undos={req.editorState.isUndo}"
        )
    if req.attemptLog:
        part5_lines.append(f"Attempt Log:\n{req.attemptLog}")
    if len(part5_lines) == 1:
        part5_lines.append("None provided.")
    part5_str = "\n".join(part5_lines)
    part_sizes["telemetry"] = len(part5_str)
    sections.append(part5_str)

    # (6) Screenshot and Spatial Coordinates (Gemini [y, x] 0-1000 scale)
    part6_lines = ["## (6) SCREENSHOT & SPATIAL CONTEXT"]
    if req.screenshot:
        part6_lines.append(f"Screenshot provided (payload size: {len(req.screenshot)} bytes base64).")
    else:
        part6_lines.append("Screenshot: Disabled or not captured.")

    if normalized_spatial_points:
        part6_lines.append(
            "Normalized Spatial Coordinates (Gemini convention [y, x], 0-1000 scale relative to actual image dimensions):\n"
            f"```json\n{json.dumps(normalized_spatial_points, indent=2)}\n```\n"
            "Trust hierarchy: Caret & cursor are strong signals of user edit focus. Gaze is noisy/ambient and must NEVER override code diff or error evidence."
        )
    part6_str = "\n".join(part6_lines)
    part_sizes["screenshot"] = len(part6_str)
    sections.append(part6_str)

    total_chars = sum(part_sizes.values())
    part_sizes["total"] = total_chars

    full_context = "\n\n" + "=" * 40 + "\n\n".join(sections)
    if len(full_context) > max_total_chars:
        full_context = full_context[:max_total_chars] + "\n... [Context capped]"

    return full_context, part_sizes
