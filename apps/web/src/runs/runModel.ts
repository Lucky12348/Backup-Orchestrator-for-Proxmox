// Live run modal model. Pure functions only (node:test), no runtime import.
// Data: GET /external-backups/runs/{id}, GET /external-backups/runs/{id}/log,
// and the SSE events run.progress / run.log / run.finished.

export type RunStatus = "pending" | "running" | "success" | "failed";

export interface RunLike {
  id: number;
  disk_id: number;
  disk_name?: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string | null;
  current_step: string | null;
  progress_message?: string | null;
  progress_percent: number | null;
  total_groups?: number | null;
  completed_groups?: number | null;
  current_group?: string | null;
  downloaded_bytes?: number | null;
  current_speed?: string | null;
  elapsed_seconds?: number | null;
  failed_groups?: { group?: string; reason?: string }[] | null;
  auto_eject_after_success?: boolean;
  message?: string | null;
  source_datastore?: string | null;
  target_datastore?: string | null;
  cancellable?: boolean;
}

export interface JournalEntry {
  at: string | null;
  step: string | null;
  source: "pbo" | "pbs";
  level: "info" | "ok" | "warn" | "err";
  code: string | null;
  params: Record<string, string>;
  text: string;
  count: number;
}

export const RUN_STEPS = ["detect", "passthrough", "mount", "sync", "verify", "eject"] as const;
export type RunStepKey = (typeof RUN_STEPS)[number];
export type StepState = "done" | "active" | "upcoming" | "failed" | "skipped";

const STEP_OF: Record<string, RunStepKey> = {
  starting: "detect",
  handoff_disk: "passthrough",
  inspect_disk: "passthrough",
  prepare_disk: "mount",
  prepare_dedicated_datastore: "mount",
  prepare_external_datastore: "mount",
  destructive_format: "mount",
  create_datastore: "mount",
  mount_datastore: "mount",
  run_export: "sync",
  sync_job: "sync",
  sync: "sync",
  export: "sync",
  success: "verify",
  auto_eject: "eject",
  eject: "eject",
  eject_flush: "eject",
  ejected: "eject",
  eject_not_finished: "eject",
};

export function stepIndex(step: string | null | undefined): number | null {
  const key = step ? STEP_OF[step] : undefined;
  return key ? RUN_STEPS.indexOf(key) : null;
}

/** Furthest step reached, from the journal (the run's own step may be a failure code). */
function reachedIndex(run: RunLike, entries: JournalEntry[]): number {
  let reached = stepIndex(run.current_step) ?? -1;
  for (const entry of entries) reached = Math.max(reached, stepIndex(entry.step) ?? -1);
  return reached;
}

/** An eject run (no source datastore, only eject steps). */
export function isEjectRun(run: RunLike, entries: JournalEntry[]): boolean {
  if (run.source_datastore) return false;
  const steps = entries.map((entry) => stepIndex(entry.step)).filter((index): index is number => index !== null);
  const current = stepIndex(run.current_step);
  return [...steps, ...(current === null ? [] : [current])].every((index) => index === RUN_STEPS.indexOf("eject")) && (steps.length > 0 || current !== null);
}

export function stepStates(run: RunLike, entries: JournalEntry[]): StepState[] {
  const reached = Math.max(0, reachedIndex(run, entries));
  const eject = RUN_STEPS.indexOf("eject");
  if (isEjectRun(run, entries)) {
    return RUN_STEPS.map((_, index) =>
      index < eject ? "skipped" : run.status === "failed" ? "failed" : run.status === "success" ? "done" : "active",
    );
  }
  return RUN_STEPS.map((_, index) => {
    if (run.status === "success") {
      if (index !== eject) return "done";
      if (reached < eject) return "upcoming"; // eject later, by hand
      // B24: the auto-eject is step 6 of the same run; a failed eject leaves
      // the backup a success but the step failed.
      return entries.some((entry) => stepIndex(entry.step) === eject && entry.level === "err") ? "failed" : "done";
    }
    if (index < reached) return "done";
    if (index === reached) return run.status === "failed" ? "failed" : "active";
    return "upcoming";
  });
}

export function isRunning(run: Pick<RunLike, "status"> | null | undefined): boolean {
  return run?.status === "pending" || run?.status === "running";
}

export interface RunStats {
  percent: number;
  transferredBytes: number | null;
  rate: string | null;
  remainingSeconds: number | null;
  elapsedSeconds: number;
}

export function runStats(run: RunLike, nowMs: number): RunStats {
  const percent = run.status === "success" ? 100 : Math.max(0, Math.min(100, run.progress_percent ?? 0));
  const started = Date.parse(run.started_at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(run.started_at) ? run.started_at : `${run.started_at}Z`);
  const ended = run.finished_at ? Date.parse(/Z|[+-]\d\d:\d\d$/.test(run.finished_at) ? run.finished_at : `${run.finished_at}Z`) : nowMs;
  const elapsedSeconds = Number.isNaN(started) ? run.elapsed_seconds ?? 0 : Math.max(0, Math.round((ended - started) / 1000));
  const syncing = isRunning(run) && stepIndex(run.current_step) === RUN_STEPS.indexOf("sync");
  const remainingSeconds =
    syncing && percent > 1 && percent < 100 ? Math.round((elapsedSeconds * (100 - percent)) / percent) : null;
  return {
    percent,
    transferredBytes: run.downloaded_bytes ?? null,
    rate: run.current_speed ?? null,
    remainingSeconds,
    elapsedSeconds,
  };
}

export type ResourceState = "done" | "running" | "pending" | "failed";

export interface ResourceRow {
  group: string; // "ct/101"
  kind: string; // "ct" | "vm" | "host"
  id: string; // "101"
  state: ResourceState;
  bytes: number | null;
  snapshots: number;
  noChanges: boolean;
}

const GROUP_RE = /^\[((vm|ct|host)\/([^\]\s]+))\]:\s*(.*)$/;
const DOWNLOADED_RE = /downloaded\s+(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)\b/i;
const UNIT: Record<string, number> = { b: 1, kib: 1024, mib: 1024 ** 2, gib: 1024 ** 3, tib: 1024 ** 4 };

/** VM/CT groups of the sync, from the PBS lines "[ct/101]: …" of the journal. */
export function resourceRows(run: RunLike, entries: JournalEntry[]): { rows: ResourceRow[]; pending: number } {
  const order: string[] = [];
  const rows = new Map<string, ResourceRow>();
  for (const entry of entries) {
    if (entry.source !== "pbs") continue;
    const match = GROUP_RE.exec(entry.text);
    if (!match) continue;
    const [, group, kind, id, rest] = match;
    if (!rows.has(group)) {
      order.push(group);
      rows.set(group, { group, kind, id, state: "pending", bytes: null, snapshots: 0, noChanges: false });
    }
    const row = rows.get(group)!;
    if (/\bsync snapshot\b/i.test(rest)) row.snapshots += 1;
    if (/no data changes/i.test(rest)) row.noChanges = true;
    const downloaded = DOWNLOADED_RE.exec(rest);
    if (downloaded) row.bytes = (row.bytes ?? 0) + Math.round(Number(downloaded[1]) * UNIT[downloaded[2].toLowerCase()]);
  }
  const failed = new Set((run.failed_groups ?? []).map((item) => item.group).filter(Boolean) as string[]);
  const currentIndex = run.current_group ? order.indexOf(run.current_group) : order.length - 1;
  order.forEach((group, index) => {
    const row = rows.get(group)!;
    if (failed.has(group)) row.state = "failed";
    else if (run.status === "success" || index < currentIndex) row.state = "done";
    else if (index === currentIndex) row.state = isRunning(run) ? "running" : run.status === "failed" ? "failed" : "done";
  });
  const pending = Math.max(0, (run.total_groups ?? order.length) - order.length);
  return { rows: order.map((group) => rows.get(group)!), pending };
}

export type LogFilter = "all" | "warn" | "err";

export function filterEntries(entries: JournalEntry[], filter: LogFilter): JournalEntry[] {
  if (filter === "err") return entries.filter((entry) => entry.level === "err");
  if (filter === "warn") return entries.filter((entry) => entry.level === "warn" || entry.level === "err");
  return entries;
}

/** Translated PBO message ({param} placeholders), PBS lines verbatim. */
export function entryText(entry: JournalEntry, templates: Record<string, string>): string {
  const template = entry.code ? templates[entry.code] : undefined;
  if (!template) return entry.text;
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => entry.params[key] ?? whole);
}

export const LEVEL_LABEL = { info: "INFO", ok: "OK", warn: "WARN", err: "ERR" } as const;

/** "02:40:03" in the viewer's time zone (PBO "…Z" and PBS "…+01:00" stamps). */
export function clockTime(at: string | null): string {
  if (!at) return "";
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((value) => String(value).padStart(2, "0")).join(":");
}

/** Text file offered by "Download the log". */
export function journalText(entries: JournalEntry[], templates: Record<string, string>): string {
  return entries
    .map((entry) => {
      const parts = [entry.at ?? "", LEVEL_LABEL[entry.level].padEnd(4), entry.source === "pbs" ? "[PBS]" : "[PBO]", entryText(entry, templates)];
      return parts.join(" ").trim() + (entry.count > 1 ? ` (×${entry.count})` : "");
    })
    .join("\n");
}

export function formatBytes(bytes: number | null | undefined, lang: "fr" | "en"): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = lang === "fr" ? ["o", "Ko", "Mo", "Go", "To"] : ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : 1;
  const text = value.toFixed(digits);
  return `${lang === "fr" ? text.replace(".", ",") : text} ${units[unit]}`;
}

/** "64.208 MiB/s" (PBS) → "64,2 Mo/s" / "64.2 MB/s". */
export function formatRate(rate: string | null | undefined, lang: "fr" | "en"): string | null {
  const match = rate ? /(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)\/s/i.exec(rate) : null;
  if (!match) return rate ?? null;
  return `${formatBytes(Math.round(Number(match[1]) * UNIT[match[2].toLowerCase()]), lang)}/s`;
}

export function formatDuration(seconds: number | null, lang: "fr" | "en"): string | null {
  if (seconds === null) return null;
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")}${lang === "fr" ? "" : " min"}`;
}

// --- SSE state ------------------------------------------------------------------------

export interface MonitorState {
  /** Run shown in the pill / modal (latest one started or followed). */
  activeRunId: number | null;
  open: boolean;
  runs: Record<number, Partial<RunLike> & { id: number }>;
  /** Open the next run that appears (set right before a long action starts). */
  openNext: boolean;
}

export const initialMonitorState: MonitorState = { activeRunId: null, open: false, runs: {}, openNext: false };

export type MonitorAction =
  | { type: "open"; runId: number }
  | { type: "minimize" }
  | { type: "close" }
  | { type: "expect" }
  | { type: "loaded"; run: RunLike }
  | { type: "event"; eventType: string; data: Record<string, unknown> };

export function monitorReducer(state: MonitorState, action: MonitorAction): MonitorState {
  switch (action.type) {
    case "open":
      return { ...state, activeRunId: action.runId, open: true, openNext: false };
    case "minimize":
      return { ...state, open: false };
    case "close": {
      const active = state.activeRunId === null ? null : state.runs[state.activeRunId];
      return { ...state, open: false, activeRunId: active && isRunning(active as RunLike) ? state.activeRunId : null };
    }
    case "expect":
      return { ...state, openNext: true };
    case "loaded":
      return {
        ...state,
        runs: { ...state.runs, [action.run.id]: { ...state.runs[action.run.id], ...action.run } },
        activeRunId: state.activeRunId ?? (isRunning(action.run) ? action.run.id : null),
      };
    case "event": {
      const runId = Number(action.data.run_id);
      // Disk preparation runs live in another table (their ids collide).
      if (!Number.isInteger(runId) || (action.data.kind && action.data.kind !== "external_backup")) return state;
      const previous = state.runs[runId] ?? { id: runId };
      const patch: Partial<RunLike> = {};
      if (action.eventType === "run.progress") Object.assign(patch, pickRunFields(action.data));
      if (action.eventType === "run.finished") patch.status = action.data.status as RunStatus;
      const updated = { ...previous, ...patch, id: runId };
      const isNew = !(runId in state.runs);
      const followNew = isNew && isRunning(updated as RunLike) && (state.activeRunId === null || !isRunning(state.runs[state.activeRunId] as RunLike));
      const openIt = state.openNext && isNew && action.eventType === "run.progress";
      return {
        ...state,
        runs: { ...state.runs, [runId]: updated },
        activeRunId: openIt || followNew ? runId : state.activeRunId,
        open: openIt ? true : state.open,
        openNext: openIt ? false : state.openNext,
      };
    }
    default:
      return state;
  }
}

const RUN_FIELDS = [
  "status",
  "current_step",
  "progress_message",
  "progress_percent",
  "total_groups",
  "completed_groups",
  "current_group",
  "downloaded_bytes",
  "current_speed",
  "elapsed_seconds",
] as const;

function pickRunFields(data: Record<string, unknown>): Partial<RunLike> {
  const patch: Record<string, unknown> = {};
  for (const key of RUN_FIELDS) if (key in data) patch[key] = data[key];
  if ("disk_id" in data && data.disk_id !== null) patch.disk_id = data.disk_id;
  return patch as Partial<RunLike>;
}
