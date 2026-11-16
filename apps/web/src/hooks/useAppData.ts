import { useEffect, useMemo, useRef, useState } from "react";

import type { RunProgressData } from "../events";
import { useEvents } from "../useEvents";
import type { BackupLaunchOptions, DiskAlert } from "../api";
import { makeNotice, noticeFromError, type Notice } from "../notices";
import { createRequestGuard } from "../requestGuard";

import {
  getAgentStatus,
  getBackupRuns,
  cleanupBackupRuns,
  cleanupExternalBackupRuns,
  ejectExternalDisk,
  getExternalBackupRuns,
  getPBSInventory,
  getPBSStatus,
  getProxmoxBackupJobs,
  getPlanningDisks,
  getPlanningOverview,
  getScheduledBackupEvents,
  getScheduledBackupRuns,
  getPreferredDisks,
  getProxmoxInventory,
  getProxmoxStatus,
  getSystemVersion,
  getDatastoreHealth,
  getDiskAlerts,
  type DatastoreHealth,
  getUnplannedAssets,
  getVMs,
  getOverview,
  prepareDisk,
  syncPBSInventory,
  syncProxmoxInventory,
  triggerAutoSync,
  runExternalBackup,
  updateDisk,
  updateAssetIgnore,
  updateProxmoxBackupJobSelection,
  updateVM,
} from "../api";
import type {
  AgentStatus,
  BackupRun,
  ExternalBackupRun,
  ExternalDisk,
  Overview,
  PBSInventoryItem,
  PBSStatus,
  PlanningOverview,
  ProxmoxStatus,
  ProxmoxBackupJob,
  VirtualMachine,
  DiskPlanningSummary,
  UnplannedAsset,
  ScheduledBackupEvent,
  ScheduledBackupRun,
  SystemVersion,
} from "../types";

export interface AppDataState {
  agentStatus: AgentStatus;
  overview: Overview;
  vms: VirtualMachine[];
  disks: ExternalDisk[];
  backupRuns: BackupRun[];
  externalBackupRuns: ExternalBackupRun[];
  planningDisks: DiskPlanningSummary[];
  planningOverview: PlanningOverview;
  unplannedAssets: UnplannedAsset[];
  scheduledBackupEvents: ScheduledBackupEvent[];
  scheduledBackupRuns: ScheduledBackupRun[];
  pbsInventory: PBSInventoryItem[];
  pbsStatus: PBSStatus;
  proxmoxStatus: ProxmoxStatus;
  proxmoxBackupJobs: ProxmoxBackupJob[];
  systemVersion: SystemVersion | null;
  /** Phase 6c: verdict of the last datastore check (null: not available). */
  datastoreHealth: DatastoreHealth | null;
  /** Phase 5f: guards decided server-side (usage, SMART, removal, USB 2.0, rotation). */
  diskAlerts: DiskAlert[];
}

async function fetchAppData(): Promise<AppDataState> {
  const [
    agentStatus,
    overview,
    vms,
    preferredDisks,
    backupRuns,
    externalBackupRuns,
    planningDisks,
    planningOverview,
    unplannedAssets,
    scheduledBackupEvents,
    scheduledBackupRuns,
    proxmoxStatus,
    proxmoxBackupJobs,
    proxmoxInventory,
    pbsStatus,
    pbsInventory,
    systemVersion,
    datastoreHealth,
    diskAlerts,
  ] = await Promise.all([
    getAgentStatus(),
    getOverview(),
    getVMs(),
    getPreferredDisks(),
    getBackupRuns(),
    getExternalBackupRuns(),
    getPlanningDisks(),
    getPlanningOverview(),
    getUnplannedAssets(),
    getScheduledBackupEvents(),
    getScheduledBackupRuns(),
    getProxmoxStatus(),
    getProxmoxBackupJobs().catch(() => []),
    getProxmoxInventory(),
    getPBSStatus(),
    getPBSInventory(),
    getSystemVersion().catch(() => null),
    getDatastoreHealth().catch(() => null),
    getDiskAlerts().catch(() => []),
  ]);

  return {
    agentStatus,
    overview,
    vms: proxmoxInventory.length > 0 ? proxmoxInventory : vms,
    disks: preferredDisks,
    backupRuns,
    externalBackupRuns,
    planningDisks,
    planningOverview,
    unplannedAssets,
    scheduledBackupEvents,
    scheduledBackupRuns,
    pbsInventory,
    pbsStatus,
    proxmoxStatus,
    proxmoxBackupJobs,
    systemVersion,
    datastoreHealth,
    diskAlerts,
  };
}

export function useAppData() {
  const [data, setData] = useState<AppDataState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // B26: no global banner any more, the shell shows these as toasts.
  const [notices, setNotices] = useState<Notice[]>([]);
  // A Set (not a single string) so that two independent mutations in flight at
  // once (e.g. ejecting disk A while toggling "trusted" on disk B) each keep
  // their own button disabled instead of the second one clearing the first's
  // key early and allowing a double-submit on a destructive action.
  const [savingKeys, setSavingKeys] = useState<Set<string>>(new Set());
  const [proxmoxSyncing, setProxmoxSyncing] = useState(false);
  const [pbsSyncing, setPbsSyncing] = useState(false);

  function startSaving(key: string) {
    setSavingKeys((current) => {
      const next = new Set(current);
      next.add(key);
      return next;
    });
  }

  function stopSaving(key: string) {
    setSavingKeys((current) => {
      if (!current.has(key)) {
        return current;
      }
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  }

  function isSaving(key: string) {
    return savingKeys.has(key);
  }

  function notify(message: string, code?: string) {
    setNotices((current) => [...current, makeNotice("success", message, code)]);
  }

  function notifyError(failure: unknown) {
    setNotices((current) => [...current, noticeFromError(failure)]);
  }

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const nextData = await fetchAppData();
      setData(nextData);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  async function refresh() {
    try {
      const nextData = await fetchAppData();
      setData(nextData);
    } catch (refreshError) {
      notifyError(refreshError);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!data) {
      return;
    }

    let cancelled = false;
    void triggerAutoSync()
      .then((result) => {
        if (!cancelled && (result.proxmox_triggered || result.pbs_triggered)) {
          window.setTimeout(() => {
            if (!cancelled) void refresh();
          }, 2500);
        }
      })
      .catch(() => {
        // Background auto-sync: no toast, the integrations page shows the state.
      });

    return () => {
      cancelled = true;
    };
  }, [data !== null]);

  const hasActiveExternalBackup =
    data?.externalBackupRuns.some((run) => run.status === "pending" || run.status === "running") ?? false;

  // Real-time updates (SSE). Disk / agent events re-fetch the small lists they
  // affect (coalesced), run.progress patches the run in place.
  const pendingRefresh = useRef<{ disks?: boolean; agent?: boolean; runs?: boolean; timer?: number }>({});
  function scheduleRefresh(part: "disks" | "agent" | "runs") {
    const pending = pendingRefresh.current;
    pending[part] = true;
    if (pending.timer !== undefined) return;
    pending.timer = window.setTimeout(() => {
      const { disks, agent, runs } = pendingRefresh.current;
      pendingRefresh.current = {};
      void Promise.all([
        disks ? getPreferredDisks() : Promise.resolve(null),
        agent || disks ? getAgentStatus() : Promise.resolve(null),
        runs ? getExternalBackupRuns() : Promise.resolve(null),
        disks ? getDiskAlerts().catch(() => null) : Promise.resolve(null),
      ])
        .then(([nextDisks, nextAgent, nextRuns, nextAlerts]) => {
          setData((current) =>
            current
              ? {
                  ...current,
                  disks: nextDisks ?? current.disks,
                  agentStatus: nextAgent ?? current.agentStatus,
                  externalBackupRuns: nextRuns ?? current.externalBackupRuns,
                  diskAlerts: nextAlerts ?? current.diskAlerts,
                }
              : current,
          );
        })
        .catch((refreshError) => {
          notifyError(refreshError);
        });
    }, 300);
  }

  const liveConnected = useEvents(
    ["disk.added", "disk.removed", "disk.updated", "agent.online", "agent.offline", "run.progress", "run.finished"],
    (event) => {
      if (event.type.startsWith("disk.")) scheduleRefresh("disks");
      else if (event.type.startsWith("agent.")) scheduleRefresh("agent");
      else if (event.type === "run.finished") {
        // A finished run also changes its disk (contents, usage, verify flag).
        scheduleRefresh("runs");
        scheduleRefresh("disks");
      }
      else if (event.type === "run.progress") {
        const progress = event.data as RunProgressData;
        if (progress.kind !== "external_backup") return;
        setData((current) => {
          if (!current) return current;
          const known = current.externalBackupRuns.some((run) => run.id === progress.run_id);
          if (!known) {
            scheduleRefresh("runs");
            return current;
          }
          const { run_id: _runId, kind: _kind, disk_id: _diskId, ...fields } = progress;
          return {
            ...current,
            externalBackupRuns: current.externalBackupRuns.map((run) =>
              run.id === progress.run_id ? ({ ...run, ...fields } as typeof run) : run,
            ),
          };
        });
      }
    },
  );

  useEffect(() => {
    // Polling only as a fallback while the live stream is down.
    if (!hasActiveExternalBackup || liveConnected) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void getExternalBackupRuns()
        .then((externalBackupRuns) => {
          setData((current) => (current ? { ...current, externalBackupRuns } : current));
        })
        .catch(() => {
          // Background fallback poll: the next tick (or the live stream) retries.
        });
    }, 2000);

    return () => window.clearInterval(intervalId);
  }, [hasActiveExternalBackup, liveConnected]);

  async function mutateVmCritical(vmId: number, critical: boolean) {
    const key = `vm-${vmId}`;
    startSaving(key);

    try {
      const updated = await updateVM(vmId, { critical });
      setData((current) =>
        current
          ? {
              ...current,
              vms: current.vms.map((item) => (item.id === updated.id ? updated : item)),
            }
          : current,
      );
    } catch (mutationError) {
      notifyError(mutationError);
    } finally {
      stopSaving(key);
    }
  }

  async function mutateDisk(
    diskId: number,
    payload: Partial<
      Pick<
        ExternalDisk,
        | "dedicated_backup_disk"
        | "display_name"
        | "preferred_root_path"
        | "notes"
        | "trusted"
        | "usable_capacity_gb"
        | "reserved_capacity_gb"
        | "planning_notes"
      >
    >,
  ) {
    const key = `disk-${diskId}`;
    startSaving(key);

    try {
      await updateDisk(diskId, payload);
      await refresh();
    } catch (mutationError) {
      notifyError(mutationError);
    } finally {
      stopSaving(key);
    }
  }

  async function runProxmoxSync(successMessage: string) {
    setProxmoxSyncing(true);

    try {
      const summary = await syncProxmoxInventory();
      await refresh();
      notify(summary.already_running
        ? "Proxmox sync is already running."
        : `${successMessage}: ${summary.total_seen} (${summary.synced_vms_count} VM, ${summary.synced_cts_count} CT)`);
    } catch (syncError) {
      notifyError(syncError);
    } finally {
      setProxmoxSyncing(false);
    }
  }

  async function runPBSSync(successMessage: string) {
    setPbsSyncing(true);

    try {
      const summary = await syncPBSInventory();
      await refresh();
      notify(summary.already_running
        ? "PBS sync is already running."
        : `${successMessage}: ${summary.total_snapshots_seen} (${summary.matched_vms} VM, ${summary.matched_cts} CT)`);
    } catch (syncError) {
      notifyError(syncError);
    } finally {
      setPbsSyncing(false);
    }
  }

  // Requests in flight, read synchronously: a double click must not send a
  // second request before React re-renders the disabled button (B25).
  const guard = useRef(createRequestGuard());

  async function startExternalBackup(diskId: number, successMessage: string, autoEjectAfterSuccess = false, options: BackupLaunchOptions = {}) {
    const key = `external-backup-${diskId}`;
    return guard.current.run(key, async () => {
      startSaving(key);
      try {
        const run = await runExternalBackup(diskId, autoEjectAfterSuccess, options);
        setData((current) =>
          current
            ? {
                ...current,
                externalBackupRuns: [run, ...current.externalBackupRuns.filter((item) => item.id !== run.id)],
              }
            : current,
        );
        notify(`${successMessage}: ${run.disk_name}`);
        return run;
      } catch (runError) {
        // 409 (run already active, disk ejected, not approved…): no run, a toast.
        notifyError(runError);
        return null;
      } finally {
        stopSaving(key);
      }
    });
  }

  async function startDiskPreparation(
    diskId: number,
    payload: {
      mode: "preserve_existing_data" | "dedicated_backup";
      mount_base_path?: string | null;
      confirm_destructive: boolean;
    },
    successMessage: string,
  ) {
    const key = `disk-prep-${diskId}`;
    startSaving(key);

    try {
      const run = await prepareDisk(diskId, payload);
      await refresh();
      notify(`${successMessage}: ${run.mount_path ?? `disk ${diskId}`}`);
      return run;
    } catch (runError) {
      notifyError(runError);
    } finally {
      stopSaving(key);
    }

    return null;
  }

  async function mutateAssetIgnore(vm: VirtualMachine, ignored: boolean, reason?: string | null) {
    const vmid = vm.external_id ?? String(vm.id);
    const key = `vm-ignore-${vm.id}`;
    startSaving(key);

    try {
      await updateAssetIgnore(vm.source, vm.node_name ?? "-", vmid, { ignored, reason });
      await refresh();
    } catch (mutationError) {
      notifyError(mutationError);
    } finally {
      stopSaving(key);
    }
  }

  async function mutateBackupJobSelection(jobId: string, selectedVmids: number[]) {
    const key = `backup-job-${jobId}`;
    startSaving(key);

    try {
      await updateProxmoxBackupJobSelection(jobId, selectedVmids);
      await refresh();
      notify("Sélection du job Proxmox mise à jour.");
    } catch (mutationError) {
      notifyError(mutationError);
    } finally {
      stopSaving(key);
    }
  }

  async function ejectDisk(diskId: number, successMessage: string) {
    const key = `disk-eject-${diskId}`;
    startSaving(key);

    try {
      await ejectExternalDisk(diskId);
      await refresh();
      notify(successMessage);
      return true;
    } catch (ejectError) {
      notifyError(ejectError);
    } finally {
      stopSaving(key);
    }

    return false;
  }

  async function cleanupActivityRuns(keepLast: number, successMessage: string) {
    const key = "activity-cleanup";
    startSaving(key);

    try {
      const [externalResult, backupResult] = await Promise.all([
        cleanupExternalBackupRuns(keepLast),
        cleanupBackupRuns(keepLast),
      ]);
      await refresh();
      notify(
        `${successMessage}: ${externalResult.deleted + backupResult.deleted}`,
      );
    } catch (cleanupError) {
      notifyError(cleanupError);
    } finally {
      stopSaving(key);
    }
  }

  const pbsInventoryByVmId = useMemo(
    () => new Map(data?.pbsInventory.map((item) => [item.vm_id, item]) ?? []),
    [data?.pbsInventory],
  );

  return {
    data,
    loading,
    error,
    notices,
    isSaving,
    proxmoxSyncing,
    pbsSyncing,
    pbsInventoryByVmId,
    load,
    refresh,
    dismissNotice: (id: number) => setNotices((current) => current.filter((notice) => notice.id !== id)),
    notify,
    notifyError,
    mutateVmCritical,
    mutateAssetIgnore,
    mutateBackupJobSelection,
    mutateDisk,
    runProxmoxSync,
    runPBSSync,
    startExternalBackup,
    startDiskPreparation,
    ejectDisk,
    cleanupActivityRuns,
  };
}
