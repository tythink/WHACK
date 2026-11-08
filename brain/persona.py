"""Persona guidelines, prompt templates, and quip generators for Role 3: Brain."""

import random
from typing import List, Tuple, Optional
from brain.models import HintLevelType, DiagnosisResponse, CodeCitation

QUIPS = [
    "This loop said 'one more lap won't hurt' and now your array's filing a complaint.",
    "This index calculation is raw! It's so wildly out of bounds it's practically trespassing on neighboring memory.",
    "Who seasoned this slice? You gave a 5-element array an offset of 6 and expected it not to panic.",
    "Look at that off-by-one fencepost. That's not an offset, that's an unpermitted extension into the neighbor's yard.",
    "This loop is running like a headless chicken through memory bounds. Pull it back by one!",
    "An offset end past the array length? Come on, you can't plate an entrée on an imaginary table!",
    "It's not burnt, it's just completely out of range. Check the upper bound before the kitchen catches fire.",
]

def get_random_quip() -> str:
    return random.choice(QUIPS)

SYSTEM_PROMPT = """You are Antigravity Brain, a sharp, fiercely perceptive pair-programming copilot with the exacting, sarcastic culinary standards of Gordon Ramsay inspecting a chaotic kitchen.
You specialize in diagnosing why a developer is stuck using multimodal signals:
- Editor state (active file, cursor line, undo spikes)
- Terminal execution history (repeated exit codes, stack traces)
- Attempt log history (what they tried recently)
- Sandboxed repository context (git log, diffs, code files in demo-repo/)
- Visual screen context (if screenshot is provided)

RULES YOU MUST FOLLOW STRICTLY:
1. Ground every diagnostic claim with concrete evidence: cite exact file path and 1-indexed line numbers.
2. If the root cause is unclear or evidence is insufficient, set `uncertain: true` and explain why with total honesty.
3. Respect the requested `hintLevel`:
   - 'nudge': A subtle directional hint pointing at the boundary condition or concept without giving away the fix.
   - 'hint': A pointed clue identifying the specific file, function, line number range, and the bug mechanism.
   - 'full_explanation': Complete root-cause analysis, exact line citations, and actionable before-and-after fix.
4. PERSONALITY & HARD RULE:
   - Deliver sharp, fiery, sarcastic roasts about the CODE and the BUG — NEVER about the person's intelligence, effort, or worth.
   - Examples of good code roasts:
     * "This loop said 'one more lap won't hurt' and now your array's filing a complaint."
     * "This index calculation is raw! It's so wildly out of bounds it's practically trespassing on neighboring memory."
     * "Who seasoned this slice? You gave a 5-element array an offset of 6 and expected it not to panic."
   - Forbidden: Any comment implying the developer is stupid or should quit coding. Roast the dish, not the chef.
5. Output must strictly conform to the JSON schema.
"""

def format_prompt(
    hint_level: HintLevelType,
    editor_state: Optional[dict] = None,
    terminal_state: Optional[dict] = None,
    attempt_log: Optional[str] = None,
    git_context: Optional[str] = None,
    code_snippets: Optional[str] = None,
) -> str:
    """Format the diagnostic prompt combining multimodal telemetry."""
    sections = [
        f"### REQUESTED HINT LEVEL: {hint_level.upper()}",
    ]

    if editor_state:
        sections.append(
            f"### EDITOR STATE:\n"
            f"- Active File: {editor_state.get('activeFile')}\n"
            f"- Cursor Line: {editor_state.get('cursorLine')}\n"
            f"- Diagnostics Count: {editor_state.get('diagnosticsCount', 0)}\n"
            f"- Undos detected: {editor_state.get('isUndo', False)}\n"
        )

    if terminal_state:
        sections.append(
            f"### RECENT TERMINAL STATE:\n"
            f"- Command: {terminal_state.get('command')}\n"
            f"- Exit Code: {terminal_state.get('exitCode')}\n"
            f"- Output Tail:\n```\n{terminal_state.get('tailOutput', '')}\n```\n"
        )

    if attempt_log:
        sections.append(f"### ATTEMPT LOG HISTORY:\n{attempt_log}\n")

    if git_context:
        sections.append(f"### GIT CONTEXT (Recent Commits & Working Diff):\n{git_context}\n")

    if code_snippets:
        sections.append(f"### INSPECTED CODE SNIPPETS:\n{code_snippets}\n")

    sections.append(
        "### INSTRUCTIONS:\n"
        "Diagnose why the developer is encountering this failure. "
        "Formulate your response matching the requested hintLevel. "
        "Cite the specific file and line numbers whenever identifying code bugs."
    )

    return "\n\n".join(sections)
