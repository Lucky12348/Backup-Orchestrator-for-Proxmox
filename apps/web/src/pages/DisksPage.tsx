import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { getDiskFilesystemUsage } from "../diskPlanning";
import { getExternalBackupReadiness, type ExternalBackupReadiness } from "../api";
import { focusedDiskId } from "../liveDisks";
import { readinessTitle } from "../readiness";
import { formatDateTime } from "../utils";
import type { DisksPageProps } from "./shared";


function CapacityBar({
  used,
  free,
  total,
  fallbackTotal,
  t,
}: {
  used: number | null;
  free: number | null;
  total: number | null;
  fallbackTotal: number;
  t: DisksPageProps["t"];
}) {
  const hasUsage = used !== null && free !== null && total !== null;
  const pct = hasUsage && total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const cls = pct >= 90 ? "danger" : pct >= 70 ? "warn" : "";

  return (
    <div className="cap-wrap">
      <div className="cap-nums">
        <span>{hasUsage ? `${used} GB ${t.diskRealUsed}` : t.diskRealCapacityUnavailable}</span>
        <span>{hasUsage ? `${free} GB ${t.diskRealFree}` : `${t.diskRawCapacity}: ${fallbackTotal} GB`}</span>
      </div>
      <div className="cap-bar">
        <div className={`cap-fill ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      <div style={{ fontSize: 11, color: "var(--t3)", marginTop: 6 }}>
        {hasUsage ? `${total} GB total` : `${t.diskRawCapacity}: ${fallbackTotal} GB`}
      </div>
    </div>
  );
}

export function DisksPage({
  data,
  language,
  isSaving,
  t,
  onDiskToggleRequest,
  onExternalBackupRequest,
  onDiskEjectRequest,
}: DisksPageProps) {
  const activeExternalBackup = data.externalBackupRuns.find((run) => run.status === "pending" || run.status === "running");
  // The server decides (PBS mode, host agent, disk approved and plugged, run
  // already active…); the button title gives the actual reason.
  const readiness = useBackupReadiness(data);
  // /disks?disk=<id> ("Review this disk" from a toast or the setup wizard).
  const focusId = focusedDiskId(useLocation().search);
  const focusRow = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    focusRow.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusId, data.disks.length]);
  return (
    <div className="page-stack">
      <PageHeader title={t.nav.disks} description={t.disksIntro} />

      {data.disks.length === 0 ? (
        <EmptyState description={t.disksEmptyDescription} title={t.emptyDisks} />
      ) : (
        <DataTable>
          <table>
            <thead>
              <tr>
                <th>{t.diskName}</th>
                <th>{t.diskConnected}</th>
                <th>{t.diskCapacity}</th>
                <th>{t.diskTrusted}</th>
                <th>{t.diskPbsVisible}</th>
                <th>{t.diskLastSeen}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.disks.map((disk) => {
                const unusable = isUnusableDisk(disk);
                const usage = getDiskFilesystemUsage(disk);
                return (
                <tr
                  key={disk.id}
                  ref={disk.id === focusId ? focusRow : undefined}
                  className={disk.id === focusId ? "disk-row-focus" : undefined}
                >
                  <td>
                    <div style={{ fontWeight: 500, color: "var(--t1)", fontSize: 13 }}>
                      {disk.display_name}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--t3)", marginTop: 2 }}>
                      {disk.serial_number}
                      {disk.model_name ? ` - ${disk.model_name}` : ""}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--t3)", marginTop: 4 }}>
                      <div>{t.diskCanonicalSerial}: {disk.canonical_serial_number ?? disk.serial_number}</div>
                      <div>{t.diskReportedSerial}: {disk.reported_serial_number ?? disk.serial_number}</div>
                      <div>{t.diskReportedModel}: {disk.reported_model_name ?? disk.model_name ?? t.notAvailable}</div>
                      <div>{t.diskReportedPath}: {disk.reported_mount_path ?? disk.mount_path ?? t.notAvailable}</div>
                      <div>{t.diskAliases}: {disk.serial_aliases?.join(", ") || t.notAvailable}</div>
                      {disk.candidate_type === "unusable" && disk.detection_reason ? (
                        <div style={{ color: "var(--danger)", marginTop: 4 }}>{disk.detection_reason}</div>
                      ) : null}
                    </div>
                  </td>
                  <td>
                    <StatusBadge tone={disk.connected ? "success" : "neutral"}>
                      {disk.connected ? t.connected : t.disconnected}
                    </StatusBadge>
                  </td>
                  <td>
                    <CapacityBar
                      used={usage?.used ?? null}
                      free={usage?.free ?? null}
                      total={usage?.total ?? null}
                      fallbackTotal={disk.capacity_gb}
                      t={t}
                    />
                  </td>
                  <td>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={disk.trusted}
                        disabled={isSaving(`disk-${disk.id}`) || unusable}
                        onChange={(event) =>
                          onDiskToggleRequest({
                            disk,
                            field: "trusted",
                            value: event.target.checked,
                          })
                        }
                      />
                      <span className="toggle-slider" />
                    </label>
                  </td>
                  <td>
                    <StatusBadge tone={disk.pbs_visible ? "success" : "neutral"}>
                      {disk.pbs_visible ? t.yes : t.no}
                    </StatusBadge>
                  </td>
                  <td style={{ fontSize: 12, color: "var(--t3)" }}>
                    {formatDateTime(disk.last_seen_at, language, t.notAvailable)}
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <button
                        className="action-button"
                        disabled={isSaving(`external-backup-${disk.id}`) || !readiness[disk.id]?.ready}
                        onClick={() => {
                          if (activeExternalBackup) {
                            window.location.hash = "#activity";
                            return;
                          }
                          onExternalBackupRequest(disk);
                        }}
                        type="button"
                        title={readinessTitle(readiness[disk.id], t.readiness)}
                      >
                        {t.externalBackupAction}
                      </button>
                      <button
                        className="ghost-button"
                        disabled={isSaving(`disk-eject-${disk.id}`) || !disk.connected}
                        onClick={() => onDiskEjectRequest(disk)}
                        type="button"
                      >
                        {isSaving(`disk-eject-${disk.id}`) ? t.ejectingDisk : t.ejectDiskAction}
                      </button>
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </DataTable>
      )}
    </div>
  );
}

function isUnusableDisk(disk: DisksPageProps["data"]["disks"][number]) {
  return disk.capacity_gb <= 0 || disk.candidate_type === "unusable";
}

/** Readiness per disk, re-asked whenever the disk list or the runs change. */
function useBackupReadiness(data: DisksPageProps["data"]) {
  const [byDisk, setByDisk] = useState<Record<number, ExternalBackupReadiness>>({});
  const key = data.disks
    .map((disk) => `${disk.id}:${disk.connected ? 1 : 0}:${disk.trusted ? 1 : 0}`)
    .concat(data.externalBackupRuns.map((run) => `r${run.id}:${run.status}`))
    .join(",");
  useEffect(() => {
    let cancelled = false;
    void Promise.all(
      data.disks.map((disk) =>
        getExternalBackupReadiness(disk.id).then(
          (result) => [disk.id, result] as const,
          () => [disk.id, null] as const,
        ),
      ),
    ).then((entries) => {
      if (cancelled) return;
      const next: Record<number, ExternalBackupReadiness> = {};
      for (const [id, result] of entries) if (result) next[id] = result;
      setByDisk(next);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return byDisk;
}
