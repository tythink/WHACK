"""Unified Signal + Facial User Status & Active Brain Caller Prototype GUI (Role 1+2).

Combines:
  - `face/` pipeline (Multi-emotion, Upper-body/Hand gestures, 6-DOF 3D Eyeball Gaze)
  - `signals/` pipeline (Mouse Cursor `(x, y)`, Keyboard Text Cursor `|` `(x, y)`,
    Idle/Dwell timers, Erase/Undo thrash, Mouse Frustration Wiggle, Window switches)
  - Active Role 3 Brain Caller dispatching the 4 mandatory inputs (never called when AFK):
      1. `mouse_cursor`: `(mouse_x_px, mouse_y_px)`
      2. `keyboard_cursor`: `(caret_x_px, caret_y_px)`
      3. `eye_gaze`: `(gaze_x_px, gaze_y_px)`
      4. `emotions`: all 6 emotion scores (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`)

Run from repository root:
    C:\\Users\\davec\\anaconda3\\envs\\whack-face\\python.exe signals/prototype_signals_gui.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import tkinter as tk
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageTk

SIGNALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SIGNALS_DIR.parent
FACE_DIR = REPO_ROOT / "face"

for _p in (str(SIGNALS_DIR), str(FACE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Reuse `face/` computer-vision & calibration modules read-only
from body_gesture_tracker import BodyGestureResult, BodyGestureTracker
from calibration import BaselineCalibrator, CalibrationProfile, DEFAULT_PROFILE_PATH
from emotion_detector import (
    EmotionDetector,
    EmotionResult,
    blendshapes_to_dict,
    build_event_payload,
    extract_feature_dict,
)
from eyeball_tracker import TiltedScreenRegressionModel
from gaze_tracker import GazeResult, GazeTracker, extract_head_pose_degrees
from mock_emitter import AsyncEventClient, DEFAULT_EVENTS_URL
from model_loader import (
    bgr_to_mp_image,
    create_face_landmarker,
    create_hand_landmarker,
    create_pose_landmarker,
    normalize_lighting_bgr,
)
import prototype_gui as face_gui

from brain_caller import ActiveBrainCaller, DEFAULT_BRAIN_URL, build_brain_input
from input_caret_tracker import InputCaretTracker, SignalSnapshot, get_screen_size_px
from status_fusion import (
    ACTIONABLE_STUCK_STATES,
    MultimodalStatusFusionEngine,
    UserStatusResult,
)

STATUS_BADGE_STYLES: Dict[str, Tuple[str, str, str]] = {
    "focused_flow": ("FOCUSED FLOW  (Do-Not-Disturb)", "#064e3b", "#6ee7b7"),
    "calm_reading": ("CALM READING  (Idle + Scanning Gaze)", "#0f172a", "#93c5fd"),
    "confused_thinking": ("CONFUSED / THINKING  (Confirming Dwell...)", "#78350f", "#fde68a"),
    "afk_away": ("AFK / AWAY  (Brain Call Disabled)", "#1e293b", "#94a3b8"),
    "talking_suppressed": ("TALKING / COLLABORATING  (Suppressed)", "#3b0764", "#d8b4fe"),
    "fatigued": ("FATIGUED  (Break Recommended)", "#4c1d95", "#ddd6fe"),
    "eureka_resolved": ("EUREKA / RESOLVED!  (Scores Reset)", "#14532d", "#86efac"),
    "stuck_frozen_at_caret": ("STUCK: FROZEN AT CARET |  -> CALL BRAIN", "#7f1d1d", "#fecaca"),
    "stuck_erase_thrash": ("STUCK: ERASE / UNDO LOOP  -> CALL BRAIN", "#7c2d12", "#fed7aa"),
    "stuck_attention_split": ("STUCK: CARET <-> ERROR PING-PONG  -> CALL BRAIN", "#78350f", "#fde68a"),
    "frustrated_blocked": ("FRUSTRATED / BLOCKED  -> CALL BRAIN", "#881337", "#fda4af"),
}

SAMPLE_SANDBOX_CODE = """def compute_moving_average(values, window=3):
    # Test here OR click into Notepad / VS Code / Browser!
    # 1) Type normally -> FOCUSED FLOW
    # 2) Stop moving mouse & keyboard, stare at '|' + furrow brow -> STUCK AT CARET |
    # 3) Hit Backspace / Ctrl+Z repeatedly -> ERASE THRASH
    total = sum(values[:window])
    return total / window
"""


class CombinedSignalsFaceGUI:
    """Interactive dark-mode dashboard fusing Face/3D-Gaze + Mouse/Caret Signals -> Role 3 Brain."""

    VIDEO_W = 440
    VIDEO_H = 264
    RADAR_W = 360
    RADAR_H = 210

    def __init__(
        self,
        camera_index: int = 0,
        brain_url: str = DEFAULT_BRAIN_URL,
        core_events_url: str = DEFAULT_EVENTS_URL,
        cooldown_sec: float = 20.0,
        headless_test: bool = False,
    ) -> None:
        self.camera_index = camera_index
        self.show_mesh = True
        self.light_norm_enabled = True
        self.desktop_overlay_enabled = False

        self.root = tk.Tk()
        self.root.title("WHACK Role 1+2 — Combined Signal + Facial Status & Active Brain Caller")
        self.root.configure(bg="#0b1120")
        self.root.geometry("1420x860")
        self.root.minsize(1240, 760)

        sw_tk = int(self.root.winfo_screenwidth() or 1920)
        sh_tk = int(self.root.winfo_screenheight() or 1080)
        sw_os, sh_os = get_screen_size_px()
        self.screen_w = sw_os if sw_os > 0 else sw_tk
        self.screen_h = sh_os if sh_os > 0 else sh_tk

        # Shared calibration profile from `face/calibration_profile.json`
        loaded_profile = CalibrationProfile.load(DEFAULT_PROFILE_PATH)
        self.calibrator = BaselineCalibrator(profile=loaded_profile)

        # Face + Body + 3D Eyeball Gaze pipeline
        self.face_landmarker = create_face_landmarker(running_mode="VIDEO")
        self.pose_landmarker = create_pose_landmarker(running_mode="VIDEO")
        self.hand_landmarker = create_hand_landmarker(running_mode="VIDEO")
        self.gaze_tracker = GazeTracker(smoothing_alpha=0.35)
        self.body_tracker = BodyGestureTracker()
        self.emotion_detector = EmotionDetector(smoothing_alpha=0.28)

        # Signals + Unified Status Fusion + Active Brain Caller
        self.input_tracker = InputCaretTracker()
        self.fusion_engine = MultimodalStatusFusionEngine(
            cooldown_sec=cooldown_sec,
            trigger_hold_sec=1.5,
        )
        self.brain_caller = ActiveBrainCaller(
            brain_url=brain_url,
            http_enabled=False,
            console_log=not headless_test,
        )
        self.core_client = AsyncEventClient(endpoint=core_events_url, heartbeat_sec=3.0)

        self.cap: Optional[cv2.VideoCapture] = None if headless_test else face_gui.open_camera(camera_index)
        self._running = True
        self._last_ts_ms = 0
        self._frame_idx = 0
        self._fps_prev = time.monotonic()
        self._fps_smooth = 0.0
        self._photo_ref: Optional[ImageTk.PhotoImage] = None

        self._cached_pose: Optional[list] = None
        self._cached_hands: List[list] = []
        self._recent_gaze_vecs: Deque[List[float]] = deque(maxlen=24)

        # Latest frame outputs for manual Force-Call `[F]`
        self._latest_sig: Optional[SignalSnapshot] = None
        self._latest_status: Optional[UserStatusResult] = None
        self._latest_gaze: Optional[GazeResult] = None
        self._latest_emo: Optional[EmotionResult] = None
        self._latest_body: Optional[BodyGestureResult] = None
        self._last_logged_call_id: int = 0

        # Click-through desktop gaze + caret overlay windows
        self._gaze_dot_win: Optional[tk.Toplevel] = None
        self._gaze_dot_cv: Optional[tk.Canvas] = None
        self._caret_dot_win: Optional[tk.Toplevel] = None

        # 10-Step v4 3D Gaze Calibration state (`[G]`)
        self._gaze_cal_win: Optional[tk.Toplevel] = None
        self._gaze_cal_canvas: Optional[tk.Canvas] = None
        self._gaze_cal_mode: str = "collect"
        self._gaze_cal_step: int = 0
        self._gaze_cal_x: List[List[float]] = []
        self._gaze_cal_y: List[Tuple[float, float]] = []
        self._gaze_cal_hint: str = ""
        self._gaze_cal_fit_px_err: float = 0.0
        self._post_click_target: Optional[Tuple[float, float]] = None
        self._post_click_remaining: int = 0
        self._vor_lock_started_at: float = 0.0
        self._vor_lock_samples: int = 0
        self._vor_ref_yaw: Optional[float] = None
        self._vor_prev_yaw: Optional[float] = None
        self._vor_prev_t: float = 0.0
        self._vor_left_cnt: int = 0
        self._vor_right_cnt: int = 0

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_cal_badge()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Top Header Bar
        hdr = tk.Frame(self.root, bg="#111827", padx=12, pady=8)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr,
            text="WHACK  |  Signal + Facial Fusion  ->  Active Role 3 Brain Caller",
            bg="#111827",
            fg="#f8fafc",
            font=("Segoe UI", 12, "bold"),
        ).pack(side=tk.LEFT)

        self.fps_lbl = tk.Label(
            hdr, text="FPS: --.-", bg="#1f2937", fg="#38bdf8", font=("Consolas", 10, "bold"), padx=8, pady=3
        )
        self.fps_lbl.pack(side=tk.LEFT, padx=(12, 6))

        self.cal_lbl = tk.Label(
            hdr, text="", bg="#1f2937", fg="#fbbf24", font=("Segoe UI", 9, "bold"), padx=8, pady=3
        )
        self.cal_lbl.pack(side=tk.LEFT, padx=4)

        btn_cfg = {"font": ("Segoe UI", 9, "bold"), "relief": tk.FLAT, "padx": 8, "pady": 4, "cursor": "hand2"}

        tk.Button(
            hdr, text="Calibrate Neutral [C]", bg="#2563eb", fg="#ffffff", command=lambda: self.start_neutral_cal(5.0), **btn_cfg
        ).pack(side=tk.RIGHT, padx=3)
        tk.Button(
            hdr, text="3D Gaze Cal [G]", bg="#0891b2", fg="#ffffff", command=self.start_9pt_gaze_calibration, **btn_cfg
        ).pack(side=tk.RIGHT, padx=3)
        self.overlay_btn = tk.Button(
            hdr, text="Desktop Dots [O]: OFF", bg="#334155", fg="#e2e8f0", command=self.toggle_desktop_overlay, **btn_cfg
        )
        self.overlay_btn.pack(side=tk.RIGHT, padx=3)
        self.brain_http_btn = tk.Button(
            hdr, text="Brain :5002 HTTP [B]: OFF", bg="#334155", fg="#e2e8f0", command=self.toggle_brain_http, **btn_cfg
        )
        self.brain_http_btn.pack(side=tk.RIGHT, padx=3)
        tk.Button(
            hdr, text="Force Brain Call [F]", bg="#dc2626", fg="#ffffff", command=self.force_brain_call, **btn_cfg
        ).pack(side=tk.RIGHT, padx=3)
        tk.Button(
            hdr, text="Reset Cooldown [R]", bg="#475569", fg="#f8fafc", command=self.reset_cooldown, **btn_cfg
        ).pack(side=tk.RIGHT, padx=3)

        # Main 3-Column Content Area
        body = tk.Frame(self.root, bg="#0b1120", padx=10, pady=8)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        # === COLUMN 0: Live Camera + Built-in Sandbox Code Editor ===
        col0 = tk.Frame(body, bg="#0b1120")
        col0.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        cam_card = tk.Frame(col0, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=8, pady=8)
        cam_card.pack(fill=tk.X)
        cam_hdr = tk.Frame(cam_card, bg="#111827")
        cam_hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            cam_hdr, text="1. LIVE FACE + BODY + 3D EYEBALL GAZE", bg="#111827", fg="#93c5fd", font=("Segoe UI", 9, "bold")
        ).pack(side=tk.LEFT)
        self.emo_summary_lbl = tk.Label(
            cam_hdr, text="flow (0.80)", bg="#111827", fg="#34d399", font=("Consolas", 9, "bold")
        )
        self.emo_summary_lbl.pack(side=tk.RIGHT)

        self.video_label = tk.Label(cam_card, bg="#050811", width=self.VIDEO_W, height=self.VIDEO_H)
        self.video_label.pack()

        self.gesture_lbl = tk.Label(
            cam_card,
            text="Gesture: None  |  Talking: NO",
            bg="#0f172a",
            fg="#cbd5e1",
            font=("Consolas", 9),
            anchor="w",
            padx=6,
            pady=4,
        )
        self.gesture_lbl.pack(fill=tk.X, pady=(6, 0))

        # Built-in Sandbox Editor Card (for testing `|` Caret + Undo/Backspace right inside GUI!)
        ed_card = tk.Frame(col0, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=8, pady=8)
        ed_card.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        ed_top = tk.Frame(ed_card, bg="#111827")
        ed_top.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            ed_top,
            text="SANDBOX CODE PAD  (or click into VS Code / Notepad!)",
            bg="#111827",
            fg="#fcd34d",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT)

        tk.Button(
            ed_top,
            text="Unfocus Pad",
            bg="#1e293b",
            fg="#cbd5e1",
            font=("Segoe UI", 8),
            relief=tk.FLAT,
            padx=6,
            command=lambda: self.root.focus_set(),
        ).pack(side=tk.RIGHT)

        self.sandbox_text = tk.Text(
            ed_card,
            bg="#090d16",
            fg="#e2e8f0",
            insertbackground="#f59e0b",
            insertwidth=3,
            selectbackground="#1d4ed8",
            font=("Consolas", 10),
            height=11,
            width=48,
            undo=True,
            padx=8,
            pady=6,
            relief=tk.FLAT,
        )
        self.sandbox_text.pack(fill=tk.BOTH, expand=True)
        self.sandbox_text.insert("1.0", SAMPLE_SANDBOX_CODE)
        self.sandbox_text.bind("<KeyPress>", self._on_sandbox_keypress)

        sim_bar = tk.Frame(ed_card, bg="#111827")
        sim_bar.pack(fill=tk.X, pady=(6, 0))
        tk.Label(sim_bar, text="Terminal Sim:", bg="#111827", fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        tk.Button(
            sim_bar,
            text="+ Failing pytest (IndexError)",
            bg="#7f1d1d",
            fg="#fecaca",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=7,
            pady=3,
            command=self._sim_failing_command,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            sim_bar,
            text="✓ Passing pytest (Exit 0)",
            bg="#065f46",
            fg="#a7f3d0",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=7,
            pady=3,
            command=self._sim_passing_command,
        ).pack(side=tk.LEFT, padx=2)

        # === COLUMN 1: 3-Coordinate Screen Radar + Signal/Facial Meters ===
        col1 = tk.Frame(body, bg="#0b1120")
        col1.grid(row=0, column=1, sticky="nsew", padx=(0, 8))

        radar_card = tk.Frame(col1, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=10, pady=8)
        radar_card.pack(fill=tk.X)

        tk.Label(
            radar_card,
            text="2. LIVE 3-COORDINATE DESKTOP TRIANGLE  (Gaze  •  Caret |  •  Mouse)",
            bg="#111827",
            fg="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))

        self.radar_cv = tk.Canvas(
            radar_card, width=self.RADAR_W, height=self.RADAR_H, bg="#070b14", highlightthickness=1, highlightbackground="#334155"
        )
        self.radar_cv.pack()

        legend = tk.Frame(radar_card, bg="#111827")
        legend.pack(fill=tk.X, pady=(5, 0))
        tk.Label(legend, text="◎ Eye Gaze", bg="#111827", fg="#38bdf8", font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(legend, text="| Keyboard Cursor", bg="#111827", fg="#f59e0b", font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(legend, text="● Mouse Cursor", bg="#111827", fg="#f8fafc", font=("Consolas", 9, "bold")).pack(side=tk.LEFT)

        # Signal & Affect Bars Card
        bars_card = tk.Frame(col1, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=10, pady=8)
        bars_card.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        tk.Label(
            bars_card,
            text="SIGNAL + FACIAL FUSION METERS",
            bg="#111827",
            fg="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 6))

        self._meter_specs = [
            ("caret_dwell", "Keyboard / Caret | Dwell (target >= 11s)", "#f59e0b"),
            ("mouse_dwell", "Mouse Cursor Idle / Dwell (target >= 11s)", "#e2e8f0"),
            ("erase_thrash", "Undo / Backspace & Same-Line Caret Thrash", "#fb923c"),
            ("mouse_wiggle", "Mouse Frustration Wiggle / Error Streak", "#f43f5e"),
            ("ping_pong", "Caret <-> Error Attention Split / Tab Hops", "#eab308"),
            ("facial_distress", "Facial + Body Distress (Confused/Frustrated)", "#38bdf8"),
            ("stuck_score", "COMBINED STUCK SCORE  (Brain Trigger Index)", "#ef4444"),
        ]
        self._meter_widgets: Dict[str, Tuple[tk.Canvas, int, tk.Label]] = {}
        for key, title, col in self._meter_specs:
            row_f = tk.Frame(bars_card, bg="#111827")
            row_f.pack(fill=tk.X, pady=3)
            top_f = tk.Frame(row_f, bg="#111827")
            top_f.pack(fill=tk.X)
            tk.Label(top_f, text=title, bg="#111827", fg="#cbd5e1", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
            val_lbl = tk.Label(top_f, text="0.00", bg="#111827", fg=col, font=("Consolas", 9, "bold"))
            val_lbl.pack(side=tk.RIGHT)
            bar_cv = tk.Canvas(row_f, height=10, bg="#1e293b", highlightthickness=0)
            bar_cv.pack(fill=tk.X, pady=(2, 0))
            rect_id = bar_cv.create_rectangle(0, 0, 0, 10, fill=col, width=0)
            self._meter_widgets[key] = (bar_cv, rect_id, val_lbl)

        self.window_info_lbl = tk.Label(
            bars_card,
            text="Active Window: --  |  Caret Source: --",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Consolas", 8),
            anchor="w",
            padx=6,
            pady=4,
        )
        self.window_info_lbl.pack(fill=tk.X, pady=(8, 0))

        # === COLUMN 2: Unified User Status + 3-Coord Readout + Active Brain Caller Log ===
        col2 = tk.Frame(body, bg="#0b1120")
        col2.grid(row=0, column=2, sticky="nsew")

        status_card = tk.Frame(col2, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=10, pady=8)
        status_card.pack(fill=tk.X)

        tk.Label(
            status_card,
            text="3. UNIFIED USER STATUS  (Signal + Facial Decision)",
            bg="#111827",
            fg="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 5))

        self.status_banner = tk.Label(
            status_card,
            text="FOCUSED FLOW  (Do-Not-Disturb)",
            bg="#064e3b",
            fg="#6ee7b7",
            font=("Segoe UI", 11, "bold"),
            pady=8,
            padx=8,
        )
        self.status_banner.pack(fill=tk.X)

        self.reasons_lbl = tk.Label(
            status_card,
            text="• Monitoring inputs...",
            bg="#0f172a",
            fg="#cbd5e1",
            font=("Consolas", 8),
            justify=tk.LEFT,
            anchor="w",
            padx=8,
            pady=6,
            wraplength=410,
        )
        self.reasons_lbl.pack(fill=tk.X, pady=(5, 0))

        # 4 Mandatory Brain Inputs Card (3 Coordinates + 6 Emotions)
        coords_card = tk.Frame(col2, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=10, pady=8)
        coords_card.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            coords_card,
            text="4 INPUTS READY FOR ROLE 3 BRAIN CALL",
            bg="#111827",
            fg="#fcd34d",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))

        self.coord_mouse_lbl = tk.Label(
            coords_card, text="1. mouse_cursor    : (   0,    0)   idle   0.0s", bg="#090d16", fg="#f8fafc", font=("Consolas", 9, "bold"), anchor="w", padx=8, pady=3
        )
        self.coord_mouse_lbl.pack(fill=tk.X, pady=1)

        self.coord_caret_lbl = tk.Label(
            coords_card, text="2. keyboard_cursor : (   0,    0)   dwell  0.0s", bg="#090d16", fg="#f59e0b", font=("Consolas", 9, "bold"), anchor="w", padx=8, pady=3
        )
        self.coord_caret_lbl.pack(fill=tk.X, pady=1)

        self.coord_gaze_lbl = tk.Label(
            coords_card, text="3. eye_gaze        : (   0,    0)   dist->|  0px", bg="#090d16", fg="#38bdf8", font=("Consolas", 9, "bold"), anchor="w", padx=8, pady=3
        )
        self.coord_gaze_lbl.pack(fill=tk.X, pady=1)

        self.coord_emotions_lbl = tk.Label(
            coords_card, text="4. emotions (6)    : conf=0.05 frust=0.04 surp=0.03 flow=0.82", bg="#090d16", fg="#a7f3d0", font=("Consolas", 8, "bold"), anchor="w", padx=8, pady=3
        )
        self.coord_emotions_lbl.pack(fill=tk.X, pady=1)

        self.cooldown_lbl = tk.Label(
            coords_card,
            text="Brain Trigger Gate: Armed  |  Calls Dispatched: 0",
            bg="#111827",
            fg="#94a3b8",
            font=("Consolas", 8),
            anchor="w",
        )
        self.cooldown_lbl.pack(fill=tk.X, pady=(4, 0))

        # Active Brain Call Log & JSON Payload Inspector
        log_card = tk.Frame(col2, bg="#111827", highlightbackground="#1e293b", highlightthickness=1, padx=10, pady=8)
        log_card.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        tk.Label(
            log_card,
            text="ACTIVE BRAIN CALL LOG & LATEST 4-INPUT PAYLOAD",
            bg="#111827",
            fg="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))

        self.call_log_text = tk.Text(
            log_card,
            bg="#090d16",
            fg="#a7f3d0",
            font=("Consolas", 8),
            height=7,
            relief=tk.FLAT,
            padx=6,
            pady=4,
            state=tk.DISABLED,
        )
        self.call_log_text.pack(fill=tk.X)

        self.payload_preview = tk.Text(
            log_card,
            bg="#070b14",
            fg="#93c5fd",
            font=("Consolas", 8),
            height=11,
            relief=tk.FLAT,
            padx=6,
            pady=4,
            state=tk.DISABLED,
        )
        self.payload_preview.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    def _bind_shortcuts(self) -> None:
        def _guard(fn):
            def _wrapped(_event=None):
                if self.root.focus_get() is self.sandbox_text:
                    return
                fn()
            return _wrapped

        for k, fn in (
            ("c", lambda: self.start_neutral_cal(5.0)),
            ("C", lambda: self.start_neutral_cal(5.0)),
            ("g", self.start_9pt_gaze_calibration),
            ("G", self.start_9pt_gaze_calibration),
            ("o", self.toggle_desktop_overlay),
            ("O", self.toggle_desktop_overlay),
            ("b", self.toggle_brain_http),
            ("B", self.toggle_brain_http),
            ("f", self.force_brain_call),
            ("F", self.force_brain_call),
            ("r", self.reset_cooldown),
            ("R", self.reset_cooldown),
            ("m", self._toggle_mesh),
            ("M", self._toggle_mesh),
        ):
            self.root.bind(f"<KeyPress-{k}>", _guard(fn))

    # ------------------------------------------------------------------
    # Controls & Sandbox Editor Hooks
    # ------------------------------------------------------------------
    def _on_sandbox_keypress(self, event) -> None:
        ks = str(getattr(event, "keysym", ""))
        if ks in (
            "Control_L",
            "Control_R",
            "Shift_L",
            "Shift_R",
            "Alt_L",
            "Alt_R",
            "Caps_Lock",
            "Escape",
        ):
            return
        ctrl = bool(getattr(event, "state", 0) & 0x4)
        if ctrl and ks.lower() == "z":
            kind = "undo"
        elif ctrl and ks.lower() == "y":
            kind = "redo"
        elif ks in ("BackSpace", "Delete"):
            kind = "backspace"
        elif ks in ("Return", "KP_Enter"):
            kind = "newline"
        else:
            kind = "forward"
        self.input_tracker.record_sandbox_edit_event(
            is_erase_or_undo=(kind in ("backspace", "undo", "redo")),
            edit_kind=kind,
        )

    def _get_sandbox_caret_screen_xy(self) -> Optional[Tuple[int, int]]:
        """When the built-in sandbox pad has focus, compute exact screen `(x, y)` of `|`."""
        try:
            if self.root.focus_get() is not self.sandbox_text:
                return None
            bbox = self.sandbox_text.bbox("insert")
            if bbox is None:
                return None
            rx = int(self.sandbox_text.winfo_rootx())
            ry = int(self.sandbox_text.winfo_rooty())
            return (rx + int(bbox[0]), ry + int(bbox[1]) + max(int(bbox[3]), 14) // 2)
        except Exception:
            return None

    def _sim_failing_command(self) -> None:
        self.fusion_engine.record_terminal_run(
            command="pytest tests/test_Stage1.py",
            exit_code=1,
            tail_output="Traceback (most recent call last):\n  File 'app.py', line 42\nIndexError: list index out of range",
            duration_ms=410,
        )

    def _sim_passing_command(self) -> None:
        self.fusion_engine.record_terminal_run(
            command="pytest tests/test_Stage1.py",
            exit_code=0,
            tail_output="4 passed in 0.28s",
            duration_ms=280,
        )

    def _toggle_mesh(self) -> None:
        self.show_mesh = not self.show_mesh

    def start_neutral_cal(self, sec: float = 5.0) -> None:
        self.calibrator.start(duration_sec=sec)
        self._refresh_cal_badge()

    def reset_cooldown(self) -> None:
        self.fusion_engine.reset_cooldown()

    def toggle_brain_http(self) -> None:
        self.brain_caller.http_enabled = not self.brain_caller.http_enabled
        on = self.brain_caller.http_enabled
        self.brain_http_btn.configure(
            text=f"Brain :5002 HTTP [B]: {'ON' if on else 'OFF'}",
            bg="#059669" if on else "#334155",
            fg="#ffffff" if on else "#e2e8f0",
        )

    def force_brain_call(self) -> None:
        if self._latest_status is None or self._latest_sig is None:
            return
        rec = self.brain_caller.invoke_brain(
            status=self._latest_status,
            sig=self._latest_sig,
            fusion_engine=self.fusion_engine,
        )
        if rec is not None:
            self._append_call_log(rec)

    def _refresh_cal_badge(self) -> None:
        p = self.calibrator.profile
        n_str = "Neutral:✓" if p.calibrated else "Neutral:Default"
        g_str = "3D-Gaze:✓(v4)" if p.gaze_calibrated else "3D-Gaze:Uncal [Press G]"
        self.cal_lbl.configure(
            text=f"{n_str} | {g_str}",
            fg="#34d399" if (p.calibrated and p.gaze_calibrated) else "#fbbf24",
        )

    # ------------------------------------------------------------------
    # Desktop Click-Through Overlay (`[O]`: Cyan Gaze Ring + Amber Caret `|`)
    # ------------------------------------------------------------------
    def toggle_desktop_overlay(self) -> None:
        self.desktop_overlay_enabled = not self.desktop_overlay_enabled
        on = self.desktop_overlay_enabled
        self.overlay_btn.configure(
            text=f"Desktop Dots [O]: {'ON' if on else 'OFF'}",
            bg="#0284c7" if on else "#334155",
        )
        if not on:
            self._destroy_desktop_overlays()

    def _destroy_desktop_overlays(self) -> None:
        for w_attr in ("_gaze_dot_win", "_caret_dot_win"):
            w = getattr(self, w_attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
                setattr(self, w_attr, None)
        self._gaze_dot_cv = None

    def _update_desktop_overlays(self, status: UserStatusResult) -> None:
        if not self.desktop_overlay_enabled or self._gaze_cal_win is not None:
            return
        if self._gaze_dot_win is None:
            gw = tk.Toplevel(self.root)
            gw.overrideredirect(True)
            gw.attributes("-topmost", True)
            gw.configure(bg="#010101")
            try:
                gw.attributes("-transparentcolor", "#010101")
            except Exception:
                pass
            cv = tk.Canvas(gw, width=44, height=44, bg="#010101", highlightthickness=0)
            cv.pack()
            cv.create_oval(5, 5, 39, 39, outline="#38bdf8", width=3)
            cv.create_oval(20, 20, 24, 24, fill="#ffffff", outline="#ffffff")
            face_gui._make_window_clickthrough(gw)
            self._gaze_dot_win = gw
            self._gaze_dot_cv = cv

        if self._caret_dot_win is None:
            cw = tk.Toplevel(self.root)
            cw.overrideredirect(True)
            cw.attributes("-topmost", True)
            cw.configure(bg="#010101")
            try:
                cw.attributes("-transparentcolor", "#010101")
            except Exception:
                pass
            ccv = tk.Canvas(cw, width=20, height=32, bg="#010101", highlightthickness=0)
            ccv.pack()
            ccv.create_line(10, 2, 10, 30, fill="#f59e0b", width=3)
            ccv.create_line(5, 2, 15, 2, fill="#f59e0b", width=2)
            ccv.create_line(5, 30, 15, 30, fill="#f59e0b", width=2)
            face_gui._make_window_clickthrough(cw)
            self._caret_dot_win = cw

        gx, gy = status.eye_gaze
        self._gaze_dot_win.geometry(f"44x44+{max(0, gx - 22)}+{max(0, gy - 22)}")
        if status.keyboard_cursor_detected:
            cx, cy = status.keyboard_cursor
            self._caret_dot_win.deiconify()
            self._caret_dot_win.geometry(f"20x32+{max(0, cx - 10)}+{max(0, cy - 16)}")

    # ------------------------------------------------------------------
    # 10-Step Head-Invariant 3D Gaze Calibration (`[G]`)
    # ------------------------------------------------------------------
    def start_9pt_gaze_calibration(self) -> None:
        if self._gaze_cal_win is not None:
            return
        win = tk.Toplevel(self.root)
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        win.configure(bg="#050811")
        win.focus_force()

        cv = tk.Canvas(win, bg="#050811", highlightthickness=0, cursor="crosshair")
        cv.pack(fill=tk.BOTH, expand=True)
        cv.bind("<Button-1>", self._on_gaze_cal_click)
        win.bind("<space>", self._on_gaze_cal_space)
        win.bind("<Return>", lambda _: self._close_9pt_gaze_calibration())
        win.bind("<Escape>", lambda _: self._close_9pt_gaze_calibration())
        win.bind("<KeyPress-r>", lambda _: self._restart_9pt_gaze_points())
        win.bind("<KeyPress-R>", lambda _: self._restart_9pt_gaze_points())

        self._gaze_cal_win = win
        self._gaze_cal_canvas = cv
        self._restart_9pt_gaze_points()

    def _restart_9pt_gaze_points(self) -> None:
        self._gaze_cal_mode = "collect"
        self._gaze_cal_step = 0
        self._gaze_cal_x = []
        self._gaze_cal_y = []
        self._gaze_cal_hint = "LOOK directly at the cyan bullseye and CLICK its center (or press Space)"
        self._post_click_target = None
        self._post_click_remaining = 0
        self._vor_lock_started_at = 0.0
        self._vor_lock_samples = 0
        self._vor_ref_yaw = None
        self._vor_prev_yaw = None
        self._vor_prev_t = 0.0
        self._vor_left_cnt = 0
        self._vor_right_cnt = 0

    def _close_9pt_gaze_calibration(self) -> None:
        if self._gaze_cal_win is not None:
            try:
                self._gaze_cal_win.destroy()
            except Exception:
                pass
            self._gaze_cal_win = None
            self._gaze_cal_canvas = None

    def _on_gaze_cal_space(self, _event=None) -> None:
        if self._gaze_cal_mode == "vor_lock":
            self._finalize_gaze_calibration()
            return
        if self._gaze_cal_mode != "collect" or self._gaze_cal_step >= len(face_gui.GAZE_CAL_9PTS):
            return
        _, nx, ny = face_gui.GAZE_CAL_9PTS[self._gaze_cal_step]
        self._record_gaze_cal_point(nx, ny)

    def _on_gaze_cal_click(self, event) -> None:
        if self._gaze_cal_canvas is None:
            return
        sw = max(self._gaze_cal_canvas.winfo_width(), self.screen_w)
        sh = max(self._gaze_cal_canvas.winfo_height(), self.screen_h)
        if self._gaze_cal_mode == "verify":
            self._close_9pt_gaze_calibration()
            return
        if (
            self._gaze_cal_mode != "collect"
            or self._gaze_cal_step >= len(face_gui.GAZE_CAL_9PTS)
            or self._post_click_remaining > 0
        ):
            return
        _, nx, ny = face_gui.GAZE_CAL_9PTS[self._gaze_cal_step]
        if math.hypot(event.x - nx * sw, event.y - ny * sh) > 75:
            self._gaze_cal_hint = "Click directly INSIDE the glowing cyan bullseye ring!"
            return
        self._record_gaze_cal_point(event.x / sw, event.y / sh)

    def _record_gaze_cal_point(self, norm_x: float, norm_y: float) -> None:
        if len(self._recent_gaze_vecs) < 4:
            self._gaze_cal_hint = "Face/eyes not detected clearly — look at the dot and click again"
            return
        for fv in list(self._recent_gaze_vecs)[-6:]:
            self._gaze_cal_x.append(list(fv))
            self._gaze_cal_y.append((float(norm_x), float(norm_y)))
        self._post_click_target = (float(norm_x), float(norm_y))
        self._post_click_remaining = 4

    def _finalize_gaze_calibration(self) -> None:
        model_dict = TiltedScreenRegressionModel.fit(self._gaze_cal_x, self._gaze_cal_y, ridge_lambda=0.85)
        if model_dict is None:
            self._gaze_cal_mode = "collect"
            self._gaze_cal_hint = "Insufficient samples — press R to retry"
            return
        self.calibrator.profile.gaze_calibrated = True
        self.calibrator.profile.gaze_model = model_dict
        self.calibrator.profile.save(DEFAULT_PROFILE_PATH)
        self.gaze_tracker.reset_filters()
        self._refresh_cal_badge()
        diag_px = math.hypot(self.screen_w, self.screen_h)
        self._gaze_cal_fit_px_err = model_dict.get("mean_error_norm", 0.0) * diag_px * 0.55
        self._gaze_cal_mode = "verify"

    def _render_9pt_calibration_overlay(self, gaze: Optional[GazeResult]) -> None:
        if self._gaze_cal_win is None or self._gaze_cal_canvas is None:
            return
        if (
            self._gaze_cal_mode == "collect"
            and self._post_click_remaining > 0
            and self._post_click_target is not None
            and gaze is not None
            and not gaze.eyes_closed
            and gaze.gaze_feature_vec
        ):
            self._gaze_cal_x.append(list(gaze.gaze_feature_vec))
            self._gaze_cal_y.append(self._post_click_target)
            self._post_click_remaining -= 1
            if self._post_click_remaining == 0:
                self._post_click_target = None
                self._gaze_cal_step += 1
                if self._gaze_cal_step >= len(face_gui.GAZE_CAL_9PTS):
                    self._gaze_cal_mode = "vor_lock"
                    self._vor_lock_started_at = time.monotonic()
                    self._vor_prev_t = time.monotonic()

        vor_elapsed = 0.0
        if self._gaze_cal_mode == "vor_lock":
            now_vor = time.monotonic()
            vor_elapsed = now_vor - self._vor_lock_started_at
            if gaze is not None and not gaze.eyes_closed and gaze.eye_blink < 0.35 and gaze.gaze_feature_vec:
                if self._vor_ref_yaw is None:
                    self._vor_ref_yaw = gaze.head_yaw
                dt_v = max(now_vor - self._vor_prev_t, 1e-3)
                yaw_vel = abs(gaze.head_yaw - self._vor_prev_yaw) / dt_v if self._vor_prev_yaw is not None else 0.0
                self._vor_prev_yaw = gaze.head_yaw
                self._vor_prev_t = now_vor
                if vor_elapsed >= face_gui.VOR_LOCK_SETTLE_SEC and yaw_vel < 85.0 and self._vor_lock_samples < 130:
                    self._gaze_cal_x.append(list(gaze.gaze_feature_vec))
                    self._gaze_cal_y.append((0.50, 0.50))
                    self._vor_lock_samples += 1
                    rel_yaw = gaze.head_yaw - self._vor_ref_yaw
                    if rel_yaw <= -7.0:
                        self._vor_left_cnt += 1
                    elif rel_yaw >= 7.0:
                        self._vor_right_cnt += 1
            if vor_elapsed >= face_gui.VOR_LOCK_DURATION_SEC:
                self._finalize_gaze_calibration()

        cv = self._gaze_cal_canvas
        sw = max(cv.winfo_width(), self.screen_w)
        sh = max(cv.winfo_height(), self.screen_h)
        cv.delete("all")
        for idx, (lbl, pnx, pny) in enumerate(face_gui.GAZE_CAL_9PTS):
            px, py = int(pnx * sw), int(pny * sh)
            if self._gaze_cal_mode in ("vor_lock", "verify") or idx < self._gaze_cal_step:
                if not (self._gaze_cal_mode == "vor_lock" and idx == 4):
                    cv.create_oval(px - 10, py - 10, px + 10, py + 10, outline="#10b981", width=2)
            elif idx == self._gaze_cal_step and self._gaze_cal_mode == "collect":
                cv.create_oval(px - 26, py - 26, px + 26, py + 26, outline="#38bdf8", width=2)
                cv.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#ffffff", outline="#ffffff")
                cv.create_text(px, py + (38 if pny < 0.8 else -38), text=lbl, fill="#7dd3fc", font=("Consolas", 11, "bold"))
            else:
                cv.create_oval(px - 8, py - 8, px + 8, py + 8, outline="#334155", width=1)

        if self._gaze_cal_mode == "collect":
            cv.create_text(sw // 2, 42, text=f"3D GAZE CALIBRATION ({self._gaze_cal_step + 1}/10) — {self._gaze_cal_hint}  [Esc: Close]", fill="#f8fafc", font=("Segoe UI", 13, "bold"))
        elif self._gaze_cal_mode == "vor_lock":
            cx, cy = sw // 2, sh // 2
            cv.create_oval(cx - 28, cy - 28, cx + 28, cy + 28, outline="#f59e0b", width=3)
            cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#ffffff", outline="#ffffff")
            l_ok, r_ok = self._vor_left_cnt >= 5, self._vor_right_cnt >= 5
            msg = "Keep EYES on center dot & slowly turn head LEFT then RIGHT!"
            cv.create_text(cx, cy + 48, text=msg, fill="#fcd34d", font=("Segoe UI", 12, "bold"))
            cv.create_text(cx, cy + 76, text=f"[ {'✓ LEFT' if l_ok else '← Left'} ]    [ {'✓ RIGHT' if r_ok else 'Right →'} ]", fill="#4ade80" if (l_ok and r_ok) else "#94a3b8", font=("Consolas", 11, "bold"))
        elif self._gaze_cal_mode == "verify":
            cv.create_text(sw // 2, 48, text=f"✓ 3D Gaze Calibrated (~{self._gaze_cal_fit_px_err:.0f}px fit) — Click anywhere or press Enter to finish", fill="#4ade80", font=("Segoe UI", 14, "bold"))
            if gaze is not None:
                cv.create_oval(gaze.screen_x_px - 18, gaze.screen_y_px - 18, gaze.screen_x_px + 18, gaze.screen_y_px + 18, outline="#38bdf8", width=3)

    # ------------------------------------------------------------------
    # Dashboard Rendering & Main Real-Time Loop
    # ------------------------------------------------------------------
    def _next_timestamp_ms(self) -> int:
        ts = int(time.monotonic() * 1000)
        if ts <= self._last_ts_ms:
            ts = self._last_ts_ms + 1
        self._last_ts_ms = ts
        return ts

    def _draw_3coord_radar(self, status: UserStatusResult) -> None:
        cv = self.radar_cv
        w, h = self.RADAR_W, self.RADAR_H
        cv.delete("all")

        # Screen zone guide (editor top 66% / terminal bottom 34%)
        split_y = int(0.66 * h)
        cv.create_line(0, split_y, w, split_y, fill="#1e293b", dash=(4, 4))
        cv.create_text(48, 12, text="screen_editor", fill="#334155", font=("Consolas", 8))
        cv.create_text(54, split_y + 11, text="screen_terminal", fill="#334155", font=("Consolas", 8))

        def _map_xy(pt: Tuple[int, int]) -> Tuple[int, int]:
            rx = int(np.clip(pt[0] / max(self.screen_w, 1), 0.02, 0.98) * w)
            ry = int(np.clip(pt[1] / max(self.screen_h, 1), 0.03, 0.97) * h)
            return rx, ry

        mx, my = _map_xy(status.mouse_cursor)
        cx, cy = _map_xy(status.keyboard_cursor)
        gx, gy = _map_xy(status.eye_gaze)

        # Connector lines showing spatial triangle relationships
        if status.keyboard_cursor_detected:
            line_col = "#f59e0b" if status.gaze_locked_near_caret else "#475569"
            cv.create_line(gx, gy, cx, cy, fill=line_col, dash=(3, 3), width=1)
        cv.create_line(gx, gy, mx, my, fill="#334155", dash=(2, 4), width=1)

        # 1. Mouse Cursor (White dot + label)
        cv.create_oval(mx - 5, my - 5, mx + 5, my + 5, fill="#f8fafc", outline="#0f172a", width=1)
        cv.create_text(mx + 14, my - 8, text="M", fill="#cbd5e1", font=("Consolas", 8, "bold"))

        # 2. Keyboard Text Cursor `|` (Amber I-beam vertical bar)
        if status.keyboard_cursor_detected:
            cv.create_line(cx, cy - 10, cx, cy + 10, fill="#f59e0b", width=3)
            cv.create_line(cx - 4, cy - 10, cx + 4, cy - 10, fill="#f59e0b", width=2)
            cv.create_line(cx - 4, cy + 10, cx + 4, cy + 10, fill="#f59e0b", width=2)
            if status.gaze_locked_near_caret:
                cv.create_oval(cx - 18, cy - 18, cx + 18, cy + 18, outline="#f59e0b", dash=(2, 2))

        # 3. 3D Eye Gaze (Cyan bullseye ring)
        g_col = "#f43f5e" if status.gaze_zone == "looking_away" else "#38bdf8"
        cv.create_oval(gx - 11, gy - 11, gx + 11, gy + 11, outline=g_col, width=2)
        cv.create_oval(gx - 2, gy - 2, gx + 2, gy + 2, fill="#ffffff", outline="#ffffff")

    def _set_meter(self, key: str, ratio: float, label_str: str) -> None:
        bar_cv, rect_id, val_lbl = self._meter_widgets[key]
        bw = max(bar_cv.winfo_width(), 240)
        fill_w = int(np.clip(ratio, 0.0, 1.0) * bw)
        bar_cv.coords(rect_id, 0, 0, fill_w, 10)
        val_lbl.configure(text=label_str)

    def _append_call_log(self, rec) -> None:
        if rec is None or rec.call_id <= self._last_logged_call_id:
            return
        self._last_logged_call_id = rec.call_id
        emo_str = f"conf={rec.emotions.get('confused', 0.0):.2f} frust={rec.emotions.get('frustrated', 0.0):.2f}"
        line = (
            f"[{rec.timestamp_str}] #{rec.call_id} {rec.trigger_status} "
            f"| mouse={rec.mouse_cursor} caret|={rec.keyboard_cursor} gaze={rec.eye_gaze} | {emo_str}\n"
            f"   -> {rec.response_summary}\n"
        )
        self.call_log_text.configure(state=tk.NORMAL)
        self.call_log_text.insert(tk.END, line)
        self.call_log_text.see(tk.END)
        self.call_log_text.configure(state=tk.DISABLED)

    def _update_ui_panels(
        self,
        sig: SignalSnapshot,
        status: UserStatusResult,
    ) -> None:
        self._draw_3coord_radar(status)

        caret_s = max(sig.keyboard_dwell_ms, sig.caret_dwell_ms) / 1000.0
        mouse_s = sig.mouse_dwell_ms / 1000.0
        erase_val = (
            min(0.15, 0.10 * sig.erase_episodes)
            if (sig.erase_episodes <= 1 and sig.undo_redo_flips == 0)
            else max(sig.caret_line_thrash_score, min(1.0, sig.weighted_erase_score / 4.8))
        )
        wiggle_val = max(sig.mouse_wiggle_score, 0.75 if self.fusion_engine.latest_terminal_dict and self.fusion_engine.latest_terminal_dict.get("exitCode") != 0 else 0.0)
        split_val = min(1.0, 0.22 * status.attention_ping_pong_count + 0.08 * sig.window_switches_per_min)

        ep_note = (
            f"{sig.erase_episodes} ep (typo ok) | {sig.undo_erase_burst}k"
            if (sig.erase_episodes <= 1 and sig.undo_erase_burst > 0 and sig.undo_redo_flips == 0)
            else f"{sig.erase_episodes} eps (w={sig.weighted_erase_score:.1f}) | {sig.undo_erase_burst}k"
        )

        self._set_meter("caret_dwell", caret_s / 14.0, f"{caret_s:4.1f}s")
        self._set_meter("mouse_dwell", mouse_s / 14.0, f"{mouse_s:4.1f}s")
        self._set_meter("erase_thrash", erase_val, ep_note)
        self._set_meter("mouse_wiggle", wiggle_val, f"{wiggle_val:.2f}")
        self._set_meter("ping_pong", split_val, f"{status.attention_ping_pong_count} flips | {sig.window_switches_per_min}/m")
        self._set_meter("facial_distress", status.facial_score, f"{status.primary_emotion} ({status.facial_score:.2f})")
        self._set_meter("stuck_score", status.stuck_score, f"{status.stuck_score:.2f}")

        win_short = sig.active_window_title[:36]
        self.window_info_lbl.configure(
            text=f"Window: {win_short}  |  Caret Source: {sig.caret_source}"
        )

        # Status Banner
        title, bg_col, fg_col = STATUS_BADGE_STYLES.get(
            status.status, (status.status.upper(), "#1e293b", "#f8fafc")
        )
        self.status_banner.configure(text=title, bg=bg_col, fg=fg_col)
        reasons_txt = "\n".join(f"• {r}" for r in status.reasons[:3]) or "• Monitoring..."
        self.reasons_lbl.configure(text=reasons_txt)

        # 4 Mandatory Brain Inputs (3 Coordinates + 6 Emotions)
        mx, my = status.mouse_cursor
        cx, cy = status.keyboard_cursor
        gx, gy = status.eye_gaze
        em = status.emotions
        c_tag = f"dwell {sig.caret_dwell_ms / 1000.0:4.1f}s" if status.keyboard_cursor_detected else "not focused"
        self.coord_mouse_lbl.configure(text=f"1. mouse_cursor    : ({mx:4d}, {my:4d})   idle  {mouse_s:4.1f}s")
        self.coord_caret_lbl.configure(text=f"2. keyboard_cursor : ({cx:4d}, {cy:4d})   {c_tag}")
        self.coord_gaze_lbl.configure(text=f"3. eye_gaze        : ({gx:4d}, {gy:4d})   dist->| {status.dist_gaze_to_caret_px:4.0f}px")
        self.coord_emotions_lbl.configure(
            text=(
                f"4. emotions (6)    : conf={em['confused']:.2f} frust={em['frustrated']:.2f} "
                f"surp={em['surprised']:.2f} flow={em['flow']:.2f} fatig={em['fatigued']:.2f} del={em['delighted']:.2f}"
            )
        )

        if status.status == "afk_away":
            cd_str = "Paused (AFK — Brain Call Disabled)"
        elif status.cooldown_remaining_sec > 0:
            cd_str = f"Cooldown {status.cooldown_remaining_sec:.1f}s"
        elif status.status in ACTIONABLE_STUCK_STATES:
            cd_str = f"Holding {status.sustained_stuck_sec:.1f}/1.5s..."
        else:
            cd_str = "Armed"
        self.cooldown_lbl.configure(
            text=f"Brain Trigger Gate: {cd_str}  |  Calls Dispatched: {self.brain_caller.call_count}  |  {self.brain_caller.last_transport_status}"
        )

        # Live JSON Preview (shows clean 3-Coordinate + 6-Emotion BrainInput packet)
        recent = self.brain_caller.recent_calls()
        if recent:
            preview_obj = recent[-1].clean_input
        else:
            preview_obj = build_brain_input(status).to_clean_dict()

        if (self._frame_idx % 6) == 0:
            pretty = json.dumps(preview_obj, indent=2)
            self.payload_preview.configure(state=tk.NORMAL)
            self.payload_preview.delete("1.0", tk.END)
            self.payload_preview.insert("1.0", pretty)
            self.payload_preview.configure(state=tk.DISABLED)

    def _fit_preview(self, frame_bgr: np.ndarray) -> np.ndarray:
        fh, fw = frame_bgr.shape[:2]
        scale = min(self.VIDEO_W / max(fw, 1), self.VIDEO_H / max(fh, 1))
        nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
        resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((self.VIDEO_H, self.VIDEO_W, 3), dtype=np.uint8)
        y0 = (self.VIDEO_H - nh) // 2
        x0 = (self.VIDEO_W - nw) // 2
        canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
        return canvas

    def _tick(self) -> None:
        if not self._running:
            return

        now = time.monotonic()
        dt = max(now - self._fps_prev, 1e-4)
        self._fps_prev = now
        inst_fps = 1.0 / dt
        self._fps_smooth = inst_fps if self._fps_smooth == 0.0 else 0.88 * self._fps_smooth + 0.12 * inst_fps
        self.fps_lbl.configure(text=f"FPS: {self._fps_smooth:4.1f}")

        # 1. Poll OS Mouse Cursor + Keyboard Caret `|` + Dwell/Thrash
        sandbox_xy = self._get_sandbox_caret_screen_xy()
        sig = self.input_tracker.poll(sandbox_caret=sandbox_xy)
        self._latest_sig = sig

        # 2. Poll Camera + MediaPipe Face / Pose / Hand / 3D Gaze
        face_detected = False
        emotion_res: Optional[EmotionResult] = None
        gaze_res: Optional[GazeResult] = None
        body_res: Optional[BodyGestureResult] = None

        raw_mirrored: Optional[np.ndarray] = None
        if self.cap is not None and self.cap.isOpened():
            ok, raw = self.cap.read()
            if ok and raw is not None and raw.size > 0:
                if raw.shape[1] > 960:
                    new_h = int(raw.shape[0] * (960.0 / raw.shape[1]))
                    raw = cv2.resize(raw, (960, new_h), interpolation=cv2.INTER_AREA)
                raw_mirrored = cv2.flip(raw, 1)

        if raw_mirrored is None:
            face_detected = self._latest_status.face_detected if self._latest_status is not None else True
            display_bgr = np.zeros((self.VIDEO_H, self.VIDEO_W, 3), dtype=np.uint8)
            cv2.putText(
                display_bgr,
                "Camera unavailable — running OS Signals mode",
                (28, self.VIDEO_H // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (180, 195, 215),
                1,
                cv2.LINE_AA,
            )
        else:
            proc_bgr = normalize_lighting_bgr(raw_mirrored) if self.light_norm_enabled else raw_mirrored
            mp_img = bgr_to_mp_image(proc_bgr)
            ts_ms = self._next_timestamp_ms()

            face_res = self.face_landmarker.detect_for_video(mp_img, ts_ms)
            if (self._frame_idx % 2) == 0:
                hand_res = self.hand_landmarker.detect_for_video(mp_img, ts_ms)
                self._cached_hands = list(hand_res.hand_landmarks) if hand_res.hand_landmarks else []
            else:
                pose_res = self.pose_landmarker.detect_for_video(mp_img, ts_ms)
                self._cached_pose = pose_res.pose_landmarks[0] if pose_res.pose_landmarks else None
            self._frame_idx += 1

            if face_res.face_landmarks:
                face_detected = True
                landmarks = face_res.face_landmarks[0]
                blendshapes = (
                    blendshapes_to_dict(face_res.face_blendshapes[0])
                    if face_res.face_blendshapes
                    else {}
                )
                matrix = (
                    np.asarray(face_res.facial_transformation_matrixes[0])
                    if face_res.facial_transformation_matrixes
                    else None
                )

                hp, hy, hr = extract_head_pose_degrees(landmarks, matrix)
                body_res = self.body_tracker.update(
                    landmarks, self._cached_pose, self._cached_hands, hp, hy, hr, self.calibrator.profile
                )
                gaze_res = self.gaze_tracker.update(
                    landmarks=landmarks,
                    blendshapes=blendshapes,
                    profile=self.calibrator.profile,
                    transformation_matrix=matrix,
                    eyes_occluded=body_res.eyes_occluded,
                    frame_bgr=proc_bgr,
                    render_eye_strip=True,
                    screen_width=self.screen_w,
                    screen_height=self.screen_h,
                )
                if not gaze_res.eyes_closed and not body_res.eyes_occluded and gaze_res.gaze_feature_vec:
                    self._recent_gaze_vecs.append(gaze_res.gaze_feature_vec)

                feats = extract_feature_dict(landmarks, blendshapes, gaze_res, body_res)
                if self.calibrator.is_active and not body_res.mouth_occluded and not body_res.eyes_occluded:
                    if self.calibrator.add_sample(feats):
                        self.calibrator.profile.save(DEFAULT_PROFILE_PATH)
                        self._refresh_cal_badge()

                emotion_res = self.emotion_detector.update(feats, gaze_res, self.calibrator.profile, body_res)

                face_gui.draw_face_overlays(
                    proc_bgr,
                    landmarks,
                    gaze_res,
                    emotion_res,
                    show_mesh=self.show_mesh,
                    pose_landmarks=self._cached_pose,
                    hand_landmarks_list=self._cached_hands,
                    body=body_res,
                )

                self.emo_summary_lbl.configure(
                    text=f"{emotion_res.primary_emotion} ({emotion_res.primary_score:.2f})",
                    fg=face_gui.EMOTION_COLORS.get(emotion_res.primary_emotion, "#34d399"),
                )
                g_desc, _ = face_gui.GESTURE_DESCRIPTIONS.get(body_res.primary_gesture, (body_res.primary_gesture, "#94a3b8"))
                self.gesture_lbl.configure(
                    text=f"Gesture: {g_desc[:28]}  |  Talking: {'YES' if emotion_res.talking else 'NO'}"
                )

                face_payload = build_event_payload(emotion_res, gaze_res, body_res)
                self.core_client.maybe_emit(face_payload)
            else:
                self.emo_summary_lbl.configure(text="No Face", fg="#94a3b8")
                self.gesture_lbl.configure(text="Gesture: --  |  Face out of frame (AFK check active)")

            if self._gaze_cal_win is not None:
                self._render_9pt_calibration_overlay(gaze_res)

            preview = self._fit_preview(proc_bgr)
            # Composite zoomed dual-eyeball strip at bottom-right of video preview if available
            if gaze_res is not None and gaze_res.eye_strip_bgr is not None:
                sh_e, sw_e = gaze_res.eye_strip_bgr.shape[:2]
                if sw_e < self.VIDEO_W - 8 and sh_e < self.VIDEO_H - 8:
                    preview[self.VIDEO_H - sh_e - 4 : self.VIDEO_H - 4, self.VIDEO_W - sw_e - 4 : self.VIDEO_W - 4] = gaze_res.eye_strip_bgr

            if self.calibrator.is_active:
                prog, rem = self.calibrator.progress()
                cv2.rectangle(preview, (12, 10), (self.VIDEO_W - 12, 34), (20, 35, 65), -1)
                cv2.putText(
                    preview,
                    f"Calibrating Neutral... {rem:.1f}s ({int(prog * 100)}%)",
                    (20, 27),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (120, 220, 255),
                    1,
                    cv2.LINE_AA,
                )
            display_bgr = preview

        self._latest_gaze = gaze_res
        self._latest_emo = emotion_res
        self._latest_body = body_res

        # 3. Fuse Signal + Facial into Unified User Status & Actively Invoke Role 3 Brain if Needed
        status = self.fusion_engine.evaluate(
            sig=sig,
            gaze=gaze_res,
            emotion=emotion_res,
            body=body_res,
            face_detected=face_detected,
            now=now,
        )
        self._latest_status = status

        call_rec = self.brain_caller.maybe_invoke(
            status=status,
            sig=sig,
            fusion_engine=self.fusion_engine,
            now=now,
        )
        if call_rec is not None:
            self._append_call_log(call_rec)

        self._update_desktop_overlays(status)
        self._update_ui_panels(sig, status)

        rgb = cv2.cvtColor(display_bgr, cv2.COLOR_BGR2RGB)
        self._photo_ref = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        self.video_label.configure(image=self._photo_ref)

        self.root.after(20, self._tick)

    def run(self) -> None:
        self.root.after(15, self._tick)
        self.root.mainloop()

    def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        self._close_9pt_gaze_calibration()
        self._destroy_desktop_overlays()
        self.brain_caller.close()
        self.core_client.close()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        for lm in (self.face_landmarker, self.pose_landmarker, self.hand_landmarker):
            try:
                lm.close()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="WHACK Combined Signal + Facial Prototype GUI")
    parser.add_argument("--camera", type=int, default=0, help="Webcam index (default: 0)")
    parser.add_argument("--brain-url", default=DEFAULT_BRAIN_URL, help="Role 3 Brain /diagnose URL")
    parser.add_argument("--cooldown", type=float, default=20.0, help="Seconds between auto Brain calls")
    args = parser.parse_args()

    gui = CombinedSignalsFaceGUI(
        camera_index=args.camera,
        brain_url=args.brain_url,
        cooldown_sec=args.cooldown,
    )
    gui.run()


if __name__ == "__main__":
    main()
