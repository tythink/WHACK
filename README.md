# WHACK

A multimodal developer assistant that detects confusion through local facial expression analysis and editor/terminal telemetry, offering timely, context-aware nudges.

## Repository Structure

```
your-team-repo/
├── .gitignore
├── README.md
│
├── tools/
│   └── board                           <-- Executable bash script
│
├── docs/
│   └── agents/                         <-- Keep all role guides version-controlled
│       ├── AGENT_ROLE1_FACE_FUSION.md
│       ├── AGENT_ROLE2_SIGNALS.md
│       ├── AGENT_ROLE3_BRAIN.md
│       └── AGENT_ROLE4_FRONTEND_CORE.md
│
├── core/                               <-- Owned by Role 4 (Core Server & Electron)
├── signals/                            <-- Owned by Role 2 (VS Code Extension & Attempt Log)
├── brain/                              <-- Owned by Role 3 (LLM Multi-modal Diagnosis)
├── demo-repo/                          <-- Owned by Role 3 (Planted bug test repo)
└── face/                               <-- Owned by Role 1 (MediaPipe & Fusion Engine)
```

## Roles & Ownership

- **Role 1 (Face & Fusion)**: Owned by `@face-fusion-lead` (`face/`, `tools/replay/`). Detects confusion via MediaPipe landmarks/blendshapes and fuses with editor/terminal behaviors.
- **Role 2 (Signals)**: Owned by `@signals-lead` (`signals/`). VS Code extension and attempt log tracker for editor & terminal telemetry.
- **Role 3 (Brain)**: Owned by `@brain-lead` (`brain/`, `demo-repo/`). Multimodal LLM diagnosis and codebase context reasoning.
- **Role 4 (Core & Frontend - Lead)**: Owned by `@frontend-core` (`core/`, shared contracts). Ingestion server and Electron HUD.

## Agent Communication

Agents coordinate asynchronously using `./tools/board`:

```bash
./tools/board post "<message>"
./tools/board read [count]
./tools/board wait @your-handle
```
