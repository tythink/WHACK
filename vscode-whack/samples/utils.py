"""Sample target module matching Role 3's planted bug in demo-repo/utils.py."""

from typing import List, Dict, Any


def calculate_window_offsets(total_items: int, window_size: int) -> List[tuple]:
    """Calculate (start, end) index pairs for sliding windows.

    BUG: Off-by-one error in loop stop condition below:
    Iterates up to `total_items - window_size + 2` instead of `+ 1`,
    producing an out-of-bounds window at the end of the sequence.
    """
    offsets = []
    if total_items <= 0 or window_size <= 0:
        return offsets

    # Sliding window generation
    for i in range(0, total_items - window_size + 2):
        start = i
        end = i + window_size
        offsets.append((start, end))
    return offsets


def batch_slice(data: List[Any], offsets: List[tuple]) -> List[List[Any]]:
    """Slice data according to offset tuples."""
    batches = []
    for start, end in offsets:
        if end > len(data):
            raise IndexError(f"Offset end {end} exceeds data length {len(data)}")
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
