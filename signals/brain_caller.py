"""Active Caller & Adapter Interface from Role 1+2 (Face + Signals) -> Role 3 (Brain).

Isolates the multimodal output contract — **3 Screen Coordinates + 6 Emotions + IDE Context** —
behind a live HTTP & JSON outbox caller (`BrainCaller` / `ActiveBrainCaller`) targeting
`POST http://127.0.0.1:5002/diagnose` and enriching proactive calls via
`GET http://127.0.0.1:3000/context`.

Mandatory Outgoing Data on Every Brain Call:
  1. `mouse_cursor`:    `{"x": mouse_x_px, "y": mouse_y_px}`
  2. `keyboard_cursor`: `{"x": caret_x_px, "y": caret_y_px}` (vertical text bar `|`)
  3. `eye_gaze`:        `{"x": gaze_x_px,  "y": gaze_y_px}`
  4. `emotions`:        `{"confused", "frustrated", "surprised", "flow", "fatigued", "delighted"}`

Strict AFK Rule: If `status.status == "afk_away"`, the main Brain is NEVER called.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import inspect
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

import requests

from input_caret_tracker import SignalSnapshot, get_screen_size_px
from status_fusion import (
    MultimodalStatusFusionEngine,
    SIX_EMOTION_KEYS,
    UserStatusResult,
    normalize_six_emotions,
    redact_secrets,
)

DEFAULT_BRAIN_URL = "http://127.0.0.1:5002/diagnose"
DEFAULT_CORE_CONTEXT_URL = "http://127.0.0.1:3000/context"
DEFAULT_OUTBOX_PATH = Path(__file__).resolve().parent / "latest_brain_call.json"
LATEST_BRAIN_CALL_PATH = DEFAULT_OUTBOX_PATH

_NON_FILE_WINDOW_TITLES = frozenset(
    {
        "desktop",
        "command prompt",
        "windows powershell",
        "powershell",
        "cmd.exe",
        "program manager",
        "task switching",
        "unknown",
    }
)


def sanitize_editor_active_file(candidate: Optional[str]) -> str:
    """Validate an IDE-supplied `activeFile` and reject raw OS window titles."""
    if not isinstance(candidate, str):
        return ""
    cleaned = candidate.strip()
    if not cleaned:
        return ""
    if any(ch in cleaned for ch in ("\r", "\n", "\x00")):
        return ""
    # Reject Win32 window titles such as "utils.py - WHACK - Visual Studio Code"
    if " - " in cleaned or " — " in cleaned or " • " in cleaned:
        return ""
    if cleaned.lower() in _NON_FILE_WINDOW_TITLES:
        return ""
    return cleaned


@dataclass
class BrainInput:
    """Clean, self-contained 3-Coordinate + 6-Emotion packet sent to Role 3 (Main Brain)."""

    mouse_cursor: Tuple[int, int]
    keyboard_cursor: Tuple[int, int]
    eye_gaze: Tuple[int, int]
    emotions: Dict[str, float]  # All 6: confused, frustrated, surprised, flow, fatigued, delighted
    status: str
    timestamp_ms: int
    keyboard_cursor_detected: bool = True
    gaze_zone: str = "screen_editor"

    def to_clean_dict(self) -> Dict[str, Any]:
        """Minimal, unambiguous JSON schema with the 3 coordinates + 6 emotions front-and-center."""
        mx, my = self.mouse_cursor
        cx, cy = self.keyboard_cursor
        gx, gy = self.eye_gaze
        return {
            "coordinates": {
                "mouse_cursor": {"x": mx, "y": my},
                "keyboard_cursor": {"x": cx, "y": cy, "detected": self.keyboard_cursor_detected},
                "eye_gaze": {"x": gx, "y": gy, "zone": self.gaze_zone},
            },
            "mouse_cursor": {"x": mx, "y": my},
            "keyboard_cursor": {"x": cx, "y": cy, "detected": self.keyboard_cursor_detected},
            "eye_gaze": {"x": gx, "y": gy, "zone": self.gaze_zone},
            "emotions": {k: self.emotions[k] for k in SIX_EMOTION_KEYS},
            "status": self.status,
            "timestamp": self.timestamp_ms,
        }


@dataclass
class BrainCallRecord:
    """Audit entry for each active call dispatched to Role 3 Brain."""

    call_id: int
    timestamp: float
    timestamp_str: str
    trigger_status: str
    confidence: float
    mouse_cursor: Tuple[int, int]
    keyboard_cursor: Tuple[int, int]
    eye_gaze: Tuple[int, int]
    emotions: Dict[str, float]
    stuck_score: float
    hint_level: str
    delivery_mode: str
    response_summary: str
    clean_input: Dict[str, Any]
    payload: Dict[str, Any]
    reasons: List[str] = field(default_factory=list)
    diagnosis_result: Optional[str] = None
    diagnosis_id: Optional[str] = None
    file: Optional[str] = None
    lines: Optional[List[int]] = None
    quip: Optional[str] = None
    source: Optional[str] = None
    error: Optional[str] = None
    http_dispatched: bool = False
    http_completed: bool = False
    http_error: Optional[str] = None

    def to_outbox_dict(self) -> Dict[str, Any]:
        """Serialize record to the locked `signals/latest_brain_call.json` outbox schema."""
        return {
            "call_id": self.call_id,
            "timestamp": self.timestamp,
            "http_dispatched": self.http_dispatched,
            "http_completed": self.http_completed,
            "http_error": self.http_error,
            "user_status": self.trigger_status,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "diagnosis_summary": self.diagnosis_result,
            "source": self.source,
            "diagnosis_id": self.diagnosis_id,
            "file": self.file,
            "lines": self.lines,
            "quip": self.quip,
            "error": self.error,
            "coordinates": self.clean_input.get("coordinates"),
            "emotions": dict(self.emotions),
            "payload": self.payload,
        }


def build_brain_input(status: UserStatusResult) -> BrainInput:
    """Extract the canonical `BrainInput` (3 coordinates + 6 emotions) from `UserStatusResult`."""
    return BrainInput(
        mouse_cursor=status.mouse_cursor,
        keyboard_cursor=status.keyboard_cursor,
        eye_gaze=status.eye_gaze,
        emotions=normalize_six_emotions(status.emotions),
        status=status.status,
        timestamp_ms=int(time.time() * 1000),
        keyboard_cursor_detected=status.keyboard_cursor_detected,
        gaze_zone=status.gaze_zone,
    )


def build_brain_call_payload(
    status: UserStatusResult,
    sig: SignalSnapshot,
    terminal_state: Optional[Dict[str, Any]] = None,
    core_context: Optional[Dict[str, Any]] = None,
    call_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Construct outgoing `/diagnose` JSON payload enriched with `:3000/context` when available."""
    b_in = build_brain_input(status)
    mx, my = b_in.mouse_cursor
    cx, cy = b_in.keyboard_cursor
    gx, gy = b_in.eye_gaze
    sw, sh = get_screen_size_px()

    ctx = core_context if isinstance(core_context, dict) else {}
    raw_repo = ctx.get("workspaceRoot")
    repo_path: Optional[str] = (
        str(raw_repo).strip() if isinstance(raw_repo, str) and raw_repo.strip() else None
    )

    ctx_ed = ctx.get("editorState")
    if not isinstance(ctx_ed, dict):
        ctx_ed = {}

    # NEVER pass sig.active_window_title as editorState.activeFile!
    active_file = sanitize_editor_active_file(ctx_ed.get("activeFile"))
    raw_line = ctx_ed.get("cursorLine")
    raw_col = ctx_ed.get("cursorColumn")
    raw_sel = ctx_ed.get("selectionText")
    raw_diag_cnt = ctx_ed.get("diagnosticsCount")
    raw_diag_sum = ctx_ed.get("diagnosticsSummary")

    editor_state: Dict[str, Any] = {
        "activeFile": active_file,
        "cursorLine": int(raw_line) if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None,
        "cursorColumn": int(raw_col) if isinstance(raw_col, int) and not isinstance(raw_col, bool) else None,
        "selectionText": redact_secrets(str(raw_sel)) if isinstance(raw_sel, str) else None,
        "diagnosticsCount": (
            int(raw_diag_cnt)
            if isinstance(raw_diag_cnt, (int, float)) and not isinstance(raw_diag_cnt, bool)
            else 0
        ),
        "diagnosticsSummary": redact_secrets(str(raw_diag_sum)) if isinstance(raw_diag_sum, str) else "",
        "cursorDwellMs": sig.keyboard_dwell_ms,
        "isUndo": sig.undo_erase_burst > 0,
        "isRedo": sig.undo_redo_flips > 0,
    }

    resolved_terminal = terminal_state
    if resolved_terminal is None:
        ctx_term = ctx.get("terminalState")
        if isinstance(ctx_term, dict) and (
            ctx_term.get("command")
            or ctx_term.get("tailOutput")
            or ctx_term.get("exitCode") is not None
        ):
            raw_exit = ctx_term.get("exitCode")
            raw_dur = ctx_term.get("durationMs")
            resolved_terminal = {
                "command": redact_secrets(str(ctx_term.get("command") or ""))[:200],
                "exitCode": (
                    int(raw_exit)
                    if isinstance(raw_exit, int) and not isinstance(raw_exit, bool)
                    else 0
                ),
                "durationMs": (
                    int(raw_dur)
                    if isinstance(raw_dur, (int, float)) and not isinstance(raw_dur, bool)
                    else 0
                ),
                "tailOutput": redact_secrets(str(ctx_term.get("tailOutput") or ""))[-800:],
            }

    win_title = redact_secrets((sig.active_window_title or "").strip())
    attempt_log = status.attempt_summary
    if win_title:
        attempt_log = (
            f"{attempt_log} [activeWindow: {win_title}]"
            if attempt_log
            else f"[activeWindow: {win_title}]"
        )

    return {
        # --- Locked `:5002/diagnose` Cross-Component Contract Fields ---
        "callId": call_id,
        "captureScreenshot": True,
        "repoPath": repo_path,
        "hintLevel": status.recommended_hint_level,
        "userStatus": status.status,
        "reasons": list(status.reasons),
        "emotions": dict(b_in.emotions),
        "gazePoint": [gx, gy] if status.face_detected else None,
        "cursorScreenPos": [mx, my],
        "caretScreenPos": [cx, cy] if status.keyboard_cursor_detected else None,
        "screenWidth": sw,
        "screenHeight": sh,
        "editorState": editor_state,
        "terminalState": resolved_terminal,
        "attemptLog": attempt_log,
        # --- Grouped & Top-Level 3 Coordinates + Multimodal Status Metadata ---
        "coordinates": {
            "mouse_cursor": {"x": mx, "y": my},
            "keyboard_cursor": {"x": cx, "y": cy, "detected": status.keyboard_cursor_detected},
            "eye_gaze": {"x": gx, "y": gy, "zone": status.gaze_zone},
        },
        "mouse_cursor": {
            "x": mx,
            "y": my,
            "dwell_ms": sig.mouse_dwell_ms,
        },
        "keyboard_cursor": {
            "x": cx,
            "y": cy,
            "detected": status.keyboard_cursor_detected,
            "source": sig.caret_source,
            "dwell_ms": sig.caret_dwell_ms,
            "erase_episodes": sig.erase_episodes,
            "weighted_erase_score": sig.weighted_erase_score,
        },
        "eye_gaze": {
            "x": gx,
            "y": gy,
            "zone": status.gaze_zone,
            "detected": status.face_detected,
            "dist_to_keyboard_cursor_px": status.dist_gaze_to_caret_px,
        },
        "user_status": {
            "state": status.status,
            "confusion_type": getattr(status, "confusion_type", None),
            "diagnostics_count": getattr(status, "diagnostics_count", 0),
            "stuck_score": status.stuck_score,
            "signal_score": status.signal_score,
            "facial_score": status.facial_score,
            "confidence": status.confidence,
            "primary_emotion": status.primary_emotion,
            "body_gesture": status.body_gesture,
            "reasons": list(status.reasons),
        },
        "stream": False,
        "timestamp": b_in.timestamp_ms,
    }


class ActiveBrainCaller:
    """Dispatches 3-coordinate + 6-emotion packets to Role 3 Brain (never when AFK)."""

    def __init__(
        self,
        brain_url: str = DEFAULT_BRAIN_URL,
        http_enabled: bool = True,
        http_timeout_sec: float = 50.0,
        core_context_url: str = DEFAULT_CORE_CONTEXT_URL,
        outbox_path: Optional[Union[Path, str]] = DEFAULT_OUTBOX_PATH,
        write_json_outbox: bool = True,
        console_log: bool = True,
        on_complete_callback: Optional[Callable[[BrainCallRecord], None]] = None,
    ) -> None:
        self.brain_url = brain_url
        env_http = os.environ.get("WHACK_BRAIN_HTTP")
        if env_http is not None and env_http.strip().lower() in ("0", "false", "no", "off"):
            self.http_enabled: bool = False
        else:
            self.http_enabled = bool(http_enabled)
        self.http_timeout_sec: float = float(http_timeout_sec)
        self.core_context_url: str = core_context_url
        self.write_json_outbox: bool = bool(write_json_outbox)
        self.outbox_path: Optional[Path] = (
            Path(outbox_path) if (outbox_path is not None and self.write_json_outbox) else None
        )
        self.console_log = bool(console_log)
        self.last_transport_status: str = (
            "Brain :5002 HTTP Ready" if self.http_enabled else "Brain :5002 HTTP OFF"
        )

        self._callbacks: List[Callable[..., Optional[Dict[str, Any]]]] = []
        self._completion_callbacks: List[Callable[[BrainCallRecord], None]] = []
        if on_complete_callback is not None:
            self._completion_callbacks.append(on_complete_callback)

        self._call_counter: int = 0
        self._history: Deque[BrainCallRecord] = deque(maxlen=25)
        self._lock = threading.Lock()
        self._outbox_lock = threading.Lock()

        self._http_queue: queue.Queue[Optional[Tuple[BrainCallRecord, Dict[str, Any]]]] = (
            queue.Queue(maxsize=8)
        )
        self._stop = threading.Event()
        self._worker_thread = threading.Thread(target=self._http_worker, daemon=True)
        self._worker_thread.start()

    def register_callback(self, fn: Callable[..., Optional[Dict[str, Any]]]) -> None:
        """Register an in-process Python hook or unit-test receiver.

        Supports:
          - 1 arg : `fn(brain_input_dict)` (receives `BrainInput.to_clean_dict()`)
          - 4 args: `fn(mouse_cursor, keyboard_cursor, eye_gaze, emotions_or_payload)`
          - 5 args: `fn(mouse_cursor, keyboard_cursor, eye_gaze, emotions, payload)`
        """
        self._callbacks.append(fn)

    def register_completion_callback(self, fn: Callable[[BrainCallRecord], None]) -> None:
        """Register an async completion listener invoked when `_http_worker` finishes."""
        with self._lock:
            self._completion_callbacks.append(fn)

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_counter

    def recent_calls(self) -> List[BrainCallRecord]:
        with self._lock:
            return list(self._history)

    def _fetch_core_context(self) -> Dict[str, Any]:
        """Query `GET http://127.0.0.1:3000/context` with a fast 0.5s timeout."""
        if not self.http_enabled or not self.core_context_url:
            return {}
        try:
            resp = requests.get(self.core_context_url, timeout=0.5)
            if resp.ok and resp.content:
                data = resp.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    @staticmethod
    def _is_suppressed_by_context(
        ctx: Dict[str, Any], now: Optional[float] = None
    ) -> Optional[str]:
        """Check IDE pause/cooldown flags from `:3000/context`."""
        if not ctx:
            return None
        if ctx.get("cameraPaused") is True:
            return "Suppressed by IDE (cameraPaused=True)"
        cooldown_until = ctx.get("cooldownUntil")
        if isinstance(cooldown_until, (int, float)) and not isinstance(cooldown_until, bool):
            # Support both Unix epoch ms (`Date.now()`) and synthetic monotonic test clocks
            if cooldown_until >= 1e11:
                ref_ms = (
                    (now * 1000.0)
                    if (now is not None and now >= 1e8)
                    else (time.time() * 1000.0)
                )
            else:
                ref_ms = (now * 1000.0) if now is not None else (time.monotonic() * 1000.0)
            if float(cooldown_until) > ref_ms:
                return "Suppressed by IDE (cooldown active)"
        return None

    def _write_outbox_file(self, record: BrainCallRecord) -> None:
        if not self.write_json_outbox or self.outbox_path is None:
            return
        target = Path(self.outbox_path)
        with self._outbox_lock:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                out_obj = record.to_outbox_dict()
                tmp_path = target.with_name(f"{target.name}.{threading.get_ident()}.tmp")
                tmp_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
                tmp_path.replace(target)
            except OSError:
                pass

    def invoke_brain(
        self,
        status: UserStatusResult,
        sig: SignalSnapshot,
        fusion_engine: Optional[MultimodalStatusFusionEngine] = None,
        now: Optional[float] = None,
        proactive: bool = False,
        core_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[BrainCallRecord]:
        """Actively dispatch the 3 coordinates + 6 emotions + IDE context to Role 3 Brain.

        Hard guard: If `status.status == "afk_away"`, NEVER call the main Brain.
        """
        if status.status == "afk_away":
            self.last_transport_status = "AFK Guard: Skipped Brain call"
            return None

        ctx = core_context if core_context is not None else self._fetch_core_context()
        if proactive and (self.http_enabled or core_context is not None):
            suppressed = self._is_suppressed_by_context(ctx, now=now)
            if suppressed is not None:
                self.last_transport_status = suppressed
                return None

        if fusion_engine is not None:
            fusion_engine.mark_brain_called(now=now)

        with self._lock:
            self._call_counter += 1
            call_id = self._call_counter

        term_state = fusion_engine.latest_terminal_dict if fusion_engine is not None else None
        brain_input = build_brain_input(status)
        clean_dict = brain_input.to_clean_dict()
        payload = build_brain_call_payload(
            status=status,
            sig=sig,
            terminal_state=term_state,
            core_context=ctx,
            call_id=call_id,
        )
        six_emotions = dict(brain_input.emotions)

        cb_summary: Optional[str] = None
        cb_quip: Optional[str] = None
        for cb in self._callbacks:
            try:
                params = list(inspect.signature(cb).parameters.values())
                has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
                if has_varargs or len(params) >= 5:
                    res = cb(
                        status.mouse_cursor,
                        status.keyboard_cursor,
                        status.eye_gaze,
                        six_emotions,
                        payload,
                    )
                elif len(params) == 1:
                    res = cb(clean_dict)
                elif len(params) == 4 and params[3].name in ("emotions", "six_emotions"):
                    res = cb(
                        status.mouse_cursor,
                        status.keyboard_cursor,
                        status.eye_gaze,
                        six_emotions,
                    )
                else:
                    res = cb(
                        status.mouse_cursor,
                        status.keyboard_cursor,
                        status.eye_gaze,
                        payload,
                    )
                if isinstance(res, dict):
                    if res.get("summary"):
                        cb_summary = str(res["summary"])
                    if res.get("quip"):
                        cb_quip = str(res["quip"])
            except Exception as exc:
                cb_summary = f"Callback error: {exc.__class__.__name__}"

        if self.http_enabled:
            mode = f"HTTP POST -> {self.brain_url}"
            summary = cb_summary or "Dispatching multimodal telemetry to Brain (:5002/diagnose)..."
            diag_res = cb_summary
            src_val: Optional[str] = None
            self.last_transport_status = f"Brain :5002 HTTP in flight (#{call_id})"
        elif cb_summary:
            mode = "Python Callback"
            summary = f'{cb_summary} — "{cb_quip}"' if cb_quip else cb_summary
            diag_res = cb_summary
            src_val = "callback"
            self.last_transport_status = f"Python Callback OK (#{call_id})"
        else:
            mode = "HTTP Disabled"
            summary = "HTTP dispatch disabled (Brain :5002 HTTP is OFF)"
            diag_res = None
            src_val = None
            self.last_transport_status = f"Brain :5002 HTTP OFF (#{call_id})"

        if self.console_log:
            emo_inline = " ".join(f"{k}={v:.2f}" for k, v in six_emotions.items())
            print(
                f"\n[BRAIN OUTPUT #{call_id}] status={status.status}\n"
                f"  mouse_cursor   : {status.mouse_cursor}\n"
                f"  keyboard_cursor: {status.keyboard_cursor} (detected={status.keyboard_cursor_detected})\n"
                f"  eye_gaze       : {status.eye_gaze} ({status.gaze_zone})\n"
                f"  emotions (6)   : {emo_inline}",
                flush=True,
            )

        rec = BrainCallRecord(
            call_id=call_id,
            timestamp=time.time(),
            timestamp_str=time.strftime("%H:%M:%S"),
            trigger_status=status.status,
            confidence=status.confidence,
            mouse_cursor=status.mouse_cursor,
            keyboard_cursor=status.keyboard_cursor,
            eye_gaze=status.eye_gaze,
            emotions=six_emotions,
            stuck_score=status.stuck_score,
            hint_level=status.recommended_hint_level,
            delivery_mode=mode,
            response_summary=summary,
            clean_input=clean_dict,
            payload=payload,
            reasons=list(status.reasons),
            diagnosis_result=diag_res,
            quip=cb_quip,
            source=src_val,
            http_dispatched=False,
            http_completed=False,
            http_error=None,
        )

        if self.http_enabled:
            try:
                self._http_queue.put_nowait((rec, payload))
                rec.http_dispatched = True
            except queue.Full:
                rec.http_dispatched = False
                rec.http_completed = True
                rec.http_error = "Brain HTTP dispatch queue full"

        with self._lock:
            self._history.append(rec)

        self._write_outbox_file(rec)
        return rec

    def maybe_invoke(
        self,
        status: UserStatusResult,
        sig: SignalSnapshot,
        fusion_engine: MultimodalStatusFusionEngine,
        now: Optional[float] = None,
    ) -> Optional[BrainCallRecord]:
        """Automatically invoke Role 3 Brain if `status.should_call_brain` is True and not AFK."""
        if status.status == "afk_away" or not status.should_call_brain:
            return None
        return self.invoke_brain(
            status=status,
            sig=sig,
            fusion_engine=fusion_engine,
            now=now,
            proactive=True,
        )

    def _http_worker(self) -> None:
        session = requests.Session()
        while not self._stop.is_set():
            try:
                item = self._http_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            if item is None:
                break
            rec, payload = item
            try:
                resp = session.post(
                    self.brain_url,
                    json=payload,
                    timeout=self.http_timeout_sec,
                )
                resp.raise_for_status()
                resp_json = resp.json() if resp.content else {}
                if (
                    isinstance(resp_json, dict)
                    and "summary" not in resp_json
                    and isinstance(resp_json.get("diagnosis"), dict)
                ):
                    resp_json = resp_json["diagnosis"]
                if not isinstance(resp_json, dict):
                    resp_json = {}

                summary_val = resp_json.get("summary")
                quip_val = resp_json.get("quip")
                file_val = resp_json.get("file")
                raw_lines = resp_json.get("lines")
                lines_val: Optional[List[int]] = None
                if isinstance(raw_lines, (list, tuple)) and len(raw_lines) >= 2:
                    try:
                        lines_val = [int(raw_lines[0]), int(raw_lines[1])]
                    except (TypeError, ValueError):
                        lines_val = None
                source_val = str(resp_json.get("source") or "model")
                err_val = resp_json.get("error")
                diag_id_val = resp_json.get("diagnosisId") or resp_json.get("diagnosis_id")
                conf_val = resp_json.get("confidence")

                loc_tag = ""
                if file_val and lines_val:
                    loc_tag = f" [{file_val}:{lines_val[0]}-{lines_val[1]}]"
                elif file_val:
                    loc_tag = f" [{file_val}]"

                diag_str = (
                    str(summary_val) if summary_val is not None else f"HTTP {resp.status_code} OK"
                )
                display_msg = (
                    f'{diag_str}{loc_tag} — "{quip_val}"' if quip_val else f"{diag_str}{loc_tag}"
                )

                with self._lock:
                    rec.diagnosis_id = str(diag_id_val) if diag_id_val is not None else None
                    rec.diagnosis_result = str(summary_val) if summary_val is not None else None
                    rec.quip = str(quip_val) if quip_val is not None else None
                    rec.file = str(file_val) if file_val is not None else None
                    rec.lines = lines_val
                    rec.source = source_val
                    rec.error = str(err_val) if err_val is not None else None
                    if isinstance(conf_val, (int, float)) and not isinstance(conf_val, bool):
                        rec.confidence = float(conf_val)
                    rec.http_completed = True
                    rec.http_error = None
                    rec.response_summary = display_msg
                    rec.timestamp = time.time()
                    self.last_transport_status = (
                        f"Brain :5002 OK [{source_val}] ({time.strftime('%H:%M:%S')})"
                    )
                    callbacks = list(self._completion_callbacks)

            except Exception as exc:
                offline_msg = f"[OFFLINE] Brain server (:5002) unreachable: {exc}"
                with self._lock:
                    rec.source = "offline"
                    rec.http_completed = True
                    rec.http_error = str(exc)
                    rec.error = str(exc)
                    rec.diagnosis_result = offline_msg
                    rec.response_summary = offline_msg
                    rec.timestamp = time.time()
                    self.last_transport_status = (
                        f"Brain :5002 offline ({time.strftime('%H:%M:%S')})"
                    )
                    callbacks = list(self._completion_callbacks)

            self._write_outbox_file(rec)
            for cb in callbacks:
                try:
                    cb(rec)
                except Exception:
                    pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._http_queue.put_nowait(None)
        except queue.Full:
            pass


# Canonical alias so callers can import either `BrainCaller` or `ActiveBrainCaller`
BrainCaller = ActiveBrainCaller
