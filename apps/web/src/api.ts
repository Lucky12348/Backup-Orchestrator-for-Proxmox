import type {
  ActivityEvent,
  AgentStatus,
  AssetIgnore,
  AutoSyncResult,
  BackupRun,
  DiskHandoffStatus,
  DiskPreparationRun,
  DiskPlanningSummary,
  ExternalBackupPreview,
  ExternalBackupRun,
  ExternalDisk,
  HostNotifHistory,
  HostNotifHistoryStatus,
  HostNotifQueue,
  HostNotifTemplates,
  HostNotifTemplatesResponse,
  HostNotifTestResult,
  MaintenanceAction,
  MaintenanceComponentStatus,
  MaintenanceStatus,
  NotificationPreferences,
  NotificationStatus,
  NotificationTestResult,
  ScheduledBackupEvent,
  ScheduledBackupEventPayload,
  ScheduledBackupCalendarOccurrence,
  ScheduledBackupRun,
  Overview,
  PBSInventoryItem,
  PBSStatus,
  PBSSyncSummary,
  ProxmoxStatus,
  ProxmoxBackupJob,
  ProxmoxBackupJobFormValues,
  ProxmoxSyncSummary,
  PlanningOverview,
  SystemVersion,
  SystemTime,
  UnplannedAsset,
  VirtualMachine,
  DiskColor,
  DiskRetention,
} from "./types";

import { ApiError } from "./notices";

const API_BASE_PATH = "/api/v1";

// Custom event to signal 401 to the app (triggers logout)
export const AUTH_EXPIRED_EVENT = "pbo:auth-expired";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> ?? {}),
  };

  // The session is an HttpOnly cookie sent automatically on same-origin requests.
  const response = await fetch(`${API_BASE_PATH}${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });

  // Session expired — dispatch event so App can redirect to login
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    throw new Error("Session expired. Please log in again.");
  }

  if (!response.ok) {
    const body = await response.text();
    let parsedDetail: string | undefined;
    let code: string | undefined;
    try {
      const parsed = JSON.parse(body) as {
        detail?: string | { message?: string; code?: string } | { loc?: (string | number)[]; msg?: string }[];
      };
      if (parsed.detail && typeof parsed.detail === "object" && !Array.isArray(parsed.detail)) code = parsed.detail.code;
      if (Array.isArray(parsed.detail)) {
        // FastAPI request-validation errors: render "field.path: message" lines.
        parsedDetail = parsed.detail
          .map((item) => `${(item.loc ?? []).filter((part) => part !== "body").join(".")}: ${item.msg ?? ""}`)
          .join("\n");
      } else {
        parsedDetail = typeof parsed.detail === "string" ? parsed.detail : parsed.detail?.message;
      }
    } catch {
      parsedDetail = undefined;
    }
    throw new ApiError(parsedDetail || body || `Request failed with status ${response.status}`, response.status, code);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getOverview() {
  return request<Overview>("/overview");
}

export function getVMs() {
  return request<VirtualMachine[]>("/vms");
}

export function updateVM(
  id: number,
  payload: Partial<Pick<VirtualMachine, "critical" | "enabled" | "size_gb">>,
) {
  return request<VirtualMachine>(`/vms/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getAssetIgnores() {
  return request<AssetIgnore[]>("/assets/ignore");
}

export function updateAssetIgnore(
  source: string,
  node: string,
  vmid: string,
  payload: { ignored: boolean; reason?: string | null },
) {
  return request<AssetIgnore>(
    `/assets/${encodeURIComponent(source)}/${encodeURIComponent(node || "-")}/${encodeURIComponent(vmid)}/ignore`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

export function getDisks() {
  return request<ExternalDisk[]>("/disks");
}

export function getPreferredDisks() {
  return request<ExternalDisk[]>("/disks/preferred");
}

export function updateDisk(
  id: number,
  payload: Partial<
    Pick<
      ExternalDisk,
      | "dedicated_backup_disk"
      | "preferred_root_path"
      | "notes"
      | "display_name"
      | "trusted"
      | "usable_capacity_gb"
      | "reserved_capacity_gb"
      | "planning_notes"
    >
  >,
) {
  return request<ExternalDisk>(`/disks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getBackupRuns() {
  return request<BackupRun[]>("/backup-runs");
}

export function getProxmoxStatus() {
  return request<ProxmoxStatus>("/integrations/proxmox/status");
}

export function syncProxmoxInventory() {
  return request<ProxmoxSyncSummary>("/integrations/proxmox/sync", {
    method: "POST",
  });
}

export function getProxmoxInventory() {
  return request<VirtualMachine[]>("/integrations/proxmox/inventory");
}

export function getProxmoxBackupJobs() {
  return request<ProxmoxBackupJob[]>("/proxmox/backup-jobs");
}

export function updateProxmoxBackupJobSelection(jobId: string, selectedVmids: number[]) {
  return request<ProxmoxBackupJob>(`/proxmox/backup-jobs/${encodeURIComponent(jobId)}/selection`, {
    method: "PATCH",
    body: JSON.stringify({ selected_vmids: selectedVmids }),
  });
}

export function createProxmoxBackupJob(payload: ProxmoxBackupJobFormValues) {
  return request<ProxmoxBackupJob>("/proxmox/backup-jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function replaceProxmoxBackupJob(jobId: string, payload: ProxmoxBackupJobFormValues) {
  return request<ProxmoxBackupJob>(`/proxmox/backup-jobs/${encodeURIComponent(jobId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteProxmoxBackupJob(jobId: string) {
  return request<void>(`/proxmox/backup-jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}

export function getPBSStatus() {
  return request<PBSStatus>("/integrations/pbs/status");
}

export function syncPBSInventory() {
  return request<PBSSyncSummary>("/integrations/pbs/sync", {
    method: "POST",
  });
}

export function getPBSInventory() {
  return request<PBSInventoryItem[]>("/integrations/pbs/inventory");
}

export function getAgentStatus() {
  return request<AgentStatus>("/agent/status");
}

export function getPlanningDisks() {
  return request<DiskPlanningSummary[]>("/planning/disks");
}

export function getPlanningOverview() {
  return request<PlanningOverview>("/planning/overview");
}

export function getScheduledBackupEvents() {
  return request<ScheduledBackupEvent[]>("/planning/events");
}

export function getScheduledBackupCalendar(start: string, end: string) {
  const params = new URLSearchParams({ start, end });
  return request<ScheduledBackupCalendarOccurrence[]>(`/planning/calendar?${params.toString()}`);
}

export function createScheduledBackupEvent(payload: ScheduledBackupEventPayload) {
  return request<ScheduledBackupEvent>("/planning/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateScheduledBackupEvent(eventId: number, payload: Partial<ScheduledBackupEventPayload>) {
  return request<ScheduledBackupEvent>(`/planning/events/${eventId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteScheduledBackupEvent(eventId: number) {
  return request<void>(`/planning/events/${eventId}`, { method: "DELETE" });
}

export function getScheduledBackupRuns() {
  return request<ScheduledBackupRun[]>("/planning/runs");
}

export function runScheduledBackupNow(eventId: number) {
  return request<ScheduledBackupRun>(`/planning/events/${eventId}/run-now`, { method: "POST" });
}

export function confirmScheduledBackupRun(runId: number) {
  return request<ScheduledBackupRun>(`/planning/runs/${runId}/confirm`, { method: "POST" });
}

export function cancelScheduledBackupRun(runId: number) {
  return request<ScheduledBackupRun>(`/planning/runs/${runId}/cancel`, { method: "POST" });
}

export function getUnplannedAssets() {
  return request<UnplannedAsset[]>("/planning/unplanned-assets");
}

export function getNotificationPreferences() {
  return request<NotificationPreferences>("/notifications/preferences");
}

export function updateNotificationPreferences(payload: Partial<NotificationPreferences>) {
  return request<NotificationPreferences>("/notifications/preferences", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function resetNotificationPreferences() {
  return request<NotificationPreferences>("/notifications/preferences/reset", { method: "POST" });
}

export function getExternalBackupPreview(diskId: number) {
  return request<ExternalBackupPreview>(`/external-backups/preview/${diskId}`);
}

export interface BackupLaunchOptions {
  verifyAfterBackup?: boolean;
  /** Back up on a disk SMART reports as failing (explicit confirmation). */
  smartOverride?: boolean;
}

export function runExternalBackup(diskId: number, autoEjectAfterSuccess = false, options: BackupLaunchOptions = {}) {
  return request<ExternalBackupRun>("/external-backups/run", {
    method: "POST",
    body: JSON.stringify({
      disk_id: diskId,
      confirmation: true,
      auto_eject_after_success: autoEjectAfterSuccess,
      verify_after_backup: Boolean(options.verifyAfterBackup),
      smart_override: Boolean(options.smartOverride),
    }),
  });
}

export function getExternalBackupRuns() {
  return request<ExternalBackupRun[]>("/external-backups/runs");
}

export function getExternalBackupRun(runId: number) {
  return request<ExternalBackupRun>(`/external-backups/runs/${runId}`);
}

export interface RunJournalEntry {
  at: string | null;
  step: string | null;
  source: "pbo" | "pbs";
  level: "info" | "ok" | "warn" | "err";
  code: string | null;
  params: Record<string, string>;
  text: string;
  count: number;
}

export interface RunJournal {
  run_id: number;
  entries: RunJournalEntry[];
  summary: { text: string; transferred_bytes: number | null; rate_bytes_per_second: number | null; no_new_data: boolean } | null;
  source_datastore: string | null;
  target_datastore: string | null;
}

/** Readable journal (progress lines removed, duplicates collapsed, coded PBO messages). */
export function getRunJournal(runId: number) {
  return request<RunJournal>(`/external-backups/runs/${runId}/log`);
}

export function cancelExternalBackupRun(runId: number) {
  return request<{ run_id: number; cancel_requested: boolean }>(`/external-backups/runs/${runId}/cancel`, { method: "POST" });
}

export function cleanupExternalBackupRuns(keepLast = 10) {
  return request<{ deleted: number; keep_last: number }>(
    `/external-backups/runs/cleanup?keep_last=${keepLast}`,
    { method: "DELETE" },
  );
}

export function deleteExternalBackupRun(runId: number) {
  return request<void>(`/external-backups/runs/${runId}`, { method: "DELETE" });
}

export function cleanupBackupRuns(keepLast = 10) {
  return request<{ deleted: number; keep_last: number }>(
    `/backup-runs/cleanup?keep_last=${keepLast}`,
    { method: "DELETE" },
  );
}

export function prepareDisk(
  diskId: number,
  payload: {
    mode: "preserve_existing_data" | "dedicated_backup";
    mount_base_path?: string | null;
    confirm_destructive: boolean;
  },
) {
  return request<DiskPreparationRun>(`/disks/${diskId}/prepare`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function handoffDiskToPBS(diskId: number) {
  return request<DiskHandoffStatus>(`/disks/${diskId}/handoff-to-pbs`, {
    method: "POST",
    body: JSON.stringify({ confirmation: true }),
  });
}

export function detachDiskFromPBS(diskId: number) {
  return request<DiskHandoffStatus>(`/disks/${diskId}/handoff-to-pbs`, {
    method: "DELETE",
  });
}

export function ejectExternalDisk(diskId: number) {
  return request<ExternalDisk>(`/disks/${diskId}/eject`, { method: "POST" });
}

export function getDiskPBSVisibility(diskId: number) {
  return request<DiskHandoffStatus>(`/disks/${diskId}/pbs-visibility`);
}

export function getDiskPreparationRuns(diskId: number) {
  return request<DiskPreparationRun[]>(`/disks/${diskId}/preparation-runs`);
}

export function getDiskPreparationRun(runId: number) {
  return request<DiskPreparationRun>(`/disks/preparation-runs/${runId}`);
}

export function getSystemTime() {
  return request<SystemTime>("/system/time");
}

export function getSystemVersion() {
  return request<SystemVersion>("/system/version");
}

export function triggerAutoSync() {
  return request<AutoSyncResult>("/system/auto-sync", { method: "POST" });
}

export function getMaintenanceStatus() {
  return request<MaintenanceStatus>("/maintenance/updates/status");
}

export function checkMaintenanceComponent(component: "app" | "proxmox-agent" | "pbs-agent") {
  return request<MaintenanceComponentStatus>(`/maintenance/updates/${component}/check`, {
    method: "POST",
  });
}

export function updateMaintenanceComponent(component: "app" | "proxmox-agent" | "pbs-agent") {
  return request<MaintenanceAction>(`/maintenance/updates/${component}/update`, {
    method: "POST",
  });
}

export function updateAllMaintenanceComponents() {
  return request<MaintenanceAction[]>("/maintenance/updates/update-all", {
    method: "POST",
  });
}

export function getNotificationStatus() {
  return request<NotificationStatus>("/notifications/status");
}

export function sendTestNotification() {
  return request<NotificationTestResult>("/notifications/test", {
    method: "POST",
  });
}

export function getHostNotifTemplates() {
  return request<HostNotifTemplatesResponse>("/notifications/host/templates");
}

export function saveHostNotifTemplates(templates: HostNotifTemplates) {
  return request<HostNotifTemplatesResponse>("/notifications/host/templates", {
    method: "PUT",
    body: JSON.stringify(templates),
  });
}

export function testHostNotifEvent(event: string, vars: Record<string, string>) {
  return request<HostNotifTestResult>("/notifications/host/test", {
    method: "POST",
    body: JSON.stringify({ event, vars }),
  });
}

export function getHostNotifHistory(filters: { limit?: number; event?: string; status?: HostNotifHistoryStatus | "" }) {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 200));
  if (filters.event) params.set("event", filters.event);
  if (filters.status) params.set("status", filters.status);
  return request<HostNotifHistory>(`/notifications/host/history?${params.toString()}`);
}

export function getHostNotifQueue() {
  return request<HostNotifQueue>("/notifications/host/queue");
}

export function getActivityEvents(limit = 100) {
  return request<ActivityEvent[]>(`/activity-events?limit=${limit}`);
}

// --- Agents (one-line install) ----------------------------------------------------

export interface AgentPairing {
  code: string;
  expires_at: string;
  // Run in order on the Proxmox host: download, then run.
  install_commands: string[];
  app_url: string;
  app_fingerprint: string | null;
}

export interface RegisteredAgent {
  id: number;
  role: "host" | "pbs";
  hostname: string;
  base_url: string;
  fingerprint: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  active: boolean;
}

export interface ReadinessReason {
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface SpaceEstimate {
  needed_bytes: number;
  free_bytes: number | null;
  new_snapshots: number;
  new_groups: number;
  known: boolean;
  sufficient: boolean | null;
}

export interface ExternalBackupReadiness {
  disk_id: number;
  ready: boolean;
  pbs_mode: string;
  reasons: ReadinessReason[];
  /** Phase 5f: non-blocking (USB 2.0, SMART to watch, verify recommended). */
  warnings?: ReadinessReason[];
  estimate?: SpaceEstimate | null;
  smart_status?: string | null;
}

export function getExternalBackupReadiness(diskId: number) {
  return request<ExternalBackupReadiness>(`/external-backups/readiness/${diskId}`);
}

export function createAgentPairing() {
  return request<AgentPairing>("/agents/pairing", { method: "POST" });
}

export function getRegisteredAgents() {
  return request<RegisteredAgent[]>("/agents");
}

export function revokeRegisteredAgent(agentId: number) {
  return request<RegisteredAgent>(`/agents/${agentId}`, { method: "DELETE" });
}

// --- Disks page (Phase 5b) -------------------------------------------------------

export type DiskDecision = "approve" | "ignore" | "reject" | "clear";

export function decideDisk(diskId: number, decision: DiskDecision, confirmName?: string) {
  return request<ExternalDisk>(`/disks/${diskId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, confirm_name: confirmName ?? null }),
  });
}

export function rescanDisks() {
  return request<{ status: string }>("/disks/rescan", { method: "POST" });
}

// --- Planning calendar, iCal export, activity (Phase 6) ------------------------------

export interface AgendaResponse {
  items: import("./views/planning/calendarModel").AgendaItem[];
  timezone: string | null;
  proxmox_unsupported: string[];
  proxmox_error: string | null;
}

export function getAgenda(start: Date, end: Date, proxmox: boolean) {
  const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), proxmox: String(proxmox) });
  return request<AgendaResponse>(`/planning/agenda?${params.toString()}`);
}

export interface CalendarFeed {
  enabled: boolean;
  url: string | null;
}

export function getCalendarFeed() {
  return request<CalendarFeed>("/calendar");
}

export function regenerateCalendarFeed() {
  return request<CalendarFeed>("/calendar/token", { method: "POST" });
}

export interface ActivityItem {
  id: string;
  type: "backup" | "eject" | "detection" | "planning";
  status: string;
  at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  disk_id: number | null;
  disk_label: string;
  run_id: number | null;
  planning_run_id?: number | null;
  event_title?: string | null;
  planned: boolean;
  auto_eject: boolean;
  step: string | null;
  progress_percent: number | null;
  transferred_bytes: number | null;
  no_new_data: boolean;
  message: string | null;
}

export function getActivityFeed(days = 90) {
  return request<ActivityItem[]>(`/activity-events/feed?days=${days}`);
}

// --- Integrations page (Phase 6b) ----------------------------------------------------

export type IntegrationKey = "pve" | "pbs" | "agent";

export interface IntegrationHealth {
  key: IntegrationKey;
  configured: boolean;
  ok: boolean;
  code: "not_configured" | "unreachable" | null;
  url: string | null;
  fingerprint: string | null;
  node: string | null;
  version: string | null;
  latency_ms: number | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
  message: string | null;
}

export function getIntegrationsHealth() {
  return request<IntegrationHealth[]>("/integrations/health");
}

export function testIntegration(key: IntegrationKey) {
  return request<IntegrationHealth>(`/integrations/${key}/test`, { method: "POST" });
}

export function deleteRevokedAgents() {
  return request<{ deleted: number }>("/agents/revoked", { method: "DELETE" });
}

// --- Datastores managed by the app (Phase 6c) --------------------------------------

export interface SourceDatastore {
  name: string;
  total: number | null;
  used: number | null;
  removable: boolean;
}

export function getDatastores() {
  return request<{ items: SourceDatastore[]; current: string; error: string | null }>("/datastores");
}

export interface DiskDatastoreState {
  state: "ok" | "config_missing" | "conflict";
  datastore: string;
  recoverable?: boolean;
  comment?: string | null;
}

export interface DatastoreHealth {
  checked: boolean;
  checked_at?: string;
  error?: string | null;
  datastores: string[];
  source: { name: string; state: "ok" | "missing" | "unknown" } | null;
  disks: Record<string, DiskDatastoreState>;
  blocked_events: number[];
}

export function getDatastoreHealth() {
  return request<DatastoreHealth>("/datastores/health");
}

export function checkDatastoreHealth() {
  return request<DatastoreHealth>("/datastores/health/check", { method: "POST" });
}

export function chooseSourceDatastore(name: string) {
  return request<DatastoreHealth>("/datastores/source", { method: "PUT", body: JSON.stringify({ name }) });
}

// --- Phase 5f: disk sheet and guards ------------------------------------------------

export type DiskAlertCode =
  | "usage_critical"
  | "usage_warning"
  | "smart_danger"
  | "smart_warning"
  | "verify_recommended"
  | "usb2_slow"
  | "rotation_stale"
  | "pbs_config_recoverable"
  | "pbs_config_missing"
  | "pbs_conflict"
  | "verify_failed";

export interface DiskAlert {
  disk_id: number;
  disk_name: string;
  code: DiskAlertCode;
  level: "warning" | "danger";
  params: Record<string, unknown>;
}

export function getDiskAlerts() {
  return request<DiskAlert[]>("/disks/alerts");
}

export interface DiskContentItem {
  group: string;
  type: "vm" | "ct" | "host";
  id: string;
  name: string | null;
  versions: number;
  oldest: string;
  newest: string;
  size_bytes: number | null;
}

export interface DiskContents {
  disk_id: number;
  cached_at: string | null;
  items: DiskContentItem[];
  total_versions: number;
}

export function getDiskContents(diskId: number) {
  return request<DiskContents>(`/disks/${diskId}/contents`);
}

export function refreshDiskContents(diskId: number) {
  return request<DiskContents>(`/disks/${diskId}/contents/refresh`, { method: "POST" });
}

export interface DiskProfilePatch {
  display_name?: string;
  color?: DiskColor | null;
  location_note?: string | null;
  retention?: DiskRetention;
}

export function updateDiskProfile(diskId: number, payload: DiskProfilePatch) {
  return request<ExternalDisk>(`/disks/${diskId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function diskPhotoUrl(disk: Pick<ExternalDisk, "id" | "photo_version">): string | null {
  return disk.photo_version ? `${API_BASE_PATH}/disks/${disk.id}/photo?v=${encodeURIComponent(disk.photo_version)}` : null;
}

export async function uploadDiskPhoto(diskId: number, file: File): Promise<ExternalDisk> {
  // multipart: no JSON Content-Type (the browser sets the boundary).
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE_PATH}/disks/${diskId}/photo`, { method: "PUT", body, credentials: "same-origin" });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    throw new Error("Session expired. Please log in again.");
  }
  if (!response.ok) {
    let code: string | undefined;
    let message = `Request failed with status ${response.status}`;
    try {
      const parsed = (await response.json()) as { detail?: { code?: string; message?: string } | string };
      if (parsed.detail && typeof parsed.detail === "object") {
        code = parsed.detail.code;
        message = parsed.detail.message ?? message;
      } else if (typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      // keep the generic message
    }
    throw new ApiError(message, response.status, code);
  }
  return (await response.json()) as ExternalDisk;
}

export function deleteDiskPhoto(diskId: number) {
  return request<ExternalDisk>(`/disks/${diskId}/photo`, { method: "DELETE" });
}

export function startDiskVerify(diskId: number, autoEject = true) {
  return request<{ run_id: number; status: string }>(`/disks/${diskId}/verify`, {
    method: "POST",
    body: JSON.stringify({ confirmation: true, auto_eject_after_success: autoEject }),
  });
}

export function dismissDiskVerify(diskId: number) {
  return request<ExternalDisk>(`/disks/${diskId}/verify/dismiss`, { method: "POST" });
}

interface SettingItem {
  key: string;
  value: unknown;
}

/** Forgotten-rotation reminder (ROTATION_STALE_DAYS, all disks). */
export async function getRotationStaleDays(): Promise<number> {
  const items = await request<SettingItem[]>("/settings?group=general");
  const value = items.find((item) => item.key === "ROTATION_STALE_DAYS")?.value;
  return typeof value === "number" ? value : 30;
}

export function setRotationStaleDays(days: number) {
  return request<SettingItem[]>("/settings", { method: "PATCH", body: JSON.stringify({ values: { ROTATION_STALE_DAYS: days } }) });
}
