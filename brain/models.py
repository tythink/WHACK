"""Data contracts and schemas for Role 3: Brain Lead.

Compatible with shared contracts specified in docs/agents/AGENT_ROLE4_FRONTEND_CORE.md
and docs/agents/AGENT_ROLE3_BRAIN.md:
- BaseEvent: { source, type, timestamp, payload }
- EditorPayload / EditorState
- TerminalPayload / TerminalState
- DiagnosisResponse: { summary, file, lines, quip, uncertain, hintLevel, confidence, explanation, citations }
- DiagnoseRequest: flexible payload for /diagnose endpoint
"""

from typing import Optional, List, Tuple, Literal, Union, Dict, Any
from pydantic import BaseModel, Field, model_validator

HintLevelType = Literal["nudge", "hint", "full_explanation", "full"]

class CodeCitation(BaseModel):
    file: str
    lines: Tuple[int, int]
    snippet: Optional[str] = None
    note: Optional[str] = None
    valid: Optional[bool] = True
    diff_overlap: Optional[bool] = None

class ExecutionTrace(BaseModel):
    tools_called: List[Dict[str, Any]] = Field(default_factory=list)
    steps_used: int = 0
    part_sizes: Dict[str, int] = Field(default_factory=dict)
    time_to_first_output: float = 0.0
    total_time: float = 0.0
    provider: str = "offline_heuristic"
    model_name: str = "heuristic-v1"
    used_fallback: bool = False
    fallback_reason: Optional[str] = None
    citation_flags: List[str] = Field(default_factory=list)
    diff_overlap: bool = False
    real_model_calls: int = 0
    fallback_calls: int = 0
    token_usage: Dict[str, int] = Field(default_factory=dict)

class DiagnosisResponse(BaseModel):
    summary: str = Field(..., description="Concise diagnostic summary")
    file: Optional[str] = Field(None, description="Identified offending file path")
    lines: Optional[Tuple[int, int]] = Field(None, description="Identified line range [start, end]")
    quip: str = Field(..., description="Encouraging/witty engineering quip")
    uncertain: bool = Field(False, description="True if confidence is low or cause is ambiguous")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Diagnostic confidence score")
    hintLevel: str = Field("nudge", description="Active hint level ('nudge' | 'hint' | 'full_explanation')")
    source: Literal["model", "offline"] = Field("offline", description="Origin of diagnosis: 'model' or 'offline'")
    explanation: Optional[str] = Field(None, description="Detailed explanation or diff for full_explanation")
    citations: List[CodeCitation] = Field(default_factory=list, description="Citations of specific files/lines")
    trace: Optional[ExecutionTrace] = Field(None, description="Diagnostic execution trace details")

    @model_validator(mode="before")
    @classmethod
    def normalize_hint_level(cls, values: Any) -> Any:
        if isinstance(values, dict):
            level = values.get("hintLevel") or values.get("hint_level") or "nudge"
            if level == "full":
                level = "full_explanation"
            values["hintLevel"] = level
        return values

class EditorState(BaseModel):
    activeFile: Optional[str] = None
    cursorLine: Optional[int] = None
    cursorColumn: Optional[int] = None
    isUndo: Optional[bool] = False
    isRedo: Optional[bool] = False
    diagnosticsCount: Optional[int] = 0
    cursorDwellMs: Optional[int] = 0
    selectionText: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def support_snake_case(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "active_file" in values and "activeFile" not in values:
                values["activeFile"] = values["active_file"]
            if "cursor_line" in values and "cursorLine" not in values:
                values["cursorLine"] = values["cursor_line"]
            if "diagnostics_count" in values and "diagnosticsCount" not in values:
                values["diagnosticsCount"] = values["diagnostics_count"]
            if "cursor_dwell_ms" in values and "cursorDwellMs" not in values:
                values["cursorDwellMs"] = values["cursor_dwell_ms"]
        return values

class TerminalState(BaseModel):
    command: Optional[str] = None
    exitCode: Optional[int] = None
    durationMs: Optional[int] = 0
    tailOutput: Optional[str] = None
    errorHash: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def support_snake_case(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "exit_code" in values and "exitCode" not in values:
                values["exitCode"] = values["exit_code"]
            if "duration_ms" in values and "durationMs" not in values:
                values["durationMs"] = values["duration_ms"]
            if "tail_output" in values and "tailOutput" not in values:
                values["tailOutput"] = values["tail_output"]
            if "error_hash" in values and "errorHash" not in values:
                values["errorHash"] = values["error_hash"]
        return values

class BaseEvent(BaseModel):
    source: Literal["face", "editor", "terminal", "hotkey", "brain"] = "brain"
    type: str = "diagnosis"
    timestamp: int
    payload: Any

class DiagnoseRequest(BaseModel):
    hintLevel: Optional[HintLevelType] = "nudge"
    screenshot: Optional[str] = Field(None, description="Optional base64-encoded screenshot")
    attemptLog: Optional[Union[str, List[Dict[str, Any]]]] = Field(
        None, description="Attempt log summary or list of telemetry events"
    )
    editorState: Optional[EditorState] = Field(None, description="Active editor state")
    editor: Optional[EditorState] = Field(None, description="Alias for editorState")
    terminalState: Optional[TerminalState] = Field(None, description="Recent terminal state")
    terminal: Optional[TerminalState] = Field(None, description="Alias for terminalState")
    events: Optional[List[Dict[str, Any]]] = Field(None, description="Recent events from Core ring buffer")
    stream: Optional[bool] = Field(False, description="Whether to stream response as SSE")

    @model_validator(mode="before")
    @classmethod
    def resolve_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "editor" in values and not values.get("editorState"):
                values["editorState"] = values["editor"]
            if "terminal" in values and not values.get("terminalState"):
                values["terminalState"] = values["terminal"]
            if "hint_level" in values and not values.get("hintLevel"):
                values["hintLevel"] = values["hint_level"]
            if "attempt_log" in values and not values.get("attemptLog"):
                values["attemptLog"] = values["attempt_log"]
        return values
