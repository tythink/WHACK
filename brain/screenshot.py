"""Screenshot capture and preprocessing utilities for multimodal diagnosis."""

import base64
import io
import os
import subprocess
import tempfile
from typing import Optional, Tuple, Dict, Any
from PIL import Image

def capture_system_screenshot(max_dimension: int = 1280, quality: int = 80) -> Optional[Dict[str, Any]]:
    """Capture system screen on macOS using screencapture and return compressed base64."""
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
        if result.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return None

        return process_image_file(tmp_path, max_dimension=max_dimension, quality=quality)
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def process_image_bytes(image_bytes: bytes, max_dimension: int = 1280, quality: int = 80) -> Dict[str, Any]:
    """Resize and compress raw image bytes into optimized JPEG base64."""
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("RGB")

    width, height = image.size
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
        "size_bytes": len(compressed_bytes),
    }

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
