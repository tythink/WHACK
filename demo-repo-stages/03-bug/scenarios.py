"""Telemetry processing and aggregation routines."""

import asyncio
import re
from typing import List, Dict, Any, Optional

def calculate_window_slices(items: Optional[List[int]] = None, window_size: int = 3) -> List[List[int]]:
    """Slice data items into sliding windows."""
    if items is None:
        items = [10, 20, 30, 40, 50]
    offsets = [(i, i + window_size) for i in range(0, len(items) - window_size + 2)]
    last_start, last_end = offsets[-1]
    if last_end > len(items):
        raise IndexError(f"list index out of range: offset end {last_end} exceeds length {len(items)}")
    return [items[s:e] for s, e in offsets]

def accumulate_metrics(values: Optional[List[int]] = None) -> int:
    """Aggregate metrics across nested stages."""
    if values is None:
        values = [10, 20, 30]
    total = 100
    def inner_calculate(items):
        nonlocal total
        for x in items:
            if x > 0:
                total += x
        return total

    count = 10
    def counter(items):
        if not items:
            return count
        count = len(items)
        return count

    val = 0
    def trigger():
        nonlocal val
        val = val + 1
        return val
    return trigger()

def extract_event_timestamp(payload: Optional[Dict[str, Any]] = None) -> str:
    """Extract timestamp from event metadata dictionary."""
    if payload is None:
        payload = {"id": "evt-123", "meta": None}
    return payload["meta"]["timestamp"]

def append_audit_entry(tag: str, log: List[str] = []) -> List[str]:
    """Append audit tag to the event history."""
    log.append(tag)
    return log

async def fetch_remote_metrics(metric_id: str) -> Dict[str, Any]:
    return {"metric_id": metric_id, "status": "ok", "value": 42.0}

def poll_remote_metrics() -> float:
    """Fetch and return remote metric reading."""
    coro = fetch_remote_metrics("cpu_usage")
    return coro["value"]

def compute_throughput_rate(success_count: int = 0, total_events: int = 0) -> float:
    """Calculate percentage rate of successful events."""
    return (success_count / total_events) * 100.0

def parse_status_code(log_line: str = "Error: connection timed out to backend") -> str:
    """Extract status code from service log line."""
    pattern = re.compile(r"status=(\d+)")
    match = pattern.search(log_line)
    return match.group(1)

def check_priority_threshold(raw_reading: Any = "98", threshold: int = 90) -> str:
    """Check whether reading exceeds priority threshold."""
    if raw_reading > threshold:
        return "ALERT"
    return "OK"
