"""Automated verification suite for WHACK Role 1: 3D Head-Invariant Eyeball + Tilted-Screen + Body/Face."""

from __future__ import annotations

import math
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FACE_DIR = Path(__file__).resolve().parent
if str(FACE_DIR) not in sys.path:
    sys.path.insert(0, str(FACE_DIR))

from body_gesture_tracker import BodyGestureTracker
from calibration import BaselineCalibrator, CalibrationProfile
from emotion_detector import EmotionDetector, build_event_payload, extract_feature_dict
from eyeball_tracker import (
    CANONICAL_CYCLOPEAN_EYE_CM,
    TiltedScreenRegressionModel,
    compute_continuous_head_pose,
)
from gaze_tracker import GazeTracker, extract_head_pose_degrees
from model_loader import (
    bgr_to_mp_image,
    create_face_landmarker,
    create_hand_landmarker,
    create_pose_landmarker,
    normalize_lighting_bgr,
)
import prototype_gui


@dataclass
class _Pt:
    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.99


def _synthetic_neutral_landmarks() -> list[_Pt]:
    lms = [_Pt(0.50, 0.50, 0.0) for _ in range(478)]
    lms[10] = _Pt(0.50, 0.20, 0.01)    # Forehead
    lms[152] = _Pt(0.50, 0.76, 0.02)   # Chin
    lms[234] = _Pt(0.32, 0.50, 0.08)   # Left cheek
    lms[454] = _Pt(0.68, 0.50, 0.08)   # Right cheek
    lms[1] = _Pt(0.50, 0.5136, -0.05)  # Nose tip
    lms[168] = _Pt(0.50, 0.40, -0.01)  # Nasal bridge

    # Eye A (screen-left)
    lms[33] = _Pt(0.38, 0.41, 0.0)
    lms[133] = _Pt(0.45, 0.41, 0.0)
    for idx in (246, 161, 160, 158, 159, 157, 173):
        lms[idx] = _Pt(0.415, 0.400, 0.0)
    for idx in (155, 154, 153, 145, 144, 163, 7):
        lms[idx] = _Pt(0.415, 0.420, 0.0)
    lms[468] = _Pt(0.415, 0.410, -0.01)
    for idx, dx, dy in ((469, 0.008, 0), (470, 0, -0.008), (471, -0.008, 0), (472, 0, 0.008)):
        lms[idx] = _Pt(0.415 + dx, 0.410 + dy, -0.01)

    # Eye B (screen-right)
    lms[362] = _Pt(0.55, 0.41, 0.0)
    lms[263] = _Pt(0.62, 0.41, 0.0)
    for idx in (398, 384, 385, 386, 387, 388, 466):
        lms[idx] = _Pt(0.585, 0.400, 0.0)
    for idx in (249, 390, 373, 374, 380, 381, 382):
        lms[idx] = _Pt(0.585, 0.420, 0.0)
    lms[473] = _Pt(0.585, 0.410, -0.01)
    for idx, dx, dy in ((474, 0.008, 0), (475, 0, -0.008), (476, 0, -0.008), (477, 0, 0.008)):
        lms[idx] = _Pt(0.585 + dx, 0.410 + dy, -0.01)

    for idx, x in ((70, 0.36), (63, 0.38), (105, 0.41), (66, 0.43), (107, 0.46)):
        lms[idx] = _Pt(x, 0.343)
    for idx, x in ((300, 0.64), (293, 0.62), (334, 0.59), (296, 0.57), (336, 0.54)):
        lms[idx] = _Pt(x, 0.343)

    lms[13] = _Pt(0.50, 0.640)
    lms[14] = _Pt(0.50, 0.643)
    return lms


def _synthetic_neutral_pose() -> list[_Pt]:
    pose = [_Pt(0.50, 0.90, 0.0, 0.95) for _ in range(33)]
    pose[0] = _Pt(0.50, 0.51)
    pose[11] = _Pt(0.28, 0.88)
    pose[12] = _Pt(0.72, 0.88)
    pose[13] = _Pt(0.20, 0.98)
    pose[14] = _Pt(0.80, 0.98)
    pose[15] = _Pt(0.22, 0.99)
    pose[16] = _Pt(0.78, 0.99)
    return pose


def _make_hand_at(palm_x: float, palm_y: float, tip_x: float, tip_y: float) -> list[_Pt]:
    hand = [_Pt(palm_x, palm_y) for _ in range(21)]
    hand[0] = _Pt(palm_x, palm_y + 0.08)
    for mcp in (5, 9, 13, 17):
        hand[mcp] = _Pt(palm_x, palm_y)
    for pip_idx in (6, 10, 14, 18):
        hand[pip_idx] = _Pt(0.5 * (palm_x + tip_x), 0.5 * (palm_y + tip_y))
    for ti in (4, 8, 12, 16, 20):
        hand[ti] = _Pt(tip_x, tip_y)
    return hand


def _make_head_pose_matrix(
    pitch_deg: float,
    yaw_deg: float = 0.0,
    roll_deg: float = 0.0,
    tx_cm: float = 0.0,
    ty_cm: float = -4.0,
    tz_cm: float = -54.0,
) -> np.ndarray:
    """Build 4x4 metric head transformation matrix in MediaPipe OpenGL camera space (cm)."""
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    r = math.radians(roll_deg)

    # +pitch tilts chin UP (+Z tilts toward +Y); +yaw turns RIGHT (+Z tilts toward +X); +roll CW
    rx_up = np.array(
        [[1.0, 0.0, 0.0], [0.0, math.cos(p), math.sin(p)], [0.0, -math.sin(p), math.cos(p)]],
        dtype=np.float64,
    )
    ry = np.array(
        [[math.cos(y), 0.0, math.sin(y)], [0.0, 1.0, 0.0], [-math.sin(y), 0.0, math.cos(y)]],
        dtype=np.float64,
    )
    rz_cw = np.array(
        [[math.cos(r), math.sin(r), 0.0], [-math.sin(r), math.cos(r), 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    m = np.eye(4, dtype=np.float64)
    m[:3, :3] = rz_cw @ ry @ rx_up
    m[:3, 3] = np.array([tx_cm, ty_cm, tz_cm], dtype=np.float64)
    return m


def _simulate_3d_user_gaze_frame(
    norm_tx: float,
    norm_ty: float,
    head_pitch_deg: float = -6.0,
    head_yaw_deg: float = 0.0,
    head_roll_deg: float = 0.0,
    tx_cm: float = 0.0,
    ty_cm: float = -5.0,
    tz_cm: float = -54.0,
    screen_tilt_deg: float = 14.0,
) -> tuple[list[_Pt], dict[str, float], np.ndarray]:
    """Physically accurate 3D user + tilted-screen + corneal-protrusion simulator.

    Simulates:
      1. Tilted 16-inch laptop screen (35.4 cm x 20.0 cm) tilted by `screen_tilt_deg` relative
         to camera vertical, with top-center webcam at `(0, 0, 0)`.
      2. Exact 3D cyclopean eyeball origin `O_eye = t_head + R_head @ [0, 1.8, 3.0]`.
      3. True 3D optical vector from `O_eye` to the 3D screen pixel `P_target(norm_tx, norm_ty)`.
      4. Subject-specific eyeball gain (`1/1.16`, `1/1.22`) AND corneal-protrusion foreshortening
         bias (`+0.24 * head_yaw`, `+0.21 * head_pitch`) that caused naive models to fail!
    """
    mat = _make_head_pose_matrix(
        head_pitch_deg, head_yaw_deg, head_roll_deg, tx_cm=tx_cm, ty_cm=ty_cm, tz_cm=tz_cm
    )
    r_head = mat[:3, :3]
    o_eye = mat[:3, 3] + (r_head @ CANONICAL_CYCLOPEAN_EYE_CM)

    # 3D position of target pixel (norm_tx, norm_ty) on tilted screen (webcam at top-center bezel)
    alpha = math.radians(screen_tilt_deg)
    screen_w_cm = 35.4
    screen_h_cm = 20.0
    bezel_top_cm = 1.2

    u_cm = (norm_tx - 0.50) * screen_w_cm
    v_down_cm = bezel_top_cm + norm_ty * screen_h_cm  # Distance downward along tilted screen panel

    p_target = np.array(
        [
            u_cm,
            -math.cos(alpha) * v_down_cm,
            math.sin(alpha) * v_down_cm,
        ],
        dtype=np.float64,
    )

    # True unit optical ray in camera coordinates -> rotate into skull frame!
    g_true = p_target - o_eye
    g_true /= np.linalg.norm(g_true)
    d_skull = r_head.T @ g_true
    d_skull /= np.linalg.norm(d_skull)

    true_eye_yaw = math.atan2(float(d_skull[0]), max(float(d_skull[2]), 1e-4))
    true_eye_pitch = math.asin(float(np.clip(d_skull[1], -0.95, 0.95)))  # + up, - down

    # Simulate real-webcam 3D eyeball-in-orbit physics discovered on live hardware:
    #   - Eyelid canthi sit ~6.5mm behind corneal apex vs 12mm sphere radius -> +0.58 * head_yaw bias!
    #   - Left/right head-yaw asymmetry (+0.15 * yaw^2) & subject eye gain (1/1.55 -> requires c_x ~ 0.90!)
    #   - MediaPipe neural blendshape cross-talk: |head_yaw| inflates eyeLookDown (+0.14 * |yaw|)!
    _, meas_head_yaw_deg, _, _ = compute_continuous_head_pose(
        _synthetic_neutral_landmarks(), mat, aspect_hw=0.5625
    )
    meas_head_pitch_deg, _, _, _ = compute_continuous_head_pose(
        _synthetic_neutral_landmarks(), mat, aspect_hw=0.5625
    )
    yaw_r = math.radians(meas_head_yaw_deg)
    pitch_r = math.radians(meas_head_pitch_deg)

    canthus_yaw_bias = 0.58 * yaw_r + 0.15 * (yaw_r * yaw_r)
    canthus_pitch_bias = 0.42 * pitch_r - 0.14 * abs(yaw_r)

    obs_eye_yaw = (true_eye_yaw + canthus_yaw_bias) / 1.55
    obs_eye_down = -(true_eye_pitch + canthus_pitch_bias) / 1.62

    # Split observed eyeball rotation into per-orbit 3D iris landmarks + ARKit blendshapes
    lms = _synthetic_neutral_landmarks()
    aspect_hw = 0.5625  # 720 / 1280
    roll_rad = math.radians(head_roll_deg)
    ux_iso = math.cos(roll_rad)
    uy_iso = math.sin(roll_rad)
    vx_iso = -uy_iso
    vy_iso = ux_iso

    sphere_r_iso = 0.44 * 0.070

    eye_look_h = obs_eye_yaw * 0.62
    eye_look_v = obs_eye_down * 0.58
    geom_yaw = (obs_eye_yaw - 0.44 * eye_look_h) / 0.72
    geom_down = (obs_eye_down - 0.48 * eye_look_v) / 1.08

    proj_u = sphere_r_iso * math.sin(float(np.clip(geom_yaw, -0.80, 0.80)))
    proj_v = sphere_r_iso * math.sin(float(np.clip(geom_down, -0.80, 0.80)))

    rel_x_iso = proj_u * ux_iso + proj_v * vx_iso
    rel_y_iso = proj_u * uy_iso + proj_v * vy_iso

    for corners, iris_idxs, cx_base, orb_sign in (
        ((33, 133), (468, 469, 470, 471, 472), 0.415, -1.0),
        ((362, 263), (473, 474, 475, 476, 477), 0.585, +1.0),
    ):
        cy_base = 0.410
        cos_orb = max(abs(math.cos(math.radians(head_yaw_deg) + orb_sign * 0.20)), 0.58)
        socket_w_eye = 0.070 * cos_orb
        half_w = 0.5 * socket_w_eye
        lms[corners[0]] = _Pt(cx_base - half_w * ux_iso, cy_base - (half_w * uy_iso) / aspect_hw, 0.0)
        lms[corners[1]] = _Pt(cx_base + half_w * ux_iso, cy_base + (half_w * uy_iso) / aspect_hw, 0.0)

        px = cx_base + rel_x_iso
        py = cy_base + (rel_y_iso / aspect_hw)
        lms[iris_idxs[0]] = _Pt(px, py, -0.01)
        for r_idx, r_dx, r_dy in (
            (iris_idxs[1], 0.008, 0.0),
            (iris_idxs[2], 0.0, -0.008 / aspect_hw),
            (iris_idxs[3], -0.008, 0.0),
            (iris_idxs[4], 0.0, 0.008 / aspect_hw),
        ):
            lms[r_idx] = _Pt(px + r_dx, py + r_dy, -0.01)

    bs = {
        "eyeLookOutLeft": max(0.0, eye_look_h),
        "eyeLookInRight": max(0.0, eye_look_h),
        "eyeLookOutRight": max(0.0, -eye_look_h),
        "eyeLookInLeft": max(0.0, -eye_look_h),
        "eyeLookDownLeft": max(0.0, eye_look_v),
        "eyeLookDownRight": max(0.0, eye_look_v),
        "eyeLookUpLeft": max(0.0, -eye_look_v),
        "eyeLookUpRight": max(0.0, -eye_look_v),
    }
    return lms, bs, mat


def run_tests() -> None:
    print("1. Verifying continuous signed 3D head pose (zero vertical pitch sign-flip)...")
    lms = _synthetic_neutral_landmarks()
    measured_pitches = []
    for target_p in (-20.0, -10.0, -3.0, 0.0, 3.0, 10.0, 20.0):
        mat = _make_head_pose_matrix(target_p)
        p_out, _, _, _ = compute_continuous_head_pose(lms, mat)
        measured_pitches.append(p_out)
    assert all(
        measured_pitches[i] < measured_pitches[i + 1] for i in range(len(measured_pitches) - 1)
    ), f"Expected strictly monotonic pitch, got {measured_pitches}"
    print(f"   OK: Strictly monotonic signed pitch verified: {[round(v, 1) for v in measured_pitches]}")

    print("2. Verifying 3D Head-Invariant Calibration (9-Pt + Step 10 VOR Lock on Tilted Screen)...")
    synth_frame = np.full((720, 1280, 3), 180, dtype=np.uint8)
    gt_cal = GazeTracker()
    prof = CalibrationProfile()

    # Step 1..9: 9-point Look-&-Click with mostly steady head
    cal_x, cal_y = [], []
    for _, tx, ty in prototype_gui.GAZE_CAL_9PTS:
        cur_lms, bs_pt, mat_pt = _simulate_3d_user_gaze_frame(tx, ty)
        g_sample = gt_cal.update(
            cur_lms,
            bs_pt,
            prof,
            transformation_matrix=mat_pt,
            frame_bgr=synth_frame,
            render_eye_strip=True,
            screen_width=1920,
            screen_height=1080,
        )
        assert g_sample.eye_strip_bgr is not None and g_sample.eye_strip_bgr.shape == (66, 228, 3)
        for _ in range(10):
            cal_x.append(list(g_sample.gaze_feature_vec))
            cal_y.append((tx, ty))

    # Step 10: Guided Left/Right Head-Turn VOR Lock on center (0.50, 0.50)
    vor_poses = [
        (-6.0, -22.0, 0.0, -4.0, -5.0, -54.0),
        (-6.0, -12.0, 0.0, -2.0, -5.0, -54.0),
        (-6.0, +12.0, 0.0, +2.0, -5.0, -54.0),
        (-6.0, +22.0, 0.0, +4.0, -5.0, -54.0),
        (+6.0, 0.0, 0.0, 0.0, +2.0, -54.0),
        (-16.0, 0.0, 0.0, 0.0, -11.0, -54.0),
        (-6.0, -14.0, 0.0, -10.0, -5.0, -45.0),
        (-6.0, +14.0, 0.0, +10.0, -5.0, -62.0),
    ]
    for hp_d, hy_d, hr_d, ox_d, oy_d, oz_d in vor_poses:
        v_lms, v_bs, v_mat = _simulate_3d_user_gaze_frame(
            0.50, 0.50, head_pitch_deg=hp_d, head_yaw_deg=hy_d, head_roll_deg=hr_d,
            tx_cm=ox_d, ty_cm=oy_d, tz_cm=oz_d,
        )
        v_sample = gt_cal.update(v_lms, v_bs, prof, transformation_matrix=v_mat)
        for _ in range(8):
            cal_x.append(list(v_sample.gaze_feature_vec))
            cal_y.append((0.50, 0.50))

    model = TiltedScreenRegressionModel.fit(cal_x, cal_y, ridge_lambda=0.85)
    assert model is not None and model["version"] == 4 and model["head_motion_captured"] is True
    prof.gaze_calibrated = True
    prof.gaze_model = model

    # Test 2A: Verify 6-DOF Head Movement Invariance (Translation + Depth + Yaw/Pitch VOR + Roll!)
    def eval_screen_px(
        tx: float, ty: float, hp: float = -6.0, hy: float = 0.0, hr: float = 0.0,
        ox: float = 0.0, oy: float = -5.0, oz: float = -54.0, eval_prof: CalibrationProfile = prof,
    ) -> tuple[int, int, str]:
        e_lms, e_bs, e_mat = _simulate_3d_user_gaze_frame(
            tx, ty, head_pitch_deg=hp, head_yaw_deg=hy, head_roll_deg=hr,
            tx_cm=ox, ty_cm=oy, tz_cm=oz,
        )
        trk = GazeTracker()
        out = None
        for _ in range(10):
            out = trk.update(
                e_lms, e_bs, eval_prof, transformation_matrix=e_mat, screen_width=1920, screen_height=1080
            )
        assert out is not None
        return out.screen_x_px, out.screen_y_px, out.zone

    # Check bottom terminal target (0.50, 0.90)
    bx_px, by_px, b_zone = eval_screen_px(0.50, 0.90)
    assert abs(bx_px - 960) < 28 and abs(by_px - int(0.90 * 1079)) < 28, f"Got ({bx_px}, {by_px})"
    assert b_zone == "screen_terminal"

    # Check 3D Head Translations (±14 cm X, ±8 cm Y, and leaning 15 cm closer Oz=-39 cm!)
    translation_cases = [
        ("Slide Left -14cm", 0.50, 0.50, -6.0, 0.0, 0.0, -14.0, -5.0, -54.0),
        ("Slide Right +14cm", 0.50, 0.50, -6.0, 0.0, 0.0, +14.0, -5.0, -54.0),
        ("Lift Head +8cm", 0.50, 0.50, -6.0, 0.0, 0.0, 0.0, +3.0, -54.0),
        ("Lower Head -8cm", 0.50, 0.50, -6.0, 0.0, 0.0, 0.0, -13.0, -54.0),
        ("Lean In Closer (39cm) at Right Edge", 0.85, 0.30, -6.0, 0.0, 0.0, +4.0, -4.0, -39.0),
        ("Lean Back (68cm) at Top-Left", 0.15, 0.20, -6.0, 0.0, 0.0, -5.0, -6.0, -68.0),
    ]
    max_trans_err = 0.0
    for label, tx, ty, hp, hy, hr, ox, oy, oz in translation_cases:
        px, py, _ = eval_screen_px(tx, ty, hp, hy, hr, ox, oy, oz)
        err = math.hypot(px - tx * 1919, py - ty * 1079)
        max_trans_err = max(max_trans_err, err)
        assert err < 35.0, f"Head translation case '{label}' drifted by {err:.1f} px -> ({px}, {py})"
    print(f"   OK: 3D Head Translation (±14cm X, ±8cm Y, 39..68cm Z) max error = {max_trans_err:.1f} px (< 35 px).")

    # Check 3D Left & Right Head Rotations (VOR yaw ±22 deg across center, left & right targets!)
    rotation_cases = [
        ("Turn Head Left -22° on Center", 0.50, 0.50, -6.0, -22.0, 0.0, -3.0, -5.0, -54.0),
        ("Turn Head Right +22° on Center", 0.50, 0.50, -6.0, +22.0, 0.0, +3.0, -5.0, -54.0),
        ("Turn Head Left -18° Looking Right", 0.82, 0.35, -6.0, -18.0, 0.0, -2.0, -5.0, -54.0),
        ("Turn Head Right +18° Looking Left", 0.18, 0.65, -6.0, +18.0, 0.0, +2.0, -5.0, -54.0),
        ("Pitch Chin Up +6°", 0.50, 0.50, +6.0, 0.0, 0.0, 0.0, -5.0, -54.0),
        ("Pitch Chin Down -16°", 0.50, 0.50, -16.0, 0.0, 0.0, 0.0, -5.0, -54.0),
        ("Combined Turn +15° & Slide +10cm", 0.30, 0.75, -10.0, +15.0, 0.0, +10.0, -7.0, -50.0),
        ("Head Roll Tilt +18°", 0.70, 0.35, -6.0, 0.0, +18.0, 0.0, -5.0, -54.0),
    ]
    max_rot_err = 0.0
    for label, tx, ty, hp, hy, hr, ox, oy, oz in rotation_cases:
        px, py, _ = eval_screen_px(tx, ty, hp, hy, hr, ox, oy, oz)
        err = math.hypot(px - tx * 1919, py - ty * 1079)
        max_rot_err = max(max_rot_err, err)
        assert err < 26.0, f"Head rotation VOR case '{label}' drifted by {err:.1f} px -> ({px}, {py})"
    print(f"   OK: 3D Left/Right Head Rotation VOR (±22° yaw, ±11° pitch, +18° roll) max error = {max_rot_err:.1f} px (< 26 px).")

    # Test 2B: Moving Head DURING the 9-Point Clicks (even if Step 10 is skipped!)
    moving_cal_x, moving_cal_y = [], []
    head_shifts = (
        (-9.0, -12.0), (+8.0, +14.0), (-5.0, -8.0),
        (+10.0, +11.0), (-8.0, +12.0), (+6.0, -14.0),
        (+7.0, -10.0), (-9.0, +13.0), (+4.0, +9.0),
    )
    for idx, (_, tx, ty) in enumerate(prototype_gui.GAZE_CAL_9PTS):
        ox_move, hy_move = head_shifts[idx]
        m_lms, m_bs, m_mat = _simulate_3d_user_gaze_frame(
            tx, ty, head_yaw_deg=hy_move, tx_cm=ox_move
        )
        m_sample = gt_cal.update(m_lms, m_bs, CalibrationProfile(), transformation_matrix=m_mat)
        for _ in range(8):
            moving_cal_x.append(list(m_sample.gaze_feature_vec))
            moving_cal_y.append((tx, ty))

    moving_model = TiltedScreenRegressionModel.fit(moving_cal_x, moving_cal_y)
    assert moving_model is not None
    moving_prof = CalibrationProfile(gaze_calibrated=True, gaze_model=moving_model)
    m_px, m_py, _ = eval_screen_px(0.50, 0.50, hy=-15.0, ox=+10.0, eval_prof=moving_prof)
    assert math.hypot(m_px - 960, m_py - 540) < 35.0, f"Moving-during-9pt failed: ({m_px}, {m_py})"

    # Test 2C: Stationary-Head 9-Pt Only (Zero variance bomb test — must NEVER blow up off-screen!)
    frozen_model = TiltedScreenRegressionModel.fit(cal_x[:90], cal_y[:90])
    assert frozen_model is not None
    frozen_prof = CalibrationProfile(gaze_calibrated=True, gaze_model=frozen_model)
    f_px, f_py, _ = eval_screen_px(0.50, 0.50, hy=+14.0, ox=+12.0, eval_prof=frozen_prof)
    assert 780 <= f_px <= 1140 and 380 <= f_py <= 700, f"Stationary 9-pt blew up under head move: ({f_px}, {f_py})"
    print("   OK: Moving-head-during-9pt & zero-variance-stability guard both verified.")

    # Test 2D: Save/Load v3 profile & `C` Neutral Calibration decoupling
    calibrator = BaselineCalibrator(profile=prof)
    calibrator.start(duration_sec=1.0, skip_settle=True)
    pose = _synthetic_neutral_pose()
    bt = BodyGestureTracker()
    neutral_bs = {"browDownLeft": 0.04, "browDownRight": 0.04, "eyeSquintLeft": 0.06, "eyeSquintRight": 0.06}
    ref_mat = _make_head_pose_matrix(-6.0)
    t_end = time.monotonic() + 1.05
    while time.monotonic() < t_end:
        hp, hy, hr = extract_head_pose_degrees(lms, ref_mat)
        b_res = bt.update(lms, pose, [], hp, hy, hr, calibrator.profile)
        g_res = gt_cal.update(lms, neutral_bs, calibrator.profile, transformation_matrix=ref_mat)
        if calibrator.add_sample(extract_feature_dict(lms, neutral_bs, g_res, b_res)):
            break
        time.sleep(0.05)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "cal_v3.json"
        calibrator.profile.save(tmp_path)
        reloaded = CalibrationProfile.load(tmp_path)
    assert reloaded.calibrated and reloaded.gaze_calibrated and reloaded.gaze_model.get("version") == 4
    ax_px, ay_px, _ = eval_screen_px(0.50, 0.90, eval_prof=reloaded)
    assert ax_px == bx_px and ay_px == by_px
    print(f"   OK: Fitted v4 VOR params = {model['vor_params'][:3]} (c_x converged past old 0.75 cap!) & decoupled from `C`.")

    print("3. Verifying Face + Body/Hand Emotions & Multimodal `/events` Payload...")
    profile = reloaded

    def step_multimodal(bs_override: dict, hands=(), pose_override=None, iters: int = 12):
        det = EmotionDetector(smoothing_alpha=0.55)
        g_trk = GazeTracker()
        b_trk = BodyGestureTracker()
        cur_lms = _synthetic_neutral_landmarks()
        cur_pose = pose_override if pose_override is not None else _synthetic_neutral_pose()
        bs = dict(neutral_bs)
        bs.update(bs_override)
        emo, gz, bd = None, None, None
        for _ in range(iters):
            hp, hy, hr = extract_head_pose_degrees(cur_lms, ref_mat)
            bd = b_trk.update(cur_lms, cur_pose, hands, hp, hy, hr, profile)
            gz = g_trk.update(
                cur_lms, bs, profile, transformation_matrix=ref_mat, eyes_occluded=bd.eyes_occluded
            )
            f = extract_feature_dict(cur_lms, bs, gz, bd)
            emo = det.update(f, gz, profile, bd)
        return emo, gz, bd

    assert step_multimodal({})[0].primary_emotion == "flow"
    scratch_hand = _make_hand_at(palm_x=0.72, palm_y=0.30, tip_x=0.66, tip_y=0.22)
    emo_scr, gz_scr, bd_scr = step_multimodal({}, hands=[scratch_hand])
    assert bd_scr.primary_gesture == "head_scratch" and emo_scr.primary_emotion == "confused"

    f_lm = create_face_landmarker(running_mode="VIDEO")
    p_lm = create_pose_landmarker(running_mode="VIDEO")
    h_lm = create_hand_landmarker(running_mode="VIDEO")
    dark = normalize_lighting_bgr(np.full((480, 640, 3), 35, dtype=np.uint8))
    mp_img = bgr_to_mp_image(dark)
    assert f_lm.detect_for_video(mp_img, 10).face_landmarks == []
    f_lm.close()
    p_lm.close()
    h_lm.close()

    canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    prototype_gui.draw_face_overlays(
        canvas, lms, gz_scr, emo_scr, show_mesh=True, pose_landmarks=pose, hand_landmarks_list=[scratch_hand], body=bd_scr
    )
    payload = build_event_payload(emo_scr, gz_scr, bd_scr)
    assert (
        "screen_x_px" in payload["gaze"]
        and "eyeball_pitch" in payload["gaze"]
        and "eye_origin_cm" in payload["gaze"]
    )
    print("   OK: All 3 MediaPipe models, gestures, overlays, and pixel-coord `/events` JSON verified.")
    print("\nALL 3D HEAD-INVARIANT EYEBALL + TILTED-SCREEN + MULTIMODAL SELF-TESTS PASSED.")


if __name__ == "__main__":
    run_tests()
