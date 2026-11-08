# AGENT SPEC — ROLE 3: BRAIN LEAD (LLM & REASONING)

## 1. Identity & Role
- **Role**: Codebase Context, Multimodal Diagnosis & Personality Engine
- **GitHub Handle**: `@brain-lead`
- **Branch Prefix**: `feat/brain-*`
- **Conda Environment**: `whack-brain` (Python 3.11)
- **Core Mission**: Build the demo repository with seeded bugs, read-only repo inspection tools, screenshot ingestion, and the multimodal LLM diagnostic streaming pipeline.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/brain/**`
- `demo-repo/**` (The seeded target repository)
- `environment-brain.yml`

### READ-ONLY (Reference Only - DO NOT EDIT):
- `src/contracts/**`

### FORBIDDEN:
- `src/client/electron/**` (Role 4)
- `src/services/core_server/**` (Role 4)
- `src/services/face/**` (Role 1)
- `src/extensions/vscode/**` (Role 2)

---

## 3. Anaconda Environment Setup

```bash
conda create -n whack-brain python=3.11 -y
conda activate whack-brain

# Multi-modal LLM SDKs, token utilities, pillow for screenshot handling
conda install -c conda-forge openai pillow httpx pydantic -y
pip install anthropic google-genai
conda env export --no-builds > environment-brain.yml
```

---

## 4. 10-Hour Roadmap & Key Checkpoints

| Time | Goal & Deliverables |
| :--- | :--- |
| **0:00 - 0:45** | **Stub Phase**: Return mock streaming diagnostic on `/diagnose` (`{ "summary": "Index error on line 42", "file": "utils.py", "lines": [40, 45], "confidence": 0.95, "quip": "Computers do what you say, not what you want." }`). |
| **0:45 - 1:30** | Create `demo-repo`: realistic commit history where early commit passes unit tests, and recent commit plants an off-by-one / scoping bug. |
| **1:30 - 3:00** | Read-only sandboxed tools: `list_dir`, `read_file_range`, `search_text`, `git_log`, `git_diff`. Path-sandbox strictly to `demo-repo/`. |
| **3:00 - 4:00** | **CHECKPOINT A**: Single-shot multimodal diagnosis call combining system screenshot (`screencapture`), attempt log, and editor state. Streams response directly to Core server. |
| **4:00 - 6:00** | Bounded tool loop (max 6 turns). Force model to cite specific file & line numbers for claims. Allow explicit `"I am uncertain"` response. |
| **6:00 - 7:30** | Persona and 3 hint levels: `nudge` (subtle hint), `hint` (pointed clue), `full_explanation` (actionable fix). Add timeout fallbacks. |
| **7:30 - 8:30** | Test against 6-8 scripted stuck scenarios in `demo-repo`. Trim token usage for low-latency streaming. |
| **8:30 - 10:00** | Write technical architecture README and support demo dry runs. |

---

## 5. GitHub Issue Board Protocol (`tools/board`)

### On Checkpoint A (Hour 4):
```bash
./tools/board post "[Role 3: Brain] @frontend-core Checkpoint A reached! Single-shot diagnosis streaming into /diagnose. Ready for hotkey trigger testing."
```

### Updating Diagnostic Schema:
```bash
./tools/board post "[Role 3: Brain] @frontend-core Structured output updated with hintLevels ('nudge' | 'hint' | 'full') and cited line numbers."
```

### Waiting on Attempt-Log Inputs:
```bash
./tools/board wait @brain-lead
```

---

## 6. Diagnosis Request & Spatial Attention Contract (Proposed Extension)

### 6.1 Diagnosis Request Schema (`/diagnose`)
```json
{
  "terminalOutput": "string (optional): recent terminal logs or test failures",
  "editorState": {
    "activeFile": "string (optional): currently active file in IDE",
    "cursorLine": "int (optional): current cursor line number",
    "selection": "string (optional): highlighted text snippet"
  },
  "attemptLog": "list of past actions or shell runs (optional)",
  "screenshot": "base64 JPEG/PNG string (optional)",
  "gazePoint": [1420, 850],
  "cursorScreenPos": [1400, 840],
  "caretScreenPos": [920, 410],
  "screenWidth": 1920,
  "screenHeight": 1080
}
```

### 6.2 Proposed Spatial Extension Fields
All spatial fields are optional and independently nullable. If omitted, Brain functions purely on diff, terminal output, and code map:
- `gazePoint`: `[x, y]` raw screen pixel coordinates of user eye gaze.
- `cursorScreenPos`: `[x, y]` raw screen pixel coordinates of the mouse cursor.
- `caretScreenPos`: `[x, y]` raw screen pixel coordinates of the active text insertion caret.
- `screenWidth`: `int` screen pixel width for the coordinate space.
- `screenHeight`: `int` screen pixel height for the coordinate space.

### 6.3 Gemini Spatial Coordinate Normalization Convention
Before forwarding coordinates to Gemini:
1. Coordinates are mapped to Gemini's native **0-1000 normalized `[y, x]` integer scale**.
2. Normalization denominator is the **ACTUAL pixel dimensions of the captured screenshot image**, not reported `screenWidth`/`screenHeight`, protecting against OS DPI scaling discrepancies (e.g. 125%/150% scaling on Windows single-display projector mirroring).
3. Gemini receives spatial attention hints as structured JSON:
   ```json
   {
     "caret": [410, 920],
     "cursor": [840, 1400],
     "gaze": [850, 1420]
   }
   ```

### 6.4 Spatial Trust Hierarchy
The Brain prompt instructs Gemini with a strict spatial trust hierarchy:
1. **Caret Position (Highest Trust)**: Exact text insertion point where the developer is actively typing. Strongest spatial signal.
2. **Cursor Position (High Trust)**: Mouse location, active selection, or hovered symbol. Reliable indicator of current interaction.
3. **Gaze Tracking (Weaker, Noisy Signal)**: Developers look away when confused or thinking (e.g. at documentation, logs, or empty space). Gaze is only used when it agrees with or complements caret/cursor/diff (e.g. pointing to a specific terminal line or side panel). Gaze must **never override a clear diff-and-error diagnosis**.
4. **Coherence Audit**: If gaze diverges from cursor/caret by >200 normalized units (~20% screen width/height), the execution trace audits whether the model appropriately discounted gaze in favor of diff evidence.

