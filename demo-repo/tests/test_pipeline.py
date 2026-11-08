"""Tests for demo-repo pipeline."""

import pytest
try:
    from pipeline import MetricsPipeline
except ImportError:
    from ..pipeline import MetricsPipeline

def test_pipeline_empty():
    p = MetricsPipeline()
    assert p.process_records([]) == []

def test_pipeline_sliding_window():
    p = MetricsPipeline(window_size=3)
    records = [{"value": v} for v in [10, 20, 30, 40, 50]]
    # Will fail with IndexError if utils.py has the off-by-one bug
    results = p.process_records(records)
    assert len(results) == 3
