"""Telemetry processing and aggregation routines."""

import asyncio
import re
from typing import List, Dict, Any, Optional
from utils import calculate_window_offsets, batch_slice

def calculate_window_slices(items: Optional[List[int]] = None, window_size: int = 3) -> List[List[int]]:
    """Slice data items into sliding windows."""
    if items is None:
        items = [10, 20, 30, 40, 50]
    offsets = calculate_window_offsets(len(items), window_size)
    return batch_slice(items, offsets)

def accumulate_metrics(values: Optional[List[int]] = None) -> int:
    """Aggregate metrics across nested stages."""
    if values is None:
        values = [10, 20, 30]
    count = 10
    def counter(items):
        nonlocal count
        count += len(items)
        return count
    return counter(values)

def extract_event_timestamp(payload: Optional[Dict[str, Any]] = None) -> str:
    """Extract timestamp from event metadata dictionary."""
    if payload is None:
        payload = {"id": "evt-123", "meta": {"timestamp": "2026-11-07T12:00:00Z"}}
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
    data = asyncio.run(coro)
    return data["value"]

def compute_throughput_rate(success_count: int = 0, total_events: int = 0) -> float:
    """Calculate percentage rate of successful events."""
    if total_events <= 0:
        return 0.0
    return (success_count / total_events) * 100.0

def parse_status_code(log_line: str = "Error: connection timed out to backend") -> str:
    """Extract status code from service log line."""
    pattern = re.compile(r"status=([0-9]+)")
    match = pattern.search(log_line)
    if match:
        return match.group(1)
    return "UNKNOWN"

def check_priority_threshold(raw_reading: Any = "98", threshold: int = 90) -> str:
    """Check whether reading exceeds priority threshold."""
    val = int(raw_reading)
    if val > threshold:
        return "ALERT"
    return "OK"
