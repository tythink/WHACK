"""Neutral-face, upper-body posture, and 9-point tilted-screen gaze calibration for WHACK Role 1."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "calibration_profile.json"


DEFAULT_BASELINE: Dict[str, float] = {
    # Blendshape priors (neutral resting values)
    "brow_down": 0.05,
    "brow_inner_up": 0.04,
    "brow_outer_up": 0.04,
    "eye_squint": 0.08,
    "eye_wide": 0.03,
    "eye_blink": 0.06,
    "mouth_smile": 0.03,
    "mouth_press": 0.04,
    "mouth_frown": 0.03,
    "mouth_stretch": 0.03,
    "mouth_pucker": 0.04,
    "mouth_roll": 0.04,
    "cheek_squint": 0.04,
    "nose_sneer": 0.03,
    "jaw_open": 0.03,
    # Neural gaze blendshape priors (used only as uncalibrated fallback before 9-pt G)
    "eye_look_h": 0.0,
    "eye_look_v": 0.0,
    "ray_u": 0.0,
    "ray_v": 0.0,
    "hit_x_cm": 0.0,
    "hit_y_cm": 11.0,
    # Geometric priors
    "ear": 0.27,
    "brow_eye_ratio": 0.28,
    "inner_brow_span": 0.24,
    "iris_ratio_x": 0.50,
    "iris_offset_y": 0.01,
    # Head pose priors (degrees)
    "head_pitch": 0.0,
    "head_yaw": 0.0,
    "head_roll": 0.0,
    # Upper-body & seating distance priors
    "face_width": 0.22,
    "chin_y": 0.72,
    "shoulder_y": 0.88,
    "shoulder_span": 0.52,
    "neck_ratio": 0.75,
}


@dataclass
class CalibrationProfile:
    """Stores per-user neutral facial/posture baselines and 9-point tilted-screen gaze regression."""

    calibrated: bool = False
    sample_count: int = 0
    duration_sec: float = 0.0
    updated_at: float = 0.0
    baseline: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BASELINE))
    spread: Dict[str, float] = field(default_factory=dict)
    gaze_calibrated: bool = False
    gaze_model: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, fallback: Optional[float] = None) -> float:
        if key in self.baseline:
            return float(self.baseline[key])
        if fallback is not None:
            return float(fallback)
        return float(DEFAULT_BASELINE.get(key, 0.0))

    def save(self, path: Path = DEFAULT_PROFILE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = DEFAULT_PROFILE_PATH) -> "CalibrationProfile":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            merged_baseline = dict(DEFAULT_BASELINE)
            merged_baseline.update(raw.get("baseline", {}))
            gaze_model = dict(raw.get("gaze_model", {}))
            is_gaze_cal = bool(raw.get("gaze_calibrated", False)) and gaze_model.get("version") == 4
            return cls(
                calibrated=bool(raw.get("calibrated", False)),
                sample_count=int(raw.get("sample_count", 0)),
                duration_sec=float(raw.get("duration_sec", 0.0)),
                updated_at=float(raw.get("updated_at", 0.0)),
                baseline=merged_baseline,
                spread=dict(raw.get("spread", {})),
                gaze_calibrated=is_gaze_cal,
                gaze_model=gaze_model if is_gaze_cal else {},
            )
        except Exception:
            return cls()


class BaselineCalibrator:
    """Collects neutral face & posture feature dictionaries over a time window.

    Includes a 1.2s settle grace period after clicking the button so looking at the button
    at the start of calibration never biases the neutral baseline.
    """

    SETTLE_DELAY_SEC = 1.2

    def __init__(self, profile: Optional[CalibrationProfile] = None) -> None:
        self.profile: CalibrationProfile = profile or CalibrationProfile()
        self._active: bool = False
        self._start_time: float = 0.0
        self._target_duration: float = 5.0
        self._samples: List[Dict[str, float]] = []

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def target_duration(self) -> float:
        return self._target_duration

    def start(self, duration_sec: float = 5.0, skip_settle: bool = False) -> None:
        self._target_duration = max(1.0, float(duration_sec))
        self._settle_sec = 0.0 if skip_settle else min(self.SETTLE_DELAY_SEC, self._target_duration * 0.25)
        self._start_time = time.monotonic()
        self._samples.clear()
        self._active = True

    def cancel(self) -> None:
        self._active = False
        self._samples.clear()

    def reset_to_defaults(self) -> CalibrationProfile:
        self.cancel()
        self.profile = CalibrationProfile()
        return self.profile

    def progress(self) -> tuple[float, float]:
        """Return `(progress_0_to_1, remaining_seconds)`."""
        if not self._active:
            return (1.0 if self.profile.calibrated else 0.0, 0.0)
        elapsed = time.monotonic() - self._start_time
        ratio = min(1.0, max(0.0, elapsed / self._target_duration))
        remaining = max(0.0, self._target_duration - elapsed)
        return (ratio, remaining)

    def add_sample(self, features: Dict[str, float]) -> bool:
        """Record one frame of features. Returns True on the frame calibration completes."""
        if not self._active:
            return False

        elapsed = time.monotonic() - self._start_time
        # Skip initial settle window (moving mouse/eyes from button to screen center) & blinks
        if (
            elapsed >= getattr(self, "_settle_sec", 0.0)
            and features.get("eye_blink", 0.0) < 0.45
            and features.get("ear", 0.25) > 0.16
        ):
            self._samples.append(dict(features))

        if elapsed >= self._target_duration:
            self._finalize(elapsed)
            return True
        return False

    def _finalize(self, elapsed: float) -> None:
        self._active = False
        if len(self._samples) < 5:
            self._samples.clear()
            return

        new_baseline = dict(DEFAULT_BASELINE)
        new_spread: Dict[str, float] = {}

        keys = set().union(*(s.keys() for s in self._samples))
        for key in keys:
            vals = np.array(
                [s[key] for s in self._samples if key in s and np.isfinite(s[key])],
                dtype=np.float64,
            )
            if vals.size == 0:
                continue
            vals.sort()
            trim = int(vals.size * 0.15)
            core = vals[trim : vals.size - trim] if vals.size - 2 * trim >= 3 else vals
            new_baseline[key] = float(np.mean(core))
            new_spread[key] = float(max(np.std(core), 1e-4))

        # Preserve any existing 9-point tilted-screen gaze regression completely untouched!
        self.profile = CalibrationProfile(
            calibrated=True,
            sample_count=len(self._samples),
            duration_sec=round(elapsed, 2),
            updated_at=time.time(),
            baseline=new_baseline,
            spread=new_spread,
            gaze_calibrated=self.profile.gaze_calibrated,
            gaze_model=dict(self.profile.gaze_model),
        )
        self._samples.clear()
