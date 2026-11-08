"""MediaPipe Face, Pose, and Hand model loaders + lighting normalization for WHACK Role 1."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODELS_DIR = Path(__file__).resolve().parent / "models"

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

DEFAULT_MODEL_PATH = MODELS_DIR / "face_landmarker.task"
DEFAULT_POSE_MODEL_PATH = MODELS_DIR / "pose_landmarker_lite.task"
DEFAULT_HAND_MODEL_PATH = MODELS_DIR / "hand_landmarker.task"

_CLAHE = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))


def _download_if_missing(url: str, dest: Path, min_bytes: int = 500_000) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > min_bytes:
        return dest

    tmp_path = dest.with_suffix(dest.suffix + ".part")
    print(f"[face] Downloading {dest.name} ...")
    urllib.request.urlretrieve(url, str(tmp_path))
    os.replace(tmp_path, dest)
    print(f"[face] {dest.name} ready ({dest.stat().st_size / (1024 * 1024):.2f} MB).")
    return dest


def ensure_face_landmarker_model(model_path: Path = DEFAULT_MODEL_PATH) -> Path:
    """Ensure `face_landmarker.task` exists locally under `face/models/`."""
    return _download_if_missing(FACE_MODEL_URL, model_path, min_bytes=1_000_000)


def ensure_pose_landmarker_model(model_path: Path = DEFAULT_POSE_MODEL_PATH) -> Path:
    """Ensure `pose_landmarker_lite.task` exists locally under `face/models/`."""
    return _download_if_missing(POSE_MODEL_URL, model_path, min_bytes=1_000_000)


def ensure_hand_landmarker_model(model_path: Path = DEFAULT_HAND_MODEL_PATH) -> Path:
    """Ensure `hand_landmarker.task` exists locally under `face/models/`."""
    return _download_if_missing(HAND_MODEL_URL, model_path, min_bytes=1_000_000)


def _running_mode_enum(running_mode: str) -> vision.RunningMode:
    return (
        vision.RunningMode.VIDEO
        if running_mode.upper() == "VIDEO"
        else vision.RunningMode.IMAGE
    )


def create_face_landmarker(
    running_mode: str = "VIDEO",
    min_detection_confidence: float = 0.5,
    min_presence_confidence: float = 0.5,
    min_tracking_confidence: float = 0.5,
    model_path: Path = DEFAULT_MODEL_PATH,
) -> vision.FaceLandmarker:
    """Create a configured MediaPipe FaceLandmarker with blendshapes + 3D pose."""
    resolved_model = ensure_face_landmarker_model(model_path)
    base_options = mp_python.BaseOptions(model_asset_buffer=resolved_model.read_bytes())
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=_running_mode_enum(running_mode),
        num_faces=1,
        min_face_detection_confidence=min_detection_confidence,
        min_face_presence_confidence=min_presence_confidence,
        min_tracking_confidence=min_tracking_confidence,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    return vision.FaceLandmarker.create_from_options(options)


def create_pose_landmarker(
    running_mode: str = "VIDEO",
    min_detection_confidence: float = 0.5,
    min_presence_confidence: float = 0.5,
    min_tracking_confidence: float = 0.5,
    model_path: Path = DEFAULT_POSE_MODEL_PATH,
) -> vision.PoseLandmarker:
    """Create a lightweight MediaPipe PoseLandmarker for shoulders, arms, and posture."""
    resolved_model = ensure_pose_landmarker_model(model_path)
    base_options = mp_python.BaseOptions(model_asset_buffer=resolved_model.read_bytes())
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=_running_mode_enum(running_mode),
        num_poses=1,
        min_pose_detection_confidence=min_detection_confidence,
        min_pose_presence_confidence=min_presence_confidence,
        min_tracking_confidence=min_tracking_confidence,
        output_segmentation_masks=False,
    )
    return vision.PoseLandmarker.create_from_options(options)


def create_hand_landmarker(
    running_mode: str = "VIDEO",
    num_hands: int = 2,
    min_detection_confidence: float = 0.48,
    min_presence_confidence: float = 0.48,
    min_tracking_confidence: float = 0.48,
    model_path: Path = DEFAULT_HAND_MODEL_PATH,
) -> vision.HandLandmarker:
    """Create a MediaPipe HandLandmarker for head-scratch, chin-rest, and facepalm cues."""
    resolved_model = ensure_hand_landmarker_model(model_path)
    base_options = mp_python.BaseOptions(model_asset_buffer=resolved_model.read_bytes())
    options = vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=_running_mode_enum(running_mode),
        num_hands=num_hands,
        min_hand_detection_confidence=min_detection_confidence,
        min_hand_presence_confidence=min_presence_confidence,
        min_tracking_confidence=min_tracking_confidence,
    )
    return vision.HandLandmarker.create_from_options(options)


def normalize_lighting_bgr(frame_bgr: np.ndarray) -> np.ndarray:
    """Adaptive LAB-space CLAHE + mild gamma normalization for dim or glare lighting."""
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    mean_l = float(np.mean(l_chan))
    std_l = float(np.std(l_chan))

    # Skip work when frame is already well-lit and well-contrasted
    if 95.0 <= mean_l <= 175.0 and std_l >= 40.0:
        return frame_bgr

    l_eq = _CLAHE.apply(l_chan)
    if mean_l < 85.0:
        # Lift shadows gently in dim rooms
        gamma = max(0.68, mean_l / 120.0)
        lut = np.array(
            [((i / 255.0) ** gamma) * 255.0 for i in range(256)], dtype=np.uint8
        )
        l_eq = cv2.LUT(l_eq, lut)
    elif mean_l > 185.0:
        # Tame harsh monitor glare
        lut = np.array(
            [((i / 255.0) ** 1.22) * 255.0 for i in range(256)], dtype=np.uint8
        )
        l_eq = cv2.LUT(l_eq, lut)

    l_blended = cv2.addWeighted(l_eq, 0.65, l_chan, 0.35, 0)
    merged = cv2.merge((l_blended, a_chan, b_chan))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def bgr_to_mp_image(frame_bgr: np.ndarray) -> mp.Image:
    """Convert an OpenCV BGR uint8 frame to a MediaPipe RGB Image."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


if __name__ == "__main__":
    f_lm = create_face_landmarker(running_mode="IMAGE")
    p_lm = create_pose_landmarker(running_mode="IMAGE")
    h_lm = create_hand_landmarker(running_mode="IMAGE")
    f_lm.close()
    p_lm.close()
    h_lm.close()
    print("Verified FaceLandmarker, PoseLandmarker, and HandLandmarker models successfully.")
