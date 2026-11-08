"""Combined Signal + Facial User Status Fusion Engine (Role 1+2).

Combines the 4 core inputs passed to Role 3 (Brain):
  1. `mouse_cursor`: `(mouse_x_px, mouse_y_px)`
  2. `keyboard_cursor`: `(caret_x_px, caret_y_px)` (vertical text bar `|`)
  3. `eye_gaze`: `(gaze_x_px, gaze_y_px)`
  4. `emotions`: all 6 affective scores (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`)
together with idle/dwell durations, typo-safe rewrite episodes, mouse frustration wiggle,
and terminal runs to classify the unified User Status and decide when to actively invoke Role 3.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

from input_caret_tracker import SignalSnapshot

SIX_EMOTION_KEYS: Tuple[str, ...] = (
    "confused",
    "frustrated",
    "surprised",
    "flow",
    "fatigued",
    "delighted",
)

ACTIONABLE_STUCK_STATES = (
    "stuck_frozen_at_caret",
    "stuck_erase_thrash",
    "stuck_attention_split",
    "frustrated_blocked",
    "fatigued",
    "error_confusion",
    "code_comprehension_confusion",
)

_SECRET_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{12,})"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{16,})"),
    re.compile(r"((?:api[_-]?key|token|secret|password)\s*[=:]\s*['\"]?[^\s'\"]+)", re.IGNORECASE),
)


def redact_secrets(text: str) -> str:
    """Strip API keys, tokens, and credentials from command/error strings."""
    out = text or ""
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def compute_error_hash(error_tail: str) -> str:
    """Normalize line numbers/hex addresses so identical errors share one hash."""
    cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", error_tail or "")
    cleaned = re.sub(r"\bline \d+\b", "line N", cleaned, flags=re.IGNORECASE)
    last_lines = [ln.strip() for ln in cleaned.strip().splitlines() if ln.strip()][-2:]
    sig = " | ".join(last_lines) if last_lines else "generic_error"
    return hashlib.sha1(sig.encode("utf-8", errors="ignore")).hexdigest()[:10]


def normalize_six_emotions(raw_map: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Ensure all 6 canonical emotions exist as rounded floats in `[0.0, 1.0]`."""
    defaults = {
        "confused": 0.05,
        "frustrated": 0.04,
        "surprised": 0.03,
        "flow": 0.82,
        "fatigued": 0.05,
        "delighted": 0.04,
    }
    src = raw_map or defaults
    return {
        k: round(max(0.0, min(1.0, float(src.get(k, defaults[k])))), 2)
        for k in SIX_EMOTION_KEYS
    }


@dataclass
class TerminalRunRecord:
    timestamp: float
    command: str
    exit_code: int
    duration_ms: int
    error_hash: str
    tail_output: str


@dataclass
class UserStatusResult:
    """Unified Signal + Facial User Status & 4-Input Brain Trigger output."""

    status: str
    stuck_score: float
    signal_score: float
    facial_score: float
    confidence: float
    should_call_brain: bool
    recommended_hint_level: str
    mouse_cursor: Tuple[int, int]
    keyboard_cursor: Tuple[int, int]
    keyboard_cursor_detected: bool
    eye_gaze: Tuple[int, int]
    gaze_zone: str
    emotions: Dict[str, float]  # All 6 emotions: confused, frustrated, surprised, flow, fatigued, delighted
    dist_gaze_to_caret_px: float
    dist_gaze_to_mouse_px: float
    dist_mouse_to_caret_px: float
    gaze_locked_near_caret: bool
    gaze_spread_px: float
    attention_ping_pong_count: int
    primary_emotion: str
    body_gesture: str
    talking: bool
    face_detected: bool
    sustained_stuck_sec: float
    cooldown_remaining_sec: float
    reasons: List[str] = field(default_factory=list)
    attempt_summary: str = ""
    confusion_type: Optional[str] = None
    diagnostics_count: int = 0


class MultimodalStatusFusionEngine:
    """Stateful fusion engine determining unified User Status from Signals + Face/Gaze."""

    CARET_LOCK_RADIUS_PX = 240.0
    PING_PONG_FAR_PX = 340.0
    FACE_OCCLUSION_GRACE_SEC = 3.0  # Hold face state during brief head-scratch / facepalm occlusion
    AFK_NO_FACE_MIN_SEC = 3.0  # Must have no face continuously >= 3s to be AFK
    AFK_TURNED_AWAY_MIN_SEC = 4.0  # Must turn head >=45 deg sideways continuously >= 4s to be AFK
    HEAD_YAW_AWAY_DEG = 45.0
    HEAD_PITCH_AWAY_DEG = 50.0

    def __init__(
        self,
        cooldown_sec: float = 25.0,
        trigger_hold_sec: float = 1.6,
        smoothing_alpha: float = 0.38,
        screen_height: int = 1080,
        fatigue_hold_sec: float = 2.5,
        fatigue_cooldown_sec: float = 180.0,
    ) -> None:
        self.cooldown_sec = float(cooldown_sec)
        self.trigger_hold_sec = float(trigger_hold_sec)
        self.alpha = float(smoothing_alpha)
        self.screen_height = int(screen_height)
        self.fatigue_hold_sec = float(fatigue_hold_sec)
        self.fatigue_cooldown_sec = float(fatigue_cooldown_sec)

        self._smoothed_stuck: float = 0.0
        self._stuck_since: Optional[float] = None
        self._last_brain_call_at: float = -1e9
        self._last_fatigue_call_at: float = -1e9
        self._last_status: str = "focused_flow"
        self._escalation_count: int = 0
        self._recent_distress: bool = False

        # Anti-false-AFK temporal tracking
        self._last_face_seen_at: Optional[float] = None
        self._face_missing_since: Optional[float] = None
        self._turned_away_since: Optional[float] = None
        self._cached_gaze: Optional[Any] = None
        self._cached_emotion: Optional[Any] = None
        self._cached_body: Optional[Any] = None

        # Rolling spatial histories
        self._gaze_hist: Deque[Tuple[float, int, int]] = deque(maxlen=90)
        self._ping_pong_flips: Deque[float] = deque(maxlen=40)
        self._last_region_state: Optional[str] = None

        # Rolling 10-min terminal & behavioral attempt history
        self._terminal_runs: Deque[TerminalRunRecord] = deque(maxlen=30)

        # Editor diagnostics
        self._editor_diagnostics_count: int = 0
        self._editor_diagnostics_summary: str = ""

    def reset_cooldown(self) -> None:
        self._last_brain_call_at = -1e9
        self._last_fatigue_call_at = -1e9
        self._stuck_since = None

    def mark_brain_called(self, now: Optional[float] = None) -> None:
        t = time.monotonic() if now is None else now
        self._last_brain_call_at = t
        if self._last_status == "fatigued":
            self._last_fatigue_call_at = t
        else:
            self._escalation_count = min(3, self._escalation_count + 1)
        self._stuck_since = None

    @property
    def latest_terminal_dict(self) -> Optional[Dict[str, Any]]:
        if not self._terminal_runs:
            return None
        r = self._terminal_runs[-1]
        return {
            "command": r.command,
            "exitCode": r.exit_code,
            "durationMs": r.duration_ms,
            "errorHash": r.error_hash if r.exit_code != 0 else None,
            "tailOutput": r.tail_output,
        }

    def record_editor_diagnostics(self, count: int, summary: str = "") -> None:
        """Record live IDE editor diagnostic count and summary."""
        self._editor_diagnostics_count = max(0, int(count))
        self._editor_diagnostics_summary = str(summary or "")

    def record_terminal_run(
        self,
        command: str,
        exit_code: int,
        tail_output: str = "",
        duration_ms: int = 250,
        now: Optional[float] = None,
    ) -> TerminalRunRecord:
        t = time.monotonic() if now is None else now
        safe_cmd = redact_secrets(command.strip() or "command")[:120]
        safe_tail = redact_secrets(tail_output.strip())[-600:]
        err_hash = compute_error_hash(safe_tail) if exit_code != 0 else ""
        rec = TerminalRunRecord(
            timestamp=t,
            command=safe_cmd,
            exit_code=int(exit_code),
            duration_ms=int(duration_ms),
            error_hash=err_hash,
            tail_output=safe_tail,
        )
        self._terminal_runs.append(rec)
        if exit_code == 0:
            self._escalation_count = 0
        return rec

    def evaluate(
        self,
        sig: SignalSnapshot,
        gaze: Optional[Any] = None,
        emotion: Optional[Any] = None,
        body: Optional[Any] = None,
        face_detected: bool = True,
        now: Optional[float] = None,
        screen_height: Optional[int] = None,
        editor_diagnostics: Optional[Union[int, Dict[str, Any]]] = None,
    ) -> UserStatusResult:
        """Fuse OS `SignalSnapshot` with `face/` Gaze, 6-Emotions, and BodyGesture results."""
        t = time.monotonic() if now is None else now

        if editor_diagnostics is not None:
            if isinstance(editor_diagnostics, int):
                self.record_editor_diagnostics(editor_diagnostics)
            elif isinstance(editor_diagnostics, dict):
                cnt = (
                    editor_diagnostics.get("count")
                    or editor_diagnostics.get("diagnosticsCount")
                    or 0
                )
                sm = (
                    editor_diagnostics.get("summary")
                    or editor_diagnostics.get("diagnosticsSummary")
                    or ""
                )
                self.record_editor_diagnostics(cnt, sm)

        mx, my = int(sig.mouse_x_px), int(sig.mouse_y_px)
        cx, cy = int(sig.caret_x_px), int(sig.caret_y_px)

        gaze_sh = getattr(gaze, "screen_height", None) if gaze is not None else None
        eff_screen_h = int(screen_height or gaze_sh or self.screen_height or 1080)
        terminal_split_y = int(0.66 * max(720, eff_screen_h))

        # 1. Resolve brief face-occlusion flicker (e.g. hand scratching head / facepalm) vs true absence
        if face_detected:
            self._last_face_seen_at = t
            self._face_missing_since = None
            if gaze is not None:
                self._cached_gaze = gaze
            if emotion is not None:
                self._cached_emotion = emotion
            if body is not None:
                self._cached_body = body
            eff_face_present = True
            face_missing_sec = 0.0
        else:
            if self._face_missing_since is None:
                self._face_missing_since = t
            face_missing_sec = t - self._face_missing_since
            if (
                self._last_face_seen_at is not None
                and (t - self._last_face_seen_at) < self.FACE_OCCLUSION_GRACE_SEC
            ):
                # Brief occlusion (< 3s) — keep last valid face/gaze/emotion instead of false AFK!
                eff_face_present = True
                gaze = gaze or self._cached_gaze
                emotion = emotion or self._cached_emotion
                body = body or self._cached_body
            else:
                eff_face_present = False

        raw_emotions = getattr(emotion, "emotions", None) if emotion is not None else None
        six_emotions = normalize_six_emotions(raw_emotions)
        primary_emotion = (
            str(getattr(emotion, "primary_emotion", ""))
            if emotion is not None and getattr(emotion, "primary_emotion", "")
            else max(SIX_EMOTION_KEYS, key=lambda k: six_emotions[k])
        )
        talking = bool(getattr(emotion, "talking", False)) if emotion else False
        body_gesture = str(getattr(body, "primary_gesture", "none")) if body else "none"
        emo_conf = float(getattr(emotion, "confidence", 0.78)) if emotion else 0.55
        gaze_conf = float(getattr(gaze, "confidence", 0.85)) if (gaze and eff_face_present) else 0.45

        confused_val = six_emotions["confused"]
        frustrated_val = six_emotions["frustrated"]
        surprised_val = six_emotions["surprised"]
        fatigued_val = six_emotions["fatigued"]
        delighted_val = six_emotions["delighted"]

        facial_distress = max(confused_val, frustrated_val)
        if body_gesture in ("head_scratch", "chin_rest", "facepalm_or_rub", "hands_thrown_up", "clenched_fist"):
            facial_distress = min(1.0, max(facial_distress, 0.58) + 0.08)

        # 2. Resolve Gaze Coordinate & Prevent Squint/Brow-Furrow/Chin-Tuck/Fatigue-Droop From False-Triggering "AFK"!
        is_actively_confused = (
            facial_distress >= 0.45
            or primary_emotion in ("confused", "frustrated")
            or fatigued_val >= 0.58
            or primary_emotion == "fatigued"
        )
        has_real_gaze = (gaze is not None) and eff_face_present
        if has_real_gaze:
            gx, gy = int(gaze.screen_x_px), int(gaze.screen_y_px)
            raw_zone = str(getattr(gaze, "zone", "screen_editor"))
            head_yaw = float(getattr(gaze, "head_yaw", 0.0))
            head_pitch = float(getattr(gaze, "head_pitch", 0.0))
            head_turned_far = (
                abs(head_yaw) >= self.HEAD_YAW_AWAY_DEG
                or abs(head_pitch) >= self.HEAD_PITCH_AWAY_DEG
            )

            # Key fix: Furrowing brows, squinting, or tucking the chin in confusion narrows eyelids and
            # shifts uncalibrated vertical gaze (`raw_zone == "looking_away"`), even though the user is
            # staring right at the screen! Keep gaze on-screen whenever the head isn't turned far away
            # OR the user is actively showing confusion/frustration while facing the desk (`|yaw| < 50°`).
            if raw_zone == "looking_away" and (
                not head_turned_far or (is_actively_confused and abs(head_yaw) < 50.0)
            ):
                gaze_zone = "screen_terminal" if gy >= terminal_split_y else "screen_editor"
                self._turned_away_since = None
            elif raw_zone == "looking_away" and head_turned_far and not is_actively_confused:
                gaze_zone = "looking_away"
                if self._turned_away_since is None:
                    self._turned_away_since = t
            else:
                gaze_zone = raw_zone
                if gaze_zone != "looking_away":
                    self._turned_away_since = None
        else:
            # Never synthesize gaze = caret when gaze is None / face is not tracked!
            gx, gy = 0, 0
            gaze_zone = "looking_away" if not eff_face_present else "no_gaze"

        turned_away_sec = (
            (t - self._turned_away_since) if self._turned_away_since is not None else 0.0
        )

        # 3. Spatial distances among the 3 coordinates
        dist_gaze_caret = math.hypot(gx - cx, gy - cy) if has_real_gaze else 0.0
        dist_gaze_mouse = math.hypot(gx - mx, gy - my) if has_real_gaze else 0.0
        dist_mouse_caret = math.hypot(mx - cx, my - cy)

        on_screen = has_real_gaze and gaze_zone in (
            "screen_editor",
            "screen_terminal",
            "eyes_closed",
        )
        if has_real_gaze and gaze_zone in ("screen_editor", "screen_terminal"):
            self._gaze_hist.append((t, gx, gy))
        while self._gaze_hist and (t - self._gaze_hist[0][0]) > 3.5:
            self._gaze_hist.popleft()

        if len(self._gaze_hist) >= 4:
            xs = [p[1] for p in self._gaze_hist]
            ys = [p[2] for p in self._gaze_hist]
            gaze_spread_px = math.hypot(
                float(max(xs) - min(xs)) * 0.5, float(max(ys) - min(ys)) * 0.5
            )
        else:
            gaze_spread_px = 0.0

        is_eyes_closed = bool(
            getattr(gaze, "eyes_closed", False)
            or gaze_zone == "eyes_closed"
            or getattr(gaze, "closure_duration_sec", 0.0) >= 0.24
        )

        gaze_locked_near_caret = bool(
            not is_eyes_closed
            and has_real_gaze
            and face_detected
            and on_screen
            and sig.caret_detected
            and dist_gaze_caret <= self.CARET_LOCK_RADIUS_PX
            and gaze_spread_px <= 125.0
        )

        # Attention ping-pong: caret parked while gaze (or mouse) jumps near <-> far repeatedly
        focus_dist = dist_gaze_caret if on_screen else dist_mouse_caret
        if sig.caret_detected and sig.caret_dwell_ms >= 5000:
            cur_reg: Optional[str] = None
            if focus_dist <= self.CARET_LOCK_RADIUS_PX:
                cur_reg = "near"
            elif focus_dist >= self.PING_PONG_FAR_PX:
                cur_reg = "far"
            if cur_reg is not None and self._last_region_state is not None and cur_reg != self._last_region_state:
                self._ping_pong_flips.append(t)
            if cur_reg is not None:
                self._last_region_state = cur_reg
        else:
            self._last_region_state = None

        while self._ping_pong_flips and (t - self._ping_pong_flips[0]) > 25.0:
            self._ping_pong_flips.popleft()
        ping_pong_count = len(self._ping_pong_flips)

        # 4. Prune 10-minute terminal window & count repeated error hashes
        while self._terminal_runs and (t - self._terminal_runs[0].timestamp) > 600.0:
            self._terminal_runs.popleft()
        failed_runs = [r for r in self._terminal_runs if r.exit_code != 0]
        err_counts: Dict[str, int] = {}
        for r in failed_runs:
            err_counts[r.error_hash] = err_counts.get(r.error_hash, 0) + 1
        max_same_error = max(err_counts.values(), default=0)
        just_passed = bool(
            self._terminal_runs
            and self._terminal_runs[-1].exit_code == 0
            and (t - self._terminal_runs[-1].timestamp) <= 4.0
        )

        has_active_errors = (self._editor_diagnostics_count > 0) or (
            len(failed_runs) > 0 and not just_passed
        )
        confusion_type: Optional[str] = None

        kb_idle_s = sig.keyboard_dwell_ms / 1000.0
        caret_idle_s = sig.caret_dwell_ms / 1000.0
        mouse_idle_s = sig.mouse_dwell_ms / 1000.0
        joint_idle_s = min(max(kb_idle_s, caret_idle_s), mouse_idle_s)

        # Component signal sub-scores
        freeze_sub = 0.0
        if on_screen and joint_idle_s >= 4.0:
            freeze_sub = min(1.0, (joint_idle_s - 3.0) / 14.0)
            if gaze_locked_near_caret:
                freeze_sub = min(1.0, freeze_sub + 0.22)

        if sig.erase_episodes <= 1 and sig.undo_redo_flips == 0:
            erase_sub = min(0.15, 0.10 * sig.erase_episodes)
        else:
            erase_sub = min(
                1.0,
                max(
                    sig.caret_line_thrash_score,
                    min(1.0, sig.weighted_erase_score / 4.8),
                ),
            )
        if sig.seconds_since_last_erase >= 3.5 and kb_idle_s < 2.2:
            erase_sub *= 0.35

        split_sub = 0.0
        if caret_idle_s >= 7.0 and (ping_pong_count >= 3 or sig.window_switches_per_min >= 6):
            split_sub = min(
                1.0,
                0.22 * ping_pong_count + 0.08 * max(0, sig.window_switches_per_min - 3),
            )

        term_err_sub = (
            0.78 if max_same_error >= 3 else (0.52 if max_same_error == 2 else (0.28 if failed_runs else 0.0))
        )
        frust_sig_sub = max(sig.mouse_wiggle_score, term_err_sub)

        signal_score = max(
            0.0,
            min(1.0, max(freeze_sub, erase_sub, split_sub, frust_sig_sub)),
        )

        reasons: List[str] = []
        cooldown_rem = max(0.0, self.cooldown_sec - (t - self._last_brain_call_at))

        # True AFK requires idle cursors (>=8s) AND either no face in camera (immediately if camera never saw a face,
        # or >=3s after face left frame) OR head turned >=45 deg sideways for >=4s without active confusion.
        no_face_afk_ready = (not eff_face_present) and (
            self._last_face_seen_at is None or face_missing_sec >= self.AFK_NO_FACE_MIN_SEC
        )
        os_active_without_camera = (not eff_face_present) and (
            kb_idle_s < 8.0 or mouse_idle_s < 8.0
        )
        is_truly_afk = (
            kb_idle_s >= 8.0
            and mouse_idle_s >= 8.0
            and (
                no_face_afk_ready
                or (turned_away_sec >= self.AFK_TURNED_AWAY_MIN_SEC and not is_actively_confused)
            )
        )

        # 5. Priority Guards & Unified User Status Classification
        if talking:
            status = "talking_suppressed"
            raw_stuck = 0.08
            reasons.append("user is speaking (talking guard active)")

        elif is_truly_afk:
            status = "afk_away"
            raw_stuck = 0.0
            self._stuck_since = None
            why = (
                f"no face {max(face_missing_sec, joint_idle_s):.0f}s"
                if not eff_face_present
                else f"head turned away {turned_away_sec:.0f}s"
            )
            reasons.append(f"cursors idle ({joint_idle_s:.0f}s) + {why} -> AFK (main Brain call disabled)")

        elif (just_passed or (delighted_val >= 0.58 and primary_emotion == "delighted")) and (
            self._recent_distress or self._smoothed_stuck >= 0.35 or just_passed
        ):
            status = "eureka_resolved"
            raw_stuck = 0.0
            self._smoothed_stuck = 0.0
            self._stuck_since = None
            self._recent_distress = False
            self._escalation_count = 0
            reasons.append("breakthrough / passing run detected -> stuck state cleared")

        else:
            is_frustrated_blocked = (
                (on_screen or os_active_without_camera)
                and (
                    (sig.mouse_wiggle_score >= 0.55 and (facial_distress >= 0.38 or sig.mouse_wiggle_score >= 0.75))
                    or max_same_error >= 3
                    or (max_same_error >= 2 and facial_distress >= 0.45)
                    or (
                        frustrated_val >= 0.68
                        and body_gesture in ("facepalm_or_rub", "hands_thrown_up", "clenched_fist", "head_shake")
                    )
                )
            )

            resumed_smooth_typing = sig.seconds_since_last_erase >= 3.5 and kb_idle_s < 2.2
            same_line_trapped = (
                sig.trapped_same_line_sec >= 5.0 or sig.caret_line_thrash_score >= 0.48
            )
            tier_a_multimodal_thrash = (
                not resumed_smooth_typing
                and sig.erase_episodes >= 3
                and same_line_trapped
                and (
                    sig.hesitation_erases >= 1
                    or sig.undo_redo_flips >= 1
                    or sig.weighted_erase_score >= 3.2
                    or sig.caret_line_thrash_score >= 0.52
                )
                and facial_distress >= 0.48
            )
            tier_b_pokerface_thrash = (
                not resumed_smooth_typing
                and (sig.erase_episodes >= 5 or sig.weighted_erase_score >= 5.2 or sig.undo_redo_flips >= 3)
                and (sig.trapped_same_line_sec >= 16.0 or sig.caret_line_thrash_score >= 0.62)
            )
            tier_c_nocam_thrash = (
                os_active_without_camera
                and not resumed_smooth_typing
                and sig.erase_episodes >= 3
                and same_line_trapped
                and (
                    sig.hesitation_erases >= 1
                    or sig.undo_redo_flips >= 1
                    or sig.weighted_erase_score >= 3.2
                    or sig.caret_line_thrash_score >= 0.52
                )
            )
            is_erase_thrash = (
                tier_a_multimodal_thrash or tier_b_pokerface_thrash or tier_c_nocam_thrash
            )

            is_attention_split = (
                caret_idle_s >= 7.5
                and ping_pong_count >= 3
                and (facial_distress >= 0.42 or sig.window_switches_per_min >= 5 or ping_pong_count >= 5)
            )

            # Confused / Frozen at Caret:
            # - Strong confusion/frustration (>=0.60) + paused typing (>=4.5s) on screen -> stuck!
            # - Moderate confusion (>=0.48) + cursors idle (>=7.5s) on screen -> stuck!
            # - Poker-face extreme freeze (>=22s) locked on caret -> stuck!
            is_frozen_at_caret = (
                not is_eyes_closed
                and on_screen
                and (
                    (facial_distress >= 0.60 and kb_idle_s >= 4.5 and mouse_idle_s >= 3.0)
                    or (facial_distress >= 0.48 and joint_idle_s >= 7.5)
                    or (joint_idle_s >= 22.0 and gaze_locked_near_caret)
                )
            )

            if is_frustrated_blocked:
                status = "frustrated_blocked"
                raw_stuck = max(0.76, 0.52 * frust_sig_sub + 0.48 * max(facial_distress, surprised_val))
                if sig.mouse_wiggle_score >= 0.55:
                    reasons.append(f"mouse frustration wiggle ({sig.mouse_wiggle_score:.2f})")
                if max_same_error >= 2:
                    reasons.append(f"same terminal error repeated {max_same_error}x")
                if facial_distress >= 0.45 or body_gesture != "none":
                    reasons.append(f"facial/body distress ({primary_emotion}: {facial_distress:.2f}, gesture: {body_gesture})")

            elif is_erase_thrash:
                status = "stuck_erase_thrash"
                raw_stuck = max(0.74, 0.60 * erase_sub + 0.40 * facial_distress)
                reasons.append(
                    f"{sig.erase_episodes} rewrite episodes ({sig.undo_erase_burst} keys, "
                    f"{sig.hesitation_erases} after hesitation, {sig.undo_redo_flips} undo<->redo flips) "
                    f"trapped {sig.trapped_same_line_sec:.1f}s on line"
                )
                if sig.caret_line_thrash_score >= 0.40:
                    reasons.append(f"keyboard_cursor oscillating on same line ({sig.caret_line_thrash_score:.2f})")
                if facial_distress >= 0.42:
                    reasons.append(f"paired with {primary_emotion} ({facial_distress:.2f})")

            elif is_attention_split:
                status = "stuck_attention_split"
                raw_stuck = max(0.72, 0.55 * split_sub + 0.45 * facial_distress)
                reasons.append(
                    f"keyboard_cursor parked {caret_idle_s:.1f}s while gaze/mouse jumped {ping_pong_count}x (>340px)"
                )
                if sig.window_switches_per_min >= 4:
                    reasons.append(f"{sig.window_switches_per_min} window switches/min")
                if facial_distress >= 0.42:
                    reasons.append(f"paired with {primary_emotion} ({facial_distress:.2f})")

            elif is_eyes_closed or (
                eff_face_present
                and fatigued_val >= 0.60
                and (
                    primary_emotion == "fatigued"
                    or fatigued_val >= facial_distress
                    or body_gesture in ("eye_rub", "shoulder_slump")
                )
            ):
                status = "fatigued"
                raw_stuck = max(0.68, fatigued_val, 0.78 if is_eyes_closed else 0.0)
                gest_note = f", gesture: {body_gesture}" if body_gesture != "none" else ""
                if is_eyes_closed:
                    reasons.append("eyes fully closed -> tired/fatigued state (Chef recommends a short break)")
                else:
                    reasons.append(
                        f"sustained tiredness/fatigue ({fatigued_val:.2f}{gest_note}) — Chef recommends a short break"
                    )

            elif is_frozen_at_caret and not is_eyes_closed:
                if facial_distress >= 0.45 or primary_emotion in ("confused", "frustrated"):
                    if has_active_errors:
                        status = "error_confusion"
                        confusion_type = "error_confusion"
                        raw_stuck = max(0.75, 0.48 * freeze_sub + 0.52 * max(facial_distress, 0.52))
                        reasons.append(
                            f"confusion paired with active errors ({self._editor_diagnostics_count} diagnostics, "
                            f"{len(failed_runs)} failed terminal runs) -> error confusion"
                        )
                    else:
                        status = "code_comprehension_confusion"
                        confusion_type = "code_comprehension_confusion"
                        raw_stuck = max(0.75, 0.48 * freeze_sub + 0.52 * max(facial_distress, 0.52))
                        reasons.append(
                            "confusion with 0 diagnostics & clean terminal -> code comprehension confusion"
                        )
                else:
                    status = "stuck_frozen_at_caret"
                    raw_stuck = max(0.75, 0.48 * freeze_sub + 0.52 * max(facial_distress, 0.52))
                    reasons.append(
                        f"keyboard_cursor idle {kb_idle_s:.1f}s & mouse_cursor idle {mouse_idle_s:.1f}s"
                    )
                if gaze_locked_near_caret:
                    reasons.append(f"eye_gaze pinned {dist_gaze_caret:.0f}px from keyboard_cursor |")
                if facial_distress >= 0.45:
                    tag = f" + {body_gesture}" if body_gesture != "none" else ""
                    reasons.append(f"sustained {primary_emotion} ({facial_distress:.2f}){tag}")

            elif on_screen and facial_distress >= 0.48 and not is_eyes_closed:
                confusion_type = (
                    "error_confusion" if has_active_errors else "code_comprehension_confusion"
                )
                status = "confused_thinking"
                raw_stuck = max(0.46, 0.65 * facial_distress + 0.25 * signal_score)
                tag = f" + {body_gesture}" if body_gesture != "none" else ""
                reasons.append(
                    f"active {primary_emotion} ({facial_distress:.2f}){tag} [type={confusion_type}] — confirming dwell ({kb_idle_s:.1f}s)..."
                )

            elif kb_idle_s >= 8.0 and mouse_idle_s >= 6.0 and facial_distress < 0.44 and on_screen:
                status = "calm_reading"
                raw_stuck = 0.14
                reasons.append(
                    f"cursors idle ({joint_idle_s:.1f}s) + calm flow expression (spread {gaze_spread_px:.0f}px) -> reading"
                )

            else:
                status = "focused_flow"
                raw_stuck = max(0.06, 0.30 * signal_score + 0.25 * facial_distress)
                if kb_idle_s < 4.0:
                    reasons.append("steady keyboard_cursor activity (Flow Do-Not-Disturb)")
                else:
                    reasons.append(f"monitoring calmly on {gaze_zone}")

        self._last_status = status
        self._smoothed_stuck = (1.0 - self.alpha) * self._smoothed_stuck + self.alpha * raw_stuck
        stuck_score = round(max(0.0, min(1.0, self._smoothed_stuck)), 2)

        if status in ACTIONABLE_STUCK_STATES:
            if status != "fatigued":
                self._recent_distress = True
            if self._stuck_since is None:
                self._stuck_since = t
            sustained_stuck_sec = t - self._stuck_since
        else:
            self._stuck_since = None
            sustained_stuck_sec = 0.0

        req_hold_sec = self.fatigue_hold_sec if status == "fatigued" else self.trigger_hold_sec
        eff_cooldown_rem = (
            max(cooldown_rem, self.fatigue_cooldown_sec - (t - self._last_fatigue_call_at))
            if status == "fatigued"
            else cooldown_rem
        )

        # Hard rule: NEVER call main Brain when AFK (`status == "afk_away"`)
        should_call_brain = (
            status != "afk_away"
            and status in ACTIONABLE_STUCK_STATES
            and sustained_stuck_sec >= req_hold_sec
            and eff_cooldown_rem <= 0.0
        )

        if self._escalation_count == 0:
            hint_level = "nudge"
        elif self._escalation_count == 1:
            hint_level = "hint"
        else:
            hint_level = "full_explanation"

        confidence = round(
            max(0.45, min(0.99, 0.45 * gaze_conf + 0.40 * emo_conf + (0.12 if sig.caret_detected else 0.03))),
            2,
        )

        attempt_summary = self._build_attempt_summary(
            sig=sig,
            status=status,
            gx=gx,
            gy=gy,
            has_gaze=has_real_gaze,
            six_emotions=six_emotions,
            max_same_error=max_same_error,
            failed_count=len(failed_runs),
        )

        return UserStatusResult(
            status=status,
            stuck_score=stuck_score,
            signal_score=round(signal_score, 2),
            facial_score=round(facial_distress, 2),
            confidence=confidence,
            should_call_brain=should_call_brain,
            recommended_hint_level=hint_level,
            mouse_cursor=(mx, my),
            keyboard_cursor=(cx, cy),
            keyboard_cursor_detected=sig.caret_detected,
            eye_gaze=(gx, gy),
            gaze_zone=gaze_zone,
            emotions=six_emotions,
            dist_gaze_to_caret_px=round(dist_gaze_caret, 1),
            dist_gaze_to_mouse_px=round(dist_gaze_mouse, 1),
            dist_mouse_to_caret_px=round(dist_mouse_caret, 1),
            gaze_locked_near_caret=gaze_locked_near_caret,
            gaze_spread_px=round(gaze_spread_px, 1),
            attention_ping_pong_count=ping_pong_count,
            primary_emotion=primary_emotion,
            body_gesture=body_gesture,
            talking=talking,
            face_detected=eff_face_present,
            sustained_stuck_sec=round(sustained_stuck_sec, 1),
            cooldown_remaining_sec=round(cooldown_rem, 1),
            reasons=reasons,
            attempt_summary=attempt_summary,
            confusion_type=confusion_type,
            diagnostics_count=self._editor_diagnostics_count,
        )

    def _build_attempt_summary(
        self,
        sig: SignalSnapshot,
        status: str,
        gx: int,
        gy: int,
        has_gaze: bool,
        six_emotions: Dict[str, float],
        max_same_error: int,
        failed_count: int,
    ) -> str:
        gaze_tag = f"gaze=({gx},{gy})" if has_gaze else "gaze=none"
        parts: List[str] = [
            f"status={status}",
            f"mouse=({sig.mouse_x_px},{sig.mouse_y_px})",
            f"caret|=({sig.caret_x_px},{sig.caret_y_px}) dwell {sig.caret_dwell_ms / 1000.0:.1f}s",
            gaze_tag,
            f"conf={six_emotions['confused']:.2f}/frust={six_emotions['frustrated']:.2f}",
        ]
        if sig.erase_episodes > 0:
            parts.append(f"{sig.erase_episodes} rewrite ep(s) ({sig.undo_erase_burst} erase keys)")
        if failed_count > 0:
            if max_same_error >= 2:
                parts.append(f"{failed_count} failed runs ({max_same_error}x identical error)")
            else:
                parts.append(f"{failed_count} failed command(s)")
        return "Last 10m: " + "; ".join(parts) + "."
