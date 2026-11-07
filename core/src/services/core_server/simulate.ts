/**
 * Mock Sensor Stream Simulator (`npm run simulate`)
 * Sends realistic Face (Role 1) and Editor/Terminal (Role 2) events to
 * `http://localhost:3000/events` every 2.5 seconds so you can test the
 * Electron HUD and Live Debug Timeline solo!
 */

const CORE_URL = process.env.CORE_URL || "http://localhost:3000";

const SAMPLE_EVENTS = [
  {
    source: "face",
    type: "face_heartbeat",
    payload: { state: "calm", score: 0.18, confidence: 0.94 },
  },
  {
    source: "editor",
    type: "file_edit",
    payload: {
      activeFile: "demo-repo/utils.py",
      isUndo: false,
      isRedo: false,
      diagnosticsCount: 1,
      cursorDwellMs: 1200,
      topDiagnostics: ["Line 42: IndexError risk in range(len(items) + 1)"],
      cursorLine: 42,
    },
  },
  {
    source: "terminal",
    type: "command_exit",
    payload: {
      command: "pytest tests/test_utils.py",
      exitCode: 1,
      durationMs: 640,
      errorHash: "idx_err_utils_42",
      tailOutput: "E   IndexError: list index out of range\nutils.py:42: IndexError",
    },
  },
  {
    source: "editor",
    type: "undo_burst",
    payload: {
      activeFile: "demo-repo/utils.py",
      isUndo: true,
      isRedo: false,
      diagnosticsCount: 2,
      cursorDwellMs: 5400,
      cursorLine: 42,
    },
  },
  {
    source: "face",
    type: "face_state_change",
    payload: { state: "confused", score: 0.82, confidence: 0.91 },
  },
  {
    source: "face",
    type: "nudge",
    payload: {
      state: "confused",
      score: 0.88,
      confidence: 0.93,
      severity: "normal",
      reasons: [
        "pytest failed 3x with same IndexError",
        "sustained brow furrow (0.88)",
        "3 rapid undos in utils.py",
      ],
    },
  },
];

async function startSimulation() {
  console.log(`[Simulator] Sending mock events to ${CORE_URL}/events ...`);
  let idx = 0;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const template = SAMPLE_EVENTS[idx % SAMPLE_EVENTS.length];
    const event = {
      ...template,
      timestamp: Date.now(),
    };

    try {
      const res = await fetch(`${CORE_URL}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(event),
      });
      console.log(
        `[Simulator] POST /events (${event.source}:${event.type}) -> HTTP ${res.status}`
      );
    } catch (err) {
      console.error(
        `[Simulator] Failed to reach ${CORE_URL}/events. Is the Core Server running?`,
        err
      );
    }

    idx++;
    await new Promise((r) => setTimeout(r, 2500));
  }
}

startSimulation();
