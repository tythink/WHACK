"""Sandboxed read-only workspace inspection and checkpointed fix tools for Role 3: Brain.

All tool operations are strictly sandboxed to the active target workspace (`repo_dir`).
Any attempt to traverse outside the sandboxed workspace raises `SandboxViolationError`.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from brain.config import resolve_repo_dir
from brain.codemap import IGNORED_DIRS

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".zip", ".tar", ".gz", ".7z", ".pdf", ".woff", ".woff2", ".ttf",
    ".mp3", ".mp4", ".mov", ".avi", ".wasm", ".sqlite", ".db",
}


class SandboxViolationError(PermissionError):
    """Raised when a path traversal attempt outside the target workspace is detected."""
    pass


def _resolve_target_repo(repo_dir: Optional[Path] = None) -> Path:
    if repo_dir is not None:
        return Path(repo_dir).expanduser().resolve()
    return resolve_repo_dir()


def _resolve_safe_path(rel_path: str = "", repo_dir: Optional[Path] = None) -> Path:
    """Resolve a path relative to target_repo, verifying it does not escape."""
    target_repo = _resolve_target_repo(repo_dir)
    cleaned = str(rel_path or "").strip()
    raw = Path(cleaned) if cleaned else Path(".")

    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (target_repo / raw).resolve()

    try:
        target.relative_to(target_repo)
    except ValueError:
        raise SandboxViolationError(
            f"Access denied: '{rel_path}' escapes sandboxed workspace boundary ({target_repo})."
        )

    return target


def _get_git_context(
    target_repo: Path, target_path: Path, rel_path: str = ""
) -> Optional[Tuple[Path, List[str]]]:
    """Determine (git_cwd, pathspec_args) for a standalone git repo or monorepo subdirectory."""
    if (target_repo / ".git").exists():
        pathspec = [target_path.relative_to(target_repo).as_posix()] if rel_path.strip() else []
        return target_repo, pathspec

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(target_repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            git_root = Path(res.stdout.strip()).resolve()
            scope = target_path if rel_path.strip() else target_repo
            rel_scope = scope.relative_to(git_root).as_posix()
            return git_root, ([rel_scope] if rel_scope and rel_scope != "." else [])
    except Exception:
        pass

    return None


def list_dir(rel_path: str = "", repo_dir: Optional[Path] = None) -> Dict[str, Any]:
    """List directory contents within target workspace in a read-only sandboxed manner."""
    target_repo = _resolve_target_repo(repo_dir)
    target_dir = _resolve_safe_path(rel_path, repo_dir=target_repo)
    if not target_dir.exists():
        return {"error": f"Directory not found: {rel_path}"}
    if not target_dir.is_dir():
        return {"error": f"Path is a file, not a directory: {rel_path}"}

    entries = []
    try:
        for entry in sorted(target_dir.iterdir()):
            if entry.name in ("__pycache__", ".git", "node_modules", ".pytest_cache"):
                continue
            is_file = entry.is_file()
            rel_entry_path = entry.relative_to(target_repo).as_posix()
            item: Dict[str, Any] = {
                "name": entry.name,
                "path": rel_entry_path,
                "type": "file" if is_file else "directory",
            }
            if is_file:
                try:
                    st = entry.stat()
                    item["size_bytes"] = st.st_size
                    if st.st_size <= 512_000 and entry.suffix.lower() not in BINARY_EXTENSIONS:
                        with open(entry, "r", encoding="utf-8", errors="ignore") as f:
                            item["line_count"] = sum(1 for _ in f)
                except Exception:
                    pass
            entries.append(item)
        rel_dir = target_dir.relative_to(target_repo).as_posix()
        return {
            "directory": rel_dir,
            "entries": entries,
            "total_entries": len(entries),
        }
    except Exception as e:
        return {"error": f"Failed to list directory {rel_path}: {str(e)}"}


def read_file_range(
    rel_path: str,
    start_line: int = 1,
    end_line: int = 100,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Read a specific line range (1-indexed, inclusive) from a file within target workspace."""
    target_repo = _resolve_target_repo(repo_dir)
    target_file = _resolve_safe_path(rel_path, repo_dir=target_repo)
    if not target_file.exists():
        return {"error": f"File not found: {rel_path}"}
    if not target_file.is_file():
        return {"error": f"Path is not a regular file: {rel_path}"}

    start_line = max(1, int(start_line or 1))
    end_line = max(start_line, int(end_line or (start_line + 99)))

    try:
        with open(target_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        total_lines = len(all_lines)

        clamped_start = min(start_line, max(1, total_lines))
        clamped_end = min(end_line, total_lines)

        selected = all_lines[clamped_start - 1 : clamped_end]
        numbered_lines = [
            f"{clamped_start + i}: {line.rstrip()}" for i, line in enumerate(selected)
        ]

        return {
            "path": target_file.relative_to(target_repo).as_posix(),
            "start_line": clamped_start,
            "end_line": clamped_end,
            "total_lines": total_lines,
            "content": "\n".join(numbered_lines),
        }
    except Exception as e:
        return {"error": f"Failed to read file {rel_path}: {str(e)}"}


# Alias for read_file callers
read_file = read_file_range


def search_text(
    query: str,
    rel_path: str = "",
    max_results: int = 25,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Search for substring/regex across text files inside target workspace."""
    target_repo = _resolve_target_repo(repo_dir)
    target_root = _resolve_safe_path(rel_path, repo_dir=target_repo)
    if not target_root.exists():
        return {"error": f"Search path does not exist: {rel_path}"}

    results: List[Dict[str, Any]] = []
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    candidate_files: List[Path] = []
    if target_root.is_file():
        candidate_files.append(target_root)
    else:
        for root, dirs, files in os.walk(target_root):
            dirs[:] = sorted(
                d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")
            )
            root_p = Path(root)
            for fname in sorted(files):
                fp = root_p / fname
                if fp.suffix.lower() in BINARY_EXTENSIONS:
                    continue
                candidate_files.append(fp)

    for file_path in candidate_files:
        try:
            if file_path.stat().st_size > 1_000_000:
                continue
            rel_file_str = file_path.relative_to(target_repo).as_posix()
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, start=1):
                    if pattern.search(line):
                        results.append({
                            "file": rel_file_str,
                            "line": line_num,
                            "content": line.strip()[:300],
                        })
                        if len(results) >= max_results:
                            break
            if len(results) >= max_results:
                break
        except Exception:
            continue

    return {
        "query": query,
        "results": results,
        "match_count": len(results),
        "truncated": len(results) >= max_results,
    }


# Alias for search_code callers
search_code = search_text


def git_log(
    n: int = 5,
    rel_path: str = "",
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve git log entries scoped to target workspace."""
    n = max(1, min(int(n or 5), 50))
    target_repo = _resolve_target_repo(repo_dir)
    target_path = _resolve_safe_path(rel_path, repo_dir=target_repo)

    git_ctx = _get_git_context(target_repo, target_path, rel_path)
    if git_ctx is None:
        return {"commits": [], "count": 0, "error": "Not a git repository"}

    cwd, pathspec = git_ctx
    cmd = ["git", "log", f"-n{n}", "--format=%H|%an|%ad|%s", "--date=short"]
    if pathspec:
        cmd.extend(["--"] + pathspec)

    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"commits": [], "count": 0, "error": f"git log failed: {result.stderr.strip()}"}

        commits = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "commit": parts[0][:8],
                    "full_hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return {"commits": commits, "count": len(commits)}
    except Exception as e:
        return {"commits": [], "count": 0, "error": f"git log failed: {str(e)}"}


def git_diff(
    commit_a: Optional[str] = "HEAD~1",
    commit_b: Optional[str] = "HEAD",
    rel_path: str = "",
    repo_dir: Optional[Path] = None,
    base_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve git diff between commits or against uncommitted working tree, scoped to target workspace."""
    target_repo = _resolve_target_repo(repo_dir)
    target_path = _resolve_safe_path(rel_path, repo_dir=target_repo)

    git_ctx = _get_git_context(target_repo, target_path, rel_path)
    if git_ctx is None:
        return {
            "commit_a": commit_a,
            "commit_b": commit_b,
            "diff": "",
            "has_changes": False,
            "error": "Not a git repository",
        }

    cwd, pathspec = git_ctx
    effective_base = (base_ref or commit_a or "").strip()
    effective_target = (commit_b or "").strip() if commit_b is not None else ""

    wants_working = (
        effective_base.upper() == "WORKING"
        or effective_target.upper() == "WORKING"
        or (effective_base == "HEAD" and not effective_target)
        or (not effective_base and not effective_target)
    )

    try:
        if wants_working:
            cmd = ["git", "diff", "HEAD"]
            if pathspec:
                cmd.extend(["--"] + pathspec)
            res = subprocess.run(
                cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            if res.returncode != 0:
                # Fallback for unborn HEAD
                fallback_cmd = ["git", "diff"] + (["--"] + pathspec if pathspec else [])
                res = subprocess.run(
                    fallback_cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
                )
            diff_output = res.stdout if res.returncode == 0 else ""
            return {
                "commit_a": "HEAD",
                "commit_b": "WORKING",
                "diff": diff_output,
                "has_changes": bool(diff_output.strip()),
            }

        cmd = ["git", "diff"]
        if effective_base and effective_target:
            cmd.extend([effective_base, effective_target])
        elif effective_base:
            cmd.append(effective_base)
        if pathspec:
            cmd.extend(["--"] + pathspec)

        result = subprocess.run(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )

        diff_output = result.stdout if result.returncode == 0 else ""

        # If HEAD~1 failed or produced empty diff in a standalone repo, check baseline tag if available
        if effective_base == "HEAD~1" and not diff_output.strip():
            alt_cmd = ["git", "diff", "baseline"]
            if pathspec:
                alt_cmd.extend(["--"] + pathspec)
            alt_res = subprocess.run(
                alt_cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            if alt_res.returncode == 0 and alt_res.stdout.strip():
                diff_output = alt_res.stdout
                commit_a = "baseline"
            elif result.returncode != 0:
                return {
                    "commit_a": commit_a,
                    "commit_b": commit_b,
                    "diff": "",
                    "has_changes": False,
                    "error": f"git diff failed: {result.stderr.strip()}",
                }

        return {
            "commit_a": commit_a,
            "commit_b": commit_b,
            "diff": diff_output,
            "has_changes": bool(diff_output.strip()),
        }
    except Exception as e:
        return {"error": f"git diff failed: {str(e)}", "diff": "", "has_changes": False}


def get_working_diff(repo_dir: Optional[Path] = None, rel_path: str = "") -> Dict[str, Any]:
    """Combine uncommitted working-tree diff (plus untracked/unstaged summary) and HEAD~1..HEAD diff."""
    target_repo = _resolve_target_repo(repo_dir)
    target_path = _resolve_safe_path(rel_path, repo_dir=target_repo)
    git_ctx = _get_git_context(target_repo, target_path, rel_path)

    if git_ctx is None:
        return {
            "working_diff": "",
            "recent_commit_diff": "",
            "untracked_files": [],
            "status_summary": "",
            "diff": "",
            "has_uncommitted": False,
            "has_changes": False,
            "source": "no-git",
        }

    cwd, pathspec = git_ctx
    untracked_files: List[str] = []
    status_lines: List[str] = []
    try:
        st_cmd = ["git", "status", "--porcelain"]
        if pathspec:
            st_cmd.extend(["--"] + pathspec)
        st_res = subprocess.run(
            st_cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4
        )
        if st_res.returncode == 0:
            for line in st_res.stdout.splitlines():
                if not line.strip():
                    continue
                status_lines.append(line)
                if line.startswith("?? "):
                    untracked_files.append(line[3:].strip())
    except Exception:
        pass

    working_res = git_diff(base_ref="WORKING", rel_path=rel_path, repo_dir=target_repo)
    working_diff = (working_res.get("diff") or "").strip()

    commit_res = git_diff("HEAD~1", "HEAD", rel_path=rel_path, repo_dir=target_repo)
    commit_diff = (commit_res.get("diff") or "").strip()

    has_uncommitted = bool(working_diff or untracked_files or status_lines)
    status_summary = "\n".join(status_lines[:30])

    combined_sections: List[str] = []
    if status_summary:
        combined_sections.append(f"# Git Status Summary:\n{status_summary}")
    if working_diff:
        combined_sections.append(f"# Uncommitted Working Tree Diff (git diff HEAD):\n{working_diff}")
    elif untracked_files:
        combined_sections.append(f"# Untracked Files:\n" + "\n".join(f"- {u}" for u in untracked_files[:20]))

    if not has_uncommitted and commit_diff:
        combined_sections.append(f"# Last Commit Diff (HEAD~1..HEAD):\n{commit_diff}")
    elif has_uncommitted and commit_diff:
        combined_sections.append(f"# Previous Commit Diff (HEAD~1..HEAD):\n{commit_diff}")

    combined_diff = "\n\n".join(combined_sections).strip()
    source = "working_tree" if has_uncommitted else ("last_commit" if commit_diff else "clean")

    return {
        "working_diff": working_diff,
        "recent_commit_diff": commit_diff,
        "untracked_files": untracked_files,
        "status_summary": status_summary,
        "diff": combined_diff,
        "has_uncommitted": has_uncommitted,
        "has_changes": bool(combined_diff),
        "source": source,
    }


def get_ast_context(
    file: str, line: int = 1, repo_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Retrieve enclosing AST symbol, callers, and callees for a file and line in target workspace."""
    from brain.codemap import enclosing_symbol, find_callers, find_callees

    target_repo = _resolve_target_repo(repo_dir)
    _resolve_safe_path(file, repo_dir=target_repo)

    enc = enclosing_symbol(file, int(line or 1), repo_dir=target_repo)
    if not enc:
        return {"symbol": None, "callers": [], "callees": []}

    sym_name = enc["name"]
    return {
        "symbol": enc,
        "callers": find_callers(sym_name, repo_dir=target_repo),
        "callees": find_callees(sym_name, repo_dir=target_repo),
    }


def run_repo_tests(repo_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run pytest in target repository and report test status."""
    target_repo = _resolve_target_repo(repo_dir)
    for pyc_dir in target_repo.rglob("__pycache__"):
        shutil.rmtree(pyc_dir, ignore_errors=True)

    try:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        test_args = ["tests/"] if (target_repo / "tests").exists() else []
        res = subprocess.run(
            [sys.executable, "-B", "-m", "pytest"] + test_args,
            cwd=str(target_repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            env=env,
        )
        return {
            "passed": res.returncode == 0,
            "exit_code": res.returncode,
            "output": res.stdout.strip(),
        }
    except Exception as e:
        return {
            "passed": False,
            "exit_code": -1,
            "output": f"Failed to execute tests: {e}",
        }


def apply_fix_to_repo(fix_data: Any, repo_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Verify oldText against live file, create checkpoint commit in target repo, and apply edit."""
    target_repo = _resolve_target_repo(repo_dir)

    if isinstance(fix_data, dict):
        rel_file = fix_data.get("file")
        start_line = int(fix_data.get("startLine") or fix_data.get("start_line", 1))
        end_line = int(fix_data.get("endLine") or fix_data.get("end_line", 1))
        old_text = fix_data.get("oldText") or fix_data.get("old_text", "")
        new_text = fix_data.get("newText") or fix_data.get("new_text", "")
    else:
        rel_file = fix_data.file
        start_line = int(fix_data.startLine)
        end_line = int(fix_data.endLine)
        old_text = fix_data.oldText
        new_text = fix_data.newText

    if not rel_file:
        return {"success": False, "error": "Missing required 'file' in structured fix"}

    # 1. Enforce path sandboxing strictly to target workspace
    target_file = _resolve_safe_path(rel_file, repo_dir=target_repo)
    if not target_file.exists() or not target_file.is_file():
        return {"success": False, "error": f"Target file '{rel_file}' not found in workspace"}

    # 2. Run before tests
    before_tests = run_repo_tests(target_repo)

    # 3. Read live file and verify oldText matches exactly
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return {"success": False, "error": f"Failed to read '{rel_file}': {e}"}

    total_lines = len(lines)
    if start_line < 1 or end_line < start_line or start_line > max(1, total_lines):
        return {
            "success": False,
            "error": f"Invalid line range {start_line}-{end_line} for '{rel_file}' ({total_lines} lines total)",
            "before_passed": before_tests["passed"],
        }

    actual_slice = "".join(lines[start_line - 1 : end_line]).replace("\r\n", "\n").rstrip("\n")
    expected_slice = old_text.replace("\r\n", "\n").rstrip("\n")

    if actual_slice != expected_slice:
        return {
            "success": False,
            "error": (
                f"Refused to apply fix: live file content at lines {start_line}-{end_line} did not match expected oldText.\n"
                f"Expected: {repr(expected_slice)}\n"
                f"Live:     {repr(actual_slice)}"
            ),
            "before_passed": before_tests["passed"],
            "after_passed": before_tests["passed"],
        }

    # 4. Create git checkpoint commit in TARGET repo
    if not (target_repo / ".git").is_dir():
        return {"success": False, "error": f"Target directory '{target_repo}' is not a git repository"}

    subprocess.run(["git", "add", "."], cwd=str(target_repo), capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", f"checkpoint: before apply-fix on {rel_file}:{start_line}", "--allow-empty"],
        cwd=str(target_repo),
        capture_output=True,
        text=True,
    )
    rev_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(target_repo), capture_output=True, text=True)
    checkpoint_hash = rev_res.stdout.strip()

    # 5. Apply the edit
    norm_new = new_text.replace("\r\n", "\n")
    replacement_lines = [l + "\n" for l in norm_new.splitlines()]
    if not norm_new.endswith("\n") and replacement_lines:
        replacement_lines[-1] = replacement_lines[-1].rstrip("\n")
        if end_line <= len(lines) and lines[end_line - 1].endswith("\n"):
            replacement_lines[-1] += "\n"

    updated_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.writelines(updated_lines)
    except Exception as e:
        return {"success": False, "error": f"Failed to write changes to '{rel_file}': {e}"}

    # 6. Run after tests
    after_tests = run_repo_tests(target_repo)

    return {
        "success": True,
        "checkpoint_hash": checkpoint_hash,
        "file": rel_file,
        "lines": [start_line, end_line],
        "before_passed": before_tests["passed"],
        "after_passed": after_tests["passed"],
        "before_output": before_tests["output"],
        "after_output": after_tests["output"],
    }


def rollback_repo_checkpoint(checkpoint_hash: str, repo_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Roll back target workspace repository to checkpoint hash using git reset --hard."""
    target_repo = _resolve_target_repo(repo_dir)
    if not (target_repo / ".git").is_dir():
        return {"success": False, "error": f"Target directory '{target_repo}' is not a git repository"}

    clean_hash = checkpoint_hash.strip()
    if not re.match(r"^[0-9a-fA-F]{4,40}$", clean_hash):
        return {"success": False, "error": f"Invalid git commit hash: {clean_hash}"}

    reset_res = subprocess.run(
        ["git", "reset", "--hard", clean_hash],
        cwd=str(target_repo),
        capture_output=True,
        text=True,
    )
    if reset_res.returncode != 0:
        return {"success": False, "error": f"git reset --hard failed: {reset_res.stderr.strip()}"}

    subprocess.run(["git", "clean", "-fd"], cwd=str(target_repo), capture_output=True, text=True)

    after_tests = run_repo_tests(target_repo)
    return {
        "success": True,
        "rolled_back_to": clean_hash,
        "tests_passed": after_tests["passed"],
        "test_output": after_tests["output"],
    }


TOOL_DEFINITIONS = [
    {
        "name": "list_dir",
        "description": "List files and subdirectories within the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "Relative directory path inside workspace (defaults to root: '')",
                }
            },
        },
    },
    {
        "name": "read_file_range",
        "description": "Read a range of 1-indexed lines from a file within the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "Relative file path inside workspace",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based starting line number",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-based ending line number (inclusive)",
                },
            },
            "required": ["rel_path"],
        },
    },
    {
        "name": "search_text",
        "description": "Search for text or regex patterns across source files in the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Pattern or text substring to search",
                },
                "rel_path": {
                    "type": "string",
                    "description": "Optional subdirectory or file to limit search to",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "git_log",
        "description": "Inspect recent git commit history in the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent commits to return (default: 5)",
                },
                "rel_path": {
                    "type": "string",
                    "description": "Optional relative path to scope git log",
                },
            },
        },
    },
    {
        "name": "git_diff",
        "description": "Inspect git diff between commits (e.g. 'HEAD~1' to 'HEAD') or uncommitted working tree ('WORKING')",
        "parameters": {
            "type": "object",
            "properties": {
                "commit_a": {
                    "type": "string",
                    "description": "Base ref (e.g. 'HEAD~1' or 'WORKING' for uncommitted working-tree changes)",
                },
                "commit_b": {
                    "type": "string",
                    "description": "Target ref (e.g. 'HEAD' or 'WORKING')",
                },
                "rel_path": {
                    "type": "string",
                    "description": "Optional relative path to scope git diff",
                },
            },
        },
    },
    {
        "name": "get_wiki",
        "description": "Get architectural intent and module documentation for the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module name to inspect (or '' for workspace overview)",
                }
            },
        },
    },
    {
        "name": "enclosing_symbol",
        "description": "Find the Python function or class enclosing a specific file and line number",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Relative file path in workspace",
                },
                "line": {
                    "type": "integer",
                    "description": "1-based line number",
                },
            },
            "required": ["file", "line"],
        },
    },
    {
        "name": "find_callers",
        "description": "Find all callers of a function or method symbol across the target workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Function or method name to search callers for",
                }
            },
            "required": ["symbol"],
        },
    },
]


def _call_get_wiki_safe(module: str, target_repo: Path) -> str:
    from brain.wiki import get_wiki

    try:
        return get_wiki(module, repo_dir=target_repo)
    except TypeError:
        return get_wiki(module)


def execute_tool(
    name: str,
    args: Optional[Dict[str, Any]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute a named sandboxed tool safely within `repo_dir`."""
    from brain.codemap import enclosing_symbol, find_callers, find_callees

    target_repo = _resolve_target_repo(repo_dir)
    safe_args = dict(args or {})
    # Never allow model-provided arguments to override the sandboxed workspace directory
    safe_args.pop("repo_dir", None)

    tools = {
        "list_dir": lambda rel_path="": list_dir(rel_path=rel_path, repo_dir=target_repo),
        "read_file_range": lambda rel_path, start_line=1, end_line=100: read_file_range(
            rel_path=rel_path, start_line=start_line, end_line=end_line, repo_dir=target_repo
        ),
        "read_file": lambda rel_path, start_line=1, end_line=150: read_file_range(
            rel_path=rel_path, start_line=start_line, end_line=end_line, repo_dir=target_repo
        ),
        "search_text": lambda query, rel_path="", max_results=25: search_text(
            query=query, rel_path=rel_path, max_results=max_results, repo_dir=target_repo
        ),
        "search_code": lambda query, rel_path="", max_results=25: search_text(
            query=query, rel_path=rel_path, max_results=max_results, repo_dir=target_repo
        ),
        "git_log": lambda n=5, rel_path="": git_log(n=n, rel_path=rel_path, repo_dir=target_repo),
        "git_diff": lambda commit_a="HEAD~1", commit_b="HEAD", rel_path="", base_ref=None: git_diff(
            commit_a=commit_a,
            commit_b=commit_b,
            rel_path=rel_path,
            repo_dir=target_repo,
            base_ref=base_ref,
        ),
        "get_working_diff": lambda rel_path="": get_working_diff(
            repo_dir=target_repo, rel_path=rel_path
        ),
        "get_wiki": lambda module="": {"wiki": _call_get_wiki_safe(module, target_repo)},
        "enclosing_symbol": lambda file, line=1: (
            enclosing_symbol(file, int(line), repo_dir=target_repo)
            or {"symbol": None, "message": f"No enclosing symbol at {file}:{line}"}
        ),
        "find_callers": lambda symbol="": {
            "callers": find_callers(symbol, repo_dir=target_repo)
        },
        "find_callees": lambda symbol="": {
            "callees": find_callees(symbol, repo_dir=target_repo)
        },
        "get_ast_context": lambda file, line=1: get_ast_context(
            file=file, line=int(line), repo_dir=target_repo
        ),
    }

    func = tools.get(name)
    if not func:
        return {"error": f"Unknown tool: '{name}'"}
    try:
        res = func(**safe_args)
        if isinstance(res, dict):
            return res
        return {"result": res}
    except SandboxViolationError as sve:
        return {"error": f"Sandbox violation: {str(sve)}"}
    except TypeError as te:
        return {"error": f"Invalid arguments for {name}: {str(te)}"}
    except Exception as ex:
        return {"error": f"Tool execution failed: {str(ex)}"}
