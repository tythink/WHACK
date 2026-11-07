"""
Stream D: Multi-Stream Fusion Engine
Computes:
  fusionScore = (w1 * stressIndex) + (w2 * panicScore)
When fusionScore > threshold (0.8), triggers Antigravity Agent Takeover session.
"""

import time
import logging
from typing import Dict, Optional, Callable

from src.contracts.events import TakeoverTriggerEvent
from src.services.core_agent.antigravity_agent import AntigravitySessionManager

logger = logging.getLogger("fusion-engine")


class FusionEngine:
    def __init__(
        self,
        w1: float = 0.5,
        w2: float = 0.5,
        threshold: float = 0.8,
        cooldown_sec: float = 15.0,
        on_trigger_callback: Optional[Callable[[dict], None]] = None,
    ):
        """
        :param w1: Weight for Emotion stressIndex (default 0.5)
        :param w2: Weight for Action panicScore (default 0.5)
        :param threshold: Trigger threshold (default 0.8)
        :param cooldown_sec: Minimum seconds between successive takeovers
        """
        self.w1 = w1
        self.w2 = w2
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.on_trigger_callback = on_trigger_callback

        self.last_stress_index: float = 0.0
        self.last_dominant_emotion: str = "neutral"
        self.last_panic_score: float = 0.0
        self.last_tab_switch_rate: float = 0.0
        self.last_active_window: str = "Desktop"

        self.latest_fusion_score: float = 0.0
        self.last_trigger_time: float = 0.0
        self.current_status: str = "monitoring"
        self.active_session_id: Optional[str] = None

        self.session_manager = AntigravitySessionManager(on_status_change=self._on_agent_status_change)

    def _on_agent_status_change(self, status: str, details: dict):
        logger.info(f"Agent status changed to: {status}")
        self.current_status = status
        if status in ("completed", "cancelled"):
            self.last_trigger_time = time.time()
            self.active_session_id = None
            self.current_status = "monitoring"

    def update_emotion(self, event: Dict):
        """Ingest event from Stream B (Emotion)."""
        self.last_stress_index = float(event.get("stressIndex", 0.0))
        self.last_dominant_emotion = str(event.get("dominantEmotion", "neutral"))
        self._evaluate_fusion()

    def update_action(self, event: Dict):
        """Ingest event from Stream C (Action)."""
        self.last_panic_score = float(event.get("panicScore", 0.0))
        self.last_tab_switch_rate = float(event.get("tabSwitchRatePerMin", 0.0))
        self.last_active_window = str(event.get("activeWindow", "Desktop"))
        self._evaluate_fusion()

    def _evaluate_fusion(self) -> float:
        """Compute fusionScore and check threshold."""
        score = (self.w1 * self.last_stress_index) + (self.w2 * self.last_panic_score)
        self.latest_fusion_score = round(max(0.0, min(1.0, score)), 4)

        now = time.time()
        is_above_threshold = self.latest_fusion_score > self.threshold

        # Update status if arming
        if is_above_threshold and self.current_status == "monitoring":
            if now - self.last_trigger_time > self.cooldown_sec:
                self._trigger_takeover()
        elif not is_above_threshold and self.current_status == "monitoring":
            if self.latest_fusion_score > 0.6:
                self.current_status = "arming"
            else:
                self.current_status = "monitoring"

        return self.latest_fusion_score

    def _trigger_takeover(self):
        """Execute takeover triggering."""
        now = time.time()
        self.last_trigger_time = now
        self.current_status = "takeover_triggered"

        import asyncio
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            asyncio.create_task(
                self.session_manager.trigger_takeover(
                    fusion_score=self.latest_fusion_score,
                    stress_index=self.last_stress_index,
                    panic_score=self.last_panic_score,
                    active_window=self.last_active_window,
                    dominant_emotion=self.last_dominant_emotion,
                    tab_switch_rate=self.last_tab_switch_rate,
                )
            )

        if self.on_trigger_callback:
            self.on_trigger_callback(self.get_current_event())

    def get_current_event(self) -> Dict:
        """Return standardized TakeoverTriggerEvent payload."""
        return {
            "timestamp": int(time.time() * 1000),
            "fusionScore": self.latest_fusion_score,
            "stressIndex": round(self.last_stress_index, 4),
            "panicScore": round(self.last_panic_score, 4),
            "weights": {"w1": self.w1, "w2": self.w2},
            "threshold": self.threshold,
            "isTriggered": self.latest_fusion_score > self.threshold,
            "status": self.current_status,
            "agentSessionId": self.session_manager.active_session_id,
            "activeWindow": self.last_active_window,
            "taskSummary": f"Assisting user on {self.last_active_window}",
            "suggestedPrompt": self.session_manager.last_prompt,
        }
