"""Tests for demo-repo utils."""

import pytest
try:
    from utils import calculate_window_offsets, batch_slice, compute_percentiles
except ImportError:
    from ..utils import calculate_window_offsets, batch_slice, compute_percentiles

def test_compute_percentiles():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    p = compute_percentiles(data, [50, 99])
    assert p["p50"] == 30.0
    assert p["p99"] >= 49.0

def test_window_offsets_clean():
    """This test tests normal window offsets and catches the off-by-one bug."""
    total = 5
    window_size = 3
    offsets = calculate_window_offsets(total, window_size)
    # For total=5, window_size=3, valid windows are:
    # (0, 3), (1, 4), (2, 5) -> exactly 3 windows (5 - 3 + 1 = 3)
    # The planted bug produces 4 windows: (0, 3), (1, 4), (2, 5), (3, 6)
    assert len(offsets) == 3, f"Expected 3 windows, got {len(offsets)}: {offsets}"
    for start, end in offsets:
        assert end <= total, f"Window end {end} exceeds total length {total}"
