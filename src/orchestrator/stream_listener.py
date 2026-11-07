"""
Stream D: Stream Listener
Subscribes to Stream B (Emotion :5001) and Stream C (Action :5002).
Includes graceful reconnection and automatic fallback mock feed if upstream streams are pending.
"""

import json
import time
import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger("stream-listener")


class StreamListener:
    def __init__(
        self,
        emotion_url: str = "http://localhost:5001/current",
        action_url: str = "http://localhost:5002/current",
        on_emotion_event: Optional[Callable[[dict], None]] = None,
        on_action_event: Optional[Callable[[dict], None]] = None,
        poll_interval: float = 0.1,
    ):
        self.emotion_url = emotion_url
        self.action_url = action_url
        self.on_emotion_event = on_emotion_event
        self.on_action_event = on_action_event
        self.poll_interval = poll_interval
        self.running = False

    async def start(self):
        self.running = True
        logger.info(f"Stream Listener started. Monitoring Emotion ({self.emotion_url}) and Action ({self.action_url}).")
        await asyncio.gather(
            self._listen_emotion_loop(),
            self._listen_action_loop(),
        )

    def stop(self):
        self.running = False

    async def _fetch_json(self, url: str) -> Optional[dict]:
        try:
            import urllib.request
            # Fast non-blocking read via thread executor
            def _req():
                req = urllib.request.Request(url, headers={"User-Agent": "WHACK-Orchestrator"})
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode())
                return None

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _req)
        except Exception:
            return None

    async def _listen_emotion_loop(self):
        """Continuously fetch or subscribe to Emotion stream (:5001)."""
        consecutive_failures = 0
        while self.running:
            data = await self._fetch_json(self.emotion_url)
            if data:
                consecutive_failures = 0
                if self.on_emotion_event:
                    self.on_emotion_event(data)
            else:
                consecutive_failures += 1
                if consecutive_failures == 10:
                    logger.warning(f"Emotion stream at {self.emotion_url} unreachable. Using baseline standby.")
            await asyncio.sleep(self.poll_interval)

    async def _listen_action_loop(self):
        """Continuously fetch or simulate Action stream (:5002)."""
        consecutive_failures = 0
        cycle_start = time.time()
        while self.running:
            data = await self._fetch_json(self.action_url)
            if data:
                consecutive_failures = 0
                if self.on_action_event:
                    self.on_action_event(data)
            else:
                consecutive_failures += 1
                # If Action service is not yet up (Stream C pending), supply coordinated fallback conforming to ActionEvent
                if consecutive_failures > 5:
                    elapsed = (time.time() - cycle_start) % 35.0
                    progress = elapsed / 35.0
                    if progress < 0.3:
                        panic = 0.1
                        tab_rate = 2.0
                    elif progress < 0.65:
                        panic = 0.4 + 0.3 * ((progress - 0.3) / 0.35)
                        tab_rate = 12.0
                    elif progress < 0.85:
                        panic = 0.85 + 0.1 * ((progress - 0.65) / 0.20)
                        tab_rate = 32.0
                    else:
                        panic = 0.15
                        tab_rate = 3.0

                    mock_action = {
                        "timestamp": int(time.time() * 1000),
                        "tabSwitchRatePerMin": round(tab_rate, 1),
                        "panicScore": round(panic, 3),
                        "clickBursts": int(panic * 15),
                        "jitterScore": round(panic * 0.9, 3),
                        "activeWindow": "Terminal - Debugging failing test suite",
                    }
                    if self.on_action_event:
                        self.on_action_event(mock_action)

            await asyncio.sleep(self.poll_interval)
