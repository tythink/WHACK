"""Upper-body posture, head dynamics, and hand-to-face gesture tracker for WHACK Role 1."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence

import numpy as np

from calibration import CalibrationProfile

# MediaPipe 21 Hand Landmark indices
WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_MCP = 13
RING_TIP = 16
PINKY_MCP = 17
PINKY_TIP = 20

FINGERTIP_IDXS = (4, 8, 12, 16, 20)
PALM_ANCHOR_IDXS = (0, 5, 9, 13, 17)
CURL_TIP_IDXS = (8, 12, 16, 20)

# MediaPipe 33 Pose Landmark indices (upper body)
POSE_NOSE = 0
POSE_LEFT_SHOULDER = 11
POSE_RIGHT_SHOULDER = 12
POSE_LEFT_ELBOW = 13
POSE_RIGHT_ELBOW = 14
POSE_LEFT_WRIST = 15
POSE_RIGHT_WRIST = 16

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

UPPER_POSE_CONNECTIONS = (
    (11, 12),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
)


@dataclass
class BodyGestureResult:
    """Per-frame body posture, hand gesture, and occlusion awareness output."""

    primary_gesture: str = "none"
    active_gestures: List[str] = field(default_factory=list)
    gesture_confidence: float = 0.0
    posture: str = "upright"
    lean_ratio: float = 1.0
    hands_detected: int = 0
    mouth_occluded: bool = False
    eyes_occluded: bool = False
    emotion_boosts: Dict[str, float] = field(
        default_factory=lambda: {
            "confused": 0.0,
            "frustrated": 0.0,
            "surprised": 0.0,
            "fatigued": 0.0,
            "delighted": 0.0,
        }
    )
    # Raw posture metrics exposed for neutral baseline calibration
    face_width: float = 0.22
    chin_y: float = 0.72
    shoulder_y: float = 0.88
    shoulder_span: float = 0.52
    neck_ratio: float = 0.75


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _vis(pt) -> float:
    v = getattr(pt, "visibility", None)
    return 1.0 if v is None else float(v)


class _HandMotionTrack:
    """Tracks short-window fingertip trajectory for scratch/rub oscillation scoring."""

    def __init__(self) -> None:
        self.samples: Deque[tuple[float, float, float, float, float]] = deque(maxlen=24)

    def push(
        self, now: float, wrist_x: float, wrist_y: float, tip_x: float, tip_y: float
    ) -> None:
        self.samples.append((now, wrist_x, wrist_y, tip_x, tip_y))
        while self.samples and (now - self.samples[0][0]) > 0.85:
            self.samples.popleft()

    def last_wrist(self, now: float) -> Optional[tuple[float, float]]:
        if not self.samples or (now - self.samples[-1][0]) > 0.50:
            return None
        _, wx, wy, _, _ = self.samples[-1]
        return (wx, wy)

    def oscillation_speed(self, face_w: float) -> float:
        """Return fingertip path speed in `face_widths / sec` over recent window."""
        if len(self.samples) < 3:
            return 0.0
        dt = self.samples[-1][0] - self.samples[0][0]
        if dt < 0.06:
            return 0.0
        pts = [(tx, ty) for _, _, _, tx, ty in self.samples]
        path = sum(
            _dist(pts[i][0], pts[i][1], pts[i - 1][0], pts[i - 1][1])
            for i in range(1, len(pts))
        )
        return float((path / max(face_w, 0.05)) / dt)


class BodyGestureTracker:
    """Correlates 478 face landmarks with 33 body pose and 21x2 hand landmarks."""

    def __init__(self) -> None:
        self._hand_tracks: tuple[_HandMotionTrack, _HandMotionTrack] = (
            _HandMotionTrack(),
            _HandMotionTrack(),
        )
        # `(timestamp, delta_yaw, delta_pitch, delta_roll)`
        self._head_history: Deque[tuple[float, float, float, float]] = deque(maxlen=40)
        self._tilt_since: Optional[float] = None

    def _assign_hand_tracks(
        self, now: float, hands: Sequence[Sequence]
    ) -> List[tuple[Sequence, float, float]]:
        """Pair detected hands with persistent motion tracks and return `(hand, tip_mx, tip_my)`."""
        out: List[tuple[Sequence, float, float]] = []
        used_tracks: set[int] = set()
        for hand in hands[:2]:
            wx, wy = float(hand[WRIST].x), float(hand[WRIST].y)
            tip_mx = float(np.mean([hand[i].x for i in FINGERTIP_IDXS]))
            tip_my = float(np.mean([hand[i].y for i in FINGERTIP_IDXS]))

            best_idx = 0
            best_d = 1e9
            for idx, trk in enumerate(self._hand_tracks):
                if idx in used_tracks:
                    continue
                lw = trk.last_wrist(now)
                if lw is not None:
                    d = _dist(wx, wy, lw[0], lw[1])
                    if d < best_d:
                        best_d = d
                        best_idx = idx
            if best_d > 0.35:
                best_idx = 0 if (0 not in used_tracks and wx < 0.5) else (1 if 1 not in used_tracks else 0)
            used_tracks.add(best_idx)
            self._hand_tracks[best_idx].push(now, wx, wy, tip_mx, tip_my)
            out.append((hand, tip_mx, tip_my))
        return out

    def _detect_head_dynamics(
        self, now: float, d_pitch: float, d_yaw: float, d_roll: float
    ) -> tuple[bool, bool, bool]:
        """Return `(head_tilt, head_shake, head_nod)`."""
        self._head_history.append((now, d_yaw, d_pitch, d_roll))
        while self._head_history and (now - self._head_history[0][0]) > 1.35:
            self._head_history.popleft()

        # Sustained head roll ("huh?" tilt >= 10.5 deg for >= 0.45s)
        if abs(d_roll) >= 10.5:
            if self._tilt_since is None:
                self._tilt_since = now
            head_tilt = (now - self._tilt_since) >= 0.45
        else:
            self._tilt_since = None
            head_tilt = False

        head_shake = False
        head_nod = False
        if len(self._head_history) >= 8:
            yaws = np.array([y for _, y, _, _ in self._head_history], dtype=np.float64)
            pitches = np.array([p for _, _, p, _ in self._head_history], dtype=np.float64)

            if float(np.ptp(yaws)) >= 8.5 and float(np.std(yaws)) >= 2.8:
                dy = np.diff(yaws)
                sig_y = dy[np.abs(dy) > 1.1]
                if sig_y.size >= 3 and int(np.sum(np.diff(np.sign(sig_y)) != 0)) >= 2:
                    head_shake = True

            if float(np.ptp(pitches)) >= 7.5 and float(np.std(pitches)) >= 2.4 and not head_shake:
                dp = np.diff(pitches)
                sig_p = dp[np.abs(dp) > 1.0]
                if sig_p.size >= 3 and int(np.sum(np.diff(np.sign(sig_p)) != 0)) >= 2:
                    head_nod = True

        return head_tilt, head_shake, head_nod

    def update(
        self,
        face_landmarks: Sequence,
        pose_landmarks: Optional[Sequence],
        hand_landmarks_list: Sequence[Sequence],
        head_pitch: float,
        head_yaw: float,
        head_roll: float,
        profile: CalibrationProfile,
    ) -> BodyGestureResult:
        now = time.monotonic()

        forehead = face_landmarks[10]
        bridge = face_landmarks[168]
        chin = face_landmarks[152]
        cheek_l = face_landmarks[234]
        cheek_r = face_landmarks[454]
        eye_a = face_landmarks[468] if len(face_landmarks) >= 478 else face_landmarks[33]
        eye_b = face_landmarks[473] if len(face_landmarks) >= 478 else face_landmarks[263]
        mouth_x = 0.5 * (face_landmarks[13].x + face_landmarks[14].x)
        mouth_y = 0.5 * (face_landmarks[13].y + face_landmarks[14].y)

        left_x = min(cheek_l.x, cheek_r.x)
        right_x = max(cheek_l.x, cheek_r.x)
        face_w = max(right_x - left_x, 0.06)
        face_h = max(chin.y - forehead.y, 0.08)
        head_cx = 0.5 * (left_x + right_x)
        eye_y = 0.5 * (eye_a.y + eye_b.y)

        boosts: Dict[str, float] = {
            "confused": 0.0,
            "frustrated": 0.0,
            "surprised": 0.0,
            "fatigued": 0.0,
            "delighted": 0.0,
        }
        gestures: List[str] = []
        mouth_occluded = False
        eyes_occluded = False
        best_conf = 0.0

        # 1. Seating distance / lean-in & upper-body posture
        base_fw = max(profile.get("face_width", face_w), 0.06)
        lean_ratio = face_w / base_fw
        posture = "upright"

        shoulder_y = profile.get("shoulder_y", 0.88)
        shoulder_span = profile.get("shoulder_span", 0.52)
        neck_ratio = profile.get("neck_ratio", 0.75)

        pose_wrist_near_head = False
        if pose_landmarks and len(pose_landmarks) >= 17:
            ls = pose_landmarks[POSE_LEFT_SHOULDER]
            rs = pose_landmarks[POSE_RIGHT_SHOULDER]
            if _vis(ls) > 0.45 and _vis(rs) > 0.45:
                shoulder_y = 0.5 * (float(ls.y) + float(rs.y))
                shoulder_span = abs(float(rs.x) - float(ls.x))
                neck_ratio = (shoulder_y - float(chin.y)) / face_w

                d_sh_y = shoulder_y - profile.get("shoulder_y", shoulder_y)
                d_chin_y = float(chin.y) - profile.get("chin_y", float(chin.y))
                base_neck = max(profile.get("neck_ratio", neck_ratio), 0.25)

                if profile.calibrated:
                    if d_sh_y > 0.048 or (d_chin_y > 0.060 and neck_ratio < base_neck * 0.82):
                        posture = "slumped"
                        gestures.append("shoulder_slump")
                        boosts["fatigued"] = max(boosts["fatigued"], 0.55)
                        best_conf = max(best_conf, 0.78)
                    elif d_sh_y < -0.038 and neck_ratio < base_neck * 0.80:
                        posture = "shrugging"
                        gestures.append("shoulder_shrug")
                        boosts["confused"] = max(boosts["confused"], 0.35)
                        boosts["frustrated"] = max(boosts["frustrated"], 0.30)
                        best_conf = max(best_conf, 0.72)

            # Backup wrist-near-head check (catches head scratch when fingers hide in hair)
            for w_idx in (POSE_LEFT_WRIST, POSE_RIGHT_WRIST):
                pw = pose_landmarks[w_idx]
                if _vis(pw) > 0.50:
                    if (
                        abs(float(pw.x) - head_cx) < 1.05 * face_w
                        and (forehead.y - 0.45 * face_h) <= float(pw.y) <= (eye_y + 0.30 * face_h)
                    ):
                        pose_wrist_near_head = True

        if profile.calibrated and lean_ratio >= 1.18:
            posture = "leaning_in"
            gestures.append("lean_in")
            lean_boost = min(0.40, (lean_ratio - 1.14) * 1.25)
            boosts["confused"] = max(boosts["confused"], lean_boost)
            best_conf = max(best_conf, 0.76)

        # 2. Head Tilt / Head Shake / Head Nod
        d_pitch = head_pitch - profile.get("head_pitch", 0.0)
        d_yaw = head_yaw - profile.get("head_yaw", 0.0)
        d_roll = head_roll - profile.get("head_roll", 0.0)
        head_tilt, head_shake, head_nod = self._detect_head_dynamics(
            now, d_pitch, d_yaw, d_roll
        )
        if head_tilt:
            gestures.append("head_tilt")
            boosts["confused"] = max(boosts["confused"], 0.46)
            best_conf = max(best_conf, 0.78)
        if head_shake:
            gestures.append("head_shake")
            boosts["frustrated"] = max(boosts["frustrated"], 0.58)
            best_conf = max(best_conf, 0.82)
        if head_nod:
            gestures.append("head_nod")
            boosts["delighted"] = max(boosts["delighted"], 0.30)

        # 3. Fine-grained 21-Landmark Hand Gestures
        hands = list(hand_landmarks_list) if hand_landmarks_list else []
        tracked_hands = self._assign_hand_tracks(now, hands)
        hands_near_upper = 0

        for idx, (hand, _tip_mx, _tip_my) in enumerate(tracked_hands):
            osc = self._hand_tracks[min(idx, 1)].oscillation_speed(face_w)
            palm_x = float(np.mean([hand[i].x for i in PALM_ANCHOR_IDXS]))
            palm_y = float(np.mean([hand[i].y for i in PALM_ANCHOR_IDXS]))
            palm_size = max(
                _dist(hand[WRIST].x, hand[WRIST].y, hand[MIDDLE_MCP].x, hand[MIDDLE_MCP].y),
                0.04,
            )

            tips = [(float(hand[i].x), float(hand[i].y)) for i in FINGERTIP_IDXS]
            knuckles = [(float(hand[i].x), float(hand[i].y)) for i in (6, 8, 10, 12)]

            if palm_y <= chin.y + 0.25 * face_h:
                hands_near_upper += 1

            # Occlusion guards: check overlap with mouth or eyes
            min_mouth_d = min(
                [_dist(palm_x, palm_y, mouth_x, mouth_y)]
                + [_dist(tx, ty, mouth_x, mouth_y) for tx, ty in tips]
            )
            if min_mouth_d < 0.34 * face_w:
                mouth_occluded = True

            min_eye_d = min(
                min(_dist(kx, ky, eye_a.x, eye_a.y), _dist(kx, ky, eye_b.x, eye_b.y))
                for kx, ky in knuckles
            )
            if min_eye_d < 0.25 * face_w:
                eyes_occluded = True

            # Check Thumb + Index nose-bridge pinch
            pinch_bridge = (
                _dist(hand[THUMB_TIP].x, hand[THUMB_TIP].y, bridge.x, bridge.y) < 0.30 * face_w
                and _dist(hand[INDEX_TIP].x, hand[INDEX_TIP].y, bridge.x, bridge.y) < 0.30 * face_w
            )
            # Check central forehead / facepalm cover
            palm_on_face = (
                abs(palm_x - head_cx) < 0.42 * face_w
                and (forehead.y - 0.12 * face_h) <= palm_y <= (mouth_y + 0.08 * face_h)
            )
            tips_on_forehead = any(
                abs(tx - head_cx) < 0.34 * face_w
                and (forehead.y - 0.08 * face_h) <= ty <= (eye_y - 0.06 * face_h)
                for tx, ty in tips
            )

            # Check temple / crown / scalp head-scratch zone
            tips_on_scalp_or_temple = any(
                abs(tx - head_cx) < 0.82 * face_w
                and (forehead.y - 0.65 * face_h) <= ty <= (eye_y + 0.20 * face_h)
                and (abs(tx - head_cx) >= 0.30 * face_w or ty < forehead.y + 0.04 * face_h)
                for tx, ty in tips
            )

            # Check chin rest / jaw stroke
            on_chin = (
                abs(palm_x - chin.x) < 0.48 * face_w
                and (mouth_y - 0.05 * face_h) <= palm_y <= (chin.y + 0.42 * face_h)
            ) or any(
                abs(tx - chin.x) < 0.42 * face_w
                and (mouth_y + 0.04 * face_h) <= ty <= (chin.y + 0.28 * face_h)
                for tx, ty in tips
            )

            # Check finger curl (fist vs thumbs-up)
            curled_fingers = sum(
                1
                for ti in CURL_TIP_IDXS
                if _dist(hand[ti].x, hand[ti].y, palm_x, palm_y) < 0.68 * palm_size
            )
            thumb_up = (
                curled_fingers >= 3
                and (hand[INDEX_MCP].y - hand[THUMB_TIP].y) > 0.38 * palm_size
                and abs(palm_x - head_cx) > 0.35 * face_w
            )
            clenched_fist = (
                curled_fingers == 4
                and _dist(hand[THUMB_TIP].x, hand[THUMB_TIP].y, palm_x, palm_y) < 0.85 * palm_size
                and palm_y < chin.y + 0.35 * face_h
            )

            # Classify gesture priority for this hand
            if min_eye_d < 0.25 * face_w and not palm_on_face:
                if "eye_rub" not in gestures:
                    gestures.append("eye_rub")
                rub_bonus = 0.72 if osc >= 0.25 else 0.58
                boosts["fatigued"] = max(boosts["fatigued"], rub_bonus)
                best_conf = max(best_conf, 0.88 if osc >= 0.25 else 0.75)

            elif pinch_bridge or palm_on_face or (tips_on_forehead and osc >= 0.28):
                if "facepalm_or_rub" not in gestures:
                    gestures.append("facepalm_or_rub")
                eyes_occluded = eyes_occluded or palm_on_face
                boosts["frustrated"] = max(boosts["frustrated"], 0.74)
                boosts["confused"] = max(boosts["confused"], 0.22)
                best_conf = max(best_conf, 0.90)

            elif tips_on_scalp_or_temple or tips_on_forehead:
                if "head_scratch" not in gestures:
                    gestures.append("head_scratch")
                scratch_boost = 0.72 if osc >= 0.22 else 0.62
                boosts["confused"] = max(boosts["confused"], scratch_boost)
                best_conf = max(best_conf, 0.92 if osc >= 0.22 else 0.78)

            elif on_chin:
                if "chin_rest" not in gestures:
                    gestures.append("chin_rest")
                mouth_occluded = True
                boosts["confused"] = max(boosts["confused"], 0.55)
                best_conf = max(best_conf, 0.82)

            elif min_mouth_d < 0.28 * face_w:
                if "mouth_cover" not in gestures:
                    gestures.append("mouth_cover")
                mouth_occluded = True
                boosts["surprised"] = max(boosts["surprised"], 0.35)
                best_conf = max(best_conf, 0.76)

            elif thumb_up:
                if "thumbs_up" not in gestures:
                    gestures.append("thumbs_up")
                boosts["delighted"] = max(boosts["delighted"], 0.72)
                best_conf = max(best_conf, 0.88)

            elif clenched_fist:
                if "clenched_fist" not in gestures:
                    gestures.append("clenched_fist")
                boosts["frustrated"] = max(boosts["frustrated"], 0.56)
                best_conf = max(best_conf, 0.80)

        # Fallback: Pose wrist raised next to head when fingers occluded in hair
        if pose_wrist_near_head and not gestures:
            gestures.append("head_scratch")
            boosts["confused"] = max(boosts["confused"], 0.60)
            best_conf = max(best_conf, 0.74)

        # Two hands raised near head level outside face = exasperation ("hands thrown up")
        if hands_near_upper >= 2 and "facepalm_or_rub" not in gestures and "head_scratch" not in gestures:
            if "hands_thrown_up" not in gestures:
                gestures.append("hands_thrown_up")
            boosts["frustrated"] = max(boosts["frustrated"], 0.66)
            best_conf = max(best_conf, 0.84)

        # Pick highest-priority gesture label for UI headline
        priority_order = (
            "head_scratch",
            "facepalm_or_rub",
            "eye_rub",
            "chin_rest",
            "hands_thrown_up",
            "clenched_fist",
            "thumbs_up",
            "mouth_cover",
            "head_shake",
            "head_tilt",
            "lean_in",
            "shoulder_slump",
            "shoulder_shrug",
            "head_nod",
        )
        primary_gesture = next((g for g in priority_order if g in gestures), "none")

        boosts = {k: round(float(np.clip(v, 0.0, 0.82)), 3) for k, v in boosts.items()}

        return BodyGestureResult(
            primary_gesture=primary_gesture,
            active_gestures=gestures,
            gesture_confidence=round(best_conf, 2),
            posture=posture,
            lean_ratio=round(lean_ratio, 2),
            hands_detected=len(hands),
            mouth_occluded=mouth_occluded,
            eyes_occluded=eyes_occluded,
            emotion_boosts=boosts,
            face_width=round(face_w, 4),
            chin_y=round(float(chin.y), 4),
            shoulder_y=round(shoulder_y, 4),
            shoulder_span=round(shoulder_span, 4),
            neck_ratio=round(neck_ratio, 4),
        )
