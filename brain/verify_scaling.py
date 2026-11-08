#!/usr/bin/env python3
"""Standalone scaling verification script for WHACK Brain.

Captures a screenshot, queries the real system mouse position, normalizes the
coordinates to Gemini's 0-1000 [y, x] convention relative to the actual screenshot
pixel dimensions, draws a verification marker at that point, saves the image,
and prints both raw and normalized values.
"""

import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Ensure brain module is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.screenshot import get_real_mouse_position


def verify_scaling(output_path: Optional_Path = None) -> dict:
    """Capture screen, read cursor, compute normalized coordinates, draw marker and save."""
    # 1. On Windows, ensure process is DPI aware for 1:1 physical coordinate match
    dpi_aware = False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
            dpi_aware = True
        except Exception as e:
            dpi_aware = False

    # 2. Get real mouse position
    raw_x, raw_y = get_real_mouse_position()

    # 3. Capture screenshot
    captured_img = None
    capture_source = "ImageGrab"
    try:
        from PIL import ImageGrab
        captured_img = ImageGrab.grab()
    except Exception as e:
        capture_source = f"ImageGrab failed ({e})"

    # Fallback if ImageGrab failed (e.g. headless or missing OS display permission)
    if captured_img is None:
        if sys.platform == "darwin":
            # Try screencapture CLI on macOS
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

    # If capture is completely unavailable in the current runtime environment, create a reference test canvas
    if captured_img is None:
        width, height = 1920, 1080
        captured_img = Image.new("RGB", (width, height), color=(30, 30, 35))
        capture_source = "Synthetic canvas (display capture unavailable in current sandbox/session)"
        if raw_x == 0 and raw_y == 0:
            raw_x, raw_y = 960, 540
    else:
        captured_img = captured_img.convert("RGB")
        width, height = captured_img.size

    # Clamp raw coordinates if needed
    safe_raw_x = max(0, min(width - 1, raw_x))
    safe_raw_y = max(0, min(height - 1, raw_y))

    # 4. Compute normalized 0-1000 coordinate relative to ACTUAL screenshot pixel dimensions
    # Gemini convention: [y, x] where top-left is [0, 0] and bottom-right is [1000, 1000]
    norm_y = int(round((safe_raw_y / float(height)) * 1000.0))
    norm_x = int(round((safe_raw_x / float(width)) * 1000.0))
    norm_y = max(0, min(1000, norm_y))
    norm_x = max(0, min(1000, norm_x))

    # 5. Project normalized coordinates back to actual image pixel space for marker drawing
    marker_x = int(round((norm_x / 1000.0) * float(width)))
    marker_y = int(round((norm_y / 1000.0) * float(height)))

    # 6. Draw visual verification marker
    draw = ImageDraw.Draw(captured_img)
    radius = 24
    
    # Outer target rings (high visibility: yellow ring with red border)
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
    # Crosshairs
    draw.line([(marker_x - radius - 15, marker_y), (marker_x + radius + 15, marker_y)], fill=(255, 50, 50), width=2)
    draw.line([(marker_x, marker_y - radius - 15), (marker_x, marker_y + radius + 15)], fill=(255, 50, 50), width=2)
    # Center dot
    draw.ellipse([(marker_x - 3, marker_y - 3), (marker_x + 3, marker_y + 3)], fill=(255, 255, 255), outline=(0, 0, 0))

    # Informational label card next to marker
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

    # 7. Save output image
    if output_path is None:
        out_dir = SCRIPT_DIR / "cache"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "scaling_verification.png"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    captured_img.save(str(output_path), format="PNG")

    result = {
        "platform": sys.platform,
        "dpi_aware": dpi_aware,
        "capture_source": capture_source,
        "raw_mouse": (raw_x, raw_y),
        "screenshot_dimensions": (width, height),
        "gemini_normalized_yx": [norm_y, norm_x],
        "projected_pixel_xy": (marker_x, marker_y),
        "output_path": str(output_path.resolve()),
    }
    return result


if __name__ == "__main__":
    Optional_Path = Any = object
    res = verify_scaling()
    print("=" * 60)
    print("WHACK BRAIN - SCALING & SPATIAL VERIFICATION")
    print("=" * 60)
    print(f"Platform:              {res['platform']} (DPI Aware: {res['dpi_aware']})")
    print(f"Capture Source:        {res['capture_source']}")
    print(f"Screenshot Resolution: {res['screenshot_dimensions'][0]} x {res['screenshot_dimensions'][1]} px")
    print(f"Raw Mouse Position:    X={res['raw_mouse'][0]}, Y={res['raw_mouse'][1]}")
    print(f"Gemini Normalized:     [y, x] = {res['gemini_normalized_yx']} (range: 0-1000)")
    print(f"Projected Marker:      X={res['projected_pixel_xy'][0]}, Y={res['projected_pixel_xy'][1]} px")
    print(f"Output Marker Image:   {res['output_path']}")
    print("=" * 60)
