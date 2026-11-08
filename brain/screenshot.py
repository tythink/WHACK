"""Screenshot capture, cross-platform display capture, and spatial normalization utilities."""

import base64
import io
import math
import os
import platform
import subprocess
import sys
import tempfile
from typing import Optional, Tuple, Dict, Any, List, Union
from PIL import Image

# Distance threshold in normalized 0-1000 scale.
# Why 200: On a normalized 1000x1000 canvas, 200 units represents 20% of the display.
# In a typical mirrored single-screen layout (e.g., 1080p), 200 units (~384px horizontal / ~216px vertical)
# corresponds to the boundary separating functional regions (e.g. active code editor split vs integrated terminal/dock).
SPATIAL_DISTANCE_THRESHOLD = 200.0

def capture_system_screenshot(max_dimension: int = 1280, quality: int = 80) -> Optional[Dict[str, Any]]:
    """Capture system screen cross-platform using Pillow ImageGrab (Windows & macOS).
    
    On Windows (single display mirrored to projector), uses native GDI capture with DPI awareness.
    On macOS, uses ImageGrab with fallback to screencapture if needed.
    """
    # 1. On Windows, ensure process is DPI aware so screenshot matches 1:1 physical coordinate space
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # 2. Try Pillow ImageGrab (cross-platform, native on Windows and modern macOS)
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        if img:
            return process_pil_image(img, max_dimension=max_dimension, quality=quality)
    except Exception:
        pass

    # 3. macOS fallback to screencapture if ImageGrab failed or raised
    if sys.platform == "darwin":
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # -x: mute sound, -C: capture cursor
            result = subprocess.run(
                ["screencapture", "-x", "-C", tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
            )
            if result.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                return process_image_file(tmp_path, max_dimension=max_dimension, quality=quality)
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    return None

def process_pil_image(image: Image.Image, max_dimension: int = 1280, quality: int = 80) -> Dict[str, Any]:
    """Resize and compress PIL Image into optimized JPEG base64 while tracking original dimensions."""
    image = image.convert("RGB")
    raw_width, raw_height = image.size
    width, height = raw_width, raw_height

    if max(width, height) > max_dimension:
        ratio = max_dimension / max(width, height)
        new_size = (int(width * ratio), int(height * ratio))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        width, height = new_size

    out_buffer = io.BytesIO()
    image.save(out_buffer, format="JPEG", quality=quality, optimize=True)
    compressed_bytes = out_buffer.getvalue()

    b64_str = base64.b64encode(compressed_bytes).decode("utf-8")
    return {
        "base64": b64_str,
        "mime_type": "image/jpeg",
        "width": width,
        "height": height,
        "raw_width": raw_width,
        "raw_height": raw_height,
        "size_bytes": len(compressed_bytes),
    }

def process_image_bytes(image_bytes: bytes, max_dimension: int = 1280, quality: int = 80) -> Dict[str, Any]:
    """Resize and compress raw image bytes into optimized JPEG base64."""
    image = Image.open(io.BytesIO(image_bytes))
    return process_pil_image(image, max_dimension=max_dimension, quality=quality)

def process_image_file(file_path: str, max_dimension: int = 1280, quality: int = 80) -> Dict[str, Any]:
    """Load and process an image file from disk."""
    with open(file_path, "rb") as f:
        return process_image_bytes(f.read(), max_dimension=max_dimension, quality=quality)

def parse_incoming_screenshot(raw_screenshot: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse incoming base64 string (including data URL prefix) or capture if None."""
    if not raw_screenshot:
        return capture_system_screenshot()

    clean_b64 = raw_screenshot.strip()
    if "," in clean_b64 and clean_b64.startswith("data:"):
        clean_b64 = clean_b64.split(",", 1)[1]

    try:
        data = base64.b64decode(clean_b64)
        return process_image_bytes(data)
    except Exception:
        return None

def normalize_spatial_point(
    point: Optional[Union[List[Union[int, float]], Tuple[Union[int, float], ...]]],
    img_width: int,
    img_height: int,
) -> Optional[List[int]]:
    """Convert raw [x, y] screen pixel point into Gemini's native 0-1000 normalized [y, x] scale.
    
    Coordinates are normalized relative to actual screenshot image dimensions:
    - norm_y = round((y / img_height) * 1000)
    - norm_x = round((x / img_width) * 1000)
    Returns: [norm_y, norm_x] clamped to [0, 1000].
    """
    if not point or len(point) < 2 or img_width <= 0 or img_height <= 0:
        return None
    try:
        x, y = float(point[0]), float(point[1])
        norm_y = max(0, min(1000, int(round((y / img_height) * 1000))))
        norm_x = max(0, min(1000, int(round((x / img_width) * 1000))))
        return [norm_y, norm_x]
    except Exception:
        return None

def normalize_request_spatial_points(
    req: Any,
    actual_width: Optional[int] = None,
    actual_height: Optional[int] = None,
) -> Dict[str, List[int]]:
    """Extract and normalize available spatial points ('gaze', 'cursor', 'caret') to Gemini's [y, x] 0-1000 scale.
    
    Prefers actual screenshot image dimensions; falls back to reported screenWidth/screenHeight if image dimensions missing.
    """
    ref_w = actual_width or getattr(req, "screenWidth", None) or 0
    ref_h = actual_height or getattr(req, "screenHeight", None) or 0
    points: Dict[str, List[int]] = {}

    if ref_w > 0 and ref_h > 0:
        gaze = getattr(req, "gazePoint", None)
        if gaze is not None:
            pt = normalize_spatial_point(gaze, ref_w, ref_h)
            if pt is not None:
                points["gaze"] = pt

        cursor = getattr(req, "cursorScreenPos", None)
        if cursor is not None:
            pt = normalize_spatial_point(cursor, ref_w, ref_h)
            if pt is not None:
                points["cursor"] = pt

        caret = getattr(req, "caretScreenPos", None)
        if caret is not None:
            pt = normalize_spatial_point(caret, ref_w, ref_h)
            if pt is not None:
                points["caret"] = pt

    return points

def evaluate_spatial_coherence(
    normalized_points: Dict[str, List[int]],
    model_explanation: Optional[str] = None,
    threshold: float = SPATIAL_DISTANCE_THRESHOLD,
) -> Optional[str]:
    """Assess whether gaze agreed with cursor/caret or diverged, noting if diagnosis leaned on it.
    
    Threshold (default 200) represents ~20% of screen diagonal/width, typically separating editor splits from terminals.
    """
    if "gaze" not in normalized_points:
        return None

    gaze_y, gaze_x = normalized_points["gaze"]
    other_points: List[Tuple[str, List[int]]] = []
    if "cursor" in normalized_points:
        other_points.append(("cursor", normalized_points["cursor"]))
    if "caret" in normalized_points:
        other_points.append(("caret", normalized_points["caret"]))

    if not other_points:
        return (
            f"Gaze point provided at normalized [y={gaze_y}, x={gaze_x}], "
            "but neither cursorScreenPos nor caretScreenPos was provided to compare spatial coherence."
        )

    distances: List[Tuple[str, float]] = []
    for label, (py, px) in other_points:
        dist = math.hypot(gaze_y - py, gaze_x - px)
        distances.append((label, dist))

    min_label, min_dist = min(distances, key=lambda d: d[1])

    if min_dist <= threshold:
        return (
            f"Gaze and {min_label} are close together (distance: {min_dist:.1f} <= {threshold:.0f} "
            f"normalized units); gaze added no distinguishable spatial information for this call."
        )
    else:
        dist_strs = [f"{lbl}={d:.1f}" for lbl, d in distances]
        explanation_lower = (model_explanation or "").lower()
        leaned_on_gaze = any(
            term in explanation_lower
            for term in ["gaze", "looking at", "looked at", "user focus", "glance", "gazing", "visual attention"]
        )
        outcome = (
            "model diagnosis explicitly referenced visual gaze focus"
            if leaned_on_gaze
            else "model adhered to diff/error without relying on divergent gaze"
        )
        return (
            f"Gaze disagreed with {', '.join([lbl for lbl, _ in distances])} "
            f"(distances: {', '.join(dist_strs)}, threshold: {threshold:.0f} units; "
            f"chosen as ~20% of screen separating functional editor/terminal panes). "
            f"Outcome: {outcome}."
        )

def get_real_mouse_position() -> Tuple[int, int]:
    """Read real mouse cursor position on Windows (GetCursorPos), macOS (CoreGraphics), or via pyautogui."""
    # 1. Try pyautogui if installed
    try:
        import pyautogui
        pos = pyautogui.position()
        return int(pos[0]), int(pos[1])
    except Exception:
        pass

    # 2. Windows via ctypes GetCursorPos
    if sys.platform.startswith("win"):
        try:
            import ctypes
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return int(pt.x), int(pt.y)
        except Exception:
            pass

    # 3. macOS via CoreGraphics
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util
            cg_path = ctypes.util.find_library("CoreGraphics")
            if cg_path:
                cg = ctypes.cdll.LoadLibrary(cg_path)
                class CGPoint(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
                cg.CGEventCreate.restype = ctypes.c_void_p
                cg.CGEventGetLocation.restype = CGPoint
                cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
                ev = cg.CGEventCreate(None)
                if ev:
                    pt = cg.CGEventGetLocation(ev)
                    return int(round(pt.x)), int(round(pt.y))
        except Exception:
            pass

    return (0, 0)
