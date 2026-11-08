"""Resilient LLM output extractor, schema normalizer, and structured-edit validator."""

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from brain.models import CodeCitation, DiagnosisResponse, StructuredEdit

DIAGNOSIS_SCORE_WEIGHTS: Dict[str, int] = {
    "summary": 4,
    "quip": 4,
    "roast": 3,
    "commentary": 3,
    "ramsayQuip": 3,
    "file": 3,
    "lines": 2,
    "explanation": 3,
    "uncertain": 2,
    "confidence": 2,
    "citations": 2,
    "suggestedFix": 2,
    "suggested_fix": 2,
    "hintLevel": 1,
}


def extract_response_text(response: Any, provider: Optional[str] = None) -> str:
    """Extract clean text across Gemini, OpenAI, and Anthropic SDK response objects."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        if "content" in response and isinstance(response["content"], str):
            return response["content"].strip()
        return json.dumps(response)

    prov = (provider or "").lower()

    # 1. Gemini (google-genai): inspect candidates[*].content.parts[*], skipping thought parts
    candidates = getattr(response, "candidates", None)
    if candidates is not None and prov in ("", "gemini", "google"):
        collected: List[str] = []
        try:
            for cand in candidates or []:
                content = getattr(cand, "content", None)
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    if bool(getattr(part, "thought", False)):
                        continue
                    txt = getattr(part, "text", None)
                    if isinstance(txt, str) and txt:
                        collected.append(txt)
        except Exception:
            pass
        if collected:
            return "".join(collected).strip()

    # 2. OpenAI: choices[0].message.content
    choices = getattr(response, "choices", None)
    if choices and len(choices) > 0:
        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            chunks: List[str] = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
                elif isinstance(getattr(block, "text", None), str):
                    chunks.append(block.text)
            if chunks:
                return "\n".join(chunks).strip()

    # 3. Anthropic: response.content text blocks (skipping thinking / tool_use)
    anth_content = getattr(response, "content", None)
    if isinstance(anth_content, list):
        blocks_text: List[str] = []
        for block in anth_content:
            if isinstance(block, dict):
                b_type = block.get("type", "text")
                b_txt = block.get("text")
            else:
                b_type = getattr(block, "type", "text")
                b_txt = getattr(block, "text", None)
            if b_type == "text" and isinstance(b_txt, str) and b_txt:
                blocks_text.append(b_txt)
        if blocks_text:
            return "\n".join(blocks_text).strip()

    # 4. Fallback to .text property (wrapped safely because google-genai .text can raise on tool calls)
    try:
        raw_txt = getattr(response, "text", None)
        if isinstance(raw_txt, str) and raw_txt.strip():
            return raw_txt.strip()
    except Exception:
        pass

    return ""


def _repair_json_candidate(candidate: str) -> str:
    """Repair common LLM JSON quirks: trailing commas outside strings and raw control chars inside strings."""
    out: List[str] = []
    in_str = False
    escape = False
    n = len(candidate)
    i = 0

    while i < n:
        ch = candidate[i]
        if in_str:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch == ",":
                # Look ahead past whitespace for closing } or ]
                j = i + 1
                while j < n and candidate[j] in " \t\r\n":
                    j += 1
                if j < n and candidate[j] in ("}", "]"):
                    # Drop trailing comma
                    pass
                else:
                    out.append(ch)
            else:
                out.append(ch)
        i += 1

    return "".join(out)


def _try_parse_dict(raw: str) -> Optional[Dict[str, Any]]:
    """Attempt strict json.loads, then repaired json.loads, returning a dict or None."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    try:
        repaired = _repair_json_candidate(raw)
        obj = json.loads(repaired)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    return None


def extract_json_objects(text: str) -> List[Dict[str, Any]]:
    """Scan text for balanced '{ ... }' objects while ignoring braces inside string literals."""
    if not text or "{" not in text:
        return []

    results: List[Dict[str, Any]] = []
    n = len(text)
    i = 0

    while i < n:
        start = text.find("{", i)
        if start == -1:
            break

        depth = 0
        in_str = False
        escape = False
        end = -1

        for j in range(start, n):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break

        if end != -1:
            candidate = text[start : end + 1]
            parsed = _try_parse_dict(candidate)
            if parsed is not None:
                results.append(parsed)
                i = end + 1
                continue

        # If outer brace wasn't valid JSON (or unclosed), step forward by 1 so nested JSON is still found
        i = start + 1

    return results


def _score_diagnosis_dict(d: Dict[str, Any]) -> int:
    """Score a parsed dict based on presence of diagnosis schema fields."""
    score = 0
    for key, weight in DIAGNOSIS_SCORE_WEIGHTS.items():
        if key in d and d[key] is not None:
            score += weight
    return score


def extract_best_json_dict(text: str) -> Optional[Dict[str, Any]]:
    """Extract the best-matching diagnosis JSON dictionary from fenced or free-form LLM output."""
    if not text or not text.strip():
        return None

    stripped = text.strip()
    direct = _try_parse_dict(stripped)
    if direct is not None and _score_diagnosis_dict(direct) > 0:
        return direct

    # Check fenced code blocks first (```json ... ``` or ``` ... ```)
    candidates: List[Dict[str, Any]] = []
    for match in re.finditer(r"```(?:json|JSON)?\s*\r?\n([\s\S]*?)```", text):
        block = match.group(1).strip()
        parsed = _try_parse_dict(block)
        if parsed is not None:
            candidates.append(parsed)
        else:
            candidates.extend(extract_json_objects(block))

    # Also scan full text with balanced brace scanner
    candidates.extend(extract_json_objects(text))

    if not candidates:
        return None

    best = max(candidates, key=_score_diagnosis_dict)
    if _score_diagnosis_dict(best) > 0:
        return best
    if len(candidates) == 1:
        return candidates[0]
    return None


def normalize_hint_level(raw_level: Any, default: str = "hint") -> str:
    """Normalize hintLevel to 'nudge' | 'hint' | 'full_explanation'."""
    fallback = default if default in ("nudge", "hint", "full_explanation") else "hint"
    if not isinstance(raw_level, str) or not raw_level.strip():
        return fallback
    val = raw_level.strip().lower()
    if val in ("full", "full_explanation", "full-explanation", "full_fix", "explanation"):
        return "full_explanation"
    if val in ("nudge", "hint"):
        return val
    return fallback


def normalize_lines(raw_lines: Any) -> Optional[Tuple[int, int]]:
    """Normalize diverse line representations ([11,14], [11], 11, '11-14', 'L11', [14,11]) to (start, end)."""
    if raw_lines is None or isinstance(raw_lines, bool):
        return None

    if isinstance(raw_lines, (int, float)):
        if math.isnan(raw_lines) or math.isinf(raw_lines):
            return None
        ln = int(raw_lines)
        return (ln, ln) if ln >= 1 else None

    if isinstance(raw_lines, dict):
        s = raw_lines.get("start") or raw_lines.get("startLine") or raw_lines.get("start_line") or raw_lines.get("line")
        e = raw_lines.get("end") or raw_lines.get("endLine") or raw_lines.get("end_line") or s
        if s is not None:
            return normalize_lines([s, e])
        return None

    if isinstance(raw_lines, (list, tuple)):
        if len(raw_lines) == 0:
            return None
        if len(raw_lines) == 1:
            return normalize_lines(raw_lines[0])
        first, second = raw_lines[0], raw_lines[1]
        if isinstance(first, bool) or isinstance(second, bool):
            return None
        try:
            a = int(str(first).strip().lstrip("Ll"))
            b = int(str(second).strip().lstrip("Ll"))
        except (TypeError, ValueError):
            return None
        if a < 1 or b < 1:
            return None
        return (min(a, b), max(a, b))

    if isinstance(raw_lines, str):
        s = raw_lines.strip()
        if not s or s.lower() in ("none", "null", "n/a"):
            return None
        # Match range like "11-14", "L11-L14", "11..14", "11:14", "[11, 14]", "lines 11 to 14"
        range_match = re.search(r"[Ll]?(-?\d+)\s*(?:[-–—:.]+|,|\bto\b)\s*[Ll]?(-?\d+)", s)
        if range_match:
            a, b = int(range_match.group(1)), int(range_match.group(2))
            if a < 1 or b < 1:
                return None
            return (min(a, b), max(a, b))
        single_match = re.search(r"(?:^|\bline\s+|[Ll])(-?\d+)\b", s, re.IGNORECASE)
        if single_match:
            a = int(single_match.group(1))
            return (a, a) if a >= 1 else None

    return None


def normalize_confidence(raw_conf: Any, default: float = 0.85) -> float:
    """Normalize confidence score, dividing percentages (1 < val <= 100) by 100 and clamping to [0, 1]."""
    if raw_conf is None or isinstance(raw_conf, bool):
        return max(0.0, min(1.0, default))

    val: Optional[float] = None
    if isinstance(raw_conf, (int, float)):
        val = float(raw_conf)
    elif isinstance(raw_conf, str):
        cleaned = raw_conf.strip().rstrip("%").strip()
        try:
            val = float(cleaned)
        except ValueError:
            low = cleaned.lower()
            if low == "high":
                val = 0.9
            elif low == "medium":
                val = 0.65
            elif low == "low":
                val = 0.35

    if val is None or math.isnan(val) or math.isinf(val):
        return max(0.0, min(1.0, default))

    if 1.0 < val <= 100.0:
        val = val / 100.0

    return round(max(0.0, min(1.0, val)), 4)


def _split_trailing_line_ref(path_str: str) -> Tuple[str, Optional[Tuple[int, int]]]:
    """Split 'path/to/file.py:12-15' into ('path/to/file.py', (12, 15)) without breaking Windows drive 'C:/...'."""
    match = re.match(r"^(.+?):[Ll]?(\d+)(?:[-:][Ll]?(\d+))?$", path_str)
    if match:
        prefix = match.group(1)
        # Ensure prefix is not a single drive letter like "C"
        if len(prefix) > 1:
            start_ln = int(match.group(2))
            end_ln = int(match.group(3)) if match.group(3) else start_ln
            lines = (min(start_ln, end_ln), max(start_ln, end_ln)) if start_ln >= 1 and end_ln >= 1 else None
            return prefix, lines
    return path_str, None


def normalize_repo_file_path(raw_file: Any, repo_dir: Optional[Path] = None) -> Optional[str]:
    """Normalize file URI/path: strip file:///, convert '\\' -> '/', and make relative to repo_dir if inside."""
    if raw_file is None or not isinstance(raw_file, str):
        return None

    s = unquote(raw_file.strip().strip("`'\""))
    if not s or s.lower() in ("none", "null", "n/a", "unknown"):
        return None

    # Strip file:// scheme
    if s.lower().startswith("file:///"):
        rest = s[8:]
        # Windows drive path e.g. C:/Users/... vs POSIX /Users/...
        if re.match(r"^[a-zA-Z]:", rest):
            s = rest
        else:
            s = "/" + rest.lstrip("/")
    elif s.lower().startswith("file://"):
        s = s[7:]

    s = s.replace("\\", "/")
    s, _ = _split_trailing_line_ref(s)
    s = s.strip()
    if not s:
        return None

    if repo_dir is not None:
        repo_root = Path(repo_dir).resolve()
        repo_posix = repo_root.as_posix().rstrip("/")

        # Case-insensitive prefix check for Windows paths (e.g. c:/Users/... vs C:/Users/...)
        if s.lower().startswith(repo_posix.lower() + "/"):
            rel_part = s[len(repo_posix) + 1 :].lstrip("./")
            if rel_part:
                return rel_part
        try:
            cand = Path(s)
            if cand.is_absolute():
                rel = cand.resolve().relative_to(repo_root)
                return rel.as_posix()
        except Exception:
            pass

        s_rel = s.lstrip("./")
        if s_rel.startswith("/") and not Path(s).exists():
            s_rel = s_rel.lstrip("/")

        # Strip redundant leading '<repo_dir.name>/' if the unprefixed file exists in repo_dir
        prefix = f"{repo_root.name}/"
        if s_rel.startswith(prefix) and not (repo_root / s_rel).exists():
            stripped_rel = s_rel[len(prefix) :]
            if stripped_rel and (repo_root / stripped_rel).exists():
                return stripped_rel

        return s_rel or None

    return s.lstrip("./") or None


def normalize_citations(raw_citations: Any, repo_dir: Optional[Path] = None) -> List[CodeCitation]:
    """Normalize citations field (None -> [], partial dicts or 'file:line' strings -> CodeCitation)."""
    if not raw_citations:
        return []
    if isinstance(raw_citations, dict):
        raw_list = [raw_citations]
    elif isinstance(raw_citations, list):
        raw_list = raw_citations
    else:
        return []

    out: List[CodeCitation] = []
    for item in raw_list:
        if isinstance(item, CodeCitation):
            norm_f = normalize_repo_file_path(item.file, repo_dir) or item.file
            out.append(item.model_copy(update={"file": norm_f}))
            continue

        if isinstance(item, str):
            path_part, ln_tuple = _split_trailing_line_ref(item.strip().strip("`'\""))
            norm_f = normalize_repo_file_path(path_part, repo_dir)
            if norm_f and ln_tuple:
                out.append(CodeCitation(file=norm_f, lines=ln_tuple))
            continue

        if isinstance(item, dict):
            raw_f = item.get("file") or item.get("filePath") or item.get("file_path") or item.get("path")
            if not isinstance(raw_f, str) or not raw_f.strip():
                continue
            _, inline_lines = _split_trailing_line_ref(raw_f.strip())
            norm_f = normalize_repo_file_path(raw_f, repo_dir)
            if not norm_f:
                continue

            ln = normalize_lines(item.get("lines"))
            if ln is None:
                sl = item.get("startLine") if item.get("startLine") is not None else (
                    item.get("start_line") if item.get("start_line") is not None else item.get("line")
                )
                el = item.get("endLine") if item.get("endLine") is not None else (
                    item.get("end_line") if item.get("end_line") is not None else sl
                )
                if sl is not None:
                    ln = normalize_lines([sl, el])
            if ln is None:
                ln = inline_lines or (1, 1)

            snippet_raw = item.get("snippet") if item.get("snippet") is not None else item.get("code")
            note_raw = item.get("note") if item.get("note") is not None else (
                item.get("reason") or item.get("description")
            )
            out.append(
                CodeCitation(
                    file=norm_f,
                    lines=ln,
                    snippet=str(snippet_raw) if snippet_raw is not None else None,
                    note=str(note_raw) if note_raw is not None else None,
                )
            )
    return out


def extract_file_line_refs_from_text(
    text: str, repo_dir: Optional[Path] = None
) -> List[Tuple[str, Optional[Tuple[int, int]]]]:
    """Extract file and optional line references (e.g. 'path/to/file.py:12-14' or 'utils.py line 11') from prose."""
    if not text:
        return []

    found: List[Tuple[str, Optional[Tuple[int, int]]]] = []
    seen = set()

    # Pattern 1: path/to/file.ext:12 or path/to/file.ext:12-15
    p1 = re.finditer(
        r"(?<![\w/])([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|c|cpp|h|rb|cs|php|kt|swift)):[Ll]?(\d+)(?:[-–:][Ll]?(\d+))?",
        text,
    )
    for m in p1:
        f_norm = normalize_repo_file_path(m.group(1), repo_dir)
        if not f_norm:
            continue
        s_ln = int(m.group(2))
        e_ln = int(m.group(3)) if m.group(3) else s_ln
        ln = (min(s_ln, e_ln), max(s_ln, e_ln)) if s_ln >= 1 else None
        key = (f_norm, ln)
        if key not in seen:
            seen.add(key)
            found.append((f_norm, ln))

    # Pattern 2: `file.py` (line 11 / lines 11-14)
    p2 = re.finditer(
        r"`?([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|c|cpp|h|rb|cs))`?"
        r"(?:[\s,()]+(?:on\s+|at\s+)?lines?\s+[Ll]?(\d+)(?:\s*(?:[-–]|to)\s*[Ll]?(\d+))?)",
        text,
        re.IGNORECASE,
    )
    for m in p2:
        f_norm = normalize_repo_file_path(m.group(1), repo_dir)
        if not f_norm:
            continue
        s_ln = int(m.group(2))
        e_ln = int(m.group(3)) if m.group(3) else s_ln
        ln = (min(s_ln, e_ln), max(s_ln, e_ln)) if s_ln >= 1 else None
        key = (f_norm, ln)
        if key not in seen:
            seen.add(key)
            found.append((f_norm, ln))

    # Pattern 3: standalone filename if it exists in repo_dir
    if not found and repo_dir is not None:
        repo_root = Path(repo_dir).resolve()
        p3 = re.finditer(
            r"`?([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|c|cpp|h|rb|cs))`?",
            text,
        )
        for m in p3:
            f_norm = normalize_repo_file_path(m.group(1), repo_dir)
            if f_norm and (repo_root / f_norm).is_file():
                ln_match = re.search(r"\blines?\s+[Ll]?(\d+)(?:\s*[-–]\s*[Ll]?(\d+))?", text, re.IGNORECASE)
                ln = None
                if ln_match:
                    a = int(ln_match.group(1))
                    b = int(ln_match.group(2)) if ln_match.group(2) else a
                    if a >= 1:
                        ln = (min(a, b), max(a, b))
                found.append((f_norm, ln))
                break

    # Prioritize files that exist on disk in repo_dir
    if repo_dir is not None and len(found) > 1:
        repo_root = Path(repo_dir).resolve()
        found.sort(key=lambda item: (0 if (repo_root / item[0]).is_file() else 1, 0 if item[1] else 1))

    return found


def _extract_punchy_sentences(text: str) -> List[str]:
    """Extract clean non-code, non-heading sentences from markdown text."""
    # Remove fenced code blocks first
    without_fences = re.sub(r"```[\s\S]*?```", " ", text)
    sentences: List[str] = []
    for raw_line in without_fences.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        line = re.sub(r"^[-*>+\d.)\s]+", "", line).strip()
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+", line)
        for p in parts:
            clean = p.strip()
            if len(clean) >= 8:
                sentences.append(clean)
    return sentences


def synthesize_diagnosis_from_text(
    text: str,
    hint_level: str = "hint",
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Synthesize a structured diagnosis dict when the LLM emits plain markdown/prose instead of JSON."""
    clean_text = (text or "").strip()
    sentences = _extract_punchy_sentences(clean_text)

    summary = sentences[0][:240] if sentences else "Diagnosed issue from workspace inspection."
    # Pick an exclamation/roast sentence for quip if available, otherwise first punchy sentence
    roast_candidates = [s for s in sentences if "!" in s and len(s) <= 220]
    if roast_candidates:
        quip = roast_candidates[0]
    elif len(sentences) >= 2 and len(sentences[1]) <= 220:
        quip = sentences[1]
    else:
        quip = summary

    refs = extract_file_line_refs_from_text(clean_text, repo_dir=repo_dir)
    top_file: Optional[str] = refs[0][0] if refs else None
    top_lines: Optional[Tuple[int, int]] = refs[0][1] if refs else None

    citations: List[Dict[str, Any]] = []
    for f_path, f_lines in refs[:3]:
        if f_lines:
            citations.append({"file": f_path, "lines": f_lines})

    file_exists = False
    if top_file and repo_dir is not None:
        file_exists = (Path(repo_dir).resolve() / top_file).is_file()

    return {
        "summary": summary,
        "file": top_file,
        "lines": top_lines,
        "quip": quip,
        "uncertain": not file_exists,
        "confidence": 0.78 if file_exists else 0.5,
        "hintLevel": normalize_hint_level(hint_level),
        "explanation": clean_text or summary,
        "citations": citations,
        "suggestedFix": None,
    }


def normalize_diagnosis_dict(
    raw: Dict[str, Any],
    default_hint_level: str = "hint",
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Normalize a raw LLM diagnosis dictionary before Pydantic validation."""
    data = dict(raw or {})

    # File & inline line fallback
    raw_file = data.get("file") or data.get("filePath") or data.get("file_path") or data.get("path")
    inline_lines: Optional[Tuple[int, int]] = None
    if isinstance(raw_file, str):
        _, inline_lines = _split_trailing_line_ref(raw_file.strip())
    norm_file = normalize_repo_file_path(raw_file, repo_dir=repo_dir)

    # Lines
    raw_lines = data.get("lines")
    if raw_lines is None:
        sl = data.get("startLine") if data.get("startLine") is not None else (
            data.get("start_line") if data.get("start_line") is not None else data.get("line")
        )
        el = data.get("endLine") if data.get("endLine") is not None else (
            data.get("end_line") if data.get("end_line") is not None else sl
        )
        if sl is not None:
            raw_lines = [sl, el]
    norm_lines = normalize_lines(raw_lines) or inline_lines

    # Explanation
    raw_exp = (
        data.get("explanation")
        or data.get("details")
        or data.get("analysis")
        or data.get("rootCause")
        or data.get("root_cause")
    )
    explanation = str(raw_exp).strip() if raw_exp is not None and str(raw_exp).strip() else None

    # Summary
    raw_sum = (
        data.get("summary")
        or data.get("diagnosis")
        or data.get("title")
        or data.get("issue")
        or data.get("message")
    )
    summary = str(raw_sum).strip() if raw_sum is not None and str(raw_sum).strip() else ""
    if not summary and explanation:
        sents = _extract_punchy_sentences(explanation)
        summary = sents[0][:240] if sents else explanation.splitlines()[0][:240]
    if not summary:
        summary = "Code issue identified during workspace inspection."

    # Quip (Gordon Ramsay roast)
    raw_quip = (
        data.get("quip")
        or data.get("roast")
        or data.get("ramsayQuip")
        or data.get("ramsay_quip")
        or data.get("commentary")
    )
    quip = str(raw_quip).strip() if raw_quip is not None and str(raw_quip).strip() else ""
    if not quip:
        quip = summary

    # Uncertain & confidence
    raw_unc = data.get("uncertain")
    if isinstance(raw_unc, bool):
        uncertain = raw_unc
    elif isinstance(raw_unc, str):
        uncertain = raw_unc.strip().lower() in ("true", "1", "yes")
    else:
        uncertain = False

    confidence = normalize_confidence(data.get("confidence"), default=0.45 if uncertain else 0.88)
    hint_level = normalize_hint_level(
        data.get("hintLevel") or data.get("hint_level"), default=default_hint_level
    )
    citations = normalize_citations(data.get("citations"), repo_dir=repo_dir)

    # If top-level file/lines missing, inherit from first citation if available
    if norm_file is None and citations:
        norm_file = citations[0].file
    if norm_lines is None and citations:
        norm_lines = citations[0].lines

    raw_fix = data.get("suggestedFix") if "suggestedFix" in data else data.get("suggested_fix")

    return {
        "summary": summary,
        "file": norm_file,
        "lines": norm_lines,
        "quip": quip,
        "uncertain": uncertain,
        "confidence": confidence,
        "hintLevel": hint_level,
        "explanation": explanation,
        "citations": citations,
        "raw_suggested_fix": raw_fix,
    }


def _reindent_replacement_lines(
    matched_file_lines: List[str],
    old_lines: List[str],
    new_lines: List[str],
) -> str:
    """Re-indent new_lines by the exact leading-indentation delta between matched_file_lines and old_lines."""
    file_indent = ""
    old_indent = ""
    for f_line, o_line in zip(matched_file_lines, old_lines):
        if f_line.strip() and o_line.strip():
            file_indent = f_line[: len(f_line) - len(f_line.lstrip())]
            old_indent = o_line[: len(o_line) - len(o_line.lstrip())]
            break

    indent_char = "\t" if ("\t" in file_indent and " " not in file_indent) else " "
    delta = len(file_indent) - len(old_indent)

    adjusted: List[str] = []
    for nl in new_lines:
        nl_clean = nl.rstrip()
        if not nl_clean.strip():
            adjusted.append("")
            continue
        if old_indent and nl_clean.startswith(old_indent):
            adjusted.append(file_indent + nl_clean[len(old_indent) :])
        elif not old_indent:
            adjusted.append(file_indent + nl_clean)
        else:
            cur_lead = len(nl_clean) - len(nl_clean.lstrip())
            new_lead = max(0, cur_lead + delta)
            adjusted.append((indent_char * new_lead) + nl_clean.lstrip())

    return "\n".join(adjusted)


def _extract_before_after_candidates_from_explanation(
    explanation: Optional[str],
) -> List[Tuple[str, str]]:
    """Extract candidate (oldText, newText) pairs from markdown explanation blocks."""
    if not explanation:
        return []

    candidates: List[Tuple[str, str]] = []
    fenced_blocks = list(re.finditer(r"```([a-zA-Z0-9_+-]*)\s*\r?\n([\s\S]*?)```", explanation))

    # 1. Single block containing '# Before' ... '# After' or '// Before' ... '// After'
    for m in fenced_blocks:
        lang = (m.group(1) or "").strip().lower()
        body = m.group(2).replace("\r\n", "\n")

        ba = re.search(
            r"(?:^|\n)[ \t]*(?:#|//)[ \t]*Before[^\n]*\n([\s\S]*?)\n+[ \t]*(?:#|//)[ \t]*After[^\n]*\n([\s\S]*)",
            body,
            re.IGNORECASE,
        )
        if ba:
            before_code = ba.group(1).strip("\n")
            after_code = ba.group(2).strip("\n")
            if before_code.strip() and after_code is not None:
                candidates.append((before_code, after_code))
                continue

        # 2. Unified diff block (```diff) with contiguous '-' and '+' lines
        if lang == "diff" or (
            any(l.startswith("-") and not l.startswith("---") for l in body.splitlines())
            and any(l.startswith("+") and not l.startswith("+++") for l in body.splitlines())
        ):
            minus_lines: List[str] = []
            plus_lines: List[str] = []
            for dl in body.splitlines():
                if dl.startswith("---") or dl.startswith("+++") or dl.startswith("@@"):
                    continue
                if dl.startswith("-"):
                    minus_lines.append(dl[1:])
                elif dl.startswith("+"):
                    plus_lines.append(dl[1:])
            if minus_lines and plus_lines:
                candidates.append(("\n".join(minus_lines), "\n".join(plus_lines)))

    # 3. Two fenced code blocks (Before block -> After block)
    code_blocks = [
        m.group(2).replace("\r\n", "\n").strip("\n")
        for m in fenced_blocks
        if (m.group(1) or "").strip().lower() not in ("diff", "json")
    ]
    if len(code_blocks) >= 2:
        b1, b2 = code_blocks[0], code_blocks[1]
        if b1.strip() and b1 != b2:
            candidates.append((b1, b2))

    return candidates


def _match_edit_against_file(
    rel_file: str,
    target_path: Path,
    old_text: str,
    new_text: str,
    anchor_line: Optional[int],
) -> Tuple[Optional[StructuredEdit], str]:
    """Verify and align (old_text, new_text) against target_path content."""
    try:
        raw_bytes = target_path.read_bytes()
        file_content_lf = raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")
    except Exception as exc:
        return None, f"Omitted structured edit: failed to read '{rel_file}': {exc}"

    old_clean = old_text.replace("\r\n", "\n").rstrip("\n")
    new_clean = new_text.replace("\r\n", "\n").rstrip("\n")

    if not old_clean.strip():
        return None, "Omitted structured edit: empty oldText"
    if old_clean == new_clean:
        return None, "Omitted structured edit: oldText and newText are identical"

    file_lines = file_content_lf.split("\n")
    # Drop trailing empty string produced by trailing newline when indexing lines
    if file_lines and file_lines[-1] == "" and file_content_lf.endswith("\n"):
        file_lines = file_lines[:-1]
    if not file_lines:
        return None, f"Omitted structured edit: target file '{rel_file}' is empty"

    old_lines = old_clean.split("\n")
    new_lines = new_clean.split("\n")
    k = len(old_lines)
    anchor = anchor_line if (anchor_line and anchor_line >= 1) else 1

    # Strategy 1: Exact full-line block match in file_lines
    if k <= len(file_lines):
        exact_line_matches = [
            idx for idx in range(len(file_lines) - k + 1) if file_lines[idx : idx + k] == old_lines
        ]
        if exact_line_matches:
            best_idx = min(exact_line_matches, key=lambda idx: abs((idx + 1) - anchor))
            start_ln = best_idx + 1
            end_ln = best_idx + k
            exact_old = "\n".join(file_lines[best_idx : best_idx + k])
            return (
                StructuredEdit(
                    file=rel_file,
                    startLine=start_ln,
                    endLine=end_ln,
                    oldText=exact_old,
                    newText=new_clean,
                ),
                f"Validated exact line match in '{rel_file}' L{start_ln}-L{end_ln}",
            )

    # Strategy 2: Indentation / whitespace-aligned contiguous line block match
    stripped_old = [l.strip() for l in old_lines]
    if any(stripped_old) and k <= len(file_lines):
        ws_matches = [
            idx
            for idx in range(len(file_lines) - k + 1)
            if [fl.strip() for fl in file_lines[idx : idx + k]] == stripped_old
        ]
        if ws_matches:
            best_idx = min(ws_matches, key=lambda idx: abs((idx + 1) - anchor))
            matched_slice = file_lines[best_idx : best_idx + k]
            exact_old = "\n".join(matched_slice)
            reindented_new = _reindent_replacement_lines(matched_slice, old_lines, new_lines)
            if exact_old == reindented_new:
                return None, "Omitted structured edit: aligned newText equals existing code"
            start_ln = best_idx + 1
            end_ln = best_idx + k
            return (
                StructuredEdit(
                    file=rel_file,
                    startLine=start_ln,
                    endLine=end_ln,
                    oldText=exact_old,
                    newText=reindented_new,
                ),
                f"Validated indentation-aligned match in '{rel_file}' L{start_ln}-L{end_ln}",
            )

    # Strategy 3: Exact substring match inside file_content_lf (e.g. inline expression)
    positions: List[int] = []
    search_from = 0
    while True:
        pos = file_content_lf.find(old_clean, search_from)
        if pos == -1:
            break
        positions.append(pos)
        search_from = pos + 1

    if positions:
        def _pos_start_line(p: int) -> int:
            return file_content_lf.count("\n", 0, p) + 1

        best_pos = min(positions, key=lambda p: abs(_pos_start_line(p) - anchor))
        start_ln = _pos_start_line(best_pos)
        end_ln = start_ln + old_clean.count("\n")
        return (
            StructuredEdit(
                file=rel_file,
                startLine=start_ln,
                endLine=end_ln,
                oldText=old_clean,
                newText=new_clean,
            ),
            f"Validated exact substring match in '{rel_file}' L{start_ln}-L{end_ln}",
        )

    return None, f"Omitted structured edit: oldText did not match '{rel_file}' (exact or whitespace-aligned)"


def validate_or_extract_structured_edit_with_note(
    fix_data: Any,
    explanation: Optional[str],
    file_hint: Optional[str],
    lines_hint: Optional[Tuple[int, int]],
    repo_dir: Path,
) -> Tuple[Optional[StructuredEdit], str]:
    """Validate structured edit (or recover from explanation), returning (StructuredEdit | None, trace_note)."""
    repo_root = Path(repo_dir).resolve()

    def _resolve_sandboxed_target(raw_path: Optional[str]) -> Optional[Tuple[str, Path]]:
        norm = normalize_repo_file_path(raw_path, repo_dir=repo_root)
        if not norm:
            return None
        cand = (repo_root / norm).resolve()
        try:
            rel = cand.relative_to(repo_root).as_posix()
        except ValueError:
            return None
        if not cand.exists() or not cand.is_file():
            return None
        return rel, cand

    anchor_hint = lines_hint[0] if lines_hint else None

    # 1. Try explicit fix_data if provided
    explicit_note = "No structured fix suggested"
    if fix_data is not None:
        raw_file: Optional[str] = None
        old_text: Optional[str] = None
        new_text: Optional[str] = None
        anchor_line: Optional[int] = anchor_hint

        if isinstance(fix_data, StructuredEdit):
            raw_file = fix_data.file or file_hint
            old_text = fix_data.oldText
            new_text = fix_data.newText
            anchor_line = fix_data.startLine or anchor_hint
        elif isinstance(fix_data, dict):
            raw_file = (
                fix_data.get("file")
                or fix_data.get("filePath")
                or fix_data.get("file_path")
                or fix_data.get("path")
                or file_hint
            )
            for k in ("oldText", "old_text", "original", "oldCode", "old_code", "search", "target", "find"):
                if fix_data.get(k) is not None:
                    old_text = str(fix_data[k])
                    break
            for k in ("newText", "new_text", "replacement", "newCode", "new_code", "replace"):
                if fix_data.get(k) is not None:
                    new_text = str(fix_data[k])
                    break
            ln = normalize_lines(
                fix_data.get("startLine")
                or fix_data.get("start_line")
                or fix_data.get("lines")
                or fix_data.get("line")
            )
            if ln:
                anchor_line = ln[0]

        resolved = _resolve_sandboxed_target(raw_file)
        if resolved is None:
            explicit_note = f"Omitted structured edit: file '{raw_file}' invalid, missing, or outside workspace"
        elif old_text is not None and new_text is not None:
            rel_file, target_path = resolved
            matched, note = _match_edit_against_file(
                rel_file, target_path, old_text, new_text, anchor_line
            )
            if matched is not None:
                return matched, note
            explicit_note = note
        else:
            explicit_note = "Omitted structured edit: missing oldText or newText"

    # 2. Try recovering a verified Before/After edit from explanation if file_hint (or fix_data file) resolves
    fallback_file = None
    if isinstance(fix_data, dict):
        fallback_file = fix_data.get("file") or fix_data.get("filePath") or fix_data.get("path")
    elif isinstance(fix_data, StructuredEdit):
        fallback_file = fix_data.file
    resolved_hint = _resolve_sandboxed_target(fallback_file or file_hint)

    if resolved_hint is not None and explanation:
        rel_file, target_path = resolved_hint
        for cand_old, cand_new in _extract_before_after_candidates_from_explanation(explanation):
            matched, note = _match_edit_against_file(
                rel_file, target_path, cand_old, cand_new, anchor_hint
            )
            if matched is not None:
                return matched, f"{note} (recovered from explanation)"

    return None, explicit_note


def validate_or_extract_structured_edit(
    fix_data: Any,
    explanation: Optional[str],
    file_hint: Optional[str],
    lines_hint: Optional[Tuple[int, int]],
    repo_dir: Path,
) -> Optional[StructuredEdit]:
    """Validate structured edit or extract a verified Before/After edit from explanation."""
    edit, _ = validate_or_extract_structured_edit_with_note(
        fix_data=fix_data,
        explanation=explanation,
        file_hint=file_hint,
        lines_hint=lines_hint,
        repo_dir=repo_dir,
    )
    return edit


def parse_diagnosis_response(
    raw_text: str,
    default_hint_level: str,
    repo_dir: Path,
) -> Tuple[DiagnosisResponse, str]:
    """Parse raw LLM text (JSON, fenced JSON, commentary+JSON, or plain prose) into a DiagnosisResponse."""
    parsed_dict = extract_best_json_dict(raw_text)
    if parsed_dict is not None:
        norm = normalize_diagnosis_dict(
            parsed_dict, default_hint_level=default_hint_level, repo_dir=repo_dir
        )
    else:
        synth = synthesize_diagnosis_from_text(
            raw_text, hint_level=default_hint_level, repo_dir=repo_dir
        )
        norm = normalize_diagnosis_dict(
            synth, default_hint_level=default_hint_level, repo_dir=repo_dir
        )

    raw_fix = norm.pop("raw_suggested_fix", None)
    validated_fix, edit_note = validate_or_extract_structured_edit_with_note(
        fix_data=raw_fix,
        explanation=norm.get("explanation"),
        file_hint=norm.get("file"),
        lines_hint=norm.get("lines"),
        repo_dir=repo_dir,
    )

    diag = DiagnosisResponse(
        summary=norm["summary"],
        file=norm["file"],
        lines=norm["lines"],
        quip=norm["quip"],
        uncertain=norm["uncertain"],
        confidence=norm["confidence"],
        hintLevel=norm["hintLevel"],
        source="model",
        explanation=norm["explanation"],
        citations=norm["citations"],
        suggestedFix=validated_fix,
    )
    return diag, edit_note


def extract_chat_reply_and_edit(
    raw_text: str,
    active_file: Optional[str],
    highlighted_lines: Optional[List[int]],
    latest_diagnosis: Optional[Dict[str, Any]],
    repo_dir: Path,
) -> Tuple[str, List[CodeCitation], Optional[StructuredEdit]]:
    """Extract clean natural-markdown chat reply, grounded file citations, and optional verified StructuredEdit."""
    text = (raw_text or "").strip()
    explicit_fix_dict: Optional[Dict[str, Any]] = None

    # Check if model included an optional <whack_fix>{...}</whack_fix> or ```whack-fix ... ``` block
    tag_match = re.search(r"<whack_fix>([\s\S]*?)</whack_fix>", text, re.IGNORECASE)
    if tag_match:
        explicit_fix_dict = extract_best_json_dict(tag_match.group(1)) or _try_parse_dict(tag_match.group(1).strip())
        text = (text[: tag_match.start()] + text[tag_match.end() :]).strip()

    fence_match = re.search(r"```whack[-_]fix\s*\r?\n([\s\S]*?)```", text, re.IGNORECASE)
    if fence_match and explicit_fix_dict is None:
        explicit_fix_dict = extract_best_json_dict(fence_match.group(1)) or _try_parse_dict(fence_match.group(1).strip())
        text = (text[: fence_match.start()] + text[fence_match.end() :]).strip()

    # Determine file and line hints for structured edit verification
    diag_file = latest_diagnosis.get("file") if isinstance(latest_diagnosis, dict) else None
    file_hint = active_file or diag_file
    lines_hint = normalize_lines(highlighted_lines)
    if lines_hint is None and isinstance(latest_diagnosis, dict):
        lines_hint = normalize_lines(latest_diagnosis.get("lines"))

    refs = extract_file_line_refs_from_text(text, repo_dir=repo_dir)
    if not file_hint and refs:
        file_hint = refs[0][0]
    if not lines_hint and refs and refs[0][1]:
        lines_hint = refs[0][1]

    repo_root = Path(repo_dir).resolve()
    citations: List[CodeCitation] = []
    for ref_file, ref_lines in refs[:5]:
        if ref_lines and (repo_root / ref_file).is_file():
            citations.append(CodeCitation(file=ref_file, lines=ref_lines))

    suggested_fix = validate_or_extract_structured_edit(
        fix_data=explicit_fix_dict,
        explanation=text,
        file_hint=file_hint,
        lines_hint=lines_hint,
        repo_dir=repo_root,
    )

    if not text:
        text = "Let's take another look at that code on the pass, chef."

    return text, citations, suggested_fix
