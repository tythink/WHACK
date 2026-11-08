"""Dynamic Local Architecture Wiki Generator and Cache for any repository.

Inspects repository structure, AST symbols, call-graph relationships, TypeScript/JS
exports, package manifests, and latest git HEAD commit activity. Persists both
`<repo_dir>/.whack/WIKI.md` (excluded from git status) and a commit-keyed cache
under `WIKI_CACHE_DIR`.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import brain.config as _brain_config
from brain.codemap import CodeMap
from brain.config import DEMO_REPO_DIR, LLM_MODEL, LLM_PROVIDER, WIKI_CACHE_DIR, get_active_api_key


def _resolve_default_repo(repo_dir: Optional[Path] = None) -> Path:
    """Resolve explicit `repo_dir` or fall back dynamically to configured target repo."""
    if repo_dir is not None and str(repo_dir).strip():
        return Path(repo_dir).expanduser().resolve()
    resolver = getattr(_brain_config, "resolve_repo_dir", None)
    if callable(resolver):
        return Path(resolver(None)).resolve()
    return Path(DEMO_REPO_DIR).expanduser().resolve()

IGNORED_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "dist-electron",
    "out",
    "runtime",
    ".whack",
    "cache",
    "build",
    ".next",
    ".turbo",
    "coverage",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".ruff_cache",
    "eggs",
    "sdist",
    "wheels",
}

PY_EXTENSIONS: Set[str] = {".py"}
TS_JS_EXTENSIONS: Set[str] = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
OTHER_CODE_EXTENSIONS: Set[str] = {".rs", ".go", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".rb"}
ALL_CODE_EXTENSIONS: Set[str] = PY_EXTENSIONS | TS_JS_EXTENSIONS | OTHER_CODE_EXTENSIONS
MANIFEST_FILENAMES: Set[str] = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "environment.yml",
    "requirements.txt",
}

MAX_DETAILED_FILES_PER_MODULE = 15
LLM_SYNTHESIS_TIMEOUT_SECONDS = 8.0


def _ensure_git_excluded(repo_dir: Path) -> bool:
    """Best-effort helper ensuring `.whack/` is listed in `<git_dir>/info/exclude`.

    Never modifies tracked files and silently succeeds/skips in read-only sandboxes.
    """
    try:
        resolved = Path(repo_dir).resolve()
        exclude_path: Optional[Path] = None

        dot_git = resolved / ".git"
        if dot_git.is_dir():
            exclude_path = dot_git / "info" / "exclude"
        else:
            res = subprocess.run(
                ["git", "rev-parse", "--git-path", "info/exclude"],
                cwd=str(resolved),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                candidate = Path(res.stdout.strip())
                exclude_path = candidate if candidate.is_absolute() else (resolved / candidate).resolve()

        if exclude_path is None:
            return False

        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing_text = ""
        if exclude_path.exists():
            existing_text = exclude_path.read_text(encoding="utf-8", errors="replace")

        existing_lines = {
            line.strip()
            for line in existing_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        if ".whack/" in existing_lines or ".whack" in existing_lines:
            return True

        prefix = "" if (not existing_text or existing_text.endswith("\n")) else "\n"
        addition = f"{prefix}# WHACK local architecture wiki\n.whack/\n"
        with open(exclude_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(addition)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _parse_wiki_metadata(markdown_text: str) -> Dict[str, Any]:
    """Extract Commit, UsedModel, and Modules metadata from wiki header comments."""
    meta: Dict[str, Any] = {
        "commit": None,
        "used_model": False,
        "modules": [],
    }
    head_Lines = markdown_text[:1200].splitlines()
    for line in head_Lines[:10]:
        commit_match = re.match(r"<!--\s*Commit:\s*([^\s|>-]+)", line)
        if commit_match:
            meta["commit"] = commit_match.group(1).strip()
        model_match = re.match(r"<!--\s*UsedModel:\s*(true|false)\s*-->", line, re.IGNORECASE)
        if model_match:
            meta["used_model"] = model_match.group(1).lower() == "true"
        mods_match = re.match(r"<!--\s*Modules:\s*(.*?)\s*-->", line)
        if mods_match:
            raw_mods = mods_match.group(1).strip()
            if raw_mods:
                meta["modules"] = [m.strip() for m in raw_mods.split(",") if m.strip()]

    if not meta["modules"]:
        meta["modules"] = re.findall(r"^###\s+Module:\s+([^\n]+)$", markdown_text, flags=re.MULTILINE)
    return meta


def _extract_readme_summary(readme_path: Path, max_chars: int = 900) -> str:
    """Extract concise title and prose overview from a README.md file, skipping large diagrams."""
    if not readme_path.is_file():
        return ""
    try:
        raw = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # Strip fenced code blocks (mermaid, directory trees, shell snippets) for clean prose overview
    cleaned = re.sub(r"```[\s\S]*?```", "", raw)
    cleaned = re.sub(r"<!--[\s\S]*?-->", "", cleaned)

    paragraphs: List[str] = []
    title = ""
    for block in re.split(r"\n\s*\n", cleaned):
        text = block.strip()
        if not text or text == "---":
            continue
        if text.startswith("# ") and not title:
            title = text.lstrip("#").strip()
            continue
        if text.startswith("#"):
            # Include short architecture/overview headers if still within budget
            header_lines = text.splitlines()
            if len(header_lines) > 1:
                rest = "\n".join(header_lines[1:]).strip()
                if rest:
                    paragraphs.append(f"**{header_lines[0].lstrip('#').strip()}**: {rest}")
            continue
        paragraphs.append(text)
        if sum(len(p) for p in paragraphs) >= max_chars:
            break

    combined = "\n\n".join(paragraphs).strip()
    if len(combined) > max_chars:
        combined = combined[:max_chars].rsplit(" ", 1)[0] + "..."
    if title and combined:
        return f"**{title}** — {combined}"
    return combined or title


def _inspect_package_json(pkg_path: Path) -> Optional[str]:
    """Summarize a Node/TypeScript package.json file concisely."""
    if not pkg_path.is_file():
        return None
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    parts: List[str] = []
    name = data.get("name")
    version = data.get("version")
    desc = data.get("description")
    if name:
        ver_str = f"@{version}" if version else ""
        parts.append(f"`{name}{ver_str}`" + (f" — {desc}" if desc else ""))
    elif desc:
        parts.append(str(desc))

    scripts = data.get("scripts")
    if isinstance(scripts, dict) and scripts:
        script_names = list(scripts.keys())[:6]
        parts.append(f"scripts: `{', '.join(script_names)}`")

    deps = list((data.get("dependencies") or {}).keys())
    dev_deps = list((data.get("devDependencies") or {}).keys())
    key_deps = (deps + [d for d in dev_deps if d in ("electron", "typescript", "vite", "vitest", "jest")])[:8]
    if key_deps:
        parts.append(f"deps: `{', '.join(key_deps)}`")

    return " | ".join(parts) if parts else None


def _inspect_ts_js_file(full_path: Path, rel_posix: str) -> Dict[str, Any]:
    """Lightweight structural parser for TypeScript/JavaScript source files."""
    info: Dict[str, Any] = {
        "path": rel_posix,
        "line_count": 0,
        "summary": None,
        "exports": [],
    }
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return info

    info["line_count"] = len(lines)

    # Check leading comment block for file overview
    comment_lines: List[str] = []
    in_block = False
    for line in lines[:20]:
        s = line.strip()
        if not s and not in_block:
            if comment_lines:
                break
            continue
        if s.startswith("/**") or s.startswith("/*"):
            in_block = True
            cleaned = s.lstrip("/*").rstrip("*/").strip()
            if cleaned:
                comment_lines.append(cleaned)
            if "*/" in s[2:]:
                in_block = False
                break
        elif in_block:
            if "*/" in s:
                cleaned = s.split("*/", 1)[0].lstrip("*").strip()
                if cleaned:
                    comment_lines.append(cleaned)
                break
            cleaned = s.lstrip("*").strip()
            if cleaned:
                comment_lines.append(cleaned)
        elif s.startswith("//"):
            cleaned = s.lstrip("/").strip()
            if cleaned:
                comment_lines.append(cleaned)
        else:
            break

    if comment_lines:
        info["summary"] = comment_lines[0][:160]

    patterns = [
        ("class", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*export\s+interface\s+([A-Za-z_]\w*)")),
        ("type", re.compile(r"^\s*export\s+type\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)")),
        ("const", re.compile(r"^\s*export\s+const\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:async\s+)?function\s+([A-Za-z_]\w*)")),
    ]

    seen: Set[str] = set()
    for idx, line in enumerate(lines, start=1):
        for kind, pat in patterns:
            m = pat.match(line)
            if m:
                sym = m.group(1)
                if sym not in seen:
                    seen.add(sym)
                    info["exports"].append({"kind": kind, "name": sym, "line": idx})
                break
        if len(info["exports"]) >= 16:
            break

    return info


def _extract_py_module_meta(full_path: Path) -> Tuple[int, Optional[str]]:
    """Return (line_count, first_docstring_line) for a Python file."""
    try:
        source = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, None
    line_count = len(source.splitlines())
    try:
        tree = ast.parse(source, filename=str(full_path))
        doc = ast.get_docstring(tree)
        if doc:
            first_line = doc.strip().splitlines()[0].strip()
            return line_count, first_line
    except SyntaxError:
        pass
    return line_count, None


class ArchitectureWiki:
    """Dynamic repository architecture wiki builder, cache manager, and section query API."""

    def __init__(self, repo_dir: Optional[Path] = None):
        self.repo_dir: Path = _resolve_default_repo(repo_dir)
        try:
            WIKI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _run_git(self, args: List[str], timeout: float = 3.0) -> Optional[str]:
        """Run a read-only git subcommand inside `self.repo_dir`, returning stripped stdout or None."""
        if not self.repo_dir.exists():
            return None
        try:
            res = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _compute_dir_fingerprint(self) -> str:
        """Compute short deterministic hash from file paths, sizes, and mtimes for non-commit trees."""
        hasher = hashlib.sha1()
        if not self.repo_dir.exists():
            return "empty"
        try:
            for root, dirnames, filenames in os.walk(self.repo_dir):
                dirnames[:] = sorted(
                    d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
                )
                for fname in sorted(filenames):
                    fpath = Path(root) / fname
                    try:
                        st = fpath.stat()
                        rel = fpath.relative_to(self.repo_dir).as_posix()
                        hasher.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
                    except OSError:
                        continue
        except OSError:
            return "unreadable"
        return hasher.hexdigest()[:8]

    def get_head_commit(self) -> str:
        """Resolve current `HEAD` commit hash, handling unborn `HEAD` and non-git directories safely."""
        if not self.repo_dir.exists():
            return "uncommitted"
        inside = self._run_git(["rev-parse", "--is-inside-work-tree"])
        if inside == "true":
            head = self._run_git(["rev-parse", "HEAD"])
            if head:
                return head
            return f"uncommitted-{self._compute_dir_fingerprint()}"
        return f"nogit-{self._compute_dir_fingerprint()}"

    # Compatibility alias resolving current HEAD commit
    def get_last_passing_commit(self) -> str:
        return self.get_head_commit()

    def get_whack_wiki_path(self) -> Path:
        """Path to `<repo_dir>/.whack/WIKI.md`."""
        return self.repo_dir / ".whack" / "WIKI.md"

    def get_cache_path(self, commit: str) -> Path:
        """Path to `<WIKI_CACHE_DIR>/<repo_slug>_<commit_hash>.md`."""
        repo_name = re.sub(r"[^\w\-]", "_", self.repo_dir.name or "repo")
        path_hash = hashlib.sha1(str(self.repo_dir).encode("utf-8")).hexdigest()[:8]
        safe_commit = re.sub(r"[^\w\-]", "_", commit)[:16]
        return WIKI_CACHE_DIR / f"{repo_name}_{path_hash}_{safe_commit}.md"

    def _build_git_activity_section(self, commit_hash: str) -> str:
        """Build the `Latest Git Activity (HEAD)` markdown section from live git metadata."""
        lines = ["## Latest Git Activity (`HEAD`)"]
        if commit_hash.startswith(("uncommitted", "nogit")):
            lines.append(f"- **Status**: `{commit_hash}` (working directory has no committed `HEAD` yet)")
            return "\n".join(lines)

        branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]) or "HEAD"
        meta_line = self._run_git(["log", "-n", "1", "--format=%H|%an|%ci|%s", "HEAD"])
        if meta_line and "|" in meta_line:
            parts = meta_line.split("|", 3)
            if len(parts) == 4:
                full_sha, author, timestamp_str, subject = parts
                lines.extend([
                    f"- **Branch**: `{branch}`",
                    f"- **HEAD Commit**: `{full_sha}`",
                    f"- **Timestamp**: `{timestamp_str}`",
                    f"- **Author**: {author}",
                    f"- **Subject**: {subject}",
                ])
        else:
            lines.append(f"- **HEAD Commit**: `{commit_hash}`")

        head_stat = self._run_git(["show", "--stat", "--oneline", "HEAD"])
        if head_stat:
            lines.extend([
                "",
                "### Touched Files in `HEAD` (`git show --stat --oneline HEAD`)",
                "```text",
                head_stat,
                "```",
            ])

        recent_log = self._run_git(["log", "-n", "5", "--oneline"])
        if recent_log:
            lines.extend([
                "",
                "### Recent Commits (`git log -n 5 --oneline`)",
                "```text",
                recent_log,
                "```",
            ])

        return "\n".join(lines)

    def _scan_and_render_modules(self, commit_hash: str) -> Tuple[str, List[str]]:
        """Dynamically scan Python AST, TypeScript/JS symbols, and manifests into module markdown."""
        if not self.repo_dir.exists():
            return "### Repository Not Found\nTarget directory does not exist.", []

        codemap = CodeMap(self.repo_dir)
        codemap.commit_hash = commit_hash

        py_meta: Dict[str, Tuple[int, Optional[str]]] = {}
        ts_meta: Dict[str, Dict[str, Any]] = {}

        # Group discovered source files:
        # - root source files -> module per file stem
        # - top-level subdirectories -> module per top-level directory name
        root_code_files: Dict[str, List[str]] = {}
        dir_code_files: Dict[str, List[str]] = {}
        dir_manifests: Dict[str, List[str]] = {}

        for current_root, dirnames, filenames in os.walk(self.repo_dir):
            dirnames[:] = sorted(
                d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
            )
            root_path = Path(current_root)
            rel_dir = root_path.relative_to(self.repo_dir)
            top_dir = rel_dir.parts[0] if rel_dir.parts else ""

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                fpath = root_path / fname
                rel_posix = fpath.relative_to(self.repo_dir).as_posix()
                suffix = fpath.suffix.lower()

                if fname in MANIFEST_FILENAMES and top_dir:
                    dir_manifests.setdefault(top_dir, []).append(rel_posix)

                if suffix not in ALL_CODE_EXTENSIONS:
                    continue

                if suffix in PY_EXTENSIONS:
                    codemap._parse_file(fpath, rel_posix)
                    py_meta[rel_posix] = _extract_py_module_meta(fpath)
                elif suffix in TS_JS_EXTENSIONS:
                    ts_meta[rel_posix] = _inspect_ts_js_file(fpath, rel_posix)

                if not top_dir:
                    mod_name = fpath.stem
                    root_code_files.setdefault(mod_name, []).append(rel_posix)
                else:
                    dir_code_files.setdefault(top_dir, []).append(rel_posix)

        # Include top-level directories that only had manifests + nested files if any
        all_module_groups: List[Tuple[str, List[str], bool]] = []
        for mod_name in sorted(root_code_files.keys()):
            all_module_groups.append((mod_name, root_code_files[mod_name], False))
        for top_dir in sorted(dir_code_files.keys()):
            # Avoid collision if a root file shares a stem with a directory
            effective_name = top_dir if top_dir not in root_code_files else f"{top_dir}/"
            all_module_groups.append((effective_name.rstrip("/"), dir_code_files[top_dir], True))

        module_names: List[str] = [m[0] for m in all_module_groups]
        sections: List[str] = ["## Module & Subsystem Reference"]

        if not all_module_groups:
            sections.append("_No source modules discovered in repository._")
            return "\n\n".join(sections), []

        for mod_name, rel_files, is_dir_module in all_module_groups:
            mod_lines: List[str] = [f"### Module: {mod_name}"]

            if is_dir_module:
                subdir_path = self.repo_dir / mod_name
                pkg_summary = _inspect_package_json(subdir_path / "package.json")
                if pkg_summary:
                    mod_lines.append(f"- **Package**: {pkg_summary}")

                sub_readme = _extract_readme_summary(subdir_path / "README.md", max_chars=280)
                if sub_readme:
                    single_line = " ".join(sub_readme.splitlines())
                    mod_lines.append(f"- **Overview**: {single_line}")

                ext_counts: Dict[str, int] = {}
                for rf in rel_files:
                    ext = Path(rf).suffix.lower() or "other"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                lang_summary = ", ".join(f"`{k}` ({v})" for k, v in sorted(ext_counts.items()))
                mod_lines.append(f"- **Source Files**: {len(rel_files)} ({lang_summary})")

            # Sort shallowest files first so primary entrypoints appear before deep fixtures
            ordered_files = sorted(rel_files, key=lambda p: (p.count("/"), p))
            detailed_files = ordered_files[:MAX_DETAILED_FILES_PER_MODULE]
            omitted_files = ordered_files[MAX_DETAILED_FILES_PER_MODULE:]

            for rel_posix in detailed_files:
                mod_lines.append("")
                mod_lines.append(self._render_file_block(rel_posix, codemap, py_meta, ts_meta))

            if omitted_files:
                sample_rest = ", ".join(f"`{p}`" for p in omitted_files[:6])
                more_suffix = f", ... (+{len(omitted_files) - 6} more)" if len(omitted_files) > 6 else ""
                mod_lines.append("")
                mod_lines.append(
                    f"- **Additional Files ({len(omitted_files)})**: {sample_rest}{more_suffix}"
                )

            sections.append("\n".join(mod_lines))

        return "\n\n".join(sections), module_names

    def _render_file_block(
        self,
        rel_posix: str,
        codemap: CodeMap,
        py_meta: Dict[str, Tuple[int, Optional[str]]],
        ts_meta: Dict[str, Dict[str, Any]],
    ) -> str:
        """Render a single `#### File: <rel_posix>` section with AST symbols and call edges."""
        lines: List[str] = [f"#### File: `{rel_posix}`"]
        suffix = Path(rel_posix).suffix.lower()

        if suffix in PY_EXTENSIONS:
            line_count, mod_doc = py_meta.get(rel_posix, (0, None))
            if mod_doc:
                lines.append(f"- **Purpose**: {mod_doc} ({line_count} lines)")
            elif line_count:
                lines.append(f"- **Size**: {line_count} lines")

            # CodeMap stores platform-specific or relative key depending on caller; we passed rel_posix
            cm_info = codemap.files.get(rel_posix)
            if cm_info:
                imports = cm_info.get("imports") or []
                if imports:
                    lines.append(f"- **Imports**: `{', '.join(imports[:6])}`")

                for cls in cm_info.get("classes", []):
                    cls_doc = f" — {cls['docstring']}" if cls.get("docstring") else ""
                    bases = f"({', '.join(cls['bases'])})" if cls.get("bases") else ""
                    lines.append(
                        f"- **Class `{cls['name']}{bases}`** (L{cls['start_line']}-L{cls['end_line']}){cls_doc}"
                    )
                    for m in cls.get("methods", [])[:10]:
                        m_doc = f": {m['docstring']}" if m.get("docstring") else ""
                        lines.append(
                            f"  - `{m['signature']}` (L{m['start_line']}-L{m['end_line']}){m_doc}"
                        )

                for fn in cm_info.get("functions", [])[:14]:
                    fn_doc = f" — {fn['docstring']}" if fn.get("docstring") else ""
                    callers = codemap.callers_index.get(fn["name"], [])
                    ext_callers = [
                        f"{Path(c['file']).as_posix()}:{c['line']}"
                        for c in callers
                        if Path(c["file"]).as_posix() != rel_posix
                    ][:3]
                    caller_note = f" *(called by: {', '.join(ext_callers)})*" if ext_callers else ""
                    lines.append(
                        f"- **Function `{fn['signature']}`** (L{fn['start_line']}-L{fn['end_line']}){fn_doc}{caller_note}"
                    )

        elif suffix in TS_JS_EXTENSIONS:
            tinfo = ts_meta.get(rel_posix, {})
            lcount = tinfo.get("line_count", 0)
            summary = tinfo.get("summary")
            if summary:
                lines.append(f"- **Purpose**: {summary} ({lcount} lines)")
            elif lcount:
                lines.append(f"- **Size**: {lcount} lines")

            exports = tinfo.get("exports") or []
            if exports:
                formatted_syms = [f"`{e['kind']} {e['name']}` (L{e['line']})" for e in exports[:12]]
                lines.append(f"- **Declarations**: {', '.join(formatted_syms)}")

        else:
            try:
                lcount = len((self.repo_dir / rel_posix).read_text(encoding="utf-8", errors="replace").splitlines())
                lines.append(f"- **Size**: {lcount} lines")
            except OSError:
                pass

        return "\n".join(lines)

    def _synthesize_overview_with_model(
        self,
        repo_name: str,
        git_section: str,
        modules_section: str,
        readme_overview: str,
    ) -> Optional[str]:
        """Optionally call configured LLM (<= 8s timeout) for an Architectural Overview & Recent Changes prose section."""
        if os.environ.get("WHACK_WIKI_NO_MODEL") == "1":
            return None

        get_prov_fn = getattr(_brain_config, "get_active_provider", None)
        configured_prov = (
            get_prov_fn()
            if callable(get_prov_fn)
            else str(os.environ.get("LLM_PROVIDER") or LLM_PROVIDER or "gemini").lower()
        )
        get_model_fn = getattr(_brain_config, "get_active_model", None)
        configured_model = (
            get_model_fn(configured_prov)
            if callable(get_model_fn)
            else str(os.environ.get("LLM_MODEL") or LLM_MODEL or "").strip()
        )

        provider_order = [configured_prov] + [
            p for p in ("gemini", "openai", "anthropic") if p != configured_prov
        ]

        selected_prov: Optional[str] = None
        api_key: Optional[str] = None
        for prov in provider_order:
            key = get_active_api_key(prov)
            if key and key.strip():
                selected_prov = prov
                api_key = key.strip()
                break

        if not selected_prov or not api_key:
            return None

        context_excerpt = (
            f"Repository: {repo_name}\n\n"
            f"README Summary:\n{readme_overview[:800]}\n\n"
            f"{git_section[:1200]}\n\n"
            f"Module Reference Excerpt:\n{modules_section[:5500]}"
        )
        prompt = (
            "You are a principal software architect maintaining a live repository wiki.\n"
            "Based strictly on the repository metadata, git HEAD commit activity, and AST module index below, "
            "write a concise markdown summary (180-300 words max) with two short subsections:\n"
            "### System Architecture & Data Flow\n"
            "### Recent Changes at `HEAD`\n"
            "Be factual, cite real module/file names from the input, and do not invent components.\n\n"
            f"{context_excerpt}"
        )

        def _invoke_llm() -> Optional[str]:
            if selected_prov in ("gemini", "google"):
                from google import genai

                model_name = (
                    configured_model
                    if (
                        configured_model
                        and "3.5" not in configured_model
                        and not configured_model.startswith(("gpt-", "claude-"))
                    )
                    else "gemini-2.5-flash"
                )
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                )
                return resp.text.strip() if getattr(resp, "text", None) else None

            if selected_prov == "openai":
                from openai import OpenAI

                model_name = (
                    configured_model
                    if (configured_model and not configured_model.startswith(("gemini", "claude-")))
                    else "gpt-4o-mini"
                )
                client = OpenAI(api_key=api_key, timeout=LLM_SYNTHESIS_TIMEOUT_SECONDS)
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=450,
                )
                content = resp.choices[0].message.content if resp.choices else None
                return content.strip() if content else None

            if selected_prov == "anthropic":
                model_name = (
                    configured_model
                    if (configured_model and configured_model.startswith("claude"))
                    else "claude-3-5-haiku-latest"
                )
                try:
                    from anthropic import Anthropic

                    client = Anthropic(api_key=api_key, timeout=LLM_SYNTHESIS_TIMEOUT_SECONDS)
                    msg = client.messages.create(
                        model=model_name,
                        max_tokens=450,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    blocks = [getattr(b, "text", "") for b in getattr(msg, "content", [])]
                    joined = "\n".join(b for b in blocks if b).strip()
                    return joined or None
                except ImportError:
                    import httpx

                    resp = httpx.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model_name,
                            "max_tokens": 450,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                        timeout=LLM_SYNTHESIS_TIMEOUT_SECONDS,
                    )
                    if resp.status_code == 200:
                        payload = resp.json()
                        blocks = [b.get("text", "") for b in payload.get("content", []) if isinstance(b, dict)]
                        joined = "\n".join(b for b in blocks if b).strip()
                        return joined or None
            return None

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_invoke_llm)
            return future.result(timeout=LLM_SYNTHESIS_TIMEOUT_SECONDS)
        except Exception:
            return None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _build_full_wiki_markdown(
        self,
        commit_hash: str,
        allow_model: bool = True,
    ) -> Tuple[str, List[str], bool]:
        """Generate complete repository wiki markdown, returning `(markdown, modules, used_model)`."""
        repo_name = self.repo_dir.name or str(self.repo_dir)
        short_commit = commit_hash[:12]
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        readme_overview = _extract_readme_summary(self.repo_dir / "README.md")
        root_pkg = _inspect_package_json(self.repo_dir / "package.json")
        git_section = self._build_git_activity_section(commit_hash)
        modules_section, module_names = self._scan_and_render_modules(commit_hash)

        used_model = False
        llm_prose: Optional[str] = None
        if allow_model:
            llm_prose = self._synthesize_overview_with_model(
                repo_name=repo_name,
                git_section=git_section,
                modules_section=modules_section,
                readme_overview=readme_overview,
            )
            if llm_prose:
                used_model = True

        body_parts: List[str] = [
            "<!-- WHACK Architecture Wiki -->",
            f"<!-- Commit: {commit_hash} -->",
            f"<!-- Generated: {now_iso} -->",
            f"<!-- UsedModel: {'true' if used_model else 'false'} -->",
            f"<!-- Modules: {', '.join(module_names)} -->",
            "",
            f"# Architecture Wiki: {repo_name} (`{short_commit}`)",
        ]

        overview_lines: List[str] = ["## Project Overview"]
        if readme_overview:
            overview_lines.append(readme_overview)
        else:
            overview_lines.append(
                f"Local architecture reference for `{repo_name}` generated dynamically from source AST and git metadata."
            )
        if root_pkg:
            overview_lines.append(f"\n- **Root Package**: {root_pkg}")
        if module_names:
            overview_lines.append(f"- **Discovered Modules ({len(module_names)})**: `{', '.join(module_names)}`")

        body_parts.append("\n".join(overview_lines))
        body_parts.append(git_section)

        if llm_prose:
            body_parts.append(f"## Architectural Overview & Recent Changes\n\n{llm_prose}")

        body_parts.append(modules_section)

        full_markdown = "\n\n".join(part.strip() for part in body_parts if part is not None) + "\n"
        return full_markdown, module_names, used_model

    def _write_dual_persistence(
        self,
        markdown_text: str,
        whack_wiki_path: Path,
        cache_wiki_path: Path,
    ) -> None:
        """Persist wiki to both `<repo_dir>/.whack/WIKI.md` and `WIKI_CACHE_DIR` safely."""
        try:
            cache_wiki_path.parent.mkdir(parents=True, exist_ok=True)
            cache_wiki_path.write_text(markdown_text, encoding="utf-8", newline="\n")
        except OSError:
            pass

        if self.repo_dir.exists():
            _ensure_git_excluded(self.repo_dir)
            try:
                whack_wiki_path.parent.mkdir(parents=True, exist_ok=True)
                whack_wiki_path.write_text(markdown_text, encoding="utf-8", newline="\n")
            except OSError:
                pass

    def update(self, allow_model: bool = True, force: bool = False) -> Dict[str, Any]:
        """Ensure `<repo_dir>/.whack/WIKI.md` and cache match `HEAD`, rebuilding if stale or forced."""
        commit_hash = self.get_head_commit()
        whack_wiki_path = self.get_whack_wiki_path()
        cache_wiki_path = self.get_cache_path(commit_hash)

        if not force:
            # Check if `.whack/WIKI.md` is already up to date for HEAD
            if whack_wiki_path.is_file():
                try:
                    existing_whack = whack_wiki_path.read_text(encoding="utf-8", errors="replace")
                    meta = _parse_wiki_metadata(existing_whack)
                    if meta["commit"] == commit_hash:
                        if not cache_wiki_path.is_file():
                            try:
                                cache_wiki_path.parent.mkdir(parents=True, exist_ok=True)
                                cache_wiki_path.write_text(existing_whack, encoding="utf-8", newline="\n")
                            except OSError:
                                pass
                        return {
                            "ok": True,
                            "repoPath": str(self.repo_dir),
                            "wikiPath": str(whack_wiki_path),
                            "cachePath": str(cache_wiki_path),
                            "commitHash": commit_hash,
                            "updated": False,
                            "usedModel": bool(meta["used_model"]),
                            "modules": list(meta["modules"]),
                        }
                except OSError:
                    pass

            # Check if commit cache already exists (e.g. branch switch) and sync `.whack/WIKI.md`
            if cache_wiki_path.is_file():
                try:
                    cached_text = cache_wiki_path.read_text(encoding="utf-8", errors="replace")
                    meta = _parse_wiki_metadata(cached_text)
                    synced_whack = False
                    if self.repo_dir.exists():
                        _ensure_git_excluded(self.repo_dir)
                        try:
                            whack_wiki_path.parent.mkdir(parents=True, exist_ok=True)
                            whack_wiki_path.write_text(cached_text, encoding="utf-8", newline="\n")
                            synced_whack = True
                        except OSError:
                            pass
                    return {
                        "ok": True,
                        "repoPath": str(self.repo_dir),
                        "wikiPath": str(whack_wiki_path),
                        "cachePath": str(cache_wiki_path),
                        "commitHash": commit_hash,
                        "updated": synced_whack,
                        "usedModel": bool(meta["used_model"]),
                        "modules": list(meta["modules"]),
                    }
                except OSError:
                    pass

        markdown_text, module_names, used_model = self._build_full_wiki_markdown(
            commit_hash=commit_hash,
            allow_model=allow_model,
        )
        self._write_dual_persistence(markdown_text, whack_wiki_path, cache_wiki_path)

        return {
            "ok": True,
            "repoPath": str(self.repo_dir),
            "wikiPath": str(whack_wiki_path),
            "cachePath": str(cache_wiki_path),
            "commitHash": commit_hash,
            "updated": True,
            "usedModel": used_model,
            "modules": module_names,
        }

    def get_or_build_wiki(self, allow_model: bool = True, force: bool = False) -> str:
        """Return full wiki markdown for current `HEAD`, reading cache or building when needed."""
        commit_hash = self.get_head_commit()
        whack_wiki_path = self.get_whack_wiki_path()
        cache_wiki_path = self.get_cache_path(commit_hash)

        if not force:
            if whack_wiki_path.is_file():
                try:
                    text = whack_wiki_path.read_text(encoding="utf-8", errors="replace")
                    if _parse_wiki_metadata(text).get("commit") == commit_hash:
                        return text
                except OSError:
                    pass
            if cache_wiki_path.is_file():
                try:
                    text = cache_wiki_path.read_text(encoding="utf-8", errors="replace")
                    # Best-effort keep `.whack/WIKI.md` aligned with current HEAD
                    if self.repo_dir.exists():
                        _ensure_git_excluded(self.repo_dir)
                        try:
                            whack_wiki_path.parent.mkdir(parents=True, exist_ok=True)
                            whack_wiki_path.write_text(text, encoding="utf-8", newline="\n")
                        except OSError:
                            pass
                    return text
                except OSError:
                    pass

        self.update(allow_model=allow_model, force=force)
        for candidate in (whack_wiki_path, cache_wiki_path):
            if candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

        markdown_text, _, _ = self._build_full_wiki_markdown(commit_hash, allow_model=allow_model)
        return markdown_text

    def get_wiki(self, module: str = "") -> str:
        """Return the wiki section relevant to `module` (or matching file), or full wiki if empty."""
        full_wiki = self.get_or_build_wiki(allow_model=False, force=False)
        if not module or not module.strip():
            return full_wiki

        raw_query = module.strip().replace("\\", "/").strip("/")
        raw_lower = raw_query.lower()
        stem_query = Path(raw_query).stem.lower()

        # Split into preamble + `### Module: <name>` sections
        parts = re.split(r"(?=^###\s+Module:\s+)", full_wiki, flags=re.MULTILINE)
        preamble = parts[0].strip() if parts else ""
        module_blocks: List[Tuple[str, str]] = []
        for block in parts[1:]:
            m = re.match(r"^###\s+Module:\s+([^\n]+)", block)
            if m:
                module_blocks.append((m.group(1).strip(), block.strip()))

        is_explicit_file_query = "/" in raw_query or any(
            raw_lower.endswith(ext) for ext in ALL_CODE_EXTENSIONS
        )

        # 1. If query looks like a root module name without nested path, check exact module match first
        if not ("/" in raw_query):
            for mod_name, block in module_blocks:
                if mod_name.lower() in (raw_lower, stem_query):
                    return block

        # 2. Search `#### File: `<path>`` subsections across modules
        matched_file_sections: List[str] = []
        for mod_name, block in module_blocks:
            file_chunks = re.split(r"(?=^####\s+File:\s+)", block, flags=re.MULTILINE)
            mod_header = file_chunks[0].strip()
            hits_in_mod: List[str] = []
            for fchunk in file_chunks[1:]:
                fm = re.match(r"^####\s+File:\s+`?([^`\n]+)`?", fchunk)
                if not fm:
                    continue
                fpath_str = fm.group(1).strip()
                fpath_lower = fpath_str.lower()
                fstem_lower = Path(fpath_str).stem.lower()
                fname_lower = Path(fpath_str).name.lower()

                if (
                    fpath_lower == raw_lower
                    or fpath_lower.endswith("/" + raw_lower)
                    or fname_lower == raw_lower
                    or fstem_lower == stem_query
                ):
                    hits_in_mod.append(fchunk.strip())

            if hits_in_mod:
                matched_file_sections.append(mod_header + "\n\n" + "\n\n".join(hits_in_mod))

        if matched_file_sections:
            return "\n\n".join(matched_file_sections)

        # 3. If path query had a top-level prefix (e.g. `core/src/...`), match top-level module
        if is_explicit_file_query and "/" in raw_lower:
            top_prefix = raw_lower.split("/", 1)[0]
            for mod_name, block in module_blocks:
                if mod_name.lower() == top_prefix:
                    return block

        # 4. Partial / substring match on module names
        partial_matches = [
            block
            for mod_name, block in module_blocks
            if raw_lower in mod_name.lower() or stem_query in mod_name.lower()
        ]
        if partial_matches:
            return "\n\n".join(partial_matches)

        # 5. Fallback to repository overview + git HEAD summary
        return preamble if preamble else full_wiki[:1200]

    # Backward-compatible alias
    def get_section(self, module: str = "") -> str:
        return self.get_wiki(module)


# Per-repository cached ArchitectureWiki managers keyed by resolved Path
_wiki_managers: Dict[Path, ArchitectureWiki] = {}


def get_wiki_manager(repo_dir: Optional[Path] = None) -> ArchitectureWiki:
    """Return a cached `ArchitectureWiki` manager for the resolved `repo_dir`."""
    target = _resolve_default_repo(repo_dir)
    manager = _wiki_managers.get(target)
    if manager is None:
        manager = ArchitectureWiki(target)
        _wiki_managers[target] = manager
    return manager


def get_wiki(module: str = "", repo_dir: Optional[Path] = None) -> str:
    """Read-only helper returning the wiki section for `module` (or full wiki if empty)."""
    return get_wiki_manager(repo_dir).get_wiki(module)


def update_repo_wiki(
    repo_dir: Optional[Path] = None,
    allow_model: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """Update `<repo_dir>/.whack/WIKI.md` and cache for `HEAD`, returning structured metadata."""
    return get_wiki_manager(repo_dir).update(allow_model=allow_model, force=force)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: `python -m brain.wiki --repo <path> --update [--no-model] [--force] [--quiet]`."""
    parser = argparse.ArgumentParser(
        prog="python -m brain.wiki",
        description="Generate or update the local WHACK Architecture Wiki (.whack/WIKI.md) on git HEAD.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=".",
        help="Target repository directory (default: current working directory '.')",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update <repo>/.whack/WIKI.md and cache for current HEAD",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Skip optional LLM prose synthesis and run fast deterministic AST + git generation",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration even if HEAD commit wiki already exists",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output on success (used by background git post-commit hooks)",
    )
    parser.add_argument(
        "--module",
        type=str,
        default="",
        help="Print a specific module or file section from the wiki",
    )

    args = parser.parse_args(argv)
    target_repo = Path(args.repo).expanduser().resolve()
    allow_model = not args.no_model

    if args.module and not args.update:
        if args.force:
            update_repo_wiki(repo_dir=target_repo, allow_model=allow_model, force=True)
        section = get_wiki(module=args.module, repo_dir=target_repo)
        if not args.quiet:
            print(section)
        return 0

    result = update_repo_wiki(
        repo_dir=target_repo,
        allow_model=allow_model,
        force=args.force,
    )
    if not args.quiet:
        print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
