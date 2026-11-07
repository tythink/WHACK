"""Unit tests for sandboxed repository inspection tools."""

import pytest
from brain.tools import (
    list_dir,
    read_file_range,
    search_text,
    git_log,
    git_diff,
    execute_tool,
    SandboxViolationError,
)

def test_list_dir():
    res = list_dir("")
    assert "entries" in res
    assert res["total_entries"] > 0
    names = [e["name"] for e in res["entries"]]
    assert "utils.py" in names
    assert "pipeline.py" in names

def test_read_file_range():
    res = read_file_range("utils.py", start_line=1, end_line=15)
    assert res["path"] == "utils.py"
    assert res["start_line"] == 1
    assert res["end_line"] == 15
    assert "calculate_window_offsets" in res["content"]

def test_search_text():
    res = search_text("calculate_window_offsets", "")
    assert res["match_count"] >= 1
    assert any(m["file"] == "utils.py" for m in res["results"])

def test_git_log():
    res = git_log(3)
    assert "commits" in res
    assert len(res["commits"]) >= 1

def test_git_diff():
    res = git_diff("HEAD~1", "HEAD")
    assert "diff" in res
    assert res["has_changes"] is True
    assert "range" in res["diff"]

def test_sandbox_path_traversal_blocked():
    """Ensure attempts to break out of demo-repo/ fail with SandboxViolationError."""
    dangerous_paths = [
        "../agent.md",
        "../../etc/passwd",
        "/etc/passwd",
        "/System/Library",
        "../face",
        "../core",
    ]
    for path in dangerous_paths:
        with pytest.raises(SandboxViolationError):
            read_file_range(path)

def test_execute_tool_sandbox():
    res = execute_tool("read_file_range", {"rel_path": "../README.md"})
    assert "error" in res
    assert "Sandbox violation" in res["error"]
