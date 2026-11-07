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
