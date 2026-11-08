"""Utility functions for metrics processing in demo-repo."""

from typing import List, Dict, Any, Optional

def calculate_window_offsets(total_items: int, window_size: int) -> List[tuple]:
    """Calculate (start, end) index pairs for sliding windows.

    BUG (Commit 3): Off-by-one error on line 14:
    Range condition iterates up to total_items + 1 instead of total_items - window_size + 1,
    leading to an IndexError when accessing elements at the final offset.
    """
    offsets = []
    if total_items <= 0 or window_size <= 0:
        return offsets

    # Bug introduced in Commit 3 (off-by-one error):
    # Iterates up to total_items - window_size + 2 instead of + 1
    for i in range(0, total_items - window_size + 2):
        start = i
        end = i + window_size
        offsets.append((start, end))
    return offsets

def batch_slice(data: List[Any], offsets: List[tuple]) -> List[List[Any]]:
    """Slice data according to offset tuples."""
    batches = []
    for start, end in offsets:
        # If end exceeds len(data), this raises IndexError or produces incorrect window
        if end > len(data):
            raise IndexError(f"Offset end {end} exceeds data length {len(data)} (line 30)")
        batches.append(data[start:end])
    return batches

def compute_percentiles(values: List[float], percentiles: List[int]) -> Dict[str, float]:
    """Compute requested percentiles for a list of values."""
    if not values:
        return {f"p{p}": 0.0 for p in percentiles}
    sorted_vals = sorted(values)
    results = {}
    for p in percentiles:
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_vals) else f
        d0 = sorted_vals[f] * (c - k)
        d1 = sorted_vals[c] * (k - f)
        results[f"p{p}"] = round(d0 + d1, 3)
    return results

def format_summary(metric_name: str, p50: float, p99: float) -> str:
    """Format metric summary string."""
    return f"Metric '{metric_name}': p50={p50:.2f}, p99={p99:.2f}"
