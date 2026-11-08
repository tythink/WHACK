"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.WhackServiceManager = void 0;
const child_process_1 = require("child_process");
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
/**
 * Manages 1-click launching, stopping, and API-key configuration for the local
 * WHACK Python services (`brain.server` on `:5002` and `signals/prototype_signals_gui.py`)
 * directly from inside VS Code — zero manual terminal setup required.
 */
class WhackServiceManager {
    extensionPath;
    workspaceRoots;
    brainUrl;
    callbacks;
    brainProc = null;
    sensorProc = null;
    startingStack = false;
    output;
    constructor(extensionPath, workspaceRoots, brainUrl, callbacks) {
        this.extensionPath = extensionPath;
        this.workspaceRoots = workspaceRoots;
        this.brainUrl = brainUrl;
        this.callbacks = callbacks;
        this.output = vscode.window.createOutputChannel("WHACK Live Services");
    }
    getWhackRoot() {
        const cfg = vscode.workspace.getConfiguration("whack");
        const explicit = (cfg.get("whackRootPath", "") || "").trim();
        const home = os.homedir();
        const candidates = [];
        if (explicit) {
            candidates.push(explicit);
        }
        candidates.push(path.resolve(this.extensionPath, ".."));
        for (const ws of this.workspaceRoots) {
            candidates.push(ws);
            candidates.push(path.resolve(ws, ".."));
        }
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            candidates.push(folder.uri.fsPath);
        }
        candidates.push(path.join(home, "Desktop", "WHACK"), path.join(home, "WHACK"), path.join(home, "Documents", "GitHub", "WHACK"), path.join(home, "Projects", "WHACK"));
        for (const cand of candidates) {
            try {
                const resolved = path.resolve(cand);
                if (fs.existsSync(path.join(resolved, "brain", "server.py")) &&
                    fs.existsSync(path.join(resolved, "signals", "prototype_signals_gui.py"))) {
                    return resolved;
                }
            }
            catch {
                // Ignore invalid path candidate
            }
        }
        return null;
    }
    getPythonPath(whackRoot) {
        const cfg = vscode.workspace.getConfiguration("whack");
        const explicit = (cfg.get("pythonPath", "") || "").trim();
        if (explicit && fs.existsSync(explicit)) {
            return explicit;
        }
        const home = os.homedir();
        const isWin = process.platform === "win32";
        const condaBins = isWin
            ? [
                path.join(home, "anaconda3", "envs", "whack-face", "python.exe"),
                path.join(home, "miniconda3", "envs", "whack-face", "python.exe"),
                path.join(home, "anaconda3", "envs", "whack-brain", "python.exe"),
                path.join(home, "miniconda3", "envs", "whack-brain", "python.exe"),
            ]
            : [
                path.join(home, "anaconda3", "envs", "whack-face", "bin", "python"),
                path.join(home, "miniconda3", "envs", "whack-face", "bin", "python"),
                path.join(home, "opt", "anaconda3", "envs", "whack-face", "bin", "python"),
                path.join(home, "anaconda3", "envs", "whack-brain", "bin", "python"),
            ];
        if (whackRoot) {
            condaBins.push(isWin
                ? path.join(whackRoot, "brain", ".venv", "Scripts", "python.exe")
                : path.join(whackRoot, "brain", ".venv", "bin", "python"));
        }
        for (const bin of condaBins) {
            if (fs.existsSync(bin)) {
                return bin;
            }
        }
        return isWin ? "python" : "python3";
    }
    inspectEnvKey(whackRoot) {
        const envKeys = [
            ["GEMINI_API_KEY", "gemini"],
            ["GOOGLE_API_KEY", "gemini"],
            ["OPENAI_API_KEY", "openai"],
            ["ANTHROPIC_API_KEY", "anthropic"],
        ];
        for (const [k, prov] of envKeys) {
            const v = process.env[k];
            if (v && v.trim() && !v.includes("your_")) {
                return { hasKey: true, provider: prov };
            }
        }
        if (!whackRoot) {
            return { hasKey: false, provider: null };
        }
        const envFiles = [
            path.join(whackRoot, ".env"),
            path.join(whackRoot, "brain", ".env"),
        ];
        let explicitProv = null;
        const foundKeys = {};
        for (const envFile of envFiles) {
            if (!fs.existsSync(envFile))
                continue;
            try {
                const content = fs.readFileSync(envFile, "utf8");
                for (const rawLine of content.split(/\r?\n/)) {
                    let line = rawLine.trim().replace(/^\uFEFF/, "");
                    if (!line || line.startsWith("#"))
                        continue;
                    if (/^export\s+/i.test(line)) {
                        line = line.replace(/^export\s+/i, "").trim();
                    }
                    if (!line.includes("="))
                        continue;
                    const eqIdx = line.indexOf("=");
                    const k = line.slice(0, eqIdx).trim();
                    const v = line
                        .slice(eqIdx + 1)
                        .trim()
                        .replace(/^['"]|['"]$/g, "");
                    if (k === "LLM_PROVIDER" && v) {
                        explicitProv = v.toLowerCase();
                    }
                    if (v && !v.includes("your_") && !v.includes("paste_")) {
                        foundKeys[k] = v;
                    }
                }
            }
            catch {
                // Ignore read errors
            }
        }
        if (explicitProv === "gemini" && (foundKeys.GEMINI_API_KEY || foundKeys.GOOGLE_API_KEY)) {
            return { hasKey: true, provider: "gemini" };
        }
        if (explicitProv === "openai" && foundKeys.OPENAI_API_KEY) {
            return { hasKey: true, provider: "openai" };
        }
        if (explicitProv === "anthropic" && foundKeys.ANTHROPIC_API_KEY) {
            return { hasKey: true, provider: "anthropic" };
        }
        for (const [k, prov] of envKeys) {
            if (foundKeys[k]) {
                return { hasKey: true, provider: prov };
            }
        }
        return { hasKey: false, provider: null };
    }
    getState() {
        const whackRoot = this.getWhackRoot();
        const pythonPath = this.getPythonPath(whackRoot);
        const envInfo = this.inspectEnvKey(whackRoot);
        return {
            whackRootFound: Boolean(whackRoot),
            whackRootPath: whackRoot,
            pythonPath,
            hasEnvApiKey: envInfo.hasKey,
            configuredProvider: envInfo.provider,
            brainManagedRunning: Boolean(this.brainProc && !this.brainProc.killed),
            sensorManagedRunning: Boolean(this.sensorProc && !this.sensorProc.killed),
            startingStack: this.startingStack,
        };
    }
    emitState() {
        this.callbacks.onStateChanged(this.getState());
    }
    showLogs() {
        this.output.show(true);
    }
    /**
     * Prompts the user for an API key inside VS Code and saves it to `brain/.env`.
     */
    async configureApiKey() {
        const whackRoot = this.getWhackRoot();
        if (!whackRoot) {
            vscode.window.showErrorMessage("WHACK could not locate the WHACK repository folder. Set `whack.whackRootPath` in VS Code Settings.");
            return false;
        }
        const picked = await vscode.window.showQuickPick([
            {
                label: "$(sparkle) Google Gemini (Recommended)",
                description: "gemini-3.1-flash-lite-preview · Fast live vision + code reasoning",
                id: "gemini",
            },
            {
                label: "$(hubot) OpenAI",
                description: "gpt-4o-mini · Vision + tool-calling",
                id: "openai",
            },
            {
                label: "$(comment-discussion) Anthropic Claude",
                description: "claude-3-5-haiku-latest · Vision + tool-calling",
                id: "anthropic",
            },
        ], {
            title: "WHACK — Configure LLM Provider & API Key",
            placeHolder: "Select which model provider WHACK Brain (:5002) should use",
        });
        if (!picked)
            return false;
        const keyVarName = picked.id === "openai"
            ? "OPENAI_API_KEY"
            : picked.id === "anthropic"
                ? "ANTHROPIC_API_KEY"
                : "GEMINI_API_KEY";
        const defaultModel = picked.id === "openai"
            ? "gpt-4o-mini"
            : picked.id === "anthropic"
                ? "claude-3-5-haiku-latest"
                : "gemini-3.1-flash-lite-preview";
        const apiKey = await vscode.window.showInputBox({
            title: `Enter ${keyVarName} for WHACK Brain`,
            prompt: `Saved locally to ${path.join(whackRoot, "brain", ".env")} (git-ignored).`,
            placeHolder: picked.id === "openai"
                ? "sk-..."
                : picked.id === "anthropic"
                    ? "sk-ant-..."
                    : "Paste your Gemini API key...",
            password: true,
            ignoreFocusOut: true,
        });
        if (!apiKey || !apiKey.trim()) {
            return false;
        }
        const envPath = path.join(whackRoot, "brain", ".env");
        const cleanKey = apiKey
            .trim()
            .replace(/^(?:export\s+)?(?:GEMINI_API_KEY|GOOGLE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*/i, "")
            .replace(/^['"]|['"]$/g, "")
            .trim();
        const existingLines = fs.existsSync(envPath)
            ? fs.readFileSync(envPath, "utf8").split(/\r?\n/)
            : [];
        const updates = new Map([
            ["LLM_PROVIDER", picked.id],
            ["LLM_MODEL", defaultModel],
            [keyVarName, cleanKey],
        ]);
        const nextLines = [];
        for (const line of existingLines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) {
                nextLines.push(line);
                continue;
            }
            const k = trimmed.slice(0, trimmed.indexOf("=")).trim();
            if (updates.has(k)) {
                nextLines.push(`${k}=${updates.get(k)}`);
                updates.delete(k);
            }
            else {
                nextLines.push(line);
            }
        }
        for (const [k, v] of updates.entries()) {
            nextLines.push(`${k}=${v}`);
        }
        fs.writeFileSync(envPath, nextLines.filter((l, idx, arr) => !(idx === arr.length - 1 && l === "")).join("\n") + "\n", "utf8");
        this.output.appendLine(`[Config] Saved ${keyVarName} and LLM_PROVIDER=${picked.id} to ${envPath}`);
        this.emitState();
        // Hot-reload or restart Brain (:5002) even if it was started outside this VS Code session
        const healthBefore = await this.fetchBrainHealth();
        if (healthBefore.online) {
            const reloaded = await this.triggerBrainReloadEnv();
            if (!reloaded) {
                this.output.appendLine("[Brain] Existing :5002 server did not hot-reload .env; restarting :5002...");
                await this.stopBrainServer(true);
                await this.startBrainServer();
            }
            else {
                this.output.appendLine("[Brain] Hot-reloaded API key on running :5002 server!");
                this.emitState();
            }
        }
        else {
            const startNow = await vscode.window.showInformationMessage(`Saved ${keyVarName} to brain/.env! Start WHACK Brain (:5002) now?`, "Start Live Stack", "Start Brain Only");
            if (startNow === "Start Live Stack") {
                await this.startFullStack();
            }
            else if (startNow === "Start Brain Only") {
                await this.startBrainServer();
            }
        }
        return true;
    }
    async fetchBrainHealth() {
        try {
            const controller = new AbortController();
            const t = setTimeout(() => controller.abort(), 1400);
            const res = await fetch(`${this.brainUrl.replace(/\/$/, "")}/health`, {
                signal: controller.signal,
            });
            clearTimeout(t);
            if (!res.ok) {
                return { online: false, apiKeyConfigured: false };
            }
            const data = await res.json().catch(() => ({}));
            return {
                online: true,
                apiKeyConfigured: Boolean(data?.apiKeyConfigured),
            };
        }
        catch {
            return { online: false, apiKeyConfigured: false };
        }
    }
    async triggerBrainReloadEnv() {
        try {
            const controller = new AbortController();
            const t = setTimeout(() => controller.abort(), 1600);
            const res = await fetch(`${this.brainUrl.replace(/\/$/, "")}/reload-env`, {
                method: "POST",
                signal: controller.signal,
            });
            clearTimeout(t);
            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                if (data?.apiKeyConfigured) {
                    return true;
                }
            }
        }
        catch {
            // Fall through to health check
        }
        const after = await this.fetchBrainHealth();
        return after.online && after.apiKeyConfigured;
    }
    async isBrainPortOnline() {
        const h = await this.fetchBrainHealth();
        return h.online;
    }
    parseBrainPort() {
        try {
            return Number(new URL(this.brainUrl).port || 5002);
        }
        catch {
            return 5002;
        }
    }
    killListenerOnPort(port) {
        return new Promise((resolve) => {
            if (process.platform === "win32") {
                const ps = (0, child_process_1.spawn)("powershell", [
                    "-NoProfile",
                    "-Command",
                    `Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }`,
                ], { stdio: "ignore", windowsHide: true });
                ps.on("close", () => setTimeout(resolve, 350));
                ps.on("error", () => resolve());
            }
            else {
                const sh = (0, child_process_1.spawn)("sh", ["-c", `lsof -ti tcp:${port} | xargs kill -9 2>/dev/null || true`], {
                    stdio: "ignore",
                });
                sh.on("close", () => setTimeout(resolve, 350));
                sh.on("error", () => resolve());
            }
        });
    }
    async startBrainServer() {
        const whackRoot = this.getWhackRoot();
        const envInfo = this.inspectEnvKey(whackRoot);
        const existing = await this.fetchBrainHealth();
        if (existing.online) {
            if (envInfo.hasKey && !existing.apiKeyConfigured) {
                const hotReloaded = await this.triggerBrainReloadEnv();
                if (hotReloaded) {
                    this.output.appendLine("[Brain] :5002 hot-reloaded brain/.env successfully.");
                    this.emitState();
                    return true;
                }
                this.output.appendLine("[Brain] Restarting stale :5002 server so it loads brain/.env...");
                await this.stopBrainServer(true);
            }
            else {
                this.output.appendLine("[Brain] :5002 is already online.");
                this.emitState();
                return true;
            }
        }
        if (!whackRoot) {
            vscode.window.showErrorMessage("WHACK repo root not found. Open the WHACK folder or set `whack.whackRootPath` in Settings.");
            return false;
        }
        const pythonBin = this.getPythonPath(whackRoot);
        const port = String(this.parseBrainPort());
        this.output.appendLine(`[Brain] Starting: "${pythonBin}" -m brain.server --port ${port} (cwd=${whackRoot})`);
        try {
            const child = (0, child_process_1.spawn)(pythonBin, ["-m", "brain.server", "--port", port], {
                cwd: whackRoot,
                env: { ...process.env, PYTHONUNBUFFERED: "1" },
                windowsHide: true,
            });
            this.brainProc = child;
            this.emitState();
            child.stdout?.on("data", (chunk) => {
                this.output.append(`[Brain] ${chunk.toString("utf8")}`);
            });
            child.stderr?.on("data", (chunk) => {
                this.output.append(`[Brain] ${chunk.toString("utf8")}`);
            });
            child.on("exit", (code) => {
                this.output.appendLine(`[Brain] Process exited (code=${code ?? "null"})`);
                if (this.brainProc === child) {
                    this.brainProc = null;
                    this.emitState();
                }
            });
            // Poll up to 8s for FastAPI startup
            for (let i = 0; i < 16; i++) {
                await new Promise((r) => setTimeout(r, 500));
                if (await this.isBrainPortOnline()) {
                    this.output.appendLine("[Brain] Server is healthy on :5002!");
                    this.emitState();
                    return true;
                }
                if (!this.brainProc) {
                    break;
                }
            }
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.output.appendLine(`[Brain] Failed to spawn: ${msg}`);
            this.brainProc = null;
            this.emitState();
        }
        return this.isBrainPortOnline();
    }
    async startSensorHud() {
        if (this.sensorProc && !this.sensorProc.killed) {
            return true;
        }
        const whackRoot = this.getWhackRoot();
        if (!whackRoot) {
            vscode.window.showErrorMessage("WHACK repo root not found. Open the WHACK folder or set `whack.whackRootPath` in Settings.");
            return false;
        }
        const pythonBin = this.getPythonPath(whackRoot);
        const scriptPath = path.join(whackRoot, "signals", "prototype_signals_gui.py");
        this.output.appendLine(`[Sensor] Launching Camera + OS Fusion HUD: "${pythonBin}" "${scriptPath}"`);
        try {
            const child = (0, child_process_1.spawn)(pythonBin, [scriptPath], {
                cwd: whackRoot,
                env: { ...process.env, PYTHONUNBUFFERED: "1" },
                windowsHide: false,
            });
            this.sensorProc = child;
            this.emitState();
            child.stdout?.on("data", (chunk) => {
                this.output.append(`[Sensor] ${chunk.toString("utf8")}`);
            });
            child.stderr?.on("data", (chunk) => {
                this.output.append(`[Sensor] ${chunk.toString("utf8")}`);
            });
            child.on("exit", (code) => {
                this.output.appendLine(`[Sensor] HUD closed (code=${code ?? "null"})`);
                if (this.sensorProc === child) {
                    this.sensorProc = null;
                    this.emitState();
                }
            });
            return true;
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.output.appendLine(`[Sensor] Failed to launch: ${msg}`);
            this.sensorProc = null;
            this.emitState();
            return false;
        }
    }
    async toggleSensorHud() {
        if (this.sensorProc && !this.sensorProc.killed) {
            await this.stopSensorHud();
        }
        else {
            await this.startSensorHud();
        }
    }
    async startFullStack() {
        if (this.startingStack)
            return;
        this.startingStack = true;
        this.emitState();
        try {
            const whackRoot = this.getWhackRoot();
            if (!whackRoot) {
                vscode.window.showErrorMessage("Could not find the WHACK folder (`brain/server.py`). Set `whack.whackRootPath` in VS Code Settings.");
                return;
            }
            const envInfo = this.inspectEnvKey(whackRoot);
            if (!envInfo.hasKey) {
                const action = await vscode.window.showWarningMessage("No LLM API key found in brain/.env yet. Set your Gemini / OpenAI / Anthropic key now so Gordon Ramsay can cook!", "🔑 Set API Key Now", "Launch Offline Anyway");
                if (action === "🔑 Set API Key Now") {
                    const configured = await this.configureApiKey();
                    if (!configured) {
                        return;
                    }
                }
                else if (!action) {
                    return;
                }
            }
            const brainOk = await this.startBrainServer();
            await this.startSensorHud();
            if (!brainOk) {
                vscode.window
                    .showWarningMessage("WHACK Brain (:5002) did not come online in time. Check the WHACK Live Services output log.", "Show Logs")
                    .then((sel) => {
                    if (sel === "Show Logs")
                        this.showLogs();
                });
            }
        }
        finally {
            this.startingStack = false;
            this.emitState();
        }
    }
    killTree(proc) {
        return new Promise((resolve) => {
            if (!proc || proc.killed || !proc.pid) {
                resolve();
                return;
            }
            const pid = proc.pid;
            if (process.platform === "win32") {
                const killer = (0, child_process_1.spawn)("taskkill", ["/PID", String(pid), "/T", "/F"], {
                    stdio: "ignore",
                    windowsHide: true,
                });
                killer.on("close", () => resolve());
                killer.on("error", () => {
                    try {
                        proc.kill();
                    }
                    catch {
                        // Ignore
                    }
                    resolve();
                });
            }
            else {
                try {
                    proc.kill("SIGTERM");
                }
                catch {
                    // Ignore
                }
                resolve();
            }
        });
    }
    async stopBrainServer(killAnyOnPort = false) {
        const p = this.brainProc;
        this.brainProc = null;
        await this.killTree(p);
        if (killAnyOnPort) {
            await this.killListenerOnPort(this.parseBrainPort());
        }
        this.emitState();
    }
    async stopSensorHud() {
        const p = this.sensorProc;
        this.sensorProc = null;
        await this.killTree(p);
        this.emitState();
    }
    async stopFullStack() {
        await Promise.all([this.stopSensorHud(), this.stopBrainServer()]);
    }
    dispose() {
        void this.stopFullStack();
        this.output.dispose();
    }
}
exports.WhackServiceManager = WhackServiceManager;
