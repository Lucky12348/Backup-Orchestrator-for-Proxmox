import { AlertTriangle, HardDrive, Loader2, Wrench, MemoryStick, Microchip, Server, ShieldAlert, ShieldCheck, Usb } from "lucide-react";
import { useState, type ComponentType } from "react";

import { diskPhotoUrl, repairDisk, startDiskVerify, type DiskAlert } from "../../api";
import { ApiError } from "../../notices";
import { openRunModal } from "../../runs/openRun";
import { useConfirm } from "../../ui/confirm";
import { useToast } from "../../ui/toast";
import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import type { ExternalDisk } from "../../types";
import { fill } from "../format";
import { COLOR_CLASS, diskColor, diskKind, type DiskKind } from "./diskProfile";

const KIND_ICON: Record<DiskKind, ComponentType<{ className?: string }>> = {
  hdd_portable: HardDrive,
  hdd_desktop: Server,
  ssd: MemoryStick,
  usb_stick: Usb,
  nvme_enclosure: Microchip,
  unknown: HardDrive,
};

const AVATAR_BOX = {
  xs: "h-5 w-5 rounded-[5px]",
  sm: "h-7 w-7 rounded-[7px]",
  md: "h-[38px] w-[38px] rounded-md",
  lg: "h-[88px] w-[88px] rounded-lg",
  hd: "h-16 w-16 rounded-[12px]",
} as const;
const AVATAR_ICON = { xs: "h-3 w-3", sm: "h-4 w-4", md: "h-[18px] w-[18px]", lg: "h-9 w-9", hd: "h-7 w-7" } as const;

/** Photo of the disk when there is one, else the icon of its kind, in its colour. */
export function DiskAvatar({
  disk,
  t,
  size = "md",
  toneClass,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  size?: "xs" | "sm" | "md" | "lg" | "hd";
  toneClass?: string;
}) {
  const photo = diskPhotoUrl(disk);
  const kind = diskKind(disk.kind);
  const Icon = KIND_ICON[kind];
  const color = diskColor(disk.color);
  const box = AVATAR_BOX[size];
  if (photo) {
    return (
      <img
        src={photo}
        alt={fill(t.diskSheet.photoAlt, { name: disk.display_name })}
        className={cn("flex-none object-cover", box, color ? `ring-2 ${COLOR_CLASS[color].ring}` : "")}
        data-photo={disk.photo_version}
      />
    );
  }
  return (
    <div
      className={cn("flex flex-none items-center justify-center", box, color ? COLOR_CLASS[color].soft : toneClass ?? "bg-subtle text-muted")}
      title={t.diskSheet.kind[kind]}
      data-kind={kind}
    >
      <Icon className={AVATAR_ICON[size]} />
    </div>
  );
}

/** Short guard badges of a card (usage 80/90 %, SMART, removal without eject, USB 2.0). */
export function AlertBadges({ alerts, t }: { alerts: DiskAlert[]; t: TranslationDictionary }) {
  if (!alerts.length) return null;
  const badges = t.guards.badge as Record<string, string>;
  return (
    <div className="flex flex-wrap gap-1.5" data-alerts={alerts.map((alert) => alert.code).join(",")}>
      {alerts.map((alert) => {
        const Icon = alert.code.startsWith("smart") ? ShieldAlert : AlertTriangle;
        const params = Object.fromEntries(
          Object.entries(alert.params).map(([key, value]) => [key, typeof value === "number" ? Math.round(value) : String(value ?? "")]),
        );
        return (
          <span
            key={alert.code}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              alert.level === "danger" ? "bg-danger/15 text-danger-text" : "bg-warning/15 text-warning-text",
            )}
          >
            <Icon className="h-3 w-3" />
            {fill(badges[alert.code] ?? alert.code, params)}
          </span>
        );
      })}
    </div>
  );
}

/** PBS configuration of the disk's datastore deleted or taken (Phase 6c/5f), told
 *  for a user: what happened, whether the data is safe, what PBO / the user does. */
export function PbsConfigNotice({
  note,
  datastore,
  serial,
  t,
}: {
  note: "missing_recoverable" | "missing" | "conflict";
  datastore: string;
  serial: string;
  t: TranslationDictionary;
}) {
  const help = t.datastoresV2.diskHelp[note];
  const command = `proxmox-backup-manager datastore create ${datastore} ${datastore} --backing-device <uuid> --reuse-datastore true --comment pbo:serial=${serial}`;
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col gap-1.5 rounded-md p-3 text-[13px] leading-[1.45]",
        note === "missing_recoverable" ? "bg-warning/15 text-warning-text" : "bg-danger/15 text-danger-text",
      )}
      data-config-notice={note}
    >
      <b>{t.datastoresV2.disk[note]}</b>
      <span>{fill(help.happened, { name: datastore })}</span>
      <span className="font-medium">{help.data}</span>
      <span>{help.next}</span>
      {note === "missing" ? (
        <>
          <span className="text-xs">{t.datastoresV2.manualSteps}</span>
          <code className="block whitespace-pre-wrap break-all rounded-md bg-bg/60 px-2 py-1.5 font-mono text-[11px] text-fg">{command}</code>
        </>
      ) : null}
    </div>
  );
}

export const CONFIG_ALERT_NOTE = {
  pbs_config_recoverable: "missing_recoverable",
  pbs_config_missing: "missing",
  pbs_conflict: "conflict",
} as const;


/** One click: PBS verification of the disk's datastore, no backup (after a
 *  removal without eject). Opens the run modal. */
export function VerifyNowButton({
  disk,
  t,
  onChanged,
  className,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  onChanged: () => Promise<void> | void;
  className?: string;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  async function verify() {
    setBusy(true);
    try {
      const result = await startDiskVerify(disk.id, false);
      openRunModal(result.run_id);
      await onChanged();
    } catch (error) {
      const errors = t.diskSheet.errors as Record<string, string>;
      toast({ tone: "danger", title: error instanceof ApiError && error.code && errors[error.code] ? errors[error.code] : error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      type="button"
      disabled={busy || !disk.connected}
      onClick={() => void verify()}
      className={cn(
        "flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2 text-[13px] font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      data-verify-now={disk.id}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
      {t.diskSheet.verify.now}
    </button>
  );
}


/** B47: delete the damaged versions on the DISK only, copy them again from the
 *  source, verify them. Versions the source no longer holds need a confirmation. */
export function RepairButton({
  diskId,
  lost = [],
  t,
  onChanged,
  className,
}: {
  diskId: number;
  /** "ct/101 2026-11-17T04:01:25Z": damaged versions no longer in the source. */
  lost?: string[];
  t: TranslationDictionary;
  onChanged?: () => Promise<void> | void;
  className?: string;
}) {
  const text = t.diskSheet.repair;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  async function confirmLost(versions: string[]): Promise<boolean> {
    return confirm({
      title: text.lostTitle,
      description: fill(text.lostText, { versions: versions.join(", ") || text.lostUnknown }),
      confirmLabel: text.lostConfirm,
      tone: "danger",
    });
  }
  async function repair() {
    setBusy(true);
    try {
      let confirmed = false;
      if (lost.length) {
        if (!(await confirmLost(lost))) return;
        confirmed = true;
      }
      let result;
      try {
        result = await repairDisk(diskId, confirmed);
      } catch (error) {
        if (!(error instanceof ApiError && error.code === "repair_confirm_lost") || !(await confirmLost([]))) throw error;
        result = await repairDisk(diskId, true);
      }
      openRunModal(result.run_id);
      await onChanged?.();
    } catch (error) {
      const errors = t.diskSheet.errors as Record<string, string>;
      toast({ tone: "danger", title: error instanceof ApiError && error.code && errors[error.code] ? errors[error.code] : error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => void repair()}
      className={cn(
        "flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2 text-[13px] font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      data-repair={diskId}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wrench className="h-4 w-4" />}
      {text.action}
    </button>
  );
}

/** B47: which VM/CT versions are damaged on the disk, and whether the source still has them. */
export function DamagedVersions({ disk, t, onChanged }: { disk: ExternalDisk; t: TranslationDictionary; onChanged?: () => Promise<void> | void }) {
  const text = t.diskSheet.repair;
  const items = disk.damaged_snapshots ?? [];
  if (!items.length) return null;
  const lost = items.filter((item) => item.in_source === false).map((item) => `${item.group} ${item.time}`);
  return (
    <div role="alert" className="flex flex-col gap-2.5 rounded-lg bg-danger/15 p-4 text-[13px] leading-[1.45] text-danger-text" data-damaged={items.length}>
      <b>{fill(text.title, { n: items.length })}</b>
      <span>{text.text}</span>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {items.map((item) => (
          <li key={item.key} className="flex flex-wrap gap-2">
            <span className="font-mono">{item.group}</span>
            <span>{item.time}</span>
            <span className="font-medium">{item.in_source === false ? text.onlyDamaged : item.in_source ? text.inSource : text.sourceUnknown}</span>
          </li>
        ))}
      </ul>
      <RepairButton diskId={disk.id} lost={lost} t={t} onChanged={onChanged} className="w-fit" />
    </div>
  );
}
