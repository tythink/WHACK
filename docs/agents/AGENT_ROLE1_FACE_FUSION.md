# AGENT SPEC — ROLE 1: FACE & FUSION LEAD

## 1. Identity & Role
- **Role**: Face Landmark/Emotion Pipeline & Decision Fusion Engine
- **GitHub Handle**: `@face-fusion-lead`
- **Branch Prefix**: `feat/face-fusion-*`
- **Conda Environment**: `whack-face` (Python 3.10 / 3.11)
- **Core Mission**: Detect genuine confusion via local face blendshapes/landmarks (calibrated relative to neutral baseline), fuse face signals with editor/terminal behaviors, and trigger nudges toward the Core server.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/face/**`
- `src/services/fusion/**` (or inside Core if TypeScript)
- `tools/replay/**` (recorded session replay & calibration scripts)
- `environment-face.yml`

### READ-ONLY (Reference Only - DO NOT EDIT):
- `src/contracts/**` (Shared JSON HTTP schemas)

### FORBIDDEN:
- `src/client/electron/**` (Role 4)
- `src/services/core_server/**` (Role 4)
- `src/extensions/vscode/**` (Role 2)
- `src/services/brain/**` (Role 3)

---

## 3. Anaconda Environment Setup

Run and maintain all Python-based CV/ML dependencies in the isolated conda environment:

```bash
# Create and activate environment
conda create -n whack-face python=3.10 -y
conda activate whack-face

# Install computer vision & inference stack
conda install -c conda-forge opencv mediapipe numpy requests -y
# Optional: export environment
conda env export --no-builds > environment-face.yml
```

---

## 4. 10-Hour Roadmap & Key Checkpoints

| Time | Goal & Deliverables |
| :--- | :--- |
| **0:00 - 0:45** | **Stub Phase**: Emit mock event `{ "source": "face", "state": "confused", "score": 0.85, "confidence": 0.9 }` via HTTP POST to `http://localhost:3000/events` every 10s. |
| **0:45 - 2:30** | Camera capture + on-device MediaPipe face mesh (brow lowering, squint, head pose). Build a quick local visual debug window. |
| **2:30 - 3:30** | 20-second calibration routine against neutral face. Smooth scores over rolling window; ignore frames when talking or looking away. |
| **3:30 - 4:00** | **CHECKPOINT A**: Real face events streaming to Core server (`/events`) at throttled rate (on change + 3s heartbeat). |
| **4:00 - 6:30** | **Fusion Engine**: Consume face, editor, and terminal events: <br>1. *High behavior score alone* $\rightarrow$ Nudge.<br>2. *Sustained brow lower + moderate behavior* $\rightarrow$ Nudge.<br>3. *Face alone sustained high* $\rightarrow$ Soft nudge only.<br>4. *Post-dismissal cooldown* (3 mins). |
| **6:30 - 8:30** | Build session replay tool (`tools/replay/`). Test thresholds against scripted "stuck" vs "smooth" sessions under venue lighting. |
| **8:30 - 10:00** | Record backup demo video and test "face-fallback toggle" (disabling camera while preserving behavior hotkey demo). |

---

## 5. GitHub Issue Board Protocol (`tools/board`)

### On Stub Completion (Minute 45):
```bash
./tools/board post "[Role 1: Face] @frontend-core Mock face heartbeat streaming to /events on :3000. Conda env 'whack-face' locked."
```

### On Checkpoint A (Hour 4):
```bash
./tools/board post "[Role 1: Face] @signals-lead @frontend-core Checkpoint A reached. Real face landmark pipeline calibrated and emitting. Branch: feat/face-fusion-v1."
```

### When Ready with Fusion Logic:
```bash
./tools/board post "[Role 1: Face] @signals-lead @brain-lead Fusion engine ready. Tested with repeated-error decay and brow-furrow triggers."
```

### Polling for Instructions:
```bash
./tools/board wait @face-fusion-lead
```
