"""3D eyeball, iris, head-pose, blink-rate, and tilted-screen coordinate tracker."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence

import numpy as np

from calibration import CalibrationProfile
from eyeball_tracker import (
    EyeballTracker,
    OneEuroFilter,
    TiltedScreenRegressionModel,
    compute_continuous_head_pose,
)

# MediaPipe 478 Face Mesh landmark indices
EYE_A_CORNERS = (33, 133)
EYE_A_EAR_PTS = (33, 160, 158, 133, 153, 144)
EYE_A_IRIS_CENTER = 468
EYE_A_IRIS_RING = (469, 470, 471, 472)

EYE_B_CORNERS = (362, 263)
EYE_B_EAR_PTS = (362, 385, 387, 263, 373, 380)
EYE_B_IRIS_CENTER = 473
EYE_B_IRIS_RING = (474, 475, 476, 477)


@dataclass
class GazeResult:
    """Per-frame 3D eyeball, blink-rate, and final screen pixel coordinate telemetry."""

    zone: str
    gaze_x: float
    gaze_y: float
    screen_x_px: int
    screen_y_px: int
    eyeball_pitch: float
    eyeball_yaw: float
    gaze_ray: tuple[float, float, float]
    ray_u: float
    ray_v: float
    iris_ratio_x: float
    iris_ratio_y: float
    iris_offset_y: float
    ear: float
    eye_blink: float
    eyes_closed: bool
    closure_duration_sec: float
    blinks_per_min: float
    fixation_ms: int
    head_pitch: float
    head_yaw: float
    head_roll: float
    confidence: float
    eye_origin_cm: tuple[float, float, float] = (0.0, 0.0, -50.0)
    hit_x_cm: float = 0.0
    hit_y_cm: float = 11.0
    gaze_feature_vec: List[float] = field(default_factory=list)
    eye_strip_bgr: Optional[np.ndarray] = field(default=None, repr=False)


def _dist2d(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _compute_ear(landmarks: Sequence, indices: tuple[int, ...]) -> float:
    p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in indices)
    denom = 2.0 * _dist2d(p1, p4)
    if denom < 1e-6:
        return 0.27
    return (_dist2d(p2, p6) + _dist2d(p3, p5)) / denom


def _compute_eye_iris_metrics(
    landmarks: Sequence,
    corners: tuple[int, int],
    ear_pts: tuple[int, ...],
    iris_idx: int,
) -> tuple[float, float, float]:
    """Return `(iris_ratio_x, iris_ratio_y, canthus_offset_y)` on mirrored frame."""
    c1, c2 = landmarks[corners[0]], landmarks[corners[1]]
    iris = landmarks[iris_idx]

    p_left, p_right = (c1, c2) if c1.x <= c2.x else (c2, c1)
    dx = p_right.x - p_left.x
    dy = p_right.y - p_left.y
    eye_w = max(math.hypot(dx, dy), 1e-5)

    ux, uy = dx / eye_w, dy / eye_w
    vx, vy = -uy, ux

    rx_proj = (iris.x - p_left.x) * ux + (iris.y - p_left.y) * uy
    ratio_x = float(np.clip(rx_proj / eye_w, 0.0, 1.0))

    canthus_cx = 0.5 * (p_left.x + p_right.x)
    canthus_cy = 0.5 * (p_left.y + p_right.y)
    offset_y = float(((iris.x - canthus_cx) * vx + (iris.y - canthus_cy) * vy) / eye_w)

    _, u1, u2, _, l1, l2 = (landmarks[i] for i in ear_pts)
    y_top = 0.5 * (u1.y + u2.y)
    y_bot = 0.5 * (l1.y + l2.y)
    lid_h = max(y_bot - y_top, 1e-4)
    ratio_y = float(np.clip((iris.y - y_top) / lid_h, 0.0, 1.0))

    return ratio_x, ratio_y, offset_y


def extract_head_pose_degrees(
    landmarks: Sequence,
    transformation_matrix: Optional[np.ndarray] = None,
) -> tuple[float, float, float]:
    """Compute continuous signed `(pitch, yaw, roll)` in degrees (no sign-bounce bug)."""
    pitch, yaw, roll, _ = compute_continuous_head_pose(landmarks, transformation_matrix)
    return pitch, yaw, roll


class GazeTracker:
    """Stateful 3D eyeball + tilted-screen coordinate tracker with 1-Euro smoothing."""

    def __init__(self, smoothing_alpha: float = 0.35) -> None:
        self.alpha = float(smoothing_alpha)
        self._eyeball = EyeballTracker()
        self._filter_x = OneEuroFilter(min_cutoff=1.15, beta=3.8)
        self._filter_y = OneEuroFilter(min_cutoff=1.05, beta=4.0)
        self._smooth_gx: float = 0.50
        self._smooth_gy: float = 0.45
        self._current_zone: str = "screen_editor"
        self._zone_entered_at: float = time.monotonic()
        self._closed_since: Optional[float] = None
        self._started_at: float = time.monotonic()
        self._blink_timestamps: Deque[float] = deque(maxlen=120)

    def reset_filters(self) -> None:
        self._filter_x.reset()
        self._filter_y.reset()

    def update(
        self,
        landmarks: Sequence,
        blendshapes: Dict[str, float],
        profile: CalibrationProfile,
        transformation_matrix: Optional[np.ndarray] = None,
        eyes_occluded: bool = False,
        frame_bgr: Optional[np.ndarray] = None,
        render_eye_strip: bool = False,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> GazeResult:
        now = time.monotonic()

        ear_a = _compute_ear(landmarks, EYE_A_EAR_PTS)
        ear_b = _compute_ear(landmarks, EYE_B_EAR_PTS)
        ear = 0.5 * (ear_a + ear_b)

        has_iris = len(landmarks) >= 478
        if has_iris:
            rx_a, ry_a, oy_a = _compute_eye_iris_metrics(
                landmarks, EYE_A_CORNERS, EYE_A_EAR_PTS, EYE_A_IRIS_CENTER
            )
            rx_b, ry_b, oy_b = _compute_eye_iris_metrics(
                landmarks, EYE_B_CORNERS, EYE_B_EAR_PTS, EYE_B_IRIS_CENTER
            )
            iris_ratio_x = 0.5 * (rx_a + rx_b)
            iris_ratio_y = 0.5 * (ry_a + ry_b)
            iris_offset_y = 0.5 * (oy_a + oy_b)
        else:
            iris_ratio_x = 0.50
            iris_ratio_y = 0.50
            iris_offset_y = 0.01

        eye_blink = 0.5 * (
            blendshapes.get("eyeBlinkLeft", 0.0) + blendshapes.get("eyeBlinkRight", 0.0)
        )

        aspect_hw = (
            float(frame_bgr.shape[0]) / max(float(frame_bgr.shape[1]), 1.0)
            if (frame_bgr is not None and frame_bgr.size > 0)
            else 0.5625
        )
        head_pitch, head_yaw, head_roll, head_rot = compute_continuous_head_pose(
            landmarks, transformation_matrix, aspect_hw=aspect_hw
        )

        base_ear = profile.get("ear", 0.27)
        ear_threshold = max(0.14, base_ear * 0.66)
        eyes_closed = (not eyes_occluded) and bool(ear < ear_threshold or eye_blink > 0.56)

        if eyes_closed:
            if self._closed_since is None:
                self._closed_since = now
            closure_sec = now - self._closed_since
        else:
            if self._closed_since is not None:
                prev_dur = now - self._closed_since
                if 0.04 <= prev_dur <= 0.48:
                    self._blink_timestamps.append(now)
            self._closed_since = None
            closure_sec = 0.0

        while self._blink_timestamps and (now - self._blink_timestamps[0]) > 60.0:
            self._blink_timestamps.popleft()
        window_sec = max(12.0, min(60.0, now - self._started_at))
        blinks_per_min = (len(self._blink_timestamps) / window_sec) * 60.0

        vor_params = (
            profile.gaze_model.get("vor_params")
            if (profile.gaze_calibrated and isinstance(profile.gaze_model, dict))
            else None
        )
        eb = self._eyeball.track(
            landmarks=landmarks,
            blendshapes=blendshapes,
            head_rot=head_rot,
            head_pitch_deg=head_pitch,
            head_yaw_deg=head_yaw,
            eyes_closed=eyes_closed,
            frame_bgr=frame_bgr,
            render_strip=render_eye_strip,
            transformation_matrix=transformation_matrix,
            vor_params=vor_params,
        )

        d_yaw = head_yaw - profile.get("head_yaw", 0.0)
        d_pitch = head_pitch - profile.get("head_pitch", 0.0)

        if not eyes_closed and not eyes_occluded:
            # 1. Primary path: v3 head-invariant 3D Ray-Plane + VOR solver (decoupled from `C`!)
            reg_pred = (
                TiltedScreenRegressionModel.predict(profile.gaze_model, eb.feature_vec)
                if profile.gaze_calibrated
                else None
            )
            if reg_pred is not None:
                pred_gx, pred_gy = reg_pred
            else:
                # 2. Uncalibrated fallback: 3D metric ray-plane intersection in centimeters
                base_x_cm = profile.get("hit_x_cm", 0.0) if profile.calibrated else 0.0
                base_y_cm = profile.get("hit_y_cm", 11.0) if profile.calibrated else 11.0
                pred_gx = 0.50 + (eb.hit_x_cm - base_x_cm) / 34.0
                pred_gy = 0.48 + (eb.hit_y_cm - base_y_cm) / 20.0

            pred_gx = float(np.clip(pred_gx, -0.30, 1.30))
            pred_gy = float(np.clip(pred_gy, -0.25, 1.25))

            self._smooth_gx = self._filter_x.filter(pred_gx, now)
            self._smooth_gy = self._filter_y.filter(pred_gy, now)

        gx = self._smooth_gx
        gy = self._smooth_gy

        screen_x_px = int(round(float(np.clip(gx, 0.0, 1.0)) * max(screen_width - 1, 1)))
        screen_y_px = int(round(float(np.clip(gy, 0.0, 1.0)) * max(screen_height - 1, 1)))

        if eyes_closed and closure_sec >= 0.24:
            new_zone = "eyes_closed"
        elif eyes_closed or eyes_occluded:
            new_zone = self._current_zone
        elif (
            gx < -0.06
            or gx > 1.06
            or gy < -0.08
            or gy > 1.08
            or (not profile.gaze_calibrated and (abs(d_yaw) > 34.0 or abs(d_pitch) > 28.0))
        ):
            new_zone = "looking_away"
        elif gy >= 0.66:
            new_zone = "screen_terminal"
        else:
            new_zone = "screen_editor"

        if new_zone != self._current_zone:
            self._current_zone = new_zone
            self._zone_entered_at = now

        fixation_ms = int((now - self._zone_entered_at) * 1000)

        pose_penalty = min(0.30, (abs(d_yaw) + abs(d_pitch)) / 140.0)
        occ_penalty = 0.20 if eyes_occluded else (0.15 if eyes_closed else 0.0)
        cal_bonus = 0.04 if profile.gaze_calibrated else 0.0
        confidence = float(
            np.clip(0.94 + cal_bonus - pose_penalty - occ_penalty, 0.35, 0.99)
        )

        return GazeResult(
            zone=self._current_zone,
            gaze_x=round(gx, 3),
            gaze_y=round(gy, 3),
            screen_x_px=screen_x_px,
            screen_y_px=screen_y_px,
            eyeball_pitch=eb.eyeball_pitch_deg,
            eyeball_yaw=eb.eyeball_yaw_deg,
            gaze_ray=eb.gaze_ray_cam,
            ray_u=round(eb.ray_u, 4),
            ray_v=round(eb.ray_v, 4),
            iris_ratio_x=round(iris_ratio_x, 3),
            iris_ratio_y=round(iris_ratio_y, 3),
            iris_offset_y=round(iris_offset_y, 4),
            ear=round(ear, 3),
            eye_blink=round(eye_blink, 3),
            eyes_closed=eyes_closed,
            closure_duration_sec=round(closure_sec, 2),
            blinks_per_min=round(blinks_per_min, 1),
            fixation_ms=fixation_ms,
            head_pitch=round(head_pitch, 1),
            head_yaw=round(head_yaw, 1),
            head_roll=round(head_roll, 1),
            confidence=round(confidence, 2),
            eye_origin_cm=eb.eye_origin_cm,
            hit_x_cm=eb.hit_x_cm,
            hit_y_cm=eb.hit_y_cm,
            gaze_feature_vec=eb.feature_vec,
            eye_strip_bgr=eb.eye_strip_bgr,
        )
