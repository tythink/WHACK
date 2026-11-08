"""Active Caller & Adapter Interface from Role 1+2 (Face + Signals) -> Role 3 (Brain).

Since Role 3 (`brain/`) is being built in parallel by a teammate, this module isolates
the exact output contract — **3 Screen Coordinates + 6 Emotions** — behind a clean,
plug-and-play adapter (`BrainInput` + `call_teammate_brain`) that already works end-to-end
right now via a built-in Placeholder Brain, live JSON file outbox (`latest_brain_call.json`),
Python callbacks, and optional HTTP POST (`http://localhost:5002/diagnose`).

Mandatory Outgoing Data on Every Brain Call:
  1. `mouse_cursor`:    `{"x": mouse_x_px, "y": mouse_y_px}`
  2. `keyboard_cursor`: `{"x": caret_x_px, "y": caret_y_px}` (vertical text bar `|`)
  3. `eye_gaze`:        `{"x": gaze_x_px,  "y": gaze_y_px}`
  4. `emotions`:        `{"confused", "frustrated", "surprised", "flow", "fatigued", "delighted"}`

Strict AFK Rule: If `status.status == "afk_away"`, the main Brain is NEVER called.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import inspect
import json
import math
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import requests

from input_caret_tracker import SignalSnapshot, get_screen_size_px
from status_fusion import (
    MultimodalStatusFusionEngine,
    SIX_EMOTION_KEYS,
    UserStatusResult,
    normalize_six_emotions,
)

DEFAULT_BRAIN_URL = "http://localhost:5002/diagnose"
LATEST_BRAIN_CALL_PATH = Path(__file__).resolve().parent / "latest_brain_call.json"


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


# ==============================================================================
# PLUG-AND-PLAY HOOK FOR TEAMMATE'S BRAIN (Role 3)
# Once your teammate's `brain` module/API is ready, swap its call right here
# (or pass a callback to `ActiveBrainCaller.register_callback(...)` / toggle HTTP `[B]`).
# ==============================================================================
def call_teammate_brain(brain_input: BrainInput, full_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Placeholder Main Brain receiver consuming the 3 coordinates + 6 emotions right now."""
    mx, my = brain_input.mouse_cursor
    cx, cy = brain_input.keyboard_cursor
    gx, gy = brain_input.eye_gaze
    emos = brain_input.emotions

    dist_to_caret = math.hypot(gx - cx, gy - cy)
    dist_to_mouse = math.hypot(gx - mx, gy - my)
    if brain_input.keyboard_cursor_detected and dist_to_caret <= 240:
        focus_spot = f"near keyboard_cursor | ({cx},{cy}) [{dist_to_caret:.0f}px]"
    elif dist_to_mouse <= 220:
        focus_spot = f"near mouse_cursor ({mx},{my}) [{dist_to_mouse:.0f}px]"
    else:
        focus_spot = f"screen point ({gx},{gy}) [{brain_input.gaze_zone}]"

    top2 = sorted(emos.items(), key=lambda kv: kv[1], reverse=True)[:2]
    emo_tag = ", ".join(f"{k}={v:.2f}" for k, v in top2)

    return {
        "accepted": True,
        "mode": "placeholder_brain_stub",
        "focus_target": focus_spot,
        "summary": (
            f"[Placeholder Brain] Received 3 coords + 6 emotions | "
            f"Gaze {focus_spot} | Top emotions: {emo_tag}"
        ),
    }


@dataclass
class BrainCallRecord:
    """Audit entry for each active call dispatched to Role 3 Brain."""

    call_id: int
    timestamp_str: str
    trigger_status: str
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
) -> Dict[str, Any]:
    """Construct outgoing Brain JSON with 3 coordinates + 6 emotions at top level + optional context."""
    b_in = build_brain_input(status)
    mx, my = b_in.mouse_cursor
    cx, cy = b_in.keyboard_cursor
    gx, gy = b_in.eye_gaze

    return {
        # --- 1. Grouped & Top-Level 3 Coordinates + 6 Emotions (Primary Contract) ---
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
            "dist_to_keyboard_cursor_px": status.dist_gaze_to_caret_px,
        },
        "emotions": dict(b_in.emotions),
        # --- Role 3 `DiagnoseRequest` spatial coordinate fields (matches `brain/models.py`) ---
        "gazePoint": [gx, gy],
        "cursorScreenPos": [mx, my],
        "caretScreenPos": [cx, cy] if status.keyboard_cursor_detected else None,
        "screenWidth": get_screen_size_px()[0],
        "screenHeight": get_screen_size_px()[1],
        # --- 2. Extra Context (available if Role 3 wants status/dwell/terminal metadata) ---
        "user_status": {
            "state": status.status,
            "stuck_score": status.stuck_score,
            "signal_score": status.signal_score,
            "facial_score": status.facial_score,
            "confidence": status.confidence,
            "primary_emotion": status.primary_emotion,
            "body_gesture": status.body_gesture,
            "reasons": list(status.reasons),
        },
        "hintLevel": status.recommended_hint_level,
        "attemptLog": status.attempt_summary,
        "editorState": {
            "activeFile": sig.active_window_title,
            "cursorDwellMs": sig.keyboard_dwell_ms,
            "isUndo": sig.undo_erase_burst > 0,
            "isRedo": False,
            "diagnosticsCount": 0,
        },
        "terminalState": terminal_state,
        "stream": False,
        "timestamp": b_in.timestamp_ms,
    }


class ActiveBrainCaller:
    """Dispatches 3-coordinate + 6-emotion packets to Role 3 Brain (never when AFK)."""

    def __init__(
        self,
        brain_url: str = DEFAULT_BRAIN_URL,
        http_enabled: bool = False,
        write_json_outbox: bool = True,
        console_log: bool = True,
    ) -> None:
        self.brain_url = brain_url
        self.http_enabled = bool(http_enabled)
        self.write_json_outbox = bool(write_json_outbox)
        self.console_log = bool(console_log)
        self.last_transport_status: str = "Placeholder Brain Stub Active (HTTP :5002 OFF)"

        self._callbacks: List[Callable[..., Optional[Dict[str, Any]]]] = []
        self._call_counter: int = 0
        self._history: Deque[BrainCallRecord] = deque(maxlen=25)
        self._lock = threading.Lock()

        self._http_queue: queue.Queue[Optional[Tuple[int, Dict[str, Any]]]] = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._worker_thread = threading.Thread(target=self._http_worker, daemon=True)
        self._worker_thread.start()

    def register_callback(self, fn: Callable[..., Optional[Dict[str, Any]]]) -> None:
        """Register an in-process Role 3 Python function.

        Flexibly supports whichever signature your teammate writes:
          - 1 arg : `fn(brain_input_or_dict)` (receives `BrainInput.to_clean_dict()`)
          - 4 args: `fn(mouse_cursor, keyboard_cursor, eye_gaze, emotions)` (or legacy 4th arg `payload`)
          - 5 args: `fn(mouse_cursor, keyboard_cursor, eye_gaze, emotions, payload)`
        """
        self._callbacks.append(fn)

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_counter

    def recent_calls(self) -> List[BrainCallRecord]:
        with self._lock:
            return list(self._history)

    def _write_outbox_file(self, clean_dict: Dict[str, Any], full_payload: Dict[str, Any]) -> None:
        if not self.write_json_outbox:
            return
        try:
            out_obj = {
                **clean_dict,
                "user_status": full_payload.get("user_status"),
            }
            tmp_path = LATEST_BRAIN_CALL_PATH.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
            tmp_path.replace(LATEST_BRAIN_CALL_PATH)
        except OSError:
            pass

    def invoke_brain(
        self,
        status: UserStatusResult,
        sig: SignalSnapshot,
        fusion_engine: Optional[MultimodalStatusFusionEngine] = None,
        now: Optional[float] = None,
    ) -> Optional[BrainCallRecord]:
        """Actively output the 3 coordinates + 6 emotions to Role 3 Brain.

        Hard guard: If `status.status == "afk_away"`, NEVER call the main Brain.
        """
        if status.status == "afk_away":
            self.last_transport_status = "AFK Guard: Skipped Brain call"
            return None

        term_state = fusion_engine.latest_terminal_dict if fusion_engine is not None else None
        brain_input = build_brain_input(status)
        clean_dict = brain_input.to_clean_dict()
        payload = build_brain_call_payload(status, sig, terminal_state=term_state)
        six_emotions = dict(brain_input.emotions)

        if fusion_engine is not None:
            fusion_engine.mark_brain_called(now=now)

        with self._lock:
            self._call_counter += 1
            call_id = self._call_counter

        self._write_outbox_file(clean_dict, payload)

        cb_summary: Optional[str] = None
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
                if isinstance(res, dict) and res.get("summary"):
                    cb_summary = str(res["summary"])
            except Exception as exc:
                cb_summary = f"Callback error: {exc.__class__.__name__}"

        if cb_summary:
            mode = "Python Callback"
            summary = cb_summary
            self.last_transport_status = f"Python Callback OK (#{call_id})"
        elif self.http_enabled:
            mode = f"HTTP POST -> {self.brain_url}"
            summary = "Dispatching 3 coords + 6 emotions to Role 3 Brain (:5002)..."
        else:
            stub_res = call_teammate_brain(brain_input, payload)
            mode = "Placeholder Brain Stub"
            summary = str(stub_res.get("summary", "Dispatched to Placeholder Brain"))
            self.last_transport_status = f"Outbox -> latest_brain_call.json (#{call_id})"

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
            timestamp_str=time.strftime("%H:%M:%S"),
            trigger_status=status.status,
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
        )
        with self._lock:
            self._history.append(rec)

        if self.http_enabled:
            try:
                self._http_queue.put_nowait((call_id, payload))
            except queue.Full:
                pass

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
        return self.invoke_brain(status=status, sig=sig, fusion_engine=fusion_engine, now=now)

    def _http_worker(self) -> None:
        session = requests.Session()
        while not self._stop.is_set():
            try:
                item = self._http_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            if item is None:
                break
            call_id, payload = item
            try:
                resp = session.post(self.brain_url, json=payload, timeout=3.5)
                if resp.ok:
                    data = resp.json() if resp.content else {}
                    diag_text = str(data.get("summary") or f"HTTP {resp.status_code} OK")
                    quip = str(data.get("quip") or "")
                    full_msg = f"{diag_text} ({quip})" if quip else diag_text
                    self.last_transport_status = f"Brain :5002 HTTP {resp.status_code} ({time.strftime('%H:%M:%S')})"
                else:
                    full_msg = f"Brain :5002 returned HTTP {resp.status_code}"
                    self.last_transport_status = full_msg
            except requests.RequestException:
                full_msg = "Role 3 HTTP (:5002) offline — saved to latest_brain_call.json"
                self.last_transport_status = "HTTP :5002 offline (saved to latest_brain_call.json)"

            with self._lock:
                for rec in self._history:
                    if rec.call_id == call_id:
                        rec.response_summary = full_msg
                        break

    def close(self) -> None:
        self._stop.set()
        try:
            self._http_queue.put_nowait(None)
        except queue.Full:
            pass
