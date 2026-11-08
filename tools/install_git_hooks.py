#!/usr/bin/env python3
"""Cross-platform installer for the WHACK local architecture wiki `post-commit` git hook.

Installs an idempotent POSIX `sh` hook into `<repo>/.git/hooks/post-commit`
(preserving any existing user post-commit commands), excludes `.whack/` via
`<git_dir>/info/exclude` so `git status` stays clean, and generates the initial
`<repo>/.whack/WIKI.md` immediately.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

BEGIN_MARKER = "# BEGIN WHACK WIKI HOOK"
END_MARKER = "# END WHACK WIKI HOOK"


def _resolve_git_path(repo_dir: Path, git_subpath: str) -> Optional[Path]:
    """Resolve a path inside `<git_dir>` using `git rev-parse --git-path`, with `.git/` fallback."""
    resolved = repo_dir.resolve()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-path", git_subpath],
            cwd=str(resolved),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw = Path(res.stdout.strip())
            return raw if raw.is_absolute() else (resolved / raw).resolve()
    except (OSError, subprocess.SubprocessError):
        pass

    dot_git = resolved / ".git"
    if dot_git.is_dir():
        return (dot_git / git_subpath).resolve()
    return None


def _ensure_exclude_entry(repo_dir: Path) -> bool:
    """Best-effort add `.whack/` to `<git_dir>/info/exclude` so git status stays clean."""
    exclude_path = _resolve_git_path(repo_dir, "info/exclude")
    if exclude_path is None:
        return False
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if exclude_path.exists():
            existing = exclude_path.read_text(encoding="utf-8", errors="replace")

        lines = {
            ln.strip()
            for ln in existing.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
        if ".whack/" in lines or ".whack" in lines:
            return True

        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(exclude_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{prefix}# WHACK local architecture wiki\n.whack/\n")
        return True
    except OSError:
        return False


def build_hook_block(
    whack_root: Optional[Path] = None,
    python_executable: Optional[Path] = None,
    sync_default: bool = False,
) -> str:
    """Build the idempotent POSIX `sh` snippet delimited by WHACK markers."""
    whack_posix = Path(whack_root or WORKSPACE_ROOT).resolve().as_posix()
    py_posix = Path(python_executable or sys.executable).resolve().as_posix()
    default_sync_val = "1" if sync_default else "0"

    lines = [
        BEGIN_MARKER,
        "# Auto-updates <repo>/.whack/WIKI.md on every git commit without dirtying git status.",
        "(",
        f'  WHACK_ROOT="{whack_posix}"',
        '  if [ ! -f "$WHACK_ROOT/brain/wiki.py" ]; then',
        '    CANDIDATE_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"',
        '    if [ -f "$CANDIDATE_ROOT/brain/wiki.py" ]; then',
        '      WHACK_ROOT="$CANDIDATE_ROOT"',
        "    fi",
        "  fi",
        '  if [ -f "$WHACK_ROOT/brain/wiki.py" ]; then',
        '    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"',
        '    PY_BIN="${WHACK_PYTHON:-}"',
        f'    if [ -z "$PY_BIN" ] && [ -f "{py_posix}" ]; then',
        f'      PY_BIN="{py_posix}"',
        "    fi",
        '    if [ -z "$PY_BIN" ]; then',
        "      for _cand in python3 python; do",
        '        if command -v "$_cand" >/dev/null 2>&1 && "$_cand" -c "import sys" >/dev/null 2>&1; then',
        '          PY_BIN="$_cand"',
        "          break",
        "        fi",
        "      done",
        "    fi",
        '    if [ -n "$PY_BIN" ]; then',
        f'      SYNC_MODE="${{WHACK_WIKI_SYNC:-{default_sync_val}}}"',
        '      if [ "$SYNC_MODE" = "1" ]; then',
        '        PYTHONPATH="$WHACK_ROOT" "$PY_BIN" -m brain.wiki --repo "$REPO_ROOT" --update --force --quiet </dev/null >/dev/null 2>&1 || true',
        "      else",
        '        PYTHONPATH="$WHACK_ROOT" "$PY_BIN" -m brain.wiki --repo "$REPO_ROOT" --update --force --quiet </dev/null >/dev/null 2>&1 &',
        "      fi",
        "    fi",
        "  fi",
        ") || true",
        END_MARKER,
    ]
    return "\n".join(lines)


def _merge_hook_content(existing_content: str, whack_block: str) -> str:
    """Insert or replace the WHACK hook block while preserving any user hook lines."""
    normalized = existing_content.replace("\r\n", "\n").replace("\r", "\n")

    if not normalized.strip():
        return f"#!/usr/bin/env sh\n\n{whack_block}\n"

    pattern = re.compile(
        rf"{re.escape(BEGIN_MARKER)}[\s\S]*?{re.escape(END_MARKER)}",
        flags=re.MULTILINE,
    )
    if pattern.search(normalized):
        updated = pattern.sub(lambda _m: whack_block, normalized)
    else:
        sep = "\n" if normalized.endswith("\n") else "\n\n"
        updated = f"{normalized}{sep}{whack_block}\n"

    if not updated.lstrip().startswith("#!"):
        updated = f"#!/usr/bin/env sh\n\n{updated.lstrip()}"
    if not updated.endswith("\n"):
        updated += "\n"
    return updated


def install_post_commit_hook(
    repo_dir: Path | str,
    sync: bool = False,
    initial_update: bool = True,
    allow_model: bool = False,
) -> Dict[str, Any]:
    """Install the WHACK `post-commit` hook into `repo_dir` and optionally build initial `.whack/WIKI.md`."""
    resolved_repo = Path(repo_dir).expanduser().resolve()
    if not resolved_repo.exists():
        return {
            "ok": False,
            "repoPath": str(resolved_repo),
            "error": f"Repository directory does not exist: {resolved_repo}",
        }

    hook_path = _resolve_git_path(resolved_repo, "hooks/post-commit")
    if hook_path is None:
        return {
            "ok": False,
            "repoPath": str(resolved_repo),
            "error": f"Not a git repository (could not resolve .git/hooks/post-commit): {resolved_repo}",
        }

    hook_block = build_hook_block(
        whack_root=WORKSPACE_ROOT,
        python_executable=Path(sys.executable),
        sync_default=sync,
    )

    try:
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        existing_text = ""
        if hook_path.exists():
            existing_text = hook_path.read_text(encoding="utf-8", errors="replace")

        merged_text = _merge_hook_content(existing_text, hook_block)
        with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(merged_text)

        try:
            current_mode = hook_path.stat().st_mode
            os.chmod(
                hook_path,
                current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRUSR | stat.S_IWUSR,
            )
        except OSError:
            pass
    except OSError as exc:
        return {
            "ok": False,
            "repoPath": str(resolved_repo),
            "hookPath": str(hook_path),
            "error": f"Failed to write post-commit hook: {exc}",
        }

    excluded = _ensure_exclude_entry(resolved_repo)

    wiki_result: Optional[Dict[str, Any]] = None
    if initial_update:
        try:
            from brain.wiki import update_repo_wiki

            wiki_result = update_repo_wiki(
                repo_dir=resolved_repo,
                allow_model=allow_model,
                force=True,
            )
        except Exception as exc:
            wiki_result = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "repoPath": str(resolved_repo),
        "hookPath": str(hook_path),
        "gitExcluded": excluded,
        "syncMode": sync,
        "wiki": wiki_result,
    }


def _default_target_repos() -> List[Path]:
    """Default targets when no repo paths are passed: WHACK workspace (+ active external demo repo if initialized)."""
    targets: List[Path] = [WORKSPACE_ROOT.resolve()]

    env_demo = (
        os.environ.get("WHACK_DEMO_REPO")
        or os.environ.get("DEMO_REPO_DIR")
        or str(Path("~/whack-demo-repo").expanduser())
    )
    demo_candidate = Path(env_demo).expanduser().resolve()
    if demo_candidate != WORKSPACE_ROOT.resolve() and (demo_candidate / ".git").exists():
        targets.append(demo_candidate)

    return targets


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the WHACK post-commit architecture wiki hook into one or more git repositories."
    )
    parser.add_argument(
        "repos",
        nargs="*",
        help="Target git repository path(s). Defaults to WHACK root (and ~/whack-demo-repo if present).",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Configure post-commit hook to run synchronously by default (useful for deterministic CI/testing).",
    )
    parser.add_argument(
        "--no-initial-update",
        action="store_true",
        help="Skip generating the initial <repo>/.whack/WIKI.md immediately after installing the hook.",
    )
    parser.add_argument(
        "--allow-model",
        action="store_true",
        help="Allow optional LLM prose synthesis during the initial wiki generation.",
    )

    args = parser.parse_args(argv)
    repo_paths = (
        [Path(r).expanduser().resolve() for r in args.repos]
        if args.repos
        else _default_target_repos()
    )

    exit_code = 0
    for repo in repo_paths:
        res = install_post_commit_hook(
            repo_dir=repo,
            sync=args.sync,
            initial_update=not args.no_initial_update,
            allow_model=args.allow_model,
        )
        if res.get("ok"):
            wiki_info = res.get("wiki") or {}
            wiki_path = wiki_info.get("wikiPath", str(repo / ".whack" / "WIKI.md"))
            commit_short = str(wiki_info.get("commitHash", ""))[:8]
            print(
                f"✓ Installed WHACK post-commit hook: {res['hookPath']}\n"
                f"  Repo: {res['repoPath']} (HEAD: {commit_short or 'n/a'})\n"
                f"  Wiki: {wiki_path}"
            )
        else:
            print(f"✗ Failed for {repo}: {res.get('error')}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
