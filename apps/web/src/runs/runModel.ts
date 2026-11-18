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
  cancel_requested?: boolean;
  /** Phase 5f: PBS verify after the sync; verify-only run (no copy). */
  verify_after_backup?: boolean | null;
  verify_only?: boolean | null;
  repair?: boolean | null;
  kind?: string | null;
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

export const RUN_STEPS = ["detect", "passthrough", "mount", "sync", "cleanup", "verify", "eject"] as const;
/** B45: a verification is a run type of its own: no copy, ends by releasing the disk. */
export const VERIFY_STEPS = ["detect", "passthrough", "mount", "verify", "release"] as const;
/** B47: a repair deletes the damaged versions on the disk, copies them again, verifies them. */
export const REPAIR_STEPS = ["detect", "passthrough", "mount", "repair", "sync", "cleanup", "verify", "release"] as const;
export type RunStepKey = (typeof RUN_STEPS)[number] | "release" | "repair";
/** not_run (B44): steps after the one that failed were not executed. */
export type StepState = "done" | "active" | "upcoming" | "failed" | "skipped" | "not_run";

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
  // Phase 5f: retention (prune + GC) and contents, then the PBS verify.
  cleanup: "cleanup",
  verify: "verify",
  verify_failed: "verify",
  success: "verify",
  // B47: repair run (delete on the disk only, sync again, verify).
  repair: "repair",
  auto_eject: "eject",
  eject: "eject",
  eject_flush: "eject",
  ejected: "eject",
  eject_not_finished: "eject",
  // B45: end of a verification (unmount + detach, no spin-down).
  release: "eject",
  released: "eject",
  release_failed: "eject",
};

export function runSteps(run: Pick<RunLike, "verify_only" | "repair">): readonly RunStepKey[] {
  return run.repair ? REPAIR_STEPS : run.verify_only ? VERIFY_STEPS : RUN_STEPS;
}

function shortStepIndex(steps: readonly string[], step: string | null | undefined): number {
  if (step === "repair") return steps.indexOf("repair");
  const key = step ? STEP_OF[step] : undefined;
  if (!key) return -1;
  return steps.indexOf(key === "eject" ? "release" : key);
}

/** Verification and repair runs: their own step list, ending with Release. */
function verifyStepStates(run: RunLike, entries: JournalEntry[]): StepState[] {
  const steps = runSteps(run) as readonly string[];
  let reached = shortStepIndex(steps, run.current_step);
  for (const entry of entries) reached = Math.max(reached, shortStepIndex(steps, entry.step));
  reached = Math.max(0, reached);
  const release = steps.indexOf("release");
  // B47: the release runs even after a failure: shown as done or failed on its own.
  const released = entries.some((entry) => entry.step === "released");
  return steps.map((_, index) => {
    if (index === release && run.status === "failed" && released) return "done";
    if (run.status === "success") {
      if (index !== release) return "done";
      return entries.some((entry) => shortStepIndex(steps, entry.step) === release && entry.level === "err") ? "failed" : "done";
    }
    // After a failure the release lines are the furthest: the failed step is the one of the error.
    const last = run.status === "failed" && released ? failedStepIndex(steps, run, entries) : reached;
    if (index < last) return "done";
    if (index === last) return run.status === "failed" ? "failed" : "active";
    return run.status === "failed" ? "not_run" : "upcoming";
  });
}

function failedStepIndex(steps: readonly string[], run: RunLike, entries: JournalEntry[]): number {
  const failing = [...entries].reverse().find((entry) => entry.level === "err" && entry.step !== "release" && entry.step !== "released");
  const index = shortStepIndex(steps, failing?.step ?? run.current_step);
  return index >= 0 ? index : steps.indexOf("verify");
}

export function stepIndex(step: string | null | undefined): number | null {
  const key = step ? STEP_OF[step] : undefined;
  return key ? (RUN_STEPS as readonly string[]).indexOf(key) : null;
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

/** Steps this run does not do: no cleanup (agent mode), no verification
 *  (not asked), no copy (verify-only run). Old runs keep their verify step. */
export function skippedSteps(run: RunLike, entries: JournalEntry[]): Set<number> {
  const index = (key: RunStepKey) => (RUN_STEPS as readonly string[]).indexOf(key);
  const own = (key: RunStepKey) => entries.filter((entry) => entry.step !== "success" && stepIndex(entry.step) === index(key));
  const reached = reachedIndex(run, entries);
  const skip = new Set<number>();
  if (run.verify_only) {
    skip.add(index("sync"));
    skip.add(index("cleanup"));
  }
  const cleanup = own("cleanup");
  if (cleanup.length ? cleanup.every((entry) => entry.code === "cleanup_agent_mode") : reached > index("cleanup") || run.status === "success") {
    skip.add(index("cleanup"));
  }
  const verify = own("verify");
  if (verify.some((entry) => entry.code === "verify_agent_mode") || (run.verify_after_backup === false && !verify.length && !run.verify_only)) {
    skip.add(index("verify"));
  }
  return skip;
}

export function stepStates(run: RunLike, entries: JournalEntry[]): StepState[] {
  if (run.verify_only || run.repair) return verifyStepStates(run, entries);
  const states = rawStepStates(run, entries);
  const skip = skippedSteps(run, entries);
  return states.map((state, index) => (skip.has(index) && (state === "done" || state === "upcoming") ? "skipped" : state));
}

function rawStepStates(run: RunLike, entries: JournalEntry[]): StepState[] {
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
    return run.status === "failed" ? "not_run" : "upcoming";
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
  // Phase 5h.1: cleanup, verification and eject concern the whole disk: once
  // the copy is over, no VM/CT is "in progress" any more.
  const copying = stepIndex(run.current_step) === RUN_STEPS.indexOf("sync");
  order.forEach((group, index) => {
    const row = rows.get(group)!;
    if (failed.has(group)) row.state = "failed";
    else if (run.status === "success" || index < currentIndex) row.state = "done";
    else if (index === currentIndex) row.state = isRunning(run) ? (copying ? "running" : "done") : run.status === "failed" ? "failed" : "done";
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
export function entryText(entry: JournalEntry, templates: Record<string, string>, lang: "fr" | "en" = "en"): string {
  const template = entry.code ? templates[entry.code] : undefined;
  if (!template) return entry.text;
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => {
    const value = entry.params[key];
    if (value === undefined) return whole;
    // "…_bytes" parameters are raw byte counts: one unit convention (units.ts).
    return key.endsWith("_bytes") && /^\d+$/.test(value) ? formatBytes(Number(value), lang) : value;
  });
}

/** "2026-11-17T02:40:03+01:00" in `timeZone` (PBO's), whatever the stamp's own offset. */
export function zonedStamp(at: string | null, timeZone: string | null | undefined): string {
  if (!at) return "";
  const date = new Date(at);
  if (Number.isNaN(date.getTime()) || !timeZone) return at;
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-GB", {
        timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
        timeZoneName: "longOffset",
      })
        .formatToParts(date)
        .map((part) => [part.type, part.value]),
    );
    const offset = String(parts.timeZoneName ?? "").replace("GMT", "") || "+00:00";
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${offset}`;
  } catch {
    return at;
  }
}

/** B37: versions copied are not the data transferred ("4 new versions copied, 0 B transferred"). */
export function copySummary(
  summary: { transferred_bytes: number | null; no_new_data: boolean; versions_copied?: number | null } | null | undefined,
  texts: { noNewData: string; copiedOne: string; copiedMany: string; copiedBytes: string; copiedNoBytes: string; transferred: string },
  lang: "fr" | "en",
): string | null {
  if (!summary) return null;
  const versions = summary.versions_copied;
  if (versions === undefined || versions === null) {
    if (summary.no_new_data) return texts.noNewData;
    return summary.transferred_bytes !== null ? texts.transferred.replace("{bytes}", formatBytes(summary.transferred_bytes, lang)) : null;
  }
  if (versions === 0) return texts.noNewData;
  const label = versions === 1 ? texts.copiedOne : texts.copiedMany.replace("{n}", String(versions));
  if (summary.no_new_data || !summary.transferred_bytes) return texts.copiedNoBytes.replace("{versions}", label);
  return texts.copiedBytes.replace("{versions}", label).replace("{bytes}", formatBytes(summary.transferred_bytes, lang));
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
export function journalText(
  entries: JournalEntry[],
  templates: Record<string, string>,
  timeZone?: string | null,
  lang: "fr" | "en" = "en",
): string {
  return entries
    .map((entry) => {
      const parts = [
        zonedStamp(entry.at, timeZone),
        LEVEL_LABEL[entry.level].padEnd(4),
        entry.source === "pbs" ? "[PBS]" : "[PBO]",
        entryText(entry, templates, lang),
      ];
      return parts.join(" ").trim() + (entry.count > 1 ? ` (×${entry.count})` : "");
    })
    .join("\n");
}

import { formatBytes } from "../units.ts";

export { formatBytes };

/** "64.208 MiB/s" (PBS) → "64,2 Mio/s" / "64.2 MiB/s". */
export function formatRate(rate: string | null | undefined, lang: "fr" | "en"): string | null {
  const match = rate ? /(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)\/s/i.exec(rate) : null;
  if (!match) return rate ?? null;
  const bytes = Math.round(Number(match[1]) * UNIT[match[2].toLowerCase()]);
  // B37: "0 B/s" says nothing (nothing transferred, or not measured yet).
  if (bytes === 0) return null;
  return `${formatBytes(bytes, lang)}/s`;
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

// --- B35: one percentage for the whole run -----------------------------------------------

/** Rough share of each step in a run's duration (the copy dominates). */
export const STEP_WEIGHT: Record<RunStepKey, number> = {
  detect: 2,
  passthrough: 5,
  mount: 8,
  repair: 8,
  sync: 55,
  cleanup: 10,
  verify: 12,
  eject: 8,
  release: 6,
};
/** An active step without a measure of its own counts as a third done. */
const ACTIVE_SHARE = 0.3;

/** B35: progress over ALL the steps of the run (weighted), the copy's own
 *  percentage inside its step. Never 100 % before the run is over. */
export function overallPercent(run: RunLike, states: readonly StepState[], steps: readonly RunStepKey[]): number {
  if (run.status === "success") return 100;
  let total = 0;
  let done = 0;
  steps.forEach((key, index) => {
    const state = states[index];
    if (state === "skipped") return;
    const weight = STEP_WEIGHT[key] ?? 5;
    total += weight;
    if (state === "done") done += weight;
    else if (state === "active") {
      const own = key === "sync" && run.progress_percent != null ? Math.max(0, Math.min(100, run.progress_percent)) / 100 : ACTIVE_SHARE;
      done += weight * own;
    }
  });
  if (!total) return 0;
  return Math.min(99, Math.max(0, (done / total) * 100));
}

// --- B36: the journal follows the end while the reader is there ---------------------------

/** At the bottom (a few pixels of slack): keep following the new lines. */
export function isAtBottom(scrollTop: number, clientHeight: number, scrollHeight: number, slack = 24): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= slack;
}

/** What the left panel shows when no VM/CT is being copied (verification,
 *  repair, nothing to copy): the step in progress and its latest lines. */
export function currentActivity(steps: readonly RunStepKey[], states: readonly StepState[], entries: readonly JournalEntry[], limit = 6): { step: RunStepKey | null; lines: JournalEntry[] } {
  const activeIndex = states.findIndex((state) => state === "active" || state === "failed");
  const lastDone = states.reduce((last, state, index) => (state === "done" ? index : last), -1);
  const index = activeIndex >= 0 ? activeIndex : lastDone;
  const step = index >= 0 ? steps[index] : null;
  const ofStep = step ? entries.filter((entry) => entry.step && (STEP_OF[entry.step] ?? null) === (step === "release" ? "eject" : step)) : [];
  const lines = (ofStep.length ? ofStep : entries).slice(-limit);
  return { step, lines };
}

/** Phase 5h.1: THE progress of a run, everywhere (modal, top-bar pill,
 *  dashboard, disk cards): weighted steps, from the journal when known. */
export function runPercent(run: RunLike, entries: readonly JournalEntry[] = []): number {
  return overallPercent(run, stepStates(run, [...entries]), runSteps(run));
}

// --- Step bar (Phase 5i) ----------------------------------------------------------------

function stampMs(at: string | null | undefined): number | null {
  if (!at) return null;
  const ms = Date.parse(/Z$|[+-]\d\d:?\d\d$/.test(at) ? at : `${at}Z`);
  return Number.isNaN(ms) ? null : ms;
}

/** Index in `steps` of a journal step code (-1 when it belongs to none). */
function entryStepIndex(run: RunLike, steps: readonly RunStepKey[], step: string | null | undefined): number {
  if (run.verify_only || run.repair) return shortStepIndex(steps, step);
  const index = stepIndex(step);
  return index === null ? -1 : index;
}

/**
 * How long each step took (seconds), from the journal: a step starts with
 * its first line and ends when a later step starts (the active one runs
 * until now, the last one until the run's end). null = not run / unknown.
 */
export function stepTimings(run: RunLike, entries: readonly JournalEntry[], steps: readonly RunStepKey[], states: readonly StepState[], nowMs: number): (number | null)[] {
  const starts: (number | null)[] = steps.map(() => null);
  for (const entry of entries) {
    const index = entryStepIndex(run, steps, entry.step);
    const ms = stampMs(entry.at);
    if (index < 0 || ms === null) continue;
    const known = starts[index];
    if (known === null || ms < known) starts[index] = ms;
  }
  if (starts[0] === null && states[0] !== "skipped") starts[0] = stampMs(run.started_at);
  const runEnd = stampMs(run.finished_at ?? null) ?? nowMs;
  return steps.map((_, index) => {
    const start = starts[index];
    const state = states[index];
    if (start === null || state === "skipped" || state === "not_run" || state === "upcoming") return null;
    let end: number | null = null;
    for (let next = index + 1; next < steps.length && end === null; next += 1) {
      if (starts[next] !== null && states[next] !== "skipped") end = starts[next];
    }
    const until = end ?? (state === "active" ? nowMs : runEnd);
    return Math.max(0, Math.round((until - start) / 1000));
  });
}

/** The step the line under the bar talks about: the active one, the failed one, else the last done. */
export function focusStep(states: readonly StepState[]): number {
  const active = states.indexOf("active");
  if (active >= 0) return active;
  const failed = states.indexOf("failed");
  if (failed >= 0) return failed;
  for (let index = states.length - 1; index >= 0; index -= 1) if (states[index] === "done") return index;
  return 0;
}
