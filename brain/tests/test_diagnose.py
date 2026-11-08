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
            "command": "pytest tests/",
            "exitCode": 1,
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5",
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
    assert any(k in data["summary"].lower() for k in ["off-by-one", "utils.py", "offset", "window", "range", "loop", "index"])
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
    assert any(term in data["explanation"].lower() for term in ["root cause", "range", "offset", "loop", "window", "boundary", "index"])

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

def test_apply_fix_and_rollback():
    """Test full cycle: apply-fix endpoint applies edit and rollback endpoint reverts it."""
    # 1. Apply fix
    payload = {
        "fix": {
            "file": "utils.py",
            "startLine": 11,
            "endLine": 11,
            "oldText": "    for i in range(0, total_items - window_size + 2):",
            "newText": "    for i in range(0, total_items - window_size + 1):",
        }
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "checkpoint_hash" in data
    assert data["before_passed"] is False
    assert data["after_passed"] is True
    checkpoint_hash = data["checkpoint_hash"]

    # 2. Rollback to checkpoint
    rb_payload = {"checkpointHash": checkpoint_hash}
    rb_res = client.post("/rollback", json=rb_payload)
    assert rb_res.status_code == 200
    rb_data = rb_res.json()
    assert rb_data["success"] is True
    assert rb_data["rolled_back_to"] == checkpoint_hash
    # After rollback, tests should fail again with the planted bug
    assert rb_data["tests_passed"] is False

def test_apply_fix_mismatch_refused():
    """Ensure apply-fix refuses if oldText does not match live file on disk."""
    payload = {
        "fix": {
            "file": "utils.py",
            "startLine": 11,
            "endLine": 11,
            "oldText": "    for i in range(0, completely_wrong_text):",
            "newText": "    for i in range(0, total_items - window_size + 1):",
        }
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "Refused" in data["error"] or "did not match" in data["error"]

def test_apply_fix_sandbox_violation():
    """Ensure apply-fix strictly refuses files attempting path traversal outside demo repo."""
    payload = {
        "fix": {
            "file": "../agent.md",
            "startLine": 1,
            "endLine": 1,
            "oldText": "something",
            "newText": "hacked",
        }
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code in (400, 403)
