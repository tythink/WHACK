# WHACK — Native VS Code Mode (`vscode-whack`)

Passive, multimodal debugging assistant for **VS Code / Antigravity**:

1. **Passive Default Mode**: Lives quietly in the bottom-right Status Bar (`WHACK · Flow 0.08`) with zero screen clutter while you code.
2. **Auto-Trigger & Code Highlighting**: When the WHACK Brain returns a diagnosis, VS Code automatically opens the cited file on the left, scrolls to the target lines, highlights primary (`coral`) & secondary (`amber`) code ranges with inline CodeLens actions, and slides open the **Right-Side Diagnosis + Chat Panel**.
3. **Graduated Help + 1-Click Fix + Right-Side Chat**:
   - Switch between **`1 · Nudge`**, **`2 · Hint`**, and **`3 · Full Fix`**
   - Click any **`file:line`** citation card to jump directly to that line
   - **`Preview Side-by-Side Diff`** (`vscode.diff`) or **`Apply Suggested Fix`** in 1 click (`Ctrl+Z` undoable)
   - Ask follow-up questions in the docked **Chatbox** (automatically attaches highlighted code context)
   - Click **`✕ Dismiss`** to clear all highlights, snooze 3 minutes, and collapse back to the passive Status Bar

---

## Quick Start (Test Right Now — Before Role 3's Brain Is Ready)

### Option A: Install the Pre-Built `.vsix` in Your Current VS Code / Antigravity Window (Fastest)
1. Press `Ctrl+Shift+X` (Extensions) -> click **`⋯`** (top-right of Extensions sidebar) -> **`Install from VSIX...`**
2. Select `vscode-whack/whack-vscode-0.1.0.vsix`
3. Look at the bottom-right Status Bar:
   - Passive monitor pill: **`WHACK · Flow 0.08`**
   - Test trigger button: **`🧪 Simulate Brain`** (or press `Ctrl+Alt+S`)
4. Click **`🧪 Simulate Brain`** to watch it open `samples/utils.py`, highlight lines `18–21` (primary) and `29–30` (related), and open the right-side Diagnosis + Chat panel!

### Option B: Launch Extension Development Host (`F5`)
1. Open the `WHACK` root folder in standard VS Code
2. Press **`F5`** (`Run WHACK VS Code Mode (Extension Host)` in `.vscode/launch.json`)

---

## Connecting Role 3's Live Brain (Zero Rework)

`vscode-whack` automatically detects your teammates' pipelines with zero configuration:
- **Live Brain (`http://127.0.0.1:5002`)**: Polls `GET /health` and calls `POST /diagnose` + `POST /chat` (falls back to Mock Brain automatically whenever `:5002` is offline).
- **Face / Signals / Core (`http://127.0.0.1:3000` & `signals/latest_brain_call.json`)**: Connects to Core WebSocket `/ws` if `core` is running; otherwise starts a lightweight embedded `:3000` receiver AND watches `signals/latest_brain_call.json` so `face/prototype_gui.py` and `signals/brain_caller.py` trigger VS Code highlights immediately!
- **Single Adapter File**: If your teammate changes any JSON field names in Brain, update only [`normalizeBrainDiagnosis()`](./src/brainAdapter.ts) in `src/brainAdapter.ts`.

## Rebuilding After Edits

With your `whack-frontend` Conda env (or Node 20):
```powershell
cd vscode-whack
npx tsc -p ./tsconfig.json
```
