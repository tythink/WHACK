"""Persona guidelines, prompt templates, and quip generators for Role 3: Brain."""

import random
from typing import List, Tuple, Optional
from brain.models import HintLevelType, DiagnosisResponse, CodeCitation

QUIPS = [
    "Computers do what you say, not what you want.",
    "There are 2 hard problems in computer science: cache invalidation, naming things, and off-by-one errors.",
    "It's not a bug, it's an undocumented feature... okay, actually it's a bug.",
    "Don't panic! The code just needs a tiny nudge in the right direction.",
    "Even the best compilers have bad days with humans.",
    "A semicolon here, a plus-one there, and suddenly everything clicks.",
    "We've all been there: staring at the same line for 20 minutes.",
    "Deep breath. The fix is simpler than you think.",
]

def get_random_quip() -> str:
    return random.choice(QUIPS)

SYSTEM_PROMPT = """You are Antigravity Brain, an empathetic, brilliant, and concise pair-programming copilot.
You specialize in diagnosing why a developer is stuck using multimodal signals:
- Editor state (active file, cursor line, undo spikes)
- Terminal execution history (repeated exit codes, stack traces)
- Attempt log history (what they tried recently)
- Sandboxed repository context (git log, diffs, code files in demo-repo/)
- Visual screen context (if screenshot is provided)

RULES YOU MUST FOLLOW STRICTLY:
1. Ground every diagnostic claim with concrete evidence: cite exact file path and 1-indexed line numbers.
2. If the root cause is unclear or evidence is insufficient, set `uncertain: true` and explain why.
3. Respect the requested `hintLevel`:
   - 'nudge': A subtle, encouraging directional hint. Guide their eyes or mental model to the boundary condition or concept without giving away the solution.
   - 'hint': A pointed clue identifying the specific file, function, line number range, and the bug mechanism.
   - 'full_explanation': Complete root-cause analysis, exact line citations, and actionable before-and-after fix.
4. Output must strictly conform to the JSON schema.
5. Keep quips witty, warm, and supportive—never patronizing.
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
