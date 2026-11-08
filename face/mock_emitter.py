"""HTTP event emitter and Phase 1 mock heartbeat generator for WHACK Role 1."""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_EVENTS_URL = "http://localhost:3000/events"


class AsyncEventClient:
    """Non-blocking background HTTP POST client for `/events` with throttling."""

    def __init__(
        self,
        endpoint: str = DEFAULT_EVENTS_URL,
        heartbeat_sec: float = 3.0,
    ) -> None:
        self.endpoint = endpoint
        self.heartbeat_sec = heartbeat_sec
        self.enabled: bool = False
        self.last_status: str = "Disabled"
        self._last_sent_at: float = 0.0
        self._last_state: Optional[str] = None
        self._last_zone: Optional[str] = None
        self._queue: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue(maxsize=2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def maybe_emit(self, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        state = f"{payload.get('state')}:{payload.get('primary_emotion')}"
        zone = payload.get("gaze", {}).get("zone")
        state_changed = (state != self._last_state) or (zone != self._last_zone)
        due_heartbeat = (now - self._last_sent_at) >= self.heartbeat_sec

        if not (state_changed or due_heartbeat):
            return
        # Throttle rapid state oscillations to at most 2 POSTs/sec
        if (now - self._last_sent_at) < 0.5:
            return

        self._last_sent_at = now
        self._last_state = str(state)
        self._last_zone = str(zone)
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def _worker(self) -> None:
        session = requests.Session()
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                resp = session.post(self.endpoint, json=item, timeout=1.2)
                self.last_status = f"HTTP {resp.status_code} ({time.strftime('%H:%M:%S')})"
            except requests.RequestException:
                self.last_status = "Core :3000 offline (skipped)"

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass


def build_mock_event(cycle_idx: int = 0) -> Dict[str, Any]:
    """Generate sample multi-emotion + gaze events for standalone mock testing."""
    scenarios = [
        ("confused", 0.85, "screen_editor", 3400, False),
        ("frustrated", 0.78, "screen_terminal", 4800, False),
        ("flow", 0.88, "screen_editor", 9200, False),
        ("surprised", 0.81, "screen_terminal", 1200, False),
        ("confused", 0.76, "looking_away", 2100, False),
        ("delighted", 0.84, "screen_editor", 1900, False),
    ]
    primary_emotion, primary_score, zone, fix_ms, talking = scenarios[cycle_idx % len(scenarios)]
    emotions = {
        "confused": 0.12,
        "frustrated": 0.08,
        "surprised": 0.05,
        "flow": 0.18,
        "fatigued": 0.06,
        "delighted": 0.05,
    }
    emotions[primary_emotion] = primary_score
    suppressed = talking or zone == "looking_away"
    distress_score = (
        0.0 if suppressed else round(max(emotions["confused"], emotions["frustrated"]), 2)
    )
    contract_state = "confused" if distress_score >= 0.56 else "calm"
    return {
        "source": "face",
        "type": "face_update",
        "timestamp": int(time.time() * 1000),
        "state": contract_state,
        "score": distress_score,
        "confidence": 0.90,
        "primary_emotion": primary_emotion,
        "primary_score": primary_score,
        "emotions": emotions,
        "gaze": {
            "zone": zone,
            "fixation_ms": fix_ms,
            "iris_ratio_x": 0.51,
            "iris_ratio_y": 0.48,
            "eyes_closed": False,
            "confidence": 0.92,
        },
        "head_pose": {"pitch": -2.1, "yaw": 1.4, "roll": 0.5},
        "talking": talking,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="WHACK Role 1 Mock Face Event Emitter")
    parser.add_argument("--url", default=DEFAULT_EVENTS_URL, help="Core /events endpoint")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between POSTs")
    parser.add_argument("--once", action="store_true", help="Emit one event and exit")
    args = parser.parse_args()

    idx = 0
    print(f"[face/mock_emitter] Streaming mock face events to {args.url} every {args.interval}s ...")
    while True:
        payload = build_mock_event(idx)
        idx += 1
        try:
            resp = requests.post(args.url, json=payload, timeout=2.0)
            print(f"[OK {resp.status_code}] {json.dumps(payload)}")
        except requests.RequestException as exc:
            print(f"[WARN Core offline: {exc.__class__.__name__}] {json.dumps(payload)}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
