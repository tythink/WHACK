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

def run_git(cmd, cwd):
    res = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(cmd)} failed:\n{res.stderr.strip()}")
    return res.stdout.strip()

def build_repo(target_dir: Path) -> Path:
    target_dir = target_dir.resolve()
    print(f"🔨 Rebuilding demo-repo at: {target_dir}")

    # Wipe existing directory for a fresh from-scratch reset
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Initialize fresh git repo
    run_git(["init", "-b", "main"], cwd=target_dir)
    run_git(["config", "user.name", "Demo Engineer"], cwd=target_dir)
    run_git(["config", "user.email", "engineer@example.com"], cwd=target_dir)

    for i, step in enumerate(COMMITS, 1):
        stage_name = step["stage"]
        stage_path = STAGES_DIR / stage_name
        if not stage_path.exists():
            raise FileNotFoundError(f"Stage directory not found: {stage_path}")

        message = step["message"]
        print(f"  [{i}/{len(COMMITS)}] Applying stage {stage_name}: '{message}'")

        # Copy files from stage into target_dir
        for item in stage_path.iterdir():
            dest = target_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Stage and commit
        run_git(["add", "."], cwd=target_dir)
        run_git(["commit", "-m", message], cwd=target_dir)

    print(f"✓ Repository built successfully at {target_dir}")
    log_output = run_git(["log", "--oneline"], cwd=target_dir)
    print("Recent commits:")
    for line in log_output.splitlines():
        print(f"  {line}")

    return target_dir

def main():
    parser = argparse.ArgumentParser(description="Build/reset fresh demo-repo outside the workspace.")
    parser.add_argument(
        "target",
        nargs="?",
        default=os.environ.get("WHACK_DEMO_REPO") or os.environ.get("DEMO_REPO_DIR") or str(DEFAULT_TARGET_DIR),
        help=f"Target directory for repo (default: {DEFAULT_TARGET_DIR})",
    )
    args = parser.parse_args()
    target_path = Path(args.target).expanduser().resolve()
    build_repo(target_path)

if __name__ == "__main__":
    main()
