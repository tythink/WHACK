#!/usr/bin/env python3
"""One-command runner for WHACK Brain diagnosis.

Features:
- Auto-regenerates demo repo at ~/whack-demo-repo if missing
- Screenshot strictly opt-in via --screenshot or --screenshot-file (default: never captures)
- Supports --no-wiki, --no-codemap
- Streams diagnosis live to stdout
- Prints full execution trace and context part breakdown
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

# Add workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from brain.config import (
    DEFAULT_GENERATED_DEMO_REPO,
    DEMO_REPO_DIR,
    LLM_PROVIDER,
    LLM_MODEL,
    get_active_api_key,
    sanitize_secret,
)
from brain.models import DiagnoseRequest, EditorState, TerminalState
from brain.agent import DiagnosticEngine
from brain.screenshot import capture_system_screenshot, process_image_file
from tools.setup_demo_repo import build_repo

def check_key():
    """Perform minimal API call to check credentials and report provider and model name."""
    print("=" * 60)
    print("🔑 CHECKING API KEY & CONNECTIVITY")
    print("=" * 60)
    print(f"Provider:   {LLM_PROVIDER}")
    print(f"Model Name: {LLM_MODEL}")

    key = get_active_api_key(LLM_PROVIDER)
    if not key or not key.strip():
        print(f"\n❌ Failure: No API key found for provider '{LLM_PROVIDER}'.")
        print(f"   Please check brain/.env or set GEMINI_API_KEY / OPENAI_API_KEY.")
        sys.exit(1)

    print("📡 Sending minimal test request to verify API key and model connectivity...")
    t0 = time.time()
    try:
        if LLM_PROVIDER in ("gemini", "google"):
            from google import genai
            client = genai.Client(api_key=key.strip())
            res = client.models.generate_content(
                model=LLM_MODEL or "gemini-3.5-flash-lite",
                contents="Ping. Respond with 'pong'.",
            )
            elapsed = time.time() - t0
            print(f"\n✅ Success! API key and model '{LLM_MODEL or 'gemini-3.5-flash-lite'}' are active and working.")
            print(f"   Response Time: {elapsed:.2f}s")
            if hasattr(res, "text") and res.text:
                print(f"   Model Reply:   {res.text.strip()}")
            if hasattr(res, "usage_metadata") and res.usage_metadata:
                print(f"   Tokens:        Total={res.usage_metadata.total_token_count} (prompt={res.usage_metadata.prompt_token_count}, completion={res.usage_metadata.candidates_token_count})")
            print("=" * 60)
            sys.exit(0)
        elif LLM_PROVIDER == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=key.strip())
            res = client.chat.completions.create(
                model=LLM_MODEL or "gpt-4o-mini",
                messages=[{"role": "user", "content": "Ping"}],
                max_tokens=5,
            )
            elapsed = time.time() - t0
            print(f"\n✅ Success! API key and model '{LLM_MODEL or 'gpt-4o-mini'}' are active and working.")
            print(f"   Response Time: {elapsed:.2f}s")
            print("=" * 60)
            sys.exit(0)
        else:
            print(f"\n❌ Failure: Unsupported provider '{LLM_PROVIDER}'.")
            sys.exit(1)
    except Exception as e:
        elapsed = time.time() - t0
        clean_err = sanitize_secret(str(e))
        err_lower = clean_err.lower()
        category = "Unknown error"
        if "404" in err_lower or "not found" in err_lower or "unknown model" in err_lower:
            category = "Unknown model name / Model not available"
        elif "401" in err_lower or "403" in err_lower or "api_key_invalid" in err_lower or "unauthenticated" in err_lower or "invalid api key" in err_lower:
            category = "Invalid API Key"
        elif "429" in err_lower or "quota" in err_lower or "resource_exhausted" in err_lower:
            category = "Quota exceeded / Rate limit"
        elif "connect" in err_lower or "network" in err_lower or "timeout" in err_lower or "nodename" in err_lower:
            category = "Network / Connectivity error"

        print(f"\n❌ Failure [{category}]: {clean_err}")
        print("=" * 60)
        sys.exit(1)

def ensure_demo_repo(target_dir: Path):
    """Regenerate demo repo at target_dir if missing."""
    if not (target_dir / ".git").exists() or not (target_dir / "utils.py").exists():
        print(f"📦 Demo repo not found at {target_dir}. Running tools/setup_demo_repo.py...")
        build_repo(target_dir)
        print("✓ Demo repo initialized.\n")

def get_demo_failure(repo_dir: Path) -> str:
    """Run pytest in target repo to capture realistic failure traceback."""
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/"],
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
        return res.stdout.strip()
    except Exception as e:
        return f"IndexError: Offset end 6 exceeds data length 5 in utils.py ({str(e)})"

async def run_demo(args):
    target_repo = Path(args.repo or DEFAULT_GENERATED_DEMO_REPO).expanduser().resolve()
    ensure_demo_repo(target_repo)

    # 1. Screenshot handling
    screenshot_b64 = None
    if args.screenshot:
        print("📸 Capturing screen (--screenshot flag passed)...")
        shot = capture_system_screenshot()
        if shot:
            screenshot_b64 = shot["base64"]
            print(f"   Captured screenshot ({shot['width']}x{shot['height']}, {shot['size_bytes']} bytes)")
    elif args.screenshot_file:
        file_path = Path(args.screenshot_file).expanduser().resolve()
        if file_path.exists():
            print(f"📸 Loading screenshot file from: {file_path}")
            shot = process_image_file(str(file_path))
            screenshot_b64 = shot["base64"]
        else:
            print(f"⚠️ Screenshot file not found: {file_path}")
    else:
        # Default: NEVER capture screen
        screenshot_b64 = None

    # 2. Capture terminal failure
    print("⚡ Running test in demo repository to capture terminal state...")
    test_output = get_demo_failure(target_repo)
    print(f"   Captured {len(test_output.splitlines())} lines of test output.\n")

    req = DiagnoseRequest(
        hintLevel=args.hint_level,
        screenshot=screenshot_b64,
        editorState=EditorState(activeFile=args.active_file, cursorLine=args.cursor_line),
        terminalState=TerminalState(
            command="pytest tests/",
            exitCode=1,
            tailOutput=test_output,
        ),
    )

    use_codemap = not args.no_codemap
    use_wiki = not args.no_wiki

    engine = DiagnosticEngine()

    print(f"🧠 [Brain] Launching diagnosis stream (codemap={use_codemap}, wiki={use_wiki}, screenshot={bool(screenshot_b64)})...")
    print("-" * 60)

    final_diagnosis = None
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
                    print(f"  📌 Citation found: {data_str}")
                elif event_type == "tools":
                    print(f"  🛠️ Tools executed: {data_str}")
                elif event_type == "diagnosis":
                    import json
                    data_obj = json.loads(data_str)
                    from brain.models import DiagnosisResponse
                    final_diagnosis = DiagnosisResponse(**data_obj)

    print("-" * 60)
    print("\n🎯 DIAGNOSIS RESULT:")
    if final_diagnosis:
        if final_diagnosis.source == "offline":
            print("\n" + "!" * 60)
            print("🚨 OFFLINE FALLBACK - not an AI diagnosis")
            print("!" * 60 + "\n")

        print(f"Source:      {final_diagnosis.source.upper()}")
        print(f"Summary:     {final_diagnosis.summary}")
        print(f"File:        {final_diagnosis.file or 'None'}")
        print(f"Lines:       {final_diagnosis.lines or 'None'}")
        print(f"Hint Level:  {final_diagnosis.hintLevel}")
        print(f"Uncertain:   {final_diagnosis.uncertain}")
        print(f"Confidence:  {final_diagnosis.confidence}")
        print(f"Quip:        \"{final_diagnosis.quip}\"")
        if final_diagnosis.explanation:
            print(f"\nExplanation:\n{final_diagnosis.explanation}")

        # Requirement 7: TRACE & Requirement 6 diff overlap
        trace = final_diagnosis.trace
        if trace:
            print("\n" + "=" * 25 + " EXECUTION TRACE " + "=" * 25)
            print(f"Diagnosis Source:      {final_diagnosis.source.upper()}")
            print(f"Provider:              {trace.provider}")
            print(f"Model Name:            {trace.model_name}")
            print(f"Steps Used:            {trace.steps_used}")
            print(f"Time to First Output:  {trace.time_to_first_output:.3f}s")
            print(f"Total Time:            {trace.total_time:.3f}s")
            print(f"Used Fallback:         {trace.used_fallback}")
            if trace.fallback_reason:
                print(f"Fallback Reason:       {trace.fallback_reason}")
            print(f"Real Model Calls:      {trace.real_model_calls}")
            print(f"Fallback Calls:        {trace.fallback_calls}")
            if trace.token_usage:
                print(f"Token Usage:           Total: {trace.token_usage.get('total_tokens', 0)} (Prompt: {trace.token_usage.get('prompt_tokens', 0)}, Completion: {trace.token_usage.get('completion_tokens', 0)})")
            print(f"Diff Overlap:          {'YES (citation overlaps diff)' if trace.diff_overlap else 'NO'}")
            print(f"Tools Called:          {trace.tools_called if trace.tools_called else 'None'}")
            print("\nContext Part Breakdown:")
            for part, size in trace.part_sizes.items():
                print(f"  - {part:<20}: {size:>6} chars")
            print("\nCitation Verification:")
            if trace.citation_flags:
                for flag in trace.citation_flags:
                    print(f"  ❌ {flag}")
            else:
                print("  ✓ All cited files and line ranges verified valid in demo-repo.")
            print("=" * 67)
    else:
        print("❌ Failed to receive final diagnosis payload.")

def main():
    parser = argparse.ArgumentParser(description="Run WHACK Brain diagnosis demo.")
    parser.add_argument("--check-key", action="store_true", default=False, help="Verify API key connectivity with minimal call and exit")
    parser.add_argument("--no-fallback", action="store_true", default=False, help="Fail loudly if model call fails instead of falling back")
    parser.add_argument("--repo", default=str(DEFAULT_GENERATED_DEMO_REPO), help="Target demo repo path")
    parser.add_argument("--hint-level", default="hint", choices=["nudge", "hint", "full_explanation", "full"], help="Hint level")
    parser.add_argument("--active-file", default="utils.py", help="Active editor file")
    parser.add_argument("--cursor-line", type=int, default=18, help="Active editor cursor line")

    # Screenshot controls (Requirement 6b)
    parser.add_argument("--screenshot", action="store_true", default=False, help="Take a real screen capture (strictly opt-in)")
    parser.add_argument("--screenshot-file", type=str, default=None, help="Load screenshot from file")
    parser.add_argument("--no-screenshot", action="store_true", default=True, help="Disable screen capture (default)")

    # Component controls (Requirement 6c)
    parser.add_argument("--no-wiki", action="store_true", default=False, help="Disable architecture wiki context")
    parser.add_argument("--no-codemap", action="store_true", default=False, help="Disable AST code map context")

    args = parser.parse_args()
    if args.check_key:
        check_key()
    asyncio.run(run_demo(args))

if __name__ == "__main__":
    main()
