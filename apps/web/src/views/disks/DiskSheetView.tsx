import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Camera, Loader2, Pencil, Play, RefreshCw, ShieldCheck, Trash2, Unplug } from "lucide-react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import {
  deleteDiskPhoto,
  dismissDiskVerify,
  getDiskContents,
  getRotationStaleDays,
  reactivateDisk,
  refreshDiskContents,
  setRotationStaleDays,
  startDiskVerify,
  updateDiskProfile,
  uploadDiskPhoto,
  type DiskAlert,
  type DiskContents,
} from "../../api";
import { cn } from "../../components/ui/utils";
import { CloseButton } from "../../ui/CloseButton";
import { StatusDot, type DotTone } from "../../ui/StatusDot";
import type { Language, TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { openRunModal } from "../../runs/openRun";
import type { ExternalBackupRun, ExternalDisk, ScheduledBackupEvent } from "../../types";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { useNow } from "../../ui/useNow";
import { useCommands } from "../commands";
import { CapacityCard, DiskHistoryList } from "./DiskSheetParts";
import { useToast } from "../../ui/toast";
import { formatBytes, usageParts } from "../../units";
import { fill, formatDay, formatReadAt, formatTime, formatWhen } from "../format";
import { ActionMenuButton } from "../../ui/ActionMenu";
import { useDiskMenu } from "./diskMenu";
import { AlertBadges, CONFIG_ALERT_NOTE, DamagedVersions, DiskAvatar, PbsConfigNotice } from "./DiskBits";
import { diskActions, diskRows, runKind, type DiskRow } from "./diskModel";
import {
  alertsFor,
  COLOR_CLASS,
  DISK_COLORS,
  diskColor,
  diskKind,
  nextRotationFor,
  retentionForm,
  retentionPayload,
  type DiskColor,
  type RetentionForm,
} from "./diskProfile";
import { StatusPill } from "./DisksView";

const card = "flex flex-col gap-3 rounded-lg border border-solid border-border bg-surface p-4";
const input = "rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent";
const secondary =
  "flex items-center justify-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3 py-2 text-[13px] font-medium text-fg hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";
const primary = "flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2 text-[13px] font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";
const SMART_TONE: Record<string, DotTone> = { ok: "ok", warning: "warn", danger: "err", unknown: "off" };

export interface DiskSheetViewProps {
  disks: ExternalDisk[];
  runs: ExternalBackupRun[];
  alerts: DiskAlert[];
  /** Plannings: the header's "Next rotation" (Phase 5h.1). */
  events?: ScheduledBackupEvent[];
  language: Language;
  t: TranslationDictionary;
  onChanged: () => Promise<void> | void;
}

export function errorMessage(error: unknown, t: TranslationDictionary): string {
  const errors = t.diskSheet.errors as Record<string, string>;
  if (error instanceof ApiError && error.code && errors[error.code]) return errors[error.code];
  return error instanceof Error ? error.message : t.error;
}

/** Whether a navigation asks for the identity panel: router state `{ editIdentity: true }`
 *  (disk menu), or a legacy #identity link. */
export function wantsIdentityPanel(state: unknown, hash: string): boolean {
  return hash === "#identity" || Boolean(state && typeof state === "object" && (state as { editIdentity?: unknown }).editIdentity === true);
}

export function DiskSheetView({ disks, runs, alerts, events = [], language, t, onChanged }: DiskSheetViewProps) {
  const { id } = useParams();
  const disk = disks.find((item) => String(item.id) === id);
  const text = t.diskSheet;
  // "Rename / colour…" from a disk menu lands on /disks/:id#identity: the panel opens.
  // The identity panel is page state. A request to open it (disk menu
  // "Rename / colour…": router state; old links: #identity) is consumed once
  // and removed from the history entry, so F5 never reopens it.
  const [editing, setEditing] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (!wantsIdentityPanel(location.state, location.hash)) return;
    setEditing(true);
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null });
  }, [location.key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!disk) {
    return (
      <div className="flex flex-col gap-4">
        <BackLink t={t} />
        <p className="m-0 text-sm text-muted">{text.notFound}</p>
      </div>
    );
  }
  const row = diskRows([disk], runs)[0];
  const diskAlerts = alertsFor(alerts, disk.id);
  return (
    <div className="flex flex-col gap-4" data-disk-sheet={disk.id}>
      <BackLink t={t} />
      {/* Phase 5h.1: a compact header on the whole width, the identity edited in a side panel. */}
      <SheetHeader disk={disk} row={row} alerts={diskAlerts} runs={runs} events={events} language={language} t={t} onEdit={() => setEditing(true)} onChanged={onChanged} />

      {diskAlerts
        .filter((alert) => alert.code in CONFIG_ALERT_NOTE)
        .map((alert) => (
          <PbsConfigNotice
            key={alert.code}
            note={CONFIG_ALERT_NOTE[alert.code as keyof typeof CONFIG_ALERT_NOTE]}
            datastore={String(alert.params.datastore ?? disk.pbs_datastore_name ?? "")}
            serial={disk.serial_number}
            t={t}
          />
        ))}

      <DamagedVersions disk={disk} t={t} onChanged={onChanged} />

      {(disk.verify_recommended || disk.verify_required) && !disk.damaged_snapshots?.length ? (
        <VerifyBanner disk={disk} t={t} language={language} onChanged={onChanged} failed={diskAlerts.some((alert) => alert.code === "verify_failed")} />
      ) : null}

      {/* 2/3 – 1/3; one column on mobile. */}
      <div className="grid items-start gap-4 lg:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-2">
          <ContentsCard disk={disk} t={t} language={language} />
          <CapacityCard disk={disk} runs={runs} language={language} t={t} className={card} />
          <section className={card} aria-labelledby="history-title">
            <h2 id="history-title" className="m-0 text-sm font-semibold">{text.history}</h2>
            <DiskHistoryList disk={disk} runs={runs} language={language} t={t} pageSize={10} filterable />
          </section>
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <HealthCard disk={disk} t={t} language={language} />
          <RetentionCard disk={disk} t={t} onChanged={onChanged} />
          <ReminderCard t={t} />
        </div>
      </div>
      <IdentityPanel open={editing} disk={disk} t={t} onClose={() => setEditing(false)} onChanged={onChanged} />
    </div>
  );
}

const SMART_LABEL_TONE: Record<string, string> = { ok: "text-success-text", warning: "text-warning-text", danger: "text-danger-text", unknown: "text-muted" };

/** Name, state, model · serial · location, four indicators, the main actions. */
function SheetHeader({
  disk,
  row,
  alerts,
  runs,
  events,
  language,
  t,
  onEdit,
  onChanged,
}: {
  disk: ExternalDisk;
  row: DiskRow<ExternalDisk>;
  alerts: DiskAlert[];
  runs: ExternalBackupRun[];
  events: ScheduledBackupEvent[];
  language: Language;
  t: TranslationDictionary;
  onEdit: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const text = t.diskSheet;
  const now = useNow();
  const commands = useCommands();
  const { toast } = useToast();
  const actions = useDiskMenu(disk, t, runs);
  const panel = diskActions(disk, row.status);
  const usage = usageParts(disk.filesystem_used_gb, disk.filesystem_total_gb, language);
  const lastBackup = runs
    .filter((run) => run.disk_id === disk.id && run.status === "success" && runKind(run) === "backup")
    .sort((a, b) => (b.finished_at ?? b.started_at).localeCompare(a.finished_at ?? a.started_at))[0];
  const next = nextRotationFor(disk, events);
  const smart = disk.smart_status ?? null;
  const ready = row.status === "approved";
  const verifiable = ready && Boolean(disk.prepared_as_pbs_datastore && disk.pbs_datastore_name);
  async function verify() {
    try {
      const result = await startDiskVerify(disk.id, false);
      openRunModal(result.run_id);
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t), diskId: disk.id });
    }
  }
  async function reactivate() {
    try {
      await reactivateDisk(disk.id);
      toast({ tone: "success", title: fill(t.disksV2.action.reactivated, { name: disk.display_name }), diskId: disk.id });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t), diskId: disk.id });
    }
  }
  const indicators: { key: string; label: string; value: string; tone?: string }[] = [
    { key: "used", label: text.indicator.used, value: usage ? `${usage.usage} · ${usage.pct}` : "—" },
    { key: "last", label: text.indicator.lastBackup, value: formatWhen(lastBackup?.finished_at ?? null, language, now, t.dashboardV2) },
    { key: "health", label: text.indicator.health, value: smart ? text.smart[smart as keyof typeof text.smart] : text.smart.none, tone: SMART_LABEL_TONE[smart ?? "unknown"] },
    { key: "next", label: text.indicator.nextRotation, value: next ? `${formatDay(next, language)} · ${formatTime(next, language)}` : text.indicator.notPlanned },
  ];
  return (
    <section className={cn(card, "gap-4")} aria-labelledby="disk-name" data-identity>
      <div className="flex flex-wrap items-start gap-4">
        <DiskAvatar disk={disk} t={t} size="hd" />
        <div className="flex min-w-[220px] flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 id="disk-name" className="m-0 min-w-0 truncate text-[24px] font-semibold tracking-[-0.02em]">{disk.display_name}</h1>
            <button type="button" onClick={onEdit} aria-label={text.edit} title={text.edit} className="flex h-8 w-8 items-center justify-center rounded-lg border-0 bg-transparent text-faint hover:bg-hover hover:text-fg" data-edit-identity>
              <Pencil className="h-4 w-4" />
            </button>
            <StatusPill status={row.status} label={t.disksV2.status[row.status]} />
          </div>
          <span className="text-[13px] text-muted">
            {[text.kind[diskKind(disk.kind)], disk.model_name, disk.serial_number, disk.location_note].filter(Boolean).join(" · ")}
          </span>
          <AlertBadges alerts={alerts} t={t} />
        </div>
        <div className="flex flex-wrap items-center gap-2" data-sheet-actions>
          <button type="button" className={primary} disabled={!ready} onClick={() => commands.backup(disk)}>
            <Play className="h-4 w-4" />
            {t.menu.disk.backup}
          </button>
          <button type="button" className={secondary} disabled={!verifiable} onClick={() => void verify()}>
            <ShieldCheck className="h-4 w-4" />
            {t.menu.disk.verify}
          </button>
          {panel.includes("reactivate") ? (
            <button type="button" className={secondary} onClick={() => void reactivate()}>
              <RefreshCw className="h-4 w-4" />
              {t.disksV2.action.reactivate}
            </button>
          ) : panel.includes("eject") ? (
            <button
              type="button"
              className={secondary}
              disabled={row.status === "running"}
              title={row.status === "running" ? t.menu.disk.ejectRunningHint : undefined}
              onClick={() => commands.eject(disk)}
            >
              <Unplug className="h-4 w-4" />
              {t.menu.disk.eject}
            </button>
          ) : null}
          <ActionMenuButton actions={actions} label={fill(t.menu.label, { name: disk.display_name })} />
        </div>
      </div>
      <dl className="m-0 grid grid-cols-2 gap-3 border-0 border-t border-solid border-border pt-4 md:grid-cols-4" data-indicators>
        {indicators.map((item) => (
          <div key={item.key} className="flex min-w-0 flex-col gap-0.5" data-indicator={item.key}>
            <dt className="text-xs text-faint">{item.label}</dt>
            <dd className={cn("m-0 truncate text-[14px] font-medium", item.tone ?? "text-fg")}>{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function BackLink({ t }: { t: TranslationDictionary }) {
  return (
    <Link to="/disks" className="flex w-fit items-center gap-1.5 text-[13px] text-muted no-underline hover:text-fg">
      <ArrowLeft className="h-4 w-4" />
      {t.diskSheet.back}
    </Link>
  );
}

function when(iso: string | null | undefined, language: Language, t: TranslationDictionary): string {
  return formatWhen(iso ?? null, language, new Date(), t.dashboardV2);
}

function VerifyBanner({
  disk,
  t,
  language,
  onChanged,
  failed = false,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  language: Language;
  onChanged: () => Promise<void> | void;
  failed?: boolean;
}) {
  const text = t.diskSheet.verify;
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  async function verify() {
    setBusy(true);
    try {
      const result = await startDiskVerify(disk.id, false);
      openRunModal(result.run_id);
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  async function dismiss() {
    setBusy(true);
    try {
      await dismissDiskVerify(disk.id);
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg bg-warning/15 p-4 text-[13px] leading-[1.45] text-warning-text" data-verify-banner>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <b>{failed ? text.failedTitle : disk.verify_required ? text.requiredTitle : text.title}</b>
        <span>{failed ? text.failedText : disk.verify_required ? text.requiredText : fill(text.text, { when: when(disk.unsafe_removal_at, language, t) })}</span>
        {disk.connected ? null : <span>{text.needsPlug}</span>}
      </div>
      <button type="button" className={primary} disabled={busy || !disk.connected} onClick={() => void verify()}>
        <ShieldCheck className="h-4 w-4" />
        {text.action}
      </button>
      <button type="button" className={secondary} disabled={busy} onClick={() => void dismiss()}>
        {text.dismiss}
      </button>
    </div>
  );
}

export function ContentsCard({ disk, t, language, initial }: { disk: ExternalDisk; t: TranslationDictionary; language: Language; initial?: DiskContents }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const [contents, setContents] = useState<DiskContents | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    getDiskContents(disk.id).then(
      (result) => !cancelled && setContents(result),
      () => !cancelled && setContents(null),
    );
    return () => {
      cancelled = true;
    };
  }, [disk.id, disk.contents_cached_at, initial]);
  async function refresh() {
    setBusy(true);
    try {
      setContents(await refreshDiskContents(disk.id));
    } catch (error) {
      toast({ tone: "warning", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  const items = contents?.items ?? [];
  const date = (iso: string) => new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en-GB", { dateStyle: "medium" }).format(new Date(iso));
  return (
    <section className={card} aria-labelledby="contents-title">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="contents-title" className="m-0 text-sm font-semibold">{text.contents}</h2>
        {contents?.cached_at ? <span className="text-xs text-faint" data-read-at>{formatReadAt(contents.cached_at, language, new Date(), text)}</span> : null}
        <span className="flex-1" />
        {disk.pbs_datastore_name ? (
          <button type="button" className={secondary} disabled={busy} onClick={() => void refresh()}>
            <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
            {text.refresh}
          </button>
        ) : null}
      </div>
      {items.length === 0 ? (
        <p className="m-0 text-[13px] text-faint">{contents?.cached_at ? text.contentsEmpty : text.contentsNever}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-[13px]" data-contents={items.length}>
            <thead>
              <tr className="border-0 border-b border-solid border-border text-left text-[13px] text-muted">
                <th className="py-1.5 font-medium">{text.col.guest}</th>
                <th className="py-1.5 text-right font-medium">{text.col.versions}</th>
                <th className="py-1.5 font-medium">{text.col.oldest}</th>
                <th className="py-1.5 font-medium">{text.col.newest}</th>
                <th className="py-1.5 text-right font-medium">{text.col.size}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.group} className="border-0 border-t border-solid border-border">
                  <td className="py-2">
                    <span className="font-medium">{item.name ?? item.group}</span>
                    {item.name ? <span className="ml-2 rounded bg-subtle px-1.5 py-px font-mono text-[11px] text-muted">{item.group}</span> : null}
                  </td>
                  <td className="py-2 text-right">{item.versions}</td>
                  <td className="py-2 text-muted">{date(item.oldest)}</td>
                  <td className="py-2">{date(item.newest)}</td>
                  <td className="py-2 text-right text-muted">{formatBytes(item.size_bytes, language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Phase 5h.1: the identity is edited in a side panel (not a permanent form),
 *  with a live preview of the name, colour and photo. */
function IdentityPanel({
  open,
  disk,
  t,
  onClose,
  onChanged,
}: {
  open: boolean;
  disk: ExternalDisk;
  t: TranslationDictionary;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(disk.display_name);
  const [color, setColor] = useState<DiskColor | null>(diskColor(disk.color));
  const [location, setLocation] = useState(disk.location_note ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setName(disk.display_name);
    setColor(diskColor(disk.color));
    setLocation(disk.location_note ?? "");
  }, [open, disk.display_name, disk.color, disk.location_note]);
  const dirty = name.trim() !== disk.display_name || color !== diskColor(disk.color) || location.trim() !== (disk.location_note ?? "");

  async function save() {
    if (!name.trim()) {
      toast({ tone: "warning", title: text.errors.disk_name_required, inbox: false });
      return;
    }
    setBusy(true);
    try {
      await updateDiskProfile(disk.id, { display_name: name.trim(), color, location_note: location.trim() || null });
      toast({ tone: "success", title: text.saved, diskId: disk.id });
      await onChanged();
      onClose();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast({ tone: "warning", title: text.errors.photo_too_large, inbox: false });
      return;
    }
    setBusy(true);
    try {
      await uploadDiskPhoto(disk.id, file);
      toast({ tone: "success", title: text.photoSaved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  async function removePhoto() {
    setBusy(true);
    try {
      await deleteDiskPhoto(disk.id);
      toast({ tone: "success", title: text.photoRemoved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }

  // Live preview: what the disk will look like everywhere once saved.
  const preview = { ...disk, display_name: name.trim() || disk.display_name, color };
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent
        aria-describedby="identity-help"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          nameInput.current?.focus();
        }}
        className="bottom-0 left-auto right-0 top-0 h-full max-h-none w-[min(440px,100vw)] max-w-none translate-x-0 overflow-y-auto rounded-none border-0 border-l border-solid"
        data-identity-panel
      >
        <div className="flex items-center gap-3 border-0 border-b border-solid border-border px-5 py-4">
          <DialogTitle className="m-0 flex-1 text-[17px] font-semibold">{text.identity}</DialogTitle>
          <CloseButton label={t.shell.close} onClick={onClose} />
        </div>
        <div className="flex flex-col gap-4 px-5 py-4">
          <DialogDescription id="identity-help" className="m-0 text-[13px] text-muted">{text.identityHelp}</DialogDescription>
          <div className="flex items-center gap-3 rounded-[12px] bg-subtle p-3" data-preview>
            <DiskAvatar disk={preview} t={t} size="hd" />
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-[17px] font-semibold" data-preview-name>{preview.display_name}</span>
              <span className="truncate text-xs text-faint">{[disk.model_name, disk.serial_number, location.trim()].filter(Boolean).join(" · ")}</span>
            </div>
          </div>
          <label className="flex flex-col gap-1.5 text-[13px] text-muted">
            {text.name}
            <input ref={nameInput} className={input} value={name} maxLength={255} onChange={(event) => setName(event.target.value)} />
          </label>
          <div className="flex flex-col gap-1.5 text-[13px] text-muted">
            <span id="color-label">{text.color}</span>
            <div role="radiogroup" aria-labelledby="color-label" className="flex gap-2">
              {DISK_COLORS.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={color === value}
                  aria-label={text.colors[value]}
                  title={text.colors[value]}
                  onClick={() => setColor(color === value ? null : value)}
                  className={cn("h-7 w-7 rounded-full border-2 border-solid", COLOR_CLASS[value].swatch, color === value ? "border-fg" : "border-transparent hover:border-border-strong")}
                />
              ))}
            </div>
          </div>
          <label className="flex flex-col gap-1.5 text-[13px] text-muted">
            {text.location}
            <input className={input} value={location} maxLength={500} placeholder={text.locationPlaceholder} onChange={(event) => setLocation(event.target.value)} />
          </label>
          <div className="flex flex-col gap-2 border-0 border-t border-solid border-border pt-3">
            <span className="text-[13px] text-muted">{text.photo}</span>
            <div className="flex flex-wrap items-center gap-2">
              <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" aria-label={text.addPhoto} onChange={(event) => void upload(event.target.files?.[0])} />
              <button type="button" className={secondary} disabled={busy} onClick={() => fileRef.current?.click()}>
                <Camera className="h-4 w-4" />
                {disk.photo_version ? text.changePhoto : text.addPhoto}
              </button>
              {disk.photo_version ? (
                <button type="button" className={secondary} disabled={busy} onClick={() => void removePhoto()} aria-label={text.removePhoto}>
                  <Trash2 className="h-4 w-4" />
                </button>
              ) : null}
            </div>
            <span className="text-xs text-faint">{text.photoHint}</span>
          </div>
        </div>
        <div className="mt-auto flex justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3.5">
          <button type="button" className={secondary} onClick={onClose}>
            {t.confirmDialogs.cancel}
          </button>
          <button type="button" className={primary} disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {text.save}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HealthCard({ disk, t, language }: { disk: ExternalDisk; t: TranslationDictionary; language: Language }) {
  const text = t.diskSheet;
  const status = disk.smart_status ?? null;
  const details = disk.smart_details ?? {};
  const reasons = (details.reasons ?? []).filter((reason) => reason in text.smartReason);
  const facts = [
    details.power_on_hours != null ? fill(text.powerOn, { hours: details.power_on_hours }) : null,
    details.temperature_c != null ? fill(text.temperature, { temp: details.temperature_c }) : null,
    details.reallocated ? fill(text.reallocated, { n: details.reallocated }) : null,
  ].filter(Boolean);
  const speed = disk.usb_speed_mbps;
  return (
    <section className={card} aria-labelledby="health-title">
      <h2 id="health-title" className="m-0 text-sm font-semibold">{text.health}</h2>
      <div className="flex flex-col gap-1.5">
        <StatusDot tone={SMART_TONE[status ?? "unknown"]} className="w-fit text-fg" data-smart={status ?? "none"}>
          {status ? text.smart[status] : text.smart.none}
        </StatusDot>
        <span className="text-[13px] leading-[1.45] text-muted">{status ? text.smartHelp[status] : text.smartHelp.none}</span>
        {reasons.length ? (
          <ul className="m-0 list-disc pl-5 text-[13px] text-muted">
            {reasons.map((reason) => (
              <li key={reason}>{(text.smartReason as Record<string, string>)[reason]}</li>
            ))}
          </ul>
        ) : null}
        {facts.length ? <span className="text-xs text-faint">{facts.join(" · ")}</span> : null}
        {disk.smart_checked_at ? <span className="text-xs text-faint">{fill(text.checkedAt, { when: when(disk.smart_checked_at, language, t) })}</span> : null}
      </div>
      <div className="flex flex-col gap-1 border-0 border-t border-solid border-border pt-3 text-[13px]">
        <span className="text-muted">{text.link}</span>
        <span className={cn(speed && speed <= 480 ? "text-warning-text" : "text-fg")}>
          {speed ? (speed <= 480 ? text.usb2 : fill(text.usbFast, { speed })) : text.linkUnknown}
        </span>
      </div>
    </section>
  );
}

function RetentionCard({ disk, t, onChanged }: { disk: ExternalDisk; t: TranslationDictionary; onChanged: () => Promise<void> | void }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const [form, setForm] = useState<RetentionForm>(() => retentionForm(disk.effective_retention));
  const [busy, setBusy] = useState(false);
  const payload = retentionPayload(form);

  async function save() {
    if (!payload) return;
    setBusy(true);
    try {
      await updateDiskProfile(disk.id, { retention: payload });
      toast({ tone: "success", title: text.saved });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  const field = (key: keyof RetentionForm, label: string) => (
    <label className="flex items-center gap-2 text-[13px] text-muted">
      <span className="flex-1">{label}</span>
      <input className={cn(input, "w-20 text-right")} inputMode="numeric" value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
    </label>
  );
  return (
    <section className={card} aria-labelledby="retention-title">
      <h2 id="retention-title" className="m-0 text-sm font-semibold">{text.retention}</h2>
      <span className="text-[13px] leading-[1.45] text-muted">{text.retentionHelp}</span>
      {field("keepLast", text.keepLast)}
      {field("keepWeekly", text.keepWeekly)}
      {field("keepMonthly", text.keepMonthly)}
      {payload ? null : <span className="text-xs text-danger-text">{text.retentionInvalid}</span>}
      <button type="button" className={primary} disabled={!payload || busy} onClick={() => void save()}>
        {text.save}
      </button>
    </section>
  );
}

/** Forgotten-rotation reminder (ROTATION_STALE_DAYS, every disk): its own card. */
function ReminderCard({ t }: { t: TranslationDictionary }) {
  const text = t.diskSheet;
  const { toast } = useToast();
  const [days, setDays] = useState<string>("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getRotationStaleDays().then((value) => setDays(String(value)), () => setDays("30"));
  }, []);
  const value = Number(days);
  const valid = /^\d+$/.test(days) && value >= 1 && value <= 365;
  async function save() {
    if (!valid) return;
    setBusy(true);
    try {
      await setRotationStaleDays(value);
      toast({ tone: "success", title: text.saved });
    } catch (error) {
      toast({ tone: "danger", title: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={card} aria-labelledby="reminder-title">
      <h2 id="reminder-title" className="m-0 text-sm font-semibold">{text.reminder}</h2>
      <label className="flex items-center gap-2 text-[13px] text-muted">
        <span className="flex-1">{text.staleDays}</span>
        <input className={cn(input, "w-20 text-right")} inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} />
      </label>
      {valid || days === "" ? null : <span className="text-xs text-danger-text">{text.staleDaysInvalid}</span>}
      <button type="button" className={secondary} disabled={!valid || busy} onClick={() => void save()}>
        {text.save}
      </button>
    </section>
  );
}

