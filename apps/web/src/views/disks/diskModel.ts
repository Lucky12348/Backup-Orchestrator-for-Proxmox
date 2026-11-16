// Disks page (docs/design/mockups/disks.html): status, tabs, search, actions
// and history of a disk. Pure module (node:test), no React.

export type DiskUiStatus = "new" | "approved" | "running" | "ejected" | "unplugged" | "rejected" | "ignored";
export type DiskTab = "all" | "plugged" | "approved" | "review" | "rejected";
export type Tone = "ok" | "run" | "warn" | "err" | "off";

export const DISK_TABS: readonly DiskTab[] = ["all", "plugged", "approved", "review", "rejected"];

export interface DiskLike {
  id: number;
  serial_number: string;
  display_name: string;
  model_name?: string | null;
  reported_serial_number?: string | null;
  canonical_serial_number?: string | null;
  serial_aliases?: string[] | null;
  capacity_gb: number;
  connected: boolean;
  trusted: boolean;
  candidate_type?: string | null;
  handoff_status?: string | null;
  pbs_visible?: boolean;
  pbs_handoff_slot?: string | null;
  dedicated_backup_disk?: boolean;
  prepared_as_pbs_datastore?: boolean;
  filesystem_type?: string | null;
  filesystem_total_gb?: number | null;
  filesystem_used_gb?: number | null;
  status?: string;
  reject_reason?: string | null;
  operator_decision?: string | null;
  partitions?: { name: string; filesystem_type: string | null; size_gb: number; label: string | null }[] | null;
}

export interface RunLike {
  id: number;
  disk_id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  current_step: string | null;
  progress_percent: number | null;
  auto_eject_after_success?: boolean;
}

const ACTIVE = new Set(["pending", "running"]);
const EJECT_STEPS = new Set(["eject", "ejected", "eject_not_finished", "eject_flush"]);

export function isActiveRun(run: RunLike): boolean {
  return ACTIVE.has(run.status);
}

export function activeRunFor(disk: DiskLike, runs: readonly RunLike[]): RunLike | undefined {
  return runs.find((run) => run.disk_id === disk.id && isActiveRun(run));
}

/** Server rejection (size 0, too small, operator) — mirrors services/disk_events.py. */
export function rejectReason(disk: DiskLike): string | null {
  if (disk.reject_reason !== undefined) return disk.reject_reason ?? null;
  if ((disk.capacity_gb ?? 0) <= 0 || disk.candidate_type === "unusable") return "zero_size";
  if (disk.operator_decision === "rejected") return "operator";
  return null;
}

export function diskUiStatus(disk: DiskLike, runs: readonly RunLike[] = []): DiskUiStatus {
  if (rejectReason(disk)) return "rejected";
  if (activeRunFor(disk, runs)) return "running";
  if (disk.status === "ejected" || (disk.handoff_status ?? "").toLowerCase() === "ejected") return "ejected";
  if (!disk.connected) return "unplugged";
  if (disk.trusted) return "approved";
  if (disk.status === "ignored" || disk.operator_decision === "ignored") return "ignored";
  return "new";
}

export const STATUS_TONE: Record<DiskUiStatus, Tone> = {
  new: "warn",
  approved: "ok",
  running: "run",
  ejected: "off",
  unplugged: "off",
  rejected: "err",
  ignored: "off",
};

/** Physically there: plugged in, being backed up, or ejected but not unplugged yet. */
function isPlugged(status: DiskUiStatus): boolean {
  return status === "new" || status === "approved" || status === "running" || status === "ejected" || status === "ignored";
}

export function inTab(status: DiskUiStatus, disk: DiskLike, tab: DiskTab): boolean {
  switch (tab) {
    case "all":
      return true;
    case "plugged":
      return isPlugged(status);
    case "approved":
      return disk.trusted && status !== "rejected";
    case "review":
      return status === "new";
    case "rejected":
      return status === "rejected";
  }
}

const compact = (value: string | null | undefined) => (value ?? "").toLowerCase().replace(/[\s_-]+/g, "");

/** Search by name, model or serial (case, spaces, dashes and underscores ignored). */
export function matchesQuery(disk: DiskLike, query: string): boolean {
  const needle = compact(query);
  if (!needle) return true;
  const haystack = [
    disk.display_name,
    disk.model_name,
    disk.serial_number,
    disk.reported_serial_number,
    disk.canonical_serial_number,
    ...(disk.serial_aliases ?? []),
  ];
  return haystack.some((value) => compact(value).includes(needle));
}

export interface DiskRow<D extends DiskLike> {
  disk: D;
  status: DiskUiStatus;
  run?: RunLike;
}

const ORDER: Record<DiskUiStatus, number> = { running: 0, new: 1, approved: 2, ejected: 3, ignored: 4, unplugged: 5, rejected: 6 };

export function diskRows<D extends DiskLike>(disks: readonly D[], runs: readonly RunLike[]): DiskRow<D>[] {
  return disks
    .map((disk) => ({ disk, status: diskUiStatus(disk, runs), run: activeRunFor(disk, runs) }))
    .sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.disk.display_name.localeCompare(b.disk.display_name));
}

export function tabCounts<D extends DiskLike>(rows: readonly DiskRow<D>[]): Record<DiskTab, number> {
  const counts = { all: 0, plugged: 0, approved: 0, review: 0, rejected: 0 } as Record<DiskTab, number>;
  for (const row of rows) for (const tab of DISK_TABS) if (inTab(row.status, row.disk, tab)) counts[tab] += 1;
  return counts;
}

export function visibleRows<D extends DiskLike>(rows: readonly DiskRow<D>[], tab: DiskTab, query: string): DiskRow<D>[] {
  return rows.filter((row) => inTab(row.status, row.disk, tab) && matchesQuery(row.disk, query));
}

// --- Detail panel ----------------------------------------------------------------

export type DiskAction = "backup" | "follow" | "eject" | "replug" | "rescan" | "unreject";

/** Attached to the PBS VM (the eject has something to undo). */
export function isAttachedToPbs(disk: DiskLike): boolean {
  const handoff = (disk.handoff_status ?? "").toLowerCase();
  return Boolean(disk.pbs_visible || disk.pbs_handoff_slot || handoff === "attached_to_pbs" || handoff === "visible_on_pbs");
}

/** Buttons of the detail panel, in order. The approval flow (new disk) is separate. */
export function diskActions(disk: DiskLike, status: DiskUiStatus): DiskAction[] {
  const ejectable = Boolean(disk.dedicated_backup_disk || disk.prepared_as_pbs_datastore);
  switch (status) {
    case "running":
      return ["follow", "eject"];
    case "approved":
      return ejectable && isAttachedToPbs(disk) ? ["backup", "eject", "rescan"] : ["backup", "rescan"];
    case "ejected":
      return ["replug", "rescan"];
    case "rejected":
      return disk.operator_decision === "rejected" ? ["unreject", "rescan"] : ["rescan"];
    case "unplugged":
    case "ignored":
      return ["rescan"];
    case "new":
      return [];
  }
}

/** "Eject" while a backup runs: the disk is ejected after it (auto-eject), not now. */
export function ejectDisabledWhileRunning(status: DiskUiStatus): boolean {
  return status === "running";
}

export interface DataWarning {
  hasData: boolean;
  partitions: { name: string; filesystem_type: string | null; size_gb: number; label: string | null }[];
  filesystem: string | null;
  usedGb: number | null;
  /** The agent did not report partitions (too old): unknown, not "empty". */
  partitionsUnknown: boolean;
}

export function dataWarning(disk: DiskLike): DataWarning {
  const partitions = disk.partitions ?? [];
  const usedGb = disk.filesystem_used_gb ?? null;
  const filesystem = disk.filesystem_type ?? partitions.find((part) => part.filesystem_type)?.filesystem_type ?? null;
  return {
    hasData: partitions.some((part) => part.filesystem_type) || Boolean(disk.filesystem_type) || (usedGb ?? 0) > 0,
    partitions,
    filesystem,
    usedGb,
    partitionsUnknown: disk.partitions == null,
  };
}

/** Approve is enabled only when the typed name matches the disk name exactly. */
export function nameConfirmed(disk: DiskLike, typed: string): boolean {
  return typed.trim() !== "" && typed.trim() === disk.display_name.trim();
}

export interface HistoryItem {
  id: number;
  kind: "backup" | "eject";
  tone: Tone;
  at: string;
  status: string;
}

export function runKind(run: RunLike): "backup" | "eject" {
  // A manual eject run starts and ends on eject steps; a backup with auto-eject
  // (B24) ends on them too, but carries the flag.
  return run.current_step && EJECT_STEPS.has(run.current_step) && !run.auto_eject_after_success ? "eject" : "backup";
}

export function diskHistory(disk: DiskLike, runs: readonly RunLike[], limit = 8): HistoryItem[] {
  return runs
    .filter((run) => run.disk_id === disk.id)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .slice(0, limit)
    .map((run) => ({
      id: run.id,
      kind: runKind(run),
      tone: isActiveRun(run) ? "run" : run.status === "success" ? "ok" : run.status === "failed" ? "err" : "off",
      at: run.finished_at ?? run.started_at,
      status: run.status,
    }));
}

/** Used part of the disk (0–100) when the filesystem usage is known. */
export function usagePercent(disk: DiskLike): number | null {
  const total = disk.filesystem_total_gb ?? 0;
  if (!total || disk.filesystem_used_gb == null) return null;
  return Math.max(0, Math.min(100, Math.round((disk.filesystem_used_gb / total) * 100)));
}
