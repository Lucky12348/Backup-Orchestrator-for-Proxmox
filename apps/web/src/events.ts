// Real-time channel: ONE EventSource on /api/v1/events shared by the whole
// app (wizard included). The browser reconnects by itself and sends
// Last-Event-ID; when the stream is closed for good (401, server restart
// refusing the request…) we retry ourselves with a growing delay.
//
// Consumers use `subscribe()` (or the useEvents() hook in useEvents.ts).

export type AppEventType =
  | "disk.added"
  | "disk.removed"
  | "disk.updated"
  | "agent.online"
  | "agent.offline"
  | "run.progress"
  | "run.log"
  | "run.finished"
  | "inbox.updated";

export const APP_EVENT_TYPES: AppEventType[] = [
  "disk.added",
  "disk.removed",
  "disk.updated",
  "agent.online",
  "agent.offline",
  "run.progress",
  "run.log",
  "run.finished",
  "inbox.updated",
];

export interface DiskEventData {
  id: number;
  serial_number: string;
  display_name: string;
  model_name: string | null;
  capacity_gb: number;
  filesystem_type?: string | null;
  connected: boolean;
  trusted: boolean;
  status: "new" | "known" | "rejected" | "unplugged" | "ejected" | "ignored" | "pulled";
  reject_reason: "zero_size" | "too_small" | null;
  min_size_gb: number;
  hostname?: string | null;
  /** Phase 5f.2: removed without eject, a PBS verification is advised. */
  verify_recommended?: boolean;
}

export interface RunProgressData {
  run_id: number;
  kind: "external_backup" | "disk_preparation";
  disk_id: number | null;
  status?: string;
  current_step?: string | null;
  progress_message?: string | null;
  progress_percent?: number | null;
  downloaded_bytes?: number | null;
  current_speed?: string | null;
  completed_groups?: number | null;
  total_groups?: number | null;
  [key: string]: unknown;
}

export interface AppEvent<T = unknown> {
  id: number;
  type: AppEventType;
  at: number;
  data: T;
}

type Handler = (event: AppEvent) => void;
type StatusListener = (connected: boolean) => void;

const EVENTS_URL = "/api/v1/events";
const MAX_RETRY_MS = 30_000;

const handlers = new Map<AppEventType | "*", Set<Handler>>();
const statusListeners = new Set<StatusListener>();
let source: EventSource | null = null;
let connected = false;
let retryTimer: number | undefined;
let retryDelay = 2000;
let lastEventId = 0;
let users = 0;

function setConnected(value: boolean) {
  if (connected === value) return;
  connected = value;
  statusListeners.forEach((listener) => listener(value));
}

function dispatch(type: AppEventType, raw: MessageEvent<string>) {
  let parsed: { at?: number; data?: unknown };
  try {
    parsed = JSON.parse(raw.data) as { at?: number; data?: unknown };
  } catch {
    return;
  }
  const id = Number(raw.lastEventId) || 0;
  if (id) lastEventId = Math.max(lastEventId, id);
  const event: AppEvent = { id, type, at: parsed.at ?? Date.now() / 1000, data: parsed.data ?? {} };
  for (const key of [type, "*"] as const) {
    handlers.get(key)?.forEach((handler) => {
      try {
        handler(event);
      } catch (error) {
        console.error("event handler failed", error);
      }
    });
  }
}

function open() {
  if (source || typeof EventSource === "undefined") return;
  // A manual reconnection resumes too (the browser only sends Last-Event-ID
  // itself when it reconnects the same EventSource).
  const url = lastEventId ? `${EVENTS_URL}?lastEventId=${lastEventId}` : EVENTS_URL;
  const next = new EventSource(url, { withCredentials: true });
  source = next;
  next.onopen = () => {
    retryDelay = 2000;
    setConnected(true);
  };
  next.onerror = () => {
    setConnected(false);
    if (next.readyState === EventSource.CLOSED) {
      next.close();
      source = null;
      scheduleRetry();
    }
    // CONNECTING: the browser retries by itself (server `retry:` hint).
  };
  APP_EVENT_TYPES.forEach((type) => next.addEventListener(type, (raw) => dispatch(type, raw as MessageEvent<string>)));
}

function scheduleRetry() {
  if (retryTimer !== undefined || users === 0) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = undefined;
    open();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS);
}

function close() {
  window.clearTimeout(retryTimer);
  retryTimer = undefined;
  source?.close();
  source = null;
  setConnected(false);
}

/** Keeps the stream open while at least one consumer is mounted. */
export function retainEvents(): () => void {
  users += 1;
  open();
  return () => {
    users -= 1;
    if (users <= 0) {
      users = 0;
      close();
    }
  };
}

export function subscribe(type: AppEventType | "*", handler: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type)!.add(handler);
  return () => handlers.get(type)?.delete(handler);
}

export function onStatusChange(listener: StatusListener): () => void {
  statusListeners.add(listener);
  return () => statusListeners.delete(listener);
}

export function isConnected(): boolean {
  return connected;
}
