import {
  app,
  BrowserWindow,
  desktopCapturer,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  screen,
  shell,
  systemPreferences,
  Tray,
} from "electron";
import fs from "fs";
import os from "os";
import path from "path";
import { createCoreServer } from "../../services/core_server/server";

const CORE_PORT = Number(process.env.PORT || 3000);
const CORE_URL = `http://127.0.0.1:${CORE_PORT}`;

let chipWindow: BrowserWindow | null = null;
let panelWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let simulatorTimer: NodeJS.Timeout | null = null;
let embeddedServer: ReturnType<typeof createCoreServer> | null = null;

function resolveRendererHtml(fileName: string): string {
  const srcPath = path.resolve(
    __dirname,
    "../../../src/client/electron/renderer",
    fileName
  );
  if (fs.existsSync(srcPath)) {
    return srcPath;
  }
  return path.join(__dirname, "renderer", fileName);
}

/**
 * Ensures `http://localhost:3000` is running.
 * If an external `npm start` Core Server is already bound to port 3000, we reuse it;
 * otherwise we boot the Core Server inside Electron automatically!
 */
async function ensureCoreServerRunning(): Promise<void> {
  try {
    const res = await fetch(`${CORE_URL}/health`);
    if (res.ok) {
      console.log(`[WHACK Electron] Connected to existing Core Server at ${CORE_URL}`);
      return;
    }
  } catch {
    // Not running yet -> start embedded Core Server
  }

  try {
    embeddedServer = createCoreServer(100);
    await embeddedServer.listen(CORE_PORT);
    console.log(`[WHACK Electron] Embedded Core Server started on ${CORE_URL}`);
  } catch (err) {
    console.warn(`[WHACK Electron] Core Server port ${CORE_PORT} check:`, err);
  }
}

function checkMediaPermissions(): void {
  try {
    if (process.platform === "darwin" || process.platform === "win32") {
      const camStatus = systemPreferences.getMediaAccessStatus("camera");
      const screenStatus = systemPreferences.getMediaAccessStatus("screen");
      console.log(
        `[WHACK Permissions] Camera: ${camStatus}, Screen Recording: ${screenStatus}`
      );
    }
  } catch {
    // Ignore on platforms that don't support getMediaAccessStatus
  }
}

function createChipWindow(): void {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth } = primaryDisplay.workAreaSize;

  const chipWidth = 248;
  const chipHeight = 52;
  const margin = 20;

  chipWindow = new BrowserWindow({
    width: chipWidth,
    height: chipHeight,
    x: screenWidth - chipWidth - margin,
    y: margin,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  chipWindow.setAlwaysOnTop(true, "screen-saver");
  chipWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  chipWindow.loadFile(resolveRendererHtml("chip.html"));

  chipWindow.on("closed", () => {
    chipWindow = null;
  });
}

function createPanelWindow(): void {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth } = primaryDisplay.workAreaSize;

  const panelWidth = 430;
  const panelHeight = 690;
  const margin = 20;

  panelWindow = new BrowserWindow({
    width: panelWidth,
    height: panelHeight,
    x: screenWidth - panelWidth - margin,
    y: 80,
    frame: false,
    transparent: false,
    backgroundColor: "#0c0c0e",
    resizable: true,
    alwaysOnTop: true,
    show: false,
    hasShadow: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  panelWindow.setAlwaysOnTop(true, "floating");
  panelWindow.loadFile(resolveRendererHtml("panel.html"));

  panelWindow.on("close", (e) => {
    if (!(app as unknown as { isQuitting?: boolean }).isQuitting) {
      e.preventDefault();
      panelWindow?.hide();
    }
  });
}

function createTray(): void {
  // Generate a clean 16x16 indigo/emerald status icon in memory so no external icon file is required
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="8" fill="#1e1b4b"/>
    <circle cx="16" cy="16" r="8" fill="#10b981"/>
    <circle cx="16" cy="16" r="4" fill="#a7f3d0"/>
  </svg>`;
  const icon = nativeImage.createFromDataURL(
    `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`
  );

  tray = new Tray(icon);
  tray.setToolTip("WHACK — Confusion-Detecting Coding Assistant");

  const updateTrayMenu = () => {
    const contextMenu = Menu.buildFromTemplate([
      {
        label: "Diagnose Now (Ctrl+Shift+K)",
        click: () => triggerManualDiagnosis("hint"),
      },
      {
        label: panelWindow?.isVisible() ? "Hide HUD Panel" : "Show HUD Panel",
        click: () => togglePanelWindow(),
      },
      { type: "separator" },
      {
        label: simulatorTimer
          ? "Stop Telemetry Simulator"
          : "Start Telemetry Simulator",
        click: () => {
          toggleDemoSimulator();
          updateTrayMenu();
        },
      },
      {
        label: "Dismiss / Snooze 3m",
        click: () => {
          fetch(`${CORE_URL}/feedback`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "not_confused" }),
          }).catch(() => undefined);
        },
      },
      {
        label: "Toggle Camera Pause",
        click: () => {
          fetch(`${CORE_URL}/feedback`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "toggle_camera" }),
          }).catch(() => undefined);
        },
      },
      { type: "separator" },
      {
        label: "Quit WHACK",
        click: () => {
          (app as unknown as { isQuitting?: boolean }).isQuitting = true;
          app.quit();
        },
      },
    ]);
    tray?.setContextMenu(contextMenu);
  };

  updateTrayMenu();
  tray.on("click", () => togglePanelWindow());
}

function showPanelWindow(): void {
  if (!panelWindow) {
    createPanelWindow();
  }
  panelWindow?.show();
  panelWindow?.focus();
}

function togglePanelWindow(): boolean {
  if (!panelWindow) {
    createPanelWindow();
  }
  if (panelWindow?.isVisible()) {
    panelWindow.hide();
    return false;
  } else {
    panelWindow?.show();
    panelWindow?.focus();
    return true;
  }
}

async function captureScreenBase64(): Promise<string | undefined> {
  try {
    const sources = await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: { width: 1280, height: 720 },
    });
    if (sources.length > 0 && !sources[0].thumbnail.isEmpty()) {
      return sources[0].thumbnail.toPNG().toString("base64");
    }
  } catch {
    // Ignore if screen capture permission is not granted
  }
  return undefined;
}

async function triggerManualDiagnosis(hintLevel = "hint"): Promise<void> {
  const screenshot = await captureScreenBase64();
  showPanelWindow();

  // Log hotkey event in Core Server
  fetch(`${CORE_URL}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: "hotkey",
      type: "manual_stuck_hotkey",
      timestamp: Date.now(),
      payload: { shortcut: "CommandOrControl+Shift+K", hintLevel },
    }),
  }).catch(() => undefined);

  // Tell panel renderer to show loading state, then call /diagnose
  panelWindow?.webContents.send("whack:do-diagnose", hintLevel);

  try {
    await fetch(`${CORE_URL}/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        triggerType: "hotkey",
        hintLevel,
        screenshot,
        fusionReasons: ["Manual 'I'm stuck' hotkey (Ctrl+Shift+K)"],
      }),
    });
  } catch (err) {
    console.error("[WHACK Electron] Failed to trigger /diagnose:", err);
  }
}

const DEMO_SEQUENCE = [
  {
    source: "face",
    type: "face_heartbeat",
    payload: { state: "calm", score: 0.16, confidence: 0.95 },
  },
  {
    source: "editor",
    type: "file_edit",
    payload: {
      activeFile: "utils.py",
      isUndo: false,
      isRedo: false,
      diagnosticsCount: 1,
      cursorDwellMs: 1400,
      topDiagnostics: ["Line 11: Off-by-one in range(0, total_items - window_size + 2)"],
      cursorLine: 11,
    },
  },
  {
    source: "terminal",
    type: "command_exit",
    payload: {
      command: "pytest tests/",
      exitCode: 1,
      durationMs: 710,
      errorHash: "idx_err_offset_end_6",
      tailOutput: "IndexError: Offset end 6 exceeds data length 5",
    },
  },
  {
    source: "editor",
    type: "undo_burst",
    payload: {
      activeFile: "utils.py",
      isUndo: true,
      isRedo: false,
      diagnosticsCount: 2,
      cursorDwellMs: 5200,
      cursorLine: 18,
    },
  },
  {
    source: "face",
    type: "nudge",
    payload: {
      state: "confused",
      score: 0.87,
      confidence: 0.92,
      severity: "normal",
      reasons: [
        "pytest failed 3x: Offset end 6 exceeds data length 5",
        "sustained brow furrow (0.87)",
        "3 rapid undos in utils.py",
      ],
    },
  },
];

let demoStep = 0;

function toggleDemoSimulator(): boolean {
  if (simulatorTimer) {
    clearInterval(simulatorTimer);
    simulatorTimer = null;
  } else {
    const emitStep = () => {
      const item = DEMO_SEQUENCE[demoStep % DEMO_SEQUENCE.length];
      demoStep++;
      fetch(`${CORE_URL}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...item, timestamp: Date.now() }),
      }).catch(() => undefined);
    };
    emitStep();
    simulatorTimer = setInterval(emitStep, 2400);
  }

  const running = Boolean(simulatorTimer);
  panelWindow?.webContents.send("whack:simulator-status", running);
  chipWindow?.webContents.send("whack:simulator-status", running);
  return running;
}

function registerIpcAndShortcuts(): void {
  ipcMain.handle("whack:toggle-panel", () => togglePanelWindow());
  ipcMain.handle("whack:show-panel", () => {
    showPanelWindow();
    return true;
  });
  ipcMain.handle("whack:hide-panel", () => {
    panelWindow?.hide();
    return false;
  });
  ipcMain.handle("whack:trigger-diagnose", (_e, hintLevel?: string) =>
    triggerManualDiagnosis(hintLevel || "hint")
  );
  ipcMain.handle("whack:toggle-simulator", () => toggleDemoSimulator());
  ipcMain.handle("whack:get-simulator-status", () => Boolean(simulatorTimer));
  ipcMain.handle(
    "whack:open-vscode",
    async (_e, filePath: string, line?: number) => {
      let absPath = filePath;
      if (!path.isAbsolute(filePath)) {
        const candidates = [
          process.env.WHACK_DEMO_REPO
            ? path.resolve(process.env.WHACK_DEMO_REPO, filePath)
            : "",
          process.env.DEMO_REPO_DIR
            ? path.resolve(process.env.DEMO_REPO_DIR, filePath)
            : "",
          path.resolve(os.homedir(), "whack-demo-repo", filePath),
          path.resolve(process.cwd(), "..", "demo-repo", filePath),
          path.resolve(process.cwd(), "..", filePath),
        ].filter(Boolean);

        absPath =
          candidates.find((candidate) => fs.existsSync(candidate)) ||
          candidates[candidates.length - 1];
      }
      const targetUri = `vscode://file/${absPath.replace(/\\/g, "/")}${
        line ? `:${line}` : ""
      }`;
      await shell.openExternal(targetUri);
      return targetUri;
    }
  );

  // Register Global "I'm Stuck" Hotkey (`Ctrl+Shift+K` on Windows/Linux, `Cmd+Shift+K` on macOS)
  const registered = globalShortcut.register("CommandOrControl+Shift+K", () => {
    triggerManualDiagnosis("hint");
  });

  if (registered) {
    console.log(
      "[WHACK Electron] Global hotkey registered: CommandOrControl+Shift+K"
    );
  } else {
    console.warn(
      "[WHACK Electron] Failed to register CommandOrControl+Shift+K"
    );
  }
}

app.whenReady().then(async () => {
  checkMediaPermissions();
  await ensureCoreServerRunning();
  createChipWindow();
  createPanelWindow();
  createTray();
  registerIpcAndShortcuts();

  // Support headless smoke verification via `--verify-smoke`
  if (process.argv.includes("--verify-smoke")) {
    setTimeout(() => {
      console.log("✓ [Electron Smoke Test] Chip & Panel windows initialized cleanly");
      (app as unknown as { isQuitting?: boolean }).isQuitting = true;
      app.quit();
    }, 1200);
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (simulatorTimer) {
    clearInterval(simulatorTimer);
  }
  if (embeddedServer) {
    embeddedServer.close().catch(() => undefined);
  }
});
