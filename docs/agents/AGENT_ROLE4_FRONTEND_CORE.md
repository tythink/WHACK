# AGENT SPEC — ROLE 4: FRONTEND & INTEGRATION LEAD (TEAM LEAD)

## 1. Identity & Role
- **Role**: Team Lead, Core Ingestion Server & Electron HUD Owner
- **GitHub Handle**: `@frontend-core`
- **Branch Prefix**: `feat/core-ui-*`
- **Environment**: Node.js 20+ / Electron (or `whack-frontend` Conda env)
- **Core Mission**: Maintain the single source of truth (`src/contracts/`), orchestrate event ingest on localhost, build the Electron floating HUD/panel, and coordinate the manual-hotkey baseline.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/services/core_server/**` (Localhost ingest server)
- `src/client/electron/**` (Electron floating chip & HUD)
- `src/contracts/**` (Event contracts and envelope specs)
- `package.json`, root configs, `.gitignore`

### FORBIDDEN:
- `src/services/face/**` (Role 1)
- `src/services/signals/**` (Role 2)
- `src/services/brain/**` (Role 3)

---

## 3. Environment Setup

```bash
# Option A: Conda Node environment
conda create -n whack-frontend nodejs=20 -y
conda activate whack-frontend

# Option B: Standard Node.js environment
npm install
```

---

## 4. 10-Hour Roadmap & Key Checkpoints

| Time | Goal & Deliverables |
| :--- | :--- |
| **0:00 - 0:45** | **Kickoff & Contract Lock**: Freeze `src/contracts/events.ts`. Build empty stubs for `/events`, `/diagnose`, `/feedback`. Setup repo layout and verify permissions on all machines. |
| **0:45 - 2:00** | Core Server: Ingest HTTP POST on `http://localhost:3000/events`, keep in-memory ring-buffer history of last 100 events, broadcast over WebSocket (`ws://localhost:3000/ws`). |
| **2:00 - 4:00** | Electron app: Floating always-on-top chip window, system tray icon, and global hotkey (`Cmd+Shift+K` / `Ctrl+Shift+K`) triggering manual `/diagnose`. |
| **4:00** | **CHECKPOINT A**: Manual hotkey $\rightarrow$ trigger Brain $\rightarrow$ stream markdown diagnosis into Electron panel. **Demo baseline guaranteed.** |
| **4:00 - 6:00** | Panel UI: Streaming markdown renderer, clickable file/line numbers, 3-level hint toggle, "I'm not confused" feedback button, and live confusion score HUD gauge. |
| **6:00 - 8:00** | Visual polish, calm distraction-free CSS/dark mode, and prepare the 3-slide pitch deck. |
| **8:00 - 10:00** | Feature freeze at 8:30. Run 3 live demo rehearsals, record backup screencast video, submit project. |

---

## 5. Contract Lock (`src/contracts/events.ts`)

```typescript
export interface BaseEvent<T = any> {
  source: "face" | "editor" | "terminal" | "hotkey";
  type: string;
  timestamp: number;
  payload: T;
}

export interface FacePayload {
  state: "calm" | "confused";
  score: number; // 0.0 - 1.0 (smoothed brow/squint index)
  confidence: number;
}

export interface EditorPayload {
  activeFile: string;
  isUndo: boolean;
  isRedo: boolean;
  diagnosticsCount: number;
  cursorDwellMs: number;
}

export interface TerminalPayload {
  command: string;
  exitCode: number;
  durationMs: number;
  errorHash?: string;
  tailOutput: string;
}

export interface DiagnosisResponse {
  summary: string;
  file?: string;
  lines?: [number, number];
  quip: string;
  uncertain: boolean;
  hintLevel: "nudge" | "hint" | "full_explanation";
}
```

---

## 6. GitHub Issue Board Protocol (`tools/board`)

### Announcing Contract Freeze (Hour 0:45):
```bash
./tools/board post "[Role 4: Core] @face-fusion-lead @signals-lead @brain-lead Contract locked in src/contracts/events.ts. Stubs active on http://localhost:3000."
```

### Broadcasting Checkpoint A Success:
```bash
./tools/board post "[Role 4: Core] Checkpoint A complete! Manual hotkey triggers streaming diagnosis into Electron HUD. Base demo is locked."
```

### Polling Board:
```bash
./tools/board wait @frontend-core
```
