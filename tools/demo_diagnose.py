#!/usr/bin/env python3
"""CLI runner for WHACK Brain workspace diagnosis.

Features:
- Targets any repository passed via --repo <path> (defaulting to active workspace or DEMO_REPO_DIR)
- Screenshot opt-in via --screenshot or --screenshot-file
- Supports --no-wiki, --no-codemap, --check-key
- Streams live diagnosis events and prints summary, quip, file, lines, source, error, and suggestedFix
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from brain.config import (
    DEFAULT_GENERATED_DEMO_REPO,
    DEMO_REPO_DIR,
    get_active_api_key,
    get_active_model,
    get_active_provider,
    resolve_repo_dir,
    sanitize_secret,
)
from brain.models import DiagnoseRequest, DiagnosisResponse, EditorState, TerminalState
from brain.agent import DiagnosticEngine
from brain.screenshot import capture_system_screenshot, process_image_file
from brain.tools import apply_fix_to_repo, rollback_repo_checkpoint


def check_key() -> None:
    """Perform minimal API call to verify credentials and report active provider/model."""
    provider = get_active_provider()
    model = get_active_model(provider)
    print("=" * 60)
    print("🔑 CHECKING API KEY & CONNECTIVITY")
    print("=" * 60)
    print(f"Provider:   {provider}")
    print(f"Model Name: {model}")

    key = get_active_api_key(provider)
    if not key:
        print(f"\n❌ Failure: No API key configured for provider '{provider}'.")
        print("   Please populate GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY in brain/.env.")
        sys.exit(1)

    print("📡 Sending minimal test request to verify API key and model connectivity...")
    t0 = time.time()
    try:
        if provider in ("gemini", "google"):
            from google import genai
            client = genai.Client(api_key=key)
            res = client.models.generate_content(
                model=model,
                contents="Ping. Respond with 'pong'.",
            )
            elapsed = time.time() - t0
            print(f"\n✅ Success! API key and model '{model}' are active and working.")
            print(f"   Response Time: {elapsed:.2f}s")
            if getattr(res, "text", None):
                print(f"   Model Reply:   {res.text.strip()}")
            print("=" * 60)
            sys.exit(0)
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=key)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Ping"}],
                max_tokens=5,
            )
            elapsed = time.time() - t0
            print(f"\n✅ Success! API key and model '{model}' are active and working ({elapsed:.2f}s).")
            print("=" * 60)
            sys.exit(0)
        elif provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Ping"}],
            )
            elapsed = time.time() - t0
            print(f"\n✅ Success! API key and model '{model}' are active and working ({elapsed:.2f}s).")
            print("=" * 60)
            sys.exit(0)
        else:
            print(f"\n❌ Failure: Unsupported provider '{provider}'.")
            sys.exit(1)
    except Exception as e:
        clean_err = sanitize_secret(str(e))
        print(f"\n❌ Connectivity Failure: {clean_err}")
        print("=" * 60)
        sys.exit(1)


def capture_workspace_test_failure(repo_dir: Path, test_cmd: Optional[str] = None) -> tuple[str, int, str]:
    """Run test command in target workspace if configured or if pytest tests exist."""
    if test_cmd:
        cmd_str = test_cmd
        try:
            res = subprocess.run(
                cmd_str,
                shell=True,
                cwd=str(repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12,
            )
            return cmd_str, res.returncode, res.stdout.strip()
        except Exception as e:
            return cmd_str, -1, f"Test command failed to execute: {e}"

    if (repo_dir / "tests").exists():
        cmd_list = [sys.executable, "-m", "pytest", "tests/"]
        try:
            res = subprocess.run(
                cmd_list,
                cwd=str(repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12,
            )
            return "pytest tests/", res.returncode, res.stdout.strip()
        except Exception as e:
            return "pytest tests/", -1, f"Pytest execution failed: {e}"

    return "", 0, ""


async def run_demo(args: argparse.Namespace) -> None:
    if args.repo:
        raw_target = Path(args.repo).expanduser()
        if not raw_target.exists() and raw_target.resolve() == DEFAULT_GENERATED_DEMO_REPO:
            from tools.setup_demo_repo import build_repo
            print(f"📦 Initializing generated demo repo at {raw_target}...")
            build_repo(raw_target)
        target_repo = resolve_repo_dir(str(raw_target))
    else:
        cwd = Path.cwd().resolve()
        target_repo = DEMO_REPO_DIR if DEMO_REPO_DIR.exists() else cwd

    print(f"📂 Target Workspace: {target_repo}")

    screenshot_b64 = None
    if args.screenshot:
        print("📸 Capturing live display (--screenshot)...")
        shot = capture_system_screenshot()
        if shot:
            screenshot_b64 = shot["base64"]
            print(
                f"   Captured screenshot (raw: {shot.get('raw_width', shot['width'])}x"
                f"{shot.get('raw_height', shot['height'])} -> resized: {shot['width']}x{shot['height']}, "
                f"{shot['size_bytes']:,} bytes)"
            )
    elif args.screenshot_file:
        file_path = Path(args.screenshot_file).expanduser().resolve()
        if file_path.exists():
            print(f"📸 Loading screenshot file: {file_path}")
            shot = process_image_file(str(file_path))
            screenshot_b64 = shot["base64"]
        else:
            print(f"⚠️ Screenshot file not found: {file_path}")

    cmd_used, exit_code, test_output = capture_workspace_test_failure(target_repo, args.test_cmd)
    if args.tail_output:
        test_output = args.tail_output
        exit_code = 1

    if cmd_used:
        print(f"⚡ Ran `{cmd_used}` (exit={exit_code}, {len(test_output.splitlines())} output lines).\n")

    editor_state = (
        EditorState(activeFile=args.active_file, cursorLine=args.cursor_line)
        if (args.active_file or args.cursor_line)
        else None
    )
    terminal_state = (
        TerminalState(command=cmd_used or None, exitCode=exit_code, tailOutput=test_output)
        if (cmd_used or test_output)
        else None
    )

    req = DiagnoseRequest(
        hintLevel=args.hint_level,
        repoPath=str(target_repo),
        captureScreenshot=bool(args.screenshot),
        screenshot=screenshot_b64,
        editorState=editor_state,
        terminalState=terminal_state,
    )

    use_codemap = not args.no_codemap
    use_wiki = not args.no_wiki
    engine = DiagnosticEngine()

    print(
        f"🧠 [Brain] Launching diagnosis stream (workspace={target_repo.name}, "
        f"codemap={use_codemap}, wiki={use_wiki}, screenshot={bool(screenshot_b64)})..."
    )
    print("-" * 60)

    final_diagnosis: Optional[DiagnosisResponse] = None
    event_type = ""
    stream_gen = engine.diagnose_stream(
        req,
        use_codemap=use_codemap,
        use_wiki=use_wiki,
        enable_screenshot=False,
        no_fallback=args.no_fallback,
    )

    async for event_chunk in stream_gen:
        for line in event_chunk.splitlines():
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:].strip()
                if event_type == "thought":
                    print(f"  💭 {data_str}")
                elif event_type == "citation":
                    print(f"  📌 Citation: {data_str}")
                elif event_type == "tools":
                    print(f"  🛠️ Tools executed: {data_str}")
                elif event_type == "diagnosis":
                    data_obj = json.loads(data_str)
                    final_diagnosis = DiagnosisResponse(**data_obj)

    print("-" * 60)
    print("\n🎯 DIAGNOSIS RESULT:")
    if not final_diagnosis:
        print("❌ Failed to receive final diagnosis payload.")
        return

    if final_diagnosis.source == "offline":
        print("\n" + "!" * 60)
        print("🚨 BRAIN LLM OFFLINE")
        print("!" * 60 + "\n")

    print(f"Source:       {final_diagnosis.source}")
    print(f"Error:        {final_diagnosis.error or 'None'}")
    print(f"Summary:      {final_diagnosis.summary}")
    print(f"Quip:         \"{final_diagnosis.quip}\"")
    print(f"File:         {final_diagnosis.file or 'None'}")
    print(f"Lines:        {final_diagnosis.lines or 'None'}")
    print(f"Hint Level:   {final_diagnosis.hintLevel}")
    print(f"Uncertain:    {final_diagnosis.uncertain}")
    print(f"Confidence:   {final_diagnosis.confidence}")
    print(f"Workspace:    {final_diagnosis.repoPath or target_repo}")

    if final_diagnosis.explanation:
        print(f"\nExplanation:\n{final_diagnosis.explanation}")

    if final_diagnosis.suggestedFix:
        fix = final_diagnosis.suggestedFix
        print("\n" + "=" * 25 + " 🔧 SUGGESTED FIX " + "=" * 25)
        print(f"Target File: {fix.file} (lines {fix.startLine}-{fix.endLine})")
        print("Original Code:")
        print(f"{fix.oldText}")
        print("Suggested Replacement:")
        print(f"{fix.newText}")
        print("=" * 68)

        if args.auto_apply:
            apply_ans = "y"
            print("\n👉 Apply this fix? [y/n]: y (auto-apply)")
        elif sys.stdin.isatty():
            apply_ans = input("\n👉 Apply this fix? [y/n]: ").strip().lower()
        else:
            apply_ans = "n"

        if apply_ans in ("y", "yes"):
            apply_res = apply_fix_to_repo(fix, repo_dir=target_repo)
            if apply_res.get("success"):
                print(f"✅ Fix applied! Checkpoint: {apply_res['checkpoint_hash']}")
                print(
                    f"   Tests Before: {'PASSED' if apply_res['before_passed'] else 'FAILED'} -> "
                    f"Tests After: {'PASSED' if apply_res['after_passed'] else 'FAILED'}"
                )
                if args.auto_rollback:
                    rb_ans = "y"
                elif sys.stdin.isatty():
                    rb_ans = input("\n👉 Rollback to checkpoint? [y/n]: ").strip().lower()
                else:
                    rb_ans = "n"
                if rb_ans in ("y", "yes"):
                    rb_res = rollback_repo_checkpoint(apply_res["checkpoint_hash"], repo_dir=target_repo)
                    if rb_res.get("success"):
                        print(f"✅ Rolled back to {rb_res['rolled_back_to']}.")
                    else:
                        print(f"❌ Rollback failed: {rb_res.get('error')}")
            else:
                print(f"❌ Failed to apply fix: {apply_res.get('error')}")
    else:
        print("\nSuggestedFix: None")

    trace = final_diagnosis.trace
    if trace:
        print("\n" + "=" * 25 + " EXECUTION TRACE " + "=" * 25)
        print(f"Provider / Model:      {trace.provider} / {trace.model_name}")
        print(f"Steps Used:            {trace.steps_used}")
        print(f"Total Time:            {trace.total_time:.3f}s (Network: {trace.time_network:.3f}s)")
        print(f"Tools Called:          {trace.tools_called if trace.tools_called else 'None'}")
        if trace.structured_edit_validation:
            print(f"Structured Fix Check:  {trace.structured_edit_validation}")
        print("=" * 67)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WHACK Brain workspace diagnosis CLI.")
    parser.add_argument("--check-key", action="store_true", default=False, help="Verify API key connectivity and exit")
    parser.add_argument("--no-fallback", action="store_true", default=False, help="Raise error if LLM call fails")
    parser.add_argument("--repo", default=None, help="Target workspace path (defaults to DEMO_REPO_DIR or cwd)")
    parser.add_argument("--hint-level", default="hint", choices=["nudge", "hint", "full_explanation", "full"], help="Hint level")
    parser.add_argument("--active-file", default=None, help="Optional active editor file path")
    parser.add_argument("--cursor-line", type=int, default=None, help="Optional active editor cursor line")
    parser.add_argument("--test-cmd", default=None, help="Optional test command to execute in workspace")
    parser.add_argument("--tail-output", default=None, help="Optional explicit failing terminal output")
    parser.add_argument("--auto-apply", action="store_true", default=False, help="Automatically apply validated suggestedFix")
    parser.add_argument("--auto-rollback", action="store_true", default=False, help="Automatically rollback after apply")
    parser.add_argument("--screenshot", action="store_true", default=False, help="Capture live display screenshot")
    parser.add_argument("--screenshot-file", type=str, default=None, help="Load screenshot from image file")
    parser.add_argument("--no-wiki", action="store_true", default=False, help="Disable architecture wiki context")
    parser.add_argument("--no-codemap", action="store_true", default=False, help="Disable AST code map context")

    args = parser.parse_args()
    if args.check_key:
        check_key()
    asyncio.run(run_demo(args))


if __name__ == "__main__":
    main()
