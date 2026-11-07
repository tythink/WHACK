# WHACK Core — Ingestion Server, Contracts & Electron HUD (Role 4)

Owned by `@frontend-core` (Role 4 — Frontend & Integration Lead).

Provides:
1. **Shared Event & API Contracts (`src/contracts/events.ts`)**: Canonical TypeScript schemas for `face`, `editor`, `terminal`, `hotkey`, and `brain` events.
2. **Core Ingestion & WebSocket Server (`src/services/core_server/`)**: Runs on `http://localhost:3000` with a 100-event in-memory ring buffer, real-time WebSocket broadcasting (`ws://localhost:3000/ws`), and seamless forwarding to Role 3's Brain service (`http://localhost:5002/diagnose`) with a built-in fallback streamer.
3. **Electron Floating HUD (`src/client/electron/`)**: Always-on-top floating status pill (`chip.html`), slide-out **incident.io × Grammarly ("Paper & Carbon")** diagnosis panel & live activity timeline (`panel.html`), system tray icon, cross-platform trigger screenshot capture (`desktopCapturer`), and global **`Ctrl+Shift+K`** (`Cmd+Shift+K`) shortcut.
4. **Judge Pitch Deck (`pitch-deck.html`)**: 4-slide interactive presentation in the **Paper & Carbon** aesthetic.

---

## Quickstart

```bash
cd core
npm install

# Launch the Electron HUD (automatically starts localhost:3000 Core Server if not already running)
npm run dev:electron
```

### Other Commands
```bash
# Run only the Core Server on http://localhost:3000
npm start

# Run the automated 7-step HTTP + WebSocket verification suite
npm run verify:server

# Stream simulated Face + Editor + Terminal telemetry to localhost:3000/events
npm run simulate
```

---

## HTTP & WebSocket Endpoints (`http://localhost:3000`)

| Method & Path | Description |
| :--- | :--- |
| `GET /health` | Health check and current confusion score / cooldown snapshot. |
| `POST /events` | Ingests a `BaseEvent` (`face`, `editor`, `terminal`, `hotkey`, `brain`), stores in 100-event ring buffer, and broadcasts to `ws://localhost:3000/ws`. |
| `GET /events` | Returns the last 100 events in memory plus the latest sensor snapshot. |
| `POST /diagnose` | Triggers Role 3 Brain (`http://localhost:5002/diagnose`) or built-in streaming stub and streams `diagnosis_chunk` + `diagnosis_done` over WebSocket. |
| `POST /feedback` | Applies user feedback (`not_confused`, `dismiss`, `snooze`, `toggle_camera`, `helpful`) with a 3-minute cooldown. |
| `ws://localhost:3000/ws` | Real-time WebSocket stream pushing `status`, `event_ingest`, `nudge`, `diagnosis_chunk`, and `diagnosis_done`. |
