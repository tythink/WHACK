"""3D spherical eyeball tracker, metric eye-origin PnP, 1-Euro filter, and v4 VOR tilted-screen solver."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

# MediaPipe 478 Face Mesh eye & iris indices
EYE_A_CORNERS = (33, 133)
EYE_A_CONTOUR = (33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7)
EYE_A_IRIS = (468, 469, 470, 471, 472)  # 468 = center, 469..472 = limbus cardinal pts

EYE_B_CORNERS = (362, 263)
EYE_B_CONTOUR = (362, 398, 384, 385, 386, 387, 388, 466, 263, 249, 390, 373, 374, 380, 381, 382)
EYE_B_IRIS = (473, 474, 475, 476, 477)  # 473 = center, 474..477 = limbus cardinal pts

# 8-parameter physical VOR & tilted-screen priors:
# (k_ex, c_x, c_x2, k_ey, c_y, c_y_abs_yaw, c_y_yaw, alpha_tilt_rad)
# Note: socket canthi sit ~6.5mm behind corneal apex vs 12mm eyeball radius -> 6.5/12 = 0.54 -> c_x ~ 0.68
DEFAULT_VOR_PARAMS: tuple[float, ...] = (1.32, 0.68, 0.0, 1.36, 0.52, 0.10, 0.0, 0.0)

# Verified cyclopean midpoint between 3D eyeball centers in MediaPipe canonical skull space (cm)
CANONICAL_CYCLOPEAN_EYE_CM = np.array([0.0, 2.0, 0.9], dtype=np.float64)


class OneEuroFilter:
    """Velocity-adaptive low-pass filter (rock-steady fixations, near-zero saccade lag)."""

    def __init__(
        self,
        min_cutoff: float = 1.15,
        beta: float = 3.8,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x_prev: Optional[float] = None
        self._dx_prev: float = 0.0
        self._t_prev: Optional[float] = None

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-4))
        return 1.0 / (1.0 + tau / max(dt, 1e-4))

    def filter(self, x: float, now: Optional[float] = None) -> float:
        t = time.monotonic() if now is None else float(now)
        if self._x_prev is None or self._t_prev is None:
            self._x_prev = float(x)
            self._t_prev = t
            return float(x)

        dt = max(t - self._t_prev, 1e-3)
        self._t_prev = t

        dx = (x - self._x_prev) / dt
        a_d = self._alpha(dt, self.d_cutoff)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev
        self._dx_prev = dx_hat

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self._x_prev
        self._x_prev = x_hat
        return float(x_hat)


@dataclass
class SingleEyeballMetrics:
    """3D spherical eyeball geometry for a single eye."""

    socket_cx: float
    socket_cy: float
    pupil_x: float
    pupil_y: float
    sphere_radius_norm: float
    iris_radius_norm: float
    sx: float  # Horizontal normalized displacement on eyeball sphere [-1, 1] (+ right)
    sy: float  # Vertical normalized displacement on eyeball sphere [-1, 1] (+ down)
    yaw_deg: float
    pitch_deg: float  # + up, - down


@dataclass
class EyeballTrackingResult:
    """Combined binocular 3D eyeball state, metric origin, gaze ray, and zoomed eye crops."""

    left_screen_eye: SingleEyeballMetrics
    right_screen_eye: SingleEyeballMetrics
    eyeball_yaw_deg: float    # + looking right, - looking left (in skull frame)
    eyeball_pitch_deg: float  # + looking up, - looking down (in skull frame)
    gaze_ray_cam: tuple[float, float, float]   # Unit 3D optical gaze vector in camera frame
    eye_origin_cm: tuple[float, float, float]  # 3D cyclopean eyeball center in camera frame (cm)
    hit_x_cm: float           # 3D ray-plane horizontal intersection on screen (cm, + right)
    hit_y_cm: float           # 3D ray-plane vertical intersection on screen (cm, + down)
    ray_u: float              # Horizontal perspective ray tangent
    ray_v: float              # Vertical perspective ray tangent (+ downward)
    feature_vec: List[float] = field(default_factory=list)
    eye_strip_bgr: Optional[np.ndarray] = None  # Combined zoomed dual-eyeball visualization


def _orthonormalize_rotation(rot_raw: np.ndarray) -> np.ndarray:
    """Ensure 3x3 rotation matrix has unit-norm orthogonal columns with +Z facing camera."""
    rot = np.asarray(rot_raw, dtype=np.float64).copy()
    sign_z = 1.0 if rot[2, 2] >= 0.0 else -1.0
    rot *= sign_z
    c0 = rot[:, 0] / max(np.linalg.norm(rot[:, 0]), 1e-6)
    c2 = rot[:, 2] / max(np.linalg.norm(rot[:, 2]), 1e-6)
    c1 = np.cross(c2, c0)
    c1 /= max(np.linalg.norm(c1), 1e-6)
    c0 = np.cross(c1, c2)
    c0 /= max(np.linalg.norm(c0), 1e-6)
    return np.column_stack((c0, c1, c2))


def compute_continuous_head_pose(
    landmarks: Sequence,
    transformation_matrix: Optional[np.ndarray] = None,
    aspect_hw: float = 0.5625,
) -> tuple[float, float, float, np.ndarray]:
    """Compute continuous signed `(pitch_deg, yaw_deg, roll_deg, R_3x3)` with zero sign flips."""
    e1, e2 = landmarks[33], landmarks[263]
    left_eye, right_eye = (e1, e2) if e1.x <= e2.x else (e2, e1)
    roll_deg = math.degrees(
        math.atan2((right_eye.y - left_eye.y) * aspect_hw, right_eye.x - left_eye.x)
    )

    if transformation_matrix is not None and transformation_matrix.shape == (4, 4):
        rot = _orthonormalize_rotation(transformation_matrix[:3, :3])
        fx = float(rot[0, 2])
        fy = float(rot[1, 2])
        fz = max(abs(float(rot[2, 2])), 1e-5)

        yaw_deg = math.degrees(math.atan2(fx, fz))
        pitch_deg = math.degrees(math.atan2(fy, fz))
        return float(pitch_deg), float(yaw_deg), float(roll_deg), rot

    # Continuous landmark-plane 3D fallback (when no matrix is supplied in synthetic tests)
    u_vec = np.array(
        [
            right_eye.x - left_eye.x,
            -(right_eye.y - left_eye.y) * aspect_hw,
            -(right_eye.z - left_eye.z),
        ],
        dtype=np.float64,
    )
    u_vec /= max(np.linalg.norm(u_vec), 1e-6)

    glabella = landmarks[168]
    chin = landmarks[152]
    v_vec = np.array(
        [
            -(chin.x - glabella.x),
            -(chin.y - glabella.y) * aspect_hw,
            -(chin.z - glabella.z),
        ],
        dtype=np.float64,
    )
    v_vec /= max(np.linalg.norm(v_vec), 1e-6)

    f_vec = np.cross(u_vec, v_vec)
    if f_vec[2] < 0:
        f_vec = -f_vec
    f_vec /= max(np.linalg.norm(f_vec), 1e-6)
    v_orth = np.cross(f_vec, u_vec)
    v_orth /= max(np.linalg.norm(v_orth), 1e-6)

    rot_fb = np.column_stack((u_vec, v_orth, f_vec))
    yaw_deg = math.degrees(math.atan2(float(f_vec[0]), max(abs(float(f_vec[2])), 1e-5)))
    pitch_deg = math.degrees(math.atan2(float(f_vec[1]), max(abs(float(f_vec[2])), 1e-5)))
    return float(pitch_deg), float(yaw_deg), float(roll_deg), rot_fb


def compute_3d_eye_origin_cm(
    landmarks: Sequence,
    head_rot: np.ndarray,
    transformation_matrix: Optional[np.ndarray] = None,
    aspect_hw: float = 0.5625,
) -> tuple[float, float, float]:
    """Compute metric 3D cyclopean eyeball center `(Ox, Oy, Oz)` in camera space (cm)."""
    if (
        transformation_matrix is not None
        and transformation_matrix.shape == (4, 4)
        and abs(float(transformation_matrix[2, 3])) > 5.0
    ):
        tx = float(transformation_matrix[0, 3])
        ty = float(transformation_matrix[1, 3])
        tz = -abs(float(transformation_matrix[2, 3]))
        t_head = np.array([tx, ty, tz], dtype=np.float64)
        o_eye = t_head + (head_rot @ CANONICAL_CYCLOPEAN_EYE_CM)
        oz = float(np.clip(o_eye[2], -150.0, -18.0))
        return float(o_eye[0]), float(o_eye[1]), oz

    # Pinhole metric 3D fallback from bi-outer-canthal width (~9.2 cm) when matrix has no translation
    e1, e2 = landmarks[33], landmarks[263]
    i1, i2 = landmarks[133], landmarks[362]
    left_outer, right_outer = (e1, e2) if e1.x <= e2.x else (e2, e1)

    dx_iso = right_outer.x - left_outer.x
    dy_iso = (right_outer.y - left_outer.y) * aspect_hw
    span_iso = max(math.hypot(dx_iso, dy_iso), 0.03)

    fx_norm = 0.866025
    cos_yaw = max(abs(float(head_rot[2, 2])), 0.55)
    dist_cm = float(np.clip((9.2 * fx_norm * cos_yaw) / span_iso, 18.0, 150.0))

    mid_x = 0.25 * (e1.x + e2.x + i1.x + i2.x)
    mid_y = 0.25 * (e1.y + e2.y + i1.y + i2.y)

    ox_cm = ((mid_x - 0.50) / fx_norm) * dist_cm
    oy_cm = ((0.50 - mid_y) * aspect_hw / fx_norm) * dist_cm
    oz_cm = -dist_cm
    return float(ox_cm), float(oy_cm), float(oz_cm)


def _unpack_vor_params(
    vor_params: Optional[Sequence[float]],
) -> tuple[float, float, float, float, float, float, float, float]:
    if vor_params is not None and len(vor_params) == 8:
        return (
            float(vor_params[0]),
            float(vor_params[1]),
            float(vor_params[2]),
            float(vor_params[3]),
            float(vor_params[4]),
            float(vor_params[5]),
            float(vor_params[6]),
            float(vor_params[7]),
        )
    p = DEFAULT_VOR_PARAMS
    return p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]


def project_observations_to_plane(
    obs_mat: np.ndarray,
    vor_params: Optional[Sequence[float]] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized 3D VOR ray reconstruction + tilted-screen intersection in centimeters.

    `obs_mat` has shape `(N, 16)`:
      `[raw_eye_yaw, raw_eye_pitch, head_yaw, head_pitch, ox, oy, oz, r00..r22]`
    """
    k_ex, c_x, c_x2, k_ey, c_y, c_y_abs_yaw, c_y_yaw, alpha_tilt = _unpack_vor_params(
        vor_params
    )

    yaw = obs_mat[:, 2]
    pitch = obs_mat[:, 3]

    # 1. De-bias eyeball-center vs eyelid-canthus depth offset, left/right yaw asymmetry,
    #    and MediaPipe's |head_yaw| -> eyeLookDown neural cross-talk
    theta_x = np.clip(
        k_ex * obs_mat[:, 0] - c_x * yaw - c_x2 * (yaw * yaw),
        -1.20,
        1.20,
    )
    phi_y = np.clip(
        k_ey * obs_mat[:, 1]
        + c_y_abs_yaw * np.abs(yaw)
        - c_y * pitch
        - c_y_yaw * yaw,
        -1.10,
        1.10,
    )

    # 2. Unit 3D optical ray in canonical skull coordinates
    cos_p = np.cos(phi_y)
    dx = cos_p * np.sin(theta_x)
    dy = np.sin(phi_y)
    dz = cos_p * np.cos(theta_x)

    # 3. Rotate skull ray into camera space: g_cam = R_head @ d_local
    rot = obs_mat[:, 7:16].reshape(-1, 3, 3)
    gx = rot[:, 0, 0] * dx + rot[:, 0, 1] * dy + rot[:, 0, 2] * dz
    gy = rot[:, 1, 0] * dx + rot[:, 1, 1] * dy + rot[:, 1, 2] * dz
    gz = rot[:, 2, 0] * dx + rot[:, 2, 1] * dy + rot[:, 2, 2] * dz
    norms = np.maximum(np.sqrt(gx * gx + gy * gy + gz * gz), 1e-6)
    gx /= norms
    gy /= norms
    gz /= norms

    # 4. Intersect P(lam) = O_eye + lam * g_cam with tilted screen plane n(alpha) . P = 0
    sin_a = math.sin(alpha_tilt)
    cos_a = math.cos(alpha_tilt)

    ox = obs_mat[:, 4]
    oy = obs_mat[:, 5]
    oz = obs_mat[:, 6]

    num = -(sin_a * oy + cos_a * oz)
    den = np.maximum(sin_a * gy + cos_a * gz, 0.20)
    lam = np.clip(num / den, 10.0, 240.0)

    px = ox + lam * gx
    py = oy + lam * gy
    pz = oz + lam * gz

    x_cm = px
    y_cm = -cos_a * py + sin_a * pz
    g_cam = np.column_stack((gx, gy, gz))
    return x_cm, y_cm, g_cam


def _refine_subpixel_pupil(
    frame_bgr: Optional[np.ndarray],
    landmarks: Sequence,
    contour_idxs: tuple[int, ...],
    mp_pupil_x: float,
    mp_pupil_y: float,
    iris_r_norm: float,
) -> tuple[float, float]:
    """Sub-pixel dark-pupil centroid inside iris ROI, masking out upper eyelashes."""
    if frame_bgr is None or frame_bgr.size == 0:
        return mp_pupil_x, mp_pupil_y

    h, w = frame_bgr.shape[:2]
    cx_px = mp_pupil_x * w
    cy_px = mp_pupil_y * h
    r_px = max(iris_r_norm * w, 3.0)

    pad = int(math.ceil(r_px * 1.15)) + 2
    x0 = max(0, int(cx_px) - pad)
    x1 = min(w, int(cx_px) + pad + 1)
    y0 = max(0, int(cy_px) - pad)
    y1 = min(h, int(cy_px) + pad + 1)
    if (x1 - x0) < 7 or (y1 - y0) < 5:
        return mp_pupil_x, mp_pupil_y

    roi_bgr = frame_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

    poly = np.array(
        [[int(landmarks[i].x * w) - x0, int(landmarks[i].y * h) - y0] for i in contour_idxs],
        dtype=np.int32,
    )
    lid_mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.fillPoly(lid_mask, [poly], 255)
    kernel_v = np.ones((3, 1), dtype=np.uint8)
    lid_mask = cv2.erode(lid_mask, kernel_v, iterations=1)

    yy, xx = np.mgrid[0 : (y1 - y0), 0 : (x1 - x0)]
    dist_sq = (xx - (cx_px - x0)) ** 2 + (yy - (cy_px - y0)) ** 2
    iris_mask = (dist_sq <= (r_px * 0.82) ** 2) & (lid_mask > 0)

    valid_pixels = gray[iris_mask]
    if valid_pixels.size < 10:
        return mp_pupil_x, mp_pupil_y

    p75 = float(np.percentile(valid_pixels, 75))
    p15 = float(np.percentile(valid_pixels, 15))
    contrast = p75 - p15
    if contrast < 8.0:
        return mp_pupil_x, mp_pupil_y

    thresh = p15 + 0.38 * contrast
    dark_weights = np.clip(thresh - gray, 0.0, None) ** 2
    dark_weights *= iris_mask.astype(np.float32)

    total_w = float(np.sum(dark_weights))
    if total_w < 1e-4:
        return mp_pupil_x, mp_pupil_y

    sub_x = (float(np.sum(xx * dark_weights)) / total_w + x0) / w
    sub_y = (float(np.sum(yy * dark_weights)) / total_w + y0) / h

    max_shift_x = (r_px * 0.35) / w
    max_shift_y = (r_px * 0.35) / h
    refined_x = mp_pupil_x + float(np.clip(sub_x - mp_pupil_x, -max_shift_x, max_shift_x))
    refined_y = mp_pupil_y + float(np.clip(sub_y - mp_pupil_y, -max_shift_y, max_shift_y))

    return (
        0.68 * mp_pupil_x + 0.32 * refined_x,
        0.68 * mp_pupil_y + 0.32 * refined_y,
    )


def _analyze_single_eyeball(
    frame_bgr: Optional[np.ndarray],
    landmarks: Sequence,
    corners: tuple[int, int],
    contour: tuple[int, ...],
    iris_idxs: tuple[int, ...],
    orbital_yaw_rad: float = 0.0,
    aspect_hw: float = 0.5625,
) -> SingleEyeballMetrics:
    """Compute 3D spherical eyeball rotation in roll- & orbital-yaw-compensated socket axes."""
    c1, c2 = landmarks[corners[0]], landmarks[corners[1]]
    p_left, p_right = (c1, c2) if c1.x <= c2.x else (c2, c1)

    dx_iso = p_right.x - p_left.x
    dy_iso = (p_right.y - p_left.y) * aspect_hw
    socket_w_iso = max(math.hypot(dx_iso, dy_iso), 1e-5)

    ux, uy = dx_iso / socket_w_iso, dy_iso / socket_w_iso
    vx, vy = -uy, ux

    socket_cx = 0.5 * (p_left.x + p_right.x)
    socket_cy = 0.5 * (p_left.y + p_right.y)

    # De-foreshorten palpebral width using this eye's outward-splayed orbital plane angle
    cos_orb = max(abs(math.cos(orbital_yaw_rad)), 0.58)
    sphere_r_iso = (0.44 * socket_w_iso) / cos_orb

    if len(landmarks) >= 478:
        c_pt = landmarks[iris_idxs[0]]
        ring_pts = [landmarks[i] for i in iris_idxs[1:]]
        mp_px = (2.0 * c_pt.x + sum(p.x for p in ring_pts)) / 6.0
        mp_py = (2.0 * c_pt.y + sum(p.y for p in ring_pts)) / 6.0
        iris_r_iso = float(
            np.mean(
                [
                    math.hypot(p.x - c_pt.x, (p.y - c_pt.y) * aspect_hw)
                    for p in ring_pts
                ]
            )
        )
        pupil_x, pupil_y = _refine_subpixel_pupil(
            frame_bgr, landmarks, contour, mp_px, mp_py, iris_r_iso
        )
    else:
        pupil_x, pupil_y = socket_cx, socket_cy
        iris_r_iso = 0.20 * socket_w_iso

    rel_x_iso = pupil_x - socket_cx
    rel_y_iso = (pupil_y - socket_cy) * aspect_hw
    proj_u = rel_x_iso * ux + rel_y_iso * uy
    proj_v = rel_x_iso * vx + rel_y_iso * vy

    sx = float(np.clip(proj_u / max(sphere_r_iso, 1e-5), -0.85, 0.85))
    sy = float(np.clip(proj_v / max(sphere_r_iso, 1e-5), -0.85, 0.85))

    yaw_deg = math.degrees(math.asin(sx))
    pitch_deg = math.degrees(math.asin(-sy))

    return SingleEyeballMetrics(
        socket_cx=socket_cx,
        socket_cy=socket_cy,
        pupil_x=pupil_x,
        pupil_y=pupil_y,
        sphere_radius_norm=sphere_r_iso,
        iris_radius_norm=iris_r_iso,
        sx=sx,
        sy=sy,
        yaw_deg=yaw_deg,
        pitch_deg=pitch_deg,
    )


def _render_dual_eyeball_strip(
    frame_bgr: np.ndarray,
    left_eye: SingleEyeballMetrics,
    right_eye: SingleEyeballMetrics,
    eyes_closed: bool,
) -> np.ndarray:
    """Render a side-by-side zoomed live eyeball HUD strip (`228 x 66` BGR)."""
    h, w = frame_bgr.shape[:2]
    tile_w, tile_h = 110, 66
    out = np.full((tile_h, tile_w * 2 + 8, 3), (14, 20, 32), dtype=np.uint8)

    for idx, (eye, label) in enumerate(((left_eye, "L-EYE"), (right_eye, "R-EYE"))):
        cx = eye.socket_cx * w
        cy = eye.socket_cy * h
        half_w = max(eye.sphere_radius_norm * w * 1.95, 24.0)
        half_h = half_w * (tile_h / tile_w)

        x0 = int(max(0, cx - half_w))
        x1 = int(min(w, cx + half_w))
        y0 = int(max(0, cy - half_h))
        y1 = int(min(h, cy + half_h))

        if (x1 - x0) >= 8 and (y1 - y0) >= 6:
            crop = cv2.resize(
                frame_bgr[y0:y1, x0:x1], (tile_w, tile_h), interpolation=cv2.INTER_LINEAR
            )
            scale_x = tile_w / max(x1 - x0, 1)
            scale_y = tile_h / max(y1 - y0, 1)

            scx = int((eye.socket_cx * w - x0) * scale_x)
            scy = int((eye.socket_cy * h - y0) * scale_y)
            s_rad = max(6, int(eye.sphere_radius_norm * w * scale_x))

            cv2.circle(crop, (scx, scy), s_rad, (130, 110, 75), 1, cv2.LINE_AA)

            if not eyes_closed:
                px = int((eye.pupil_x * w - x0) * scale_x)
                py = int((eye.pupil_y * h - y0) * scale_y)
                i_rad = max(3, int(eye.iris_radius_norm * w * scale_x))

                cv2.circle(crop, (px, py), i_rad, (120, 255, 120), 1, cv2.LINE_AA)
                cv2.drawMarker(
                    crop,
                    (px, py),
                    (0, 255, 255),
                    cv2.MARKER_CROSS,
                    8,
                    1,
                    cv2.LINE_AA,
                )
                vx = int(np.clip(px + eye.sx * 28, 2, tile_w - 3))
                vy = int(np.clip(py + eye.sy * 28, 2, tile_h - 3))
                cv2.arrowedLine(
                    crop, (scx, scy), (vx, vy), (255, 210, 50), 2, cv2.LINE_AA, tipLength=0.32
                )

            cv2.putText(
                crop,
                label,
                (4, 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (200, 220, 245),
                1,
                cv2.LINE_AA,
            )
            ox = idx * (tile_w + 8)
            out[:, ox : ox + tile_w] = crop

    return out


class EyeballTracker:
    """Computes binocular 3D eyeball rotation, metric 3D eye origin, and VOR gaze ray."""

    def track(
        self,
        landmarks: Sequence,
        blendshapes: Dict[str, float],
        head_rot: np.ndarray,
        head_pitch_deg: float,
        head_yaw_deg: float,
        eyes_closed: bool,
        frame_bgr: Optional[np.ndarray] = None,
        render_strip: bool = False,
        transformation_matrix: Optional[np.ndarray] = None,
        vor_params: Optional[Sequence[float]] = None,
    ) -> EyeballTrackingResult:
        aspect_hw = (
            float(frame_bgr.shape[0]) / max(float(frame_bgr.shape[1]), 1.0)
            if (frame_bgr is not None and frame_bgr.size > 0)
            else 0.5625
        )
        head_yaw_rad = math.radians(head_yaw_deg)
        head_pitch_rad = math.radians(head_pitch_deg)

        # EYE_A (33, 133) sits on screen-left (x < 0.5); EYE_B (362, 263) sits on screen-right (x > 0.5).
        # Human orbits splay outward ~11.5 deg (0.20 rad): turning head RIGHT (+yaw) makes screen-left
        # eye MORE frontal to the webcam and screen-right eye MORE foreshortened behind the nose bridge!
        a_is_left = (landmarks[33].x + landmarks[133].x) <= (landmarks[362].x + landmarks[263].x)
        orb_yaw_a = (head_yaw_rad - 0.20) if a_is_left else (head_yaw_rad + 0.20)
        orb_yaw_b = (head_yaw_rad + 0.20) if a_is_left else (head_yaw_rad - 0.20)

        eye_a = _analyze_single_eyeball(
            frame_bgr,
            landmarks,
            EYE_A_CORNERS,
            EYE_A_CONTOUR,
            EYE_A_IRIS,
            orbital_yaw_rad=orb_yaw_a,
            aspect_hw=aspect_hw,
        )
        eye_b = _analyze_single_eyeball(
            frame_bgr,
            landmarks,
            EYE_B_CORNERS,
            EYE_B_CONTOUR,
            EYE_B_IRIS,
            orbital_yaw_rad=orb_yaw_b,
            aspect_hw=aspect_hw,
        )
        left_eye, right_eye = (eye_a, eye_b) if a_is_left else (eye_b, eye_a)

        # Frontal-visibility binocular weighting: favor the unoccluded frontal eye during head turns!
        w_left = max(math.cos(head_yaw_rad - 0.22), 0.12) ** 3
        w_right = max(math.cos(head_yaw_rad + 0.22), 0.12) ** 3
        w_sum = w_left + w_right
        geom_sx = (w_left * left_eye.sx + w_right * right_eye.sx) / w_sum
        geom_sy = (w_left * left_eye.sy + w_right * right_eye.sy) / w_sum

        eye_look_h = 0.5 * (
            blendshapes.get("eyeLookOutLeft", 0.0) + blendshapes.get("eyeLookInRight", 0.0)
        ) - 0.5 * (
            blendshapes.get("eyeLookOutRight", 0.0) + blendshapes.get("eyeLookInLeft", 0.0)
        )
        eye_look_v = 0.5 * (
            blendshapes.get("eyeLookDownLeft", 0.0) + blendshapes.get("eyeLookDownRight", 0.0)
        ) - 0.5 * (
            blendshapes.get("eyeLookUpLeft", 0.0) + blendshapes.get("eyeLookUpRight", 0.0)
        )

        raw_eye_yaw_rad = math.asin(float(np.clip(geom_sx, -0.85, 0.85))) * 0.72 + (
            eye_look_h * 0.44
        )
        raw_eye_down_rad = math.asin(float(np.clip(geom_sy, -0.85, 0.85))) * 1.08 + (
            eye_look_v * 0.48
        )
        raw_eye_pitch_rad = -raw_eye_down_rad  # + up, - down

        r_norm = _orthonormalize_rotation(head_rot)
        ox_cm, oy_cm, oz_cm = compute_3d_eye_origin_cm(
            landmarks, r_norm, transformation_matrix=transformation_matrix, aspect_hw=aspect_hw
        )

        r_flat = r_norm.reshape(-1)
        feature_vec = [
            float(raw_eye_yaw_rad),
            float(raw_eye_pitch_rad),
            float(head_yaw_rad),
            float(head_pitch_rad),
            float(ox_cm),
            float(oy_cm),
            float(oz_cm),
            float(r_flat[0]),
            float(r_flat[1]),
            float(r_flat[2]),
            float(r_flat[3]),
            float(r_flat[4]),
            float(r_flat[5]),
            float(r_flat[6]),
            float(r_flat[7]),
            float(r_flat[8]),
        ]

        k_ex, c_x, c_x2, k_ey, c_y, c_y_abs_yaw, c_y_yaw, alpha_tilt = _unpack_vor_params(
            vor_params
        )
        active_vor = (k_ex, c_x, c_x2, k_ey, c_y, c_y_abs_yaw, c_y_yaw, alpha_tilt)

        obs_1 = np.asarray([feature_vec], dtype=np.float64)
        x_arr, y_arr, g_arr = project_observations_to_plane(obs_1, active_vor)
        hit_x_cm = float(x_arr[0])
        hit_y_cm = float(y_arr[0])
        g_cam = g_arr[0]

        cal_theta_x = (
            k_ex * raw_eye_yaw_rad
            - c_x * head_yaw_rad
            - c_x2 * (head_yaw_rad * head_yaw_rad)
        )
        cal_phi_y = (
            k_ey * raw_eye_pitch_rad
            + c_y_abs_yaw * abs(head_yaw_rad)
            - c_y * head_pitch_rad
            - c_y_yaw * head_yaw_rad
        )
        eyeball_yaw_deg = math.degrees(cal_theta_x)
        eyeball_pitch_deg = math.degrees(cal_phi_y)

        gz = max(abs(float(g_cam[2])), 0.15)
        ray_u = float(g_cam[0] / gz)
        ray_v = float(-g_cam[1] / gz)

        strip = (
            _render_dual_eyeball_strip(frame_bgr, left_eye, right_eye, eyes_closed)
            if (render_strip and frame_bgr is not None)
            else None
        )

        return EyeballTrackingResult(
            left_screen_eye=left_eye,
            right_screen_eye=right_eye,
            eyeball_yaw_deg=round(eyeball_yaw_deg, 1),
            eyeball_pitch_deg=round(eyeball_pitch_deg, 1),
            gaze_ray_cam=(
                round(float(g_cam[0]), 4),
                round(float(g_cam[1]), 4),
                round(float(g_cam[2]), 4),
            ),
            eye_origin_cm=(round(ox_cm, 2), round(oy_cm, 2), round(oz_cm, 2)),
            hit_x_cm=round(hit_x_cm, 3),
            hit_y_cm=round(hit_y_cm, 3),
            ray_u=ray_u,
            ray_v=ray_v,
            feature_vec=feature_vec,
            eye_strip_bgr=strip,
        )


class TiltedScreenRegressionModel:
    """v4 Levenberg-Marquardt 3D Ray-Plane + Asymmetric VOR solver invariant to head turns & moves."""

    SCALE_CM = 15.0
    # (k_ex, c_x, c_x2, k_ey, c_y, c_y_abs_yaw, c_y_yaw, alpha_tilt)
    PARAM_BOUNDS: tuple[tuple[float, float], ...] = (
        (0.65, 3.20),   # k_ex
        (-0.40, 1.85),  # c_x (linear head-yaw VOR / canthus-depth compensation)
        (-0.90, 0.90),  # c_x2 (left-vs-right head-yaw asymmetry)
        (0.65, 3.20),   # k_ey
        (-0.40, 1.85),  # c_y (linear head-pitch VOR compensation)
        (-0.50, 0.90),  # c_y_abs_yaw (|head_yaw| -> eyeLookDown cross-talk cancellation)
        (-0.50, 0.50),  # c_y_yaw (camera/head roll coupling during yaw)
        (-0.55, 0.55),  # alpha_tilt (tilted screen angle in rad)
    )

    @classmethod
    def _build_plane_design(cls, x_cm: np.ndarray, y_cm: np.ndarray) -> np.ndarray:
        xs = x_cm / cls.SCALE_CM
        ys = (y_cm - 11.0) / cls.SCALE_CM
        return np.column_stack(
            [
                np.ones_like(xs),
                xs,
                ys,
                ys * ys,
                xs * ys,
            ]
        )

    @classmethod
    def _solve_screen_map(
        cls,
        design: np.ndarray,
        y_mat: np.ndarray,
        sample_weights: np.ndarray,
        ridge_lambda: float,
    ) -> np.ndarray:
        w_col = sample_weights[:, None]
        wd = design * w_col
        reg_scale = max(float(ridge_lambda), 0.05)
        reg = np.diag([0.0, 1e-4, 1e-4, 0.06 * reg_scale, 0.08 * reg_scale])
        lhs = design.T @ wd + reg
        rhs = wd.T @ y_mat
        return np.linalg.solve(lhs, rhs)

    @classmethod
    def _clamp_params(cls, vec: np.ndarray) -> np.ndarray:
        out = vec.copy()
        for j, (lo, hi) in enumerate(cls.PARAM_BOUNDS):
            out[j] = float(np.clip(out[j], lo, hi))
        return out

    @classmethod
    def fit(
        cls,
        samples_x: Sequence[Sequence[float]],
        targets_xy: Sequence[tuple[float, float]],
        ridge_lambda: float = 0.85,
    ) -> Optional[Dict[str, Any]]:
        """Fit v4 8-parameter 3D VOR + tilted-screen geometry via Levenberg-Marquardt."""
        obs_mat = np.asarray(samples_x, dtype=np.float64)
        y_mat = np.asarray(targets_xy, dtype=np.float64)
        if (
            obs_mat.ndim != 2
            or obs_mat.shape[0] < 9
            or obs_mat.shape[1] != 16
            or y_mat.shape != (obs_mat.shape[0], 2)
        ):
            return None

        n_samples = obs_mat.shape[0]

        bucket_keys = [
            (int(round(float(tx) * 12.0)), int(round(float(ty) * 12.0)))
            for tx, ty in y_mat
        ]
        bucket_indices: Dict[tuple[int, int], List[int]] = {}
        for idx, b_key in enumerate(bucket_keys):
            bucket_indices.setdefault(b_key, []).append(idx)

        raw_w = np.ones(n_samples, dtype=np.float64)
        for idxs in bucket_indices.values():
            raw_w[idxs] = 1.0 / float(len(idxs))
        sample_weights = raw_w * (float(n_samples) / max(float(np.sum(raw_w)), 1e-6))
        sqrt_grid_w = np.sqrt(sample_weights / float(n_samples))[:, None]

        multi_buckets = [
            np.asarray(idxs, dtype=np.intp)
            for idxs in bucket_indices.values()
            if len(idxs) >= 2
        ]
        total_scatter_pts = max(sum(int(b.size) for b in multi_buckets), 1)
        scatter_scale = math.sqrt(2.6 / float(total_scatter_pts))

        # Identifiability check: regress out [1, target_x, target_y] so head turns that merely
        # follow the 9 dots (without Step 10) cannot masquerade as fixed-target VOR excitation!
        t_design = np.column_stack([np.ones(n_samples, dtype=np.float64), y_mat])
        t_pinv = np.linalg.pinv(t_design)

        def _indep_residual(col: np.ndarray) -> np.ndarray:
            return col - t_design @ (t_pinv @ col)

        yaw_res = _indep_residual(obs_mat[:, 2])
        pitch_res = _indep_residual(obs_mat[:, 3])
        ox_res = _indep_residual(obs_mat[:, 4])
        oy_res = _indep_residual(obs_mat[:, 5])

        yaw_std = float(np.std(yaw_res))
        pitch_std = float(np.std(pitch_res))

        if yaw_std > 1e-3:
            slope_ox = float(np.cov(yaw_res, ox_res, bias=True)[0, 1]) / (yaw_std ** 2)
            indep_ox_std = float(np.std(ox_res - slope_ox * yaw_res))
        else:
            indep_ox_std = float(np.std(ox_res))

        if pitch_std > 1e-3:
            slope_oy = float(np.cov(pitch_res, oy_res, bias=True)[0, 1]) / (pitch_std ** 2)
            indep_oy_std = float(np.std(oy_res - slope_oy * pitch_res))
        else:
            indep_oy_std = float(np.std(oy_res))

        yaw_excit = yaw_std / 0.08
        pitch_excit = pitch_std / 0.07
        trans_x_excit = indep_ox_std / 4.5
        trans_y_excit = indep_oy_std / 3.5

        w_kx = 0.0008 / (1.0 + 25.0 * (trans_x_excit ** 2))
        w_cx = 0.030 / (1.0 + 120.0 * (yaw_excit ** 2))
        w_cx2 = 0.035 / (1.0 + 60.0 * (yaw_excit ** 2))
        w_ky = 0.0006 / (1.0 + 25.0 * (trans_y_excit ** 2))
        w_cy = 0.030 / (1.0 + 90.0 * (pitch_excit ** 2))
        w_cy_yaw = 0.030 / (1.0 + 80.0 * (yaw_excit ** 2))
        w_screen_cm = 0.022 / (1.0 + 25.0 * (trans_x_excit ** 2 + trans_y_excit ** 2))

        prior_sqrt_w = np.sqrt(
            np.array(
                [w_kx, w_cx, w_cx2, w_ky, w_cy, w_cy_yaw, w_cy_yaw * 1.4, 0.0008],
                dtype=np.float64,
            )
        )
        p0 = np.asarray(DEFAULT_VOR_PARAMS, dtype=np.float64)

        def eval_residuals(
            params: np.ndarray,
        ) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
            x_cm, y_cm, _ = project_observations_to_plane(obs_mat, params)
            design = cls._build_plane_design(x_cm, y_cm)
            weights = cls._solve_screen_map(design, y_mat, sample_weights, ridge_lambda)
            preds = design @ weights

            res_parts: List[np.ndarray] = [((preds - y_mat) * sqrt_grid_w).reshape(-1)]

            for b_idxs in multi_buckets:
                b_preds = preds[b_idxs]
                b_mean = np.mean(b_preds, axis=0, keepdims=True)
                res_parts.append(((b_preds - b_mean) * scatter_scale).reshape(-1))

            # Physical screen-scale & aspect-ratio consistency prevents k_ex/k_ey from trading off
            # against weights_x[1]/weights_y[2] when head only rotates on the neck without sliding
            w_est_cm = cls.SCALE_CM / max(float(weights[1, 0]), 0.12)
            h_est_cm = cls.SCALE_CM / max(float(weights[2, 1]), 0.15)
            aspect_est = w_est_cm / max(h_est_cm, 5.0)
            s_pen = math.sqrt(w_screen_cm)

            res_parts.append(prior_sqrt_w * (params - p0))
            res_parts.append(
                np.array(
                    [
                        s_pen * ((w_est_cm - 37.0) / 14.0),
                        s_pen * 2.2 * ((aspect_est - 1.75) / 0.22),
                        1.5 * max(0.0, 0.05 - float(weights[1, 0])),
                        1.5 * max(0.0, 0.05 - float(weights[2, 1])),
                    ],
                    dtype=np.float64,
                )
            )
            r_vec = np.concatenate(res_parts)
            loss = float(np.dot(r_vec, r_vec))
            return r_vec, loss, weights, preds

        # 1. Coarse multi-start seed search across c_x, k_ex, and tilt
        best_p = p0.copy()
        best_r, best_loss, best_w, best_preds = eval_residuals(best_p)

        for seed_cx in (0.32, 0.68, 1.05):
            for seed_kx in (1.05, 1.45):
                for seed_tilt in (-0.22, 0.0, 0.22):
                    cand = cls._clamp_params(
                        np.array(
                            [seed_kx, seed_cx, 0.0, seed_kx * 1.04, 0.52, 0.10, 0.0, seed_tilt],
                            dtype=np.float64,
                        )
                    )
                    c_r, c_loss, c_w, c_preds = eval_residuals(cand)
                    if c_loss < best_loss:
                        best_p, best_r, best_loss, best_w, best_preds = (
                            cand,
                            c_r,
                            c_loss,
                            c_w,
                            c_preds,
                        )

        # 2. Damped Gauss-Newton (Levenberg-Marquardt) global convergence
        n_params = best_p.size
        mu = 1e-2
        eps_fd = 1e-4
        eye_p = np.eye(n_params, dtype=np.float64)

        for _ in range(24):
            jac = np.zeros((best_r.size, n_params), dtype=np.float64)
            for j in range(n_params):
                p_plus = best_p.copy()
                lo, hi = cls.PARAM_BOUNDS[j]
                step_j = eps_fd if (p_plus[j] + eps_fd <= hi) else -eps_fd
                p_plus[j] += step_j
                r_plus, _, _, _ = eval_residuals(p_plus)
                jac[:, j] = (r_plus - best_r) / step_j

            jtj = jac.T @ jac
            jtr = jac.T @ best_r
            if float(np.max(np.abs(jtr))) < 1e-7:
                break

            accepted = False
            for _ in range(6):
                try:
                    delta = np.linalg.solve(jtj + mu * eye_p, -jtr)
                except np.linalg.LinAlgError:
                    mu *= 10.0
                    continue
                cand = cls._clamp_params(best_p + delta)
                c_r, c_loss, c_w, c_preds = eval_residuals(cand)
                if c_loss < best_loss - 1e-9:
                    imp = best_loss - c_loss
                    best_p, best_r, best_loss, best_w, best_preds = (
                        cand,
                        c_r,
                        c_loss,
                        c_w,
                        c_preds,
                    )
                    mu = max(mu * 0.35, 1e-6)
                    accepted = True
                    if imp < 1e-7:
                        break
                    break
                mu = min(mu * 4.0, 1e3)
            if not accepted:
                break

        # 3. Coordinate polish with `while improved:` in case any bound is active
        for step_scale in (0.04, 0.01, 0.0025):
            improved = True
            iters = 0
            while improved and iters < 12:
                improved = False
                iters += 1
                for j in range(n_params):
                    for sign in (-1.0, 1.0):
                        cand = best_p.copy()
                        cand[j] += sign * step_scale
                        cand = cls._clamp_params(cand)
                        c_r, c_loss, c_w, c_preds = eval_residuals(cand)
                        if c_loss < best_loss - 1e-8:
                            best_p, best_r, best_loss, best_w, best_preds = (
                                cand,
                                c_r,
                                c_loss,
                                c_w,
                                c_preds,
                            )
                            improved = True

        pt_errs = np.hypot(best_preds[:, 0] - y_mat[:, 0], best_preds[:, 1] - y_mat[:, 1])
        err_norm = float(np.sum(sample_weights * pt_errs) / n_samples)

        return {
            "version": 4,
            "vor_params": [round(float(v), 6) for v in best_p],
            "weights_x": [round(float(v), 6) for v in best_w[:, 0]],
            "weights_y": [round(float(v), 6) for v in best_w[:, 1]],
            "tilt_deg": round(math.degrees(float(best_p[7])), 1),
            "head_motion_captured": bool(
                yaw_excit > 0.35 or pitch_excit > 0.35 or trans_x_excit > 0.35
            ),
            "yaw_range_deg": round(
                math.degrees(float(np.max(obs_mat[:, 2]) - np.min(obs_mat[:, 2]))), 1
            ),
            "mean_error_norm": round(err_norm, 4),
            "sample_count": int(n_samples),
        }

    @classmethod
    def predict(
        cls,
        model_dict: Dict[str, Any],
        feature_vec: Sequence[float],
    ) -> Optional[tuple[float, float]]:
        """Predict normalized `(screen_x, screen_y)` invariant to 3D head turns & translations."""
        if not model_dict or model_dict.get("version") != 4:
            return None
        vor_params = model_dict.get("vor_params", [])
        wx = np.asarray(model_dict.get("weights_x", []), dtype=np.float64)
        wy = np.asarray(model_dict.get("weights_y", []), dtype=np.float64)
        if len(vor_params) != 8 or len(feature_vec) != 16 or wx.size != 5 or wy.size != 5:
            return None

        obs_1 = np.asarray([feature_vec], dtype=np.float64)
        x_cm, y_cm, _ = project_observations_to_plane(obs_1, vor_params)
        design = cls._build_plane_design(x_cm, y_cm)[0]
        gx = float(np.dot(design, wx))
        gy = float(np.dot(design, wy))
        return gx, gy
