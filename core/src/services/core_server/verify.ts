import assert from "assert";
import { WebSocket } from "ws";
import { WsMessage } from "../../contracts";
import { createCoreServer } from "./server";

async function runVerification() {
  process.env.USE_BRAIN_STUB = "true";
  const core = createCoreServer(100);
  const port = await core.listen(0); // Bind to free local port for isolated test
  const baseUrl = `http://127.0.0.1:${port}`;
  const wsUrl = `ws://127.0.0.1:${port}/ws`;

  const receivedWsMessages: WsMessage[] = [];

  try {
    // 1. Connect WebSocket client and wait for initial status message
    const ws = new WebSocket(wsUrl);
    ws.on("message", (raw) => {
      const msg = JSON.parse(raw.toString()) as WsMessage;
      receivedWsMessages.push(msg);
    });

    await new Promise<void>((resolve, reject) => {
      ws.on("open", () => resolve());
      ws.on("error", reject);
    });

    // Wait briefly for initial status push
    await new Promise((r) => setTimeout(r, 50));
    assert(
      receivedWsMessages.some((m) => m.type === "status"),
      "WebSocket did not receive initial status message"
    );
    console.log("✓ [1/6] WebSocket connected & received initial status");

    // 2. Test Role 1 Face Stub (flat format from AGENT_ROLE1_FACE_FUSION.md)
    const faceRes = await fetch(`${baseUrl}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "face",
        state: "confused",
        score: 0.85,
        confidence: 0.9,
      }),
    });
    assert.strictEqual(faceRes.status, 200);
    const faceJson = (await faceRes.json()) as { ok: boolean; eventCount: number };
    assert.strictEqual(faceJson.ok, true);
    assert.strictEqual(faceJson.eventCount, 1);
    console.log("✓ [2/6] Role 1 Face stub ingested & normalized");

    // 3. Test Role 2 Editor & Terminal events (envelope format from AGENT_ROLE4_FRONTEND_CORE.md)
    const editorRes = await fetch(`${baseUrl}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "editor",
        type: "edit",
        timestamp: Date.now(),
        payload: {
          activeFile: "utils.py",
          isUndo: true,
          isRedo: false,
          diagnosticsCount: 2,
          cursorDwellMs: 4500,
        },
      }),
    });
    assert.strictEqual(editorRes.status, 200);

    const termRes = await fetch(`${baseUrl}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "terminal",
        type: "command_exit",
        timestamp: Date.now(),
        payload: {
          command: "pytest",
          exitCode: 1,
          durationMs: 820,
          errorHash: "idx_err_42",
          tailOutput: "IndexError: list index out of range at utils.py:42",
        },
      }),
    });
    assert.strictEqual(termRes.status, 200);
    console.log("✓ [3/6] Role 2 Editor & Terminal events ingested");

    // 4. Test POST /diagnose (Brain streaming over WebSocket + final DiagnosisResponse)
    const diagRes = await fetch(`${baseUrl}/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        triggerType: "hotkey",
        hintLevel: "full_explanation",
      }),
    });
    assert.strictEqual(diagRes.status, 200);
    const diagJson = (await diagRes.json()) as {
      ok: boolean;
      diagnosis: { file: string; lines: [number, number]; quip: string };
    };
    assert.strictEqual(diagJson.ok, true);
    assert.strictEqual(diagJson.diagnosis.file, "utils.py");
    assert.deepStrictEqual(diagJson.diagnosis.lines, [40, 45]);

    await new Promise((r) => setTimeout(r, 50));
    assert(
      receivedWsMessages.some((m) => m.type === "diagnosis_chunk"),
      "Expected diagnosis_chunk over WebSocket"
    );
    assert(
      receivedWsMessages.some((m) => m.type === "diagnosis_done"),
      "Expected diagnosis_done over WebSocket"
    );
    console.log("✓ [4/6] POST /diagnose streamed chunks & completed over WebSocket");

    // 5. Test POST /feedback ("not_confused" cooldown + threshold nudge)
    const fbRes = await fetch(`${baseUrl}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "not_confused",
      }),
    });
    assert.strictEqual(fbRes.status, 200);
    const fbJson = (await fbRes.json()) as {
      ok: boolean;
      snapshot: { faceState: string; cooldownUntil: number | null };
    };
    assert.strictEqual(fbJson.ok, true);
    assert.strictEqual(fbJson.snapshot.faceState, "calm");
    assert(
      typeof fbJson.snapshot.cooldownUntil === "number" &&
        fbJson.snapshot.cooldownUntil > Date.now(),
      "Expected active cooldown after 'not_confused' feedback"
    );
    console.log("✓ [5/6] POST /feedback ('not_confused') applied cooldown & reset state");

    // 6. Test Role 3 Brain broadcast_to_core (`source: "brain"`, `type: "diagnosis"`)
    const brainEventRes = await fetch(`${baseUrl}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "brain",
        type: "diagnosis",
        timestamp: Date.now(),
        payload: {
          summary: "Off-by-one in utils.py",
          file: "utils.py",
          lines: [40, 45],
          quip: "Check your loop bounds!",
          uncertain: false,
          confidence: 0.96,
          hintLevel: "full_explanation",
          explanation: "Change `range(len(items) + 1)` to `range(len(items))`.",
        },
      }),
    });
    assert.strictEqual(brainEventRes.status, 200);
    console.log("✓ [6/7] Role 3 Brain broadcast_to_core event ingested & pushed to HUD");

    // 7. Test 100-Event Ring Buffer Cap
    for (let i = 0; i < 105; i++) {
      await fetch(`${baseUrl}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "terminal",
          type: "spam_test",
          timestamp: Date.now(),
          payload: {
            command: `echo ${i}`,
            exitCode: 0,
            durationMs: 10,
            tailOutput: `${i}`,
          },
        }),
      });
    }
    const historyRes = await fetch(`${baseUrl}/events`);
    const historyJson = (await historyRes.json()) as {
      events: unknown[];
      snapshot: { eventCount: number };
    };
    assert.strictEqual(historyJson.events.length, 100);
    assert.strictEqual(historyJson.snapshot.eventCount, 100);
    console.log("✓ [6/6] Ring buffer strictly capped at 100 events");

    ws.close();
    console.log("\n🎉 ALL CORE SERVER & CONTRACT CHECKS PASSED!");
  } finally {
    await core.close();
  }
}

runVerification().catch((err) => {
  console.error("❌ Verification failed:", err);
  process.exit(1);
});
