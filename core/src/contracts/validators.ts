import { BaseEvent, EventSource, FeedbackPayload } from "./events";

const VALID_SOURCES: EventSource[] = [
  "face",
  "editor",
  "terminal",
  "hotkey",
  "brain",
];

/**
 * Checks whether an incoming HTTP POST body is a valid `BaseEvent`.
 * Also supports flat payloads (e.g. Role 1's 45-min stub `{ source: "face", state: "confused", ... }`)
 * by wrapping them automatically into a canonical `BaseEvent` so teammates' stubs never fail!
 */
export function normalizeIncomingEvent(body: unknown): {
  valid: boolean;
  event?: BaseEvent;
  error?: string;
} {
  if (!body || typeof body !== "object") {
    return { valid: false, error: "Request body must be a JSON object." };
  }

  const raw = body as Record<string, unknown>;
  const source = raw.source as EventSource | undefined;

  if (!source || !VALID_SOURCES.includes(source)) {
    return {
      valid: false,
      error: `Invalid or missing 'source'. Expected one of: ${VALID_SOURCES.join(", ")}.`,
    };
  }

  const timestamp =
    typeof raw.timestamp === "number" ? raw.timestamp : Date.now();
  const type =
    typeof raw.type === "string" && raw.type.trim().length > 0
      ? raw.type
      : `${source}_update`;

  // If the sender already wrapped data inside `payload`, use it directly.
  // Otherwise, treat the remaining fields on the object as the payload (handles Role 1's flat stub!).
  let payload: unknown = raw.payload;
  if (payload === undefined) {
    const { source: _s, type: _t, timestamp: _ts, ...rest } = raw;
    payload = rest;
  }

  return {
    valid: true,
    event: {
      source,
      type,
      timestamp,
      payload,
    },
  };
}

export function normalizeFeedbackPayload(body: unknown): FeedbackPayload {
  const raw = (body && typeof body === "object" ? body : {}) as Record<
    string,
    unknown
  >;
  const validActions = [
    "dismiss",
    "not_confused",
    "helpful",
    "snooze",
    "toggle_camera",
  ] as const;

  const rawAction = String(raw.action || "dismiss").replace(/\s+/g, "_");
  const action = (
    validActions.includes(rawAction as (typeof validActions)[number])
      ? rawAction
      : "dismiss"
  ) as FeedbackPayload["action"];

  return {
    action,
    timestamp: typeof raw.timestamp === "number" ? raw.timestamp : Date.now(),
    cooldownMs:
      typeof raw.cooldownMs === "number" ? raw.cooldownMs : 3 * 60 * 1000,
    cameraPaused:
      typeof raw.cameraPaused === "boolean" ? raw.cameraPaused : undefined,
  };
}
