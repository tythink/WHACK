#!/usr/bin/env python3
"""Standalone scaling verification script for WHACK Brain.

Captures a real screenshot, queries the real system mouse position, normalizes the
coordinates to Gemini's 0-1000 [y, x] convention relative to the actual screenshot
pixel dimensions, draws a verification marker at that point, saves the image,
and prints both raw and normalized values.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Union
from PIL import Image, ImageDraw

# Ensure brain module is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.screenshot import get_real_mouse_position

Optional_Path = Optional[Union[str, Path]]


def verify_scaling(output_path: Optional_Path = None) -> dict:
    """Capture real screen, read cursor, compute normalized coordinates, draw marker and save."""
    dpi_aware = False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
            dpi_aware = True
        except Exception:
            dpi_aware = False

    ctypes_debug = None
    try:
        mouse_res = get_real_mouse_position(return_debug=True)
        (raw_x, raw_y), ctypes_debug = mouse_res
    except Exception as exc:
        print(f"\n[ERROR] Failed to query mouse position: {exc}\n")
        raise

    captured_img = None
    capture_source = "ImageGrab"
    try:
        from PIL import ImageGrab
        captured_img = ImageGrab.grab()
    except Exception as e:
        capture_source = f"ImageGrab failed ({e})"

    if captured_img is None and sys.platform == "darwin":
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_name = tmp.name
        try:
            res = subprocess.run(["screencapture", "-x", tmp_name], capture_output=True, timeout=3)
            if res.returncode == 0 and os.path.exists(tmp_name) and os.path.getsize(tmp_name) > 0:
                captured_img = Image.open(tmp_name).convert("RGB")
                capture_source = "screencapture (macOS CLI)"
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    if captured_img is None:
        raise RuntimeError(
            f"Real screen capture failed ({capture_source}). "
            "Run verify_scaling.py inside an active desktop display session."
        )

    captured_img = captured_img.convert("RGB")
    width, height = captured_img.size

    safe_raw_x = max(0, min(width - 1, raw_x))
    safe_raw_y = max(0, min(height - 1, raw_y))

    norm_y = max(0, min(1000, int(round((safe_raw_y / float(height)) * 1000.0))))
    norm_x = max(0, min(1000, int(round((safe_raw_x / float(width)) * 1000.0))))

    marker_x = int(round((norm_x / 1000.0) * float(width)))
    marker_y = int(round((norm_y / 1000.0) * float(height)))

    draw = ImageDraw.Draw(captured_img)
    radius = 24

    draw.ellipse(
        [(marker_x - radius, marker_y - radius), (marker_x + radius, marker_y + radius)],
        outline=(255, 50, 50),
        width=3,
    )
    draw.ellipse(
        [(marker_x - (radius - 4), marker_y - (radius - 4)), (marker_x + (radius - 4), marker_y + (radius - 4))],
        outline=(255, 230, 0),
        width=2,
    )
    draw.line([(marker_x - radius - 15, marker_y), (marker_x + radius + 15, marker_y)], fill=(255, 50, 50), width=2)
    draw.line([(marker_x, marker_y - radius - 15), (marker_x, marker_y + radius + 15)], fill=(255, 50, 50), width=2)
    draw.ellipse([(marker_x - 3, marker_y - 3), (marker_x + 3, marker_y + 3)], fill=(255, 255, 255), outline=(0, 0, 0))

    text_label = (
        f"Real Mouse: ({raw_x}, {raw_y})\n"
        f"Screenshot: {width}x{height}\n"
        f"Gemini [y, x]: [{norm_y}, {norm_x}]\n"
        f"Projected: ({marker_x}, {marker_y})"
    )

    box_x = marker_x + 35
    box_y = marker_y - 30
    if box_x + 220 > width:
        box_x = marker_x - 250
    if box_y + 80 > height:
        box_y = height - 90
    if box_y < 10:
        box_y = 10

    draw.rectangle([(box_x, box_y), (box_x + 230, box_y + 80)], fill=(0, 0, 0, 220), outline=(255, 230, 0), width=2)
    draw.text((box_x + 10, box_y + 8), text_label, fill=(255, 255, 255))

    if output_path is None:
        out_dir = SCRIPT_DIR / "cache"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "scaling_verification.png"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    captured_img.save(str(output_path), format="PNG")

    return {
        "platform": sys.platform,
        "dpi_aware": dpi_aware,
        "capture_source": capture_source,
        "raw_mouse": (raw_x, raw_y),
        "ctypes_return": ctypes_debug.get("raw_ret") if ctypes_debug else None,
        "point_struct": ctypes_debug.get("point_struct") if ctypes_debug else None,
        "ctypes_debug": ctypes_debug,
        "screenshot_dimensions": (width, height),
        "gemini_normalized_yx": [norm_y, norm_x],
        "projected_pixel_xy": (marker_x, marker_y),
        "output_path": str(output_path.resolve()),
    }


if __name__ == "__main__":
    res = verify_scaling()
    print("=" * 60)
    print("WHACK BRAIN - SCALING & SPATIAL VERIFICATION")
    print("=" * 60)
    print(f"Platform:              {res['platform']} (DPI Aware: {res['dpi_aware']})")
    print(f"Capture Source:        {res['capture_source']}")
    print(f"Screenshot Resolution: {res['screenshot_dimensions'][0]} x {res['screenshot_dimensions'][1]} px")
    print(f"Raw Mouse Position:    X={res['raw_mouse'][0]}, Y={res['raw_mouse'][1]}")
    if res.get("ctypes_return") is not None:
        print(f"ctypes Return Value:   {res['ctypes_return']}")
    if res.get("point_struct") is not None:
        print(f"POINT Struct Contents: {res['point_struct']}")
    print(f"Gemini Normalized:     [y, x] = {res['gemini_normalized_yx']} (range: 0-1000)")
    print(f"Projected Marker:      X={res['projected_pixel_xy'][0]}, Y={res['projected_pixel_xy'][1]} px")
    print(f"Output Marker Image:   {res['output_path']}")
    print("=" * 60)
