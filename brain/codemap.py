"""AST-based Code Map for any target workspace using Python standard library.

Builds a fast structural index of a workspace:
- Functions and classes with line ranges, signatures, and docstrings
- Imports
- Best-effort call graph edges by symbol name
- Per-workspace cache invalidated by git HEAD commit hash and file mtimes
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set

from brain.config import resolve_repo_dir

IGNORED_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "out",
    "runtime",
    ".whack",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "site-packages",
    ".tox",
    ".eggs",
}


class CodeMap:
    def __init__(self, repo_dir: Optional[Path] = None):
        self.repo_dir: Path = Path(repo_dir).expanduser().resolve() if repo_dir else resolve_repo_dir()
        self.commit_hash: str = ""
        self._last_max_mtime: float = 0.0
        self._last_file_count: int = 0
        self.files: Dict[str, Dict[str, Any]] = {}
        self.callers_index: Dict[str, List[Dict[str, Any]]] = {}
        self.callees_index: Dict[str, List[str]] = {}

    def get_head_commit(self) -> str:
        """Get the current HEAD commit hash of the workspace if git is initialized."""
        if not (self.repo_dir / ".git").exists():
            return "no-git"
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def _discover_python_files(self) -> Tuple[List[Tuple[Path, str]], float]:
        """Walk workspace pruning ignored directories and return ((full_path, rel_posix), max_mtime)."""
        py_files: List[Tuple[Path, str]] = []
        max_mtime = 0.0

        if not self.repo_dir.exists() or not self.repo_dir.is_dir():
            return py_files, max_mtime

        for root, dirs, files in os.walk(self.repo_dir):
            dirs[:] = sorted(
                d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")
            )
            root_path = Path(root)
            for fname in sorted(files):
                if not fname.endswith(".py"):
                    continue
                full_path = root_path / fname
                try:
                    st = full_path.stat()
                    if st.st_size > 512_000:
                        continue
                    if st.st_mtime > max_mtime:
                        max_mtime = st.st_mtime
                    rel_posix = full_path.relative_to(self.repo_dir).as_posix()
                    py_files.append((full_path, rel_posix))
                except OSError:
                    continue

        return py_files, max_mtime

    def build(self, force: bool = False) -> None:
        """Build or refresh code map if HEAD commit or Python file mtimes changed."""
        py_files, max_mtime = self._discover_python_files()
        current_head = self.get_head_commit()

        if (
            not force
            and self.files
            and self.commit_hash == current_head
            and self._last_max_mtime == max_mtime
            and self._last_file_count == len(py_files)
        ):
            return

        self.commit_hash = current_head
        self._last_max_mtime = max_mtime
        self._last_file_count = len(py_files)
        self.files = {}
        self.callers_index = {}
        self.callees_index = {}

        for py_path, rel_str in py_files:
            self._parse_file(py_path, rel_str)

    def _format_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Format function signature with args and return annotation."""
        args_strs = []
        for a in node.args.args:
            name = a.arg
            if a.annotation:
                args_strs.append(f"{name}: {ast.unparse(a.annotation)}")
            else:
                args_strs.append(name)
        sig = f"{node.name}({', '.join(args_strs)})"
        if node.returns:
            sig += f" -> {ast.unparse(node.returns)}"
        return sig

    def _get_first_docstring_line(self, node: ast.AST) -> Optional[str]:
        """Extract first line of docstring if present."""
        doc = ast.get_docstring(node)
        if doc:
            first_line = doc.strip().splitlines()[0].strip()
            return first_line if first_line else None
        return None

    def _parse_file(self, full_path: Path, rel_path: str) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
            tree = ast.parse(source, filename=str(full_path))
        except Exception:
            return

        file_info: Dict[str, Any] = {
            "path": rel_path,
            "imports": [],
            "classes": [],
            "functions": [],
        }

        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    file_info["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [a.name for a in node.names]
                file_info["imports"].append(f"{mod}: {', '.join(names)}")

        def extract_calls(parent_node: ast.AST) -> List[Tuple[str, int]]:
            calls = []
            for child in ast.walk(parent_node):
                if isinstance(child, ast.Call):
                    name = None
                    if isinstance(child.func, ast.Name):
                        name = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        name = child.func.attr
                    if name:
                        calls.append((name, getattr(child, "lineno", 0)))
            return calls

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cls_info = {
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "docstring": self._get_first_docstring_line(node),
                    "bases": [ast.unparse(b) for b in node.bases],
                    "methods": [],
                }
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_info = {
                            "name": item.name,
                            "full_name": f"{node.name}.{item.name}",
                            "start_line": item.lineno,
                            "end_line": getattr(item, "end_lineno", item.lineno),
                            "signature": self._format_signature(item),
                            "docstring": self._get_first_docstring_line(item),
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        }
                        cls_info["methods"].append(method_info)

                        calls = extract_calls(item)
                        callee_names = [c[0] for c in calls]
                        self.callees_index[f"{rel_path}:{method_info['full_name']}"] = callee_names
                        for callee, line in calls:
                            self.callers_index.setdefault(callee, []).append({
                                "file": rel_path,
                                "caller": method_info["full_name"],
                                "line": line,
                            })
                file_info["classes"].append(cls_info)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_info = {
                    "name": node.name,
                    "full_name": node.name,
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "signature": self._format_signature(node),
                    "docstring": self._get_first_docstring_line(node),
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                }
                file_info["functions"].append(fn_info)

                calls = extract_calls(node)
                callee_names = [c[0] for c in calls]
                self.callees_index[f"{rel_path}:{node.name}"] = callee_names
                for callee, line in calls:
                    self.callers_index.setdefault(callee, []).append({
                        "file": rel_path,
                        "caller": node.name,
                        "line": line,
                    })

        self.files[rel_path] = file_info

    def enclosing_symbol(self, file: str, line: int) -> Optional[Dict[str, Any]]:
        """Find the innermost function or class enclosing `line` in `file`."""
        self.build()
        clean_query = file.replace("\\", "/").lstrip("./")
        norm_file = Path(clean_query).name

        matched_info = None
        # Prefer exact relative path match first, then suffix match, then basename match
        if clean_query in self.files:
            matched_info = self.files[clean_query]
        else:
            for path_key, info in self.files.items():
                if clean_query.endswith(path_key) or path_key.endswith(clean_query):
                    matched_info = info
                    break
            if matched_info is None:
                for path_key, info in self.files.items():
                    if Path(path_key).name == norm_file:
                        matched_info = info
                        break

        if not matched_info:
            return None

        for cls in matched_info["classes"]:
            for m in cls["methods"]:
                if m["start_line"] <= line <= m["end_line"]:
                    return {
                        "name": m["full_name"],
                        "type": "method",
                        "start_line": m["start_line"],
                        "end_line": m["end_line"],
                        "signature": m["signature"],
                        "docstring": m["docstring"],
                        "file": matched_info["path"],
                    }
            if cls["start_line"] <= line <= cls["end_line"]:
                return {
                    "name": cls["name"],
                    "type": "class",
                    "start_line": cls["start_line"],
                    "end_line": cls["end_line"],
                    "docstring": cls["docstring"],
                    "file": matched_info["path"],
                }

        for fn in matched_info["functions"]:
            if fn["start_line"] <= line <= fn["end_line"]:
                return {
                    "name": fn["name"],
                    "type": "function",
                    "start_line": fn["start_line"],
                    "end_line": fn["end_line"],
                    "signature": fn["signature"],
                    "docstring": fn["docstring"],
                    "file": matched_info["path"],
                }

        return None

    def find_callers(self, symbol: str) -> List[Dict[str, Any]]:
        """Find all callers of symbol across the codebase."""
        self.build()
        target_name = symbol.split(".")[-1]
        return self.callers_index.get(target_name, [])

    def find_callees(self, symbol: str) -> List[str]:
        """Find all functions/methods called by symbol."""
        self.build()
        results = []
        for key, callees in self.callees_index.items():
            if key.endswith(f":{symbol}") or key.endswith(f".{symbol}"):
                results.extend(callees)
        return list(dict.fromkeys(results))

    def format_summary(self, max_chars: int = 2500) -> str:
        """Format compact outline of the repository for LLM context."""
        self.build()
        head_label = self.commit_hash[:8] if len(self.commit_hash) >= 8 else self.commit_hash
        lines = [f"Code Map ({self.repo_dir.name}, HEAD: {head_label}):"]
        for rel_path, info in sorted(self.files.items()):
            lines.append(f"\n[{rel_path}]")
            if info["imports"]:
                lines.append(f"  imports: {', '.join(info['imports'][:5])}")
            for cls in info["classes"]:
                doc_snippet = f" - \"{cls['docstring']}\"" if cls["docstring"] else ""
                lines.append(f"  class {cls['name']}(L{cls['start_line']}-L{cls['end_line']}){doc_snippet}")
                for m in cls["methods"]:
                    mdoc = f" - \"{m['docstring']}\"" if m["docstring"] else ""
                    lines.append(f"    def {m['signature']} (L{m['start_line']}-L{m['end_line']}){mdoc}")
            for fn in info["functions"]:
                fdoc = f" - \"{fn['docstring']}\"" if fn["docstring"] else ""
                lines.append(f"  def {fn['signature']} (L{fn['start_line']}-L{fn['end_line']}){fdoc}")

        out = "\n".join(lines)
        if len(out) > max_chars:
            out = out[:max_chars] + "\n... [code map truncated]"
        return out


# Per-workspace cache keyed by resolved path string
_codemap_cache: Dict[str, CodeMap] = {}


def get_codemap(repo_dir: Optional[Path] = None) -> CodeMap:
    """Retrieve or build cached CodeMap for the requested workspace."""
    target = Path(repo_dir).expanduser().resolve() if repo_dir else resolve_repo_dir()
    key = str(target)
    cmap = _codemap_cache.get(key)
    if cmap is None:
        cmap = CodeMap(target)
        _codemap_cache[key] = cmap
    cmap.build()
    return cmap


# Alias matching cross-component convention
get_code_map = get_codemap


def enclosing_symbol(file: str, line: int, repo_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return get_codemap(repo_dir).enclosing_symbol(file, line)


def find_callers(symbol: str, repo_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    return get_codemap(repo_dir).find_callers(symbol)


def find_callees(symbol: str, repo_dir: Optional[Path] = None) -> List[str]:
    return get_codemap(repo_dir).find_callees(symbol)
