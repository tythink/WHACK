"""Tests for metrics processing utilities."""

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

def test_window_offsets():
    """Verify sliding window offset generation."""
    total = 5
    window_size = 3
    offsets = calculate_window_offsets(total, window_size)
    assert len(offsets) == 3
    for start, end in offsets:
        assert end <= total
