"""Metrics processing pipeline."""

from typing import List, Dict, Any, Optional

try:
    from utils import (
        calculate_window_offsets,
        batch_slice,
        compute_percentiles,
        format_summary,
    )
except ImportError:
    from .utils import (
        calculate_window_offsets,
        batch_slice,
        compute_percentiles,
        format_summary,
    )

class MetricsPipeline:
    def __init__(self, window_size: int = 5):
        self.window_size = window_size
        self.processed_count = 0

    def process_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process incoming raw metric records and compute rolling statistics."""
        if not records:
            return []

        values = [r["value"] for r in records if "value" in r]
        
        # Calculate sliding windows
        offsets = calculate_window_offsets(len(values), self.window_size)
        
        # Batch slices
        windows = batch_slice(values, offsets)
        
        results = []
        for i, window in enumerate(windows):
            stats = compute_percentiles(window, [50, 90, 99])
            results.append({
                "window_index": i,
                "count": len(window),
                "stats": stats,
            })
            self.processed_count += len(window)

        return results

    def get_summary(self) -> Dict[str, Any]:
        return {
            "processed_count": self.processed_count,
            "window_size": self.window_size,
        }
