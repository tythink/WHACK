# HACKATHON AGENT SPECIFICATIONS & SYSTEM ARCHITECTURE

## Role Allocation Overview
- **Role 1 (Face & Fusion)**:
  - **Stream B (Face)**: Emotion Detect Lead (`@emotion-lead`) - `src/services/emotion/**`
  - **Stream D (Fusion)**: Orchestrator & Antigravity Lead (`@core-antigravity`) - `src/orchestrator/**`, `src/services/core_agent/**`
- **Role 2 (Action)**:
  - **Stream C**: Action Detect Lead (`@action-lead`) - `src/services/action/**`
- **Role 3 (Frontend)**:
  - **Stream A**: Frontend Lead (`@frontend-lead`) - `src/frontend/**`

---

# AGENT SPEC — STREAM A: FRONTEND LEAD

## 1. Identity & Role
- **Role**: Frontend / UX Engineer
- **Handle**: `@frontend-lead`
- **Branch Prefix**: `feat/ui-*`
- **Core Mission**: Build a reactive dashboard and overlay HUD displaying real-time webcam feed, emotion scores, action metrics, and a dramatic "Antigravity Agent Takeover" takeover status indicator.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/frontend/**`
- `public/**`
- `package.json` (frontend dependencies only)

### READ-ONLY (Reference Only - NEVER EDIT):
- `src/contracts/**` (Shared event contracts)

### FORBIDDEN (Never touch or inspect deeply):
- `src/services/emotion/**`
- `src/services/action/**`
- `src/services/core_agent/**`
- `src/orchestrator/**`

---

## 3. Communication Protocol (Issue Board)

Use `./tools/board` from project root for communication.

### When starting work:
```bash
./tools/board post "[Stream A: Frontend] Starting work on HUD overlay and WebSocket subscriber."
```

### When depending on backend streams:
If emotion/action streams are not ready yet, **do not block**. Use mock data conforming to `src/contracts/events.ts`.

### When ready for integration:
```bash
./tools/board post "[Stream A: Frontend] @core-antigravity UI is listening for takeover events on WS /ws/stream. Branch: feat/ui-dashboard ready."
```

### When waiting on updates:
```bash
./tools/board wait @frontend-lead
```

---

## 4. Key Responsibilities
1. Real-time stress/panic gauge meters (0–100%).
2. Visual trigger alerts: Screen border glows red when takeover threshold is approached.
3. Antigravity Takeover Banner: An animated terminal/overlay view showing active autonomous operations being carried out.
4. Fallback mock simulator for testing without hardware feeds connected.

---

# AGENT SPEC — STREAM B: EMOTION DETECT LEAD

## 1. Identity & Role
- **Role**: Computer Vision & Emotion Recognition Engineer
- **Handle**: `@emotion-lead`
- **Branch Prefix**: `feat/emotion-*`
- **Core Mission**: Capture webcam video, extract facial landmarks, classify facial expressions (frustration, anger, stress, panic), and stream standardized `EmotionEvent` payloads.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/emotion/**`
- `models/emotion/**`
- `requirements-emotion.txt` or emotion-specific config

### READ-ONLY (Reference Only - NEVER EDIT):
- `src/contracts/**` (Shared event schemas)

### FORBIDDEN:
- `src/frontend/**`
- `src/services/action/**`
- `src/services/core_agent/**`
- `src/orchestrator/**`

---

## 3. Communication Protocol (Issue Board)

Use `./tools/board` from the root directory.

### When starting:
```bash
./tools/board post "[Stream B: Emotion] Initializing OpenCV/MediaPipe/FER pipeline in src/services/emotion."
```

### When ready for downstream consumer (Orchestrator):
```bash
./tools/board post "[Stream B: Emotion] @core-antigravity Emotion stream is live. Emitting EmotionEvent on local port 5001. Commit: <HASH>"
```

### Polling incoming messages:
```bash
./tools/board wait @emotion-lead
```

---

## 4. Key Deliverables & Rules
1. Fast local inference (>= 15 FPS) using lightweight models (e.g., MediaPipe Face Mesh, DeepFace, or ONNX FER).
2. Calculate and emit an exponential moving average `stressIndex` (0.0 to 1.0).
3. Strictly format event output to match `EmotionEvent` in `src/contracts/events.ts`.
4. Provide a `--mock` flag that produces synthetic escalating stress curves for demo testing.

---

# AGENT SPEC — STREAM C: ACTION DETECT LEAD

## 1. Identity & Role
- **Role**: OS Telemetry & User Behavior Engineer
- **Handle**: `@action-lead`
- **Branch Prefix**: `feat/action-*`
- **Core Mission**: Detect frantic user behavior: rapid tab switching (Alt-Tab / Cmd-Tab), aggressive click bursts, jittery mouse patterns, and active window context.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/action/**`
- `requirements-action.txt` or action-specific config

### READ-ONLY (Reference Only - NEVER EDIT):
- `src/contracts/**` (Shared event schemas)

### FORBIDDEN:
- `src/frontend/**`
- `src/services/emotion/**`
- `src/services/core_agent/**`
- `src/orchestrator/**`

---

## 3. Communication Protocol (Issue Board)

Use `./tools/board` from the repo root.

### When starting:
```bash
./tools/board post "[Stream C: Action] Telemetry daemon initiated (monitoring keypress rate and window focus)."
```

### When handing off to Orchestrator:
```bash
./tools/board post "[Stream C: Action] @core-antigravity Action telemetry ready on port 5002. Emitting ActionEvent with panicScore. Commit: <HASH>"
```

### Polling incoming commands:
```bash
./tools/board wait @action-lead
```

---

## 4. Key Deliverables & Rules
1. Non-invasive hook to measure rapid window switching and cursor jitter.
2. Calculate `tabSwitchRatePerMin` and `panicScore` (0.0 to 1.0).
3. Support macOS / Windows / Linux fallback hooks or simulated event feeds.
4. Output must match `ActionEvent` in `src/contracts/events.ts`.

---

# AGENT SPEC — STREAM D: ORCHESTRATOR & ANTIGRAVITY LEAD

## 1. Identity & Role
- **Role**: Systems Architect & Antigravity Agent Specialist
- **Handle**: `@core-antigravity`
- **Branch Prefix**: `feat/antigravity-*`
- **Core Mission**: Ingest real-time streams from Emotion and Action detectors, fuse them into a trigger score, invoke the Antigravity SDK to launch an autonomous computer-use task, and notify the Frontend.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/core_agent/**`
- `src/orchestrator/**`
- Root integration scripts (`docker-compose.yml`, root `Makefile`, etc.)

### READ-ONLY (Reference Only - NEVER EDIT):
- `src/contracts/**`

### FORBIDDEN:
- `src/frontend/**` (Stream A)
- `src/services/emotion/**` (Stream B)
- `src/services/action/**` (Stream C)

---

## 3. Communication Protocol (Issue Board)

Use `./tools/board` to orchestrate cross-team handoffs.

### When starting:
```bash
./tools/board post "[Stream D: Core] Fusion orchestrator listening for Stream B (:5001) and Stream C (:5002)."
```

### Requesting changes from upstream:
```bash
./tools/board post "[Stream D: Core] @emotion-lead Please ensure confidence threshold is clamped >= 0.5 in EmotionEvent."
```

### Notifying Frontend of live pipeline:
```bash
./tools/board post "[Stream D: Core] @frontend-lead Antigravity execution pipeline wired. Broadcasting TakeoverTriggerEvent on WS :8080."
```

### Polling incoming updates:
```bash
./tools/board wait @core-antigravity
```

---

## 4. Key Deliverables & Rules
1. Multi-stream fusion engine: 
   $$fusionScore = (w_1 \cdot stressIndex) + (w_2 \cdot panicScore)$$
2. When $fusionScore > 0.8$, immediately trigger the Antigravity Agent SDK session.
3. Provide prompt templates for Antigravity explaining the situation: *"User is overwhelmed struggling to solve task X. Take control, inspect open window, and complete the blocking step."*
4. Broadcast state transitions to Stream A (`src/frontend`).
