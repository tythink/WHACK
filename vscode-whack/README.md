# WHACK — Native VS Code Extension (`vscode-whack`)

Real-time, multimodal debugging assistant with Gordon Ramsay's live persona for **VS Code / Antigravity**.

## Redesigned GUI: "🔥 WHACK · Chef's Pass"

1. **1-Click Live Stack Control (Zero Terminal Juggling)**:
   - Click the **`WHACK`** pill in the bottom-right Status Bar (or the **`🔥`** icon in the top-right editor bar) to open **Chef's Pass**.
   - **`🔑 Set Key`** prompts securely inside VS Code for your Gemini / OpenAI / Anthropic API key and writes `brain/.env` automatically.
   - **`🚀 Launch Live Stack`** starts both the **Brain Server (`:5002`)** and the **Live Camera + 3D Gaze + OS Signal Sensor (`signals/prototype_signals_gui.py`)** in the background, with live LED status pills (`Brain · gemini-3-flash-preview` and `Sensor · Live`).
   - **`📷 Sensor HUD`** toggles the companion camera & 3D gaze calibration window independently anytime.
2. **Live Multimodal Telemetry Strip**:
   - Displays your fused real-time state (`FLOW`, `THINKING`, `STUCK`, or `AFK GUARD`) and animated **`Conf`**, **`Frust`**, and **`Flow`** bars streamed live from your webcam + OS signals (`:3000/events`).
3. **Proactive Live Trigger, Gordon's Live Roast & Graduated Fixes**:
   - When `face/ + signals/` detects sustained confusion/frustration on screen (and never while AFK), it triggers `POST /diagnose` on `:5002` against whichever folder you currently have open in VS Code.
   - VS Code plays a harmonic chime, opens the cited file, highlights primary (`coral`) & secondary (`amber`) code ranges with inline CodeLens actions, and presents **🔥 Gordon's Live Take** alongside **`1 · Nudge`**, **`2 · Hint`**, and **`3 · Full Fix`**.
   - Click any **`file:line`** citation pill to jump directly to the bug, **`⇄ Preview Diff`** to inspect a side-by-side `vscode.diff`, **`✓ Apply Suggested Fix`** (`Ctrl+Z` undoable), or ask follow-up questions in the docked **Chatbox** (`POST /chat`).

---

## Quick Start in VS Code

### Option A: Install the Pre-Built `.vsix` (Recommended)
1. Press `Ctrl+Shift+X` (Extensions) -> click **`⋯`** (top-right of Extensions sidebar) -> **`Install from VSIX...`**
2. Select `vscode-whack/whack-vscode-0.1.0.vsix` and reload VS Code if prompted.
3. Click **`🔥 WHACK`** in the bottom-right Status Bar to open **Chef's Pass**.
4. Click **`🔑 Set Key`** once to paste your API key (`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`).
5. Click **`🚀 Launch Live Stack`** — Brain (`:5002`) and the Camera/Signal HUD start automatically!
   - *Tip*: In the Sensor HUD window, look naturally at your code and press **`[C]`** (calibrate neutral expression) and **`[G]`** (9-point 3D gaze calibration), or press **`Ctrl+Shift+K`** / **`⚡ Diagnose`** inside VS Code anytime for an instant on-demand check.

### Option B: Launch Extension Development Host (`F5`)
1. Open the `WHACK` root folder in VS Code.
2. Press **`F5`** (`Run WHACK VS Code Mode (Extension Host)` in `.vscode/launch.json`).

---

## Live Architecture & Endpoints

- **Live Brain (`http://127.0.0.1:5002`)**: Polls `GET /health`, triggers `POST /diagnose` with your active workspace `repoPath`, diagnostics, terminal state, and screen capture, calls `POST /chat` for follow-up chat, and updates `.whack/WIKI.md` via `POST /wiki/update` (plus `.git/hooks/post-commit` on every `git commit`). When `:5002` or the configured LLM API key is unavailable, the UI surfaces an honest offline/API-key banner rather than fabricated diagnoses.
- **Signals & Core Bridge (`http://127.0.0.1:3000` & `signals/latest_brain_call.json`)**: Connects to Core WebSocket `/ws` if `core` is running; otherwise runs an embedded `:3000` receiver exposing `POST /events` and `GET /context` so `signals/brain_caller.py` reads your live VS Code workspace root, active file/cursor, diagnostics, and snooze/pause state automatically.

## Rebuilding After Edits

With your `whack-frontend` Conda env (or Node 20):
```powershell
cd vscode-whack
npx tsc -p ./tsconfig.json
```
