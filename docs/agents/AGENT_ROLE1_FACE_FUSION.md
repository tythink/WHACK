# AGENT SPEC — ROLE 1 (COMBINED ROLE 1 & 2): FACE, SIGNALS & FUSION LEAD

## 1. Identity & Role
- **Role**: Face/Body/3D-Gaze Pipeline, Editor & Terminal Signals Telemetry, Attempt-Log Tracker & Multimodal Decision Fusion Engine *(merges Role 1: Face & Fusion + Role 2: Signals & Attempt-Log)*
- **GitHub Handle**: `@face-fusion-lead` *(also monitors legacy `@signals-lead` mentions on `tools/board`)*
- **Branch Prefix**: `feat/face-fusion-*` / `feat/signals-*`
- **Runtime & Conda Environment**: Unified `whack-face` (Python 3.10 / 3.11 for CV/ML, terminal hooks, git state & log analyzers) + Node.js 20+ (for the VS Code extension)
- **Core Mission**:
  1. **Face, Body & 3D Gaze (`face/`)**: Detect developer affective states (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`/`eureka`) calibrated relative to a neutral baseline, recognize upper-body posture & hand-to-face gestures, and track 6-DOF head-invariant 3D eyeball screen coordinates `(screen_x_px, screen_y_px)` and focus zones (`screen_editor`, `screen_terminal`, `looking_away`, `eyes_closed`).
  2. **Editor, Terminal & Attempt-Log Signals (`signals/`)**: Build the VS Code telemetry extension, capture terminal command runs/failures, track the "last-known-good" git state & clean working-tree diff, strip secrets/tokens, and summarize user attempts (*"What Did They Try"*) for the Brain.
  3. **Multimodal Decision Fusion Engine**: Fuse real-time facial/body affect and exact screen gaze focus with editor/terminal behavioral signals, triggering context-aware nudges toward the Core server (`http://localhost:3000/events`).

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `face/**` *(and legacy `src/services/face/**`, `src/services/fusion/**`)*
- `signals/**` *(and legacy `src/extensions/vscode/**`, `src/services/signals/**`)*
- `tools/replay/**` (recorded session replay & calibration scripts)
- `tools/terminal_wrapper/**` (shell wrapper / fallback terminal hooks)
- `environment-face.yml`

### READ-ONLY (Reference Only - DO NOT EDIT):
- `core/` shared contracts (`src/contracts/**` — Shared JSON HTTP schemas)

### FORBIDDEN:
- `core/**`, `src/client/electron/**`, `src/services/core_server/**` (Role 4: Frontend & Core)
- `brain/**`, `demo-repo/**`, `src/services/brain/**` (Role 3: Brain)

---

## 3. Unified Anaconda & Runtime Environment Setup

All Python-based CV/ML pipelines (`face/`) and Python terminal/git/attempt-log analyzers (`signals/`) run inside the unified `whack-face` Conda environment, paired with Node.js 20+ for compiling the VS Code extension:

```bash
# 1. Create and activate unified Python environment
conda create -n whack-face python=3.10 tk -y
conda activate whack-face

# 2. Install Face, Body & 3D Gaze CV/ML stack (face/)
pip install mediapipe opencv-python numpy pillow requests

# 3. Install Signals, Git State & Attempt-Log stack (when starting signals/)
pip install gitpython pydantic

# 4. Node.js 20+ runtime for VS Code telemetry extension (signals/)
# Either use system Node.js 20+ or install inside Conda:
conda install -c conda-forge nodejs=20 -y

# Optional: export unified environment
conda env export --no-builds > environment-face.yml
```

---

## 4. Stream Architecture & Multimodal Fusion

### Stream A — Face, Upper-Body & 3D Eyeball/Gaze Pipeline (`face/`)
- **Multi-Emotion Classification**: Calibrated relative to user neutral baseline (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`/`eureka`).
- **Upper-Body & Hand-to-Face Gestures**: MediaPipe Pose + Hand landmarks (`head_scratch`, `chin_rest`, `facepalm_or_rub`, `eye_rub`, `hands_thrown_up`, `clenched_fist`, `thumbs_up`, `mouth_cover`, `lean_in`, `shoulder_slump`, `shoulder_shrug`, `head_tilt`, `head_shake`, `head_nod`) fused via Noisy-OR with facial AU blendshapes, guarded against talking and hand occlusion.
- **6-DOF Head-Invariant 3D Gaze Tracking**: MediaPipe 478-landmark iris + dark-pupil refinement + 4×4 metric head transformation matrix + tilted-screen VOR calibration outputting `(screen_x_px, screen_y_px)` and zone (`screen_editor`, `screen_terminal`, `looking_away`, `eyes_closed`).

### Stream B — OS Cursor, Text Caret (`|`), Editor, Terminal & Attempt-Log Signals (`signals/`)
- **3-Coordinate Spatial & Idle/Dwell Telemetry**:
  1. **Mouse Cursor Coordinate `(mouse_x_px, mouse_y_px)`**: Live screen position, `mouse_dwell_ms`, and frustration wiggle detection.
  2. **Keyboard / Text Cursor (Caret `|`) Coordinate `(caret_x_px, caret_y_px)`**: Live vertical text-bar screen position (via Win32 `GetGUIThreadInfo` + MSAA `OBJID_CARET` / editor APIs), `keyboard_dwell_ms`, Undo/Backspace bursts, and same-line erase oscillation.
  3. **Eye-Tracking Gaze Coordinate `(gaze_x_px, gaze_y_px)`**: Paired from `face/` (`screen_x_px`, `screen_y_px`, `fixation_ms`, `zone`).
- **VS Code / Terminal / Git Telemetry**: Tracks active file switching / ping-pong edits, diagnostics, terminal `exitCode` / `errorHash` / `tailOutput`, rolling 10-minute **Attempt-Log Summarizer**, and **Last-Known-Good Git Tracker** (with secret redaction).

### Stream C — Unified Signal + Facial Status Engine & Active Role 3 Brain Caller
- **Active Caller Architecture (Role 1+2 $\rightarrow$ Role 3 Brain)**:
  - **Role 1+2 (`face/` + `signals/`) is the ACTIVE side** that evaluates combined facial + behavioral signals continuously to determine the unified **User Status** and **actively invokes Role 3 (`@brain-lead`)** (via direct function/callback invocation or HTTP POST to Brain `/diagnose` on `:5002`, while Role 3 waits passively to be called).
  - Every active call to Role 3 Brain passes **4 core inputs** (3 screen coordinates + the 6 emotions):
    1. **`mouse_cursor`**: `{ "x": mouse_x_px, "y": mouse_y_px }` (coordinate of the mouse cursor)
    2. **`keyboard_cursor`**: `{ "x": caret_x_px, "y": caret_y_px }` (coordinate of the keyboard text cursor / vertical bar `|`)
    3. **`eye_gaze`**: `{ "x": gaze_x_px, "y": gaze_y_px }` (coordinate of the 3D eye-tracking point on screen)
    4. **`emotions`** *(all 6 calibrated emotion scores `0.0–1.0`)*: `{ "confused", "frustrated", "surprised", "flow", "fatigued", "delighted" }`
    *(alongside the fused `user_status`, `confidence`, `reasons`, and `attemptLog` context).*
  - **Strict AFK Rule**: If `user_status == "afk_away"` (sustained missing face $\ge 3\text{s}$ or sustained head turned completely away $\ge 4\text{s}$ while cursors are idle), **never call the main Brain**.
- **Combined Signal + Facial User Status Rules**:
  1. **`stuck_frozen_at_caret` $\rightarrow$ Active Brain Call**: Keyboard cursor `|` & mouse cursor idle/dwell sustained while `eye_gaze` stays pinned near `keyboard_cursor` (or error region) **and** facial/body affect shows `confused` / `frustrated` (`head_scratch`, `chin_rest`), or severe prolonged freeze on code.
  2. **`stuck_erase_thrash` / `stuck_attention_split` / `frustrated_blocked` $\rightarrow$ Active Brain Call**: Rapid Undo/Backspace erase-loops on the `keyboard_cursor` line, `eye_gaze` / `mouse_cursor` ping-ponging far from a parked `keyboard_cursor`, mouse frustration wiggle, or repeated terminal `errorHash` failures combined with facial/body distress.
  3. **Anti-Annoyance & Context Disambiguation Guards (Do NOT Call Brain)**:
     - **`afk_away`**: Mouse & keyboard cursor idle **+** (`looking_away` or no face detected) $\rightarrow$ user is away from desk; pause timers.
     - **`calm_reading`**: Mouse & keyboard cursor idle **+** `flow` facial state **+** `eye_gaze` smoothly scanning lines $\rightarrow$ reading docs/code; suppress call.
     - **`focused_flow`**: Steady `keyboard_cursor` progression + `eye_gaze` near `keyboard_cursor` + `flow` $\rightarrow$ Do-Not-Disturb lock.
     - **`talking_suppressed`**: `talking == true` $\rightarrow$ collaborating/speaking; suppress call.
     - **Post-call / post-dismissal cooldown** (e.g., 45s min interval / 3-min dismissal cooldown) & **`eureka_resolved`** reset on `delighted` / passing run.

---

## 5. Combined 10-Hour Roadmap & Key Checkpoints

| Time | Face, Body & Gaze (`face/`) | Signals & Attempt-Log (`signals/`) |
| :--- | :--- | :--- |
| **0:00 - 0:45** | **Stub Phase**: Emit mock `"face"` events to `http://localhost:3000/events` every 10s. | **Stub Phase**: Emit canned `"editor"` (`activeFile: "app.py"`, `undoCount: 3`, `diagnostics: { count: 2 }`) & `"terminal"` mock events to `/events`. |
| **0:45 - 2:30** | Camera capture + on-device MediaPipe face/pose/hand + 3D eyeball/gaze tracking + local visual debug GUI. | **0:45 - 2:00**: VS Code Extension skeleton (`isUndo`, `isRedo`, cursor idle dwell, active file switches, diagnostic counts). |
| **2:30 - 3:30** | Neutral face baseline + 3D tilted-screen/VOR gaze calibration. Rolling-window smoothing; ignore frames when talking or looking away. | **2:00 - 3:30**: Terminal telemetry via VS Code shell integration API (exit codes & output tail) + fallback shell wrapper (`tools/terminal_wrapper/`). |
| **3:30 - 4:00** | **CHECKPOINT A (Face)**: Calibrated real-time `"face"` events streaming to Core `/events` (on state change + 3s heartbeat). | **CHECKPOINT A (Signals)**: Live `"editor"` & `"terminal"` events flowing to Core + initial **Attempt-Log Summarizer** active. |
| **4:00 - 6:30** | **Unified Multimodal Fusion Engine**: Combine `"face"`, `"editor"`, and `"terminal"` streams with gaze-zone correlation, confidence weighting, and 3-min post-dismissal cooldown. | **4:00 - 5:30**: **Last-Known-Good Tracker** (git snapshot on exit code `0` + clean working-tree diff). <br>**5:30 - 7:00**: Derived fusion features (repeated-error hash, undo rate, ping-pong edits) + secret/token redaction. |
| **6:30 - 8:30** | Build session replay tool (`tools/replay/`). Test fusion thresholds against scripted "stuck" vs "smooth" sessions under venue lighting. | **7:00 - 8:30**: Record scripted editor/terminal test sessions for replay; harden edge cases (empty git repos, logs > 100 lines). |
| **8:30 - 10:00** | Record backup demo video, verify "face-fallback toggle" (camera off while preserving behavior/hotkey demo), and rehearse live stuck-state trigger as demo operator. | *(Shared live demo rehearsal & operator duties)* |

---

## 6. GitHub Issue Board Protocol (`tools/board`)

### On Stub Completion (Minute 45):
```bash
./tools/board post "[Role 1: Face+Signals] @frontend-core Mock face, editor, and terminal heartbeats streaming to /events on :3000. Unified env 'whack-face' locked."
```

### On Checkpoint A (Hour 4):
```bash
./tools/board post "[Role 1: Face+Signals] @brain-lead @frontend-core Checkpoint A reached. Calibrated face/gaze pipeline + live editor/terminal watcher & attempt-log summarizer emitting to :3000/events."
```

### Handing off Last-Known-Good Diff to Brain:
```bash
./tools/board post "[Role 1: Face+Signals] @brain-lead Last-known-good git diff calculator active. Brain can consume diff & attempt summary payloads via /events or module export."
```

### When Ready with Unified Fusion Logic:
```bash
./tools/board post "[Role 1: Face+Signals] @brain-lead @frontend-core Multimodal fusion engine ready. Tested with repeated-error decay, undo bursts, 3D gaze zones, and face/body confusion triggers."
```

### Polling for Instructions:
```bash
./tools/board wait @face-fusion-lead
# (Also check legacy @signals-lead mentions via `./tools/board read 10`)
```

---

## 7. Operating Rules & Human-in-the-Loop Protocol
- **Always Confirm the Plan**: Every time, please confirm the plan with me before starting or executing tasks.
- **Clarify When Unclear**: If there is anything unclear, you should always ask me and confirm the details with me.
- **Always Check GitHub Issues & Agent Board**: Proactively check open GitHub Issues (`gh issue list --state open`), recent board messages (`./tools/board read 10`), and remote branches (`git fetch origin`) before starting new tasks and whenever committing/pushing code, so contract changes, requests, or blockers from `@brain-lead` and `@frontend-core` are caught immediately.
- **Cross-Agent Communication**: If you have any question with other AI agents, or when pushing a milestone, raise an issue or post an update using `tools/board` / GitHub Issues.
