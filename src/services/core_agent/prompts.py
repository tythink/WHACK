"""
Stream D: Antigravity Agent Prompt Templates
Provides structured prompts for Antigravity computer-use agent sessions when takeover triggers.
"""

from typing import Optional, Dict

TAKEOVER_PROMPT_TEMPLATE = """You are Antigravity, an autonomous computer-use agent stepping in to assist the user.
The user is detected to be critically overwhelmed and struggling to solve a task.

[SITUATION ASSESSMENT]
- Active Window: {active_window}
- Current Task Context: {task_summary}
- Emotion State: Stress Index {stress_index:.2f} (Dominant Emotion: {dominant_emotion})
- Action State: Panic Score {panic_score:.2f} (Tab Switches/min: {tab_switch_rate})
- Fusion Score: {fusion_score:.2f} (Threshold: {threshold:.2f})

[PRIMARY OBJECTIVE]
User is overwhelmed struggling to solve task {task_summary}. Take control, inspect open window, and complete the blocking step.

[INSTRUCTIONS]
1. Quickly inspect the active screen and open window to diagnose what is blocking progress (e.g. failing build, confusing UI dialog, stuck git conflict, or unresolved error).
2. Take autonomous action to complete the blocking step cleanly and safely.
3. Once completed or unblocked, restore control to the user with a concise status summary of actions taken.
"""


def build_antigravity_prompt(
    active_window: Optional[str] = None,
    task_summary: Optional[str] = None,
    stress_index: float = 0.85,
    panic_score: float = 0.85,
    fusion_score: float = 0.85,
    threshold: float = 0.80,
    dominant_emotion: str = "panic",
    tab_switch_rate: float = 18.0,
) -> str:
    """Build standardized Antigravity prompt explaining the situation."""
    return TAKEOVER_PROMPT_TEMPLATE.format(
        active_window=active_window or "Active Desktop Window",
        task_summary=task_summary or "blocking development / terminal workflow",
        stress_index=stress_index,
        panic_score=panic_score,
        fusion_score=fusion_score,
        threshold=threshold,
        dominant_emotion=dominant_emotion,
        tab_switch_rate=tab_switch_rate,
    )
