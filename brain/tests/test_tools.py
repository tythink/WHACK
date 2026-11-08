"""Unit tests for sandboxed workspace tools, working-tree git diffs, and per-workspace CodeMap caching."""

import subprocess
from pathlib import Path

import pytest

from brain.codemap import get_codemap
from brain.tools import (
    SandboxViolationError,
    execute_tool,
    get_ast_context,
    get_working_diff,
    git_diff,
    git_log,
    list_dir,
    read_file,
    read_file_range,
    search_code,
    search_text,
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


def test_git_diff():
    res = git_diff("HEAD~1", "HEAD")
    assert "diff" in res
    assert "has_changes" in res
    assert isinstance(res["diff"], str)


def test_sandbox_path_traversal_blocked():
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


def test_execute_tool_sandbox_and_dynamic_repo_isolation(tmp_path: Path):
    ws_a = tmp_path / "repo_a"
    ws_b = tmp_path / "repo_b"
    ws_a.mkdir()
    ws_b.mkdir()
    (ws_a / "alpha.py").write_text("def run_alpha():\n    return 42\n", encoding="utf-8")
    (ws_b / "beta.py").write_text("def call_alpha():\n    return run_alpha()\n", encoding="utf-8")

    # Ignored directory inside ws_a should be skipped by CodeMap and search_code
    nm = ws_a / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "ignored.py").write_text("def run_alpha():\n    pass\n", encoding="utf-8")

    res_read = read_file("alpha.py", 1, 10, repo_dir=ws_a)
    assert "run_alpha" in res_read["content"]

    res_search = search_code("run_alpha", repo_dir=ws_a)
    assert res_search["match_count"] == 1
    assert res_search["results"][0]["file"] == "alpha.py"

    # Attempt to escape ws_a into ws_b fails
    escape_res = execute_tool("read_file_range", {"rel_path": "../repo_b/beta.py"}, repo_dir=ws_a)
    assert "error" in escape_res
    assert "Sandbox violation" in escape_res["error"]

    # Attempt by LLM args to override repo_dir is stripped and cannot escape ws_a
    override_res = execute_tool(
        "read_file_range",
        {"rel_path": "beta.py", "repo_dir": str(ws_b)},
        repo_dir=ws_a,
    )
    assert "error" in override_res

    # Per-repo CodeMap cache keeps ws_a and ws_b isolated
    cmap_a = get_codemap(ws_a)
    cmap_b = get_codemap(ws_b)
    assert cmap_a is not cmap_b
    assert "alpha.py" in cmap_a.files and "beta.py" not in cmap_a.files
    assert "beta.py" in cmap_b.files and "alpha.py" not in cmap_b.files

    ast_ctx = get_ast_context("beta.py", 2, repo_dir=ws_b)
    assert ast_ctx["symbol"]["name"] == "call_alpha"
    assert "run_alpha" in ast_ctx["callees"]


def test_working_tree_and_commit_git_diff(tmp_path: Path):
    repo = tmp_path / "git_ws"
    repo.mkdir()
    f = repo / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@whack.local"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=str(repo), check=True, capture_output=True)

    f.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c2 bug"], cwd=str(repo), check=True, capture_output=True)

    # Clean working tree -> get_working_diff falls back to last commit HEAD~1..HEAD
    clean_bundle = get_working_diff(repo)
    assert clean_bundle["source"] == "last_commit"
    assert "return a - b" in clean_bundle["diff"]

    # Uncommitted edit + untracked file -> get_working_diff surfaces working_tree diff
    f.write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
    (repo / "new_helper.py").write_text("x = 1\n", encoding="utf-8")

    dirty_diff = git_diff(base_ref="WORKING", repo_dir=repo)
    assert dirty_diff["has_changes"] is True
    assert "return a * b" in dirty_diff["diff"]

    dirty_bundle = get_working_diff(repo)
    assert dirty_bundle["has_uncommitted"] is True
    assert dirty_bundle["source"] == "working_tree"
    assert "return a * b" in dirty_bundle["working_diff"]
    assert "new_helper.py" in dirty_bundle["untracked_files"]
