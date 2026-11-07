"""Validation test suite for 8 developer stuck scenarios evaluated against realistic demo code."""

import sys
from pathlib import Path

from brain.config import DEMO_REPO_DIR

# Add demo repository (generated path outside repo) to sys.path for direct module import
if str(DEMO_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_REPO_DIR))

import pytest
import asyncio
from brain.models import DiagnoseRequest, EditorState, TerminalState
from brain.agent import DiagnosticEngine
from scenarios import (
    calculate_window_slices,
    accumulate_metrics,
    extract_event_timestamp,
    append_audit_entry,
    poll_remote_metrics,
    compute_throughput_rate,
    parse_status_code,
    check_priority_threshold,
)

engine = DiagnosticEngine()

def test_scenario_1_off_by_one():
    with pytest.raises(IndexError) as exc:
        calculate_window_slices()
    
    req = DiagnoseRequest(
        hintLevel="hint",
        editorState=EditorState(activeFile="utils.py", cursorLine=18),
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput=str(exc.value),
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert diag.file == "utils.py"
    assert diag.lines == (16, 22)
    assert diag.uncertain is False
    assert "Off-by-one" in diag.summary or "offset" in diag.summary.lower()

def test_scenario_3_none_dereference():
    with pytest.raises(TypeError) as exc:
        extract_event_timestamp()

    req = DiagnoseRequest(
        hintLevel="nudge",
        editorState=EditorState(activeFile="scenarios.py"),
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: 'NoneType' object is not subscriptable",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert diag.uncertain is False
    assert "None" in diag.summary or "None" in diag.quip

def test_scenario_6_zero_division():
    with pytest.raises(ZeroDivisionError) as exc:
        compute_throughput_rate()

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="ZeroDivisionError: division by zero",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert diag.uncertain is False
    assert "Zero" in diag.summary or "division by zero" in diag.explanation.lower()

def test_scenario_5_unawaited():
    with pytest.raises(TypeError) as exc:
        poll_remote_metrics()

    req = DiagnoseRequest(
        hintLevel="hint",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: 'coroutine' object is not subscriptable\nRuntimeWarning: coroutine was never awaited",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert "coroutine" in diag.summary.lower() or "await" in diag.summary.lower()

def test_scenario_7_regex():
    with pytest.raises(AttributeError) as exc:
        parse_status_code()

    req = DiagnoseRequest(
        hintLevel="hint",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="AttributeError: 'NoneType' object has no attribute 'group'",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert "regex" in diag.summary.lower() or "group" in diag.summary.lower()

def test_scenario_8_type_mismatch():
    with pytest.raises(TypeError) as exc:
        check_priority_threshold()

    req = DiagnoseRequest(
        hintLevel="full_explanation",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="TypeError: '>' not supported between instances of 'str' and 'int'",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert "type mismatch" in diag.summary.lower() or "string" in diag.summary.lower()

def test_scenario_4_mutable_default():
    first = append_audit_entry("first")
    second = append_audit_entry("second")
    assert len(second) == 2  # State persisted across invocations

    req = DiagnoseRequest(
        hintLevel="nudge",
        terminalState=TerminalState(
            command="python -m scenarios",
            exitCode=1,
            tailOutput="AssertionError: mutable default argument persisted elements across calls",
        ),
    )
    diag = asyncio.run(engine.diagnose(req))
    assert "mutable" in diag.summary.lower()
