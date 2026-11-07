"""Sandboxed read-only tools for Role 3: Brain Lead.

All tool operations are strictly sandboxed to demo-repo/.
Any attempt to access parent directories, symlinks outside demo-repo,
or forbidden paths will raise a PermissionError.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

from brain.config import DEMO_REPO_DIR, BASE_DIR

class SandboxViolationError(PermissionError):
    """Raised when a path traversal attempt is detected."""
    pass

def _resolve_safe_path(rel_path: str = "") -> Path:
    """Resolve a path relative to DEMO_REPO_DIR, verifying it does not escape."""
    raw = Path(str(rel_path or "").strip())
    
    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (DEMO_REPO_DIR / raw).resolve()

    try:
        # Check that DEMO_REPO_DIR is target or an ancestor of target
        target.relative_to(DEMO_REPO_DIR)
    except ValueError:
        raise SandboxViolationError(
            f"Access denied: '{rel_path}' escapes sandboxed demo-repo boundary."
        )

    return target

def list_dir(rel_path: str = "") -> Dict[str, Any]:
    """List directory contents within demo-repo/ in a read-only sandboxed manner."""
    target_dir = _resolve_safe_path(rel_path)
    if not target_dir.exists():
        return {"error": f"Directory not found: {rel_path}"}
    if not target_dir.is_dir():
        return {"error": f"Path is a file, not a directory: {rel_path}"}

    entries = []
    try:
        for entry in sorted(target_dir.iterdir()):
            if entry.name == "__pycache__":
                continue
            is_file = entry.is_file()
            rel_entry_path = str(entry.relative_to(DEMO_REPO_DIR))
            item: Dict[str, Any] = {
                "name": entry.name,
                "path": rel_entry_path,
                "type": "file" if is_file else "directory",
            }
            if is_file:
                item["size_bytes"] = entry.stat().st_size
                try:
                    with open(entry, "r", encoding="utf-8", errors="ignore") as f:
                        item["line_count"] = sum(1 for _ in f)
                except Exception:
                    pass
            entries.append(item)
        return {
            "directory": str(target_dir.relative_to(DEMO_REPO_DIR)),
            "entries": entries,
            "total_entries": len(entries),
        }
    except Exception as e:
        return {"error": f"Failed to list directory {rel_path}: {str(e)}"}

def read_file_range(rel_path: str, start_line: int = 1, end_line: int = 100) -> Dict[str, Any]:
    """Read a specific line range (1-indexed, inclusive) from a file within demo-repo/."""
    target_file = _resolve_safe_path(rel_path)
    if not target_file.exists():
        return {"error": f"File not found: {rel_path}"}
    if not target_file.is_file():
        return {"error": f"Path is not a regular file: {rel_path}"}

    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line

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
            "path": str(target_file.relative_to(DEMO_REPO_DIR)),
            "start_line": clamped_start,
            "end_line": clamped_end,
            "total_lines": total_lines,
            "content": "\n".join(numbered_lines),
        }
    except Exception as e:
        return {"error": f"Failed to read file {rel_path}: {str(e)}"}

def search_text(query: str, rel_path: str = "", max_results: int = 25) -> Dict[str, Any]:
    """Search for substring/regex across text files inside demo-repo/."""
    target_root = _resolve_safe_path(rel_path)
    if not target_root.exists():
        return {"error": f"Search path does not exist: {rel_path}"}

    results: List[Dict[str, Any]] = []
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        # Fallback to literal search if regex compilation fails
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    files_to_search = (
        [target_root] if target_root.is_file() else sorted(target_root.rglob("*"))
    )

    for file_path in files_to_search:
        if not file_path.is_file():
            continue
        if ".git" in file_path.parts or "__pycache__" in file_path.parts:
            continue
        # Skip common binary extensions
        if file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".pyc", ".so", ".bin", ".zip", ".tar"}:
            continue

        try:
            rel_file_str = str(file_path.relative_to(DEMO_REPO_DIR))
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, start=1):
                    if pattern.search(line):
                        results.append({
                            "file": rel_file_str,
                            "line": line_num,
                            "content": line.strip()
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

def git_log(n: int = 5, rel_path: str = "") -> Dict[str, Any]:
    """Retrieve git log entries scoped to demo-repo/."""
    n = max(1, min(n, 50))
    target_path = _resolve_safe_path(rel_path)

    # Determine git command context
    has_inner_git = (DEMO_REPO_DIR / ".git").is_dir()
    if has_inner_git:
        cwd = DEMO_REPO_DIR
        cmd = ["git", "log", f"-n{n}", "--format=%H|%an|%ad|%s", "--date=short"]
        if rel_path:
            cmd.extend(["--", str(target_path.relative_to(DEMO_REPO_DIR))])
    else:
        cwd = BASE_DIR
        cmd = ["git", "log", f"-n{n}", "--format=%H|%an|%ad|%s", "--date=short", "--", "demo-repo"]

    try:
        result = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"error": f"git log failed: {result.stderr.strip()}"}

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
        return {"error": f"git log failed: {str(e)}"}

def git_diff(commit_a: Optional[str] = "HEAD~1", commit_b: Optional[str] = "HEAD", rel_path: str = "") -> Dict[str, Any]:
    """Retrieve git diff between two commits or against working tree, scoped to demo-repo/."""
    target_path = _resolve_safe_path(rel_path)
    has_inner_git = (DEMO_REPO_DIR / ".git").is_dir()

    if has_inner_git:
        cwd = DEMO_REPO_DIR
        cmd = ["git", "diff"]
        if commit_a and commit_b:
            cmd.extend([commit_a, commit_b])
        elif commit_a:
            cmd.append(commit_a)
        if rel_path:
            cmd.extend(["--", str(target_path.relative_to(DEMO_REPO_DIR))])
    else:
        cwd = BASE_DIR
        cmd = ["git", "diff"]
        if commit_a and commit_b:
            cmd.extend([commit_a, commit_b])
        elif commit_a:
            cmd.append(commit_a)
        cmd.extend(["--", "demo-repo"])

    try:
        result = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"error": f"git diff failed: {result.stderr.strip()}"}
        return {
            "commit_a": commit_a,
            "commit_b": commit_b,
            "diff": result.stdout,
            "has_changes": bool(result.stdout.strip())
        }
    except Exception as e:
        return {"error": f"git diff failed: {str(e)}"}

TOOL_DEFINITIONS = [
    {
        "name": "list_dir",
        "description": "List files and subdirectories within demo-repo/",
        "parameters": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "Relative directory path inside demo-repo (defaults to root: '')",
                }
            },
        },
    },
    {
        "name": "read_file_range",
        "description": "Read a range of lines from a specific file within demo-repo/",
        "parameters": {
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "Relative file path inside demo-repo (e.g. 'utils.py')",
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
        "description": "Search for text or regex patterns in code files under demo-repo/",
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
        "description": "Inspect recent commit history in demo-repo/",
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
        "description": "Inspect diff between commits or against working tree in demo-repo/",
        "parameters": {
            "type": "object",
            "properties": {
                "commit_a": {
                    "type": "string",
                    "description": "Base commit (e.g. 'HEAD~1')",
                },
                "commit_b": {
                    "type": "string",
                    "description": "Target commit (e.g. 'HEAD') or null for uncommitted working tree",
                },
                "rel_path": {
                    "type": "string",
                    "description": "Optional relative path to scope git diff",
                },
            },
        },
    },
]

def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a named tool safely with provided keyword arguments."""
    tools = {
        "list_dir": list_dir,
        "read_file_range": read_file_range,
        "search_text": search_text,
        "git_log": git_log,
        "git_diff": git_diff,
    }
    func = tools.get(name)
    if not func:
        return {"error": f"Unknown tool: '{name}'"}
    try:
        return func(**args)
    except SandboxViolationError as sve:
        return {"error": f"Sandbox violation: {str(sve)}"}
    except TypeError as te:
        return {"error": f"Invalid arguments for {name}: {str(te)}"}
    except Exception as ex:
        return {"error": f"Tool execution failed: {str(ex)}"}
