# WHACK Brain Service (Role 3: LLM & Reasoning Lead)

The Brain Service is WHACK's codebase reasoning, multimodal diagnostic, and personality engine. It ingests editor telemetry, terminal outputs, developer attempt histories, and visual screenshots to diagnose developer confusion, citing exact line numbers and providing 3 levels of contextual assistance.

## Key Capabilities

1. **Multimodal Ingestion**:
   - macOS screencapture ingestion (`screencapture -x -C`) with PIL image downscaling and compression.
   - Editor telemetry (active file, cursor line, diagnostics count, undo/redo rates).
   - Terminal execution logs (commands, exit codes, stderr/stdout tails, error hashes).
   - Attempt-log analysis (what the user tried recently).

2. **Strictly Sandboxed Codebase Tools**:
   All tools are path-sandboxed strictly to `demo-repo/` with directory traversal protection:
   - `list_dir(rel_path)`: Directory exploration.
   - `read_file_range(rel_path, start_line, end_line)`: Line-numbered file reading.
   - `search_text(query, rel_path)`: Fast substring/regex searching across code files.
   - `git_log(n, rel_path)`: Recent commit history inspection.
   - `git_diff(commit_a, commit_b, rel_path)`: Working tree or commit diff inspection.

3. **Bounded Reasoning Loop & Grounded Citations**:
   - Max 6 tool turns to ensure fast response latency (< 1.5s).
   - Enforces concrete line number citations (`file`, `lines: [start, end]`).
   - Supports explicit `"uncertain": true` responses when evidence is ambiguous.

4. **3-Level Progressive Hint System**:
   - `nudge`: Subtle directional clues that point the developer in the right direction without spoiling the fix.
   - `hint`: Pointed clues naming the exact file, function, and nature of the bug.
   - `full_explanation`: Complete root cause analysis with before/after code diff.

5. **Dual Online / Offline Mode**:
   - Integrates with Gemini API (`gemini-2.5-flash`) and OpenAI API (`gpt-4o-mini`).
   - Includes a deterministic, low-latency heuristic fallback engine with 0 external API dependencies, guaranteeing demo readiness during offline presentations.

6. **Streaming & Server-Sent Events (SSE)**:
   - Supports live token/event streaming via `text/event-stream` on `POST /diagnose`.

---

## API Endpoints

### `GET /health`
Returns service status, loaded sandboxed tools, and configured repository paths.

### `GET /diagnose`
Returns mock streaming stub conforming to Milestone 0:45:
```json
{
  "summary": "Index error on line 42",
  "file": "utils.py",
  "lines": [40, 45],
  "confidence": 0.95,
  "quip": "Computers do what you say, not what you want.",
  "uncertain": false,
  "hintLevel": "nudge"
}
```

### `POST /diagnose`
Accepts `DiagnoseRequest`:
```json
{
  "hintLevel": "hint",
  "stream": false,
  "editorState": {
    "activeFile": "utils.py",
    "cursorLine": 18
  },
  "terminalState": {
    "command": "pytest demo-repo/tests",
    "exitCode": 1,
    "tailOutput": "IndexError: Offset end 6 exceeds data length 5 (line 30)"
  }
}
```

Returns `DiagnosisResponse`:
```json
{
  "summary": "Off-by-one error in utils.py: calculate_window_offsets produces one offset too many.",
  "file": "utils.py",
  "lines": [16, 22],
  "confidence": 0.98,
  "quip": "There are 2 hard problems in computer science: cache invalidation, naming things, and off-by-one errors.",
  "uncertain": false,
  "hintLevel": "hint",
  "explanation": "In utils.py line 18, range(0, total_items - window_size + 2) iterates one index past the boundary.",
  "citations": [
    {
      "file": "utils.py",
      "lines": [16, 22],
      "snippet": "for i in range(0, total_items - window_size + 2):",
      "note": "Stop condition creates an offset whose end exceeds array length"
    }
  ]
}
```

### `POST /tools/execute`
Executes a sandboxed tool directly inside `demo-repo/`.

---

## Running the Service

```bash
# Start brain server on port 5002
python -m brain.server --port 5002
```

## Running Tests

```bash
# Run unit and scenario tests
PYTHONPATH=. brain/.venv/bin/pytest brain/tests -v
```
