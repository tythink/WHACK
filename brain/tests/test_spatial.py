"""Unit tests for cross-platform spatial normalization and trust hierarchy."""

import pytest
from brain.models import DiagnoseRequest
from brain.screenshot import (
    normalize_spatial_point,
    normalize_request_spatial_points,
    evaluate_spatial_coherence,
    get_real_mouse_position,
    SPATIAL_DISTANCE_THRESHOLD,
)


def test_normalize_spatial_point():
    # 1920x1080 screen
    # Point at (960, 540) is exactly center -> [500, 500] in Gemini [y, x]
    point = [960, 540]
    norm = normalize_spatial_point(point, img_width=1920, img_height=1080)
    assert norm == [500, 500]

    # Point at top-left (0, 0) -> [0, 0]
    assert normalize_spatial_point([0, 0], 1920, 1080) == [0, 0]

    # Point at bottom-right (1920, 1080) -> [1000, 1000]
    assert normalize_spatial_point([1920, 1080], 1920, 1080) == [1000, 1000]

    # Out of bounds clamped: x=-50 -> 0, y=1500 -> 1000. Result [y, x] is [1000, 0]
    assert normalize_spatial_point([-50, 1500], 1920, 1080) == [1000, 0]


def test_normalize_against_actual_image_dimensions():
    # Demonstrating DPI scaling mismatch: reported screen is 1920x1080, but screenshot image is 3840x2160 (Retina/HiDPI 200%)
    # Gaze reported at raw mouse pos [1920, 1080] on a 2x scaled image:
    norm = normalize_spatial_point([1920, 1080], img_width=3840, img_height=2160)
    assert norm == [500, 500]


def test_diagnose_request_spatial_fields():
    # Test that DiagnoseRequest accepts optional spatial fields in both camelCase and snake_case
    req_data = {
        "terminalOutput": "TypeError",
        "gazePoint": [100, 200],
        "cursorScreenPos": [150, 250],
        "caretScreenPos": [120, 220],
        "screenWidth": 1920,
        "screenHeight": 1080,
    }
    req = DiagnoseRequest(**req_data)
    assert req.gazePoint == [100, 200]
    assert req.cursorScreenPos == [150, 250]
    assert req.caretScreenPos == [120, 220]
    assert req.screenWidth == 1920
    assert req.screenHeight == 1080

    norm_pts = normalize_request_spatial_points(req, actual_width=1920, actual_height=1080)
    assert "gaze" in norm_pts
    assert "cursor" in norm_pts
    assert "caret" in norm_pts
    # Check [y, x] order: y=200/1080 -> 185, x=100/1920 -> 52
    assert norm_pts["gaze"] == [185, 52]


def test_spatial_coherence_evaluation():
    # Case 1: Gaze is close to cursor/caret (<= 200 units)
    points_close = {
        "caret": [400, 300],
        "cursor": [410, 310],
        "gaze": [420, 320],
    }
    note_close = evaluate_spatial_coherence(points_close, model_explanation="Looking at index calculation.")
    assert "close together" in note_close
    assert "no distinguishable spatial information" in note_close

    # Case 2: Gaze is divergent (> 200 units)
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
        # Access the underlying structure from byref/pointer
        pt = byref_pt._obj
        pt.x = 800
        pt.y = 600
        return 1

    monkeypatch.setattr(ctypes.windll.user32, "GetCursorPos", mock_get_cursor_pos)

    # Standard call
    pos = get_real_mouse_position()
    assert pos == (800, 600)

    # Call with return_debug=True
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
