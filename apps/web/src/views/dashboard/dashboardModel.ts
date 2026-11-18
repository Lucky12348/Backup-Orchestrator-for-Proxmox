// Dashboard (docs/design/mockups/dashboard.html): protection block, next
// rotation, "to do" list, rotation disks, last runs. Pure (node:test).

import { activeRunFor, diskUiStatus, type DiskLike, type DiskUiStatus, type RunLike, usagePercent } from "../disks/diskModel.ts";
import { todoAlerts, type AlertLike } from "../disks/diskProfile.ts";
import { runPercent } from "../../runs/runModel.ts";

export interface OverviewLike {
  total_vms: number;
  protected_vms: number;
  ignored_vms: number;
  infrastructure_ignored?: number;
  coverage_percent: number;
}

export interface DashboardRun extends RunLike {
  disk_name: string;
  message?: string | null;
}

export interface EventLike {
  id: number;
  title: string;
  enabled: boolean;
  disk_serial: string;
  disk_label_or_model: string | null;
  next_occurrence_at: string | null;
}

export interface ScheduledRunLike {
  id: number;
  event_id: number;
  event_title: string | null;
  status: string;
  scheduled_for: string;
}

export type Protection = "protected" | "at_risk" | "empty";

export interface ProtectionSummary {
  state: Protection;
  percent: number;
  total: number;
  protectedCount: number;
  atRisk: number;
  ignored: number;
}

export function protectionSummary(overview: OverviewLike): ProtectionSummary {
  const total = overview.total_vms;
  const protectedCount = Math.min(overview.protected_vms, total);
  const state: Protection = total === 0 ? "empty" : protectedCount >= total ? "protected" : "at_risk";
  return {
    state,
    percent: total === 0 ? 0 : Math.round(overview.coverage_percent),
    total,
    protectedCount,
    atRisk: total - protectedCount,
    ignored: overview.ignored_vms,
  };
}

const byStartDesc = (a: RunLike, b: RunLike) => b.started_at.localeCompare(a.started_at);

/** The running task (follow it) or else the last run (what happened last). */
export function currentOrLastRun<R extends RunLike>(runs: readonly R[]): { run: R; active: boolean } | null {
  const sorted = [...runs].sort(byStartDesc);
  const active = sorted.find((run) => run.status === "pending" || run.status === "running");
  if (active) return { run: active, active: true };
  return sorted[0] ? { run: sorted[0], active: false } : null;
}

export function lastSuccessAt(runs: readonly RunLike[]): string | null {
  return [...runs].filter((run) => run.status === "success").sort(byStartDesc)[0]?.finished_at ?? null;
}

const compact = (value: string | null | undefined) => (value ?? "").toLowerCase().replace(/[\s_-]+/g, "");

/** Planning serial ↔ disk (case, spaces and dashes ignored, aliases included). */
export function findDiskBySerial<D extends DiskLike>(disks: readonly D[], serial: string): D | undefined {
  const wanted = compact(serial);
  if (!wanted) return undefined;
  return disks.find((disk) =>
    [disk.serial_number, disk.reported_serial_number, disk.canonical_serial_number, ...(disk.serial_aliases ?? [])].some(
      (value) => compact(value) === wanted,
    ),
  );
}

export interface NextRotation {
  at: string;
  title: string;
  diskLabel: string;
  diskId: number | null;
  diskPlugged: boolean;
}

export function nextRotation(events: readonly EventLike[], disks: readonly DiskLike[], now: Date): NextRotation | null {
  const next = events
    .filter((event) => event.enabled && event.next_occurrence_at && new Date(event.next_occurrence_at) >= now)
    .sort((a, b) => (a.next_occurrence_at ?? "").localeCompare(b.next_occurrence_at ?? ""))[0];
  if (!next?.next_occurrence_at) return null;
  const disk = findDiskBySerial(disks, next.disk_serial);
  return {
    at: next.next_occurrence_at,
    title: next.title,
    diskLabel: disk?.display_name ?? next.disk_label_or_model ?? next.disk_serial,
    diskId: disk?.id ?? null,
    diskPlugged: Boolean(disk && disk.connected),
  };
}

export type TodoKind = "datastore_missing" | "to_confirm" | "new_disk" | "missed_rotation" | "agent_offline" | "recent_failure" | "disk_alert";

export interface TodoItem {
  kind: TodoKind;
  tone: "warn" | "err";
  /** Disk name, rotation title… */
  label: string;
  href: string;
  /** to_confirm: the planned run to confirm or cancel. */
  runId?: number;
  /** disk_alert (Phase 5f): the server-side guard (usage, SMART, removal, rotation). */
  alert?: AlertLike;
}

const DAY_MS = 24 * 3600 * 1000;

/** Only what needs an action from the operator. */
export function todoItems(input: {
  disks: readonly DiskLike[];
  runs: readonly DashboardRun[];
  scheduledRuns: readonly ScheduledRunLike[];
  agentConnected: boolean;
  agentConfigured: boolean;
  now: Date;
  /** Name of the source datastore when it no longer exists on PBS. */
  missingDatastore?: string | null;
  /** Phase 5f: GET /disks/alerts. */
  alerts?: readonly AlertLike[];
}): TodoItem[] {
  const items: TodoItem[] = [];
  if (input.missingDatastore) {
    items.push({ kind: "datastore_missing", tone: "warn", label: input.missingDatastore, href: "/integrations" });
  }
  // Planned runs waiting for a confirmation (start_mode = manual confirmation).
  for (const run of input.scheduledRuns) {
    if (run.status === "waiting_for_confirmation") {
      items.push({ kind: "to_confirm", tone: "warn", label: run.event_title ?? "", href: "/planning", runId: run.id });
    }
  }
  for (const disk of input.disks) {
    if (diskUiStatus(disk, input.runs) === "new") {
      items.push({ kind: "new_disk", tone: "warn", label: disk.display_name, href: `/disks?disk=${disk.id}` });
    }
  }
  for (const alert of todoAlerts(input.alerts)) {
    items.push({
      kind: "disk_alert",
      tone: alert.level === "danger" ? "err" : "warn",
      label: alert.disk_name ?? "",
      href: `/disks/${alert.disk_id}`,
      alert,
    });
  }
  const recentMissed = input.scheduledRuns
    .filter((run) => run.status === "missed" && input.now.getTime() - new Date(run.scheduled_for).getTime() < 7 * DAY_MS)
    .sort((a, b) => b.scheduled_for.localeCompare(a.scheduled_for))[0];
  if (recentMissed) {
    items.push({ kind: "missed_rotation", tone: "err", label: recentMissed.event_title ?? "", href: "/planning" });
  }
  if (input.agentConfigured && !input.agentConnected) {
    items.push({ kind: "agent_offline", tone: "err", label: "", href: "/integrations" });
  }
  const last = [...input.runs].sort(byStartDesc)[0];
  if (last && last.status === "failed" && input.now.getTime() - new Date(last.finished_at ?? last.started_at).getTime() < DAY_MS) {
    items.push({ kind: "recent_failure", tone: "err", label: last.disk_name, href: "/activity" });
  }
  return items;
}

export interface RotationDisk {
  id: number;
  name: string;
  status: DiskUiStatus;
  usage: number | null;
  usedGb: number | null;
  totalGb: number;
  freeGb: number | null;
  lastBackupAt: string | null;
  percent: number | null;
}

/** Approved disks (the rotation), the one being backed up first. */
export function rotationDisks(disks: readonly DiskLike[], runs: readonly RunLike[], limit = 4): RotationDisk[] {
  return disks
    .filter((disk) => disk.trusted && !disk.reject_reason)
    .map((disk) => {
      const status = diskUiStatus(disk, runs);
      const total = disk.filesystem_total_gb ?? disk.capacity_gb;
      const lastSuccess = runs
        .filter((run) => run.disk_id === disk.id && run.status === "success")
        .sort(byStartDesc)[0];
      return {
        id: disk.id,
        name: disk.display_name,
        status,
        usage: usagePercent(disk),
        usedGb: disk.filesystem_used_gb ?? null,
        totalGb: total,
        freeGb: disk.filesystem_used_gb != null ? Math.max(0, total - disk.filesystem_used_gb) : null,
        lastBackupAt: lastSuccess?.finished_at ?? null,
        // Phase 5h.1: the same weighted percentage as the run modal.
        percent: (() => {
          const run = activeRunFor(disk, runs);
          return run ? runPercent(run as Parameters<typeof runPercent>[0]) : null;
        })(),
      };
    })
    .sort((a, b) => Number(b.status === "running") - Number(a.status === "running") || (b.lastBackupAt ?? "").localeCompare(a.lastBackupAt ?? ""))
    .slice(0, limit);
}

export type RunTone = "ok" | "err" | "run" | "off";

export function recentRuns<R extends DashboardRun>(runs: readonly R[], limit = 5): { run: R; tone: RunTone }[] {
  return [...runs]
    .sort(byStartDesc)
    .slice(0, limit)
    .map((run) => ({
      run,
      tone: run.status === "pending" || run.status === "running" ? "run" : run.status === "success" ? "ok" : run.current_step === "cancelled" ? "off" : "err",
    }));
}

export interface Integration {
  key: "pve" | "pbs" | "agent";
  ok: boolean;
}

export function integrations(status: { pve: boolean; pbs: boolean; agent: boolean }): { items: Integration[]; allOk: boolean } {
  const items: Integration[] = [
    { key: "pve", ok: status.pve },
    { key: "pbs", ok: status.pbs },
    { key: "agent", ok: status.agent },
  ];
  return { items, allOk: items.every((item) => item.ok) };
}

/** B42: the hero state counts what is waiting in "To do": never "all protected"
 *  in green while something needs attention. */
export type DashboardState = "protected" | "attention" | "at_risk" | "empty";

export function dashboardState(protection: Pick<ProtectionSummary, "state">, todoCount: number): DashboardState {
  if (protection.state === "empty") return "empty";
  if (protection.state === "at_risk") return "at_risk";
  return todoCount > 0 ? "attention" : "protected";
}
