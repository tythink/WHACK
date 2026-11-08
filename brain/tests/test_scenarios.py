"""Validation test suite for 8 developer stuck scenarios evaluated against realistic demo code."""

import sys
from pathlib import Path

from brain.config import DEMO_REPO_DIR

# Add demo repository (generated path outside repo) to sys.path for direct module import
if str(DEMO_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_REPO_DIR))

import pytest
import asyncio
import subprocess
import importlib
from brain.models import DiagnoseRequest, EditorState, TerminalState
from brain.agent import DiagnosticEngine
import scenarios

engine = DiagnosticEngine()

def switch_branch(branch: str):
    subprocess.run(
        ["git", "checkout", branch],
        cwd=str(DEMO_REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    importlib.reload(scenarios)

@pytest.fixture(autouse=True)
def ensure_main_branch():
    yield
    switch_branch("main")

def test_scenario_1_off_by_one():
    switch_branch("scenario-1")
    with pytest.raises(IndexError) as exc:
        scenarios.calculate_window_slices()
    
    req = DiagnoseRequest(
        hintLevel="hint",
        editorState=EditorState(activeFile="utils.py", cursorLine=18),
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput=str(exc.value),
        ),
    )
    diag = engine._heuristic_diagnosis(req, "hint")
    assert diag.file == "utils.py"
    assert diag.lines == (10, 15)
    assert diag.uncertain is False
    assert "Off-by-one" in diag.summary or "offset" in diag.summary.lower()

def test_scenario_3_none_dereference():
    switch_branch("scenario-3")
    with pytest.raises(TypeError) as exc:
        scenarios.extract_event_timestamp()

    req = DiagnoseRequest(
        hintLevel="nudge",
        editorState=EditorState(activeFile="scenarios.py"),
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: 'NoneType' object is not subscriptable",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "nudge")
    assert diag.uncertain is False
    assert "None" in diag.summary or "None" in diag.quip

def test_scenario_6_zero_division():
    switch_branch("scenario-6")
    with pytest.raises(ZeroDivisionError) as exc:
        scenarios.compute_throughput_rate()

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="ZeroDivisionError: division by zero",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "full_explanation")
    assert diag.uncertain is False
    assert "Zero" in diag.summary or "division by zero" in diag.explanation.lower()

def test_scenario_5_unawaited():
    switch_branch("scenario-5")
    with pytest.raises(TypeError) as exc:
        scenarios.poll_remote_metrics()

    req = DiagnoseRequest(
        hintLevel="hint",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: 'coroutine' object is not subscriptable\nRuntimeWarning: coroutine was never awaited",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "hint")
    assert "coroutine" in diag.summary.lower() or "await" in diag.summary.lower()

def test_scenario_7_regex():
    switch_branch("scenario-7")
    with pytest.raises(AttributeError) as exc:
        scenarios.parse_status_code()

    req = DiagnoseRequest(
        hintLevel="hint",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="AttributeError: 'NoneType' object has no attribute 'group'",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "hint")
    assert "regex" in diag.summary.lower() or "group" in diag.summary.lower()

def test_scenario_8_type_mismatch():
    switch_branch("scenario-8")
    with pytest.raises(TypeError) as exc:
        scenarios.check_priority_threshold()

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: '>' not supported between instances of 'str' and 'int'",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "full_explanation")
    assert "type mismatch" in diag.summary.lower() or "string" in diag.summary.lower()

def test_scenario_4_mutable_default():
    switch_branch("scenario-4")
    first = scenarios.append_audit_entry("first")
    second = scenarios.append_audit_entry("second")
    assert len(second) == 2  # State persisted across invocations

    req = DiagnoseRequest(
        hintLevel="nudge",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="AssertionError: mutable default argument persisted elements across calls",
        ),
    )
    diag = engine._heuristic_diagnosis(req, "nudge")
    assert "mutable" in diag.summary.lower()
