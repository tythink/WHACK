"""Automated verification suite for Code Comprehension Confusion vs Error Confusion (R2, R3)."""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any

import pytest

from brain.agent import engine
from brain.context import is_code_comprehension_request
from brain.models import DiagnoseRequest, EditorState, TerminalState


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    utils_code = (
        "def calculate_window_offsets(total_items: int, window_size: int):\n"
        "    offsets = []\n"
        "    if total_items <= 0 or window_size <= 0:\n"
        "        return offsets\n"
        "    for i in range(0, total_items - window_size + 1):\n"
        "        offsets.append((i, i + window_size))\n"
        "    return offsets\n\n"
        "def batch_slice(data, offsets):\n"
        "    out = []\n"
        "    for start, end in offsets:\n"
        "        if end > len(data):\n"
        "            raise IndexError(f'Offset end {end} exceeds data length {len(data)}')\n"
        "        out.append(data[start:end])\n"
        "    return out\n"
    )
    (repo_dir / "utils.py").write_text(utils_code, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "chef@whack.local"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Chef Ramsay"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True, capture_output=True)
    return repo_dir


def test_is_code_comprehension_request_classification():
    """Verify is_code_comprehension_request across explicit and implicit variations."""
    # 1. Explicit status strings
    assert is_code_comprehension_request(
        DiagnoseRequest(userStatus="code_comprehension_confusion")
    ) is True
    assert is_code_comprehension_request(
        DiagnoseRequest(userStatus="confusion_code_comprehension")
    ) is True
    assert is_code_comprehension_request(
        DiagnoseRequest(userStatus="error_confusion")
    ) is False
    assert is_code_comprehension_request(
        DiagnoseRequest(userStatus="confusion_error")
    ) is False

    # 2. Confusion with clean environment (zero diagnostics, exit code 0 or None) -> True
    req_clean_conf = DiagnoseRequest(
        userStatus="confused_thinking",
        editorState=EditorState(diagnosticsCount=0, diagnosticsSummary=""),
        terminalState=TerminalState(exitCode=0),
    )
    assert is_code_comprehension_request(req_clean_conf) is True

    # 3. Confusion via emotions with zero diagnostics -> True
    req_emo_conf = DiagnoseRequest(
        userStatus="stuck_frozen_at_caret",
        emotions={"confused": 0.85, "flow": 0.10},
        editorState=EditorState(diagnosticsCount=0, diagnosticsSummary=""),
        terminalState=TerminalState(exitCode=None),
    )
    assert is_code_comprehension_request(req_emo_conf) is True

    # 4. Confusion with active diagnostics -> False (Error Confusion)
    req_diag_err = DiagnoseRequest(
        userStatus="confused_thinking",
        editorState=EditorState(diagnosticsCount=2, diagnosticsSummary="TypeError on line 14"),
        terminalState=TerminalState(exitCode=0),
    )
    assert is_code_comprehension_request(req_diag_err) is False

    # 5. Confusion with failing terminal run -> False (Error Confusion)
    req_term_err = DiagnoseRequest(
        userStatus="confused_thinking",
        editorState=EditorState(diagnosticsCount=0, diagnosticsSummary=""),
        terminalState=TerminalState(exitCode=1, tailOutput="AssertionError: test failed"),
    )
    assert is_code_comprehension_request(req_term_err) is False

    # 6. Focused flow without confusion -> False
    req_flow = DiagnoseRequest(
        userStatus="focused_flow",
        emotions={"flow": 0.90, "confused": 0.05},
        editorState=EditorState(diagnosticsCount=0),
    )
    assert is_code_comprehension_request(req_flow) is False


def test_code_comprehension_confusion_walkthrough_response(
    monkeypatch: pytest.MonkeyPatch, temp_workspace: Path
):
    """Verify code comprehension requests return conceptual walkthrough without bug assertion and suggestedFix is None."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-comprehension-key")

    mock_response = {
        "summary": "Walkthrough of calculate_window_offsets: generates sliding window index tuples",
        "file": "utils.py",
        "lines": [1, 7],
        "quip": "Right chef! Let's break down this recipe step-by-step so you understand the flow.",
        "uncertain": False,
        "confidence": 0.95,
        "hintLevel": "hint",
        "explanation": "This function takes `total_items` and `window_size` to generate sliding window start/end index tuples.",
        "citations": [
            {
                "file": "utils.py",
                "lines": [1, 7],
                "snippet": "def calculate_window_offsets(total_items: int, window_size: int):",
                "note": "Main sliding window offset generator",
            }
        ],
        # Even if the underlying model outputs a suggestedFix, the comprehension engine MUST enforce suggestedFix is None
        "suggestedFix": {
            "file": "utils.py",
            "startLine": 5,
            "endLine": 5,
            "oldText": "    for i in range(0, total_items - window_size + 1):",
            "newText": "    for i in range(0, total_items - window_size + 1):",
        },
    }

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    thought=False,
                                    text=json.dumps(mock_response),
                                )
                            ]
                        )
                    )
                ],
            )

    monkeypatch.setattr(
        "google.genai.Client", lambda api_key: SimpleNamespace(models=FakeModels())
    )

    req = DiagnoseRequest(
        hintLevel="hint",
        repoPath=str(temp_workspace),
        userStatus="code_comprehension_confusion",
        editorState=EditorState(activeFile="utils.py", cursorLine=3, diagnosticsCount=0),
        terminalState=TerminalState(exitCode=0),
        emotions={"confused": 0.78, "flow": 0.12},
    )

    diag = asyncio.run(engine.diagnose(req))

    assert diag.source == "model"
    assert diag.file == "utils.py"
    assert diag.lines is not None
    # Crucial acceptance criteria: suggestedFix MUST be None for code comprehension confusion
    assert diag.suggestedFix is None, f"Expected suggestedFix to be None in comprehension mode, got {diag.suggestedFix}"
    assert "calculate_window_offsets" in diag.summary
    assert "bug" not in diag.summary.lower() and "defect" not in diag.summary.lower()
    assert diag.uncertain is False


def test_error_confusion_diagnosis_and_fix_response(
    monkeypatch: pytest.MonkeyPatch, temp_workspace: Path
):
    """Verify error confusion requests return root cause diagnosis with suggestedFix populated."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-error-key")

    mock_err_response = {
        "summary": "IndexError in batch_slice when slice offset end exceeds data buffer length",
        "file": "utils.py",
        "lines": [12, 12],
        "quip": "An offset end past the array length? You can't plate an entrée on an imaginary table!",
        "uncertain": False,
        "confidence": 0.98,
        "hintLevel": "full_explanation",
        "explanation": "Clamp slice end index to len(data) to prevent raising IndexError.",
        "citations": [
            {
                "file": "utils.py",
                "lines": [12, 12],
                "snippet": "            raise IndexError(f'Offset end {end} exceeds data length {len(data)}')",
                "note": "Root cause exception location",
            }
        ],
        "suggestedFix": {
            "file": "utils.py",
            "startLine": 12,
            "endLine": 12,
            "oldText": "            raise IndexError(f'Offset end {end} exceeds data length {len(data)}')",
            "newText": "            end = min(end, len(data))",
        },
    }

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    thought=False,
                                    text=json.dumps(mock_err_response),
                                )
                            ]
                        )
                    )
                ],
            )

    monkeypatch.setattr(
        "google.genai.Client", lambda api_key: SimpleNamespace(models=FakeModels())
    )

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        repoPath=str(temp_workspace),
        userStatus="error_confusion",
        editorState=EditorState(
            activeFile="utils.py",
            cursorLine=12,
            diagnosticsCount=1,
            diagnosticsSummary="IndexError on line 12",
        ),
        terminalState=TerminalState(
            command="pytest",
            exitCode=1,
            tailOutput="IndexError: Offset end 6 exceeds data length 5",
        ),
        emotions={"confused": 0.82, "frustrated": 0.40},
    )

    diag = asyncio.run(engine.diagnose(req))

    assert diag.source == "model"
    assert diag.file == "utils.py"
    assert diag.lines == (12, 12)
    assert diag.suggestedFix is not None
    assert diag.suggestedFix.file == "utils.py"
    assert diag.suggestedFix.startLine == 12
    assert "IndexError" in diag.summary
