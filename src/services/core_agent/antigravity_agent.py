"""
Stream D: Antigravity Agent Session Manager
Launches autonomous computer-use sessions when takeover is triggered by the Fusion Engine.
"""

import uuid
import time
import asyncio
import logging
from typing import Optional, Dict, Callable

from src.services.core_agent.prompts import build_antigravity_prompt

logger = logging.getLogger("antigravity-core")


class AntigravitySessionManager:
    """Manages autonomous Antigravity agent sessions triggered by panic/stress fusion."""

    def __init__(self, on_status_change: Optional[Callable[[str, dict], None]] = None):
        self.on_status_change = on_status_change
        self.active_session_id: Optional[str] = None
        self.current_status: str = "monitoring"
        self.last_prompt: Optional[str] = None

    async def trigger_takeover(
        self,
        fusion_score: float,
        stress_index: float,
        panic_score: float,
        active_window: Optional[str] = None,
        task_summary: Optional[str] = None,
        dominant_emotion: str = "panic",
        tab_switch_rate: float = 20.0,
    ) -> str:
        """Trigger an Antigravity agent takeover session."""
        session_id = f"ag-takeover-{uuid.uuid4().hex[:8]}"
        self.active_session_id = session_id
        self.current_status = "takeover_triggered"

        prompt = build_antigravity_prompt(
            active_window=active_window or "VS Code / Terminal",
            task_summary=task_summary or "resolving build errors and git conflicts",
            stress_index=stress_index,
            panic_score=panic_score,
            fusion_score=fusion_score,
            dominant_emotion=dominant_emotion,
            tab_switch_rate=tab_switch_rate,
        )
        self.last_prompt = prompt

        logger.info(f"🚨 TAKEOVER TRIGGERED! Initiating session {session_id} (fusionScore={fusion_score:.2f})")
        logger.info(f"Generated Antigravity Prompt:\n{prompt}")

        if self.on_status_change:
            self.on_status_change("takeover_triggered", {
                "session_id": session_id,
                "prompt": prompt,
                "fusion_score": fusion_score,
            })

        # Launch simulated or live SDK execution task in background
        asyncio.create_task(self._execute_agent_workflow(session_id, prompt))
        return session_id

    async def _execute_agent_workflow(self, session_id: str, prompt: str):
        """Execute the agent takeover stages."""
        try:
            self.current_status = "executing"
            logger.info(f"Session {session_id} entered EXECUTING stage: Antigravity inspecting desktop...")
            if self.on_status_change:
                self.on_status_change("executing", {"session_id": session_id})

            # Autonomous task execution duration (e.g. 8-10 seconds of simulated autonomous actions)
            await asyncio.sleep(4.0)
            logger.info(f"Session {session_id}: Step 1 - Diagnosing blocking error complete.")

            await asyncio.sleep(4.0)
            logger.info(f"Session {session_id}: Step 2 - Executing automated resolution.")

            self.current_status = "completed"
            logger.info(f"✅ Session {session_id} COMPLETED. Restoring control to user.")
            if self.on_status_change:
                self.on_status_change("completed", {"session_id": session_id})

        except Exception as e:
            logger.error(f"Error in session {session_id}: {e}")
            self.current_status = "monitoring"
            if self.on_status_change:
                self.on_status_change("cancelled", {"session_id": session_id, "error": str(e)})
