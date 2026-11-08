"""Context Assembler strictly implementing the 6-part context ordering rule.

Assembles model input in exact order:
(1) failing output & IDE diagnostics
(2) uncommitted working-tree diff + recent commit diff
(3) enclosing symbol, callers, and active cursor window
(4) wiki section for affected modules (or concise workspace overview)
(5) live CV emotions, fusion status/reasons, attempt log, and editor state
(6) screenshot & normalized spatial coordinates if enabled
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from brain.config import resolve_repo_dir
from brain.models import DiagnoseRequest, AttemptEvent
from brain.tools import get_working_diff, read_file_range
from brain.codemap import get_code_map, enclosing_symbol, find_callers
from brain.persona import SYSTEM_PROMPT
from brain.extractor import normalize_repo_file_path

# Re-export for callers importing SYSTEM_INSTRUCTIONS from brain.context
SYSTEM_INSTRUCTIONS = SYSTEM_PROMPT


def is_code_comprehension_request(req: DiagnoseRequest) -> bool:
    """Determine whether request is for code comprehension assistance rather than error diagnosis."""
    st = (req.userStatus or "").lower()
    if (
        "code_comprehension_confusion" in st
        or "confusion_code_comprehension" in st
        or "confused_comprehension" in st
        or "comprehension" in st
    ):
        return True
    if "error_confusion" in st or "confusion_error" in st:
        return False

    has_active_errors = (
        bool(req.editorState and (req.editorState.diagnosticsCount or 0) > 0)
        or bool(
            req.editorState
            and req.editorState.diagnosticsSummary
            and req.editorState.diagnosticsSummary.strip()
        )
        or bool(
            req.terminalState
            and req.terminalState.exitCode is not None
            and req.terminalState.exitCode != 0
        )
    )
    if has_active_errors:
        return False

    is_confused = (
        "confus" in st
        or (isinstance(req.emotions, dict) and float(req.emotions.get("confused", 0) or 0) >= 0.45)
        or any("confus" in str(r).lower() for r in (req.reasons or []))
    )
    return is_confused


def extract_affected_files_and_lines_from_diff(diff_text: str) -> List[Tuple[str, int]]:
    """Extract (file, line) pairs from unified diff."""
    results: List[Tuple[str, int]] = []
    current_file: Optional[str] = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
        elif line.startswith("@@ ") and current_file:
            match = re.search(r"\+(\d+)", line)
            if match:
                results.append((current_file, int(match.group(1))))
    return results


def extract_traceback_frames(terminal_tail: str) -> List[Tuple[str, int]]:
    """Extract (file, line) frames from Python/JS/TS tracebacks."""
    if not terminal_tail:
        return []
    results: List[Tuple[str, int]] = []
    # Python: File "utils.py", line 22
    for f, l in re.findall(r'File "([^"]+)", line (\d+)', terminal_tail):
        results.append((f.strip(), int(l)))
    # Pytest / Node / TS: path/to/file.py:22:
    for f, l in re.findall(r"([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx)):(\d+):", terminal_tail):
        results.append((f.strip(), int(l)))
    return results


def _safe_get_wiki(module: str, repo_dir: Path) -> str:
    from brain.wiki import get_wiki

    try:
        return get_wiki(module, repo_dir=repo_dir)
    except TypeError:
        return get_wiki(module)


def _format_emotions(emotions: Optional[Dict[str, float]]) -> Optional[str]:
    """Format all CV emotion scores clearly, highlighting dominant affective state."""
    if not emotions:
        return None
    canonical_order = [
        "confused",
        "frustrated",
        "surprised",
        "fatigued",
        "delighted",
        "flow",
        "frustration",
        "confusion",
        "surprise",
        "anger",
        "sad",
        "fear",
        "happy",
        "neutral",
    ]
    seen = set()
    ordered_pairs: List[Tuple[str, float]] = []

    for key in canonical_order:
        if key in emotions and emotions[key] is not None:
            try:
                ordered_pairs.append((key, float(emotions[key])))
                seen.add(key)
            except (TypeError, ValueError):
                pass
    for key, val in sorted(emotions.items()):
        if key not in seen and val is not None:
            try:
                ordered_pairs.append((key, float(val)))
            except (TypeError, ValueError):
                pass

    if not ordered_pairs:
        return None

    scores_str = ", ".join(f"{k}={v:.2f}" for k, v in ordered_pairs)
    dominant = max(ordered_pairs, key=lambda kv: kv[1])
    return f"Dominant signal: {dominant[0]} ({dominant[1]:.2f}) | All scores: {scores_str}"


def _format_attempt_log(attempt_log: Any) -> Optional[str]:
    if not attempt_log:
        return None
    if isinstance(attempt_log, str):
        return attempt_log.strip() or None
    if isinstance(attempt_log, list):
        lines: List[str] = []
        for entry in attempt_log[-15:]:
            if isinstance(entry, AttemptEvent):
                item = entry.model_dump(exclude_none=True)
            elif isinstance(entry, dict):
                item = entry
            else:
                lines.append(f"- {entry}")
                continue
            summary = item.get("summary") or item.get("command") or item.get("type") or json.dumps(item)
            loc = f" ({item['file']}:{item.get('line', '')})" if item.get("file") else ""
            exit_part = f" [exit={item['exitCode']}]" if item.get("exitCode") is not None else ""
            lines.append(f"- [{item.get('source', 'event')}] {summary}{loc}{exit_part}")
        return "\n".join(lines) if lines else None
    return str(attempt_log)


def assemble_ordered_context(
    req: DiagnoseRequest,
    repo_dir: Optional[Path] = None,
    use_codemap: bool = True,
    use_wiki: bool = True,
    normalized_spatial_points: Optional[Dict[str, List[int]]] = None,
    has_screenshot: Optional[bool] = None,
    screenshot_bytes: Optional[int] = None,
    max_total_chars: int = 16000,
) -> Tuple[str, Dict[str, int]]:
    """Assemble the 6 context parts in strict order for `repo_dir`, returning (context_text, part_sizes)."""
    target_repo = Path(repo_dir).expanduser().resolve() if repo_dir else resolve_repo_dir(req.repoPath)
    part_sizes: Dict[str, int] = {}
    sections: List[str] = []

    is_comp = is_code_comprehension_request(req)

    # (1) Failing output & IDE diagnostics
    fail_parts: List[str] = []
    if req.terminalState:
        if req.terminalState.command:
            fail_parts.append(f"Command executed: {req.terminalState.command}")
        if req.terminalState.exitCode is not None:
            fail_parts.append(f"Exit code: {req.terminalState.exitCode}")
        if req.terminalState.tailOutput:
            fail_parts.append(f"Output / Traceback:\n```\n{req.terminalState.tailOutput.strip()}\n```")
    if req.editorState and req.editorState.diagnosticsSummary:
        fail_parts.append(f"IDE Diagnostics Summary:\n{req.editorState.diagnosticsSummary.strip()}")

    if is_comp and not fail_parts:
        part1_str = "## (1) FAILING OUTPUT\nNone (zero active editor diagnostics and clean terminal state — code comprehension mode)."
    else:
        part1_str = "## (1) FAILING OUTPUT\n" + ("\n".join(fail_parts) if fail_parts else "None provided.")
    part_sizes["failing_output"] = len(part1_str)
    sections.append(part1_str)

    # (2) Uncommitted working-tree diff + recent commit diff
    diff_bundle = get_working_diff(target_repo)
    diff_chunks: List[str] = []
    if req.diff and req.diff.strip():
        diff_chunks.append(f"# Explicit Request Diff:\n{req.diff.strip()}")
    if diff_bundle.get("diff"):
        diff_chunks.append(diff_bundle["diff"])
    raw_diff = "\n\n".join(diff_chunks).strip() or "No uncommitted working-tree or recent commit diff found."
    if len(raw_diff) > 5500:
        raw_diff = raw_diff[:5500] + "\n... [diff truncated; use git_diff / read_file_range tools for more]"
    part2_str = f"## (2) GIT DIFF (Workspace: {target_repo.name})\n```diff\n{raw_diff}\n```"
    part_sizes["git_diff"] = len(part2_str)
    sections.append(part2_str)

    # (3) Enclosing symbol, callers, and active editor code window
    part3_lines = ["## (3) CODE MAP & SYMBOL CALLERS"]
    affected_modules = set()
    active_rel_file: Optional[str] = None
    if req.editorState and req.editorState.activeFile:
        active_rel_file = normalize_repo_file_path(req.editorState.activeFile, repo_dir=target_repo)

    if use_codemap:
        symbols_seen = set()
        diff_for_locs = (
            (diff_bundle.get("working_diff") or "")
            + "\n"
            + (diff_bundle.get("recent_commit_diff") or "")
            + "\n"
            + (req.diff or "")
        )
        diff_locations = extract_affected_files_and_lines_from_diff(diff_for_locs)
        tb_locations = extract_traceback_frames(
            req.terminalState.tailOutput if req.terminalState and req.terminalState.tailOutput else ""
        )
        all_locs = diff_locations + tb_locations

        if active_rel_file and req.editorState and req.editorState.cursorLine:
            all_locs.append((active_rel_file, req.editorState.cursorLine))

        for file_name, line_num in all_locs:
            norm_f = normalize_repo_file_path(file_name, repo_dir=target_repo) or file_name
            stem = Path(norm_f).stem
            if stem:
                affected_modules.add(stem)

            enc = enclosing_symbol(norm_f, line_num, repo_dir=target_repo)
            if enc and enc["name"] not in symbols_seen:
                symbols_seen.add(enc["name"])
                callers = find_callers(enc["name"], repo_dir=target_repo)
                callers_str = (
                    ", ".join(f"{c['file']}:{c['line']} in {c['caller']}" for c in callers[:4])
                    or "None found"
                )
                part3_lines.append(
                    f"- Symbol: `{enc['name']}` ({enc['type']}) in `{enc['file']}` L{enc['start_line']}-L{enc['end_line']}\n"
                    f"  Signature: `{enc.get('signature', 'N/A')}`\n"
                    f"  Callers: {callers_str}"
                )

        if len(part3_lines) == 1:
            outline = get_code_map(target_repo).format_summary(max_chars=1200)
            part3_lines.append(outline or "No active Python symbols identified.")
    else:
        part3_lines.append("Code map disabled by flag.")

    # Include focused active file snippet around cursor if available
    if active_rel_file and (target_repo / active_rel_file).is_file():
        center_ln = (req.editorState.cursorLine if req.editorState and req.editorState.cursorLine else 1)
        win_start = max(1, center_ln - 15)
        win_end = center_ln + 15
        snippet_res = read_file_range(active_rel_file, start_line=win_start, end_line=win_end, repo_dir=target_repo)
        if snippet_res.get("content"):
            part3_lines.append(
                f"\nActive File Window (`{active_rel_file}` L{snippet_res['start_line']}-L{snippet_res['end_line']}):\n"
                f"```\n{snippet_res['content']}\n```"
            )

    part3_str = "\n".join(part3_lines)
    part_sizes["codemap"] = len(part3_str)
    sections.append(part3_str)

    # (4) Wiki section for affected modules only
    part4_lines = ["## (4) ARCHITECTURE WIKI (Affected Modules Only)"]
    if use_wiki:
        wiki_snippets: List[str] = []
        for mod in sorted(affected_modules):
            sec = _safe_get_wiki(mod, target_repo)
            if sec and sec.strip():
                wiki_snippets.append(sec.strip())

        if not wiki_snippets:
            overview = _safe_get_wiki("", target_repo)
            if overview and overview.strip():
                wiki_snippets.append(overview.strip()[:2000])

        if wiki_snippets:
            part4_lines.extend(wiki_snippets[:4])
        else:
            part4_lines.append("No module wiki section available for this workspace.")
    else:
        part4_lines.append("Architecture wiki disabled by flag.")

    part4_str = "\n\n".join(part4_lines)
    part_sizes["wiki"] = len(part4_str)
    sections.append(part4_str)

    # (5) Affective emotions, user status, fusion reasons, editor state, and attempt log
    part5_lines = ["## (5) LIVE TELEMETRY, EMOTIONS & ATTEMPT LOG"]
    part5_lines.append(f"Target Workspace: {target_repo}")
    if req.userStatus:
        part5_lines.append(f"User Status: {req.userStatus}")
    if req.reasons:
        part5_lines.append(f"Trigger / Fusion Reasons: {', '.join(str(r) for r in req.reasons)}")

    emotions_str = _format_emotions(req.emotions)
    if emotions_str:
        part5_lines.append(f"Facial Emotions (CV): {emotions_str}")

    fatigue_score = 0.0
    if isinstance(req.emotions, dict) and req.emotions.get("fatigued") is not None:
        try:
            fatigue_score = float(req.emotions["fatigued"])
        except (TypeError, ValueError):
            fatigue_score = 0.0
    if (req.userStatus and "fatigued" in str(req.userStatus).lower()) or fatigue_score >= 0.58:
        part5_lines.append(
            f"FATIGUE / TIREDNESS ALERT: Developer shows high fatigue (status={req.userStatus or 'fatigued'}, "
            f"fatigued={fatigue_score:.2f}). Remind them warmly in Gordon Ramsay's protective Head Chef voice "
            "to step away from the keyboard and take a 5-10 minute break!"
        )

    if req.editorState:
        part5_lines.append(
            f"Editor: activeFile={active_rel_file or req.editorState.activeFile}, "
            f"line={req.editorState.cursorLine}, col={req.editorState.cursorColumn}, "
            f"diagnosticsCount={req.editorState.diagnosticsCount}, "
            f"undo={req.editorState.isUndo}, dwellMs={req.editorState.cursorDwellMs}"
        )
        if req.editorState.diagnosticsSummary:
            part5_lines.append(f"Editor Diagnostics: {req.editorState.diagnosticsSummary}")
        if req.editorState.selectionText:
            part5_lines.append(f"Selected Text:\n```\n{req.editorState.selectionText[:800]}\n```")

    formatted_attempts = _format_attempt_log(req.attemptLog)
    if formatted_attempts:
        part5_lines.append(f"Attempt Log:\n{formatted_attempts}")

    if is_comp:
        part5_lines.append(
            "CONFUSION MODE: CODE COMPREHENSION (no active compiler/linter diagnostics or failing terminal runs). "
            "Developer is seeking to understand and mentally trace the active code. "
            "Walk them through the concepts, control flow, and architecture around their cursor/selection. "
            "Do NOT assert a bug, defect, or report an error. suggestedFix must be null."
        )

    part5_str = "\n".join(part5_lines)
    part_sizes["telemetry"] = len(part5_str)
    sections.append(part5_str)

    # (6) Screenshot and Spatial Coordinates (Gemini [y, x] 0-1000 scale)
    part6_lines = ["## (6) SCREENSHOT & SPATIAL CONTEXT"]
    shot_present = has_screenshot if has_screenshot is not None else bool(req.screenshot)
    if shot_present:
        size_note = f"{screenshot_bytes:,} bytes" if screenshot_bytes else f"{len(req.screenshot or '')} chars base64"
        part6_lines.append(f"Screenshot attached ({size_note}).")
    else:
        part6_lines.append("Screenshot: Not captured or disabled.")

    if normalized_spatial_points:
        part6_lines.append(
            "Normalized Spatial Coordinates (Gemini convention [y, x], 0-1000 scale relative to raw display/screenshot):\n"
            f"```json\n{json.dumps(normalized_spatial_points, indent=2)}\n```\n"
            "Trust hierarchy: Caret & cursor are primary signals of active focus. Gaze is ambient and must NEVER override code diff or error evidence."
        )
    part6_str = "\n".join(part6_lines)
    part_sizes["screenshot"] = len(part6_str)
    sections.append(part6_str)

    total_chars = sum(part_sizes.values())
    part_sizes["total"] = total_chars

    full_context = "\n\n" + ("=" * 40 + "\n\n").join(sections)
    if len(full_context) > max_total_chars:
        full_context = full_context[:max_total_chars] + "\n... [Context capped]"

    return full_context, part_sizes


# Alias matching cross-component naming
assemble_context_packet = assemble_ordered_context
