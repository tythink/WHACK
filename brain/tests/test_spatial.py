"""Unit tests for cross-platform spatial normalization, raw_width/raw_height scaling, and trust hierarchy."""

import asyncio
import base64
import io
import pytest
from PIL import Image

from brain.agent import DiagnosticEngine
from brain.models import DiagnoseRequest
from brain.screenshot import (
    draw_spatial_markers_on_screenshot,
    evaluate_spatial_coherence,
    get_real_mouse_position,
    normalize_request_spatial_points,
    normalize_spatial_point,
)


def test_normalize_spatial_point():
    point = [960, 540]
    norm = normalize_spatial_point(point, img_width=1920, img_height=1080)
    assert norm == [500, 500]

    assert normalize_spatial_point([0, 0], 1920, 1080) == [0, 0]
    assert normalize_spatial_point([1920, 1080], 1920, 1080) == [1000, 1000]
    assert normalize_spatial_point([-50, 1500], 1920, 1080) == [1000, 0]


def test_normalize_against_actual_image_dimensions():
    norm = normalize_spatial_point([1920, 1080], img_width=3840, img_height=2160)
    assert norm == [500, 500]


def test_diagnose_request_spatial_fields_and_aliases():
    req_data = {
        "terminalOutput": "TypeError",
        "gazePoint": [100, 200],
        "cursorScreenPos": [150, 250],
        "caretScreenPos": [120, 220],
        "screenWidth": 1920,
        "screenHeight": 1080,
    }
    req = DiagnoseRequest(**req_data)
    assert req.gazePoint == [100.0, 200.0]
    assert req.cursorScreenPos == [150.0, 250.0]
    assert req.caretScreenPos == [120.0, 220.0]
    assert req.screenWidth == 1920
    assert req.screenHeight == 1080

    norm_pts = normalize_request_spatial_points(req, actual_width=1920, actual_height=1080)
    assert norm_pts["gaze"] == [185, 52]

    # Alternate names (eyeGaze, mouseCursor, keyboardCursor) including {x, y} dicts
    alt_req = DiagnoseRequest(
        eyeGaze={"x": 960, "y": 540},
        mouseCursor=[480, 270],
        keyboardCursor={"x": 1920, "y": 1080},
    )
    alt_norm = normalize_request_spatial_points(alt_req, actual_width=1920, actual_height=1080)
    assert alt_norm["gaze"] == [500, 500]
    assert alt_norm["cursor"] == [250, 250]
    assert alt_norm["caret"] == [1000, 1000]


def test_engine_normalizes_against_raw_dimensions_not_resized_1280x720(monkeypatch: pytest.MonkeyPatch):
    """Ensure 1920x1080 screenshots resized to 1280x720 normalize coordinates against raw 1920x1080!"""
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    img = Image.new("RGB", (1920, 1080), color=(20, 25, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

    req = DiagnoseRequest(
        hintLevel="hint",
        screenshot=b64_img,
        gazePoint=[960, 540],
        cursorScreenPos=[1920, 1080],
        caretScreenPos=[480, 270],
        screenWidth=1920,
        screenHeight=1080,
    )

    eng = DiagnosticEngine()
    diag = asyncio.run(eng.diagnose(req))
    assert diag.trace is not None
    # Resized payload dimensions are 1280x720, while spatial_points MUST be normalized against raw 1920x1080!
    assert diag.trace.screenshot_dimensions == (1280, 720)
    assert diag.trace.spatial_points == {
        "gaze": [500, 500],
        "cursor": [1000, 1000],
        "caret": [250, 250],
    }


def test_spatial_coherence_evaluation():
    points_close = {
        "caret": [400, 300],
        "cursor": [410, 310],
        "gaze": [420, 320],
    }
    note_close = evaluate_spatial_coherence(points_close, model_explanation="Looking at index calculation.")
    assert "close together" in note_close
    assert "no distinguishable spatial information" in note_close

    points_far = {
        "caret": [200, 200],
        "cursor": [210, 210],
        "gaze": [800, 800],
    }
    note_far = evaluate_spatial_coherence(points_far, model_explanation="The slice bounds are wrong in utils.py.")
    assert "disagreed" in note_far
    assert "adhered to diff/error" in note_far


def test_get_real_mouse_position_windows_success(monkeypatch):
    import sys
    import ctypes
    monkeypatch.setattr(sys, "platform", "win32")

    def mock_get_cursor_pos(byref_pt):
        pt = byref_pt._obj
        pt.x = 800
        pt.y = 600
        return 1

    monkeypatch.setattr(ctypes.windll.user32, "GetCursorPos", mock_get_cursor_pos)

    pos = get_real_mouse_position()
    assert pos == (800, 600)

    pos, debug_info = get_real_mouse_position(return_debug=True)
    assert pos == (800, 600)
    assert debug_info["raw_ret"] == 1
    assert debug_info["point_x"] == 800
    assert debug_info["point_y"] == 600
    assert "POINT(x=800, y=600)" in debug_info["point_struct"]


def test_get_real_mouse_position_windows_failure_raises(monkeypatch):
    import sys
    import ctypes
    monkeypatch.setattr(sys, "platform", "win32")

    def mock_get_cursor_pos_fail(byref_pt):
        return 0

    monkeypatch.setattr(ctypes.windll.user32, "GetCursorPos", mock_get_cursor_pos_fail)
    monkeypatch.setattr(ctypes, "GetLastError", lambda: 5)

    with pytest.raises(RuntimeError) as exc_info:
        get_real_mouse_position()

    err_msg = str(exc_info.value)
    assert "Windows GetCursorPos failed" in err_msg
    assert "return code=0" in err_msg
    assert "GetLastError=5" in err_msg


def test_draw_spatial_markers_on_screenshot(tmp_path):
    img = Image.new("RGB", (200, 200), color=(30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    out_file = tmp_path / "test_gaze.png"
    annotated_bytes, out_str = draw_spatial_markers_on_screenshot(
        raw_bytes,
        markers={"gaze": (100, 100), "cursor": (50, 50)},
        output_path=out_file,
    )
    assert len(annotated_bytes) > 0
    assert out_file.exists()
    assert out_file.stat().st_size > 0
