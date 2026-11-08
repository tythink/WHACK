"""Interactive Multimodal Face + 3D Eyeball + Body/Hand Prototype GUI for WHACK Role 1.

Run from repository root:
    python face/prototype_gui.py
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import ttk
from typing import Deque, Dict, List, Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageTk

if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

FACE_DIR = Path(__file__).resolve().parent
if str(FACE_DIR) not in sys.path:
    sys.path.insert(0, str(FACE_DIR))

from body_gesture_tracker import (
    FINGERTIP_IDXS,
    HAND_CONNECTIONS,
    UPPER_POSE_CONNECTIONS,
    BodyGestureResult,
    BodyGestureTracker,
)
from calibration import BaselineCalibrator, CalibrationProfile, DEFAULT_PROFILE_PATH
from emotion_detector import (
    EMOTION_KEYS,
    EmotionDetector,
    EmotionResult,
    blendshapes_to_dict,
    build_event_payload,
    extract_feature_dict,
)
from eyeball_tracker import TiltedScreenRegressionModel
from gaze_tracker import (
    EYE_A_EAR_PTS,
    EYE_A_IRIS_CENTER,
    EYE_A_IRIS_RING,
    EYE_B_EAR_PTS,
    EYE_B_IRIS_CENTER,
    EYE_B_IRIS_RING,
    GazeResult,
    GazeTracker,
    extract_head_pose_degrees,
)
from mock_emitter import AsyncEventClient, DEFAULT_EVENTS_URL
from model_loader import (
    bgr_to_mp_image,
    create_face_landmarker,
    create_hand_landmarker,
    create_pose_landmarker,
    normalize_lighting_bgr,
)

BROW_A_PTS = (70, 63, 105, 66, 107)
BROW_B_PTS = (300, 293, 334, 296, 336)
LIPS_OUTER_PTS = (
    61, 146, 91, 181, 84, 17, 314, 405, 321, 375,
    291, 409, 270, 269, 267, 0, 37, 39, 40, 185, 61,
)
FACE_OVAL_PTS = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
    397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
    172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109, 10,
)

EMOTION_COLORS: Dict[str, str] = {
    "confused": "#f59e0b",
    "frustrated": "#ef4444",
    "surprised": "#38bdf8",
    "flow": "#10b981",
    "fatigued": "#a855f7",
    "delighted": "#22c55e",
}

EMOTION_LABELS: Dict[str, str] = {
    "confused": "Confused (Brow + Head Scratch)",
    "frustrated": "Frustrated (Lip Press / Facepalm)",
    "surprised": "Surprised (Brow Raise + Wide)",
    "flow": "Flow / Neutral (Focused)",
    "fatigued": "Fatigued (Droop / Slump / Eye Rub)",
    "delighted": "Delighted / Eureka (Smile / 👍)",
}

GESTURE_DESCRIPTIONS: Dict[str, tuple[str, str]] = {
    "none": ("None (Neutral Posture)", "#94a3b8"),
    "head_scratch": ("HEAD SCRATCH  (+Confused)", "#fbbf24"),
    "chin_rest": ("CHIN REST / THINKING  (+Confused)", "#fbbf24"),
    "facepalm_or_rub": ("FACEPALM / FOREHEAD RUB  (+Frustrated)", "#f87171"),
    "eye_rub": ("EYE RUB  (+Fatigued)", "#c084fc"),
    "hands_thrown_up": ("HANDS THROWN UP  (+Frustrated)", "#f87171"),
    "clenched_fist": ("CLENCHED FIST  (+Frustrated)", "#f87171"),
    "thumbs_up": ("THUMBS UP  (+Delighted / Eureka)", "#4ade80"),
    "mouth_cover": ("MOUTH COVER  (Occlusion Guard)", "#38bdf8"),
    "head_shake": ("HEAD SHAKE  (+Frustrated)", "#f87171"),
    "head_tilt": ("HEAD TILT 'HUH?'  (+Confused)", "#fbbf24"),
    "lean_in": ("LEANING IN  (+Concentration/Confused)", "#fbbf24"),
    "shoulder_slump": ("SHOULDER SLUMP  (+Fatigued)", "#c084fc"),
    "shoulder_shrug": ("SHOULDER SHRUG  (+Confused)", "#fbbf24"),
    "head_nod": ("HEAD NOD  (+Delighted)", "#4ade80"),
}

FUSION_STYLE: Dict[str, tuple[str, str]] = {
    "ok": ("#064e3b", "#6ee7b7"),
    "positive": ("#14532d", "#86efac"),
    "warn": ("#78350f", "#fcd34d"),
    "alert": ("#7f1d1d", "#fca5a5"),
    "muted": ("#1e293b", "#94a3b8"),
}

# 3x3 9-point tilted-screen calibration grid `(label, norm_x, norm_y)` + Step 10 VOR Head-Move Lock
GAZE_CAL_9PTS: tuple[tuple[str, float, float], ...] = (
    ("1/10  TOP-LEFT", 0.08, 0.10),
    ("2/10  TOP-CENTER", 0.50, 0.10),
    ("3/10  TOP-RIGHT", 0.92, 0.10),
    ("4/10  MID-LEFT", 0.08, 0.50),
    ("5/10  CENTER", 0.50, 0.50),
    ("6/10  MID-RIGHT", 0.92, 0.50),
    ("7/10  BOTTOM-LEFT (Terminal)", 0.08, 0.90),
    ("8/10  BOTTOM-CENTER (Terminal)", 0.50, 0.90),
    ("9/10  BOTTOM-RIGHT (Terminal)", 0.92, 0.90),
)
VOR_LOCK_DURATION_SEC: float = 6.5
VOR_LOCK_SETTLE_SEC: float = 0.60


def _make_window_clickthrough(win: tk.Toplevel) -> None:
    """Apply Win32 WS_EX_LAYERED | WS_EX_TRANSPARENT so clicks pass straight through."""
    if not sys.platform.startswith("win"):
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            hwnd = win.winfo_id()
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent
        )
    except Exception:
        pass


def open_camera(
    camera_index: int, width: int = 1280, height: int = 720
) -> Optional[cv2.VideoCapture]:
    """Open webcam at HD resolution when supported so eyeballs have 2x vertical detail."""
    backends = [cv2.CAP_DSHOW, cv2.CAP_ANY] if sys.platform.startswith("win") else [cv2.CAP_ANY]
    for backend in backends:
        cap = cv2.VideoCapture(camera_index, backend)
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return cap
            cap.release()
    return None


def _draw_polyline(
    frame: np.ndarray,
    landmarks: Sequence,
    indices: Sequence[int],
    color: tuple[int, int, int],
    thickness: int = 1,
    closed: bool = False,
) -> None:
    h, w = frame.shape[:2]
    pts = np.array(
        [[int(landmarks[i].x * w), int(landmarks[i].y * h)] for i in indices],
        dtype=np.int32,
    )
    cv2.polylines(
        frame, [pts], isClosed=closed, color=color, thickness=thickness, lineType=cv2.LINE_AA
    )


def draw_body_and_hands_overlays(
    frame_bgr: np.ndarray,
    pose_landmarks: Optional[Sequence],
    hand_landmarks_list: Sequence[Sequence],
    body: Optional[BodyGestureResult] = None,
) -> None:
    """Draw upper-body posture lines and 21-point hand skeletons on BGR frame."""
    h, w = frame_bgr.shape[:2]
    active = body is not None and body.primary_gesture != "none"

    if pose_landmarks and len(pose_landmarks) >= 17:
        pose_color = (80, 190, 255) if (body and body.posture != "upright") else (120, 140, 165)
        for a_idx, b_idx in UPPER_POSE_CONNECTIONS:
            pa, pb = pose_landmarks[a_idx], pose_landmarks[b_idx]
            va = getattr(pa, "visibility", 1.0)
            vb = getattr(pb, "visibility", 1.0)
            if (va is None or va > 0.45) and (vb is None or vb > 0.45):
                ax, ay = int(pa.x * w), int(pa.y * h)
                bx, by = int(pb.x * w), int(pb.y * h)
                cv2.line(frame_bgr, (ax, ay), (bx, by), pose_color, 2, cv2.LINE_AA)
        for j_idx in (11, 12, 13, 14, 15, 16):
            pj = pose_landmarks[j_idx]
            vj = getattr(pj, "visibility", 1.0)
            if vj is None or vj > 0.45:
                cv2.circle(
                    frame_bgr,
                    (int(pj.x * w), int(pj.y * h)),
                    4,
                    (90, 220, 255),
                    -1,
                    cv2.LINE_AA,
                )

    for hand in hand_landmarks_list:
        bone_col = (0, 200, 255) if active else (180, 220, 110)
        for a_idx, b_idx in HAND_CONNECTIONS:
            ax, ay = int(hand[a_idx].x * w), int(hand[a_idx].y * h)
            bx, by = int(hand[b_idx].x * w), int(hand[b_idx].y * h)
            cv2.line(frame_bgr, (ax, ay), (bx, by), bone_col, 2, cv2.LINE_AA)
        for idx, pt in enumerate(hand):
            px, py = int(pt.x * w), int(pt.y * h)
            is_tip = idx in FINGERTIP_IDXS
            cv2.circle(
                frame_bgr,
                (px, py),
                4 if is_tip else 2,
                (60, 255, 255) if is_tip else (230, 240, 255),
                -1,
                cv2.LINE_AA,
            )


def draw_face_overlays(
    frame_bgr: np.ndarray,
    landmarks: Sequence,
    gaze: GazeResult,
    emotion: EmotionResult,
    show_mesh: bool = True,
    pose_landmarks: Optional[Sequence] = None,
    hand_landmarks_list: Sequence[Sequence] = (),
    body: Optional[BodyGestureResult] = None,
) -> None:
    """Draw face contours, 3D eyeball/iris rings, body/hand skeletons, and gaze ray."""
    h, w = frame_bgr.shape[:2]

    if show_mesh:
        draw_body_and_hands_overlays(frame_bgr, pose_landmarks, hand_landmarks_list, body)

        for idx in FACE_OVAL_PTS[:-1:2]:
            px, py = int(landmarks[idx].x * w), int(landmarks[idx].y * h)
            cv2.circle(frame_bgr, (px, py), 1, (110, 125, 140), -1, cv2.LINE_AA)

        brow_bgr = (
            (50, 150, 255)
            if emotion.primary_emotion in ("confused", "frustrated")
            else (220, 210, 90)
        )
        _draw_polyline(frame_bgr, landmarks, BROW_A_PTS, brow_bgr, thickness=2)
        _draw_polyline(frame_bgr, landmarks, BROW_B_PTS, brow_bgr, thickness=2)

        _draw_polyline(
            frame_bgr, landmarks, EYE_A_EAR_PTS, (180, 230, 180), thickness=1, closed=True
        )
        _draw_polyline(
            frame_bgr, landmarks, EYE_B_EAR_PTS, (180, 230, 180), thickness=1, closed=True
        )

        # Draw projected 3D eyeball sphere circles + iris rings + optical vectors per eye
        if len(landmarks) >= 478 and not gaze.eyes_closed:
            for corners, center_idx, ring_idxs in (
                ((33, 133), EYE_A_IRIS_CENTER, EYE_A_IRIS_RING),
                ((362, 263), EYE_B_IRIS_CENTER, EYE_B_IRIS_RING),
            ):
                c1, c2 = landmarks[corners[0]], landmarks[corners[1]]
                scx = int(0.5 * (c1.x + c2.x) * w)
                scy = int(0.5 * (c1.y + c2.y) * h)
                sphere_r = max(5, int(0.44 * np.hypot((c2.x - c1.x) * w, (c2.y - c1.y) * h)))
                cv2.circle(frame_bgr, (scx, scy), sphere_r, (140, 120, 80), 1, cv2.LINE_AA)

                cx = int(landmarks[center_idx].x * w)
                cy = int(landmarks[center_idx].y * h)
                ring_pts = [
                    (int(landmarks[r].x * w), int(landmarks[r].y * h)) for r in ring_idxs
                ]
                radius = max(
                    2,
                    int(np.mean([np.hypot(rx - cx, ry - cy) for rx, ry in ring_pts])),
                )
                cv2.circle(frame_bgr, (cx, cy), radius, (0, 255, 190), 1, cv2.LINE_AA)
                cv2.circle(frame_bgr, (cx, cy), 2, (0, 255, 255), -1, cv2.LINE_AA)

                # Per-eyeball 3D optical ray vector
                ex_end = int(cx + (gaze.gaze_x - 0.50) * sphere_r * 2.8)
                ey_end = int(cy + (gaze.gaze_y - 0.50) * sphere_r * 2.8)
                cv2.line(frame_bgr, (cx, cy), (ex_end, ey_end), (255, 220, 60), 2, cv2.LINE_AA)

        lip_color = (220, 80, 255) if emotion.talking else (150, 160, 180)
        _draw_polyline(
            frame_bgr,
            landmarks,
            LIPS_OUTER_PTS,
            lip_color,
            thickness=2 if emotion.talking else 1,
        )

    bridge = landmarks[168]
    bx, by = int(bridge.x * w), int(bridge.y * h)
    dx = int((gaze.gaze_x - 0.50) * (w * 0.24))
    dy = int((gaze.gaze_y - 0.50) * (h * 0.28))
    tx = int(np.clip(bx + dx, 10, w - 10))
    ty = int(np.clip(by + dy, 10, h - 10))
    cv2.arrowedLine(
        frame_bgr,
        (bx, by),
        (tx, ty),
        (255, 220, 60),
        2,
        cv2.LINE_AA,
        tipLength=0.25,
    )


class PrototypeGUI:
    """Tkinter + OpenCV real-time multimodal face, 3D eyeball, body, and screen-gaze GUI."""

    VIDEO_W = 608
    VIDEO_H = 380

    def __init__(self, camera_index: int = 0, events_url: str = DEFAULT_EVENTS_URL) -> None:
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.title("WHACK Role 1 — Multimodal Face + 3D Eyeball + Body Prototype")
        self.root.configure(bg="#0f172a")
        self.root.minsize(1140, 670)

        self.screen_w = max(self.root.winfo_screenwidth(), 1280)
        self.screen_h = max(self.root.winfo_screenheight(), 720)

        loaded_profile = CalibrationProfile.load(DEFAULT_PROFILE_PATH)
        self.calibrator = BaselineCalibrator(profile=loaded_profile)
        self.gaze_tracker = GazeTracker(smoothing_alpha=0.35)
        self.body_tracker = BodyGestureTracker()
        self.emotion_detector = EmotionDetector(smoothing_alpha=0.30)
        self.event_client = AsyncEventClient(endpoint=events_url, heartbeat_sec=3.0)

        self.face_landmarker = create_face_landmarker(running_mode="VIDEO")
        self.pose_landmarker = create_pose_landmarker(running_mode="VIDEO")
        self.hand_landmarker = create_hand_landmarker(running_mode="VIDEO")

        self.camera_index = camera_index
        self.cap: Optional[cv2.VideoCapture] = None
        self.show_mesh: bool = True
        self.light_norm_enabled: bool = True
        self._running: bool = True
        self._t0: float = time.monotonic()
        self._last_ts_ms: int = 0
        self._frame_idx: int = 0
        self._cached_pose: Optional[Sequence] = None
        self._cached_hands: List[Sequence] = []
        self._fps_prev: float = time.monotonic()
        self._fps_smooth: float = 0.0
        self._photo_ref: Optional[ImageTk.PhotoImage] = None
        self._eye_photo_ref: Optional[ImageTk.PhotoImage] = None
        self._last_json_update: float = 0.0

        # Rolling buffer of recent open-eye 3D eyeball feature vectors for Look-&-Click calibration
        self._recent_gaze_vecs: Deque[List[float]] = deque(maxlen=10)

        # Interactive 9-point + Step 10 Head-Move VOR Lock calibration state
        self._gaze_cal_win: Optional[tk.Toplevel] = None
        self._gaze_cal_canvas: Optional[tk.Canvas] = None
        self._gaze_cal_mode: str = "collect"  # "collect" | "vor_lock" | "verify"
        self._gaze_cal_step: int = 0
        self._gaze_cal_x: List[List[float]] = []
        self._gaze_cal_y: List[tuple[float, float]] = []
        self._gaze_cal_hint: str = ""
        self._gaze_cal_fit_px_err: float = 0.0
        self._post_click_target: Optional[tuple[float, float]] = None
        self._post_click_remaining: int = 0
        self._vor_lock_started_at: float = 0.0
        self._vor_lock_frame_idx: int = 0
        self._vor_lock_samples: int = 0
        self._vor_ref_yaw: Optional[float] = None
        self._vor_prev_yaw: Optional[float] = None
        self._vor_prev_t: float = 0.0
        self._vor_left_cnt: int = 0
        self._vor_right_cnt: int = 0

        # Floating click-through desktop screen gaze dot (`O` hotkey)
        self.screen_dot_enabled: bool = False
        self._screen_dot_win: Optional[tk.Toplevel] = None
        self._screen_dot_canvas: Optional[tk.Canvas] = None
        self._screen_dot_ring: Optional[int] = None

        self._build_ui()
        self._bind_shortcuts()
        self._connect_camera(self.camera_index)

        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#1e293b", padx=14, pady=8)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="WHACK Role 1  •  3D Eyeball Tracking + Face & Body Multimodal Prototype",
            font=("Segoe UI", 12, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        ).pack(side=tk.LEFT)

        right_hdr = tk.Frame(header, bg="#1e293b")
        right_hdr.pack(side=tk.RIGHT)

        tk.Label(
            right_hdr, text="Camera:", font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b"
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.cam_var = tk.StringVar(value=str(self.camera_index))
        cam_combo = ttk.Combobox(
            right_hdr,
            textvariable=self.cam_var,
            values=["0", "1", "2"],
            width=3,
            state="readonly",
        )
        cam_combo.pack(side=tk.LEFT, padx=(0, 6))
        cam_combo.bind("<<ComboboxSelected>>", self._on_camera_selected)

        self.cal_badge = tk.Label(
            right_hdr,
            text="",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=3,
        )
        self.cal_badge.pack(side=tk.LEFT, padx=8)
        self._refresh_calibration_badge()

        self.fps_label = tk.Label(
            right_hdr,
            text="FPS: --",
            font=("Consolas", 10, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
        )
        self.fps_label.pack(side=tk.LEFT, padx=(8, 0))

        body = tk.Frame(self.root, bg="#0f172a", padx=12, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        # Left Column: Video + Zoomed Dual-Eyeball Strip + Controls
        left_col = tk.Frame(body, bg="#0f172a")
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        video_border = tk.Frame(left_col, bg="#334155", padx=2, pady=2)
        video_border.pack()

        self.video_label = tk.Label(
            video_border,
            bg="#020617",
            width=self.VIDEO_W,
            height=self.VIDEO_H,
        )
        self.video_label.pack()

        # Live Zoomed Dual-Eyeball Strip Card
        eye_strip_card = tk.Frame(left_col, bg="#1e293b", padx=10, pady=6)
        eye_strip_card.pack(fill=tk.X, pady=(8, 0))

        self.eye_strip_label = tk.Label(
            eye_strip_card,
            bg="#0e1420",
            width=228,
            height=66,
        )
        self.eye_strip_label.pack(side=tk.LEFT, padx=(0, 10))

        eye_info = tk.Frame(eye_strip_card, bg="#1e293b")
        eye_info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            eye_info,
            text="3D EYEBALL TRACKING & FINAL SCREEN COORD",
            font=("Segoe UI", 8, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
            anchor="w",
        ).pack(fill=tk.X)

        self.coord_big_lbl = tk.Label(
            eye_info,
            text="Screen: (---- px, ---- px)",
            font=("Consolas", 12, "bold"),
            fg="#38bdf8",
            bg="#1e293b",
            anchor="w",
        )
        self.coord_big_lbl.pack(fill=tk.X, pady=(2, 1))

        self.eyeball_angles_lbl = tk.Label(
            eye_info,
            text="Eyeball Pitch/Yaw: --° / --°  |  Ray: (--, --, --)",
            font=("Consolas", 8),
            fg="#cbd5e1",
            bg="#1e293b",
            anchor="w",
        )
        self.eyeball_angles_lbl.pack(fill=tk.X)

        # Controls Row 1
        ctrl1 = tk.Frame(left_col, bg="#0f172a", pady=7)
        ctrl1.pack(fill=tk.X)

        self._make_btn(
            ctrl1,
            "9-Pt Gaze Calib [G]",
            "#0284c7",
            self.start_9pt_gaze_calibration,
        ).pack(side=tk.LEFT, padx=(0, 5))

        self.dot_btn = self._make_btn(
            ctrl1,
            "Screen Dot: OFF [O]",
            "#0f766e",
            self.toggle_screen_dot,
        )
        self.dot_btn.pack(side=tk.LEFT, padx=(0, 5))

        self._make_btn(
            ctrl1,
            "Neutral Face [C]",
            "#2563eb",
            lambda: self.start_calibration(5.0),
        ).pack(side=tk.LEFT, padx=(0, 5))

        self._make_btn(
            ctrl1,
            "Reset [R]",
            "#475569",
            self.reset_calibration,
        ).pack(side=tk.LEFT, padx=(0, 5))

        self.mesh_btn = self._make_btn(
            ctrl1,
            "Mesh: ON [M]",
            "#334155",
            self.toggle_mesh,
        )
        self.mesh_btn.pack(side=tk.LEFT)

        # Controls Row 2
        ctrl2 = tk.Frame(left_col, bg="#0f172a")
        ctrl2.pack(fill=tk.X)

        self.norm_btn = self._make_btn(
            ctrl2,
            "CLAHE: ON [N]",
            "#4338ca",
            self.toggle_light_norm,
        )
        self.norm_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.post_btn = self._make_btn(
            ctrl2,
            "POST :3000: OFF [P]",
            "#334155",
            self.toggle_http_post,
        )
        self.post_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.http_status_lbl = tk.Label(
            ctrl2,
            text="POST :3000 OFF (press P to stream)",
            font=("Segoe UI", 8),
            fg="#94a3b8",
            bg="#0f172a",
        )
        self.http_status_lbl.pack(side=tk.LEFT)

        # Right Column: Fusion + Emotions + Body/Hands + Gaze + JSON
        right_col = tk.Frame(body, bg="#0f172a")
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.fusion_banner = tk.Label(
            right_col,
            text="WAITING FOR FACE...",
            font=("Segoe UI", 10, "bold"),
            bg="#1e293b",
            fg="#cbd5e1",
            anchor="w",
            padx=12,
            pady=6,
        )
        self.fusion_banner.pack(fill=tk.X, pady=(0, 7))

        # Panel 1: Multi-Emotion Bars
        emo_card = tk.Frame(right_col, bg="#1e293b", padx=12, pady=7)
        emo_card.pack(fill=tk.X, pady=(0, 7))

        emo_hdr = tk.Frame(emo_card, bg="#1e293b")
        emo_hdr.pack(fill=tk.X, pady=(0, 3))

        tk.Label(
            emo_hdr,
            text="MULTIMODAL EMOTION SCORES (FACE + BODY/HANDS)",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
        ).pack(side=tk.LEFT)

        self.primary_lbl = tk.Label(
            emo_hdr,
            text="Primary: --",
            font=("Segoe UI", 10, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        )
        self.primary_lbl.pack(side=tk.RIGHT)

        self.emo_bars: Dict[str, tuple[tk.Canvas, int, tk.Label]] = {}
        for key in EMOTION_KEYS:
            row = tk.Frame(emo_card, bg="#1e293b", pady=1)
            row.pack(fill=tk.X)

            tk.Label(
                row,
                text=EMOTION_LABELS[key],
                width=29,
                anchor="w",
                font=("Segoe UI", 9),
                fg="#e2e8f0",
                bg="#1e293b",
            ).pack(side=tk.LEFT)

            bar_canvas = tk.Canvas(
                row,
                height=12,
                bg="#0f172a",
                highlightthickness=0,
            )
            bar_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
            rect_id = bar_canvas.create_rectangle(
                0, 0, 0, 12, fill=EMOTION_COLORS[key], width=0
            )

            pct_lbl = tk.Label(
                row,
                text="  0%",
                width=5,
                anchor="e",
                font=("Consolas", 9, "bold"),
                fg="#cbd5e1",
                bg="#1e293b",
            )
            pct_lbl.pack(side=tk.RIGHT)
            self.emo_bars[key] = (bar_canvas, rect_id, pct_lbl)

        # Panel 2: Body Posture & Hand Gesture Cues
        body_card = tk.Frame(right_col, bg="#1e293b", padx=12, pady=7)
        body_card.pack(fill=tk.X, pady=(0, 7))

        tk.Label(
            body_card,
            text="UPPER-BODY POSTURE & HAND-TO-HEAD GESTURES",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 3))

        self.gesture_lbl = self._metric_row(body_card, "Active Cue:")
        self.posture_lbl = self._metric_row(body_card, "Posture:")
        self.occ_lbl = self._metric_row(body_card, "Hands/Guard:")

        # Panel 3: Screen Gaze Minimap + Metrics
        gaze_card = tk.Frame(right_col, bg="#1e293b", padx=12, pady=7)
        gaze_card.pack(fill=tk.X, pady=(0, 7))

        tk.Label(
            gaze_card,
            text=f"SCREEN GAZE MAP  ({self.screen_w}x{self.screen_h} MONITOR)",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 3))

        gaze_split = tk.Frame(gaze_card, bg="#1e293b")
        gaze_split.pack(fill=tk.X)

        self.MAP_W = 216
        self.MAP_H = 124
        self.gaze_canvas = tk.Canvas(
            gaze_split,
            width=self.MAP_W,
            height=self.MAP_H,
            bg="#090d16",
            highlightthickness=1,
            highlightbackground="#334155",
        )
        self.gaze_canvas.pack(side=tk.LEFT, padx=(0, 12))
        self._init_gaze_minimap()

        metrics_col = tk.Frame(gaze_split, bg="#1e293b")
        metrics_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.zone_lbl = self._metric_row(metrics_col, "Focus Zone:")
        self.fix_lbl = self._metric_row(metrics_col, "Fixation:")
        self.eyes_lbl = self._metric_row(metrics_col, "Eyes / Blink:")
        self.talk_lbl = self._metric_row(metrics_col, "Talking:")
        self.pose_lbl = self._metric_row(metrics_col, "Head P/Y/R:")

        # Panel 4: Live `/events` JSON Preview
        json_card = tk.Frame(right_col, bg="#1e293b", padx=12, pady=6)
        json_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            json_card,
            text="LIVE /events PAYLOAD PREVIEW",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#1e293b",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 2))

        self.json_text = tk.Text(
            json_card,
            height=6,
            bg="#090d16",
            fg="#93c5fd",
            font=("Consolas", 8),
            relief=tk.FLAT,
            padx=8,
            pady=4,
        )
        self.json_text.pack(fill=tk.BOTH, expand=True)
        self.json_text.configure(state=tk.DISABLED)

    @staticmethod
    def _make_btn(parent: tk.Widget, text: str, bg: str, cmd) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg="#f8fafc",
            activebackground="#3b82f6",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=4,
            cursor="hand2",
        )

    @staticmethod
    def _metric_row(parent: tk.Widget, title: str) -> tk.Label:
        row = tk.Frame(parent, bg="#1e293b", pady=1)
        row.pack(fill=tk.X)
        tk.Label(
            row,
            text=title,
            width=12,
            anchor="w",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#1e293b",
        ).pack(side=tk.LEFT)
        val = tk.Label(
            row,
            text="--",
            anchor="w",
            font=("Consolas", 9, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        )
        val.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return val

    def _init_gaze_minimap(self) -> None:
        c = self.gaze_canvas
        self._mon_x0, self._mon_y0 = 26, 15
        self._mon_x1, self._mon_y1 = self.MAP_W - 26, self.MAP_H - 15
        mon_h = self._mon_y1 - self._mon_y0
        self._split_y = int(self._mon_y0 + 0.66 * mon_h)

        c.create_text(
            self.MAP_W // 2,
            8,
            text="looking_away (off-screen)",
            fill="#64748b",
            font=("Segoe UI", 7),
        )
        self._rect_editor = c.create_rectangle(
            self._mon_x0,
            self._mon_y0,
            self._mon_x1,
            self._split_y,
            fill="#0f172a",
            outline="#475569",
            width=1,
        )
        self._rect_terminal = c.create_rectangle(
            self._mon_x0,
            self._split_y,
            self._mon_x1,
            self._mon_y1,
            fill="#111827",
            outline="#475569",
            width=1,
        )
        c.create_text(
            (self._mon_x0 + self._mon_x1) // 2,
            (self._mon_y0 + self._split_y) // 2,
            text="screen_editor",
            fill="#94a3b8",
            font=("Consolas", 8),
        )
        c.create_text(
            (self._mon_x0 + self._mon_x1) // 2,
            (self._split_y + self._mon_y1) // 2,
            text="screen_terminal",
            fill="#94a3b8",
            font=("Consolas", 8),
        )
        cx, cy = self.MAP_W // 2, (self._mon_y0 + self._split_y) // 2
        self._gaze_halo = c.create_oval(
            cx - 9, cy - 9, cx + 9, cy + 9, outline="#38bdf8", width=1
        )
        self._gaze_dot = c.create_oval(
            cx - 4, cy - 4, cx + 4, cy + 4, fill="#38bdf8", outline="#ffffff", width=1
        )

    def _bind_shortcuts(self) -> None:
        self.root.bind("<KeyPress-c>", lambda _: self.start_calibration(5.0))
        self.root.bind("<KeyPress-C>", lambda _: self.start_calibration(5.0))
        self.root.bind("<KeyPress-l>", lambda _: self.start_calibration(20.0))
        self.root.bind("<KeyPress-L>", lambda _: self.start_calibration(20.0))
        self.root.bind("<KeyPress-g>", lambda _: self.start_9pt_gaze_calibration())
        self.root.bind("<KeyPress-G>", lambda _: self.start_9pt_gaze_calibration())
        self.root.bind("<KeyPress-o>", lambda _: self.toggle_screen_dot())
        self.root.bind("<KeyPress-O>", lambda _: self.toggle_screen_dot())
        self.root.bind("<KeyPress-r>", lambda _: self.reset_calibration())
        self.root.bind("<KeyPress-R>", lambda _: self.reset_calibration())
        self.root.bind("<KeyPress-m>", lambda _: self.toggle_mesh())
        self.root.bind("<KeyPress-M>", lambda _: self.toggle_mesh())
        self.root.bind("<KeyPress-n>", lambda _: self.toggle_light_norm())
        self.root.bind("<KeyPress-N>", lambda _: self.toggle_light_norm())
        self.root.bind("<KeyPress-p>", lambda _: self.toggle_http_post())
        self.root.bind("<KeyPress-P>", lambda _: self.toggle_http_post())
        self.root.bind("<KeyPress-q>", lambda _: self.shutdown())
        self.root.bind("<KeyPress-Q>", lambda _: self.shutdown())
        self.root.bind("<Escape>", lambda _: self._on_escape())

    def _on_escape(self) -> None:
        if self._gaze_cal_win is not None:
            self._close_9pt_gaze_calibration()
        else:
            self.shutdown()

    def _connect_camera(self, idx: int) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.camera_index = idx
        self.cap = open_camera(idx, 1280, 720)

    def _on_camera_selected(self, _event=None) -> None:
        try:
            idx = int(self.cam_var.get())
        except ValueError:
            idx = 0
        self._connect_camera(idx)

    # ------------------------------------------------------------------
    # Floating Click-Through Desktop Screen Dot (`O`)
    # ------------------------------------------------------------------
    def toggle_screen_dot(self) -> None:
        self.screen_dot_enabled = not self.screen_dot_enabled
        if self.screen_dot_enabled:
            self._ensure_screen_dot_win()
            self.dot_btn.configure(text="Screen Dot: ON [O]", bg="#059669")
        else:
            self._destroy_screen_dot_win()
            self.dot_btn.configure(text="Screen Dot: OFF [O]", bg="#0f766e")

    def _ensure_screen_dot_win(self) -> None:
        if self._screen_dot_win is not None:
            return
        key_color = "#010203"
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", key_color)
        except Exception:
            win.attributes("-alpha", 0.75)
        win.configure(bg=key_color)
        win.geometry("46x46+600+400")

        cv = tk.Canvas(win, width=46, height=46, bg=key_color, highlightthickness=0)
        cv.pack()
        ring = cv.create_oval(4, 4, 42, 42, outline="#38bdf8", width=3)
        cv.create_oval(20, 20, 26, 26, fill="#ffffff", outline="#38bdf8", width=1)

        _make_window_clickthrough(win)
        self._screen_dot_win = win
        self._screen_dot_canvas = cv
        self._screen_dot_ring = ring

    def _destroy_screen_dot_win(self) -> None:
        if self._screen_dot_win is not None:
            try:
                self._screen_dot_win.destroy()
            except Exception:
                pass
            self._screen_dot_win = None
            self._screen_dot_canvas = None
            self._screen_dot_ring = None

    def _update_screen_dot_position(self, gaze: GazeResult) -> None:
        if not self.screen_dot_enabled or self._gaze_cal_win is not None:
            if self._screen_dot_win is not None and self._gaze_cal_win is not None:
                self._screen_dot_win.withdraw()
            return
        self._ensure_screen_dot_win()
        if self._screen_dot_win is None or self._screen_dot_canvas is None:
            return
        self._screen_dot_win.deiconify()
        x = int(np.clip(gaze.screen_x_px, 23, self.screen_w - 23)) - 23
        y = int(np.clip(gaze.screen_y_px, 23, self.screen_h - 23)) - 23
        self._screen_dot_win.geometry(f"46x46+{x}+{y}")
        col = "#f43f5e" if gaze.zone == "looking_away" else (
            "#34d399" if gaze.zone == "screen_terminal" else "#38bdf8"
        )
        if self._screen_dot_ring is not None:
            self._screen_dot_canvas.itemconfigure(self._screen_dot_ring, outline=col)

    # ------------------------------------------------------------------
    # Interactive 9-Point Look-&-Click Tilted-Screen Calibration (`G`)
    # ------------------------------------------------------------------
    def start_9pt_gaze_calibration(self) -> None:
        """Open full-screen 3x3 Look-&-Click calibration + live accuracy verification."""
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
        self._gaze_cal_hint = "LOOK directly at the cyan bullseye and CLICK its center (or press Space) — head can move naturally!"
        self._post_click_target = None
        self._post_click_remaining = 0
        self._vor_lock_started_at = 0.0
        self._vor_lock_frame_idx = 0
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
            self._post_click_target = None
            self._post_click_remaining = 0

    def _on_gaze_cal_space(self, _event=None) -> None:
        if self._gaze_cal_mode == "vor_lock":
            self._finalize_gaze_calibration()
            return
        if self._gaze_cal_mode != "collect" or self._gaze_cal_step >= len(GAZE_CAL_9PTS):
            return
        _, nx, ny = GAZE_CAL_9PTS[self._gaze_cal_step]
        self._record_gaze_cal_point(nx, ny)

    def _on_gaze_cal_click(self, event) -> None:
        if self._gaze_cal_canvas is None:
            return
        sw = max(self._gaze_cal_canvas.winfo_width(), self.screen_w)
        sh = max(self._gaze_cal_canvas.winfo_height(), self.screen_h)

        if self._gaze_cal_mode == "verify":
            # In verification mode, clicking bottom-center Finish closes overlay
            if abs(event.x - sw // 2) < 160 and abs(event.y - (sh - 58)) < 32:
                self._close_9pt_gaze_calibration()
            return

        if (
            self._gaze_cal_mode != "collect"
            or self._gaze_cal_step >= len(GAZE_CAL_9PTS)
            or self._post_click_remaining > 0
        ):
            return

        _, nx, ny = GAZE_CAL_9PTS[self._gaze_cal_step]
        tx, ty = nx * sw, ny * sh
        if np.hypot(event.x - tx, event.y - ty) > 75:
            self._gaze_cal_hint = "Click directly INSIDE the glowing cyan bullseye ring while staring at its center dot!"
            return

        # Record exact normalized pixel where user's fovea & cursor met
        self._record_gaze_cal_point(event.x / sw, event.y / sh)

    def _record_gaze_cal_point(self, norm_x: float, norm_y: float) -> None:
        if len(self._recent_gaze_vecs) < 4:
            self._gaze_cal_hint = "Eyes closed or face not detected — look clearly at the dot and click again"
            return

        # Capture the 6 pre-click fixated frames + next 4 post-click frames
        for fv in list(self._recent_gaze_vecs)[-6:]:
            self._gaze_cal_x.append(list(fv))
            self._gaze_cal_y.append((float(norm_x), float(norm_y)))

        self._post_click_target = (float(norm_x), float(norm_y))
        self._post_click_remaining = 4

    def _advance_or_solve_9pt(self) -> None:
        self._gaze_cal_step += 1
        if self._gaze_cal_step < len(GAZE_CAL_9PTS):
            self._gaze_cal_hint = "Great! Now look directly at the next bullseye and click it"
            return

        # Enter Step 10/10: Guided Left/Right Head-Turn VOR Lock on center bullseye (0.50, 0.50)
        self._gaze_cal_mode = "vor_lock"
        self._vor_lock_started_at = time.monotonic()
        self._vor_lock_frame_idx = 0
        self._vor_lock_samples = 0
        self._vor_ref_yaw = None
        self._vor_prev_yaw = None
        self._vor_prev_t = time.monotonic()
        self._vor_left_cnt = 0
        self._vor_right_cnt = 0

    def _finalize_gaze_calibration(self) -> None:
        model_dict = TiltedScreenRegressionModel.fit(
            self._gaze_cal_x, self._gaze_cal_y, ridge_lambda=0.85
        )
        if model_dict is None:
            self._gaze_cal_mode = "collect"
            self._gaze_cal_hint = "Not enough samples collected — press R to retry"
            return

        self.calibrator.profile.gaze_calibrated = True
        self.calibrator.profile.gaze_model = model_dict
        self.calibrator.profile.save(DEFAULT_PROFILE_PATH)
        self.gaze_tracker.reset_filters()
        self._refresh_calibration_badge()

        diag_px = math.hypot(self.screen_w, self.screen_h)
        self._gaze_cal_fit_px_err = model_dict.get("mean_error_norm", 0.0) * diag_px * 0.55
        self._gaze_cal_mode = "verify"

    def _render_9pt_calibration_overlay(self, gaze: Optional[GazeResult]) -> None:
        if self._gaze_cal_win is None or self._gaze_cal_canvas is None:
            return

        # Collect post-click lock frames before advancing dot
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
                self._advance_or_solve_9pt()

        # Step 10/10: Collect crisp Left/Right moving-head VOR lock frames on center (0.50, 0.50)
        vor_elapsed = 0.0
        if self._gaze_cal_mode == "vor_lock":
            now_vor = time.monotonic()
            vor_elapsed = now_vor - self._vor_lock_started_at
            if (
                gaze is not None
                and not gaze.eyes_closed
                and gaze.eye_blink < 0.35
                and gaze.gaze_feature_vec
            ):
                if self._vor_ref_yaw is None:
                    self._vor_ref_yaw = gaze.head_yaw

                dt_vor = max(now_vor - self._vor_prev_t, 1e-3)
                yaw_vel = (
                    abs(gaze.head_yaw - self._vor_prev_yaw) / dt_vor
                    if self._vor_prev_yaw is not None
                    else 0.0
                )
                self._vor_prev_yaw = gaze.head_yaw
                self._vor_prev_t = now_vor

                # Skip motion-blurred whip frames (> 85 deg/s); keep crisp left/center/right turns
                if (
                    vor_elapsed >= VOR_LOCK_SETTLE_SEC
                    and yaw_vel < 85.0
                    and self._vor_lock_samples < 130
                ):
                    self._gaze_cal_x.append(list(gaze.gaze_feature_vec))
                    self._gaze_cal_y.append((0.50, 0.50))
                    self._vor_lock_samples += 1

                    rel_yaw = gaze.head_yaw - self._vor_ref_yaw
                    if rel_yaw <= -7.0:
                        self._vor_left_cnt += 1
                    elif rel_yaw >= 7.0:
                        self._vor_right_cnt += 1

            if vor_elapsed >= VOR_LOCK_DURATION_SEC:
                self._finalize_gaze_calibration()

        cv = self._gaze_cal_canvas
        sw = max(cv.winfo_width(), self.screen_w)
        sh = max(cv.winfo_height(), self.screen_h)
        cv.delete("all")

        # Draw editor / terminal zone reference line (66% screen height)
        split_y = int(0.66 * sh)
        cv.create_line(0, split_y, sw, split_y, fill="#1e293b", dash=(6, 6), width=2)
        cv.create_text(
            84, split_y - 14, text="screen_editor", fill="#334155", font=("Consolas", 10)
        )
        cv.create_text(
            92, split_y + 14, text="screen_terminal", fill="#334155", font=("Consolas", 10)
        )

        # Draw all 9 grid anchors
        for idx, (lbl, pnx, pny) in enumerate(GAZE_CAL_9PTS):
            px, py = int(pnx * sw), int(pny * sh)
            if self._gaze_cal_mode in ("vor_lock", "verify") or idx < self._gaze_cal_step:
                if not (self._gaze_cal_mode == "vor_lock" and idx == 4):
                    cv.create_oval(px - 12, py - 12, px + 12, py + 12, outline="#10b981", width=2)
                    cv.create_text(px, py, text="✓", fill="#10b981", font=("Segoe UI", 9, "bold"))
            elif idx == self._gaze_cal_step and self._gaze_cal_mode == "collect":
                pulse = 28 + int(5 * math.sin(time.monotonic() * 7.0))
                cv.create_oval(
                    px - pulse, py - pulse, px + pulse, py + pulse, outline="#38bdf8", width=2
                )
                cv.create_oval(
                    px - 16, py - 16, px + 16, py + 16, fill="#0c2d48", outline="#7dd3fc", width=2
                )
                cv.create_oval(px - 4, py - 4, px + 4, py + 4, fill="#ffffff", outline="#ffffff")
                label_y = py + 42 if pny < 0.80 else py - 42
                cv.create_text(
                    px, label_y, text=lbl, fill="#7dd3fc", font=("Consolas", 11, "bold")
                )
            else:
                cv.create_oval(px - 10, py - 10, px + 10, py + 10, outline="#334155", width=1)

        if self._gaze_cal_mode == "collect":
            cv.create_text(
                sw // 2,
                38,
                text=f"HEAD-INVARIANT 3D EYEBALL CALIBRATION  (Step {self._gaze_cal_step + 1}/10)",
                fill="#f8fafc",
                font=("Segoe UI", 14, "bold"),
            )
            cv.create_text(
                sw // 2,
                64,
                text=self._gaze_cal_hint + "   [Esc: Cancel]",
                fill="#93c5fd",
                font=("Segoe UI", 11),
            )
        elif self._gaze_cal_mode == "vor_lock":
            rem = max(0.0, VOR_LOCK_DURATION_SEC - vor_elapsed)
            prog = float(np.clip(vor_elapsed / VOR_LOCK_DURATION_SEC, 0.0, 1.0))
            cx, cy = int(0.50 * sw), int(0.50 * sh)
            pulse = 34 + int(6 * math.sin(time.monotonic() * 8.0))

            cv.create_oval(
                cx - pulse, cy - pulse, cx + pulse, cy + pulse, outline="#f59e0b", width=3
            )
            cv.create_oval(
                cx - 18, cy - 18, cx + 18, cy + 18, fill="#451a03", outline="#fde68a", width=2
            )
            cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#ffffff", outline="#ffffff")

            left_done = self._vor_left_cnt >= 5
            right_done = self._vor_right_cnt >= 5
            phase_prompt = (
                "← Slowly turn HEAD LEFT (keep EYES on gold dot!)"
                if not left_done
                else (
                    "Now slowly turn HEAD RIGHT → (keep EYES on gold dot!)"
                    if not right_done
                    else "✓ Left & Right Head Turns Captured! Gently nod up/down..."
                )
            )
            cv.create_text(
                cx,
                cy + 50,
                text=phase_prompt,
                fill="#4ade80" if (left_done and right_done) else "#fcd34d",
                font=("Segoe UI", 12, "bold"),
            )

            l_txt = "✓ LEFT LOCKED" if left_done else "← Turn Head Left"
            r_txt = "✓ RIGHT LOCKED" if right_done else "Turn Head Right →"
            cv.create_text(
                cx - 115,
                cy + 78,
                text=f"[ {l_txt} ]",
                fill="#4ade80" if left_done else "#94a3b8",
                font=("Consolas", 10, "bold"),
            )
            cv.create_text(
                cx + 115,
                cy + 78,
                text=f"[ {r_txt} ]",
                fill="#4ade80" if right_done else "#94a3b8",
                font=("Consolas", 10, "bold"),
            )

            bar_w = 360
            bx0, by0 = cx - bar_w // 2, cy + 98
            cv.create_rectangle(bx0, by0, bx0 + bar_w, by0 + 10, fill="#1e293b", outline="#475569")
            cv.create_rectangle(
                bx0, by0, bx0 + int(bar_w * prog), by0 + 10, fill="#f59e0b", width=0
            )

            cv.create_text(
                sw // 2,
                38,
                text=f"STEP 10/10: LEFT & RIGHT HEAD-TURN VOR LOCK  ({rem:.1f}s)",
                fill="#fbbf24",
                font=("Segoe UI", 14, "bold"),
            )
            cv.create_text(
                sw // 2,
                66,
                text="Keep your EYES glued to the gold dot while smoothly turning your HEAD left (~20°), then right (~20°)!   [Space: Finish Early]",
                fill="#fde68a",
                font=("Segoe UI", 11),
            )
        else:
            tilt_deg = self.calibrator.profile.gaze_model.get("tilt_deg", 0.0)
            yaw_span = self.calibrator.profile.gaze_model.get("yaw_range_deg", 0.0)
            cv.create_text(
                sw // 2,
                38,
                text=(
                    f"3D HEAD-TURN GAZE SAVED!  (Residual ~{self._gaze_cal_fit_px_err:.0f} px"
                    f"  |  Yaw Span {yaw_span:.0f}°  |  Tilt {tilt_deg:+.1f}°)  —  Turn head left/right to test!"
                ),
                fill="#4ade80",
                font=("Segoe UI", 13, "bold"),
            )
            cv.create_text(
                sw // 2,
                66,
                text="Press [Enter] or [Esc] to finish  |  Press [R] to recalibrate",
                fill="#cbd5e1",
                font=("Segoe UI", 11),
            )
            bx0, by0, bx1, by1 = sw // 2 - 130, sh - 82, sw // 2 + 130, sh - 36
            cv.create_rectangle(bx0, by0, bx1, by1, fill="#059669", outline="#6ee7b7", width=2)
            cv.create_text(
                sw // 2,
                (by0 + by1) // 2,
                text="Save & Return to GUI [Enter]",
                fill="#ffffff",
                font=("Segoe UI", 11, "bold"),
            )

            if gaze is not None:
                gx_px, gy_px = gaze.screen_x_px, gaze.screen_y_px
                ox, oy, oz = gaze.eye_origin_cm
                cv.create_oval(
                    gx_px - 22, gy_px - 22, gx_px + 22, gy_px + 22, outline="#38bdf8", width=3
                )
                cv.create_oval(
                    gx_px - 5, gy_px - 5, gx_px + 5, gy_px + 5, fill="#ffffff", outline="#38bdf8"
                )
                cv.create_text(
                    gx_px + 86,
                    gy_px - 18,
                    text=f"({gx_px} px, {gy_px} px)\n{gaze.zone} | {abs(oz):.0f}cm",
                    fill="#7dd3fc",
                    font=("Consolas", 10, "bold"),
                )

    def start_calibration(self, duration_sec: float = 5.0) -> None:
        self.calibrator.start(duration_sec)
        self._refresh_calibration_badge()

    def reset_calibration(self) -> None:
        self.calibrator.reset_to_defaults()
        self.gaze_tracker.reset_filters()
        if DEFAULT_PROFILE_PATH.exists():
            try:
                DEFAULT_PROFILE_PATH.unlink()
            except OSError:
                pass
        self._refresh_calibration_badge()

    def toggle_mesh(self) -> None:
        self.show_mesh = not self.show_mesh
        self.mesh_btn.configure(text=f"Mesh: {'ON' if self.show_mesh else 'OFF'} [M]")

    def toggle_light_norm(self) -> None:
        self.light_norm_enabled = not self.light_norm_enabled
        self.norm_btn.configure(
            text=f"CLAHE: {'ON' if self.light_norm_enabled else 'OFF'} [N]",
            bg="#4338ca" if self.light_norm_enabled else "#334155",
        )

    def toggle_http_post(self) -> None:
        self.event_client.enabled = not self.event_client.enabled
        on = self.event_client.enabled
        self.post_btn.configure(
            text=f"POST :3000: {'ON' if on else 'OFF'} [P]",
            bg="#059669" if on else "#334155",
        )
        if not on:
            self.http_status_lbl.configure(text="POST :3000 OFF (press P to stream)")

    def _refresh_calibration_badge(self) -> None:
        p = self.calibrator.profile
        if self.calibrator.is_active:
            self.cal_badge.configure(text="CALIBRATING...", bg="#b45309", fg="#fef3c7")
        elif p.calibrated and p.gaze_calibrated:
            self.cal_badge.configure(
                text="Calibrated (Face + 3D Head-Free Gaze)", bg="#065f46", fg="#a7f3d0"
            )
        elif p.gaze_calibrated:
            self.cal_badge.configure(
                text="3D Head-Free Gaze Calibrated", bg="#0369a1", fg="#e0f2fe"
            )
        elif p.calibrated:
            self.cal_badge.configure(
                text=f"Face Calibrated ({p.sample_count}f) — Press G for Gaze",
                bg="#065f46",
                fg="#a7f3d0",
            )
        else:
            self.cal_badge.configure(
                text="Uncalibrated (Press G & C)", bg="#334155", fg="#cbd5e1"
            )

    def _next_timestamp_ms(self) -> int:
        ts = int((time.monotonic() - self._t0) * 1000)
        if ts <= self._last_ts_ms:
            ts = self._last_ts_ms + 1
        self._last_ts_ms = ts
        return ts

    def _render_no_camera_frame(self) -> np.ndarray:
        canvas = np.zeros((self.VIDEO_H, self.VIDEO_W, 3), dtype=np.uint8)
        canvas[:] = (22, 27, 38)
        cv2.putText(
            canvas,
            f"Camera {self.camera_index} unavailable or busy",
            (110, 175),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (100, 180, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            "Close other camera apps or switch Camera dropdown (0/1/2)",
            (65, 215),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (180, 190, 205),
            1,
            cv2.LINE_AA,
        )
        return canvas

    def _draw_video_hud(
        self,
        frame_bgr: np.ndarray,
        emotion: Optional[EmotionResult],
        gaze: Optional[GazeResult],
        body: Optional[BodyGestureResult],
    ) -> None:
        w = frame_bgr.shape[1]

        if self.calibrator.is_active:
            ratio, rem = self.calibrator.progress()
            overlay = frame_bgr.copy()
            cv2.rectangle(overlay, (0, 0), (w, 66), (15, 23, 42), -1)
            cv2.addWeighted(overlay, 0.82, frame_bgr, 0.18, 0, frame_bgr)
            cv2.putText(
                frame_bgr,
                f"CALIBRATING NEUTRAL FACE & POSTURE — Relax face ({rem:.1f}s)",
                (14, 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (120, 230, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.rectangle(frame_bgr, (16, 40), (w - 16, 52), (50, 65, 85), -1)
            fill_w = int((w - 32) * ratio)
            cv2.rectangle(frame_bgr, (16, 40), (16 + fill_w, 52), (56, 189, 248), -1)
            return

        if emotion is None or gaze is None:
            cv2.putText(
                frame_bgr,
                "No face detected — center face in lighting",
                (18, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
                (90, 165, 255),
                2,
                cv2.LINE_AA,
            )
            return

        overlay = frame_bgr.copy()
        cv2.rectangle(overlay, (10, 10), (w - 10, 44), (15, 23, 42), -1)
        cv2.addWeighted(overlay, 0.74, frame_bgr, 0.26, 0, frame_bgr)
        pill_txt = (
            f"{emotion.primary_emotion.upper()} ({int(emotion.primary_score * 100)}%) | "
            f"Look: ({gaze.screen_x_px}, {gaze.screen_y_px}) [{gaze.zone}]"
        )
        if body and body.primary_gesture != "none":
            pill_txt += f" | {body.primary_gesture}"
        cv2.putText(
            frame_bgr,
            pill_txt,
            (18, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (240, 250, 255),
            1,
            cv2.LINE_AA,
        )

    def _update_dashboard(
        self,
        emotion: EmotionResult,
        gaze: GazeResult,
        body: BodyGestureResult,
        payload: Dict,
    ) -> None:
        bg_hex, fg_hex = FUSION_STYLE.get(emotion.fusion_level, ("#1e293b", "#cbd5e1"))
        self.fusion_banner.configure(text=emotion.fusion_status, bg=bg_hex, fg=fg_hex)

        # Dual-eyeball zoomed crop + final pixel coordinate headline
        if gaze.eye_strip_bgr is not None:
            strip_rgb = cv2.cvtColor(gaze.eye_strip_bgr, cv2.COLOR_BGR2RGB)
            self._eye_photo_ref = ImageTk.PhotoImage(image=Image.fromarray(strip_rgb))
            self.eye_strip_label.configure(image=self._eye_photo_ref)

        self.coord_big_lbl.configure(
            text=f"Screen: ({gaze.screen_x_px:4d} px, {gaze.screen_y_px:4d} px)  [{gaze.gaze_x:.2f}, {gaze.gaze_y:.2f}]",
            fg="#f43f5e" if gaze.zone == "looking_away" else "#38bdf8",
        )
        rx, ry, rz = gaze.gaze_ray
        _, _, oz_cm = gaze.eye_origin_cm
        self.eyeball_angles_lbl.configure(
            text=(
                f"Eyeball P/Y: {gaze.eyeball_pitch:+4.1f}°/{gaze.eyeball_yaw:+4.1f}° | "
                f"Dist: {abs(oz_cm):.0f}cm | Ray: ({rx:+.2f},{ry:+.2f},{rz:+.2f})"
            )
        )

        # Emotion bars
        self.primary_lbl.configure(
            text=f"Primary: {emotion.primary_emotion.upper()} ({int(emotion.primary_score * 100)}%)",
            fg=EMOTION_COLORS.get(emotion.primary_emotion, "#f8fafc"),
        )
        for key in EMOTION_KEYS:
            score = emotion.emotions.get(key, 0.0)
            bar_canvas, rect_id, pct_lbl = self.emo_bars[key]
            cw = max(bar_canvas.winfo_width(), 140)
            fill_px = int(cw * np.clip(score, 0.0, 1.0))
            bar_canvas.coords(rect_id, 0, 0, fill_px, 12)
            pct_lbl.configure(
                text=f"{int(round(score * 100)):3d}%",
                fg="#ffffff" if key == emotion.primary_emotion else "#94a3b8",
            )

        # Body & Hand Gesture panel
        g_txt, g_col = GESTURE_DESCRIPTIONS.get(
            body.primary_gesture, (body.primary_gesture, "#fbbf24")
        )
        if len(body.active_gestures) > 1:
            extras = ", ".join(g for g in body.active_gestures if g != body.primary_gesture)
            g_txt = f"{g_txt}  [+{extras}]"
        self.gesture_lbl.configure(text=g_txt, fg=g_col)

        posture_col = "#fbbf24" if body.posture != "upright" else "#e2e8f0"
        self.posture_lbl.configure(
            text=f"{body.posture}  (scale {body.lean_ratio:.2f}x)",
            fg=posture_col,
        )
        occ_parts = [f"{body.hands_detected} hand(s)"]
        if body.mouth_occluded:
            occ_parts.append("Mouth Guard: ON")
        if body.eyes_occluded:
            occ_parts.append("Eye Guard: ON")
        self.occ_lbl.configure(
            text="  |  ".join(occ_parts),
            fg="#38bdf8" if (body.mouth_occluded or body.eyes_occluded) else "#94a3b8",
        )

        # Gaze minimap
        ed_fill = "#0c2d48" if gaze.zone == "screen_editor" else "#0f172a"
        ed_out = "#38bdf8" if gaze.zone == "screen_editor" else "#475569"
        tm_fill = "#0c2d48" if gaze.zone == "screen_terminal" else "#111827"
        tm_out = "#38bdf8" if gaze.zone == "screen_terminal" else "#475569"
        self.gaze_canvas.itemconfigure(self._rect_editor, fill=ed_fill, outline=ed_out)
        self.gaze_canvas.itemconfigure(self._rect_terminal, fill=tm_fill, outline=tm_out)

        mon_w = self._mon_x1 - self._mon_x0
        mon_h = self._mon_y1 - self._mon_y0
        px = int(np.clip(self._mon_x0 + gaze.gaze_x * mon_w, 8, self.MAP_W - 8))
        py = int(np.clip(self._mon_y0 + gaze.gaze_y * mon_h, 8, self.MAP_H - 8))
        dot_color = (
            "#f43f5e"
            if gaze.zone == "looking_away"
            else ("#a855f7" if gaze.eyes_closed else "#38bdf8")
        )
        self.gaze_canvas.coords(self._gaze_halo, px - 9, py - 9, px + 9, py + 9)
        self.gaze_canvas.coords(self._gaze_dot, px - 4, py - 4, px + 4, py + 4)
        self.gaze_canvas.itemconfigure(self._gaze_halo, outline=dot_color)
        self.gaze_canvas.itemconfigure(self._gaze_dot, fill=dot_color)

        zone_colors = {
            "screen_editor": "#38bdf8",
            "screen_terminal": "#34d399",
            "looking_away": "#fb7185",
            "eyes_closed": "#c084fc",
        }
        self.zone_lbl.configure(
            text=f"{gaze.zone}  ({gaze.screen_x_px}, {gaze.screen_y_px})",
            fg=zone_colors.get(gaze.zone, "#f8fafc"),
        )
        self.fix_lbl.configure(text=f"{gaze.fixation_ms / 1000.0:.1f}s ({gaze.fixation_ms} ms)")
        eye_state = "CLOSED" if gaze.eyes_closed else "OPEN"
        self.eyes_lbl.configure(
            text=f"{eye_state} (EAR {gaze.ear:.2f} | {gaze.blinks_per_min:.0f}/m)",
            fg="#c084fc" if gaze.eyes_closed else "#e2e8f0",
        )
        self.talk_lbl.configure(
            text="YES (Nudges muted)" if emotion.talking else "No",
            fg="#f472b6" if emotion.talking else "#94a3b8",
        )
        self.pose_lbl.configure(
            text=f"{gaze.head_pitch:+.0f}° / {gaze.head_yaw:+.0f}° / {gaze.head_roll:+.0f}°"
        )

        self._update_screen_dot_position(gaze)

        if self.event_client.enabled:
            self.http_status_lbl.configure(text=f"Events POST: {self.event_client.last_status}")

        now = time.monotonic()
        if (now - self._last_json_update) >= 0.22:
            self._last_json_update = now
            formatted = json.dumps(payload, indent=2)
            self.json_text.configure(state=tk.NORMAL)
            self.json_text.delete("1.0", tk.END)
            self.json_text.insert(tk.END, formatted)
            self.json_text.configure(state=tk.DISABLED)

    @staticmethod
    def _fit_aspect_preview(frame_bgr: np.ndarray, dst_w: int, dst_h: int) -> np.ndarray:
        """Resize high-res camera frame into GUI preview box without aspect distortion."""
        sh, sw = frame_bgr.shape[:2]
        scale = min(dst_w / max(sw, 1), dst_h / max(sh, 1))
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        if nw == dst_w and nh == dst_h:
            return resized
        canvas = np.zeros((dst_h, dst_w, 3), dtype=np.uint8)
        y0 = (dst_h - nh) // 2
        x0 = (dst_w - nw) // 2
        canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
        return canvas

    def _tick(self) -> None:
        if not self._running:
            return

        now = time.monotonic()
        dt = max(now - self._fps_prev, 1e-4)
        self._fps_prev = now
        inst_fps = 1.0 / dt
        self._fps_smooth = (
            inst_fps if self._fps_smooth == 0.0 else 0.88 * self._fps_smooth + 0.12 * inst_fps
        )
        self.fps_label.configure(text=f"FPS: {self._fps_smooth:4.1f}")

        raw_mirrored: Optional[np.ndarray] = None
        if self.cap is not None and self.cap.isOpened():
            ok, raw = self.cap.read()
            if ok and raw is not None and raw.size > 0:
                # Cap max width to 960px so MediaPipe iris gets ~2x eye detail while keeping 30 FPS
                if raw.shape[1] > 960:
                    new_h = int(raw.shape[0] * (960.0 / raw.shape[1]))
                    raw = cv2.resize(raw, (960, new_h), interpolation=cv2.INTER_AREA)
                raw_mirrored = cv2.flip(raw, 1)

        if raw_mirrored is None:
            display_bgr = self._render_no_camera_frame()
        else:
            proc_bgr = (
                normalize_lighting_bgr(raw_mirrored) if self.light_norm_enabled else raw_mirrored
            )
            mp_img = bgr_to_mp_image(proc_bgr)
            ts_ms = self._next_timestamp_ms()

            face_res = self.face_landmarker.detect_for_video(mp_img, ts_ms)

            # Downscale slightly for fast Pose/Hand skeleton tracking
            if (self._frame_idx % 2) == 0:
                hand_res = self.hand_landmarker.detect_for_video(mp_img, ts_ms)
                self._cached_hands = (
                    list(hand_res.hand_landmarks) if hand_res.hand_landmarks else []
                )
            else:
                pose_res = self.pose_landmarker.detect_for_video(mp_img, ts_ms)
                self._cached_pose = (
                    pose_res.pose_landmarks[0] if pose_res.pose_landmarks else None
                )
            self._frame_idx += 1

            emotion_res: Optional[EmotionResult] = None
            gaze_res: Optional[GazeResult] = None
            body_res: Optional[BodyGestureResult] = None

            if face_res.face_landmarks:
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
                    landmarks,
                    self._cached_pose,
                    self._cached_hands,
                    hp,
                    hy,
                    hr,
                    self.calibrator.profile,
                )
                # Extract clean zoomed eyeball crops BEFORE drawing mesh overlays onto proc_bgr
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

                if (
                    not gaze_res.eyes_closed
                    and not body_res.eyes_occluded
                    and gaze_res.gaze_feature_vec
                ):
                    self._recent_gaze_vecs.append(gaze_res.gaze_feature_vec)

                features = extract_feature_dict(landmarks, blendshapes, gaze_res, body_res)

                if self.calibrator.is_active:
                    if not body_res.mouth_occluded and not body_res.eyes_occluded:
                        completed = self.calibrator.add_sample(features)
                        if completed:
                            self.calibrator.profile.save(DEFAULT_PROFILE_PATH)
                            self._refresh_calibration_badge()

                emotion_res = self.emotion_detector.update(
                    features, gaze_res, self.calibrator.profile, body_res
                )

                draw_face_overlays(
                    proc_bgr,
                    landmarks,
                    gaze_res,
                    emotion_res,
                    show_mesh=self.show_mesh,
                    pose_landmarks=self._cached_pose,
                    hand_landmarks_list=self._cached_hands,
                    body=body_res,
                )
                payload = build_event_payload(emotion_res, gaze_res, body_res)
                self.event_client.maybe_emit(payload)
                self._update_dashboard(emotion_res, gaze_res, body_res, payload)
            else:
                if self.show_mesh:
                    draw_body_and_hands_overlays(
                        proc_bgr, self._cached_pose, self._cached_hands, None
                    )
                self.fusion_banner.configure(
                    text="NO FACE DETECTED — Waiting for user in frame",
                    bg="#1e293b",
                    fg="#94a3b8",
                )

            if self._gaze_cal_win is not None:
                self._render_9pt_calibration_overlay(gaze_res)

            preview_bgr = self._fit_aspect_preview(proc_bgr, self.VIDEO_W, self.VIDEO_H)
            self._draw_video_hud(preview_bgr, emotion_res, gaze_res, body_res)
            display_bgr = preview_bgr

        rgb = cv2.cvtColor(display_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo_ref = ImageTk.PhotoImage(image=img)
        self.video_label.configure(image=self._photo_ref)

        self.root.after(15, self._tick)

    def run(self) -> None:
        self.root.after(10, self._tick)
        self.root.mainloop()

    def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        self._close_9pt_gaze_calibration()
        self._destroy_screen_dot_win()
        self.event_client.close()
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
    parser = argparse.ArgumentParser(
        description="WHACK Role 1 Multimodal Face + 3D Eyeball + Body Prototype GUI"
    )
    parser.add_argument("--camera", type=int, default=0, help="Webcam device index (default: 0)")
    parser.add_argument("--url", default=DEFAULT_EVENTS_URL, help="Core /events endpoint")
    args = parser.parse_args()

    app = PrototypeGUI(camera_index=args.camera, events_url=args.url)
    app.run()


if __name__ == "__main__":
    main()
