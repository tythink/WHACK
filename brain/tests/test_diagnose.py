"""Unit tests for Brain diagnosis engine and FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from brain.server import app
from brain.models import DiagnoseRequest, EditorState, TerminalState

client = TestClient(app)

def test_health_endpoint():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["service"] == "brain"
    assert "utils.py" or "available_tools" in data

def test_stub_diagnose():
    """Verify stub phase requirements from AGENT_ROLE3_BRAIN.md."""
    res = client.get("/diagnose")
    assert res.status_code == 200
    data = res.json()
    assert "Index error" in data["summary"]
    assert data["file"] == "utils.py"
    assert data["confidence"] == 0.95
    assert "quip" in data

def test_post_diagnose_nudge():
    payload = {
        "hintLevel": "nudge",
        "editorState": {"activeFile": "utils.py", "cursorLine": 18},
        "terminalState": {
            "command": "pytest demo-repo/tests",
            "exitCode": 1,
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5 (line 30)",
        },
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["file"] == "utils.py"
    assert data["hintLevel"] == "nudge"
    assert data["uncertain"] is False
    assert len(data["lines"]) == 2

def test_post_diagnose_hint():
    payload = {
        "hintLevel": "hint",
        "editorState": {"activeFile": "utils.py"},
        "terminalState": {
            "command": "pytest demo-repo/tests",
            "exitCode": 1,
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5",
        },
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "Off-by-one" in data["summary"] or "utils.py" in data["summary"]
    assert data["hintLevel"] == "hint"

def test_post_diagnose_full_explanation():
    payload = {
        "hintLevel": "full_explanation",
        "terminalState": {
            "tailOutput": "IndexError: list index out of range in batch_slice",
        },
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["hintLevel"] == "full_explanation"
    assert "Root Cause" in data["explanation"] or "for i in range" in data["explanation"]

def test_post_diagnose_streaming_sse():
    payload = {
        "hintLevel": "nudge",
        "stream": True,
        "terminalState": {
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5",
        },
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    text = res.text
    assert "event: start" in text
    assert "event: diagnosis" in text
    assert "event: done" in text
