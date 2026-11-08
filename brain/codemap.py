"""AST-based Code Map for target repository using only Python standard library.

Builds a structural index of the target codebase:
- Functions and classes with line ranges, signatures, and docstrings
- Imports
- Best-effort call graph edges by symbol name
- Cache invalidated by git HEAD commit hash
"""

import ast
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from brain.config import DEMO_REPO_DIR, CACHE_DIR

class CodeMap:
    def __init__(self, repo_dir: Optional[Path] = None):
        self.repo_dir = Path(repo_dir or DEMO_REPO_DIR).resolve()
        self.commit_hash: str = ""
        self.files: Dict[str, Dict[str, Any]] = {}
        # Index: symbol_name -> list of caller locations {"file", "caller", "line"}
        self.callers_index: Dict[str, List[Dict[str, Any]]] = {}
        # Index: (file, symbol_name) -> list of callee names
        self.callees_index: Dict[str, List[str]] = {}

    def get_head_commit(self) -> str:
        """Get the current HEAD commit hash of the repo."""
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

    def build(self, force: bool = False):
        """Build or refresh code map if HEAD changed."""
        current_head = self.get_head_commit()
        if not force and self.commit_hash and self.commit_hash == current_head and self.files:
            return

        self.commit_hash = current_head
        self.files = {}
        self.callers_index = {}
        self.callees_index = {}

        if not self.repo_dir.exists():
            return

        for py_path in sorted(self.repo_dir.rglob("*.py")):
            rel_str = str(py_path.relative_to(self.repo_dir))
            if any(part in (".git", "__pycache__", ".venv", "venv", ".pytest_cache") for part in py_path.parts):
                continue
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

    def _parse_file(self, full_path: Path, rel_path: str):
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

        # Track imports
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    file_info["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [a.name for a in node.names]
                file_info["imports"].append(f"{mod}: {', '.join(names)}")

        # Helper to extract calls inside a node
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

        # Classes
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
        norm_file = str(Path(file).name)
        # Find matching file entry
        matched_info = None
        for path_key, info in self.files.items():
            if path_key == file or Path(path_key).name == norm_file or file.endswith(path_key):
                matched_info = info
                break

        if not matched_info:
            return None

        # Check methods in classes first (innermost)
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

        # Check top-level functions
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
        # Symbol could be 'func' or 'Class.func'
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
        lines = [f"Code Map (HEAD: {self.commit_hash[:8] if len(self.commit_hash) >= 8 else self.commit_hash}):"]
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

# Global singleton
_global_codemap: Optional[CodeMap] = None

def get_codemap(repo_dir: Optional[Path] = None) -> CodeMap:
    global _global_codemap
    target = Path(repo_dir or DEMO_REPO_DIR).resolve()
    if _global_codemap is None or _global_codemap.repo_dir != target:
        _global_codemap = CodeMap(target)
    _global_codemap.build()
    return _global_codemap

def enclosing_symbol(file: str, line: int) -> Optional[Dict[str, Any]]:
    return get_codemap().enclosing_symbol(file, line)

def find_callers(symbol: str) -> List[Dict[str, Any]]:
    return get_codemap().find_callers(symbol)

def find_callees(symbol: str) -> List[str]:
    return get_codemap().find_callees(symbol)
