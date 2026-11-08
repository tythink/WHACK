"""Multimodal diagnostic and interactive chat engine with bounded tool loop and workspace sandboxing."""

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, AsyncGenerator, Union
import httpx

from brain.config import (
    CORE_SERVER_URL,
    LLM_TIMEOUT_SECONDS,
    MAX_TOOL_TURNS,
    expected_api_key_var,
    get_active_api_key,
    get_active_model,
    get_active_provider,
    resolve_repo_dir,
    sanitize_secret,
)
from brain.models import (
    ChatRequest,
    ChatResponse,
    DiagnoseRequest,
    DiagnosisResponse,
    ExecutionTrace,
    StructuredEdit,
    ToolCallRecord,
)
from brain.tools import execute_tool, read_file_range, TOOL_DEFINITIONS
from brain.screenshot import (
    capture_system_screenshot,
    evaluate_spatial_coherence,
    normalize_request_spatial_points,
    parse_incoming_screenshot,
)
from brain.persona import build_chat_system_prompt, build_diagnose_system_prompt
from brain.context import assemble_ordered_context, is_code_comprehension_request
from brain.citation_verifier import verify_citations
from brain.extractor import (
    extract_chat_reply_and_edit,
    extract_response_text,
    normalize_hint_level,
    normalize_lines,
    normalize_repo_file_path,
    parse_diagnosis_response,
    validate_or_extract_structured_edit_with_note,
)


def validate_structured_edit(
    fix_data: Any,
    repo_dir: Path,
    explanation: Optional[str] = None,
    file_hint: Optional[str] = None,
    lines_hint: Optional[Tuple[int, int]] = None,
) -> Tuple[Optional[StructuredEdit], str]:
    """Validate or align a StructuredEdit against live files inside `repo_dir`."""
    return validate_or_extract_structured_edit_with_note(
        fix_data=fix_data,
        explanation=explanation,
        file_hint=file_hint,
        lines_hint=lines_hint,
        repo_dir=repo_dir,
    )


def _extract_gemini_function_calls(response: Any) -> List[Any]:
    """Extract function calls from a google-genai response (real SDK or lightweight test mock)."""
    direct_fcs = getattr(response, "function_calls", None)
    if direct_fcs:
        return list(direct_fcs)

    extracted: List[Any] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                extracted.append(fc)
    return extracted


def _accumulate_gemini_usage(response: Any, totals: Dict[str, int]) -> None:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return
    p_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
    c_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
    tot_tok = int(getattr(usage, "total_token_count", 0) or (p_tok + c_tok))
    totals["prompt_tokens"] = totals.get("prompt_tokens", 0) + p_tok
    totals["completion_tokens"] = totals.get("completion_tokens", 0) + c_tok
    totals["total_tokens"] = totals.get("total_tokens", 0) + tot_tok


class DiagnosticEngine:
    def __init__(self):
        self.max_turns = MAX_TOOL_TURNS
        self.timeout = LLM_TIMEOUT_SECONDS

    def _build_offline_diagnosis(
        self,
        hint_level: str,
        error_reason: str,
        target_repo: Path,
        reasons: List[str],
        missing_key: bool,
        trace: ExecutionTrace,
    ) -> DiagnosisResponse:
        if missing_key:
            summary = (
                "Brain LLM is offline — configure GEMINI_API_KEY "
                "(or OPENAI_API_KEY / ANTHROPIC_API_KEY) in brain/.env."
            )
            quip = "The stove isn't even lit! Give me an API key in brain/.env before asking me to cook."
        else:
            summary = f"Brain LLM is offline — {error_reason}"
            quip = "The stove lost flame mid-service! Check your API key, quota, or network connection."

        return DiagnosisResponse(
            summary=summary,
            file=None,
            lines=None,
            quip=quip,
            uncertain=True,
            confidence=0.0,
            hintLevel=hint_level,
            source="offline",
            error=error_reason,
            repoPath=str(target_repo),
            reasons=list(reasons),
            explanation=error_reason,
            citations=[],
            suggestedFix=None,
            trace=trace,
        )

    async def diagnose(
        self,
        req: DiagnoseRequest,
        use_codemap: bool = True,
        use_wiki: bool = True,
        enable_screenshot: bool = False,
        no_fallback: bool = False,
    ) -> DiagnosisResponse:
        """Execute multimodal diagnosis with bounded tool loop, extraction, and grounding verification."""
        start_time = time.time()
        hint_level = normalize_hint_level(req.hintLevel, default="hint")
        target_repo = resolve_repo_dir(req.repoPath)

        provider = get_active_provider()
        model_name = get_active_model(provider)
        active_key = get_active_api_key(provider)

        # 1. Screenshot capture / parsing
        # Auto-enable live screenshot when req.screenshot is empty and captureScreenshot is True
        # or (captureScreenshot is None and (enable_screenshot or spatial coords provided))
        t_img_0 = time.time()
        screenshot_data: Optional[Dict[str, Any]] = None
        has_any_spatial = any(
            pt is not None for pt in (req.gazePoint, req.cursorScreenPos, req.caretScreenPos)
        )
        should_capture = (
            req.captureScreenshot is True
            or (req.captureScreenshot is None and (enable_screenshot or has_any_spatial))
        )

        if req.screenshot and req.screenshot.strip():
            screenshot_data = await asyncio.to_thread(parse_incoming_screenshot, req.screenshot)
        elif should_capture:
            try:
                screenshot_data = await asyncio.to_thread(capture_system_screenshot)
            except Exception:
                screenshot_data = None
        time_image = round(time.time() - t_img_0, 3)

        # 2. Spatial normalization against RAW display/screenshot dimensions (NOT resized 1280x720!)
        t_ctx_0 = time.time()
        raw_w = (screenshot_data.get("raw_width") if screenshot_data else None) or req.screenWidth or 1920
        raw_h = (screenshot_data.get("raw_height") if screenshot_data else None) or req.screenHeight or 1080
        normalized_points = normalize_request_spatial_points(
            req, actual_width=int(raw_w), actual_height=int(raw_h)
        )

        # 3. Assemble 6-part context scoped to target_repo
        shot_size_bytes: Optional[int] = None
        if screenshot_data and screenshot_data.get("base64"):
            shot_size_bytes = screenshot_data.get("size_bytes") or len(
                base64.b64decode(screenshot_data["base64"])
            )

        context_text, part_sizes = await asyncio.to_thread(
            assemble_ordered_context,
            req,
            target_repo,
            use_codemap,
            use_wiki,
            normalized_points,
            bool(screenshot_data and screenshot_data.get("base64")),
            shot_size_bytes,
        )
        time_context = round(time.time() - t_ctx_0, 3)

        trace = ExecutionTrace(
            tools_called=[],
            steps_used=0,
            part_sizes=part_sizes,
            time_image=time_image,
            time_context=time_context,
            provider=provider,
            model_name=model_name,
            used_fallback=False,
            fallback_reason=None,
            real_model_calls=0,
            fallback_calls=0,
            token_usage={},
            diff_overlap=False,
            spatial_points=normalized_points if normalized_points else None,
            gaze_coherence_note=None,
        )

        if screenshot_data and screenshot_data.get("base64") and shot_size_bytes is not None:
            trace.screenshot_bytes = shot_size_bytes
            trace.screenshot_mime_type = screenshot_data.get("mime_type", "image/jpeg")
            trace.screenshot_dimensions = (
                screenshot_data.get("width") or int(raw_w),
                screenshot_data.get("height") or int(raw_h),
            )

        # 4. If no API key is configured, return honest offline response immediately
        if not active_key:
            err_msg = f"Missing {expected_api_key_var(provider)} in brain/.env or environment"
            if no_fallback:
                raise RuntimeError(f"{err_msg} (--no-fallback active)")
            trace.used_fallback = True
            trace.fallback_calls = 1
            trace.fallback_reason = err_msg
            trace.time_to_first_output = round(time.time() - start_time, 3)
            trace.total_time = trace.time_to_first_output
            if normalized_points:
                trace.gaze_coherence_note = evaluate_spatial_coherence(normalized_points)
            return self._build_offline_diagnosis(
                hint_level=hint_level,
                error_reason=err_msg,
                target_repo=target_repo,
                reasons=list(req.reasons or []),
                missing_key=True,
                trace=trace,
            )

        # 5. Execute bounded multi-turn tool loop against active LLM provider
        timeout_budget = float(self.timeout or LLM_TIMEOUT_SECONDS or 22.0)
        try:
            t0 = time.time()
            (
                diagnosis,
                tool_calls_log,
                steps,
                token_usage,
                timing_breakdown,
                edit_note,
            ) = await asyncio.wait_for(
                self._run_tool_loop(
                    context_text=context_text,
                    req=req,
                    hint_level=hint_level,
                    provider=provider,
                    model_name=model_name,
                    api_key=active_key,
                    target_repo=target_repo,
                    screenshot_data=screenshot_data,
                    normalized_points=normalized_points,
                ),
                timeout=timeout_budget,
            )
            trace.tools_called = tool_calls_log
            trace.steps_used = steps
            trace.time_to_first_output = round(time.time() - t0, 3)
            trace.time_network = timing_breakdown.get("time_network", 0.0)
            trace.time_parsing = timing_breakdown.get("time_parsing", 0.0)
            trace.real_model_calls = max(1, steps)
            trace.token_usage = token_usage
            trace.structured_edit_validation = edit_note
        except asyncio.TimeoutError:
            err_msg = f"LLM call timed out after {timeout_budget:.1f}s ({provider}/{model_name})"
            if no_fallback:
                raise RuntimeError(f"{err_msg} (--no-fallback active)")
            trace.used_fallback = True
            trace.fallback_calls = 1
            trace.fallback_reason = err_msg
            trace.time_to_first_output = round(time.time() - start_time, 3)
            trace.total_time = trace.time_to_first_output
            if normalized_points:
                trace.gaze_coherence_note = evaluate_spatial_coherence(normalized_points)
            return self._build_offline_diagnosis(
                hint_level=hint_level,
                error_reason=err_msg,
                target_repo=target_repo,
                reasons=list(req.reasons or []),
                missing_key=False,
                trace=trace,
            )
        except Exception as exc:
            clean_err = sanitize_secret(str(exc))
            err_msg = f"LLM call failed ({provider}/{model_name}): {clean_err}"
            if no_fallback:
                raise RuntimeError(f"{err_msg} (--no-fallback active)")
            trace.used_fallback = True
            trace.fallback_calls = 1
            trace.fallback_reason = err_msg
            trace.time_to_first_output = round(time.time() - start_time, 3)
            trace.total_time = trace.time_to_first_output
            if normalized_points:
                trace.gaze_coherence_note = evaluate_spatial_coherence(normalized_points)
            return self._build_offline_diagnosis(
                hint_level=hint_level,
                error_reason=err_msg,
                target_repo=target_repo,
                reasons=list(req.reasons or []),
                missing_key=False,
                trace=trace,
            )

        # 6. Grounding Citation Verification against target_repo
        diagnosis, citation_flags, any_diff_overlap = await asyncio.to_thread(
            verify_citations, diagnosis, target_repo
        )
        trace.citation_flags = citation_flags
        trace.diff_overlap = any_diff_overlap

        # 7. Spatial coherence evaluation
        if normalized_points:
            trace.gaze_coherence_note = evaluate_spatial_coherence(
                normalized_points, model_explanation=diagnosis.explanation
            )

        diagnosis.source = "model"
        diagnosis.error = None
        if is_code_comprehension_request(req):
            diagnosis.suggestedFix = None
        diagnosis.repoPath = str(target_repo)
        diagnosis.reasons = list(req.reasons or [])
        trace.total_time = round(time.time() - start_time, 3)
        diagnosis.trace = trace
        return diagnosis

    async def diagnose_stream(
        self,
        req: DiagnoseRequest,
        use_codemap: bool = True,
        use_wiki: bool = True,
        enable_screenshot: bool = False,
        no_fallback: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Stream diagnosis progress and result over Server-Sent Events (SSE)."""
        yield f"event: start\ndata: {json.dumps({'status': 'analyzing', 'hintLevel': req.hintLevel})}\n\n"
        await asyncio.sleep(0.01)

        yield f"event: thought\ndata: {json.dumps({'message': 'Assembling workspace context: diff, code map, wiki...'})}\n\n"
        await asyncio.sleep(0.01)

        diagnosis = await self.diagnose(
            req,
            use_codemap=use_codemap,
            use_wiki=use_wiki,
            enable_screenshot=enable_screenshot,
            no_fallback=no_fallback,
        )

        if diagnosis.trace and diagnosis.trace.tools_called:
            yield f"event: tools\ndata: {json.dumps({'tools': diagnosis.trace.tools_called})}\n\n"
            await asyncio.sleep(0.01)

        if diagnosis.file and diagnosis.lines:
            yield f"event: citation\ndata: {json.dumps({'file': diagnosis.file, 'lines': diagnosis.lines})}\n\n"
            await asyncio.sleep(0.01)

        asyncio.create_task(broadcast_to_core(diagnosis.model_dump()))

        yield f"event: diagnosis\ndata: {diagnosis.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    async def _run_llm_conversation(
        self,
        provider: str,
        model_name: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        target_repo: Path,
        screenshot_data: Optional[Dict[str, Any]] = None,
        prior_history: Optional[List[Dict[str, str]]] = None,
        max_turns: int = 4,
        final_synthesis_prompt: str = "Tool budget reached. Provide your complete final response now.",
    ) -> Tuple[str, List[Dict[str, Any]], int, Dict[str, int], float]:
        """Execute a bounded multi-turn tool-enabled conversation across Gemini, OpenAI, or Anthropic."""
        tools_called: List[Dict[str, Any]] = []
        steps = 0
        token_usage: Dict[str, int] = {}
        t_net_0 = time.time()
        raw_text = ""
        bounded_turns = max(1, int(max_turns or 4))
        history_items = prior_history or []

        if provider in ("gemini", "google"):
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            fn_decls = [
                types.FunctionDeclaration(
                    name=td["name"],
                    description=td["description"],
                    parameters=td.get("parameters"),
                )
                for td in TOOL_DEFINITIONS
            ]
            gemini_tool = types.Tool(function_declarations=fn_decls)

            contents: List[Any] = []
            for h in history_items:
                role = "model" if h["role"] == "assistant" else "user"
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=h["content"])]))

            user_parts: List[Any] = []
            if screenshot_data and screenshot_data.get("base64"):
                try:
                    img_bytes = base64.b64decode(screenshot_data["base64"])
                    user_parts.append(
                        types.Part.from_bytes(
                            data=img_bytes,
                            mime_type=screenshot_data.get("mime_type", "image/jpeg"),
                        )
                    )
                except Exception:
                    pass
            user_parts.append(types.Part.from_text(text=user_prompt))
            contents.append(types.Content(role="user", parts=user_parts))

            # IMPORTANT: Never combine response_mime_type="application/json" with tools=[...] on Gemini!
            tool_config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[gemini_tool],
            )

            active_gemini_model = model_name
            fallback_candidates = [
                m
                for m in ("gemini-3.1-flash-lite-preview", "gemini-3-flash-preview")
                if m != active_gemini_model
            ]

            async def _gemini_generate(cfg: Any) -> Any:
                nonlocal active_gemini_model
                try:
                    return await asyncio.to_thread(
                        client.models.generate_content,
                        model=active_gemini_model,
                        contents=contents,
                        config=cfg,
                    )
                except Exception as gen_exc:
                    err_s = str(gen_exc)
                    if (
                        "404" in err_s
                        or "NOT_FOUND" in err_s
                        or "no longer available" in err_s.lower()
                    ):
                        for fb_model in list(fallback_candidates):
                            fallback_candidates.remove(fb_model)
                            try:
                                resp = await asyncio.to_thread(
                                    client.models.generate_content,
                                    model=fb_model,
                                    contents=contents,
                                    config=cfg,
                                )
                                active_gemini_model = fb_model
                                return resp
                            except Exception as fb_exc:
                                fb_s = str(fb_exc)
                                if (
                                    "404" in fb_s
                                    or "NOT_FOUND" in fb_s
                                    or "no longer available" in fb_s.lower()
                                ):
                                    continue
                                raise
                    raise

            for turn in range(bounded_turns):
                response = await _gemini_generate(tool_config)
                steps += 1
                _accumulate_gemini_usage(response, token_usage)

                fcs = _extract_gemini_function_calls(response)
                if not fcs:
                    raw_text = extract_response_text(response, "gemini")
                    break

                # Append model's function-call content
                candidates = getattr(response, "candidates", None) or []
                if candidates and getattr(candidates[0], "content", None) is not None:
                    contents.append(candidates[0].content)
                else:
                    contents.append(
                        types.Content(
                            role="model",
                            parts=[
                                types.Part.from_function_call(
                                    name=fc.name, args=dict(getattr(fc, "args", None) or {})
                                )
                                for fc in fcs
                            ],
                        )
                    )

                fn_resp_parts: List[Any] = []
                for fc in fcs:
                    fc_name = str(fc.name)
                    raw_args = getattr(fc, "args", None)
                    fc_args = dict(raw_args) if raw_args else {}
                    tool_res = await asyncio.to_thread(execute_tool, fc_name, fc_args, target_repo)
                    rec = ToolCallRecord(
                        tool=fc_name,
                        args=fc_args,
                        turn=turn + 1,
                        ok="error" not in tool_res,
                    )
                    tools_called.append(rec.model_dump())
                    fn_resp_parts.append(
                        types.Part.from_function_response(name=fc_name, response=tool_res)
                    )

                contents.append(types.Content(role="user", parts=fn_resp_parts))

            if not raw_text:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=final_synthesis_prompt)],
                    )
                )
                final_resp = await _gemini_generate(
                    types.GenerateContentConfig(system_instruction=system_prompt)
                )
                steps += 1
                _accumulate_gemini_usage(final_resp, token_usage)
                raw_text = extract_response_text(final_resp, "gemini")

            return raw_text, tools_called, steps, token_usage, round(time.time() - t_net_0, 3)

        if provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            openai_tools = [{"type": "function", "function": td} for td in TOOL_DEFINITIONS]

            messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            for h in history_items:
                messages.append({"role": h["role"], "content": h["content"]})

            if screenshot_data and screenshot_data.get("base64"):
                mime = screenshot_data.get("mime_type", "image/jpeg")
                b64 = screenshot_data["base64"]
                user_content: Any = [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": user_prompt},
                ]
            else:
                user_content = user_prompt
            messages.append({"role": "user", "content": user_content})

            for turn in range(bounded_turns):
                completion = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=model_name,
                    messages=messages,
                    tools=openai_tools,
                )
                steps += 1
                if getattr(completion, "usage", None):
                    token_usage["prompt_tokens"] = token_usage.get("prompt_tokens", 0) + int(
                        completion.usage.prompt_tokens or 0
                    )
                    token_usage["completion_tokens"] = token_usage.get("completion_tokens", 0) + int(
                        completion.usage.completion_tokens or 0
                    )
                    token_usage["total_tokens"] = token_usage.get("total_tokens", 0) + int(
                        completion.usage.total_tokens or 0
                    )

                msg = completion.choices[0].message
                tcs = getattr(msg, "tool_calls", None)
                if not tcs:
                    raw_text = extract_response_text(completion, "openai")
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": getattr(msg, "content", None) or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tcs
                        ],
                    }
                )

                for tc in tcs:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments or "{}")
                        if not isinstance(fn_args, dict):
                            fn_args = {}
                    except Exception:
                        fn_args = {}
                    tool_res = await asyncio.to_thread(execute_tool, fn_name, fn_args, target_repo)
                    rec = ToolCallRecord(
                        tool=fn_name,
                        args=fn_args,
                        turn=turn + 1,
                        ok="error" not in tool_res,
                    )
                    tools_called.append(rec.model_dump())
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_res),
                        }
                    )

            if not raw_text:
                messages.append({"role": "user", "content": final_synthesis_prompt})
                final_comp = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=model_name,
                    messages=messages,
                )
                steps += 1
                raw_text = extract_response_text(final_comp, "openai")

            return raw_text, tools_called, steps, token_usage, round(time.time() - t_net_0, 3)

        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            anthropic_tools = [
                {
                    "name": td["name"],
                    "description": td["description"],
                    "input_schema": td["parameters"],
                }
                for td in TOOL_DEFINITIONS
            ]

            messages = []
            for h in history_items:
                messages.append({"role": h["role"], "content": h["content"]})

            if screenshot_data and screenshot_data.get("base64"):
                user_blocks: Any = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": screenshot_data.get("mime_type", "image/jpeg"),
                            "data": screenshot_data["base64"],
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ]
            else:
                user_blocks = user_prompt
            messages.append({"role": "user", "content": user_blocks})

            for turn in range(bounded_turns):
                resp = await asyncio.to_thread(
                    client.messages.create,
                    model=model_name,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=messages,
                    tools=anthropic_tools,
                )
                steps += 1
                usage = getattr(resp, "usage", None)
                if usage:
                    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
                    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                    token_usage["prompt_tokens"] = token_usage.get("prompt_tokens", 0) + in_tok
                    token_usage["completion_tokens"] = token_usage.get("completion_tokens", 0) + out_tok
                    token_usage["total_tokens"] = token_usage.get("total_tokens", 0) + in_tok + out_tok

                content_blocks = getattr(resp, "content", None) or []
                tool_uses = []
                serialized_assistant_blocks = []
                for blk in content_blocks:
                    b_type = blk.get("type") if isinstance(blk, dict) else getattr(blk, "type", None)
                    if b_type == "tool_use":
                        b_id = blk.get("id") if isinstance(blk, dict) else getattr(blk, "id", "")
                        b_name = blk.get("name") if isinstance(blk, dict) else getattr(blk, "name", "")
                        b_in = (
                            blk.get("input")
                            if isinstance(blk, dict)
                            else getattr(blk, "input", None)
                        ) or {}
                        tool_uses.append((b_id, b_name, dict(b_in)))
                        serialized_assistant_blocks.append(
                            {"type": "tool_use", "id": b_id, "name": b_name, "input": dict(b_in)}
                        )
                    elif b_type == "text":
                        b_txt = blk.get("text") if isinstance(blk, dict) else getattr(blk, "text", "")
                        serialized_assistant_blocks.append({"type": "text", "text": b_txt})

                if not tool_uses:
                    raw_text = extract_response_text(resp, "anthropic")
                    break

                messages.append({"role": "assistant", "content": serialized_assistant_blocks})
                tool_results = []
                for b_id, b_name, b_args in tool_uses:
                    tool_res = await asyncio.to_thread(execute_tool, b_name, b_args, target_repo)
                    rec = ToolCallRecord(
                        tool=b_name,
                        args=b_args,
                        turn=turn + 1,
                        ok="error" not in tool_res,
                    )
                    tools_called.append(rec.model_dump())
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": b_id,
                            "content": json.dumps(tool_res),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})

            if not raw_text:
                messages.append({"role": "user", "content": final_synthesis_prompt})
                final_resp = await asyncio.to_thread(
                    client.messages.create,
                    model=model_name,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=messages,
                )
                steps += 1
                raw_text = extract_response_text(final_resp, "anthropic")

            return raw_text, tools_called, steps, token_usage, round(time.time() - t_net_0, 3)

        raise RuntimeError(f"Unsupported LLM provider '{provider}'")

    async def _run_tool_loop(
        self,
        context_text: str,
        req: DiagnoseRequest,
        hint_level: str,
        provider: str,
        model_name: str,
        api_key: str,
        target_repo: Path,
        screenshot_data: Optional[Dict[str, Any]] = None,
        normalized_points: Optional[Dict[str, List[int]]] = None,
    ) -> Tuple[DiagnosisResponse, List[Dict[str, Any]], int, Dict[str, int], Dict[str, float], str]:
        """Run bounded tool-calling loop and extract validated DiagnosisResponse."""
        is_comp = is_code_comprehension_request(req)
        system_prompt = build_diagnose_system_prompt(hint_level=hint_level, is_comprehension=is_comp)
        if is_comp:
            mode_instruction = (
                "### ASSISTANCE MODE: CODE COMPREHENSION WALKTHROUGH\n"
                "The user is experiencing code comprehension confusion (no active compiler/linter diagnostics or failing tests). "
                "Do NOT search for, assert, or invent a bug. Do NOT provide a suggestedFix (set suggestedFix: null). "
                "Provide a clear, illuminating conceptual walkthrough and explanation of the code around the active cursor/selection, "
                "then emit a single valid JSON object matching the DiagnosisResponse schema "
                "(summary, file, lines, quip, uncertain: false, confidence, hintLevel, explanation, citations, suggestedFix: null)."
            )
        else:
            mode_instruction = (
                "### ASSISTANCE MODE: ERROR DIAGNOSIS & FIX\n"
                "Inspect real workspace files via tools if needed to verify exact line numbers and `oldText`, "
                "then emit a single valid JSON object matching the DiagnosisResponse schema "
                "(summary, file, lines, quip, uncertain, confidence, hintLevel, explanation, citations, suggestedFix)."
            )
        user_prompt = (
            f"{context_text}\n\n"
            f"### TARGET HINT LEVEL: {hint_level.upper()}\n"
            f"{mode_instruction}"
        )

        raw_text, tools_called, steps, token_usage, time_network = await self._run_llm_conversation(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            target_repo=target_repo,
            screenshot_data=screenshot_data,
            max_turns=self.max_turns,
            final_synthesis_prompt=(
                "Tool budget reached. Now emit your final DiagnosisResponse JSON object immediately "
                "using the evidence gathered above."
            ),
        )

        if not raw_text or not raw_text.strip():
            raise RuntimeError("LLM returned an empty response")

        t_parse_0 = time.time()
        diag, edit_note = await asyncio.to_thread(
            parse_diagnosis_response, raw_text, hint_level, target_repo
        )
        if is_comp:
            diag.suggestedFix = None
            edit_note = "Code comprehension mode: no suggested fix asserted."
            if diag.file is None and req.editorState and req.editorState.activeFile:
                diag.file = normalize_repo_file_path(req.editorState.activeFile, repo_dir=target_repo)
            if diag.lines is None and req.editorState and req.editorState.cursorLine:
                diag.lines = (req.editorState.cursorLine, req.editorState.cursorLine)
        time_parsing = round(time.time() - t_parse_0, 3)

        timing = {"time_network": time_network, "time_parsing": time_parsing}
        return diag, tools_called, steps, token_usage, timing, edit_note

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """Handle interactive follow-up developer chat in Gordon Ramsay's mentoring voice."""
        target_repo = resolve_repo_dir(req.repoPath)
        provider = get_active_provider()
        model_name = get_active_model(provider)
        active_key = get_active_api_key(provider)

        if not active_key:
            err_msg = f"Missing {expected_api_key_var(provider)} in brain/.env or environment"
            return ChatResponse(
                reply=(
                    "The stove isn't even lit, chef! Add `GEMINI_API_KEY` "
                    "(or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) to `brain/.env` so we can "
                    "dig into this workspace live."
                ),
                mode="offline",
                error=err_msg,
                citations=[],
                suggestedFix=None,
            )

        norm_active_file = normalize_repo_file_path(req.activeFile, repo_dir=target_repo)
        system_prompt = build_chat_system_prompt(
            repo_dir=target_repo,
            active_file=norm_active_file or req.activeFile,
            latest_diagnosis=req.latestDiagnosis,
        )

        # Normalize prior chat history cleanly
        normalized_history: List[Dict[str, str]] = []
        for msg in (req.history or [])[-10:]:
            if not isinstance(msg, dict):
                continue
            r = str(msg.get("role") or "user").lower()
            c = str(msg.get("content") or msg.get("text") or "").strip()
            if c and r in ("user", "assistant", "model"):
                normalized_history.append(
                    {"role": "assistant" if r in ("assistant", "model") else "user", "content": c}
                )

        # Build contextual user message with active file window / snippet / latest diagnosis
        ctx_blocks: List[str] = []
        if req.latestDiagnosis and isinstance(req.latestDiagnosis, dict):
            ctx_blocks.append(
                "### LATEST DIAGNOSIS CONTEXT\n"
                f"- Summary: {req.latestDiagnosis.get('summary')}\n"
                f"- File: {req.latestDiagnosis.get('file')} (lines: {req.latestDiagnosis.get('lines')})\n"
                f"- Explanation: {str(req.latestDiagnosis.get('explanation') or '')[:1200]}"
            )

        hl_range = normalize_lines(req.highlightedLines)
        if req.codeSnippet and req.codeSnippet.strip():
            loc_label = f" ({norm_active_file})" if norm_active_file else ""
            ctx_blocks.append(f"### HIGHLIGHTED CODE SNIPPET{loc_label}\n```\n{req.codeSnippet.strip()}\n```")
        elif norm_active_file and (target_repo / norm_active_file).is_file():
            s_ln = max(1, (hl_range[0] - 15) if hl_range else 1)
            e_ln = (hl_range[1] + 15) if hl_range else 100
            file_slice = await asyncio.to_thread(
                read_file_range, norm_active_file, s_ln, e_ln, target_repo
            )
            if file_slice.get("content"):
                ctx_blocks.append(
                    f"### ACTIVE FILE WINDOW (`{norm_active_file}` L{file_slice['start_line']}-L{file_slice['end_line']})\n"
                    f"```\n{file_slice['content']}\n```"
                )

        ctx_blocks.append(f"### DEVELOPER QUESTION\n{req.message.strip()}")
        full_user_prompt = "\n\n".join(ctx_blocks)

        timeout_budget = float(self.timeout or LLM_TIMEOUT_SECONDS or 22.0)
        try:
            raw_text, _, _, _, _ = await asyncio.wait_for(
                self._run_llm_conversation(
                    provider=provider,
                    model_name=model_name,
                    api_key=active_key,
                    system_prompt=system_prompt,
                    user_prompt=full_user_prompt,
                    target_repo=target_repo,
                    prior_history=normalized_history,
                    max_turns=min(2, max(1, self.max_turns)),
                    final_synthesis_prompt="Now answer the developer's question directly in clear Markdown.",
                ),
                timeout=timeout_budget,
            )
        except asyncio.TimeoutError:
            err_msg = f"Chat LLM call timed out after {timeout_budget:.1f}s"
            return ChatResponse(
                reply=f"Lost flame in the kitchen ({err_msg}). Ask me again in a moment, chef.",
                mode="offline",
                error=err_msg,
            )
        except Exception as exc:
            clean_err = sanitize_secret(str(exc))
            err_msg = f"Chat LLM call failed ({provider}/{model_name}): {clean_err}"
            return ChatResponse(
                reply=f"Kitchen equipment fault ({err_msg}). Check `brain/.env` and try again, chef.",
                mode="offline",
                error=err_msg,
            )

        reply_md, citations, suggested_fix = await asyncio.to_thread(
            extract_chat_reply_and_edit,
            raw_text,
            norm_active_file or req.activeFile,
            req.highlightedLines,
            req.latestDiagnosis,
            target_repo,
        )

        return ChatResponse(
            reply=reply_md,
            mode="brain_live",
            error=None,
            citations=citations,
            suggestedFix=suggested_fix,
        )


async def broadcast_to_core(diagnosis: Union[DiagnosisResponse, Dict[str, Any]]) -> None:
    """Broadcast diagnosis payload to Core Server if active."""
    payload = diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else dict(diagnosis)
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{CORE_SERVER_URL}/events",
                json={
                    "source": "brain",
                    "type": "diagnosis",
                    "timestamp": int(time.time() * 1000),
                    "payload": payload,
                },
            )
    except Exception:
        pass


engine = DiagnosticEngine()
