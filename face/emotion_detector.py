"""Multimodal face + body/hand emotion classifier, occlusion guard, and fusion engine."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Sequence

import numpy as np

from body_gesture_tracker import BodyGestureResult
from calibration import CalibrationProfile
from gaze_tracker import GazeResult

EMOTION_KEYS = ("confused", "frustrated", "surprised", "flow", "fatigued", "delighted")


@dataclass
class EmotionResult:
    """Per-frame multimodal emotion scores, talking guard, and fusion status."""

    primary_emotion: str
    primary_score: float
    confidence: float
    emotions: Dict[str, float]
    talking: bool
    fusion_status: str
    fusion_level: str  # "ok" | "warn" | "alert" | "muted" | "positive"
    sustained_distress_sec: float = 0.0
    eureka_transition: bool = False


def blendshapes_to_dict(face_blendshapes_list: Sequence) -> Dict[str, float]:
    """Convert MediaPipe `Category` list into `{category_name: score}`."""
    if not face_blendshapes_list:
        return {}
    return {
        str(cat.category_name): float(cat.score)
        for cat in face_blendshapes_list
        if getattr(cat, "category_name", None) is not None
    }


def extract_feature_dict(
    landmarks: Sequence,
    blendshapes: Dict[str, float],
    gaze: GazeResult,
    body: Optional[BodyGestureResult] = None,
) -> Dict[str, float]:
    """Extract normalized blendshape, geometric, and posture features."""
    outer_a, outer_b = landmarks[33], landmarks[263]
    outer_eye_span = max(math.hypot(outer_a.x - outer_b.x, outer_a.y - outer_b.y), 1e-4)

    canthus_a_y = 0.5 * (landmarks[33].y + landmarks[133].y)
    canthus_b_y = 0.5 * (landmarks[362].y + landmarks[263].y)
    brow_a_y = 0.5 * (landmarks[107].y + landmarks[105].y)
    brow_b_y = 0.5 * (landmarks[336].y + landmarks[334].y)
    brow_eye_ratio = 0.5 * (
        (canthus_a_y - brow_a_y) + (canthus_b_y - brow_b_y)
    ) / outer_eye_span

    ib_a, ib_b = landmarks[107], landmarks[336]
    inner_brow_span = math.hypot(ib_a.x - ib_b.x, ib_a.y - ib_b.y) / outer_eye_span

    lip_top, lip_bot = landmarks[13], landmarks[14]
    lip_aperture = math.hypot(lip_top.x - lip_bot.x, lip_top.y - lip_bot.y) / outer_eye_span

    cheek_l, cheek_r = landmarks[234], landmarks[454]
    face_width = max(abs(cheek_r.x - cheek_l.x), 0.06)

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

    feats: Dict[str, float] = {
        "brow_down": 0.5
        * (blendshapes.get("browDownLeft", 0.0) + blendshapes.get("browDownRight", 0.0)),
        "brow_inner_up": blendshapes.get("browInnerUp", 0.0),
        "brow_outer_up": 0.5
        * (
            blendshapes.get("browOuterUpLeft", 0.0)
            + blendshapes.get("browOuterUpRight", 0.0)
        ),
        "eye_squint": 0.5
        * (blendshapes.get("eyeSquintLeft", 0.0) + blendshapes.get("eyeSquintRight", 0.0)),
        "eye_wide": 0.5
        * (blendshapes.get("eyeWideLeft", 0.0) + blendshapes.get("eyeWideRight", 0.0)),
        "eye_blink": gaze.eye_blink,
        "mouth_smile": 0.5
        * (
            blendshapes.get("mouthSmileLeft", 0.0)
            + blendshapes.get("mouthSmileRight", 0.0)
        ),
        "mouth_press": 0.5
        * (
            blendshapes.get("mouthPressLeft", 0.0)
            + blendshapes.get("mouthPressRight", 0.0)
        ),
        "mouth_frown": 0.5
        * (
            blendshapes.get("mouthFrownLeft", 0.0)
            + blendshapes.get("mouthFrownRight", 0.0)
        ),
        "mouth_stretch": 0.5
        * (
            blendshapes.get("mouthStretchLeft", 0.0)
            + blendshapes.get("mouthStretchRight", 0.0)
        ),
        "mouth_pucker": blendshapes.get("mouthPucker", 0.0),
        "mouth_roll": 0.5
        * (
            blendshapes.get("mouthRollLower", 0.0)
            + blendshapes.get("mouthRollUpper", 0.0)
        ),
        "cheek_squint": 0.5
        * (
            blendshapes.get("cheekSquintLeft", 0.0)
            + blendshapes.get("cheekSquintRight", 0.0)
        ),
        "nose_sneer": 0.5
        * (blendshapes.get("noseSneerLeft", 0.0) + blendshapes.get("noseSneerRight", 0.0)),
        "jaw_open": blendshapes.get("jawOpen", 0.0),
        "eye_look_h": eye_look_h,
        "eye_look_v": eye_look_v,
        "ear": gaze.ear,
        "brow_eye_ratio": brow_eye_ratio,
        "inner_brow_span": inner_brow_span,
        "lip_aperture": lip_aperture,
        "iris_ratio_x": gaze.iris_ratio_x,
        "iris_offset_y": gaze.iris_offset_y,
        "ray_u": gaze.ray_u,
        "ray_v": gaze.ray_v,
        "hit_x_cm": gaze.hit_x_cm,
        "hit_y_cm": gaze.hit_y_cm,
        "head_pitch": gaze.head_pitch,
        "head_yaw": gaze.head_yaw,
        "head_roll": gaze.head_roll,
        "face_width": body.face_width if body is not None else face_width,
        "chin_y": body.chin_y if body is not None else float(landmarks[152].y),
    }
    if body is not None:
        feats["shoulder_y"] = body.shoulder_y
        feats["shoulder_span"] = body.shoulder_span
        feats["neck_ratio"] = body.neck_ratio
    return feats


def _pos(val: float) -> float:
    return val if val > 0.0 else 0.0


def _noisy_or(p_face: float, p_body: float) -> float:
    """Combine independent face and body/hand gesture evidence."""
    pf = float(np.clip(p_face, 0.0, 1.0))
    pb = float(np.clip(p_body, 0.0, 1.0))
    return float(1.0 - (1.0 - pf) * (1.0 - pb))


class EmotionDetector:
    """Multimodal face + body/hand scorer with occlusion guard & transition memory."""

    def __init__(self, smoothing_alpha: float = 0.30) -> None:
        self.alpha = float(smoothing_alpha)
        self._smoothed: Dict[str, float] = {
            "confused": 0.05,
            "frustrated": 0.04,
            "surprised": 0.03,
            "flow": 0.82,
            "fatigued": 0.04,
            "delighted": 0.03,
        }
        self._mouth_history: Deque[tuple[float, float]] = deque(maxlen=40)
        self._drowsy_history: Deque[tuple[float, float]] = deque(maxlen=60)
        self._distress_since: Optional[float] = None
        self._last_distress_at: Optional[float] = None

    def _detect_talking(
        self, now: float, jaw_open: float, lip_aperture: float, mouth_occluded: bool
    ) -> bool:
        if mouth_occluded:
            # Hand resting on chin or covering mouth distorts lip landmarks; freeze talking guard
            return False

        combined = 0.65 * jaw_open + 0.35 * min(1.0, lip_aperture * 3.0)
        self._mouth_history.append((now, combined))

        while self._mouth_history and (now - self._mouth_history[0][0]) > 1.25:
            self._mouth_history.popleft()

        if len(self._mouth_history) < 8:
            return False

        vals = np.array([v for _, v in self._mouth_history], dtype=np.float64)
        if float(np.std(vals)) < 0.032 or float(np.ptp(vals)) < 0.09:
            return False

        diffs = np.diff(vals)
        significant = diffs[np.abs(diffs) > 0.012]
        if significant.size < 3:
            return False
        sign_changes = int(np.sum(np.diff(np.sign(significant)) != 0))
        return sign_changes >= 2

    def update(
        self,
        features: Dict[str, float],
        gaze: GazeResult,
        profile: CalibrationProfile,
        body: Optional[BodyGestureResult] = None,
    ) -> EmotionResult:
        now = time.monotonic()
        body_res = body or BodyGestureResult()

        talking = self._detect_talking(
            now,
            features.get("jaw_open", 0.0),
            features.get("lip_aperture", 0.0),
            body_res.mouth_occluded,
        )

        # Positive calibrated deltas above user's neutral baseline
        d_brow_down = _pos(features["brow_down"] - profile.get("brow_down") * 0.90)
        geom_brow_lower = _pos(
            profile.get("brow_eye_ratio") - features["brow_eye_ratio"] - 0.004
        )
        geom_brow_pinch = _pos(
            profile.get("inner_brow_span") - features["inner_brow_span"] - 0.004
        )
        geom_brow_raise = _pos(
            features["brow_eye_ratio"] - profile.get("brow_eye_ratio") - 0.005
        )

        brow_furrow = float(
            np.clip(
                d_brow_down * 1.90 + geom_brow_lower * 7.8 + geom_brow_pinch * 6.8,
                0.0,
                1.0,
            )
        )

        d_squint = _pos(features["eye_squint"] - profile.get("eye_squint") * 0.90)
        d_brow_up = max(
            _pos(features["brow_inner_up"] - profile.get("brow_inner_up") * 0.85),
            _pos(features["brow_outer_up"] - profile.get("brow_outer_up") * 0.85),
        )
        brow_raise = float(np.clip(d_brow_up * 1.70 + geom_brow_raise * 7.2, 0.0, 1.0))

        d_wide = _pos(features["eye_wide"] - profile.get("eye_wide") * 0.85)
        d_jaw = _pos(features["jaw_open"] - profile.get("jaw_open") * 0.90)
        d_smile = _pos(features["mouth_smile"] - profile.get("mouth_smile") * 0.85)
        d_cheek = _pos(features["cheek_squint"] - profile.get("cheek_squint") * 0.85)
        d_sneer = _pos(features["nose_sneer"] - profile.get("nose_sneer") * 0.85)
        d_pucker = _pos(features["mouth_pucker"] - profile.get("mouth_pucker") * 0.90)

        # Hand-Occlusion Guard: downweight distorted mouth/eye blendshapes when occluded by hand
        mouth_occ_scale = 0.22 if body_res.mouth_occluded else 1.0
        eyes_occ_scale = 0.25 if body_res.eyes_occluded else 1.0
        d_jaw *= mouth_occ_scale
        d_smile *= 0.45 if body_res.mouth_occluded else 1.0
        d_wide *= eyes_occ_scale

        mouth_tension = float(
            np.clip(
                max(
                    _pos(features["mouth_press"] - profile.get("mouth_press") * 0.85) * 2.1,
                    _pos(features["mouth_frown"] - profile.get("mouth_frown") * 0.85) * 2.1,
                    _pos(features.get("mouth_roll", 0.0) - profile.get("mouth_roll", 0.04) * 0.85)
                    * 1.7,
                    d_sneer * 1.9,
                    _pos(features["mouth_stretch"] - profile.get("mouth_stretch") * 0.85) * 1.6,
                )
                * mouth_occ_scale,
                0.0,
                1.0,
            )
        )
        if talking:
            mouth_tension *= 0.30

        eyes_shut = bool(
            gaze.eyes_closed
            and (
                gaze.closure_duration_sec >= 0.18
                or gaze.zone == "eyes_closed"
                or features.get("eye_blink", 0.0) >= 0.60
            )
        )

        # FACS AU4 (Brow Lowerer) + AU7 (Lid Tightener/Squint) co-activation synergy
        au4_au7_synergy = (
            0.14 if (brow_furrow > 0.28 and d_squint > 0.12 and not eyes_shut) else 0.0
        )
        # Stress blink-rate reinforcement (>28 blinks/min)
        stress_blink_bonus = 0.07 if gaze.blinks_per_min > 28.0 else 0.0

        smile_suppress = 1.0 - 0.75 * min(1.0, d_smile * 2.0)

        # 1. Confused (Face + Body Noisy-OR: head_scratch, chin_rest, lean_in, head_tilt)
        if eyes_shut:
            face_confused = 0.0
            raw_confused = 0.0
        else:
            face_confused = float(
                np.clip(
                    (
                        0.74 * brow_furrow
                        + 0.46 * min(1.0, d_squint * 2.1)
                        + 0.14 * min(1.0, d_pucker * 1.6 * mouth_occ_scale)
                        + au4_au7_synergy
                        + stress_blink_bonus * 0.5
                    )
                    * (1.0 - 0.55 * mouth_tension)
                    * smile_suppress,
                    0.0,
                    1.0,
                )
            )
            raw_confused = _noisy_or(
                face_confused, body_res.emotion_boosts.get("confused", 0.0)
            )

        # 2. Frustrated (Face + Body Noisy-OR: facepalm_or_rub, head_shake, clenched_fist, hands_thrown_up)
        face_frustrated = float(
            np.clip(
                (
                    0.52 * brow_furrow * (0.30 + 1.0 * mouth_tension)
                    + 0.68 * mouth_tension
                    + 0.22 * min(1.0, d_sneer * 2.2 * mouth_occ_scale)
                    + stress_blink_bonus
                )
                * smile_suppress,
                0.0,
                1.0,
            )
        )
        raw_frustrated = _noisy_or(
            face_frustrated, body_res.emotion_boosts.get("frustrated", 0.0)
        )

        # 3. Surprised (Face + Body: mouth_cover when brows raised)
        jaw_surprise = d_jaw * (0.20 if talking else 1.0)
        face_surprised = float(
            np.clip(
                0.58 * brow_raise
                + 0.55 * min(1.0, d_wide * 2.4)
                + 0.48 * min(1.0, jaw_surprise * 1.85),
                0.0,
                1.0,
            )
        )
        surp_body = (
            body_res.emotion_boosts.get("surprised", 0.0)
            if brow_raise > 0.22
            else 0.0
        )
        raw_surprised = _noisy_or(face_surprised, surp_body)

        # 4. Delighted / Eureka (AU6 Cheek + AU12 Smile + Thumbs-Up + Post-Distress Relief Transition!)
        duchenne_synergy = 0.12 if (d_smile > 0.18 and d_cheek > 0.10) else 0.0
        recent_distress = (
            self._last_distress_at is not None and (now - self._last_distress_at) <= 15.0
        )
        eureka_transition = bool(
            recent_distress
            and brow_furrow < 0.22
            and (d_smile > 0.14 or body_res.primary_gesture == "thumbs_up")
        )
        transition_bonus = 0.22 if eureka_transition else 0.0

        face_delighted = float(
            np.clip(
                (1.60 * d_smile + 0.65 * d_cheek + duchenne_synergy + transition_bonus)
                * (1.0 - 0.45 * brow_furrow),
                0.0,
                1.0,
            )
        )
        raw_delighted = _noisy_or(
            face_delighted, body_res.emotion_boosts.get("delighted", 0.0)
        )

        # 5. Fatigued (Heavy lids + prolonged closure + head droop + eye_rub + shoulder_slump)
        base_ear = max(profile.get("ear", 0.27), 0.18)
        base_blink = profile.get("eye_blink", 0.06)
        is_brief_blink = gaze.eyes_closed and gaze.closure_duration_sec < 0.24
        if is_brief_blink or body_res.eyes_occluded:
            lid_droop = 0.0
            ear_drop = 0.0
        else:
            ear_drop = _pos(base_ear - gaze.ear - 0.018) / base_ear
            blink_heavy = _pos(gaze.eye_blink - base_blink - 0.07)
            lid_droop = min(1.0, 2.40 * ear_drop + 2.20 * blink_heavy)

        self._drowsy_history.append((now, lid_droop))
        while self._drowsy_history and (now - self._drowsy_history[0][0]) > 1.0:
            self._drowsy_history.popleft()
        rolling_lid = float(np.mean([v for _, v in self._drowsy_history]))

        if eyes_shut:
            closure_boost = min(1.0, 0.65 + max(0.0, gaze.closure_duration_sec - 0.18) * 1.35)
        else:
            closure_boost = (
                min(1.0, 0.35 + (gaze.closure_duration_sec - 0.25) * 1.35)
                if gaze.closure_duration_sec > 0.25
                else 0.0
            )
        d_pitch = gaze.head_pitch - profile.get("head_pitch", 0.0)
        head_droop = min(0.75, _pos(-d_pitch - 5.5) / 12.5)
        yawn_bonus = (
            0.42
            if (d_jaw > 0.35 and (gaze.eye_blink > 0.18 or ear_drop > 0.10) and not talking)
            else 0.0
        )

        face_fatigued = float(
            np.clip(
                0.92 * rolling_lid + closure_boost + head_droop + yawn_bonus,
                0.0,
                1.0,
            )
        )
        raw_fatigued = _noisy_or(
            face_fatigued, body_res.emotion_boosts.get("fatigued", 0.0)
        )

        # 6. Flow / Neutral
        max_expressive = max(
            raw_confused, raw_frustrated, raw_surprised, raw_delighted, raw_fatigued
        )
        focus_adj = 0.08 if gaze.zone in ("screen_editor", "screen_terminal") else -0.10
        raw_flow = float(np.clip(1.0 - 1.18 * max_expressive + focus_adj, 0.04, 0.98))

        raw_scores = {
            "confused": raw_confused,
            "frustrated": raw_frustrated,
            "surprised": raw_surprised,
            "flow": raw_flow,
            "fatigued": raw_fatigued,
            "delighted": raw_delighted,
        }

        for k in EMOTION_KEYS:
            self._smoothed[k] = (
                (1.0 - self.alpha) * self._smoothed[k] + self.alpha * raw_scores[k]
            )

        rounded = {
            k: round(float(np.clip(self._smoothed[k], 0.0, 1.0)), 2) for k in EMOTION_KEYS
        }
        primary = max(EMOTION_KEYS, key=lambda k: self._smoothed[k])
        primary_score = rounded[primary]

        # Track sustained distress & recent distress memory for Eureka transitions
        on_screen = gaze.zone in ("screen_editor", "screen_terminal")
        distress_val = max(self._smoothed["confused"], self._smoothed["frustrated"])
        if distress_val >= 0.52 and not talking:
            self._last_distress_at = now
        if distress_val >= 0.55 and on_screen and not talking:
            if self._distress_since is None:
                self._distress_since = now
            sustained_sec = now - self._distress_since
        else:
            self._distress_since = None
            sustained_sec = 0.0

        fusion_status, fusion_level = self._evaluate_fusion(
            primary,
            primary_score,
            gaze,
            body_res,
            talking,
            sustained_sec,
            eureka_transition,
        )

        gesture_conf_bonus = 0.06 if body_res.primary_gesture != "none" else 0.0
        confidence = float(
            np.clip(
                0.50 * gaze.confidence
                + 0.45 * min(1.0, 0.45 + primary_score * 0.65)
                + gesture_conf_bonus,
                0.40,
                0.99,
            )
        )

        return EmotionResult(
            primary_emotion=primary,
            primary_score=primary_score,
            confidence=round(confidence, 2),
            emotions=rounded,
            talking=talking,
            fusion_status=fusion_status,
            fusion_level=fusion_level,
            sustained_distress_sec=round(sustained_sec, 1),
            eureka_transition=eureka_transition,
        )

    @staticmethod
    def _evaluate_fusion(
        primary: str,
        score: float,
        gaze: GazeResult,
        body: BodyGestureResult,
        talking: bool,
        sustained_sec: float,
        eureka_transition: bool,
    ) -> tuple[str, str]:
        cue_tag = f" + {body.primary_gesture}" if body.primary_gesture != "none" else ""

        if talking:
            return ("SUPPRESSED — User is talking (pair/voice guard)", "muted")
        if gaze.zone == "looking_away":
            if primary in ("confused", "frustrated") and score >= 0.55:
                return (f"DEEP THOUGHT — {primary}{cue_tag} while looking away", "muted")
            return ("SUPPRESSED — Looking away from screen", "muted")
        if gaze.zone == "eyes_closed" or gaze.eyes_closed:
            if primary == "fatigued" or score >= 0.50:
                return (f"BREAK RECOMMENDATION — Fatigue{cue_tag} detected", "warn")
            return (f"RESTING — Eyes closed{cue_tag}", "ok")
        if primary in ("confused", "frustrated") and score >= 0.56:
            if sustained_sec >= 2.5:
                return (
                    f"TRIGGER NUDGE — Sustained {primary}{cue_tag} ({sustained_sec:.1f}s) on {gaze.zone}",
                    "alert",
                )
            return (
                f"MONITORING — {primary.capitalize()}{cue_tag} on {gaze.zone} ({sustained_sec:.1f}s)",
                "warn",
            )
        if primary == "fatigued" and score >= 0.60:
            return (f"BREAK RECOMMENDATION — Fatigue{cue_tag} detected", "warn")
        if primary == "surprised" and score >= 0.58:
            return (f"SPIKE — Surprise{cue_tag} on {gaze.zone}", "warn")
        if primary == "delighted" and score >= 0.56:
            if eureka_transition:
                return (f"EUREKA — Breakthrough after struggle{cue_tag}!", "positive")
            return (f"EUREKA — Positive resolution{cue_tag}", "positive")
        return (f"SILENCE ALL — Focused flow on {gaze.zone}", "ok")


def build_event_payload(
    emotion: EmotionResult,
    gaze: GazeResult,
    body: Optional[BodyGestureResult] = None,
) -> Dict[str, Any]:
    """Build the `/events` HTTP JSON payload aligned with Core `FacePayload` + rich multimodal fields."""
    b = body or BodyGestureResult()
    suppressed = emotion.talking or gaze.zone == "looking_away" or gaze.eyes_closed
    distress_score = (
        0.0
        if suppressed
        else round(
            max(
                float(emotion.emotions.get("confused", 0.0)),
                float(emotion.emotions.get("frustrated", 0.0)),
            ),
            2,
        )
    )
    eyes_shut = gaze.eyes_closed and (
        gaze.closure_duration_sec >= 0.18 or gaze.zone == "eyes_closed"
    )
    if eyes_shut or emotion.primary_emotion == "fatigued":
        contract_state = "fatigued"
        payload_score = round(float(emotion.emotions.get("fatigued", 0.85)), 2)
    elif distress_score >= 0.56:
        contract_state = "confused"
        payload_score = distress_score
    else:
        contract_state = "calm"
        payload_score = distress_score

    return {
        "source": "face",
        "type": "face_update",
        "timestamp": int(time.time() * 1000),
        "state": contract_state,
        "score": payload_score,
        "distress_score": distress_score,
        "confidence": emotion.confidence,
        "primary_emotion": emotion.primary_emotion,
        "primary_score": emotion.primary_score,
        "emotions": dict(emotion.emotions),
        "fusion_status": emotion.fusion_status,
        "fusion_level": emotion.fusion_level,
        "gaze": {
            "zone": gaze.zone,
            "screen_x_px": gaze.screen_x_px,
            "screen_y_px": gaze.screen_y_px,
            "norm_x": gaze.gaze_x,
            "norm_y": gaze.gaze_y,
            "eyeball_pitch": gaze.eyeball_pitch,
            "eyeball_yaw": gaze.eyeball_yaw,
            "eye_origin_cm": list(gaze.eye_origin_cm),
            "fixation_ms": gaze.fixation_ms,
            "iris_ratio_x": gaze.iris_ratio_x,
            "iris_ratio_y": gaze.iris_ratio_y,
            "eyes_closed": gaze.eyes_closed,
            "blinks_per_min": gaze.blinks_per_min,
            "confidence": gaze.confidence,
        },
        "head_pose": {
            "pitch": gaze.head_pitch,
            "yaw": gaze.head_yaw,
            "roll": gaze.head_roll,
        },
        "body": {
            "primary_gesture": b.primary_gesture,
            "active_gestures": list(b.active_gestures),
            "posture": b.posture,
            "lean_ratio": b.lean_ratio,
            "hands_detected": b.hands_detected,
            "mouth_occluded": b.mouth_occluded,
        },
        "talking": emotion.talking,
    }
