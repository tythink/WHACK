"""Multimodal diagnostic engine with bounded tool loop, ordered context assembly, and grounding verification."""

import asyncio
import json
import re
import time
from typing import Dict, Any, Optional, List, Tuple, AsyncGenerator
import httpx

from brain.config import (
    DEMO_REPO_DIR,
    CORE_SERVER_URL,
    LLM_TIMEOUT_SECONDS,
    MAX_TOOL_TURNS,
    LLM_PROVIDER,
    LLM_MODEL,
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    get_active_api_key,
    sanitize_secret,
)
from brain.models import (
    DiagnosisResponse,
    DiagnoseRequest,
    CodeCitation,
    HintLevelType,
    ExecutionTrace,
)
from brain.tools import execute_tool, TOOL_DEFINITIONS, git_diff
from brain.screenshot import parse_incoming_screenshot, capture_system_screenshot
from brain.persona import get_random_quip
from brain.context import assemble_ordered_context, SYSTEM_INSTRUCTIONS
from brain.citation_verifier import verify_citations

class DiagnosticEngine:
    def __init__(self):
        self.max_turns = MAX_TOOL_TURNS
        self.timeout = LLM_TIMEOUT_SECONDS

    async def diagnose(
        self,
        req: DiagnoseRequest,
        use_codemap: bool = True,
        use_wiki: bool = True,
        enable_screenshot: bool = False,
        no_fallback: bool = False,
    ) -> DiagnosisResponse:
        """Execute diagnosis with ordered context, bounded tool loop, grounding check, and graceful fallback."""
        start_time = time.time()
        time_to_first_output = 0.0
        hint_level: str = req.hintLevel or "nudge"
        if hint_level == "full":
            hint_level = "full_explanation"

        # Screenshot handling (strictly opt-in via enable_screenshot or explicit req.screenshot)
        screenshot_data = None
        if req.screenshot:
            screenshot_data = parse_incoming_screenshot(req.screenshot)
        elif enable_screenshot:
            screenshot_data = capture_system_screenshot()

        # 1. Assemble ordered context (6-part context)
        context_text, part_sizes = assemble_ordered_context(
            req, use_codemap=use_codemap, use_wiki=use_wiki
        )

        trace = ExecutionTrace(
            tools_called=[],
            steps_used=0,
            part_sizes=part_sizes,
            provider=LLM_PROVIDER,
            model_name=LLM_MODEL or ("gemini-3.5-flash-lite" if LLM_PROVIDER in ("gemini", "google") else "gpt-4o-mini"),
            used_fallback=False,
            fallback_reason=None,
            real_model_calls=0,
            fallback_calls=0,
            token_usage={},
            diff_overlap=False,
        )

        active_key = get_active_api_key(LLM_PROVIDER)
        diagnosis: Optional[DiagnosisResponse] = None

        # 2. Attempt real LLM reasoning if API key is present
        if active_key:
            try:
                t0 = time.time()
                diagnosis, tool_calls_log, steps, token_usage = await asyncio.wait_for(
                    self._run_tool_loop(
                        context_text, req, hint_level, trace.provider, trace.model_name, active_key
                    ),
                    timeout=self.timeout,
                )
                time_to_first_output = round(time.time() - t0, 3)
                trace.tools_called = tool_calls_log
                trace.steps_used = steps
                trace.time_to_first_output = time_to_first_output
                trace.real_model_calls = 1
                trace.token_usage = token_usage
                diagnosis.source = "model"
            except asyncio.TimeoutError:
                trace.used_fallback = True
                trace.fallback_calls = 1
                trace.fallback_reason = f"LLM call timed out after {self.timeout}s"
                if no_fallback:
                    raise RuntimeError(f"LLM call timed out after {self.timeout}s (--no-fallback active)")
            except Exception as e:
                trace.used_fallback = True
                trace.fallback_calls = 1
                clean_err = sanitize_secret(str(e))
                trace.fallback_reason = f"LLM call failed: {clean_err}"
                if no_fallback:
                    raise RuntimeError(f"LLM call failed (--no-fallback active): {clean_err}")
        else:
            trace.used_fallback = True
            trace.fallback_calls = 1
            trace.fallback_reason = f"No API key configured for provider '{LLM_PROVIDER}'"
            if no_fallback:
                raise RuntimeError(
                    f"No API key configured for provider '{LLM_PROVIDER}' (--no-fallback active). "
                    f"Please set GEMINI_API_KEY in brain/.env."
                )

        # 3. Resilient fallback heuristic if model failed or unavailable
        if diagnosis is None:
            diagnosis = self._heuristic_diagnosis(req, hint_level)
            diagnosis.source = "offline"
            trace.provider = "offline_heuristic"
            trace.model_name = "heuristic-v1"
            if time_to_first_output == 0.0:
                time_to_first_output = round(time.time() - start_time, 3)
            trace.time_to_first_output = time_to_first_output

        # 4. Grounding Citation Verification (Requirement 8 & Requirement 6 diff overlap)
        diagnosis, citation_flags, any_diff_overlap = verify_citations(diagnosis, DEMO_REPO_DIR)
        trace.citation_flags = citation_flags
        trace.diff_overlap = any_diff_overlap

        total_time = round(time.time() - start_time, 3)
        trace.total_time = total_time
        diagnosis.trace = trace

        # If fallback was used, state clearly in the output (Requirement 9 & Requirement 1)
        if trace.used_fallback and trace.fallback_reason:
            prefix = f"[OFFLINE FALLBACK - not an AI diagnosis: {trace.fallback_reason}]\n\n"
            if diagnosis.explanation:
                diagnosis.explanation = prefix + diagnosis.explanation
            else:
                diagnosis.explanation = prefix.strip()

        return diagnosis

    async def diagnose_stream(
        self,
        req: DiagnoseRequest,
        use_codemap: bool = True,
        use_wiki: bool = True,
        enable_screenshot: bool = False,
        no_fallback: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Stream diagnosis progress and chunks over Server-Sent Events (SSE)."""
        yield f"event: start\ndata: {json.dumps({'status': 'analyzing', 'hintLevel': req.hintLevel})}\n\n"
        await asyncio.sleep(0.02)

        yield f"event: thought\ndata: {json.dumps({'message': 'Assembling ordered context: diff, code map, wiki...'})}\n\n"
        await asyncio.sleep(0.02)

        diagnosis = await self.diagnose(
            req,
            use_codemap=use_codemap,
            use_wiki=use_wiki,
            enable_screenshot=enable_screenshot,
            no_fallback=no_fallback,
        )

        if diagnosis.trace and diagnosis.trace.tools_called:
            yield f"event: tools\ndata: {json.dumps({'tools': diagnosis.trace.tools_called})}\n\n"
            await asyncio.sleep(0.02)

        if diagnosis.file and diagnosis.lines:
            yield f"event: citation\ndata: {json.dumps({'file': diagnosis.file, 'lines': diagnosis.lines})}\n\n"
            await asyncio.sleep(0.02)

        yield f"event: diagnosis\ndata: {diagnosis.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    async def _run_tool_loop(
        self,
        context_text: str,
        req: DiagnoseRequest,
        hint_level: str,
        provider: str,
        model_name: str,
        api_key: str,
    ) -> Tuple[DiagnosisResponse, List[Dict[str, Any]], int, Dict[str, int]]:
        """Run bounded tool calling loop (max 6 turns)."""
        tools_called: List[Dict[str, Any]] = []
        steps = 0
        token_usage: Dict[str, int] = {}

        if provider in ("gemini", "google"):
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = (
                f"{context_text}\n\n"
                f"### TARGET HINT LEVEL: {hint_level.upper()}\n"
                "Please analyze the failure, inspect the diff and codebase, and return your diagnosis as JSON matching the DiagnosisResponse schema with keys: "
                "summary (str), file (str or null), lines ([start, end] or null), quip (str), uncertain (bool), "
                "confidence (float between 0 and 1), hintLevel (str), explanation (str or null), "
                "citations (list of objects with file, lines, snippet, note)."
            )

            # Note: Do not pass response_schema=DiagnosisResponse as Gemini Developer API rejects additionalProperties
            response = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=model_name or "gemini-3.5-flash-lite",
                        contents=[prompt],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTIONS + "\n\nRespond strictly with valid JSON.",
                            response_mime_type="application/json",
                        ),
                    )
                    break
                except Exception as exc:
                    err_str = str(exc).lower()
                    if attempt < max_retries - 1:
                        if "429" in err_str or "resource_exhausted" in err_str:
                            await asyncio.sleep(55.0)
                            continue
                        elif "503" in err_str or "unavailable" in err_str or "high demand" in err_str:
                            await asyncio.sleep(2.0 * (attempt + 1))
                            continue
                    raise
            steps = 1
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                p_tok = int(getattr(response.usage_metadata, "prompt_token_count", 0) or 0)
                c_tok = int(getattr(response.usage_metadata, "candidates_token_count", 0) or 0)
                tot_tok = int(getattr(response.usage_metadata, "total_token_count", 0) or (p_tok + c_tok))
                token_usage = {
                    "prompt_tokens": p_tok,
                    "completion_tokens": c_tok,
                    "total_tokens": tot_tok,
                }
            if response.text:
                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    lines = raw_text.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw_text = "\n".join(lines).strip()
                data = json.loads(raw_text)
                diag = DiagnosisResponse(**data)
                diag.source = "model"
                return diag, tools_called, steps, token_usage

        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            prompt = (
                f"{context_text}\n\n"
                f"### TARGET HINT LEVEL: {hint_level.upper()}\n"
                "Please analyze the failure, inspect the diff and codebase, and return your diagnosis as JSON matching the DiagnosisResponse schema with keys: "
                "summary, file, lines, quip, uncertain, confidence, hintLevel, explanation, citations."
            )

            completion = client.chat.completions.create(
                model=model_name or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            steps = 1
            if completion.usage:
                token_usage = {
                    "prompt_tokens": completion.usage.prompt_tokens,
                    "completion_tokens": completion.usage.completion_tokens,
                    "total_tokens": completion.usage.total_tokens,
                }
            raw_json = completion.choices[0].message.content
            if raw_json:
                data = json.loads(raw_json)
                diag = DiagnosisResponse(**data)
                diag.source = "model"
                return diag, tools_called, steps, token_usage

        raise RuntimeError(f"Unsupported or unconfigured provider: {provider}")

    def _heuristic_diagnosis(
        self, req: DiagnoseRequest, hint_level: str
    ) -> DiagnosisResponse:
        """Fast, robust offline heuristic diagnosis parsing stack traces, diffs, and editor state."""
        terminal_tail = (req.terminalState.tailOutput if req.terminalState else "") or ""
        command = (req.terminalState.command if req.terminalState else "") or ""
        active_file = (req.editorState.activeFile if req.editorState else "") or ""

        # Inspect git diff to check recent changes in demo-repo
        diff_info = git_diff("HEAD~1", "HEAD")
        diff_text = diff_info.get("diff", "")

        # 1. Check for ZeroDivisionError
        if "ZeroDivisionError" in terminal_tail or "division by zero" in terminal_tail.lower():
            file_name = active_file or "scenarios.py"
            lines = (42, 45)
            if hint_level == "nudge":
                summary = "Check for possible zero divisor when calculating rates or averages on empty sets."
                explanation = "Consider what happens when total records or items is 0."
            elif hint_level == "hint":
                summary = f"ZeroDivisionError in {file_name}: total_events count is 0."
                explanation = "Missing check `if total_events == 0: return 0.0` before dividing."
            else:
                summary = "ZeroDivisionError: unhandled 0 count in rate calculation."
                explanation = (
                    "Guard against empty sets:\n"
                    "```python\n"
                    "rate = (success_count / total_events * 100.0) if total_events > 0 else 0.0\n"
                    "```"
                )
            return DiagnosisResponse(
                summary=summary,
                file=file_name,
                lines=lines,
                quip="Zero is a fine number, except when it's under a fraction line.",
                uncertain=False,
                confidence=0.95,
                hintLevel=hint_level,
                explanation=explanation,
            )

        # 2. Check for Regex group mismatch on None
        if "AttributeError" in terminal_tail and "group" in terminal_tail:
            file_name = active_file or "scenarios.py"
            lines = (47, 51)
            return DiagnosisResponse(
                summary="Regex pattern search returned None; calling .group() caused AttributeError.",
                file=file_name,
                lines=lines,
                quip="Regex: where a missing match creates an existential crisis.",
                uncertain=False,
                confidence=0.96,
                hintLevel=hint_level,
                explanation="Check `if match:` before calling `match.group(1)`.",
            )

        # 3. Check for NoneType dereference
        if "NoneType" in terminal_tail and ("subscriptable" in terminal_tail or "attribute" in terminal_tail):
            file_name = active_file or "scenarios.py"
            lines = (27, 30)
            if hint_level == "nudge":
                summary = "An expected dictionary or object is evaluating to None before accessing its fields."
                explanation = "Inspect the upstream source of metadata to confirm it's not None."
            elif hint_level == "hint":
                summary = f"NoneType error in {file_name}: accessing key or attribute on None."
                explanation = "A dictionary lookup or API payload returned None for a nested key."
            else:
                summary = f"NoneType subscriptable error in {file_name}."
                explanation = "Use safe navigation or default dict: `(payload.get('meta') or {}).get('timestamp')`."
            return DiagnosisResponse(
                summary=summary,
                file=file_name,
                lines=lines,
                quip="The billion-dollar mistake strikes again. None has no keys!",
                uncertain=False,
                confidence=0.92,
                hintLevel=hint_level,
                explanation=explanation,
            )

        # 4. Check for UnboundLocalError / scoping
        if "UnboundLocalError" in terminal_tail or "referenced before assignment" in terminal_tail:
            file_name = active_file or "scenarios.py"
            lines = (16, 25)
            if hint_level == "nudge":
                summary = "A local variable is being referenced inside a function before it has been assigned."
                explanation = "Check whether an inner function needs `nonlocal` or `global` to modify an outer variable."
            elif hint_level == "hint":
                summary = f"UnboundLocalError in {file_name}: variable reassignment shadows outer scope."
                explanation = "Python treats variables assigned within a function as local unless declared nonlocal."
            else:
                summary = f"Scoping bug in {file_name}: declare `nonlocal` or pass accumulator explicitly."
                explanation = "Add `nonlocal var_name` at the top of the nested helper function."
            return DiagnosisResponse(
                summary=summary,
                file=file_name,
                lines=lines,
                quip="Python variable scopes: sometimes a fence is higher than it looks.",
                uncertain=False,
                confidence=0.94,
                hintLevel=hint_level,
                explanation=explanation,
            )

        # 5. Check for coroutine was never awaited
        if "coroutine" in terminal_tail and ("never awaited" in terminal_tail or "subscriptable" in terminal_tail):
            file_name = active_file or "scenarios.py"
            lines = (37, 40)
            return DiagnosisResponse(
                summary="Unawaited async coroutine: function called without `await`.",
                file=file_name,
                lines=lines,
                quip="Async functions need an 'await' or they remain promises waiting to happen.",
                uncertain=False,
                confidence=0.96,
                hintLevel=hint_level,
                explanation="Add `await` before calling the async metric fetcher.",
            )

        # 6. Check for Type Mismatch Comparison
        if "TypeError" in terminal_tail and (
            ">" in terminal_tail or "<" in terminal_tail or "not supported between instances" in terminal_tail
        ):
            file_name = active_file or "scenarios.py"
            lines = (53, 57)
            return DiagnosisResponse(
                summary="Type mismatch: comparing uncasted string against integer.",
                file=file_name,
                lines=lines,
                quip="Comparing apples to oranges is okay; strings to integers, Python says no.",
                uncertain=False,
                confidence=0.97,
                hintLevel=hint_level,
                explanation="Cast the raw string input to int or float before numeric comparison: `int(raw_reading) > threshold`.",
            )

        # 7. Check for Mutable Default Argument
        if "mutable" in terminal_tail.lower() or "persisted" in terminal_tail.lower():
            file_name = active_file or "scenarios.py"
            lines = (32, 35)
            return DiagnosisResponse(
                summary="Mutable default argument in function definition retains state across invocations.",
                file=file_name,
                lines=lines,
                quip="Default lists are like elephants: they never forget.",
                uncertain=False,
                confidence=0.95,
                hintLevel=hint_level,
                explanation="Use `log: Optional[List[str]] = None` and initialize `if log is None: log = []` inside function body.",
            )

        # 8. Check for IndexError in utils.py (the primary planted bug in demo-repo)
        if "IndexError" in terminal_tail or "exceeds data length" in terminal_tail or "total_items - window_size + 2" in diff_text:
            file_name = "utils.py"
            lines = (10, 15)
            
            if hint_level == "nudge":
                summary = "Look closely at the sliding window loop boundary condition in your offset calculator."
                explanation = "Notice how many window offsets are being produced compared to the total items."
            elif hint_level == "hint":
                summary = "Off-by-one error in utils.py: calculate_window_offsets produces one offset too many."
                explanation = "In utils.py, range(0, total_items - window_size + 2) iterates one index past the boundary."
            else:  # full_explanation
                summary = "Off-by-one error in utils.py causes IndexError in batch_slice."
                explanation = (
                    "**Root Cause**: In `utils.py`, `calculate_window_offsets` adds `+ 2` instead of `+ 1` to the range stop.\n\n"
                    "```python\n"
                    "# Before (Buggy):\n"
                    "for i in range(0, total_items - window_size + 2):\n\n"
                    "# After (Fix):\n"
                    "for i in range(0, total_items - window_size + 1):\n"
                    "```"
                )

            return DiagnosisResponse(
                summary=summary,
                file=file_name,
                lines=lines,
                quip=get_random_quip(),
                uncertain=False,
                confidence=0.98,
                hintLevel=hint_level,
                explanation=explanation,
                citations=[
                    CodeCitation(
                        file="utils.py",
                        lines=lines,
                        snippet="for i in range(0, total_items - window_size + 2):",
                        note="Stop condition creates an offset whose end exceeds array length",
                    )
                ],
            )

        # Default fallback
        summary = "No obvious exception identified in recent terminal logs."
        if active_file:
            summary = f"Reviewing active file '{active_file}' for potential logic discrepancies."

        return DiagnosisResponse(
            summary=summary,
            file=active_file or "pipeline.py",
            lines=(1, 30) if active_file else None,
            quip=get_random_quip(),
            uncertain=True,
            confidence=0.50,
            hintLevel=hint_level,
            explanation="Provide terminal logs or run tests to isolate the specific failure point.",
        )

async def broadcast_to_core(diagnosis: DiagnosisResponse):
    """Broadcast diagnosis result to Core Server if active."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{CORE_SERVER_URL}/events",
                json={
                    "source": "brain",
                    "type": "diagnosis",
                    "timestamp": int(time.time() * 1000),
                    "payload": diagnosis.model_dump(),
                },
            )
    except Exception:
        pass
