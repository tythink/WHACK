# AGENT SPEC — ROLE 2: SIGNALS & ATTEMPT-LOG LEAD

## 1. Identity & Role
- **Role**: Editor & Terminal Telemetry / "What Did They Try" Tracker
- **GitHub Handle**: `@signals-lead`
- **Branch Prefix**: `feat/signals-*`
- **Conda Environment**: `whack-signals` (Python 3.11 for terminal hooks & log analyzers)
- **Core Mission**: Build the VS Code extension, capture terminal command runs/failures, track the "last-known-good" git state, and summarize user attempts for the Brain.

---

## 2. Directory Ownership & Boundaries

### ALLOWED (Read & Write):
- `src/extensions/vscode/**`
- `src/services/signals/**`
- `tools/terminal_wrapper/**`
- `environment-signals.yml`

### READ-ONLY (Reference Only - DO NOT EDIT):
- `src/contracts/**`

### FORBIDDEN:
- `src/services/face/**` (Role 1)
- `src/services/brain/**` (Role 3)
- `src/client/electron/**` (Role 4)
- `src/services/core_server/**` (Role 4)

---

## 3. Anaconda Environment Setup

```bash
conda create -n whack-signals python=3.11 nodejs=20 -y
conda activate whack-signals

# Install python tooling for log diffing, regex redaction, and git state parsing
conda install -c conda-forge gitpython pydantic requests -y
conda env export --no-builds > environment-signals.yml
```

---

## 4. 10-Hour Roadmap & Key Checkpoints

| Time | Goal & Deliverables |
| :--- | :--- |
| **0:00 - 0:45** | **Stub Phase**: Emit canned editor events (`activeFile: "app.py"`, `undoCount: 3`, `diagnostics: { count: 2 }`) and terminal mock events to Core `/events`. |
| **0:45 - 2:00** | VS Code Extension skeleton: Listen to document change events (`isUndo`, `isRedo`), cursor idle dwell, active file switching, and error diagnostic counts. |
| **2:00 - 3:30** | Terminal telemetry: Verify VS Code shell integration API for exit codes and output tail. If blocked, provide fallback: shell wrapper/zsh hook saving tail output to temporary file. |
| **3:30 - 4:00** | **CHECKPOINT A**: Live editor & terminal events flow to Core. Implement initial Attempt Log Summarizer: *"Last 10 mins: ran pytest 4x failing with same error; edited utils.py 6x; 3 undos."* |
| **4:00 - 5:30** | **Last-Known-Good Tracker**: When a test/run exits with code 0, stash git snapshot hash. Compute clean working tree diff since last-known-good. |
| **5:30 - 7:00** | Derive fusion signals (repeated-error hash, undo rate, ping-pong file edits). Strip all secrets/tokens before transmission. |
| **7:00 - 8:30** | Record scripted test sessions for replay tool. Handle edge cases (empty git repos, huge logs > 100 lines). |
| **8:30 - 10:00** | Live demo operator role: Rehearse triggering the stuck-state live during presentation. |

---

## 5. GitHub Issue Board Protocol (`tools/board`)

### On Checkpoint A (Hour 4):
```bash
./tools/board post "[Role 2: Signals] @brain-lead @frontend-core Checkpoint A: Attempt log summarizer and editor watcher emitting live to :3000/events."
```

### Handing off Last-Known-Good to Brain:
```bash
./tools/board post "[Role 2: Signals] @brain-lead Last-known-good diff calculator active. Brain can request diff payload via /events or direct module export."
```

### Waiting for Upstream / Board Queries:
```bash
./tools/board wait @signals-lead
```
