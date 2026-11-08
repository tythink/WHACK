#!/usr/bin/env python3
"""Build a fresh demo-repo git repository from stages in a path outside this workspace.

Can be run repeatedly as a one-command reset.
Default target: ~/whack-demo-repo
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Base paths
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools import install_git_hooks

STAGES_DIR = (WORKSPACE_ROOT / "demo-repo-stages").resolve()
DEFAULT_TARGET_DIR = Path(os.path.expanduser("~/whack-demo-repo")).resolve()

COMMITS = [
    {
        "stage": "01-initial",
        "message": "feat: initial working metrics pipeline and offset calculations",
    },
    {
        "stage": "02-tests",
        "message": "feat: add sliding window tests and 8 scripted scenario baselines",
    },
    {
        "stage": "03-bug",
        "message": "perf: optimize window buffer and index calculation",
    },
]

SCENARIO_BRANCHES = [
    {
        "branch": "scenario-1",
        "stage": "03-bug",
        "message": "perf: optimize window buffer and index calculation",
    },
    {
        "branch": "scenario-2",
        "stage": "03-bug-2-scoping",
        "message": "fix: aggregate nested stage metric calculations",
    },
    {
        "branch": "scenario-3",
        "stage": "03-bug-3-none-deref",
        "message": "feat: simplify telemetry event metadata extraction",
    },
    {
        "branch": "scenario-4",
        "stage": "03-bug-4-mutable-default",
        "message": "feat: optimize audit log history buffer",
    },
    {
        "branch": "scenario-5",
        "stage": "03-bug-5-unawaited",
        "message": "feat: fetch async metric readings",
    },
    {
        "branch": "scenario-6",
        "stage": "03-bug-6-zero-division",
        "message": "perf: inline throughput calculation",
    },
    {
        "branch": "scenario-7",
        "stage": "03-bug-7-regex",
        "message": "fix: parse log line status codes",
    },
    {
        "branch": "scenario-8",
        "stage": "03-bug-8-type-mismatch",
        "message": "feat: compare raw readings directly",
    },
]

def run_git(cmd, cwd):
    res = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"git {' '.join(cmd)} failed:\n{err_msg}")
    return res.stdout.strip()

def _remove_readonly(func, path, exc):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def copy_stage_files(stage_path: Path, target_dir: Path):
    for item in stage_path.iterdir():
        dest = target_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest, onexc=_remove_readonly)
            shutil.copytree(item, dest, copy_function=shutil.copy)
        else:
            shutil.copy(item, dest)

def build_repo(target_dir: Path, sync_hook: bool = False) -> Path:
    target_dir = target_dir.resolve()
    print(f"🔨 Rebuilding demo-repo at: {target_dir}")

    # Wipe existing directory for a fresh from-scratch reset
    if target_dir.exists():
        shutil.rmtree(target_dir, onexc=_remove_readonly)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Initialize fresh git repo
    run_git(["init", "-b", "main"], cwd=target_dir)
    run_git(["config", "user.name", "Demo Engineer"], cwd=target_dir)
    run_git(["config", "user.email", "engineer@example.com"], cwd=target_dir)

    # 1. Apply 01-initial
    copy_stage_files(STAGES_DIR / "01-initial", target_dir)
    run_git(["add", "."], cwd=target_dir)
    run_git(["commit", "-m", COMMITS[0]["message"]], cwd=target_dir)

    # 2. Apply 02-tests (baseline passing commit)
    copy_stage_files(STAGES_DIR / "02-tests", target_dir)
    run_git(["add", "."], cwd=target_dir)
    run_git(["commit", "-m", COMMITS[1]["message"]], cwd=target_dir)
    baseline_commit = run_git(["rev-parse", "HEAD"], cwd=target_dir)
    run_git(["tag", "-f", "baseline"], cwd=target_dir)

    # 3. Apply 03-bug on main (Scenario 1)
    copy_stage_files(STAGES_DIR / "03-bug", target_dir)
    run_git(["add", "."], cwd=target_dir)
    run_git(["commit", "-m", COMMITS[2]["message"]], cwd=target_dir)
    run_git(["branch", "-f", "scenario-1"], cwd=target_dir)
    print(f"  ✓ Created main & scenario-1 at {run_git(['rev-parse', '--short', 'HEAD'], cwd=target_dir)}")

    # 4. Create scenario branches 2 through 8 from baseline commit
    for sc in SCENARIO_BRANCHES[1:]:
        branch = sc["branch"]
        stage = sc["stage"]
        msg = sc["message"]
        run_git(["checkout", "-B", branch, baseline_commit], cwd=target_dir)
        copy_stage_files(STAGES_DIR / stage, target_dir)
        run_git(["add", "."], cwd=target_dir)
        run_git(["commit", "-m", msg], cwd=target_dir)
        head_short = run_git(["rev-parse", "--short", "HEAD"], cwd=target_dir)
        print(f"  ✓ Created {branch} at {head_short}: '{msg}'")

    # 5. Return to main
    run_git(["checkout", "main"], cwd=target_dir)

    # 6. Install WHACK post-commit hook and generate initial .whack/WIKI.md
    hook_res = install_git_hooks.install_post_commit_hook(
        target_dir,
        sync=sync_hook,
        initial_update=True,
        allow_model=False,
    )
    if hook_res.get("ok"):
        wiki_info = hook_res.get("wiki") or {}
        print(f"  ✓ Installed WHACK post-commit hook & generated wiki: {wiki_info.get('wikiPath', target_dir / '.whack' / 'WIKI.md')}")
    else:
        print(f"  ⚠ Warning: hook installation skipped: {hook_res.get('error')}")

    print(f"\n✓ Repository built successfully at {target_dir} with all 8 scenario branches.")
    return target_dir

def main():
    parser = argparse.ArgumentParser(description="Build/reset fresh demo-repo outside the workspace.")
    parser.add_argument(
        "target",
        nargs="?",
        default=os.environ.get("WHACK_DEMO_REPO") or os.environ.get("DEMO_REPO_DIR") or str(DEFAULT_TARGET_DIR),
        help=f"Target directory for repo (default: {DEFAULT_TARGET_DIR})",
    )
    parser.add_argument(
        "--sync-hook",
        action="store_true",
        help="Configure the installed post-commit hook to run synchronously by default.",
    )
    args = parser.parse_args()
    target_path = Path(args.target).expanduser().resolve()
    build_repo(target_path, sync_hook=args.sync_hook)

if __name__ == "__main__":
    main()
