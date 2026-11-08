"""End-to-end scenario tests through DiagnosticEngine (replacing removed _heuristic_diagnosis)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brain.agent import DiagnosticEngine
from brain.models import DiagnoseRequest, EditorState, TerminalState

engine = DiagnosticEngine()


@pytest.fixture
def scenario_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "scenarios_repo"
    ws.mkdir()
    (ws / "scenarios.py").write_text(
        "def compute_throughput_rate(success_count: int, total_events: int) -> float:\n"
        "    return (success_count / total_events) * 100.0\n\n"
        "async def fetch_remote():\n"
        "    return {'ok': True}\n\n"
        "async def poll_remote_metrics():\n"
        "    data = fetch_remote()\n"
        "    return data['ok']\n\n"
        "def append_audit_entry(entry: str, log: list = []):\n"
        "    log.append(entry)\n"
        "    return log\n",
        encoding="utf-8",
    )
    return ws


def test_engine_has_no_heuristic_diagnosis():
    """Confirm dummy/canned _heuristic_diagnosis was completely deleted."""
    assert not hasattr(engine, "_heuristic_diagnosis")


def test_scenario_zero_division_with_tool_call(
    monkeypatch: pytest.MonkeyPatch, scenario_workspace: Path
):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-scenario-key")

    turn = {"n": 0}

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            turn["n"] += 1
            if turn["n"] == 1:
                return SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(
                            name="read_file_range",
                            args={"rel_path": "scenarios.py", "start_line": 1, "end_line": 5},
                        )
                    ],
                    candidates=[],
                )
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    thought=False,
                                    text=json.dumps({
                                        "summary": "ZeroDivisionError in compute_throughput_rate when total_events is 0",
                                        "file": "scenarios.py",
                                        "lines": [2, 2],
                                        "quip": "Dividing by zero on the pass? Even an empty pot cannot divide itself!",
                                        "uncertain": False,
                                        "confidence": 0.96,
                                        "hintLevel": "full_explanation",
                                        "explanation": "Guard `total_events == 0` before dividing.",
                                        "suggestedFix": {
                                            "file": "scenarios.py",
                                            "line": 2,
                                            "oldText": "return (success_count / total_events) * 100.0",
                                            "newText": "return (success_count / total_events) * 100.0 if total_events else 0.0",
                                        },
                                    }),
                                )
                            ]
                        )
                    )
                ],
            )

    monkeypatch.setattr("google.genai.Client", lambda api_key: SimpleNamespace(models=FakeModels()))

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        repoPath=str(scenario_workspace),
        captureScreenshot=False,
        editorState=EditorState(activeFile="scenarios.py", cursorLine=2),
        terminalState=TerminalState(
            command="pytest",
            exitCode=1,
            tailOutput="ZeroDivisionError: division by zero",
        ),
    )

    diag = asyncio.run(engine.diagnose(req))
    assert diag.source == "model"
    assert diag.file == "scenarios.py"
    assert diag.lines == (2, 2)
    assert diag.suggestedFix is not None
    assert diag.suggestedFix.oldText == "    return (success_count / total_events) * 100.0"
    assert diag.suggestedFix.newText == (
        "    return (success_count / total_events) * 100.0 if total_events else 0.0"
    )


def test_scenario_prose_exhausted_turns_recovery(
    monkeypatch: pytest.MonkeyPatch, scenario_workspace: Path
):
    """Even if the model returns plain markdown after tool inspection, extractor synthesizes cleanly."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-scenario-key")

    prose = (
        "In `scenarios.py:8`, `fetch_remote()` is an unawaited coroutine sitting cold on the counter!\n\n"
        "```python\n"
        "# Before\n"
        "data = fetch_remote()\n\n"
        "# After\n"
        "data = await fetch_remote()\n"
        "```"
    )

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text=prose)])
                    )
                ],
            )

    monkeypatch.setattr("google.genai.Client", lambda api_key: SimpleNamespace(models=FakeModels()))

    req = DiagnoseRequest(
        hintLevel="hint",
        repoPath=str(scenario_workspace),
        captureScreenshot=False,
        terminalState=TerminalState(
            tailOutput="TypeError: 'coroutine' object is not subscriptable",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert diag.source == "model"
    assert diag.file == "scenarios.py"
    assert diag.lines == (8, 8)
    assert diag.suggestedFix is not None
    assert diag.suggestedFix.oldText == "    data = fetch_remote()"
    assert diag.suggestedFix.newText == "    data = await fetch_remote()"


def test_scenario_offline_honest_reporting(
    monkeypatch: pytest.MonkeyPatch, scenario_workspace: Path
):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    req = DiagnoseRequest(
        hintLevel="nudge",
        repoPath=str(scenario_workspace),
        captureScreenshot=False,
        terminalState=TerminalState(tailOutput="AssertionError: mutable default persisted"),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert diag.source == "offline"
    assert diag.uncertain is True
    assert diag.confidence == 0.0
    assert diag.error is not None
    assert diag.file is None
