// Activity page (Phase 6): chronological list, filters, one-line summary.
// Pure (node:test).

import { formatBytes, type Lang } from "../../units.ts";

export type ActivityType = "backup" | "eject" | "detection" | "planning";
export type ActivityState = "ok" | "failed" | "running" | "info";

export interface ActivityLike {
  id: string;
  type: ActivityType;
  status: string;
  at: string;
  duration_seconds: number | null;
  disk_id: number | null;
  disk_label: string;
  run_id: number | null;
  event_title?: string | null;
  planned: boolean;
  auto_eject: boolean;
  step: string | null;
  progress_percent: number | null;
  transferred_bytes: number | null;
  no_new_data: boolean;
  message: string | null;
}

export const ACTIVITY_TYPES: readonly ActivityType[] = ["backup", "eject", "detection", "planning"];
export const ACTIVITY_STATES: readonly ActivityState[] = ["ok", "failed", "running", "info"];
export const PERIODS = [1, 7, 30, 90, 366] as const;

export function activityState(item: ActivityLike): ActivityState {
  if (item.status === "pending" || item.status === "running") return "running";
  if (item.status === "success") return "ok";
  if (item.status === "failed" || item.status === "missed") return "failed";
  return "info";
}

/** "runs" = backups and ejects (the default filter). */
export type ActivityTypeFilter = ActivityType | "runs" | "";

export interface ActivityFilters {
  type: ActivityTypeFilter;
  state: ActivityState | "";
  diskId: number | null;
  days: number;
  query: string;
}

const compact = (value: string | null | undefined) => (value ?? "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

export function filterActivity<T extends ActivityLike>(items: readonly T[], filters: ActivityFilters, now: Date, summary?: (item: T) => string): T[] {
  const since = now.getTime() - filters.days * 24 * 3600 * 1000;
  const needle = compact(filters.query.trim());
  return items.filter((item) => {
    if (filters.type === "runs" && item.type !== "backup" && item.type !== "eject") return false;
    if (filters.type && filters.type !== "runs" && item.type !== filters.type) return false;
    if (filters.state && activityState(item) !== filters.state) return false;
    if (filters.diskId !== null && item.disk_id !== filters.diskId) return false;
    if (new Date(item.at).getTime() < since) return false;
    if (!needle) return true;
    return [item.disk_label, item.message, item.event_title, summary?.(item)].some((value) => compact(value).includes(needle));
  });
}

/** "42 s", "12 min", "1 h 05". */
export function durationText(seconds: number | null, lang: Lang): string | null {
  if (seconds === null || seconds < 0) return null;
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return lang === "fr" ? `${hours} h ${String(rest).padStart(2, "0")}` : `${hours} h ${String(rest).padStart(2, "0")}`;
}

export interface SummaryTexts {
  backupOk: string;
  backupNoData: string;
  backupOkPlain: string;
  backupEjected: string;
  backupFailed: string;
  backupRunning: string;
  ejectOk: string;
  ejectFailed: string;
  ejectRunning: string;
  plugged: string;
  unplugged: string;
  replugged: string;
  missed: string;
  cancelled: string;
  to_confirm: string;
}

/** One line per entry; failure reasons are the step code's translation when known. */
export function activitySummary(item: ActivityLike, texts: SummaryTexts, lang: Lang, stepTexts: Record<string, string> = {}): string {
  const reason = (item.step && stepTexts[item.step]) || item.message || "";
  const state = activityState(item);
  if (item.type === "detection") return item.status === "replugged" ? texts.replugged : item.status === "plugged" ? texts.plugged : texts.unplugged;
  if (item.type === "planning") {
    const template = texts[item.status as "missed" | "cancelled" | "to_confirm"] ?? texts.missed;
    return template.replace("{title}", item.event_title ?? "");
  }
  if (item.type === "eject") {
    if (state === "running") return texts.ejectRunning;
    return state === "ok" ? texts.ejectOk : texts.ejectFailed.replace("{reason}", reason);
  }
  if (state === "running") return texts.backupRunning.replace("{pct}", String(Math.round(item.progress_percent ?? 0)));
  if (state === "failed") return texts.backupFailed.replace("{reason}", reason);
  if (item.no_new_data) return texts.backupNoData;
  if (item.transferred_bytes !== null) {
    const size = formatBytes(item.transferred_bytes, lang);
    return (item.auto_eject ? texts.backupEjected : texts.backupOk).replace("{size}", size);
  }
  return texts.backupOkPlain;
}

/** Disks offered by the disk filter (those seen in the list). */
export function activityDisks(items: readonly ActivityLike[]): { id: number; label: string }[] {
  const seen = new Map<number, string>();
  for (const item of items) if (item.disk_id !== null && !seen.has(item.disk_id)) seen.set(item.disk_id, item.disk_label);
  return [...seen].map(([id, label]) => ({ id, label })).sort((a, b) => a.label.localeCompare(b.label));
}

// --- Grouping (Phase 6b) -------------------------------------------------------------

/** An entry of the list: one item, or several identical consecutive ones. */
export interface ActivityGroup<T extends ActivityLike> {
  item: T;
  count: number;
  members: T[];
}

const REPLUG_WINDOW_MS = 30 * 60 * 1000;

const sameDisk = (a: ActivityLike, b: ActivityLike) =>
  a.disk_id !== null && b.disk_id !== null ? a.disk_id === b.disk_id : a.disk_label === b.disk_label;

/**
 * Newest-first list → groups:
 * - "unplugged" then "plugged" again (≤ 30 min, same disk) = one "replugged" line;
 * - consecutive entries with the same type, disk, state and summary = one line ×N.
 */
export function groupActivity<T extends ActivityLike>(items: readonly T[], summary: (item: T) => string): ActivityGroup<T>[] {
  const merged: ActivityGroup<T>[] = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const older = items[index + 1];
    if (
      item.type === "detection" &&
      item.status === "plugged" &&
      older?.type === "detection" &&
      older.status === "unplugged" &&
      sameDisk(item, older) &&
      new Date(item.at).getTime() - new Date(older.at).getTime() <= REPLUG_WINDOW_MS
    ) {
      merged.push({ item: { ...item, status: "replugged" }, count: 1, members: [item, older] });
      index += 1;
      continue;
    }
    merged.push({ item, count: 1, members: [item] });
  }
  const groups: ActivityGroup<T>[] = [];
  for (const group of merged) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.item.type === group.item.type &&
      last.item.status === group.item.status &&
      sameDisk(last.item, group.item) &&
      activityState(last.item) !== "running" &&
      summary(last.item) === summary(group.item)
    ) {
      last.count += group.count;
      last.members.push(...group.members);
      continue;
    }
    groups.push({ ...group, members: [...group.members] });
  }
  return groups;
}
