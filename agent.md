# Multi-Agent Specification & Architecture

This repository operates under a multi-agent structure with 4 specialized roles coordinating via `tools/board`.

## Role Directory & Specifications

Full role specifications are version-controlled in `docs/agents/`:

1. **Role 1: Face & Fusion Lead** (`@face-fusion-lead`)
   - Spec: [docs/agents/AGENT_ROLE1_FACE_FUSION.md](docs/agents/AGENT_ROLE1_FACE_FUSION.md)
   - Scope: `face/`, `tools/replay/`, `environment-face.yml`
   - Mission: Face landmark/emotion pipeline & decision fusion engine.

2. **Role 2: Signals & Attempt-Log Lead** (`@signals-lead`)
   - Spec: [docs/agents/AGENT_ROLE2_SIGNALS.md](docs/agents/AGENT_ROLE2_SIGNALS.md)
   - Scope: `signals/`, `environment-signals.yml`
   - Mission: Editor & terminal telemetry, attempt log summarizer.

3. **Role 3: Brain Lead** (`@brain-lead`)
   - Spec: [docs/agents/AGENT_ROLE3_BRAIN.md](docs/agents/AGENT_ROLE3_BRAIN.md)
   - Scope: `brain/`, `demo-repo/`, `environment-brain.yml`
   - Mission: Multimodal LLM diagnosis, codebase context & personality engine.

4. **Role 4: Frontend & Integration Lead** (`@frontend-core`)
   - Spec: [docs/agents/AGENT_ROLE4_FRONTEND_CORE.md](docs/agents/AGENT_ROLE4_FRONTEND_CORE.md)
   - Scope: `core/`, contracts, Electron HUD, team lead.
   - Mission: Localhost ingestion server, Electron floating HUD, contract lock.

## Communication Protocol

All agents interact asynchronously via GitHub issue comments using the `tools/board` script:

```bash
./tools/board post "<message>"
./tools/board read [count]
./tools/board wait @handle
```
