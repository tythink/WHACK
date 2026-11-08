"""Scripted stuck scenarios for testing multimodal diagnosis in demo-repo.

Each scenario demonstrates a common bug category that triggers developer frustration.
"""

import asyncio
import re
from typing import List, Dict, Any, Optional

# --- Scenario 1: Off-by-one Index Error ---
def run_scenario_1_off_by_one():
    """Iterates past the end of the array due to range + 2 instead of + 1."""
    items = [10, 20, 30, 40, 50]
    window_size = 3
    # Off-by-one: range tries to access items[5:8]
    offsets = [(i, i + window_size) for i in range(0, len(items) - window_size + 2)]
    last_start, last_end = offsets[-1]
    if last_end > len(items):
        raise IndexError(f"IndexError: list index out of range: offset end {last_end} > {len(items)} (utils.py:18)")
    return [items[s:e] for s, e in offsets]

# --- Scenario 2: Scoping / Variable Shadowing ---
def run_scenario_2_scoping():
    """UnboundLocalError: Local variable 'total' referenced before assignment in loop."""
    total = 100
    def inner_calculate(items):
        nonlocal total
        # Bug: if condition fails, local reassignment without declaration causes UnboundLocalError
        for x in items:
            if x > 0:
                total += x
        return total
    
    # Shadowing variant:
    count = 10
    def faulty_counter(items):
        # Referencing count before assignment inside inner scope
        if not items:
            return count  # Referencing outer
        count = len(items)  # Causes UnboundLocalError if referenced earlier
        return count
    
    # Execute buggy behavior
    def broken_accumulator(numbers):
        # Attempting to modify outer variable without nonlocal
        acc = 0
        def step(v):
            # acc += v  # Python raises UnboundLocalError: local variable 'acc' referenced before assignment
            nonlocal acc
            acc += v
        for n in numbers:
            step(n)
        return acc

    # Trigger scoping error
    val = 0
    def trigger():
        nonlocal val
        val = val + 1
        return val
    return trigger()

# --- Scenario 3: NoneType Dereference ---
def run_scenario_3_none_dereference():
    """TypeError: 'NoneType' object is not subscriptable on missing metadata."""
    payload: Dict[str, Any] = {"id": "evt-123", "meta": None}
    # Bug: Assuming meta is always a dict
    timestamp = payload["meta"]["timestamp"]  # Raises TypeError
    return timestamp

# --- Scenario 4: Mutable Default Argument ---
def run_scenario_4_mutable_default(tag: str, log: List[str] = []) -> List[str]:
    """Bug: Mutable default argument persists entries across function calls."""
    log.append(tag)
    return log

# --- Scenario 5: Async Unawaited Coroutine ---
async def fetch_remote_metrics(metric_id: str) -> Dict[str, Any]:
    return {"metric_id": metric_id, "status": "ok", "value": 42.0}

def run_scenario_5_unawaited():
    """Bug: Calling async coroutine synchronously without await."""
    coro = fetch_remote_metrics("cpu_usage")
    # Result is <coroutine object fetch_remote_metrics ...>, not the dict
    # Accessing coro["value"] raises TypeError: 'coroutine' object is not subscriptable
    return coro["value"]

# --- Scenario 6: Division by Zero ---
def run_scenario_6_zero_division():
    """ZeroDivisionError on empty input dataset."""
    success_count = 0
    total_events = 0
    # Bug: Missing guard for total_events == 0
    rate = (success_count / total_events) * 100.0  # Raises ZeroDivisionError
    return rate

# --- Scenario 7: Regex Group Mismatch ---
def run_scenario_7_regex():
    """AttributeError: 'NoneType' object has no attribute 'group'."""
    pattern = re.compile(r"status=(\d+)")
    log_line = "Error: connection timed out to backend"
    match = pattern.search(log_line)
    # Bug: Calling .group(1) on None match
    status_code = match.group(1)  # Raises AttributeError
    return status_code

# --- Scenario 8: Type Mismatch Comparison ---
def run_scenario_8_type_mismatch():
    """TypeError: '>' not supported between instances of 'str' and 'int'."""
    raw_reading = "98"  # String from query param / json
    threshold = 90      # Integer threshold
    # Bug: String comparison or uncasted comparison
    if raw_reading > threshold:  # In Python 3 raises TypeError
        return "ALERT"
    return "OK"
