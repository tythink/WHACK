"""Unit tests for resilient LLM response extraction, normalization, and structured edit alignment."""

from pathlib import Path
from types import SimpleNamespace

from brain.extractor import (
    extract_best_json_dict,
    extract_chat_reply_and_edit,
    extract_response_text,
    normalize_citations,
    normalize_confidence,
    normalize_diagnosis_dict,
    normalize_hint_level,
    normalize_lines,
    normalize_repo_file_path,
    parse_diagnosis_response,
    synthesize_diagnosis_from_text,
    validate_or_extract_structured_edit,
)


def test_extract_response_text_across_sdks():
    # 1. Gemini: skips thought=True parts and concatenates text parts
    gemini_resp = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(thought=True, text="Internal chain-of-thought..."),
                        SimpleNamespace(thought=False, text='{"summary": "Off-by-one", '),
                        SimpleNamespace(thought=False, text='"quip": "It is raw!"}'),
                    ]
                )
            )
        ]
    )
    assert extract_response_text(gemini_resp, "gemini") == '{"summary": "Off-by-one", "quip": "It is raw!"}'

    # 2. OpenAI: choices[0].message.content
    openai_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="  {\"summary\": \"Null deref\"}  "))]
    )
    assert extract_response_text(openai_resp, "openai") == '{"summary": "Null deref"}'

    # 3. Anthropic: content text blocks, skipping thinking and tool_use
    anthropic_resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="Checking lines..."),
            SimpleNamespace(type="tool_use", id="tu_1", name="read_file_range", input={"rel_path": "a.py"}),
            SimpleNamespace(type="text", text='{"summary": "Missing await", "quip": "Cold coroutine!"}'),
        ]
    )
    assert extract_response_text(anthropic_resp, "anthropic") == '{"summary": "Missing await", "quip": "Cold coroutine!"}'


def test_balanced_brace_scanner_ignores_string_braces_and_picks_diagnosis():
    noisy_output = """
Bloody hell, look at this telemetry metadata {"gaze": [500, 500]} before we even plate the dish!

```json
{
  "summary": "Dictionary comprehension bug in format_map {k: v}",
  "file": "utils.py",
  "lines": [34, 36],
  "roast": "This dict literal `{\\"p\\": 0.0}` is rawer than tartare!",
  "confidence": 92,
  "explanation": "Check `{f\\"p{p}\\": 0.0}` handling when empty.",
  "uncertain": false,
}
```
Now get back on the line, chef!
"""
    extracted = extract_best_json_dict(noisy_output)
    assert extracted is not None
    assert "Dictionary comprehension bug" in extracted["summary"]
    assert extracted["file"] == "utils.py"
    assert extracted["lines"] == [34, 36]


def test_prose_fallback_synthesis_when_json_omitted(tmp_path: Path):
    (tmp_path / "utils.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    prose = (
        "This loop boundary in `utils.py:11-12` is completely raw! "
        "You are iterating one index past `total_items - window_size + 1`, which triggers an IndexError."
    )

    synth = synthesize_diagnosis_from_text(prose, hint_level="hint", repo_dir=tmp_path)
    assert synth["file"] == "utils.py"
    assert synth["lines"] == (11, 12)
    assert synth["uncertain"] is False
    assert "raw!" in synth["quip"]

    diag, _ = parse_diagnosis_response(prose, default_hint_level="hint", repo_dir=tmp_path)
    assert diag.source == "model"
    assert diag.file == "utils.py"
    assert diag.lines == (11, 12)
    assert len(diag.citations) >= 1


def test_field_normalizers(tmp_path: Path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    target = sub / "worker.py"
    target.write_text("x = 1\n", encoding="utf-8")

    # lines coercion
    assert normalize_lines([11, 14]) == (11, 14)
    assert normalize_lines([11]) == (11, 11)
    assert normalize_lines(11) == (11, 11)
    assert normalize_lines("11-14") == (11, 14)
    assert normalize_lines("L11") == (11, 11)
    assert normalize_lines([14, 11]) == (11, 14)
    assert normalize_lines(0) is None
    assert normalize_lines([0, 5]) is None
    assert normalize_lines([-3, 2]) is None
    assert normalize_lines("") is None
    assert normalize_lines(True) is None

    # confidence coercion
    assert normalize_confidence(0.85) == 0.85
    assert normalize_confidence(88) == 0.88
    assert normalize_confidence("95%") == 0.95
    assert normalize_confidence(250) == 1.0
    assert normalize_confidence(-5) == 0.0

    # hintLevel coercion
    assert normalize_hint_level("full") == "full_explanation"
    assert normalize_hint_level("BOGUS", default="nudge") == "nudge"

    # file path normalization (file:///, backslashes, absolute inside repo_dir)
    uri = f"file:///{target.as_posix()}"
    assert normalize_repo_file_path(uri, repo_dir=tmp_path) == "pkg/worker.py"
    assert normalize_repo_file_path(r"pkg\worker.py:12", repo_dir=tmp_path) == "pkg/worker.py"

    # citations coercion
    assert normalize_citations(None, repo_dir=tmp_path) == []
    cits = normalize_citations(
        [{"file": r"pkg\worker.py", "line": 7, "snippet": "x = 1"}],
        repo_dir=tmp_path,
    )
    assert len(cits) == 1
    assert cits[0].file == "pkg/worker.py"
    assert cits[0].lines == (7, 7)

    # quip alias mapping
    norm = normalize_diagnosis_dict(
        {"summary": "Bad slice", "ramsayQuip": "Raw slice on the pass!", "confidence": 75},
        default_hint_level="hint",
        repo_dir=tmp_path,
    )
    assert norm["quip"] == "Raw slice on the pass!"
    assert norm["confidence"] == 0.75


def test_structured_edit_exact_and_indentation_aligned_matching(tmp_path: Path):
    code = (
        "def calculate_window_offsets(total_items: int, window_size: int):\n"
        "    offsets = []\n"
        "    for i in range(0, total_items - window_size + 2):\n"
        "        offsets.append((i, i + window_size))\n"
        "    return offsets\n"
    )
    # Write with CRLF to verify CRLF -> LF matching resilience
    (tmp_path / "utils.py").write_bytes(code.replace("\n", "\r\n").encode("utf-8"))

    # 1. Mis-indented (0 leading spaces) + alias keys ('filePath', 'original', 'replacement', 'line')
    fix = validate_or_extract_structured_edit(
        fix_data={
            "filePath": "utils.py",
            "line": 3,
            "original": "for i in range(0, total_items - window_size + 2):\n    offsets.append((i, i + window_size))",
            "replacement": "for i in range(0, total_items - window_size + 1):\n    offsets.append((i, i + window_size))",
        },
        explanation=None,
        file_hint=None,
        lines_hint=None,
        repo_dir=tmp_path,
    )
    assert fix is not None
    assert fix.file == "utils.py"
    assert fix.startLine == 3
    assert fix.endLine == 4
    # Must match the exact 4-space and 8-space indentation in utils.py!
    assert fix.oldText == (
        "    for i in range(0, total_items - window_size + 2):\n"
        "        offsets.append((i, i + window_size))"
    )
    assert fix.newText == (
        "    for i in range(0, total_items - window_size + 1):\n"
        "        offsets.append((i, i + window_size))"
    )

    # 2. Recovery from '# Before' / '# After' block in explanation when fix_data is None
    explanation = (
        "Fix the off-by-one stop value:\n"
        "```python\n"
        "# Before\n"
        "for i in range(0, total_items - window_size + 2):\n\n"
        "# After\n"
        "for i in range(0, total_items - window_size + 1):\n"
        "```"
    )
    recovered = validate_or_extract_structured_edit(
        fix_data=None,
        explanation=explanation,
        file_hint="utils.py",
        lines_hint=(3, 3),
        repo_dir=tmp_path,
    )
    assert recovered is not None
    assert recovered.startLine == 3
    assert recovered.endLine == 3
    assert recovered.oldText == "    for i in range(0, total_items - window_size + 2):"
    assert recovered.newText == "    for i in range(0, total_items - window_size + 1):"

    # 3. Non-matching / hallucinated oldText is rejected (never destructive)
    bad = validate_or_extract_structured_edit(
        fix_data={
            "file": "utils.py",
            "oldText": "    while totally_invented_code():",
            "newText": "    pass",
        },
        explanation=None,
        file_hint="utils.py",
        lines_hint=(3, 3),
        repo_dir=tmp_path,
    )
    assert bad is None

    # 4. Sandbox escape is rejected
    escape = validate_or_extract_structured_edit(
        fix_data={
            "file": "../outside.py",
            "oldText": "x = 1",
            "newText": "x = 2",
        },
        explanation=None,
        file_hint=None,
        lines_hint=None,
        repo_dir=tmp_path,
    )
    assert escape is None


def test_extract_chat_reply_and_whack_fix_tag(tmp_path: Path):
    (tmp_path / "app.py").write_text("def greet():\n    return 'raw'\n", encoding="utf-8")
    raw_chat = (
        "Look at `app.py:2` — returning `'raw'` straight to the dining room!\n\n"
        '<whack_fix>{"file": "app.py", "line": 2, "oldText": "return \'raw\'", "newText": "return \'cooked\'"}</whack_fix>'
    )
    reply, citations, fix = extract_chat_reply_and_edit(
        raw_chat,
        active_file="app.py",
        highlighted_lines=[2],
        latest_diagnosis=None,
        repo_dir=tmp_path,
    )
    assert "<whack_fix>" not in reply
    assert "app.py:2" in reply
    assert len(citations) == 1
    assert citations[0].file == "app.py"
    assert fix is not None
    assert fix.oldText == "    return 'raw'"
    assert fix.newText == "    return 'cooked'"
