"""Data contracts and schemas for Role 3: Brain Service."""

from typing import Optional, List, Tuple, Literal, Union, Dict, Any
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

HintLevelType = Literal["nudge", "hint", "full_explanation"]


def _coerce_point(val: Any) -> Optional[List[Union[int, float]]]:
    """Coerce [x, y], (x, y), or {'x': x, 'y': y} into [x, y], skipping undetected points."""
    if val is None:
        return None
    if isinstance(val, dict):
        if val.get("detected") is False:
            return None
        if "x" in val and "y" in val and val["x"] is not None and val["y"] is not None:
            try:
                return [float(val["x"]), float(val["y"])]
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(val, (list, tuple)) and len(val) >= 2:
        try:
            return [float(val[0]), float(val[1])]
        except (TypeError, ValueError):
            return None
    return None


class CodeCitation(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    file: str
    lines: Tuple[int, int]
    snippet: Optional[str] = None
    note: Optional[str] = None
    valid: Optional[bool] = True
    diff_overlap: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_citation(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "file" not in data:
                for k in ("filePath", "file_path", "path"):
                    if k in data and data[k]:
                        data["file"] = data[k]
                        break
            if "lines" not in data or data.get("lines") is None:
                sl = data.get("startLine") or data.get("start_line") or data.get("line")
                el = data.get("endLine") or data.get("end_line") or sl
                if sl is not None:
                    try:
                        s_i, e_i = int(sl), int(el)
                        data["lines"] = (min(s_i, e_i), max(s_i, e_i))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(data["lines"], int) and not isinstance(data["lines"], bool):
                data["lines"] = (data["lines"], data["lines"])
            elif isinstance(data["lines"], (list, tuple)) and len(data["lines"]) == 1:
                data["lines"] = (int(data["lines"][0]), int(data["lines"][0]))
            return data
        return values


class StructuredEdit(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    file: str = Field(..., description="Relative path of file to edit")
    startLine: int = Field(..., description="1-indexed start line of text to replace")
    endLine: int = Field(..., description="1-indexed end line of text to replace (inclusive)")
    oldText: str = Field(..., description="Exact existing text in the file to be replaced")
    newText: str = Field(..., description="New replacement text")

    @model_validator(mode="before")
    @classmethod
    def support_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "file" not in data or not data.get("file"):
                for k in ("filePath", "file_path", "path"):
                    if data.get(k):
                        data["file"] = data[k]
                        break
            if "oldText" not in data:
                for k in ("old_text", "original", "oldCode", "old_code", "search", "target", "find"):
                    if k in data and data[k] is not None:
                        data["oldText"] = data[k]
                        break
            if "newText" not in data:
                for k in ("new_text", "replacement", "newCode", "new_code", "replace"):
                    if k in data and data[k] is not None:
                        data["newText"] = data[k]
                        break
            if "startLine" not in data:
                if "start_line" in data and data["start_line"] is not None:
                    data["startLine"] = data["start_line"]
                elif isinstance(data.get("lines"), (list, tuple)) and len(data["lines"]) >= 1:
                    data["startLine"] = data["lines"][0]
                elif "line" in data and data["line"] is not None:
                    data["startLine"] = data["line"]
            if "endLine" not in data:
                if "end_line" in data and data["end_line"] is not None:
                    data["endLine"] = data["end_line"]
                elif isinstance(data.get("lines"), (list, tuple)) and len(data["lines"]) >= 2:
                    data["endLine"] = data["lines"][1]
                elif "startLine" in data and data["startLine"] is not None:
                    data["endLine"] = data["startLine"]
            return data
        return values


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    turn: int = 1
    ok: bool = True
    summary: Optional[str] = None


class ExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    tools_called: List[Dict[str, Any]] = Field(default_factory=list)
    steps_used: int = 0
    part_sizes: Dict[str, int] = Field(default_factory=dict)
    time_to_first_output: float = 0.0
    total_time: float = 0.0
    time_image: float = Field(0.0, description="Time spent on image capture, resizing, and base64 encoding (seconds)")
    time_context: float = Field(0.0, description="Time spent assembling ordered context and AST codemap (seconds)")
    time_network: float = Field(0.0, description="Time spent on the remote LLM network call (seconds)")
    time_parsing: float = Field(0.0, description="Time spent parsing LLM response into schema (seconds)")
    provider: str = "gemini"
    model_name: str = "gemini-3.1-flash-lite-preview"
    used_fallback: bool = False
    fallback_reason: Optional[str] = None
    citation_flags: List[str] = Field(default_factory=list)
    diff_overlap: bool = False
    real_model_calls: int = 0
    fallback_calls: int = 0
    token_usage: Dict[str, int] = Field(default_factory=dict)
    spatial_points: Optional[Dict[str, List[int]]] = Field(
        None, description="Normalized 0-1000 [y, x] spatial coordinates for gaze, cursor, and caret"
    )
    gaze_coherence_note: Optional[str] = Field(
        None, description="Spatial coherence evaluation note regarding gaze vs cursor/caret agreement"
    )
    screenshot_bytes: Optional[int] = Field(
        None, description="Actual image payload size in bytes sent to API"
    )
    screenshot_mime_type: Optional[str] = Field(
        None, description="MIME type of screenshot sent to API"
    )
    screenshot_dimensions: Optional[Tuple[int, int]] = Field(
        None, description="Dimensions [width, height] of screenshot"
    )
    structured_edit_validation: Optional[str] = Field(
        None, description="Validation note or reason for inclusion/omission of structured fix"
    )


class DiagnosisResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    diagnosisId: str = Field(default_factory=lambda: uuid4().hex[:12], description="Unique diagnosis identifier")
    summary: str = Field(..., description="Concise diagnostic summary")
    file: Optional[str] = Field(None, description="Identified offending file path")
    lines: Optional[Tuple[int, int]] = Field(None, description="Identified line range [start, end]")
    quip: str = Field(..., description="Gordon Ramsay code-roasting quip (roasts code, never developer)")
    uncertain: bool = Field(False, description="True if confidence is low, offline, or cause is ambiguous")
    confidence: float = Field(0.95, ge=0.0, le=1.0, description="Diagnostic confidence score")
    hintLevel: Literal["nudge", "hint", "full_explanation"] = Field(
        "hint", description="Active hint level ('nudge' | 'hint' | 'full_explanation')"
    )
    source: Literal["model", "offline"] = Field("model", description="Origin of diagnosis: 'model' or 'offline'")
    error: Optional[str] = Field(None, description="Error details when Brain LLM is offline or failed")
    repoPath: Optional[str] = Field(None, description="Resolved workspace path diagnosed")
    reasons: List[str] = Field(default_factory=list, description="Trigger/fusion reasons forwarded from request")
    explanation: Optional[str] = Field(None, description="Detailed explanation or diff")
    citations: List[CodeCitation] = Field(default_factory=list, description="Citations of specific files/lines")
    suggestedFix: Optional[StructuredEdit] = Field(None, description="Verified structured edit suggestion")
    trace: Optional[ExecutionTrace] = Field(None, description="Diagnostic execution trace details")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "diagnosis_id" in data and "diagnosisId" not in data:
                data["diagnosisId"] = data["diagnosis_id"]
            level = data.get("hintLevel") or data.get("hint_level") or "hint"
            if level == "full":
                level = "full_explanation"
            if level not in ("nudge", "hint", "full_explanation"):
                level = "hint"
            data["hintLevel"] = level
            if "suggested_fix" in data and "suggestedFix" not in data:
                data["suggestedFix"] = data["suggested_fix"]
            if "repo_path" in data and "repoPath" not in data:
                data["repoPath"] = data["repo_path"]
            if data.get("citations") is None:
                data["citations"] = []
            if data.get("reasons") is None:
                data["reasons"] = []
            return data
        return values


class EditorState(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    activeFile: Optional[str] = None
    cursorLine: Optional[int] = None
    cursorColumn: Optional[int] = None
    isUndo: Optional[bool] = False
    isRedo: Optional[bool] = False
    diagnosticsCount: Optional[int] = 0
    diagnosticsSummary: Optional[str] = None
    cursorDwellMs: Optional[int] = 0
    selectionText: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def support_snake_case(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "active_file" in data and "activeFile" not in data:
                data["activeFile"] = data["active_file"]
            if "cursor_line" in data and "cursorLine" not in data:
                data["cursorLine"] = data["cursor_line"]
            if "cursor_column" in data and "cursorColumn" not in data:
                data["cursorColumn"] = data["cursor_column"]
            if "diagnostics_count" in data and "diagnosticsCount" not in data:
                data["diagnosticsCount"] = data["diagnostics_count"]
            if "diagnostics_summary" in data and "diagnosticsSummary" not in data:
                data["diagnosticsSummary"] = data["diagnostics_summary"]
            if "cursor_dwell_ms" in data and "cursorDwellMs" not in data:
                data["cursorDwellMs"] = data["cursor_dwell_ms"]
            if "selection_text" in data and "selectionText" not in data:
                data["selectionText"] = data["selection_text"]
            return data
        return values


class TerminalState(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    command: Optional[str] = None
    exitCode: Optional[int] = None
    durationMs: Optional[int] = 0
    tailOutput: Optional[str] = None
    errorHash: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def support_snake_case(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "exit_code" in data and "exitCode" not in data:
                data["exitCode"] = data["exit_code"]
            if "duration_ms" in data and "durationMs" not in data:
                data["durationMs"] = data["duration_ms"]
            if "tail_output" in data and "tailOutput" not in data:
                data["tailOutput"] = data["tail_output"]
            elif "output" in data and "tailOutput" not in data:
                data["tailOutput"] = data["output"]
            elif "recentOutput" in data and "tailOutput" not in data:
                data["tailOutput"] = data["recentOutput"]
            if "error_hash" in data and "errorHash" not in data:
                data["errorHash"] = data["error_hash"]
            return data
        return values


class AttemptEvent(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timestamp: Optional[Union[int, float, str]] = None
    source: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None
    command: Optional[str] = None
    exitCode: Optional[int] = None
    payload: Optional[Any] = None


class BaseEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    source: Literal["face", "editor", "terminal", "hotkey", "brain"] = "brain"
    type: str = "diagnosis"
    timestamp: int
    payload: Any


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    hintLevel: Literal["nudge", "hint", "full_explanation"] = "hint"
    repoPath: Optional[str] = Field(None, description="Target workspace root directory path")
    captureScreenshot: Optional[bool] = Field(
        None, description="Explicit screenshot capture toggle (None auto-enables when spatial coords present)"
    )
    screenshot: Optional[str] = Field(None, description="Optional base64-encoded screenshot")
    attemptLog: Union[List[Union[AttemptEvent, Dict[str, Any]]], str] = Field(
        default_factory=list, description="Attempt log summary or list of telemetry events"
    )
    editorState: Optional[EditorState] = Field(None, description="Active editor state")
    terminalState: Optional[TerminalState] = Field(None, description="Recent terminal state")

    # Multimodal spatial coordinates in raw screen pixels
    gazePoint: Optional[List[Union[int, float]]] = Field(
        None, description="User gaze point [x, y] in raw screen pixels"
    )
    cursorScreenPos: Optional[List[Union[int, float]]] = Field(
        None, description="Mouse cursor position [x, y] in raw screen pixels"
    )
    caretScreenPos: Optional[List[Union[int, float]]] = Field(
        None, description="Text caret insertion position [x, y] in raw screen pixels"
    )
    screenWidth: int = Field(1920, description="Reported screen width in pixels for coordinate space")
    screenHeight: int = Field(1080, description="Reported screen height in pixels for coordinate space")

    # Affective & fusion context
    emotions: Optional[Dict[str, float]] = Field(None, description="Live CV emotion scores (0.0-1.0)")
    userStatus: Optional[str] = Field(None, description="Fused user status label")
    reasons: List[str] = Field(default_factory=list, description="Fusion trigger reasons")
    callId: Optional[int] = Field(None, description="Caller sequence identifier")
    stream: bool = Field(False, description="Whether to stream response as SSE")

    # Optional telemetry fields
    events: Optional[List[Dict[str, Any]]] = Field(None, description="Recent events from Core ring buffer")
    diff: Optional[str] = Field(None, description="Optional explicit unified diff text")

    @model_validator(mode="before")
    @classmethod
    def resolve_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)

        # Workspace path aliases
        if not data.get("repoPath"):
            for k in ("workspaceRoot", "workspace_root", "repo_path"):
                if data.get(k):
                    data["repoPath"] = data[k]
                    break

        # Capture screenshot alias
        if "captureScreenshot" not in data and "capture_screenshot" in data:
            data["captureScreenshot"] = data["capture_screenshot"]

        # Hint level normalization
        raw_level = data.get("hintLevel") or data.get("hint_level") or "hint"
        if raw_level == "full":
            raw_level = "full_explanation"
        if raw_level not in ("nudge", "hint", "full_explanation"):
            raw_level = "hint"
        data["hintLevel"] = raw_level

        # Editor & terminal aliases
        if not data.get("editorState"):
            for k in ("editor", "editor_state"):
                if data.get(k) is not None:
                    data["editorState"] = data[k]
                    break
        if not data.get("terminalState"):
            for k in ("terminal", "terminal_state"):
                if data.get(k) is not None:
                    data["terminalState"] = data[k]
                    break

        # Attempt log
        if "attemptLog" not in data and "attempt_log" in data:
            data["attemptLog"] = data["attempt_log"]
        if data.get("attemptLog") is None:
            data["attemptLog"] = []

        # Spatial aliases & point coercion
        raw_gaze = data.get("gazePoint")
        if raw_gaze is None:
            for k in ("eyeGaze", "eye_gaze", "gaze_point"):
                if data.get(k) is not None:
                    raw_gaze = data[k]
                    break
        data["gazePoint"] = _coerce_point(raw_gaze)

        raw_cursor = data.get("cursorScreenPos")
        if raw_cursor is None:
            for k in ("mouseCursor", "mouse_cursor", "cursor_screen_pos"):
                if data.get(k) is not None:
                    raw_cursor = data[k]
                    break
        data["cursorScreenPos"] = _coerce_point(raw_cursor)

        raw_caret = data.get("caretScreenPos")
        if raw_caret is None:
            for k in ("keyboardCursor", "keyboard_cursor", "caret_screen_pos"):
                if data.get(k) is not None:
                    raw_caret = data[k]
                    break
        data["caretScreenPos"] = _coerce_point(raw_caret)

        # Screen dimensions
        sw = data.get("screenWidth") if data.get("screenWidth") is not None else data.get("screen_width")
        sh = data.get("screenHeight") if data.get("screenHeight") is not None else data.get("screen_height")
        data["screenWidth"] = int(sw) if sw else 1920
        data["screenHeight"] = int(sh) if sh else 1080

        # Emotions, status, fusion reasons, callId, stream
        if not data.get("userStatus") and data.get("user_status"):
            data["userStatus"] = data["user_status"]

        if "reasons" not in data or data.get("reasons") is None:
            for k in ("fusionReasons", "fusion_reasons"):
                if data.get(k) is not None:
                    data["reasons"] = data[k]
                    break
        if data.get("reasons") is None:
            data["reasons"] = []

        if data.get("callId") is None and data.get("call_id") is not None:
            data["callId"] = data["call_id"]

        if data.get("stream") is None:
            data["stream"] = False

        return data


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message: str = Field(..., description="Developer follow-up message")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="Prior conversation messages")
    activeFile: Optional[str] = Field(None, description="Currently active file in editor")
    highlightedLines: Optional[List[int]] = Field(None, description="Highlighted line numbers [start, end]")
    codeSnippet: Optional[str] = Field(None, description="Selected or visible code snippet")
    latestDiagnosis: Optional[Dict[str, Any]] = Field(None, description="Most recent diagnosis context")
    repoPath: Optional[str] = Field(None, description="Active workspace root path")

    @model_validator(mode="before")
    @classmethod
    def resolve_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        if not data.get("repoPath"):
            for k in ("workspaceRoot", "workspace_root", "repo_path"):
                if data.get(k):
                    data["repoPath"] = data[k]
                    break
        if "activeFile" not in data and "active_file" in data:
            data["activeFile"] = data["active_file"]
        if "highlightedLines" not in data and "highlighted_lines" in data:
            data["highlightedLines"] = data["highlighted_lines"]
        if "codeSnippet" not in data and "code_snippet" in data:
            data["codeSnippet"] = data["code_snippet"]
        if "latestDiagnosis" not in data and "latest_diagnosis" in data:
            data["latestDiagnosis"] = data["latest_diagnosis"]
        if data.get("history") is None:
            data["history"] = []
        return data


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    reply: str
    mode: Literal["brain_live", "offline"]
    error: Optional[str] = None
    citations: List[CodeCitation] = Field(default_factory=list)
    suggestedFix: Optional[StructuredEdit] = Field(None)


class WikiUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    repoPath: Optional[str] = None
    allowModel: bool = True
    force: bool = True

    @model_validator(mode="before")
    @classmethod
    def resolve_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        if not data.get("repoPath"):
            for k in ("workspaceRoot", "workspace_root", "repo_path"):
                if data.get(k):
                    data["repoPath"] = data[k]
                    break
        if "allowModel" not in data and "allow_model" in data:
            data["allowModel"] = data["allow_model"]
        return data


class ApplyFixRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fix: StructuredEdit
    repoPath: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def resolve_fix(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "repoPath" not in data:
                for k in ("repo_path", "workspaceRoot", "workspace_root"):
                    if data.get(k):
                        data["repoPath"] = data[k]
                        break
            if "file" in data and "fix" not in data:
                data["fix"] = {
                    "file": data.get("file"),
                    "startLine": data.get("startLine") or data.get("start_line", 1),
                    "endLine": data.get("endLine") or data.get("end_line", 1),
                    "oldText": data.get("oldText") or data.get("old_text", ""),
                    "newText": data.get("newText") or data.get("new_text", ""),
                }
            return data
        return values


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    checkpointHash: str = Field(..., description="Git commit hash to reset target repo to")
    repoPath: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def resolve_hash(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data = dict(values)
            if "checkpoint_hash" in data and "checkpointHash" not in data:
                data["checkpointHash"] = data["checkpoint_hash"]
            if "repoPath" not in data:
                for k in ("repo_path", "workspaceRoot", "workspace_root"):
                    if data.get(k):
                        data["repoPath"] = data[k]
                        break
            return data
        return values
