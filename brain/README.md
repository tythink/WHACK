# WHACK Brain Service (Role 3: LLM & Reasoning Lead)

The Brain Service is WHACK's live codebase reasoning, multimodal diagnostic, and Gordon Ramsay personality engine. It reacts in real time to facial affect, 3D gaze/caret/mouse coordinates, live screenshots, editor diagnostics, and terminal outputs to diagnose why a developer is stuck — citing verified line numbers and offering 1-click patches.

## Key Capabilities

1. **Multimodal Live Ingestion**:
   - Cross-platform live screen capture (`PIL.ImageGrab` / `screencapture`) with spatial `[y, x]` (`0..1000`) coordinate normalization against raw physical display dimensions.
   - 6 calibrated CV affective scores (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`), fusion status, and behavioral trigger reasons.
   - Active VS Code workspace `repoPath`, workspace-relative `activeFile`, cursor coordinates, IDE `diagnosticsSummary`, and timed terminal execution logs.

2. **Dynamically Sandboxed Codebase Tools**:
   All tools are path-sandboxed strictly to the open workspace (`repoPath`, falling back to `DEMO_REPO_DIR`) with directory traversal protection:
   - `read_file` / `read_file_range`: Line-numbered file reading.
   - `search_code` / `search_text`: Fast regex/substring search across workspace files.
   - `get_ast_context`: Enclosing symbol, callers, and callees via `CodeMap`.
   - `git_diff` / `get_working_diff`: Uncommitted working-tree edits (`git diff HEAD`) + recent commit diff.
   - `git_log`: Recent commit history inspection.
   - `get_wiki_context`: Dynamic architecture & `HEAD` commit wiki from `<repo>/.whack/WIKI.md`.

3. **Bounded Tool Loop & Resilient Result Extraction (`brain/extractor.py`)**:
   - Up to 4 async tool-calling turns across Gemini (`gemini-2.5-flash`), OpenAI (`gpt-4o-mini`), and Anthropic (`claude-3-5-haiku-latest`).
   - Balanced JSON/fence/prose extraction, line/confidence schema normalization, citation snippet verification, and indentation-aware `StructuredEdit` patch alignment.

4. **Gordon Ramsay Developer Copilot & Honest Offline Reporting**:
   - Witty, sharp Gordon Ramsay code-roasting personality ("roast the raw dish, never the chef") across 3 progressive hint levels (`nudge`, `hint`, `full_explanation`) and interactive `/chat`.
   - Returns an honest `source: "offline"` response whenever no API key is configured or the model is unreachable — zero fake heuristic diagnoses.

---

## API Endpoints

- **`GET /health`**: Service status, active `provider`, `model`, `apiKeyConfigured`, available tools, and default workspace path.
- **`POST /diagnose`**: Multimodal JSON or `text/event-stream` (SSE) diagnosis; broadcasts completed `DiagnosisResponse` to `:3000/events`.
- **`POST /chat`**: Interactive follow-up mentoring chat (`ChatRequest` -> `ChatResponse`) without re-triggering HUD/VS Code broadcasts.
- **`POST /wiki/update`**: Regenerates `<repo>/.whack/WIKI.md` and commit cache for `HEAD`.
- **`POST /tools/execute`**: Runs a sandboxed inspection tool inside `repoPath`.
- **`POST /apply-fix` & `POST /rollback`**: Checkpointed patch application and rollback.

---

## Configuration & Running

1. Copy `brain/.env.example` to `brain/.env` and set `GEMINI_API_KEY` (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`).
2. Start the server:
   ```bash
   python -m brain.server --port 5002
   ```
3. Run tests:
   ```bash
   pytest brain/tests -v
   ```
