#!/usr/bin/env python3
"""Ablation Study evaluation for WHACK Brain diagnosis.

Evaluates the 8 developer scenarios across 3 context modes:
- Mode A: Diff and Error Only (no codemap, no wiki)
- Mode B: Plus Code Map (with codemap, no wiki)
- Mode C: Plus Code Map and Wiki (full context)

Ground truth lives exclusively here, never in the target demo repo.
"""

import argparse
import asyncio
import importlib
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, Any, List

# Add workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from brain.config import (
    DEMO_REPO_DIR,
    LLM_PROVIDER,
    LLM_MODEL,
    get_active_api_key,
    sanitize_secret,
)
# Add demo repo to sys.path to run realistic functions
if str(DEMO_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_REPO_DIR))

from brain.models import DiagnoseRequest, EditorState, TerminalState
from brain.agent import DiagnosticEngine
from brain.tools import git_diff
import scenarios

def switch_branch(branch: str):
    res = subprocess.run(
        ["git", "checkout", branch],
        cwd=str(DEMO_REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to checkout {branch} in {DEMO_REPO_DIR}: {res.stderr}")
    importlib.reload(scenarios)

def run_mutable_default_trigger():
    scenarios.append_audit_entry("entry_1")
    res = scenarios.append_audit_entry("entry_2")
    if len(res) > 1:
        raise AssertionError(f"mutable default argument persisted state across invocations (len={len(res)})")

# Ground truth definitions (maintained strictly in test harness)
GROUND_TRUTH = [
    {
        "id": "scenario_1",
        "name": "Off-by-One Slices",
        "branch": "scenario-1",
        "file": "utils.py",
        "lines": (8, 15),
        "trigger": lambda: scenarios.calculate_window_slices(),
        "expected_exc": IndexError,
    },
    {
        "id": "scenario_2",
        "name": "Scoping / Shadowing",
        "branch": "scenario-2",
        "file": "scenarios.py",
        "lines": (15, 25),
        "trigger": lambda: scenarios.accumulate_metrics(),
        "expected_exc": UnboundLocalError,
    },
    {
        "id": "scenario_3",
        "name": "NoneType Dereference",
        "branch": "scenario-3",
        "file": "scenarios.py",
        "lines": (26, 32),
        "trigger": lambda: scenarios.extract_event_timestamp(),
        "expected_exc": TypeError,
    },
    {
        "id": "scenario_4",
        "name": "Mutable Default Arg",
        "branch": "scenario-4",
        "file": "scenarios.py",
        "lines": (32, 38),
        "trigger": run_mutable_default_trigger,
        "expected_exc": AssertionError,
    },
    {
        "id": "scenario_5",
        "name": "Unawaited Coroutine",
        "branch": "scenario-5",
        "file": "scenarios.py",
        "lines": (41, 46),
        "trigger": lambda: scenarios.poll_remote_metrics(),
        "expected_exc": TypeError,
    },
    {
        "id": "scenario_6",
        "name": "Zero Division",
        "branch": "scenario-6",
        "file": "scenarios.py",
        "lines": (47, 51),
        "trigger": lambda: scenarios.compute_throughput_rate(0, 0),
        "expected_exc": ZeroDivisionError,
    },
    {
        "id": "scenario_7",
        "name": "Regex None Match",
        "branch": "scenario-7",
        "file": "scenarios.py",
        "lines": (52, 58),
        "trigger": lambda: scenarios.parse_status_code(),
        "expected_exc": AttributeError,
    },
    {
        "id": "scenario_8",
        "name": "Type Mismatch",
        "branch": "scenario-8",
        "file": "scenarios.py",
        "lines": (59, 65),
        "trigger": lambda: scenarios.check_priority_threshold(),
        "expected_exc": TypeError,
    },
]

MODES = [
    {"name": "Mode A (Diff+Error)", "codemap": False, "wiki": False},
    {"name": "Mode B (+CodeMap)",   "codemap": True,  "wiki": False},
    {"name": "Mode C (+CodeMap+Wiki)", "codemap": True,  "wiki": True},
]

MAX_CALL_CAP = 30

async def run_ablation(no_fallback: bool = True):
    total_planned_calls = len(GROUND_TRUTH) * len(MODES)
    print("=" * 75)
    print(f"📊 WHACK Brain Ablation Evaluation")
    print(f"Configured Provider:   {LLM_PROVIDER}")
    print(f"Configured Model:      {LLM_MODEL}")
    print(f"No Fallback Enforced:  {no_fallback}")
    print(f"Planned API Calls:     {total_planned_calls} ({len(GROUND_TRUTH)} scenarios x {len(MODES)} modes)")
    print(f"Maximum Allowed Cap:   {MAX_CALL_CAP} calls")
    print("=" * 75 + "\n")

    # Honest Check: Abort if provider resolves to offline or missing key
    if LLM_PROVIDER in ("offline", "offline_heuristic", "none"):
        print(f"❌ Ablation aborted: LLM provider is set to '{LLM_PROVIDER}'. Ablation requires an active real model.")
        sys.exit(1)

    active_key = get_active_api_key(LLM_PROVIDER)
    if not active_key or not active_key.strip():
        print(f"❌ Ablation aborted: No API key found for provider '{LLM_PROVIDER}'. Ablation requires a valid API key.")
        sys.exit(1)

    if total_planned_calls > MAX_CALL_CAP:
        print(f"❌ Error: Planned calls ({total_planned_calls}) exceeds cap of {MAX_CALL_CAP}!")
        sys.exit(1)

    engine = DiagnosticEngine()
    results = []
    call_count = 0
    total_real_model_calls = 0
    total_fallback_calls = 0
    total_tokens_consumed = 0

    try:
        for sc in GROUND_TRUTH:
            # Checkout scenario branch to align git diff and repository state with this bug
            switch_branch(sc["branch"])

            # Trigger failure on disk to capture real traceback
            tail_output = ""
            try:
                sc["trigger"]()
            except Exception as e:
                tail_output = "".join(traceback.format_exception(type(e), e, e.__traceback__))

            req = DiagnoseRequest(
                hintLevel="hint",
                editorState=EditorState(activeFile=sc["file"], cursorLine=sc["lines"][0]),
                terminalState=TerminalState(
                    command=f"python -m scenarios",
                    exitCode=1,
                    tailOutput=tail_output,
                ),
            )

            for mode in MODES:
                call_count += 1
                if call_count > MAX_CALL_CAP:
                    print(f"⚠️ Reached call cap of {MAX_CALL_CAP}. Halting further evaluations.")
                    break

                print(f"[{call_count}/{total_planned_calls}] Evaluating {sc['name']} in {mode['name']}...", end=" ", flush=True)

                try:
                    diag = await engine.diagnose(
                        req,
                        use_codemap=mode["codemap"],
                        use_wiki=mode["wiki"],
                        enable_screenshot=False,
                        no_fallback=no_fallback,
                    )
                except Exception as e:
                    clean_err = sanitize_secret(str(e))
                    print(f"FAILED!\n❌ Fatal model failure with --no-fallback: {clean_err}")
                    sys.exit(1)

                trace = diag.trace
                real_call = (trace.real_model_calls > 0) if trace else False
                fell_back = (diag.source == "offline" or (trace and trace.used_fallback))

                if real_call:
                    total_real_model_calls += 1
                if fell_back:
                    total_fallback_calls += 1

                # Real token usage from API response usage_metadata
                tokens_used = 0
                if trace and trace.token_usage:
                    tokens_used = trace.token_usage.get("total_tokens", 0)
                total_tokens_consumed += tokens_used

                # Evaluate accuracy against ground truth
                correct_file = bool(diag.file and Path(diag.file).name == Path(sc["file"]).name)
                
                # Check line range overlap or close proximity (within +/- 3 lines)
                correct_lines = False
                if diag.lines and sc["lines"]:
                    d_start, d_end = diag.lines
                    gt_start, gt_end = sc["lines"]
                    # Overlap check
                    if max(d_start, gt_start) <= min(d_end, gt_end) or abs(d_start - gt_start) <= 3:
                        correct_lines = True

                valid_citations = bool(trace and len(trace.citation_flags) == 0)
                steps = trace.steps_used if trace else 1
                total_time = trace.total_time if trace else 0.05

                mode_name = mode["name"]
                if fell_back:
                    mode_name = f"{mode['name']} [INVALID]"

                print(f"done ({total_time:.2f}s, tokens: {tokens_used})", flush=True)
                await asyncio.sleep(4.1)  # Respect free tier rate limit: 15 requests per minute (<1 req/4s)

                results.append({
                    "scenario": sc["name"],
                    "mode": mode_name,
                    "file_correct": correct_file,
                    "lines_correct": correct_lines,
                    "valid_cit": valid_citations,
                    "steps": steps,
                    "time": total_time,
                    "tokens": tokens_used,
                    "source": diag.source,
                    "uncertain": diag.uncertain,
                    "invalid": fell_back,
                })
    finally:
        switch_branch("main")

    # Print summary table
    print("\n" + "=" * 98)
    print(f"{'Scenario':<22} | {'Mode':<26} | {'File':<5} | {'Lines':<5} | {'Citations':<9} | {'Steps':<5} | {'Time':<6} | {'Tokens':<6}")
    print("-" * 98)
    for r in results:
        f_str = "✓" if r["file_correct"] else "✗"
        l_str = "✓" if r["lines_correct"] else "✗"
        c_str = "Valid" if r["valid_cit"] else "Flagged"
        print(
            f"{r['scenario']:<22} | {r['mode']:<26} | {f_str:<5} | {l_str:<5} | {c_str:<9} | {r['steps']:<5} | {r['time']:<5.2f}s | {r['tokens']:<6}"
        )
    print("=" * 98)
    print(f"\n📊 Ablation Summary:")
    print(f"  • Total evaluations:   {call_count}/{MAX_CALL_CAP}")
    print(f"  • Real model calls:    {total_real_model_calls}")
    print(f"  • Fallback calls:      {total_fallback_calls} {'(marked INVALID)' if total_fallback_calls > 0 else ''}")
    print(f"  • Total tokens used:   {total_tokens_consumed}\n")

def main():
    parser = argparse.ArgumentParser(description="Run WHACK Brain ablation study.")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        default=True,
        help="Abort if model call fails (default: True)",
    )
    parser.add_argument(
        "--allow-fallback",
        dest="no_fallback",
        action="store_false",
        help="Allow offline fallback if model fails (marks run as INVALID)",
    )
    args = parser.parse_args()
    asyncio.run(run_ablation(no_fallback=args.no_fallback))

if __name__ == "__main__":
    main()
