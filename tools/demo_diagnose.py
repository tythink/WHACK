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
from brain.screenshot import (
    capture_system_screenshot,
    process_image_file,
    draw_spatial_markers_on_screenshot,
)
from brain.tools import apply_fix_to_repo, rollback_repo_checkpoint
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
    import base64
    target_repo = Path(args.repo or DEFAULT_GENERATED_DEMO_REPO).expanduser().resolve()
    ensure_demo_repo(target_repo)

    # 1. Parse Dummy Gaze if provided
    gaze_coords = None
    if args.dummy_gaze:
        try:
            parts = [float(c.strip()) for c in args.dummy_gaze.split(",")]
            if len(parts) >= 2:
                gaze_coords = (int(round(parts[0])), int(round(parts[1])))
                print(f"👁️ [Dummy Gaze] Injected gaze coordinates: X={gaze_coords[0]}, Y={gaze_coords[1]}")
        except Exception as e:
            print(f"⚠️ Failed to parse --dummy-gaze '{args.dummy_gaze}': {e}")

    # 2. Screenshot handling
    screenshot_b64 = None
    raw_shot_bytes = None
    shot = None
    if args.screenshot:
        print("📸 Capturing screen (--screenshot flag passed)...")
        shot = capture_system_screenshot()
        if shot:
            screenshot_b64 = shot["base64"]
            raw_shot_bytes = base64.b64decode(shot["base64"])
            print(f"   Captured screenshot (raw: {shot.get('raw_width', shot['width'])}x{shot.get('raw_height', shot['height'])} -> resized: {shot['width']}x{shot['height']}, {shot['size_bytes']:,} bytes)")
    elif args.screenshot_file:
        file_path = Path(args.screenshot_file).expanduser().resolve()
        if file_path.exists():
            print(f"📸 Loading screenshot file from: {file_path}")
            shot = process_image_file(str(file_path))
            screenshot_b64 = shot["base64"]
            raw_shot_bytes = base64.b64decode(shot["base64"])
        else:
            print(f"⚠️ Screenshot file not found: {file_path}")
    else:
        # Default: NEVER capture screen
        screenshot_b64 = None

    # If dummy gaze and screenshot are both present, draw visual gaze marker on image
    annotated_image_path = None
    if raw_shot_bytes and gaze_coords:
        markers = {"gaze": gaze_coords}
        annotated_bytes, out_path = draw_spatial_markers_on_screenshot(
            raw_shot_bytes,
            markers=markers,
        )
        annotated_image_path = out_path
        print(f"   🖼️ [Annotated Screenshot] Saved with distinct gaze marker to: {out_path}")

    # 3. Capture terminal failure
    print("⚡ Running test in demo repository to capture terminal state...")
    test_output = get_demo_failure(target_repo)
    print(f"   Captured {len(test_output.splitlines())} lines of test output.\n")

    req = DiagnoseRequest(
        hintLevel=args.hint_level,
        screenshot=screenshot_b64,
        gazePoint=list(gaze_coords) if gaze_coords else None,
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
            print(f"  • Image Prep/Encode: {trace.time_image:.3f}s")
            print(f"  • Context Assembly:  {trace.time_context:.3f}s")
            print(f"  • Network Request:   {trace.time_network:.3f}s")
            print(f"  • Response Parsing:  {trace.time_parsing:.3f}s")
            print(f"Used Fallback:         {trace.used_fallback}")
            if trace.fallback_reason:
                print(f"Fallback Reason:       {trace.fallback_reason}")
            print(f"Real Model Calls:      {trace.real_model_calls}")
            print(f"Fallback Calls:        {trace.fallback_calls}")
            if trace.token_usage:
                print(f"Token Usage:           Total: {trace.token_usage.get('total_tokens', 0)} (Prompt: {trace.token_usage.get('prompt_tokens', 0)}, Completion: {trace.token_usage.get('completion_tokens', 0)})")
            if trace.screenshot_bytes:
                dims = trace.screenshot_dimensions or ('?', '?')
                print(f"Screenshot Payload:    {trace.screenshot_bytes:,} bytes (MIME: {trace.screenshot_mime_type}, {dims[0]}x{dims[1]} px)")
            if trace.spatial_points:
                print(f"Spatial Attention:     {trace.spatial_points}")
            if trace.gaze_coherence_note:
                print(f"Gaze Coherence:        {trace.gaze_coherence_note}")
            if trace.structured_edit_validation:
                print(f"Structured Fix Status: {trace.structured_edit_validation}")
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

        # Requirement 4 & 6: Structured Edit & Terminal Apply/Rollback Click Simulation
        if final_diagnosis.suggestedFix:
            fix = final_diagnosis.suggestedFix
            print("\n" + "=" * 25 + " 🔧 STRUCTURED FIX " + "=" * 25)
            print(f"Target File: {fix.file} (lines {fix.startLine}-{fix.endLine})")
            print("Original Code:")
            print(f"  {fix.oldText}")
            print("Suggested Replacement:")
            print(f"  {fix.newText}")
            print("=" * 69)

            auto_apply = getattr(args, "auto_apply", False)
            if not auto_apply:
                apply_ans = input("\n👉 Apply this fix? [y/n]: ").strip().lower()
            else:
                print("\n👉 Apply this fix? [y/n]: y (auto-apply)")
                apply_ans = "y"

            if apply_ans in ("y", "yes"):
                print(f"\n🔨 Applying fix to '{fix.file}' in target repo ({target_repo})...")
                apply_res = apply_fix_to_repo(fix, repo_dir=target_repo)
                if apply_res.get("success"):
                    print("✅ Fix applied successfully!")
                    print(f"   Checkpoint Commit Hash: {apply_res['checkpoint_hash']}")
                    print(f"   Tests Before Apply:     {'PASSED' if apply_res['before_passed'] else 'FAILED'}")
                    print(f"   Tests After Apply:      {'PASSED' if apply_res['after_passed'] else 'FAILED'}")
                    if apply_res.get("after_output"):
                        summary_line = apply_res["after_output"].splitlines()[-1] if apply_res["after_output"].splitlines() else ""
                        print(f"   Test Summary:           {summary_line}")

                    auto_rb = getattr(args, "auto_rollback", False)
                    if not auto_rb:
                        rb_ans = input("\n👉 Rollback this fix to restore original broken state? [y/n]: ").strip().lower()
                    else:
                        print("\n👉 Rollback this fix to restore original broken state? [y/n]: y (auto-rollback)")
                        rb_ans = "y"

                    if rb_ans in ("y", "yes"):
                        print(f"\n⏪ Rolling back target repo to checkpoint {apply_res['checkpoint_hash']}...")
                        rb_res = rollback_repo_checkpoint(apply_res["checkpoint_hash"], repo_dir=target_repo)
                        if rb_res.get("success"):
                            print(f"✅ Rollback complete to {rb_res['rolled_back_to']}!")
                            print(f"   Tests After Rollback: {'PASSED' if rb_res['tests_passed'] else 'FAILED (verified original failure restored)'}")
                        else:
                            print(f"❌ Rollback failed: {rb_res.get('error')}")
                else:
                    print(f"❌ Failed to apply fix: {apply_res.get('error')}")
        else:
            print("\nℹ️ No validated structured fix available for automatic apply.")
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

    # Spatial controls (Requirement 2: Dummy Gaze)
    parser.add_argument("--dummy-gaze", type=str, default=None, help="Inject fixed dummy gaze coordinates 'X,Y' (e.g. '1420,850' or '500,600')")

    # Apply / Rollback click simulation controls (Requirement 6)
    parser.add_argument("--auto-apply", action="store_true", default=False, help="Automatically answer 'y' to apply prompt")
    parser.add_argument("--auto-rollback", action="store_true", default=False, help="Automatically answer 'y' to rollback prompt")

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
