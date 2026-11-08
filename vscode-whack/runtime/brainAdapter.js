"use strict";
/**
 * ============================================================================
 * LIVE BRAIN & CORE ADAPTER (`brainAdapter.ts`)
 * ============================================================================
 * Connects the VS Code Mode Extension directly to:
 *   1. Role 3's Brain LLM Server (`http://127.0.0.1:5002/diagnose`, `/chat`, `/wiki/update`)
 *   2. Role 4's Core Event/WS Server (`http://127.0.0.1:3000` & `ws://127.0.0.1:3000/ws`)
 *   3. Role 1+2's Face & Signals streams (`POST :3000/events`, `GET :3000/context`,
 *      & `signals/latest_brain_call.json`)
 *
 * Zero Mock Brain / simulator fallbacks: when `:5002` is offline or missing an API key,
 * reports honest `source: "offline"` status without fabricating files, lines, or diffs.
 * ============================================================================
 */
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
exports.WhackBrainBridge = void 0;
exports.normalizeBrainDiagnosis = normalizeBrainDiagnosis;
const crypto = __importStar(require("crypto"));
const fs = __importStar(require("fs"));
const http = __importStar(require("http"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const BRAIN_HTTP_TIMEOUT_MS = 50000;
function parseCitations(rawCitations, fallbackFile, fallbackLines) {
    if (!Array.isArray(rawCitations))
        return [];
    return rawCitations
        .map((c) => {
        if (!c || typeof c !== "object")
            return null;
        const obj = c;
        const cFile = obj.file || obj.file_path || fallbackFile;
        if (typeof cFile !== "string" || !cFile.trim())
            return null;
        let cLines;
        if (Array.isArray(obj.lines) && obj.lines.length >= 2) {
            const s = Number(obj.lines[0]) || 1;
            const e = Number(obj.lines[1]) || s;
            cLines = [s, Math.max(s, e)];
        }
        else if (typeof obj.line === "number") {
            cLines = [obj.line, obj.line];
        }
        else if (fallbackLines) {
            cLines = fallbackLines;
        }
        if (!cLines)
            return null;
        return {
            file: cFile,
            lines: cLines,
            snippet: typeof obj.snippet === "string" ? obj.snippet : undefined,
            note: typeof obj.note === "string"
                ? obj.note
                : typeof obj.message === "string"
                    ? obj.message
                    : undefined,
            replacement: typeof obj.replacement === "string" ? obj.replacement : undefined,
        };
    })
        .filter((x) => x !== null);
}
function parseSuggestedFix(rawFix, fallbackFile, fallbackLines) {
    if (!rawFix || typeof rawFix !== "object")
        return undefined;
    const fixObj = rawFix;
    const rep = typeof fixObj.newText === "string"
        ? fixObj.newText
        : typeof fixObj.new_text === "string"
            ? fixObj.new_text
            : typeof fixObj.replacement === "string"
                ? fixObj.replacement
                : undefined;
    if (typeof rep !== "string")
        return undefined;
    const fStart = fixObj.startLine ?? fixObj.start_line;
    const fEnd = fixObj.endLine ?? fixObj.end_line ?? fStart;
    const fixLines = Array.isArray(fixObj.lines) && fixObj.lines.length >= 2
        ? [Number(fixObj.lines[0]) || 1, Number(fixObj.lines[1]) || 1]
        : typeof fStart === "number"
            ? [Number(fStart) || 1, Number(fEnd) || Number(fStart) || 1]
            : fallbackLines;
    const findStr = typeof fixObj.oldText === "string"
        ? fixObj.oldText
        : typeof fixObj.old_text === "string"
            ? fixObj.old_text
            : typeof fixObj.find === "string"
                ? fixObj.find
                : undefined;
    const fileStr = typeof fixObj.file === "string" && fixObj.file.trim()
        ? fixObj.file
        : fallbackFile;
    return {
        file: fileStr,
        lines: fixLines,
        startLine: fixLines?.[0],
        endLine: fixLines?.[1],
        find: findStr,
        oldText: findStr,
        replacement: rep,
        newText: rep,
    };
}
/**
 * Adapts Role 3's Brain JSON response into our strict `DiagnosisResponse`.
 * Preserves `source: "offline"` honestly without inventing fake citations or patches.
 */
function normalizeBrainDiagnosis(rawInput) {
    const raw = (rawInput && typeof rawInput === "object" ? rawInput : {});
    const rawLevel = raw.hintLevel || raw.hint_level;
    const hintLevel = rawLevel === "nudge" || rawLevel === "hint" || rawLevel === "full_explanation"
        ? rawLevel
        : rawLevel === "full"
            ? "full_explanation"
            : "hint";
    const source = raw.source === "offline"
        ? "offline"
        : raw.source === "model"
            ? "model"
            : raw.error
                ? "offline"
                : "model";
    const diagnosisId = typeof raw.diagnosisId === "string"
        ? raw.diagnosisId
        : typeof raw.diagnosis_id === "string"
            ? raw.diagnosis_id
            : undefined;
    const errorStr = typeof raw.error === "string" && raw.error.trim() ? raw.error : undefined;
    const repoPath = typeof raw.repoPath === "string" && raw.repoPath.trim()
        ? raw.repoPath
        : typeof raw.repo_path === "string" && raw.repo_path.trim()
            ? raw.repo_path
            : undefined;
    const reasons = Array.isArray(raw.reasons)
        ? raw.reasons.map(String).filter((r) => r.trim().length > 0)
        : undefined;
    // Honest offline response: never attach fake file highlights or fake suggested fixes
    if (source === "offline") {
        return {
            diagnosisId,
            summary: typeof raw.summary === "string" && raw.summary.trim()
                ? raw.summary
                : "WHACK Brain (:5002) is offline.",
            file: undefined,
            lines: undefined,
            quip: typeof raw.quip === "string" && raw.quip.trim()
                ? raw.quip
                : "Start brain.server on port 5002 so I can actually see what you're cooking.",
            uncertain: true,
            confidence: typeof raw.confidence === "number" ? raw.confidence : 0,
            hintLevel,
            source: "offline",
            error: errorStr,
            repoPath,
            reasons,
            explanation: typeof raw.explanation === "string" && raw.explanation.trim()
                ? raw.explanation
                : errorStr,
            citations: [],
            suggestedFix: null,
        };
    }
    const fileCandidate = raw.file || raw.file_path || raw.filepath || raw.targetFile || undefined;
    const file = typeof fileCandidate === "string" && fileCandidate.trim()
        ? fileCandidate.trim()
        : undefined;
    let lines;
    const rawLines = raw.lines || raw.line_range || raw.lineRange;
    if (Array.isArray(rawLines) && rawLines.length >= 2) {
        const s = Number(rawLines[0]) || 1;
        const e = Number(rawLines[1]) || s;
        lines = [s, Math.max(s, e)];
    }
    else if (typeof raw.start_line === "number") {
        const s = Number(raw.start_line) || 1;
        const e = Number(raw.end_line ?? s) || s;
        lines = [s, Math.max(s, e)];
    }
    else if (typeof raw.line === "number") {
        lines = [raw.line, raw.line];
    }
    const citations = parseCitations(raw.citations || raw.locations || raw.places_to_correct, file, lines);
    if (citations.length === 0 && file && lines) {
        citations.push({
            file,
            lines,
            note: typeof raw.summary === "string" && raw.summary.trim()
                ? raw.summary
                : "Identified target lines",
        });
    }
    const rawFix = raw.suggestedFix || raw.suggested_fix || raw.structuredEdit || raw.fix;
    let suggestedFix = parseSuggestedFix(rawFix, file, lines);
    if (!suggestedFix && typeof raw.replacement === "string" && file && lines) {
        suggestedFix = {
            file,
            lines,
            startLine: lines[0],
            endLine: lines[1],
            replacement: raw.replacement,
            newText: raw.replacement,
        };
    }
    return {
        diagnosisId,
        summary: typeof raw.summary === "string" && raw.summary.trim()
            ? raw.summary
            : "WHACK Brain identified a code region to inspect.",
        file: file || citations[0]?.file,
        lines: lines || citations[0]?.lines,
        quip: typeof raw.quip === "string" && raw.quip.trim()
            ? raw.quip
            : "Computers do what you say, not what you want.",
        uncertain: Boolean(raw.uncertain),
        hintLevel,
        source: "model",
        error: errorStr,
        repoPath,
        reasons,
        confidence: typeof raw.confidence === "number" ? raw.confidence : 0.95,
        explanation: typeof raw.explanation === "string" ? raw.explanation : undefined,
        citations,
        suggestedFix: suggestedFix ?? null,
        tierVariants: raw.tierVariants,
    };
}
/**
 * Zero-dependency RFC-6455 WebSocket client using Node's built-in `http`/`net`/`crypto`.
 */
class LightweightWsClient {
    wsUrl;
    onMessage;
    onDisconnected;
    socket = null;
    closedByApp = false;
    reconnectTimer = null;
    rxBuffer = Buffer.alloc(0);
    constructor(wsUrl, onMessage, onDisconnected) {
        this.wsUrl = wsUrl;
        this.onMessage = onMessage;
        this.onDisconnected = onDisconnected;
    }
    connect() {
        this.closedByApp = false;
        let parsed;
        try {
            parsed = new URL(this.wsUrl);
        }
        catch {
            return;
        }
        const key = crypto.randomBytes(16).toString("base64");
        const req = http.request({
            hostname: parsed.hostname || "127.0.0.1",
            port: Number(parsed.port || 3000),
            path: parsed.pathname || "/ws",
            method: "GET",
            headers: {
                Connection: "Upgrade",
                Upgrade: "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": key,
            },
        });
        req.on("upgrade", (_res, socket) => {
            this.socket = socket;
            this.rxBuffer = Buffer.alloc(0);
            socket.on("data", (chunk) => {
                this.rxBuffer = Buffer.concat([this.rxBuffer, chunk]);
                this.drainFrames();
            });
            socket.on("close", () => {
                this.socket = null;
                if (!this.closedByApp) {
                    this.onDisconnected();
                    this.scheduleReconnect();
                }
            });
            socket.on("error", () => {
                socket.destroy();
            });
        });
        req.on("error", () => {
            if (!this.closedByApp) {
                this.onDisconnected();
                this.scheduleReconnect();
            }
        });
        req.end();
    }
    drainFrames() {
        while (this.rxBuffer.length >= 2) {
            const first = this.rxBuffer[0];
            const second = this.rxBuffer[1];
            const opcode = first & 0x0f;
            const isMasked = (second & 0x80) !== 0;
            let payloadLen = second & 0x7f;
            let offset = 2;
            if (payloadLen === 126) {
                if (this.rxBuffer.length < 4)
                    return;
                payloadLen = this.rxBuffer.readUInt16BE(2);
                offset = 4;
            }
            else if (payloadLen === 127) {
                if (this.rxBuffer.length < 10)
                    return;
                payloadLen = Number(this.rxBuffer.readBigUInt64BE(2));
                offset = 10;
            }
            const maskLen = isMasked ? 4 : 0;
            if (this.rxBuffer.length < offset + maskLen + payloadLen) {
                return;
            }
            let payload = this.rxBuffer.subarray(offset + maskLen, offset + maskLen + payloadLen);
            if (isMasked) {
                const mask = this.rxBuffer.subarray(offset, offset + 4);
                const unmasked = Buffer.alloc(payload.length);
                for (let i = 0; i < payload.length; i++) {
                    unmasked[i] = payload[i] ^ mask[i % 4];
                }
                payload = unmasked;
            }
            this.rxBuffer = this.rxBuffer.subarray(offset + maskLen + payloadLen);
            if (opcode === 0x1) {
                try {
                    const parsed = JSON.parse(payload.toString("utf8"));
                    this.onMessage(parsed);
                }
                catch {
                    // Ignore malformed JSON
                }
            }
            else if (opcode === 0x9 && this.socket) {
                const mask = crypto.randomBytes(4);
                const pong = Buffer.alloc(6);
                pong[0] = 0x8a;
                pong[1] = 0x80;
                mask.copy(pong, 2);
                this.socket.write(pong);
            }
            else if (opcode === 0x8 && this.socket) {
                this.socket.end();
            }
        }
    }
    scheduleReconnect() {
        if (this.reconnectTimer || this.closedByApp)
            return;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            if (!this.closedByApp) {
                this.connect();
            }
        }, 3000);
    }
    close() {
        this.closedByApp = true;
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.socket) {
            this.socket.destroy();
            this.socket = null;
        }
    }
}
class WhackBrainBridge {
    coreUrl;
    brainUrl;
    extensionPath;
    workspaceRoots;
    callbacks;
    wsClient = null;
    embeddedServer = null;
    embeddedWsSockets = new Set();
    healthTimer = null;
    outboxWatchers = [];
    outboxDebounceTimer = null;
    seenOutboxStates = new Set();
    lastOutboxCallId = 0;
    lastOutboxMtimeMs = 0;
    pendingNudgeTimer = null;
    lastExternalNudgeCallAt = 0;
    contextProvider = null;
    cachedExternalContext = {};
    disposed = false;
    status = {
        faceState: "calm",
        confusionScore: 0.08,
        cooldownUntil: null,
        cameraPaused: false,
        userStatusLabel: "Flow",
        brainOnline: false,
        askingBrain: false,
        coreMode: "offline",
    };
    constructor(coreUrl, brainUrl, extensionPath, workspaceRoots, callbacks) {
        this.coreUrl = coreUrl;
        this.brainUrl = brainUrl;
        this.extensionPath = extensionPath;
        this.workspaceRoots = workspaceRoots;
        this.callbacks = callbacks;
    }
    setContextProvider(provider) {
        this.contextProvider = provider;
    }
    async start() {
        await this.checkBrainHealth();
        if (this.disposed)
            return;
        await this.connectOrStartCore();
        if (this.disposed) {
            this.dispose();
            return;
        }
        this.watchSignalsOutbox();
        this.healthTimer = setInterval(() => {
            if (this.status.sensorActive &&
                this.status.lastSensorSeenAt &&
                Date.now() - this.status.lastSensorSeenAt > 7000) {
                this.status.sensorActive = false;
                this.callbacks.onStatusUpdate({ ...this.status });
            }
            this.checkBrainHealth().catch(() => undefined);
        }, 5000);
        this.healthTimer.unref?.();
    }
    async refreshHealth() {
        return this.checkBrainHealth();
    }
    async checkBrainHealth() {
        try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 2000);
            const res = await fetch(`${this.brainUrl.replace(/\/$/, "")}/health`, {
                signal: controller.signal,
            });
            clearTimeout(timer);
            const online = res.ok;
            let apiKeyConfigured = this.status.apiKeyConfigured;
            let brainProvider = this.status.brainProvider;
            let brainModel = this.status.brainModel;
            if (online) {
                try {
                    const data = await res.json();
                    if (typeof data?.apiKeyConfigured === "boolean") {
                        apiKeyConfigured = data.apiKeyConfigured;
                    }
                    if (typeof data?.provider === "string") {
                        brainProvider = data.provider;
                    }
                    if (typeof data?.model === "string") {
                        brainModel = data.model;
                    }
                }
                catch {
                    // Ignore malformed health body
                }
            }
            const changed = online !== this.status.brainOnline ||
                apiKeyConfigured !== this.status.apiKeyConfigured ||
                brainProvider !== this.status.brainProvider ||
                brainModel !== this.status.brainModel;
            this.status.brainOnline = online;
            this.status.apiKeyConfigured = apiKeyConfigured;
            this.status.brainProvider = brainProvider;
            this.status.brainModel = brainModel;
            if (changed) {
                this.callbacks.onStatusUpdate({ ...this.status });
            }
            return online;
        }
        catch {
            if (this.status.brainOnline) {
                this.status.brainOnline = false;
                this.callbacks.onStatusUpdate({ ...this.status });
            }
            return false;
        }
    }
    async connectOrStartCore() {
        try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 900);
            const res = await fetch(`${this.coreUrl}/health`, {
                signal: controller.signal,
            });
            clearTimeout(timer);
            if (res.ok) {
                this.status.coreMode = "external";
                this.callbacks.onStatusUpdate({ ...this.status });
                this.connectExternalWs();
                if (this.contextProvider) {
                    this.pushExternalContext(this.contextProvider());
                }
                return;
            }
        }
        catch {
            // External Core Server not running -> start embedded :3000 receiver inside VS Code
        }
        await this.startEmbeddedCoreReceiver();
    }
    connectExternalWs() {
        const wsUrl = this.coreUrl.replace(/^http/, "ws") + "/ws";
        this.wsClient?.close();
        this.wsClient = new LightweightWsClient(wsUrl, (msg) => this.handleIncomingCoreMessage(msg), () => {
            // Reconnects automatically via LightweightWsClient
        });
        this.wsClient.connect();
    }
    clearPendingNudgeTimer() {
        if (this.pendingNudgeTimer) {
            clearTimeout(this.pendingNudgeTimer);
            this.pendingNudgeTimer = null;
        }
    }
    handleIncomingCoreMessage(msg) {
        if (!msg || typeof msg !== "object")
            return;
        if (msg.type === "status") {
            this.status.faceState = msg.faceState === "confused" ? "confused" : "calm";
            this.status.confusionScore = Number(msg.confusionScore ?? 0);
            this.status.cooldownUntil = msg.cooldownUntil ?? null;
            this.status.cameraPaused = Boolean(msg.cameraPaused);
            this.status.userStatusLabel =
                this.status.faceState === "confused" || this.status.confusionScore >= 0.7
                    ? "Stuck"
                    : this.status.confusionScore >= 0.4
                        ? "Thinking"
                        : "Flow";
            this.callbacks.onStatusUpdate({ ...this.status });
        }
        else if (msg.type === "diagnosis_done" && msg.diagnosis) {
            this.clearPendingNudgeTimer();
            const normalized = normalizeBrainDiagnosis(msg.diagnosis);
            this.status.askingBrain = false;
            this.status.brainOnline = normalized.source === "model";
            this.callbacks.onStatusUpdate({ ...this.status });
            if (!this.isInCooldown()) {
                this.callbacks.onDiagnosisTriggered(normalized, "Brain Stream");
            }
        }
        else if (msg.type === "nudge") {
            this.status.faceState = "confused";
            this.status.confusionScore = Math.max(this.status.confusionScore, Number(msg.score ?? 0.82));
            this.status.userStatusLabel = "Stuck";
            this.callbacks.onStatusUpdate({ ...this.status });
            // When connected as WS client to an external Core server and a standalone `nudge`
            // arrives without an in-flight Brain call or subsequent `diagnosis_done`, trigger cleanly once.
            if (this.status.coreMode === "external" &&
                !this.isInCooldown() &&
                !this.status.askingBrain &&
                !this.pendingNudgeTimer &&
                Date.now() - this.lastExternalNudgeCallAt > 15000) {
                const nudgeReasons = Array.isArray(msg.reasons)
                    ? msg.reasons.map(String)
                    : undefined;
                this.pendingNudgeTimer = setTimeout(() => {
                    this.pendingNudgeTimer = null;
                    if (this.disposed ||
                        this.isInCooldown() ||
                        this.status.askingBrain) {
                        return;
                    }
                    this.lastExternalNudgeCallAt = Date.now();
                    this.status.askingBrain = true;
                    this.callbacks.onStatusUpdate({ ...this.status });
                    const ctx = this.contextProvider ? this.contextProvider() : {};
                    this.requestDiagnosis("hint", ctx, { reasons: nudgeReasons })
                        .then((diag) => {
                        this.status.askingBrain = false;
                        this.callbacks.onStatusUpdate({ ...this.status });
                        if (!this.isInCooldown()) {
                            this.callbacks.onDiagnosisTriggered(diag, "Core Fusion Nudge");
                        }
                    })
                        .catch(() => {
                        this.status.askingBrain = false;
                        this.callbacks.onStatusUpdate({ ...this.status });
                    });
                }, 1200);
                this.pendingNudgeTimer.unref?.();
            }
        }
    }
    buildContextPayload() {
        const live = this.contextProvider
            ? this.contextProvider()
            : this.cachedExternalContext;
        const defaultRoot = this.workspaceRoots[0]?.replace(/\\/g, "/") ?? null;
        const workspaceRoot = live.workspaceRoot || defaultRoot;
        return {
            ok: true,
            workspaceRoot,
            cooldownUntil: this.isInCooldown() ? this.status.cooldownUntil : null,
            cameraPaused: Boolean(this.status.cameraPaused),
            editorState: {
                activeFile: live.activeFile ?? "",
                cursorLine: live.cursorLine ?? 1,
                cursorColumn: live.cursorColumn ?? 1,
                selectionText: live.selectionText ?? "",
                diagnosticsCount: live.diagnosticsCount ?? 0,
                diagnosticsSummary: live.diagnosticsSummary ?? "",
            },
            terminalState: live.terminalCommand !== undefined
                ? {
                    command: live.terminalCommand,
                    exitCode: live.terminalExitCode ?? 0,
                    durationMs: live.terminalDurationMs ?? 0,
                    tailOutput: live.terminalTail ?? "",
                }
                : null,
        };
    }
    /**
     * Embedded `:3000` HTTP + WS server serving `/health`, `/context`, `/events`, `/diagnose`, `/feedback`.
     */
    async startEmbeddedCoreReceiver() {
        let parsedPort = 3000;
        try {
            parsedPort = Number(new URL(this.coreUrl).port || 3000);
        }
        catch {
            parsedPort = 3000;
        }
        const srv = http.createServer(async (req, res) => {
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
            res.setHeader("Access-Control-Allow-Headers", "Content-Type");
            if (req.method === "OPTIONS") {
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ ok: true }));
                return;
            }
            const url = new URL(req.url || "/", "http://127.0.0.1");
            if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({
                    ok: true,
                    service: "whack-vscode-embedded-core",
                    faceState: this.status.faceState,
                    confusionScore: this.status.confusionScore,
                    cooldownUntil: this.isInCooldown() ? this.status.cooldownUntil : null,
                    cameraPaused: this.status.cameraPaused,
                }));
                return;
            }
            if (req.method === "GET" && url.pathname === "/context") {
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify(this.buildContextPayload()));
                return;
            }
            const chunks = [];
            req.on("data", (c) => chunks.push(c));
            req.on("end", async () => {
                let body = {};
                try {
                    const raw = Buffer.concat(chunks).toString("utf8").trim();
                    body = raw ? JSON.parse(raw) : {};
                }
                catch {
                    res.writeHead(400, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ ok: false, error: "Invalid JSON" }));
                    return;
                }
                if (req.method === "POST" && url.pathname === "/context") {
                    if (body && typeof body === "object") {
                        this.cachedExternalContext = {
                            ...this.cachedExternalContext,
                            workspaceRoot: typeof body.workspaceRoot === "string"
                                ? body.workspaceRoot
                                : this.cachedExternalContext.workspaceRoot,
                            activeFile: body.editorState?.activeFile ?? this.cachedExternalContext.activeFile,
                            cursorLine: body.editorState?.cursorLine ?? this.cachedExternalContext.cursorLine,
                            cursorColumn: body.editorState?.cursorColumn ?? this.cachedExternalContext.cursorColumn,
                            selectionText: body.editorState?.selectionText ?? this.cachedExternalContext.selectionText,
                            diagnosticsCount: body.editorState?.diagnosticsCount ??
                                this.cachedExternalContext.diagnosticsCount,
                            diagnosticsSummary: body.editorState?.diagnosticsSummary ??
                                this.cachedExternalContext.diagnosticsSummary,
                            terminalCommand: body.terminalState?.command ?? this.cachedExternalContext.terminalCommand,
                            terminalExitCode: body.terminalState?.exitCode ?? this.cachedExternalContext.terminalExitCode,
                            terminalDurationMs: body.terminalState?.durationMs ??
                                this.cachedExternalContext.terminalDurationMs,
                            terminalTail: body.terminalState?.tailOutput ?? this.cachedExternalContext.terminalTail,
                        };
                    }
                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ ok: true }));
                    return;
                }
                if (req.method === "POST" && url.pathname === "/events") {
                    const source = body.source;
                    const payload = body.payload !== undefined ? body.payload : body;
                    if (source === "face" && !this.status.cameraPaused) {
                        const score = Number(payload.score ?? 0);
                        const rawUserStatus = typeof payload.user_status === "string" ? payload.user_status : "";
                        const state = payload.state === "confused" || score >= 0.7 ? "confused" : "calm";
                        this.status.faceState = state;
                        this.status.confusionScore = score;
                        this.status.sensorActive = true;
                        this.status.lastSensorSeenAt = Date.now();
                        this.status.userStatusRaw = rawUserStatus || undefined;
                        if (payload.emotions && typeof payload.emotions === "object") {
                            this.status.emotions = payload.emotions;
                        }
                        if (payload.gaze && typeof payload.gaze.zone === "string") {
                            this.status.gazeZone = payload.gaze.zone;
                        }
                        if (Array.isArray(payload.reasons)) {
                            this.status.liveReasons = payload.reasons.map(String);
                        }
                        if (rawUserStatus === "afk_away") {
                            this.status.userStatusLabel = "AFK Guard";
                        }
                        else if (rawUserStatus === "talking_suppressed") {
                            this.status.userStatusLabel = "Talking (DND)";
                        }
                        else if (rawUserStatus === "fatigued" ||
                            Number(payload.emotions?.fatigued ?? 0) >= 0.6) {
                            this.status.userStatusLabel = "Tired · Break";
                        }
                        else if (rawUserStatus === "frustrated_blocked") {
                            this.status.userStatusLabel = "Frustrated";
                        }
                        else if (rawUserStatus.startsWith("stuck_")) {
                            this.status.userStatusLabel = "Stuck";
                        }
                        else if (rawUserStatus === "confused_thinking" || score >= 0.4) {
                            this.status.userStatusLabel = "Thinking";
                        }
                        else {
                            this.status.userStatusLabel = state === "confused" ? "Stuck" : "Flow";
                        }
                        this.callbacks.onStatusUpdate({ ...this.status });
                        this.broadcastEmbeddedWs({
                            type: "status",
                            timestamp: Date.now(),
                            faceState: this.status.faceState,
                            confusionScore: this.status.confusionScore,
                            cooldownUntil: this.isInCooldown() ? this.status.cooldownUntil : null,
                            cameraPaused: this.status.cameraPaused,
                            eventCount: 1,
                        });
                    }
                    else if (source === "brain" &&
                        (body.type === "diagnosis" ||
                            body.type === "brain_diagnosis" ||
                            payload.summary)) {
                        const diag = normalizeBrainDiagnosis(payload);
                        this.status.askingBrain = false;
                        this.status.brainOnline = diag.source === "model";
                        this.callbacks.onStatusUpdate({ ...this.status });
                        this.broadcastEmbeddedWs({ type: "diagnosis_done", diagnosis: diag });
                        if (!this.isInCooldown()) {
                            this.callbacks.onDiagnosisTriggered(diag, "Role 3 Brain");
                        }
                    }
                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ ok: true }));
                    return;
                }
                if (req.method === "POST" && url.pathname === "/diagnose") {
                    const liveCtx = this.contextProvider ? this.contextProvider() : {};
                    const diag = await this.requestDiagnosis(body.hintLevel || "hint", {
                        ...liveCtx,
                        workspaceRoot: body.repoPath || liveCtx.workspaceRoot,
                        activeFile: body.editorState?.activeFile ?? liveCtx.activeFile,
                        cursorLine: body.editorState?.cursorLine ?? liveCtx.cursorLine,
                        cursorColumn: body.editorState?.cursorColumn ?? liveCtx.cursorColumn,
                        selectionText: body.editorState?.selectionText ?? liveCtx.selectionText,
                        diagnosticsCount: body.editorState?.diagnosticsCount ?? liveCtx.diagnosticsCount,
                        diagnosticsSummary: body.editorState?.diagnosticsSummary ?? liveCtx.diagnosticsSummary,
                        captureScreenshot: typeof body.captureScreenshot === "boolean"
                            ? body.captureScreenshot
                            : true,
                    });
                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ ok: true, diagnosis: diag }));
                    return;
                }
                if (req.method === "POST" && url.pathname === "/feedback") {
                    this.applyLocalFeedback(body.action || "dismiss");
                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ ok: true }));
                    return;
                }
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ ok: true }));
            });
        });
        srv.on("upgrade", (req, socket) => {
            const key = req.headers["sec-websocket-key"];
            if (!key || typeof key !== "string") {
                socket.destroy();
                return;
            }
            const accept = crypto
                .createHash("sha1")
                .update(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                .digest("base64");
            socket.write("HTTP/1.1 101 Switching Protocols\r\n" +
                "Upgrade: websocket\r\n" +
                "Connection: Upgrade\r\n" +
                `Sec-WebSocket-Accept: ${accept}\r\n\r\n`);
            this.embeddedWsSockets.add(socket);
            socket.on("close", () => this.embeddedWsSockets.delete(socket));
            socket.on("error", () => this.embeddedWsSockets.delete(socket));
        });
        await new Promise((resolve) => {
            srv.once("error", () => {
                this.status.coreMode = "external";
                this.connectExternalWs();
                if (this.contextProvider) {
                    this.pushExternalContext(this.contextProvider());
                }
                resolve();
            });
            srv.listen(parsedPort, "127.0.0.1", () => {
                this.embeddedServer = srv;
                this.status.coreMode = "embedded";
                this.callbacks.onStatusUpdate({ ...this.status });
                resolve();
            });
        });
    }
    broadcastEmbeddedWs(obj) {
        if (this.embeddedWsSockets.size === 0)
            return;
        const data = Buffer.from(JSON.stringify(obj), "utf8");
        let header;
        if (data.length < 126) {
            header = Buffer.from([0x81, data.length]);
        }
        else {
            header = Buffer.alloc(4);
            header[0] = 0x81;
            header[1] = 126;
            header.writeUInt16BE(data.length, 2);
        }
        const frame = Buffer.concat([header, data]);
        for (const sock of this.embeddedWsSockets) {
            try {
                sock.write(frame);
            }
            catch {
                this.embeddedWsSockets.delete(sock);
            }
        }
    }
    /**
     * Watches `signals/latest_brain_call.json` without duplicating in-flight HTTP calls:
     * - Tracks `${call_id}:${Boolean(http_completed)}`
     * - `http_dispatched === true && !http_completed`: shows "Asking Brain..." spinner only
     * - `http_dispatched === true && http_completed && http_error`: surfaces honest offline diagnosis
     * - `http_dispatched === false`: invokes `requestDiagnosis(...)` from VS Code
     */
    watchSignalsOutbox() {
        const candidateDirs = [];
        const seenDirs = new Set();
        const addCandidate = (dir) => {
            const key = path.resolve(dir).toLowerCase();
            if (!seenDirs.has(key) && fs.existsSync(dir)) {
                seenDirs.add(key);
                candidateDirs.push(path.resolve(dir));
            }
        };
        for (const root of this.workspaceRoots) {
            addCandidate(path.join(root, "signals"));
            addCandidate(path.resolve(root, "..", "signals"));
        }
        addCandidate(path.resolve(this.extensionPath, "..", "signals"));
        addCandidate(path.join(os.homedir(), "Desktop", "WHACK", "signals"));
        addCandidate(path.join(os.homedir(), "WHACK", "signals"));
        for (const signalsDir of candidateDirs) {
            const outboxPath = path.join(signalsDir, "latest_brain_call.json");
            // Pre-seed existing stale outbox file so old historical runs don't re-fire on launch
            try {
                if (fs.existsSync(outboxPath)) {
                    const stat = fs.statSync(outboxPath);
                    if (Date.now() - stat.mtimeMs > 3000) {
                        const initialRaw = JSON.parse(fs.readFileSync(outboxPath, "utf8"));
                        const initCallId = Number(initialRaw.call_id || 0);
                        const initKey = `${initCallId}:${Boolean(initialRaw.http_completed)}`;
                        this.seenOutboxStates.add(initKey);
                        this.lastOutboxCallId = initCallId;
                        this.lastOutboxMtimeMs = stat.mtimeMs;
                    }
                }
            }
            catch {
                // Ignore unreadable initial outbox
            }
            try {
                const watcher = fs.watch(signalsDir, (_eventType, filename) => {
                    if (filename && filename !== "latest_brain_call.json")
                        return;
                    if (this.outboxDebounceTimer) {
                        clearTimeout(this.outboxDebounceTimer);
                    }
                    this.outboxDebounceTimer = setTimeout(() => {
                        this.outboxDebounceTimer = null;
                        this.processSignalsOutboxFile(outboxPath);
                    }, 45);
                    this.outboxDebounceTimer.unref?.();
                });
                this.outboxWatchers.push(watcher);
            }
            catch {
                // Ignore fs.watch errors
            }
        }
    }
    processSignalsOutboxFile(outboxPath) {
        try {
            if (!fs.existsSync(outboxPath))
                return;
            const stat = fs.statSync(outboxPath);
            const raw = JSON.parse(fs.readFileSync(outboxPath, "utf8"));
            const callId = Number(raw.call_id ?? 0);
            const httpCompleted = Boolean(raw.http_completed);
            // Detect `signals` process restart (`call_id` reset back to 1)
            if ((callId > 0 && callId < this.lastOutboxCallId) ||
                (callId === 1 &&
                    this.lastOutboxCallId === 1 &&
                    stat.mtimeMs - this.lastOutboxMtimeMs > 4000 &&
                    !httpCompleted)) {
                this.seenOutboxStates.clear();
            }
            const stateKey = callId > 0
                ? `${callId}:${httpCompleted}`
                : `ts:${Number(raw.timestamp || stat.mtimeMs)}:${httpCompleted}`;
            if (this.seenOutboxStates.has(stateKey)) {
                return;
            }
            this.seenOutboxStates.add(stateKey);
            if (this.seenOutboxStates.size > 150) {
                const oldest = this.seenOutboxStates.values().next().value;
                if (oldest !== undefined) {
                    this.seenOutboxStates.delete(oldest);
                }
            }
            if (callId > 0) {
                this.lastOutboxCallId = callId;
            }
            this.lastOutboxMtimeMs = stat.mtimeMs;
            const userStatusObj = raw.user_status && typeof raw.user_status === "object"
                ? raw.user_status
                : raw.payload?.user_status && typeof raw.payload.user_status === "object"
                    ? raw.payload.user_status
                    : null;
            const stateStr = String(typeof raw.user_status === "string"
                ? raw.user_status
                : userStatusObj?.state || raw.status || "focused_flow");
            const stuckScore = Number(userStatusObj?.stuck_score ?? raw.confidence ?? this.status.confusionScore ?? 0);
            this.status.confusionScore = stuckScore;
            this.status.faceState =
                stuckScore >= 0.65 || stateStr.includes("stuck") || stateStr.includes("frustrated")
                    ? "confused"
                    : "calm";
            this.status.userStatusLabel = stateStr.includes("fatigued")
                ? "Tired · Break"
                : stateStr.includes("stuck") || stateStr.includes("frustrated")
                    ? "Stuck"
                    : stateStr.includes("afk")
                        ? "AFK"
                        : stuckScore >= 0.4
                            ? "Thinking"
                            : "Flow";
            const reasons = Array.isArray(raw.reasons)
                ? raw.reasons.map(String)
                : Array.isArray(userStatusObj?.reasons)
                    ? userStatusObj.reasons.map(String)
                    : undefined;
            const rawHint = raw.payload?.hintLevel || raw.hintLevel;
            const hintLevel = rawHint === "nudge" || rawHint === "hint" || rawHint === "full_explanation"
                ? rawHint
                : "hint";
            // Case 1: `signals` already dispatched `POST :5002/diagnose` and is awaiting response
            if (raw.http_dispatched === true && !httpCompleted) {
                this.status.askingBrain = true;
                this.callbacks.onStatusUpdate({ ...this.status });
                return;
            }
            // Case 2: `signals` completed its HTTP call
            if (raw.http_dispatched === true && httpCompleted) {
                this.status.askingBrain = false;
                if (raw.http_error) {
                    this.status.brainOnline = false;
                    this.callbacks.onStatusUpdate({ ...this.status });
                    if (!this.isInCooldown()) {
                        const ctx = this.contextProvider ? this.contextProvider() : {};
                        const offlineDiag = this.buildOfflineDiagnosis(hintLevel, String(raw.http_error), ctx.workspaceRoot || this.workspaceRoots[0], reasons);
                        this.callbacks.onDiagnosisTriggered(offlineDiag, "Signals Trigger (Brain Offline)");
                    }
                    return;
                }
                this.status.brainOnline = raw.source !== "offline";
                this.callbacks.onStatusUpdate({ ...this.status });
                return;
            }
            this.callbacks.onStatusUpdate({ ...this.status });
            // Case 3: Only call `requestDiagnosis` when `raw.http_dispatched === false`
            if (raw.http_dispatched === false &&
                !stateStr.includes("afk") &&
                (stateStr.startsWith("stuck_") ||
                    stateStr === "frustrated_blocked" ||
                    stateStr === "fatigued" ||
                    stuckScore >= 0.65) &&
                !this.isInCooldown()) {
                this.status.askingBrain = true;
                this.callbacks.onStatusUpdate({ ...this.status });
                const ctx = this.contextProvider ? this.contextProvider() : {};
                this.requestDiagnosis(hintLevel, ctx, { reasons })
                    .then((diag) => {
                    this.status.askingBrain = false;
                    this.callbacks.onStatusUpdate({ ...this.status });
                    if (!this.isInCooldown()) {
                        this.callbacks.onDiagnosisTriggered(diag, "Signals+Face Trigger");
                    }
                })
                    .catch(() => {
                    this.status.askingBrain = false;
                    this.callbacks.onStatusUpdate({ ...this.status });
                });
            }
        }
        catch {
            // Ignore partial atomic file writes
        }
    }
    isInCooldown() {
        if (!this.status.cooldownUntil)
            return false;
        if (Date.now() >= this.status.cooldownUntil) {
            this.status.cooldownUntil = null;
            return false;
        }
        return true;
    }
    applyLocalFeedback(action) {
        if (action === "dismiss" || action === "snooze" || action === "break") {
            const minutes = action === "break" ? 5 : 3;
            this.status.cooldownUntil = Date.now() + minutes * 60 * 1000;
            this.status.faceState = "calm";
            this.status.confusionScore = 0.05;
            this.status.askingBrain = false;
            this.status.userStatusLabel = action === "break" ? "On Break ☕" : "Snoozed";
            this.callbacks.onStatusUpdate({ ...this.status });
        }
        if (this.status.coreMode === "external") {
            fetch(`${this.coreUrl}/feedback`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: action === "dismiss" ? "not_confused" : action === "break" ? "snooze" : action,
                }),
            }).catch(() => undefined);
        }
    }
    buildOfflineDiagnosis(hintLevel, errorDetail, repoPath, reasons) {
        const baseBrain = this.brainUrl.replace(/\/$/, "");
        return {
            diagnosisId: `offline-${Date.now()}`,
            summary: "WHACK Brain (:5002) is offline.",
            file: undefined,
            lines: undefined,
            quip: "Start brain.server on port 5002 so I can actually see what you're cooking.",
            uncertain: true,
            confidence: 0,
            hintLevel,
            source: "offline",
            error: errorDetail,
            repoPath,
            reasons,
            explanation: `Could not reach ${baseBrain}/diagnose (${errorDetail}). Run \`python -m uvicorn brain.server:app --port 5002\` with an API key in \`brain/.env\`.`,
            citations: [],
            suggestedFix: null,
        };
    }
    /**
     * Calls Role 3 Brain (`POST :5002/diagnose`) with a 28s timeout and full workspace context.
     * Returns an honest `source: "offline"` payload if `:5002` is unreachable.
     */
    async requestDiagnosis(hintLevel, editorContext, options) {
        const baseBrain = this.brainUrl.replace(/\/$/, "");
        const liveCtx = this.contextProvider ? this.contextProvider() : {};
        const merged = { ...liveCtx, ...editorContext };
        const repoPath = merged.workspaceRoot || this.workspaceRoots[0]?.replace(/\\/g, "/");
        try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), BRAIN_HTTP_TIMEOUT_MS);
            const res = await fetch(`${baseBrain}/diagnose`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    hintLevel,
                    repoPath,
                    captureScreenshot: merged.captureScreenshot ?? true,
                    attemptLog: merged.attemptLog ?? "",
                    userStatus: this.status.userStatusRaw,
                    emotions: this.status.emotions,
                    fusionReasons: options?.reasons ?? this.status.liveReasons,
                    editorState: {
                        activeFile: merged.activeFile ?? "",
                        cursorLine: merged.cursorLine ?? 1,
                        cursorColumn: merged.cursorColumn ?? 1,
                        selectionText: merged.selectionText ?? "",
                        diagnosticsCount: merged.diagnosticsCount ?? 0,
                        diagnosticsSummary: merged.diagnosticsSummary ?? "",
                    },
                    terminalState: merged.terminalCommand !== undefined
                        ? {
                            command: merged.terminalCommand,
                            exitCode: merged.terminalExitCode ?? 0,
                            durationMs: merged.terminalDurationMs ?? 0,
                            tailOutput: merged.terminalTail ?? "",
                        }
                        : null,
                    stream: false,
                }),
                signal: controller.signal,
            });
            clearTimeout(timer);
            if (res.ok) {
                const json = await res.json();
                const normalized = normalizeBrainDiagnosis(json);
                if (!normalized.repoPath && repoPath) {
                    normalized.repoPath = repoPath;
                }
                this.status.brainOnline = normalized.source === "model";
                this.callbacks.onStatusUpdate({ ...this.status });
                return normalized;
            }
            const httpErr = `HTTP ${res.status} ${res.statusText || ""}`.trim();
            this.status.brainOnline = false;
            this.callbacks.onStatusUpdate({ ...this.status });
            return this.buildOfflineDiagnosis(hintLevel, httpErr, repoPath, options?.reasons);
        }
        catch (err) {
            const errMsg = err instanceof Error
                ? err.name === "AbortError"
                    ? `Timed out after ${BRAIN_HTTP_TIMEOUT_MS / 1000}s`
                    : err.message
                : String(err);
            this.status.brainOnline = false;
            this.callbacks.onStatusUpdate({ ...this.status });
            return this.buildOfflineDiagnosis(hintLevel, errMsg, repoPath, options?.reasons);
        }
    }
    /**
     * Sends a follow-up question directly to `POST :5002/chat` (28s timeout).
     * Never calls `/diagnose`, so chatting never broadcasts to `:3000/events` or wipes `chatHistory`.
     */
    async sendChatMessage(params) {
        const baseBrain = this.brainUrl.replace(/\/$/, "");
        const liveCtx = this.contextProvider ? this.contextProvider() : {};
        const repoPath = params.repoPath ||
            params.latestDiagnosis?.repoPath ||
            liveCtx.workspaceRoot ||
            this.workspaceRoots[0]?.replace(/\\/g, "/");
        try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), BRAIN_HTTP_TIMEOUT_MS);
            const res = await fetch(`${baseBrain}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: params.message,
                    history: params.history,
                    activeFile: params.activeFile || liveCtx.activeFile,
                    highlightedLines: params.highlightedLines,
                    codeSnippet: params.codeSnippet,
                    latestDiagnosis: params.latestDiagnosis,
                    repoPath,
                }),
                signal: controller.signal,
            });
            clearTimeout(timer);
            if (res.ok) {
                const data = (await res.json());
                const mode = data.mode === "offline" || data.source === "offline"
                    ? "offline"
                    : "brain_live";
                this.status.brainOnline = mode === "brain_live";
                this.callbacks.onStatusUpdate({ ...this.status });
                const fallbackFile = params.activeFile || params.latestDiagnosis?.file || liveCtx.activeFile;
                const citations = parseCitations(data.citations, fallbackFile, params.highlightedLines);
                const suggestedFix = parseSuggestedFix(data.suggestedFix || data.suggested_fix, fallbackFile, params.highlightedLines);
                return {
                    reply: typeof data.reply === "string" && data.reply.trim()
                        ? data.reply
                        : "No reply returned from Brain.",
                    mode,
                    error: typeof data.error === "string" ? data.error : undefined,
                    citations: citations.length > 0 ? citations : undefined,
                    suggestedFix: suggestedFix ?? null,
                };
            }
            const httpErr = `HTTP ${res.status}`;
            this.status.brainOnline = false;
            this.callbacks.onStatusUpdate({ ...this.status });
            return {
                reply: "Brain server (:5002) is unreachable — start `uvicorn brain.server:app --port 5002` to chat live.",
                mode: "offline",
                error: httpErr,
            };
        }
        catch (err) {
            const errMsg = err instanceof Error
                ? err.name === "AbortError"
                    ? `Timed out after ${BRAIN_HTTP_TIMEOUT_MS / 1000}s`
                    : err.message
                : String(err);
            this.status.brainOnline = false;
            this.callbacks.onStatusUpdate({ ...this.status });
            return {
                reply: "Brain server (:5002) is unreachable — start `uvicorn brain.server:app --port 5002` to chat live.",
                mode: "offline",
                error: errMsg,
            };
        }
    }
    /**
     * Triggers background repo wiki refresh (`POST :5002/wiki/update`) on `git commit`.
     */
    triggerWikiUpdate(repoPath) {
        const baseBrain = this.brainUrl.replace(/\/$/, "");
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), BRAIN_HTTP_TIMEOUT_MS);
        fetch(`${baseBrain}/wiki/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repoPath: repoPath.replace(/\\/g, "/"),
                allowModel: true,
                force: true,
            }),
            signal: controller.signal,
        })
            .catch(() => undefined)
            .finally(() => clearTimeout(timer));
    }
    pushExternalContext(ctx) {
        this.cachedExternalContext = ctx;
        if (this.status.coreMode !== "external")
            return;
        fetch(`${this.coreUrl}/context`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                workspaceRoot: ctx.workspaceRoot || this.workspaceRoots[0]?.replace(/\\/g, "/"),
                editorState: {
                    activeFile: ctx.activeFile ?? "",
                    cursorLine: ctx.cursorLine ?? 1,
                    cursorColumn: ctx.cursorColumn ?? 1,
                    selectionText: ctx.selectionText ?? "",
                    diagnosticsCount: ctx.diagnosticsCount ?? 0,
                    diagnosticsSummary: ctx.diagnosticsSummary ?? "",
                },
                terminalState: ctx.terminalCommand !== undefined
                    ? {
                        command: ctx.terminalCommand,
                        exitCode: ctx.terminalExitCode ?? 0,
                        durationMs: ctx.terminalDurationMs ?? 0,
                        tailOutput: ctx.terminalTail ?? "",
                    }
                    : null,
            }),
        }).catch(() => undefined);
    }
    postTelemetryEvent(source, type, payload) {
        if (this.status.coreMode === "external") {
            fetch(`${this.coreUrl}/events`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    source,
                    type,
                    timestamp: Date.now(),
                    payload,
                }),
            }).catch(() => undefined);
        }
    }
    dispose() {
        this.disposed = true;
        this.clearPendingNudgeTimer();
        if (this.outboxDebounceTimer) {
            clearTimeout(this.outboxDebounceTimer);
            this.outboxDebounceTimer = null;
        }
        if (this.healthTimer) {
            clearInterval(this.healthTimer);
            this.healthTimer = null;
        }
        for (const w of this.outboxWatchers) {
            try {
                w.close();
            }
            catch {
                // Ignore close errors
            }
        }
        this.outboxWatchers = [];
        this.wsClient?.close();
        for (const sock of this.embeddedWsSockets) {
            sock.destroy();
        }
        this.embeddedWsSockets.clear();
        if (this.embeddedServer) {
            this.embeddedServer.close();
            this.embeddedServer = null;
        }
    }
}
exports.WhackBrainBridge = WhackBrainBridge;
