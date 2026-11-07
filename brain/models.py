"""Data contracts and schemas for Role 3: Brain Lead.

Compatible with src/contracts/events.ts:
- DiagnosisResponse: { summary, file, lines, quip, uncertain, hintLevel }
- DiagnoseRequest: payload sent to /diagnose
"""

from typing import Optional, List, Tuple, Literal, Union, Dict, Any
from pydantic import BaseModel, Field

HintLevelType = Literal["nudge", "hint", "full_explanation"]

class CodeCitation(BaseModel):
    file: str
    lines: Tuple[int, int]
    snippet: Optional[str] = None
    note: Optional[str] = None

class DiagnosisResponse(BaseModel):
    summary: str = Field(..., description="Concise diagnostic summary")
    file: Optional[str] = Field(None, description="Identified offending file path")
    lines: Optional[Tuple[int, int]] = Field(None, description="Identified line range [start, end]")
    quip: str = Field(..., description="Encouraging/witty engineering quip")
    uncertain: bool = Field(False, description="True if confidence is low or cause is ambiguous")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Diagnostic confidence score")
    hintLevel: HintLevelType = Field("nudge", description="Active hint level")
    explanation: Optional[str] = Field(None, description="Detailed explanation or diff for full_explanation")
    citations: List[CodeCitation] = Field(default_factory=list, description="Citations of specific files/lines")

class EditorState(BaseModel):
    activeFile: Optional[str] = None
    cursorLine: Optional[int] = None
    cursorColumn: Optional[int] = None
    isUndo: Optional[bool] = False
    isRedo: Optional[bool] = False
    diagnosticsCount: Optional[int] = 0
    cursorDwellMs: Optional[int] = 0
    selectionText: Optional[str] = None

class TerminalState(BaseModel):
    command: Optional[str] = None
    exitCode: Optional[int] = None
    durationMs: Optional[int] = 0
    tailOutput: Optional[str] = None
    errorHash: Optional[str] = None

class DiagnoseRequest(BaseModel):
    hintLevel: Optional[HintLevelType] = "nudge"
    screenshot: Optional[str] = Field(None, description="Optional base64-encoded screenshot")
    attemptLog: Optional[Union[str, List[Dict[str, Any]]]] = Field(
        None, description="Attempt log summary or list of telemetry events"
    )
    editorState: Optional[EditorState] = Field(None, description="Active editor state")
    terminalState: Optional[TerminalState] = Field(None, description="Recent terminal state")
    stream: Optional[bool] = Field(False, description="Whether to stream response as SSE")
